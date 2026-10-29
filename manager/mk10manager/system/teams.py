"""Microsoft Teams: are you in a call, muted, on camera, sharing? Read from the Teams window's accessibility data.

Microsoft retired Teams' local API for hardware in 2026 with no replacement, so this reads what a screen reader
reads, through Windows UI Automation. The contract comes from shuush (github.com/supermem613/shuush, MIT), and
was confirmed on the work PC's Teams in October 2026 (docs/research-notes.md):

- While you're in a call, a Teams window has a button whose AutomationId is ``microphone-button``. Its name is
  "Mute mic" while you're live and "Unmute mic" while you're muted. No button means no call.
- In the same window: ``video-button`` ("Turn camera on" / "Turn camera off"), ``share-button`` ("Share" /
  "Stop sharing") and ``raisehands-button`` (always "Raise"; whether the hand is up comes from its pressed
  state, when Teams reports one).
- ``call-duration-custom`` is the call clock. Its name ("Elapsed time 04:04") lags; its child text ("04:34")
  is the one on screen, so the larger of the two is used.
- Your own video tile is an image named "Myself video, <you>, ..., Hand raised position 1, ...": that's where
  the raised hand shows. While you share, a separate "Sharing control bar" window has a button named "You're
  sharing your screen". Content Teams shares inside the meeting (a whiteboard, an Excel sheet) gets its own,
  separate "Stop sharing" button instead of changing ``share-button``.
- Everything shown comes from what Teams itself displays. Nothing is inferred: no guessing which devices Teams
  uses from Windows' audio, no camera names from Windows' device list. When Teams doesn't show something, it's
  unknown here too.
- Buttons have to be found again on every read. WebView2 swaps its accessibility nodes when it redraws, and a
  node kept from an earlier read goes on returning the old name without any error.
- Pressing a button (UI Automation's Invoke pattern) works without Teams having focus and without a shortcut.

The names are English. In another language a state reads as unknown, and the explicit commands then refuse to
press rather than guess.

Windows only, through ctypes (no extra packages). The vtable slots below were checked against Wine's
uiautomationclient.idl. Everything else gets NullTeams. `report()` and `tree_report()` print what Teams exposes,
for when Teams changes and this needs updating.
"""

import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal

IS_WIN = sys.platform.startswith("win")
MIC_BUTTON, CAMERA_BUTTON, SHARE_BUTTON, HAND_BUTTON = (
    "microphone-button", "video-button", "share-button", "raisehands-button")
CONTROLS = {"mic": MIC_BUTTON, "camera": CAMERA_BUTTON, "share": SHARE_BUTTON, "hand": HAND_BUTTON}
CALL_CLOCK = "call-duration-custom"
STOP_SHARE = "stop_share"       # press(): whichever Stop sharing button Teams shows


@dataclass(frozen=True)
class TeamsState:
    available: bool = True        # False: we can't read Teams at all on this system (or it's switched off)
    running: bool = False         # a Teams window exists
    in_call: bool = False
    muted: bool | None = None     # None: in a call, but the button's name didn't say which (another language?)
    camera: bool | None = None    # True: your camera is on
    sharing: bool | None = None   # True: you're sharing your screen
    hand: bool | None = None      # True: your hand is up (from your own video tile, when it's on screen)
    shared: str = ""              # what you're sharing, in Teams' words after "You're sharing": "your screen"
    meeting: str = ""             # the meeting's name, from its window title
    started: float = 0.0          # time.monotonic() when Teams' own call clock says the call began (0: unknown)
    call_since: float = 0.0       # when the call began, as the monitor keeps it (Teams' clock, else first seen)
    names: tuple = ()             # ((AutomationId, name), ...) of the buttons, as read
    note: str = ""
    read_at: float = 0.0

    @property
    def live(self) -> bool:
        return self.in_call and self.muted is False

    def name_of(self, automation_id: str) -> str:
        return dict(self.names).get(automation_id, "")

    @property
    def button_name(self) -> str:
        return self.name_of(MIC_BUTTON)

    @property
    def clock_known(self) -> bool:
        """Only Teams' own call clock is shown. Without it (teams-for-linux has none) the time is unknown."""
        return self.in_call and bool(self.call_since)

    def call_seconds(self, now: float | None = None) -> int:
        if not self.in_call or not self.call_since:
            return 0
        return max(0, int((now if now is not None else time.monotonic()) - self.call_since))

    def same(self, other: "TeamsState") -> bool:
        return (self.available, self.running, self.in_call, self.muted, self.camera, self.sharing, self.hand,
                self.shared, self.meeting, self.note) == \
               (other.available, other.running, other.in_call, other.muted, other.camera, other.sharing,
                other.hand, other.shared, other.meeting, other.note)


