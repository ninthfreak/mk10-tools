"""A Teams window that only exists in the tests: a mic button that reads and presses like the real one."""

import threading
import time

from mk10manager.system.teams import TeamsState, parse_mic_name


class FakeTeams:
    note = ""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = True
        self.in_call = False
        self.muted = False
        self.name = None            # set to make the button say something else (another language)
        self.press_works = True     # False: Teams ignores the press
        self.misses = 0             # how many reads in a row lose the button, as a WebView2 redraw does
        self.presses = 0
        self.reads = 0

    # ---- what the monitor calls
    def read(self) -> TeamsState:
        with self.lock:
            self.reads += 1
            now = time.monotonic()
            if self.misses > 0:
                self.misses -= 1
                return TeamsState(available=True, running=True, read_at=now)
            if not self.in_call:
                return TeamsState(available=True, running=self.running, read_at=now)
            name = self.name or ("Unmute mic" if self.muted else "Mute mic")
            return TeamsState(available=True, running=True, in_call=True, muted=parse_mic_name(name),
                              button_name=name, read_at=now)

    def press(self):
        with self.lock:
            self.presses += 1
            if not self.in_call:
                return False, "Not in a Teams call"
            if self.press_works:
                self.muted = not self.muted
            return True, ""

    def close(self):
        pass

    # ---- what the tests call
    def join(self, muted=False):
        with self.lock:
            self.in_call, self.muted = True, muted

    def leave(self):
        with self.lock:
            self.in_call = False
