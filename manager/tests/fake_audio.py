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
        self.levels = {OUTPUT: 0.0, INPUT: 0.0}
        self.asked_for: frozenset = frozenset()
        self.reads = 0

    # ---- what the monitor calls
    def read(self, want_levels=frozenset()) -> AudioState:
        with self.lock:
            self.asked_for = frozenset(want_levels)
            self.reads += 1
            sides = {}
            for target, side in self.sides.items():
                level = self.levels[target] if target in want_levels else 0.0
                sides[target] = Endpoint(side.name, side.muted, side.volume, level)
            return AudioState(sides[OUTPUT], sides[INPUT], self.available, "")

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
                self.levels[target] = level


class MultiDeviceAudio(_VolumeMath):
    """Two devices on each side, a default for each, and apps that stream to devices of their own choosing,
    the way Teams picks its own headset whatever Windows' default is."""

    note = ""

    def __init__(self):
        self.lock = threading.Lock()
        self.devices = {"spk": Endpoint("Speakers (Realtek(R) Audio)", False, 60, 0.0),
                        "hp": Endpoint("Headphones (Jabra Evolve2 65)", False, 40, 0.0),
                        "mic": Endpoint("Microphone (Realtek(R) Audio)", True, 70, 0.0),
                        "hs": Endpoint("Headset Microphone (Jabra Evolve2 65)", False, 80, 0.0)}
        self.defaults = {OUTPUT: "spk", INPUT: "mic"}
        self.device_ids = {OUTPUT: None, INPUT: None}
        self.streams: dict[str, dict] = {}      # app -> {side: device id it's streaming to, or None}

    def _id(self, target):
        return self.device_ids.get(target) or self.defaults[target]

    def read(self, want_levels=frozenset()) -> AudioState:
        with self.lock:
            return AudioState(self.devices[self._id(OUTPUT)], self.devices[self._id(INPUT)], True, "")

    def apply(self, target, mode, value=0):
        with self.lock:
            dev = self._id(target)
            cur = self.devices[dev]
            mute, volume = self._new_values(cur, mode, value)
            self.devices[dev] = Endpoint(cur.name, cur.muted if mute is None else mute,
                                         cur.volume if volume is None else volume, cur.level)
        return True, ""

    def set_device_ids(self, ids):
        with self.lock:
            self.device_ids = {OUTPUT: ids.get(OUTPUT), INPUT: ids.get(INPUT)}

    def app_devices(self, name_part):
        with self.lock:
            found = self.streams.get(name_part, {})
            return {OUTPUT: found.get(OUTPUT), INPUT: found.get(INPUT)}

    def close(self):
        pass

    def muted(self, dev):
        with self.lock:
            return self.devices[dev].muted
