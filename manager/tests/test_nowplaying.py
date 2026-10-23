"""What's playing: the state model, the two backends' parsing, the face, and the whole thing end to end."""

import time

import pytest
from PySide6.QtCore import QObject, QRectF, Signal
from PySide6.QtGui import QImage, QPainter

from conftest import wait_until
from fake_mk10 import FakeMK10
from fake_nowplaying import FakeNowPlaying
from mk10manager import conditions, rules
from mk10manager.config import Config
from mk10manager.controller import Controller
from mk10manager.render.canvas import Renderer
from mk10manager.render.template import RenderContext, expand
from mk10manager.system import nowplaying as np


class FakeWatcher(QObject):
    changed = Signal(str, str)
    method, note, available = "test", "", True

    def start(self):
        pass

    def stop(self):
        pass


def playing(**kw) -> np.Track:
    base = dict(title="Teardrop", artist="Massive Attack", album="Mezzanine", player="Spotify",
                status=np.PLAYING, position=65.0, length=330.0, available=True, read_at=time.monotonic())
    base.update(kw)
    return np.Track(**base)


# ---------------------------------------------------------------- the state model
def test_position_moves_on_its_own_while_playing():
    track = playing(position=10.0, read_at=time.monotonic() - 3)
    assert 12.5 < track.position_now() < 13.5, "a 1 s poll should still give a smooth bar"
    assert playing(status=np.PAUSED, position=10.0, read_at=time.monotonic() - 3).position_now() == 10.0
    # it never runs past the end of the track
    assert playing(position=320.0, length=330.0, read_at=time.monotonic() - 60).position_now() == 330.0
    # ... and without a length it just keeps counting, for players that don't say
    assert playing(position=10.0, length=0.0, read_at=time.monotonic() - 3).position_now() > 12.0


def test_track_flags():
    assert playing().playing and playing().known
    assert not np.Track().known
    assert not playing(status=np.PAUSED).playing
    assert playing().same_track(playing(position=99.0))
    assert not playing().same_track(playing(title="Angel"))


def test_format_time():
    assert [np.format_time(s) for s in (0, 5, 65, 3725, -4)] == ["0:00", "0:05", "1:05", "1:02:05", "0:00"]


# ---------------------------------------------------------------- backend parsing
def test_linux_parsing(monkeypatch):
    backend = np.LinuxNowPlaying()
    monkeypatch.setattr(backend, "playerctl", "/usr/bin/playerctl")
    fields = ["spotify", "Playing", "Teardrop", "Massive Attack", "Mezzanine",
              "file:///tmp/art.jpg", "65000000", "330000000"]

    class Result:
        returncode = 0
        stdout = np.SEP.join(fields) + "\n"

    monkeypatch.setattr(np.subprocess, "run", lambda *a, **k: Result())
    track = backend.read()
    assert track.title == "Teardrop" and track.artist == "Massive Attack" and track.album == "Mezzanine"
    assert track.player == "spotify" and track.status == np.PLAYING
    assert track.position == pytest.approx(65.0) and track.length == pytest.approx(330.0)
    assert track.art_key == "file:///tmp/art.jpg" and track.available

    # "no players found" exits non-zero and simply means nothing is playing
    class Empty:
        returncode = 1
        stdout = ""
        stderr = "No players found"

    monkeypatch.setattr(np.subprocess, "run", lambda *a, **k: Empty())
    quiet = backend.read()
    assert quiet.available and not quiet.known

    # a player that only fills in some fields mustn't blow up
    class Partial:
        returncode = 0
        stdout = np.SEP.join(["mpv", "Paused", "something.mkv"])

    monkeypatch.setattr(np.subprocess, "run", lambda *a, **k: Partial())
    part = backend.read()
    assert part.title == "something.mkv" and part.status == np.PAUSED and part.length == 0.0


def test_missing_playerctl_is_not_an_error(monkeypatch):
    backend = np.LinuxNowPlaying()
    monkeypatch.setattr(backend, "playerctl", None)
    track = backend.read()
    assert not track.available and "playerctl" in track.note
    assert "playerctl" in np.describe_backend(backend)


def test_windows_helpers():
    assert np._tidy_app_id("Spotify.exe") == "Spotify"
    assert np._tidy_app_id("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic") == "ZuneMusic"
    assert np._tidy_app_id("") == ""
    assert np._seconds(None) == 0.0
    assert np._seconds(__import__("datetime").timedelta(seconds=90)) == pytest.approx(90.0)
    # a Windows box without PyWinRT reports why rather than failing
    backend = np.WindowsNowPlaying.__new__(np.WindowsNowPlaying)
    backend._mod = None
    backend.note = np.WINRT_MISSING
    assert not backend.read().available
    assert backend.fetch_art(np.Track()) == b""


