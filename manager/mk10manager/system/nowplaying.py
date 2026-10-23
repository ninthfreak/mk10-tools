"""What's playing right now, whoever is playing it.

Both systems have one place that every player reports into, so this needs no per-app work:

- **Windows:** SMTC (`Windows.Media.Control`) — the same thing the volume flyout's now-playing panel shows.
  Reached through PyWinRT's `winrt-Windows.Media.Control` package, which is optional: without it this
  quietly reports nothing rather than failing.
- **Linux:** MPRIS2 over D-Bus, through `playerctl`, which the media keys already use.

Reading happens on NowPlayingMonitor's own thread; the GUI side reads `monitor.track` and listens to `changed`.
"""

import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal

IS_WIN = sys.platform.startswith("win")
PLAYING, PAUSED, STOPPED = "playing", "paused", "stopped"
MAX_ART_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class Track:
    title: str = ""
    artist: str = ""
    album: str = ""
    player: str = ""             # the app doing the playing, as the system names it
    status: str = ""             # playing / paused / stopped / ""
    position: float = 0.0        # seconds into the track when this was read
    length: float = 0.0          # seconds, 0 when the player doesn't say
    art_key: str = ""            # identifies the artwork, so it's only fetched when it changes
    art: bytes = b""             # the artwork itself, ready for QImage.fromData
    read_at: float = 0.0         # time.monotonic() when this was read, for working out the position since
    available: bool = True       # False means we can't see what's playing at all
    note: str = ""

    @property
    def playing(self) -> bool:
        return self.status == PLAYING

    @property
    def known(self) -> bool:
        return bool(self.title or self.artist)

    def position_now(self) -> float:
        """The position as it is *now*: a once-a-second read still gives a smooth progress bar."""
        if not self.playing or not self.read_at:
            return self.position
        moved = self.position + max(0.0, time.monotonic() - self.read_at)
        return min(moved, self.length) if self.length else moved

    def same_track(self, other: "Track") -> bool:
        return (self.title, self.artist, self.album, self.player) == \
               (other.title, other.artist, other.album, other.player)


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs) if hours else "%d:%02d" % (minutes, secs)


class NullNowPlaying:
    note = "Reading what's playing isn't available on this system."

    def read(self) -> Track:
        return Track(available=False, note=self.note)

    def fetch_art(self, track: Track) -> bytes:
        return b""

    def close(self):
        pass


# ---------------------------------------------------------------------------- Linux
_US = 1_000_000.0
SEP = "\x1f"
PLAYERCTL_FORMAT = SEP.join("{{%s}}" % f for f in (
    "playerName", "status", "title", "artist", "album", "mpris:artUrl", "position", "mpris:length"))


class LinuxNowPlaying:
    """MPRIS through playerctl. Covers Firefox, Chromium, Spotify, VLC, mpv and most of the rest."""

    def __init__(self):
        self.playerctl = shutil.which("playerctl")

    @property
    def note(self) -> str:
        if not self.playerctl:
            return "playerctl wasn't found, so what's playing can't be read. Install playerctl."
        return ""

    def read(self) -> Track:
        if not self.playerctl:
            return Track(available=False, note=self.note)
        try:
            r = subprocess.run([self.playerctl, "metadata", "--format", PLAYERCTL_FORMAT],
                               capture_output=True, text=True, timeout=3)
        except (OSError, subprocess.TimeoutExpired) as e:
            return Track(available=False, note=str(e))
        if r.returncode != 0:
            # playerctl exits non-zero with "No players found", which simply means nothing is playing.
            return Track(available=True, read_at=time.monotonic())
        parts = (r.stdout or "").strip("\n").split(SEP)
        parts += [""] * (8 - len(parts))
        player, status, title, artist, album, art_url, position, length = parts[:8]
        return Track(title=title.strip(), artist=artist.strip(), album=album.strip(),
                     player=player.strip(), status=self._status(status),
                     position=_number(position) / _US, length=_number(length) / _US,
                     art_key=art_url.strip(), read_at=time.monotonic(), available=True)

    @staticmethod
    def _status(text: str) -> str:
        text = (text or "").strip().lower()
        return {"playing": PLAYING, "paused": PAUSED, "stopped": STOPPED}.get(text, "")

    def fetch_art(self, track: Track) -> bytes:
        return fetch_art_url(track.art_key)

    def close(self):
        pass


def _number(text) -> float:
    try:
        return float(str(text).strip() or 0)
    except ValueError:
        return 0.0


def fetch_art_url(url: str) -> bytes:
    """MPRIS artwork is a URL: usually a file on disk, sometimes one the player wants us to download."""
    url = (url or "").strip()
    if not url:
        return b""
    if url.startswith("file://"):
        try:
            path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
            with open(path, "rb") as f:
                return f.read(MAX_ART_BYTES)
        except OSError:
            return b""
    if not url.startswith(("http://", "https://")):
        return b""
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            return r.read(MAX_ART_BYTES)
    except Exception:
        return b""


