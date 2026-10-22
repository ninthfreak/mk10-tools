"""Page templates, the Slack integration, and the two of them together."""

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from conftest import wait_until
from fake_audio import FakeAudio
from fake_mk10 import FakeMK10
from fake_slack import FakeSlack
from mk10manager import conditions, rules, templates
from mk10manager.config import KEY_COUNT, Config
from mk10manager.controller import Controller
from mk10manager.render.canvas import Renderer
from mk10manager.render.template import RenderContext, expand
from mk10manager.system.audio import INPUT, OUTPUT, AudioMonitor
from mk10manager.system.slack import (SlackClient, SlackError, SlackMonitor, SlackState, channel_ids,
                                      deep_link, hide_token, read_token, write_token)


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


def connected(**kw) -> SlackState:
    base = dict(connected=True, user_id="U1", user_name="tony", team_id="T0ACME", team_name="Acme Corp",
                presence="active")
    base.update(kw)
    return SlackState(**base)


# ---------------------------------------------------------------- templates
def test_every_template_builds_a_usable_page():
    icons = Renderer().icons
    assert [t.id for t in templates.TEMPLATES][0] == "blank", "blank should be the first thing offered"
    for t in templates.TEMPLATES:
        page = t.build()
        assert len(page.keys) == KEY_COUNT, t.id
        for key in page.keys:
            if key is None:
                continue
            assert "label" in key and "actions" in key, t.id
            if key.get("icon", {}) and key["icon"].get("kind") == "builtin":
                assert icons.exists(key["icon"]["name"]), (t.id, key["icon"]["name"])
            for state in key.get("states") or []:
                assert state.get("when") in dict(conditions.CONDITIONS), (t.id, state.get("when"))
                if state.get("icon_mode") == "choose":
                    assert icons.exists(state["icon"]), (t.id, state["icon"])
        assert page.screen.get("face") in Renderer().faces, t.id
        if page.rule:
            assert page.rule_text, "a template offering a rule must say what it does"
            assert page.rule.get("page") is None, "the page id is filled in when it's actually added"


def test_template_lookup():
    assert templates.get("slack").id == "slack"
    assert templates.get("nope") is None
    assert len(templates.build("nope").keys) == KEY_COUNT        # falls back to blank


def test_slack_template_covers_the_integrations():
    page = templates.build("slack")
    modes = {a["mode"] for k in page.keys if k for a in k["actions"] if a["type"] == "slack"}
    assert {"status", "clear_status", "presence", "dnd", "end_dnd", "open"} <= modes
    # ... and the OS-level sound keys, which is what actually mutes you in a huddle
    audio = {(a["target"], a["mode"]) for k in page.keys if k for a in k["actions"] if a["type"] == "audio"}
    assert (INPUT, "toggle_mute") in audio and (OUTPUT, "toggle_mute") in audio
    whens = {s["when"] for k in page.keys if k for s in k.get("states") or []}
    assert {"slack_huddle", "slack_dnd", "slack_presence", "slack_unread", "input_muted", "output_muted"} == whens


# ---------------------------------------------------------------- conditions and text
def test_slack_conditions():
    huddling = connected(in_huddle=True, dnd=True, presence="away", unread=3)
    assert conditions.matches({"when": "slack_huddle", "value": "yes"}, slack=huddling)
    assert not conditions.matches({"when": "slack_huddle", "value": "no"}, slack=huddling)
    assert conditions.matches({"when": "slack_dnd", "value": "yes"}, slack=huddling)
    assert conditions.matches({"when": "slack_presence", "value": "away"}, slack=huddling)
    assert not conditions.matches({"when": "slack_presence", "value": "active"}, slack=huddling)
    assert conditions.matches({"when": "slack_unread", "value": "yes"}, slack=huddling)
    quiet = connected()
    assert conditions.matches({"when": "slack_huddle", "value": "no"}, slack=quiet)
    assert conditions.matches({"when": "slack_unread", "value": "no"}, slack=quiet)
    assert conditions.matches({"when": "slack_presence", "value": "active"}, slack=quiet)
    # nothing is true while Slack isn't set up
    for value in ("yes", "no"):
        assert not conditions.matches({"when": "slack_huddle", "value": value}, slack=SlackState())
        assert not conditions.matches({"when": "slack_huddle", "value": value})
    assert rules.evaluate([{"when": "slack_huddle", "value": "yes", "page": "p"}], "", "", {},
                          slack=huddling) == "p"


