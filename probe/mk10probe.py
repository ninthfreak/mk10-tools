#!/usr/bin/env python3
"""
mk10probe.py - first-contact test tool for the Waveshare MK10 (and likely MK20/SK18).

Everything here comes from statically reading the program that runs on the MK10
(it ships inside Waveshare's ScreenKey v1.1 Windows installer, device firmware V2.32).
Your unit's firmware may be older or newer.

Commands
  selftest              Check the packet code. No device needed.
  demo                  Run the probe against a SIMULATED MK10. No device needed (Linux/macOS only).
  list                  List serial ports with their USB IDs.
  probe                 Read-only first contact. Tries the newer packet format, then the older one,
                        prints anything the device sends (including raw bytes), then prints key
                        presses until you press Ctrl+C.
  backlight LEVEL       Set screen brightness 0-100 (newer format only; harmless, reversible).
  show-test             Push a test pattern to the main screen (newer format only; needs Pillow).
                        It stays until the theme reloads; unplug/replug to reset.
  storage               Read-only: firmware version and free space (older format).
  send-test             Rehearsal: sends two tiny files to /data/mk10probe/ - one with a correct checksum
                        (should be accepted) and one with a wrong checksum (should be refused).
  update FILE           Firmware update. FILE is the KeyboardDevice program, or the ScreenKey installer
                        .exe to extract it from. Checks the file, the device and free space, then asks you
                        to type UPDATE before sending anything. Sent to /data/Upgrade/KeyboardDevice; the
                        device swaps it in only if the checksum matches. There is no automatic way back.

Common options: --port PORT   --log FILE   --no-dtr

Known USB IDs (from a real MK10 on Windows):
  1D6B:0104  serial port (Linux "multifunction composite gadget" - a generic ID, so we confirm by handshake)
  4250:426F  keyboard chip (QMK)        05E3:0610  internal USB hub

Newer format ("V2", little-endian):
  A1 A5 5A 5E | u32 seq | u32 cmd | u32 len | u32 crc32(len as 4 bytes) | payload | u32 crc32(payload)
  cmd 101 = JSON {"method": "...", "parameters": {...}}; device replies with the same seq.
  cmd 100 = JPEG, full screen, exact resolution. Key presses arrive as JSON "keyStateChanged".

Older format ("V1", little-endian):
  ASCII "AA551234 FIXEDCMDHEAD " | u32 type | u32 cmd | u32 len | u32 crc32(payload) | payload
  type 0 = command, 2 = acknowledgement. Payload = Qt QDataStream QMap<QString,QString>.
  Every command gets an acknowledgement with the same cmd number.
"""

import argparse
import collections
import datetime
import io
import json
import os
import re
import struct
import sys
import threading
import time
import zlib

MAGIC = b"\xA1\xA5\x5A\x5E"
MAGIC_V1 = b"AA551234 FIXEDCMDHEAD "
HEADER_LEN = 20                       # V2: magic + seq + cmd + len + crc(len)
HEADER_LEN_V1 = len(MAGIC_V1) + 16    # V1: magic + type + cmd + len + crc(payload)
CMD_SHOW_JPG = 100
CMD_JSON = 101
MAX_PAYLOAD = 16 * 1024 * 1024

TYPE_NAMES = {0: "CMD", 1: "FILE", 2: "ACK", 3: "FILE_ACK"}
CMD_NAMES = {0: "FIND_DEVICE", 1: "SEND_SYSTEM_DATA", 2: "RELOAD", 3: "GET_THEME", 4: "SET_BACKLIGHT",
             5: "SCAN_STATE", 6: "FILE_START", 7: "FILE_END", 8: "GET_VERSION", 9: "CANVAS_FLIP",
             10: "GET_SCREEN_MESSAGE", 11: "DELETE_THEME", 12: "SEND_PIXMAP", 13: "DEVICE_CMD",
             14: "REQUEST_UPLOAD_KEY", 15: "SEND_JSON", 100: "SHOW_JPG", 101: "JSON"}
V1_FIND_DEVICE = 0
V1_GET_THEME = 3          # reply: theme files + storage (MB)
V1_FILE_START = 6         # payload {device_path: size}
V1_FILE_END = 7           # payload {device_path: crc32 as decimal}; reply {"res": "1"/"0", "fileName": path}
V1_GET_VERSION = 8
ABORT_TRANSFER = b"AA551234 Abort file transfer 123455AA"

UPDATE_DEVICE_PATH = "/data/Upgrade/KeyboardDevice"   # temp location; device swaps it in only if the CRC passes
MIN_FREE_MB = 12
KNOWN_FIRMWARE = {  # sha256 -> description
    "25d8a404b4524412ad889050e4c09d49c2606d8e97e17cb148e3e26b8d5b4ab2":
        "KeyboardDevice V2.32 from Waveshare ScreenKey v1.1 (ScreenKeyWindows_v1_1.exe)",
}

# Live-data names the device's themes ask for (Chinese in firmware) -> English
DATA_NAMES = {"Cpu使用率": "CPU usage", "Cpu温度": "CPU temperature", "Gpu温度": "GPU temperature",
              "Gpu使用率": "GPU usage", "RAM使用率": "RAM usage", "时间": "time", "日期": "date",
              "时": "hour", "分": "minute", "秒": "second"}

# Commands this tool will never send (they change or delete things on the device).
NEVER_SEND = {"deleteFiles", "saveToFile", "setFileCRC", "keyboardInput"}

Packet = collections.namedtuple("Packet", "proto seq ptype cmd payload")   # proto "v1"/"v2"


# ---------------------------------------------------------------- packet layer

def crc32(data):
    return zlib.crc32(data) & 0xFFFFFFFF


def build_frame(seq, cmd, payload):
    n = len(payload)
    return (MAGIC
            + struct.pack("<III", seq & 0xFFFFFFFF, cmd, n)
            + struct.pack("<I", crc32(struct.pack("<I", n)))
            + payload
            + struct.pack("<I", crc32(payload)))


def build_frame_v1(cmd, payload, ptype=0):
    return MAGIC_V1 + struct.pack("<IIII", ptype, cmd, len(payload), crc32(payload)) + payload


def _qstr(s):
    b = s.encode("utf-16-be")
    return struct.pack(">I", len(b)) + b


def qmap_encode(d):
    """Qt5 QDataStream << QMap<QString,QString> (count, then pairs, last key first)."""
    out = struct.pack(">i", len(d))
    for k in sorted(d, reverse=True):
        out += _qstr(k) + _qstr(d[k])
    return out


def _qbytes(b):
    """Qt5 QDataStream << QByteArray: length then the raw bytes (0xFFFFFFFF means null)."""
    return struct.pack(">I", 0xFFFFFFFF) if b is None else struct.pack(">I", len(b)) + b


def qmap_bytes_encode(d):
    """Qt5 QDataStream << QMap<QString,QByteArray>, which is what SEND_PIXMAP carries."""
    out = struct.pack(">i", len(d))
    for k in sorted(d, reverse=True):
        out += _qstr(k) + _qbytes(d[k])
    return out


def qmap_bytes_decode(b):
    """Inverse of qmap_bytes_encode. Values come back as bytes; None if it doesn't parse."""
    try:
        (count,) = struct.unpack_from(">I", b, 0)
        if count > 10000:
            return None
        pos, out = 4, {}
        for _ in range(count):
            key, pos = _qstr_at(b, pos)
            (n,) = struct.unpack_from(">I", b, pos)
            pos += 4
            if n == 0xFFFFFFFF:
                out[key] = b""
                continue
            if pos + n > len(b):
                return None
            out[key] = b[pos:pos + n]
            pos += n
        return out if pos == len(b) else None
    except (struct.error, UnicodeDecodeError):
        return None


def qmap_decode(b):
    """Inverse of qmap_encode. Returns a dict, or None if it doesn't look like one."""
    try:
        (count,) = struct.unpack_from(">I", b, 0)
        if count > 10000:
            return None
        pos, out = 4, {}
        for _ in range(count):
            pair = []
            for _ in range(2):
                (n,) = struct.unpack_from(">I", b, pos)
                pos += 4
                if n == 0xFFFFFFFF:
                    pair.append("")
                    continue
                if n % 2 or pos + n > len(b):
                    return None
                pair.append(b[pos:pos + n].decode("utf-16-be"))
                pos += n
            out[pair[0]] = pair[1]
        return out if pos == len(b) else None
    except (struct.error, UnicodeDecodeError):
        return None


def _qstr_at(b, p):
    (n,) = struct.unpack_from(">I", b, p)
    p += 4
    if n == 0xFFFFFFFF:
        return "", p
    return b[p:p + n].decode("utf-16-be"), p + n


def _qvariant_at(b, p):
    (t,) = struct.unpack_from(">I", b, p)
    p += 5                                           # type id + null flag
    if t == 1:
        return bool(b[p]), p + 1
    if t in (2, 3):
        return struct.unpack_from(">i" if t == 2 else ">I", b, p)[0], p + 4
    if t == 6:
        return struct.unpack_from(">d", b, p)[0], p + 8
    if t == 10:
        return _qstr_at(b, p)
    raise ValueError("unsupported QVariant type %d" % t)


def qvariant_maps_decode(b):
    """Qt5 QDataStream QList<QMap<QString,QVariant>> -> list of dicts, or None."""
    try:
        (count,) = struct.unpack_from(">I", b, 0)
        if count > 1000:
            return None
        p, out = 4, []
        for _ in range(count):
            (m,) = struct.unpack_from(">I", b, p)
            p += 4
            d = {}
            for _ in range(m):
                k, p = _qstr_at(b, p)
                d[k], p = _qvariant_at(b, p)
            out.append(d)
        return out if p == len(b) else None
    except (struct.error, UnicodeDecodeError, ValueError):
        return None


def qvariant_maps_encode(items):
    """Inverse of qvariant_maps_decode for str/int/bool values (used by the simulator)."""
    out = struct.pack(">I", len(items))
    for d in items:
        out += struct.pack(">I", len(d))
        for k in sorted(d, reverse=True):
            v = d[k]
            out += _qstr(k)
            if isinstance(v, bool):
                out += struct.pack(">IBB", 1, 0, int(v))
            elif isinstance(v, int):
                out += struct.pack(">IBi", 2, 0, v)
            else:
                out += struct.pack(">IB", 10, 0) + _qstr(str(v))
    return out


