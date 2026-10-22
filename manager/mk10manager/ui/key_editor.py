"""Editor for one key: how it looks and what it does."""

import copy

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QPushButton, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from ..actions import ACTION_TYPES, new_action, summary
from ..config import new_key, store_image
from ..render.template import HELP
from .icon_picker import IconPicker
from .widgets import ColorButton, Form, PlainEdit, dialog_open, help_button


class KeyEditor(QWidget):
    changed = Signal()
    test_requested = Signal(int)

    def __init__(self, ctl, providers: dict, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.providers = providers
        self.page = None
        self.pos = 0
        self.key = None
        self._loading = False
        self.step_form = None

        outer = QVBoxLayout(self)
        head = QHBoxLayout()
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        head.addWidget(self.title, 1)
        self.test_btn = QPushButton("Run now")
        self.test_btn.setToolTip("Run this key's actions now, as if you pressed it")
        self.test_btn.clicked.connect(lambda: self.test_requested.emit(self.pos))
        head.addWidget(self.test_btn)
        outer.addLayout(head)

        # empty state
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setContentsMargins(0, 8, 0, 8)
        el.addWidget(QLabel("This key is empty."))
        setup = QPushButton("Set up this key")
        setup.clicked.connect(self._create)
        el.addWidget(setup, 0, Qt.AlignLeft)
        el.addStretch(1)
        outer.addWidget(self.empty)

        # look
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        look = QGroupBox("Look")
        form = QFormLayout(look)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.label = PlainEdit("", 2)
        lrow = QHBoxLayout()
        lrow.addWidget(self.label, 1)
        lrow.addWidget(help_button("Labels can use placeholders too, like {var:mode}.\n\n" + HELP))
        form.addRow("Label", self._wrap(lrow))
        self.label_pos = QComboBox()
        for v, l in (("bottom", "Bottom"), ("top", "Top"), ("middle", "Middle")):
            self.label_pos.addItem(l, v)
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 72)
        self.bold = QCheckBox("Bold")
        row = QHBoxLayout()
        row.addWidget(self.label_pos)
        row.addWidget(QLabel("Size"))
        row.addWidget(self.font_size)
        row.addWidget(self.bold)
        row.addStretch(1)
        form.addRow("Label position", self._wrap(row))
        self.text_color = ColorButton()
        self.bg = ColorButton()
        crow = QHBoxLayout()
        crow.addWidget(self.text_color)
        crow.addWidget(QLabel("Background"))
        crow.addWidget(self.bg)
        crow.addStretch(1)
        form.addRow("Text colour", self._wrap(crow))
        irow = QHBoxLayout()
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(44, 44)
        self.icon_preview.setAlignment(Qt.AlignCenter)
        self.icon_preview.setStyleSheet("background: #2C2C2A; border-radius: 6px;")
        b_icon = QPushButton("Choose icon")
        b_file = QPushButton("Picture file")
        self.b_remove = QPushButton("Remove")
        b_icon.clicked.connect(self._choose_icon)
        b_file.clicked.connect(self._choose_file)
        self.b_remove.clicked.connect(self._remove_icon)
        for w in (self.icon_preview, b_icon, b_file, self.b_remove):
            irow.addWidget(w)
        irow.addStretch(1)
        form.addRow("Icon", self._wrap(irow))
        self.icon_color = ColorButton()
        self.icon_size = QSlider(Qt.Horizontal)
        self.icon_size.setRange(20, 100)
        self.icon_size_label = QLabel()
        srow = QHBoxLayout()
        self.icon_color_label = QLabel("Colour")
        srow.addWidget(self.icon_color_label)
        srow.addWidget(self.icon_color)
        srow.addWidget(QLabel("Size"))
        srow.addWidget(self.icon_size, 1)
        srow.addWidget(self.icon_size_label)
        self.icon_opts = self._wrap(srow)
        form.addRow("", self.icon_opts)
        bl.addWidget(look)

        # actions
        acts = QGroupBox("When pressed")
        al = QVBoxLayout(acts)
        self.steps = QListWidget()
        self.steps.setMaximumHeight(130)
        al.addWidget(self.steps)
        brow = QHBoxLayout()
        add = QToolButton()
        add.setText("Add step")
        add.setPopupMode(QToolButton.InstantPopup)
        add.setToolButtonStyle(Qt.ToolButtonTextOnly)
        menu = QMenu(add)
        for kind, (label, _fields) in ACTION_TYPES.items():
            menu.addAction(label).setData(kind)
        menu.triggered.connect(lambda a: self._add_step(a.data()))
        add.setMenu(menu)
        self.b_del = QPushButton("Remove step")
        self.b_up = QPushButton("Up")
        self.b_down = QPushButton("Down")
        self.b_del.clicked.connect(self._del_step)
        self.b_up.clicked.connect(lambda: self._move_step(-1))
        self.b_down.clicked.connect(lambda: self._move_step(1))
        for w in (add, self.b_del, self.b_up, self.b_down):
            brow.addWidget(w)
        brow.addStretch(1)
        al.addLayout(brow)
        self.step_box = QGroupBox("Step")
        self.step_layout = QVBoxLayout(self.step_box)
        al.addWidget(self.step_box)
        bl.addWidget(acts)
        clear = QPushButton("Clear this key")
        clear.clicked.connect(self._clear)
        bl.addWidget(clear, 0, Qt.AlignLeft)
        bl.addStretch(1)
        outer.addWidget(self.body)
        outer.addStretch(1)

        self.label.textChanged.connect(lambda: self._set("label", self.label.toPlainText()))
        self.label_pos.currentIndexChanged.connect(lambda i: self._set("label_pos", self.label_pos.itemData(i)))
        self.font_size.valueChanged.connect(lambda v: self._set("font_size", v))
        self.bold.toggled.connect(lambda on: self._set("bold", bool(on)))
        self.text_color.colorChanged.connect(lambda c: self._set("text_color", c))
        self.bg.colorChanged.connect(lambda c: self._set("bg", c))
        self.icon_color.colorChanged.connect(self._icon_color)
        self.icon_size.valueChanged.connect(self._icon_size)
        self.steps.currentRowChanged.connect(self._show_step)

    @staticmethod
    def _wrap(layout):
        w = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        w.setLayout(layout)
        return w

    # ---- loading
    def load(self, page: dict, pos: int):
        self.page, self.pos = page, pos
        keys = page["keys"]
        self.key = keys[pos] if pos < len(keys) else None
        self.title.setText("Key %d" % (pos + 1))
        self._loading = True
        try:
            self.empty.setVisible(self.key is None)
            self.body.setVisible(self.key is not None)
            self.test_btn.setEnabled(bool(self.key and self.key.get("actions")))
            if self.key is None:
                return
            k = self.key
            self.label.setPlainText(k.get("label") or "")
            self.label_pos.setCurrentIndex(max(0, self.label_pos.findData(k.get("label_pos") or "bottom")))
            self.font_size.setValue(int(k.get("font_size") or 20))
            self.bold.setChecked(bool(k.get("bold")))
            self.text_color.set_color(k.get("text_color") or "#FFFFFF")
            self.bg.set_color(k.get("bg") or "#000000")
            self.icon_size.setValue(int(k.get("icon_size") or 62))
            self.icon_size_label.setText("%d%%" % self.icon_size.value())
            self._refresh_icon()
            self._fill_steps()
        finally:
            self._loading = False

    def _refresh_icon(self):
        icon = self.key.get("icon") if self.key else None
        self.b_remove.setEnabled(bool(icon))
        self.icon_opts.setVisible(bool(icon))
        builtin = bool(icon and icon.get("kind") == "builtin")
        self.icon_color.setVisible(builtin)
        self.icon_color_label.setVisible(builtin)
        if builtin:
            self.icon_color.set_color(icon.get("color") or "#FFFFFF")
        img = self.ctl.renderer.icons.icon_image(icon, 36) if icon else None
        if img is not None and not img.isNull():
            self.icon_preview.setPixmap(QPixmap.fromImage(img).scaled(36, 36, Qt.KeepAspectRatio,
                                                                      Qt.SmoothTransformation))
        else:
            self.icon_preview.clear()
            self.icon_preview.setText("none")
        self.icon_preview.setStyleSheet("background: %s; border-radius: 6px; color: gray;"
                                        % ((self.key or {}).get("bg") or "#2C2C2A"))

    def _fill_steps(self):
        row = self.steps.currentRow()
        self.steps.blockSignals(True)
        self.steps.clear()
        for a in self.key.get("actions") or []:
            self.steps.addItem(QListWidgetItem(summary(a, self.ctl.page_name)))
        self.steps.blockSignals(False)
        n = self.steps.count()
        if n:
            self.steps.setCurrentRow(min(max(row, 0), n - 1))
        self._show_step(self.steps.currentRow())

    # ---- edits
    def _set(self, field, value):
        if self._loading or self.key is None:
            return
        if self.key.get(field) == value:
            return
        self.key[field] = value
        if field == "bg":
            self._refresh_icon()
        self.changed.emit()

    def _create(self):
        self.page["keys"][self.pos] = new_key("")
        self.load(self.page, self.pos)
        self.changed.emit()

    def _clear(self):
        self.page["keys"][self.pos] = None
        self.load(self.page, self.pos)
        self.changed.emit()

    def _choose_icon(self):
        key = self.key            # the key being edited when the dialog opened
        if key is None:
            return
        icon = key.get("icon") or {}
        d = IconPicker(self.ctl.renderer.icons, icon.get("name", "") if icon.get("kind") == "builtin" else "",
                       icon.get("color") or key.get("text_color") or "#FFFFFF", key.get("bg") or "#2C2C2A",
                       self.window())
        with dialog_open():
            ok = d.exec()
        if ok and d.selected_name():
            key["icon"] = {"kind": "builtin", "name": d.selected_name(), "color": d.color.color()}
            if key is self.key:
                self._refresh_icon()
            self.changed.emit()

    def _choose_file(self):
        page, pos = self.page, self.pos
        with dialog_open():
            path, _ = QFileDialog.getOpenFileName(self.window(), "Choose a picture", "",
                                                  "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg)")
        if path:
            self.set_picture(path, page, pos)

    def set_picture(self, path: str, page: dict | None = None, pos: int | None = None):
        page = page if page is not None else self.page
        pos = pos if pos is not None else self.pos
        if page["keys"][pos] is None:
            page["keys"][pos] = new_key("")
        page["keys"][pos]["icon"] = {"kind": "file", "path": store_image(path)}
        if page is self.page and pos == self.pos:
            self.load(self.page, self.pos)
        self.changed.emit()

    def _remove_icon(self):
        self.key["icon"] = None
        self._refresh_icon()
        self.changed.emit()

    def _icon_color(self, c):
        icon = self.key.get("icon") if self.key else None
        if icon and icon.get("kind") == "builtin" and not self._loading:
            icon["color"] = c
            self._refresh_icon()
            self.changed.emit()

    def _icon_size(self, v):
        self.icon_size_label.setText("%d%%" % v)
        self._set("icon_size", int(v))

    # ---- steps
    def _add_step(self, kind):
        a = new_action(kind)
        if kind == "page" and self.page:
            others = [p for p in self.ctl.config.pages if p["id"] != self.page["id"]]
            a["page"] = others[0]["id"] if others else None
        self.key.setdefault("actions", []).append(a)
        self._fill_steps()
        self.steps.setCurrentRow(self.steps.count() - 1)
        self.test_btn.setEnabled(True)
        self.changed.emit()

    def _del_step(self):
        i = self.steps.currentRow()
        acts = self.key.get("actions") or []
        if 0 <= i < len(acts):
            del acts[i]
            self._fill_steps()
            self.test_btn.setEnabled(bool(acts))
            self.changed.emit()

    def _move_step(self, d):
        i = self.steps.currentRow()
        acts = self.key.get("actions") or []
        j = i + d
        if 0 <= i < len(acts) and 0 <= j < len(acts):
            acts[i], acts[j] = acts[j], acts[i]
            self.steps.blockSignals(True)
            self._fill_steps()
            self.steps.blockSignals(False)
            self.steps.setCurrentRow(j)
            self.changed.emit()

    def _show_step(self, row):
        if self.step_form is not None:
            self.step_form.setParent(None)
            self.step_form.deleteLater()
            self.step_form = None
        acts = (self.key or {}).get("actions") or []
        has = 0 <= row < len(acts)
        self.step_box.setVisible(has)
        self.b_del.setEnabled(has)
        self.b_up.setEnabled(has and row > 0)
        self.b_down.setEnabled(has and row < len(acts) - 1)
        if not has:
            return
        a = acts[row]
        label, fields = ACTION_TYPES.get(a.get("type"), ("Unknown", []))
        self.step_box.setTitle("Step %d: %s" % (row + 1, label))
        self.step_form = Form(fields, a, self.providers)
        self.step_form.changed.connect(lambda _k, row=row: self._step_changed(row))
        self.step_layout.addWidget(self.step_form)

    def _step_changed(self, row):
        acts = self.key.get("actions") or []
        if 0 <= row < len(acts):
            it = self.steps.item(row)
            if it:
                it.setText(summary(acts[row], self.ctl.page_name))
        self.changed.emit()