def test_new_condition_defaults_and_descriptions():
    for kind, _label in conditions.CONDITIONS:
        cond = conditions.new_condition(kind)
        assert conditions.describe(cond) != "?", kind
        if kind in conditions.CHOICE_CONDITIONS:
            assert cond["value"] in [v for v, _ in conditions.CHOICE_CONDITIONS[kind]], kind


def test_slack_placeholders():
    ctx = RenderContext(slack=connected(in_huddle=True, dnd=True, presence="away", unread=4, mentions=1,
                                        status_text="In a meeting"))
    assert expand("{slack_team}/{slack_user}", ctx) == "Acme Corp/tony"
    assert expand("{slack_presence} {slack_unread} {slack_mentions}", ctx) == "away 4 1"
    assert expand("[{slack_huddle}][{slack_dnd}]", ctx) == "[in a huddle][snoozed]"
    assert expand("{slack_huddle:Busy|Free}", ctx) == "Busy"
    assert expand("{slack_status}", ctx) == "In a meeting"
    quiet = RenderContext(slack=connected())
    assert expand("[{slack_huddle:Busy|Free}][{slack_unread}]", quiet) == "[Free][]"
    assert expand("[{slack_huddle}][{slack_team}]", RenderContext()) == "[][]"


# ---------------------------------------------------------------- the client
def test_deep_links_and_helpers():
    assert deep_link("T1", "channel", "C2") == "slack://channel?team=T1&id=C2"
    assert deep_link("T1", "channel", "U2") == "slack://user?team=T1&id=U2"
    assert deep_link("T1") == "slack://open?team=T1"
    assert deep_link("") == "slack://open"
    assert deep_link("", "channel", "C2").startswith("https://slack.com/app_redirect")
    assert channel_ids("C1, C2\nC3 ,") == ["C1", "C2", "C3"]
    assert channel_ids("") == []
    assert hide_token("xoxp-123456789012-abcd") == "xoxp-1234…abcd"
    assert hide_token("") == "" and hide_token("tiny") == "…"


def test_token_is_kept_in_its_own_file(tmp_path):
    assert read_token(tmp_path) == ""
    assert write_token(tmp_path, "xoxp-secret") == (True, "")
    assert read_token(tmp_path) == "xoxp-secret"
    path = tmp_path / "slack-token"
    assert path.stat().st_mode & 0o077 == 0, "the token file must not be readable by anyone else"
    # clearing it removes the file rather than leaving an empty one around
    assert write_token(tmp_path, "") == (True, "")
    assert not path.exists() and read_token(tmp_path) == ""


def test_client_refuses_to_call_without_a_token():
    with pytest.raises(SlackError):
        SlackClient("").call("auth.test")


# ---------------------------------------------------------------- the monitor
def test_monitor_only_talks_to_slack_when_switched_on(qapp):
    fake = FakeSlack()
    mon = SlackMonitor(fake)
    mon.start()
    try:
        mon.configure("", [], 5, "channels", True)           # on, but no token
        assert wait_until(qapp, lambda: not mon.state.connected)
        assert fake.reads == 0
        mon.configure("xoxp-x", [], 5, "channels", True)
        assert wait_until(qapp, lambda: mon.state.connected, 3)
        assert fake.token == "xoxp-x"
        fake.set(in_huddle=True)
        mon.refresh_soon()
        assert wait_until(qapp, lambda: mon.state.in_huddle, 3)
        before = fake.reads
        mon.configure("xoxp-x", [], 5, "channels", False)     # switched off
        assert wait_until(qapp, lambda: not mon.state.connected, 3)
        assert fake.reads - before <= 1
    finally:
        mon.stop()


def test_monitor_apply_reports_problems(qapp):
    fake = FakeSlack()
    mon = SlackMonitor(fake)
    assert mon.apply("dnd", {}) == (False, "Slack isn't set up. Add a token on the Slack tab.")
    mon.configure("xoxp-x", [], 5, "channels", True)
    assert mon.apply("presence", {"presence": "away"}) == (True, "")
    assert fake.state.presence == "away"
    fake.fail_with = "Slack said: missing_scope"
    ok, msg = mon.apply("dnd", {"minutes": 30})
    assert not ok and "missing_scope" in msg


