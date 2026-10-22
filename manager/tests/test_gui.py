"""Drive the real editor window offscreen, against the fake MK10."""

import time

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QMessageBox

from conftest import wait_until
from fake_mk10 import FakeMK10


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def gui(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MK10_CONFIG_DIR", str(tmp_path))
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    from mk10manager.config import Config
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


def px(jpeg, x, y):
    c = QImage.fromData(jpeg).pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def test_edit_key_through_editor(qapp, gui):
    win, ctl, dev, cfg = gui
    win._select("key", 6)                              # an empty key
    ke = win.key_editor
    assert ke.empty.isVisibleTo(win) and not ke.body.isVisibleTo(win)
    ke._create()
    assert cfg.pages[0]["keys"][6] is not None
    ke.label.setPlainText("Hi {var:mode}")
    ke.bg.set_color("#EF9F27")
    ke.bg.colorChanged.emit("#EF9F27")
    ke._add_step("variable")
    ctl.set_variable("mode", "x")
    n = len(dev.frames)
    assert wait_until(qapp, lambda: len(dev.frames) > n)
    r, g, b = px(dev.frames[-1], 128 + 64, 128 + 10)  # key 7 = row 1, col 1
    assert r > 180 and b < 90, (r, g, b)
    assert cfg.pages[0]["keys"][6]["label"] == "Hi {var:mode}"
    # editing the step's form updates the action and the summary
    form = ke.step_form
    assert form is not None
    form._set("value", "meeting")
    assert cfg.pages[0]["keys"][6]["actions"][0]["value"] == "meeting"
    assert "meeting" in ke.steps.item(0).text()
    # pressing the key on the device runs it
    dev.tap(1, 1)
    assert wait_until(qapp, lambda: cfg.variables.get("mode") == "meeting")
    # undo goes back
    ctl.flush_undo()
    win.undo_act.trigger()
    ctl.flush_undo()
    assert cfg.pages[0]["keys"][6] is None or cfg.pages[0]["keys"][6]["actions"][0].get("value") != "meeting"


def test_pages_swap_paste_and_screen(qapp, gui, monkeypatch):
    win, ctl, dev, cfg = gui
    # the New page window really gets built; we just answer it instead of showing it
    monkeypatch.setattr("mk10manager.ui.new_page.NewPageDialog.exec", lambda self: 1)
    monkeypatch.setattr("mk10manager.ui.new_page.NewPageDialog.page_name", lambda self: "Games")
    win._add_page()
    assert [p["name"] for p in cfg.pages] == ["Main", "Games"]
    assert ctl.page["name"] == "Games" and win.tabs.currentIndex() == 1
    assert all(k is None for k in ctl.page["keys"])
    win.tabs.setCurrentIndex(0)
    assert ctl.page["name"] == "Main"
    # swap keys 0 and 9
    first = cfg.pages[0]["keys"][0]
    win._swap(0, 9)
    assert cfg.pages[0]["keys"][9] is first and cfg.pages[0]["keys"][0] is None
    # copy and paste
    win._menu_action("copy", "key", 9)
    win._menu_action("paste", "key", 3)
    assert cfg.pages[0]["keys"][3]["label"] == first["label"] and cfg.pages[0]["keys"][3] is not first
    # screen: switch to digital through the editor combo
    win._select("screen", 0)
    se = win.screen_editor
    se.face.setCurrentIndex(se.face.findData("digital"))
    assert cfg.pages[0]["screen"]["face"] == "digital"
    se.face.setCurrentIndex(se.face.findData("analog"))
    assert cfg.pages[0]["screen"]["face"] == "analog"
    # copy screen to all pages
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", lambda *a, **k: QMessageBox.Yes)
    win._screen_all()
    assert cfg.pages[1]["screen"] == cfg.pages[0]["screen"]
    # delete Games page
    gid = cfg.pages[1]["id"]
    ctl.set_page(gid)
    cfg.remove_page(gid)
    ctl.set_page(cfg.default_page()["id"])
    win._build_tabs()
    assert win.tabs.count() == 1 and ctl.page["name"] == "Main"


def test_rules_panel_and_command_server(qapp, gui):
    win, ctl, dev, cfg = gui
    from mk10manager.app import CommandServer
    browser = cfg.add_page("Browser")
    win._build_tabs()
    ctl.watcher.changed.emit("firefox", "Docs")
    win.rules._add_for_app()
    rule = cfg.rules[-1]
    assert rule["value"] == "firefox"
    row = win.rules.rows.itemAt(0).widget()
    row.page.setCurrentIndex(row.page.findData(browser["id"]))
    assert rule["page"] == browser["id"]
    ctl.watcher.changed.emit("gimp", "")
    ctl.watcher.changed.emit("firefox", "Docs 2")
    assert wait_until(qapp, lambda: ctl.page_id == browser["id"])
    assert win.tabs.currentIndex() == 1

    server = CommandServer(win, ctl)
    server.handle("set\tmode\tfocus")
    assert cfg.variables["mode"] == "focus"
    server.handle("page\tmain")
    assert ctl.page["name"] == "Main"
    server.handle("message\tBuild done\t1")
    assert ctl.message == "Build done"


def test_device_panel_brightness(qapp, gui):
    win, ctl, dev, cfg = gui
    win.device.brightness.setValue(33)
    assert wait_until(qapp, lambda: dev.backlight() == 33, 3)
    assert cfg.settings["brightness"] == 33


def test_screen_all_keeps_editor_live(qapp, gui, monkeypatch):
    win, ctl, dev, cfg = gui
    cfg.add_page("Two")
    win._build_tabs()
    win._select("screen", 0)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.question", lambda *a, **k: QMessageBox.Yes)
    win._screen_all()
    win.screen_editor.form._set("bg", "#185FA5")
    assert ctl.page["screen"]["options"]["bg"] == "#185FA5"
    assert cfg.pages[1]["screen"]["options"].get("bg") != "#185FA5"     # a copy, not shared


def test_reload_waits_while_a_dialog_is_open(qapp, gui):
    from mk10manager.ui.widgets import dialog_open
    win, ctl, dev, cfg = gui
    other = cfg.add_page("Other")
    win._build_tabs()
    win._select("key", 0)
    editor_key = win.key_editor.key
    with dialog_open():
        ctl.set_page(other["id"])          # e.g. a rule fires while the colour picker is open
        assert win.key_editor.key is editor_key
    assert wait_until(qapp, lambda: win.key_editor.page is other, 2)
    assert win.tabs.currentIndex() == 1
