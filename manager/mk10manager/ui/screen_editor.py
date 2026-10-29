"""Editor for the small screen on the current page."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .widgets import Form


class ScreenEditor(QWidget):
    changed = Signal()
    apply_all = Signal()

    def __init__(self, ctl, providers: dict, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.providers = providers
        self.page = None
        self.form = None
        lay = QVBoxLayout(self)
        self.title = QLabel("Small screen")
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        lay.addWidget(self.title)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show"))
        self.face = QComboBox()
        row.addWidget(self.face, 1)
        lay.addLayout(row)
        self.box = QGroupBox("Options")
        self.box_layout = QVBoxLayout(self.box)
        lay.addWidget(self.box)
        hint = QLabel("Each page has its own small screen. Pages can switch on their own when you change apps; "
                      "set that up under Context rules.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        allp = QPushButton("Use this screen on every page")
        allp.clicked.connect(self.apply_all.emit)
        lay.addWidget(allp, 0, Qt.AlignLeft)
        lay.addStretch(1)
        self.face.currentIndexChanged.connect(self._face_changed)

    def load(self, page: dict):
        self.page = page
        screen = page.setdefault("screen", {"face": "analog", "options": {}})
        self.title.setText("Small screen on “%s”" % page["name"])
        self.face.blockSignals(True)
        self._fill_faces(screen.get("face"))
        self.face.setCurrentIndex(max(0, self.face.findData(screen.get("face"))))
        self.face.blockSignals(False)
        self._build()

    def _fill_faces(self, current):
        """Every face but those of switched-off integrations, unless this page already shows one."""
        hidden = self.providers.get("hidden", lambda kind: set())("faces")
        off_note = self.providers.get("off_note", lambda kind, item: "")
        self.face.clear()
        for fid, face in self.ctl.renderer.faces.items():
            if fid not in hidden or fid == current:
                self.face.addItem(face.label + off_note("faces", fid), fid)

    def _build(self):
        if self.form is not None:
            self.form.setParent(None)
            self.form.deleteLater()
        screen = self.page["screen"]
        face = self.ctl.renderer.face(screen.get("face"))
        opts = screen.setdefault("options", {})
        self.form = Form(face.options, opts, self.providers)
        self.form.changed.connect(self._opt_changed)
        self.box_layout.addWidget(self.form)

    def _opt_changed(self, key):
        if key in ("command", "refresh"):
            self.ctl.rerun_face_command()
        self.changed.emit()

    def _face_changed(self, i):
        screen = self.page["screen"]
        fid = self.face.itemData(i)
        if screen.get("face") == fid:
            return
        # keep options per face, so switching back and forth doesn't lose settings
        stash = screen.setdefault("saved", {})
        stash[screen.get("face")] = screen.get("options") or {}
        screen["face"] = fid
        screen["options"] = stash.pop(fid, {})
        self._build()
        self.changed.emit()
