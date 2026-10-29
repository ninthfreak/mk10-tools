"""Teams: reading the mic button, explicit mute on / off that checks before and after, and the wiring into keys,
rules, placeholders and actions. The UI Automation reader itself needs Windows; these drive a fake one."""

import time
from dataclasses import replace

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
from mk10manager.system.audio import INPUT, OUTPUT, AudioMonitor, AudioState, Endpoint
from mk10manager.system.teams import (MIC_BUTTON, TeamsMonitor, TeamsState, meeting_from_title, parse_camera_name,
                                      parse_mic_name, parse_pressed, parse_share_name, short_device_name)
from mk10manager import micsync
from mk10manager.micsync import MicSync
from fake_audio import FakeAudio


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


def in_call(muted=False, name=None) -> TeamsState:
    name = name or ("Unmute mic" if muted else "Mute mic")
    return TeamsState(available=True, running=True, in_call=True, muted=parse_mic_name(name),
                      names=((MIC_BUTTON, name),))


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
    assert len(fake.presses) == 1 and fake.muted
    assert m.state.muted is True, "the state should show the change straight away"


def test_mute_me_when_already_muted_presses_nothing(qapp, mon):
    m, fake = mon
    fake.join(muted=True)
    assert m.command("mute_on") == (True, "already")
    assert fake.presses == [] and fake.muted


def test_unmute_me_and_switch(qapp, mon):
    m, fake = mon
    fake.join(muted=True)
    assert m.command("mute_off") == (True, "")
    assert not fake.muted
    assert m.command("mute_off") == (True, "already")
    assert m.command("toggle") == (True, "")
    assert fake.muted and len(fake.presses) == 2


def test_nothing_is_pressed_outside_a_call(qapp, mon):
    m, fake = mon
    ok, message = m.command("mute_on")
    assert not ok and "Not in a Teams call" in message
    assert fake.presses == []


@pytest.mark.parametrize("kind", ["mute_on", "mute_off", "toggle"])
def test_nothing_is_pressed_when_the_state_cant_be_read(qapp, mon, kind):
    m, fake = mon
    fake.join(muted=False)
    fake.name = "Stummschaltung"
    ok, message = m.command(kind)
    assert not ok and "Can't tell" in message and "Stummschaltung" in message
    assert fake.presses == []


def test_a_press_teams_ignores_is_reported(qapp, mon):
    m, fake = mon
    fake.join(muted=False)
    fake.press_works = False
    ok, message = m.command("mute_on")
    assert not ok and "still shows you live" in message
    assert len(fake.presses) == 1, "one press, then it only reads"


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
    assert actions.summary({"type": "teams", "mode": "camera_toggle"}) == "Teams: switch my camera"
    assert actions.new_action("teams")["mode"] == "mic"
    page = templates.build("teams")
    mic, sound, camera = page.keys[:3]
    assert [a["mode"] for a in mic["actions"]] == ["mic"]
    assert sound["actions"][0]["type"] == "audio" and sound["actions"][0]["target"] == OUTPUT
    assert [a["mode"] for a in camera["actions"]] == ["camera_toggle"]
    assert page.rule["when"] == "teams_call" and page.screen["face"] == "teams"
    audio = AudioState(Endpoint("Headphones (Jabra Evolve2 65)", False, 50), Endpoint("Microphone (Realtek(R) Audio)",
                                                                                      False, 50), True)
    look = lambda key, st, a=audio: conditions.resolve_key(key, RenderContext(teams=st, audio=a))
    assert look(mic, TeamsState())["label"] == "Mic", "no device names Teams doesn't show"
    assert look(sound, TeamsState())["label"] == "Speakers"
    assert look(mic, in_call(muted=True))["bg"] == templates.ORANGE
    assert look(mic, in_call(muted=False))["bg"] == templates.BLUE
    pc_muted = AudioState(audio.output, Endpoint(audio.input.name, True, 50), True)
    assert look(mic, TeamsState(), pc_muted)["bg"] == templates.ORANGE, "outside a call it shows the PC microphone"
    on = replace(in_call(), camera=True)
    assert look(camera, on)["bg"] == templates.BLUE and look(camera, on)["label"] == "Camera"
    assert look(camera, replace(on, camera=False))["bg"] == templates.DARK


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
    ctl.runner.run([{"type": "teams", "mode": "mute_on"}])
    assert wait_until(qapp, lambda: fake.muted and ctl.teams.state.muted is True, 3), failures
    assert conditions.resolve_key(ctl.page["keys"][0], ctl.context())["bg"] == templates.ORANGE
    assert expand("{teams_mic}", ctl.context()) == "muted"
    ctl.runner.run([{"type": "teams", "mode": "mute_on"}])  # again: already muted, nothing pressed
    time.sleep(0.3)
    assert len(fake.presses) == 1 and not failures
    ctl.runner.run(page.keys[2]["actions"])                 # the camera key
    assert wait_until(qapp, lambda: fake.camera and ctl.teams.state.camera is True, 3), failures


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


