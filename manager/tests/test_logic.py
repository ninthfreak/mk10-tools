import json
import sys
import time
from datetime import datetime

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from conftest import wait_until
from fake_mk10 import FakeMK10
from mk10manager import rules
from mk10manager.config import Config
from mk10manager.controller import Controller
from mk10manager.device.manager import DeviceManager
from mk10manager.render.template import RenderContext, expand, format_time, uses_seconds
from mk10manager.system import inputs


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


def test_templates():
    ctx = RenderContext(now=datetime(2026, 10, 22, 14, 5, 9), time_24h=True, app="firefox", title="Docs",
                        page="Main", variables={"mode": "meeting"})
    assert expand("{time} {seconds} {app} {page} {var:mode} {var:none}|", ctx) == "14:05 09 firefox Main meeting |"
    assert expand("{date}", ctx) == "Thu 22 Oct"
    assert expand("{date:%d/%m}", ctx) == "22/10"
    assert expand("{unknown} {", ctx) == "{unknown} {"
    assert format_time(ctx.now, False) == "2:05 PM"
    assert uses_seconds("{time:%H:%M:%S}") and uses_seconds("{seconds}") and not uses_seconds("{time}")


def test_hotkey_parsing():
    assert inputs.parse_hotkey("Ctrl+Shift+M") == (["ctrl", "shift"], "m")
    assert inputs.parse_hotkey("Meta+E") == (["meta"], "e")
    assert inputs.parse_hotkey("Ctrl++") == (["ctrl", "shift"], "=")
    assert inputs.parse_hotkey("F13") == ([], "f13")
    assert inputs.parse_hotkey("Alt+PgDown") == (["alt"], "pgdown")
    assert inputs.parse_hotkey("Volume Up") == ([], "volume up")
    for bad in ("", "Ctrl+Q+W", "Ctrl+Banana"):
        with pytest.raises(ValueError):
            inputs.parse_hotkey(bad)


def test_rules():
    now = datetime(2026, 10, 22, 23, 30)
    rs = [
        {"enabled": False, "when": "app", "value": "firefox", "page": "off"},
        {"enabled": True, "when": "app", "value": "Code, firefox", "page": "p_app"},
        {"enabled": True, "when": "variable", "name": "mode", "value": "meeting", "page": "p_var"},
        {"enabled": True, "when": "time", "from": "22:00", "to": "06:00", "page": "p_night"},
    ]
    assert rules.evaluate(rs, "firefox", "", {}, now) == "p_app"
    assert rules.evaluate(rs, "gimp", "", {"mode": "Meeting"}, now) == "p_var"
    assert rules.evaluate(rs, "gimp", "", {}, now) == "p_night"
    assert rules.evaluate(rs, "gimp", "", {}, datetime(2026, 10, 22, 12, 0)) is None
    assert rules.matches({"when": "title", "value": "zoom"}, "", "Zoom Meeting", {}, now)


def test_config_roundtrip(tmp_path):
    c = Config(path=tmp_path / "config.json")
    p = c.add_page("Browser")
    c.rules.append({"enabled": True, "when": "app", "value": "firefox", "page": p["id"]})
    c.save()
    c2 = Config.load(tmp_path / "config.json")
    assert [x["name"] for x in c2.pages] == ["Main", "Browser"]
    assert c2.add_page("Browser")["name"] == "Browser 2"
    assert c2.remove_page(p["id"]) and not c2.rules
    (tmp_path / "broken.json").write_text("{not json")
    c3 = Config.load(tmp_path / "broken.json")
    assert c3.pages and c3.load_problem and list(tmp_path.glob("config.unreadable-*.json"))
    # a UTF-8 BOM (Windows editors) is fine
    (tmp_path / "bom.json").write_bytes(b"\xef\xbb\xbf" + (tmp_path / "config.json").read_bytes())
    assert [x["name"] for x in Config.load(tmp_path / "bom.json").pages] == ["Main", "Browser"]
    # unreadable (here: a folder where the file should be) -> never overwritten
    (tmp_path / "dir.json").mkdir()
    c4 = Config.load(tmp_path / "dir.json")
    assert c4.save_blocked and c4.load_problem
    c4.save()
    assert (tmp_path / "dir.json").is_dir()
    # only one backup per run: the second save doesn't replace the .bak of the original
    c2.save()
    bak = (tmp_path / "config.bak").read_text()
    c2.add_page("Another")
    c2.save()
    assert (tmp_path / "config.bak").read_text() == bak


def test_rule_app_names_and_summaries():
    from mk10manager.actions import ACTION_TYPES, summary
    assert rules.app_names("Firefox.exe, code ,") == ["firefox", "code"]
    assert rules.matches({"when": "app", "value": "FIREFOX.EXE"}, "firefox", "", {}, datetime.now())
    for kind in ACTION_TYPES:
        summary({"type": kind, "mode": "bogus", "ms": "x", "value": None})
    summary({"type": "nonsense"})


def test_command_timeout_kills_children():
    from mk10manager.actions import run_command_output
    t = time.time()
    out = run_command_output("sleep 30; echo late", timeout=0.5)
    assert out == "(command took too long)" and time.time() - t < 5


