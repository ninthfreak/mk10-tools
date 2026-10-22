"""What a key can do. Each action type lists its fields so the editor can build its form."""

import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request

from PySide6.QtCore import QObject, Signal

from .schema import Field, fill_defaults
from .system import inputs

ACTION_TYPES = {
    "hotkey": ("Keyboard shortcut", [
        Field("keys", "Shortcut", "hotkey", "", help="Click the box and press the shortcut, or type it, "
                                                     "like Ctrl+Shift+M."),
    ]),
    "text": ("Type text", [
        Field("text", "Text", "multiline", ""),
    ]),
    "media": ("Media key", [
        Field("key", "Key", "choice", "play_pause", inputs.MEDIA_KEYS),
    ]),
    "open": ("Open a website, file or app", [
        Field("target", "Address or path", "file", "", help="A web address (https://...), a file, a folder or "
                                                             "a program."),
    ]),
    "command": ("Run a command", [
        Field("command", "Command", "text", "", help="Runs in your system shell, in the background."),
        Field("show_output", "Show its output on the small screen", "bool", False),
    ]),
    "http": ("Web request", [
        Field("method", "Method", "choice", "POST", [("GET", "GET"), ("POST", "POST"), ("PUT", "PUT")]),
        Field("url", "Address", "text", "", help="For example a Home Assistant webhook."),
        Field("headers", "Headers", "multiline", "", help="One per line, like Authorization: Bearer xyz"),
        Field("body", "Body", "multiline", "", show_if=("method", ("POST", "PUT"))),
        Field("show_result", "Show the result on the small screen", "bool", False),
    ]),
    "page": ("Change page", [
        Field("mode", "Go to", "choice", "goto", [("goto", "A specific page"), ("next", "Next page"),
                                                  ("previous", "Previous page"), ("back", "Last page")]),
        Field("page", "Page", "page", None, show_if=("mode", ("goto",))),
    ]),
    "variable": ("Set a variable", [
        Field("mode", "Change", "choice", "set", [("set", "Set to a value"), ("toggle", "Toggle on / off"),
                                                  ("cycle", "Step through a list"), ("clear", "Clear")]),
        Field("name", "Variable", "variable", "mode"),
        Field("value", "Value", "text", "", show_if=("mode", ("set",))),
        Field("values", "List (comma separated)", "text", "work, meeting, break", show_if=("mode", ("cycle",))),
    ]),
    "message": ("Show a message", [
        Field("text", "Message", "template", "", help="Shown over the small screen."),
        Field("seconds", "For (seconds)", "int", 2, minimum=1, maximum=60),
    ]),
    "brightness": ("Brightness", [
        Field("mode", "Change", "choice", "set", [("set", "Set to"), ("up", "Brighter"), ("down", "Dimmer")]),
        Field("value", "Level or step (%)", "int", 50, minimum=0, maximum=100),
    ]),
    "delay": ("Wait", [
        Field("ms", "Milliseconds", "int", 200, minimum=10, maximum=60000),
    ]),
}

UI_ACTIONS = {"page", "variable", "message", "brightness", "open"}


def new_action(kind: str) -> dict:
    a = {"type": kind}
    a.update(fill_defaults({}, ACTION_TYPES[kind][1]))
    return a


def summary(action: dict, page_name=lambda pid: pid) -> str:
    t = action.get("type")
    label = ACTION_TYPES.get(t, ("Unknown",))[0]
    a = fill_defaults(action, ACTION_TYPES.get(t, ("", []))[1])
    if t == "hotkey":
        return "Shortcut  %s" % (a["keys"] or "(not set)")
    if t == "text":
        s = a["text"].replace("\n", " ")
        return "Type  “%s”" % (s[:30] + ("..." if len(s) > 30 else ""))
    if t == "media":
        return dict(inputs.MEDIA_KEYS).get(a["key"], a["key"])
    if t == "open":
        return "Open  %s" % (a["target"] or "(not set)")
    if t == "command":
        return "Run  %s" % (a["command"] or "(not set)")
    if t == "http":
        return "%s  %s" % (a["method"], a["url"] or "(no address)")
    if t == "page":
        if a["mode"] == "goto":
            return "Go to page  %s" % (page_name(a["page"]) if a["page"] else "(not set)")
        return {"next": "Next page", "previous": "Previous page", "back": "Last page"}.get(a["mode"], label)
    if t == "variable":
        n = a["name"] or "?"
        return {"set": "Set %s = %s" % (n, a["value"]), "toggle": "Toggle %s" % n,
                "cycle": "Step %s through %s" % (n, a["values"]), "clear": "Clear %s" % n}.get(a["mode"], label)
    if t == "message":
        return "Message  “%s”" % a["text"]
    if t == "brightness":
        return {"set": "Brightness %s%%" % a["value"], "up": "Brighter by %s%%" % a["value"],
                "down": "Dimmer by %s%%" % a["value"]}.get(a["mode"], label)
    if t == "delay":
        return "Wait %s ms" % a["ms"]
    return label


