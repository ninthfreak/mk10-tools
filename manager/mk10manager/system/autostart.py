"""Start MK10 Manager when you log in (Windows registry Run key, or a Linux autostart .desktop file)."""

import os
import shlex
import sys
from pathlib import Path

NAME = "MK10 Manager"


def launch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    root = Path(__file__).resolve().parents[2]
    launcher = root / "launch.pyw"
    exe = sys.executable
    if sys.platform.startswith("win"):
        w = Path(exe).with_name("pythonw.exe")
        if w.exists():
            exe = str(w)
    return [exe, str(launcher), "--minimized"]


def _desktop_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / "mk10-manager.desktop"


def is_enabled() -> bool:
    if sys.platform.startswith("win"):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, NAME)
                return True
        except OSError:
            return False
    return _desktop_file().exists()


def set_enabled(on: bool) -> tuple[bool, str]:
    cmd = launch_command()
    try:
        if sys.platform.startswith("win"):
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                                winreg.KEY_SET_VALUE) as k:
                if on:
                    winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, " ".join('"%s"' % c if " " in c else c
                                                                          for c in cmd))
                else:
                    try:
                        winreg.DeleteValue(k, NAME)
                    except FileNotFoundError:
                        pass
            return True, ""
        f = _desktop_file()
        if on:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("[Desktop Entry]\nType=Application\nName=%s\nExec=%s\nX-GNOME-Autostart-enabled=true\n"
                         "Terminal=false\n" % (NAME, " ".join(shlex.quote(c) for c in cmd)), encoding="utf-8")
        elif f.exists():
            f.unlink()
        return True, ""
    except OSError as e:
        return False, str(e)
