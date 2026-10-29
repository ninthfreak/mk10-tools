"""A teams-for-linux D-Bus control that only exists in the tests. Run as its own process on a private session
bus (an in-process service would deadlock: Qt's blocking D-Bus call holds Python's lock while it waits).

    python fake_teams_for_linux.py [BUS_NAME] [in-call|idle]

It answers GetState, Mute, Unmute, ToggleMute, ToggleVideo and ToggleHandRaise the way teams-for-linux's
dbus-control.md describes: Mute/Unmute return false when the state already matches or isn't known.
"""

import json
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusVirtualObject

BUS = sys.argv[1] if len(sys.argv) > 1 else "com.github.IsmaelMartinez.teams_for_linux"
PATH = "/com/github/IsmaelMartinez/teams_for_linux"
INTERFACE = "com.github.IsmaelMartinez.teams_for_linux.Control"
IN_CALL = (sys.argv[2] if len(sys.argv) > 2 else "in-call") == "in-call"

state = {"presenceStatus": "Available", "presenceStatusCode": 1, "inCall": IN_CALL, "incomingCall": False,
         "incomingCallCaller": None, "cameraEnabled": False,
         "microphoneState": "silent" if IN_CALL else "unknown",
         "microphoneControlState": "unmuted" if IN_CALL else "unknown",
         "screenSharing": False, "meetingStarted": False}


def set_mute(muted: bool, force: bool) -> bool:
    want = "muted" if muted else "unmuted"
    current = state["microphoneControlState"]
    if current == want or (current not in ("muted", "unmuted") and not force):
        return False
    toggle_mute()
    return True


def toggle_mute():
    muted = state["microphoneControlState"] != "muted"
    state["microphoneControlState"] = "muted" if muted else "unmuted"
    state["microphoneState"] = "muted" if muted else "silent"


class Control(QDBusVirtualObject):
    def introspect(self, path):
        return ""

    def handleMessage(self, message, connection):
        if message.interface() != INTERFACE:
            return False
        member, args = message.member(), message.arguments()
        if member == "GetState":
            reply = json.dumps(state)
        elif member in ("Mute", "Unmute"):
            if len(args) != 1 or not isinstance(args[0], bool):
                connection.send(message.createErrorReply("org.freedesktop.DBus.Error.InvalidArgs", "want b"))
                return True
            reply = set_mute(member == "Mute", args[0])
        elif member == "ToggleMute":
            toggle_mute()
            reply = True
        elif member == "ToggleVideo":
            state["cameraEnabled"] = not state["cameraEnabled"]
            reply = True
        elif member == "ToggleHandRaise":
            reply = True
        else:
            connection.send(message.createErrorReply("org.freedesktop.DBus.Error.UnknownMethod", member))
            return True
        connection.send(message.createReply(reply))
        return True


app = QCoreApplication([])
control = Control()
bus = QDBusConnection.sessionBus()
if not (bus.registerService(BUS) and bus.registerVirtualObject(PATH, control)):
    sys.exit("couldn't register %s" % BUS)
print("ready", flush=True)
app.exec()