def test_art_url_handling(tmp_path):
    art = tmp_path / "cover.jpg"
    art.write_bytes(b"\xff\xd8binary")
    assert np.fetch_art_url(art.as_uri()) == b"\xff\xd8binary"
    assert np.fetch_art_url("") == b""
    assert np.fetch_art_url("file:///definitely/not/here.jpg") == b""
    assert np.fetch_art_url("data:image/png;base64,xxxx") == b""


def test_null_backend():
    backend = np.NullNowPlaying()
    assert not backend.read().available
    assert np.describe_backend(backend) == np.NullNowPlaying.note
    backend.close()


def test_report_runs_without_a_player():
    text = np.report(reads=1, gap=0)
    assert "what's playing" in text.lower() and "Backend:" in text


# ---------------------------------------------------------------- conditions and text
def test_playing_condition():
    assert conditions.matches({"when": "playing", "value": "yes"}, track=playing())
    assert not conditions.matches({"when": "playing", "value": "no"}, track=playing())
    assert conditions.matches({"when": "playing", "value": "no"}, track=np.Track(available=True))
    assert not conditions.matches({"when": "playing", "value": "yes"}, track=np.Track(available=False))
    assert not conditions.matches({"when": "playing", "value": "yes"})
    assert conditions.describe({"when": "playing", "value": "yes"}) != "?"
    assert rules.evaluate([{"when": "playing", "value": "yes", "page": "p"}], "", "", {},
                          track=playing()) == "p"


def test_now_playing_placeholders():
    ctx = RenderContext(now_playing=playing())
    assert expand("{track} - {artist} ({album}) on {player}", ctx) == \
        "Teardrop - Massive Attack (Mezzanine) on Spotify"
    assert expand("{track_time}/{track_length}", ctx) == "1:05/5:30"
    assert expand("{track_status}", ctx) == "playing"
    assert expand("[{track}][{track_time}]", RenderContext()) == "[][]"
    # a player with no length leaves the total blank rather than printing 0:00
    assert expand("[{track_length}]", RenderContext(now_playing=playing(length=0.0))) == "[]"


# ---------------------------------------------------------------- the face
def _screen(track, opts=None):
    img = QImage(240, 296, QImage.Format_RGB32)
    p = QPainter(img)
    Renderer().paint_screen(p, QRectF(0, 0, 240, 296), {"face": "nowplaying", "options": opts or {}},
                            RenderContext(now_playing=track))
    p.end()
    return img


def test_face_draws_every_state(qapp):
    art = QImage(80, 80, QImage.Format_RGB32)
    art.fill(0x4080C0)
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    art.save(buf, "PNG")
    art_bytes = bytes(buf.data())
    for opts in ({"art": "cover"}, {"art": "top"}, {"art": "none"}, {"show_progress": False},
                 {"show_player": False}):
        _screen(playing(art=art_bytes, art_key="k"), opts)
        _screen(playing(art=art_bytes, art_key="k", length=0.0), opts)
    # nothing playing: the idle text is shown instead, and nothing crashes
    idle = _screen(np.Track(available=True), {"idle_text": "Nothing on"})
    assert idle.pixelColor(120, 148) is not None
    _screen(np.Track(available=False, note="no backend"))
    # a title far too long for the screen still paints
    _screen(playing(title="x" * 300, artist="y" * 300))


def test_face_shows_the_track(qapp):
    """The title should actually be drawn: a blank screen and a filled one differ."""
    blank = _screen(np.Track(available=True), {"idle_text": ""})
    shown = _screen(playing(), {"art": "none"})
    assert blank != shown


# ---------------------------------------------------------------- the monitor
def test_monitor_follows_the_player(qapp):
    fake = FakeNowPlaying()
    mon = np.NowPlayingMonitor(fake)
    mon.IDLE = mon.BUSY = 0.05
    mon.start()
    try:
        assert wait_until(qapp, lambda: fake.reads > 0, 3)
        assert not mon.track.known
        fake.play()
        assert wait_until(qapp, lambda: mon.track.title == "Teardrop", 3)
        assert mon.track.playing and mon.track.player == "Spotify"
        fake.stop()
        assert wait_until(qapp, lambda: not mon.track.known, 3)
    finally:
        mon.stop()