# ---------------------------------------------------------------------------- Windows
WINRT_PACKAGES = ("winrt-Windows.Media.Control", "winrt-Windows.Foundation",
                  "winrt-Windows.Storage.Streams")
WINRT_MISSING = ("Reading what's playing on Windows needs PyWinRT. In the app's folder run:\n"
                 "    .venv\\Scripts\\pip install " + " ".join(WINRT_PACKAGES))

_SMTC_STATUS = {4: PLAYING, 5: PAUSED, 3: STOPPED}      # GlobalSystemMediaTransportControlsSessionPlaybackStatus


class WindowsNowPlaying:
    """SMTC through PyWinRT. Every WinRT call here is async, so each read runs a tiny event loop."""

    def __init__(self):
        self.note = ""
        self._loop = None
        self._mod = None
        self._streams = None
        if not self._load():
            self.note = WINRT_MISSING

    def _load(self) -> bool:
        try:
            import winrt.windows.media.control as control
            import winrt.windows.storage.streams as streams
        except ImportError:
            return False
        except Exception as e:                     # a broken install shouldn't take the app down
            self.note = "PyWinRT wouldn't load: %s" % e
            return False
        self._mod, self._streams = control, streams
        return True

    def _run(self, coro):
        import asyncio
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def read(self) -> Track:
        if self._mod is None:
            return Track(available=False, note=self.note)
        try:
            return self._run(self._read())
        except Exception as e:
            return Track(available=False, note="Couldn't read what's playing: %s" % e)

    async def _read(self) -> Track:
        manager = await self._mod.GlobalSystemMediaTransportControlsSessionManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return Track(available=True, read_at=time.monotonic())
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        timeline = session.get_timeline_properties()
        status = _SMTC_STATUS.get(int(getattr(info.playback_status, "value", info.playback_status)), "")
        title = getattr(props, "title", "") or ""
        artist = getattr(props, "artist", "") or ""
        album = getattr(props, "album_title", "") or ""
        player = getattr(session, "source_app_user_model_id", "") or ""
        return Track(title=title.strip(), artist=artist.strip(), album=album.strip(),
                     player=_tidy_app_id(player), status=status,
                     position=_seconds(getattr(timeline, "position", None)),
                     length=_seconds(getattr(timeline, "end_time", None)),
                     art_key="%s|%s|%s" % (title, artist, album),
                     read_at=time.monotonic(), available=True)

    def fetch_art(self, track: Track) -> bytes:
        if self._mod is None:
            return b""
        try:
            return self._run(self._art())
        except Exception:
            return b""

    async def _art(self) -> bytes:
        manager = await self._mod.GlobalSystemMediaTransportControlsSessionManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return b""
        props = await session.try_get_media_properties_async()
        ref = getattr(props, "thumbnail", None)
        if ref is None:
            return b""
        stream = await ref.open_read_async()
        size = min(int(getattr(stream, "size", 0) or 0), MAX_ART_BYTES)
        if size <= 0:
            return b""
        reader = self._streams.DataReader(stream)
        await reader.load_async(size)
        return bytes(reader.read_bytes(size))

    def close(self):
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None


def _seconds(value) -> float:
    """WinRT hands back a timedelta for positions and lengths."""
    if value is None:
        return 0.0
    for attr in ("total_seconds",):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return max(0.0, float(fn()))
            except Exception:
                return 0.0
    return max(0.0, _number(value))


_APP_ID = re.compile(r"[._]")


def _tidy_app_id(app_id: str) -> str:
    """'Spotify.exe' and 'Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic' -> something readable."""
    name = (app_id or "").split("!")[0].strip()
    if name.lower().endswith(".exe"):
        return name[:-4]
    parts = [p for p in _APP_ID.split(name) if p and not p.endswith("bbwe")]
    return parts[-1] if parts else name


# ---------------------------------------------------------------------------- picking one
def make_backend():
    import os
    if os.environ.get("MK10_NO_NOWPLAYING"):
        return NullNowPlaying()
    if IS_WIN:
        return WindowsNowPlaying()
    if sys.platform.startswith("linux"):
        return LinuxNowPlaying()
    return NullNowPlaying()


def describe_backend(backend=None) -> str:
    backend = backend if backend is not None else make_backend()
    if isinstance(backend, NullNowPlaying):
        return NullNowPlaying.note
    note = getattr(backend, "note", "")
    if isinstance(backend, LinuxNowPlaying) and not backend.playerctl:
        return note
    name = "Windows media controls (SMTC)" if isinstance(backend, WindowsNowPlaying) else "playerctl (MPRIS)"
    return "%s. %s" % (name, note) if note else name


