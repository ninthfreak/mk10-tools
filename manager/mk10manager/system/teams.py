"""Microsoft Teams: are you in a call, and are you muted? Read from the Teams window's accessibility data.

Microsoft retired Teams' local API for hardware in 2026 with no replacement, so this reads what a screen reader
reads, through Windows UI Automation. The contract comes from shuush (github.com/supermem613/shuush, MIT),
where it was checked against the new, WebView2-based Teams:

- While you're in a call, a Teams window has a button whose AutomationId is ``microphone-button``.
- Its name is "Mute mic" while you're live and "Unmute mic" while you're muted. No button means no call.
- It has to be found again on every read. WebView2 swaps its accessibility nodes when it redraws, and a node
  kept from an earlier read goes on returning the old name without any error.
- Pressing it (UI Automation's Invoke pattern) toggles mute, without Teams having focus and without a keyboard
  shortcut.

The names are English. In another language this reads "in a call, mute state unknown", and the explicit
mute on / mute off actions then refuse to press rather than guess.

Windows only, through ctypes (no extra packages). The vtable slots below were checked against Wine's
uiautomationclient.idl. Everything else gets NullTeams. `report()` prints what Teams exposes, for when Teams
changes and this needs updating.
"""

import queue
import sys
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

IS_WIN = sys.platform.startswith("win")
MIC_BUTTON = "microphone-button"


@dataclass(frozen=True)
class TeamsState:
    available: bool = True        # False: we can't read Teams at all on this system (or it's switched off)
    running: bool = False         # a Teams window exists
    in_call: bool = False
    muted: bool | None = None     # None: in a call, but the button's name didn't say which (another language?)
    button_name: str = ""         # what the mic button calls itself, as read
    note: str = ""
    read_at: float = 0.0

    @property
    def live(self) -> bool:
        return self.in_call and self.muted is False

    def same(self, other: "TeamsState") -> bool:
        return (self.available, self.running, self.in_call, self.muted, self.note) == \
               (other.available, other.running, other.in_call, other.muted, other.note)


def parse_mic_name(name: str) -> bool | None:
    """'Unmute mic' -> muted (True), 'Mute mic' -> live (False), anything else -> None. A shortcut such as
    '(Ctrl+Shift+M)' after the words doesn't matter."""
    words = (name or "").strip().lower()
    if words.startswith("unmute"):
        return True
    if words.startswith("mute"):
        return False
    return None


class NullTeams:
    note = "Reading Teams needs Windows (UI Automation). Teams on Linux runs in a browser, which this can't read yet."

    def read(self) -> TeamsState:
        return TeamsState(available=False, note=self.note)

    def press(self):
        return False, self.note

    def close(self):
        pass


# ---------------------------------------------------------------------------- Windows
if IS_WIN:
    import ctypes
    from ctypes import POINTER, byref, c_int, c_ushort, c_void_p

    from .activewin import _exe
    from .audio import GUID, Interface

    _ole32 = ctypes.windll.ole32
    _oleaut32 = ctypes.windll.oleaut32
    _oleaut32.SysAllocString.restype = c_void_p
    _oleaut32.SysAllocString.argtypes = (ctypes.c_wchar_p,)
    _oleaut32.SysFreeString.argtypes = (c_void_p,)

    class VARIANT(ctypes.Structure):
        """Only VT_BSTR and VT_I4 are built here. 24 bytes on x64, 16 on x86, as Windows expects."""
        _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                    ("value", c_void_p), ("extra", c_void_p)]

    CLSID_CUIAutomation = GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
    IID_IUIAutomation = GUID("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")
    IID_InvokePattern = GUID("{FB377FBE-8EA6-46D5-9C73-6499642D3059}")
    IID_TogglePattern = GUID("{94CF8058-9B8D-4AB9-8BFD-4CD0A33C8C70}")
    IID_LegacyPattern = GUID("{828055AD-355B-4435-86D5-3B51C14A9B1B}")

COINIT_MULTITHREADED = 0x0      # UI Automation clients belong in the MTA
RPC_E_CHANGED_MODE = -2147417850
CLSCTX_INPROC_SERVER = 0x1
VT_I4, VT_BSTR = 3, 8
TREE_CHILDREN, TREE_DESCENDANTS = 0x2, 0x4
UIA_NAME, UIA_AUTOMATION_ID, UIA_CONTROL_TYPE = 30005, 30011, 30003
UIA_BUTTON = 50000
UIA_INVOKE, UIA_TOGGLE, UIA_LEGACY = 10000, 10015, 10018

