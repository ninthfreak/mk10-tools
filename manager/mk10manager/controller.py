"""Ties it together: settings, the device, drawing, key presses, context rules, variables and undo."""

import json
import threading
import time
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage

from . import integrations
from . import rules as rules_mod
from .actions import ActionRunner, run_command_output
from .config import Config, config_dir
from .device.layout import DeviceLayout
from .device.manager import DeviceManager
from .render import meters
from .render.canvas import Renderer
from .render.template import RenderContext, expand
from .system.activewin import ActiveAppWatcher
from .system.audio import AudioMonitor
from .system.nowplaying import NowPlayingMonitor
from .system.slack import SlackMonitor, channel_ids, read_token
from .micsync import PC_MUTE, PC_UNMUTE, TEAMS_MUTE, TEAMS_UNMUTE, MicSync
from .system.audio import INPUT
from .system.teams import TeamsMonitor

NOT_UNDOABLE = ("variables", "seen_apps")
# Device and app settings are changed live on the Device tab; undo should never roll them back.
NOT_UNDOABLE_SETTINGS = ("brightness", "port", "press_feedback", "time_24h", "font_family", "start_minimized",
                         "tray_hint_shown", "audio_levels", "meter_fps", "integrations", "slack_team_id",
                         "slack_channels", "slack_unread_source", "slack_poll_seconds",
                         "nowplaying_enabled", "nowplaying_art", "teams_sync_mic")
SLOW_TICK_MS = 200          # how often we look at the clock and the message timer when nothing is animating
_NO_RESULT = object()


