"""A Teams window that only exists in the tests: call buttons that read and press like the real ones."""

import threading
import time

from mk10manager.system.teams import (CAMERA_BUTTON, HAND_BUTTON, MIC_BUTTON, SHARE_BUTTON, STOP_SHARE, TeamsState,
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
        self.sharing = False        # a screen or window: share-button says Stop sharing
        self.content = False        # a whiteboard or an Excel sheet: a Stop sharing button of its own
        self.hand = None            # what the hand button's pressed state says (None: it doesn't say)
        self.shared = "your screen" # what the sharing bar says, while sharing
        self.elapsed = None         # seconds on Teams' call clock when the call was joined (None: no clock)
        self.joined_at = 0.0
        self.name = None            # set to make the mic button say something else (another language)
        self.press_works = True     # False: Teams ignores presses
        self.misses = 0             # how many reads in a row lose the buttons, as a WebView2 redraw does
        self.presses = []
        self.focused = []           # the presses that asked for the call window to come forward
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
            started = 0.0
            if self.elapsed is not None:
                started = now - int(self.elapsed + now - self.joined_at)      # the clock shows whole seconds
            return TeamsState(available=True, running=True, in_call=True, muted=parse_mic_name(names[MIC_BUTTON]),
                              camera=parse_camera_name(names[CAMERA_BUTTON]),
                              sharing=parse_share_name(names[SHARE_BUTTON]) or self.content, hand=self.hand,
                              shared=self.shared if self.sharing else "", meeting=self.meeting, started=started,
                              names=tuple(sorted(names.items())), read_at=now)

    def press(self, control="mic", focus=False, want=None):
        with self.lock:
            self.presses.append(control)
            if focus:
                self.focused.append(control)
            if not self.in_call:
                return False, "Not in a Teams call"
            if self.press_works:
                if control == "mic":
                    self.muted = not self.muted
                elif control == "camera":
                    self.camera = not self.camera
                elif control == "share" and self.sharing:       # otherwise Share only opens Teams' picker
                    self.sharing = False
                elif control == STOP_SHARE:
                    if not (self.sharing or self.content):
                        return False, "Teams isn't showing a Stop sharing button"
                    self.sharing = self.content = False
            return True, ""

    def close(self):
        pass

    # ---- what the tests call
    def join(self, muted=False, camera=False, elapsed=None):
        with self.lock:
            self.in_call, self.muted, self.camera = True, muted, camera
            self.elapsed, self.joined_at = elapsed, time.monotonic()

    def leave(self):
        with self.lock:
            self.in_call = False