def key_event(pkt):
    """Return (row, col, pressed, device_action_dict) for a key packet in either format, else None."""
    if pkt.proto == "v1" and pkt.cmd == 13:
        items = qvariant_maps_decode(pkt.payload)
        if items and items[0].get("type") == "keyState":
            k = items[0]
            return k.get("row"), k.get("col"), bool(k.get("pressed")), (items[1] if len(items) > 1 else {})
        return None
    if pkt.proto == "v2" and pkt.cmd == CMD_JSON:
        try:
            d = json.loads(pkt.payload)
        except ValueError:
            return None
        if isinstance(d, dict) and d.get("method") == "keyStateChanged":
            p = d.get("parameters", {})
            return p.get("row"), p.get("col"), bool(p.get("pressed")), {}
    return None


class FrameParser:
    """Feed it raw bytes in any chunk size; it returns complete packets in either format."""

    KEEP = max(len(MAGIC), len(MAGIC_V1)) - 1

    def __init__(self):
        self.buf = bytearray()
        self.junk_bytes = 0
        self.bad_frames = 0
        self.junk = bytearray()        # unframed bytes, for display

    def _drop(self, n):
        self.junk_bytes += n
        if len(self.junk) < 4096:
            self.junk += self.buf[:n]
        del self.buf[:n]

    def take_junk(self):
        j, self.junk = bytes(self.junk), bytearray()
        return j

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            i2, i1 = self.buf.find(MAGIC), self.buf.find(MAGIC_V1)
            hits = [i for i in (i2, i1) if i >= 0]
            if not hits:
                # keep only a tail that could be the start of a marker split across reads
                keep = 0
                for k in range(min(len(self.buf), self.KEEP), 0, -1):
                    tail = bytes(self.buf[-k:])
                    if MAGIC.startswith(tail) or MAGIC_V1.startswith(tail):
                        keep = k
                        break
                if len(self.buf) > keep:
                    self._drop(len(self.buf) - keep)
                return out
            i = min(hits)
            if i > 0:
                self._drop(i)
                continue
            if self.buf.startswith(MAGIC):
                if len(self.buf) < HEADER_LEN:
                    return out
                seq, cmd, n, ncrc = struct.unpack_from("<IIII", self.buf, 4)
                if ncrc != crc32(struct.pack("<I", n)) or n > MAX_PAYLOAD:
                    self.bad_frames += 1
                    self._drop(1)
                    continue
                total = HEADER_LEN + n + 4
                if len(self.buf) < total:
                    return out
                payload = bytes(self.buf[HEADER_LEN:HEADER_LEN + n])
                (pcrc,) = struct.unpack_from("<I", self.buf, HEADER_LEN + n)
                del self.buf[:total]
                if pcrc != crc32(payload):
                    self.bad_frames += 1
                    continue
                out.append(Packet("v2", seq, None, cmd, payload))
            else:
                if len(self.buf) < HEADER_LEN_V1:
                    return out
                ptype, cmd, n, pcrc = struct.unpack_from("<IIII", self.buf, len(MAGIC_V1))
                if n > MAX_PAYLOAD or ptype > 3:
                    self.bad_frames += 1
                    self._drop(1)
                    continue
                total = HEADER_LEN_V1 + n
                if len(self.buf) < total:
                    return out
                payload = bytes(self.buf[HEADER_LEN_V1:total])
                if pcrc != crc32(payload):
                    self.bad_frames += 1
                    self._drop(1)
                    continue
                del self.buf[:total]
                out.append(Packet("v1", None, ptype, cmd, payload))


def describe(pkt):
    """Human-readable payload: dict for JSON / Qt maps, text otherwise."""
    p = pkt.payload
    if pkt.cmd == CMD_SHOW_JPG and pkt.proto == "v2":
        return "<JPEG %d bytes>" % len(p)
    if pkt.proto == "v1":
        m = qmap_decode(p)
        if m is not None:
            return m
        lst = qvariant_maps_decode(p)
        if lst is not None:
            return {"items": lst}
    try:
        d = json.loads(p.decode("utf-8"))
        if isinstance(d, dict):
            return d
    except (UnicodeDecodeError, ValueError):
        pass
    if not p:
        return "<empty>"
    return "<%d bytes: %s%s>" % (len(p), p[:64].hex(" "), " ..." if len(p) > 64 else "")


def label(pkt):
    name = CMD_NAMES.get(pkt.cmd, str(pkt.cmd))
    if pkt.proto == "v2":
        return "new format, seq %d, %s" % (pkt.seq, name)
    return "old format, %s, %s" % (TYPE_NAMES.get(pkt.ptype, pkt.ptype), name)


def hexdump(b, limit=256):
    lines = []
    for i in range(0, min(len(b), limit), 16):
        chunk = b[i:i + 16]
        text = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        lines.append("    %04x  %-47s  %s" % (i, chunk.hex(" "), text))
    if len(b) > limit:
        lines.append("    ... %d more bytes (see log)" % (len(b) - limit))
    return "\n".join(lines)


