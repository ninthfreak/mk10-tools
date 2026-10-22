"""Level meters: the bar behind a key, and the bars on the small screen.

A key's meter reads one number (the live loudness of the speakers or the microphone, a volume, or a variable)
and paints it behind the icon and label, the way a video call shows who's talking.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ..schema import Field, fill_defaults
from ..system.audio import INPUT, OUTPUT

BLUE, TRACK = "#378ADD", "#1A1A19"

SOURCES = [
    ("none", "Nothing"),
    ("output_level", "Live sound coming out"),
    ("input_level", "Live sound going in (microphone)"),
    ("output_volume", "Speaker volume"),
    ("input_volume", "Microphone volume"),
    ("variable", "A variable, 0 to 100"),
]
LIVE_SOURCES = {"output_level": OUTPUT, "input_level": INPUT}
ACTIVE = tuple(s for s, _ in SOURCES if s != "none")

STYLES = [
    ("bar_bottom", "Fills up from the bottom"),
    ("bar_left", "Fills across from the left"),
    ("edge", "Thin bar along the bottom"),
    ("ring", "Ring around the edge"),
]

METER_FIELDS = [
    Field("source", "Show", "choice", "none", SOURCES,
          help="Draws behind the icon and label, so the key itself still reads normally."),
    Field("name", "Variable", "variable", "level", show_if=("source", ("variable",))),
    Field("style", "Style", "choice", "bar_bottom", STYLES, show_if=("source", ACTIVE)),
    Field("color", "Meter colour", "color", BLUE, show_if=("source", ACTIVE)),
    Field("show_track", "Dim the rest of the key", "bool", False, show_if=("source", ACTIVE),
          help="Off keeps the key's own background where the meter hasn't reached."),
    Field("track", "Dim it to", "color", TRACK, show_if=("show_track", (True,))),
    Field("strength", "How strong (%)", "int", 55, minimum=10, maximum=100, show_if=("source", ACTIVE),
          help="Lower keeps the key's own colour showing through."),
]


def new_meter() -> dict:
    return {"source": "none"}


def values(meter: dict | None) -> dict:
    return fill_defaults(meter or {}, METER_FIELDS)


def active(meter: dict | None) -> bool:
    return bool(meter) and (meter.get("source") or "none") != "none"


def live_target(meter: dict | None) -> str | None:
    """OUTPUT / INPUT if this meter needs the live loudness measured, else None."""
    if not active(meter):
        return None
    return LIVE_SOURCES.get(meter.get("source"))


def _number(text) -> float | None:
    try:
        return float(str(text).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def meter_value(meter: dict | None, ctx) -> float | None:
    """0.0 - 1.0, or None when there's nothing to show."""
    if not active(meter):
        return None
    v = values(meter)
    source = v["source"]
    audio = getattr(ctx, "audio", None)
    if source in LIVE_SOURCES:
        if audio is None or not audio.available:
            return None
        return max(0.0, min(1.0, audio.side(LIVE_SOURCES[source]).level))
    if source in ("output_volume", "input_volume"):
        if audio is None or not audio.available:
            return None
        vol = audio.side(OUTPUT if source == "output_volume" else INPUT).volume
        return None if vol is None else max(0.0, min(1.0, vol / 100.0))
    if source == "variable":
        n = _number(ctx.variables.get(str(v["name"] or "").strip(), ""))
        return None if n is None else max(0.0, min(1.0, n / 100.0))
    return None


def _alpha(color: str, strength: int) -> QColor:
    c = QColor(color)
    c.setAlpha(max(25, min(255, int(round(255 * max(10, min(100, int(strength))) / 100.0)))))
    return c


def paint_bar(p: QPainter, rect: QRectF, frac: float, color: str, track: str | None = None,
              vertical: bool = False, radius: float = 0.0, strength: int = 100):
    """One bar, filled to `frac`. Used by key meters and by the small screen."""
    frac = max(0.0, min(1.0, frac))
    p.setPen(Qt.NoPen)
    if track:
        p.setBrush(_alpha(track, strength))
        if radius:
            p.drawRoundedRect(rect, radius, radius)
        else:
            p.drawRect(rect)
    if frac <= 0:
        return
    if vertical:
        h = rect.height() * frac
        filled = QRectF(rect.left(), rect.bottom() - h, rect.width(), h)
    else:
        filled = QRectF(rect.left(), rect.top(), rect.width() * frac, rect.height())
    p.setBrush(_alpha(color, strength))
    if radius:
        p.save()
        p.setClipRect(filled)
        p.drawRoundedRect(rect, radius, radius)
        p.restore()
    else:
        p.drawRect(filled)


def paint_meter(p: QPainter, rect: QRectF, meter: dict, frac: float):
    """The background meter on one key."""
    v = values(meter)
    style, color, strength = v["style"], v["color"], int(v["strength"] or 55)
    track = v["track"] if v["show_track"] else None
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if style == "ring":
        inset = max(3.0, min(rect.width(), rect.height()) * 0.045)
        width = inset * 1.6
        box = rect.adjusted(inset + width / 2, inset + width / 2, -inset - width / 2, -inset - width / 2)
        p.setBrush(Qt.NoBrush)
        if track:
            p.setPen(QPen(_alpha(track, strength), width, Qt.SolidLine, Qt.FlatCap))
            p.drawArc(box, 0, 360 * 16)
        if frac > 0:
            p.setPen(QPen(_alpha(color, strength), width, Qt.SolidLine, Qt.FlatCap))
            p.drawArc(box, 90 * 16, -int(round(360 * 16 * max(0.0, min(1.0, frac)))))
    elif style == "edge":
        h = max(4.0, rect.height() * 0.1)
        bar = QRectF(rect.left(), rect.bottom() - h, rect.width(), h)
        paint_bar(p, bar, frac, color, track, vertical=False, strength=strength)
    elif style == "bar_left":
        paint_bar(p, rect, frac, color, track, vertical=False, strength=strength)
    else:
        paint_bar(p, rect, frac, color, track, vertical=True, strength=strength)
    p.restore()