# ---------------------------------------------------------------- round 2: camera, sharing, hand, meeting
@pytest.mark.parametrize("fn, name, value", [
    (parse_camera_name, "Turn camera off", True), (parse_camera_name, "Turn camera on", False),
    (parse_camera_name, "Kamera", None), (parse_share_name, "Stop sharing", True), (parse_share_name, "Share", False),
    (parse_share_name, "", None),
])
def test_button_names(fn, name, value):
    assert fn(name) is value


def test_pressed_state_and_titles():
    assert parse_pressed("pressed=true;haspopup=false") is True
    assert parse_pressed("expanded=false;pressed=false") is False
    assert parse_pressed("", 1) is True and parse_pressed("", 0) is False
    assert parse_pressed("haspopup=menu") is None and parse_pressed("", None) is None
    assert meeting_from_title("Test | Lemans Corporation | me@x.com | Microsoft Teams") == "Test"
    assert meeting_from_title("Microsoft Teams") == ""
    assert short_device_name("Headset Microphone (Jabra Evolve2 65)") == "Jabra Evolve2 65"
    assert short_device_name("Microphone (Realtek(R) Audio)") == "Realtek(R) Audio"
    assert short_device_name("Built-in Audio") == "Built-in Audio"


def test_camera_commands(qapp, mon):
    m, fake = mon
    fake.join(camera=False)
    assert m.command("camera_on") == (True, "")
    assert fake.camera and fake.presses == ["camera"]
    assert m.command("camera_on") == (True, "already")
    assert m.command("camera_toggle") == (True, "") and not fake.camera
    fake.press_works = False
    ok, message = m.command("camera_on")
    assert not ok and "camera off" in message


def test_the_call_clock_and_meeting(qapp, mon):
    m, fake = mon
    fake.join()
    assert wait_until(qapp, lambda: m.state.in_call, 2)
    since = m.state.call_since
    assert since and m.state.meeting == "Weekly sync"
    fake.muted = True
    assert wait_until(qapp, lambda: m.state.muted is True, 2)
    assert m.state.call_since == since, "the clock keeps running through changes"


def test_more_conditions_and_placeholders():
    st = replace(in_call(), camera=True, sharing=False, hand=None, meeting="Weekly sync",
                 call_since=time.monotonic() - 75)
    t = lambda when, value, s=st: conditions.matches({"when": when, "value": value}, teams=s)
    assert t("teams_camera", "yes") and not t("teams_camera", "no")
    assert t("teams_sharing", "no") and not t("teams_sharing", "yes")
    assert not t("teams_hand", "yes") and not t("teams_hand", "no"), "unknown is never true"
    ctx = RenderContext(teams=st)
    assert expand("{teams_camera} {teams_sharing} [{teams_hand}]", ctx) == "on not sharing []"
    assert expand("{teams_meeting} {teams_time}", ctx) == "Weekly sync 1:15"
    assert expand("{teams_camera:Live|Dark}", ctx) == "Live"


