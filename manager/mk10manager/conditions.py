"""Conditions: 'is the microphone muted?', 'is Firefox active?'. One evaluator, used in two places.

Context rules (`rules.py`) use them to switch pages. Keys use them to change how they look: a key can carry a
list of *states*, and the first one whose condition is true paints over the key's normal label, colours and icon.
That's what makes a mute key show whether sound is actually muted.

A condition is a plain dict: {"when": kind, plus the fields that kind needs}. Rules and key states share the
shape, so they share this code and the editor widget in `ui/condition_row.py`.
"""

from datetime import datetime

from .schema import Field
from .system.audio import INPUT, OUTPUT, AudioState

CONDITIONS = [
    ("app", "App is"),
    ("title", "Window title contains"),
    ("variable", "Variable is"),
    ("time", "Time is between"),
    ("output_muted", "Speakers are"),
    ("input_muted", "Microphone is"),
]

AUDIO_CONDITIONS = ("output_muted", "input_muted")
MUTE_CHOICES = [("yes", "muted"), ("no", "not muted")]
CONDITION_DEFAULTS = {"when": "app", "value": "", "name": "mode", "from": "09:00", "to": "17:00"}


def new_condition(when: str = "app") -> dict:
    c = dict(CONDITION_DEFAULTS)
    c["when"] = when
    if when in AUDIO_CONDITIONS:
        c["value"] = "yes"
    return c


def _minutes(hhmm):
    try:
        h, m = str(hhmm).strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def app_names(value: str) -> list[str]:
    """'Firefox.exe, code' -> ['firefox', 'code'] (the same clean-up as the active-app detection)."""
    out = []
    for v in str(value or "").split(","):
        v = v.strip().lower()
        if v.endswith(".exe"):
            v = v[:-4]
        if v:
            out.append(v)
    return out


def wants_muted(value) -> bool:
    return str(value if value is not None else "yes").strip().lower() not in ("no", "false", "0", "not muted")


def matches(cond: dict, app: str = "", title: str = "", variables: dict | None = None,
            now: datetime | None = None, audio: AudioState | None = None) -> bool:
    """Is this condition true right now? Unknown kinds, and sound we can't read, are never true."""
    if not cond.get("enabled", True):
        return False
    variables = variables or {}
    when = cond.get("when")
    value = str(cond.get("value") or "").strip()
    if when == "app":
        return bool(app) and app.lower() in app_names(value)
    if when == "title":
        return bool(value) and value.lower() in (title or "").lower()
    if when == "variable":
        name = str(cond.get("name") or "").strip()
        return bool(name) and str(variables.get(name, "")).strip().lower() == value.lower()
    if when == "time":
        a, b = _minutes(cond.get("from")), _minutes(cond.get("to"))
        if a is None or b is None:
            return False
        t = now or datetime.now()
        mins = t.hour * 60 + t.minute
        return a <= mins < b if a <= b else (mins >= a or mins < b)       # overnight ranges work too
    if when in AUDIO_CONDITIONS:
        if audio is None or not audio.available:
            return False
        muted = audio.side(INPUT if when == "input_muted" else OUTPUT).muted
        return muted is not None and muted == wants_muted(cond.get("value"))
    return False


def matches_in(cond: dict, ctx) -> bool:
    """Same, for anything that carries a RenderContext (keys while they're being drawn)."""
    return matches(cond, ctx.app, ctx.title, ctx.variables, ctx.now, getattr(ctx, "audio", None))


def describe(cond: dict) -> str:
    when = cond.get("when")
    if when == "app":
        return "%s is active" % (cond.get("value") or "?")
    if when == "title":
        return "the window title contains “%s”" % (cond.get("value") or "")
    if when == "variable":
        return "%s is %s" % (cond.get("name") or "?", cond.get("value") or "(empty)")
    if when == "time":
        return "it's between %s and %s" % (cond.get("from"), cond.get("to"))
    if when in AUDIO_CONDITIONS:
        what = "the microphone" if when == "input_muted" else "the speakers"
        return "%s %s %s" % (what, "is" if when == "input_muted" else "are",
                             dict(MUTE_CHOICES).get(cond.get("value") or "yes", "muted"))
    return "?"


# ---------------------------------------------------------------------------- key states
ICON_MODES = [("keep", "Keep the key's own icon"), ("choose", "Use another icon"), ("none", "No icon")]

#: What a state is allowed to change about the key. The editor builds this form itself.
STATE_LOOK_FIELDS = [
    Field("use_label", "Change the label", "bool", False),
    Field("label", "Label", "template", "", show_if=("use_label", (True,))),
    Field("use_bg", "Change the background", "bool", True),
    Field("bg", "Background", "color", "#854F0B", show_if=("use_bg", (True,))),
    Field("use_text", "Change the text colour", "bool", False),
    Field("text_color", "Text colour", "color", "#FFFFFF", show_if=("use_text", (True,))),
    Field("icon_mode", "Icon", "choice", "choose", ICON_MODES),
    Field("icon", "Which icon", "icon", "volume-off", show_if=("icon_mode", ("choose",))),
    Field("icon_color", "Icon colour", "color", "#FFFFFF", show_if=("icon_mode", ("choose",))),
]


def new_state(when: str = "output_muted") -> dict:
    s = new_condition(when)
    s["enabled"] = True
    for f in STATE_LOOK_FIELDS:
        s[f.key] = f.default
    if when == "input_muted":
        s["icon"] = "microphone-off"
    return s


def describe_state(state: dict) -> str:
    changes = []
    if state.get("use_label"):
        changes.append("label “%s”" % (state.get("label") or ""))
    if state.get("use_bg"):
        changes.append("background")
    if state.get("use_text"):
        changes.append("text colour")
    mode = state.get("icon_mode") or "keep"
    if mode == "choose":
        changes.append("the %s icon" % (state.get("icon") or "?"))
    elif mode == "none":
        changes.append("no icon")
    what = ", ".join(changes) if changes else "nothing yet"
    off = "" if state.get("enabled", True) else "(off)  "
    return "%sWhen %s: %s" % (off, describe(state), what)


def resolve_key(key: dict | None, ctx) -> dict | None:
    """The key as it should look right now: the first matching state painted over the key's own look."""
    if not key or not key.get("states"):
        return key
    for s in key["states"]:
        if not isinstance(s, dict) or not s.get("enabled", True) or not matches_in(s, ctx):
            continue
        out = dict(key)
        if s.get("use_label"):
            out["label"] = s.get("label") or ""
        if s.get("use_bg") and s.get("bg"):
            out["bg"] = s["bg"]
        if s.get("use_text") and s.get("text_color"):
            out["text_color"] = s["text_color"]
        mode = s.get("icon_mode") or "keep"
        if mode == "none":
            out["icon"] = None
        elif mode == "choose" and s.get("icon"):
            out["icon"] = {"kind": "builtin", "name": s["icon"], "color": s.get("icon_color") or "#FFFFFF"}
        return out
    return key