def _shell_popen(command: str, capture: bool):
    kwargs = dict(shell=True, stdin=subprocess.DEVNULL, cwd=os.path.expanduser("~"))
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    else:
        kwargs.update(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000 if capture else 0    # CREATE_NO_WINDOW when collecting output
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _kill_tree(p):
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                           creationflags=0x08000000)
        else:
            import signal
            os.killpg(p.pid, signal.SIGKILL)
    except Exception:
        p.kill()


def run_command_output(command: str, timeout: float = 20) -> str:
    try:
        p = _shell_popen(command, True)
    except OSError as e:
        return "(%s)" % e
    try:
        out, _ = p.communicate(timeout=timeout)
        return (out or "").strip()
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        try:
            p.communicate(timeout=2)
        except Exception:
            pass
        return "(command took too long)"


def http_request(a: dict) -> str:
    headers = {}
    for line in (a.get("headers") or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    body = (a.get("body") or "").encode() if a.get("method") in ("POST", "PUT") else None
    if body and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json" if body.strip()[:1] in (b"{", b"[") else "text/plain"
    req = urllib.request.Request(a.get("url") or "", data=body, headers=headers, method=a.get("method") or "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read(2000).decode("utf-8", "replace").strip()
            return "%d %s" % (r.status, text[:80]) if text else "%d %s" % (r.status, r.reason)
    except Exception as e:
        return "Failed: %s" % e


class ActionRunner(QObject):
    """Runs a key's actions in order on a worker thread. Actions that change the app itself (page, variables,
    messages, brightness, opening things) are handed back to the GUI thread through ui_action."""

    ui_action = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="actions", daemon=True)
        self._thread.start()

    def run(self, actions: list[dict]):
        if actions:
            self._q.put([dict(a) for a in actions])

    def _loop(self):
        while True:
            batch = self._q.get()
            for a in batch:
                try:
                    self._one(a)
                except Exception as e:      # never let one bad action kill the worker
                    try:
                        what = summary(a)
                    except Exception:
                        what = str(a.get("type"))
                    self.failed.emit("%s failed: %s" % (what, e))

    def _one(self, a: dict):
        t = a.get("type")
        if t in UI_ACTIONS:
            self.ui_action.emit(a)
            return
        if t == "hotkey":
            ok, msg = inputs.send_hotkey(a.get("keys", ""))
        elif t == "text":
            ok, msg = inputs.type_text(a.get("text", ""))
        elif t == "media":
            ok, msg = inputs.media(a.get("key", "play_pause"))
        elif t == "delay":
            try:
                ms = int(a.get("ms") or 0)
            except (TypeError, ValueError):
                ms = 0
            time.sleep(max(0, ms) / 1000)
            ok, msg = True, ""
        elif t == "command":
            cmd = (a.get("command") or "").strip()
            if not cmd:
                ok, msg = False, "No command set"
            elif a.get("show_output"):
                def collect():
                    self.ui_action.emit({"type": "message", "text": run_command_output(cmd) or "(no output)",
                                         "seconds": 4, "literal": True})
                threading.Thread(target=collect, daemon=True).start()
                ok, msg = True, ""
            else:
                _shell_popen(cmd, False)
                ok, msg = True, ""
        elif t == "http":
            if not a.get("url"):
                ok, msg = False, "No address set"
            else:
                def send():
                    result = http_request(a)
                    if a.get("show_result") or result.startswith("Failed"):
                        self.ui_action.emit({"type": "message", "text": result, "seconds": 3, "literal": True})
                threading.Thread(target=send, daemon=True).start()
                ok, msg = True, ""
        else:
            ok, msg = False, "Unknown action %s" % t
        if not ok:
            self.failed.emit("%s: %s" % (summary(a), msg))
