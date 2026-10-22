"""Small-screen faces. Each face lists its options (the editor builds a form from them) and paints itself.

To add a face: subclass Face, give it an id, label and options, implement paint(), and add it to FACES.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QTextOption

from ..schema import Field, fill_defaults
from .template import HELP, RenderContext, expand, format_time, uses_seconds

BLUE, ORANGE, LIGHT, GREY, DIM = "#378ADD", "#EF9F27", "#F1EFE8", "#888780", "#B4B2A9"
MARGIN = 10   # the small screen has rounded corners


def make_font(ctx: RenderContext, px: float, bold: bool = False) -> QFont:
    f = QFont(ctx.font_family) if ctx.font_family else QFont()
    f.setPixelSize(max(6, int(px)))
    f.setBold(bold)
    return f


def fit_font(ctx, text, px, max_w, bold=False, min_px=10):
    """Shrink the font until the widest line fits max_w."""
    lines = text.split("\n") or [""]
    while px > min_px:
        f = make_font(ctx, px, bold)
        fm = QFontMetricsF(f)
        if max(fm.horizontalAdvance(l) for l in lines) <= max_w:
            return f
        px -= 2
    return make_font(ctx, min_px, bold)


def draw_text(p: QPainter, rect: QRectF, text: str, font: QFont, color: str,
              halign=Qt.AlignHCenter, valign=Qt.AlignVCenter, wrap=True):
    if not text:
        return
    p.setFont(font)
    p.setPen(QColor(color))
    opt = QTextOption(halign | valign)
    opt.setWrapMode(QTextOption.WordWrap if wrap else QTextOption.NoWrap)
    p.drawText(rect, text, opt)


def text_height(font: QFont, lines: int = 1) -> float:
    return QFontMetricsF(font).lineSpacing() * lines


class Face:
    id = ""
    label = ""
    options: list[Field] = []

    def values(self, opts):
        return fill_defaults(opts, self.options)

    def needs_seconds(self, opts) -> bool:
        return False

    def command(self, opts) -> tuple[str, int] | None:
        """(shell command, refresh seconds) if this face shows command output."""
        return None

    def paint(self, p: QPainter, rect: QRectF, opts: dict, ctx: RenderContext, output: str = ""):
        raise NotImplementedError


COMMON_TEXT_HELP = "Placeholders you can use:\n" + HELP


class AnalogClock(Face):
    id = "analog"
    label = "Analogue clock"
    options = [
        Field("bg", "Background", "color", "#000000"),
        Field("dial", "Dial", "choice", "ring", [("ring", "Ring"), ("filled", "Filled"), ("none", "None")]),
        Field("dial_color", "Dial colour", "color", "#444441", show_if=("dial", ("ring", "filled"))),
        Field("ticks", "Tick marks", "choice", "hours",
              [("none", "None"), ("quarters", "Quarters"), ("hours", "Hours"), ("minutes", "Hours and minutes")]),
        Field("tick_color", "Tick colour", "color", "#D3D1C7"),
        Field("numerals", "Numbers", "choice", "none", [("none", "None"), ("quarters", "12, 3, 6, 9"), ("all", "All")]),
        Field("numeral_color", "Number colour", "color", "#D3D1C7", show_if=("numerals", ("quarters", "all"))),
        Field("hour_color", "Hour hand", "color", BLUE),
        Field("minute_color", "Minute hand", "color", ORANGE),
        Field("hand_width", "Hand thickness", "int", 8, minimum=2, maximum=16),
        Field("seconds", "Second hand", "bool", False, help="Redraws the whole display once a second."),
        Field("second_color", "Second hand colour", "color", LIGHT, show_if=("seconds", (True,))),
        Field("top_text", "Text above", "template", "", help=COMMON_TEXT_HELP),
        Field("bottom_text", "Text below", "template", "{date}", help=COMMON_TEXT_HELP),
        Field("text_color", "Text colour", "color", DIM),
        Field("text_size", "Text size", "int", 22, minimum=10, maximum=48),
    ]

    def needs_seconds(self, opts):
        v = self.values(opts)
        return bool(v["seconds"]) or uses_seconds(v["top_text"]) or uses_seconds(v["bottom_text"])

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        inner = rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        tf = make_font(ctx, v["text_size"])
        top, bottom = expand(v["top_text"], ctx, output), expand(v["bottom_text"], ctx, output)
        th = text_height(tf) + 4
        area = QRectF(inner)
        if top:
            draw_text(p, QRectF(inner.left(), inner.top(), inner.width(), th), top,
                      fit_font(ctx, top, v["text_size"], inner.width()), v["text_color"], wrap=False)
            area.setTop(area.top() + th)
        if bottom:
            draw_text(p, QRectF(inner.left(), inner.bottom() - th, inner.width(), th), bottom,
                      fit_font(ctx, bottom, v["text_size"], inner.width()), v["text_color"], wrap=False)
            area.setBottom(area.bottom() - th)
        r = min(area.width(), area.height()) / 2 - 2
        c = area.center()
        p.setRenderHint(QPainter.Antialiasing)

        if v["dial"] == "filled":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(v["dial_color"]))
            p.drawEllipse(c, r, r)
        elif v["dial"] == "ring":
            p.setPen(QPen(QColor(v["dial_color"]), max(2.0, r * 0.03)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r - 1, r - 1)

        def point(angle_deg, dist):
            a = math.radians(angle_deg - 90)
            return QPointF(c.x() + math.cos(a) * dist, c.y() + math.sin(a) * dist)

        ticks = v["ticks"]
        tick_outer = r * 0.92
        if ticks != "none":
            for i in range(60):
                is_hour, is_quarter = i % 5 == 0, i % 15 == 0
                if ticks == "quarters" and not is_quarter:
                    continue
                if ticks == "hours" and not is_hour:
                    continue
                length = r * (0.14 if is_quarter else 0.09 if is_hour else 0.04)
                width = max(1.5, r * (0.045 if is_quarter else 0.03 if is_hour else 0.012))
                p.setPen(QPen(QColor(v["tick_color"]), width, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(point(i * 6, tick_outer - length), point(i * 6, tick_outer))

        if v["numerals"] != "none":
            nf = make_font(ctx, r * 0.2)
            dist = tick_outer - r * (0.32 if ticks != "none" else 0.12)
            for h in range(1, 13):
                if v["numerals"] == "quarters" and h % 3:
                    continue
                pt = point(h * 30, dist)
                box = QRectF(pt.x() - r * 0.2, pt.y() - r * 0.15, r * 0.4, r * 0.3)
                draw_text(p, box, str(h), nf, v["numeral_color"], wrap=False)

        now = ctx.now
        secs = now.second if v["seconds"] else 0
        hour_angle = ((now.hour % 12) + now.minute / 60 + secs / 3600) * 30
        minute_angle = (now.minute + secs / 60) * 6
        hw = float(v["hand_width"])

        def hand(angle, length, width, color, tail=0.12):
            p.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(point(angle + 180, r * tail), point(angle, length))

        hand(hour_angle, r * 0.52, hw, v["hour_color"])
        hand(minute_angle, r * 0.8, max(2.0, hw * 0.7), v["minute_color"])
        if v["seconds"]:
            hand(now.second * 6, r * 0.86, max(1.5, hw * 0.28), v["second_color"], 0.18)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(v["second_color"] if v["seconds"] else v["minute_color"]))
        p.drawEllipse(c, max(3.0, hw * 0.6), max(3.0, hw * 0.6))


class DigitalClock(Face):
    id = "digital"
    label = "Digital clock"
    options = [
        Field("bg", "Background", "color", "#000000"),
        Field("color", "Time colour", "color", BLUE),
        Field("size", "Time size", "int", 84, minimum=30, maximum=140),
        Field("bold", "Bold", "bool", True),
        Field("seconds", "Show seconds", "bool", False),
        Field("top_text", "Text above", "template", "", help=COMMON_TEXT_HELP),
        Field("bottom_text", "Text below", "template", "{date}", help=COMMON_TEXT_HELP),
        Field("text_color", "Text colour", "color", ORANGE),
        Field("text_size", "Text size", "int", 26, minimum=10, maximum=60),
    ]

    def needs_seconds(self, opts):
        v = self.values(opts)
        return bool(v["seconds"]) or uses_seconds(v["top_text"]) or uses_seconds(v["bottom_text"])

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        inner = rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        t = format_time(ctx.now, ctx.time_24h, bool(v["seconds"]))
        if not ctx.time_24h and " " in t:
            t = t.replace(" ", "\n", 1)   # AM/PM on its own line
        tfont = fit_font(ctx, t, v["size"], inner.width(), bool(v["bold"]))
        top, bottom = expand(v["top_text"], ctx, output), expand(v["bottom_text"], ctx, output)
        sf = make_font(ctx, v["text_size"])
        th_top = text_height(sf, top.count("\n") + 1) + 6 if top else 0
        th_bot = text_height(sf, bottom.count("\n") + 1) + 6 if bottom else 0
        time_h = text_height(tfont, t.count("\n") + 1)
        total = th_top + time_h + th_bot
        y = inner.top() + max(0.0, (inner.height() - total) / 2)
        if top:
            draw_text(p, QRectF(inner.left(), y, inner.width(), th_top), top,
                      fit_font(ctx, top, v["text_size"], inner.width()), v["text_color"])
            y += th_top
        draw_text(p, QRectF(inner.left(), y, inner.width(), time_h), t, tfont, v["color"], wrap=False)
        y += time_h
        if bottom:
            draw_text(p, QRectF(inner.left(), y, inner.width(), th_bot), bottom,
                      fit_font(ctx, bottom, v["text_size"], inner.width()), v["text_color"])


class TextFace(Face):
    id = "text"
    label = "Text"
    options = [
        Field("bg", "Background", "color", "#000000"),
        Field("text", "Text", "multiline", "{page}\n{time}", help=COMMON_TEXT_HELP),
        Field("color", "Text colour", "color", LIGHT),
        Field("size", "Text size", "int", 34, minimum=10, maximum=120),
        Field("bold", "Bold", "bool", False),
        Field("align", "Line up", "choice", "center", [("center", "Centre"), ("left", "Left")]),
        Field("valign", "Position", "choice", "middle", [("top", "Top"), ("middle", "Middle"), ("bottom", "Bottom")]),
        Field("shrink", "Shrink long text to fit", "bool", True),
        Field("command", "Command for {output}", "text", "",
              help="Optional. A shell command whose output fills {output}, for example a script that prints "
                   "the weather. Runs in the background."),
        Field("refresh", "Run the command every (seconds)", "int", 60, minimum=1, maximum=86400),
    ]

    def needs_seconds(self, opts):
        return uses_seconds(self.values(opts)["text"])

    def command(self, opts):
        v = self.values(opts)
        cmd = (v.get("command") or "").strip()
        return (cmd, int(v.get("refresh") or 60)) if cmd else None

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        p.setRenderHint(QPainter.TextAntialiasing)
        inner = rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        text = expand(v["text"], ctx, output)
        size = int(v["size"])
        font = make_font(ctx, size, bool(v["bold"]))
        halign = Qt.AlignLeft if v["align"] == "left" else Qt.AlignHCenter
        valign = {"top": Qt.AlignTop, "bottom": Qt.AlignBottom}.get(v["valign"], Qt.AlignVCenter)
        if v["shrink"]:
            while size > 10:
                font = make_font(ctx, size, bool(v["bold"]))
                p.setFont(font)
                needed = p.boundingRect(inner, int(halign | Qt.AlignTop | Qt.TextWordWrap), text)
                if needed.height() <= inner.height() and needed.width() <= inner.width():
                    break
                size -= 2
        draw_text(p, inner, text, font, v["color"], halign, valign)


class ImageFace(Face):
    id = "image"
    label = "Picture"
    options = [
        Field("path", "Picture", "file", "", file_filter="Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg)"),
        Field("fit", "Fit", "choice", "contain", [("contain", "Whole picture"), ("cover", "Fill the screen"),
                                                  ("stretch", "Stretch")]),
        Field("bg", "Background", "color", "#000000"),
        Field("caption", "Caption", "template", "", help=COMMON_TEXT_HELP),
        Field("caption_color", "Caption colour", "color", LIGHT),
        Field("caption_size", "Caption size", "int", 24, minimum=10, maximum=60),
    ]

    def __init__(self, icons=None):
        self.icons = icons

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        caption = expand(v["caption"], ctx, output)
        cf = make_font(ctx, v["caption_size"])
        area = QRectF(rect)
        if caption:
            ch = text_height(cf) + 8
            area.setBottom(area.bottom() - ch - MARGIN)
            draw_text(p, QRectF(rect.left() + MARGIN, area.bottom(), rect.width() - 2 * MARGIN, ch), caption,
                      fit_font(ctx, caption, v["caption_size"], rect.width() - 2 * MARGIN), v["caption_color"])
        img = self.icons.file_image(v["path"]) if (self.icons and v["path"]) else None
        if img is None:
            draw_text(p, area, "Choose a picture", make_font(ctx, 22), GREY)
            return
        iw, ih = img.width(), img.height()
        if v["fit"] == "stretch":
            target = area
            src = QRectF(0, 0, iw, ih)
        elif v["fit"] == "cover":
            scale = max(area.width() / iw, area.height() / ih)
            sw, sh = area.width() / scale, area.height() / scale
            src = QRectF((iw - sw) / 2, (ih - sh) / 2, sw, sh)
            target = area
        else:
            scale = min(area.width() / iw, area.height() / ih)
            tw, th = iw * scale, ih * scale
            target = QRectF(area.left() + (area.width() - tw) / 2, area.top() + (area.height() - th) / 2, tw, th)
            src = QRectF(0, 0, iw, ih)
        p.drawImage(target, img, src)


class BlankFace(Face):
    id = "blank"
    label = "Plain colour"
    options = [Field("bg", "Colour", "color", "#000000")]

    def paint(self, p, rect, opts, ctx, output=""):
        p.fillRect(rect, QColor(self.values(opts)["bg"]))


def make_faces(icons) -> dict[str, Face]:
    faces = [AnalogClock(), DigitalClock(), TextFace(), ImageFace(icons), BlankFace()]
    return {f.id: f for f in faces}
