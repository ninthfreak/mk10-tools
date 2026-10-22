"""System audio: what the speakers and microphone are doing, and changing it.

Windows talks to Core Audio through ctypes (no extra packages). Linux uses PipeWire's or PulseAudio's `pactl`,
with `parec` for the live level meters. Reading happens on AudioMonitor's own thread; the GUI side reads
`monitor.state` and listens to `changed`.

Live levels are only measured while something on screen asks for them (`want_levels`). On Linux that matters:
measuring the microphone level means opening a recording stream, so it stays closed otherwise. On Windows the
meters come from the audio engine and record nothing.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace

from PySide6.QtCore import QObject, Signal

OUTPUT, INPUT = "output", "input"
TARGETS = [(OUTPUT, "Speakers (output)"), (INPUT, "Microphone (input)")]
MODES = [("toggle_mute", "Toggle mute"), ("mute", "Mute"), ("unmute", "Unmute"),
         ("volume_up", "Volume up"), ("volume_down", "Volume down"), ("set_volume", "Set the volume")]
IS_WIN = sys.platform.startswith("win")


@dataclass(frozen=True)
class Endpoint:
    """One side of the sound card. `volume` is 0-100; `level` is the live loudness, 0.0-1.0."""
    name: str = ""
    muted: bool | None = None
    volume: int | None = None
    level: float = 0.0

    @property
    def known(self) -> bool:
        return self.muted is not None or self.volume is not None


@dataclass(frozen=True)
class AudioState:
    output: Endpoint = field(default_factory=Endpoint)
    input: Endpoint = field(default_factory=Endpoint)
    available: bool = False
    note: str = ""

    def side(self, target: str) -> Endpoint:
        return self.input if target == INPUT else self.output


def clamp_volume(v) -> int:
    return max(0, min(100, int(round(float(v)))))


class _VolumeMath:
    """Shared mode handling: works out the new mute and volume from the current state."""

    def _new_values(self, cur: Endpoint, mode: str, value: int):
        """-> (mute or None, volume or None). None means 'leave this alone'."""
        if mode == "toggle_mute":
            return (not cur.muted) if cur.muted is not None else True, None
        if mode == "mute":
            return True, None
        if mode == "unmute":
            return False, None
        if mode == "set_volume":
            return None, clamp_volume(value)
        if mode in ("volume_up", "volume_down"):
            base = cur.volume if cur.volume is not None else 50
            return None, clamp_volume(base + (value if mode == "volume_up" else -value))
        raise ValueError("Unknown audio action %s" % mode)


class NullAudio:
    """No sound control here (an unsupported system, or switched off)."""
    note = "Sound control isn't available on this system."

    def read(self, want_levels=frozenset()) -> AudioState:
        return AudioState(available=False, note=self.note)

    def apply(self, target: str, mode: str, value: int = 0):
        return False, self.note

    def close(self):
        pass


# ---------------------------------------------------------------------------- Linux
PACTL = {OUTPUT: ("sink", "@DEFAULT_SINK@"), INPUT: ("source", "@DEFAULT_SOURCE@")}
_VOL_RE = re.compile(r"(\d+)%")


def _run(cmd, timeout=3):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or "exit code %d" % r.returncode).strip()[:200]
    return r.stdout, ""


class LinuxAudio(_VolumeMath):
    """PipeWire or PulseAudio through pactl, with parec for the live levels (one process per side)."""

    NAME_REFRESH = 5.0          # listing devices is slow, so friendly names are re-read only this often

    def __init__(self):
        self.pactl = shutil.which("pactl")
        self.parec = shutil.which("parec")
        self._readers: dict[str, _ParecReader] = {}
        self._devices: dict[str, tuple[str, str]] = {}       # target -> (device id, friendly name)
        self._devices_next = 0.0

    @property
    def note(self) -> str:
        if not self.pactl:
            return ("pactl wasn't found, so the sound state can't be read. Install pulseaudio-utils "
                    "(it works with PipeWire too).")
        if not self.parec:
            return "parec wasn't found, so the live level meters stay empty. Install pulseaudio-utils."
        return ""

    def read(self, want_levels=frozenset()) -> AudioState:
        if not self.pactl:
            return AudioState(available=False, note=self.note)
        self._refresh_devices()
        sides = {}
        for target in (OUTPUT, INPUT):
            kind, fallback = PACTL[target]
            dev_id, friendly = self._devices.get(target, (fallback, ""))
            sides[target] = Endpoint(friendly or dev_id, self._muted(kind, dev_id), self._volume(kind, dev_id),
                                     self._level(target, dev_id, want_levels))
        return AudioState(sides[OUTPUT], sides[INPUT], True, self.note)

    def _muted(self, kind, dev):
        out, _ = _run([self.pactl, "get-%s-mute" % kind, dev])
        return None if out is None else ("yes" in out.lower())

    def _volume(self, kind, dev):
        out, _ = _run([self.pactl, "get-%s-volume" % kind, dev])
        if out is None:
            return None
        found = _VOL_RE.findall(out)
        return clamp_volume(found[0]) if found else None

    def _refresh_devices(self):
        if time.time() < self._devices_next:
            return
        self._devices_next = time.time() + self.NAME_REFRESH
        for target, (kind, fallback) in PACTL.items():
            out, _ = _run([self.pactl, "get-default-%s" % kind])
            dev_id = (out or "").strip() or fallback
            self._devices[target] = (dev_id, self._describe(kind, dev_id))

    def _describe(self, kind, dev_id) -> str:
        out, _ = _run([self.pactl, "list", "%ss" % kind], timeout=5)
        if not out:
            return ""
        current = None
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Name:"):
                current = s.split(":", 1)[1].strip()
            elif s.startswith("Description:") and current == dev_id:
                return s.split(":", 1)[1].strip()
        return ""

    def _level(self, target, dev_id, want_levels) -> float:
        reader = self._readers.get(target)
        if target not in want_levels or not self.parec:
            if reader:
                reader.stop()
                del self._readers[target]
            return 0.0
        source = dev_id + ".monitor" if target == OUTPUT else dev_id
        if reader and reader.source != source:
            reader.stop()
            reader = None
        if reader is None:
            reader = _ParecReader(self.parec, source)
            reader.start()
            self._readers[target] = reader
        return reader.peak()

    def apply(self, target, mode, value=0):
        if not self.pactl:
            return False, self.note
        kind, fallback = PACTL[target]
        dev_id = self._devices.get(target, (fallback, ""))[0]
        cur = Endpoint(muted=self._muted(kind, dev_id), volume=self._volume(kind, dev_id))
        mute, volume = self._new_values(cur, mode, value)
        if mute is not None:
            out, err = _run([self.pactl, "set-%s-mute" % kind, dev_id, "1" if mute else "0"])
            if out is None:
                return False, err
        if volume is not None:
            out, err = _run([self.pactl, "set-%s-volume" % kind, dev_id, "%d%%" % volume])
            if out is None:
                return False, err
        return True, ""

    def close(self):
        for reader in self._readers.values():
            reader.stop()
        self._readers.clear()


class _ParecReader:
    """Records quietly from one device and keeps the loudest sample since the last look."""

    RATE = 8000

    def __init__(self, parec: str, source: str):
        self.parec, self.source = parec, source
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._peak = 0.0
        self._lock = threading.Lock()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="mk10-level", daemon=True)
        self._thread.start()

    def _run(self):
        import array
        cmd = [self.parec, "--device=%s" % self.source, "--format=s16le", "--rate=%d" % self.RATE,
               "--channels=1", "--latency-msec=50", "--raw"]
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                          stdin=subprocess.DEVNULL)
        except OSError:
            return
        chunk = self.RATE // 20 * 2        # about 50 ms of 16-bit mono
        while not self._stop.is_set():
            try:
                data = self._proc.stdout.read(chunk)
            except (OSError, ValueError):
                break
            if not data:
                break
            samples = array.array("h")
            samples.frombytes(data[:len(data) // 2 * 2])
            loud = max((abs(s) for s in samples), default=0) / 32768.0
            with self._lock:
                self._peak = max(self._peak, loud)

    def peak(self) -> float:
        with self._lock:
            p, self._peak = self._peak, 0.0
        return p

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass


# ---------------------------------------------------------------------------- Windows
if IS_WIN:
    import ctypes
    from ctypes import POINTER, byref, c_float, c_int, c_long, c_void_p, c_wchar_p
    from ctypes.wintypes import DWORD, LPCWSTR

    _ole32 = ctypes.windll.ole32
    CLSCTX_ALL = 0x17
    COINIT_APARTMENTTHREADED = 0x2
    RPC_E_CHANGED_MODE = -2147417850
    VT_LPWSTR = 31

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", DWORD), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]

        def __init__(self, text=""):
            super().__init__()
            if text:
                _ole32.CLSIDFromString(LPCWSTR(text), byref(self))

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", DWORD)]

    class PROPVARIANT(ctypes.Structure):
        """Only the string case is read; the union is kept as a blob of the right size (16 bytes on x64)."""
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ubyte), ("r2", ctypes.c_ubyte),
                    ("r3", DWORD), ("blob", ctypes.c_byte * 16)]

        def text(self) -> str:
            if self.vt != VT_LPWSTR:
                return ""
            ptr = ctypes.cast(ctypes.addressof(self) + 8, POINTER(c_void_p)).contents.value
            return (ctypes.cast(ptr, c_wchar_p).value or "") if ptr else ""

    CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    IID_IAudioEndpointVolume = GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")
    IID_IAudioMeterInformation = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
    PKEY_Device_FriendlyName = PROPERTYKEY(GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)
    EDATAFLOW = {OUTPUT: 0, INPUT: 1}          # eRender, eCapture
    EROLE_CONSOLE = 0
    STGM_READ = 0


class Interface:
    """A COM interface pointer, called by vtable slot number. Slots count the three IUnknown methods first."""

    def __init__(self, ptr):
        self.ptr = ptr

    def __bool__(self):
        return bool(self.ptr)

    def call(self, slot: int, *typed_args) -> int:
        """typed_args are (ctype, value) pairs. Returns the HRESULT (negative means failure)."""
        vtable = ctypes.cast(self.ptr, POINTER(POINTER(c_void_p))).contents
        proto = ctypes.WINFUNCTYPE(c_long, c_void_p, *[t for t, _ in typed_args])
        return proto(vtable[slot])(self.ptr, *[v for _, v in typed_args])

    def release(self):
        if self.ptr:
            self.call(2)                        # IUnknown::Release
            self.ptr = None


# vtable slots, from the Core Audio headers
ENUM_GET_DEFAULT = 4            # IMMDeviceEnumerator::GetDefaultAudioEndpoint
DEV_ACTIVATE = 3                # IMMDevice::Activate
DEV_OPEN_PROPS = 4              # IMMDevice::OpenPropertyStore
PROPS_GET_VALUE = 5             # IPropertyStore::GetValue
VOL_SET_SCALAR = 7              # IAudioEndpointVolume::SetMasterVolumeLevelScalar
VOL_GET_SCALAR = 9              # IAudioEndpointVolume::GetMasterVolumeLevelScalar
VOL_SET_MUTE = 14               # IAudioEndpointVolume::SetMute
VOL_GET_MUTE = 15               # IAudioEndpointVolume::GetMute
METER_GET_PEAK = 3              # IAudioMeterInformation::GetPeakValue


class WindowsAudio(_VolumeMath):
    """Core Audio through ctypes. The peak meters come from the audio engine, so nothing is recorded."""

    NAME_REFRESH = 5.0

    def __init__(self):
        self._local = threading.local()          # COM is per-thread: read() and apply() run on different ones
        self._names: dict[str, str] = {}
        self._names_next = 0.0
        self.note = ""

    # ---- plumbing
    def _com_ready(self) -> bool:
        if getattr(self._local, "ready", False):
            return True
        hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        if hr < 0 and hr != RPC_E_CHANGED_MODE:
            self.note = "CoInitializeEx failed (0x%08X)" % (hr & 0xFFFFFFFF)
            return False
        self._local.ready = True
        return True

    def _enumerator(self) -> Interface | None:
        ptr = c_void_p()
        hr = _ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                     byref(IID_IMMDeviceEnumerator), byref(ptr))
        if hr < 0 or not ptr:
            self.note = "No audio device enumerator (0x%08X)" % (hr & 0xFFFFFFFF)
            return None
        return Interface(ptr)

    def _default_device(self, enum: Interface, target: str) -> Interface | None:
        ptr = c_void_p()
        hr = enum.call(ENUM_GET_DEFAULT, (c_int, EDATAFLOW[target]), (c_int, EROLE_CONSOLE),
                       (POINTER(c_void_p), byref(ptr)))
        return Interface(ptr) if hr >= 0 and ptr else None

    @staticmethod
    def _activate(device: Interface, iid) -> Interface | None:
        ptr = c_void_p()
        hr = device.call(DEV_ACTIVATE, (POINTER(GUID), byref(iid)), (DWORD, CLSCTX_ALL),
                         (c_void_p, None), (POINTER(c_void_p), byref(ptr)))
        return Interface(ptr) if hr >= 0 and ptr else None

    # ---- reading
    @staticmethod
    def _mute_and_volume(vol: Interface):
        muted, scalar = c_int(0), c_float(0.0)
        ok_mute = vol.call(VOL_GET_MUTE, (POINTER(c_int), byref(muted))) >= 0
        ok_vol = vol.call(VOL_GET_SCALAR, (POINTER(c_float), byref(scalar))) >= 0
        return (bool(muted.value) if ok_mute else None, clamp_volume(scalar.value * 100) if ok_vol else None)

    def _peak(self, device: Interface) -> float:
        meter = self._activate(device, IID_IAudioMeterInformation)
        if meter is None:
            return 0.0
        try:
            peak = c_float(0.0)
            if meter.call(METER_GET_PEAK, (POINTER(c_float), byref(peak))) < 0:
                return 0.0
            return max(0.0, min(1.0, float(peak.value)))
        finally:
            meter.release()

    def _friendly_name(self, device: Interface) -> str:
        props = c_void_p()
        if device.call(DEV_OPEN_PROPS, (DWORD, STGM_READ), (POINTER(c_void_p), byref(props))) < 0 or not props:
            return ""
        store = Interface(props)
        pv = PROPVARIANT()
        try:
            if store.call(PROPS_GET_VALUE, (POINTER(PROPERTYKEY), byref(PKEY_Device_FriendlyName)),
                          (POINTER(PROPVARIANT), byref(pv))) < 0:
                return ""
            return pv.text()
        finally:
            _ole32.PropVariantClear(byref(pv))      # the name is a COM-allocated string
            store.release()

    def read(self, want_levels=frozenset()) -> AudioState:
        if not self._com_ready():
            return AudioState(available=False, note=self.note)
        enum = self._enumerator()
        if enum is None:
            return AudioState(available=False, note=self.note)
        fresh_names = time.time() >= self._names_next
        if fresh_names:
            self._names_next = time.time() + self.NAME_REFRESH
        sides = {}
        try:
            for target in (OUTPUT, INPUT):
                device = self._default_device(enum, target)
                if device is None:
                    sides[target] = Endpoint()
                    self._names.pop(target, None)
                    continue
                try:
                    if fresh_names:
                        self._names[target] = self._friendly_name(device)
                    muted = volume = None
                    vol = self._activate(device, IID_IAudioEndpointVolume)
                    if vol is not None:
                        try:
                            muted, volume = self._mute_and_volume(vol)
                        finally:
                            vol.release()
                    level = self._peak(device) if target in want_levels else 0.0
                    sides[target] = Endpoint(self._names.get(target, ""), muted, volume, level)
                finally:
                    device.release()
        finally:
            enum.release()
        return AudioState(sides[OUTPUT], sides[INPUT], True, self.note)

    # ---- changing
    def apply(self, target, mode, value=0):
        if not self._com_ready():
            return False, self.note
        enum = self._enumerator()
        if enum is None:
            return False, self.note
        device = self._default_device(enum, target)
        enum.release()
        if device is None:
            return False, "There's no default %s device" % target
        try:
            vol = self._activate(device, IID_IAudioEndpointVolume)
            if vol is None:
                return False, "Couldn't open the volume control"
            try:
                muted, volume = self._mute_and_volume(vol)
                want_mute, want_volume = self._new_values(Endpoint(muted=muted, volume=volume), mode, value)
                if want_mute is not None:
                    if vol.call(VOL_SET_MUTE, (c_int, 1 if want_mute else 0), (c_void_p, None)) < 0:
                        return False, "Windows refused the mute change"
                if want_volume is not None:
                    if vol.call(VOL_SET_SCALAR, (c_float, c_float(want_volume / 100.0)), (c_void_p, None)) < 0:
                        return False, "Windows refused the volume change"
                return True, ""
            finally:
                vol.release()
        finally:
            device.release()

    def close(self):
        pass


# ---------------------------------------------------------------------------- picking one
def make_backend():
    if os.environ.get("MK10_NO_AUDIO"):
        return NullAudio()
    if IS_WIN:
        return WindowsAudio()
    if sys.platform.startswith("linux"):
        return LinuxAudio()
    return NullAudio()


def describe_backend(backend=None) -> str:
    backend = backend if backend is not None else make_backend()
    if isinstance(backend, NullAudio):
        return NullAudio.note
    if isinstance(backend, WindowsAudio):
        name = "Windows Core Audio"
    elif isinstance(backend, LinuxAudio):
        if not backend.pactl:
            return backend.note
        name = "pactl (PipeWire / PulseAudio)"
    else:
        name = type(backend).__name__
    note = getattr(backend, "note", "")
    return "%s. %s" % (name, note) if note else name


# ---------------------------------------------------------------------------- monitor
class AudioMonitor(QObject):
    """Reads the backend on its own thread. `state` is always safe to read from the GUI thread."""

    changed = Signal()

    SLOW = 0.5          # seconds between reads when nothing needs a live level
    FAST = 0.1          # ... and when something does
    DECAY = 0.72        # level meters fall back this fast, so speech looks like speech and not like noise

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self.backend = backend if backend is not None else make_backend()
        self.state = AudioState(note=getattr(self.backend, "note", ""))
        self._want: frozenset = frozenset()
        self._levels_on = True
        self._held = {OUTPUT: 0.0, INPUT: 0.0}
        self._stop = threading.Event()
        self._poke = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- GUI thread
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mk10-audio", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        self._stop.set()
        self._poke.set()
        if self._thread:
            self._thread.join(timeout)
        try:
            self.backend.close()
        except Exception:
            pass

    def want_levels(self, targets):
        """Which live levels the current page needs: a set of OUTPUT / INPUT."""
        new = frozenset(targets) if self._levels_on else frozenset()
        if new != self._want:
            self._want = new
            self._poke.set()

    @property
    def levels_live(self) -> bool:
        return bool(self._want)

    def set_levels_enabled(self, on: bool):
        self._levels_on = bool(on)
        if not on:
            self._want = frozenset()
        self._poke.set()

    def refresh_soon(self):
        """Read again right away: used straight after a key muted something."""
        self._poke.set()

    def apply(self, target: str, mode: str, value: int = 0):
        """Called from the action worker thread."""
        try:
            ok, msg = self.backend.apply(target, mode, value)
        except Exception as e:
            ok, msg = False, str(e)
        self.refresh_soon()
        return ok, msg

    # ---- background thread
    def _run(self):
        while not self._stop.is_set():
            want = self._want
            try:
                state = self.backend.read(want)
            except Exception as e:
                state = AudioState(available=False, note="Reading the sound state failed: %s" % e)
            state = self._hold_peaks(state, want)
            if state != self.state:
                self.state = state
                self.changed.emit()
            self._poke.wait(self.FAST if want else self.SLOW)
            self._poke.clear()

    def _hold_peaks(self, state: AudioState, want) -> AudioState:
        """A raw peak jumps about; hold the loudest and let it sink back so a meter reads like a meter."""
        sides = {}
        for target in (OUTPUT, INPUT):
            side = state.side(target)
            if target not in want:
                self._held[target] = 0.0
            else:
                self._held[target] = max(side.level, self._held[target] * self.DECAY)
            sides[target] = replace(side, level=round(self._held[target], 3))
        return replace(state, output=sides[OUTPUT], input=sides[INPUT])
