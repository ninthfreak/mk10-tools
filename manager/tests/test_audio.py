"""Context-sensitive keys: conditions, meters, the audio action, and the whole thing end to end."""

from datetime import datetime

import pytest
from PySide6.QtCore import QObject, QRectF, Signal
from PySide6.QtGui import QImage, QPainter

from conftest import wait_until
from fake_audio import FakeAudio
from fake_mk10 import FakeMK10
from mk10manager import conditions, rules
from mk10manager.config import Config, new_key
from mk10manager.controller import SLOW_TICK_MS, Controller
from mk10manager.render import meters
from mk10manager.render.canvas import Renderer
from mk10manager.render.template import RenderContext, expand
from mk10manager.system.audio import INPUT, OUTPUT, AudioMonitor, AudioState, Endpoint


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


def state(out_muted=False, in_muted=False, out_vol=60, in_vol=70, out_level=0.0, in_level=0.0, available=True):
    return AudioState(Endpoint("Speakers", out_muted, out_vol, out_level),
                      Endpoint("Mic", in_muted, in_vol, in_level), available, "")


# ---------------------------------------------------------------- conditions
def test_audio_conditions():
    muted = state(out_muted=True, in_muted=False)
    assert conditions.matches({"when": "output_muted", "value": "yes"}, audio=muted)
    assert not conditions.matches({"when": "output_muted", "value": "no"}, audio=muted)
    assert conditions.matches({"when": "input_muted", "value": "no"}, audio=muted)
    assert not conditions.matches({"when": "input_muted", "value": "yes"}, audio=muted)
    # nothing is true when we can't read the sound card, or when the state is unknown
    assert not conditions.matches({"when": "output_muted", "value": "yes"}, audio=state(available=False))
    unknown = AudioState(Endpoint("x", None, None), Endpoint("y", None, None), True, "")
    assert not conditions.matches({"when": "output_muted", "value": "yes"}, audio=unknown)
    assert not conditions.matches({"when": "output_muted", "value": "yes"})
    # switched-off states never match
    assert not conditions.matches({"enabled": False, "when": "output_muted", "value": "yes"}, audio=muted)
    # the old conditions still work through the shared evaluator
    now = datetime(2026, 10, 22, 23, 30)
    assert rules.matches({"when": "app", "value": "Firefox.exe"}, "firefox", "", {}, now)
    assert rules.evaluate([{"when": "input_muted", "value": "yes", "page": "p"}], "", "", {}, now,
                          state(in_muted=True)) == "p"


def test_describe_covers_every_condition():
    for kind, _label in conditions.CONDITIONS:
        assert conditions.describe(conditions.new_condition(kind)) != "?"
        assert "When" in conditions.describe_state(conditions.new_state(kind))
    assert conditions.describe({"when": "nonsense"}) == "?"


def test_resolve_key_picks_the_first_matching_state():
    key = new_key("Mute", "#185FA5")
    key["icon"] = {"kind": "builtin", "name": "volume", "color": "#FFFFFF"}
    off = conditions.new_state("output_muted")
    off.update({"use_bg": True, "bg": "#854F0B", "use_label": True, "label": "Muted",
                "icon_mode": "choose", "icon": "volume-off"})
    never = conditions.new_state("app")
    never["value"] = "nothing-is-called-this"
    key["states"] = [never, off]

    plain = conditions.resolve_key(key, RenderContext(audio=state()))
    assert plain["label"] == "Mute" and plain["bg"] == "#185FA5" and plain["icon"]["name"] == "volume"
    changed = conditions.resolve_key(key, RenderContext(audio=state(out_muted=True)))
    assert changed["label"] == "Muted" and changed["bg"] == "#854F0B"
    assert changed["icon"]["name"] == "volume-off"
    assert key["label"] == "Mute", "the stored key must not be touched"
    # a state that only drops the icon
    key["states"] = [dict(off, icon_mode="none", use_label=False, use_bg=False)]
    assert conditions.resolve_key(key, RenderContext(audio=state(out_muted=True)))["icon"] is None


# ---------------------------------------------------------------- meters
def test_meter_values_and_live_targets():
    ctx = RenderContext(audio=state(out_vol=40, in_level=0.5), variables={"load": "75", "word": "x"})
    assert meters.meter_value({"source": "output_volume"}, ctx) == pytest.approx(0.4)
    assert meters.meter_value({"source": "input_level"}, ctx) == pytest.approx(0.5)
    assert meters.meter_value({"source": "variable", "name": "load"}, ctx) == pytest.approx(0.75)
    assert meters.meter_value({"source": "variable", "name": "word"}, ctx) is None
    assert meters.meter_value({"source": "none"}, ctx) is None
    assert meters.meter_value(None, ctx) is None
    assert meters.meter_value({"source": "output_volume"}, RenderContext()) is None
    assert meters.live_target({"source": "input_level"}) == INPUT
    assert meters.live_target({"source": "output_level"}) == OUTPUT
    assert meters.live_target({"source": "output_volume"}) is None
    assert meters.live_target(None) is None


