"""Teams on Linux through teams-for-linux's D-Bus control: a pretend busctl for the logic, and the real busctl
against a pretend teams-for-linux on a private session bus for the whole path."""

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from conftest import wait_until
from fake_audio import FakeAudio
from mk10manager.system import teams_linux as tl
from mk10manager.system.audio import INPUT, AudioMonitor
from mk10manager.system.teams import TeamsMonitor

HERE = os.path.dirname(os.path.abspath(__file__))


class FakeBusctl:
    """Answers like `busctl --user --json=short call ...` would, for one or more teams-for-linux instances."""

    def __init__(self, instances=None):
        self.instances = instances if instances is not None else {tl.BUS_PREFIX: self.snapshot()}
        self.calls = []
        self.bus_down = False

    @staticmethod
    def snapshot(in_call=True, mic="unmuted", camera=False, sharing=False):
        return {"presenceStatus": "Available", "presenceStatusCode": 1, "inCall": in_call, "incomingCall": False,
                "incomingCallCaller": None, "cameraEnabled": camera, "microphoneState": "silent",
                "microphoneControlState": mic, "screenSharing": sharing, "meetingStarted": False}

    def __call__(self, args, timeout=2.0):
        self.calls.append(list(args))
        if self.bus_down:
            return False, "Failed to connect to bus: No such file or directory"
        assert args[0] == "call"
        if args[1] == "org.freedesktop.DBus":
            return True, [["org.freedesktop.DBus", ":1.7", "org.other.App"] + list(self.instances)]
        name, path, iface, method = args[1:5]
        assert path == tl.OBJECT_PATH and iface == tl.INTERFACE
        st = self.instances.get(name)
        if st is None:
            return False, "Call failed: The name %s was not provided by any .service files" % name
        if method == "GetState":
            return True, [json.dumps(st)]
        if method in ("Mute", "Unmute"):
            assert args[5:] == ["b", "false"]
            want = "muted" if method == "Mute" else "unmuted"
            if st["microphoneControlState"] == want or st["microphoneControlState"] not in ("muted", "unmuted"):
                return True, [False]
            st["microphoneControlState"] = want
            return True, [True]
        if method == "ToggleVideo":
            st["cameraEnabled"] = not st["cameraEnabled"]
            return True, [True]
        if method in ("ToggleMute", "ToggleHandRaise"):
            return True, [True]
        return False, "Call failed: No such method '%s'" % method


# ---------------------------------------------------------------- what it reports
def test_its_state_becomes_ours():
    s = tl.state_from_snapshot(FakeBusctl.snapshot(mic="muted", camera=True, sharing=True))
    assert s.available and s.running and s.in_call and s.muted is True and s.camera is True and s.sharing is True
    assert s.hand is None and s.meeting == "" and s.shared == "" and s.started == 0.0, "nothing it doesn't report"
    assert tl.state_from_snapshot(FakeBusctl.snapshot(mic="unmuted")).muted is False
    for unclear in ("unknown", "off", None):
        assert tl.state_from_snapshot(FakeBusctl.snapshot(mic=unclear)).muted is None
    idle = tl.state_from_snapshot(FakeBusctl.snapshot(in_call=False, mic="unknown"))
    assert idle.running and not idle.in_call and idle.muted is None and idle.camera is None


def test_reading_and_finding_it():
    bus = FakeBusctl()
    backend = tl.TeamsForLinux(bus)
    s = backend.read()
    assert s.in_call and s.muted is False
    backend.read()
    assert sum(1 for c in bus.calls if c[1] == "org.freedesktop.DBus") == 1, "the bus name is remembered"


def test_with_two_profiles_the_one_in_a_call_wins():
    bus = FakeBusctl({tl.BUS_PREFIX: FakeBusctl.snapshot(in_call=False, mic="unknown"),
                      tl.BUS_PREFIX + ".Work": FakeBusctl.snapshot(in_call=True, mic="muted")})
    backend = tl.TeamsForLinux(bus)
    assert backend.read().muted is True
    assert backend.press("camera") == (True, "")
    assert bus.instances[tl.BUS_PREFIX + ".Work"]["cameraEnabled"] is True


def test_not_running_or_switched_off_says_how_to_switch_it_on():
    s = tl.TeamsForLinux(FakeBusctl({})).read()
    assert s.available and not s.running and "dbusControl" in s.note
    bus = FakeBusctl()
    bus.bus_down = True
    s = tl.TeamsForLinux(bus).read()
    assert not s.available and "session bus" in s.note