def parse_mic_name(name: str) -> bool | None:
    """'Unmute mic' -> muted (True), 'Mute mic' -> live (False), anything else -> None. A shortcut such as
    '(Ctrl+Shift+M)' after the words doesn't matter."""
    words = (name or "").strip().lower()
    if words.startswith("unmute"):
        return True
    if words.startswith("mute"):
        return False
    return None


def parse_camera_name(name: str) -> bool | None:
    """'Turn camera off' -> the camera is on (True), 'Turn camera on' -> off (False)."""
    words = (name or "").strip().lower()
    if words.startswith("turn camera off"):
        return True
    if words.startswith("turn camera on"):
        return False
    return None


def parse_share_name(name: str) -> bool | None:
    """'Stop sharing' -> sharing (True), 'Share' -> not (False)."""
    words = (name or "").strip().lower()
    if words.startswith("stop shar"):
        return True
    if words.startswith("share"):
        return False
    return None


def parse_pressed(aria_properties: str = "", toggle_state: int | None = None) -> bool | None:
    """A button's pressed state: from its ARIA properties ('pressed=true;...') when Chromium passes them on,
    otherwise from the Toggle pattern (0 off, 1 on). None when neither says."""
    m = re.search(r"(?:^|;)\s*pressed\s*=\s*(true|false|mixed)", aria_properties or "", re.I)
    if m:
        return {"true": True, "false": False}.get(m.group(1).lower())
    if toggle_state in (0, 1):
        return bool(toggle_state)
    return None


def parse_duration(text: str) -> int | None:
    """'04:34' -> 274, '1:02:03' -> 3723, 'Elapsed time 04:04' -> 244. None when there's no time in it."""
    m = re.search(r"(\d+):(\d{2})(?::(\d{2}))?\s*$", text or "")
    if not m:
        return None
    a, b, c = m.groups()
    return int(a) * 3600 + int(b) * 60 + int(c) if c else int(a) * 60 + int(b)


def parse_shared(name: str) -> str:
    """"You're sharing your screen" -> 'your screen'. '' for any other button."""
    m = re.match(r"\s*you(?:['\u2019]re| are) sharing\s+(.+?)\s*$", name or "", re.I)
    return m.group(1) if m else ""


def is_stop_sharing(name: str) -> bool:
    """Teams' Stop sharing buttons: ``share-button`` while you share a screen or window, and a separate one for a
    whiteboard or an Excel sheet. Their names start with the words, whatever the capitals."""
    return (name or "").strip().lower().startswith("stop sharing")


def hand_from_tile(name: str) -> bool | None:
    """Your own video tile: 'Myself video, <you>, ..., Hand raised position 1, ...'. None for anyone else's."""
    words = (name or "").strip().lower()
    if not words.startswith("myself"):
        return None
    return "hand raised" in words


def teams_settings_folder() -> str:
    """Where the new Teams (ms-teams.exe) keeps its own settings files."""
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "MSTeams_8wekyb3d8bbwe", "LocalCache",
                        "Microsoft", "MSTeams")


def meeting_from_title(title: str) -> str:
    """'Weekly sync | Lemans Corporation | me@x.com | Microsoft Teams' -> 'Weekly sync'."""
    first = (title or "").split(" | ")[0].strip()
    return "" if first.lower() in ("", "microsoft teams") else first


def short_device_name(name: str) -> str:
    """Windows names devices like 'Headset Microphone (Jabra Evolve2 65)'. The part in brackets is the one
    that tells you which; 'Microphone (Realtek(R) Audio)' keeps its own inner brackets."""
    name = (name or "").strip()
    start, end = name.find("("), name.rfind(")")
    if 0 < start < end and name[start + 1:end].strip():
        return name[start + 1:end].strip()
    return name


class NullTeams:
    note = "Reading Teams needs Windows, or Linux with teams-for-linux."

    def read(self) -> TeamsState:
        return TeamsState(available=False, note=self.note)

    def press(self, control: str = "mic", focus: bool = False, want=None):
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
UIA_BUTTON, UIA_IMAGE = 50000, 50006
UIA_INVOKE, UIA_TOGGLE, UIA_LEGACY = 10000, 10015, 10018
IGNORE_CASE, MATCH_SUBSTRING = 0x1, 0x2      # PropertyConditionFlags (substring needs Windows 10 1809 or later)
CONTROL_TYPES = {50000: "Button", 50002: "CheckBox", 50004: "Edit", 50005: "Hyperlink", 50006: "Image",
                 50007: "ListItem", 50008: "List", 50009: "Menu", 50011: "MenuItem", 50017: "StatusBar",
                 50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50024: "TreeItem", 50025: "Custom",
                 50026: "Group", 50029: "DataItem", 50030: "Document", 50032: "Window", 50033: "Pane",
                 50034: "Header"}