def _key_pixels(key, ctx, w=128, h=128):
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    Renderer().paint_key(p, QRectF(0, 0, w, h), key, ctx)
    p.end()
    return img


def test_meter_is_drawn_behind_the_key(qapp):
    key = new_key("", "#000000")
    key["meter"] = {"source": "input_level", "style": "bar_bottom", "color": "#378ADD", "strength": 100}
    quiet = _key_pixels(key, RenderContext(audio=state(in_level=0.0)))
    loud = _key_pixels(key, RenderContext(audio=state(in_level=0.9)))
    assert quiet.pixelColor(64, 20).blue() < 40          # nothing near the top while it's quiet
    assert loud.pixelColor(64, 20).blue() > 120          # the bar reaches the top when it's loud
    assert loud.pixelColor(64, 120).blue() > 120
    # every style paints something and none of them crash
    for style, _label in meters.STYLES:
        key["meter"]["style"] = style
        _key_pixels(key, RenderContext(audio=state(in_level=0.6)))


# ---------------------------------------------------------------- placeholders
def test_audio_placeholders():
    ctx = RenderContext(audio=state(out_muted=True, out_vol=65, in_vol=80, in_level=0.42))
    assert expand("{out_device} {volume} {mute}", ctx) == "Speakers 65 muted"
    assert expand("{in_device} {mic_volume} {mic_mute}|", ctx) == "Mic 80 |"
    assert expand("{mute:Off|On} {mic_mute:Off|On}", ctx) == "Off On"
    assert expand("{mic_level}", ctx) == "42"
    assert expand("{volume}{mute}|", RenderContext()) == "|"


# ---------------------------------------------------------------- the monitor
def test_monitor_only_measures_what_is_asked_for(qapp):
    fake = FakeAudio()
    fake.set(INPUT, level=0.8)
    mon = AudioMonitor(fake)
    mon.start()
    try:
        assert wait_until(qapp, lambda: mon.state.available)
        assert mon.state.input.level == 0.0 and fake.asked_for == frozenset()
        mon.want_levels({INPUT})
        assert wait_until(qapp, lambda: mon.state.input.level > 0.5, 3)
        assert fake.asked_for == frozenset({INPUT})
        # peaks are held and sink back rather than flapping to zero
        fake.set(INPUT, level=0.0)
        assert wait_until(qapp, lambda: 0.0 < mon.state.input.level < 0.5, 3)
        # the fall is gentle now, so give it longer to settle all the way to nothing
        assert wait_until(qapp, lambda: mon.state.input.level == 0.0, 6)
        # switching the meters off stops the measuring
        mon.want_levels({INPUT})
        mon.set_levels_enabled(False)
        mon.want_levels({INPUT})
        assert wait_until(qapp, lambda: fake.asked_for == frozenset(), 3)
    finally:
        mon.stop()


def test_peak_decay_is_measured_in_seconds(qapp):
    """The fall has to be the same speed whatever the poll rate, or the meter's feel changes with it."""
    import time as _time
    mon = AudioMonitor(FakeAudio())
    mon._held = {OUTPUT: 1.0, INPUT: 0.0}
    mon._held_at = _time.monotonic() - 1.0          # pretend a whole second passed since the last hold
    held = mon._hold_peaks(state(), frozenset({OUTPUT}))
    assert held.output.level == pytest.approx(AudioMonitor.DECAY_PER_SECOND, abs=0.02)
    # half the time, less than half the fall (it's a curve, not a line)
    mon._held = {OUTPUT: 1.0, INPUT: 0.0}
    mon._held_at = _time.monotonic() - 0.5
    half = mon._hold_peaks(state(), frozenset({OUTPUT})).output.level
    assert AudioMonitor.DECAY_PER_SECOND < half < 1.0
    # a level that isn't wanted drops straight to nothing
    mon._held = {OUTPUT: 1.0, INPUT: 1.0}
    assert mon._hold_peaks(state(), frozenset()).output.level == 0.0
    # a louder reading wins immediately: meters rise at once and fall slowly
    mon._held = {OUTPUT: 0.1, INPUT: 0.0}
    mon._held_at = _time.monotonic()
    assert mon._hold_peaks(state(out_level=0.9), frozenset({OUTPUT})).output.level == pytest.approx(0.9)


def test_monitor_apply_goes_through_to_the_backend(qapp):
    fake = FakeAudio()
    mon = AudioMonitor(fake)
    assert mon.apply(OUTPUT, "toggle_mute") == (True, "")
    assert fake.sides[OUTPUT].muted is True
    mon.apply(OUTPUT, "toggle_mute")
    assert fake.sides[OUTPUT].muted is False
    mon.apply(INPUT, "volume_down", 25)
    assert fake.sides[INPUT].volume == 45
    mon.apply(INPUT, "set_volume", 300)
    assert fake.sides[INPUT].volume == 100
    mon.apply(INPUT, "mute")
    assert fake.sides[INPUT].muted is True
    with pytest.raises(ValueError):
        fake.apply(INPUT, "nonsense")


