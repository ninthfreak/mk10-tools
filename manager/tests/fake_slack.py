"""A Slack workspace that only exists in the tests. No network, no token."""

import threading
from dataclasses import replace

from mk10manager.system.slack import SlackState


class FakeSlack:
    """Stands in for SlackReader: the monitor can't tell the difference."""

    def __init__(self, connected: bool = True):
        self.lock = threading.Lock()
        self.state = SlackState(connected=connected, user_id="U1", user_name="tony", team_id="T0ACME",
                                team_name="Acme Corp", presence="active")
        self.token = ""
        self.reads = 0
        self.applied = []
        self.fail_with = ""

    # ---- what the monitor calls
    def set_token(self, token):
        self.token = (token or "").strip()

    def read(self, channels=(), unread_source="channels") -> SlackState:
        with self.lock:
            self.reads += 1
            return self.state

    def apply(self, mode, params):
        self.applied.append((mode, dict(params or {})))
        if self.fail_with:
            return False, self.fail_with
        with self.lock:
            if mode == "presence":
                self.state = replace(self.state, presence=params.get("presence") or "auto")
            elif mode == "dnd":
                self.state = replace(self.state, dnd=True)
            elif mode == "end_dnd":
                self.state = replace(self.state, dnd=False)
            elif mode == "status":
                self.state = replace(self.state, status_text=params.get("text") or "",
                                     status_emoji=params.get("emoji") or "")
            elif mode == "clear_status":
                self.state = replace(self.state, status_text="", status_emoji="")
        return True, ""

    # ---- what the tests call
    def set(self, **kw):
        with self.lock:
            self.state = replace(self.state, **kw)
