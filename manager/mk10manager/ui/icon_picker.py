"""Pick one of the built-in icons, with search."""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from ..render.icons import IconLibrary
from .widgets import ColorButton

TILE = 56


class IconPicker(QDialog):
    def __init__(self, icons: IconLibrary, name: str = "", color: str = "#FFFFFF", bg: str = "#2C2C2A", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose an icon")
        self.resize(640, 520)
        self.icons, self.bg = icons, bg
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search icons, for example: microphone, play, light, terminal")
        self.search.setClearButtonEnabled(True)
        self.color = ColorButton(color)
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Colour"))
        top.addWidget(self.color)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(TILE, TILE))
        self.list.setGridSize(QSize(TILE + 40, TILE + 30))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        lay.addWidget(self.list, 1)
        self.count = QLabel()
        self.count.setStyleSheet("color: gray;")
        lay.addWidget(self.count)
        credit = QLabel("Icons from Tabler Icons (MIT licence).")
        credit.setStyleSheet("color: gray;")
        lay.addWidget(credit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._fill)
        self.search.textChanged.connect(lambda _: self._timer.start())
        self.color.colorChanged.connect(lambda _: self._fill())
        self._initial = name
        self.search.setText(name.split("-")[0] if name else "")
        self._fill()
        self.search.setFocus()

    def _tile(self, name: str) -> QIcon:
        pm = QPixmap(TILE, TILE)
        pm.fill(QColor(self.bg))
        img = self.icons.builtin_image(name, TILE - 16, self.color.color())
        if img is not None:
            p = QPainter(pm)
            p.drawImage(8, 8, img)
            p.end()
        return QIcon(pm)

    def _fill(self):
        current = self.selected_name() or self._initial
        names = self.icons.search(self.search.text(), limit=300)
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        for n in names:
            it = QListWidgetItem(self._tile(n), n)
            it.setData(Qt.UserRole, n)
            it.setToolTip(n)
            self.list.addItem(it)
            if n == current:
                self.list.setCurrentItem(it)
        self.list.setUpdatesEnabled(True)
        total = len(self.icons.icons)
        self.count.setText("%d shown of %d. Type to narrow it down." % (len(names), total) if len(names) >= 300
                           else "%d found." % len(names))

    def selected_name(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None
