"""Bottom tabs: context rules, variables, and device settings."""

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFontComboBox, QFormLayout, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from .. import rules as rules_mod
from ..config import config_dir
from ..device.manager import all_ports
from ..system import autostart, inputs


def _muted(text: str) -> QLabel:
    l = QLabel(text)
    l.setWordWrap(True)
    l.setStyleSheet("color: gray;")
    return l


class RuleRow(QFrame):
    changed = Signal()
    remove = Signal(object)
    move = Signal(object, int)

    def __init__(self, rule: dict, pages, apps, variables, parent=None):
        super().__init__(parent)
        self.rule = rule
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        self.enabled = QCheckBox()
        self.enabled.setToolTip("Turn this rule on or off")
        self.enabled.setChecked(rule.get("enabled", True))
        lay.addWidget(self.enabled)
        lay.addWidget(QLabel("When"))
        self.cond = QComboBox()
        for v, l in rules_mod.CONDITIONS:
            self.cond.addItem(l, v)
        self.cond.setCurrentIndex(max(0, self.cond.findData(rule.get("when", "app"))))
        lay.addWidget(self.cond)

        self.app = QComboBox()
        self.app.setEditable(True)
        self.app.addItems(apps)
        self.app.setCurrentText(rule.get("value", "") if rule.get("when") == "app" else "")
        self.app.lineEdit().setPlaceholderText("firefox, code")
        self.app.setMinimumWidth(150)
        self.title = QLineEdit(rule.get("value", "") if rule.get("when") == "title" else "")
        self.title.setPlaceholderText("Zoom Meeting")
        self.var = QComboBox()
        self.var.setEditable(True)
        self.var.addItems(variables)
        self.var.setCurrentText(rule.get("name", "mode"))
        self.var.setMinimumWidth(110)
        self.var_is = QLabel("is")
        self.var_value = QLineEdit(rule.get("value", "") if rule.get("when") == "variable" else "")
        self.var_value.setPlaceholderText("meeting")
        self.t_from = QLineEdit(rule.get("from", "09:00"))
        self.t_and = QLabel("and")
        self.t_to = QLineEdit(rule.get("to", "17:00"))
        for t in (self.t_from, self.t_to):
            t.setInputMask("99:99")
            t.setFixedWidth(64)
        for w in (self.app, self.title, self.var, self.var_is, self.var_value, self.t_from, self.t_and, self.t_to):
            lay.addWidget(w)
        lay.addWidget(QLabel("show page"))
        self.page = QComboBox()
        self.page.addItem("(choose)", None)
        for pid, name in pages:
            self.page.addItem(name, pid)
        self.page.setCurrentIndex(max(0, self.page.findData(rule.get("page"))))
        lay.addWidget(self.page)
        lay.addStretch(1)
        for text, tip, fn in (("↑", "Move up (rules higher up win)", lambda: self.move.emit(self, -1)),
                              ("↓", "Move down", lambda: self.move.emit(self, 1)),
                              ("✕", "Delete this rule", lambda: self.remove.emit(self))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            lay.addWidget(b)
        self._show()
        self.enabled.toggled.connect(lambda on: self._set("enabled", bool(on)))
        self.cond.currentIndexChanged.connect(self._cond_changed)
        self.app.currentTextChanged.connect(lambda t: self._set("value", t.strip()))
        self.title.textChanged.connect(lambda t: self._set("value", t))
        self.var.currentTextChanged.connect(lambda t: self._set("name", t.strip()))
        self.var_value.textChanged.connect(lambda t: self._set("value", t))
        self.t_from.textChanged.connect(lambda t: self._set("from", t))
        self.t_to.textChanged.connect(lambda t: self._set("to", t))
        self.page.currentIndexChanged.connect(lambda i: self._set("page", self.page.itemData(i)))

    def _show(self):
        w = self.rule.get("when", "app")
        self.app.setVisible(w == "app")
        self.title.setVisible(w == "title")
        for x in (self.var, self.var_is, self.var_value):
            x.setVisible(w == "variable")
        for x in (self.t_from, self.t_and, self.t_to):
            x.setVisible(w == "time")

    def _cond_changed(self, i):
        self.rule["when"] = self.cond.itemData(i)
        w = self.rule["when"]
        self.rule["value"] = {"app": self.app.currentText().strip(), "title": self.title.text(),
                              "variable": self.var_value.text()}.get(w, "")
        self._show()
        self.changed.emit()

    def _set(self, key, value):
        if self.rule.get(key) != value:
            self.rule[key] = value
            self.changed.emit()


class RulesPanel(QWidget):
    changed = Signal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.now = QLabel()
        self.now.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.now, 1)
        self.add_app = QPushButton("Add a rule for this app")
        self.add_app.clicked.connect(self._add_for_app)
        top.addWidget(self.add_app)
        lay.addLayout(top)
        note = ctl.watcher.note if hasattr(ctl.watcher, "note") else ""
        self.note = _muted(note)
        self.note.setVisible(bool(note))
        lay.addWidget(self.note)
        self.rows_box = QWidget()
        self.rows = QVBoxLayout(self.rows_box)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.rows_box)
        self.scroll = scroll
        lay.addWidget(scroll, 1)
        self.empty = _muted("No rules yet. Rules switch pages for you, for example: when firefox is active, show "
                            "the Browser page. Higher rules win.")
        lay.addWidget(self.empty)
        lay.addStretch(1)
        bottom = QHBoxLayout()
        add = QPushButton("Add rule")
        add.clicked.connect(self._add)
        bottom.addWidget(add)
        self.fallback = QCheckBox("When no rule matches, go back to")
        bottom.addWidget(self.fallback)
        self.default_page = QComboBox()
        bottom.addWidget(self.default_page)
        bottom.addStretch(1)
        lay.addLayout(bottom)
        self.fallback.toggled.connect(self._fallback)
        self.default_page.currentIndexChanged.connect(self._default_changed)
        self.reload()
        self.set_app(ctl.app, ctl.title)

    def _pages(self):
        return [(p["id"], p["name"]) for p in self.ctl.config.pages]

    def reload(self):
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        apps = list(self.ctl.config.data.get("seen_apps", []))
        variables = sorted(self.ctl.config.variables)
        for r in self.ctl.config.rules:
            row = RuleRow(r, self._pages(), apps, variables)
            row.changed.connect(self.changed.emit)
            row.remove.connect(self._remove)
            row.move.connect(self._move)
            self.rows.addWidget(row)
        self.rows.addStretch(1)
        self.empty.setVisible(not self.ctl.config.rules)
        self.scroll.setVisible(bool(self.ctl.config.rules))
        s = self.ctl.config.settings
        self.fallback.blockSignals(True)
        self.fallback.setChecked(bool(s.get("fallback_to_default", True)))
        self.fallback.blockSignals(False)
        self.default_page.blockSignals(True)
        self.default_page.clear()
        for pid, name in self._pages():
            self.default_page.addItem(name, pid)
        self.default_page.setCurrentIndex(max(0, self.default_page.findData(self.ctl.config.default_page()["id"])))
        self.default_page.blockSignals(False)

    def set_app(self, app, title):
        if not getattr(self.ctl.watcher, "available", True):
            self.now.setText("Active app: not available on this desktop")
            self.add_app.setEnabled(False)
            return
        self.now.setText("Active app: <b>%s</b>  %s" % (app or "(switch to another app)",
                                                        ("— " + title[:60]) if title else ""))
        self.add_app.setEnabled(bool(app))

    def _add(self, rule=None):
        pages = self.ctl.config.pages
        other = [p for p in pages if p["id"] != self.ctl.config.default_page()["id"]]
        r = rule or rules_mod.new_rule((other or pages)[0]["id"])
        self.ctl.config.rules.append(r)
        self.reload()
        self.changed.emit()

    def _add_for_app(self):
        r = rules_mod.new_rule(None)
        r["value"] = self.ctl.app
        self._add(r)

    def _remove(self, row):
        self.ctl.config.rules.remove(row.rule)
        self.reload()
        self.changed.emit()

    def _move(self, row, d):
        rs = self.ctl.config.rules
        i = rs.index(row.rule)
        j = i + d
        if 0 <= j < len(rs):
            rs[i], rs[j] = rs[j], rs[i]
            self.reload()
            self.changed.emit()

    def _fallback(self, on):
        self.ctl.config.settings["fallback_to_default"] = bool(on)
        self.changed.emit()

    def _default_changed(self, i):
        self.ctl.config.settings["default_page"] = self.default_page.itemData(i)
        self.changed.emit()