# vtable slots, IUnknown's three first (checked against Wine's include/uiautomationclient.idl)
AUTO_GET_ROOT = 5               # IUIAutomation::GetRootElement
AUTO_TRUE_CONDITION = 21        # IUIAutomation::CreateTrueCondition
AUTO_PROPERTY_CONDITION = 23    # IUIAutomation::CreatePropertyCondition
EL_FIND_FIRST = 5               # IUIAutomationElement::FindFirst
EL_FIND_ALL = 6                 # IUIAutomationElement::FindAll
EL_PATTERN_AS = 14              # IUIAutomationElement::GetCurrentPatternAs
EL_PROCESS_ID = 20              # IUIAutomationElement::get_CurrentProcessId
EL_NAME = 23                    # IUIAutomationElement::get_CurrentName
EL_AUTOMATION_ID = 29           # IUIAutomationElement::get_CurrentAutomationId
EL_CLASS_NAME = 30              # IUIAutomationElement::get_CurrentClassName
ARRAY_LENGTH = 3                # IUIAutomationElementArray::get_Length
ARRAY_ELEMENT = 4               # IUIAutomationElementArray::GetElement
INVOKE = 3                      # IUIAutomationInvokePattern::Invoke
TOGGLE_STATE = 4                # IUIAutomationTogglePattern::get_CurrentToggleState
LEGACY_DEFAULT_ACTION = 4       # IUIAutomationLegacyIAccessiblePattern::DoDefaultAction


