"""Everything the user sets up, stored as one JSON file they never need to open."""

import copy
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

APP_NAME = "MK10 Manager"
CONFIG_VERSION = 1
KEY_COUNT = 10

DEFAULT_SETTINGS = {
    "brightness": 80,
    "press_feedback": True,
    "port": "auto",
    "start_minimized": False,
    "time_24h": True,
    "font_family": "",
    "default_page": None,          # page id; None = first page
    "fallback_to_default": True,   # when no rule matches after a context change, go to the default page
    "tray_hint_shown": False,
}


def config_dir() -> Path:
    env = os.environ.get("MK10_CONFIG_DIR")
    if env:
        return Path(env)
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mk10-manager"


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def store_image(src: str) -> str:
    """Copy a picture into the settings folder so settings keep working if the original moves."""
    import hashlib
    try:
        data = Path(src).read_bytes()
    except OSError:
        return src
    folder = config_dir() / "images"
    if Path(src).resolve().parent == folder.resolve():
        return src
    folder.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(data).hexdigest()[:12] + Path(src).suffix.lower()
    dest = folder / name
    if not dest.exists():
        dest.write_bytes(data)
    return str(dest)


def new_key(label: str = "", bg: str = "#2C2C2A") -> dict:
    return {
        "label": label,
        "label_pos": "bottom",
        "font_size": 20,
        "text_color": "#FFFFFF",
        "bold": False,
        "bg": bg,
        "icon": None,          # {"kind": "builtin", "name": ..., "color": ...} or {"kind": "file", "path": ...}
        "icon_size": 62,       # percent of the key
        "actions": [],
    }


def default_screen() -> dict:
    return {"face": "analog", "options": {}}


def _icon(name, color="#FFFFFF"):
    return {"kind": "builtin", "name": name, "color": color}


def default_config() -> dict:
    blue, orange = "#185FA5", "#854F0B"
    keys = [None] * KEY_COUNT
    samples = [
        ("Mute", "volume-off", blue, [{"type": "media", "key": "mute"}]),
        ("Play", "player-play", orange, [{"type": "media", "key": "play_pause"}]),
        ("Next", "player-skip-forward", blue, [{"type": "media", "key": "next"}]),
        ("Vol -", "volume-2", orange, [{"type": "media", "key": "volume_down"}]),
        ("Vol +", "volume", blue, [{"type": "media", "key": "volume_up"}]),
    ]
    for i, (label, icon, bg, actions) in enumerate(samples):
        k = new_key(label, bg)
        k["icon"] = _icon(icon)
        k["actions"] = actions
        keys[i] = k
    page = {"id": new_id(), "name": "Main", "keys": keys, "screen": default_screen()}
    return {
        "version": CONFIG_VERSION,
        "settings": dict(DEFAULT_SETTINGS),
        "pages": [page],
        "rules": [],
        "variables": {},
        "seen_apps": [],
    }


