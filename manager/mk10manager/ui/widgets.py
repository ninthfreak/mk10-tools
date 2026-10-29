"""Small reusable editor widgets, and a form builder driven by schema.Field lists."""

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..schema import Field, fill_defaults, visible
from ..system import inputs

_dialogs_open = 0


class dialog_open:
    """Wrap every dialog the editors open. While one is open, the main window holds back editor reloads, so a page
    switch (from a key, a rule or a script) can't pull the form out from under the dialog."""

    def __enter__(self):
        global _dialogs_open
        _dialogs_open += 1

    def __exit__(self, *exc):
        global _dialogs_open
        _dialogs_open -= 1
        return False


def dialogs_open() -> bool:
    return _dialogs_open > 0


PALETTE = ["#185FA5", "#378ADD", "#85B7EB", "#0C447C", "#854F0B", "#EF9F27", "#FAC775", "#633806",
           "#000000", "#2C2C2A", "#5F5E5A", "#888780", "#D3D1C7", "#F1EFE8", "#FFFFFF", "#534AB7"]
_palette_set = False


def swatch_icon(color: str, size: int = 16) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(128, 128, 128))
    p.setBrush(QColor(color))
    p.drawRoundedRect(0.5, 0.5, size - 1, size - 1, 3, 3)
    p.end()
    return QIcon(pm)


class ColorButton(QToolButton):
    colorChanged = Signal(str)

    def __init__(self, color="#000000", parent=None):
        super().__init__(parent)
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setMinimumWidth(110)
        self._color = color
        self.clicked.connect(self._pick)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = QColor(color).name().upper() if QColor(color).isValid() else "#000000"
        self.setIcon(swatch_icon(self._color, 18))
        self.setText(self._color)

    def _pick(self):
        global _palette_set
        if not _palette_set:
            for i, c in enumerate(PALETTE):
                QColorDialog.setCustomColor(i, QColor(c))
            _palette_set = True
        with dialog_open():
            c = QColorDialog.getColor(QColor(self._color), self.window(), "Choose a colour")
        if c.isValid():
            self.set_color(c.name())
            self.colorChanged.emit(self._color)


class HotkeyRecordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Record a shortcut")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Press the shortcut now. Some system shortcuts can't be recorded; type those instead."))
        self.edit = QKeySequenceEdit()
        try:
            self.edit.setMaximumSequenceLength(1)
        except AttributeError:
            pass
        lay.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.edit.setFocus()

    def text(self) -> str:
        return self.edit.keySequence().toString(QKeySequence.PortableText).split(",")[0].strip()


class HotkeyEdit(QWidget):
    textChanged = Signal(str)

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.line = QLineEdit(text)
        self.line.setPlaceholderText("Ctrl+Shift+M")
        rec = QPushButton("Record")
        rec.clicked.connect(self._record)
        row.addWidget(self.line, 1)
        row.addWidget(rec)
        lay.addLayout(row)
        self.problem = QLabel()
        self.problem.setStyleSheet("color: #BA7517;")
        self.problem.setWordWrap(True)
        lay.addWidget(self.problem)
        self.line.textChanged.connect(self._changed)
        self._check(text)

    def _record(self):
        d = HotkeyRecordDialog(self.window())
        with dialog_open():
            ok = d.exec()
        if ok and d.text():
            self.line.setText(d.text())

    def _check(self, text):
        if not text:
            self.problem.setText("")
            self.problem.hide()
            return
        try:
            inputs.parse_hotkey(text)
            self.problem.hide()
        except ValueError as e:
            self.problem.setText(str(e))
            self.problem.show()

    def _changed(self, text):
        self._check(text)
        self.textChanged.emit(text)


