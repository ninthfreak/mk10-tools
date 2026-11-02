"""Finds the MK10, keeps the connection alive, and moves pictures and key presses across it.

All serial I/O happens on one background thread. The GUI side only calls show()/send() (non-blocking)
and listens to the Qt signals.
"""

import collections
import os
import threading
import time

from PySide6.QtCore import QObject, Signal

from .layout import DeviceLayout
from .protocol import (CMD_SHOW_JPG, ERROR_SEQ, V1_FIND_DEVICE, FrameParser, build_frame, build_frame_v1,
                       build_json, key_event, qmap_decode, qmap_encode)

USB_VID, USB_PID = 0x1D6B, 0x0104   # generic Linux USB gadget ID, so every candidate is confirmed by handshake


class OldFirmware(Exception):
    pass


class NoAnswer(Exception):
    pass


def candidate_ports() -> list[str]:
    env = os.environ.get("MK10_PORT")
    if env:
        return [env]
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [p.device for p in list_ports.comports() if (p.vid, p.pid) == (USB_VID, USB_PID)]


def all_ports() -> list[tuple[str, str]]:
    """(device, description) for the port picker."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        tag = "  (looks like an MK10)" if (p.vid, p.pid) == (USB_VID, USB_PID) else ""
        out.append((p.device, (p.description or p.device) + tag))
    return out


class Connection:
    """One open serial link. Blocking; used only from the manager's thread (and tests)."""

    def __init__(self, port: str):
        import serial
        self.port = port
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = 115200
        self.ser.timeout = 0.02
        self.ser.write_timeout = 5
        self.ser.dtr = True
        self.ser.rts = True
        self.ser.open()
        self.parser = FrameParser()
        self.seq = 1

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def _next_seq(self) -> int:
        s = self.seq
        self.seq = 1 if self.seq >= 0x7FFFFFFF else self.seq + 1
        return s

    def send_json(self, method: str, parameters: dict | None = None) -> int:
        seq = self._next_seq()
        self.ser.write(build_json(seq, method, parameters))
        return seq

    def show_jpeg(self, data: bytes) -> int:
        seq = self._next_seq()
        self.ser.write(build_frame(seq, CMD_SHOW_JPG, data))
        return seq

    def read(self):
        data = self.ser.read(65536)
        return self.parser.feed(data) if data else []

    def handshake(self, timeout: float = 3.0) -> DeviceLayout:
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        seq = self.send_json("getInfo")
        end = time.time() + timeout
        while time.time() < end:
            for pkt in self.read():
                d = pkt.json()
                if d and (pkt.seq == seq or d.get("ack_method") == "getInfo") and "result" in d:
                    return DeviceLayout.from_info(d)
        # No answer in the new format: is it older firmware that only speaks the old one?
        self.ser.write(build_frame_v1(V1_FIND_DEVICE, qmap_encode({})))
        end = time.time() + 1.5
        while time.time() < end:
            for pkt in self.read():
                if pkt.proto == "v1" and pkt.cmd == V1_FIND_DEVICE:
                    info = qmap_decode(pkt.payload) or {}
                    raise OldFirmware(info.get("version", "unknown"))
        raise NoAnswer()