class Config:
    def __init__(self, data: dict | None = None, path: Path | None = None):
        self.data = data if data is not None else default_config()
        self.path = path
        self.load_problem = ""          # shown to the user once, if loading went wrong
        self.save_blocked = False       # True if the file exists but couldn't be read: never overwrite it
        self._backed_up = False
        self._normalise()

    # ---- load / save
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or (config_dir() / "config.json")
        if not path.exists():
            return cls(None, path)
        try:
            raw = path.read_bytes()
        except OSError as e:
            c = cls(None, path)
            c.save_blocked = True
            c.load_problem = ("Couldn't read your settings file (%s). Using a blank setup for now and NOT saving, so "
                              "the file stays safe. Restart MK10 Manager to try again." % e)
            return c
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            if isinstance(data, dict) and isinstance(data.get("pages"), list) and data["pages"]:
                return cls(data, path)
            reason = "it doesn't contain any pages"
        except (UnicodeDecodeError, ValueError) as e:
            reason = "it isn't valid JSON (%s)" % e
        keep = path.with_name("config.unreadable-%s.json" % time.strftime("%Y%m%d-%H%M%S"))
        try:
            keep.write_bytes(raw)
            where = "A copy was kept as %s." % keep.name
        except OSError:
            where = "Keeping a copy failed too."
        c = cls(None, path)
        c.load_problem = "Your settings file couldn't be used because %s. Started with a fresh setup. %s" % (
            reason, where)
        return c

    def save(self):
        if not self.path or self.save_blocked:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        if self.path.exists() and not self._backed_up:
            # One backup per run: the settings as they were when the app started.
            try:
                shutil.copy2(self.path, self.path.with_suffix(".bak"))
                self._backed_up = True
            except OSError:
                pass
        os.replace(tmp, self.path)

    def snapshot(self) -> str:
        return json.dumps(self.data, sort_keys=True)

    def restore(self, snap: str):
        self.data = json.loads(snap)
        self._normalise()

    def _normalise(self):
        d = self.data
        d.setdefault("version", CONFIG_VERSION)
        s = d.setdefault("settings", {})
        for k, v in DEFAULT_SETTINGS.items():
            s.setdefault(k, v)
        d.setdefault("rules", [])
        d.setdefault("variables", {})
        d.setdefault("seen_apps", [])
        if not d.get("pages"):
            d["pages"] = default_config()["pages"]
        for p in d["pages"]:
            p.setdefault("id", new_id())
            p.setdefault("name", "Page")
            p.setdefault("screen", default_screen())
            keys = p.setdefault("keys", [])
            keys.extend([None] * (KEY_COUNT - len(keys)))
            for k in keys:
                if k:
                    for kk, vv in new_key().items():
                        k.setdefault(kk, copy.deepcopy(vv))

    # ---- accessors
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    @property
    def pages(self) -> list[dict]:
        return self.data["pages"]

    @property
    def rules(self) -> list[dict]:
        return self.data["rules"]

    @property
    def variables(self) -> dict:
        return self.data["variables"]

    def page(self, page_id) -> dict | None:
        for p in self.pages:
            if p["id"] == page_id:
                return p
        return None

    def page_by_name(self, name: str) -> dict | None:
        for p in self.pages:
            if p["name"].strip().lower() == str(name).strip().lower():
                return p
        return None

    def page_index(self, page_id) -> int:
        for i, p in enumerate(self.pages):
            if p["id"] == page_id:
                return i
        return -1

    def default_page(self) -> dict:
        return self.page(self.settings.get("default_page")) or self.pages[0]

    def unique_page_name(self, base: str) -> str:
        names = {p["name"].lower() for p in self.pages}
        if base.lower() not in names:
            return base
        n = 2
        while "%s %d" % (base, n) in names or ("%s %d" % (base, n)).lower() in names:
            n += 1
        return "%s %d" % (base, n)

    def add_page(self, name: str = "Page", copy_from: dict | None = None, empty_keys: bool = True) -> dict:
        if copy_from:
            p = copy.deepcopy(copy_from)
            if empty_keys:
                p["keys"] = [None] * KEY_COUNT
        else:
            p = {"keys": [None] * KEY_COUNT, "screen": default_screen()}
        p["id"] = new_id()
        p["name"] = self.unique_page_name(name)
        self.pages.append(p)
        return p

    def remove_page(self, page_id) -> bool:
        if len(self.pages) <= 1:
            return False
        i = self.page_index(page_id)
        if i < 0:
            return False
        del self.pages[i]
        self.data["rules"] = [r for r in self.rules if r.get("page") != page_id]
        if self.settings.get("default_page") == page_id:
            self.settings["default_page"] = None
        return True

    def note_app(self, app: str, limit: int = 60):
        if not app:
            return False
        seen = self.data["seen_apps"]
        if app in seen:
            return False
        seen.append(app)
        del seen[:-limit]
        return True