def jpeg_size(data):
    """Return (width, height) from a JPEG's SOF marker, or None."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seglen
    return None


# ---------------------------------------------------------------- logging

class Log:
    def __init__(self, path):
        self.f = open(path, "a", encoding="utf-8") if path else None
        self.path = path

    def _ts(self):
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def packet(self, direction, pkt, raw):
        if not self.f:
            return
        d = describe(pkt)
        if not isinstance(d, str):
            d = json.dumps(d)
        self.f.write("%s %s [%s] len=%d %s\n" % (self._ts(), direction, label(pkt), len(pkt.payload), d))
        if not (pkt.proto == "v2" and pkt.cmd == CMD_SHOW_JPG):
            self.f.write("   raw=%s\n" % raw.hex())
        self.f.flush()

    def raw_rx(self, data):
        if self.f:
            self.f.write("%s RX-BYTES %d: %s\n" % (self._ts(), len(data), data.hex()))
            self.f.flush()

    def note(self, text):
        if self.f:
            self.f.write("# %s %s\n" % (self._ts(), text))
            self.f.flush()


# ---------------------------------------------------------------- device link

class Link:
    def __init__(self, port, log, dtr=True):
        import serial  # pyserial
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = 115200
        self.ser.timeout = 0.05
        self.ser.dtr = dtr
        self.ser.rts = dtr
        self.ser.open()
        self.parser = FrameParser()
        self.log = log
        self.seq = 1
        self.rx_total = 0

    def close(self):
        self.ser.close()

    def _write(self, frame, pkt):
        self.ser.write(frame)
        self.ser.flush()
        self.log.packet("TX", pkt, frame)

    def send_frame(self, cmd, payload, seq=None):
        if seq is None:
            seq = self.seq
            self.seq += 1
        frame = build_frame(seq, cmd, payload)
        self._write(frame, Packet("v2", seq, None, cmd, payload))
        return seq

    def send_json(self, method, parameters=None):
        if method in NEVER_SEND:
            raise ValueError("refusing to send %s from the probe tool" % method)
        seq = self.seq
        self.seq += 1
        body = {"method": method, "parameters": parameters or {}, "requestId": seq}
        return self.send_frame(CMD_JSON, json.dumps(body, separators=(",", ":")).encode(), seq)

    def send_v1(self, cmd, fields=None, payload=None):
        if payload is None:
            payload = qmap_encode(fields or {})
        self._write(build_frame_v1(cmd, payload), Packet("v1", None, 0, cmd, payload))

    def write_raw(self, data, progress=False):
        """Unframed bytes (file contents). Logged by size and fingerprint, not content."""
        import hashlib
        step = 16 * 1024
        next_report = 0.1
        for i in range(0, len(data), step):
            self.ser.write(data[i:i + step])
            if progress and len(data) > 256 * 1024 and (i + step) / len(data) >= next_report:
                print("    sent %3d%%" % min(100, int((i + step) * 100 / len(data))))
                next_report += 0.1
        self.ser.flush()
        if self.log.f:
            self.log.note("TX-RAW %d bytes sha256=%s" % (len(data), hashlib.sha256(data).hexdigest()))

    def poll(self, show_junk=True):
        data = self.ser.read(4096)
        got = []
        if data:
            self.rx_total += len(data)
            self.log.raw_rx(data)
            for pkt in self.parser.feed(data):
                raw = (build_frame(pkt.seq, pkt.cmd, pkt.payload) if pkt.proto == "v2"
                       else build_frame_v1(pkt.cmd, pkt.payload, pkt.ptype))
                self.log.packet("RX", pkt, raw)
                got.append(pkt)
        junk = self.parser.take_junk()
        if junk and show_junk:
            print("  (bytes that aren't a packet in either format:)")
            print(hexdump(junk))
        return got

    def wait_for(self, match, timeout=3.0, on_other=None):
        end = time.time() + timeout
        while time.time() < end:
            for pkt in self.poll():
                if match(pkt):
                    return pkt
                if on_other:
                    on_other(pkt)
        return None


def print_event(pkt):
    k = key_event(pkt)
    if k:
        row, col, pressed, action = k
        extra = ""
        if action:
            target = action.get("Url") or action.get("pageName") or action.get("jumpToPage") or ""
            extra = "   (device's own action: %s %s)" % (action.get("type", "?"), target)
        print("  KEY %s  row %s  col %s%s" % ("DOWN" if pressed else "UP  ", row, col, extra))
        return
    d = describe(pkt)
    if pkt.proto == "v1" and pkt.cmd == 15 and isinstance(d, dict):
        names = [DATA_NAMES.get(n, n) for n in d.get("deviceRequestSystemData", [])]
        print("  device's current page wants live data: %s" % ", ".join(names))
        return
    if True:
        print("  device says (%s):" % label(pkt))
        print("   ", json.dumps(d, indent=2).replace("\n", "\n    ") if isinstance(d, dict) else d)


def show_reply(title, pkt):
    print("\n== %s" % title)
    if pkt is None:
        print("  No reply within the timeout.")
        return None
    d = describe(pkt)
    print("  (%s)" % label(pkt))
    print("  " + (json.dumps(d, indent=2).replace("\n", "\n  ") if isinstance(d, dict) else str(d)))
    return d


# ---------------------------------------------------------------- port finding

def candidate_ports():
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    likely = [p for p in ports if (p.vid, p.pid) == (0x1D6B, 0x0104)]
    if not likely:
        likely = [p for p in ports
                  if "ttyACM" in p.device or "usbmodem" in p.device
                  or (p.vid is not None and sys.platform.startswith("win"))]
    return ports, likely


def cmd_list(_args):
    ports, likely = candidate_ports()
    if not ports:
        print("No serial ports found. Is the MK10 plugged in with a data cable?")
        return 1
    for p in ports:
        ids = "%04x:%04x" % (p.vid, p.pid) if p.vid is not None else "----:----"
        mark = "  <- likely" if p in likely else ""
        print("%-16s %s  %s / %s  serial=%s%s"
              % (p.device, ids, p.manufacturer or "?", p.product or p.description or "?",
                 p.serial_number or "?", mark))
    return 0


def pick_port(args):
    if args.port:
        return args.port
    _ports, likely = candidate_ports()
    if len(likely) == 1:
        print("Using %s (pass --port to choose another)." % likely[0].device)
        return likely[0].device
    if not likely:
        sys.exit("No likely MK10 port found. Run 'list' and pass --port.")
    sys.exit("Several possible ports: %s. Run 'list' and pass --port."
             % ", ".join(p.device for p in likely))


def open_link(args):
    port = pick_port(args)
    log = Log(args.log)
    try:
        link = Link(port, log, dtr=not getattr(args, "no_dtr", False))
    except Exception as e:  # serial.SerialException
        msg = str(e)
        print("Could not open %s: %s" % (port, msg))
        if sys.platform.startswith("win"):
            if "Access is denied" in msg or "PermissionError" in msg:
                print("Another program has this port open, usually ScreenKey.\n"
                      "Quit it fully (check the system tray by the clock), then try again.")
            elif "FileNotFoundError" in msg or "cannot find" in msg:
                print("That COM port doesn't exist. Run 'list' to see the right one.")
            sys.exit(1)
        if "Permission denied" in msg or "Errno 13" in msg:
            print("Fix: add yourself to the serial group, then log out and back in:\n"
                  "  Debian/Ubuntu/Fedora:  sudo usermod -aG dialout $USER\n"
                  "  Arch:                  sudo usermod -aG uucp $USER")
        if "busy" in msg.lower() or "Errno 16" in msg:
            print("Something else has the port. Close ScreenKey, or stop ModemManager:\n"
                  "  sudo systemctl stop ModemManager")
        sys.exit(1)
    log.note("opened %s, dtr=%s, tool=mk10probe v2" % (port, link.ser.dtr))
    return link


# ---------------------------------------------------------------- commands

def run_probe(link, listen_seconds):
    print("Step 1: handshake in the newer format (getInfo)")
    seq = link.send_json("getInfo")
    pkt = link.wait_for(lambda p: p.proto == "v2" and p.seq == seq, on_other=print_event)
    info = show_reply("getInfo reply", pkt)
    v2 = pkt is not None

    print("\nStep 2: handshake in the older format (FIND_DEVICE, then GET_VERSION)")
    link.send_v1(V1_FIND_DEVICE)
    pkt = link.wait_for(lambda p: p.proto == "v1" and p.cmd == V1_FIND_DEVICE, on_other=print_event)
    show_reply("FIND_DEVICE reply", pkt)
    v1 = pkt is not None
    link.send_v1(V1_GET_VERSION)
    show_reply("GET_VERSION reply",
               link.wait_for(lambda p: p.proto == "v1" and p.cmd == V1_GET_VERSION, on_other=print_event))

    if v2:
        print("\nStep 3: ask for the command list (an unknown method makes it list them)")
        seq = link.send_json("mk10probe_list_methods")
        show_reply("method list reply",
                   link.wait_for(lambda p: p.proto == "v2" and p.seq == seq, on_other=print_event))

    print("\nSummary: newer format %s, older format %s."
          % ("ANSWERED" if v2 else "no answer", "ANSWERED" if v1 else "no answer"))
    if not (v1 or v2):
        print("Bytes received from the device so far: %d." % link.rx_total)
        print("If that's 0: unplug the MK10, wait 10 s, plug it back in, wait 30 s, and run this again.\n"
              "If it still says 0, try again with --no-dtr.")

    if listen_seconds == 0:
        print("\nNow press keys on the MK10, top row left to right, then the bottom row. Ctrl+C to stop.")
    else:
        print("\nNow press keys on the MK10 for %d seconds." % listen_seconds)
    print("(Presses may also type characters here. That is the separate keyboard side; ignore it.)")
    end = time.time() + listen_seconds if listen_seconds else None
    try:
        while end is None or time.time() < end:
            for pkt in link.poll():
                print_event(pkt)
    except KeyboardInterrupt:
        print()
    p = link.parser
    if p.buf:
        print("  (leftover bytes at the end:)")
        print(hexdump(bytes(p.buf)))
    print("\nDone. Bytes received: %d. Unframed bytes: %d. Bad packets: %d."
          % (link.rx_total, p.junk_bytes, p.bad_frames))
    if link.log.path:
        print("Log saved to %s" % link.log.path)
    return info


def cmd_probe(args):
    link = open_link(args)
    try:
        run_probe(link, args.seconds)
    except KeyboardInterrupt:
        print("\nStopped early.")
        if link.log.path:
            print("Log saved to %s" % link.log.path)
    finally:
        link.close()
    return 0


def cmd_backlight(args):
    if not 0 <= args.level <= 100:
        sys.exit("Level must be 0-100.")
    link = open_link(args)
    try:
        seq = link.send_json("getInfo")
        link.wait_for(lambda p: p.proto == "v2" and p.seq == seq)
        seq = link.send_json("setBacklight", {"level": args.level})
        show_reply("setBacklight reply",
                   link.wait_for(lambda p: p.proto == "v2" and p.seq == seq, on_other=print_event))
    finally:
        link.close()
    return 0


def screen_size_from_info(d):
    """Find width/height in a getInfo reply, wherever they sit."""
    if not isinstance(d, dict):
        return None
    stack = [d]
    while stack:
        cur = stack.pop()
        w, h = cur.get("deviceWidth"), cur.get("deviceHeight")
        try:
            w, h = int(w), int(h)
            if w > 0 and h > 0:
                return w, h
        except (TypeError, ValueError):
            pass
        stack.extend(v for v in cur.values() if isinstance(v, dict))
    return None


def panel_rects(info):
    """The device's own layout from getInfo (list of dicts with x, y, width, height, isKey, row, col)."""
    if not isinstance(info, dict):
        return None
    stack = [info]
    while stack:
        cur = stack.pop()
        rects = cur.get("rects")
        if isinstance(rects, list) and rects:
            return rects
        stack.extend(v for v in cur.values() if isinstance(v, dict))
    return None


def make_layout_pattern(width, height, rects):
    """Draw each area the device reported: keys numbered 1-10, the small screen labelled, gaps grey."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), (60, 60, 60))           # grey = pixels that aren't on any screen
    draw = ImageDraw.Draw(img)
    try:
        big, small = ImageFont.load_default(size=44), ImageFont.load_default(size=16)
    except TypeError:
        big = small = ImageFont.load_default()

    def text(xy, s, font, fill=(255, 255, 255)):
        try:
            draw.text(xy, s, fill=fill, font=font, anchor="mm")
        except ValueError:
            draw.text((xy[0] - 4 * len(s), xy[1] - 6), s, fill=fill, font=font)

    colors = [(0, 90, 200), (230, 120, 0)]                              # blue / orange
    keys = [r for r in rects if r.get("isKey")]
    keys.sort(key=lambda r: (r.get("row", 0), r.get("col", 0)))
    for n, r in enumerate(keys):
        x, y, w, h = r["x"], r["y"], r["width"], r["height"]
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=colors[n % 2], outline=(255, 255, 255), width=4)
        draw.rectangle([x + 6, y + 6, x + 22, y + 22], fill=(255, 255, 255))   # top-left corner marker
        text((x + w // 2, y + h // 2 - 10), str(n + 1), big)
        text((x + w // 2, y + h - 22), "row %s col %s" % (r.get("row"), r.get("col")), small)
    for r in rects:
        if r.get("isKey"):
            continue
        x, y, w, h = r["x"], r["y"], r["width"], r["height"]
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=(20, 20, 20), outline=(255, 255, 255), width=4)
        draw.rectangle([x + 6, y + 6, x + 22, y + 22], fill=(255, 255, 255))
        text((x + w // 2, y + 30), "TOP", small)
        text((x + w // 2, y + h // 2 - 20), "SMALL", big, fill=(230, 120, 0))
        text((x + w // 2, y + h // 2 + 30), "SCREEN", big, fill=(0, 150, 210))
        text((x + w // 2, y + h - 30), "%dx%d at x=%d" % (w, h, x), small)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, progressive=False, optimize=False)
    return buf.getvalue()


def make_test_pattern(width, height, tile=128):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Blue/orange/white/grey: readable with red-green color blindness.
    colors = [(0, 90, 200), (230, 120, 0), (90, 90, 90), (0, 150, 210), (200, 160, 0)]
    try:
        big, small = ImageFont.load_default(size=40), ImageFont.load_default(size=16)
    except TypeError:                      # Pillow older than 10.1
        big = small = ImageFont.load_default()

    def text(xy, s, font):
        try:
            draw.text(xy, s, fill=(255, 255, 255), font=font, anchor="mm")
        except ValueError:                 # old bitmap font: no anchor support
            draw.text((xy[0] - 4 * len(s), xy[1] - 6), s, fill=(255, 255, 255), font=font)

    rows, cols = max(1, height // tile), max(1, width // tile)
    n = 0
    for r in range(rows):
        for c in range(cols):
            x, y = c * tile, r * tile
            draw.rectangle([x + 2, y + 2, x + tile - 3, y + tile - 3],
                           fill=colors[n % len(colors)], outline=(255, 255, 255), width=3)
            text((x + tile // 2, y + tile // 2 - 10), str(n + 1), big)
            text((x + tile // 2, y + tile - 22), "r%d c%d" % (r, c), small)
            n += 1
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, progressive=False, optimize=False)
    return buf.getvalue()


def cmd_show_test(args):
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("show-test needs Pillow:  py -m pip install pillow   (or pip install pillow)")
    link = open_link(args)
    try:
        seq = link.send_json("getInfo")
        pkt = link.wait_for(lambda p: p.proto == "v2" and p.seq == seq)
        info = describe(pkt) if pkt else None
        if not isinstance(info, dict):
            print("No answer in the newer format, so this firmware can't take a pushed image this way yet.")
            return 1
        res = info.get("result", {}) if isinstance(info.get("result"), dict) else {}
        print("Device: %s, firmware %s, canvas %sx%s"
              % (res.get("deviceModel", "?"), res.get("deviceVersion", "?"),
                 res.get("deviceWidth", "?"), res.get("deviceHeight", "?")))
        size = screen_size_from_info(info) or (args.width, args.height)
        rects = panel_rects(info)
        if rects:
            print("\nSending a %dx%d picture laid out the way the device described itself:" % size)
            print("  keys numbered 1-10 (blue/orange), the small screen labelled, unused pixels grey,")
            print("  and a white square in each area's top-left corner.")
            jpg = make_layout_pattern(size[0], size[1], rects)
        else:
            print("\nSending a %dx%d test pattern..." % size)
            jpg = make_test_pattern(*size)
        seq = link.send_frame(CMD_SHOW_JPG, jpg)
        show_reply("show-jpg reply",
                   link.wait_for(lambda p: p.proto == "v2" and p.seq in (seq, 0xFFFFFFFF),
                                 timeout=5, on_other=print_event))
        print("If the size was wrong, the error above should say what the device wants.")
    finally:
        link.close()
    return 0


# ---------------------------------------------------------------- old-format file transfer / update

def v1_wait(link, cmd, timeout=3.0):
    return link.wait_for(lambda p: p.proto == "v1" and p.cmd == cmd, timeout=timeout,
                         on_other=lambda p: None)


def v1_handshake(link):
    """FIND_DEVICE. Returns the device's info dict, or None."""
    link.send_v1(V1_FIND_DEVICE)
    pkt = v1_wait(link, V1_FIND_DEVICE)
    if pkt is None:
        return None
    d = describe(pkt)
    return d if isinstance(d, dict) else {}


