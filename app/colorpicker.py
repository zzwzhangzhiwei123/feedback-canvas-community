"""Color picker with app-wide custom color slots.

Slots are stored once per user (not per project, tab or version):
    empty slot  -> click stores the current color (the current color is NOT changed)
    filled slot -> click selects it and loads its color; adjust; "保存" replaces it
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QColorDialog, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                               QLabel, QLayout, QMenu, QPushButton, QVBoxLayout, QWidget)

SLOTS = 16
COLUMNS = 8


def store_path():
    override = os.environ.get("FEEDBACK_DATA_DIR")
    if override:
        root = Path(override).expanduser() / "shared"
    else:
        # .../FeedbackStudio/FeedbackStudio-v0.x.y -> .../FeedbackStudio/shared (shared by all versions)
        root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)).parent / "shared"
    root.mkdir(parents=True, exist_ok=True)
    return root / "custom-colors.json"


def _valid(value):
    if not isinstance(value, str):
        return None
    color = QColor(value)
    return color.name(QColor.NameFormat.HexArgb) if color.isValid() else None


class CustomColors:
    """Single source of truth; reloaded from disk each time a picker opens."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.colors = [None] * SLOTS

    def _file(self):
        return self.path or store_path()

    def load(self):
        path = self._file()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                values = data.get("colors", []) if isinstance(data, dict) else []
                self.colors = [_valid(values[i]) if i < len(values) else None for i in range(SLOTS)]
                return self
            except (OSError, ValueError):
                pass
        # First run: keep colors the user already saved in Qt's own custom wells (default white = empty).
        migrated = [None] * SLOTS
        for i in range(min(SLOTS, QColorDialog.customCount())):
            color = QColorDialog.customColor(i)
            if color.isValid() and color.rgba() != 0xFFFFFFFF:
                migrated[i] = color.name(QColor.NameFormat.HexArgb)
        self.colors = migrated
        if any(migrated):
            self.save()
        return self

    def save(self):
        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".colors-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "colors": self.colors}, stream, ensure_ascii=False, indent=1)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def first_empty(self):
        return next((i for i, c in enumerate(self.colors) if c is None), None)


_STORE = None


def store():
    global _STORE
    if _STORE is None:
        _STORE = CustomColors()
    return _STORE


class SlotButton(QWidget):
    clicked = Signal(int)
    double_clicked = Signal(int)
    context = Signal(int, object)

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.color = None
        self.selected = False
        self.setFixedSize(QSize(30, 24))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, color, selected):
        self.color, self.selected = color, selected
        self.setToolTip("空格：点击把当前颜色存进来" if color is None else
                        f"{QColor(color).name(QColor.NameFormat.HexRgb).upper()} · 点击选中，调色后点“保存颜色”替换；右键清空")
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self.color is None:
            p.setBrush(QColor("#262626"))
            p.setPen(QPen(QColor("#5c5c5c"), 1, Qt.PenStyle.DashLine))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QPen(QColor("#6e6e6e"), 1.2))
            c = r.center()
            p.drawLine(c.x() - 4, c.y(), c.x() + 4, c.y())
            p.drawLine(c.x(), c.y() - 4, c.x(), c.y() + 4)
        else:
            color = QColor(self.color)
            if color.alpha() < 255:  # checkerboard behind translucent colors
                p.fillRect(r, QColor("#bdbdbd"))
                for x in range(int(r.left()), int(r.right()), 6):
                    for y in range(int(r.top()), int(r.bottom()), 6):
                        if (x // 6 + y // 6) % 2:
                            p.fillRect(QRectF(x, y, 6, 6).intersected(r), QColor("#ffffff"))
            p.setBrush(color)
            p.setPen(QPen(QColor("#5c5c5c"), 1))
            p.drawRoundedRect(r, 3, 3)
        if self.selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 4, 4)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        elif event.button() == Qt.MouseButton.RightButton:
            self.context.emit(self.index, event.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self.index)


def _find_layout(layout, widget):
    if layout is None:
        return None
    if layout.indexOf(widget) >= 0:
        return layout
    for i in range(layout.count()):
        child = layout.itemAt(i).layout()
        found = _find_layout(child, widget) if isinstance(child, QLayout) else None
        if found is not None:
            return found
    return None