class WindowsTeams:
    """UI Automation through ctypes. Every call must come from the one thread that first used it."""

    PID_CACHE_SECONDS = 30.0

    def __init__(self):
        self._uia = None                 # IUIAutomation
        self._mic_condition = None       # AutomationId == microphone-button
        self._true_condition = None
        self._window = None              # the Teams window the button was last found in (never the button itself)
        self._pids: dict[int, bool] = {}
        self._pids_at = 0.0
        self.note = ""

    # ---- plumbing
    def _ready(self) -> bool:
        if self._uia is not None:
            return True
        hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        if hr < 0 and hr != RPC_E_CHANGED_MODE:
            self.note = "CoInitializeEx failed (0x%08X)" % (hr & 0xFFFFFFFF)
            return False
        ptr = c_void_p()
        hr = _ole32.CoCreateInstance(byref(CLSID_CUIAutomation), None, CLSCTX_INPROC_SERVER,
                                     byref(IID_IUIAutomation), byref(ptr))
        if hr < 0 or not ptr:
            self.note = "UI Automation isn't available (0x%08X)" % (hr & 0xFFFFFFFF)
            return False
        self._uia = Interface(ptr)
        self._mic_condition = self._property_condition(UIA_AUTOMATION_ID, MIC_BUTTON)
        cond = c_void_p()
        if self._uia.call(AUTO_TRUE_CONDITION, (POINTER(c_void_p), byref(cond))) >= 0 and cond:
            self._true_condition = Interface(cond)
        if not self._mic_condition or not self._true_condition:
            self.note = "Couldn't build UI Automation search conditions"
            self.close()
            return False
        self.note = ""
        return True

    def _property_condition(self, prop: int, value):
        v = VARIANT()
        if isinstance(value, str):
            v.vt, v.value = VT_BSTR, _oleaut32.SysAllocString(value)
        else:
            v.vt, v.value = VT_I4, int(value)
        cond = c_void_p()
        try:
            hr = self._uia.call(AUTO_PROPERTY_CONDITION, (c_int, prop), (VARIANT, v), (POINTER(c_void_p), byref(cond)))
        finally:
            if v.vt == VT_BSTR and v.value:
                _oleaut32.SysFreeString(v.value)     # the condition keeps its own copy
        return Interface(cond) if hr >= 0 and cond else None

    @staticmethod
    def _text(element, slot: int) -> str:
        bstr = c_void_p()
        if element.call(slot, (POINTER(c_void_p), byref(bstr))) < 0 or not bstr:
            return ""
        try:
            return ctypes.wstring_at(bstr.value)
        finally:
            _oleaut32.SysFreeString(bstr)

    @staticmethod
    def _pid(element) -> int:
        pid = c_int(0)
        return pid.value if element.call(EL_PROCESS_ID, (POINTER(c_int), byref(pid))) >= 0 else 0

    @staticmethod
    def _find_first(element, scope: int, condition):
        found = c_void_p()
        hr = element.call(EL_FIND_FIRST, (c_int, scope), (c_void_p, condition.ptr), (POINTER(c_void_p), byref(found)))
        return Interface(found) if hr >= 0 and found else None

    @staticmethod
    def _find_all(element, scope: int, condition) -> list:
        arr = c_void_p()
        if element.call(EL_FIND_ALL, (c_int, scope), (c_void_p, condition.ptr), (POINTER(c_void_p), byref(arr))) < 0 \
                or not arr:
            return []
        array = Interface(arr)
        try:
            count = c_int(0)
            if array.call(ARRAY_LENGTH, (POINTER(c_int), byref(count))) < 0:
                return []
            out = []
            for i in range(count.value):
                el = c_void_p()
                if array.call(ARRAY_ELEMENT, (c_int, i), (POINTER(c_void_p), byref(el))) >= 0 and el:
                    out.append(Interface(el))
            return out
        finally:
            array.release()

    def _pattern(self, element, pattern_id: int, iid):
        ptr = c_void_p()
        hr = element.call(EL_PATTERN_AS, (c_int, pattern_id), (POINTER(GUID), byref(iid)),
                          (POINTER(c_void_p), byref(ptr)))
        return Interface(ptr) if hr >= 0 and ptr else None

    def _is_teams(self, pid: int) -> bool:
        if pid <= 0:
            return False
        now = time.monotonic()
        if now - self._pids_at > self.PID_CACHE_SECONDS:       # a recycled pid mustn't keep an old answer
            self._pids.clear()
            self._pids_at = now
        if pid not in self._pids:
            self._pids[pid] = "teams" in (_exe(pid) or "").lower()   # ms-teams.exe, and the classic Teams.exe
        return self._pids[pid]

    def _teams_windows(self) -> list:
        root = c_void_p()
        if self._uia.call(AUTO_GET_ROOT, (POINTER(c_void_p), byref(root))) < 0 or not root:
            return []
        desktop = Interface(root)
        try:
            windows = self._find_all(desktop, TREE_CHILDREN, self._true_condition)
        finally:
            desktop.release()
        teams = []
        for w in windows:
            if self._is_teams(self._pid(w)):
                teams.append(w)
            else:
                w.release()
        return teams

    def _find_mic(self):
        """(button or None, whether any Teams window exists). The window is cached; the button never is."""
        if self._window is not None:
            mic = self._find_first(self._window, TREE_DESCENDANTS, self._mic_condition)
            if mic is not None:
                return mic, True
            self._window.release()
            self._window = None
        windows = self._teams_windows()
        mic = None
        for w in windows:
            if mic is None:
                mic = self._find_first(w, TREE_DESCENDANTS, self._mic_condition)
                if mic is not None:
                    self._window = w
                    continue
            w.release()
        return mic, bool(windows)

    # ---- the backend interface
    def read(self) -> TeamsState:
        if not self._ready():
            return TeamsState(available=False, note=self.note)
        mic, running = self._find_mic()
        if mic is None:
            return TeamsState(available=True, running=running, read_at=time.monotonic())
        try:
            name = self._text(mic, EL_NAME)
        finally:
            mic.release()
        return TeamsState(available=True, running=True, in_call=True, muted=parse_mic_name(name),
                          button_name=name, read_at=time.monotonic())

    def press(self):
        if not self._ready():
            return False, self.note
        mic, _running = self._find_mic()
        if mic is None:
            return False, "Not in a Teams call"
        try:
            invoke = self._pattern(mic, UIA_INVOKE, IID_InvokePattern)
            if invoke is not None:
                try:
                    hr = invoke.call(INVOKE)
                finally:
                    invoke.release()
                return (True, "") if hr >= 0 else (False, "Teams refused the press (0x%08X)" % (hr & 0xFFFFFFFF))
            legacy = self._pattern(mic, UIA_LEGACY, IID_LegacyPattern)
            if legacy is not None:
                try:
                    hr = legacy.call(LEGACY_DEFAULT_ACTION)
                finally:
                    legacy.release()
                return (True, "") if hr >= 0 else (False, "Teams refused the press (0x%08X)" % (hr & 0xFFFFFFFF))
            return False, "Teams' mic button can't be pressed from outside"
        finally:
            mic.release()

    def describe(self) -> list[str]:
        """What Teams exposes right now, for report(): windows, the mic button, and every button in the call."""
        if not self._ready():
            return ["UI Automation: not available. %s" % self.note]
        lines = []
        started = time.perf_counter()
        windows = self._teams_windows()
        lines.append("Teams windows: %d (found in %.0f ms)" % (len(windows), (time.perf_counter() - started) * 1000))
        buttons_condition = self._property_condition(UIA_CONTROL_TYPE, UIA_BUTTON)
        try:
            for w in windows:
                lines.append("  window %r  class %r  process %s"
                             % (self._text(w, EL_NAME), self._text(w, EL_CLASS_NAME), self._pid(w)))
                started = time.perf_counter()
                mic = self._find_first(w, TREE_DESCENDANTS, self._mic_condition)
                took = (time.perf_counter() - started) * 1000
                if mic is None:
                    lines.append("    no %s here (looked for %.0f ms)" % (MIC_BUTTON, took))
                    continue
                try:
                    name = self._text(mic, EL_NAME)
                    lines.append("    %s found in %.0f ms: name %r -> %s" % (
                        MIC_BUTTON, took, name, {True: "muted", False: "live", None: "UNKNOWN"}[parse_mic_name(name)]))
                    patterns = []
                    for pid, iid, label in ((UIA_INVOKE, IID_InvokePattern, "Invoke"),
                                            (UIA_TOGGLE, IID_TogglePattern, "Toggle"),
                                            (UIA_LEGACY, IID_LegacyPattern, "LegacyIAccessible")):
                        p = self._pattern(mic, pid, iid)
                        if p is None:
                            continue
                        if pid == UIA_TOGGLE:
                            state = c_int(-1)
                            p.call(TOGGLE_STATE, (POINTER(c_int), byref(state)))
                            label += " (state %d)" % state.value
                        patterns.append(label)
                        p.release()
                    lines.append("    it can be pressed with: %s" % (", ".join(patterns) or "nothing"))
                finally:
                    mic.release()
                if buttons_condition is None:
                    continue
                buttons = self._find_all(w, TREE_DESCENDANTS, buttons_condition)
                lines.append("    buttons in this window: %d" % len(buttons))
                for b in buttons[:80]:
                    lines.append("      %-34s %s" % (self._text(b, EL_AUTOMATION_ID) or "-", self._text(b, EL_NAME)))
                for b in buttons:
                    b.release()
        finally:
            if buttons_condition is not None:
                buttons_condition.release()
            for w in windows:
                w.release()
        return lines

    def close(self):
        for attr in ("_window", "_mic_condition", "_true_condition", "_uia"):
            obj = getattr(self, attr)
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass
                setattr(self, attr, None)


