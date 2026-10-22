"""Keyboard shortcuts, typing and media keys, on Windows and Linux.

Windows uses SendInput directly. Linux uses whichever tool is installed: xdotool on X11; ydotool or wtype on
Wayland (xdotool there only reaches older X11 apps). Every function returns (ok, message).
"""

import os
import shutil
import subprocess
import sys
import time

IS_WIN = sys.platform.startswith("win")

# name -> (Windows virtual key, extended?, X keysym, Linux input code)
KEYS = {}


def _add(names, vk, ext, keysym, code):
    for n in names.split("|"):
        KEYS[n] = (vk, ext, keysym, code)


_LETTER_CODES = dict(a=30, b=48, c=46, d=32, e=18, f=33, g=34, h=35, i=23, j=36, k=37, l=38, m=50, n=49, o=24,
                     p=25, q=16, r=19, s=31, t=20, u=22, v=47, w=17, x=45, y=21, z=44)
for _ch, _code in _LETTER_CODES.items():
    _add(_ch, ord(_ch.upper()), False, _ch, _code)
for _d in range(10):
    _add(str(_d), 0x30 + _d, False, str(_d), 11 if _d == 0 else _d + 1)
for _f in range(1, 25):
    _code = 58 + _f if _f <= 10 else {11: 87, 12: 88}.get(_f, 183 + _f - 13)
    _add("f%d" % _f, 0x6F + _f, False, "F%d" % _f, _code)
_add("esc|escape", 0x1B, False, "Escape", 1)
_add("tab", 0x09, False, "Tab", 15)
_add("backspace", 0x08, False, "BackSpace", 14)
_add("return|enter", 0x0D, False, "Return", 28)
_add("space", 0x20, False, "space", 57)
_add("ins|insert", 0x2D, True, "Insert", 110)
_add("del|delete", 0x2E, True, "Delete", 111)
_add("home", 0x24, True, "Home", 102)
_add("end", 0x23, True, "End", 107)
_add("pgup|pageup|page up", 0x21, True, "Prior", 104)
_add("pgdown|pagedown|page down|pgdn", 0x22, True, "Next", 109)
_add("left", 0x25, True, "Left", 105)
_add("up", 0x26, True, "Up", 103)
_add("right", 0x27, True, "Right", 106)
_add("down", 0x28, True, "Down", 108)
_add("print|printscreen|sysreq", 0x2C, True, "Print", 99)
_add("pause", 0x13, False, "Pause", 119)
_add("scrolllock", 0x91, False, "Scroll_Lock", 70)
_add("capslock", 0x14, False, "Caps_Lock", 58)
_add("numlock", 0x90, True, "Num_Lock", 69)
_add("menu", 0x5D, True, "Menu", 127)
_add("-|minus", 0xBD, False, "minus", 12)
_add("=|equal", 0xBB, False, "equal", 13)
_add("[|bracketleft", 0xDB, False, "bracketleft", 26)
_add("]|bracketright", 0xDD, False, "bracketright", 27)
_add("\\|backslash", 0xDC, False, "backslash", 43)
_add(";|semicolon", 0xBA, False, "semicolon", 39)
_add("'|apostrophe", 0xDE, False, "apostrophe", 40)
_add(",|comma", 0xBC, False, "comma", 51)
_add(".|period", 0xBE, False, "period", 52)
_add("/|slash", 0xBF, False, "slash", 53)
_add("`|grave", 0xC0, False, "grave", 41)
_add("media play|toggle media play/pause|media play/pause|media pause|play_pause", 0xB3, True,
     "XF86AudioPlay", 164)
_add("media next|next", 0xB0, True, "XF86AudioNext", 163)
_add("media previous|previous", 0xB1, True, "XF86AudioPrev", 165)
_add("media stop|stop", 0xB2, True, "XF86AudioStop", 166)
_add("volume up|volume_up", 0xAF, True, "XF86AudioRaiseVolume", 115)
_add("volume down|volume_down", 0xAE, True, "XF86AudioLowerVolume", 114)
_add("volume mute|mute", 0xAD, True, "XF86AudioMute", 113)

MODIFIERS = {
    "ctrl": (0x11, False, "ctrl", 29), "control": (0x11, False, "ctrl", 29),
    "shift": (0x10, False, "shift", 42),
    "alt": (0x12, False, "alt", 56),
    "meta": (0x5B, True, "super", 125), "win": (0x5B, True, "super", 125), "super": (0x5B, True, "super", 125),
    "cmd": (0x5B, True, "super", 125),
}


def parse_hotkey(spec: str):
    """'Ctrl+Shift+M' -> (['ctrl', 'shift'], 'm'). Raises ValueError for unknown keys."""
    spec = (spec or "").strip()
    if not spec:
        raise ValueError("No shortcut set")
    if spec.endswith("++"):
        parts = spec[:-2].split("+") + ["+"]
    else:
        parts = spec.split("+")
    parts = [p.strip() for p in parts if p.strip()]
    mods, key = [], None
    for i, part in enumerate(parts):
        low = part.lower()
        if low in MODIFIERS and i < len(parts) - 1:
            mods.append(low if low not in ("control", "win", "super", "cmd") else
                        {"control": "ctrl", "win": "meta", "super": "meta", "cmd": "meta"}[low])
        else:
            if key is not None:
                raise ValueError("Use one key plus modifiers, like Ctrl+Shift+M")
            key = low
    if key is None:
        raise ValueError("No key in shortcut")
    if key == "+":
        key = "="
        if "shift" not in mods:
            mods.append("shift")
    if key not in KEYS and key not in MODIFIERS:
        raise ValueError("Unknown key: %s" % key)
    return mods, key


def _lookup(name):
    return MODIFIERS.get(name) or KEYS[name]