def open_target(target: str) -> bool:
    """Web address, file, folder, or a program name like notepad / firefox."""
    import os
    import re
    import shutil
    import subprocess
    import sys
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", target) or target.lower().startswith(("www.", "mailto:")):
        return QDesktopServices.openUrl(QUrl.fromUserInput(target))
    path = os.path.expandvars(os.path.expanduser(target))
    if os.path.exists(path):
        if sys.platform.startswith("win"):
            try:
                os.startfile(path)
                return True
            except OSError:
                return False
        if os.path.isfile(path) and os.access(path, os.X_OK):
            subprocess.Popen([path], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))
    if sys.platform.startswith("win"):
        try:
            os.startfile(target)        # Windows finds registered programs: notepad, calc, firefox ...
            return True
        except OSError:
            return QDesktopServices.openUrl(QUrl.fromUserInput(target))
    exe = shutil.which(target)
    if exe:
        subprocess.Popen([exe], start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    return QDesktopServices.openUrl(QUrl.fromUserInput(target))


class Controller(QObject):
    preview_changed = Signal(QImage)
    page_changed = Signal(str)
    config_changed = Signal()            # config replaced or edited elsewhere (undo, rules): editors reload
    settings_changed = Signal()          # device/app settings changed by a key (brightness)
    variables_changed = Signal()
    integrations_changed = Signal()      # one was switched on or off: editors show or hide its parts
    notify = Signal(str)                 # short message for the status bar
    device_pressed = Signal(int, bool)   # key position, pressed
    app_changed = Signal(str, str)
    connection_changed = Signal(bool, str)
    undo_state = Signal(bool, bool)
    _output_ready = Signal(str, str)

    def __init__(self, config: Config, device: DeviceManager | None = None, watcher: ActiveAppWatcher | None = None,
                 renderer: Renderer | None = None, audio: AudioMonitor | None = None,
                 slack: SlackMonitor | None = None, nowplaying: NowPlayingMonitor | None = None,
                 teams: TeamsMonitor | None = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.renderer = renderer or Renderer()
        self.device = device or DeviceManager(config.settings.get("port", "auto"))
        self.watcher = watcher or ActiveAppWatcher()
        self.audio = audio or AudioMonitor()
        self.audio.set_levels_enabled(bool(config.settings.get("audio_levels", True)))
        self.slack = slack or SlackMonitor()
        self.nowplaying = nowplaying or NowPlayingMonitor()
        self.teams = teams or TeamsMonitor()
        self.micsync = MicSync()
        self._sync_device = None        # the microphone micsync last compared; a new one starts it afresh
        self.runner = ActionRunner(self.audio, self.slack, self.teams)
        self.layout = DeviceLayout.default()
        self.page_id = config.default_page()["id"]
        self.prev_page_id = None
        self.pressed: set[int] = set()
        self.message, self.message_until = None, 0.0
        self.outputs: dict[str, str] = {}
        self._cmd_next: dict[str, float] = {}
        self._cmd_running: set[str] = set()
        self.app, self.title = "", ""
        self.last_image: QImage | None = None
        self._last_second = None
        self._last_minute = None
        self._last_audio_switches = None
        self._live_targets: frozenset = frozenset()
        self._dirty = True
        self._force_push = True
        self._last_rule_result = _NO_RESULT
        self._pending_undo = False
        self._undo = [self._undo_snapshot()]
        self._redo: list[str] = []

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self._save_now)
        self._tick = QTimer(self)
        self._tick.setInterval(SLOW_TICK_MS)
        self._tick.timeout.connect(self._on_tick)

        self.device.connected.connect(self._on_connected)
        self.device.disconnected.connect(self._on_disconnected)
        self.device.key_event.connect(self._on_key)
        self.device.status_changed.connect(self._on_device_status)
        self.device.device_error.connect(lambda e: self.notify.emit("MK10 says: %s" % e))
        self.watcher.changed.connect(self._on_app)
        self.audio.changed.connect(self._on_audio)
        self.slack.changed.connect(self._on_slack)
        self.nowplaying.changed.connect(self._on_nowplaying)
        self.teams.changed.connect(self._on_teams)
        self.runner.ui_action.connect(self._on_ui_action)
        self.runner.failed.connect(self.notify.emit)
        self._output_ready.connect(self._on_output)

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        self.device.start()
        self.watcher.start()
        self.audio.start()
        self.apply_slack_settings()
        self.slack.start()
        self.apply_nowplaying_settings()
        self.nowplaying.start()
        self.apply_teams_settings()
        self.teams.start()
        self._update_live_meters()
        self._tick.start()
        self.refresh(force=True)

    def shutdown(self):
        self._tick.stop()
        self.watcher.stop()
        self.audio.stop()
        self.slack.stop()
        self.nowplaying.stop()
        self.teams.stop()
        self.device.stop()
        if self._save_timer.isActive():
            self._save_timer.stop()
        self._save_now()

    # ---------------------------------------------------------------- state helpers
    @property
    def page(self) -> dict:
        p = self.config.page(self.page_id)
        if p is None:
            p = self.config.default_page()
            self.page_id = p["id"]
        return p

    def page_name(self, page_id) -> str:
        p = self.config.page(page_id)
        return p["name"] if p else "(deleted page)"

    def context(self) -> RenderContext:
        s = self.config.settings
        return RenderContext(now=datetime.now(), time_24h=bool(s.get("time_24h", True)), app=self.app,
                             title=self.title, page=self.page["name"], variables=dict(self.config.variables),
                             outputs=self.outputs, font_family=s.get("font_family") or "",
                             audio=self.audio.state, slack=self.slack.state,
                             now_playing=self.nowplaying.track, teams=self.teams.state)

    # ---------------------------------------------------------------- drawing
    def refresh(self, force: bool = False):
        """Redraw everything; send it to the device if it changed (or force)."""
        msg = self.message if self.message and time.time() < self.message_until else None
        img = self.renderer.compose(self.layout, self.page, self.context(), frozenset(self.pressed),
                                    self.outputs, msg)
        changed = self.last_image is None or img != self.last_image
        if changed:
            self.last_image = img
            self.preview_changed.emit(img)
        if (changed or force or self._force_push) and self.device.is_connected:
            self._force_push = False
            self.device.show(self.renderer.to_jpeg(img))
        self._dirty = False

    def _on_tick(self):
        now = datetime.now()
        if self.message and time.time() >= self.message_until:
            self.message = None
            self._dirty = True
        if now.minute != self._last_minute:
            self._last_minute = now.minute
            if any(r.get("when") == "time" for r in self.config.rules):
                self._evaluate_rules()
        if now.second != self._last_second and self.teams.state.in_call:
            self._sync_mic()        # a follow that didn't land gets looked at again once it's had its time
        self._update_live_meters()
        self._run_face_command()
        if self._live_targets and self.device.is_connected and not self.device.ready_for_frame:
            return      # the device is still drawing the last picture; composing another would waste it
        if self._dirty or now.second != self._last_second:
            self._last_second = now.second
            self.refresh()

    def _run_face_command(self):
        screen = self.page.get("screen") or {}
        cmd = self.renderer.face(screen.get("face")).command(screen.get("options") or {})
        if not cmd:
            return
        command, every = cmd
        if command in self._cmd_running or time.time() < self._cmd_next.get(command, 0):
            return
        self._cmd_running.add(command)
        self._cmd_next[command] = time.time() + max(1, every)

        def work():
            self._output_ready.emit(command, run_command_output(command))
        threading.Thread(target=work, daemon=True).start()

    def _on_output(self, command, output):
        self._cmd_running.discard(command)
        if self.outputs.get(command) != output:
            self.outputs[command] = output
            self._dirty = True

    def rerun_face_command(self):
        self._cmd_next.clear()

    # ---------------------------------------------------------------- live meters
    def page_live_targets(self) -> frozenset:
        """Which sound levels this page actually shows. Nothing else gets measured."""
        if not self.config.settings.get("audio_levels", True):
            return frozenset()
        wanted = set()
        for key in self.page.get("keys") or []:
            if key:
                target = meters.live_target(key.get("meter"))
                if target:
                    wanted.add(target)
        screen = self.page.get("screen") or {}
        wanted |= self.renderer.face(screen.get("face")).live_targets(screen.get("options") or {})
        return frozenset(wanted)

    def _update_live_meters(self):
        """Tell the audio monitor what to measure, and tick fast enough to animate it."""
        targets = self.page_live_targets()
        if targets == self._live_targets:
            return
        self._live_targets = targets
        self.audio.want_levels(targets)
        if targets:
            # A cap, not a promise: the device's acknowledgements decide the rate it actually achieves.
            fps = max(1, min(30, int(self.config.settings.get("meter_fps", 10) or 10)))
            interval = max(25, int(round(1000 / fps)))
        else:
            interval = SLOW_TICK_MS
        if self._tick.interval() != interval:
            self._tick.setInterval(interval)

    def _on_nowplaying(self):
        self._dirty = True
        self._evaluate_rules()

    def _on_slack(self):
        self._dirty = True
        self._evaluate_rules()

    def _on_teams(self):
        self._dirty = True
        self._sync_mic()
        self._evaluate_rules()

    def _sync_mic(self):
        """During a Teams call, keep the PC microphone's mute and Teams' mute the same (see micsync.py)."""
        if not self.config.settings.get("teams_sync_mic", True) or not getattr(self.teams, "sync_safe", True):
            self.micsync.reset()
            return
        audio = self.audio.state
        device = audio.input.name if audio.available else None
        if device != self._sync_device:
            # A new default microphone (a headset plugged in mid-call) isn't the PC changing its mute: the PC
            # side starts again by following Teams, so the switch can never unmute anyone.
            self._sync_device = device
            self.micsync.reset()
        pc_muted = audio.input.muted if audio.available else None
        action = self.micsync.decide(self.teams.state, pc_muted, time.monotonic())
        if action in (PC_MUTE, PC_UNMUTE):
            self.runner.run([{"type": "audio", "target": INPUT, "mode": "mute" if action == PC_MUTE else "unmute"}])
        elif action in (TEAMS_MUTE, TEAMS_UNMUTE):
            self.runner.run([{"type": "teams", "mode": "mute_on" if action == TEAMS_MUTE else "mute_off"}])

    def _on_audio(self):
        self._dirty = True
        switches = (self.audio.state.output.muted, self.audio.state.input.muted,
                    self.audio.state.output.volume, self.audio.state.input.volume)
        if switches != self._last_audio_switches:      # levels move constantly; mute and volume don't
            self._last_audio_switches = switches
            self._sync_mic()
            self._evaluate_rules()

    # ---------------------------------------------------------------- device events
    def _on_connected(self, layout: DeviceLayout):
        self.layout = layout
        self._force_push = True
        self.device.send("setBacklight", {"level": int(self.config.settings.get("brightness", 80))})
        self.connection_changed.emit(True, self.device.status)
        self.refresh(force=True)

    def _on_disconnected(self, reason: str):
        self.pressed.clear()
        self.connection_changed.emit(False, self.device.status)

    def _on_device_status(self, text: str):
        self.connection_changed.emit(self.device.is_connected, text)

    def _on_key(self, row: int, col: int, pressed: bool):
        pos = self.layout.position(row, col)
        if pos is None:
            return
        self.device_pressed.emit(pos, pressed)
        feedback = bool(self.config.settings.get("press_feedback", True))
        if pressed:
            keys = self.page.get("keys") or []
            key = keys[pos] if pos < len(keys) else None
            if feedback:
                self.pressed.add(pos)
                self.refresh()
            if key and key.get("actions"):
                self.runner.run(key["actions"])
        else:
            if pos in self.pressed:
                self.pressed.discard(pos)
                self.refresh()
            # The firmware may draw its own press effect; send our picture again shortly after release.
            QTimer.singleShot(150, lambda: self.refresh(force=True))

    def test_press(self, pos: int):
        keys = self.page.get("keys") or []
        if 0 <= pos < len(keys) and keys[pos]:
            self.runner.run(keys[pos].get("actions") or [])

    # ---------------------------------------------------------------- pages, variables, messages
    def set_page(self, page_id, manual: bool = True):
        if not self.config.page(page_id) or page_id == self.page_id:
            return
        self.prev_page_id = self.page_id
        self.page_id = page_id
        self.pressed.clear()
        self.page_changed.emit(page_id)
        self._update_live_meters()
        self.refresh(force=True)

    def step_page(self, delta: int):
        pages = self.config.pages
        i = self.config.page_index(self.page_id)
        self.set_page(pages[(i + delta) % len(pages)]["id"])

    def set_variable(self, name: str, value):
        name = (name or "").strip()
        if not name:
            return
        vars_ = self.config.variables
        if value is None:
            if name not in vars_:
                return
            del vars_[name]
        else:
            if vars_.get(name) == str(value):
                return
            vars_[name] = str(value)
        self.variables_changed.emit()
        self._dirty = True
        self.save_later()
        self._evaluate_rules()
        self.refresh()

    def flash(self, text: str, seconds: float = 2.0):
        self.message = text
        self.message_until = time.time() + max(0.5, seconds)
        self.refresh()

    def set_brightness(self, level: int, persist: bool = True):
        level = max(0, min(100, int(level)))
        self.config.settings["brightness"] = level
        if self.device.is_connected:
            self.device.send("setBacklight", {"level": level})
        if persist:
            self.save_later()

    def _on_ui_action(self, a: dict):
        t = a.get("type")
        if t == "page":
            mode = a.get("mode", "goto")
            if mode == "goto":
                self.set_page(a.get("page"))
            elif mode == "next":
                self.step_page(1)
            elif mode == "previous":
                self.step_page(-1)
            elif mode == "back" and self.prev_page_id:
                self.set_page(self.prev_page_id)
        elif t == "variable":
            name, mode = a.get("name") or "", a.get("mode", "set")
            cur = self.config.variables.get(name.strip(), "")
            if mode == "set":
                self.set_variable(name, a.get("value", ""))
            elif mode == "clear":
                self.set_variable(name, None)
            elif mode == "toggle":
                self.set_variable(name, "off" if cur == "on" else "on")
            elif mode == "cycle":
                items = [v.strip() for v in str(a.get("values") or "").split(",") if v.strip()]
                if items:
                    nxt = items[(items.index(cur) + 1) % len(items)] if cur in items else items[0]
                    self.set_variable(name, nxt)
        elif t == "message":
            text = a.get("text", "")
            if not a.get("literal"):
                text = expand(text, self.context())
            self.flash(text, float(a.get("seconds") or 2))
        elif t == "brightness":
            cur = int(self.config.settings.get("brightness", 80))
            v = int(a.get("value") or 0)
            new = {"set": v, "up": cur + v, "down": cur - v}.get(a.get("mode", "set"), v)
            self.set_brightness(new)
            self.settings_changed.emit()
        elif t == "open":
            target = (a.get("target") or "").strip()
            if target and not open_target(target):
                self.notify.emit("Couldn't open %s" % target)

    # ---------------------------------------------------------------- context
    def _on_app(self, app: str, title: str):
        self.app, self.title = app, title
        if self.config.note_app(app):
            self.save_later()
        self.app_changed.emit(app, title)
        self._dirty = True
        self._evaluate_rules()

    def _evaluate_rules(self):
        target = rules_mod.evaluate(self.config.rules, self.app, self.title, self.config.variables,
                                    audio=self.audio.state, slack=self.slack.state,
                                    track=self.nowplaying.track, teams=self.teams.state)
        if target is None and self.config.settings.get("fallback_to_default", True) and self.config.rules:
            target = self.config.default_page()["id"]
        if target == self._last_rule_result:
            return
        self._last_rule_result = target
        if target:
            self.set_page(target, manual=False)

    # ---------------------------------------------------------------- edits, saving, undo
    def _undo_snapshot(self) -> str:
        d = {k: v for k, v in self.config.data.items() if k not in NOT_UNDOABLE}
        d["settings"] = {k: v for k, v in d.get("settings", {}).items() if k not in NOT_UNDOABLE_SETTINGS}
        return json.dumps(d, sort_keys=True)

    def edited(self, undoable: bool = True):
        """The editor changed config.data in place."""
        self._dirty = True
        self._update_live_meters()
        self.refresh()
        self.save_later()
        self._pending_undo = self._pending_undo or undoable

    def save_later(self):
        self._save_timer.start()

    def _save_now(self):
        if self._pending_undo:
            snap = self._undo_snapshot()
            if snap != self._undo[-1]:
                self._undo.append(snap)
                del self._undo[:-100]
                self._redo.clear()
            self._pending_undo = False
            self.undo_state.emit(len(self._undo) > 1, bool(self._redo))
        try:
            self.config.save()
        except OSError as e:
            self.notify.emit("Couldn't save settings: %s" % e)

    def flush_undo(self):
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_now()

    def _restore(self, snap: str):
        keep = {k: self.config.data.get(k) for k in NOT_UNDOABLE}
        keep_settings = {k: self.config.settings[k] for k in NOT_UNDOABLE_SETTINGS if k in self.config.settings}
        data = json.loads(snap)
        data.update(keep)
        data.setdefault("settings", {}).update(keep_settings)
        self.config.restore(json.dumps(data))
        if not self.config.page(self.page_id):
            self.page_id = self.config.default_page()["id"]
        self.config_changed.emit()
        self.page_changed.emit(self.page_id)
        self._dirty = True
        self.refresh(force=True)
        try:
            self.config.save()
        except OSError:
            pass
        self.undo_state.emit(len(self._undo) > 1, bool(self._redo))

    def undo(self):
        self.flush_undo()
        if len(self._undo) > 1:
            self._redo.append(self._undo.pop())
            self._restore(self._undo[-1])

    def redo(self):
        self.flush_undo()
        if self._redo:
            snap = self._redo.pop()
            self._undo.append(snap)
            self._restore(snap)

    def apply_settings(self):
        s = self.config.settings
        self.device.set_port(s.get("port", "auto"))
        self.set_brightness(s.get("brightness", 80), persist=False)
        self.apply_audio_settings()
        self.apply_slack_settings()
        self.apply_nowplaying_settings()
        self.apply_teams_settings()
        self.edited(undoable=False)

    def apply_slack_settings(self):
        """Re-read the token file and the Slack settings on the Integrations tab. Called at start-up and after a change there."""
        s = self.config.settings
        folder = self.config.path.parent if self.config.path else config_dir()
        self.slack.configure(read_token(folder), channel_ids(s.get("slack_channels", "")),
                             s.get("slack_poll_seconds", 10), s.get("slack_unread_source", "channels"),
                             integrations.is_on(s, "slack"))

    def apply_nowplaying_settings(self):
        s = self.config.settings
        self.nowplaying.configure(bool(s.get("nowplaying_enabled", True)), bool(s.get("nowplaying_art", True)))

    def apply_teams_settings(self):
        self.teams.configure(integrations.is_on(self.config.settings, "teams"))

    def set_integration(self, integration_id: str, on: bool):
        """Switch an integration on or off (the Integrations tab). Its monitor starts or stops reading, and the
        editors are told to show or hide its parts. Nothing that uses it is changed."""
        switches = self.config.settings.setdefault("integrations", {})
        if bool(switches.get(integration_id)) == bool(on):
            return
        switches[integration_id] = bool(on)
        self.apply_slack_settings()
        self.apply_teams_settings()
        self._dirty = True
        self.integrations_changed.emit()
        self.edited(undoable=False)

    def apply_audio_settings(self):
        """Called when the live-meter switch or the meter speed changes on the Device tab."""
        self.audio.set_levels_enabled(bool(self.config.settings.get("audio_levels", True)))
        self._live_targets = None       # force _update_live_meters to re-apply the rate as well
        self._update_live_meters()
