"""Which app has focus right now. Windows: Win32 API. Linux: depends on the desktop (see detect())."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from PySide6.QtCore import QObject, Signal

IS_WIN = sys.platform.startswith("win")
OWN_PID = os.getpid()


def _run(cmd, timeout=2.0):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _norm(app: str) -> str:
    app = (app or "").strip()
    if app.lower().endswith(".exe"):
        app = app[:-4]
    return app.lower()


# ---------------------------------------------------------------- Windows
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _u = ctypes.WinDLL("user32", use_last_error=True)
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _u.GetForegroundWindow.restype = wintypes.HWND
    _u.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    _u.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _u.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    _k.OpenProcess.restype = wintypes.HANDLE
    _k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                              ctypes.POINTER(wintypes.DWORD))
    _k.CloseHandle.argtypes = (wintypes.HANDLE,)
    _u.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u.EnumChildWindows.argtypes = (wintypes.HWND, _WNDENUMPROC, wintypes.LPARAM)

    # The taskbar, Start menu, Alt-Tab and the desktop take focus for a moment while you switch apps.
    # Treat them as "no change" so they don't flip pages.
    SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "MultitaskingViewFrame", "XamlExplorerHostIslandWindow",
                     "TopLevelWindowForOverflowXamlIsland", "NotifyIconOverflowWindow", "Progman", "WorkerW",
                     "ForegroundStaging", "Windows.UI.Core.CoreWindow"}
    SHELL_APPS = {"startmenuexperiencehost", "searchhost", "searchapp", "shellexperiencehost", "lockapp",
                  "textinputhost", "shellhost"}

    def _pid(hwnd) -> int:
        pid = wintypes.DWORD()
        _u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _exe(pid: int) -> str:
        h = _k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            size = wintypes.DWORD(1024)
            pbuf = ctypes.create_unicode_buffer(1024)
            if _k.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                return os.path.basename(pbuf.value)
            return ""
        finally:
            _k.CloseHandle(h)

    def _windows():
        hwnd = _u.GetForegroundWindow()
        if not hwnd:
            return "self"
        cls = ctypes.create_unicode_buffer(256)
        _u.GetClassNameW(hwnd, cls, 256)
        if cls.value in SHELL_CLASSES:
            return "self"
        n = _u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        _u.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        pid = _pid(hwnd)
        if pid == OWN_PID:
            return "self"
        app = _norm(_exe(pid))
        if app == "applicationframehost":
            # Store apps live in a child window owned by the real process.
            found = []

            def cb(child, _lp):
                cpid = _pid(child)
                if cpid != pid:
                    found.append(cpid)
                    return False
                return True
            _u.EnumChildWindows(hwnd, _WNDENUMPROC(cb), 0)
            if found:
                app = _norm(_exe(found[0])) or app
        if app in SHELL_APPS or (app == "explorer" and (not title or title in ("Program Manager", "Task Switching",
                                                                                 "Task View"))):
            return "self"
        return app, title


# ---------------------------------------------------------------- Linux helpers
def _xprop():
    out = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    m = re.search(r"window id # (0x[0-9a-fA-F]+)", out or "")
    if not m or int(m.group(1), 16) == 0:
        return None
    wid = m.group(1)
    out = _run(["xprop", "-id", wid, "WM_CLASS", "_NET_WM_NAME", "WM_NAME", "_NET_WM_PID"]) or ""
    return parse_xprop(out)


def parse_xprop(out: str):
    cls = re.search(r'WM_CLASS\([^)]*\) = "([^"]*)"(?:, "([^"]*)")?', out)
    title = re.search(r'_NET_WM_NAME\([^)]*\) = "(.*)"', out) or re.search(r'WM_NAME\([^)]*\) = "(.*)"', out)
    pid = re.search(r"_NET_WM_PID\([^)]*\) = (\d+)", out)
    if pid and int(pid.group(1)) == OWN_PID:
        return "self"
    app = (cls.group(2) or cls.group(1)) if cls else ""
    return _norm(app), (title.group(1) if title else "")


def _hyprland():
    out = _run(["hyprctl", "activewindow", "-j"])
    try:
        d = json.loads(out or "")
    except ValueError:
        return None
    if not isinstance(d, dict) or not d:
        return None
    if d.get("pid") == OWN_PID:
        return "self"
    return _norm(d.get("class") or d.get("initialClass") or ""), d.get("title") or ""


def _sway():
    out = _run(["swaymsg", "-t", "get_tree", "-r"])
    try:
        tree = json.loads(out or "")
    except ValueError:
        return None
    return parse_sway(tree)


def parse_sway(tree):
    stack = [tree]
    while stack:
        n = stack.pop()
        if n.get("focused") and n.get("type") in ("con", "floating_con"):
            if n.get("pid") == OWN_PID:
                return "self"
            app = n.get("app_id") or (n.get("window_properties") or {}).get("class") or ""
            return _norm(app), n.get("name") or ""
        stack.extend(n.get("nodes", []) + n.get("floating_nodes", []))
    return None


def _kdotool():
    app = _run(["kdotool", "getactivewindow", "getwindowclassname"])
    if app is None:
        return None
    pid = (_run(["kdotool", "getactivewindow", "getwindowpid"]) or "").strip()
    if pid.isdigit() and int(pid) == OWN_PID:
        return "self"
    title = _run(["kdotool", "getactivewindow", "getwindowname"]) or ""
    return _norm(app.strip()), title.strip()


def _gnome_window_calls():
    out = _run(["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
                "--object-path", "/org/gnome/Shell/Extensions/Windows",
                "--method", "org.gnome.Shell.Extensions.Windows.List"])
    m = re.match(r"\('(.*)',\)\s*$", (out or "").strip(), re.S)
    if not m:
        return None
    try:
        wins = json.loads(m.group(1))
    except ValueError:
        return None
    for w in wins:
        if w.get("focus"):
            if w.get("pid") == OWN_PID:
                return "self"
            return _norm(w.get("wm_class") or w.get("wm_class_instance") or ""), w.get("title") or ""
    return None


def detect():
    """(method name, function or None, note for the user)."""
    if IS_WIN:
        return "Windows", _windows, ""
    if not sys.platform.startswith("linux"):
        return "none", None, "Active-app detection isn't supported on this system."
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        return "Hyprland", _hyprland, ""
    if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
        return "Sway", _sway, ""
    if wayland and "kde" in desktop:
        if shutil.which("kdotool"):
            return "KDE (kdotool)", _kdotool, ""
        return "none", None, "On KDE Wayland, install kdotool to switch pages by app."
    if wayland and "gnome" in desktop:
        if shutil.which("gdbus") and _gnome_window_calls() is not None:
            return "GNOME (Window Calls extension)", _gnome_window_calls, ""
        return "none", None, ("GNOME on Wayland doesn't tell other apps which window is active. "
                              "The 'Window Calls' GNOME extension makes it possible (untested).")
    if os.environ.get("DISPLAY") and shutil.which("xprop"):
        note = "Wayland session: only older X11 apps will be seen." if wayland else ""
        return "X11 (xprop)", _xprop, note
    return "none", None, "Install xprop (x11-utils) to switch pages by app."


class ActiveAppWatcher(QObject):
    changed = Signal(str, str)        # app, window title

    def __init__(self, interval: float = 0.35, parent=None):
        super().__init__(parent)
        self.method, self._fn, self.note = detect()
        self.interval = interval
        self.app, self.title = "", ""
        self._stop = threading.Event()
        self._thread = None

    @property
    def available(self) -> bool:
        return self._fn is not None

    def start(self):
        if not self._fn or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="active-app", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        candidate = None
        while not self._stop.is_set():
            try:
                got = self._fn()
            except Exception:
                got = None
            if got and got != "self":
                # Only report a change once it has held for two checks in a row, so a window that has focus for a
                # split second while you switch (a menu, a notification) doesn't flip pages.
                if got == candidate and got != (self.app, self.title):
                    self.app, self.title = got
                    self.changed.emit(*got)
                candidate = got
            self._stop.wait(self.interval)