# ---------------------------------------------------------------- Windows
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT
    _user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
    _user32.MapVirtualKeyW.restype = wintypes.UINT
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4

    def _vk_input(vk, ext, up):
        flags = (KEYEVENTF_EXTENDEDKEY if ext else 0) | (KEYEVENTF_KEYUP if up else 0)
        scan = _user32.MapVirtualKeyW(vk, 0) & 0xFF
        return INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))

    def _unicode_input(unit, up):
        return INPUT(type=1, u=_INPUTUNION(ki=KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
                                                         0, 0)))

    def _send(events):
        arr = (INPUT * len(events))(*events)
        n = _user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
        if n != len(events):
            return False, "Windows blocked the keystrokes (error %d)." % ctypes.get_last_error()
        return True, ""

    def _win_hotkey(mods, key):
        downs = [_vk_input(*_lookup(m)[:2], False) for m in mods]
        vk, ext = _lookup(key)[:2]
        events = downs + [_vk_input(vk, ext, False), _vk_input(vk, ext, True)]
        events += [_vk_input(*_lookup(m)[:2], True) for m in reversed(mods)]
        return _send(events)

    def _win_type(text):
        events = []
        for ch in text.replace("\r\n", "\n"):
            if ch == "\n":
                events += [_vk_input(0x0D, False, False), _vk_input(0x0D, False, True)]
                continue
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                events += [_unicode_input(unit, False), _unicode_input(unit, True)]
        return _send(events) if events else (True, "")


# ---------------------------------------------------------------- Linux
def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "exit code %d" % r.returncode).strip()[:200]
    return True, ""


def _linux_tools():
    """Tools to try, best first."""
    tools = []
    if _wayland():
        if shutil.which("ydotool"):
            tools.append("ydotool")
        if shutil.which("wtype"):
            tools.append("wtype")
        if shutil.which("xdotool") and os.environ.get("DISPLAY"):
            tools.append("xdotool")
    else:
        if shutil.which("xdotool"):
            tools.append("xdotool")
        if shutil.which("ydotool"):
            tools.append("ydotool")
    return tools


NO_TOOL = ("No keystroke tool found. Install xdotool (X11) or ydotool (Wayland; its service must be running), "
           "or wtype (Sway/Hyprland).")


def _linux_hotkey(mods, key):
    errors = []
    for tool in _linux_tools():
        if tool == "xdotool":
            combo = "+".join([_lookup(m)[2] for m in mods] + [_lookup(key)[2]])
            ok, msg = _run(["xdotool", "key", "--clearmodifiers", combo])
        elif tool == "ydotool":
            codes = [_lookup(m)[3] for m in mods] + [_lookup(key)[3]]
            seq = ["%d:1" % c for c in codes] + ["%d:0" % c for c in reversed(codes)]
            ok, msg = _run(["ydotool", "key"] + seq)
        else:
            args = ["wtype"]
            for m in mods:
                args += ["-M", _lookup(m)[2] if m != "meta" else "logo"]
            args += ["-k", _lookup(key)[2]]
            for m in reversed(mods):
                args += ["-m", _lookup(m)[2] if m != "meta" else "logo"]
            ok, msg = _run(args)
        if ok:
            return True, ""
        errors.append("%s: %s" % (tool, msg))
    return False, ("; ".join(errors) if errors else NO_TOOL)


def _linux_type(text):
    errors = []
    for tool in _linux_tools():
        if tool == "xdotool":
            ok, msg = _run(["xdotool", "type", "--delay", "8", "--", text], timeout=30)
        elif tool == "ydotool":
            ok, msg = _run(["ydotool", "type", "--", text], timeout=30)
        else:
            ok, msg = _run(["wtype", "--", text], timeout=30)
        if ok:
            return True, ""
        errors.append("%s: %s" % (tool, msg))
    return False, ("; ".join(errors) if errors else NO_TOOL)


MEDIA_LINUX = {
    "play_pause": [["playerctl", "play-pause"]],
    "next": [["playerctl", "next"]],
    "previous": [["playerctl", "previous"]],
    "stop": [["playerctl", "stop"]],
    "volume_up": [["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "5%+"],
                  ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+5%"]],
    "volume_down": [["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"],
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-5%"]],
    "mute": [["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
             ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"]],
}

MEDIA_KEYS = [("play_pause", "Play / pause"), ("next", "Next track"), ("previous", "Previous track"),
              ("stop", "Stop"), ("volume_up", "Volume up"), ("volume_down", "Volume down"), ("mute", "Mute")]


# ---------------------------------------------------------------- public
def send_hotkey(spec: str):
    try:
        mods, key = parse_hotkey(spec)
    except ValueError as e:
        return False, str(e)
    if IS_WIN:
        return _win_hotkey(mods, key)
    if sys.platform.startswith("linux"):
        return _linux_hotkey(mods, key)
    return False, "Shortcuts aren't supported on this system yet."


def type_text(text: str):
    if not text:
        return True, ""
    if IS_WIN:
        return _win_type(text)
    if sys.platform.startswith("linux"):
        return _linux_type(text)
    return False, "Typing isn't supported on this system yet."


def media(key: str):
    if key not in dict(MEDIA_KEYS):
        return False, "Unknown media key %s" % key
    if IS_WIN:
        return _win_hotkey([], key)
    if sys.platform.startswith("linux"):
        for cmd in MEDIA_LINUX.get(key, []):
            if shutil.which(cmd[0]):
                ok, msg = _run(cmd)
                if ok:
                    return True, ""
        return _linux_hotkey([], key)
    return False, "Media keys aren't supported on this system yet."


def describe_backend() -> str:
    if IS_WIN:
        return "Windows SendInput"
    tools = _linux_tools()
    if tools:
        return ", ".join(tools)
    return NO_TOOL
