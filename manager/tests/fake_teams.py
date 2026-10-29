"""A Teams window that only exists in the tests: call buttons that read and press like the real ones."""

import threading
import time

from mk10manager.system.teams import (CAMERA_BUTTON, HAND_BUTTON, MIC_BUTTON, SHARE_BUTTON, TeamsState,
                                      parse_camera_name, parse_mic_name, parse_share_name)


class FakeTeams:
    note = ""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = True
        self.in_call = False
        self.meeting = "Weekly sync"
        self.muted = False
        self.camera = False
        self.sharing = False
        self.hand = None            # what the hand button's pressed state says (None: it doesn't say)
        self.name = None            # set to make the mic button say something else (another language)
        self.press_works = True     # False: Teams ignores presses
        self.misses = 0             # how many reads in a row lose the buttons, as a WebView2 redraw does
        self.presses = []
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
            names = {MIC_BUTTON: self.name or ("Unmute mic" if self.muted else "Mute mic"),
                     CAMERA_BUTTON: "Turn camera off" if self.camera else "Turn camera on",
                     SHARE_BUTTON: "Stop sharing" if self.sharing else "Share", HAND_BUTTON: "Raise"}
            return TeamsState(available=True, running=True, in_call=True, muted=parse_mic_name(names[MIC_BUTTON]),
                              camera=parse_camera_name(names[CAMERA_BUTTON]),
                              sharing=parse_share_name(names[SHARE_BUTTON]), hand=self.hand, meeting=self.meeting,
                              names=tuple(sorted(names.items())), read_at=now)

    def press(self, control="mic"):
        with self.lock:
            self.presses.append(control)
            if not self.in_call:
                return False, "Not in a Teams call"
            if self.press_works:
                if control == "mic":
                    self.muted = not self.muted
                elif control == "camera":
                    self.camera = not self.camera
            return True, ""

    def close(self):
        pass

    # ---- what the tests call
    def join(self, muted=False, camera=False):
        with self.lock:
            self.in_call, self.muted, self.camera = True, muted, camera

    def leave(self):
        with self.lock:
            self.in_call = False
