"""Bottom tabs: context rules, variables, and device settings."""

import threading

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFontComboBox, QFormLayout, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider,
                               QSpinBox, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from .. import rules as rules_mod
from ..config import config_dir
from ..device.manager import all_ports
from ..system import audio as audio_mod
from ..system import autostart, inputs
from ..system import nowplaying as np_mod
from ..system import slack as slack_mod
from .condition_row import ConditionRow


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
        self.cond = ConditionRow(rule, {"apps": lambda: apps, "variables": lambda: variables})
        self.cond.changed.connect(self.changed.emit)
        lay.addWidget(self.cond, 1)
        lay.addWidget(QLabel("show page"))
        self.page = QComboBox()
        self.page.addItem("(choose)", None)
        for pid, name in pages:
            self.page.addItem(name, pid)
        self.page.setCurrentIndex(max(0, self.page.findData(rule.get("page"))))
        lay.addWidget(self.page)
        for text, tip, fn in (("↑", "Move up (rules higher up win)", lambda: self.move.emit(self, -1)),
                              ("↓", "Move down", lambda: self.move.emit(self, 1)),
                              ("✕", "Delete this rule", lambda: self.remove.emit(self))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(fn)
            lay.addWidget(b)
        self.page.currentIndexChanged.connect(lambda i: self._set("page", self.page.itemData(i)))

    @property
    def enabled(self):
        return self.cond.enabled

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


SLACK_HELP = (
    "Slack has no local control port, so this uses its Web API with a token of your own.\n\n"
    "1. Go to api.slack.com/apps and click Create New App → From scratch. Pick your workspace.\n"
    "2. Open OAuth & Permissions and add these User Token Scopes:\n     %s\n"
    "3. Click Install to Workspace, then copy the User OAuth Token (it starts with xoxp-).\n"
    "4. Paste it below and press Check.\n\n"
    "Some workspaces need an admin to approve a new app first." % ", ".join(slack_mod.SCOPES))


class SlackPanel(QWidget):
    """Token, workspace and what to watch. The token never goes into config.json."""

    changed = Signal()
    _checked = Signal(bool, str)

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        s = ctl.config.settings
        lay = QHBoxLayout(self)
        left = QFormLayout()
        lay.addLayout(left, 3)
        right = QVBoxLayout()
        lay.addLayout(right, 2)

        self.enabled = QCheckBox("Talk to Slack")
        self.enabled.setChecked(bool(s.get("slack_enabled", False)))
        left.addRow("", self.enabled)
        trow = QHBoxLayout()
        self.token = QLineEdit(slack_mod.read_token(self._folder()))
        self.token.setEchoMode(QLineEdit.Password)
        self.token.setPlaceholderText("xoxp-...")
        self.check = QPushButton("Check")
        self.check.clicked.connect(self._check)
        trow.addWidget(self.token, 1)
        trow.addWidget(self.check)
        left.addRow("User token", self._wrap(trow))
        self.who = _muted("")
        left.addRow("", self.who)
        self.channels = QLineEdit(s.get("slack_channels", ""))
        self.channels.setPlaceholderText("C01ABCDEFG, C02HIJKLMN")
        self.channels.setToolTip("Conversation IDs to count unread messages in. In Slack, open a channel, "
                                 "click its name, and the ID is at the bottom of the About tab.")
        left.addRow("Watch these channels", self.channels)
        self.unread_source = QComboBox()
        for v, l in slack_mod.UNREAD_SOURCES:
            self.unread_source.addItem(l, v)
        self.unread_source.setCurrentIndex(max(0, self.unread_source.findData(
            s.get("slack_unread_source", "channels"))))
        left.addRow("Count unread from", self.unread_source)
        prow = QHBoxLayout()
        self.poll = QSpinBox()
        self.poll.setRange(5, 600)
        self.poll.setValue(int(s.get("slack_poll_seconds", 10)))
        prow.addWidget(self.poll)
        prow.addWidget(QLabel("seconds"))
        prow.addStretch(1)
        left.addRow("Check Slack every", self._wrap(prow))
        self.state_line = _muted("")
        left.addRow("Now", self.state_line)

        help_label = _muted(SLACK_HELP)
        help_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(help_label)
        right.addWidget(_muted("The token is kept in a file of its own in the settings folder, readable only "
                               "by you — never in config.json, so it stays out of backups and undo. Anyone who "
                               "gets it can act as you in Slack: press Check with the box empty to remove it."))
        right.addWidget(_muted("Slack can't tell us whether your microphone is muted in a huddle, and can't "
                               "start or join one. Use a microphone mute key for that: it works in Slack, "
                               "Teams and everything else."))
        right.addStretch(1)

        self.enabled.toggled.connect(lambda on: self._set("slack_enabled", bool(on)))
        self.channels.textChanged.connect(lambda t: self._set("slack_channels", t.strip()))
        self.unread_source.currentIndexChanged.connect(
            lambda i: self._set("slack_unread_source", self.unread_source.itemData(i)))
        self.poll.valueChanged.connect(lambda v: self._set("slack_poll_seconds", int(v)))
        self._checked.connect(self._check_done)
        ctl.slack.changed.connect(self.refresh_state)
        self.refresh_state()

    def _folder(self):
        return self.ctl.config.path.parent if self.ctl.config.path else config_dir()

    @staticmethod
    def _wrap(layout):
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    def _set(self, key, value):
        self.ctl.config.settings[key] = value
        self.ctl.apply_slack_settings()
        self.changed.emit()

    def _check(self):
        """Save the token, then ask Slack who it belongs to. The call happens off the GUI thread."""
        token = self.token.text().strip()
        ok, problem = slack_mod.write_token(self._folder(), token)
        if not ok:
            self.who.setText("Couldn't save the token: %s" % problem)
            return
        self.ctl.apply_slack_settings()
        self.changed.emit()
        if not token:
            self.who.setText("Token removed.")
            return
        self.check.setEnabled(False)
        self.who.setText("Asking Slack...")

        def work():
            try:
                me = slack_mod.SlackClient(token).call("auth.test")
                self._checked.emit(True, "%s|%s|%s" % (me.get("user") or "?", me.get("team") or "?",
                                                       me.get("team_id") or ""))
            except Exception as e:
                self._checked.emit(False, str(e))
        threading.Thread(target=work, name="slack-check", daemon=True).start()

    def _check_done(self, ok, text):
        self.check.setEnabled(True)
        if not ok:
            self.who.setText(text)
            return
        user, team, team_id = text.split("|", 2)
        self.who.setText("Signed in as %s in %s." % (user, team))
        if team_id:
            self.ctl.config.settings["slack_team_id"] = team_id
            self.changed.emit()
        self.ctl.slack.refresh_soon()

    def refresh_state(self):
        st = self.ctl.slack.state
        if not self.ctl.config.settings.get("slack_enabled"):
            self.state_line.setText("Switched off.")
        elif st.connected:
            bits = ["in a huddle" if st.in_huddle else "no huddle", st.presence or "?"]
            if st.dnd:
                bits.append("snoozed")
            if st.unread:
                bits.append("%d unread" % st.unread)
            self.state_line.setText(", ".join(bits))
        else:
            self.state_line.setText(st.note or "Not connected yet.")

    def reload(self):
        s = self.ctl.config.settings
        for w, v in ((self.enabled, s.get("slack_enabled", False)),):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        for w, v in ((self.channels, s.get("slack_channels", "")),):
            w.blockSignals(True)
            w.setText(str(v))
            w.blockSignals(False)
        self.refresh_state()


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
        arow = QHBoxLayout()
        self.levels = QCheckBox("Live level meters")
        self.levels.setToolTip("Measure how loud the speakers and microphone are, for meters behind keys and on "
                               "the small screen. Only runs while a page actually shows one.")
        self.levels.setChecked(bool(s.get("audio_levels", True)))
        self.meter_fps = QSpinBox()
        self.meter_fps.setRange(1, 20)
        self.meter_fps.setValue(int(s.get("meter_fps", 6)))
        self.meter_fps.setToolTip("How many times a second the whole picture is redrawn and sent to the MK10 "
                                  "while a live meter is on screen. Lower if the display looks busy.")
        arow.addWidget(self.levels)
        arow.addWidget(QLabel("Updates a second"))
        arow.addWidget(self.meter_fps)
        arow.addStretch(1)
        left.addRow("Sound meters", self._wrap(arow))
        nrow = QHBoxLayout()
        self.np_enabled = QCheckBox("Show what's playing")
        self.np_enabled.setToolTip("Reads the system's own media info, so it works for any player: "
                                   "Spotify, a browser tab, VLC.")
        self.np_enabled.setChecked(bool(s.get("nowplaying_enabled", True)))
        self.np_art = QCheckBox("Album art")
        self.np_art.setToolTip("Some players give a web address for the artwork, so fetching it means a "
                               "small download each time the track changes.")
        self.np_art.setChecked(bool(s.get("nowplaying_art", True)))
        nrow.addWidget(self.np_enabled)
        nrow.addWidget(self.np_art)
        nrow.addStretch(1)
        left.addRow("Now playing", self._wrap(nrow))

        self.minimized = QCheckBox("Start hidden (in the tray)")
        self.minimized.setChecked(bool(s.get("start_minimized")))
        self.login = QCheckBox("Start when I log in")
        self.login.setChecked(autostart.is_enabled())
        right.addWidget(self.minimized)
        right.addWidget(self.login)
        right.addSpacing(8)
        right.addWidget(_muted("Keystrokes are sent with: %s" % inputs.describe_backend()))
        right.addWidget(_muted("Sound is read and changed with: %s"
                               % audio_mod.describe_backend(getattr(ctl.audio, "backend", None))))
        right.addWidget(_muted("What's playing is read with: %s"
                               % np_mod.describe_backend(getattr(ctl.nowplaying, "backend", None))))
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
        self.np_enabled.toggled.connect(lambda on: self._np_setting("nowplaying_enabled", bool(on)))
        self.np_art.toggled.connect(lambda on: self._np_setting("nowplaying_art", bool(on)))
        self.levels.toggled.connect(lambda on: self._audio_setting("audio_levels", bool(on)))
        self.meter_fps.valueChanged.connect(lambda v: self._audio_setting("meter_fps", int(v)))
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

    def _np_setting(self, key, value):
        self.ctl.config.settings[key] = value
        self.ctl.apply_nowplaying_settings()
        self.ctl.nowplaying.refresh_soon()
        self.changed.emit()

    def _audio_setting(self, key, value):
        self.ctl.config.settings[key] = value
        self.ctl.apply_audio_settings()
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
                     (self.levels, s.get("audio_levels", True)),
                     (self.np_enabled, s.get("nowplaying_enabled", True)),
                     (self.np_art, s.get("nowplaying_art", True)),
                     (self.minimized, s.get("start_minimized", False))):
            w.blockSignals(True)
            w.setChecked(bool(v))
            w.blockSignals(False)
        self.brightness.blockSignals(True)
        self.brightness.setValue(int(s.get("brightness", 80)))
        self.b_label.setText("%d%%" % self.brightness.value())
        self.brightness.blockSignals(False)
        self.meter_fps.blockSignals(True)
        self.meter_fps.setValue(int(s.get("meter_fps", 6)))
        self.meter_fps.blockSignals(False)
