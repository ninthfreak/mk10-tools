"""Open the real editor offscreen against the fake MK10 and save screenshots. Usage: python tests/shots.py OUTDIR"""

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
os.environ["MK10_CONFIG_DIR"] = tempfile.mkdtemp()

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from fake_mk10 import FakeMK10

out = sys.argv[1] if len(sys.argv) > 1 else "."
os.makedirs(out, exist_ok=True)
dev = FakeMK10("v2")
dev.start()
os.environ["MK10_PORT"] = dev.port

app = QApplication([])
from mk10manager.config import Config
from mk10manager.controller import Controller
from mk10manager.render.icons import RES
from mk10manager.ui.main_window import MainWindow


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "X11 (xprop)", "", True

    def start(self):
        pass

    def stop(self):
        pass


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


cfg = Config.load()
ctl = Controller(cfg, watcher=FakeWatcher())
win = MainWindow(ctl, QIcon(str(RES / "icon.png")))
win.resize(1280, 820)
ctl.start()
win.show()
pump(1.5)
ctl.watcher.changed.emit("firefox", "MK10 notes - Mozilla Firefox")
pump(0.3)
win.grab().save(os.path.join(out, "1-key.png"))

win.preview.selected.emit("screen", 0)
win._select("screen", 0)
pump(0.3)
win.grab().save(os.path.join(out, "2-screen.png"))

# add a page and two rules, select key with a step
win.ctl.config.add_page("Browser")
cfg.rules.append({"enabled": True, "when": "app", "value": "firefox", "page": cfg.pages[1]["id"]})
cfg.rules.append({"enabled": True, "when": "variable", "name": "mode", "value": "meeting", "page": cfg.pages[0]["id"]})
win._config_changed()
win._select("key", 1)
win.key_editor._add_step("hotkey")
pump(0.3)
win.grab().save(os.path.join(out, "3-rules.png"))
win.bottom.setCurrentIndex(3)   # Device and app; Slack was added ahead of it
pump(0.3)
win.grab().save(os.path.join(out, "4-device.png"))

from mk10manager.ui.icon_picker import IconPicker
d = IconPicker(ctl.renderer.icons, "microphone", "#FFFFFF", "#185FA5", win)
d.show()
pump(0.5)
d.grab().save(os.path.join(out, "5-icons.png"))
d.close()
print("frames sent:", len(dev.frames), "status:", ctl.device.status)
win.quit()
dev.unplug()
