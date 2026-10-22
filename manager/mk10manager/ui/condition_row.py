"""One row of condition widgets, editing a condition dict in place.

Used twice: by a context rule ("when Firefox is active, show the Browser page") and by a key state
("when the microphone is muted, make this key orange"). Both store the same fields, so both use this.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QWidget

from ..conditions import AUDIO_CONDITIONS, CONDITIONS, MUTE_CHOICES


class ConditionRow(QWidget):
    changed = Signal()

    def __init__(self, cond: dict, providers: dict | None = None, with_enabled: bool = True,
                 lead: str = "When", parent=None):
        super().__init__(parent)
        self.cond = cond
        providers = providers or {}
        apps = list(providers.get("apps", lambda: [])())
        variables = list(providers.get("variables", lambda: [])())
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.enabled = QCheckBox()
        self.enabled.setToolTip("Turn this on or off")
        self.enabled.setChecked(cond.get("enabled", True))
        self.enabled.setVisible(with_enabled)
        lay.addWidget(self.enabled)
        if lead:
            lay.addWidget(QLabel(lead))
        self.when = QComboBox()
        for v, l in CONDITIONS:
            self.when.addItem(l, v)
        self.when.setCurrentIndex(max(0, self.when.findData(cond.get("when", "app"))))
        lay.addWidget(self.when)

        when = cond.get("when", "app")
        self.app = QComboBox()
        self.app.setEditable(True)
        self.app.addItems(apps)
        self.app.setCurrentText(cond.get("value", "") if when == "app" else "")
        self.app.lineEdit().setPlaceholderText("firefox, code")
        self.app.setMinimumWidth(140)
        self.title = QLineEdit(cond.get("value", "") if when == "title" else "")
        self.title.setPlaceholderText("Zoom Meeting")
        self.var = QComboBox()
        self.var.setEditable(True)
        self.var.addItems(variables)
        self.var.setCurrentText(cond.get("name", "mode"))
        self.var.setMinimumWidth(100)
        self.var_is = QLabel("is")
        self.var_value = QLineEdit(cond.get("value", "") if when == "variable" else "")
        self.var_value.setPlaceholderText("meeting")
        self.t_from = QLineEdit(cond.get("from", "09:00"))
        self.t_and = QLabel("and")
        self.t_to = QLineEdit(cond.get("to", "17:00"))
        for t in (self.t_from, self.t_to):
            t.setInputMask("99:99")
            t.setFixedWidth(64)
        self.mute = QComboBox()
        for v, l in MUTE_CHOICES:
            self.mute.addItem(l, v)
        self.mute.setCurrentIndex(max(0, self.mute.findData(cond.get("value") if when in AUDIO_CONDITIONS
                                                            else "yes")))
        for w in (self.app, self.title, self.var, self.var_is, self.var_value, self.t_from, self.t_and,
                  self.t_to, self.mute):
            lay.addWidget(w)
        lay.addStretch(1)
        self.lay = lay
        self._show()

        self.enabled.toggled.connect(lambda on: self._set("enabled", bool(on)))
        self.when.currentIndexChanged.connect(self._when_changed)
        self.app.currentTextChanged.connect(lambda t: self._set("value", t.strip()))
        self.title.textChanged.connect(lambda t: self._set("value", t))
        self.var.currentTextChanged.connect(lambda t: self._set("name", t.strip()))
        self.var_value.textChanged.connect(lambda t: self._set("value", t))
        self.t_from.textChanged.connect(lambda t: self._set("from", t))
        self.t_to.textChanged.connect(lambda t: self._set("to", t))
        self.mute.currentIndexChanged.connect(lambda i: self._set("value", self.mute.itemData(i)))

    def add_widget(self, w):
        """Put something after the condition widgets but before the stretch (the rule's page picker)."""
        self.lay.insertWidget(self.lay.count() - 1, w)

    def _show(self):
        w = self.cond.get("when", "app")
        self.app.setVisible(w == "app")
        self.title.setVisible(w == "title")
        for x in (self.var, self.var_is, self.var_value):
            x.setVisible(w == "variable")
        for x in (self.t_from, self.t_and, self.t_to):
            x.setVisible(w == "time")
        self.mute.setVisible(w in AUDIO_CONDITIONS)

    def _when_changed(self, i):
        self.cond["when"] = self.when.itemData(i)
        w = self.cond["when"]
        self.cond["value"] = {"app": self.app.currentText().strip(), "title": self.title.text(),
                              "variable": self.var_value.text()}.get(w, self.mute.currentData()
                                                                     if w in AUDIO_CONDITIONS else "")
        self._show()
        self.changed.emit()

    def _set(self, key, value):
        if self.cond.get(key) != value:
            self.cond[key] = value
            self.changed.emit()
