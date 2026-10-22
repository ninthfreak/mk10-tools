"""Draws the whole MK10 canvas (keys and small screen) as one picture, the way the device wants it."""

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from ..device.layout import DeviceLayout
from .faces import draw_text, fit_font, make_faces, make_font
from .icons import IconLibrary
from .template import RenderContext, expand

EMPTY_KEY = QColor(0, 0, 0)


class Renderer:
    def __init__(self, icons: IconLibrary | None = None):
        self.icons = icons or IconLibrary()
        self.faces = make_faces(self.icons)

    def face(self, face_id: str):
        return self.faces.get(face_id) or self.faces["analog"]

    # ---- keys
    def paint_key(self, p: QPainter, rect: QRectF, key: dict | None, ctx: RenderContext, pressed=False):
        if not key:
            p.fillRect(rect, EMPTY_KEY)
            return
        if pressed:
            # Draw normally into a buffer, then shrink it a little: reads as a press, like OpenDeck.
            buf = QImage(int(rect.width()), int(rect.height()), QImage.Format_RGB32)
            bp = QPainter(buf)
            self.paint_key(bp, QRectF(0, 0, rect.width(), rect.height()), key, ctx, False)
            bp.end()
            p.fillRect(rect, QColor(key.get("bg") or "#000000").darker(160))
            m = rect.width() * 0.08
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(rect.adjusted(m, m, -m, -m), buf)
            return
        p.fillRect(rect, QColor(key.get("bg") or "#000000"))
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        pad = 6
        inner = rect.adjusted(pad, pad, -pad, -pad)
        label = expand(key.get("label") or "", ctx)
        pos = key.get("label_pos") or "bottom"
        font = make_font(ctx, key.get("font_size") or 20, bool(key.get("bold")))
        label_h = 0.0
        if label:
            p.setFont(font)
            br = p.boundingRect(inner, int(Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap), label)
            label_h = min(br.height(), inner.height())
        icon = key.get("icon")
        if icon:
            area = QRectF(inner)
            if label and pos == "bottom":
                area.setBottom(inner.bottom() - label_h - 2)
            elif label and pos == "top":
                area.setTop(inner.top() + label_h + 2)
            side = min(rect.width() * (key.get("icon_size") or 62) / 100.0, area.width(), area.height())
            if side >= 8:
                img = self.icons.icon_image(icon, max(16, int(side)))
                if img is not None and not img.isNull():
                    iw, ih = img.width(), img.height()
                    scale = min(side / iw, side / ih)
                    tw, th = iw * scale, ih * scale
                    target = QRectF(area.center().x() - tw / 2, area.center().y() - th / 2, tw, th)
                    p.drawImage(target, img)
        if label:
            valign = {"top": Qt.AlignTop, "middle": Qt.AlignVCenter}.get(pos, Qt.AlignBottom)
            color = key.get("text_color") or "#FFFFFF"
            if icon and pos == "middle":
                self._outlined_text(p, inner, label, font, color, valign)
            else:
                draw_text(p, inner, label, font, color, Qt.AlignHCenter, valign)

    @staticmethod
    def _outlined_text(p, rect, text, font, color, valign):
        """Text over a picture: dark outline so it stays readable."""
        fm = QFontMetricsF(font)
        lines = text.split("\n")
        total = fm.lineSpacing() * len(lines)
        y0 = rect.top() + (rect.height() - total) / 2 if valign == Qt.AlignVCenter else rect.top()
        path = QPainterPath()
        for i, line in enumerate(lines):
            x = rect.left() + (rect.width() - fm.horizontalAdvance(line)) / 2
            path.addText(x, y0 + fm.ascent() + i * fm.lineSpacing(), font, line)
        p.setPen(QPen(QColor(0, 0, 0, 200), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPath(path)

    def key_image(self, key, w, h, ctx, pressed=False) -> QImage:
        img = QImage(w, h, QImage.Format_RGB32)
        p = QPainter(img)
        self.paint_key(p, QRectF(0, 0, w, h), key, ctx, pressed)
        p.end()
        return img

    # ---- small screen
    def paint_screen(self, p, rect, screen: dict | None, ctx, output="", message=None):
        screen = screen or {"face": "analog", "options": {}}
        p.save()
        p.setClipRect(rect)
        self.face(screen.get("face")).paint(p, rect, screen.get("options") or {}, ctx, output)
        if message:
            self._message(p, rect, message, ctx)
        p.restore()

    @staticmethod
    def _message(p, rect, text, ctx):
        box = rect.adjusted(14, rect.height() * 0.28, -14, -rect.height() * 0.28)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#378ADD"), 3))
        p.setBrush(QColor(12, 12, 12, 235))
        p.drawRoundedRect(box, 14, 14)
        f = fit_font(ctx, text, 34, box.width() - 20, True)
        draw_text(p, box.adjusted(10, 6, -10, -6), text, f, "#F1EFE8")

    def screen_image(self, screen, w, h, ctx, output="", message=None) -> QImage:
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        self.paint_screen(p, QRectF(0, 0, w, h), screen, ctx, output, message)
        p.end()
        return img

    # ---- whole canvas
    def compose(self, layout: DeviceLayout, page: dict, ctx: RenderContext, pressed=frozenset(),
                outputs: dict | None = None, message: str | None = None) -> QImage:
        img = QImage(layout.width, layout.height, QImage.Format_RGB32)
        img.fill(Qt.black)
        p = QPainter(img)
        keys = page.get("keys") or []
        for i, r in enumerate(layout.keys):
            key = keys[i] if i < len(keys) else None
            self.paint_key(p, QRectF(r.x, r.y, r.w, r.h), key, ctx, i in pressed)
        for i, r in enumerate(layout.screens):
            screen = page.get("screen") if i == 0 else {"face": "blank", "options": {}}
            output = ""
            cmd = self.face((screen or {}).get("face")).command((screen or {}).get("options") or {})
            if cmd and outputs:
                output = outputs.get(cmd[0], "")
            self.paint_screen(p, QRectF(r.x, r.y, r.w, r.h), screen, ctx, output, message if i == 0 else None)
        p.end()
        return img

    @staticmethod
    def to_jpeg(img: QImage, quality: int = 90) -> bytes:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "JPG", quality)
        return bytes(buf.data())
