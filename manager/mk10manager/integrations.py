"""Optional integrations with specific apps (Slack, Teams), each switched on and off on the Integrations tab.

What the operating system itself provides (sound, what's playing, the active window) is the app's own, not an
integration: it's always there.

One that's off adds nothing to the window: its key steps, conditions, small-screen faces, page templates and
placeholders are hidden, its settings fold away, and its monitor stops reading. Keys, rules and screens that
already use it are kept exactly as they are; they do nothing until it's switched back on, and the editors mark
them "off".

To add an integration: give it an entry in INTEGRATIONS naming the parts it adds (action types, condition
ids, face ids, template ids, placeholders), give its monitor a configure(enabled, ...) for the controller to
call from apply_settings(), and give it a settings widget in ui/integrations_panel.py if it needs one.
"""

import re
from dataclasses import dataclass

KINDS = ("actions", "conditions", "faces", "templates")


@dataclass(frozen=True)
class Integration:
    id: str
    name: str
    description: str
    actions: tuple = ()           # keys of actions.ACTION_TYPES
    conditions: tuple = ()        # ids in conditions.CONDITIONS
    faces: tuple = ()             # face ids (render/faces.py)
    templates: tuple = ()         # template ids (templates.py)
    placeholders: tuple = ()      # placeholder names; one ending in "_" covers every name starting with it

    def owns_placeholder(self, name: str) -> bool:
        return any(name.startswith(p) if p.endswith("_") else name == p for p in self.placeholders)


INTEGRATIONS = (
    Integration("slack", "Slack",
                "Your Slack status, away, snooze, huddles and unread counts, through Slack's own API. Needs a "
                "token from you.",
                actions=("slack",),
                conditions=("slack_huddle", "slack_dnd", "slack_presence", "slack_unread"),
                faces=("slack",), templates=("slack",), placeholders=("slack_",)),
    Integration("teams", "Microsoft Teams",
                "In a call, muted, camera and sharing, read from what the Teams window shows; mute, camera and "
                "sharing keys. Windows only for now.",
                actions=("teams",),
                conditions=("teams_call", "teams_muted", "teams_camera", "teams_sharing", "teams_hand"),
                faces=("teams",), templates=("teams",), placeholders=("teams_",)),
)
BY_ID = {i.id: i for i in INTEGRATIONS}
#: The per-integration switches these replaced (they lived on the Slack and Device and app tabs).
OLD_SWITCHES = ("slack_enabled", "teams_enabled")
_PLACEHOLDER = re.compile(r"\{([a-zA-Z_]+)(?::[^{}]*)?\}")


def is_on(settings: dict, integration_id: str) -> bool:
    return bool((settings.get("integrations") or {}).get(integration_id, False))


def enabled(settings: dict) -> set:
    return {i.id for i in INTEGRATIONS if is_on(settings, i.id)}


def owner(kind: str, item_id) -> Integration | None:
    """The integration an action type, condition, face or template belongs to; None for the app's own."""
    for i in INTEGRATIONS:
        if item_id in getattr(i, kind):
            return i
    return None


def hidden(settings: dict, kind: str) -> set:
    """The action types, conditions, faces or templates of every integration that's switched off."""
    return {item for i in INTEGRATIONS if not is_on(settings, i.id) for item in getattr(i, kind)}


def off_note(settings: dict, kind: str, item_id) -> str:
    """' (Slack is off)' for a part of a switched-off integration, '' otherwise: for the editors' lists."""
    i = owner(kind, item_id)
    return "  (%s is off)" % i.name if i is not None and not is_on(settings, i.id) else ""


def _mentions(text, integration: Integration) -> bool:
    return isinstance(text, str) and any(integration.owns_placeholder(m.group(1))
                                         for m in _PLACEHOLDER.finditer(text))


def usage(data: dict, integration: Integration) -> dict:
    """How many keys, small screens and context rules use an integration, across every page."""
    keys = screens = 0
    for page in data.get("pages") or []:
        for key in page.get("keys") or []:
            if not key:
                continue
            states = key.get("states") or []
            if (any(a.get("type") in integration.actions for a in key.get("actions") or [])
                    or any(st.get("when") in integration.conditions for st in states)
                    or _mentions(key.get("label"), integration)
                    or any(_mentions(st.get("label"), integration) for st in states)):
                keys += 1
        screen = page.get("screen") or {}
        if screen.get("face") in integration.faces or any(
                _mentions(v, integration) for v in (screen.get("options") or {}).values()):
            screens += 1
    rules = sum(1 for r in data.get("rules") or [] if r.get("when") in integration.conditions)
    return {"keys": keys, "screens": screens, "rules": rules}


def describe_usage(counts: dict) -> str:
    parts = []
    for n, one, many in ((counts["keys"], "key", "keys"), (counts["screens"], "small screen", "small screens"),
                         (counts["rules"], "context rule", "context rules")):
        if n:
            parts.append("%d %s" % (n, one if n == 1 else many))
    if not parts:
        return "Nothing on your pages uses it."
    return "Used by %s." % (" and ".join(parts) if len(parts) < 3 else "%s, %s and %s" % tuple(parts))


def initial_switches(data: dict) -> dict:
    """For settings saved before integrations could be switched off: on if a page already uses it (or, for
    Slack, if it was switched on). A brand-new setup uses none, so all start off."""
    old = data.get("settings") or {}
    return {i.id: bool(any(usage(data, i).values()) or (i.id == "slack" and old.get("slack_enabled")))
            for i in INTEGRATIONS}