class VariablesPanel(QWidget):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self._loading = False
        lay = QVBoxLayout(self)
        lay.addWidget(_muted("Variables are named values that keys can set (Set a variable step), the small screen "
                             "can show ({var:name}) and rules can react to. You can also set them from scripts: "
                             "python launch.pyw set mode=meeting"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add variable")
        rem = QPushButton("Remove")
        add.clicked.connect(self._add)
        rem.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        self.table.itemChanged.connect(self._edited)
        ctl.variables_changed.connect(self.reload)
        self.reload()

    def reload(self):
        if self.table.state() == QAbstractItemView.EditingState:
            return
        self._loading = True
        self.table.setRowCount(0)
        for name, value in sorted(self.ctl.config.variables.items()):
            r = self.table.rowCount()
            self.table.insertRow(r)
            n = QTableWidgetItem(name)
            n.setData(Qt.UserRole, name)
            self.table.setItem(r, 0, n)
            self.table.setItem(r, 1, QTableWidgetItem(str(value)))
        self._loading = False

    def _edited(self, item):
        if self._loading:
            return
        r = item.row()
        name_item, value_item = self.table.item(r, 0), self.table.item(r, 1)
        if not name_item:
            return
        old = name_item.data(Qt.UserRole)
        new = name_item.text().strip()
        value = value_item.text() if value_item else ""
        if old and old != new:
            self.ctl.config.variables.pop(old, None)
        name_item.setData(Qt.UserRole, new)
        if new:
            self.ctl.set_variable(new, value)

    def _add(self):
        base, n = "variable", 1
        while ("%s%d" % (base, n)) in self.ctl.config.variables:
            n += 1
        self.ctl.set_variable("%s%d" % (base, n), "")

    def _remove(self):
        r = self.table.currentRow()
        if r >= 0 and self.table.item(r, 0):
            self.ctl.set_variable(self.table.item(r, 0).text().strip(), None)


class DevicePanel(QWidget):
    changed = Signal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        s = ctl.config.settings
        lay = QHBoxLayout(self)
        left = QFormLayout()
        lay.addLayout(left, 3)
        right = QVBoxLayout()
        lay.addLayout(right, 2)

        self.status = QLabel(ctl.device.status)
        self.status.setWordWrap(True)
        left.addRow("Status", self.status)
        prow = QHBoxLayout()
        self.port = QComboBox()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._fill_ports)
        prow.addWidget(self.port, 1)
        prow.addWidget(refresh)
        left.addRow("Connection", self._wrap(prow))
        brow = QHBoxLayout()
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(int(s.get("brightness", 80)))
        self.b_label = QLabel("%d%%" % self.brightness.value())
        brow.addWidget(self.brightness, 1)
        brow.addWidget(self.b_label)
        left.addRow("Brightness", self._wrap(brow))
        self.feedback = QCheckBox("Shrink a key's picture briefly while it's pressed")
        self.feedback.setChecked(bool(s.get("press_feedback", True)))
        left.addRow("", self.feedback)
        self.h24 = QCheckBox("24-hour time")
        self.h24.setChecked(bool(s.get("time_24h", True)))
        left.addRow("", self.h24)
        frow = QHBoxLayout()
        self.custom_font = QCheckBox("Custom font")
        self.font = QFontComboBox()
        fam = s.get("font_family") or ""
        self.custom_font.setChecked(bool(fam))
        if fam:
            self.font.setCurrentFont(QFont(fam))
        self.font.setEnabled(bool(fam))
        frow.addWidget(self.custom_font)
        frow.addWidget(self.font, 1)
        left.addRow("Text on the device", self._wrap(frow))

        self.minimized = QCheckBox("Start hidden (in the tray)")
        self.minimized.setChecked(bool(s.get("start_minimized")))
        self.login = QCheckBox("Start when I log in")
        self.login.setChecked(autostart.is_enabled())
        right.addWidget(self.minimized)
        right.addWidget(self.login)
        right.addSpacing(8)
        right.addWidget(_muted("Keystrokes are sent with: %s" % inputs.describe_backend()))
        w = ctl.watcher
        detect = "Active app detection: %s" % (getattr(w, "method", "?"))
        if getattr(w, "note", ""):
            detect += ". " + w.note
        right.addWidget(_muted(detect))
        right.addWidget(_muted("Only one program can use the MK10 at a time. Quit OpenDeck (or disable the MK10 "
                               "plugin) and ScreenKey while this app is running."))
        folder = QPushButton("Open the settings folder")
        folder.setToolTip("Your settings, pictures and the log file (mk10-manager.log) live here.")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir()))))
        right.addWidget(folder, 0, Qt.AlignLeft)
        right.addStretch(1)

        self._fill_ports()
        self.port.currentIndexChanged.connect(self._port_changed)
        self._bright_timer = QTimer(self)
        self._bright_timer.setSingleShot(True)
        self._bright_timer.setInterval(120)
        self._bright_timer.timeout.connect(lambda: self.ctl.set_brightness(self.brightness.value()))
        self.brightness.valueChanged.connect(self._brightness)
        self.feedback.toggled.connect(lambda on: self._set("press_feedback", bool(on)))
        self.h24.toggled.connect(lambda on: self._set("time_24h", bool(on)))
        self.custom_font.toggled.connect(self._font_toggle)
        self.font.currentFontChanged.connect(lambda f: self.custom_font.isChecked() and
                                             self._set("font_family", f.family()))
        self.minimized.toggled.connect(lambda on: self._set("start_minimized", bool(on)))
        self.login.toggled.connect(self._login)
        ctl.connection_changed.connect(lambda ok, text: self.status.setText(text))

    @staticmethod
    def _wrap(layout):
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    def _fill_ports(self):
        cur = self.ctl.config.settings.get("port", "auto")
        self.port.blockSignals(True)
        self.port.clear()
        self.port.addItem("Find it automatically", "auto")
        for dev, desc in all_ports():
            self.port.addItem("%s  %s" % (dev, desc), dev)
        if cur != "auto" and self.port.findData(cur) < 0:
            self.port.addItem("%s  (not plugged in)" % cur, cur)
        self.port.setCurrentIndex(max(0, self.port.findData(cur)))
        self.port.blockSignals(False)

    def _port_changed(self, i):
        self.ctl.config.settings["port"] = self.port.itemData(i)
        self.ctl.device.set_port(self.port.itemData(i))
        self.changed.emit()

    def _brightness(self, v):
        self.b_label.setText("%d%%" % v)
        self._bright_timer.start()

    def _set(self, key, value):
        self.ctl.config.settings[key] = value
        self.changed.emit()

    def _font_toggle(self, on):
        self.font.setEnabled(on)
        self._set("font_family", self.font.currentFont().family() if on else "")

    def _login(self, on):
        ok, msg = autostart.set_enabled(bool(on))
        if not ok:
            self.ctl.notify.emit("Couldn't change the login setting: %s" % msg)

    def reload(self):
        s = self.ctl.config.settings
        for w, v in ((self.feedback, s.get("press_feedback", True)), (self.h24, s.get("time_24h", True)),
                     (self.minimized, s.get("start_minimized", False))):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        self.brightness.blockSignals(True)
        self.brightness.setValue(int(s.get("brightness", 80)))
        self.b_label.setText("%d%%" % self.brightness.value())
        self.brightness.blockSignals(False)
