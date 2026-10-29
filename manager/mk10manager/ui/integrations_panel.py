"""The Integrations tab: switch each app integration on or off, and set up the ones that are on.

An integration that's off shows only its name, what it does and what uses it. Its settings fold away, and the
rest of the window hides its key steps, conditions, faces, templates and placeholders (integrations.py).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from .. import integrations
from ..system import teams as teams_mod
from .panels import SlackPanel


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: gray;")
    return label


class TeamsSettings(QWidget):
    changed = Signal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.sync = QCheckBox("Keep the PC microphone in step")
        self.sync.setToolTip("During a Teams call, muting in Teams mutes the PC microphone too, and muting the PC "
                             "microphone (the MK10's key, a keyboard's mic key) mutes Teams. When they disagree "
                             "and can't be put right, the live one is muted, never the other way round. Outside "
                             "a call nothing is touched.")
        self.status = _muted("")
        backend = getattr(ctl.teams, "backend", None)
        if ctl.teams.sync_safe:
            lay.addWidget(self.sync)
        else:
            self.sync.setVisible(False)
        lay.addWidget(self.status)
        if not isinstance(backend, teams_mod.NullTeams):
            lay.addWidget(_muted("Read with: %s.%s" % (teams_mod.describe_backend(backend),
                                                      " Teams has to be in English." if teams_mod.IS_WIN else "")))
        if not ctl.teams.sync_safe:
            lay.addWidget(_muted("teams-for-linux needs its D-Bus control switched on: \"dbusControl\": "
                                 "{\"enabled\": true} in its config.json, then restart it. The PC microphone isn't "
                                 "kept in step with it: it judges “muted” from the call's sound, so a microphone "
                                 "muted on the PC already reads as muted there, and following each other could "
                                 "leave you muted."))
        self.sync.toggled.connect(self._sync)
        ctl.teams.changed.connect(self.show_state)
        self.reload()

    def _sync(self, on):
        self.ctl.config.settings["teams_sync_mic"] = bool(on)
        self.changed.emit()

    def show_state(self):
        t = self.ctl.teams.state
        if isinstance(getattr(self.ctl.teams, "backend", None), teams_mod.NullTeams):
            text = "Not available on this system: it needs Windows, or Linux with teams-for-linux."
        elif not t.available:
            text = t.note or "Can't read Teams."
        elif not t.running:
            text = "Now: " + (t.note or "Teams isn't open.")
        elif not t.in_call:
            text = "Now: Teams is open, not in a call."
        elif t.muted is None:
            text = "Now: in a call; Teams' mute state isn't known%s." % (
                " (the button says “%s”)" % t.button_name if t.button_name else "")
        else:
            text = "Now: in %s, %s" % ("“%s”" % t.meeting if t.meeting else "a call", "muted" if t.muted else "live")
            if t.camera is not None:
                text += ", camera %s" % ("on" if t.camera else "off")
            if t.sharing:
                text += ", sharing %s" % t.shared if t.shared else ", sharing"
            text += "."
        self.status.setText(text)

    def reload(self):
        self.sync.blockSignals(True)
        self.sync.setChecked(bool(self.ctl.config.settings.get("teams_sync_mic", True)))
        self.sync.blockSignals(False)
        self.show_state()


class IntegrationsPanel(QWidget):
    changed = Signal()

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        scroll.setWidget(inner)
        lay.addWidget(_muted("Switch on the apps you use. One that's off adds nothing to the window and reads "
                             "nothing in the background. Keys that use it are kept: they do nothing until it's "
                             "back on."))
        self.settings_widgets = {"slack": SlackPanel(ctl), "teams": TeamsSettings(ctl)}
        self.boxes, self.usage = {}, {}
        for integ in integrations.INTEGRATIONS:
            box = QGroupBox(integ.name)
            box.setCheckable(True)
            v = QVBoxLayout(box)
            v.addWidget(_muted(integ.description))
            self.usage[integ.id] = _muted("")
            v.addWidget(self.usage[integ.id])
            body = self.settings_widgets.get(integ.id)
            if body is not None:
                v.addWidget(body)
                body.changed.connect(self.changed.emit)
            box.toggled.connect(lambda on, iid=integ.id: self._switch(iid, on))
            self.boxes[integ.id] = box
            lay.addWidget(box)
        lay.addStretch(1)
        self.reload()

    def _switch(self, integration_id: str, on: bool):
        self.ctl.set_integration(integration_id, on)          # saves it too
        self._show(integration_id)

    def _show(self, integration_id: str):
        on = integrations.is_on(self.ctl.config.settings, integration_id)
        body = self.settings_widgets.get(integration_id)
        if body is not None:
            body.setVisible(on)
        counts = integrations.usage(self.ctl.config.data, integrations.BY_ID[integration_id])
        text = integrations.describe_usage(counts)
        if not on and any(counts.values()):
            text += " They do nothing while it's off."
        self.usage[integration_id].setText(text)

    def reload(self):
        for iid, box in self.boxes.items():
            box.blockSignals(True)
            box.setChecked(integrations.is_on(self.ctl.config.settings, iid))
            box.blockSignals(False)
            body = self.settings_widgets.get(iid)
            if body is not None and hasattr(body, "reload"):
                body.reload()
            self._show(iid)
