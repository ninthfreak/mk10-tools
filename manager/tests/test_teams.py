"""Teams: reading the mic button, explicit mute on / off that checks before and after, and the wiring into keys,
rules, placeholders and actions. The UI Automation reader itself needs Windows; these drive a fake one."""

import time

import pytest
from PySide6.QtCore import QObject, Signal

from conftest import wait_until
from fake_mk10 import FakeMK10
from fake_teams import FakeTeams
from mk10manager import actions, conditions, rules, templates
from mk10manager.config import Config
from mk10manager.controller import Controller
from mk10manager.render.template import RenderContext, expand
from mk10manager.system import teams as teams_mod
from mk10manager.system.teams import TeamsMonitor, TeamsState, parse_mic_name


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


def in_call(muted=False, name=None) -> TeamsState:
    name = name or ("Unmute mic" if muted else "Mute mic")
    return TeamsState(available=True, running=True, in_call=True, muted=parse_mic_name(name), button_name=name)


# ---------------------------------------------------------------- reading the button
@pytest.mark.parametrize("name, muted", [
    ("Mute mic", False), ("Unmute mic", True), ("Unmute mic (Ctrl+Shift+M)", True), ("  mute mic", False),
    ("Stummschaltung aufheben", None), ("", None), ("Microphone", None),
])
def test_the_button_name_says_muted_or_live(name, muted):
    assert parse_mic_name(name) is muted


def test_off_windows_it_says_why():
    if teams_mod.IS_WIN:
        pytest.skip("this checks the fallback")
    s = teams_mod.make_backend().read()
    assert not s.available and "Windows" in s.note


# ---------------------------------------------------------------- explicit commands
@pytest.fixture
def mon(qapp):
    fake = FakeTeams()
    m = TeamsMonitor(fake)
    m.IDLE = m.BUSY = 0.05
    m.CONFIRM_SECONDS = 0.5
    m.start()
    yield m, fake
    m.stop()


def test_mute_me_presses_once_and_checks_it_took(qapp, mon):
    m, fake = mon
    fake.join(muted=False)
    assert m.command("mute_on") == (True, "")
    assert fake.presses == 1 and fake.muted
    assert m.state.muted is True, "the state should show the change straight away"


def test_mute_me_when_already_muted_presses_nothing(qapp, mon):
    m, fake = mon
    fake.join(muted=True)
    assert m.command("mute_on") == (True, "already")
    assert fake.presses == 0 and fake.muted


def test_unmute_me_and_switch(qapp, mon):
    m, fake = mon
    fake.join(muted=True)
    assert m.command("mute_off") == (True, "")
    assert not fake.muted
    assert m.command("mute_off") == (True, "already")
    assert m.command("toggle") == (True, "")
    assert fake.muted and fake.presses == 2


def test_nothing_is_pressed_outside_a_call(qapp, mon):
    m, fake = mon
    ok, message = m.command("mute_on")
    assert not ok and "Not in a Teams call" in message
    assert fake.presses == 0


@pytest.mark.parametrize("kind", ["mute_on", "mute_off", "toggle"])
def test_nothing_is_pressed_when_the_state_cant_be_read(qapp, mon, kind):
    m, fake = mon
    fake.join(muted=False)
    fake.name = "Stummschaltung"
    ok, message = m.command(kind)
    assert not ok and "Can't tell" in message and "Stummschaltung" in message
    assert fake.presses == 0


def test_a_press_teams_ignores_is_reported(qapp, mon):
    m, fake = mon
    fake.join(muted=False)
    fake.press_works = False
    ok, message = m.command("mute_on")
    assert not ok and "still shows you live" in message
    assert fake.presses == 1, "one press, then it only reads"


def test_switched_off_does_nothing(qapp, mon):
    m, fake = mon
    fake.join()
    m.configure(False)
    assert wait_until(qapp, lambda: not m.state.available, 2)
    ok, message = m.command("mute_on")
    assert not ok and "switched off" in message
    reads = fake.reads
    time.sleep(0.2)
    assert fake.reads == reads, "nothing should be read while it's switched off"


def test_unknown_command(qapp, mon):
    m, _fake = mon
    assert m.command("explode") == (False, "Unknown Teams command explode")


# ---------------------------------------------------------------- following the call
def test_a_redraw_that_hides_the_button_for_a_moment_doesnt_end_the_call(qapp):
    m = TeamsMonitor(FakeTeams())
    m.state = in_call(muted=True)
    gone = TeamsState(available=True, running=True)
    seen = []
    for _ in range(4):
        m.state = m._smooth(gone)          # what the reading loop does with each result
        seen.append(m.state.in_call)
    assert seen == [True, True, False, False], "three misses in a row is a real hang-up"
    m.state = in_call(muted=True)
    m.state = m._smooth(gone)
    m.state = m._smooth(in_call(muted=True))
    m.state = m._smooth(gone)
    assert m.state.in_call, "a miss after the button came back starts the count again"


def test_the_monitor_follows_join_mute_and_leave(qapp, mon):
    m, fake = mon
    assert wait_until(qapp, lambda: m.state.available and m.state.running, 2)
    fake.join(muted=False)
    assert wait_until(qapp, lambda: m.state.live, 2)
    fake.muted = True
    assert wait_until(qapp, lambda: m.state.muted is True, 2)
    fake.leave()
    assert wait_until(qapp, lambda: not m.state.in_call, 2)


