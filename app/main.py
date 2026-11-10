"""反馈画布的原生桌面界面、文档事务和本地工作流。"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid
import weakref

DOCUMENTS = weakref.WeakSet()

from PySide6.QtCore import QEventLoop, QEvent, QPointF, QRectF, QSaveFile, QIODevice, QSize, QStandardPaths, Qt, QTimer, QSignalBlocker
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QFontDatabase, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider,
    QSpinBox, QDoubleSpinBox, QTabWidget, QTextBrowser, QToolBar,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
    QAbstractItemView, QHeaderView, QStackedWidget,
)

# 同时支持 python app/main.py、python -m app.main 和冻结入口。
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import contracts
import storage
import assets_runtime as assets
from jobs import Jobs
from save_progress import SaveEvents, SaveIndicator
from compressor import CompressionDesk
from board import BoardView
from extras import CaptureOverlay, CompareDialog, ExportDialog
from icons import icon
from colorpicker import get_color
from items import new_record, image_to_base64

LOGGER = logging.getLogger("feedback_studio")
ACCENT = "#c5c5c5"
KINDS = {
    "select": "选择", "image": "图片", "text": "文字", "rect": "矩形",
    "ellipse": "椭圆", "line": "直线", "arrow": "箭头", "pen": "画笔",
    "highlighter": "荧光笔", "polyline": "直线钢笔", "bezier": "曲线钢笔",
    "number": "编号", "region": "区域", "magnifier": "放大镜", "redact": "色块", "eraser": "橡皮擦",
}
TOOL_KEYS = {"select":"Q", "image":"Ctrl+I", "text":"Ctrl+N", "rect":"R", "ellipse":"O", "line":"L", "arrow":"A", "pen":"B", "highlighter":"H", "polyline":"P", "bezier":"U", "number":"N", "region":"F", "magnifier":"M", "redact":"D", "eraser":"E"}
STYLE_DEFAULT = {
    "stroke": "#147d72", "fill": "transparent", "stroke_width": 3.0,
    "font_family": "Microsoft YaHei", "font_size": 18.0, "bold": False,
    "text_color": "#293936", "text_bg": "transparent", "align": "left",
    "opacity": 1.0, "end_arrow": False,
    "arrow_style": "standard", "magnifier_shape": "circle",
    "corner_radius": 0.0, "text_outline": False,
}
STYLESHEET = """
QMainWindow, QDialog { background: #282828; color: #dedede; }
QWidget { color: #dedede; }
QWidget:disabled { color: #a8a8a8; }
QToolBar { background: #282828; border: none; border-bottom: 1px solid #494949; spacing: 5px; padding: 8px 12px; }
QToolBar::separator { background: #494949; width: 1px; margin: 9px 7px; }
QToolButton, QPushButton { background: #2c2c2c; border: 1px solid #494949; border-radius: 6px; padding: 6px 9px; }
QToolButton:hover, QPushButton:hover { background: #4b4b4b; border-color: #494949; }
QToolButton:pressed, QPushButton:pressed { background: #4b4b4b; }
QToolButton:checked, QPushButton:checked { color: #dedede; background: #4b4b4b; border-color: #494949; }
QToolButton:disabled, QPushButton:disabled { color: #a8a8a8; border-color: #494949; background: #282828; }
QToolButton::menu-indicator { width: 7px; }
QPushButton#primary { color: #dedede; background: #4b4b4b; border-color: #494949; font-weight: 600; }
QPushButton#primary:hover { background: #5a5a5a; }
QToolButton#tool { border: 1px solid transparent; background: transparent; padding: 7px; border-radius: 7px; }
QToolButton#tool:hover { background: #4b4b4b; }
QToolButton#tool:checked { background: #4b4b4b; border-color: #494949; }
QFrame#sidebar { background: #282828; border-left: 1px solid #494949; }
QFrame#emptyCard { background: #2c2c2c; border: 1px solid #494949; border-radius: 14px; }
QLabel#muted { color: #a8a8a8; }
QLabel#section { color: #dedede; font-weight: 600; }
QTabWidget::pane { border: none; background: #282828; }
QTabBar::tab { background: #282828; color: #a8a8a8; min-width: 50px; padding: 12px 12px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #dedede; border-bottom-color: #4b4b4b; }
QScrollArea { border: none; background: #282828; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #353535; border: 1px solid #494949; border-radius: 5px; padding: 5px 6px; min-height: 18px; selection-background-color: #4b4b4b; selection-color: #dedede; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: #a8a8a8; }
QComboBox::drop-down { border: none; width: 18px; }
QCheckBox { spacing: 7px; padding: 3px 0; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #494949; border-radius: 3px; background: #353535; }
QCheckBox::indicator:checked { background: #4b4b4b; border-color: #a8a8a8; image: none; }
QGroupBox { border: 1px solid #494949; border-radius: 7px; margin-top: 13px; padding: 13px 7px 7px; }
QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px; color: #a8a8a8; }
QTreeWidget { background: #282828; border: none; outline: none; alternate-background-color: #2c2c2c; }
QTreeWidget::item { min-height: 32px; border-bottom: 1px solid #494949; }
QTreeWidget::item:selected { color: #dedede; background: #4b4b4b; }
QHeaderView::section { background: #2c2c2c; color: #a8a8a8; border: none; padding: 5px 3px; }
QMenu { background: #2c2c2c; border: 1px solid #494949; padding: 6px; }
QMenu::item { padding: 7px 23px 7px 12px; border-radius: 4px; }
QMenu::item:selected { color: #dedede; background: #4b4b4b; }
QMenu::item:disabled { color: #a8a8a8; }
QMenu::separator { height: 1px; background: #494949; margin: 5px; }
QStatusBar { background: #282828; border-top: 1px solid #494949; color: #a8a8a8; }
QStatusBar::item { border: none; }
QSlider::groove:horizontal { height: 4px; background: #494949; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #a8a8a8; border-radius: 2px; }
QSlider::handle:horizontal { background: #c5c5c5; border: 2px solid #494949; width: 12px; margin: -6px 0; border-radius: 7px; }
QScrollBar:vertical { width: 7px; background: transparent; }
QScrollBar::handle:vertical { background: #494949; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #dedede; background: #2c2c2c; border: none; padding: 6px; }
"""


def resource_path(relative):
    return Path(getattr(sys, "_MEIPASS", _APP_DIR.parent)) / relative


def runtime_directory():
    override = os.environ.get("FEEDBACK_DATA_DIR")
    root = Path(override).expanduser() if override else Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    ) / "v0.6.5"
    fresh = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    if fresh and not override:
        # Carry over presets, recent files and window layout from the previous release
        # (recovery copies stay with their own version).
        for version in ("v0.6.4", "v0.6.3"):
            previous = root.parent.parent / f"FeedbackStudio-{version}" / version / "settings.json"
            try:
                if previous.is_file() and not (root / "settings.json").exists():
                    (root / "settings.json").write_bytes(previous.read_bytes())
                    break
            except OSError:
                pass
    return root


def write_json(path, value):
    descriptor, temporary = tempfile.mkstemp(prefix=".feedback-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("设置文件应包含 JSON 对象。")
    return data


@lru_cache(maxsize=128)
def image_fingerprint(encoded):
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()

def fingerprint(records):
    digest = hashlib.sha256()
    # 分条计算，避免为了比较快照再创建整个项目的大字符串。
    for record in sorted(records, key=lambda value: value["id"]):
        metadata = dict(record)
        if metadata.get("image"):
            metadata["image"] = image_fingerprint(metadata["image"])
        digest.update(json.dumps(metadata, ensure_ascii=False, sort_keys=True,
                                 allow_nan=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def installed_fonts():
    from items import ensure_fonts
    ensure_fonts()
    available = set(QFontDatabase.families())
    options = []
    for label, candidates in (
        ("微软雅黑", ("Microsoft YaHei", "微软雅黑", "Microsoft YaHei UI")),
        ("黑体", ("SimHei", "黑体")), ("宋体", ("SimSun", "宋体")),
        ("楷体", ("KaiTi", "楷体", "KaiTi_GB2312")), ("Arial", ("Arial",)),
    ):
        match = next((name for name in candidates if name in available), None)
        if match:
            options.append((label, match))
    return options or [(QApplication.font().family(), QApplication.font().family())]


def clean_style(candidate):
    style = deepcopy(STYLE_DEFAULT)
    style["font_family"] = installed_fonts()[0][1]
    if not isinstance(candidate, dict):
        return style
    for key, value in candidate.items():
        if key in ("stroke", "fill", "text_color", "text_bg"):
            if isinstance(value, str) and QColor(value).isValid():
                style[key] = value
        elif key in ("stroke_width", "font_size", "opacity", "corner_radius"):
            bounds = {"stroke_width": (0, 120), "font_size": (6, 256),
                      "opacity": (0, 1), "corner_radius": (0, 200)}[key]
            if isinstance(value, (float, int)) and math.isfinite(value):
                style[key] = max(bounds[0], min(bounds[1], value))
        elif key in ("bold", "end_arrow", "text_outline") and isinstance(value, bool):
            style[key] = value
        elif key == "arrow_style" and value in ("standard", "slim", "chevron", "double", "tapered", "outline", "tapered_round", "outline_round"):
            style[key] = value
        elif key == "magnifier_shape" and value in ("circle", "rect"):
            style[key] = value
        elif key == "align" and value in ("left", "center", "right"):
            style[key] = value
        elif key == "font_family" and value in QFontDatabase.families():
            style[key] = value
    return style


class MainWindow(QMainWindow):
    def __init__(self, project_path=None, host=None, recovery_dir=None):
        super().__init__()
        self.host = host
        DOCUMENTS.add(self)
        self._legacy_source = None
        self.setObjectName("feedbackStudio")
        self.setMinimumSize(1000, 680)
        self.setFont(QFont(installed_fonts()[0][1], 10))
        QApplication.instance().setFont(self.font())
        self.resize(1440, 900)
        self.setWindowIcon(icon("region", ACCENT))
        palette = QPalette()
        for role, color in (
            (QPalette.ColorRole.Window, "#282828"),
            (QPalette.ColorRole.WindowText, "#dedede"),
            (QPalette.ColorRole.Base, "#353535"),
            (QPalette.ColorRole.AlternateBase, "#2c2c2c"),
            (QPalette.ColorRole.Text, "#dedede"),
            (QPalette.ColorRole.Button, "#2c2c2c"),
            (QPalette.ColorRole.ButtonText, "#dedede"),
            (QPalette.ColorRole.BrightText, "#ffffff"),
            (QPalette.ColorRole.Light, "#494949"),
            (QPalette.ColorRole.Midlight, "#3b3b3b"),
            (QPalette.ColorRole.Dark, "#202020"),
            (QPalette.ColorRole.Mid, "#494949"),
            (QPalette.ColorRole.Shadow, "#161616"),
            (QPalette.ColorRole.Highlight, "#4b4b4b"),
            (QPalette.ColorRole.HighlightedText, "#dedede"),
            (QPalette.ColorRole.ToolTipBase, "#2c2c2c"),
            (QPalette.ColorRole.ToolTipText, "#dedede"),
            (QPalette.ColorRole.Link, "#c5c5c5"),
            (QPalette.ColorRole.LinkVisited, "#a8a8a8"),
            (QPalette.ColorRole.PlaceholderText, "#a8a8a8"),
            (QPalette.ColorRole.Accent, "#4b4b4b"),
        ):
            palette.setColor(role, QColor(color))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                     QPalette.ColorRole.ButtonText, QPalette.ColorRole.HighlightedText,
                     QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#a8a8a8"))
        QApplication.instance().setPalette(palette)
        self.setStyleSheet(STYLESHEET)
        self.data_dir = runtime_directory()
        self.settings_path = self.data_dir / "settings.json"
        document_dir = Path(recovery_dir) if recovery_dir else self.data_dir
        document_dir.mkdir(parents=True, exist_ok=True)
        self.recovery_path = document_dir / "recovery.fbboard"
        self.recovery_marker = document_dir / "recovery.json"
        self.templates_dir = self.data_dir / "templates"
        self.current_file = None
        self._initial_path = project_path
        self._loading = 0
        self._refreshing = False
        self._refresh_pending = False
        self._closing = False
        self._capture = None
        self._capture_pending = False
        self.jobs=Jobs(self)
        self._document_epoch=0
        self._import_seq=0;self._import_next=1;self._imports={}
        self._autosave_busy=False
        self._manual_save_busy=False
        self._save_again=False
        self._save_outcome=False
        self._save_wait_loop=None
        self._saving_digest=None
        self._save_token=None
        self.save_events=SaveEvents(self)
        self.save_events.progress.connect(self._on_save_progress)
        self._settings_busy=False;self._settings_waiting=None
        self._history = []
        self._history_index = -1
        self._saved_digest = None
        self._current_digest = None
        self._autosaved_digest = None
        self._recovery_protected = False
        self._highlighter = False
        self._pen_style = None
        self._paste_count = 0
        self._clipboard_signature = None
        self._last_view_meta = None
        self._settings_error_reported = False
        self._startup_error = None
        try:
            self.settings = host.settings if host is not None else read_json(self.settings_path)
        except (OSError, ValueError) as exc:
            self.settings = {}
            self._startup_error = f"无法读取旧设置，已使用默认设置。\n{exc}"
        defaults = [clean_style({}), clean_style({"stroke": "#d45d4c", "text_color": "#b8473a", "bold": True}),
                    clean_style({"stroke": "#ddb437", "stroke_width": 22, "opacity": 0.35})]
        stored = self.settings.get("presets", [])
        self.presets = [clean_style(stored[i]) if isinstance(stored, list) and i < len(stored)
                        else defaults[i] for i in range(3)]
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(800)
        self._settings_timer.timeout.connect(self._save_settings)
        self.actions = {}
        self.view = BoardView(self)
        self.view.setBackgroundBrush(QColor("#202020"))
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.style.update(clean_style(self.settings.get("style")))
        self.view.continuous = True
        selection_filter = self.settings.get("select_filter", "all")
        self.view.select_filter = selection_filter if selection_filter in ("all", "images", "annotations") else "all"
        self._build_actions()
        self._build_toolbar()
        self._build_workspace()
        self.workspace_stack=QStackedWidget()
        self.board_workspace=self.takeCentralWidget()
        self.workspace_stack.addWidget(self.board_workspace)
        self.desk=CompressionDesk(self.jobs,self.data_dir,self)
        self.workspace_stack.addWidget(self.desk)
        self.desk.leave.connect(self._leave_compression)
        self.desk.copied.connect(lambda path:self.message(f"已复制 {Path(path).name}，可粘贴到聊天软件或文件夹。",10000))
        self.setCentralWidget(self.workspace_stack)
        self._build_footer()
        self._build_empty_card()
        self._connect_view()
        self._restore_window_settings()
        self._reset_history(clean=True)
        self._refresh_ui()
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(45000)
        self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start()
        self._view_timer = QTimer(self)
        self._view_timer.setInterval(350)
        self._view_timer.timeout.connect(self._view_tick)
        self._view_timer.start()
        QTimer.singleShot(0, self, self._startup)

    def _action(self, key, title, callback, image=None, shortcut=None, checkable=False):
        action = QAction(icon(image, ACCENT) if image else icon("more"), title, self)
        action.setCheckable(checkable)
        action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        if shortcut:
            action.setShortcuts([QKeySequence(value) for value in shortcut] if isinstance(shortcut, list)
                                else [QKeySequence(shortcut)])
        def invoke(*args):
            self._flush_pending_wheel()
            return callback(*args)
        if checkable:
            action.toggled.connect(invoke)
        else:
            action.triggered.connect(lambda _checked=False: invoke())
        self.view.addAction(action)
        self.actions[key] = action
        return action

    def _flush_pending_wheel(self):
        if self.view._wheel_timer.isActive():
            self.view._wheel_timer.stop()
            self.view._commit()

    def _build_actions(self):
        self._action("new", "新建画布", self.new_project, "plus", "Ctrl+K")
        self._action("open", "打开项目…", self.open_project, "open", "Ctrl+L")
        self._action("import", "导入图片", self.import_images, "image", "Ctrl+I")
        self._action("capture", "区域截图", self.capture_screen, "capture")
        self._action("save", "保存项目", self.save_project, "save", "Ctrl+S")
        self._action("save_as", "项目另存为…", lambda: self.save_project(save_as=True), "save", "Ctrl+Shift+S")
        self._action("export", "导出画布…", self.export_image, "export", "Ctrl+E")
        self._action("export_selection", "导出选中…", lambda: self.export_image(True), "export", "Ctrl+Shift+E")
        self._action("copy", "复制选中", self.copy_selection, "group", "Ctrl+C")
        self._action("paste", "粘贴", self.paste, "image", "Ctrl+V")
        self._action("undo", "撤销", self.undo, "undo", "Ctrl+Z")
        self._action("redo", "重做", self.redo, "redo", "Ctrl+Shift+Z")
        self._action("compare", "对比两张图片", self.compare_images, "compare")
        self._action("new_text", "新建文字…", self.new_text, "text", "Ctrl+N")
        self._action("fullscreen", "全屏", self.toggle_fullscreen, "fit", "Ctrl+F")
        self._action("minimize", "最小化", lambda: self.window().showMinimized())
        self._action("always_top", "窗口置顶", self.set_always_top, "lock", checkable=True)
        self._action("sidebar", "显示属性侧栏", self.set_sidebar_visible, "template", checkable=True)
        self.actions["sidebar"].setChecked(bool(self.settings.get("sidebar", True)))
        self._action("minimal", "极简模式", self.set_minimal, "fit", "Tab", checkable=True)
        self._action("help", "快捷键与使用帮助", self.show_help)
        self._action("fit", "适配全部图元", lambda: self.view.focus_items(), "fit")
        self._action("reset_view", "重置视图（100% 并居中）", self.reset_view, "fit")
        self._action("templates_save", "将选中保存为模板…", self.save_template, "template")
        self._action("group", "编组", lambda: self._view_command("group_selection"), "group")
        self._action("ungroup", "解组", lambda: self._view_command("ungroup_selection"), "group")
        self._action("duplicate", "原位附近复制", lambda: self._view_command("duplicate"), "group")
        self._action("delete", "删除选中", lambda: self._view_command("delete_selection"))

    def _tool_button(self, action, text=False):
        button = QToolButton()
        button.setDefaultAction(action)
        button.setIconSize(QSize(20, 20))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text else Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setMinimumHeight(34)
        button.setAccessibleName(action.text())
        return button

    def _build_toolbar(self):
        self.top_toolbar = QToolBar("主工具栏", self)
        self.top_toolbar.setObjectName("mainToolbar")
        self.top_toolbar.setMovable(False)
        self.top_toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.top_toolbar)
        identity = QWidget()
        identity.setFixedWidth(184)
        brand = QVBoxLayout(identity)
        brand.setContentsMargins(2, 0, 12, 0)
        brand.setSpacing(0)
        title = QLabel("反馈画布")
        title.setStyleSheet("font-size: 19px; font-weight: 650; color: #dedede;")
        subtitle = QLabel("FEEDBACK STUDIO")
        subtitle.setStyleSheet("font-size: 9px; letter-spacing: 2px; color: #a8a8a8;")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        identity.setToolTip("本地参考图与视觉批注工作台")
        self.top_toolbar.addWidget(identity)
        self.top_toolbar.addSeparator()
        for key in ("import",):
            self.top_toolbar.addWidget(self._tool_button(self.actions[key], True))
        self.top_toolbar.addSeparator()
        for key in ("undo", "redo"):
            self.top_toolbar.addWidget(self._tool_button(self.actions[key]))
        for key in ("save",):
            button = self._tool_button(self.actions[key], True)
            if key == "save":
                button.setText("保存")
            elif key == "export":
                button.setText("导出")
            self.top_toolbar.addWidget(button)
        stretch = QWidget()
        stretch.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.top_toolbar.addWidget(stretch)
        self.pack_button=QPushButton("打包已框选")
        self.pack_button.setToolTip("进入压缩台，预览、命名并调节文件大小")
        self.pack_button.clicked.connect(lambda:self.open_compression(True))
        self.top_toolbar.addWidget(self.pack_button)
        self.top_toolbar.addWidget(self._tool_button(self.actions["compare"]))
        self.template_menu = QMenu(self)
        self.template_menu.aboutToShow.connect(self._fill_template_menu)
        more = QToolButton()
        more.setIcon(icon("more", ACCENT))
        more.setToolTip("项目、视图与帮助")
        more.setAccessibleName("更多菜单")
        more.setMinimumHeight(34)
        self.more_menu = QMenu(self)
        for key in ("new", "open", "save_as", "paste", "new_text"):
            self.more_menu.addAction(self.actions[key])
            if key == "open":
                self.recent_menu = self.more_menu.addMenu("最近打开")
                self.recent_menu.setToolTipsVisible(True)
                self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        self.more_menu.addSeparator()
        for key in ("fit", "reset_view", "sidebar", "minimal", "fullscreen", "always_top"):
            self.more_menu.addAction(self.actions[key])
        self.more_menu.addSeparator()
        self.more_menu.addAction("显示全部批注", lambda: self.set_annotations_visible(True))
        self.more_menu.addAction("隐藏全部批注", lambda: self.set_annotations_visible(False))
        self.more_menu.addSeparator()
        self.more_menu.addAction("打开可编辑示例", self.open_demo)
        self.more_menu.addAction(self.actions["help"])
        more.setMenu(self.more_menu)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.top_toolbar.addWidget(more)
        self.top_toolbar.addWidget(self._tool_button(self.actions["sidebar"]))

    def _build_workspace(self):
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toolbox = QScrollArea()
        self.toolbox.setFixedWidth(58)
        self.toolbox.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.toolbox.setWidgetResizable(True)
        tools = QWidget()
        tools.setStyleSheet(".QWidget { background: #282828; }")
        column = QVBoxLayout(tools)
        column.setContentsMargins(7, 9, 7, 9)
        column.setSpacing(3)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions = {}
        self.tool_buttons = {}
        self.shortcut_toggle = QToolButton()
        self.shortcut_toggle.setToolTip("展开/折叠快捷键文字，保留工具图标")
        self.shortcut_toggle.clicked.connect(self._toggle_tool_shortcuts)
        column.addWidget(self.shortcut_toggle)
        for kind, label in KINDS.items():
            if kind in ("text", "pen", "number"):
                line = QFrame()
                line.setFrameShape(QFrame.Shape.HLine)
                line.setStyleSheet("color: #494949; margin: 3px 5px;")
                column.addWidget(line)
            action = QAction(icon("pen" if kind == "highlighter" else kind,
                                  "#ae851d" if kind == "highlighter" else "#c5c5c5"), label + "（" + TOOL_KEYS[kind] + "）", self)
            action.setCheckable(True)
            action.triggered.connect(lambda _checked=False, tool=kind: self.choose_tool(tool))
            self.tool_group.addAction(action)
            self.tool_actions[kind] = action
            button = QToolButton()
            button.setObjectName("tool")
            button.setDefaultAction(action)
            button.setIconSize(QSize(22, 22))
            self.tool_buttons[kind] = button
            button.setFixedSize(42, 38)
            button.setToolTip(label + " (" + TOOL_KEYS[kind] + ")" + (" · 在图片上拖动框选" if kind == "magnifier" else ""))
            button.setAccessibleName(label)
            column.addWidget(button)
        self._show_tool_shortcuts(bool(self.settings.get("tool_shortcuts", True)))
        column.addStretch()
        self.toolbox.setWidget(tools)
        layout.addWidget(self.toolbox)
        layout.addWidget(self.view, 1)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(250)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._properties_page(), "属性")
        self.tabs.addTab(self._regions_page(), "区域")
        self.tabs.currentChanged.connect(lambda index: self._refresh_regions() if index == 1 else None)
        side_layout.addWidget(self.tabs)
        layout.addWidget(self.sidebar)
        self.setCentralWidget(central)
        self.sidebar.setVisible(self.actions["sidebar"].isChecked())
        self.tool_actions["select"].setChecked(True)

    def _show_tool_shortcuts(self, expanded):
        self.settings["tool_shortcuts"] = expanded
        self.toolbox.setFixedWidth(210 if expanded else 58)
        self.shortcut_toggle.setText("‹ 收起快捷键" if expanded else "›")
        for kind, button in self.tool_buttons.items():
            self.tool_actions[kind].setText(KINDS[kind] + "（" + TOOL_KEYS[kind] + "）")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if expanded else Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setFixedSize(192 if expanded else 42, 38)

    def _toggle_tool_shortcuts(self):
        self._show_tool_shortcuts(not self.settings.get("tool_shortcuts", True))
        self._settings_timer.start()

    def _form(self, parent):
        form = QFormLayout(parent)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return form

    def _properties_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setStyleSheet(".QWidget { background: #282828; }")
        column = QVBoxLayout(body)
        column.setContentsMargins(13, 14, 13, 14)
        column.setSpacing(12)
        self.selection_title = QLabel("绘制设置")
        self.selection_title.setObjectName("section")
        column.addWidget(self.selection_title)
        self.selection_hint = QLabel("未选中图元时，修改下一笔样式。")
        self.selection_hint.setObjectName("muted")
        self.selection_hint.setWordWrap(True)
        column.addWidget(self.selection_hint)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("对象名称")
        self.name_edit.setAccessibleName("对象名称")
        self.name_edit.editingFinished.connect(self._rename_selected)
        column.addWidget(self.name_edit)
        self.edit_text_button = QPushButton("编辑文字内容…")
        self.edit_text_button.clicked.connect(self._edit_selected_text)
        column.addWidget(self.edit_text_button)
        self.style_controls = {}
        style_widget = QWidget()
        form = self._form(style_widget)
        for key, label in (("stroke", "描边"), ("fill", "填充"), ("text_color", "文字颜色"), ("text_bg", "文字底色")):
            button = QPushButton()
            button.setMinimumWidth(110)
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, name=key: self._choose_color(name))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda pos, name=key, widget=button: self._color_menu(name, widget, pos))
            button.setToolTip("单击选择颜色；右键设为透明")
            self.style_controls[key] = button
            if key in ("fill", "text_bg"):
                row=QWidget();row_layout=QHBoxLayout(row);row_layout.setContentsMargins(0,0,0,0);row_layout.setSpacing(4)
                button.setMinimumWidth(65)
                row_layout.addWidget(button,1)
                transparent=QPushButton("透明")
                transparent.setToolTip("取消填充；选中整组时取消组底色" if key=="fill" else "取消文字底色")
                transparent.clicked.connect(lambda _checked=False,name=key:self._set_style(name,"transparent"))
                if key=="fill":self.fill_transparent=transparent
                else:self.text_bg_transparent=transparent
                row_layout.addWidget(transparent)
                form.addRow("填充色" if key=="fill" else "文字底色",row)
            else:
                form.addRow(label, button)
        width = QDoubleSpinBox()
        width.setRange(0, 120)
        width.setDecimals(2)
        width.setSingleStep(0.5)
        width.setSuffix(" px")
        width.setKeyboardTracking(False)
        width.valueChanged.connect(lambda value: self._set_style("stroke_width", value))
        self.style_controls["stroke_width"] = width
        form.addRow("线宽", width)
        radius = QDoubleSpinBox()
        radius.setRange(0, 200)
        radius.setDecimals(1)
        radius.setSingleStep(1)
        radius.setSuffix(" px")
        radius.setKeyboardTracking(False)
        radius.setAccessibleName("矩形圆角半径")
        radius.setToolTip("画布像素；0 为直角")
        radius.valueChanged.connect(lambda value: self._set_style("corner_radius", value))
        self.style_controls["corner_radius"] = radius
        self.corner_radius_label = QLabel("圆角半径")
        form.addRow(self.corner_radius_label, radius)
        family = QComboBox()
        for label, value in installed_fonts():
            family.addItem(label, value)
        family.currentIndexChanged.connect(lambda _index: self._set_style("font_family", family.currentData()))
        self.style_controls["font_family"] = family
        form.addRow("字体", family)
        size = QDoubleSpinBox()
        size.setRange(6, 256)
        size.setDecimals(1)
        size.setKeyboardTracking(False)
        size.setSuffix(" pt")
        size.valueChanged.connect(lambda value: self._set_style("font_size", value))
        self.style_controls["font_size"] = size
        form.addRow("字号", size)
        self.text_width = QDoubleSpinBox()
        self.text_width.setRange(1, 100000)
        self.text_width.setDecimals(2)
        self.text_width.setSingleStep(10)
        self.text_width.setSuffix(" px")
        self.text_width.setKeyboardTracking(False)
        self.text_width.setAccessibleName("文字换行宽度")
        self.text_width.valueChanged.connect(self._apply_text_width)
        self.text_width_label = QLabel("换行宽度")
        form.addRow(self.text_width_label, self.text_width)
        bold = QCheckBox("加粗文字")
        bold.toggled.connect(lambda value: self._set_style("bold", value))
        self.style_controls["bold"] = bold
        form.addRow("字重", bold)
        outline = QCheckBox("文字白色描边")
        outline.toggled.connect(lambda value: self._set_style("text_outline", value))
        self.style_controls["text_outline"] = outline
        self.text_outline_label = QLabel("文字描边")
        form.addRow(self.text_outline_label, outline)
        alignment = QComboBox()
        for label, value in (("左对齐", "left"), ("居中", "center"), ("右对齐", "right")):
            alignment.addItem(label, value)
        alignment.currentIndexChanged.connect(lambda _index: self._set_style("align", alignment.currentData()))
        self.style_controls["align"] = alignment
        form.addRow("文字对齐", alignment)
        opacity = QSpinBox()
        opacity.setRange(0, 100)
        opacity.setSuffix(" %")
        opacity.setKeyboardTracking(False)
        opacity.valueChanged.connect(lambda value: self._set_style("opacity", value / 100.0))
        self.style_controls["opacity"] = opacity
        form.addRow("不透明度", opacity)
        arrow = QCheckBox("路径末端箭头")
        arrow.toggled.connect(lambda value: self._set_style("end_arrow", value))
        self.style_controls["end_arrow"] = arrow
        form.addRow("箭头", arrow)
        arrow_style = QComboBox()
        for label, value in (("标准尖头", "standard"), ("细长尖头", "slim"), ("燕尾箭头", "chevron"), ("双向箭头", "double"), ("实心渐尖", "tapered_round"), ("空心渐尖", "outline_round"), ("旧版实心渐尖", "tapered"), ("旧版空心渐尖", "outline")):
            arrow_style.addItem(label, value)
        arrow_style.currentIndexChanged.connect(lambda _: self._set_style("arrow_style", arrow_style.currentData()))
        self.style_controls["arrow_style"] = arrow_style
        form.addRow("箭头样式", arrow_style)
        lens_shape = QComboBox()
        lens_shape.addItem("圆形框选", "circle")
        lens_shape.addItem("矩形框选", "rect")
        lens_shape.currentIndexChanged.connect(lambda _: self._set_style("magnifier_shape", lens_shape.currentData()))
        self.style_controls["magnifier_shape"] = lens_shape
        form.addRow("放大镜形状", lens_shape)

        column.addWidget(style_widget)
        presets_box = QGroupBox("三组常用样式")
        preset_layout = QVBoxLayout(presets_box)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(["样式 1 · 常规", "样式 2 · 强调", "样式 3 · 荧光"])
        preset_layout.addWidget(self.preset_combo)
        row = QHBoxLayout()
        apply_button = QPushButton("应用")
        apply_button.clicked.connect(self.apply_preset)
        remember = QPushButton("记住当前")
        remember.clicked.connect(self.remember_preset)
        row.addWidget(apply_button)
        row.addWidget(remember)
        preset_layout.addLayout(row)
        column.addWidget(presets_box)
        self.crop_box = QGroupBox("原图取样 / 裁剪")
        crop_form = self._form(self.crop_box)
        self.crop_controls = {}
        for key, title, minimum, maximum in (("x", "左侧 %", 0, 99), ("y", "顶部 %", 0, 99),
                                               ("w", "宽度 %", 1, 100), ("h", "高度 %", 1, 100)):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(minimum, maximum)
            slider.setMinimumWidth(35)
            slider.setAccessibleName("取样" + title)
            spin = QSpinBox()
            spin.setRange(minimum, maximum)
            spin.setFixedWidth(53)
            spin.setKeyboardTracking(False)
            spin.setAccessibleName("取样" + title)
            slider.valueChanged.connect(spin.setValue)
            spin.valueChanged.connect(slider.setValue)
            slider.sliderReleased.connect(self._apply_crop)
            spin.editingFinished.connect(self._apply_crop)
            row_layout.addWidget(slider, 1)
            row_layout.addWidget(spin)
            self.crop_controls[key] = (slider, spin)
            crop_form.addRow(title, row_widget)
        self.diameter = QSpinBox()
        self.diameter.setRange(48, 2400)
        self.diameter.setSuffix(" px")
        self.diameter.setKeyboardTracking(False)
        self.diameter.editingFinished.connect(self._apply_diameter)
        self.diameter_label = QLabel("镜面宽度")
        crop_form.addRow(self.diameter_label, self.diameter)
        reset = QPushButton("重置裁剪")
        reset.clicked.connect(lambda: self._view_command("reset_crop"))
        crop_form.addRow(reset)
        column.addWidget(self.crop_box)
        mode_box = QGroupBox("交互")
        modes = QVBoxLayout(mode_box)
        self.continuous_box = QCheckBox("连续绘制当前工具")
        self.continuous_box.setChecked(self.view.continuous)
        self.continuous_box.toggled.connect(self._continuous_changed)
        self.continuous_box.hide()
        modes.addWidget(QLabel("画布选择过滤"))
        self.filter_combo = QComboBox()
        for text, key in (("全部图元", "all"), ("仅图片", "images"), ("仅批注", "annotations")):
            self.filter_combo.addItem(text, key)
        self.filter_combo.setCurrentIndex(self.filter_combo.findData(self.view.select_filter))
        self.filter_combo.currentIndexChanged.connect(self._filter_changed)
        modes.addWidget(self.filter_combo)
        column.addWidget(mode_box)
        note = QLabel("数据仅保存在本机。\n45 秒自动恢复副本，不覆盖源项目。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        column.addWidget(note)
        column.addStretch()
        scroll.setWidget(body)
        return scroll

    def _layers_page(self):
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(7, 12, 7, 8)
        hint = QLabel("双击名称重命名；Ctrl 多选。\n独立选取子批注可绕过画布选择过滤。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        column.addWidget(hint)
        self.layers = QTreeWidget()
        self.layers.setColumnCount(3)
        self.layers.setHeaderLabels(["图层", "显", "锁"])
        self.layers.setRootIsDecorated(False)
        self.layers.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.layers.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.layers.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in (1, 2):
            self.layers.header().setSectionResizeMode(index, QHeaderView.ResizeMode.Fixed)
            self.layers.setColumnWidth(index, 30)
        self.layers.itemSelectionChanged.connect(self._layer_selection)
        self.layers.itemChanged.connect(self._layer_renamed)
        self.layers.setAlternatingRowColors(True)
        column.addWidget(self.layers, 1)
        show = QPushButton("恢复全部隐藏图层")
        show.clicked.connect(self.unhide_all)
        column.addWidget(show)
        return page

    def _regions_page(self):
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(10, 12, 10, 10)
        hint = QLabel("用区域工具组织反馈。双击定位；复制包含区域框与中心位于区域内的图元。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        column.addWidget(hint)
        self.regions = QTreeWidget()
        self.regions.setHeaderHidden(True)
        self.regions.setRootIsDecorated(False)
        self.regions.itemDoubleClicked.connect(lambda item, _column: self.focus_region(item.data(0, Qt.ItemDataRole.UserRole)))
        column.addWidget(self.regions, 1)
        focus = QPushButton("定位所选区域")
        focus.clicked.connect(lambda: self.focus_region(self._current_region_id()))
        copy_button = QPushButton("复制区域内图元")
        copy_button.clicked.connect(lambda: self.copy_region(self._current_region_id()))
        column.addWidget(focus)
        column.addWidget(copy_button)
        add = QPushButton("绘制新区域")
        add.clicked.connect(lambda: self.choose_tool("region"))
        column.addWidget(add)
        return page

    def _build_footer(self):
        status = self.statusBar()
        status.setSizeGripEnabled(True)
        self.selection_status = QLabel()
        self.selection_status.setMinimumWidth(150)
        status.addWidget(self.selection_status)
        self.guidance = QLabel("拖入图片 / Ctrl+V 粘贴  ·  更多菜单查看帮助")
        self.guidance.setObjectName("muted")
        status.addWidget(self.guidance, 1)
        self.zoom_button = QToolButton()
        self.zoom_button.setText("100%")
        self.zoom_button.setFixedWidth(68)
        self.zoom_button.setToolTip("设置画布缩放")
        self.zoom_button.setStyleSheet("border: none; padding: 3px;")
        menu = QMenu(self)
        for percent in (25, 50, 75, 100, 150, 200, 400):
            menu.addAction(f"{percent}%", lambda _checked=False, value=percent: self.set_zoom(value / 100))
        self.zoom_button.setMenu(menu)
        self.zoom_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.save_indicator=SaveIndicator(self)
        status.addPermanentWidget(self.save_indicator,1)
        self.save_indicator.hide()
        status.addPermanentWidget(self.zoom_button)
        fit = self._tool_button(self.actions["fit"])
        fit.setStyleSheet("border: none; padding: 3px;")
        status.addPermanentWidget(fit)
        self.restore_ui_button = QPushButton("退出极简")
        self.restore_ui_button.clicked.connect(lambda: self.actions["minimal"].setChecked(False))
        self.restore_ui_button.hide()
        status.addPermanentWidget(self.restore_ui_button)

    def _build_empty_card(self):
        self.empty_card = QFrame(self.view.viewport())
        self.empty_card.setObjectName("emptyCard")
        self.empty_card.setFixedSize(430, 222)
        layout = QVBoxLayout(self.empty_card)
        layout.setContentsMargins(25, 24, 25, 20)
        layout.setSpacing(10)
        title = QLabel("把参考图放在这里")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 23px; font-weight: 600; color: #dedede;")
        layout.addWidget(title)
        text = QLabel("拖入图片，粘贴截图，开始一轮清晰反馈。")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setObjectName("muted")
        layout.addWidget(text)
        row = QHBoxLayout()
        row.addStretch()
        add = QPushButton("导入图片")
        add.setObjectName("primary")
        add.setIcon(icon("image", "#dedede"))
        add.clicked.connect(lambda: self.import_images())
        row.addWidget(add)
        row.addStretch()
        layout.addLayout(row)
        keys = QLabel("Ctrl+V 粘贴  /  Ctrl+L 打开项目  /  更多菜单帮助")
        keys.setAlignment(Qt.AlignmentFlag.AlignCenter)
        keys.setObjectName("muted")
        layout.addWidget(keys)
        self.view.viewport().installEventFilter(self)
        self._position_empty_card()

    def _connect_view(self):
        self.view.magnifier_region.connect(self.create_magnifier)
        self.view.changed.connect(self.checkpoint)
        self.view.selection_changed.connect(self._queue_refresh)
        self.view.message.connect(self.message)
        self.view.command.connect(self.dispatch_command)
        self.view.files_dropped.connect(self._files_dropped)
        self.view.tool_changed.connect(self._tool_changed)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.view.customContextMenuRequested.connect(self.context_menu)

    def _restore_window_settings(self):
        if self.host is not None: return
        geometry = self.settings.get("geometry")
        if isinstance(geometry, str):
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii", errors="ignore")))
            if not any(screen.availableGeometry().intersects(self.frameGeometry()) for screen in QApplication.screens()):
                screen = QApplication.primaryScreen()
                if screen:
                    self.move(screen.availableGeometry().center() - self.rect().center())
        meta = self.settings.get("view")
        if isinstance(meta, dict):
            try:
                self.view.restore_view(meta)
            except (TypeError, ValueError, KeyError, OverflowError):
                self.view.reset_zoom()
        if self.settings.get("always_top", False):
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            with QSignalBlocker(self.actions["always_top"]):
                self.actions["always_top"].setChecked(True)

    def _startup(self):
        if self.host is not None: return
        if self._closing:
            return
        if self._startup_error:
            self.message(self._startup_error, 10000)
        recovered = self.recover_session()
        if not recovered and self._initial_path:
            self.open_project(self._initial_path)
        elif not recovered and self.settings.get("last_project"):
            recent = Path(self.settings["last_project"])
            if recent.is_file():
                self.open_project(str(recent))
                if isinstance(self.settings.get("view"), dict):
                    self.view.restore_view(self.settings["view"])
        self._initial_path = None
        self._position_empty_card()
        self.view.setFocus()

    def _guard(self, title, callback):
        try:
            return callback()
        except Exception as exc:
            LOGGER.exception(title)
            QMessageBox.warning(self, title, f"操作未完成，当前项目仍保留。\n\n{exc}")
            return False

    def message(self, text, timeout=5000):
        self.statusBar().showMessage(str(text), timeout)

    @property
    def dirty(self):
        return self._current_digest != self._saved_digest

    def _reset_history(self, clean):
        records = self.view.records()
        self._current_digest = fingerprint(records)
        self._history = [{"records": records, "digest": self._current_digest,
                          "selected": [item.record["id"] for item in self.view.selected()]}]
        self._history_index = 0
        self._saved_digest = self._current_digest if clean else None
        self._autosaved_digest = None
        self._update_title()

    def checkpoint(self):
        if self._loading or self._closing:
            return
        records = self.view.records()
        digest = fingerprint(records)
        if digest != self._current_digest:
            del self._history[self._history_index + 1:]
            self._history.append({"records": records, "digest": digest,
                                  "selected": [item.record["id"] for item in self.view.selected()]})
            if len(self._history) > 60:
                del self._history[:len(self._history) - 60]
            self._history_index = len(self._history) - 1
            keep=[r for state in self._history for r in state["records"]]
            keep.extend(storage._clipboard_records or [])
            self._collect_assets(keep)
            self._current_digest = digest
            self._update_title()
            if not self.dirty:
                self._clear_recovery()
            self._settings_timer.start()
        self._queue_refresh()

    @contextmanager
    def _transaction(self):
        before = self.view.records()
        selected = [item.record["id"] for item in self.view.selected()]
        self._loading += 1
        try:
            yield
            self.view.update_links()
        except Exception:
            self.view.load_records(before)
            self.view.select_items([item for identifier in selected if (item := self.view.by_id(identifier)) is not None])
            raise
        finally:
            self._loading -= 1
        self.checkpoint()

    def _view_command(self, method, *args):
        def perform():
            self._flush_pending_wheel()
            with self._transaction():
                getattr(self.view, method)(*args)
        return self._guard("画布操作", perform)

    def undo(self):
        self._flush_pending_wheel()
        if self._history_index > 0:
            self._restore_snapshot(self._history_index - 1)

    def redo(self):
        if self._history_index + 1 < len(self._history):
            self._restore_snapshot(self._history_index + 1)

    def _restore_snapshot(self, index):
        def restore():
            snapshot = self._history[index]
            self._loading += 1
            try:
                self.view.cancel_gesture()
                self.view.restore_records(snapshot["records"])
                self.view.select_items([item for identifier in snapshot["selected"]
                                        if (item := self.view.by_id(identifier)) is not None])
            finally:
                self._loading -= 1
            self._history_index = index
            self._current_digest = snapshot["digest"]
            self._update_title()
            if not self.dirty:
                self._clear_recovery()
            self._queue_refresh()
        self._guard("恢复撤销记录", restore)

    def _collect_assets(self, extra):
        keep=list(extra)+(storage._clipboard_records or [])
        for doc in list(DOCUMENTS):
            if not doc._closing:
                keep.extend(r for state in doc._history for r in state["records"])
                keep.extend(doc.view.records())
        assets.collect(keep)

    def _update_title(self):
        name = Path(self.current_file).name if self.current_file else "未命名画布"
        self.setWindowTitle(f"{name}[*] — 反馈画布 / FEEDBACK STUDIO")
        self.setWindowModified(self.dirty)
        if self.host is not None: self.host.update_document(self)
        self.actions["undo"].setEnabled(self._history_index > 0)
        self.actions["redo"].setEnabled(self._history_index + 1 < len(self._history))
        self.actions["save"].setToolTip("保存项目 · Ctrl+S" + (" · 有未保存修改" if self.dirty else ""))

    def _queue_refresh(self, *_args):
        if not self._refresh_pending and not self._closing:
            self._refresh_pending = True
            QTimer.singleShot(0, self, self._refresh_ui)

    def _refresh_ui(self):
        self._refresh_pending = False
        if self._closing:
            return
        self._tool_changed(self.view.tool)
        self._refreshing = True
        try:
            items = self.view.selected()
            records = [item.to_record() for item in items]
            source = records[0] if records else self.view.style
            count = len(items)
            boards = [i for i in items if i.record.get("group_background")]
            self.selection_title.setText(f"选中 {count} 个图元" if count else "绘制设置")
            locked = any(record.get("locked") for record in records)
            self.selection_hint.setText("含锁定图层；解锁后再编辑。" if locked else
                                        "混合值以首个图元显示；修改应用到所有选中项。" if count > 1 else
                                        "修改应用到选中图元，也作为下一笔的样式。" if count else
                                        "未选中图元时，修改下一笔样式。")
            self.name_edit.setEnabled(count == 1 and not locked)
            self.name_edit.setText(self._record_name(source) if count == 1 else "")
            self.edit_text_button.setVisible(count == 1 and source.get("kind") in ("text", "number", "region"))
            self.edit_text_button.setEnabled(not locked)
            text_width_visible = False
            self.text_width.setVisible(text_width_visible)
            self.text_width_label.setVisible(text_width_visible)
            self.text_width.setEnabled(not locked)
            if text_width_visible:
                with QSignalBlocker(self.text_width):
                    self.text_width.setValue(source["w"])
            for key, kind, label in (("corner_radius", "rect", self.corner_radius_label),
                                     ("text_outline", "text", self.text_outline_label)):
                visible = all(record["kind"] == kind for record in records) if records else self.view.tool == kind
                self.style_controls[key].setVisible(visible)
                label.setVisible(visible)
            self.fill_transparent.setEnabled(not locked and (bool(boards) or not any(r["kind"]=="pen" for r in records)))
            self.text_bg_transparent.setEnabled(not locked and not any(r["kind"]=="pen" for r in records))
            for key, widget in self.style_controls.items():
                widget.setEnabled(not locked and (bool(boards) and key=="fill" or not any(r["kind"]=="pen" for r in records)))
                value = boards[0].record.get("fill","transparent") if key=="fill" and boards else source.get(key, self.view.style.get(key, STYLE_DEFAULT.get(key)))
                with QSignalBlocker(widget):
                    if key in ("stroke", "fill", "text_color", "text_bg"):
                        self._paint_color_button(widget, value)
                    elif isinstance(widget, QCheckBox):
                        widget.setChecked(bool(value))
                    elif isinstance(widget, QComboBox):
                        widget.setCurrentIndex(widget.findData(value))
                        widget.setToolTip(str(value) if key == "font_family" else "")
                    else:
                        widget.setValue(round(float(value) * 100) if key == "opacity" else float(value))
                mixed = count > 1 and any(record.get(key) != source.get(key) for record in records[1:])
                if mixed:
                    widget.setToolTip("混合值；当前显示首个选中图元")
            crop_visible = count == 1 and source.get("kind") in ("image", "magnifier")
            self.crop_box.setVisible(crop_visible)
            self.crop_box.setEnabled(not locked)
            if crop_visible:
                crop = source.get("crop") or [0, 0, 1, 1]
                for index, key in enumerate(("x", "y", "w", "h")):
                    slider, spin = self.crop_controls[key]
                    with QSignalBlocker(slider), QSignalBlocker(spin):
                        slider.setValue(round(crop[index] * 100))
                        spin.setValue(round(crop[index] * 100))
                magnifier = source["kind"] == "magnifier"
                self.diameter.setVisible(magnifier)
                self.diameter_label.setVisible(magnifier)
                with QSignalBlocker(self.diameter):
                    self.diameter.setValue(round(source.get("w", 180)))
            self.actions["copy"].setEnabled(bool(count))
            self.actions["export_selection"].setEnabled(bool(count))
            self.actions["templates_save"].setEnabled(bool(count))
            self.actions["compare"].setEnabled(count == 2 and all(record["kind"] == "image" for record in records))
            if self.tabs.currentIndex() == 1:
                self._refresh_regions()
            all_count = len(self.view.all_items())
            self.selection_status.setText(f"  {count} 项选中  /  {all_count} 个图元")
            self.empty_card.setVisible(all_count == 0)
            self._position_empty_card()
            self._tool_changed(self.view.tool)
            self._update_title()
        finally:
            self._refreshing = False

    @staticmethod
    def _record_name(record):
        name = record.get("name") or str(record.get("text", "")).split("\n")[0] or KINDS.get(record.get("kind"), "图元")
        return str(name)

    def _refresh_layers(self, selected):
        selected_ids = {item.record["id"] for item in selected}
        position = self.layers.verticalScrollBar().value()
        with QSignalBlocker(self.layers):
            self.layers.clear()
            items = sorted(self.view.all_items(), key=lambda item: item.zValue(), reverse=True)
            for item in items:
                record = item.to_record()
                identifier = record["id"]
                row = QTreeWidgetItem([self._record_name(record), "", ""])
                row.setData(0, Qt.ItemDataRole.UserRole, identifier)
                row.setIcon(0, icon(record["kind"], "#a8a8a8" if record.get("hidden") else "#c5c5c5"))
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsEditable)
                details = [KINDS.get(record["kind"], record["kind"])]
                if record.get("attached_to"):
                    owner = self.view.by_id(record["attached_to"])
                    details.append("附着于：" + (self._record_name(owner.record) if owner else "来源已移除"))
                if record.get("group"):
                    details.append("属于编组；此列表可单独选择成员")
                row.setToolTip(0, "\n".join(details))
                self.layers.addTopLevelItem(row)
                row.setSelected(identifier in selected_ids)
                for col, key, image in ((1, "hidden", "eye"), (2, "locked", "lock" if record.get("locked") else "unlock")):
                    button = QToolButton()
                    button.setIcon(icon(image, "#dedede" if record.get(key) else "#a8a8a8"))
                    button.setIconSize(QSize(16, 16))
                    button.setFixedSize(26, 28)
                    button.setStyleSheet("border: none; padding: 3px; background: transparent;")
                    button.setText("显示" if key == "hidden" and record.get(key) else "隐藏" if key == "hidden" else "解锁" if record.get(key) else "锁定")
                    button.setToolTip(button.text())
                    button.setAccessibleName(button.text() + " " + self._record_name(record))
                    if key == "hidden" and record.get(key):
                        button.setStyleSheet("border: 1px dashed #494949; padding: 3px; background: #2c2c2c;")
                    button.clicked.connect(lambda _checked=False, ident=identifier, prop=key: self._toggle_layer(ident, prop))
                    self.layers.setItemWidget(row, col, button)
            self.layers.verticalScrollBar().setValue(position)

    def _refresh_regions(self):
        current = self._current_region_id()
        with QSignalBlocker(self.regions):
            self.regions.clear()
            for item in self.view.all_items():
                if item.record["kind"] != "region":
                    continue
                row = QTreeWidgetItem([self._record_name(item.record)])
                row.setData(0, Qt.ItemDataRole.UserRole, item.record["id"])
                row.setIcon(0, icon("region", ACCENT))
                self.regions.addTopLevelItem(row)
                if item.record["id"] == current:
                    self.regions.setCurrentItem(row)
            if self.regions.currentItem() is None and self.regions.topLevelItemCount():
                self.regions.setCurrentItem(self.regions.topLevelItem(0))

    def _layer_selection(self):
        if self._refreshing:
            return
        identifiers = [row.data(0, Qt.ItemDataRole.UserRole) for row in self.layers.selectedItems()]
        # 侧栏精确选取成员，不触发鼠标命中时的编组／附着扩选。
        self.view.board_scene.clearSelection()
        hidden = False
        for identifier in identifiers:
            item = self.view.by_id(identifier)
            if item is not None:
                item.setSelected(True)
                hidden = hidden or not item.isVisible()
        self.view.selection_changed.emit()
        if hidden:
            self.message("隐藏图层不可在画布选中，请先点击该行的显示按钮。")

    def _layer_renamed(self, row, column):
        if self._refreshing or column != 0:
            return
        identifier = row.data(0, Qt.ItemDataRole.UserRole)
        self._patch_ids([identifier], {"name": row.text(0).strip() or "未命名图层"}, allow_locked=True)

    def _rename_selected(self):
        if self._refreshing:
            return
        items = self.view.selected()
        if len(items) == 1 and not items[0].record.get("locked"):
            self._patch_ids([items[0].record["id"]], {"name": self.name_edit.text().strip() or "未命名图层"})

    def _toggle_layer(self, identifier, key):
        item = self.view.by_id(identifier)
        if item is not None:
            self._patch_ids([identifier], {key: not bool(item.record.get(key))}, allow_locked=True)

    def _patch_ids(self, identifiers, updates, allow_locked=False):
        def patch():
            with self._transaction():
                for identifier in identifiers:
                    item = self.view.by_id(identifier)
                    if item is None or (item.record.get("locked") and not allow_locked):
                        continue
                    record = item.to_record()
                    record.update(deepcopy(updates))
                    item.apply_record(record)
        return self._guard("更新图元", patch)

    def _paint_color_button(self, button, value):
        color = QColor(value)
        transparent = color.alpha() == 0
        button.setText("透明" if transparent else color.name(QColor.NameFormat.HexRgb).upper())
        background = "#fafbf8" if transparent else f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"
        ink = "#667b6d" if transparent or color.lightnessF() > 0.57 or color.alphaF() < 0.5 else "#ffffff"
        button.setStyleSheet(f"QPushButton {{ background: {background}; color: {ink}; border: 1px {'dashed' if transparent else 'solid'} #494949; }}")

    def _choose_group_color(self):
        groups = {i.record.get("group") for i in self.view.selected()} - {None, ""}
        boards = [i for i in self.view.all_items() if i.record.get("group_background") and i.record.get("group") in groups]
        if not boards:
            return
        color = get_color(QColor(boards[0].record["fill"]), self, "编组底板颜色", alpha=False)
        if color.isValid():
            self._patch_ids([i.record["id"] for i in boards], {"fill": color.name()})

    def _choose_color(self, key):
        items = self.view.selected()
        boards = [i for i in items if i.record.get("group_background")]
        value = boards[0].record.get("fill","transparent") if key=="fill" and boards else items[0].record.get(key, "transparent") if items else self.view.style.get(key, "transparent")
        initial=QColor(value)
        # Picking a color after clearing must not inherit a zero alpha channel.
        if initial.alpha()==0:initial.setAlpha(255)
        color = get_color(initial, self, "选择颜色", alpha=True)
        if color.isValid():
            self._set_style(key, "transparent" if color.alpha() == 0 else color.name(QColor.NameFormat.HexArgb))

    def _color_menu(self, key, widget, point):
        menu = QMenu(self)
        menu.addAction("选择颜色…", lambda: self._choose_color(key))
        menu.addAction("设为透明", lambda: self._set_style(key, "transparent"))
        menu.exec(widget.mapToGlobal(point))

    def _set_style(self, key, value):
        if self._refreshing or value is None:
            return
        def update():
            with self._transaction():
                self.view.set_property(key, value)
            self._settings_timer.start()
            self._queue_refresh()
        self._guard("修改样式", update)

    def apply_preset(self):
        preset = deepcopy(self.presets[self.preset_combo.currentIndex()])
        def apply():
            with self._transaction():
                for key, value in preset.items():
                    self.view.set_property(key, value)
            self._settings_timer.start()
            self._queue_refresh()
        self._guard("应用样式", apply)

    def remember_preset(self):
        items = self.view.selected()
        source = items[0].to_record() if items else self.view.style
        self.presets[self.preset_combo.currentIndex()] = clean_style(source)
        self._save_settings()
        self.message("已记住当前样式；下次启动仍可使用。")

    def _continuous_changed(self, checked):
        self.view.continuous = checked
        self._settings_timer.start()

    def _filter_changed(self, _index):
        self.view.select_filter = self.filter_combo.currentData()
        self._settings_timer.start()
        self.message("选择过滤已切换。")

    def choose_tool(self, tool):
        if self._highlighter and tool != "highlighter":
            self.view.style.update(self._pen_style or {})
            self._highlighter = False
            self._pen_style = None
        if tool == "highlighter":
            if not self._highlighter:
                self._pen_style = deepcopy(self.view.style)
            self._highlighter = True
            self.view.style.update(stroke="#ddb437", stroke_width=22.0, opacity=0.35, end_arrow=False)
            self.view.set_tool("pen")
        elif tool == "image":
            self.view.set_tool("image")
            self.import_images()
            self.view.set_tool("select")
        elif tool == "magnifier":
            self.view.set_tool("magnifier")
            self.view.select_items([])
            self.tabs.setCurrentIndex(0)
            self.set_sidebar_visible(True)
            self.message("右侧选择圆形或矩形，然后在图片上拖出要放大的区域；Esc 退出。", 10000)
        else:
            self.view.set_tool(tool)
        if tool != "select":
            self.view.select_items([])
        self._queue_refresh()
        self._settings_timer.start()
        self.view.setFocus()

    def _tool_changed(self, tool):
        if self._highlighter and tool != "pen":
            self.view.style.update(self._pen_style or {})
            self._highlighter = False
            self._pen_style = None
        active = "highlighter" if self._highlighter and tool == "pen" else tool
        action = self.tool_actions.get(active)
        if action:
            action.setChecked(True)
        if hasattr(self, "guidance"):
            current = self.view.selected()
            kind = current[0].record["kind"] if tool == "select" and len(current)==1 else tool
            if kind == "pen" and tool == "select":
                guidance = "笔迹可移动、整体缩放 · E 橡皮擦 · 滚轮缩放画布"
            elif kind in ("text", "number"):
                guidance = "Ctrl+滚轮调整字号 · 滚轮缩放画布"
            elif kind == "eraser":
                guidance = "拖动局部擦除笔迹 · Ctrl+滚轮调整橡皮擦大小"
            elif kind in ("line","arrow","polyline","bezier","pen","rect","ellipse","region","magnifier"):
                guidance = "Ctrl+滚轮调整粗细 · 滚轮缩放画布"
                if kind in ("polyline","bezier"): guidance += " · 右键收笔 · Q 切回选择"
            else:
                guidance = "C+拖动裁切选中内容 · 滚轮缩放画布 · 中键平移"
            self.guidance.setText(f"{KINDS.get(kind if tool == 'select' and current else active, '选择')}工具  ·  {guidance}")

    def _apply_crop(self):
        if self._refreshing:
            return
        selected = self.view.selected()
        if len(selected) != 1 or selected[0].record["kind"] not in ("image", "magnifier"):
            return
        x, y, w, h = [self.crop_controls[key][1].value() / 100 for key in ("x", "y", "w", "h")]
        self._patch_ids([selected[0].record["id"]], {"crop": [x, y, min(w, 1 - x), min(h, 1 - y)]})
        self._queue_refresh()

    def _apply_diameter(self):
        if self._refreshing:
            return
        selected = self.view.selected()
        if len(selected) == 1 and selected[0].record["kind"] == "magnifier":
            diameter = self.diameter.value()
            r = selected[0].record
            height = diameter * r["h"] / max(.001, r["w"])
            self._patch_ids([r["id"]], {"w": diameter, "h": height})

    def _apply_text_width(self, value):
        if self._refreshing:
            return
        selected = self.view.selected()
        if len(selected) == 1 and selected[0].record["kind"] == "text" and not selected[0].record.get("locked"):
            self._flush_pending_wheel()
            self.view.finish_text()
            self._patch_ids([selected[0].record["id"]], {"w": value, "h": 1})

    def _edit_selected_text(self):
        selected = self.view.selected()
        if len(selected) == 1 and not selected[0].record.get("locked"):
            self._view_command("edit_text", selected[0])

    def _center(self):
        return self.view.mapToScene(self.view.viewport().rect().center())

    def _top_z(self):
        return max((item.zValue() for item in self.view.all_items()), default=0) + 1

    def _add_records(self, records):
        if not records:
            return
        with self._transaction():
            self.view.add_records(records, select=True)
        self.view.setFocus()

    def _layout_images(self, records, anchor=None):
        if not records:
            return records
        columns = math.ceil(math.sqrt(len(records)))
        rows = math.ceil(len(records) / columns)
        widths, heights = [0.0] * columns, [0.0] * rows
        for index, record in enumerate(records):
            original_w, original_h = record["w"], record["h"]
            ratio = 1.0
            record["scale"] = ratio
            record.setdefault("source_width", original_w)
            record.setdefault("source_height", original_h)
            widths[index % columns] = max(widths[index % columns], original_w * ratio)
            heights[index // columns] = max(heights[index // columns], original_h * ratio)
        gap = 8.0
        center = anchor if anchor is not None else self.view.insertion_point()
        left = center.x() - (sum(widths) + gap * (columns - 1)) / 2
        top = center.y() - (sum(heights) + gap * (rows - 1)) / 2
        z = self._top_z()
        for index, record in enumerate(records):
            col, row = index % columns, index // columns
            record.update(x=left + sum(widths[:col]) + col * gap,
                          y=top + sum(heights[:row]) + row * gap, z=z + index)
        return records

    def _dialog_dir(self):
        directory = self.settings.get("last_directory")
        if isinstance(directory, str) and Path(directory).is_dir():
            return directory
        return str(Path.home())

    def _remember_directory(self, path):
        self.settings["last_directory"] = str(Path(path).resolve().parent)
        self._settings_timer.start()

    def import_images(self, paths=None):
        anchor=self.view.insertion_point()
        if paths is None or isinstance(paths,bool):
            paths,_=QFileDialog.getOpenFileNames(self,"导入参考图片",self._dialog_dir(),"图片 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.gif)")
        if isinstance(paths,(str,Path)):paths=[str(paths)]
        if not paths:return False
        paths=list(paths)
        def load():
            records=[]
            try:
                for path in paths:
                    record=storage.import_image(path)
                    record.update(name=Path(path).stem,source_path=str(path));records.append(record)
                return records
            except Exception:
                assets.release_pending(records)
                raise
        self._remember_directory(paths[0])
        return self._queue_import(load,anchor)

    def _files_dropped(self, paths):
        projects = [str(path) for path in paths if str(path).lower().endswith(".fbboard")]
        images = [str(path) for path in paths if not str(path).lower().endswith(".fbboard")]
        if projects:
            if not self.open_project(projects[0]):
                return
            if len(projects) > 1:
                self.message("一次只打开一个项目；其余项目未载入。")
        if images:
            self.import_images(images)

    def new_text(self, text=None):
        if text is None:
            center = self.view.insertion_point()
            record = new_record("text", **self.view.style)
            record.update(x=center.x(), y=center.y(), w=1, h=1, z=self._top_z())
            self._add_records([record])
            self.view.edit_text(self.view.by_id(record["id"]))
            return True
        if not str(text).strip():
            return False
        def create():
            center = self.view.insertion_point()
            record = new_record("text", **self.view.style)
            record.update(text=str(text), x=center.x(), y=center.y(), w=1, h=1, z=self._top_z())
            self._add_records([record])
            return True
        return self._guard("新建文字", create)

    def _clone(self, records, dx=40, dy=40):
        clones = storage.clone_records(records, dx=dx, dy=dy)
        identifiers = {before["id"]: after["id"] for before, after in zip(records, clones)}
        # 存储层已重映射编组、附着和来源；连接线也必须指向新图元。
        for record in clones:
            for key in ("link_source", "link_target"):
                if key in record:
                    record[key] = identifiers.get(record[key], "")
        return clones

    def _clones_at_center(self, records, anchor=None):
        visible = [dict(record, hidden=False) for record in records]
        bounds = storage.records_bounds(visible)
        center = anchor if anchor is not None else self.view.insertion_point()
        clones = self._clone(records, center.x() - bounds.center().x(), center.y() - bounds.center().y())
        minimum_z = min((record.get("z", 0) for record in clones), default=0)
        z = self._top_z()
        for record in clones:
            record["z"] = z + record.get("z", 0) - minimum_z
        return clones

    def _output_background(self, records):
        groups = {r.get("group") for r in records}
        if len(groups) == 1 and next(iter(groups), None):
            group = next(iter(groups))
            board = next((i for i in self.view.all_items() if i.record.get("group_background") and i.record.get("group") == group), None)
            if board:
                return board.record.get("fill", "#202020")
        return "#202020"

    def copy_selection(self, configure=False):
        self.view.finish_text()
        records = self.view.selection_records()
        if not records:
            self.message("请先选择要复制的对象。")
            return False
        def copy():
            storage.clipboard_write(records, background=self._output_background(records), padding=0)
            self.message("已快速复制：内部可编辑，外部为适中尺寸图片。需要控制大小时使用右上角压缩台。")
            return True
        return self._guard("复制选中", copy)

    def reset_clipboard_options(self):
        self.settings.pop("clipboard_options", None)
        self._save_settings()
        self.message("复制始终保留原图分辨率，底色跟随编组或画布。")

    def _queue_import(self, function, anchor, rich=False):
        epoch=self._document_epoch
        self._import_seq+=1;sequence=self._import_seq
        self.message("正在后台载入图片，可以继续操作画布…")
        def ready(records,error):
            self._imports[sequence]=(records,error,epoch,anchor,rich)
            while self._import_next in self._imports:
                values=self._imports.pop(self._import_next);self._import_next+=1
                incoming,failure,document,position,editable=values
                if failure:
                    self._collect_assets([])
                    self.message("图片载入失败："+failure.strip().splitlines()[-1],10000)
                    continue
                if document!=self._document_epoch or self._closing:
                    assets.release_pending(incoming or []);continue
                try:
                    if incoming:
                        placed=self._clones_at_center(incoming,position) if editable else self._layout_images(incoming,position)
                        self._add_records(placed)
                        self.message(f"已载入 {len(placed)} 个对象。")
                except Exception as exc:
                    LOGGER.exception("插入图片失败");self.message(str(exc),10000)
                finally:assets.release_pending(incoming or [])
        self.jobs.submit(function,ready,10)
        return True

    def paste(self):
        # A colleague's share link: Ctrl+V anywhere on the canvas opens it.
        lan=getattr(self.host,'lan',None) if self.host is not None else None
        if lan is not None:
            text=QApplication.clipboard().text()
            if text and len(text)<4000:
                from lan_core import find_link
                if find_link(text):
                    return lan.open_link(text)
        anchor=self.view.insertion_point()
        try:payload=storage.capture_clipboard()
        except Exception as exc:
            self.message(f"无法读取剪贴板：{exc}");return False
        kind,value=payload
        if kind=="text":return self.new_text(value)
        if kind=="empty":return False
        if kind=="records":
            self._add_records(self._clones_at_center(value,anchor));return True
        return self._queue_import(lambda:storage.decode_clipboard(payload),anchor,kind=="records_json")

    def create_magnifier(self, source=None, region=None):
        if source is None:
            images = [item for item in self.view.selected() if item.record["kind"] == "image"]
            if len(images) != 1:
                self.message("请选择一张来源图片。")
                return False
            source = images[0]
        if region is None:
            self.choose_tool("magnifier")
            return True
        captured_region = QRectF(region)
        def create():
            source_record = source.to_record()
            bounds = source.sceneBoundingRect()
            from items import base64_to_image
            region = QRectF(captured_region).intersected(QRectF(0, 0, source_record["w"], source_record["h"]))
            if region.width() <= 0 or region.height() <= 0:
                return False
            sx, sy, sw, sh = source_record.get("crop") or [0, 0, 1, 1]
            rx, ry = region.x()/source_record["w"], region.y()/source_record["h"]
            rw, rh = region.width()/source_record["w"], region.height()/source_record["h"]
            if source_record.get("flip_x"): rx = 1-rx-rw
            if source_record.get("flip_y"): ry = 1-ry-rh
            crop = [sx+rx*sw, sy+ry*sh, rw*sw, rh*sh]
            image = base64_to_image(source_record["image"])
            pixel_w, pixel_h = crop[2]*image.width(), crop[3]*image.height()
            width = max(48.0, min(1200.0, region.width()*abs(source.scale())*2))
            height = width*pixel_h/max(.001, pixel_w)
            lens = new_record("magnifier", **self.view.style)
            if lens["magnifier_shape"] == "circle":
                width = height = min(width, height)
            lens.update(source_id=source_record["id"], image=source_record["image"], crop=crop,
                        flip_x=source_record.get("flip_x",False), flip_y=source_record.get("flip_y",False),
                        x=bounds.right() + 48, y=source.mapToScene(region.center()).y() - height / 2,
                        w=width, h=height, z=self._top_z() + 1, name="局部放大", opacity=1.0)
            origin, target = source.mapToScene(region.center()), QPointF(lens["x"] + width / 2, lens["y"] + height / 2)
            connector = new_record("line", x=origin.x(), y=origin.y(), w=target.x() - origin.x(), h=target.y() - origin.y(),
                                   stroke=lens["stroke"], stroke_width=2.0, z=lens["z"] - 1,
                                   points=[{"x": 0, "y": 0, "in": [0, 0], "out": [0, 0]},
                                           {"x": target.x() - origin.x(), "y": target.y() - origin.y(), "in": [0, 0], "out": [0, 0]}],
                                   link_source=source_record["id"], link_target=lens["id"], name="放大镜连接线")
            self._add_records([connector, lens])
            self.view.select_items([self.view.by_id(lens["id"])])
            self.message("已放大框选区域，保持原图比例；拖动镜面移动，拖动角点等比缩放。")
            return True
        return self._guard("创建放大镜", create)

    def compare_images(self):
        items = self.view.selected()
        if len(items) != 2 or any(item.record["kind"] != "image" for item in items):
            self.message("请恰好选中两张图片进行对比。")
            return False
        def compare():
            images = [storage.render_records([item.to_record()], background="transparent", padding=0) for item in items]
            CompareDialog(images[0], images[1], self).exec()
            return True
        return self._guard("图片对比", compare)

    def capture_screen(self):
        if self._capture_pending or self._capture is not None:
            return
        self._capture_pending = True
        self._capture_window_state = self.windowState()
        self.actions["capture"].setEnabled(False)
        self.hide()
        QTimer.singleShot(180, self, self._begin_capture)

    def _begin_capture(self):
        if self._closing:
            return
        try:
            self._capture = CaptureOverlay()
            self._capture.captured.connect(self._captured)
            self._capture.canceled.connect(self._restore_after_capture)
            self._capture_pending = False
            self._capture.show()
            self._capture.raise_()
            self._capture.activateWindow()
        except Exception as exc:
            LOGGER.exception("区域截图失败")
            self._restore_after_capture()
            QMessageBox.warning(self, "无法截图", str(exc))

    def _restore_after_capture(self):
        overlay, self._capture = self._capture, None
        self._capture_pending = False
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
        self.setWindowState(getattr(self, "_capture_window_state", Qt.WindowState.WindowNoState))
        self.show()
        self.raise_()
        self.activateWindow()
        self.actions["capture"].setEnabled(True)
        self.view.setFocus()

    def _captured(self, image):
        self._restore_after_capture()
        def insert():
            if image.isNull():
                raise ValueError("截图为空，请重新选择区域。")
            encoded = image_to_base64(image)
            if not encoded:
                raise ValueError("截图编码失败。")
            record = new_record("image", w=image.width(), h=image.height(), image=encoded, name="区域截图")
            self._add_records(self._layout_images([record]))
            self.message("区域截图已放入画布。")
        self._guard("插入截图", insert)

    def _confirm_unsaved(self):
        if self._save_wait_loop is not None:
            return False
        if self._manual_save_busy and not self._wait_for_save():
            return False
        self.view.finish_text()
        if self.view.active_path is not None: self.view.finish_path()
        self._flush_pending_wheel()
        self.checkpoint()
        if not self.dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("保存本次修改？")
        box.setText("当前画布有尚未保存的修改。")
        box.setInformativeText("保存为可继续编辑的 .fbboard 项目，或明确放弃本次修改。")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Save).setText("保存")
        box.button(QMessageBox.StandardButton.Discard).setText("不保存")
        box.button(QMessageBox.StandardButton.Cancel).setText("取消")
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        result = box.exec()
        if result == QMessageBox.StandardButton.Cancel:
            return False
        if result == QMessageBox.StandardButton.Save:
            return self.save_project(wait=True) and not self.dirty
        return True

    def new_project(self):
        if self.host is not None: return self.host.new_document()
        if not self._confirm_unsaved():
            return False
        def create():
            self._load_document([], {}, None, clean=True)
            self.view.reset_zoom()
            self.view.centerOn(0, 0)
            self._clear_recovery()
            self.message("已新建空白画布。")
            return True
        return self._guard("新建画布", create)

    def _load_document(self, records, meta, path, clean=True):
        self._document_epoch+=1
        previous = self.view.records()
        previous_view = self.view.view_meta()
        self._loading += 1
        try:
            self.view.cancel_gesture()
            self.view.load_records(records)
            self.view.update_links()
            if isinstance(meta.get("view"), dict):
                self.view.restore_view(meta["view"])
            else:
                self.view.focus_items()
        except Exception:
            self.view.load_records(previous)
            self.view.restore_view(previous_view)
            raise
        finally:
            self._loading -= 1
        self.current_file = str(path) if path else None
        self._legacy_source = self.current_file if path and meta.get("app_version") != contracts.APP_VERSION else None
        self._reset_history(clean)
        self.view.set_tool("select")
        self._queue_refresh()

    def open_project(self, path=None):
        if self.host is not None: return self.host.open_document(path)
        if path is None or isinstance(path, bool):
            path, _ = QFileDialog.getOpenFileName(self, "打开反馈项目", self._dialog_dir(), "反馈画布项目 (*.fbboard)")
        if not path:
            return False
        if not self._confirm_unsaved():
            return False
        def load():
            records, meta = storage.load_project(str(path))
            is_recovery = Path(path).resolve() == self.recovery_path.resolve()
            self._load_document(records, meta, None if is_recovery else path, clean=not is_recovery)
            if not is_recovery:
                self._clear_recovery()
                self._remember_project(path)
            self._remember_directory(path)
            self.message(f"已打开 {Path(path).name}")
            return True
        return self._guard("打开项目", load)

    def _recent_projects(self):
        stored = self.settings.get("recent_projects", [])
        if not isinstance(stored, list):
            return []
        result, seen = [], set()
        for value in stored:
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                path = os.path.abspath(value)
                key = os.path.normcase(path)
            except (OSError, ValueError):
                continue
            if key not in seen and path.lower().endswith(".fbboard"):
                seen.add(key)
                result.append(path)
            if len(result) == 15:
                break
        return result

    def _remember_project(self, path):
        path = os.path.abspath(str(path))
        # 示例和自动恢复副本不是用户保存的项目，不放入历史记录。
        if Path(path).resolve() in (self.recovery_path.resolve(), resource_path("assets/demo.fbboard").resolve()):
            return
        key = os.path.normcase(path)
        self.settings["recent_projects"] = ([path] + [p for p in self._recent_projects()
                                            if os.path.normcase(p) != key])[:15]
        self._settings_timer.start()

    def _fill_recent_menu(self):
        self.recent_menu.clear()
        paths = self._recent_projects()
        if not paths:
            self.recent_menu.addAction("暂无记录，打开或保存项目后会显示在这里").setEnabled(False)
            return
        for value in paths:
            path = Path(value)
            label = f"{path.name}  —  {path.parent}"
            action = self.recent_menu.addAction(label.replace("&", "&&"))
            action.setToolTip(value)
            action.triggered.connect(lambda _checked=False, file=value: self._open_recent_project(file))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("清空最近打开记录", self._clear_recent_projects)

    def _open_recent_project(self, path):
        if not Path(path).is_file():
            QMessageBox.information(self, "找不到项目", f"文件可能已移动或删除，请通过“打开项目”重新选择。\n\n{path}")
            return
        self.open_project(path)

    def _clear_recent_projects(self):
        self.settings["recent_projects"] = []
        self._settings_timer.start()
        self.message("已清空最近打开记录，项目文件未删除。")

    def _project_meta(self):
        return {"view": self.view.view_meta(), "app": contracts.APP_NAME,
                "app_version": contracts.APP_VERSION, "saved_at": time.time()}

    def _on_save_progress(self,token,value,phase):
        if token==self._save_token and self._manual_save_busy:
            self.save_indicator.update_progress(min(value,99),phase)

    def _hide_save_indicator(self,token):
        if token==self._save_token and not self._manual_save_busy:
            self.save_indicator.hide()
            self.selection_status.show();self.guidance.show()

    def _wait_for_save(self):
        if not self._manual_save_busy:return self._save_outcome
        if self._save_wait_loop is not None:return False
        loop=QEventLoop(self);self._save_wait_loop=loop
        workspace_enabled=self.workspace_stack.isEnabled()
        toolbar_enabled=self.top_toolbar.isEnabled()
        self.workspace_stack.setEnabled(False);self.top_toolbar.setEnabled(False)
        self.save_events.finished.connect(loop.quit)
        try:
            if self._manual_save_busy:loop.exec()
        finally:
            self.save_events.finished.disconnect(loop.quit)
            self._save_wait_loop=None
            self.workspace_stack.setEnabled(workspace_enabled)
            self.top_toolbar.setEnabled(toolbar_enabled)
            loop.deleteLater()
        return self._save_outcome

    def save_project(self,path=None,save_as=False,wait=False):
        binding=getattr(self,"_lan_binding",None)
        if binding:return binding.save(path,save_as,wait)
        if self._manual_save_busy:
            self.view.finish_text();self.checkpoint()
            if self._current_digest!=self._saving_digest:self._save_again=True
            return self._wait_for_save() if wait else True
        if isinstance(path,bool):path=None
        destination=str(path) if path else self.current_file
        if self._legacy_source and not path:
            source=Path(self._legacy_source)
            destination=str(source.with_name(source.stem+"-v0.6.5"+source.suffix))
            save_as=True
        if not destination or save_as:
            default=destination or str(Path(self._dialog_dir())/"未命名画布.fbboard")
            destination,_=QFileDialog.getSaveFileName(self,"保存反馈项目",default,"反馈画布项目 (*.fbboard)")
        if not destination:return False
        if not destination.lower().endswith(".fbboard"):destination+=".fbboard"
        try:
            if self.host is not None and not self.host.can_save(self, destination):
                return False
            if Path(destination).resolve()==self.recovery_path.resolve():
                raise ValueError("该文件用于自动恢复，请另选项目名称。")
            self.view.finish_text();self.checkpoint()
            records=self.view.records()
            digest,epoch=self._current_digest,self._document_epoch
            meta=self._project_meta()
            pins=assets.pin(records)
        except Exception as exc:
            self.message(f"无法开始保存：{exc}",10000);return False
        self._manual_save_busy=True;self._save_outcome=False;self._save_again=False
        self._saving_digest=digest
        token=uuid.uuid4().hex;self._save_token=token
        self.selection_status.hide();self.guidance.hide()
        self.save_indicator.begin()
        last_report=[0.0,None]
        def report(value,phase):
            now=time.monotonic()
            if value<0 or value>=99 or now-last_report[0]>=.06 or last_report[1] is None:
                last_report[:]=[now,value]
                self.save_events.progress.emit(token,value,phase)
        def write():
            try:storage.save_project(destination,records,meta,progress=report)
            finally:assets.unpin(pins)
        def done(result,error):
            if self.host is not None: self.host._reserved.pop(self,None)
            self._manual_save_busy=False
            self._save_outcome=not bool(error)
            if error:
                LOGGER.error("项目保存失败：%s",error)
                self.save_indicator.finish(False,"保存失败，原文件未替换；请重试")
                self.message("保存失败："+error.strip().splitlines()[-1],15000)
                self._save_again=False
            else:
                if epoch==self._document_epoch:
                    self.current_file=destination;self._saved_digest=digest;self._legacy_source=None
                    self._remember_project(destination)
                    if not self.dirty: self._clear_recovery()
                    self._remember_directory(destination);self._update_title()
                if self._save_again and epoch==self._document_epoch and self._current_digest!=digest:
                    self._save_again=False
                    self.save_project(path=destination)
                    return
                phase="保存完成" if not self.dirty else "已保存快照，后续修改尚未保存"
                self.save_indicator.finish(True,phase)
                self.message(f"已保存 {Path(destination).name}"+("；仍有新修改未保存。" if self.dirty else ""),7000)
                QTimer.singleShot(4500,self,lambda:self._hide_save_indicator(token))
            self.save_events.finished.emit()
        self.jobs.submit(write,done,30)
        return self._wait_for_save() if wait else True

    def autosave(self):
        if self._closing or self._loading or self._recovery_protected or self._autosave_busy or self._manual_save_busy:
            return False
        if self.view.gesture or self.view.editor is not None or self.view.active_path is not None:
            return False
        if not self.dirty or self._current_digest==self._autosaved_digest:return False
        self._autosave_busy=True
        records=self._history[self._history_index]["records"]
        digest,epoch=self._current_digest,self._document_epoch
        saved_digest=self._saved_digest
        meta=self._project_meta();meta["recovered_from"]=self.current_file
        temporary=self.recovery_path.parent/("recovery-pending-"+uuid.uuid4().hex+".fbboard")
        pins=assets.pin(records)
        def write():
            try:storage.save_project(str(temporary),records,meta)
            finally:assets.unpin(pins)
        def done(result,error):
            self._autosave_busy=False
            if not error and not self._closing and epoch==self._document_epoch and saved_digest==self._saved_digest and self.dirty:
                try:
                    os.replace(temporary,self.recovery_path)
                    write_json(self.recovery_marker,{"dirty":True,"source":meta["recovered_from"],"updated_at":time.time()})
                    self._autosaved_digest=digest
                except Exception as exc:self.message(f"恢复副本保存失败：{exc}",10000)
            else:
                temporary.unlink(missing_ok=True)
                if error:self.message("后台恢复保存失败，请手动保存。",10000)
        self.jobs.submit(write,done,-5)
        return True

    def _clear_recovery(self):
        if self._recovery_protected:
            return
        try:
            if self.recovery_marker.exists():
                write_json(self.recovery_marker, {"dirty": False})
            if self.recovery_path.exists():
                self.recovery_path.unlink()
            self._autosaved_digest = None
        except OSError:
            LOGGER.exception("清理恢复副本失败")

    def recover_session(self):
        try:
            marker = read_json(self.recovery_marker)
        except (OSError, ValueError) as exc:
            self._recovery_protected = self.recovery_path.exists()
            self.message(f"恢复标记无法读取；旧恢复文件已保留：{exc}", 10000)
            return False
        if not marker.get("dirty") or not self.recovery_path.exists():
            return False
        box = QMessageBox(self)
        box.setWindowTitle("发现未保存的画布")
        box.setText("上次会话留下了一份自动恢复副本。")
        box.setInformativeText("是否恢复？恢复后需要手动另存；不会自动覆盖原项目。")
        recover = box.addButton("恢复画布", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("丢弃恢复副本", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(recover)
        box.exec()
        if box.clickedButton() is discard:
            self._clear_recovery()
            return False
        if box.clickedButton() is not recover:
            self._recovery_protected = True
            return False
        try:
            records, meta = storage.load_project(str(self.recovery_path))
            self._load_document(records, meta, None, clean=False)
            self._autosaved_digest = self._current_digest
            self.message("已恢复上次画布。请保存为项目；恢复副本可能不含最后 45 秒的修改。", 12000)
            return True
        except Exception as exc:
            self._recovery_protected = True
            LOGGER.exception("恢复画布失败")
            QMessageBox.warning(self, "无法恢复画布", f"旧恢复副本已保留，不会被覆盖。\n{exc}")
            return False

    def open_compression(self,selection=True):
        self.view.finish_text()
        records=self.view.selection_records() if selection else self.view.records()
        if not any(not r.get("hidden") for r in records):
            self.message("请先框选要打包的内容。");return False
        self.workspace_stack.setCurrentWidget(self.desk)
        self.top_toolbar.setEnabled(False)
        self.desk.open_records(records,self._output_background(records))
        if self.host is not None: self.host.position_overlay()
        return True

    def _leave_compression(self):
        self.workspace_stack.setCurrentWidget(self.board_workspace)
        self.top_toolbar.setEnabled(True)
        if self.host is not None: self.host.position_overlay()
        self.view.setFocus()

    def export_image(self,selection=False):
        return self.open_compression(selection or bool(self.view.selected()))

    def _fill_template_menu(self):
        self.template_menu.clear()
        self.template_menu.addAction("内置 · 箭头 + 文字", self.builtin_template)
        self.template_menu.addAction(self.actions["templates_save"])
        templates = sorted(self.templates_dir.glob("*.fbboard"), key=lambda path: path.name.casefold()) if self.templates_dir.exists() else []
        if templates:
            self.template_menu.addSeparator()
            for path in templates:
                self.template_menu.addAction(path.stem, lambda _checked=False, file=path: self.load_template(file))

    def builtin_template(self):
        def insert():
            group = str(uuid.uuid4())
            arrow = new_record("arrow", **self.view.style)
            arrow.update(x=0, y=60, w=140, h=-40, group=group,
                         points=[{"x": 0, "y": 0, "in": [0, 0], "out": [0, 0]},
                                 {"x": 140, "y": -40, "in": [0, 0], "out": [0, 0]}])
            text = new_record("text", **self.view.style)
            text.update(x=160, y=0, w=300, h=70, text="请在这里补充反馈", group=group)
            self._add_records(self._clones_at_center([arrow, text]))
        self._guard("添加内置模板", insert)

    def save_template(self):
        records = self.view.selection_records()
        if not records:
            self.message("先选择要保存为模板的图元。")
            return False
        name, accepted = QInputDialog.getText(self, "保存本地模板", "模板名称")
        if not accepted:
            return False
        name = name.strip()
        if (not name or len(name) > 80 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or name.endswith((".", " ")) or name in (".", "..")
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name)):
            QMessageBox.warning(self, "模板名称无效", "请输入 1–80 字的普通文件名，不要包含路径或系统保留字符。")
            return False
        path = self.templates_dir / (name + ".fbboard")
        if path.exists() and QMessageBox.question(self, "覆盖本地模板", f"“{name}”已存在，是否替换？",
                                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                                  QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return False
        def save():
            self.templates_dir.mkdir(parents=True, exist_ok=True)
            storage.save_project(str(path), records, {"template_name": name})
            self.message(f"已保存本地模板“{name}”。")
            return True
        return self._guard("保存模板", save)

    def load_template(self, path):
        def load():
            records, _meta = storage.load_project(str(path))
            self._add_records(self._clones_at_center(records))
            self.message("模板已以独立副本放到视口中心。")
            return True
        return self._guard("载入模板", load)

    def _current_region_id(self):
        item = self.regions.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def focus_region(self, identifier):
        region = self.view.by_id(identifier) if identifier else None
        if region is None:
            self.message("请先创建或选择一个区域。")
            return
        self.view.fitRegion(region)

    def copy_region(self, identifier):
        region = self.view.by_id(identifier) if identifier else None
        if region is None:
            self.message("请先创建或选择一个区域。")
            return False
        def copy():
            bounds = QRectF(0, 0, region.record["w"], region.record["h"])
            records = [item.to_record() for item in self.view.all_items()
                       if item is region or (item.isVisible() and
                                             bounds.contains(region.mapFromScene(item.sceneBoundingRect().center())))]
            storage.clipboard_write(records)
            self.message(f"已复制区域内 {len(records)} 个图元（含区域框）。")
            return True
        return self._guard("复制区域", copy)

    def context_menu(self, point):
        selected = self.view.selected()
        count = len(selected)
        images = [item for item in selected if item.record["kind"] == "image"]
        editable = bool(count) and not any(item.record.get("locked") for item in selected)
        menu = QMenu(self)
        menu.addAction(self.actions["paste"])
        for key in ("duplicate", "delete"):
            self.actions[key].setEnabled(editable)
            menu.addAction(self.actions[key])
        menu.addSeparator()
        self.actions["group"].setEnabled(count > 1 and editable)
        self.actions["ungroup"].setEnabled(editable)
        menu.addAction(self.actions["group"])
        menu.addAction(self.actions["ungroup"])
        menu.addAction("锁定选中", lambda: self._patch_selected({"locked": True}, True)).setEnabled(bool(count))
        menu.addAction("解锁选中", lambda: self._patch_selected({"locked": False}, True)).setEnabled(bool(count))
        menu.addAction("隐藏选中", lambda: self._patch_selected({"hidden": True}, True)).setEnabled(bool(count))
        menu.addAction("恢复全部隐藏", self.unhide_all)
        menu.addAction("解锁全部对象", lambda: self._patch_ids([i.record["id"] for i in self.view.all_items()], {"locked": False}, True))
        menu.addSeparator()
        menu.addAction("附着到选中图片", self.attach_selection).setEnabled(len(images) == 1 and count > 1 and editable)
        menu.addAction("解除附着", self.detach_selection).setEnabled(editable)
        menu.addAction("替换图片…（保留位置与批注）", self.replace_image).setEnabled(len(images) == 1 and editable)
        menu.addAction(self.actions["compare"])
        menu.addAction("创建联动放大镜", lambda: self.create_magnifier()).setEnabled(len(images) == 1)
        selection = menu.addMenu("选择")
        selection.addAction("全选", self.view.select_all)
        selection.addAction("只选图片", lambda: self.select_kind(True))
        selection.addAction("只选批注", lambda: self.select_kind(False))
        selection.addAction("取消选择", lambda: self.view.select_items([]))
        geometry = menu.addMenu("对齐、统一尺寸与分布")
        geometry.setEnabled(count > 1 and editable)
        for label, direction in (("左对齐", "left"), ("水平居中", "hcenter"), ("右对齐", "right"),
                                  ("顶对齐", "top"), ("垂直居中", "vcenter"), ("底对齐", "bottom")):
            geometry.addAction(label, lambda _checked=False, value=direction: self._view_command("align_selection", value))
        geometry.addSeparator()
        geometry.addAction("统一宽度", lambda: self._view_command("normalize_selection", "width"))
        geometry.addAction("统一高度", lambda: self._view_command("normalize_selection", "height"))
        geometry.addAction("自动排列", lambda: self._view_command("arrange_selection"))
        geometry.addAction("水平均匀分布", lambda: self.distribute("x")).setEnabled(count >= 3)
        geometry.addAction("垂直均匀分布", lambda: self.distribute("y")).setEnabled(count >= 3)
        transform = menu.addMenu("前后顺序与变换")
        transform.setEnabled(editable)
        transform.addAction("移到最前", lambda: self._view_command("change_z", True))
        transform.addAction("移到最后", lambda: self._view_command("change_z", False))
        transform.addSeparator()
        transform.addAction("水平翻转", lambda: self._view_command("flip_selection", "x"))
        transform.addAction("垂直翻转", lambda: self._view_command("flip_selection", "y"))
        transform.addAction("重置裁剪（同批内容一起恢复）", lambda: self._view_command("reset_crop"))
        transform.addAction("重置旋转与缩放", lambda: self._view_command("reset_transform"))
        menu.addSeparator()
        menu.addAction("编辑文字…", self._edit_selected_text).setEnabled(count == 1 and editable and selected[0].record["kind"] in ("text", "region", "number"))
        path = selected[0] if count == 1 and selected[0].record["kind"] in ("polyline", "bezier", "line", "arrow") else None
        menu.addAction("编辑路径节点", lambda: self._view_command("edit_nodes", path)).setEnabled(path is not None and editable)
        menu.addAction("完成当前路径", lambda: self._view_command("finish_path")).setEnabled(self.view.tool in ("polyline", "bezier"))
        menu.exec(self.view.viewport().mapToGlobal(point))

    def _patch_selected(self, updates, allow_locked=False):
        identifiers = [record["id"] for record in self.view.selection_records()]
        return self._patch_ids(identifiers, updates, allow_locked)

    def select_kind(self, images):
        self.view.select_items([item for item in self.view.all_items()
                                if (item.record["kind"] == "image") == images and item.isVisible()])

    def show_layers(self):
        self.set_sidebar_visible(True)
        self.tabs.setCurrentIndex(0)

    def attach_selection(self):
        items = self.view.selected()
        images = [item for item in items if item.record["kind"] == "image"]
        if len(images) != 1 or len(items) < 2:
            self.message("附着需要恰好一张图片及至少一个批注。")
            return False
        if any(item.record.get("locked") for item in items):
            self.message("请先解锁选中图层。")
            return False
        image = images[0]
        self._patch_ids([item.record["id"] for item in items if item is not image], {"attached_to": image.record["id"]})
        self.message("批注已附着到图片。")
        return True

    def detach_selection(self):
        return self._patch_selected({"attached_to": ""})

    def replace_image(self):
        images = [item for item in self.view.selected() if item.record["kind"] == "image"]
        if len(images) != 1 or images[0].record.get("locked"):
            self.message("请选择一张未锁定的图片。")
            return False
        path, _ = QFileDialog.getOpenFileName(self, "替换来源图片", self._dialog_dir(), "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff)")
        if not path:
            return False
        def replace():
            imported = storage.import_image(path)
            with self._transaction():
                record = images[0].to_record()
                record.update(image=imported["image"], source_path=path,
                              source_width=imported["w"], source_height=imported["h"])
                images[0].apply_record(record)
            self._remember_directory(path)
            self.message("已替换原图；ID、显示尺寸、变换、裁剪及批注关系均保留。")
            return True
        return self._guard("替换图片", replace)

    def unhide_all(self):
        self.view.annotations_visible = True
        self._patch_ids([item.record["id"] for item in self.view.all_items()], {"hidden": False}, True)

    def set_annotations_visible(self, visible):
        # 显隐作为可撤销的图层属性，不让临时不可见状态污染序列化结果。
        self.view.annotations_visible = True
        self._patch_ids([item.record["id"] for item in self.view.all_items() if item.record["kind"] != "image"],
                        {"hidden": not visible}, True)
        self.message("全部批注已显示。" if visible else "全部批注已隐藏；可撤销或通过“恢复全部隐藏”恢复。")

    def distribute(self, axis):
        def arrange():
            records = self.view.selection_records()
            items = {record["id"]: self.view.by_id(record["id"]) for record in records}
            items = {key: item for key, item in items.items() if item is not None and item.isVisible()
                     and not item.record.get("link_source")}
            parents = {key: key for key in items}
            def root(key):
                while parents[key] != key:
                    key = parents[key]
                return key
            groups = {}
            for key, item in items.items():
                group = item.record.get("group")
                if group:
                    if group in groups:
                        parents[root(key)] = root(groups[group])
                    else:
                        groups[group] = key
                owner = item.record.get("attached_to")
                if owner in items:
                    parents[root(key)] = root(owner)
            units = {}
            for key, item in items.items():
                units.setdefault(root(key), []).append(item)
            units = [unit for unit in units.values() if not any(item.record.get("locked") for item in unit)]
            if len(units) < 3:
                self.message("均匀分布需要至少三个独立且未锁定的图元或编组。")
                return False
            entries = []
            for unit in units:
                bounds = QRectF(unit[0].sceneBoundingRect())
                for item in unit[1:]:
                    bounds = bounds.united(item.sceneBoundingRect())
                entries.append((unit, bounds))
            horizontal = axis == "x"
            entries.sort(key=lambda entry: entry[1].left() if horizontal else entry[1].top())
            start = entries[0][1].left() if horizontal else entries[0][1].top()
            end = entries[-1][1].right() if horizontal else entries[-1][1].bottom()
            lengths = [bounds.width() if horizontal else bounds.height() for _, bounds in entries]
            gap = (end - start - sum(lengths)) / (len(entries) - 1)
            cursor = start
            with self._transaction():
                for (unit, bounds), length in zip(entries, lengths):
                    delta = cursor - (bounds.left() if horizontal else bounds.top())
                    for item in unit:
                        record = item.to_record()
                        record[axis] += delta
                        item.apply_record(record)
                    cursor += length + gap
            self.message("已按外边界均匀分布；编组和附着关系保持不变。")
            return True
        return self._guard("均匀分布", arrange)

    def dispatch_command(self, command):
        if command.startswith("tool:"):
            self.choose_tool(command.split(":",1)[1]);return
        dispatch = {
            "new": self.new_project, "open": self.open_project, "import": self.import_images,
            "save": self.save_project, "save_as": lambda: self.save_project(save_as=True),
            "export": self.export_image, "export_selection": lambda: self.export_image(True),
            "copy": self.copy_selection, "paste": self.paste, "undo": self.undo, "redo": self.redo,
            "fullscreen": self.toggle_fullscreen,
            "always_top": lambda: self.actions["always_top"].setChecked(not self.actions["always_top"].isChecked()),
            "new_text": self.new_text, "minimize": lambda: self.window().showMinimized(),
            "magnifier": lambda: self.choose_tool("magnifier"),
        }
        handler = dispatch.get(command)
        if handler is not None:
            self._flush_pending_wheel()
            self._guard("执行画布命令", handler)
        else:
            LOGGER.warning("未识别的画布命令：%s", command)
            self.message(f"未识别的画布命令：{command}")

    def set_zoom(self, factor):
        current = abs(self.view.transform().m11())
        if current:
            center = self.view.insertion_point()
            self.view.scale(factor / current, factor / current)
            self.view.centerOn(center)
        self._view_tick()

    def reset_view(self):
        self.view.reset_zoom()
        items = [item for item in self.view.all_items() if item.isVisible()]
        if items:
            bounds = QRectF(items[0].sceneBoundingRect())
            for item in items[1:]:
                bounds = bounds.united(item.sceneBoundingRect())
            self.view.centerOn(bounds.center())
        else:
            self.view.centerOn(0, 0)
        self._view_tick()

    def set_sidebar_visible(self, visible):
        if hasattr(self, "sidebar"):
            self.sidebar.setVisible(visible and not self.actions["minimal"].isChecked())
            self._settings_timer.start()

    def set_minimal(self, minimal):
        binding=getattr(self,"_lan_binding",None)
        if binding:binding.bar.setVisible(not minimal)
        self.top_toolbar.setVisible(not minimal)
        self.toolbox.setVisible(not minimal)
        self.sidebar.setVisible(not minimal and self.actions["sidebar"].isChecked())
        self.statusBar().setVisible(not minimal)
        self.restore_ui_button.hide()
        if self.host is not None: self.host.position_overlay()
        self.view.setFocus()

    def set_always_top(self, enabled):
        if self.host is not None:
            self.host.set_always_top(enabled)
            return
        state = self.windowState()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.setWindowState(state)
        self.show()
        self._settings_timer.start()
        self.message("窗口置顶已开启。" if enabled else "窗口置顶已关闭。")

    def toggle_fullscreen(self):
        if self.host is not None:
            self.host.toggle_fullscreen()
            return
        if self.isFullScreen():
            self.setWindowState(getattr(self, "_before_fullscreen", Qt.WindowState.WindowNoState))
            self.show()
        else:
            self._before_fullscreen = self.windowState()
            self.showFullScreen()

    def _view_tick(self):
        if self._closing or (self.host is not None and self.host.current_document() is not self):
            return
        self.zoom_button.setText(f"{abs(self.view.transform().m11()) * 100:.0f}%")
        meta = self.view.view_meta()
        if meta != self._last_view_meta:
            self._last_view_meta = deepcopy(meta)
            self._settings_timer.start()

    def _save_settings(self):
        if self.host is not None:
            self.host.save_preferences(self)
            return
        try:
            self.settings.update(style=clean_style(self._pen_style if self._highlighter else self.view.style),
                                 presets=self.presets, continuous=self.view.continuous,
                                 select_filter=self.view.select_filter, view=self.view.view_meta(), last_project=self.current_file,
                                 sidebar=self.actions["sidebar"].isChecked(),
                                 always_top=self.actions["always_top"].isChecked(),
                                 geometry=bytes(self.saveGeometry().toBase64()).decode("ascii"))
            snapshot=deepcopy(self.settings)
            if self._closing:
                write_json(self.settings_path,snapshot)
            else:
                self._settings_waiting=snapshot
                self._flush_settings_background()
            self._settings_error_reported = False
        except Exception as exc:
            LOGGER.exception("保存本地设置失败")
            if not self._settings_error_reported:
                self.message(f"无法保存本地设置：{exc}", 10000)
                self._settings_error_reported = True

    def _flush_settings_background(self):
        if self._settings_busy or self._settings_waiting is None:return
        snapshot=self._settings_waiting;self._settings_waiting=None;self._settings_busy=True
        def done(result,error):
            self._settings_busy=False
            if error:LOGGER.error("设置保存失败：%s",error)
            if not self._closing:self._flush_settings_background()
        self.jobs.submit(lambda:write_json(self.settings_path,snapshot),done,-10)

    def _position_empty_card(self):
        if hasattr(self, "empty_card"):
            bounds = self.view.viewport().rect()
            self.empty_card.move((bounds.width() - self.empty_card.width()) // 2,
                                 max(15, (bounds.height() - self.empty_card.height()) // 2 - 25))

    def eventFilter(self, watched, event):
        if watched is self.view.viewport() and event.type() == QEvent.Type.Resize:
            self._position_empty_card()
        return super().eventFilter(watched, event)

    def open_demo(self):
        if self.host is not None:
            return self.host.open_document(str(resource_path("assets/demo.fbboard")))
        candidates = [resource_path("assets/demo.fbboard"), _APP_DIR.parent / "outputs" / "示例反馈.fbboard"]
        path = next((p for p in candidates if p.is_file()), None)
        if path is not None and self._confirm_unsaved():
            records, meta = storage.load_project(str(path))
            self._load_document(records, meta, None, clean=True)
            self.view.focus_items()
            self.message("示例中的图片、文字、箭头和曲线都可编辑；保存时会另存为你的项目。")

    def show_help(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("反馈画布 · 使用指南")
        dialog.resize(730, 690)
        column = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet("QTextBrowser { border: none; background: #282828; padding: 18px; }")
        browser.setHtml("""
        <h2 style='color:#dedede'>反馈画布 / FEEDBACK STUDIO</h2>
        <p>本地图片与反馈批注工作台。没有账户或云同步；可使用系统截图后 Ctrl+V 粘贴。</p>
        <h3>项目与剪贴板</h3>
        <p>Ctrl+K 新建画布 · Ctrl+L 打开项目 · Ctrl+N 新建文字 · Ctrl+I 导入图片<br>
        Ctrl+S 保存 · Ctrl+Shift+S 另存为<br>
        右上角“打包已框选”进入压缩台；更多菜单可查看“最近打开”。<br>
        Ctrl+C 复制可编辑图元与图片 · Ctrl+V 粘贴图元、系统图片或纯文本<br>
        Ctrl+C 快速复制适中尺寸图片；右上角“打包已框选”进入压缩台，自行选择尺寸、质量、格式与文件名。底色跟随编组或画布。<br>
        粘贴与 Ctrl+N 新建文字出现在鼠标位置。Ctrl+G 编组自动添加窄边底板，右侧“组底色”可改颜色。<br>
        Ctrl+Z 撤销 · Ctrl+Shift+Z 重做（最多 60 个状态）。空白处单击或 Esc 一次取消选择。<br>
        画布快捷键仅在画布获得焦点时生效；文字编辑器内使用文字编辑操作，不触发画布命令。</p>
        <h3>PureRef 2.1 视图与鼠标操作</h3>
        <p>中键拖动或 Alt+左键拖动平移画布；Z+左键拖动缩放视图。<br>
        Space 聚焦选中项 · Ctrl+Space 聚焦全部 · Ctrl+0 恢复 100%。<br>
        鼠标左键选择、拖动与框选；Shift 配合选择或约束绘制。<br>
        滚轮缩放画布；Ctrl+滚轮调整文字字号或线宽。Ctrl+Shift+Alt+左键左右拖动调整选中内容透明度。<br>
        右键拖动窗口；右键单击打开菜单。Ctrl+F 全屏 · Ctrl+Shift+A 窗口置顶。<br>
        Tab 进入或退出极简模式，保留文件标签；底部倍率可精确选择缩放。</p>
        <h3>变换、裁剪与排列</h3>
        <p>Ctrl+Alt+左键拖动缩放图元；Ctrl+左键拖动旋转；Ctrl+Shift+左键拖动按 45° 吸附旋转。<br>
        C+左键拖动裁剪；V+左键拖动平移裁剪内容；Shift+V+左键拖动缩放裁剪内容。<br>
        Ctrl+Shift+C 重置裁剪 · Ctrl+Shift+T 重置变换。<br>
        Ctrl+G 编组 · Ctrl+Shift+G 解组 · Alt+L 锁定或解锁。<br>
        上/下方向键调整前后层级；Ctrl+方向键向对应边缘对齐；Ctrl+P 自动排列。<br>
        Ctrl+Alt+Left 统一高度 · Ctrl+Alt+Right 统一宽度。<br>
        Shift+Alt+H 水平翻转 · Shift+Alt+V 垂直翻转。</p>
        <h3>绘制与节点</h3>
        <p>左侧提供图片、文字、矩形、椭圆、直线、箭头、画笔、荧光笔、折线、贝塞尔曲线、编号、区域、放大镜和遮挡。<br>
        曲线钢笔逐次点击布点，自动平滑连接；选中曲线后随时拖动节点，双击曲线加点。Enter 完成，Esc 取消。旧项目的手动曲线仍保留控制柄。<br>箭头支持标准、细长、燕尾、双向、实心渐尖、空心渐尖六种样式，头部与线宽同步缩放。放大镜在图片上拖动框选，右侧可选择圆形或矩形，等比显示取样内容。<br>
        文字在画布内实时编辑，仅按 Enter 手动换行；拖动角点等比缩放文字。开启连续绘制可保持当前工具。</p>
        <h3>图片、编组与局部反馈</h3>
        <p>选中一张图片与若干批注 → 附着到选中图片。所有对象直接在画布上选择。<br>
        替换图片保留 ID、位置、显示尺寸、裁剪、变换和批注关系。重置裁剪与重置变换为独立操作。<br>
        选中图片后点击放大镜，在属性中调整原图取样的 x/y/宽/高百分比与镜面直径；连线随来源与放大镜更新。<br>
        恰好选中两张图片可进行并排、叠加或翻页对比。右键菜单提供对齐、统一尺寸、排列、分布与前后层级。</p>
        <h3>区域、模板与本地保存</h3>
        <p>区域页双击定位；复制区域包含区域框及中心落在区域内的可见图元，并非裁切导出。<br>
        文件标签支持多开、切换、拖动排序与关闭，每个文件独立撤销与自动恢复。<br>
        每 45 秒仅在内容变化时更新独立恢复副本，绝不自动覆盖源项目；恢复可能不含最后 45 秒的修改。<br>
        .fbboard 保留原始图像、全部批注和隐藏图层。遮挡只是可编辑覆盖层，不会擦除底图；敏感素材请只分享检查后的扁平化导出图，不要分享原项目。</p>
        """)
        column.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(dialog.reject)
        column.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        if not getattr(self,"_close_confirmed",False) and not self._confirm_unsaved():
            event.ignore()
            return
        self._closing = True
        self.desk.cancel_stage()
        self.jobs.pool.waitForDone()
        for pending in self.recovery_path.parent.glob("recovery-pending-*.fbboard"):
            pending.unlink(missing_ok=True)
        self._autosave_timer.stop()
        self._view_timer.stop()
        self._settings_timer.stop()
        self._save_settings()
        self._clear_recovery()
        if self._capture is not None:
            self._capture.hide()
            self._capture.deleteLater()
            self._capture = None
        event.accept()


def install_exception_hook(directory):
    LOGGER.setLevel(logging.INFO)
    if not LOGGER.handlers:
        try:
            handler = RotatingFileHandler(directory / "errors.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            LOGGER.addHandler(handler)
        except OSError:
            LOGGER.addHandler(logging.StreamHandler())
    showing_error = False
    def exception_hook(kind, value, traceback):
        nonlocal showing_error
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, traceback)
            return
        LOGGER.error("未处理的界面异常", exc_info=(kind, value, traceback))
        if QApplication.instance() and not showing_error:
            showing_error = True
            try:
                QMessageBox.critical(QApplication.activeWindow(), "操作发生异常",
                                     f"本次操作未完成，请先保存当前项目。\n\n{value}\n\n诊断目录：{directory}")
            finally:
                showing_error = False
    sys.excepthook = exception_hook


def main(argv=None):
    parser = argparse.ArgumentParser(description="反馈画布 / FEEDBACK STUDIO")
    parser.add_argument("project", nargs="?", help="要打开的 .fbboard 项目")
    parser.add_argument("--demo", action="store_true", help="打开随附的示例项目（若存在）")
    parser.add_argument("--smoke-test", metavar="REPORT", help=argparse.SUPPRESS)
    parser.add_argument("--lan-smoke-test", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    from lan_core import find_link
    share_link = find_link(arguments.project) if arguments.project else None
    if share_link:
        arguments.project = None
    QApplication.setOrganizationName("FeedbackStudio")
    QApplication.setApplicationName("FeedbackStudio-v0.6.5")
    QApplication.setApplicationDisplayName(contracts.APP_NAME)
    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    app.setFont(QFont(installed_fonts()[0][1], 10))
    try:
        from lan_ui import forward_link, register_url_scheme
        # A link clicked in a browser goes to the window that is already open.
        if share_link and not arguments.smoke_test and forward_link(share_link):
            return 0
        if getattr(sys, "frozen", False) and not arguments.smoke_test:
            register_url_scheme(sys.executable)
        directory = runtime_directory()
        install_exception_hook(directory)
        project = arguments.project
        if arguments.demo and not project:
            candidates = [resource_path("assets/demo.fbboard"), _APP_DIR.parent / "outputs" / "示例反馈.fbboard"]
            project = next((str(path) for path in candidates if path.is_file()), None)
        from workspace import WorkspaceWindow
        window = WorkspaceWindow(MainWindow, project)
        window.setWindowIcon(icon("region", ACCENT))
        window.show()
        if share_link:
            QTimer.singleShot(700, window, lambda: window.lan.open_link(share_link))
        if arguments.smoke_test:
            def smoke_check():
                try:
                    if window.current_document()._loading_project:
                        QTimer.singleShot(300, smoke_check)
                        return
                    preview = window.grab()
                    records = window.current_document().view.records()
                    lan_ok=None
                    if arguments.lan_smoke_test:
                        from lan_core import ShareService,ShareClient,file_hash
                        folder=directory/('lan-smoke-'+uuid.uuid4().hex);folder.mkdir()
                        source=folder/'source.fbboard';changed=folder/'changed.fbboard'
                        storage.save_project(str(source),[new_record('text',text='LAN smoke original')],{})
                        storage.save_project(str(changed),[new_record('text',text='LAN smoke edited')],{})
                        service=ShareService(source,folder/'server',storage.load_project)
                        try:
                            client=ShareClient(service.connection_code,'smoke')
                            client.download(folder/'download.fbboard');lease=client.acquire()['lease']
                            info=client.upload(changed,lease,0);client.release(lease)
                            lan_ok=info['revision']==1 and file_hash(source)==file_hash(changed)
                        finally:service.stop()
                    write_json(Path(arguments.smoke_test), {"ok": not preview.isNull() and lan_ok is not False, "objects": len(records), "frozen": bool(getattr(sys,"frozen",False)), "platform": app.platformName(), "version": contracts.APP_VERSION,"lan":lan_ok})
                    window._closing = True
                    app.exit(0 if not preview.isNull() else 2)
                except Exception:
                    import traceback
                    LOGGER.exception("Frozen smoke test failed")
                    write_json(Path(arguments.smoke_test), {"ok":False,"error":traceback.format_exc(),"version":contracts.APP_VERSION})
                    app.exit(3)
            QTimer.singleShot(600, smoke_check)
        if arguments.demo and not project:
            window.current_document().message("未找到随附示例，已打开空白画布。", 8000)
        return app.exec()
    except Exception as exc:
        LOGGER.exception("应用启动失败")
        QMessageBox.critical(None, "无法启动反馈画布", f"请确认本机数据目录可写、应用文件完整。\n\n{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
