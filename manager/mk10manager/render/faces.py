"""Small-screen faces. Each face lists its options (the editor builds a form from them) and paints itself.

To add a face: subclass Face, give it an id, label and options, implement paint(), and add it to FACES.
"""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QTextOption

from ..schema import Field, fill_defaults
from ..system.audio import INPUT, OUTPUT
from ..system.nowplaying import format_time as format_track_time
from ..system.teams import short_device_name
from . import meters
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


def _duration(minutes: int) -> str:
    """26 -> '26 min', 90 -> '1h 30m', 1500 -> '25h'. Keeps a long snooze from printing a silly number."""
    minutes = max(0, int(minutes))
    if minutes < 60:
        return "%d min" % minutes
    hours, rest = divmod(minutes, 60)
    return "%dh %dm" % (hours, rest) if rest else "%dh" % hours


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

    def live_targets(self, opts) -> set:
        """Which live sound levels this face needs measured (see system.audio)."""
        return set()

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


class AudioFace(Face):
    """What the speakers and microphone are doing: which devices, how loud, and whether they're muted."""

    id = "audio"
    label = "Sound"
    options = [
        Field("bg", "Background", "color", "#000000"),
        Field("top_text", "Text at the top", "template", "{time}", help=COMMON_TEXT_HELP),
        Field("bottom_text", "Text at the bottom", "template", "", help=COMMON_TEXT_HELP),
        Field("text_color", "Text colour", "color", LIGHT),
        Field("text_size", "Text size", "int", 30, minimum=10, maximum=60),
        Field("show_output", "Show the speakers", "bool", True),
        Field("output_color", "Speaker colour", "color", BLUE),
        Field("show_input", "Show the microphone", "bool", True),
        Field("input_color", "Microphone colour", "color", ORANGE),
        Field("live", "Show how loud it is right now", "bool", True,
              help="Draws the live loudness over the volume bar. On Linux this measures the microphone, so it "
                   "only runs while this screen is showing."),
        Field("muted_color", "Colour when muted", "color", "#5F5E5A"),
    ]

    #: which live levels this face needs measuring, so the app can leave the microphone alone otherwise
    def live_targets(self, opts) -> set:
        v = self.values(opts)
        if not v["live"]:
            return set()
        return ({OUTPUT} if v["show_output"] else set()) | ({INPUT} if v["show_input"] else set())

    def needs_seconds(self, opts):
        v = self.values(opts)
        return uses_seconds(v["top_text"]) or uses_seconds(v["bottom_text"])

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        inner = rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        audio = getattr(ctx, "audio", None)
        tf = make_font(ctx, v["text_size"])
        top, bottom = expand(v["top_text"], ctx, output), expand(v["bottom_text"], ctx, output)
        area = QRectF(inner)
        if top:
            th = text_height(tf, top.count("\n") + 1) + 4
            draw_text(p, QRectF(inner.left(), area.top(), inner.width(), th), top,
                      fit_font(ctx, top, v["text_size"], inner.width()), v["text_color"], wrap=False)
            area.setTop(area.top() + th)
        if bottom:
            bh = text_height(tf, bottom.count("\n") + 1) + 4
            draw_text(p, QRectF(inner.left(), area.bottom() - bh, inner.width(), bh), bottom,
                      fit_font(ctx, bottom, v["text_size"], inner.width()), v["text_color"], wrap=False)
            area.setBottom(area.bottom() - bh)
        if audio is None or not audio.available:
            draw_text(p, area, "Sound state\nnot available", make_font(ctx, 24), GREY)
            return
        blocks = ([(OUTPUT, "SPEAKERS", v["output_color"])] if v["show_output"] else []) + \
                 ([(INPUT, "MICROPHONE", v["input_color"])] if v["show_input"] else [])
        if not blocks:
            return
        title_font = make_font(ctx, min(20.0, area.width() * 0.095), True)
        name_px = min(24.0, area.width() * 0.115)
        bar_h = max(14.0, min(24.0, area.height() * 0.09))
        block_h = text_height(title_font) + 2 + text_height(make_font(ctx, name_px)) + 6 + bar_h
        gap = max(10.0, (area.height() - block_h * len(blocks)) / max(1, len(blocks)))
        y = area.top()
        for target, title, color in blocks:
            self._block(p, QRectF(area.left(), y, area.width(), block_h), audio.side(target), title, color,
                        v, ctx, title_font, name_px, bar_h)
            y += block_h + gap

    def _block(self, p, box, side, title, color, v, ctx, title_font, name_px, bar_h):
        muted = bool(side.muted)
        shown = v["muted_color"] if muted else color
        th = text_height(title_font)
        # top row: who this is on the left, and either MUTED or the volume on the right
        right = "MUTED" if muted else ("%d" % side.volume if side.volume is not None else "")
        right_color = ORANGE if muted else v["text_color"]
        right_w = QFontMetricsF(title_font).horizontalAdvance(right) + 8 if right else 0
        if right:
            draw_text(p, QRectF(box.right() - right_w, box.top(), right_w, th), right, title_font,
                      right_color, Qt.AlignRight, wrap=False)
        draw_text(p, QRectF(box.left(), box.top(), box.width() - right_w, th),
                  self._fit(title, title_font, box.width() - right_w), title_font, shown, Qt.AlignLeft, wrap=False)
        name = side.name or "(unknown)"
        nf = fit_font(ctx, name, name_px, box.width(), False, 11)
        nh = text_height(nf)
        draw_text(p, QRectF(box.left(), box.top() + th + 2, box.width(), nh),
                  self._fit(name, nf, box.width()), nf, v["muted_color"] if muted else v["text_color"],
                  Qt.AlignLeft, wrap=False)
        bar = QRectF(box.left(), box.bottom() - bar_h, box.width(), bar_h)
        meters.paint_bar(p, bar, (side.volume or 0) / 100.0, shown, "#2C2C2A", radius=bar_h / 2)
        if v["live"] and not muted and side.level > 0.02:
            over = QRectF(bar.left() + 2, bar.top() + bar_h * 0.3, bar.width() - 4, bar_h * 0.4)
            meters.paint_bar(p, over, side.level, LIGHT, None, radius=bar_h * 0.2)

    @staticmethod
    def _fit(text: str, font, width: float) -> str:
        """Cut a long device name off with an ellipsis rather than letting it run over."""
        fm = QFontMetricsF(font)
        if fm.horizontalAdvance(text) <= width:
            return text
        while text and fm.horizontalAdvance(text + "…") > width:
            text = text[:-1]
        return text + "…"


