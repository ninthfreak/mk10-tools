"""Context rules: 'when this is true, show that page'. Checked whenever the context changes.

The conditions themselves live in `conditions.py`, shared with the states that make a key change its look.
"""

from datetime import datetime

from .conditions import CONDITIONS, app_names, describe as describe_condition, matches, new_condition

__all__ = ["CONDITIONS", "app_names", "describe", "evaluate", "matches", "new_rule"]


def new_rule(page_id=None) -> dict:
    r = new_condition("app")
    r["enabled"] = True
    r["page"] = page_id
    return r


def evaluate(rules: list, app: str, title: str, variables: dict, now: datetime | None = None,
             audio=None, slack=None):
    """Page id of the first matching rule that points at a page, or None."""
    now = now or datetime.now()
    for r in rules:
        if r.get("page") and matches(r, app, title, variables, now, audio, slack):
            return r["page"]
    return None


def describe(rule: dict, page_name) -> str:
    return "When %s, show %s" % (describe_condition(rule),
                                 page_name(rule.get("page")) if rule.get("page") else "(no page)")
