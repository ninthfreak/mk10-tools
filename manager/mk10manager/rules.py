"""Context rules: 'when this is true, show that page'. Checked whenever the context changes."""

from datetime import datetime

CONDITIONS = [
    ("app", "App is"),
    ("title", "Window title contains"),
    ("variable", "Variable is"),
    ("time", "Time is between"),
]


def app_names(value: str) -> list[str]:
    """'Firefox.exe, code' -> ['firefox', 'code'] (same clean-up as the active-app detection)."""
    out = []
    for v in str(value or "").split(","):
        v = v.strip().lower()
        if v.endswith(".exe"):
            v = v[:-4]
        if v:
            out.append(v)
    return out


def new_rule(page_id=None) -> dict:
    return {"enabled": True, "when": "app", "value": "", "name": "mode", "from": "09:00", "to": "17:00",
            "page": page_id}


def _minutes(hhmm: str):
    try:
        h, m = str(hhmm).strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def matches(rule: dict, app: str, title: str, variables: dict, now: datetime) -> bool:
    if not rule.get("enabled", True):
        return False
    when = rule.get("when")
    value = str(rule.get("value") or "").strip()
    if when == "app":
        return bool(app) and app.lower() in app_names(value)
    if when == "title":
        return bool(value) and value.lower() in (title or "").lower()
    if when == "variable":
        name = str(rule.get("name") or "").strip()
        return bool(name) and str(variables.get(name, "")).strip().lower() == value.lower()
    if when == "time":
        a, b = _minutes(rule.get("from")), _minutes(rule.get("to"))
        if a is None or b is None:
            return False
        t = now.hour * 60 + now.minute
        return a <= t < b if a <= b else (t >= a or t < b)    # overnight ranges work too
    return False


def evaluate(rules: list, app: str, title: str, variables: dict, now: datetime | None = None):
    """Page id of the first matching rule that points at a page, or None."""
    now = now or datetime.now()
    for r in rules:
        if r.get("page") and matches(r, app, title, variables, now):
            return r["page"]
    return None


def describe(rule: dict, page_name) -> str:
    when = rule.get("when")
    if when == "app":
        cond = "%s is active" % (rule.get("value") or "?")
    elif when == "title":
        cond = "the window title contains “%s”" % (rule.get("value") or "")
    elif when == "variable":
        cond = "%s is %s" % (rule.get("name") or "?", rule.get("value") or "(empty)")
    else:
        cond = "it's between %s and %s" % (rule.get("from"), rule.get("to"))
    return "When %s, show %s" % (cond, page_name(rule.get("page")) if rule.get("page") else "(no page)")