class SlackFace(Face):
    """Slack, plus the bits of the sound card that matter while you're in a huddle."""

    id = "slack"
    label = "Slack"
    options = [
        Field("bg", "Background", "color", "#000000"),
        Field("top_text", "Text at the top", "template", "{time}", help=COMMON_TEXT_HELP),
        Field("bottom_text", "Text at the bottom", "template", "", help=COMMON_TEXT_HELP),
        Field("text_color", "Text colour", "color", LIGHT),
        Field("text_size", "Text size", "int", 28, minimum=10, maximum=60),
        Field("accent", "Highlight colour", "color", BLUE),
        Field("busy_color", "Colour when busy", "color", ORANGE),
        Field("show_status", "Show my Slack status", "bool", True),
        Field("show_unread", "Show unread messages", "bool", True),
        Field("show_mic", "Show the microphone", "bool", True,
              help="The real microphone, from the sound card — not Slack's. That's the one a mute key changes."),
        Field("live", "Show how loud the microphone is", "bool", True, show_if=("show_mic", (True,))),
    ]

    def live_targets(self, opts) -> set:
        v = self.values(opts)
        return {INPUT} if (v["show_mic"] and v["live"]) else set()

    def needs_seconds(self, opts):
        v = self.values(opts)
        return uses_seconds(v["top_text"]) or uses_seconds(v["bottom_text"])

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        inner = rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        area = QRectF(inner)
        tf = make_font(ctx, v["text_size"])
        for text, at_top in ((expand(v["top_text"], ctx, output), True),
                             (expand(v["bottom_text"], ctx, output), False)):
            if not text:
                continue
            h = text_height(tf, text.count("\n") + 1) + 4
            y = area.top() if at_top else area.bottom() - h
            draw_text(p, QRectF(inner.left(), y, inner.width(), h), text,
                      fit_font(ctx, text, v["text_size"], inner.width()), v["text_color"], wrap=False)
            if at_top:
                area.setTop(area.top() + h)
            else:
                area.setBottom(area.bottom() - h)

        slack = getattr(ctx, "slack", None)
        rows = []
        if slack is None or not slack.connected:
            rows.append(("SLACK", slack.note[:40] if (slack and slack.note) else "not set up", GREY, False))
        else:
            huddle = "In a huddle" if slack.in_huddle else "No huddle"
            rows.append(("HUDDLE", huddle, v["busy_color"] if slack.in_huddle else v["accent"],
                         slack.in_huddle))
            presence = "Away" if slack.away else "Active"
            if slack.dnd:
                left = slack.dnd_minutes_left()
                presence += (" · snoozed %s" % _duration(left)) if left else " · snoozed"
            rows.append(("ME", presence, v["busy_color"] if (slack.away or slack.dnd) else v["accent"],
                         slack.dnd or slack.away))
            status = slack.status_text or str(slack.status_emoji or "").strip(":").replace("_", " ")
            if v["show_status"] and status:
                rows.append(("STATUS", status, v["accent"], False))
            if v["show_unread"]:
                if slack.unread:
                    text = "%d unread" % slack.unread
                    if slack.mentions:
                        text += " · %d for me" % slack.mentions
                else:
                    text = "All caught up"
                rows.append(("MESSAGES", text, v["busy_color"] if slack.unread else v["accent"],
                             bool(slack.unread)))
        self._rows(p, area, rows, v, ctx)

    def _rows(self, p, area, rows, v, ctx):
        """One line per thing: a small grey label on the left, the answer on the right."""
        audio = getattr(ctx, "audio", None)
        mic = v["show_mic"] and audio is not None and audio.available
        label_font = make_font(ctx, min(16.0, area.width() * 0.075), True)
        value_px = min(float(v.get("row_px") or 26.0), area.width() * 0.12)
        lh = text_height(label_font)
        row_h = max(lh, text_height(make_font(ctx, value_px))) + 4
        bar_h = max(10.0, min(20.0, area.height() * 0.075)) if mic else 0.0
        mic_block = (lh + 3 + bar_h) if mic else 0.0
        gap_min = 5.0

        def needed(n):
            slots = n + (1 if mic else 0)
            return row_h * n + mic_block + gap_min * max(0, slots - 1)

        while len(rows) > 2 and needed(len(rows)) > area.height():
            rows = rows[:-1]
        slots = len(rows) + (1 if mic else 0)
        spare = area.height() - row_h * len(rows) - mic_block
        gap = max(gap_min, min(18.0, spare / max(1, slots - 1))) if slots > 1 else 0.0

        y = area.top()
        for title, value, colour, strong in rows:
            label_w = QFontMetricsF(label_font).horizontalAdvance(title) + 10
            draw_text(p, QRectF(area.left(), y, label_w, row_h), title, label_font, GREY,
                      Qt.AlignLeft, wrap=False)
            room = area.width() - label_w
            vf = fit_font(ctx, value, value_px, room, strong, 11)
            draw_text(p, QRectF(area.left() + label_w, y, room, row_h), self._clip(value, vf, room),
                      vf, colour, Qt.AlignRight, wrap=False)
            y += row_h + gap
        if not mic:
            return
        side = audio.side(INPUT)
        muted = bool(side.muted)
        draw_text(p, QRectF(area.left(), y, area.width(), lh), "MICROPHONE", label_font, GREY,
                  Qt.AlignLeft, wrap=False)
        if muted:
            draw_text(p, QRectF(area.left(), y, area.width(), lh), "MUTED", label_font, v["busy_color"],
                      Qt.AlignRight, wrap=False)
        bar = QRectF(area.left(), y + lh + 3, area.width(), bar_h)
        colour = GREY if muted else v["accent"]
        meters.paint_bar(p, bar, (side.volume or 0) / 100.0, colour, "#2C2C2A", radius=bar_h / 2)
        if v["live"] and not muted and side.level > 0.02:
            over = QRectF(bar.left() + 2, bar.top() + bar_h * 0.3, bar.width() - 4, bar_h * 0.4)
            meters.paint_bar(p, over, side.level, LIGHT, None, radius=bar_h * 0.2)

    @staticmethod
    def _clip(text: str, font, width: float) -> str:
        fm = QFontMetricsF(font)
        if fm.horizontalAdvance(text) <= width:
            return text
        while text and fm.horizontalAdvance(text + "…") > width:
            text = text[:-1]
        return text + "…"