# ---------------------------------------------------------------- keeping the PC microphone in step
def call_state(muted):
    return in_call(muted=muted)


def test_sync_rules():
    s = MicSync()
    assert s.decide(TeamsState(available=True), False, 0) is None, "outside a call nothing is touched"
    assert s.decide(call_state(True), False, 0) == micsync.PC_MUTE, "a call starts: the PC follows Teams"
    assert s.decide(call_state(True), False, 1) is None, "give the follow time to land"
    assert s.decide(call_state(True), True, 1.5) is None, "they agree"
    assert s.decide(call_state(False), True, 2) == micsync.PC_UNMUTE, "Teams changed: the PC follows"
    assert s.decide(call_state(False), False, 2.2) is None
    assert s.decide(call_state(False), True, 10) == micsync.TEAMS_MUTE, "the PC changed: Teams follows"
    assert s.decide(call_state(True), True, 10.5) is None
    assert s.decide(call_state(True), False, 20) == micsync.TEAMS_UNMUTE


def test_sync_never_unmutes_to_settle_a_disagreement():
    s = MicSync()
    s.decide(call_state(False), False, 0)
    assert s.decide(call_state(False), True, 1) == micsync.TEAMS_MUTE      # the PC muted: Teams should follow
    # ... but Teams never does. Once the follow has had its time, the live side (Teams) is muted again, not the
    # PC unmuted.
    assert s.decide(call_state(False), True, 5) == micsync.TEAMS_MUTE
    assert s.decide(call_state(False), True, 9) == micsync.TEAMS_MUTE
    assert s.decide(call_state(False), True, 13) is None, "three tries, then it stops nagging"
    assert s.decide(call_state(True), True, 14) is None, "and picks up again once they agree"


def test_sync_switched_off_or_unreadable():
    s = MicSync()
    assert s.decide(call_state(None), False, 0) is None
    assert s.decide(call_state(True), None, 0) is None
    assert s.decide(TeamsState(available=False), True, 0) is None


@pytest.fixture
def sync_rig(qapp, tmp_path, monkeypatch):
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    cfg = Config(path=tmp_path / "config.json")
    fake, audio = FakeTeams(), FakeAudio()
    mon = TeamsMonitor(fake)
    mon.IDLE = mon.BUSY = 0.05
    mon.CONFIRM_SECONDS = 0.5
    am = AudioMonitor(audio)
    am.SLOW = am.FAST = 0.05
    ctl = Controller(cfg, watcher=FakeWatcher(), audio=am, teams=mon)
    ctl.micsync.PENDING_SECONDS = 0.6
    ctl.start()
    assert wait_until(qapp, lambda: ctl.device.is_connected, 5), ctl.device.status
    yield ctl, fake, audio
    ctl.shutdown()
    dev.unplug()


def test_the_pc_microphone_follows_teams_and_back(qapp, sync_rig):
    ctl, fake, audio = sync_rig
    fake.join(muted=True)
    assert wait_until(qapp, lambda: audio.sides[INPUT].muted is True, 4), "joining muted mutes the PC too"
    with fake.lock:
        fake.muted = False                                   # unmuted in Teams' own window
    assert wait_until(qapp, lambda: audio.sides[INPUT].muted is False, 4)
    audio.set(INPUT, muted=True)                             # a keyboard's mic key
    assert wait_until(qapp, lambda: fake.muted is True, 4), "Teams follows the PC"
    fake.leave()
    assert wait_until(qapp, lambda: not ctl.teams.state.in_call, 4)
    audio.set(INPUT, muted=False)
    time.sleep(0.5)
    assert fake.muted is True, "outside a call nothing is synced"


