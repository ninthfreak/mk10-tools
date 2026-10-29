"""The editor window: page tabs, live preview, inspector, and the bottom tabs. Plus the tray icon."""

import copy

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMenu,
                               QMessageBox, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QSystemTrayIcon,
                               QTabBar, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)

from ..config import APP_NAME, store_image
from .. import integrations
from .. import templates as templates_mod
from ..render import template as text_template
from .integrations_panel import IntegrationsPanel
from .key_editor import KeyEditor
from .new_page import COPY, NewPageDialog
from .panels import DevicePanel, RulesPanel, VariablesPanel
from .preview import DevicePreview
from .screen_editor import ScreenEditor
from .widgets import dialog_open, dialogs_open


class MainWindow(QMainWindow):
    def __init__(self, ctl, icon: QIcon):
        super().__init__()
        self.ctl = ctl
        self.icon = icon
        self.clipboard = None
        self.sel = ("key", 0)
        self._rebuilding = False
        self._quitting = False
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icon)
        self.resize(1200, 780)

        providers = {
            "pages": lambda: [(p["id"], p["name"]) for p in self.ctl.config.pages],
            "apps": lambda: list(self.ctl.config.data.get("seen_apps", [])),
            "variables": lambda: sorted(set(self.ctl.config.variables) |
                                        {r.get("name") for r in self.ctl.config.rules
                                         if r.get("when") == "variable" and r.get("name")}),
            "icons": ctl.renderer.icons,
            # the parts of switched-off integrations, for the editors to leave out
            "hidden": lambda kind: integrations.hidden(self.ctl.config.settings, kind),
            "off_note": lambda kind, item: integrations.off_note(self.ctl.config.settings, kind, item),
            "help": lambda text: text.replace(text_template.HELP, text_template.placeholder_help(
                integrations.enabled(self.ctl.config.settings))),
        }

        # ---- toolbar: pages, undo, connection
        tb = QToolBar("Pages")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)
        tb.addWidget(QLabel("  Pages  "))
        self.tabs = QTabBar()
        self.tabs.setMovable(True)
        self.tabs.setExpanding(False)
        self.tabs.setDocumentMode(True)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.setToolTip("Click to switch pages. Drag to reorder. Right-click to rename, copy or delete.")
        tb.addWidget(self.tabs)
        add = QToolButton()
        add.setText("+ New page")
        add.setAutoRaise(True)
        add.clicked.connect(self._add_page)
        tb.addWidget(add)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.undo_act = QAction("Undo", self)
        self.undo_act.setShortcuts([QKeySequence.Undo])
        self.redo_act = QAction("Redo", self)
        self.redo_act.setShortcuts([QKeySequence.Redo, QKeySequence("Ctrl+Y")])
        self.undo_act.triggered.connect(self.ctl.undo)
        self.redo_act.triggered.connect(self.ctl.redo)
        self.undo_act.setEnabled(False)
        self.redo_act.setEnabled(False)
        tb.addAction(self.undo_act)
        tb.addAction(self.redo_act)
        self.conn = QLabel()
        self.conn.setContentsMargins(12, 0, 8, 0)
        tb.addWidget(self.conn)

        # ---- left: preview and bottom tabs
        self.preview = DevicePreview()
        self.preview.set_layout(ctl.layout)
        hint = QLabel("Click a key or the small screen to edit it. Drag keys to swap them, or drop a picture on "
                      "one. Right-click for more.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        self.bottom = QTabWidget()
        self.rules = RulesPanel(ctl)
        self.variables = VariablesPanel(ctl)
        self.device = DevicePanel(ctl)
        self.integrations = IntegrationsPanel(ctl)
        self.bottom.addTab(self.rules, "Context rules")
        self.bottom.addTab(self.variables, "Variables")
        self.bottom.addTab(self.integrations, "Integrations")
        self.bottom.addTab(self.device, "Device and app")
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 4, 8)
        top_split = QSplitter(Qt.Vertical)
        pv = QWidget()
        pl = QVBoxLayout(pv)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.preview, 1)
        pl.addWidget(hint)
        top_split.addWidget(pv)
        top_split.addWidget(self.bottom)
        top_split.setStretchFactor(0, 3)
        top_split.setStretchFactor(1, 2)
        ll.addWidget(top_split)

        # ---- right: inspector
        self.key_editor = KeyEditor(ctl, providers)
        self.screen_editor = ScreenEditor(ctl, providers)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.key_editor)
        self.stack.addWidget(self.screen_editor)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self.stack)
        scroll.setMinimumWidth(400)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(scroll)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([720, 460])
        self.split, self.top_split = split, top_split
        self.setCentralWidget(split)

        # ---- wiring
        self.preview.selected.connect(self._select)
        self.preview.swap.connect(self._swap)
        self.preview.file_dropped.connect(self._dropped)
        self.preview.menu_action.connect(self._menu_action)
        self.key_editor.changed.connect(self._key_edited)
        self.key_editor.test_requested.connect(self.ctl.test_press)
        self.screen_editor.changed.connect(lambda: self.ctl.edited())
        self.screen_editor.apply_all.connect(self._screen_all)
        self.rules.changed.connect(lambda: self.ctl.edited())
        self.device.changed.connect(lambda: self.ctl.edited(undoable=False))
        self.integrations.changed.connect(lambda: self.ctl.edited(undoable=False))
        ctl.integrations_changed.connect(self._integrations_changed)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabMoved.connect(self._tab_moved)
        self.tabs.tabBarDoubleClicked.connect(lambda i: self._rename(i))
        self.tabs.customContextMenuRequested.connect(self._tab_menu)

        ctl.preview_changed.connect(self.preview.set_image)
        ctl.page_changed.connect(self._page_changed)
        ctl.config_changed.connect(self._config_changed)
        ctl.settings_changed.connect(self.device.reload)
        ctl.notify.connect(lambda t: self.statusBar().showMessage(t, 8000))
        ctl.device_pressed.connect(self.preview.pressed)
        ctl.app_changed.connect(self.rules.set_app)
        ctl.connection_changed.connect(self._connection)
        ctl.undo_state.connect(self._undo_state)

        self._build_tabs()
        self._connection(ctl.device.is_connected, ctl.device.status)
        if ctl.last_image is not None:
            self.preview.set_image(ctl.last_image)
        self._select("key", 0)
        self._restore_geometry()
        self.tray = self._make_tray()

    # ---------------------------------------------------------------- pages
    def _build_tabs(self):
        self._rebuilding = True
        while self.tabs.count():
            self.tabs.removeTab(0)
        default_id = self.ctl.config.default_page()["id"]
        for p in self.ctl.config.pages:
            i = self.tabs.addTab(p["name"])
            self.tabs.setTabData(i, p["id"])
            self.tabs.setTabToolTip(i, "Default page" if p["id"] == default_id else "")
        self.tabs.setCurrentIndex(max(0, self.ctl.config.page_index(self.ctl.page_id)))
        self._rebuilding = False

    def _tab_changed(self, i):
        if self._rebuilding or i < 0:
            return
        pid = self.tabs.tabData(i)
        if pid and pid != self.ctl.page_id:
            self.ctl.set_page(pid)

    def _tab_moved(self, frm, to):
        pages = self.ctl.config.pages
        p = pages.pop(frm)
        pages.insert(to, p)
        self.ctl.edited()

    def _page_changed(self, pid):
        if dialogs_open():
            self._defer("page")
            return
        i = self.ctl.config.page_index(pid)
        if i >= 0 and self.tabs.currentIndex() != i:
            self._rebuilding = True
            self.tabs.setCurrentIndex(i)
            self._rebuilding = False
        self._load_editor()

    def _defer(self, what):
        """A dialog is open: reload once it closes instead of pulling the form out from under it."""
        self._deferred = getattr(self, "_deferred", set()) | {what}
        if not getattr(self, "_defer_timer", None):
            self._defer_timer = QTimer(self)
            self._defer_timer.setInterval(250)
            self._defer_timer.timeout.connect(self._run_deferred)
        self._defer_timer.start()

    def _run_deferred(self):
        if dialogs_open():
            return
        self._defer_timer.stop()
        todo, self._deferred = self._deferred, set()
        if "config" in todo:
            self._config_changed()
        elif "page" in todo:
            self._page_changed(self.ctl.page_id)

    def _add_page(self):
        d = NewPageDialog(self.ctl, self)
        with dialog_open():
            ok = d.exec()
        if not ok or not d.page_name():
            return
        choice, name, wants_rule = d.choice(), d.page_name(), d.wants_rule()
        if choice == COPY:
            p = self.ctl.config.add_page(name, copy_from=self.ctl.page, empty_keys=True)
        else:
            built = templates_mod.build(choice)
            p = self.ctl.config.add_page(name)
            p["keys"] = built.keys
            p["screen"] = built.screen
            for key, value in built.variables.items():
                self.ctl.config.variables.setdefault(key, value)
            if built.rule and wants_rule:
                rule = dict(built.rule)
                rule["page"] = p["id"]
                self.ctl.config.rules.append(rule)
                self.rules.reload()
        self._build_tabs()
        self.ctl.edited()
        self.ctl.set_page(p["id"])

    def _rename(self, i):
        if i < 0:
            return
        p = self.ctl.config.page(self.tabs.tabData(i))
        with dialog_open():
            name, ok = QInputDialog.getText(self, "Rename page", "Page name:", text=p["name"])
        if p is None or self.ctl.config.page(p["id"]) is not p:
            return
        if ok and name.strip() and name.strip() != p["name"]:
            p["name"] = name.strip()
            self.tabs.setTabText(i, p["name"])
            self.rules.reload()
            self._load_editor()
            self.ctl.edited()

    def _tab_menu(self, pos):
        i = self.tabs.tabAt(pos)
        if i < 0:
            return
        pid = self.tabs.tabData(i)
        m = QMenu(self)
        a_rename = m.addAction("Rename")
        a_dup = m.addAction("Duplicate")
        a_default = m.addAction("Make this the default page")
        m.addSeparator()
        a_del = m.addAction("Delete")
        a_del.setEnabled(len(self.ctl.config.pages) > 1)
        with dialog_open():
            a = m.exec(self.tabs.mapToGlobal(pos))
        if self.ctl.config.page(pid) is None:
            return
        if a == a_rename:
            self._rename(i)
        elif a == a_dup:
            src = self.ctl.config.page(pid)
            p = self.ctl.config.add_page(src["name"] + " copy", copy_from=src, empty_keys=False)
            self._build_tabs()
            self.ctl.edited()
            self.ctl.set_page(p["id"])
        elif a == a_default:
            self.ctl.config.settings["default_page"] = pid
            self._build_tabs()
            self.rules.reload()
            self.ctl.edited()
        elif a == a_del:
            p = self.ctl.config.page(pid)
            with dialog_open():
                answer = QMessageBox.question(self, "Delete page", "Delete the page “%s” and its keys? Rules that "
                                              "point to it are removed too. You can undo this." % p["name"])
            if answer != QMessageBox.Yes or self.ctl.config.page(pid) is None:
                return
            was_current = pid == self.ctl.page_id
            self.ctl.config.remove_page(pid)
            if was_current:
                self.ctl.set_page(self.ctl.config.default_page()["id"])
            self._build_tabs()
            self.rules.reload()
            self._load_editor()
            self.ctl.edited()

    # ---------------------------------------------------------------- selection and editors
    def _select(self, kind, index):
        self.sel = (kind, index)
        self.preview.set_selection(kind, index)
        self._load_editor()

    def _load_editor(self):
        page = self.ctl.page
        kind, index = self.sel
        if kind == "screen":
            self.screen_editor.load(page)
            self.stack.setCurrentWidget(self.screen_editor)
        else:
            self.key_editor.load(page, index)
            self.stack.setCurrentWidget(self.key_editor)
        self._update_empty()

    def _update_empty(self):
        keys = self.ctl.page.get("keys") or []
        self.preview.empty_keys = {i for i in range(len(self.ctl.layout.keys)) if i >= len(keys) or not keys[i]}
        self.preview.update()

    def _key_edited(self):
        self._update_empty()
        self.ctl.edited()

    def _swap(self, a, b):
        keys = self.ctl.page["keys"]
        keys[a], keys[b] = keys[b], keys[a]
        self.sel = ("key", b)
        self._load_editor()
        self.ctl.edited()

    def _dropped(self, kind, index, path):
        self.sel = (kind, index)
        if kind == "key":
            self.key_editor.load(self.ctl.page, index)
            self.key_editor.set_picture(path)
        else:
            self.ctl.page["screen"] = {"face": "image", "options": {"path": store_image(path), "fit": "contain"}}
            self.ctl.edited()
        self._load_editor()

    def _menu_action(self, name, kind, index):
        keys = self.ctl.page["keys"]
        if name == "test":
            self.ctl.test_press(index)
        elif name == "copy":
            self.clipboard = copy.deepcopy(keys[index])
            self.statusBar().showMessage("Key %d copied" % (index + 1), 3000)
        elif name == "paste" and self.clipboard is not None:
            keys[index] = copy.deepcopy(self.clipboard)
            self._load_editor()
            self.ctl.edited()
        elif name == "clear":
            keys[index] = None
            self._load_editor()
            self.ctl.edited()
        elif name == "screen_all":
            self._screen_all()

    def _screen_all(self):
        src_page = self.ctl.page
        with dialog_open():
            answer = QMessageBox.question(self, "Use on every page", "Give every page this same small-screen setup?")
        if answer != QMessageBox.Yes:
            return
        for p in self.ctl.config.pages:
            if p is not src_page:
                p["screen"] = copy.deepcopy(src_page.get("screen"))
        self._load_editor()
        self.ctl.edited()

    def _config_changed(self):
        if dialogs_open():
            self._defer("config")
            return
        self._build_tabs()
        self.rules.reload()
        self.variables.reload()
        self.device.reload()
        self.integrations.reload()
        self._load_editor()

    def _integrations_changed(self):
        """One was switched on or off: rebuild what lists its parts (rules, and the open key or screen)."""
        if dialogs_open():
            self._defer("config")
            return
        self.rules.reload()
        self._load_editor()

    def _undo_state(self, can_undo, can_redo):
        self.undo_act.setEnabled(can_undo)
        self.redo_act.setEnabled(can_redo)

    def _connection(self, ok, text):
        self.preview.set_layout(self.ctl.layout)
        if ok:
            self.conn.setText("<span style='color:#378ADD'>●</span> MK10 connected")
        else:
            self.conn.setText("<span style='color:#EF9F27'>○</span> MK10 not connected")
        self.conn.setToolTip(text)
        if not ok:
            self.statusBar().showMessage(text, 0)
        else:
            self.statusBar().clearMessage()

    # ---------------------------------------------------------------- tray, closing
    def _make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.icon, self)
        tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Open the editor", self.show_editor)
        pages = menu.addMenu("Show page")
        pages.aboutToShow.connect(lambda: self._fill_page_menu(pages))
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda r: self.show_editor() if r in (QSystemTrayIcon.Trigger,
                                                                     QSystemTrayIcon.DoubleClick) else None)
        tray.show()
        self._tray_menu = menu
        return tray

    def _fill_page_menu(self, menu):
        menu.clear()
        for p in self.ctl.config.pages:
            a = menu.addAction(p["name"])
            a.setCheckable(True)
            a.setChecked(p["id"] == self.ctl.page_id)
            a.triggered.connect(lambda _=False, pid=p["id"]: self.ctl.set_page(pid))

    def show_editor(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        self._save_geometry()
        if self._quitting or self.tray is None:
            if not self._quitting:
                self.quit()
            e.accept()
            return
        e.ignore()
        self.hide()
        s = self.ctl.config.settings
        if not s.get("tray_hint_shown"):
            s["tray_hint_shown"] = True
            self.ctl.save_later()
            self.tray.showMessage(APP_NAME, "Still running in the tray so the MK10 keeps working. Right-click the "
                                            "tray icon to quit.", self.icon, 6000)

    def quit(self):
        self._quitting = True
        self._save_geometry()
        self.ctl.shutdown()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def _save_geometry(self):
        q = QSettings(APP_NAME, APP_NAME)
        q.setValue("geometry", self.saveGeometry())
        q.setValue("split", self.split.saveState())
        q.setValue("top_split", self.top_split.saveState())

    def _restore_geometry(self):
        q = QSettings(APP_NAME, APP_NAME)
        g = q.value("geometry")
        if g:
            self.restoreGeometry(g)
        for key, w in (("split", self.split), ("top_split", self.top_split)):
            v = q.value(key)
            if v:
                w.restoreState(v)
