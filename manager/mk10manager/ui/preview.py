"""Live picture of the MK10. Click to select a key or the small screen; drag keys to swap; drop pictures on keys."""

import time

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from ..device.layout import DeviceLayout

SELECT = QColor("#378ADD")
PRESS = QColor("#EF9F27")
BODY = QColor("#1E1E1C")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg")


class DevicePreview(QWidget):
    selected = Signal(str, int)            # "key"/"screen", index
    swap = Signal(int, int)
    file_dropped = Signal(str, int, str)   # kind, index, path
    menu_action = Signal(str, str, int)    # action name, kind, index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = DeviceLayout.default()
        self.image: QImage | None = None
        self.selection = ("key", 0)
        self.hover = None
        self.flash: dict[int, float] = {}
        self._drag_from = None
        self._press_pos = None
        self.empty_keys: set[int] = set()
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(420, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(60)
        self._flash_timer.timeout.connect(self._flash_tick)

    # ---- data
    def set_layout(self, layout: DeviceLayout):
        self.layout_ = layout
        self.update()

    def set_image(self, img: QImage):
        self.image = img
        self.update()

    def set_selection(self, kind: str, index: int):
        self.selection = (kind, index)
        self.update()

    def pressed(self, pos: int, down: bool):
        self.flash[pos] = time.time() + (10 if down else 0.35)
        self._flash_timer.start()
        self.update()

    def _flash_tick(self):
        now = time.time()
        self.flash = {k: v for k, v in self.flash.items() if v > now}
        if not self.flash:
            self._flash_timer.stop()
        self.update()

    # ---- geometry
    def _scale_offset(self):
        lw, lh = self.layout_.width, self.layout_.height
        pad = 18
        s = min((self.width() - 2 * pad) / lw, (self.height() - 2 * pad) / lh)
        s = max(s, 0.05)
        ox = (self.width() - lw * s) / 2
        oy = (self.height() - lh * s) / 2
        return s, ox, oy

    def _to_canvas(self, p: QPointF):
        s, ox, oy = self._scale_offset()
        return (p.x() - ox) / s, (p.y() - oy) / s

    def _rect_for(self, kind, index) -> QRectF | None:
        rects = self.layout_.keys if kind == "key" else self.layout_.screens
        if not (0 <= index < len(rects)):
            return None
        r = rects[index]
        s, ox, oy = self._scale_offset()
        return QRectF(ox + r.x * s, oy + r.y * s, r.w * s, r.h * s)

    def hit(self, p: QPointF):
        return self.layout_.hit(*self._to_canvas(p))

    # ---- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        s, ox, oy = self._scale_offset()
        lw, lh = self.layout_.width * s, self.layout_.height * s
        p.setPen(Qt.NoPen)
        p.setBrush(BODY)
        p.drawRoundedRect(QRectF(ox - 12, oy - 12, lw + 24, lh + 24), 14, 14)
        if self.image is not None and not self.image.isNull():
            p.drawImage(QRectF(ox, oy, lw, lh), self.image)
        # quiet numbers on empty keys so you can tell which is which
        f = QFont()
        f.setPixelSize(max(9, int(16 * s)))
        p.setFont(f)
        for i in self.empty_keys:
            r = self._rect_for("key", i)
            if r:
                p.setPen(QColor(110, 110, 105))
                p.drawText(r, Qt.AlignCenter, str(i + 1))
        p.setBrush(Qt.NoBrush)
        if self.hover and self.hover != self.selection:
            r = self._rect_for(*self.hover)
            if r:
                p.setPen(QPen(QColor(200, 200, 195, 150), 1.5))
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
        for pos in self.flash:
            r = self._rect_for("key", pos)
            if r:
                p.setPen(QPen(PRESS, 3))
                p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 4, 4)
        r = self._rect_for(*self.selection)
        if r:
            p.setPen(QPen(SELECT, 3.5))
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 5, 5)
        p.end()

    # ---- mouse
    def mouseMoveEvent(self, e):
        h = self.hit(e.position())
        if h != self.hover:
            self.hover = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.update()
        if (self._drag_from is not None and e.buttons() & Qt.LeftButton and self._press_pos is not None
                and (e.position() - self._press_pos).manhattanLength() > 12):
            src = self._drag_from
            self._drag_from = None
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData("application/x-mk10-key", str(src).encode())
            drag.setMimeData(mime)
            r = self._rect_for("key", src)
            if self.image is not None and r is not None:
                lr = self.layout_.keys[src]
                pm = self.image.copy(lr.x, lr.y, lr.w, lr.h).scaled(int(r.width()), int(r.height()))
                drag.setPixmap(QPixmap.fromImage(pm))
                drag.setHotSpot(QPoint(int(r.width() / 2), int(r.height() / 2)))
            drag.exec(Qt.MoveAction)

    def leaveEvent(self, _):
        self.hover = None
        self.update()

    def mousePressEvent(self, e):
        h = self.hit(e.position())
        if not h:
            return
        self.selection = h
        self.selected.emit(*h)
        self.update()
        if e.button() == Qt.LeftButton and h[0] == "key":
            self._drag_from = h[1]
            self._press_pos = e.position()
        elif e.button() == Qt.RightButton:
            self._context_menu(e.globalPosition().toPoint(), *h)

    def mouseReleaseEvent(self, e):
        self._drag_from = None
        self._press_pos = None

    def _context_menu(self, at, kind, index):
        m = QMenu(self)
        if kind == "key":
            for name, label in (("test", "Run this key's actions"), (None, None), ("copy", "Copy"),
                                ("paste", "Paste"), ("clear", "Clear")):
                if name is None:
                    m.addSeparator()
                else:
                    m.addAction(label).setData(name)
        else:
            m.addAction("Use this screen on every page").setData("screen_all")
        a = m.exec(at)
        if a and a.data():
            self.menu_action.emit(a.data(), kind, index)

    def keyPressEvent(self, e):
        kind, index = self.selection
        if kind == "key" and e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.menu_action.emit("clear", kind, index)
            return
        if kind == "key" and e.matches(QKeySequence.Copy):
            self.menu_action.emit("copy", kind, index)
            return
        if kind == "key" and e.matches(QKeySequence.Paste):
            self.menu_action.emit("paste", kind, index)
            return
        moves = {Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1), Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0)}
        if e.key() in moves:
            lay = self.layout_
            if kind == "screen":
                if e.key() == Qt.Key_Left and lay.keys:
                    self._select("key", lay.cols - 1)
                return
            row, col = divmod(index, lay.cols)
            dr, dc = moves[e.key()]
            row, col = row + dr, col + dc
            if col >= lay.cols and lay.screens:
                self._select("screen", 0)
            else:
                pos = lay.position(max(0, min(lay.rows - 1, row)), max(0, min(lay.cols - 1, col)))
                if pos is not None:
                    self._select("key", pos)
            return
        super().keyPressEvent(e)

    def _select(self, kind, index):
        self.selection = (kind, index)
        self.selected.emit(kind, index)
        self.update()

    # ---- drops
    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasFormat("application/x-mk10-key") or (md.hasUrls() and any(
                u.toLocalFile().lower().endswith(IMAGE_EXT) for u in md.urls())):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        h = self.hit(e.position())
        md = e.mimeData()
        if h and (h[0] == "key" or not md.hasFormat("application/x-mk10-key")):
            self.hover = h
            self.update()
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        h = self.hit(e.position())
        if not h:
            return
        md = e.mimeData()
        if md.hasFormat("application/x-mk10-key"):
            src = int(bytes(md.data("application/x-mk10-key")).decode())
            if h[0] == "key" and h[1] != src:
                self.swap.emit(src, h[1])
                self._select("key", h[1])
            e.acceptProposedAction()
            return
        for u in md.urls():
            path = u.toLocalFile()
            if path.lower().endswith(IMAGE_EXT):
                self.file_dropped.emit(h[0], h[1], path)
                self._select(*h)
                e.acceptProposedAction()
                return