def test_watcher_debounce(qapp):
    from mk10manager.system.activewin import ActiveAppWatcher
    w = ActiveAppWatcher(interval=0.02)
    seq = iter([("a", "1"), ("b", ""), ("a", "1"), ("a", "1"), "self", ("a", "1"), ("c", "x"), ("c", "x")] +
               [("c", "x")] * 50)
    w._fn = lambda: next(seq, ("c", "x"))
    got = []
    w.changed.connect(lambda a, t: got.append(a))
    w.start()
    assert wait_until(qapp, lambda: got[-1:] == ["c"], 3)
    w.stop()
    assert got == ["a", "c"]


@pytest.fixture
def rig(qapp, tmp_path, monkeypatch):
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    cfg = Config(path=tmp_path / "config.json")
    watcher = FakeWatcher()
    ctl = Controller(cfg, DeviceManager(), watcher)
    ctl.start()
    assert wait_until(qapp, lambda: ctl.device.is_connected and dev.frames), ctl.device.status
    yield ctl, dev, watcher, cfg
    ctl.shutdown()
    dev.unplug()


def pixel(jpeg: bytes, x, y):
    img = QImage.fromData(jpeg)
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def test_controller_end_to_end(qapp, rig):
    ctl, dev, watcher, cfg = rig
    main = cfg.pages[0]
    assert dev.backlight() == 80
    # first key is the blue "Mute" key
    r, g, b = pixel(dev.frames[-1], 10, 10)
    assert b > 120 and r < 60

    # a page for the browser, with a key that switches the "mode" variable
    browser = cfg.add_page("Browser")
    meeting = cfg.add_page("Meeting")
    browser["keys"][0] = {"label": "Meet", "bg": "#854F0B", "actions": [
        {"type": "variable", "mode": "set", "name": "mode", "value": "meeting"}]}
    meeting["screen"] = {"face": "text", "options": {"text": "In a meeting", "bg": "#185FA5"}}
    cfg.rules[:] = [
        {"enabled": True, "when": "variable", "name": "mode", "value": "meeting", "page": meeting["id"]},
        {"enabled": True, "when": "app", "value": "firefox", "page": browser["id"]},
    ]
    ctl.edited()

    watcher.changed.emit("firefox", "Some page")
    assert wait_until(qapp, lambda: ctl.page_id == browser["id"])
    # an earlier picture may still be on its way, so wait for the Browser page's orange key 0
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 64, 20)[0] > 100)

    dev.tap(0, 0)
    assert wait_until(qapp, lambda: cfg.variables.get("mode") == "meeting")
    assert wait_until(qapp, lambda: ctl.page_id == meeting["id"])
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 700, 20)[2] > 120)   # blue text-face background

    # going back to another app with no rule match: falls back to the default page
    cfg.variables.clear()
    watcher.changed.emit("gimp", "")
    assert wait_until(qapp, lambda: ctl.page_id == main["id"])

    # undo removes the Meeting page edits
    ctl.flush_undo()
    before = len(cfg.pages)
    cfg.add_page("Scratch")
    ctl.edited()
    ctl.flush_undo()
    assert len(cfg.pages) == before + 1
    ctl.undo()
    assert len(cfg.pages) == before
    ctl.redo()
    assert len(cfg.pages) == before + 1

    # saved to disk
    ctl.flush_undo()
    saved = json.loads(cfg.path.read_text())
    assert any(p["name"] == "Scratch" for p in saved["pages"])


def test_face_command_and_message(qapp, rig):
    ctl, dev, watcher, cfg = rig
    cmd = "%s -c \"print('hello there')\"" % sys.executable
    ctl.page["screen"] = {"face": "text", "options": {"text": "{output}", "command": cmd, "refresh": 60}}
    ctl.edited()
    assert wait_until(qapp, lambda: ctl.outputs.get(cmd) == "hello there", 8)
    ctl._on_ui_action({"type": "message", "text": "Hi {page}", "seconds": 1})
    assert ctl.message == "Hi Main"
    assert wait_until(qapp, lambda: ctl.message is None, 3)


def test_brightness_and_cycle(qapp, rig):
    ctl, dev, watcher, cfg = rig
    ctl._on_ui_action({"type": "brightness", "mode": "down", "value": 30})
    assert wait_until(qapp, lambda: dev.backlight() == 50)
    for expected in ("a", "b", "a"):
        ctl._on_ui_action({"type": "variable", "mode": "cycle", "name": "x", "values": "a, b"})
        assert cfg.variables["x"] == expected
    ctl._on_ui_action({"type": "variable", "mode": "toggle", "name": "t"})
    assert cfg.variables["t"] == "on"


def test_undo_keeps_device_settings(qapp, rig):
    ctl, dev, watcher, cfg = rig
    ctl.flush_undo()
    ctl.set_brightness(20)
    cfg.settings["time_24h"] = False
    ctl.edited(undoable=False)
    ctl.flush_undo()
    cfg.pages[0]["keys"][0]["label"] = "Changed"
    ctl.edited()
    ctl.flush_undo()
    ctl.undo()
    assert cfg.pages[0]["keys"][0]["label"] == "Mute"
    assert cfg.settings["brightness"] == 20 and cfg.settings["time_24h"] is False