def test_slack_action_summaries():
    from mk10manager.actions import summary
    assert summary({"type": "slack", "mode": "status", "status_text": "Lunch"}) == "Slack status  “Lunch”"
    assert summary({"type": "slack", "mode": "dnd", "snooze_minutes": 45}) == "Slack: snooze for 45 min"
    assert summary({"type": "slack", "mode": "presence", "presence": "away"}) == "Slack: show me as Away"
    assert summary({"type": "slack", "mode": "clear_status"}) == "Slack: clear my status"
    assert summary({"type": "slack", "mode": "open", "channel": "C1"}) == "Open Slack  C1"


def test_slack_action_without_the_integration_says_so():
    from mk10manager.actions import ActionRunner
    runner = ActionRunner(None, None)
    problems = []
    runner.failed.connect(problems.append)
    runner._one({"type": "slack", "mode": "dnd"})
    assert problems and "isn't set up" in problems[0]


# ---------------------------------------------------------------- end to end
@pytest.fixture
def rig(qapp, tmp_path, monkeypatch):
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    cfg = Config(path=tmp_path / "config.json")
    cfg.settings["slack_enabled"] = True
    write_token(tmp_path, "xoxp-test")
    slack = FakeSlack()
    ctl = Controller(cfg, watcher=FakeWatcher(), audio=AudioMonitor(FakeAudio()),
                     slack=SlackMonitor(slack))
    ctl.start()
    assert wait_until(qapp, lambda: ctl.device.is_connected and dev.frames), ctl.device.status
    yield ctl, dev, cfg, slack
    ctl.shutdown()
    dev.unplug()


def pixel(jpeg: bytes, x, y):
    c = QImage.fromData(jpeg).pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def add_slack_page(ctl, cfg, with_rule=False):
    built = templates.build("slack")
    page = cfg.add_page("Slack")
    page["keys"] = built.keys
    page["screen"] = built.screen
    if with_rule and built.rule:
        rule = dict(built.rule)
        rule["page"] = page["id"]
        cfg.rules.append(rule)
    ctl.edited()
    return page


def test_slack_page_reacts_to_slack(qapp, rig):
    ctl, dev, cfg, slack = rig
    assert wait_until(qapp, lambda: ctl.slack.state.connected, 3)
    page = add_slack_page(ctl, cfg)
    ctl.set_page(page["id"])
    # key 2 is the huddle key: dark while there's no huddle
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 128 + 20, 20)[0] < 80, 3)
    slack.set(in_huddle=True)
    ctl.slack.refresh_soon()
    assert wait_until(qapp, lambda: ctl.slack.state.in_huddle, 3)
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 128 + 20, 20)[0] > 90, 3), "huddle key should go orange"
    r, g, b = pixel(dev.frames[-1], 128 + 20, 20)
    assert r > b


def test_slack_key_press_reaches_slack(qapp, rig):
    ctl, dev, cfg, slack = rig
    page = add_slack_page(ctl, cfg)
    ctl.set_page(page["id"])
    assert wait_until(qapp, lambda: ctl.slack.state.connected, 3)
    dev.tap(0, 3)                                            # the Away key
    assert wait_until(qapp, lambda: slack.state.presence == "away", 3)
    dev.tap(1, 3)                                            # Clear: status, snooze and presence back to normal
    assert wait_until(qapp, lambda: slack.state.presence == "auto", 3)
    assert [m for m, _ in slack.applied if m == "clear_status"]


def test_huddle_rule_switches_pages(qapp, rig):
    ctl, dev, cfg, slack = rig
    main = cfg.pages[0]
    page = add_slack_page(ctl, cfg, with_rule=True)
    ctl.set_page(main["id"])
    assert wait_until(qapp, lambda: ctl.slack.state.connected, 3)
    slack.set(in_huddle=True)
    ctl.slack.refresh_soon()
    assert wait_until(qapp, lambda: ctl.page_id == page["id"], 3)
    slack.set(in_huddle=False)
    ctl.slack.refresh_soon()
    assert wait_until(qapp, lambda: ctl.page_id == main["id"], 3)


def test_slack_face_needs_the_microphone_level(qapp, rig):
    ctl, dev, cfg, slack = rig
    page = add_slack_page(ctl, cfg)
    ctl.set_page(page["id"])
    assert INPUT in ctl.page_live_targets(), "the Slack screen shows the live microphone"
    assert OUTPUT in ctl.page_live_targets(), "and the mute keys meter both sides"