# vtable slots, IUnknown's three first (checked against Wine's include/uiautomationclient.idl)
AUTO_GET_ROOT = 5               # IUIAutomation::GetRootElement
AUTO_CONTROL_WALKER = 14        # IUIAutomation::get_ControlViewWalker
AUTO_TRUE_CONDITION = 21        # IUIAutomation::CreateTrueCondition
AUTO_PROPERTY_CONDITION = 23    # IUIAutomation::CreatePropertyCondition
AUTO_PROPERTY_CONDITION_EX = 24 # IUIAutomation::CreatePropertyConditionEx
AUTO_OR_CONDITION = 28          # IUIAutomation::CreateOrCondition
EL_SET_FOCUS = 3                # IUIAutomationElement::SetFocus
EL_FIND_FIRST = 5               # IUIAutomationElement::FindFirst
EL_FIND_ALL = 6                 # IUIAutomationElement::FindAll
EL_PATTERN_AS = 14              # IUIAutomationElement::GetCurrentPatternAs
EL_PROCESS_ID = 20              # IUIAutomationElement::get_CurrentProcessId
EL_CONTROL_TYPE = 21            # IUIAutomationElement::get_CurrentControlType
EL_NAME = 23                    # IUIAutomationElement::get_CurrentName
EL_AUTOMATION_ID = 29           # IUIAutomationElement::get_CurrentAutomationId
EL_CLASS_NAME = 30              # IUIAutomationElement::get_CurrentClassName
EL_HELP_TEXT = 31               # IUIAutomationElement::get_CurrentHelpText
EL_OFFSCREEN = 38               # IUIAutomationElement::get_CurrentIsOffscreen
EL_ITEM_STATUS = 42             # IUIAutomationElement::get_CurrentItemStatus
EL_ARIA_ROLE = 45               # IUIAutomationElement::get_CurrentAriaRole
EL_ARIA_PROPERTIES = 46         # IUIAutomationElement::get_CurrentAriaProperties
ARRAY_LENGTH = 3                # IUIAutomationElementArray::get_Length
ARRAY_ELEMENT = 4               # IUIAutomationElementArray::GetElement
WALK_FIRST_CHILD = 4            # IUIAutomationTreeWalker::GetFirstChildElement
WALK_NEXT_SIBLING = 6           # IUIAutomationTreeWalker::GetNextSiblingElement
INVOKE = 3                      # IUIAutomationInvokePattern::Invoke
TOGGLE_STATE = 4                # IUIAutomationTogglePattern::get_CurrentToggleState
LEGACY_DEFAULT_ACTION = 4       # IUIAutomationLegacyIAccessiblePattern::DoDefaultAction


