"""Teams on Linux, through teams-for-linux's own D-Bus control (its v2.25.0, October 2026, and later).

teams-for-linux (github.com/IsmaelMartinez/teams-for-linux, released about weekly) is a Teams client for Linux
that publishes its call state on the session bus and takes mute, camera and hand commands. Leaning on it means
keeping up with Teams' changes is their job, not ours. It's off until switched on in its config.json:
``"dbusControl": {"enabled": true}``, then restart it.

The contract (docs-site/docs/development/dbus-control.md, read 29 October 2026):

- Bus name ``com.github.IsmaelMartinez.teams_for_linux`` (or the ``busName`` set for a profile, such as
  ``…teams_for_linux.Work``; any name starting with the default is found), object path
  ``/com/github/IsmaelMartinez/teams_for_linux``, interface ``….Control``.
- ``GetState() -> s``: JSON with inCall, cameraEnabled, screenSharing, microphoneControlState ("muted",
  "unmuted", "off", "unknown") and presence. No meeting name, call clock, devices, hand state or what's shared.
- ``Mute(b force)`` / ``Unmute(b force)`` press Teams' Ctrl+Shift+M only when its own reading disagrees, and
  return whether they sent it. ``ToggleVideo``, ``ToggleHandRaise`` and ``LeaveCall`` send Teams' shortcuts.
  Nothing starts or stops sharing.

Its microphone reading comes from the call's WebRTC statistics, not Teams' mute button: the outgoing track
switched off, or no sound for about half a second, counts as muted (its speakingIndicator.js says a microphone
with its own noise reduction "can also read zero while unmuted and quiet"). So a microphone muted on the PC
reads as muted in Teams too, and keeping the PC microphone in step with it would be circular: SYNC_SAFE is False.

It's called through ``busctl``, the way teams-for-linux's own docs do, as a separate process: Qt's blocking
D-Bus call holds Python's lock for its whole wait, so a slow answer would freeze the window.
"""

import json
import shutil
import subprocess
import time

from .teams import STOP_SHARE, TeamsState

BUS_PREFIX = "com.github.IsmaelMartinez.teams_for_linux"
OBJECT_PATH = "/com/github/IsmaelMartinez/teams_for_linux"
INTERFACE = BUS_PREFIX + ".Control"
HOW_TO = ("teams-for-linux isn't running, or its D-Bus control is off: add \"dbusControl\": {\"enabled\": true} "
          "to its config.json and restart it.")
NO_SHARING = "teams-for-linux can't start or stop sharing from outside: use Teams' own button."


def busctl(args: list, timeout: float = 2.0):
    """busctl --user --json=short ARGS. (True, the reply's values) or (False, why not)."""
    exe = shutil.which("busctl")
    if not exe:
        return False, "busctl wasn't found (it comes with systemd)."
    try:
        out = subprocess.run([exe, "--user", "--json=short", "--timeout=%gs" % timeout] + list(args),
                             capture_output=True, text=True, timeout=timeout + 2)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if out.returncode != 0:
        return False, (out.stderr or out.stdout).strip() or "busctl failed (%d)" % out.returncode
    try:
        return True, json.loads(out.stdout).get("data") or []
    except (ValueError, AttributeError):
        return False, "busctl's answer wasn't JSON: %r" % out.stdout[:200]


def state_from_snapshot(snapshot: dict, now: float | None = None) -> TeamsState:
    """teams-for-linux's GetState JSON as the TeamsState the keys and screen read. Only what it reports."""
    in_call = snapshot.get("inCall") is True
    mic = snapshot.get("microphoneControlState")
    camera, sharing = snapshot.get("cameraEnabled"), snapshot.get("screenSharing")
    return TeamsState(available=True, running=True, in_call=in_call,
                      muted={"muted": True, "unmuted": False}.get(mic) if in_call else None,
                      camera=camera if in_call and isinstance(camera, bool) else None,
                      sharing=sharing if in_call and isinstance(sharing, bool) else None,
                      read_at=time.monotonic() if now is None else now)


