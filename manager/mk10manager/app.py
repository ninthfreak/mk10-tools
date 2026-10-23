"""Start-up: one copy at a time, command-line control of a running copy, then the window and tray."""

import argparse
import getpass
import hashlib
import logging
import logging.handlers
import sys
import threading
import traceback

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .config import APP_NAME, Config, config_dir
from .render.icons import RES

USAGE = """examples:
  launch.pyw                      open MK10 Manager (or bring the running one to the front)
  launch.pyw --minimized          start hidden in the tray
  launch.pyw set mode=meeting     set a variable in the running app
  launch.pyw page Browser         switch the running app to a page
  launch.pyw message "Build done" 3   show a message on the small screen for 3 seconds
  launch.pyw nowplaying           print what this PC says is playing, and exit (a check, not a command)"""


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "mk10-manager-" + hashlib.sha1(user.encode()).hexdigest()[:10]


def parse_args(argv):
    p = argparse.ArgumentParser(prog="mk10-manager", description=APP_NAME, epilog=USAGE,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    p.add_argument("command", nargs="?", choices=["show", "set", "page", "message", "nowplaying"],
                   help=argparse.SUPPRESS)
    p.add_argument("args", nargs="*", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def send_to_running(message: str) -> bool:
    s = QLocalSocket()
    s.connectToServer(server_name())
    if not s.waitForConnected(500):
        return False
    s.write(message.encode("utf-8") + b"\n")
    s.flush()
    s.waitForBytesWritten(1000)
    s.disconnectFromServer()
    return True


def build_message(ns) -> str | None:
    if ns.command in (None, "show"):
        return "show"
    if ns.command == "set":
        if not ns.args or "=" not in ns.args[0]:
            raise SystemExit("Use: set NAME=VALUE")
        name, value = " ".join(ns.args).split("=", 1)
        return "set\t%s\t%s" % (name.strip(), value)
    if ns.command == "page":
        if not ns.args:
            raise SystemExit("Use: page NAME")
        return "page\t%s" % " ".join(ns.args)
    if ns.command == "message":
        if not ns.args:
            raise SystemExit('Use: message "TEXT" [SECONDS]')
        secs = ns.args[1] if len(ns.args) > 1 else "3"
        return "message\t%s\t%s" % (ns.args[0], secs)
    return None


class CommandServer:
    def __init__(self, window, ctl):
        self.window, self.ctl = window, ctl
        self.server = QLocalServer()
        QLocalServer.removeServer(server_name())       # clears a stale socket left by a crash (Linux)
        self.server.listen(server_name())
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))

    def _read(self, sock):
        while sock.canReadLine():
            line = bytes(sock.readLine()).decode("utf-8", "replace").rstrip("\n")
            self.handle(line)

    def handle(self, line: str):
        parts = line.split("\t")
        cmd = parts[0]
        if cmd == "show":
            self.window.show_editor()
        elif cmd == "set" and len(parts) >= 3:
            self.ctl.set_variable(parts[1], parts[2])
        elif cmd == "page" and len(parts) >= 2:
            p = self.ctl.config.page_by_name(parts[1])
            if p:
                self.ctl.set_page(p["id"])
            else:
                self.ctl.notify.emit("No page called %s" % parts[1])
        elif cmd == "message" and len(parts) >= 2:
            try:
                secs = float(parts[2]) if len(parts) > 2 else 3
            except ValueError:
                secs = 3
            self.ctl.flash(parts[1], secs)


def setup_logging() -> str:
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "mk10-manager.log"
    h = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    if sys.stderr:
        root.addHandler(logging.StreamHandler(sys.stderr))

    def hook(kind, value, tb):
        logging.getLogger("mk10").error("Unexpected error:\n%s", "".join(traceback.format_exception(kind, value, tb)))
    sys.excepthook = hook

    def thread_hook(args):
        logging.getLogger("mk10").error("Unexpected error in %s:\n%s", getattr(args.thread, "name", "a thread"),
                                        "".join(traceback.format_exception(args.exc_type, args.exc_value,
                                                                           args.exc_traceback)))
    threading.excepthook = thread_hook
    try:
        import faulthandler
        global _fault_file
        _fault_file = open(folder / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(_fault_file)
    except Exception:
        pass
    return str(path)


def main(argv=None):
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    icon = QIcon(str(RES / "icon.png"))
    app.setWindowIcon(icon)
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mk10.manager")
        except Exception:
            pass

    if ns.command == "nowplaying":
        # A local check, not something to hand to a running copy.
        from .system.nowplaying import report
        print(report())
        return 0

    msg = build_message(ns)
    if send_to_running(msg):
        return 0
    if ns.command not in (None, "show"):
        if sys.stderr:
            print("MK10 Manager isn't running, so there's nothing to send that to.", file=sys.stderr)
        return 1

    log_path = setup_logging()
    log = logging.getLogger("mk10")
    log.info("Starting %s (Python %s, %s)", APP_NAME, sys.version.split()[0], sys.platform)
    from .controller import Controller
    from .ui.main_window import MainWindow
    config = Config.load()
    ctl = Controller(config)
    ctl.notify.connect(lambda t: log.info("notice: %s", t))
    ctl.device.status_changed.connect(lambda t: log.info("device: %s", t))
    ctl.log_path = log_path
    win = MainWindow(ctl, icon)
    server = CommandServer(win, ctl)    # noqa: F841 (kept alive by the reference below)
    win._command_server = server
    app.setQuitOnLastWindowClosed(win.tray is None)
    ctl.start()
    if not (ns.minimized or config.settings.get("start_minimized")) or win.tray is None:
        win.show()
    if config.load_problem:
        log.warning(config.load_problem)
        from PySide6.QtWidgets import QMessageBox
        win.show_editor()
        QMessageBox.warning(win, APP_NAME, config.load_problem)
    rc = app.exec()
    return rc