class TeamsFace(SlackFace):
    """A Teams call at a glance: the meeting, how long it's run, and every switch that matters in it."""

    id = "teams"
    label = "Microsoft Teams"
    options = [
        Field("bg", "Background", "color", "#000000"),
        Field("top_text", "Text at the top during a call", "template", "{teams_meeting}", help=COMMON_TEXT_HELP),
        Field("idle_text", "Text at the top otherwise", "template", "{time}", help=COMMON_TEXT_HELP),
        Field("text_color", "Text colour", "color", LIGHT),
        Field("text_size", "Text size", "int", 26, minimum=10, maximum=60),
        Field("accent", "Highlight colour", "color", BLUE),
        Field("busy_color", "Colour when muted, sharing or hand up", "color", ORANGE),
        Field("show_devices", "Name Windows' microphone and speakers", "bool", False,
              help="Windows' default devices: the ones the PC microphone and speaker keys mute. Teams may be "
                   "using others, and doesn't show which during a call."),
        Field("row_px", "Row text size", "int", 21, minimum=12, maximum=30),
    ]

    def needs_seconds(self, opts):
        v = self.values(opts)
        return uses_seconds(v["top_text"]) or uses_seconds(v["idle_text"])

    def live_targets(self, opts) -> set:
        return set()

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        area = rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        teams = getattr(ctx, "teams", None)
        audio = getattr(ctx, "audio", None)
        in_call = bool(teams is not None and teams.available and teams.in_call)

        title = expand(v["top_text"] if in_call else v["idle_text"], ctx, output).replace("\n", " ")
        if title:
            tf = fit_font(ctx, title, v["text_size"], area.width(), True, 14)
            h = text_height(tf) + 6
            draw_text(p, QRectF(area.left(), area.top(), area.width(), h),
                      self._clip(title, tf, area.width()), tf, v["text_color"], wrap=False)
            area.setTop(area.top() + h + 4)

        busy, accent = v["busy_color"], v["accent"]
        rows = []
        if teams is None or not teams.available:
            rows.append(("TEAMS", ((teams.note if teams else "") or "can't be read")[:40], GREY, False))
        elif in_call:
            rows.append(("CALL", format_track_time(teams.call_seconds()) if teams.clock_known else "Yes",
                         accent, False))
        else:
            rows.append(("CALL", "None" if teams.running else "Teams closed", GREY, False))

        muted = teams.muted if in_call and teams.muted is not None else \
            (audio.input.muted if audio is not None and audio.available else None)
        mic = {True: "Muted", False: "Live", None: "?"}[muted]
        if v["show_devices"] and audio is not None and audio.available and audio.input.name:
            mic += " · " + short_device_name(audio.input.name)
        rows.append(("MIC", mic, busy if muted else accent, bool(muted)))
        if audio is not None and audio.available:
            out = audio.output
            words = short_device_name(out.name) if (v["show_devices"] and out.name) else ""
            if out.muted:
                words = "Muted · " + words if words else "Muted"
            rows.append(("OUT", words or "On", busy if out.muted else accent, bool(out.muted)))
        if in_call:
            if teams.camera is not None:
                rows.append(("CAMERA", "On" if teams.camera else "Off", accent if teams.camera else GREY, False))
            if teams.sharing is not None:
                what = (teams.shared[:1].upper() + teams.shared[1:]) if teams.shared else "Yes"
                rows.append(("SHARING", what if teams.sharing else "No", busy if teams.sharing else GREY,
                             bool(teams.sharing)))
            if teams.hand is not None:
                rows.append(("HAND", "Raised" if teams.hand else "Down", busy if teams.hand else GREY,
                             bool(teams.hand)))
        self._rows(p, area, rows, dict(v, show_mic=False, live=False), ctx)


