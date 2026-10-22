"""Ready-made pages. "New page" offers these instead of starting from nothing.

A template builds a whole page — keys, their actions, the states that make them react, and the small screen —
so a page for an app works the moment you add it. Some need a detail or two first (a Slack token, a channel id);
each one says so in `setup`, and the New page window shows that before you commit to it.

To add a template: write a function that returns a Page, wrap it in a Template, and put it in TEMPLATES.
"""

from dataclasses import dataclass, field

from .conditions import new_state
from .config import KEY_COUNT, new_key
from .system.audio import INPUT, OUTPUT

BLUE, ORANGE, DARK = "#185FA5", "#854F0B", "#2C2C2A"
LIGHT_BLUE, LIGHT_ORANGE = "#378ADD", "#EF9F27"


@dataclass
class Page:
    keys: list
    screen: dict
    rule: dict | None = None          # an optional context rule, offered as a tick box
    rule_text: str = ""               # how that rule is described in the New page window
    variables: dict = field(default_factory=dict)


@dataclass
class Template:
    id: str
    name: str
    description: str
    build: object                     # () -> Page
    setup: tuple = ()                 # what the user still has to do, shown before they pick it


def _key(label, icon, bg=BLUE, actions=None, icon_color="#FFFFFF", **extra):
    k = new_key(label, bg)
    if icon:
        k["icon"] = {"kind": "builtin", "name": icon, "color": icon_color}
    k["actions"] = actions or []
    k.update(extra)
    return k


def _mute_key(target, label, muted_label, icon, muted_icon, meter_color):
    """A mute key that shows the real state and how loud that side is right now."""
    k = _key(label, icon, DARK, [{"type": "audio", "target": target, "mode": "toggle_mute"}])
    state = new_state("input_muted" if target == INPUT else "output_muted")
    state.update({"use_bg": True, "bg": ORANGE, "use_label": True, "label": muted_label,
                  "icon_mode": "choose", "icon": muted_icon, "icon_color": "#FFFFFF"})
    k["states"] = [state]
    k["meter"] = {"source": "input_level" if target == INPUT else "output_level",
                  "style": "bar_bottom", "color": meter_color, "strength": 80}
    return k


def _pad(keys) -> list:
    keys = list(keys)
    return keys + [None] * (KEY_COUNT - len(keys))


# ---------------------------------------------------------------------------- the templates
def blank_page() -> Page:
    return Page(_pad([]), {"face": "analog", "options": {}})


def sound_page() -> Page:
    """Everything about the speakers and the microphone, on one page."""
    keys = [
        _mute_key(OUTPUT, "Mute", "Muted", "volume", "volume-off", LIGHT_BLUE),
        _key("Vol -", "volume-2", DARK, [{"type": "audio", "target": OUTPUT, "mode": "volume_down",
                                          "value": 5}],
             meter={"source": "output_volume", "style": "edge", "color": LIGHT_BLUE, "strength": 90}),
        _key("Vol +", "volume", DARK, [{"type": "audio", "target": OUTPUT, "mode": "volume_up", "value": 5}],
             meter={"source": "output_volume", "style": "edge", "color": LIGHT_BLUE, "strength": 90}),
        _key("Play", "player-play", ORANGE, [{"type": "media", "key": "play_pause"}]),
        _key("Next", "player-skip-forward", DARK, [{"type": "media", "key": "next"}]),
        _mute_key(INPUT, "Mic", "Mic off", "microphone", "microphone-off", LIGHT_ORANGE),
        _key("Mic -", "microphone-2", DARK, [{"type": "audio", "target": INPUT, "mode": "volume_down",
                                              "value": 5}],
             meter={"source": "input_volume", "style": "edge", "color": LIGHT_ORANGE, "strength": 90}),
        _key("Mic +", "microphone-2", DARK, [{"type": "audio", "target": INPUT, "mode": "volume_up",
                                              "value": 5}],
             meter={"source": "input_volume", "style": "edge", "color": LIGHT_ORANGE, "strength": 90}),
        _key("Previous", "player-skip-back", DARK, [{"type": "media", "key": "previous"}]),
        _key("Back", "arrow-back-up", DARK, [{"type": "page", "mode": "back"}]),
    ]
    return Page(_pad(keys), {"face": "audio", "options": {}})