def v1_storage(link):
    """GET_THEME. Returns dict (themes + bytesTotal/bytesAvailable in MB), or None."""
    link.send_v1(V1_GET_THEME)
    pkt = v1_wait(link, V1_GET_THEME, timeout=5)
    if pkt is None:
        return None
    d = describe(pkt)
    return d if isinstance(d, dict) else {}


def print_storage(st):
    if st is None:
        print("  No reply to the storage query.")
        return None
    total, free = st.get("bytesTotal"), st.get("bytesAvailable")
    print("  Storage: %s MB free of %s MB" % (free, total))
    others = {k: v for k, v in st.items() if k not in ("bytesTotal", "bytesAvailable")}
    if others:
        print("  Other entries the device listed (themes and files):")
        for k, v in sorted(others.items()):
            print("    %s  %s" % (k, v))
    try:
        return int(free)
    except (TypeError, ValueError):
        return None


def send_file(link, device_path, data, crc=None, end_timeout=10.0, progress=False):
    """Old-format transfer. Returns the device's FILE_END reply dict, or a dict with 'error'."""
    # Reset any half-finished transfer on the device (harmless if none is active).
    link.write_raw(ABORT_TRANSFER)
    time.sleep(0.5)
    link.poll(show_junk=False)

    link.send_v1(V1_FILE_START, {device_path: str(len(data))})
    if v1_wait(link, V1_FILE_START, timeout=5) is None:
        return {"error": "no acknowledgement to FILE_START; nothing was sent"}
    link.write_raw(data, progress=progress)
    time.sleep(0.5 + len(data) / 2_000_000)          # let the device finish writing to flash
    value = crc32(data) if crc is None else crc
    link.send_v1(V1_FILE_END, {device_path: str(value)})
    pkt = v1_wait(link, V1_FILE_END, timeout=end_timeout)
    if pkt is None:
        return {"error": "no reply to FILE_END"}
    d = describe(pkt)
    return d if isinstance(d, dict) else {"error": "unreadable reply: %s" % d}


def cmd_storage(args):
    link = open_link(args)
    try:
        info = v1_handshake(link)
        if info is None:
            print("No answer to the handshake. Replug the MK10, wait 30 s, and try again.")
            return 1
        print("Device: %s, firmware %s" % (info.get("screen_model", "?"), info.get("version", "?")))
        print_storage(v1_storage(link))
    finally:
        link.close()
    return 0


def rehearsal(link):
    """Two tiny harmless transfers: a good one must be accepted, a bad-checksum one must be refused."""
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    body = ("mk10probe rehearsal file, %s\n" % stamp).encode()
    ok = True

    print("\nRehearsal 1: tiny file with the correct checksum (should be ACCEPTED)")
    r1 = send_file(link, "/data/mk10probe/test-ok.txt", body)
    print("  device replied: %s" % r1)
    good = r1.get("res") == "1"
    print("  %s" % ("PASS" if good else "FAIL"))
    ok = ok and good

    print("Rehearsal 2: tiny file with a WRONG checksum (should be REFUSED)")
    r2 = send_file(link, "/data/mk10probe/test-badcrc.txt", body, crc=crc32(body) ^ 0x5A5A5A5A)
    print("  device replied: %s" % r2)
    refused = r2.get("res") == "0"
    print("  %s" % ("PASS" if refused else "FAIL"))
    ok = ok and refused

    print("Rehearsal 3: the device still answers afterwards")
    alive = v1_handshake(link) is not None
    print("  %s" % ("PASS" if alive else "FAIL"))
    return ok and alive


def cmd_send_test(args):
    link = open_link(args)
    try:
        info = v1_handshake(link)
        if info is None:
            print("No answer to the handshake. Replug the MK10, wait 30 s, and try again.")
            return 1
        print("Device: %s, firmware %s" % (info.get("screen_model", "?"), info.get("version", "?")))
        print_storage(v1_storage(link))
        ok = rehearsal(link)
    finally:
        link.close()
    print("\nRehearsal %s." % ("PASSED: transfers work, and the device refuses a bad checksum" if ok
                               else "FAILED. Don't run 'update'. Send me the log"))
    if link.log.path:
        print("Log saved to %s" % link.log.path)
    return 0 if ok else 1


def find_firmware_in_installer(blob):
    """Find the device program inside a ScreenKey installer: a version manifest followed by
    a 4-byte big-endian length and the ARM program. Returns (version, program_bytes) or None."""
    import re
    for m in re.finditer(rb'\{\s*"version":\s*"(V\d+\.\d+)",\s*"fileMessage"', blob):
        end = blob.find(b"\x00", m.end(), m.end() + 4096)
        if end < 0:
            continue
        elf = blob.find(b"\x7fELF", end, end + 64)
        if elf < 4:
            continue
        (n,) = struct.unpack(">I", blob[elf - 4:elf])
        prog = bytes(blob[elf:elf + n])
        if len(prog) == n and 100_000 < n < 50_000_000:
            return m.group(1).decode(), prog
    return None


def check_program(prog):
    """Sanity-check that this is a 32-bit ARM Linux hard-float program. Returns list of problems."""
    problems = []
    if prog[:4] != b"\x7fELF":
        problems.append("not an ELF program")
        return problems
    if prog[4] != 1:
        problems.append("not 32-bit")
    if struct.unpack_from("<H", prog, 18)[0] != 40:
        problems.append("not ARM")
    if b"/lib/ld-linux-armhf.so.3" not in prog[:4096]:
        problems.append("not built for ARM hard-float Linux")
    if b"KeyboardDevice" not in prog and b"FIXEDCMDHEAD" not in prog:
        problems.append("doesn't look like the MK10 device program")
    return problems


def program_version(prog):
    import re
    vers = re.findall(rb"V\d\.\d\d", prog)
    if not vers:
        return None
    return max(set(vers), key=vers.count).decode()


def load_update_file(path):
    import hashlib
    import mmap
    with open(path, "rb") as f:
        head = f.read(4)
        f.seek(0)
        if head == b"\x7fELF":
            prog = f.read()
            source = "program file"
            version = program_version(prog)
        else:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                found = find_firmware_in_installer(mm)
            finally:
                mm.close()
            if not found:
                sys.exit("Couldn't find the device program inside %s." % path)
            version, prog = found
            source = "extracted from installer"
    problems = check_program(prog)
    if problems:
        sys.exit("This file doesn't look right: %s. Stopping." % "; ".join(problems))
    sha = hashlib.sha256(prog).hexdigest()
    md5 = hashlib.md5(prog).hexdigest()
    return prog, version, source, sha, md5


def version_tuple(v):
    try:
        return tuple(int(x) for x in v.lstrip("Vv").split("."))
    except (AttributeError, ValueError):
        return None