# ---------------------------------------------------------------------------- checking it works
def report(reads: int = 3, gap: float = 1.5) -> str:
    """What the system says is playing, in detail. Run this on the machine itself before trusting the face."""
    lines = ["MK10 Manager: what's playing", "=" * 40,
             "System: %s" % sys.platform,
             "Python: %s" % sys.version.split()[0],
             "Running: %s" % (sys.executable or "?")]
    if IS_WIN:
        lines.append("")
        lines.append("PyWinRT packages:")
        for module, package in (("winrt.windows.media.control", "winrt-Windows.Media.Control"),
                                ("winrt.windows.foundation", "winrt-Windows.Foundation"),
                                ("winrt.windows.storage.streams", "winrt-Windows.Storage.Streams")):
            try:
                __import__(module)
                lines.append("  found    %s" % package)
            except ImportError:
                lines.append("  MISSING  %s" % package)
            except Exception as e:
                lines.append("  BROKEN   %s (%s)" % (package, e))
    backend = make_backend()
    lines += ["", "Backend: %s" % type(backend).__name__, "Says: %s" % (describe_backend(backend) or "(nothing)")]
    for i in range(max(1, reads)):
        if i:
            time.sleep(gap)
        try:
            track = backend.read()
        except Exception as e:
            lines.append("")
            lines.append("Read %d FAILED: %s" % (i + 1, e))
            continue
        lines += ["", "Read %d" % (i + 1),
                  "  available  %s" % track.available,
                  "  note       %s" % (track.note or "(none)"),
                  "  player     %r" % track.player,
                  "  status     %r" % track.status,
                  "  title      %r" % track.title,
                  "  artist     %r" % track.artist,
                  "  album      %r" % track.album,
                  "  position   %.1f s of %.1f s" % (track.position, track.length),
                  "  art key    %s" % (track.art_key[:70] or "(none)")]
        if track.known and track.art_key:
            try:
                art = backend.fetch_art(track)
            except Exception as e:
                art = b""
                lines.append("  art        FAILED: %s" % e)
            if art:
                kind = "JPEG" if art[:2] == b"\xff\xd8" else "PNG" if art[:8].startswith(b"\x89PNG") else "?"
                lines.append("  art        %d bytes, looks like %s" % (len(art), kind))
            else:
                lines.append("  art        nothing came back")
    try:
        backend.close()
    except Exception:
        pass
    lines += ["", "If the title and artist look right above, the small screen will show them."]
    if IS_WIN and isinstance(backend, WindowsNowPlaying) and backend._mod is None:
        lines += ["", WINRT_MISSING]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- monitor
class NowPlayingMonitor(QObject):
    """Reads what's playing on its own thread. `track` is always safe to read from the GUI thread."""

    changed = Signal()

    IDLE = 2.0           # seconds between reads when nothing is playing
    BUSY = 1.0           # ... and when something is

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self.backend = backend if backend is not None else make_backend()
        self.track = Track(note=getattr(self.backend, "note", ""))
        self._art: dict = {}            # art_key -> bytes, a couple of tracks' worth
        self._enabled = True
        self._want_art = True
        self._stop = threading.Event()
        self._poke = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- GUI thread
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mk10-nowplaying", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        self._poke.set()
        if self._thread:
            self._thread.join(timeout)
        try:
            self.backend.close()
        except Exception:
            pass

    def configure(self, enabled: bool, artwork: bool = True):
        was = self._enabled
        self._enabled = bool(enabled)
        self._want_art = bool(artwork)
        if was and not self._enabled:
            self.track = Track(available=False, note="Switched off on the Device and app tab.")
            self.changed.emit()
        self._poke.set()

    def refresh_soon(self):
        self._poke.set()

    # ---- background thread
    def _run(self):
        while not self._stop.is_set():
            if self._enabled:
                try:
                    track = self.backend.read()
                except Exception as e:
                    track = Track(available=False, note="Reading what's playing failed: %s" % e)
                track = self._with_art(track)
                if self._differs(track):
                    self.track = track
                    self.changed.emit()
                else:
                    self.track = track      # keep read_at fresh so the progress bar stays honest
            self._poke.wait(self.BUSY if self.track.playing else self.IDLE)
            self._poke.clear()

    def _differs(self, track: Track) -> bool:
        """Position moves constantly; only redraw for things the screen would actually show differently."""
        old = self.track
        return (track.title, track.artist, track.album, track.player, track.status, track.available,
                track.note, track.art_key, int(track.length)) != \
               (old.title, old.artist, old.album, old.player, old.status, old.available,
                old.note, old.art_key, int(old.length))

    def _with_art(self, track: Track) -> Track:
        if not self._want_art or not track.art_key or not track.known:
            return track
        if track.art_key in self._art:
            return replace(track, art=self._art[track.art_key])
        try:
            data = self.backend.fetch_art(track)
        except Exception:
            data = b""
        self._art[track.art_key] = data
        if len(self._art) > 6:                      # a handful is plenty; the screen shows one at a time
            for key in list(self._art)[:-6]:
                del self._art[key]
        return replace(track, art=data)
