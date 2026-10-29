"""Placeholders in text: {time}, {date}, {app}, {var:mode}, {date:%d/%m} and so on."""

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..system.audio import INPUT, OUTPUT, AudioState
from ..system.nowplaying import Track, format_time as format_track_time
from ..system.slack import SlackState
from ..system.teams import TeamsState, short_device_name

PLACEHOLDER = re.compile(r"\{([a-zA-Z_]+)(?::([^{}]*))?\}")

#: (integration id, or None for the app's own, help line). placeholder_help() leaves out switched-off ones.
HELP_LINES = (
    (None, "{time}  current time (follows the 24-hour setting)"),
    (None, "{seconds}  seconds, 00-59"),
    (None, "{date}  short date, like Thu 22 Oct"),
    (None, "{day}  {month}  {year}"),
    (None, "{time:%H:%M:%S}  or  {date:%d/%m}  any strftime format"),
    (None, "{app}  active app      {title}  active window title"),
    (None, "{page}  current page name"),
    (None, "{var:mode}  value of the variable named mode"),
    (None, "{output}  output of this face's command"),
    (None, "{out_device}  {in_device}  the speakers and microphone in use"),
    (None, "{volume}  {mic_volume}  their volume, 0-100"),
    (None, "{level}  {mic_level}  how loud they are right now, 0-100"),
    (None, "{mute}  {mic_mute}  “muted”, or nothing when they aren't"),
    (None, "{mute:Muted|Live}  your own two words instead"),
    ("slack", "{slack_huddle}  {slack_dnd}  {slack_unread}  {slack_mentions}"),
    ("slack", "{slack_presence}  {slack_status}  {slack_team}  {slack_user}"),
    ("slack", "{slack_huddle:In a huddle|Free}  your own two words again"),
    (None, "{track}  {artist}  {album}  {player}  what's playing now"),
    (None, "{track_time}  {track_length}  how far in, and how long"),
    ("teams", "{teams_call}  “in a call”, or nothing      {teams_mic}  “muted” / “live” in a Teams call"),
    ("teams", "{teams_mic:Muted|Live}  your own two words"),
    ("teams", "{teams_camera}  {teams_sharing}  {teams_hand}  {teams_meeting}  {teams_time}  (how long the call "
              "has run, by Teams' own clock)"),
    ("teams", "{teams_shared}  what Teams' sharing bar says you're sharing (“your screen”), when it says"),
    (None, "{in_device:short}  {out_device:short}  just the part of the device name in brackets"),
)


def placeholder_help(enabled: set | None = None) -> str:
    """The placeholder list, without those of integrations that aren't in `enabled` (None: all of them)."""
    return "\n".join(line for owner, line in HELP_LINES if owner is None or enabled is None or owner in enabled)


HELP = placeholder_help()


@dataclass
class RenderContext:
    now: datetime = field(default_factory=datetime.now)
    time_24h: bool = True
    app: str = ""
    title: str = ""
    page: str = ""
    variables: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)       # command -> latest output
    font_family: str = ""
    audio: AudioState = field(default_factory=AudioState)
    slack: SlackState = field(default_factory=SlackState)
    now_playing: Track = field(default_factory=Track)
    teams: TeamsState = field(default_factory=lambda: TeamsState(available=False))


def format_time(now: datetime, h24: bool, seconds: bool = False) -> str:
    if h24:
        return now.strftime("%H:%M:%S" if seconds else "%H:%M")
    s = now.strftime("%I:%M:%S %p" if seconds else "%I:%M %p")
    return s[1:] if s.startswith("0") else s


