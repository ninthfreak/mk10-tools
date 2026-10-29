"""Integrations (Slack, Teams, Now playing) can be switched off, and then add nothing to the window."""

import copy

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon

from conftest import wait_until
from fake_mk10 import FakeMK10
from mk10manager import actions, conditions, integrations, templates
from mk10manager.config import Config
from mk10manager.render import template as text_template
from mk10manager.render.faces import make_faces


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


# ---------------------------------------------------------------- the registry
def test_every_part_an_integration_names_exists():
    faces = make_faces(None)
    template_ids = {t.id for t in templates.TEMPLATES}
    condition_ids = {c for c, _ in conditions.CONDITIONS}
    for i in integrations.INTEGRATIONS:
        assert set(i.actions) <= set(actions.ACTION_TYPES), i.id
        assert set(i.conditions) <= condition_ids, i.id
        assert set(i.faces) <= set(faces), i.id
        assert set(i.templates) <= template_ids, i.id
    owners = {owner for owner, _line in text_template.HELP_LINES} - {None}
    assert owners <= set(integrations.BY_ID), "help lines tagged with an integration that doesn't exist"


def test_every_slack_and_teams_part_belongs_to_its_integration():
    """A new Teams condition or action that isn't added to the registry would never be hidden."""
    for cid, _ in conditions.CONDITIONS:
        for prefix in ("slack_", "teams_"):
            if cid.startswith(prefix):
                assert integrations.owner("conditions", cid).id == prefix[:-1], cid
    for kind in ("slack", "teams"):
        assert integrations.owner("actions", kind).id == kind


def test_hidden_parts_and_notes():
    settings = {"integrations": {"teams": True}}
    assert "slack" in integrations.hidden(settings, "actions")
    assert "teams" not in integrations.hidden(settings, "actions")
    assert {"playing", "slack_dnd"} <= integrations.hidden(settings, "conditions")
    assert integrations.hidden(settings, "templates") == {"slack"}
    assert integrations.off_note(settings, "faces", "slack") == "  (Slack is off)"
    assert integrations.off_note(settings, "faces", "teams") == ""
    assert integrations.off_note(settings, "faces", "analog") == "", "the app's own parts are never off"
    assert integrations.enabled(settings) == {"teams"}


def test_placeholder_help_leaves_out_switched_off_integrations():
    help_text = text_template.placeholder_help({"teams"})
    assert "{teams_call}" in help_text and "{time}" in help_text
    assert "{slack_huddle}" not in help_text and "{track}" not in help_text
    assert text_template.HELP == text_template.placeholder_help(None)


# ---------------------------------------------------------------- what uses one
def test_usage_counts_keys_screens_and_rules():
    data = Config().data
    page = templates.build("teams")
    data["pages"].append({"id": "t", "name": "Teams", "keys": page.keys, "screen": page.screen})
    data["rules"].append({"when": "teams_call", "value": "yes", "page": "t"})
    counts = integrations.usage(data, integrations.BY_ID["teams"])
    assert counts == {"keys": 3, "screens": 1, "rules": 1}, "mic, camera and share; the speakers key is plain audio"
    assert integrations.describe_usage(counts) == "Used by 3 keys, 1 small screen and 1 context rule."
    assert integrations.usage(data, integrations.BY_ID["slack"]) == {"keys": 0, "screens": 0, "rules": 0}
    assert integrations.describe_usage({"keys": 0, "screens": 0, "rules": 0}) == "Nothing on your pages uses it."
    data["pages"][0]["keys"][0]["label"] = "{track}"           # a placeholder counts too
    assert integrations.usage(data, integrations.BY_ID["nowplaying"])["keys"] == 1


# ---------------------------------------------------------------- settings from before
def test_a_new_setup_starts_with_every_integration_off():
    assert Config().settings["integrations"] == {"slack": False, "teams": False, "nowplaying": False}


def test_old_settings_keep_what_they_use():
    data = Config().data
    del data["settings"]["integrations"]
    data["settings"].update(slack_enabled=True, teams_enabled=True, nowplaying_enabled=True)
    page = templates.build("teams")
    data["pages"].append({"id": "t", "name": "Teams", "keys": page.keys, "screen": page.screen})
    cfg = Config(copy.deepcopy(data))
    # Slack was switched on; Teams has a page; Now playing was on only by default, and nothing uses it
    assert cfg.settings["integrations"] == {"slack": True, "teams": True, "nowplaying": False}
    for old in integrations.OLD_SWITCHES:
        assert old not in cfg.settings
    cfg.settings["integrations"]["nowplaying"] = True
    again = Config(copy.deepcopy(cfg.data))
    assert again.settings["integrations"]["nowplaying"] is True, "a choice once made is kept"


