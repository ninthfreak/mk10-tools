"""Slack: what you're doing in it, and changing it from a key.

Slack has no local control port (Teams' one was retired), so everything here is the Web API over HTTPS with a
user token. Reading happens on SlackMonitor's own thread; the GUI side reads `monitor.state` and listens to
`changed`.

The token is a password. It lives in its own file in the settings folder with owner-only permissions, never in
`config.json`, so it stays out of undo snapshots, backups and anything the user might share.
"""

import json
import os
import ssl
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

API = "https://slack.com/api/"
TOKEN_FILE = "slack-token"
PRESENCE_CHOICES = [("auto", "Active"), ("away", "Away")]

#: How much of the Slack state we can read. Everything here is documented API except `unread_all` (see below).
UNREAD_SOURCES = [
    ("channels", "Only the conversations listed below"),
    ("all", "Everything (unofficial, may stop working)"),
]


@dataclass(frozen=True)
class SlackState:
    connected: bool = False
    note: str = ""
    user_id: str = ""
    user_name: str = ""
    team_id: str = ""
    team_name: str = ""
    presence: str = ""              # "active" / "away" / ""
    in_huddle: bool = False
    huddle_channel: str = ""
    dnd: bool = False               # snoozed right now
    dnd_until: int = 0              # epoch seconds, 0 when not snoozed
    status_text: str = ""
    status_emoji: str = ""
    unread: int = 0
    mentions: int = 0

    @property
    def away(self) -> bool:
        return self.presence == "away"

    def dnd_minutes_left(self) -> int:
        """Rounded up, so 30 seconds left still reads as 1 minute rather than none."""
        if not self.dnd or not self.dnd_until:
            return 0
        return max(0, int((self.dnd_until - time.time() + 59) // 60))


# ---------------------------------------------------------------------------- token on disk
def token_path(config_dir) -> "os.PathLike":
    return config_dir / TOKEN_FILE


def read_token(config_dir) -> str:
    try:
        return token_path(config_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_token(config_dir, token: str) -> tuple[bool, str]:
    """Owner-only permissions, and removed entirely when the token is cleared."""
    path = token_path(config_dir)
    token = (token or "").strip()
    try:
        if not token:
            if path.exists():
                path.unlink()
            return True, ""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create it closed before writing, so the secret is never briefly world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token)
        return True, ""
    except OSError as e:
        return False, str(e)


def hide_token(text: str) -> str:
    """For anything that might be shown or logged."""
    text = (text or "").strip()
    return "" if not text else "%s…%s" % (text[:9], text[-4:]) if len(text) > 16 else "…"


# ---------------------------------------------------------------------------- the API
class SlackError(Exception):
    pass


class SlackClient:
    """One Slack workspace. Blocking; used from the monitor's thread and the Slack settings' Check button."""

    TIMEOUT = 10

    def __init__(self, token: str = ""):
        self.token = (token or "").strip()
        self._ctx = ssl.create_default_context()
        self.retry_after = 0.0        # set when Slack rate-limits us

    def call(self, method: str, params: dict | None = None) -> dict:
        if not self.token:
            raise SlackError("No Slack token set yet.")
        if time.time() < self.retry_after:
            raise SlackError("Slack asked us to slow down; waiting.")
        body = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}).encode()
        req = urllib.request.Request(
            API + method, data=body,
            headers={"Authorization": "Bearer " + self.token,
                     "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT, context=self._ctx) as r:
                data = json.loads(r.read(1 << 20).decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self.retry_after = time.time() + float(e.headers.get("Retry-After") or 30)
                raise SlackError("Slack is rate-limiting us; waiting %d s." % (self.retry_after - time.time()))
            raise SlackError("Slack said %s %s" % (e.code, e.reason))
        except (urllib.error.URLError, OSError) as e:
            raise SlackError("Couldn't reach Slack: %s" % getattr(e, "reason", e))
        except ValueError:
            raise SlackError("Slack sent something that isn't JSON.")
        if not isinstance(data, dict):
            raise SlackError("Slack sent something unexpected.")
        if not data.get("ok"):
            raise SlackError(FRIENDLY_ERRORS.get(data.get("error"), "Slack said: %s" % (data.get("error") or "?")))
        return data


FRIENDLY_ERRORS = {
    "invalid_auth": "That token isn't valid. Make a new one and paste it in again.",
    "token_revoked": "That token has been revoked. Make a new one.",
    "account_inactive": "That Slack account is inactive.",
    "missing_scope": "The token is missing a permission (scope) this needs. The Slack settings on the Integrations tab list them.",
    "not_allowed_token_type": "That's the wrong kind of token: this needs a user token (xoxp-...).",
    "ratelimited": "Slack is rate-limiting us.",
}

#: What the app asks for, shown in the UI so the user can tick the right boxes when making the token.
SCOPES = ["users:read", "users.profile:read", "users.profile:write", "users:write", "dnd:read", "dnd:write",
          "channels:read", "groups:read", "im:read", "chat:write"]


class SlackReader:
    """Turns a handful of API calls into one SlackState. Separate from the monitor so tests can fake it."""

    def __init__(self, client: SlackClient | None = None):
        self.client = client or SlackClient()
        self._identity = None           # auth.test rarely changes; ask once per token

    def set_token(self, token: str):
        if token != self.client.token:
            self.client.token = (token or "").strip()
            self._identity = None
            self.client.retry_after = 0.0

    def identity(self) -> dict:
        if self._identity is None:
            self._identity = self.client.call("auth.test")
        return self._identity

    def read(self, channels=(), unread_source="channels") -> SlackState:
        if not self.client.token:
            return SlackState(note="No Slack token set yet. Add one on the Integrations tab.")
        try:
            me = self.identity()
        except SlackError as e:
            return SlackState(note=str(e))
        user_id = me.get("user_id") or ""
        base = dict(user_id=user_id, user_name=me.get("user") or "", team_id=me.get("team_id") or "",
                    team_name=me.get("team") or "", connected=True)
        try:
            profile = self.client.call("users.profile.get", {"user": user_id}).get("profile") or {}
            presence = self.client.call("users.getPresence", {"user": user_id}).get("presence") or ""
            dnd = self.client.call("dnd.info", {"user": user_id})
        except SlackError as e:
            return SlackState(note=str(e), **base)
        huddle = str(profile.get("huddle_state") or "")
        snoozed = bool(dnd.get("snooze_enabled"))
        unread, mentions, note = self._unread(channels, unread_source)
        return SlackState(
            note=note, presence=presence,
            in_huddle=(huddle == "in_a_huddle"),
            huddle_channel=str(profile.get("huddle_state_channel_id") or ""),
            dnd=snoozed, dnd_until=int(dnd.get("snooze_endtime") or 0) if snoozed else 0,
            status_text=str(profile.get("status_text") or ""),
            status_emoji=str(profile.get("status_emoji") or ""),
            unread=unread, mentions=mentions, **base)

    def _unread(self, channels, source) -> tuple[int, int, str]:
        if source == "all":
            # client.counts isn't in Slack's documentation and may change shape or stop working without notice.
            try:
                data = self.client.call("client.counts")
            except SlackError as e:
                return 0, 0, "Couldn't read the unread count: %s" % e
            unread = mentions = 0
            for group in ("channels", "mpims", "ims", "threads"):
                items = data.get(group)
                if isinstance(items, dict):             # "threads" comes back as one object
                    items = [items]
                for item in items or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("has_unreads"):
                        unread += 1
                    mentions += int(item.get("mention_count_display") or item.get("mention_count") or 0)
            return unread, mentions, ""
        unread = mentions = 0
        for cid in channels:
            try:
                info = self.client.call("conversations.info", {"channel": cid}).get("channel") or {}
            except SlackError as e:
                return unread, mentions, "Couldn't read %s: %s" % (cid, e)
            n = int(info.get("unread_count_display") or 0)
            unread += 1 if n else 0
            mentions += n
        return unread, mentions, ""

    # ---- things a key does
    def apply(self, mode: str, a: dict) -> tuple[bool, str]:
        try:
            if mode == "status":
                profile = {"status_text": a.get("text") or "", "status_emoji": a.get("emoji") or ""}
                minutes = int(a.get("minutes") or 0)
                profile["status_expiration"] = int(time.time() + minutes * 60) if minutes else 0
                self.client.call("users.profile.set", {"profile": json.dumps(profile)})
            elif mode == "clear_status":
                self.client.call("users.profile.set",
                                 {"profile": json.dumps({"status_text": "", "status_emoji": "",
                                                         "status_expiration": 0})})
            elif mode == "presence":
                self.client.call("users.setPresence", {"presence": a.get("presence") or "auto"})
            elif mode == "dnd":
                self.client.call("dnd.setSnooze", {"num_minutes": max(1, int(a.get("minutes") or 30))})
            elif mode == "end_dnd":
                self.client.call("dnd.endSnooze")
            elif mode == "post":
                channel = (a.get("channel") or "").strip()
                if not channel:
                    return False, "No channel set"
                self.client.call("chat.postMessage", {"channel": channel, "text": a.get("text") or ""})
            else:
                return False, "Unknown Slack action %s" % mode
        except SlackError as e:
            return False, str(e)
        return True, ""


# ---------------------------------------------------------------------------- deep links
def deep_link(team_id: str, kind: str = "open", target: str = "") -> str:
    """slack://... so a key can jump straight into the app. Falls back to opening Slack."""
    team = urllib.parse.quote((team_id or "").strip())
    target = (target or "").strip()
    if not target or kind == "open":
        return "slack://open?team=%s" % team if team else "slack://open"
    if not team:
        # Without the workspace id only Slack's web redirect can resolve it.
        return "https://slack.com/app_redirect?channel=%s" % urllib.parse.quote(target)
    word = "user" if target[:1].upper() == "U" else "channel"
    return "slack://%s?team=%s&id=%s" % (word, team, urllib.parse.quote(target))


def channel_ids(text: str) -> list[str]:
    """'C123, C456' -> ['C123', 'C456']."""
    return [c.strip() for c in str(text or "").replace("\n", ",").split(",") if c.strip()]


# ---------------------------------------------------------------------------- monitor
class SlackMonitor(QObject):
    """Polls Slack on its own thread. `state` is always safe to read from the GUI thread."""

    changed = Signal()

    MIN_POLL = 5.0

    def __init__(self, reader: SlackReader | None = None, parent=None):
        super().__init__(parent)
        self.reader = reader if reader is not None else SlackReader()
        self.state = SlackState()
        self._poll = 10.0
        self._channels: tuple = ()
        self._unread_source = "channels"
        self._enabled = False
        self._stop = threading.Event()
        self._poke = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- GUI thread
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mk10-slack", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        self._poke.set()
        if self._thread:
            self._thread.join(timeout)

    def configure(self, token: str, channels, poll_seconds: float, unread_source: str, enabled: bool):
        self.reader.set_token(token)
        self._channels = tuple(channels or ())
        self._poll = max(self.MIN_POLL, float(poll_seconds or 10))
        self._unread_source = unread_source or "channels"
        was, self._enabled = self._enabled, bool(enabled and token)
        if not self._enabled and was:
            self.state = SlackState(note="Slack is switched off on the Integrations tab." if not enabled
                                    else "No Slack token set yet.")
            self.changed.emit()
        self._poke.set()

    def refresh_soon(self):
        self._poke.set()

    def apply(self, mode: str, a: dict):
        """Called from the action worker thread."""
        if not self._enabled:
            return False, "Slack is switched off, or has no token yet: see the Integrations tab."
        try:
            ok, msg = self.reader.apply(mode, a)
        except Exception as e:
            ok, msg = False, str(e)
        self.refresh_soon()
        return ok, msg

    # ---- background thread
    def _run(self):
        while not self._stop.is_set():
            if self._enabled:
                try:
                    state = self.reader.read(self._channels, self._unread_source)
                except Exception as e:
                    state = SlackState(note="Reading Slack failed: %s" % e)
                if state != self.state:
                    self.state = state
                    self.changed.emit()
            self._poke.wait(self._poll if self._enabled else 1.0)
            self._poke.clear()