# ---------------------------------------------------------------- end to end
@pytest.fixture
def rig(qapp, tmp_path, monkeypatch):
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    cfg = Config(path=tmp_path / "config.json")
    fake = FakeAudio()
    ctl = Controller(cfg, watcher=FakeWatcher(), audio=AudioMonitor(fake))
    ctl.start()
    assert wait_until(qapp, lambda: ctl.device.is_connected and dev.frames), ctl.device.status
    yield ctl, dev, cfg, fake
    ctl.shutdown()
    dev.unplug()


def pixel(jpeg: bytes, x, y):
    c = QImage.fromData(jpeg).pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def test_mute_key_follows_the_real_mute_state(qapp, rig):
    ctl, dev, cfg, fake = rig
    assert wait_until(qapp, lambda: ctl.audio.state.available)
    # key 1 is the sample mute key: blue while sound is on
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 20, 20)[2] > 100)
    r, g, b = pixel(dev.frames[-1], 20, 20)
    assert b > r

    # pressing it mutes the speakers for real, and the key turns orange
    dev.tap(0, 0)
    assert wait_until(qapp, lambda: fake.sides[OUTPUT].muted is True, 3)
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 20, 20)[0] > 80, 3)
    r, g, b = pixel(dev.frames[-1], 20, 20)
    assert r > b, "a muted key should be orange, not blue"

    # ... and something else muting them (not us) shows up just the same
    dev.tap(0, 0)
    assert wait_until(qapp, lambda: fake.sides[OUTPUT].muted is False, 3)
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 20, 20)[2] > 100, 3)
    fake.set(OUTPUT, muted=True)
    assert wait_until(qapp, lambda: pixel(dev.frames[-1], 20, 20)[0] > 80, 3)


def test_mic_key_and_audio_rule(qapp, rig):
    ctl, dev, cfg, fake = rig
    meeting = cfg.add_page("Meeting")
    cfg.rules.append({"enabled": True, "when": "input_muted", "value": "yes", "page": meeting["id"]})
    ctl.edited()
    assert wait_until(qapp, lambda: ctl.audio.state.input.muted is False)
    assert ctl.page_id == cfg.pages[0]["id"]
    # key 6 is the sample microphone key, bottom-left on the device
    dev.tap(1, 0)
    assert wait_until(qapp, lambda: fake.sides[INPUT].muted is True, 3)
    assert wait_until(qapp, lambda: ctl.page_id == meeting["id"], 3)
    fake.set(INPUT, muted=False)
    assert wait_until(qapp, lambda: ctl.page_id == cfg.pages[0]["id"], 3)


def test_live_meters_only_run_for_the_page_that_shows_them(qapp, rig):
    ctl, dev, cfg, fake = rig
    # the sample page has both mute keys, so both sides are measured
    assert wait_until(qapp, lambda: fake.asked_for == frozenset({OUTPUT, INPUT}), 3)
    assert ctl._tick.interval() < SLOW_TICK_MS
    plain = cfg.add_page("Plain")
    ctl.set_page(plain["id"])
    assert wait_until(qapp, lambda: fake.asked_for == frozenset(), 3)
    assert ctl._tick.interval() == SLOW_TICK_MS
    # a small screen showing the sound face needs them again
    plain["screen"] = {"face": "audio", "options": {}}
    ctl.edited()
    assert wait_until(qapp, lambda: fake.asked_for == frozenset({OUTPUT, INPUT}), 3)
    # turning the meters off on the Device tab stops all of it
    cfg.settings["audio_levels"] = False
    ctl.apply_audio_settings()
    assert wait_until(qapp, lambda: fake.asked_for == frozenset(), 3)
    assert ctl._tick.interval() == SLOW_TICK_MS


def test_audio_action_summaries():
    from mk10manager.actions import summary
    assert summary({"type": "audio", "target": "input", "mode": "toggle_mute"}) == "Toggle mute on the microphone"
    assert summary({"type": "audio", "target": "output", "mode": "volume_up", "value": 5}) == \
        "Volume up on the speakers by 5%"
    assert summary({"type": "audio", "target": "output", "mode": "set_volume", "value": 30}) == \
        "Set the speakers to 30%"


def test_audio_action_without_a_backend_reports_why():
    from mk10manager.actions import ActionRunner
    runner = ActionRunner(None)
    problems = []
    runner.failed.connect(problems.append)
    runner._one({"type": "audio", "target": "output", "mode": "toggle_mute"})
    assert problems and "isn't available" in problems[0]