def test_monitor_fetches_art_once_per_track(qapp):
    fake = FakeNowPlaying()
    fake.set_art("cover-1", b"\xff\xd8the-art")
    fake.play(art_key="cover-1")
    mon = np.NowPlayingMonitor(fake)
    mon.IDLE = mon.BUSY = 0.05
    mon.start()
    try:
        assert wait_until(qapp, lambda: mon.track.art == b"\xff\xd8the-art", 3)
        before = fake.art_fetches
        time.sleep(0.3)
        qapp.processEvents()
        assert fake.art_fetches == before, "the same artwork must not be fetched again and again"
        # a new track fetches again
        fake.play(title="Angel", art_key="cover-2")
        fake.set_art("cover-2", b"\xff\xd8other")
        assert wait_until(qapp, lambda: mon.track.art == b"\xff\xd8other", 3)
    finally:
        mon.stop()


def test_monitor_can_skip_art(qapp):
    fake = FakeNowPlaying()
    fake.set_art("k", b"\xff\xd8art")
    fake.play(art_key="k")
    mon = np.NowPlayingMonitor(fake)
    mon.IDLE = mon.BUSY = 0.05
    mon.configure(True, artwork=False)
    mon.start()
    try:
        assert wait_until(qapp, lambda: mon.track.known, 3)
        time.sleep(0.2)
        qapp.processEvents()
        assert fake.art_fetches == 0 and mon.track.art == b""
    finally:
        mon.stop()


def test_monitor_switches_off(qapp):
    fake = FakeNowPlaying()
    fake.play()
    mon = np.NowPlayingMonitor(fake)
    mon.IDLE = mon.BUSY = 0.05
    mon.start()
    try:
        assert wait_until(qapp, lambda: mon.track.known, 3)
        mon.configure(False)
        assert wait_until(qapp, lambda: not mon.track.available, 3)
        before = fake.reads
        time.sleep(0.2)
        assert fake.reads == before, "nothing should be read while it's switched off"
    finally:
        mon.stop()


# ---------------------------------------------------------------- end to end
@pytest.fixture
def rig(qapp, tmp_path, monkeypatch):
    dev = FakeMK10("v2")
    dev.start()
    monkeypatch.setenv("MK10_PORT", dev.port)
    monkeypatch.setenv("MK10_NO_AUDIO", "1")
    cfg = Config(path=tmp_path / "config.json")
    fake = FakeNowPlaying()
    ctl = Controller(cfg, watcher=FakeWatcher(), nowplaying=np.NowPlayingMonitor(fake))
    ctl.nowplaying.IDLE = ctl.nowplaying.BUSY = 0.05
    ctl.start()
    assert wait_until(qapp, lambda: ctl.device.is_connected and dev.frames), ctl.device.status
    yield ctl, dev, cfg, fake
    ctl.shutdown()
    dev.unplug()


def test_screen_follows_what_is_playing(qapp, rig):
    ctl, dev, cfg, fake = rig
    ctl.page["screen"] = {"face": "nowplaying", "options": {"art": "none", "idle_text": ""}}
    ctl.edited()
    assert wait_until(qapp, lambda: ctl.nowplaying.track.available, 3)
    before = len(dev.frames)
    fake.play()
    assert wait_until(qapp, lambda: ctl.nowplaying.track.title == "Teardrop", 3)
    assert wait_until(qapp, lambda: len(dev.frames) > before, 3), "the device should get a new picture"
    assert expand("{track}", ctl.context()) == "Teardrop"


def test_playing_rule_switches_pages(qapp, rig):
    ctl, dev, cfg, fake = rig
    main = cfg.pages[0]
    music = cfg.add_page("Music")
    cfg.rules.append({"enabled": True, "when": "playing", "value": "yes", "page": music["id"]})
    ctl.edited()
    assert wait_until(qapp, lambda: ctl.nowplaying.track.available, 3)
    assert ctl.page_id == main["id"]
    fake.play()
    assert wait_until(qapp, lambda: ctl.page_id == music["id"], 3)
    fake.stop()
    assert wait_until(qapp, lambda: ctl.page_id == main["id"], 3)


def test_switch_it_off_from_settings(qapp, rig):
    ctl, dev, cfg, fake = rig
    fake.play()
    assert wait_until(qapp, lambda: ctl.nowplaying.track.known, 3)
    cfg.settings["nowplaying_enabled"] = False
    ctl.apply_nowplaying_settings()
    assert wait_until(qapp, lambda: not ctl.nowplaying.track.available, 3)
    assert expand("{track}", ctl.context()) == ""