def expand(text: str, ctx: RenderContext, output: str = "") -> str:
    if not text or "{" not in text:
        return text or ""

    def sub(m):
        name, arg = m.group(1).lower(), m.group(2)
        now = ctx.now
        try:
            if name == "time":
                return now.strftime(arg) if arg else format_time(now, ctx.time_24h)
            if name == "date":
                return now.strftime(arg) if arg else "%s %d %s" % (now.strftime("%a"), now.day, now.strftime("%b"))
        except ValueError:
            return m.group(0)
        if name == "seconds":
            return now.strftime("%S")
        if name == "day":
            return now.strftime("%A")
        if name == "month":
            return now.strftime("%B")
        if name == "year":
            return str(now.year)
        if name == "app":
            return ctx.app
        if name == "title":
            return ctx.title
        if name == "page":
            return ctx.page
        if name == "var":
            return str(ctx.variables.get((arg or "").strip(), ""))
        if name == "output":
            return output
        audio = ctx.audio or AudioState()
        side = audio.side(INPUT if name.startswith("mic_") or name.startswith("in_") else OUTPUT)
        if name in ("out_device", "in_device"):
            return short_device_name(side.name) if (arg or "").strip().lower() == "short" else side.name
        if name in ("volume", "mic_volume"):
            return "" if side.volume is None else str(side.volume)
        if name in ("level", "mic_level"):
            return str(int(round(side.level * 100))) if audio.available else ""
        if name in ("mute", "mic_mute"):
            words = [w.strip() for w in (arg or "").split("|")]
            on = words[0] if words and words[0] else "muted"
            off = words[1] if len(words) > 1 else ""
            if side.muted is None:
                return ""
            return on if side.muted else off
        if name.startswith("slack_"):
            return _slack(name, arg, ctx.slack or SlackState())
        if name in ("track", "artist", "album", "player", "track_time", "track_length", "track_status"):
            return _now_playing(name, ctx.now_playing or Track())
        if name.startswith("teams_"):
            return _teams(name, arg, ctx.teams or TeamsState(available=False))
        return m.group(0)

    return PLACEHOLDER.sub(sub, text)


TEAMS_WORDS = {"teams_mic": ("muted", "muted", "live"), "teams_camera": ("camera", "on", "off"),
               "teams_sharing": ("sharing", "sharing", "not sharing"), "teams_hand": ("hand", "raised", "down")}


def _teams(name: str, arg, t: TeamsState) -> str:
    if not t.available or not t.in_call:
        return ""
    if name == "teams_shared":
        return (t.shared or (arg or "").strip()) if t.sharing else ""
    if name == "teams_call":
        return _two_words(arg, "in a call")[0]
    if name == "teams_meeting":
        return t.meeting
    if name == "teams_time":
        return format_track_time(t.call_seconds()) if t.clock_known else ""
    if name not in TEAMS_WORDS:
        return "{%s}" % name
    field_name, yes, no = TEAMS_WORDS[name]
    value = getattr(t, field_name)
    if value is None:
        return ""
    words = [w.strip() for w in (arg or "").split("|")]
    on = words[0] if words and words[0] else yes
    off = words[1] if len(words) > 1 and words[1] else no
    return on if value else off


def _two_words(arg, when_true: str) -> tuple[str, str]:
    words = [w.strip() for w in (arg or "").split("|")]
    return (words[0] if words and words[0] else when_true), (words[1] if len(words) > 1 else "")


def _slack(name: str, arg, s: SlackState) -> str:
    if not s.connected and name not in ("slack_team", "slack_user"):
        return ""
    if name == "slack_huddle":
        on, off = _two_words(arg, "in a huddle")
        return on if s.in_huddle else off
    if name == "slack_dnd":
        on, off = _two_words(arg, "snoozed")
        return on if s.dnd else off
    if name == "slack_unread":
        return str(s.unread) if s.unread else ""
    if name == "slack_mentions":
        return str(s.mentions) if s.mentions else ""
    if name == "slack_presence":
        return s.presence or ""
    if name == "slack_status":
        return s.status_text or ""
    if name == "slack_team":
        return s.team_name or ""
    if name == "slack_user":
        return s.user_name or ""
    return ""


def _now_playing(name: str, t: Track) -> str:
    if not t.available or not t.known:
        return ""
    if name == "track":
        return t.title
    if name == "artist":
        return t.artist
    if name == "album":
        return t.album
    if name == "player":
        return t.player
    if name == "track_status":
        return t.status
    if name == "track_time":
        return format_track_time(t.position_now())
    if name == "track_length":
        return format_track_time(t.length) if t.length else ""
    return ""


def uses_seconds(text: str) -> bool:
    if not text:
        return False
    if "{seconds}" in text:
        return True
    return any(m.group(1).lower() in ("time", "date") and m.group(2) and "%S" in m.group(2)
               for m in PLACEHOLDER.finditer(text))