class TeamsForLinux:
    """The TeamsMonitor backend on Linux. `run` is busctl() unless a test gives its own."""

    SYNC_SAFE = False            # its "muted" includes a microphone muted on the PC (see above)
    NAMES_SECONDS = 10.0         # how long a found bus name is trusted before looking again

    def __init__(self, run=None):
        self.run = run or busctl
        self.note = ""
        self._names: list[str] = []
        self._names_at = 0.0
        self._name = None        # the instance last read: the one in a call, else the first

    def _find(self, fresh: bool = False):
        """teams-for-linux's bus names: (True, [names]) or (False, why the bus couldn't be asked)."""
        if not fresh and self._names and time.monotonic() - self._names_at < self.NAMES_SECONDS:
            return True, self._names
        ok, data = self.run(["call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                             "ListNames"])
        if not ok:
            return False, data
        names = data[0] if data and isinstance(data[0], list) else []
        self._names = sorted(n for n in names if n == BUS_PREFIX or n.startswith(BUS_PREFIX + "."))
        self._names_at = time.monotonic()
        return True, self._names

    def _call(self, name: str, method: str, *typed_args):
        """typed_args are D-Bus signature characters and values, as busctl takes them: ("b", "false")."""
        return self.run(["call", name, OBJECT_PATH, INTERFACE, method] + [str(a) for a in typed_args])

    def _snapshot(self, name: str):
        ok, data = self._call(name, "GetState")
        if not ok:
            return None
        try:
            snapshot = json.loads(data[0])
        except (ValueError, TypeError, IndexError):
            return None
        return snapshot if isinstance(snapshot, dict) else None

    # ---- the backend interface
    def read(self) -> TeamsState:
        ok, names = self._find()
        if not ok:
            return TeamsState(available=False, note="Can't ask the session bus: %s" % names)
        snapshots = [(n, self._snapshot(n)) for n in names]
        if names and not any(s for _n, s in snapshots):
            ok, names = self._find(fresh=True)         # it may have restarted under another name
            snapshots = [(n, self._snapshot(n)) for n in names] if ok else []
        snapshots = [(n, s) for n, s in snapshots if s]
        if not snapshots:
            self._name = None
            return TeamsState(available=True, running=False, note=HOW_TO, read_at=time.monotonic())
        self._name, snapshot = next(((n, s) for n, s in snapshots if s.get("inCall") is True), snapshots[0])
        return state_from_snapshot(snapshot)

    def press(self, control: str = "mic", focus: bool = False, want=None):
        """mic (Mute/Unmute when `want` is given, else ToggleMute), camera, hand. Sharing can't be done."""
        if control in ("share", STOP_SHARE):
            return False, NO_SHARING
        name = self._name
        if name is None:
            ok, names = self._find(fresh=True)
            name = names[0] if ok and names else None
        if name is None:
            return False, HOW_TO
        if control == "mic":
            call = ("ToggleMute",) if want is None else (("Mute" if want else "Unmute"), "b", "false")
        elif control == "camera":
            call = ("ToggleVideo",)
        elif control == "hand":
            call = ("ToggleHandRaise",)
        else:
            return False, "teams-for-linux has no %s control" % control
        ok, data = self._call(name, *call)
        if not ok:
            return False, "teams-for-linux: %s" % data
        if data and data[0] is True:
            return True, ""
        return False, "teams-for-linux didn't send it (it thinks that's already so, or doesn't know the state)"

    def describe(self) -> list[str]:
        """For report(): which instances answer, and exactly what each reports."""
        ok, names = self._find(fresh=True)
        if not ok:
            return ["Session bus: can't ask it. %s" % names]
        lines = ["teams-for-linux on the session bus: %s" % (", ".join(names) or "none found")]
        if not names:
            lines.append("  " + HOW_TO)
        for n in names:
            ok, data = self._call(n, "GetState")
            lines.append("  %s GetState: %s" % (n, data[0] if ok and data else "failed: %s" % data))
        lines.append("Its microphone state comes from the call's audio, not Teams' button: a quiet microphone "
                     "with its own noise gate reads as muted.")
        return lines

    def close(self):
        pass
