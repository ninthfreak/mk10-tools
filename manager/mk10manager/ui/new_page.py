"""The window that opens when you add a page: start blank, copy this one, or take a ready-made template."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from .. import integrations
from .. import templates as templates_mod

COPY = "__copy__"
PREVIEW_W = 430


class NewPageDialog(QDialog):
    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.ctl = ctl
        self.setWindowTitle("New page")
        self.resize(760, 520)
        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        outer.addLayout(body, 1)

        self.list = QListWidget()
        self.list.setMaximumWidth(230)
        hidden = integrations.hidden(ctl.config.settings, "templates")
        for t in templates_mod.TEMPLATES:
            if t.id in hidden:
                continue
            item = QListWidgetItem(t.name)
            item.setData(Qt.UserRole, t.id)
            self.list.addItem(item)
        copy_item = QListWidgetItem("Copy of “%s”" % ctl.page["name"])
        copy_item.setData(Qt.UserRole, COPY)
        self.list.insertItem(1, copy_item)
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        if hidden:
            more = QLabel("More templates come with integrations: switch them on in the Integrations tab.")
            more.setWordWrap(True)
            more.setMaximumWidth(230)
            more.setStyleSheet("color: gray;")
            left.addWidget(more)
        body.addLayout(left)

        side = QVBoxLayout()
        body.addLayout(side, 1)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(160)
        self.preview.setStyleSheet("background: #1E1E1C; border-radius: 8px;")
        side.addWidget(self.preview)
        self.description = QLabel()
        self.description.setWordWrap(True)
        side.addWidget(self.description)
        self.setup = QLabel()
        self.setup.setWordWrap(True)
        self.setup.setStyleSheet("color: #BA7517;")
        side.addWidget(self.setup)
        self.rule = QCheckBox()
        self.rule.setChecked(True)
        side.addWidget(self.rule)
        side.addStretch(1)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.name = QLineEdit()
        name_row.addWidget(self.name, 1)
        side.addLayout(name_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Add page")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)

        self.list.currentRowChanged.connect(self._pick)
        self.name.textChanged.connect(self._check_name)
        self.list.setCurrentRow(0)

    # ---- what was chosen
    def choice(self) -> str:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else "blank"

    def page_name(self) -> str:
        return self.name.text().strip()

    def wants_rule(self) -> bool:
        return self.rule.isVisible() and self.rule.isChecked()

    # ---- wiring
    def _pick(self, _row):
        choice = self.choice()
        if choice == COPY:
            self._show_copy()
        else:
            self._show_template(templates_mod.get(choice))

    def _show_copy(self):
        self.description.setText("A copy of this page's small screen and settings, with all ten keys empty.")
        self.setup.setText("")
        self.setup.setVisible(False)
        self.rule.setVisible(False)
        self.name.setText(self.ctl.config.unique_page_name(self.ctl.page["name"] + " copy"))
        page = dict(self.ctl.page)
        page["keys"] = [None] * len(self.ctl.page.get("keys") or [])
        self._draw(page)

    def _show_template(self, template):
        if template is None:
            return
        built = template.build()
        self.description.setText(template.description)
        self.setup.setText("\n".join("• " + s for s in template.setup))
        self.setup.setVisible(bool(template.setup))
        self.rule.setVisible(bool(built.rule))
        if built.rule:
            self.rule.setText(built.rule_text or "Also add a context rule for this page")
        self.name.setText(self.ctl.config.unique_page_name(
            "Page" if template.id == "blank" else template.name))
        self._draw({"name": template.name, "keys": built.keys, "screen": built.screen})

    def _draw(self, page):
        """Draw the page exactly as the device would, so the choice is obvious."""
        layout = self.ctl.layout
        try:
            img = self.ctl.renderer.compose(layout, page, self.ctl.context())
        except Exception:
            self.preview.clear()
            return
        height = int(PREVIEW_W * layout.height / max(1, layout.width))
        self.preview.setPixmap(QPixmap.fromImage(img).scaled(PREVIEW_W, height, Qt.KeepAspectRatio,
                                                             Qt.SmoothTransformation))

    def _check_name(self, text):
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(text.strip()))