def make_backend():
    return WindowsTeams() if IS_WIN else NullTeams()


def describe_backend(backend=None) -> str:
    backend = backend if backend is not None else make_backend()
    if isinstance(backend, NullTeams):
        return "not available here (Windows only)"
    if IS_WIN and isinstance(backend, WindowsTeams):
        return "UI Automation (the Teams window's accessibility data)"
    return type(backend).__name__


def report(reads: int = 3, gap: float = 1.0) -> str:
    """Everything that matters when the Teams keys look wrong: what the reader sees, and what Teams exposes."""
    backend = make_backend()
    lines = ["MK10 Manager: what Teams looks like from here", "Reader: %s" % describe_backend(backend), ""]
    seen_call = False
    try:
        for i in range(reads):
            if i:
                time.sleep(gap)
            s = backend.read()
            if not s.available:
                lines.append("Read %d: not available. %s" % (i + 1, s.note))
            elif not s.in_call:
                lines.append("Read %d: %s" % (i + 1, "Teams is running, not in a call" if s.running
                                              else "no Teams window found"))
            else:
                seen_call = True
                lines.append("Read %d: in a call, %s (button name %r)" % (
                    i + 1, {True: "muted", False: "live", None: "mute state UNKNOWN"}[s.muted], s.button_name))
        if hasattr(backend, "describe"):
            lines.append("")
            lines.extend(backend.describe())
        lines += ["", "Nothing was pressed or changed." if seen_call else
                  "Nothing was pressed or changed. Run this during a Teams call to see the mic button."]
    finally:
        backend.close()
    return "\n".join(lines)


