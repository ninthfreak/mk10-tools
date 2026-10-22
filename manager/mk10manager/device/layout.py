"""Where the keys and screens sit on the MK10's single canvas, as reported by getInfo."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int
    row: int | None = None
    col: int | None = None
    is_key: bool = True

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


@dataclass
class DeviceLayout:
    width: int
    height: int
    rows: int
    cols: int
    keys: list[Rect] = field(default_factory=list)      # index = row * cols + col
    screens: list[Rect] = field(default_factory=list)   # non-key display areas
    model: str = "MK10"
    version: str = ""

    @property
    def key_count(self) -> int:
        return len(self.keys)

    def position(self, row: int, col: int) -> int | None:
        if 0 <= row < self.rows and 0 <= col < self.cols:
            pos = row * self.cols + col
            return pos if pos < len(self.keys) else None
        return None

    def hit(self, px: float, py: float):
        """('key', position) or ('screen', index) or None."""
        for i, r in enumerate(self.keys):
            if r.contains(px, py):
                return ("key", i)
        for i, r in enumerate(self.screens):
            if r.contains(px, py):
                return ("screen", i)
        return None

    @classmethod
    def default(cls) -> "DeviceLayout":
        keys = [Rect(c * 128, r * 128, 128, 128, r, c, True) for r in range(2) for c in range(5)]
        return cls(896, 304, 2, 5, keys, [Rect(656, 0, 240, 296, None, None, False)], "MK10", "")

    @classmethod
    def from_info(cls, info: dict) -> "DeviceLayout":
        """Build from a getInfo result. Falls back to the known MK10 layout for anything missing."""
        d = cls.default()
        if not isinstance(info, dict):
            return d
        res = info.get("result", info)
        try:
            width, height = int(res.get("deviceWidth", d.width)), int(res.get("deviceHeight", d.height))
        except (TypeError, ValueError):
            width, height = d.width, d.height
        panel = res.get("devicePanel") or {}
        rects = panel.get("rects") if isinstance(panel, dict) else None
        model = str(res.get("deviceModel") or d.model)
        version = str(res.get("deviceVersion") or "")
        if not isinstance(rects, list) or not rects:
            d.width, d.height, d.model, d.version = width, height, model, version
            return d
        keys, screens = [], []
        for r in rects:
            try:
                rect = Rect(int(r["x"]), int(r["y"]), int(r["width"]), int(r["height"]),
                            int(r["row"]) if r.get("row") is not None else None,
                            int(r["col"]) if r.get("col") is not None else None,
                            bool(r.get("isKey", True)))
            except (KeyError, TypeError, ValueError):
                continue
            (keys if rect.is_key and rect.row is not None and rect.col is not None else screens).append(rect)
        if not keys:
            d.width, d.height, d.model, d.version = width, height, model, version
            return d
        rows = int(panel.get("rectRows") or (max(k.row for k in keys) + 1))
        cols = int(panel.get("rectCols") or (max(k.col for k in keys) + 1))
        keys.sort(key=lambda k: (k.row, k.col))
        return cls(width, height, rows, cols, keys, screens, model, version)