# ---------------------------------------------------------------- conditions and placeholders
def test_conditions():
    live, muted, unknown = in_call(False), in_call(True), in_call(name="Microphone")
    nocall = TeamsState(available=True, running=True)
    off = TeamsState(available=False)
    call_yes = {"when": "teams_call", "value": "yes"}
    call_no = {"when": "teams_call", "value": "no"}
    m_yes = {"when": "teams_muted", "value": "yes"}
    m_no = {"when": "teams_muted", "value": "no"}
    t = lambda c, s: conditions.matches(c, teams=s)
    assert t(call_yes, live) and not t(call_no, live)
    assert t(call_no, nocall) and not t(call_yes, nocall)
    assert t(m_yes, muted) and not t(m_no, muted)
    assert t(m_no, live) and not t(m_yes, live)
    for s in (nocall, unknown, off):
        assert not t(m_yes, s) and not t(m_no, s), "muted or live means nothing outside a call or unread"
    assert not t(call_yes, off) and not t(call_no, off), "unreadable is never true"
    assert "Teams has me muted" == conditions.describe(m_yes)
    assert conditions.new_state("teams_muted")["icon"] == "microphone-off"


def test_rules_see_teams():
    rs = [{"enabled": True, "when": "teams_call", "value": "yes", "page": "call"}]
    assert rules.evaluate(rs, "", "", {}, teams=in_call()) == "call"
    assert rules.evaluate(rs, "", "", {}, teams=TeamsState()) is None


def test_placeholders():
    ctx = RenderContext(teams=in_call(muted=True))
    assert expand("{teams_mic}", ctx) == "muted"
    assert expand("{teams_mic:Off|On}", ctx) == "Off"
    assert expand("{teams_call}", ctx) == "in a call"
    ctx.teams = in_call(muted=False)
    assert expand("{teams_mic}", ctx) == "live"
    assert expand("{teams_mic:Off|On}", ctx) == "On"
    for s in (TeamsState(available=True), TeamsState(available=False), in_call(name="Mikrofon")):
        ctx.teams = s
        assert expand("[{teams_mic}]", ctx) == "[]"
    assert expand("[{teams_call}]", RenderContext()) == "[]"


def test_action_summary_and_template():
    assert actions.summary({"type": "teams", "mode": "mute_off"}) == "Teams: unmute me"
    assert actions.new_action("teams")["mode"] == "mute_on"
    page = templates.build("teams")
    keys = [k for k in page.keys if k]
    modes = [a["mode"] for k in keys for a in k["actions"] if a["type"] == "teams"]
    assert modes == ["mute_on", "mute_off", "toggle"]
    assert page.rule["when"] == "teams_call"
    labels = lambda st: [conditions.resolve_key(k, RenderContext(teams=st))["label"] for k in keys[:3]]
    assert labels(in_call(muted=True)) == ["Muted", "Unmute", "Muted"], "the key that's done is lit"
    assert labels(in_call(muted=False)) == ["Mute", "Live", "Live"]
    assert labels(TeamsState(available=True)) == ["Mute", "Unmute", "Switch"], "outside a call they're just keys"


# ---------------------------------------------------------------- end to end
@pytest.fixture
def rig(qapp, tmp_path, monkeypatch):
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    monkeypatch.setenv("MK10_NO_AUDIO", "1")
    cfg = Config(path=tmp_path / "config.json")
    fake = FakeTeams()
    mon = TeamsMonitor(fake)
    mon.IDLE = mon.BUSY = 0.05
    mon.CONFIRM_SECONDS = 0.5
    ctl = Controller(cfg, watcher=FakeWatcher(), teams=mon)
    ctl.start()
    assert wait_until(qapp, lambda: ctl.device.is_connected and dev.frames), ctl.device.status
    yield ctl, dev, cfg, fake
    ctl.shutdown()
    dev.unplug()


def test_a_key_mutes_teams_and_shows_it(qapp, rig):
    ctl, dev, cfg, fake = rig
    page = templates.build("teams")
    ctl.page["keys"] = page.keys
    ctl.edited()
    fake.join(muted=False)
    assert wait_until(qapp, lambda: ctl.teams.state.live, 3)
    failures = []
    ctl.runner.failed.connect(failures.append)
    ctl.runner.run(page.keys[0]["actions"])                 # the Mute key
    assert wait_until(qapp, lambda: fake.muted and ctl.teams.state.muted is True, 3), failures
    assert conditions.resolve_key(ctl.page["keys"][0], ctl.context())["label"] == "Muted"
    assert expand("{teams_mic}", ctl.context()) == "muted"
    ctl.runner.run(page.keys[0]["actions"])                 # again: already muted, nothing pressed
    time.sleep(0.3)
    assert fake.presses == 1 and not failures


def test_a_teams_call_rule_switches_pages(qapp, rig):
    ctl, dev, cfg, fake = rig
    main = cfg.pages[0]
    call = cfg.add_page("Call")
    cfg.rules.append({"enabled": True, "when": "teams_call", "value": "yes", "page": call["id"]})
    ctl.edited()
    fake.join()
    assert wait_until(qapp, lambda: ctl.page_id == call["id"], 3)
    fake.leave()
    assert wait_until(qapp, lambda: ctl.page_id == main["id"], 3)


def test_switch_it_off_from_settings(qapp, rig):
    ctl, dev, cfg, fake = rig
    fake.join()
    assert wait_until(qapp, lambda: ctl.teams.state.in_call, 3)
    cfg.settings["teams_enabled"] = False
    ctl.apply_teams_settings()
    assert wait_until(qapp, lambda: not ctl.teams.state.available, 3)
    assert "teams_enabled" in __import__("mk10manager.controller", fromlist=["x"]).NOT_UNDOABLE_SETTINGS