# ---------------------------------------------------------------- the window
@pytest.fixture
def gui(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MK10_CONFIG_DIR", str(tmp_path))
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    from mk10manager.controller import Controller
    from mk10manager.ui.main_window import MainWindow
    cfg = Config.load()
    ctl = Controller(cfg, watcher=FakeWatcher())
    win = MainWindow(ctl, QIcon())
    ctl.start()
    win.show()
    assert wait_until(qapp, lambda: dev.frames), ctl.device.status
    yield win, ctl, dev, cfg
    win.quit_for_test = True
    ctl.shutdown()
    win.hide()
    dev.unplug()


def menu_items(menu):
    menu.aboutToShow.emit()
    return {a.data() for a in menu.actions()}


def step_menu(win):
    from PySide6.QtWidgets import QToolButton
    buttons = [b for b in win.key_editor.findChildren(QToolButton) if b.text() == "Add step"]
    return buttons[0].menu()


def state_menu(win):
    from PySide6.QtWidgets import QToolButton
    buttons = [b for b in win.key_editor.findChildren(QToolButton) if b.text() == "Add a state"]
    return buttons[0].menu()


def test_switched_off_integrations_add_nothing_to_the_window(qapp, gui):
    win, ctl, dev, cfg = gui
    win._select("key", 0)
    assert not ({"slack", "teams"} & menu_items(step_menu(win)))
    assert not ({"playing", "teams_call", "slack_dnd"} & menu_items(state_menu(win)))
    win._select("screen", 0)
    faces = {win.screen_editor.face.itemData(i) for i in range(win.screen_editor.face.count())}
    assert not ({"slack", "teams", "nowplaying"} & faces) and "analog" in faces
    from mk10manager.ui.new_page import NewPageDialog
    dlg = NewPageDialog(ctl, win)
    ids = {dlg.list.item(i).data(0x0100) for i in range(dlg.list.count())}
    assert not ({"slack", "teams"} & ids) and "sound" in ids
    dlg.deleteLater()

    # switch Teams on from the Integrations tab: its parts appear, and its settings unfold
    box = win.integrations.boxes["teams"]
    assert not win.integrations.settings_widgets["teams"].isVisibleTo(win.integrations)
    box.setChecked(True)
    assert integrations.is_on(cfg.settings, "teams")
    assert win.integrations.settings_widgets["teams"].isVisibleTo(win.integrations)
    win._select("key", 0)
    assert "teams" in menu_items(step_menu(win)) and "slack" not in menu_items(step_menu(win))
    assert "teams_call" in menu_items(state_menu(win))
    win._select("screen", 0)
    faces = {win.screen_editor.face.itemData(i) for i in range(win.screen_editor.face.count())}
    assert "teams" in faces and "slack" not in faces


def test_switching_off_keeps_what_uses_it_and_marks_it(qapp, gui):
    win, ctl, dev, cfg = gui
    win.integrations.boxes["teams"].setChecked(True)
    key = cfg.pages[0]["keys"][0]
    key["actions"] = [{"type": "teams", "mode": "camera_toggle"}]
    key["states"] = [conditions.new_state("teams_camera")]
    cfg.pages[0]["screen"] = {"face": "teams", "options": {}}
    ctl.edited()
    win.integrations.boxes["teams"].setChecked(False)
    assert not ctl.teams.state.available, "its monitor stops reading"
    assert key["actions"][0]["type"] == "teams" and cfg.pages[0]["screen"]["face"] == "teams", "nothing is removed"
    win._select("key", 0)
    assert win.key_editor.steps.item(0).text().endswith("(Microsoft Teams is off)")
    assert win.key_editor.states.item(0).text().endswith("(Microsoft Teams is off)")
    win._select("screen", 0)
    se = win.screen_editor
    assert se.face.currentData() == "teams", "the page's own face stays listed"
    assert "(Microsoft Teams is off)" in se.face.currentText()
    assert "They do nothing while it's off" in win.integrations.usage["teams"].text()


def test_a_rule_keeps_its_switched_off_condition(qapp, gui):
    win, ctl, dev, cfg = gui
    cfg.rules.append({"enabled": True, "when": "teams_call", "value": "yes", "page": cfg.pages[0]["id"]})
    win.rules.reload()
    from mk10manager.ui.panels import RuleRow
    row = win.rules.findChildren(RuleRow)[0]
    whens = [row.cond.when.itemData(i) for i in range(row.cond.when.count())]
    assert "teams_call" in whens and "teams_muted" not in whens and "slack_dnd" not in whens
    assert row.cond.when.currentText().endswith("(Microsoft Teams is off)")


def test_switching_isnt_undone(qapp, gui):
    win, ctl, dev, cfg = gui
    cfg.pages[0]["name"] = "Renamed"
    ctl.edited()
    ctl.flush_undo()
    win.integrations.boxes["slack"].setChecked(True)
    win.undo_act.trigger()
    assert cfg.pages[0]["name"] != "Renamed"
    assert integrations.is_on(cfg.settings, "slack"), "undo leaves the switch alone"
