"""Placeholders in text: {time}, {date}, {app}, {var:mode}, {date:%d/%m} and so on."""

import re
from dataclasses import dataclass, field
from datetime import datetime

from ..system.audio import INPUT, OUTPUT, AudioState

PLACEHOLDER = re.compile(r"\{([a-zA-Z_]+)(?::([^{}]*))?\}")

HELP = (
    "{time}  current time (follows the 24-hour setting)\n"
    "{seconds}  seconds, 00-59\n"
    "{date}  short date, like Thu 22 Oct\n"
    "{day}  {month}  {year}\n"
    "{time:%H:%M:%S}  or  {date:%d/%m}  any strftime format\n"
    "{app}  active app      {title}  active window title\n"
    "{page}  current page name\n"
    "{var:mode}  value of the variable named mode\n"
    "{output}  output of this face's command\n"
    "{out_device}  {in_device}  the speakers and microphone in use\n"
    "{volume}  {mic_volume}  their volume, 0-100\n"
    "{level}  {mic_level}  how loud they are right now, 0-100\n"
    "{mute}  {mic_mute}  “muted”, or nothing when they aren't\n"
    "{mute:Muted|Live}  your own two words instead"
)


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
            return side.name
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
        return m.group(0)

    return PLACEHOLDER.sub(sub, text)


def uses_seconds(text: str) -> bool:
    if not text:
        return False
    if "{seconds}" in text:
        return True
    return any(m.group(1).lower() in ("time", "date") and m.group(2) and "%S" in m.group(2)
               for m in PLACEHOLDER.finditer(text))