class DeviceManager(QObject):
    connected = Signal(object)            # DeviceLayout
    disconnected = Signal(str)            # reason
    status_changed = Signal(str)          # one line for the status bar
    key_event = Signal(int, int, bool)    # row, col, pressed
    device_error = Signal(str)            # error replies from the device
    frame_done = Signal()                 # the device has drawn the last picture: the next can go now

    def __init__(self, port_setting: str = "auto", parent=None):
        super().__init__(parent)
        self._port_setting = port_setting or "auto"
        self._frame: bytes | None = None
        self._frame_lock = threading.Lock()
        self._commands = collections.deque()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._thread: threading.Thread | None = None
        self.layout: DeviceLayout | None = None
        self.port: str | None = None
        self.status = "Starting"
        self.frames_sent = 0
        self.last_frame_ms = 0.0          # how long writing the picture to the port took
        self.last_round_trip_ms = 0.0     # ... and how long until the device said it had drawn it
        self._awaiting = None             # seq of the picture the device is still working on
        self._sent_at = 0.0
        self._acks = True                 # does this device acknowledge pictures? see _session
        self._misses = 0

    # ---- called from the GUI thread
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mk10-device", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def set_port(self, setting: str):
        if (setting or "auto") != self._port_setting:
            self._port_setting = setting or "auto"
            self._reconnect.set()
            self._wake.set()

    @property
    def is_connected(self) -> bool:
        return self.layout is not None

    @property
    def ready_for_frame(self) -> bool:
        """False while the device is still drawing the last picture, so nobody wastes effort on the next."""
        return self._awaiting is None

    def show(self, jpeg: bytes):
        """Queue a full-canvas JPEG. Only the newest one is sent if several pile up."""
        with self._frame_lock:
            self._frame = jpeg
        self._wake.set()

    def send(self, method: str, parameters: dict | None = None):
        self._commands.append((method, parameters or {}))
        self._wake.set()

    # ---- background thread
    def _set_status(self, text: str):
        if text != self.status:
            self.status = text
            self.status_changed.emit(text)

    def _sleep(self, seconds: float):
        end = time.time() + seconds
        while not self._stop.is_set() and not self._reconnect.is_set() and time.time() < end:
            time.sleep(0.05)

    def _ports(self) -> list[str]:
        if os.environ.get("MK10_PORT"):
            return [os.environ["MK10_PORT"]]
        if self._port_setting != "auto":
            return [self._port_setting]
        return candidate_ports()

    def _run(self):
        while not self._stop.is_set():
            self._reconnect.clear()
            ports = self._ports()
            if not ports:
                self._set_status("Looking for the MK10. Plug it in.")
                self._sleep(2)
                continue
            conn = layout = None
            for port in ports:
                try:
                    self._set_status("Connecting on %s" % port)
                    conn = Connection(port)
                    layout = conn.handshake()
                    break
                except OldFirmware as e:
                    self._set_status("The MK10 on %s has firmware %s. This app needs V2.32 or newer." % (port, e))
                except NoAnswer:
                    self._set_status("%s didn't answer like an MK10." % port)
                except Exception as e:   # serial errors: busy port, permissions, unplugged mid-way
                    msg = str(e)
                    if "denied" in msg.lower() or "busy" in msg.lower() or "PermissionError" in msg:
                        self._set_status("%s is in use or not allowed. Is OpenDeck or ScreenKey running?" % port)
                    else:
                        self._set_status("Couldn't open %s: %s" % (port, msg))
                if conn:
                    conn.close()
                    conn = None
            if not layout:
                self._sleep(3)
                continue
            self.port, self.layout = conn.port, layout
            self._awaiting, self._misses, self._acks = None, 0, True
            self._set_status("Connected on %s: %s, firmware %s" % (conn.port, layout.model, layout.version or "?"))
            self.connected.emit(layout)
            reason = "stopped"
            try:
                self._session(conn)
                if self._reconnect.is_set():
                    reason = "port changed"
            except Exception as e:
                reason = str(e) or e.__class__.__name__
            finally:
                conn.close()
                self.layout = None
                self.port = None
            if not self._stop.is_set():
                self._set_status("Disconnected (%s). Reconnecting." % reason)
                self.disconnected.emit(reason)
                self._sleep(1)

    #: How long to wait for a picture to be acknowledged before assuming it won't be.
    ACK_TIMEOUT = 0.4
    #: After this many unanswered pictures, stop waiting for acknowledgements at all.
    ACK_GIVE_UP = 3

    def _session(self, conn: Connection):
        """One picture is in flight at a time: the device's acknowledgement asks for the next one.

        That is how Waveshare's own host code paces itself, and it lets the device set the rate instead of us
        guessing at one. A device that never acknowledges (older firmware, say) is noticed after a few tries
        and the gate is dropped, so it keeps working exactly as it did before.
        """
        while not self._stop.is_set() and not self._reconnect.is_set():
            for pkt in conn.read():
                k = key_event(pkt)
                if k:
                    self.key_event.emit(*k)
                    continue
                if self._awaiting is not None and pkt.seq == self._awaiting:
                    # The real device answers a picture with cmd 100; the test double answers with JSON.
                    self.last_round_trip_ms = (time.perf_counter() - self._sent_at) * 1000
                    self._awaiting = None
                    self._misses = 0
                    self._acks = True
                    self.frame_done.emit()
                d = pkt.json()
                if d and (pkt.seq == ERROR_SEQ or d.get("success") is False):
                    self.device_error.emit(str(d.get("errorString") or d))
            if self._awaiting is not None and time.perf_counter() - self._sent_at > self.ACK_TIMEOUT:
                self._awaiting = None
                self._misses += 1
                if self._acks and self._misses >= self.ACK_GIVE_UP:
                    self._acks = False
                    self._set_status(self.status + "  (it doesn't acknowledge pictures; sending on a timer)")
            while self._commands:
                method, params = self._commands.popleft()
                conn.send_json(method, params)
            if self._awaiting is None:
                with self._frame_lock:
                    frame, self._frame = self._frame, None
                if frame is not None:
                    t = time.perf_counter()
                    seq = conn.show_jpeg(frame)
                    self.last_frame_ms = (time.perf_counter() - t) * 1000
                    self.frames_sent += 1
                    if self._acks:
                        self._awaiting, self._sent_at = seq, time.perf_counter()
