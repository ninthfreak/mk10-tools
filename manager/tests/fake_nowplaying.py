"""A media player that only exists in the tests."""

import threading
import time
from dataclasses import replace

from mk10manager.system.nowplaying import Track


class FakeNowPlaying:
    note = ""

    def __init__(self, available: bool = True):
        self.lock = threading.Lock()
        self.track = Track(available=available)
        self.art_for: dict = {}
        self.reads = 0
        self.art_fetches = 0

    # ---- what the monitor calls
    def read(self) -> Track:
        with self.lock:
            self.reads += 1
            return replace(self.track, read_at=time.monotonic())

    def fetch_art(self, track: Track) -> bytes:
        with self.lock:
            self.art_fetches += 1
            return self.art_for.get(track.art_key, b"")

    def close(self):
        pass

    # ---- what the tests call
    def play(self, title="Teardrop", artist="Massive Attack", album="Mezzanine", player="Spotify",
             position=10.0, length=300.0, art_key="", status="playing"):
        with self.lock:
            self.track = Track(title=title, artist=artist, album=album, player=player, status=status,
                               position=position, length=length, art_key=art_key, available=True)

    def stop(self):
        with self.lock:
            self.track = Track(available=True)

    def set_art(self, key: str, data: bytes):
        with self.lock:
            self.art_for[key] = data
