"""Start MK10 Manager. Double-click on Windows (opens without a console), or run: python3 launch.pyw"""

import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _fail(text):
    try:
        with open(os.path.join(HERE, "startup-error.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    if sys.platform.startswith("win"):
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "MK10 Manager", 0x10)
    elif sys.stderr:
        print(text, file=sys.stderr)
    sys.exit(1)


# Commands that print a report. They need a real console, so they must not go to pythonw.exe.
CONSOLE_COMMANDS = {"nowplaying", "teams"}
WANTS_CONSOLE = bool(CONSOLE_COMMANDS.intersection(sys.argv[1:]))


def _venv_python(console=False):
    if sys.platform.startswith("win"):
        exe = os.path.join(HERE, ".venv", "Scripts", "python.exe" if console else "pythonw.exe")
    else:
        exe = os.path.join(HERE, ".venv", "bin", "python")
    return exe if os.path.exists(exe) else None


# Double-clicked with the system Python? Hand over to the app's own Python in .venv, which has the requirements.
venv = _venv_python(WANTS_CONSOLE)
if venv and os.path.normcase(os.path.abspath(sys.prefix)) != os.path.normcase(os.path.join(HERE, ".venv")):
    if WANTS_CONSOLE:
        # Stay attached so the report reaches whoever asked for it.
        sys.exit(subprocess.call([venv, os.path.abspath(__file__)] + sys.argv[1:], cwd=HERE))
    subprocess.Popen([venv, os.path.abspath(__file__)] + sys.argv[1:], cwd=HERE)
    sys.exit(0)

if sys.version_info < (3, 10):
    _fail("MK10 Manager needs Python 3.10 or newer. This is Python %d.%d." % sys.version_info[:2])
try:
    import PySide6  # noqa: F401
    import serial  # noqa: F401
except ImportError as e:
    _fail("Something MK10 Manager needs isn't installed (%s).\n\nRun run-windows.bat (or run-linux.sh) once to "
          "set it up." % e.name)

try:
    from mk10manager.app import main
    code = main()
except SystemExit:
    raise
except BaseException:
    _fail("MK10 Manager stopped with an error:\n\n" + traceback.format_exc()[-1500:] +
          "\n\nThis is also saved in startup-error.txt next to launch.pyw.")
sys.exit(code)