# ---------------------------------------------------------------------------- the monitor
class TeamsMonitor(QObject):
    """Reads Teams on its own thread. `state` is safe to read from the GUI thread. Presses happen on the same
    thread as the reads, because the UI Automation objects belong to it."""

    changed = Signal()

    BUSY = 0.75          # seconds between reads during a call (shuush uses the same)
    IDLE = 3.0           # ... and outside one, where each read searches the whole Teams window and finds nothing
                         # (the expensive case; `report()` prints how long a search takes, to tune this by)
    HOLD_MISSES = 2      # a call that vanishes for this many reads in a row has really ended
    CONFIRM_SECONDS = 1.5
    COMMANDS = ("mute_on", "mute_off", "toggle")

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self.backend = backend if backend is not None else make_backend()
        self.state = TeamsState(available=not isinstance(self.backend, NullTeams),
                                note=getattr(self.backend, "note", ""))
        self._enabled = True
        self._misses = 0
        self._requests: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._poke = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- GUI thread (and the action worker)
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mk10-teams", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        self._poke.set()
        if self._thread:
            self._thread.join(timeout)

    def configure(self, enabled: bool):
        was = self._enabled
        self._enabled = bool(enabled)
        if was and not self._enabled:
            self.state = TeamsState(available=False, note="Switched off on the Device and app tab.")
            self.changed.emit()
        self._poke.set()

    def refresh_soon(self):
        self._poke.set()

    def command(self, kind: str, timeout: float = 5.0):
        """mute_on / mute_off / toggle. Blocks until done: call it from a worker, never the GUI thread.
        Returns (ok, message); "already" means nothing needed pressing."""
        if kind not in self.COMMANDS:
            return False, "Unknown Teams command %s" % kind
        if not self._enabled:
            return False, "Reading Teams is switched off on the Device and app tab."
        if not (self._thread and self._thread.is_alive()):
            return False, "The Teams reader isn't running."
        done, result = threading.Event(), {}
        self._requests.put((kind, done, result))
        self._poke.set()
        if not done.wait(timeout):
            return False, "Teams didn't answer in time"
        return result.get("ok", False), result.get("message", "")

    # ---- the reading thread
    def _run(self):
        try:
            while not self._stop.is_set():
                self._serve_requests()
                if self._enabled:
                    self._publish(self._smooth(self._read()))
                self._poke.wait(self.BUSY if self.state.in_call else self.IDLE)
                self._poke.clear()
        finally:
            self._fail_requests("The Teams reader stopped.")
            try:
                self.backend.close()
            except Exception:
                pass

    def _read(self) -> TeamsState:
        try:
            return self.backend.read()
        except Exception as e:
            return TeamsState(available=False, note="Reading Teams failed: %s" % e)

    def _smooth(self, new: TeamsState) -> TeamsState:
        """Teams' accessibility nodes vanish for a moment while it redraws. Don't end the call on one miss."""
        if new.available and not new.in_call and self.state.in_call and self._misses < self.HOLD_MISSES:
            self._misses += 1
            return self.state
        self._misses = 0
        return new

    def _publish(self, new: TeamsState):
        old, self.state = self.state, new
        if not new.same(old):
            self.changed.emit()

    def _serve_requests(self):
        while True:
            try:
                kind, done, result = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                ok, message = self._do(kind) if self._enabled else (False, "Reading Teams is switched off.")
            except Exception as e:
                ok, message = False, "Teams command failed: %s" % e
            result.update(ok=ok, message=message)
            done.set()

    def _fail_requests(self, message: str):
        while True:
            try:
                _kind, done, result = self._requests.get_nowait()
            except queue.Empty:
                return
            result.update(ok=False, message=message)
            done.set()

    def _do(self, kind: str):
        """Read, press only if it's needed, then read again until Teams shows the change."""
        now = self._read()
        self._misses = 0
        self._publish(now)
        if not now.available:
            return False, now.note or "Teams can't be read here"
        if not now.in_call:
            return False, "Not in a Teams call"
        if now.muted is None:
            return False, "Can't tell whether Teams has you muted (its mic button says %r), so nothing was " \
                          "pressed" % now.button_name
        want = {"mute_on": True, "mute_off": False}.get(kind, not now.muted)
        if now.muted == want:
            return True, "already"
        ok, message = self.backend.press()
        if not ok:
            return False, message
        deadline = time.monotonic() + self.CONFIRM_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.15)
            after = self._read()
            if after.in_call and after.muted == want:
                self._publish(after)
                return True, ""
        return False, "Pressed Teams' mic button, but Teams still shows you %s" % ("live" if want else "muted")