def cmd_update(args):
    prog, version, source, sha, md5 = load_update_file(args.file)
    known = KNOWN_FIRMWARE.get(sha)
    print("Update file: %s (%s)" % (args.file, source))
    print("  version:  %s" % (version or "unknown"))
    print("  size:     %d bytes" % len(prog))
    print("  sha256:   %s" % sha)
    print("  md5:      %s" % md5)
    print("  %s" % ("Matches: " + known if known else
                    "NOT a fingerprint I know. Only continue if it came from Waveshare's own installer or update server."))

    link = open_link(args)
    try:
        info = v1_handshake(link)
        if info is None:
            print("\nNo answer to the handshake. Replug the MK10, wait 30 s, and try again. Nothing was changed.")
            return 1
        current = info.get("version", "?")
        print("\nDevice: %s, firmware %s" % (info.get("screen_model", "?"), current))
        if info.get("screen_model") not in (None, "MK10"):
            print("This tool has only been checked against the MK10. Stopping.")
            return 1
        cv, nv = version_tuple(current), version_tuple(version)
        if cv and nv and nv <= cv and not args.force:
            print("The device already has %s, and this file is %s. Nothing to do (use --force to send anyway)."
                  % (current, version))
            return 0

        free = print_storage(v1_storage(link))
        if free is None and not args.skip_space_check:
            print("Couldn't read free space. Stopping (use --skip-space-check to override).")
            return 1
        if free is not None and free < MIN_FREE_MB:
            print("Only %d MB free; the update needs about %d MB. Stopping. Nothing was changed." % (free, MIN_FREE_MB))
            return 1

        if not rehearsal(link):
            print("\nThe rehearsal didn't behave as expected, so I'm stopping before sending the update.\n"
                  "Nothing was changed apart from tiny test files in /data/mk10probe/. Send me the log.")
            return 1
        print("Rehearsal passed.")

        print("\nAbout to send %s to the device as %s." % (version or "the program", UPDATE_DEVICE_PATH))
        print("The device only swaps it in if the checksum matches. There is no automatic way back.")
        try:
            answer = input("Type UPDATE to continue, anything else to stop: ")
        except EOFError:
            answer = ""
        if answer.strip() != "UPDATE":
            print("Stopped. Nothing was changed.")
            return 1

        print("\nSending (about %d seconds). Don't unplug or press keys." % max(5, len(prog) // 400_000))
        r = send_file(link, UPDATE_DEVICE_PATH, prog, end_timeout=90, progress=True)
        print("\nDevice replied: %s" % r)
        if r.get("res") == "1":
            print("\nThe device says the update was installed.\n"
                  "Next: unplug the MK10, wait 10 s, plug it back in, wait 30 s, then run:\n"
                  "  py mk10probe.py probe      (Linux: python3 mk10probe.py probe)\n"
                  "The newer format should now answer, with version %s." % (version or "?"))
            return 0
        if r.get("error", "").startswith("no acknowledgement"):
            print("\nThe device never acknowledged the start, so the update wasn't sent. It's still on %s.\n"
                  "Unplug it, wait 10 s, plug it back in, and send me the log." % current)
        elif "error" in r:
            print("\nThe device didn't confirm (%s), so I can't tell whether it installed the update.\n"
                  "Unplug it, wait 10 s, plug it back in, wait 30 s, run 'probe', and send me both logs.\n"
                  "Don't run 'update' again until we've looked." % r["error"])
        else:
            print("\nThe device reported a failure (res=%s).\n"
                  "Most likely the checksum didn't match and nothing was installed. Less likely, the swap itself failed.\n"
                  "Unplug it, wait 10 s, plug it back in, run 'probe', and send me both logs." % r.get("res"))
        return 1
    finally:
        link.close()
        if link.log.path:
            print("Log saved to %s" % link.log.path)


# ---------------------------------------------------------------- self-test

# ---------------------------------------------------------------- exploring the unknown parts

#: Any method the firmware doesn't know makes it list the ones it does, which is how we find them.
METHOD_FISHING_NAME = "mk10probeWhichMethodsExist"

#: Old-format commands that only read. None of these write files or change settings.
READONLY_V1 = [(8, "GET_VERSION"), (3, "GET_THEME"), (5, "SCAN_STATE")]

#: getFilesBySuffix takes parameters nobody has written down. These are the obvious guesses.
FILE_LIST_GUESSES = [{"suffix": ".jpg"}, {"suffix": "jpg"},
                     {"path": "/mnt/SDCARD", "suffix": ".json"}, {}]


def method_list_from_error(text):
    """The error names the available methods; pull them out if we recognise the shape."""
    if not text:
        return []
    inside = re.search(r"\[([^\]]*)\]", str(text))
    body = inside.group(1) if inside else str(text).split(":")[-1]
    names = [n.strip().strip("'\"") for n in body.replace(",", " ").split()]
    return [n for n in names if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{2,}", n)]


def missing_parameter_from_error(text):
    """'Missing 'suffixs' parameter' -> 'suffixs'. The firmware tells us what it wanted; listen to it."""
    m = re.search(r"[Mm]issing\s+['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s+parameter", str(text or ""))
    return m.group(1) if m else None


def ask_json(link, method, params=None, timeout=3.0):
    seq = link.send_json(method, params or {})
    pkt = link.wait_for(lambda p: p.proto == "v2" and p.cmd == CMD_JSON
                        and (p.seq == seq or p.seq == 0xFFFFFFFF), timeout)
    return show_reply("%s %s" % (method, json.dumps(params or {}, separators=(",", ":"))), pkt)


def ask_v1(link, cmd, title, fields=None, payload=None, timeout=3.0):
    link.send_v1(cmd, fields, payload)
    pkt = link.wait_for(lambda p: p.proto == "v1" and p.cmd == cmd, timeout)
    print("\n== V1 %d %s" % (cmd, title))
    if pkt is None:
        print("  No reply within the timeout.")
        return None
    fields_back = qmap_decode(pkt.payload)
    if fields_back is not None:
        print("  reply fields:")
        for k, v in sorted(fields_back.items()):
            print("    %-28s %s" % (k, v[:120] if isinstance(v, str) else v))
        return fields_back
    as_bytes = qmap_bytes_decode(pkt.payload)
    if as_bytes is not None:
        print("  reply is a name -> bytes map:")
        for k, v in sorted(as_bytes.items()):
            print("    %-28s %d bytes" % (k, len(v)))
        return as_bytes
    if not pkt.payload:
        print("  acknowledged, with nothing in the reply.")
        return {}
    print("  reply payload (%d bytes), not a map we recognise:" % len(pkt.payload))
    print(hexdump(pkt.payload))
    return None


def cmd_explore(args):
    """Read-only reconnaissance: what methods exist, and what the read commands say."""
    link = open_link(args)
    try:
        print("Nothing here writes a file, deletes anything or changes a setting.")
        seq = link.send_json("getInfo")
        pkt = link.wait_for(lambda p: p.proto == "v2" and p.cmd == CMD_JSON and p.seq == seq, 3.0)
        result = ((describe(pkt) if pkt else None) or {}).get("result") or {}
        if result:
            print("\n== getInfo\n  %s, firmware %s, canvas %sx%s   (the log has the whole reply)"
                  % (result.get("deviceModel", "?"), result.get("deviceVersion", "?"),
                     result.get("deviceWidth", "?"), result.get("deviceHeight", "?")))
        else:
            print("\nNo answer in the new format. Is this older firmware? Try 'probe' first.")

        reply = ask_json(link, METHOD_FISHING_NAME)
        methods = method_list_from_error((reply or {}).get("errorString"))
        if methods:
            print("\n== Methods this firmware admits to")
            for name in sorted(methods):
                mark = "  (we use it)" if name in ("getInfo", "setBacklight") else ""
                mark = "  (NEVER sent from here)" if name in NEVER_SEND else mark
                print("  %-24s%s" % (name, mark))
            unknown = [m for m in methods if m not in NEVER_SEND
                       and m not in ("getInfo", "setBacklight", "keyStateChanged")]
            print("\n  Not understood yet: %s" % (", ".join(sorted(unknown)) or "(none)"))
        else:
            print("\n  Couldn't read a method list out of that reply.")

        print("\n---- read-only old-format commands ----")
        for cmd, title in READONLY_V1:
            ask_v1(link, cmd, title)

        if "getFilesBySuffix" in methods or args.force_file_list:
            print("\n---- getFilesBySuffix: guessing its parameters (read-only) ----")
            wanted = None
            for params in FILE_LIST_GUESSES:
                reply = ask_json(link, "getFilesBySuffix", params)
                wanted = wanted or missing_parameter_from_error((reply or {}).get("errorString"))
                if reply and reply.get("success"):
                    wanted = None
                    break
            if wanted:
                print("\n---- it wants a parameter called %r; trying that in a few shapes ----" % wanted)
                for value in (".jpg", "jpg", [".jpg", ".png"], ".jpg,.png", ["jpg"], ""):
                    reply = ask_json(link, "getFilesBySuffix", {wanted: value})
                    if reply and reply.get("success"):
                        print("\n  That worked: %s takes %r." % (wanted, value))
                        break
                else:
                    print("\n  None of those shapes satisfied it.")

        if args.try_volume:
            print("\n---- setVolume: the parameter name is a guess; this only changes the device speaker ----")
            for params in ({"level": 3}, {"volume": 3}, {"value": 3}):
                ask_json(link, "setVolume", params)

        print("\nDone. The whole exchange is in the log file.")
    finally:
        link.close()
    return 0


#: getFilesBySuffix wants {"suffixs": [...]} and answers {"filePaths": [...]}. Confirmed on a real V2.32.
FILE_SUFFIX_SWEEP = [
    ["wav", "mp3"],                      # what Waveshare's own demo asks for, with no leading dot
    ["Theme", "theme"],                  # ... so try the theme suffix that way too
    [".Theme", ".theme", ".THEME"],      # the SD card's config.json points at a .Theme file
    [".jpg", ".jpeg", ".png", ".bmp", ".gif"],
    [".json", ".conf", ".ini", ".txt", ".xml"],
    [".bin", ".dat", ".so", ".ttf", ".zip"],
    ["jpg", "png", "json"],              # without the dot, in case matching differs
    [""],                                # an empty suffix might mean everything
    [],                                  # ... and so might an empty list
]
#: It ignores a folder we pass under these names, or complains about one of them. Either tells us something.
PATH_PARAM_GUESSES = ["path", "dir", "directory", "folder", "root", "dirPath"]


def ask_files(link, params):
    reply = ask_json(link, "getFilesBySuffix", params)
    paths = ((reply or {}).get("result") or {}).get("filePaths")
    if isinstance(paths, list):
        print("  -> %d file(s)" % len(paths))
        for path in paths[:40]:
            # Waveshare's own demo shows each entry is {"filePath": ..., "crc": ...}, not a bare string.
            if isinstance(path, dict):
                print("       %-50s crc %s" % (path.get("filePath", "?"), path.get("crc", "?")))
            else:
                print("       %s" % path)
        if len(paths) > 40:
            print("       ... and %d more" % (len(paths) - 40))
    return reply, paths


def cmd_files(args):
    """Read-only: ask the device what files it can see, now that we know how to ask."""
    link = open_link(args)
    try:
        print("Read-only. getFilesBySuffix only lists paths; nothing here writes or deletes.")
        link.send_json("getInfo")
        link.wait_for(lambda p: p.proto == "v2" and p.cmd == CMD_JSON, 3.0)

        if args.suffix:
            ask_files(link, {"suffixs": list(args.suffix)})
        else:
            print("\n---- what does it find, by suffix? ----")
            for group in FILE_SUFFIX_SWEEP:
                ask_files(link, {"suffixs": group})

        folders = list(args.path or [])
        if folders:
            print("\n---- does it take a folder? (the error messages are the point) ----")
            suffixes = list(args.suffix or []) or [".jpg", ".png", ".json"]
            for folder in folders:
                for name in PATH_PARAM_GUESSES:
                    _reply, paths = ask_files(link, {"suffixs": suffixes, name: folder})
                    if paths:
                        print("\n  %s=%r found something. That's the folder parameter." % (name, folder))
                        break
        print("\nDone. An empty list every time means it only looks where it wants to.")
    finally:
        link.close()
    return 0


def cmd_pixmap(args):
    """Try SEND_PIXMAP: the one lead for updating part of the display instead of all of it."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        sys.exit("This needs Pillow:  py -m pip install pillow")
    link = open_link(args)
    try:
        info = v1_handshake(link)
        if info is None:
            print("No answer to the old-format handshake. Try 'probe' first.")
            return 1
        print("Device: %s, firmware %s" % (info.get("screen_model", "?"), info.get("version", "?")))

        names = list(args.name or [])
        if not names:
            theme = ask_v1(link, V1_GET_THEME, "GET_THEME (what images does it know about?)") or {}
            names = [k for k in theme if k not in ("bytesTotal", "bytesAvailable")][:6]
            if names:
                print("\n  Trying the names GET_THEME returned.")
            else:
                names = ["0", "1", "key0", "0.jpg", "/mnt/SDCARD/0.jpg"]
                print("\n  GET_THEME listed nothing, so falling back to guesses.")

        picture = make_test_pattern(args.width, args.height, tile=min(args.width, args.height))
        print("\nSending a %dx%d picture (%d bytes) under each name in turn." % (
            args.width, args.height, len(picture)))
        print("WATCH THE DEVICE. Anything that changes is the answer; nothing changing is also an answer.")
        for name in names:
            ask_v1(link, 12, "SEND_PIXMAP {%r: <%d bytes>}" % (name, len(picture)),
                   payload=qmap_bytes_encode({name: picture}))
            time.sleep(args.pause)
        print("\n---- SEND_PIXMAP with nothing in it, in case the error is informative ----")
        ask_v1(link, 12, "SEND_PIXMAP {}", payload=qmap_bytes_encode({}))

        print("\nIf the display is now wrong, start MK10 Manager (or run show-test) to repaint it.")
        print("Nothing above wrote a file: SEND_PIXMAP moves pixels, not storage.")
    finally:
        link.close()
    return 0


# ---------------------------------------------------------------- mining the firmware for the theme format

#: Words the firmware's theme parser is known to use, from the earlier static read. Anything near these is
#: likely part of the same vocabulary.
THEME_ANCHORS = ("Theme", "theme", "OriginPoint", "QGraphicsPixmapItem", "rotate", "scale", "pivot",
                 "widget", "Widget", "clock", "Clock", "progress", "Progress", "dial", "hand",
                 "background", "pixmap", "font", "colour", "color", "layer", "item", "page")

#: Things that look like JSON keys in a config: short, wordy, no spaces or punctuation.
JSON_KEY = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]{2,39}$")
#: A C++ type name as the compiler writes it in a mangled symbol: a length then that many characters.
MANGLED_NAME = __import__("re").compile(r"^(\d{1,2})([A-Za-z_][A-Za-z0-9_]*)$")


def is_symbol(text):
    """Mangled C++ symbols are noise when you're looking for a config format."""
    return text.startswith(("_Z", "_GLOBAL__")) or "::" in text


def widget_catalogue(by_text):
    """C++ class names appear length-prefixed ('10FlipClocks'). Those classes are the theme's widgets."""
    names = set()
    for text in by_text:
        m = MANGLED_NAME.match(text)
        if m and len(m.group(2)) == int(m.group(1)):
            names.add(m.group(2))
    return sorted(names)


def extract_strings(blob, min_len=4):
    """ASCII and UTF-16LE strings, the way the `strings` tool does it, plus their offsets."""
    out = []
    run, start = bytearray(), 0
    for i, byte in enumerate(blob):
        if 32 <= byte < 127:
            if not run:
                start = i
            run.append(byte)
            continue
        if len(run) >= min_len:
            out.append((start, run.decode("ascii")))
        run = bytearray()
    if len(run) >= min_len:
        out.append((start, run.decode("ascii")))
    return out


def theme_report(prog, show_all=False):
    found = extract_strings(prog)
    by_text = {}
    for offset, text in found:
        by_text.setdefault(text, offset)

    print("Program is %d bytes, %d distinct strings." % (len(prog), len(by_text)))

    urls = sorted({t for t in by_text if t.startswith(("http://", "https://", "ftp://")) and len(t) > 12})
    hosts = sorted({t for t in by_text
                    if __import__("re").fullmatch(r"[a-z0-9][a-z0-9.-]{4,60}\.(com|cn|net|org|io)", t)})
    print("\n== Addresses it talks to (%d urls, %d bare hostnames)" % (len(urls), len(hosts)))
    for t in urls[:40]:
        print("  %s" % t)
    for t in hosts[:40]:
        print("  (host) %s" % t)
    if not urls and not hosts:
        print("  (none found)")

    paths = sorted(t for t in by_text if t.startswith("/") and len(t) > 6 and " " not in t)
    interesting = [t for t in paths if any(w in t for w in ("theme", "Theme", "SDCARD", "data/", "config"))]
    print("\n== Paths that mention a theme, the card or config (%d)" % len(interesting))
    for t in interesting[:60]:
        print("  %s" % t)

    hits = sorted(t for t in by_text if any(a in t for a in THEME_ANCHORS) and len(t) < 60)
    print("\n== Strings containing a theme word (%d)" % len(hits))
    for t in hits[:200 if show_all else 80]:
        print("  %s" % t)

    keys = sorted(t for t in by_text
                  if JSON_KEY.match(t) and not is_symbol(t) and any(a in t for a in THEME_ANCHORS))
    print("\n== Of those, the ones shaped like JSON keys (%d)" % len(keys))
    print("  " + "\n  ".join(keys[:120]))

    widgets = widget_catalogue(by_text)
    print("\n== Classes the program defines (%d) — the widget types a theme can contain" % len(widgets))
    for t in widgets[:120]:
        print("  %s" % t)
    print("\nIf a group above looks like the theme vocabulary, that is what a .Theme file is made of.")


def cmd_theme_strings(args):
    """Read a ScreenKey installer or the device program and print everything that looks theme-shaped."""
    try:
        blob = pathlib_read(args.file)
    except OSError as e:
        sys.exit("Couldn't read %s: %s" % (args.file, e))
    prog = blob
    found = find_firmware_in_installer(blob)
    if found:
        version, prog = found
        print("Found the device program inside that installer: %s, %d bytes.\n" % (version, len(prog)))
    elif blob[:4] == b"\x7fELF":
        print("That looks like the device program itself.\n")
    else:
        print("Not an installer we recognise and not an ELF; reading it as raw bytes anyway.\n")
    theme_report(prog, args.all)
    if prog is not blob and len(blob) > len(prog):
        print("\n---- and now the installer as a whole, for addresses the Windows app uses ----")
        outer = extract_strings(blob, min_len=12)
        urls = sorted({t for _o, t in outer if t.startswith(("http://", "https://")) and len(t) > 14})
        print("== %d url(s) anywhere in the installer" % len(urls))
        for t in urls[:60]:
            print("  %s" % t)
        if len(urls) > 60:
            print("  ... and %d more" % (len(urls) - 60))

    found_zips = embedded_zips(blob)
    if found_zips:
        print("\n== %d complete archive(s) inside the file" % len(found_zips))
        for start, data in found_zips:
            print("  at offset %d, %d bytes" % (start, len(data)))
        print("  Run 'unpack' on the same file to list and extract what's in them.")
    return 0


def embedded_zips(blob, limit=40):
    """Find whole zip archives inside a bigger file, by their end-of-central-directory records."""
    import io
    import zipfile
    out = []
    for m in __import__("re").finditer(rb"PK\x05\x06", blob):
        pos = m.start()
        if pos + 22 > len(blob):
            continue
        cd_size, cd_offset = struct.unpack_from("<II", blob, pos + 12)
        (comment,) = struct.unpack_from("<H", blob, pos + 20)
        start = pos - cd_size - cd_offset
        if start < 0 or cd_size == 0:
            continue
        data = bytes(blob[start:pos + 22 + comment])
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.namelist()
        except Exception:
            continue
        out.append((start, data))
        if len(out) >= limit:
            break
    return out


def safe_member(name):
    """Never let an archive write outside where we told it to."""
    import posixpath
    clean = name.replace("\\", "/")
    if clean.startswith("/") or ".." in clean.split("/"):
        return None
    return posixpath.normpath(clean).lstrip("./") or None


#: Words that would appear inside a theme file but not in ordinary installer padding. The Chinese ones are
#: "default theme" and "Waveshare", which is how the stock theme folder is named.
THEME_MARKERS = [b"OriginPoint_ax", b"item_offset", b"pageData", b"pageSwitchMode", b"scaledWidthTo",
                 b"lineargradient_color", b"FlipClock", b"CircularProgressBar",
                 b"\xe9\xbb\x98\xe8\xae\xa4\xe4\xb8\xbb\xe9\xa2\x98", b"\xe5\xbe\xae\xe9\x9b\xaa"]


def json_object_at(blob, start, limit=4 * 1024 * 1024):
    """Walk a brace-balanced object from `start`, respecting strings and escapes. Returns its end, or None."""
    if start >= len(blob) or blob[start:start + 1] != b"{":
        return None
    depth, i, in_string, escaped = 0, start, False, False
    stop = min(len(blob), start + limit)
    while i < stop:
        c = blob[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == 0x5C:                      # backslash
                escaped = True
            elif c == 0x22:                      # quote
                in_string = False
        elif c == 0x22:
            in_string = True
        elif c == 0x7B:                          # {
            depth += 1
        elif c == 0x7D:                          # }
            depth -= 1
            if depth == 0:
                return i + 1
        elif c == 0 or (c < 9 and c != 0):       # a NUL means we've run past the end of a text blob
            return None
        i += 1
    return None


def find_theme_json(blob, window=512 * 1024, tries=60, verbose=False):
    """Look for JSON objects that mention theme things. Installers often hold their payload uncompressed.

    Walks back from each marker to the nearest '{' and tries to parse a balanced object from there, then the
    one before that, and so on. rfind does the scanning, because a byte-at-a-time loop over 350 MB is hopeless.
    """
    import json as _json
    seen, out, counts = set(), [], {}
    for marker in THEME_MARKERS:
        hits = 0
        pos = blob.find(marker)
        while pos >= 0:
            hits += 1
            lo = max(0, pos - window)
            start = blob.rfind(b"{", lo, pos + 1)
            for _ in range(tries):
                if start < 0:
                    break
                end = json_object_at(blob, start)
                if end is not None and end > pos and (start, end) not in seen:
                    seen.add((start, end))
                    try:
                        data = _json.loads(blob[start:end].decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        data = None
                    if isinstance(data, dict) and data:
                        out.append((start, end, data))
                        break
                start = blob.rfind(b"{", lo, start)
            pos = blob.find(marker, pos + 1)
            if hits >= 4000:                    # a marker in the firmware's symbol table repeats endlessly
                break
        counts[marker] = hits
        if verbose:
            name = marker.decode("utf-8", "replace")
            print("  %-24s %d hit(s)" % (name, hits))
    return sorted(out), counts


def cmd_theme_dig(args):
    """Offline: hunt for the theme file itself inside an installer, as plain JSON."""
    try:
        blob = pathlib_read(args.file)
    except OSError as e:
        sys.exit("Couldn't read %s: %s" % (args.file, e))
    print("Searching %d bytes for JSON that mentions theme things." % len(blob))
    print("On a few hundred MB this takes a minute or two.\n")
    found, counts = find_theme_json(blob, verbose=True)
    dot_theme = blob.count(b".Theme") + blob.count(b".theme")
    print("\n  %-24s %d hit(s)" % ("the text '.Theme'", dot_theme))
    if not found:
        print("\nMarkers were there but no JSON object parsed around any of them."
              if any(counts.values()) else "\nNone of the theme markers appear in that file at all.")
        print("That usually means the theme is packed rather than stored as text. This installer is wrapped")
        print("with Enigma (see its .enigma1/.enigma2 sections), which 7-Zip cannot open either.")
        return 1
    out_root = None
    if args.out:
        import pathlib as _p
        out_root = _p.Path(args.out)
        out_root.mkdir(parents=True, exist_ok=True)
    print("\nFound %d JSON object(s):" % len(found))
    for n, (start, end, data) in enumerate(found, 1):
        keys = ", ".join(sorted(data)[:12])
        print("\n== %d  offset %d, %d bytes" % (n, start, end - start))
        print("   top-level keys: %s" % (keys or "(none)"))
        preview = __import__("json").dumps(data, ensure_ascii=False)[:400]
        print("   %s%s" % (preview, "..." if len(preview) == 400 else ""))
        if out_root is not None:
            dest = out_root / ("theme-candidate-%d.json" % n)
            dest.write_text(__import__("json").dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            print("   saved to %s" % dest)
    if out_root is None:
        print("\nPass --out FOLDER to save them.")
    return 0


def cmd_unpack(args):
    """Offline: list, and optionally extract, the archives packed inside a ScreenKey installer."""
    import io
    import zipfile
    try:
        blob = pathlib_read(args.file)
    except OSError as e:
        sys.exit("Couldn't read %s: %s" % (args.file, e))
    found_zips = embedded_zips(blob)
    if not found_zips:
        print("No complete zip archive found inside that file.")
        print("NSIS and Inno installers aren't zips; 7-Zip can usually still open them.")
        return 1
    print("Found %d archive(s)." % len(found_zips))
    out_root = None
    if args.out:
        import pathlib as _p
        out_root = _p.Path(args.out)
        if out_root.exists() and any(out_root.iterdir()):
            sys.exit("%s already has files in it. Give me an empty folder." % out_root)
        out_root.mkdir(parents=True, exist_ok=True)
    for n, (start, data) in enumerate(found_zips, 1):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            print("\n== Archive %d at offset %d: %d entries" % (n, start, len(names)))
            interesting = [x for x in names if any(w in x.lower()
                                                   for w in ("theme", "font", ".json", ".jpg", ".png"))]
            for name in (interesting or names)[:60]:
                print("   %s" % name)
            if len(interesting or names) > 60:
                print("   ... and %d more" % (len(interesting or names) - 60))
            if out_root is None:
                continue
            target = out_root / ("archive%d" % n)
            target.mkdir(parents=True, exist_ok=True)
            written = 0
            for info in z.infolist():
                if info.is_dir():
                    continue
                clean = safe_member(info.filename)
                if clean is None:
                    print("   skipped an entry that pointed outside: %s" % info.filename)
                    continue
                dest = target / clean
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as dst:
                    dst.write(src.read(64 * 1024 * 1024))
                written += 1
            print("   extracted %d file(s) to %s" % (written, target))
    if out_root is None:
        print("\nPass --out FOLDER (an empty one) to extract them.")
    return 0


def pathlib_read(path):
    import pathlib as _p
    return _p.Path(path).read_bytes()


def cmd_selftest(_args):
    ok = True

    def check(name, cond):
        nonlocal ok
        print("  %s  %s" % ("PASS" if cond else "FAIL", name))
        ok = ok and cond

    print("Packet self-test (no device needed)")
    check("CRC-32 matches the standard check value", crc32(b"123456789") == 0xCBF43926)

    payload = b'{"method":"getInfo","parameters":{}}'
    f = build_frame(7, CMD_JSON, payload)
    expected = (bytes.fromhex("a1a55a5e") + struct.pack("<III", 7, 101, len(payload))
                + struct.pack("<I", zlib.crc32(struct.pack("<I", len(payload)))) + payload
                + struct.pack("<I", zlib.crc32(payload)))
    check("new-format frame matches the firmware's packet builder", f == expected)

    p = FrameParser()
    check("parser reads back one new-format packet", p.feed(f) == [Packet("v2", 7, None, CMD_JSON, payload)])

    m = {"version": "V2.32", "a": ""}
    enc = qmap_encode(m)
    check("Qt map encoding round-trips", qmap_decode(enc) == m)
    check("Qt map encoding: empty map is 4 zero bytes", qmap_encode({}) == b"\0\0\0\0")
    v1 = build_frame_v1(V1_GET_VERSION, enc, 2)
    check("old-format frame layout", v1[:22] == MAGIC_V1 and struct.unpack_from("<IIII", v1, 22)
          == (2, 8, len(enc), zlib.crc32(enc)))
    p = FrameParser()
    check("parser reads back one old-format packet", p.feed(v1) == [Packet("v1", None, 2, 8, enc)])

    stream = b"AT\r\n" + f + v1 + build_frame(8, CMD_JSON, b"{}") + b"\x00garbage"
    p = FrameParser()
    got = []
    for i in range(len(stream)):            # one byte at a time, like a slow USB read
        got += p.feed(stream[i:i + 1])
    check("parser handles mixed formats, junk and 1-byte chunks",
          [(g.proto, g.cmd) for g in got] == [("v2", 101), ("v1", 8), ("v2", 101)])
    check("parser keeps unframed bytes for display", p.take_junk().startswith(b"AT\r\n"))

    bad = bytearray(f)
    bad[-1] ^= 0xFF
    p = FrameParser()
    got = p.feed(bytes(bad) + build_frame(9, CMD_JSON, b"{}"))
    check("parser drops a corrupt packet and keeps going", [g.seq for g in got] == [9] and p.bad_frames == 1)

    # A real key packet captured from an MK10 (firmware V2.29): row 1, col 4, pressed.
    real = bytes.fromhex(
        "4141353531323334204649584544434d444845414420020000000d0000008f01000043becc02"
        "00000002000000040000000800740079007000650000000a0000000010006b00650079005300"
        "74006100740065000000060072006f00770000000200000000010000000e0070007200650073"
        "007300650064000000020000000001000000060063006f006c00000002000000000400000005"
        "0000000800740079007000650000000a0000000014006f006e0065004c006500760065006c00"
        "550070000000220070006100720065006e0074004400650073006300720069007000740069006f"
        "006e0000000a00000000089875976252076362000000100070006100670065004e0061006d0065"
        "0000000a00000000140070006100720065006e007400500061006700650000001000690063006f"
        "006e00500061007400680000000a0000000040002f007300740061007400690063002f00690063"
        "006f006e002f006400610072006b002f006f006e0065004c006500760065006c00550070002e00"
        "70006e006700000016006400650073006300720069007000740069006f006e0000000a00000000"
        "0c8fd456de52304e0a4e005c42")
    got = FrameParser().feed(real)
    ke = key_event(got[0]) if got else None
    check("decodes a real MK10 key packet (row 1, col 4, pressed)",
          ke is not None and ke[:3] == (1, 4, True) and ke[3].get("type") == "oneLevelUp")

    items = [{"type": "keyState", "row": 1, "col": 3, "pressed": 1}, {"type": "openWeb", "Url": "x"}]
    check("Qt variant-map encoding round-trips", qvariant_maps_decode(qvariant_maps_encode(items)) == items)

    pixmap = {"a.jpg": b"\xff\xd8\x00bin", "b": b""}
    check("QMap<QString,QByteArray> round-trips (SEND_PIXMAP's payload)",
          qmap_bytes_decode(qmap_bytes_encode(pixmap)) == pixmap)
    check("an empty name map round-trips", qmap_bytes_decode(qmap_bytes_encode({})) == {})
    check("rubbish isn't mistaken for a name map", qmap_bytes_decode(b"not a map at all") is None)
    check("method names come out of the firmware's error",
          method_list_from_error("Unknown method: x. Available methods: [getInfo, setBacklight, playAudio]")
          == ["getInfo", "setBacklight", "playAudio"])
    check("a useless error yields no method names", method_list_from_error("") == [])
    check("the firmware's complaint names the parameter it wanted",
          missing_parameter_from_error("Missing 'suffixs' parameter") == "suffixs")
    check("a complaint about something else names nothing",
          missing_parameter_from_error("Incorrect resolution") is None)

    fake_prog = (b"\x7fELF\x01\x01\x01" + b"\0" * 11 + struct.pack("<H", 40) + b"\0" * 32
                 + b"/lib/ld-linux-armhf.so.3\0" + b"FIXEDCMDHEAD V2.99 V2.99" + b"\0" * 300_000)
    manifest = b'{\r\n\t"version": "V2.99",\r\n\t"fileMessage": [{"name": "KeyboardDevice"}]\r\n}\x00'
    blob = b"MZ" + b"\x11" * 5000 + manifest + struct.pack(">I", len(fake_prog)) + fake_prog + b"\x00\x00\x01\xb0tail"
    found = find_firmware_in_installer(blob)
    check("finds the device program inside an installer", found is not None and found[0] == "V2.99"
          and found[1] == fake_prog)
    check("program sanity checks accept an ARM hard-float program", check_program(fake_prog) == [])
    check("program sanity checks reject other files", check_program(b"MZ" + b"\0" * 100) != [])
    check("version comparison", version_tuple("V2.32") > version_tuple("V2.29"))

    try:
        jpg = make_test_pattern(640, 256)
        check("test pattern builds at 640x256", jpeg_size(jpg) == (640, 256))
    except ImportError:
        print("  SKIP  test pattern (Pillow not installed; only needed for show-test)")

    print("\nAll good." if ok else "\nSomething failed. Send me this output.")
    return 0 if ok else 1


# ---------------------------------------------------------------- simulated MK10

SIM_METHODS = ["getInfo", "setBacklight", "setVolume", "saveToFile", "setFileCRC",
               "deleteFiles", "getFilesBySuffix", "playAudio", "stopAudio", "keyboardInput"]


class SimulatedMK10(threading.Thread):
    """Behaves like the decoded firmware, as far as the static read shows. Not the real thing.
    v1_only=True imitates an older firmware that only speaks the old format."""

    def __init__(self, fd, width=896, height=304, v1_only=False, free_mb=120, fail=()):
        super().__init__(daemon=True)
        self.fd, self.w, self.h, self.v1_only = fd, width, height, v1_only
        self.parser = FrameParser()
        self.stop = threading.Event()
        self.connected = False
        self.version = "V2.29" if v1_only else "V2.32"
        self.free_mb, self.fail = free_mb, set(fail)
        self.app_path = "/data/KeyboardDevice"
        self.remaining, self.file_path, self.file_buf, self.last_crc = 0, None, bytearray(), 0
        self.files = {}
        self.installed = None

    def reply(self, seq, obj):
        os.write(self.fd, build_frame(seq, CMD_JSON, json.dumps(obj).encode()))

    def ack(self, cmd, fields):
        os.write(self.fd, build_frame_v1(cmd, qmap_encode(fields) if fields is not None else b"", 2))

    def handle(self, pkt):
        if pkt.proto == "v1":
            if pkt.ptype != 0:
                return
            m = qmap_decode(pkt.payload) or {}
            first = sorted(m.items())[0] if m else (None, None)       # QMap begin() = smallest key
            if pkt.cmd == V1_FIND_DEVICE:
                self.connected = True
                self.ack(pkt.cmd, {"version": self.version, "upgradeToLatestMethod": "1",
                                   "screen_width": "896", "screen_height": "304", "screen_model": "MK10",
                                   "deviceName": "ScreenKey", "deviceBl": "93", "deviceVolume": "7"})
            elif pkt.cmd == V1_GET_VERSION:
                self.ack(pkt.cmd, {"version": self.version})
            elif pkt.cmd == V1_GET_THEME:
                fields = {"bytesTotal": "120", "bytesAvailable": str(self.free_mb)}
                fields.update({k: v for k, v in self.files.items()})
                self.ack(pkt.cmd, fields)
            elif pkt.cmd == V1_FILE_START:
                if "no_start_ack" in self.fail:
                    return
                self.file_path, self.remaining, self.file_buf = first[0], int(first[1]), bytearray()
                self.ack(pkt.cmd, None)
            elif pkt.cmd == V1_FILE_END:
                path, value = first
                ok = (str(self.last_crc) == value) and self.last_crc != 0 and "bad_install" not in self.fail
                if ok and "KeyboardDevice" in (path or ""):
                    if path != self.app_path:
                        self.installed = bytes(self.file_buf)
                        self.version = program_version(self.installed) or self.version
                if ok:
                    self.files[path] = value
                self.ack(pkt.cmd, {"res": "1" if ok else "0", "fileName": path or ""})
            else:
                self.ack(pkt.cmd, {})
            return
        if self.v1_only:
            return
        if pkt.cmd == CMD_JSON:
            req = json.loads(pkt.payload)
            m = req.get("method")
            if m == "getInfo":
                self.connected = True
                rects = [{"row": r, "col": c, "x": c * 128, "y": r * 128, "width": 128, "height": 128,
                          "isKey": True} for r in range(2) for c in range(5)]
                rects.append({"x": 656, "y": 0, "width": 240, "height": 296, "isKey": False})
                self.reply(pkt.seq, {"ack_method": m, "success": True, "result": {
                    "deviceModel": "MK10 (SIMULATED)", "deviceVersion": "V2.32",
                    "deviceWidth": self.w, "deviceHeight": self.h,
                    "devicePanel": {"rectCols": 5, "rectRows": 2, "rects": rects,
                                    "screenWidth": self.w, "screenHeight": self.h}}})
            elif m == "setBacklight":
                self.reply(pkt.seq, {"ack_method": m, "success": True,
                                     "result": {"level": req["parameters"].get("level")}})
            else:
                self.reply(pkt.seq, {"ack_method": m, "success": False,
                                     "errorString": "Unknown method: %s. Available methods: [%s]"
                                     % (m, ", ".join(SIM_METHODS))})
        elif pkt.cmd == CMD_SHOW_JPG:
            if jpeg_size(pkt.payload) != (self.w, self.h):
                self.reply(0xFFFFFFFF, {"success": False, "errorString":
                                        "Incorrect resolution, the device requires the resolution to be %d*%d"
                                        % (self.w, self.h)})
            else:
                self.reply(pkt.seq, {"success": True})

    def run(self):
        presses = [(0, 0), (0, 4), (1, 2)]
        next_press = None
        while not self.stop.is_set():
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                data = b""
            except OSError:
                return
            if not data:
                time.sleep(0.01)
            # Mimic the firmware's readyRead(): abort marker, then raw file bytes, then packets.
            if data.startswith(ABORT_TRANSFER):
                self.remaining = 0
            elif self.remaining > 0 and data:
                take = data[:self.remaining]
                self.file_buf += take
                self.remaining -= len(take)
                data = data[len(take):]
                if self.remaining == 0:
                    self.last_crc = crc32(bytes(self.file_buf))
            for pkt in self.parser.feed(data):
                self.handle(pkt)
            if self.connected and next_press is None:
                next_press = time.time() + 1.0
            if next_press and presses and time.time() >= next_press:
                r, c = presses.pop(0)
                if self.v1_only:       # older firmware: QVariant maps in cmd 13, like the real V2.29 log
                    for pressed in (True, False):
                        os.write(self.fd, build_frame_v1(13, qvariant_maps_encode(
                            [{"type": "keyState", "row": r, "col": c, "pressed": int(pressed)},
                             {"type": "openWeb", "Url": "example.com"}]), 2))
                    next_press = time.time() + 0.8
                    continue
                for pressed in (True, False):
                    os.write(self.fd, build_frame(0, CMD_JSON, json.dumps(
                        {"method": "keyStateChanged",
                         "parameters": {"row": r, "col": c, "pressed": pressed}}).encode()))
                next_press = time.time() + 0.8


def cmd_demo(args):
    if not hasattr(os, "openpty"):
        sys.exit("demo needs Linux or macOS.")
    import tty
    master, slave = os.openpty()
    tty.setraw(slave)
    os.set_blocking(master, False)
    sim = SimulatedMK10(master, v1_only=args.old_firmware)
    sim.start()
    print("*** SIMULATED MK10%s. This shows what a session could look like. ***\n"
          % (" (older firmware)" if args.old_firmware else ""))
    link = Link(os.ttyname(slave), Log(args.log))
    try:
        run_probe(link, 4)
        if not args.old_firmware:
            try:
                import PIL  # noqa: F401
                print("\nBonus: test-pattern push against the simulator")
                link.send_frame(CMD_SHOW_JPG, make_test_pattern(640, 256))
                show_reply("wrong-size image (should be refused)",
                           link.wait_for(lambda p: p.seq == 0xFFFFFFFF, on_other=print_event))
                seq = link.send_frame(CMD_SHOW_JPG, make_test_pattern(896, 304))
                show_reply("right-size image", link.wait_for(lambda p: p.seq == seq, on_other=print_event))
            except ImportError:
                pass
    finally:
        sim.stop.set()
        link.close()
    return 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Waveshare MK10 first-contact tool (unofficial).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--port", help="serial port, e.g. COM6 or /dev/ttyACM0")
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        p.add_argument("--log", default="mk10probe-%s.log" % stamp,
                       help="log file (default: mk10probe-<time>.log)")
        p.add_argument("--no-dtr", action="store_true", help="open the port without asserting DTR/RTS")

    sub.add_parser("selftest", help="check the packet code, no device needed")
    p = sub.add_parser("demo", help="run against a simulated MK10, no device needed")
    p.add_argument("--log", default=None)
    p.add_argument("--old-firmware", action="store_true", help="simulate firmware that only speaks the old format")
    sub.add_parser("list", help="list serial ports and USB IDs")
    p = sub.add_parser("probe", help="read-only first contact")
    common(p)
    p.add_argument("--seconds", type=int, default=0, help="listen time; 0 = until Ctrl+C")
    p = sub.add_parser("backlight", help="set brightness 0-100")
    common(p)
    p.add_argument("level", type=int)
    p = sub.add_parser("show-test", help="push a test pattern (needs Pillow)")
    common(p)
    p.add_argument("--width", type=int, default=640, help="used only if getInfo has no size")
    p.add_argument("--height", type=int, default=256)
    p = sub.add_parser("storage", help="read-only: firmware version and free space")
    common(p)
    p = sub.add_parser("send-test", help="rehearsal: two tiny harmless file transfers")
    common(p)
    p = sub.add_parser("explore", help="read-only: list the firmware's methods and try the read commands")
    common(p)
    p.add_argument("--try-volume", action="store_true",
                   help="also try setVolume's parameter names (changes the device speaker, nothing else)")
    p.add_argument("--force-file-list", action="store_true",
                   help="try getFilesBySuffix even if the method list didn't mention it")
    p = sub.add_parser("theme-strings", help="offline: mine a ScreenKey installer or the device program "
                                             "for the theme format (no device needed)")
    p.add_argument("file", help="the ScreenKey installer .exe, or an extracted KeyboardDevice")
    p.add_argument("--all", action="store_true", help="print more of each group")
    p = sub.add_parser("theme-dig", help="offline: hunt for the theme file itself inside an installer")
    p.add_argument("file", help="the ScreenKey installer .exe")
    p.add_argument("--out", help="folder to save what it finds")
    p = sub.add_parser("unpack", help="offline: list or extract archives packed inside an installer")
    p.add_argument("file", help="the ScreenKey installer .exe")
    p.add_argument("--out", help="empty folder to extract into")
    p = sub.add_parser("files", help="read-only: ask the device which files it can see")
    common(p)
    p.add_argument("--suffix", action="append", help="suffix to ask for (repeatable); default: a sweep")
    p.add_argument("--path", action="append", help="folder to try passing as well (repeatable)")
    p = sub.add_parser("pixmap", help="experiment: can SEND_PIXMAP update part of the display?")
    common(p)
    p.add_argument("--name", action="append", help="image name to try (repeatable); default: ask GET_THEME")
    p.add_argument("--width", type=int, default=128)
    p.add_argument("--height", type=int, default=128)
    p.add_argument("--pause", type=float, default=1.5, help="seconds between attempts, to watch the screen")
    p = sub.add_parser("update", help="send a firmware update (asks before changing anything)")
    common(p)
    p.add_argument("file", help="the KeyboardDevice program file, or the ScreenKey installer .exe to extract it from")
    p.add_argument("--force", action="store_true", help="send even if the device already has this version or newer")
    p.add_argument("--skip-space-check", action="store_true", help="send even if free space can't be read")

    args = ap.parse_args()
    try:                                   # don't crash on consoles that can't show Chinese text
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if args.cmd not in ("selftest", "theme-strings", "unpack", "theme-dig"):
        try:
            import serial  # noqa: F401
        except ImportError:
            sys.exit("This needs pyserial:  py -m pip install pyserial   (Linux: pip install pyserial)")
    handler = {"selftest": cmd_selftest, "demo": cmd_demo, "list": cmd_list, "probe": cmd_probe,
               "backlight": cmd_backlight, "show-test": cmd_show_test, "storage": cmd_storage,
               "send-test": cmd_send_test, "update": cmd_update, "explore": cmd_explore,
               "pixmap": cmd_pixmap, "files": cmd_files, "theme-strings": cmd_theme_strings,
               "unpack": cmd_unpack, "theme-dig": cmd_theme_dig}[args.cmd]
    sys.exit(handler(args))


if __name__ == "__main__":
    main()