def test_pressing():
    bus = FakeBusctl()
    backend = tl.TeamsForLinux(bus)
    backend.read()
    assert backend.press("mic", want=True) == (True, "")
    assert bus.calls[-1][4:] == ["Mute", "b", "false"], "never force: it refuses when it doesn't know"
    ok, message = backend.press("mic", want=True)
    assert not ok and "didn't send" in message
    assert backend.press("mic") == (True, "") and bus.calls[-1][4] == "ToggleMute"
    assert backend.press("hand") == (True, "") and bus.calls[-1][4] == "ToggleHandRaise"
    for control in ("share", "stop_share"):
        assert backend.press(control) == (False, tl.NO_SHARING)


def test_commands_through_the_monitor(qapp):
    bus = FakeBusctl()
    mon = TeamsMonitor(tl.TeamsForLinux(bus))
    mon.IDLE = mon.BUSY = 0.05
    mon.start()
    try:
        assert wait_until(qapp, lambda: mon.state.in_call, 2)
        assert not mon.sync_safe
        assert mon.command("mute_on") == (True, "")
        assert bus.instances[tl.BUS_PREFIX]["microphoneControlState"] == "muted"
        assert mon.command("mute_on") == (True, "already")
        assert mon.command("camera_toggle") == (True, "") and bus.instances[tl.BUS_PREFIX]["cameraEnabled"]
        bus.instances[tl.BUS_PREFIX]["screenSharing"] = True
        assert wait_until(qapp, lambda: mon.state.sharing is True, 2)
        assert mon.command("share_stop") == (False, tl.NO_SHARING)
    finally:
        mon.stop()


def test_the_pc_microphone_isnt_synced_with_it(qapp, tmp_path, monkeypatch):
    """Its "muted" includes the PC microphone being muted, so following each other could leave you muted."""
    from PySide6.QtCore import QObject, Signal
    from fake_mk10 import FakeMK10
    from mk10manager.config import Config
    from mk10manager.controller import Controller

    class Watcher(QObject):
        changed = Signal(str, str)
        method, note, available = "test", "", True

        def start(self):
            pass

        def stop(self):
            pass

    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    cfg = Config(path=tmp_path / "config.json")
    cfg.settings["integrations"]["teams"] = True
    bus = FakeBusctl({tl.BUS_PREFIX: FakeBusctl.snapshot(mic="muted")})
    mon = TeamsMonitor(tl.TeamsForLinux(bus))
    mon.IDLE = mon.BUSY = 0.05
    audio = FakeAudio()
    am = AudioMonitor(audio)
    am.SLOW = am.FAST = 0.05
    ctl = Controller(cfg, watcher=Watcher(), audio=am, teams=mon)
    ctl.start()
    try:
        assert wait_until(qapp, lambda: ctl.teams.state.muted is True, 3)
        time.sleep(0.6)
        assert audio.sides[INPUT].muted is False, "Teams reads muted, but the PC microphone is left alone"
        assert not any(c[4:5] in (["Mute"], ["Unmute"], ["ToggleMute"]) for c in bus.calls if len(c) > 4)
    finally:
        ctl.shutdown()
        dev.unplug()


# ---------------------------------------------------------------- the real busctl, a pretend teams-for-linux
needs_dbus = pytest.mark.skipif(not (sys.platform.startswith("linux") and shutil.which("busctl")
                                     and shutil.which("dbus-daemon")), reason="needs Linux, busctl and dbus-daemon")


@pytest.fixture
def session_bus(monkeypatch):
    daemon = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    address = daemon.stdout.readline().strip()
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", address)
    services = []

    def start(name=tl.BUS_PREFIX, mode="in-call"):
        p = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_teams_for_linux.py"), name, mode],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        assert p.stdout.readline().strip() == "ready"
        services.append(p)
        return p

    yield start
    for p in services:
        p.kill()
        p.wait()
    daemon.kill()
    daemon.wait()


@needs_dbus
def test_the_whole_path_through_busctl(session_bus):
    backend = tl.TeamsForLinux()
    s = backend.read()
    assert s.available and not s.running and "dbusControl" in s.note, "nothing on the bus yet"
    session_bus()
    s = backend.read()                         # found again straight away: a name that answered nothing is retried
    assert s.in_call and s.muted is False and s.camera is False
    assert backend.press("mic", want=True) == (True, "")
    assert backend.read().muted is True
    assert backend.press("mic", want=True)[0] is False, "already muted: teams-for-linux refuses"
    assert backend.press("camera") == (True, "") and backend.read().camera is True
    lines = backend.describe()
    assert tl.BUS_PREFIX in lines[0] and '"inCall": true' in "\n".join(lines)


@needs_dbus
def test_a_profile_with_its_own_bus_name_is_found(session_bus):
    session_bus(tl.BUS_PREFIX + ".Work", "in-call")
    session_bus(tl.BUS_PREFIX + ".Personal", "idle")
    backend = tl.TeamsForLinux()
    assert backend.read().in_call
    assert backend.press("mic", want=True) == (True, "")
    assert backend.read().muted is True