class NowPlayingFace(Face):
    """Whatever the system says is playing: Spotify, a browser tab, VLC. Artwork if the player gives us any."""

    id = "nowplaying"
    label = "Now playing"
    options = [
        Field("bg", "Background", "color", "#000000"),
        Field("art", "Show the artwork", "choice", "cover",
              [("cover", "Fill the screen behind the text"), ("top", "Above the text"), ("none", "Don't show it")]),
        Field("dim", "Dim the artwork (%)", "int", 65, minimum=0, maximum=100, show_if=("art", ("cover",)),
              help="Darkens the picture so the words on top of it stay readable."),
        Field("title_color", "Title colour", "color", LIGHT),
        Field("title_size", "Title size", "int", 32, minimum=10, maximum=70),
        Field("artist_color", "Artist colour", "color", BLUE),
        Field("artist_size", "Artist size", "int", 24, minimum=10, maximum=60),
        Field("show_progress", "Show how far through", "bool", True),
        Field("progress_color", "Progress colour", "color", ORANGE),
        Field("show_player", "Show which player", "bool", True),
        Field("idle_text", "When nothing is playing", "template", "{time}", help=COMMON_TEXT_HELP),
        Field("idle_color", "Idle colour", "color", GREY),
    ]

    def needs_seconds(self, opts):
        v = self.values(opts)
        return bool(v["show_progress"]) or uses_seconds(v["idle_text"])

    def paint(self, p, rect, opts, ctx, output=""):
        v = self.values(opts)
        p.fillRect(rect, QColor(v["bg"]))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        track = getattr(ctx, "now_playing", None)
        if track is None or not track.available or not track.known:
            idle = expand(v["idle_text"], ctx, output)
            if idle:
                draw_text(p, rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN), idle,
                          fit_font(ctx, idle, v["title_size"], rect.width() - 2 * MARGIN), v["idle_color"])
            return
        inner = rect.adjusted(MARGIN, MARGIN, -MARGIN, -MARGIN)
        art = QImage.fromData(track.art) if track.art else None
        if art is not None and art.isNull():
            art = None
        area = QRectF(inner)
        if art is not None and v["art"] == "cover":
            self._cover(p, rect, art, int(v["dim"]))
        elif art is not None and v["art"] == "top":
            side = min(inner.width() * 0.8, inner.height() * 0.45)
            box = QRectF(inner.center().x() - side / 2, inner.top(), side, side)
            self._fit(p, box, art)
            area.setTop(box.bottom() + 8)

        lines = []
        if v["show_player"] and track.player:
            lines.append(("player", track.player.upper(), GREY))
        lines.append(("title", track.title, v["title_color"]))
        if track.artist:
            lines.append(("artist", track.artist, v["artist_color"]))
        bar_h = 6.0 if v["show_progress"] and track.length else 0.0
        time_h = text_height(make_font(ctx, 18)) if bar_h else 0.0
        text_area = QRectF(area.left(), area.top(), area.width(), area.height() - bar_h - time_h - 8)
        self._lines(p, text_area, lines, v, ctx)
        if bar_h:
            self._progress(p, QRectF(area.left(), area.bottom() - bar_h - time_h - 2, area.width(), bar_h),
                           track, v, ctx, time_h)

    def _lines(self, p, area, lines, v, ctx):
        """Player, title and artist, stacked and centred. Each is shrunk to the width, then the whole
        stack is made to fit the height — dropping the player name first, since it matters least."""
        def build(items, scale=1.0):
            out = []
            for kind, text, colour in items:
                if not text:
                    continue
                if kind == "player":
                    out.append((text, make_font(ctx, 16 * scale, True), colour))
                    continue
                big = kind == "title"
                size = (v["title_size"] if big else v["artist_size"]) * scale
                out.append((text, fit_font(ctx, text, size, area.width(), big, 16 if big else 12), colour))
            return out

        def height(built):
            return sum(text_height(f) for _t, f, _c in built) + 4 * max(0, len(built) - 1)

        built = build(lines)
        if height(built) > area.height() and len(lines) > 1:
            built = build([line for line in lines if line[0] != "player"])
        scale = 1.0
        while height(built) > area.height() and scale > 0.55:
            scale -= 0.1
            built = build([line for line in lines if line[0] != "player"], scale)
        y = area.top() + max(0.0, (area.height() - height(built)) / 2)
        for text, font, colour in built:
            h = text_height(font)
            draw_text(p, QRectF(area.left(), y, area.width(), h), self._clip(text, font, area.width()),
                      font, colour, Qt.AlignHCenter, wrap=False)
            y += h + 4

    def _progress(self, p, bar, track, v, ctx, time_h):
        done = track.position_now()
        meters.paint_bar(p, bar, (done / track.length) if track.length else 0.0, v["progress_color"],
                         "#2C2C2A", radius=bar.height() / 2)
        if not time_h:
            return
        f = make_font(ctx, 18)
        row = QRectF(bar.left(), bar.bottom() + 2, bar.width(), time_h)
        draw_text(p, row, format_track_time(done), f, GREY, Qt.AlignLeft, wrap=False)
        draw_text(p, row, format_track_time(track.length), f, GREY, Qt.AlignRight, wrap=False)

    @staticmethod
    def _cover(p, rect, art, dim):
        scale = max(rect.width() / art.width(), rect.height() / art.height())
        sw, sh = rect.width() / scale, rect.height() / scale
        p.drawImage(rect, art, QRectF((art.width() - sw) / 2, (art.height() - sh) / 2, sw, sh))
        if dim > 0:
            p.fillRect(rect, QColor(0, 0, 0, int(255 * max(0, min(100, dim)) / 100.0)))

    @staticmethod
    def _fit(p, box, art):
        scale = min(box.width() / art.width(), box.height() / art.height())
        w, h = art.width() * scale, art.height() * scale
        p.drawImage(QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h), art)

    @staticmethod
    def _clip(text, font, width):
        fm = QFontMetricsF(font)
        if fm.horizontalAdvance(text) <= width:
            return text
        while text and fm.horizontalAdvance(text + "…") > width:
            text = text[:-1]
        return text + "…"


class BlankFace(Face):
    id = "blank"
    label = "Plain colour"
    options = [Field("bg", "Colour", "color", "#000000")]

    def paint(self, p, rect, opts, ctx, output=""):
        p.fillRect(rect, QColor(self.values(opts)["bg"]))


def make_faces(icons) -> dict[str, Face]:
    faces = [AnalogClock(), DigitalClock(), TextFace(), AudioFace(), SlackFace(), TeamsFace(), NowPlayingFace(),
             ImageFace(icons), BlankFace()]
    return {f.id: f for f in faces}