class WindowsTeams:
    """UI Automation through ctypes. Every call must come from the one thread that first used it."""

    PID_CACHE_SECONDS = 30.0
    BAR_RETRY_SECONDS = 3.0      # looking for the sharing bar lists every window: not on every read

    def __init__(self):
        self._uia = None                 # IUIAutomation
        self._mic_condition = None       # AutomationId == microphone-button
        self._controls_condition = None  # AutomationId is one of the call buttons or the call clock
        self._read_condition = None      # ... or an image (the video tiles), or named Stop sharing
        self._stop_condition = None      # named Stop sharing, for pressing it
        self._button_condition = None
        self._true_condition = None
        self._window = None              # the call window, kept between reads (never the buttons: see above)
        self._bar = None                 # the sharing control bar's window, while you share
        self._bar_next = 0.0
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
        ids = list(CONTROLS.values()) + [CALL_CLOCK]
        self._mic_condition = self._property_condition(UIA_AUTOMATION_ID, MIC_BUTTON)
        self._controls_condition = self._either([self._property_condition(UIA_AUTOMATION_ID, i) for i in ids])
        self._read_condition = self._either([self._property_condition(UIA_AUTOMATION_ID, i) for i in ids]
                                            + [self._property_condition(UIA_CONTROL_TYPE, UIA_IMAGE),
                                               self._stop_sharing_condition()])
        self._stop_condition = self._stop_sharing_condition()
        self._button_condition = self._property_condition(UIA_CONTROL_TYPE, UIA_BUTTON)
        cond = c_void_p()
        if self._uia.call(AUTO_TRUE_CONDITION, (POINTER(c_void_p), byref(cond))) >= 0 and cond:
            self._true_condition = Interface(cond)
        if not all((self._mic_condition, self._controls_condition, self._read_condition, self._stop_condition,
                    self._button_condition, self._true_condition)):
            self.note = "Couldn't build UI Automation search conditions"
            self.close()
            return False
        self.note = ""
        return True

    def _property_condition(self, prop: int, value, flags: int = 0):
        v = VARIANT()
        if isinstance(value, str):
            v.vt, v.value = VT_BSTR, _oleaut32.SysAllocString(value)
        else:
            v.vt, v.value = VT_I4, int(value)
        cond = c_void_p()
        try:
            if flags:
                hr = self._uia.call(AUTO_PROPERTY_CONDITION_EX, (c_int, prop), (VARIANT, v), (c_int, flags),
                                    (POINTER(c_void_p), byref(cond)))
            else:
                hr = self._uia.call(AUTO_PROPERTY_CONDITION, (c_int, prop), (VARIANT, v),
                                    (POINTER(c_void_p), byref(cond)))
        finally:
            if v.vt == VT_BSTR and v.value:
                _oleaut32.SysFreeString(v.value)     # the condition keeps its own copy
        return Interface(cond) if hr >= 0 and cond else None

    def _stop_sharing_condition(self):
        """Name contains 'stop sharing', any capitals; just 'Stop sharing' where Windows can't match part of a
        name. is_stop_sharing() then checks the name starts with it."""
        return (self._property_condition(UIA_NAME, "stop sharing", IGNORE_CASE | MATCH_SUBSTRING)
                or self._property_condition(UIA_NAME, "Stop sharing", IGNORE_CASE))

    def _either(self, conditions: list):
        """a OR b OR ...: one search then finds everything we read. Releases the conditions it's given."""
        combined = None
        for i, one in enumerate(conditions):
            if one is None:
                for rest in [combined] + conditions[i + 1:]:
                    if rest is not None:
                        rest.release()
                return None
            if combined is None:
                combined = one
                continue
            both = c_void_p()
            hr = self._uia.call(AUTO_OR_CONDITION, (c_void_p, combined.ptr), (c_void_p, one.ptr),
                                (POINTER(c_void_p), byref(both)))
            combined.release()                       # the new condition holds its own references
            one.release()
            if hr < 0 or not both:
                for rest in conditions[i + 1:]:
                    if rest is not None:
                        rest.release()
                return None
            combined = Interface(both)
        return combined

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
    def _int(element, slot: int) -> int:
        value = c_int(0)
        return value.value if element.call(slot, (POINTER(c_int), byref(value))) >= 0 else 0

    def _pid(self, element) -> int:
        return self._int(element, EL_PROCESS_ID)

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

    def _toggle_state(self, element) -> int | None:
        toggle = self._pattern(element, UIA_TOGGLE, IID_TogglePattern)
        if toggle is None:
            return None
        try:
            state = c_int(-1)
            return state.value if toggle.call(TOGGLE_STATE, (POINTER(c_int), byref(state))) >= 0 else None
        finally:
            toggle.release()

    def _pressed(self, element) -> bool | None:
        return parse_pressed(self._text(element, EL_ARIA_PROPERTIES), self._toggle_state(element))

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

    def _controls_in(self, window, tiles: bool = False):
        """({AutomationId: element} for the call buttons and clock, [(control type, name)] of the video tiles and
        any other Stop sharing button), from one search. Without `tiles` only the buttons and clock are looked for."""
        found, others = {}, []
        wanted = set(CONTROLS.values()) | {CALL_CLOCK}
        for el in self._find_all(window, TREE_DESCENDANTS, self._read_condition if tiles else self._controls_condition):
            automation_id = self._text(el, EL_AUTOMATION_ID)
            if automation_id in wanted and automation_id not in found:
                found[automation_id] = el
                continue
            if tiles:
                kind, name = self._int(el, EL_CONTROL_TYPE), self._text(el, EL_NAME)
                if name and (kind == UIA_IMAGE or (kind == UIA_BUTTON and is_stop_sharing(name))):
                    others.append((kind, name))
            el.release()
        return found, others

    def _stop_button(self, window):
        """Whichever Stop sharing button Teams shows in the call window: share-button first."""
        buttons = self._find_all(window, TREE_DESCENDANTS, self._stop_condition)
        chosen = None
        for b in sorted(buttons, key=lambda b: self._text(b, EL_AUTOMATION_ID) != SHARE_BUTTON):
            if chosen is None and self._int(b, EL_CONTROL_TYPE) == UIA_BUTTON and is_stop_sharing(self._text(b, EL_NAME)):
                chosen = b
            else:
                b.release()
        return chosen

    @staticmethod
    def _release_all(elements: dict):
        for el in elements.values():
            el.release()

    def _find_call(self, tiles: bool = False):
        """(call window or None, {AutomationId: element}, [(type, name)] of tiles and other Stop sharing buttons,
        whether any Teams window exists)."""
        if self._window is not None:
            controls, others = self._controls_in(self._window, tiles)
            if MIC_BUTTON in controls:
                return self._window, controls, others, True
            self._release_all(controls)
            self._window.release()
            self._window = None
        windows = self._teams_windows()
        call = None
        for w in windows:
            if call is None:
                mic = self._find_first(w, TREE_DESCENDANTS, self._mic_condition)
                if mic is not None:
                    mic.release()
                    call = w
                    continue
            w.release()
        if call is None:
            return None, {}, [], bool(windows)
        self._window = call
        controls, others = self._controls_in(call, tiles)
        return call, controls, others, True

    def _elapsed(self, clock) -> int | None:
        """Seconds on Teams' call clock. Its name ('Elapsed time 04:04') is updated less often than the text
        inside it ('04:34'), which is what the window shows; the larger is the newer."""
        values = [parse_duration(self._text(clock, EL_NAME))]
        inner = self._find_first(clock, TREE_CHILDREN, self._true_condition)
        if inner is not None:
            try:
                values.append(parse_duration(self._text(inner, EL_NAME)))
            finally:
                inner.release()
        values = [v for v in values if v is not None]
        return max(values) if values else None

    def _shared(self) -> str:
        """What the sharing control bar says you're sharing ('your screen'), or '' when it can't be found."""
        if self._bar is not None:
            what = self._bar_says(self._bar)
            if what is not None:
                return what
            self._forget_bar()                         # the bar closed (or was replaced): look for it again
        if time.monotonic() < self._bar_next:
            return ""
        self._bar_next = time.monotonic() + self.BAR_RETRY_SECONDS
        for w in self._teams_windows():
            if self._bar is None and self._text(w, EL_NAME).lower().startswith("sharing control bar"):
                self._bar = w
            else:
                w.release()
        return (self._bar_says(self._bar) or "") if self._bar is not None else ""

    def _bar_says(self, bar) -> str | None:
        """None when the window doesn't answer any more."""
        buttons = self._find_all(bar, TREE_DESCENDANTS, self._button_condition)
        if not buttons:
            return None
        try:
            for b in buttons:
                what = parse_shared(self._text(b, EL_NAME))
                if what:
                    return what
            return ""
        finally:
            for b in buttons:
                b.release()

    def _forget_bar(self) -> str:
        if self._bar is not None:
            self._bar.release()
            self._bar = None
        self._bar_next = 0.0
        return ""

    # ---- the backend interface
    def read(self) -> TeamsState:
        if not self._ready():
            return TeamsState(available=False, note=self.note)
        window, controls, others, running = self._find_call(tiles=True)
        try:
            if MIC_BUTTON not in controls:
                self._forget_bar()
                return TeamsState(available=True, running=running, read_at=time.monotonic())
            names = {aid: self._text(el, EL_NAME) for aid, el in controls.items() if aid != CALL_CLOCK}
            started = 0.0
            if CALL_CLOCK in controls:
                elapsed = self._elapsed(controls[CALL_CLOCK])
                if elapsed is not None:
                    started = time.monotonic() - elapsed
            tiles = [name for kind, name in others if kind == UIA_IMAGE]
            content_shared = any(kind == UIA_BUTTON for kind, _name in others)   # its own Stop sharing button
            hand = next((h for h in map(hand_from_tile, tiles) if h is not None), None)
            if hand is None and HAND_BUTTON in controls:
                hand = self._pressed(controls[HAND_BUTTON])
            meeting = meeting_from_title(self._text(window, EL_NAME)) if window is not None else ""
        finally:
            self._release_all(controls)
        sharing = parse_share_name(names.get(SHARE_BUTTON, ""))
        shared = self._shared() if sharing else self._forget_bar()
        if content_shared:
            sharing = True
        return TeamsState(available=True, running=True, in_call=True, muted=parse_mic_name(names[MIC_BUTTON]),
                          camera=parse_camera_name(names.get(CAMERA_BUTTON, "")), sharing=sharing, hand=hand,
                          shared=shared, meeting=meeting, started=started,
                          names=tuple(sorted(names.items())), read_at=time.monotonic())

    def press(self, control: str = "mic", focus: bool = False, want=None):
        """Press one of the call buttons (`want`, the state asked for, isn't needed: a press flips it), or STOP_SHARE for whichever Stop sharing button Teams shows. With
        `focus`, bring the call window forward first (for a button that opens something there, such as Share's
        picker)."""
        if not self._ready():
            return False, self.note
        automation_id = CONTROLS.get(control, control)
        window, controls, _tiles, _running = self._find_call()
        try:
            if MIC_BUTTON not in controls:
                return False, "Not in a Teams call"
            if control == STOP_SHARE:
                button = self._stop_button(window) if window is not None else None
                if button is not None:
                    controls["_stop"] = button             # released with the rest below
            else:
                button = controls.get(automation_id)
            if button is None:
                return False, ("Teams isn't showing a Stop sharing button" if control == STOP_SHARE
                               else "Teams' %s button isn't there" % control)
            if focus and window is not None:
                window.call(EL_SET_FOCUS)              # best effort: Windows may refuse to change the foreground
            for pattern_id, iid, slot in ((UIA_INVOKE, IID_InvokePattern, INVOKE),
                                          (UIA_LEGACY, IID_LegacyPattern, LEGACY_DEFAULT_ACTION)):
                pattern = self._pattern(button, pattern_id, iid)
                if pattern is None:
                    continue
                try:
                    hr = pattern.call(slot)
                finally:
                    pattern.release()
                return (True, "") if hr >= 0 else (False, "Teams refused the press (0x%08X)" % (hr & 0xFFFFFFFF))
            return False, "Teams' %s button can't be pressed from outside" % control
        finally:
            self._release_all(controls)

    def describe(self) -> list[str]:
        """What Teams exposes right now, for report(): windows, the call buttons, and every button in the call."""
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
                mic.release()
                started = time.perf_counter()
                controls, others = self._controls_in(w, tiles=True)
                tile_names = [name for kind, name in others if kind == UIA_IMAGE]
                lines.append("    call buttons, clock and video tiles found in %.0f ms (one search)"
                             % ((time.perf_counter() - started) * 1000))
                try:
                    clock = controls.pop(CALL_CLOCK, None)
                    if clock is None:
                        lines.append("      call clock: not found (%s)" % CALL_CLOCK)
                    else:
                        lines.append("      call clock: %r -> %s s" % (self._text(clock, EL_NAME), self._elapsed(clock)))
                        clock.release()
                    for name in tile_names[:30]:
                        lines.append("      video tile: %r%s" % (name[:200], "  <- you: hand %s" % (
                            "raised" if hand_from_tile(name) else "down") if hand_from_tile(name) is not None else ""))
                    if not tile_names:
                        lines.append("      video tiles: none on screen")
                    for kind, name in others:
                        if kind == UIA_BUTTON:
                            lines.append("      another Stop sharing button: %r" % name)
                    for aid, el in sorted(controls.items()):
                        patterns = [label for pid, iid, label in ((UIA_INVOKE, IID_InvokePattern, "Invoke"),
                                                                   (UIA_TOGGLE, IID_TogglePattern, "Toggle"),
                                                                   (UIA_LEGACY, IID_LegacyPattern, "Legacy"))
                                    if self._has_pattern(el, pid, iid)]
                        lines.append("      %-20s %-18r pressed=%-5s aria=%r  [%s]" % (
                            aid, self._text(el, EL_NAME), self._pressed(el), self._text(el, EL_ARIA_PROPERTIES),
                            ", ".join(patterns)))
                finally:
                    self._release_all(controls)
                if buttons_condition is None:
                    continue
                buttons = self._find_all(w, TREE_DESCENDANTS, buttons_condition)
                lines.append("    buttons in this window: %d" % len(buttons))
                for b in buttons[:80]:
                    lines.append("      %-34s %s" % (self._text(b, EL_AUTOMATION_ID) or "-", self._text(b, EL_NAME)))
                for b in buttons:
                    b.release()
            for w in windows:
                if self._text(w, EL_NAME).lower().startswith("sharing control bar"):
                    lines.append("  the sharing bar says you're sharing: %r" % (self._bar_says(w) or "(it doesn't say)"))
        finally:
            if buttons_condition is not None:
                buttons_condition.release()
            for w in windows:
                w.release()
        return lines

    def _has_pattern(self, element, pattern_id, iid) -> bool:
        p = self._pattern(element, pattern_id, iid)
        if p is None:
            return False
        p.release()
        return True

    def tree(self, max_nodes: int = 4000, max_depth: int = 45) -> list[str]:
        """Every element of each Teams window, indented by depth: type, AutomationId, name, and whatever else
        might carry participants, speaking or hand state. Read-only, and slow on purpose-free large windows."""
        if not self._ready():
            return ["UI Automation: not available. %s" % self.note]
        walker_ptr = c_void_p()
        if self._uia.call(AUTO_CONTROL_WALKER, (POINTER(c_void_p), byref(walker_ptr))) < 0 or not walker_ptr:
            return ["Couldn't get UI Automation's tree walker."]
        walker = Interface(walker_ptr)
        lines, count = [], [0]

        def child(el, slot):
            out = c_void_p()
            return Interface(out) if walker.call(slot, (c_void_p, el.ptr), (POINTER(c_void_p), byref(out))) >= 0 \
                and out else None

        def node(el, depth):
            if count[0] >= max_nodes:
                return
            count[0] += 1
            kind = self._int(el, EL_CONTROL_TYPE)
            parts = [CONTROL_TYPES.get(kind, str(kind))]
            aid = self._text(el, EL_AUTOMATION_ID)
            if aid:
                parts.append("[%s]" % aid)
            name = self._text(el, EL_NAME)
            if name:
                parts.append(repr(name[:200]))
            for label, slot in (("status", EL_ITEM_STATUS), ("role", EL_ARIA_ROLE), ("aria", EL_ARIA_PROPERTIES),
                                ("help", EL_HELP_TEXT)):
                text = self._text(el, slot)
                if text:
                    parts.append("%s=%r" % (label, text[:160]))
            if kind in (50000, 50002, 50007):                    # buttons, check boxes, list items
                state = self._toggle_state(el)
                if state is not None:
                    parts.append("toggle=%d" % state)
            if self._int(el, EL_OFFSCREEN):
                parts.append("offscreen")
            lines.append("  " * depth + " ".join(parts))
            if depth >= max_depth:
                return
            c = child(el, WALK_FIRST_CHILD)
            while c is not None and count[0] < max_nodes:
                node(c, depth + 1)
                nxt = child(c, WALK_NEXT_SIBLING)
                c.release()
                c = nxt
            if c is not None:
                c.release()

        windows = self._teams_windows()
        try:
            for w in windows:
                count[0] = 0
                started = time.perf_counter()
                lines.append("=" * 100)
                lines.append("window %r  class %r" % (self._text(w, EL_NAME), self._text(w, EL_CLASS_NAME)))
                node(w, 0)
                lines.append("(%d elements%s, %.1f s)" % (count[0], ", stopped at the limit"
                                                          if count[0] >= max_nodes else "",
                                                          time.perf_counter() - started))
        finally:
            for w in windows:
                w.release()
            walker.release()
        return lines

    def close(self):
        for attr in ("_window", "_bar", "_mic_condition", "_controls_condition", "_read_condition",
                     "_stop_condition", "_button_condition", "_true_condition", "_uia"):
            obj = getattr(self, attr)
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass
                setattr(self, attr, None)