def slack_page() -> Page:
    """Slack, as far as its API allows, plus the microphone keys that huddles actually need."""
    huddle = _key("Huddle", "headphones", DARK, [{"type": "slack", "mode": "open", "channel": ""}])
    huddle_on = new_state("slack_huddle")
    huddle_on.update({"value": "yes", "use_bg": True, "bg": ORANGE, "use_label": True, "label": "In huddle",
                      "icon_mode": "choose", "icon": "headphones", "icon_color": "#FFFFFF"})
    huddle["states"] = [huddle_on]

    dnd = _key("Snooze", "bell", DARK, [{"type": "slack", "mode": "dnd", "snooze_minutes": 30}])
    dnd_on = new_state("slack_dnd")
    dnd_on.update({"value": "yes", "use_bg": True, "bg": ORANGE, "use_label": True, "label": "Snoozed",
                   "icon_mode": "choose", "icon": "bell-off", "icon_color": "#FFFFFF"})
    dnd["states"] = [dnd_on]

    away = _key("Away", "user", DARK, [{"type": "slack", "mode": "presence", "presence": "away"}])
    away_on = new_state("slack_presence")
    away_on.update({"value": "away", "use_bg": True, "bg": ORANGE, "use_label": True, "label": "Away",
                    "icon_mode": "choose", "icon": "user-off", "icon_color": "#FFFFFF"})
    away["states"] = [away_on]

    unread = _key("Slack", "brand-slack", DARK, [{"type": "slack", "mode": "open", "channel": ""}])
    unread_some = new_state("slack_unread")
    unread_some.update({"value": "yes", "use_bg": True, "bg": BLUE, "use_label": True,
                        "label": "{slack_unread} new", "icon_mode": "keep"})
    unread["states"] = [unread_some]

    keys = [
        _mute_key(INPUT, "Mic", "Mic off", "microphone", "microphone-off", LIGHT_ORANGE),
        huddle,
        dnd,
        away,
        unread,
        _mute_key(OUTPUT, "Mute", "Muted", "volume", "volume-off", LIGHT_BLUE),
        _key("Meeting", "calendar", DARK,
             [{"type": "slack", "mode": "status", "status_text": "In a meeting",
               "status_emoji": ":calendar:", "minutes": 60}]),
        _key("Focus", "coffee", DARK,
             [{"type": "slack", "mode": "status", "status_text": "Heads down",
               "status_emoji": ":coffee:", "minutes": 120},
              {"type": "slack", "mode": "dnd", "snooze_minutes": 60}]),
        _key("Clear", "circle-x", DARK, [{"type": "slack", "mode": "clear_status"},
                                         {"type": "slack", "mode": "end_dnd"},
                                         {"type": "slack", "mode": "presence", "presence": "auto"}]),
        _key("Back", "arrow-back-up", DARK, [{"type": "page", "mode": "back"}]),
    ]
    rule = {"enabled": True, "when": "slack_huddle", "value": "yes", "page": None}
    return Page(_pad(keys), {"face": "slack", "options": {}}, rule,
                "Show this page whenever I'm in a Slack huddle")


TEMPLATES = [
    Template("blank", "Blank page", "Ten empty keys and an analogue clock. Start from nothing.", blank_page),
    Template("sound", "Sound and microphone",
             "Mute, volume and media keys for both the speakers and the microphone. The keys show what's "
             "really muted and how loud each side is. The small screen names both devices.", sound_page),
    Template("slack", "Slack",
             "Microphone and speaker mute for huddles, a huddle indicator, snooze, away, two status keys and "
             "an unread count. The small screen shows your Slack state next to the real microphone.",
             slack_page,
             setup=("Add a Slack token on the Slack tab, or the Slack keys won't do anything.",
                    "Slack can't tell us whether you're muted inside a huddle, and can't start or join one. "
                    "The microphone key mutes the microphone itself, which works everywhere.")),
]


def get(template_id: str) -> Template | None:
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    return None


def build(template_id: str) -> Page:
    t = get(template_id)
    return t.build() if t else blank_page()
