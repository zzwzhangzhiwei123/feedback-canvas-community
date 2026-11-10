"""“图片”侧栏：只对选中的图片生效的非破坏性调色（随时可改、可重置、可撤销）。"""
from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSlider,
                               QVBoxLayout, QWidget)

import adjust

GROUPS = [
    ("浅色", [("brightness", "亮度", -100, 100), ("exposure", "曝光", -100, 100),
             ("contrast", "对比度", -100, 100), ("shadows", "阴影", -100, 100)]),
    ("颜色", [("saturation", "饱和度", -100, 100), ("warmth", "暖度", -100, 100),
             ("tint", "淡色", -100, 100)]),
    ("色阶", [("levels_black", "黑场", 0, 253), ("levels_gamma", "中间调", 10, 300),
             ("levels_white", "白场", 2, 255)]),
]
SHARPEN = [("sharpen_amount", "锐化强度", 0, 300), ("sharpen_radius", "锐化半径", 3, 50)]


def _fmt(key, value):
    if key == "levels_gamma":
        return f"{value / 100:.2f}"
    if key == "sharpen_radius":
        return f"{value / 10:.1f}"
    return str(int(value))


class CurveEditor(QWidget):
    changed = Signal(list)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.points = [[0, 0], [255, 255]]
        self.drag = None
        self.setFixedHeight(200)
        self.setMinimumWidth(180)
        self.setMouseTracking(True)
        self.setToolTip("点击添加控制点，拖动调整；双击或右键删除控制点")

    def set_points(self, points):
        self.points = [list(p) for p in points]
        self.update()

    def _box(self):
        side = min(self.width(), self.height()) - 8
        return QRectF((self.width() - side) / 2, 4, side, side)

    def _to_screen(self, p):
        b = self._box()
        return QPointF(b.left() + p[0] / 255 * b.width(), b.bottom() - p[1] / 255 * b.height())

    def _to_value(self, pos):
        b = self._box()
        x = round((pos.x() - b.left()) / b.width() * 255)
        y = round((b.bottom() - pos.y()) / b.height() * 255)
        return [max(0, min(255, x)), max(0, min(255, y))]

    def _hit(self, pos):
        for i, p in enumerate(self.points):
            if (self._to_screen(p) - pos).manhattanLength() < 10:
                return i
        return None

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        b = self._box()
        p.fillRect(b, QColor("#1f1f1f"))
        p.setPen(QPen(QColor("#3a3a3a"), 1))
        for i in range(1, 4):
            p.drawLine(QPointF(b.left() + b.width() * i / 4, b.top()), QPointF(b.left() + b.width() * i / 4, b.bottom()))
            p.drawLine(QPointF(b.left(), b.top() + b.height() * i / 4), QPointF(b.right(), b.top() + b.height() * i / 4))
        p.setPen(QPen(QColor("#555555"), 1, Qt.PenStyle.DashLine))
        p.drawLine(b.bottomLeft(), b.topRight())
        values = adjust.curve_values(self.points)
        path = QPainterPath()
        for x in range(256):
            pt = QPointF(b.left() + x / 255 * b.width(), b.bottom() - values[x] * b.height())
            path.moveTo(pt) if x == 0 else path.lineTo(pt)
        p.setPen(QPen(QColor("#e6e6e6"), 1.6))
        p.drawPath(path)
        for i, pt in enumerate(self.points):
            p.setBrush(QColor("#ffffff") if i == self.drag else QColor("#2a2a2a"))
            p.setPen(QPen(QColor("#e6e6e6"), 1.4))
            p.drawEllipse(self._to_screen(pt), 4.5, 4.5)
        p.setPen(QPen(QColor("#494949"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(b)

    def mousePressEvent(self, e):
        pos = e.position()
        i = self._hit(pos)
        if e.button() == Qt.MouseButton.RightButton:
            self._remove(i)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if i is None and self._box().adjusted(-4, -4, 4, 4).contains(pos) and len(self.points) < 16:
            v = self._to_value(pos)
            if all(abs(v[0] - q[0]) > 3 for q in self.points):
                self.points.append(v)
                self.points.sort()
                i = self.points.index(v)
                self.changed.emit(deepcopy(self.points))
        self.drag = i
        self.update()

    def mouseMoveEvent(self, e):
        if self.drag is None:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self._hit(e.position()) is not None else Qt.CursorShape.CrossCursor)
            return
        v = self._to_value(e.position())
        i = self.drag
        if i in (0, len(self.points) - 1):
            v[0] = self.points[i][0] if len(self.points) > 2 else v[0]
        lo = self.points[i - 1][0] + 1 if i > 0 else 0
        hi = self.points[i + 1][0] - 1 if i < len(self.points) - 1 else 255
        v[0] = max(lo, min(hi, v[0]))
        self.points[i] = v
        self.update()
        self.changed.emit(deepcopy(self.points))

    def mouseReleaseEvent(self, _e):
        if self.drag is not None:
            self.drag = None
            self.update()
            self.finished.emit()

    def mouseDoubleClickEvent(self, e):
        self._remove(self._hit(e.position()))

    def _remove(self, i):
        if i is None or i in (0, len(self.points) - 1):
            return
        self.points.pop(i)
        self.drag = None
        self.update()
        self.changed.emit(deepcopy(self.points))
        self.finished.emit()


class ImagePanel(QScrollArea):
    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setStyleSheet(".QWidget { background: #282828; }")
        col = QVBoxLayout(body)
        col.setContentsMargins(13, 14, 13, 14)
        col.setSpacing(6)
        self.hint = QLabel()
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        col.addWidget(self.hint)
        self.content = QWidget()
        box = QVBoxLayout(self.content)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        self.sliders = {}
        for title, rows in GROUPS:
            self._section(box, title)
            for key, label, lo, hi in rows:
                self._slider(box, key, label, lo, hi)
        self._section(box, "曲线")
        self.curve = CurveEditor()
        self.curve.changed.connect(lambda pts: self._set("curve", pts))
        self.curve.finished.connect(self._commit_soon)
        box.addWidget(self.curve)
        reset_curve = QPushButton("重置曲线")
        reset_curve.clicked.connect(lambda: (self.curve.set_points([[0, 0], [255, 255]]), self._set("curve", [[0, 0], [255, 255]]), self._commit_soon()))
        box.addWidget(reset_curve)
        self._section(box, "锐化")
        for key, label, lo, hi in SHARPEN:
            self._slider(box, key, label, lo, hi)
        box.addSpacing(12)
        self.reset = QPushButton("全部重置（恢复原图）")
        self.reset.clicked.connect(self.reset_all)
        box.addWidget(self.reset)
        note = QLabel("调整只影响显示与导出/复制，原图像素始终保留，可随时重置。双击滑块恢复默认。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        box.addWidget(note)
        col.addWidget(self.content)
        col.addStretch()
        self.setWidget(body)
        self.params = deepcopy(adjust.DEFAULTS)
        self._loading = False
        self._pending = False
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.setInterval(16)
        self._apply_timer.timeout.connect(self._apply_now)
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(450)
        self._commit_timer.timeout.connect(self._commit)
        self.refresh()

    # --- construction --------------------------------------------------------------------
    def _section(self, box, title):
        label = QLabel(title)
        label.setObjectName("section")
        label.setContentsMargins(0, 10, 0, 2)
        box.addWidget(label)

    def _slider(self, box, key, label, lo, hi):
        row = QHBoxLayout()
        name = QLabel(label)
        value = QLabel(_fmt(key, adjust.DEFAULTS[key]))
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(name)
        row.addStretch()
        row.addWidget(value)
        box.addLayout(row)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(int(adjust.DEFAULTS[key]))
        slider.setPageStep(10)

        def moved(v, key=key, value=value):
            value.setText(_fmt(key, v))
            self._set(key, v)

        slider.valueChanged.connect(moved)
        slider.sliderReleased.connect(self._commit_soon)
        slider.mouseDoubleClickEvent = lambda _e, s=slider, k=key: s.setValue(int(adjust.DEFAULTS[k]))
        box.addWidget(slider)
        self.sliders[key] = (slider, value)

    # --- selection -----------------------------------------------------------------------
    def targets(self):
        return [i for i in self.doc.view.selected() if i.record.get("kind") == "image" and not i.record.get("locked")]

    def refresh(self):
        items = self.targets()
        self.content.setEnabled(bool(items))
        if not items:
            self.hint.setText("选中一张或多张图片后，在这里调色。只作用于选中的图片。")
            return
        self.hint.setText(f"正在调整 {len(items)} 张图片。" if len(items) > 1 else "正在调整选中的图片。")
        if self._apply_timer.isActive() or self._commit_timer.isActive():
            return
        source = items[0].record.get("adjust")
        params = adjust.clean(source) or deepcopy(adjust.DEFAULTS)
        self.params = params
        self._loading = True
        try:
            for key, (slider, value) in self.sliders.items():
                slider.setValue(int(round(params[key])))
                value.setText(_fmt(key, params[key]))
            self.curve.set_points(params["curve"])
        finally:
            self._loading = False

    # --- editing -------------------------------------------------------------------------
    def _set(self, key, value):
        if self._loading:
            return
        self.params[key] = deepcopy(value)
        self._apply_timer.start()
        self._commit_timer.start()

    def _apply_now(self):
        cleaned = adjust.clean(self.params)
        for item in self.targets():
            record = item.to_record()
            if cleaned is None:
                record.pop("adjust", None)
            else:
                record["adjust"] = deepcopy(cleaned)
            item.apply_record(record)
        self.doc.view.viewport().update()

    def _commit_soon(self):
        if self._apply_timer.isActive():
            self._apply_timer.stop()
            self._apply_now()
        self._commit_timer.start(80)

    def _commit(self):
        if self._apply_timer.isActive():
            self._apply_timer.stop()
            self._apply_now()
        self.doc.checkpoint()

    def reset_all(self):
        self.params = deepcopy(adjust.DEFAULTS)
        self._loading = True
        try:
            for key, (slider, value) in self.sliders.items():
                slider.setValue(int(adjust.DEFAULTS[key]))
                value.setText(_fmt(key, adjust.DEFAULTS[key]))
            self.curve.set_points(adjust.DEFAULTS["curve"])
        finally:
            self._loading = False
        self._apply_now()
        self._commit()