def make_backend():
    """Windows: the Teams window, read like a screen reader does. Linux: teams-for-linux's own D-Bus control."""
    if IS_WIN:
        return WindowsTeams()
    if sys.platform.startswith("linux"):
        from .teams_linux import TeamsForLinux
        return TeamsForLinux()
    return NullTeams()


def describe_backend(backend=None) -> str:
    backend = backend if backend is not None else make_backend()
    if isinstance(backend, NullTeams):
        return "not available here (Windows, or Linux with teams-for-linux)"
    if IS_WIN and isinstance(backend, WindowsTeams):
        return "UI Automation (the Teams window's accessibility data)"
    if type(backend).__name__ == "TeamsForLinux":
        return "teams-for-linux's D-Bus control, through busctl"
    return type(backend).__name__


def _state_words(s: TeamsState) -> str:
    word = lambda v, yes, no: {True: yes, False: no, None: "?"}[v]
    sharing = word(s.sharing, "sharing", "not sharing") + (" %s" % s.shared if s.sharing and s.shared else "")
    return "%s, camera %s, %s, hand %s, call clock %s" % (
        word(s.muted, "muted", "live"), word(s.camera, "on", "off"), sharing, word(s.hand, "up", "down"),
        "%d s" % (time.monotonic() - s.started) if s.started else "not read")


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
                lines.append("Read %d: in %s: %s" % (i + 1, repr(s.meeting) if s.meeting else "a call",
                                                    _state_words(s)))
        if hasattr(backend, "describe"):
            lines.append("")
            lines.extend(backend.describe())
        if IS_WIN:
            lines.append("")
            lines.extend(settings_report())
            lines.append("")
            lines.extend(cameras_report())
        lines += ["", "Nothing was pressed or changed." if seen_call else
                  "Nothing was pressed or changed. Run this during a Teams call to see the call buttons."]
    finally:
        backend.close()
    return "\n".join(lines)