class ColorPicker(QDialog):
    def __init__(self, initial=None, parent=None, title="选择颜色", alpha=True, colors=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.colors = colors or store()
        self.colors.load()
        self.selected = None
        column = QVBoxLayout(self)
        column.setContentsMargins(10, 10, 10, 10)
        self.picker = QColorDialog(self)
        options = QColorDialog.ColorDialogOption.DontUseNativeDialog | QColorDialog.ColorDialogOption.NoButtons
        if alpha:
            options |= QColorDialog.ColorDialogOption.ShowAlphaChannel
        self.picker.setOptions(options)
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        if initial is not None and QColor(initial).isValid():
            self.picker.setCurrentColor(QColor(initial))
        column.addWidget(self.picker)
        self._replace_builtin_custom_section()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)
        self.refresh()

    # -- construction ------------------------------------------------------
    def _replace_builtin_custom_section(self):
        wells = [w for w in self.picker.findChildren(QWidget) if "ColorWell" in w.metaObject().className()]
        custom = min(wells, key=lambda w: w.sizeHint().height()) if len(wells) >= 2 else None
        hidden = []
        for label in self.picker.findChildren(QLabel):
            if "custom colors" in label.text().replace("&", "").lower():
                hidden.append(label)
        for button in self.picker.findChildren(QPushButton):
            if "custom colors" in button.text().replace("&", "").lower():
                hidden.append(button)
        if custom is not None:
            hidden.append(custom)
        self.panel = QWidget()
        box = QVBoxLayout(self.panel)
        box.setContentsMargins(0, 4, 0, 0)
        box.setSpacing(5)
        title = QLabel("自定义颜色（所有项目通用）")
        title.setStyleSheet("font-weight:600;")
        box.addWidget(title)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.slots = []
        for i in range(SLOTS):
            slot = SlotButton(i)
            slot.clicked.connect(self.slot_clicked)
            slot.double_clicked.connect(self.slot_double_clicked)
            slot.context.connect(self.slot_menu)
            grid.addWidget(slot, i // COLUMNS, i % COLUMNS)
            self.slots.append(slot)
        box.addLayout(grid)
        row = QHBoxLayout()
        self.save_button = QPushButton("保存颜色")
        self.save_button.clicked.connect(self.save_current)
        self.clear_button = QPushButton("清空选中格")
        self.clear_button.clicked.connect(lambda: self.clear_slot(self.selected))
        row.addWidget(self.save_button, 1)
        row.addWidget(self.clear_button)
        box.addLayout(row)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#a8a8a8;")
        box.addWidget(self.hint)
        layout = _find_layout(self.picker.layout(), custom) if custom is not None else None
        if layout is not None:
            layout.insertWidget(layout.indexOf(custom), self.panel)
        else:
            self.layout().addWidget(self.panel)
        for widget in hidden:
            widget.hide()

    # -- behaviour ---------------------------------------------------------
    def current(self):
        return self.picker.currentColor()

    def refresh(self):
        for slot in self.slots:
            slot.set_state(self.colors.colors[slot.index], slot.index == self.selected)
        filled = self.selected is not None and self.colors.colors[self.selected] is not None
        self.save_button.setText("保存颜色（替换选中格）" if filled else "保存颜色（存入空格）")
        self.clear_button.setEnabled(filled)
        if filled:
            self.hint.setText("已选中一个颜色格：调好颜色后点“保存颜色”替换它。点空白格可另存一份。")
        elif self.colors.first_empty() is None:
            self.hint.setText("格子已满：先点选一个颜色格再保存来替换，或右键清空。")
        else:
            self.hint.setText("先调好颜色，再点任意空白格（或“保存颜色”）存起来。")

    def slot_clicked(self, index):
        value = self.colors.colors[index]
        if value is None:
            # Empty: store the color being edited. Never load anything into the picker.
            self.colors.colors[index] = self.current().name(QColor.NameFormat.HexArgb)
            self.colors.save()
        else:
            self.picker.setCurrentColor(QColor(value))
        self.selected = index
        self.refresh()

    def slot_double_clicked(self, index):
        if self.colors.colors[index] is not None:
            self.picker.setCurrentColor(QColor(self.colors.colors[index]))
            self.accept()

    def save_current(self):
        value = self.current().name(QColor.NameFormat.HexArgb)
        if self.selected is not None and self.colors.colors[self.selected] is not None:
            target = self.selected
        else:
            target = self.colors.first_empty()
            if target is None:
                self.hint.setText("格子已满：先点选一个颜色格再保存来替换，或右键清空。")
                return False
        self.colors.colors[target] = value
        self.colors.save()
        self.selected = target
        self.refresh()
        return True

    def clear_slot(self, index):
        if index is None or self.colors.colors[index] is None:
            return
        self.colors.colors[index] = None
        self.colors.save()
        if self.selected == index:
            self.selected = None
        self.refresh()

    def slot_menu(self, index, global_pos):
        if self.colors.colors[index] is None:
            return
        menu = QMenu(self)
        menu.addAction("清空此格", lambda: self.clear_slot(index))
        menu.exec(global_pos)

    def selected_color(self):
        return QColor(self.current())


def get_color(initial=None, parent=None, title="选择颜色", alpha=True):
    """Drop-in for QColorDialog.getColor; returns an invalid QColor when cancelled."""
    dialog = ColorPicker(initial, parent, title, alpha)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.selected_color()
        return QColor()
    finally:
        dialog.deleteLater()
