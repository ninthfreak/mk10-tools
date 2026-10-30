"""A sound card that only exists in the tests: mute and volume can be set, levels are whatever we say."""

import threading

from mk10manager.system.audio import INPUT, OUTPUT, AudioState, Endpoint, _VolumeMath


class FakeAudio(_VolumeMath):
    note = ""

    def __init__(self, available: bool = True):
        self.available = available
        self.lock = threading.Lock()
        self.sides = {
            OUTPUT: Endpoint("Test speakers", False, 60, 0.0),
            INPUT: Endpoint("Test microphone", False, 70, 0.0),
        }
        self.level_values = {OUTPUT: 0.0, INPUT: 0.0}
        self.asked_for: frozenset = frozenset()
        self.reads = 0
        self.level_reads = 0

    # ---- what the monitor calls
    def read(self, want_levels=frozenset()) -> AudioState:
        with self.lock:
            self.asked_for = frozenset(want_levels)
            self.reads += 1
            sides = {}
            for target, side in self.sides.items():
                level = self.level_values[target] if target in want_levels else 0.0
                sides[target] = Endpoint(side.name, side.muted, side.volume, level)
            return AudioState(sides[OUTPUT], sides[INPUT], self.available, "")

    def levels(self, want_levels=frozenset()) -> dict:
        with self.lock:
            self.level_reads += 1
            return {target: self.level_values[target] for target in want_levels}

    def apply(self, target, mode, value=0):
        if not self.available:
            return False, "no sound here"
        with self.lock:
            cur = self.sides[target]
            mute, volume = self._new_values(cur, mode, value)
            self.sides[target] = Endpoint(cur.name, cur.muted if mute is None else mute,
                                          cur.volume if volume is None else volume, cur.level)
        return True, ""

    def close(self):
        pass

    # ---- what the tests call
    def set(self, target, muted=None, volume=None, level=None):
        with self.lock:
            cur = self.sides[target]
            self.sides[target] = Endpoint(cur.name, cur.muted if muted is None else muted,
                                          cur.volume if volume is None else volume, cur.level)
            if level is not None:
                self.level_values[target] = level