def tree_report() -> str:
    """The deep dump: every element in each Teams window. Contains the names of people in the meeting."""
    backend = make_backend()
    try:
        if not hasattr(backend, "tree"):
            return "The Teams tree can only be read on Windows."
        lines = ["MK10 Manager: the Teams windows' whole accessibility tree, %s" % time.strftime("%Y-%m-%d %H:%M"),
                 "Read-only. It includes the names of people in the meeting: blank them out before sharing if you"
                 " want to.", ""]
        lines.extend(backend.tree())
        return "\n".join(lines)
    finally:
        backend.close()


def settings_report() -> list[str]:
    """The device entries in Teams' own settings file, exactly as written there. Shown for reference, not used."""
    if not IS_WIN:
        return []
    path = os.path.join(teams_settings_folder(), "cmd_settings.json")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        return ["Teams' cmd_settings.json: can't read it (%s)" % e]
    key = re.compile(r'"([A-Za-z_]*(?:[Cc]amera|[Mm]icrophone|[Ss]peaker|[Rr]inger|[Dd]evice|[Aa]udio)[A-Za-z_]*)"'
                     r'\s*:\s*("(?:[^"\\]|\\.){0,200}"|true|false|-?\d+)')
    found = sorted({"%s: %s" % m.groups() for m in key.finditer(text)})
    return ["Device entries in Teams' cmd_settings.json (as written; not used): %s" % ("none" if not found else "")] \
        + ["  " + f for f in found[:40]]