def test_the_mic_key_is_teams_in_a_call_and_the_pc_otherwise(qapp, sync_rig):
    ctl, fake, audio = sync_rig
    key = templates.build("teams").keys[0]
    assert wait_until(qapp, lambda: ctl.audio.state.available, 3)
    ctl.runner.run(key["actions"])
    assert wait_until(qapp, lambda: audio.sides[INPUT].muted is True, 3), "no call: the PC microphone"
    assert fake.presses == []
    audio.set(INPUT, muted=False)
    fake.join(muted=False)
    assert wait_until(qapp, lambda: ctl.teams.state.live, 3)
    ctl.runner.run(key["actions"])
    assert wait_until(qapp, lambda: fake.muted and audio.sides[INPUT].muted is True, 4), \
        "in a call: Teams, and the PC follows"
    assert fake.presses == ["mic"]


def test_sync_can_be_switched_off(qapp, sync_rig):
    ctl, fake, audio = sync_rig
    ctl.config.settings["teams_sync_mic"] = False
    fake.join(muted=True)
    assert wait_until(qapp, lambda: ctl.teams.state.muted is True, 3)
    time.sleep(0.5)
    assert audio.sides[INPUT].muted is False


def test_the_teams_screen_paints(qapp):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from mk10manager.render.canvas import Renderer
    face = Renderer().face("teams")
    audio = AudioState(Endpoint("Speakers (X)", True, 50), Endpoint("Mic (Y)", False, 50), True)
    for st in (replace(in_call(muted=True), camera=True, sharing=True, hand=True, meeting="Weekly sync",
                       call_since=time.monotonic() - 30),
               TeamsState(available=True, running=False), TeamsState(available=False, note="Windows only")):
        img = QImage(240, 296, QImage.Format_RGB32)
        p = QPainter(img)
        face.paint(p, QRectF(0, 0, 240, 296), {}, RenderContext(teams=st, audio=audio))
        p.end()


# ---------------------------------------------------------------- round 3: clock, sharing, hand, devices
@pytest.mark.parametrize("text, seconds", [("04:34", 274), ("Elapsed time 04:04", 244), ("1:02:03", 3723),
                                           ("00:00", 0), ("", None), ("Elapsed time", None)])
def test_call_clock_text(text, seconds):
    assert teams_mod.parse_duration(text) == seconds


def test_what_is_shared_and_the_hand_on_my_tile():
    assert teams_mod.parse_shared("You're sharing your screen") == "your screen"
    assert teams_mod.parse_shared("You\u2019re sharing Notepad") == "Notepad"
    assert teams_mod.parse_shared("Stop sharing") == ""
    me = ("Myself video, Sass, Tony, Has control of production tools, Muted, video is on, Fill frame, "
          "Hand raised position 1, Has context menu")
    assert teams_mod.hand_from_tile(me) is True
    assert teams_mod.hand_from_tile("Myself video, Sass, Tony, Muted, video is on") is False
    assert teams_mod.hand_from_tile("Doe, Jane, Hand raised position 2") is None, "someone else's tile"


def test_the_call_clock_is_teams_own(qapp, mon):
    m, fake = mon
    fake.join(elapsed=274)              # the Manager starts (or notices the call) 4.5 minutes in
    assert wait_until(qapp, lambda: m.state.in_call, 2)
    assert abs(m.state.call_seconds() - 274) <= 1
    since = m.state.call_since
    time.sleep(0.3)
    assert abs(m.state.call_since - since) < 1.0, "steady from read to read"
    assert "4:3" in expand("{teams_time}", RenderContext(teams=m.state))
    fake.join(elapsed=3)                # left and joined again: a new clock
    assert wait_until(qapp, lambda: m.state.call_seconds() < 10, 2)