class FileEdit(QWidget):
    textChanged = Signal(str)

    def __init__(self, text="", file_filter="", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.line = QLineEdit(text)
        self.filter = file_filter
        b = QPushButton("Browse")
        b.clicked.connect(self._browse)
        lay.addWidget(self.line, 1)
        lay.addWidget(b)
        self.line.textChanged.connect(self.textChanged.emit)

    def _browse(self):
        with dialog_open():
            path, _ = QFileDialog.getOpenFileName(self.window(), "Choose a file", self.line.text(), self.filter)
        if path:
            self.line.setText(path)


class IconEdit(QWidget):
    """Pick one of the built-in icons. Stores the icon's name."""

    textChanged = Signal(str)

    def __init__(self, name="", icons=None, color="#FFFFFF", parent=None):
        super().__init__(parent)
        self.icons = icons
        self.color = color
        self._name = name or ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.preview = QLabel()
        self.preview.setFixedSize(32, 32)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background: #2C2C2A; border-radius: 5px; color: gray;")
        self.button = QPushButton()
        self.button.clicked.connect(self._pick)
        lay.addWidget(self.preview)
        lay.addWidget(self.button, 1)
        self._refresh()

    def name(self) -> str:
        return self._name

    def set_name(self, name: str):
        self._name = name or ""
        self._refresh()

    def set_color(self, color: str):
        self.color = color or "#FFFFFF"
        self._refresh()

    def _refresh(self):
        self.button.setText(self._name or "Choose an icon")
        img = self.icons.builtin_image(self._name, 28, self.color) if (self.icons and self._name) else None
        if img is not None and not img.isNull():
            self.preview.setPixmap(QPixmap.fromImage(img))
        else:
            self.preview.clear()
            self.preview.setText("none")

    def _pick(self):
        if self.icons is None:
            return
        from .icon_picker import IconPicker            # imported here: icon_picker uses this module
        d = IconPicker(self.icons, self._name, self.color, "#2C2C2A", self.window())
        with dialog_open():
            ok = d.exec()
        if ok and d.selected_name():
            self._name = d.selected_name()
            self._refresh()
            self.textChanged.emit(self._name)


class PlainEdit(QPlainTextEdit):
    def __init__(self, text="", lines=3, parent=None):
        super().__init__(parent)
        self.setPlainText(text or "")
        self.setTabChangesFocus(True)
        h = self.fontMetrics().lineSpacing() * lines + 14
        self.setFixedHeight(h)


def help_button(text: str) -> QToolButton:
    b = QToolButton()
    b.setText("?")
    b.setToolTip(text)
    b.setAutoRaise(True)
    b.setFixedWidth(22)
    b.clicked.connect(lambda: _show_help(b, text))
    return b


def _show_help(btn, text):
    from PySide6.QtWidgets import QToolTip
    QToolTip.showText(btn.mapToGlobal(btn.rect().bottomLeft()), text, btn)


class Form(QWidget):
    """Builds a form for a list of Fields, editing `values` (a dict) in place. Emits changed() after edits.

    providers: dict with optional callables 'pages' -> [(id, name)], 'apps' -> [str], 'variables' -> [str],
    'help' -> the help text to show for a field's (it leaves out switched-off integrations' placeholders),
    plus 'icons' (an IconLibrary) for icon fields."""

    changed = Signal(str)

    def __init__(self, fields: list[Field], values: dict, providers: dict | None = None, parent=None):
        super().__init__(parent)
        self.fields = fields
        self.values = values
        filled = fill_defaults(values, fields)
        for k, v in filled.items():
            values.setdefault(k, v)
        self.providers = providers or {}
        self.layout_ = QFormLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.layout_.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.rows = {}
        for f in fields:
            w = self._make(f)
            container = QWidget()
            h = QHBoxLayout(container)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(w, 1)
            if f.help and f.kind not in ("bool",):
                h.addWidget(help_button(self.providers.get("help", lambda text: text)(f.help)))
            label = QLabel(f.label) if f.kind != "bool" else QLabel("")
            self.layout_.addRow(label, container)
            self.rows[f.key] = (label, container)
        self._update_visibility()

    def _set(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        if any(f.show_if and f.show_if[0] == key for f in self.fields):
            self._update_visibility()
        if key == "icon_color":
            for f in self.fields:
                if f.kind == "icon":
                    w = self.rows[f.key][1].layout().itemAt(0).widget()
                    if isinstance(w, IconEdit):
                        w.set_color(value)
        self.changed.emit(key)

    def _update_visibility(self):
        for f in self.fields:
            label, container = self.rows[f.key]
            show = visible(f, self.values)
            label.setVisible(show)
            container.setVisible(show)

    def _make(self, f: Field) -> QWidget:
        v = self.values.get(f.key, f.default)
        k = f.key
        if f.kind == "bool":
            w = QCheckBox(f.label)
            w.setChecked(bool(v))
            if f.help:
                w.setToolTip(f.help)
            w.toggled.connect(lambda on, k=k: self._set(k, bool(on)))
            return w
        if f.kind == "int":
            w = QSpinBox()
            w.setRange(f.minimum, f.maximum)
            w.setValue(int(v if v is not None else f.default or 0))
            w.valueChanged.connect(lambda n, k=k: self._set(k, int(n)))
            return w
        if f.kind == "color":
            w = ColorButton(v or "#000000")
            w.colorChanged.connect(lambda c, k=k: self._set(k, c))
            return w
        if f.kind == "choice":
            w = QComboBox()
            for val, lab in f.choices:
                w.addItem(lab, val)
            i = w.findData(v)
            w.setCurrentIndex(max(0, i))
            w.currentIndexChanged.connect(lambda i, w=w, k=k: self._set(k, w.itemData(i)))
            return w
        if f.kind == "page":
            w = QComboBox()
            pages = self.providers.get("pages", lambda: [])()
            w.addItem("(choose a page)", None)
            for pid, name in pages:
                w.addItem(name, pid)
            i = w.findData(v)
            w.setCurrentIndex(max(0, i))
            w.currentIndexChanged.connect(lambda i, w=w, k=k: self._set(k, w.itemData(i)))
            return w
        if f.kind in ("app", "variable"):
            w = QComboBox()
            w.setEditable(True)
            items = self.providers.get("apps" if f.kind == "app" else "variables", lambda: [])()
            w.addItems(items)
            w.setCurrentText(str(v or ""))
            w.currentTextChanged.connect(lambda t, k=k: self._set(k, t.strip()))
            return w
        if f.kind == "icon":
            w = IconEdit(v or "", self.providers.get("icons"), self.values.get("icon_color") or "#FFFFFF")
            w.textChanged.connect(lambda t, k=k: self._set(k, t))
            return w
        if f.kind == "hotkey":
            w = HotkeyEdit(v or "")
            w.textChanged.connect(lambda t, k=k: self._set(k, t.strip()))
            return w
        if f.kind == "file":
            w = FileEdit(v or "", f.file_filter)
            w.textChanged.connect(lambda t, k=k: self._set(k, t.strip()))
            return w
        if f.kind == "multiline":
            w = PlainEdit(v or "", 3)
            w.textChanged.connect(lambda w=w, k=k: self._set(k, w.toPlainText()))
            return w
        w = QLineEdit(str(v or ""))
        if f.kind == "time":
            w.setInputMask("99:99")
        w.textChanged.connect(lambda t, k=k: self._set(k, t))
        return w