def cameras_report() -> list[str]:
    """Which cameras Windows has, and any trace of which one Teams chose in its own settings files."""
    if not IS_WIN:
        return ["Cameras: only listed on Windows."]
    import subprocess
    lines = []
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-PnpDevice -PresentOnly -Class Camera,Image -ErrorAction SilentlyContinue | "
                              "ForEach-Object { $_.FriendlyName }"],
                             capture_output=True, text=True, timeout=30).stdout
        cameras = sorted({line.strip() for line in out.splitlines() if line.strip()})
    except (OSError, subprocess.SubprocessError) as e:
        cameras = []
        lines.append("Cameras: couldn't ask Windows (%s)" % e)
    lines.append("Cameras Windows knows: %s" % (", ".join(cameras) or "none found"))
    root = teams_settings_folder()
    if not os.path.isdir(root):
        lines.append("Teams' settings folder wasn't found (%s)" % root)
        return lines
    key = re.compile(r'"([A-Za-z_]*(?:[Cc]amera|[Vv]ideo[Dd]evice|[Ww]ebcam)[A-Za-z_]*)"\s*:\s*("[^"]{0,120}"|true|false|\d+)')
    hits = []
    for folder, _dirs, files in os.walk(root):
        if folder[len(root):].count(os.sep) > 3:
            continue
        for f in files:
            if not f.lower().endswith(".json"):
                continue
            path = os.path.join(folder, f)
            try:
                if os.path.getsize(path) > 3_000_000:
                    continue
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            found = ["%s: %s" % m.groups() for m in key.finditer(text)][:12]
            found += ["mentions %r" % c for c in cameras if c and c in text]
            if found:
                hits.append("  %s\n    %s" % (os.path.relpath(path, root), "\n    ".join(found)))
    lines.append("Camera settings in Teams' own files: %s" % ("\n" + "\n".join(hits) if hits else "none found"))
    return lines


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
    #: command -> (the state it changes, the value it wants or None to flip it, the button it presses)
    COMMANDS = {"mute_on": ("muted", True, "mic"), "mute_off": ("muted", False, "mic"),
                "toggle": ("muted", None, "mic"),
                "camera_on": ("camera", True, "camera"), "camera_off": ("camera", False, "camera"),
                "camera_toggle": ("camera", None, "camera"),
                "share_stop": ("sharing", False, "share"), "share_toggle": ("sharing", None, "share")}

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
            self.state = TeamsState(available=False, note="Microsoft Teams is switched off on the Integrations tab.")
            self.changed.emit()
        self._poke.set()

    def refresh_soon(self):
        self._poke.set()

    @property
    def sync_safe(self) -> bool:
        """False when the backend's "muted" already includes the PC microphone being muted (teams-for-linux
        judges it from the call's sound), so keeping the two in step would feed on itself."""
        return bool(getattr(self.backend, "SYNC_SAFE", True))

    def command(self, kind: str, timeout: float = 5.0):
        """One of COMMANDS. Blocks until done: call it from a worker, never the GUI thread. Returns (ok, message);
        "already" means nothing needed pressing."""
        if kind not in self.COMMANDS:
            return False, "Unknown Teams command %s" % kind
        if not self._enabled:
            return False, "Microsoft Teams is switched off on the Integrations tab."
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
        old = self.state
        if new.in_call:
            # Only Teams' own clock: a time counted from when this app noticed the call would be a guess.
            since = old.call_since if old.in_call and old.call_since else 0.0
            if new.started:
                # Teams' clock counts whole seconds, so the call began up to a second before `started`: the
                # earliest reading is the closest. A jump of more than a couple of seconds is a new clock.
                since = min(since, new.started) if since and abs(since - new.started) < 2.0 else new.started
            new = replace(new, call_since=since)
        self.state = new
        if not new.same(old):
            self.changed.emit()

    def _serve_requests(self):
        while True:
            try:
                kind, done, result = self._requests.get_nowait()
            except queue.Empty:
                return
            try:
                ok, message = self._do(kind) if self._enabled else (False, "Microsoft Teams is switched off.")
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
        field_name, want, control = self.COMMANDS[kind]
        now = self._read()
        self._misses = 0
        self._publish(now)
        if not now.available:
            return False, now.note or "Teams can't be read here"
        if not now.in_call:
            return False, "Not in a Teams call"
        current = getattr(now, field_name)
        if current is None:
            return False, "Can't tell what Teams' %s button means (it says %r), so nothing was pressed" % (
                control, now.name_of(CONTROLS[control]))
        if want is None:
            want = not current
        if current == want:
            return True, "already"
        if field_name == "sharing" and want:
            # Share opens Teams' picker: what to share is chosen there, so there's nothing to wait for here.
            ok, message = self.backend.press(control, focus=True)
            return (True, "Teams' share picker is open") if ok else (False, message)
        ok, message = self.backend.press(STOP_SHARE if field_name == "sharing" else control, want=want)
        if not ok:
            return False, message
        deadline = time.monotonic() + self.CONFIRM_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.15)
            after = self._read()
            if after.in_call and getattr(after, field_name) == want:
                self._publish(after)
                return True, ""
        what = {"muted": ("muted", "live"), "camera": ("with the camera on", "with the camera off"),
                "sharing": ("sharing", "not sharing")}[field_name]
        return False, "Pressed Teams' %s button, but Teams still shows you %s" % (control, what[1] if want else what[0])