def test_the_share_key_stops_sharing_or_opens_the_picker(qapp, mon):
    m, fake = mon
    fake.join()
    with fake.lock:
        fake.sharing = True
    assert m.command("share_toggle") == (True, "")
    assert not fake.sharing and fake.presses == ["stop_share"] and fake.focused == []
    assert m.command("share_stop") == (True, "already")
    assert m.command("share_toggle") == (True, "Teams' share picker is open")
    assert fake.focused == ["share"], "opening the picker brings the call window forward"
    assert not fake.sharing, "nothing is shared until it's picked in Teams"


def test_a_whiteboard_or_excel_share_counts_and_stops(qapp, mon):
    m, fake = mon
    fake.join()
    with fake.lock:
        fake.content = True             # Teams shows a Stop sharing button of its own; share-button says Share
    assert wait_until(qapp, lambda: m.state.sharing is True, 2)
    assert m.state.shared == "", "Teams' sharing bar isn't there, so what's shared is unknown"
    share = templates.build("teams").keys[3]
    ctx = RenderContext(teams=m.state)
    assert expand(conditions.resolve_key(share, ctx)["label"], ctx).strip() == "Sharing"
    assert m.command("share_stop") == (True, "")
    assert not fake.content and fake.presses == ["stop_share"]


def test_sharing_shows_what_is_shared(qapp, mon):
    m, fake = mon
    fake.join()
    with fake.lock:
        fake.sharing = True
    assert wait_until(qapp, lambda: m.state.shared == "your screen", 2)
    ctx = RenderContext(teams=m.state)
    assert expand("Sharing {teams_shared}", ctx) == "Sharing your screen"
    page = templates.build("teams")
    share = page.keys[3]
    assert [a["mode"] for a in share["actions"]] == ["share_toggle"]
    look = conditions.resolve_key(share, ctx)
    assert look["bg"] == templates.ORANGE and expand(look["label"], ctx) == "Sharing your screen"
    with fake.lock:
        fake.sharing = False
    assert wait_until(qapp, lambda: m.state.sharing is False, 2)
    ctx = RenderContext(teams=m.state)
    assert expand("[{teams_shared}]", ctx) == "[]"
    assert conditions.resolve_key(share, ctx)["label"] == "Share"
    assert actions.summary({"type": "teams", "mode": "share_stop"}) == "Teams: stop sharing"


def test_stop_sharing_buttons():
    assert teams_mod.is_stop_sharing("Stop sharing") and teams_mod.is_stop_sharing("Stop Sharing")
    assert not teams_mod.is_stop_sharing("Share") and not teams_mod.is_stop_sharing("Sharing control bar")


def test_more_placeholders_round_3():
    st = replace(in_call(), sharing=True, shared="")
    assert expand("{teams_shared:something}", RenderContext(teams=st)) == "something", "sharing, but not what"


def test_the_teams_screen_says_what_is_shared(qapp):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from mk10manager.render.canvas import Renderer
    face = Renderer().face("teams")
    st = replace(in_call(), camera=True, sharing=True, shared="your screen")
    img = QImage(240, 296, QImage.Format_RGB32)
    p = QPainter(img)
    face.paint(p, QRectF(0, 0, 240, 296), {"show_devices": True}, RenderContext(teams=st))
    p.end()


def test_a_new_default_microphone_mid_call_never_unmutes_teams(qapp, sync_rig):
    ctl, fake, audio = sync_rig
    fake.join(muted=True)
    assert wait_until(qapp, lambda: audio.sides[INPUT].muted is True, 4), "the PC follows Teams: muted"
    time.sleep(0.3)
    with audio.lock:                    # a headset plugged in: Windows makes it the default, and it's live
        audio.sides[INPUT] = Endpoint("Headset Microphone (Jabra)", False, 80, 0.0)
    assert wait_until(qapp, lambda: audio.sides[INPUT].muted is True, 4), "the new microphone follows Teams"
    time.sleep(0.5)
    assert fake.muted and fake.presses == [], "a new microphone isn't the PC unmuting: Teams stays muted"
