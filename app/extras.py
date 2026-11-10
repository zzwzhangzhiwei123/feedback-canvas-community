"""截图、图片对比与导出选项；截图只保存在内存中。"""

import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class CaptureOverlay(QWidget):
    captured = Signal(QImage)
    canceled = Signal()

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setWindowTitle("区域截图")
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._start = None
        self._selection = QRectF()
        self._dragging = False
        self._finished = False

        screens = QGuiApplication.screens()
        if not screens:
            raise RuntimeError("没有可用于截图的显示器。")
        self._virtual_geometry = QRect()
        for screen in screens:
            self._virtual_geometry = self._virtual_geometry.united(
                screen.virtualGeometry()
            )

        # 必须在任何覆盖窗口显示前完成所有屏幕抓取。
        grabs = [(screen.geometry(), screen.grabWindow(0)) for screen in screens]
        if any(pixmap.isNull() for _, pixmap in grabs):
            raise RuntimeError("无法读取屏幕画面，请检查系统的屏幕录制权限。")
        self._image = QImage(
            self._virtual_geometry.size(), QImage.Format.Format_ARGB32_Premultiplied
        )
        if self._image.isNull():
            raise RuntimeError("屏幕画面过大，无法分配截图内存。")
        self._image.setDevicePixelRatio(1.0)
        self._image.fill(QColor("#101827"))
        painter = QPainter(self._image)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for geometry, pixmap in grabs:
            source = pixmap.toImage()
            # 原始抓图是设备像素；统一映射到各屏幕的逻辑坐标及逻辑尺寸。
            source.setDevicePixelRatio(1.0)
            target = QRectF(geometry.translated(-self._virtual_geometry.topLeft()))
            painter.drawImage(target, source, QRectF(source.rect()))
        painter.end()

        active_screen = QGuiApplication.screenAt(QCursor.pos()) or screens[0]
        self._help_area = QRectF(
            active_screen.geometry().translated(-self._virtual_geometry.topLeft())
        )
        self.setGeometry(self._virtual_geometry)

    def showEvent(self, event):
        super().showEvent(event)
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _bounded_point(self, point):
        return QPointF(
            max(0.0, min(point.x(), float(self._image.width()))),
            max(0.0, min(point.y(), float(self._image.height()))),
        )

    def _capture_rect(self):
        if self._selection.isEmpty():
            return QRect()
        return self._selection.toAlignedRect().intersected(self._image.rect())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self._finished:
            self._start = self._bounded_point(event.position())
            self._selection = QRectF()
            self._dragging = True
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and not self._finished:
            self._selection = QRectF(
                self._start, self._bounded_point(event.position())
            ).normalized()
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._selection = QRectF(
                self._start, self._bounded_point(event.position())
            ).normalized()
            self._dragging = False
            self.update()
            self._finish()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _finish(self):
        rect = self._capture_rect()
        if self._finished or rect.isEmpty():
            return
        result = self._image.copy(rect)
        self._finished = True
        self._dragging = False
        self.hide()
        self.captured.emit(result)

    def _cancel(self):
        if self._finished:
            return
        self._finished = True
        self._dragging = False
        self.hide()
        self.canceled.emit()

    def closeEvent(self, event):
        self._cancel()
        event.accept()

    def _draw_badge(self, painter, text, x, y, bounds):
        metrics = painter.fontMetrics()
        width = min(float(metrics.horizontalAdvance(text) + 24), bounds.width())
        height = min(float(metrics.height() + 16), bounds.height())
        x = max(bounds.left(), min(x, bounds.right() - width))
        y = max(bounds.top(), min(y, bounds.bottom() - height))
        box = QRectF(x, y, width, height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(40, 40, 40, 235))
        painter.drawRoundedRect(box, 7, 7)
        painter.setPen(QColor("#dedede"))
        painter.drawText(
            box.adjusted(10, 0, -10, 0),
            Qt.AlignmentFlag.AlignCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(width - 20))),
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(QPointF(0, 0), self._image)
        bounds = QRectF(self._image.rect())
        selection = self._selection.intersected(bounds)
        shade = QColor(20, 20, 20, 145)
        if selection.isEmpty():
            painter.fillRect(bounds, shade)
        else:
            for area in (
                QRectF(0, 0, bounds.width(), selection.top()),
                QRectF(0, selection.bottom(), bounds.width(), bounds.height() - selection.bottom()),
                QRectF(0, selection.top(), selection.left(), selection.height()),
                QRectF(selection.right(), selection.top(), bounds.width() - selection.right(), selection.height()),
            ):
                painter.fillRect(area, shade)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#dedede"), 1))
            painter.drawRect(selection)
            painter.setPen(QPen(QColor("#4b4b4b"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(selection)
            capture_rect = self._capture_rect()
            self._draw_badge(
                painter,
                f"{capture_rect.width()} × {capture_rect.height()} 逻辑像素",
                selection.left(),
                selection.bottom() + 10,
                bounds.adjusted(8, 8, -8, -8),
            )
        help_text = "拖拽选择区域 · 松开鼠标或按 Enter 完成 · Esc 取消"
        self._draw_badge(
            painter,
            help_text,
            self._help_area.center().x() - (painter.fontMetrics().horizontalAdvance(help_text) + 24) / 2,
            self._help_area.top() + 24,
            self._help_area.adjusted(8, 8, -8, -8),
        )
        painter.end()


class _ComparisonView(QWidget):
    def __init__(self, image_a, image_b, parent=None):
        super().__init__(parent)
        self.images = (QImage(image_a), QImage(image_b))
        for image in self.images:
            image.setDevicePixelRatio(1.0)
        self.mode = "side"
        self.opacity = 0.5
        self.flipped = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(360, 220)

    def sizeHint(self):
        return QSize(960, 540)

    def _panel(self, painter, rect, title):
        painter.setPen(QPen(QColor("#494949"), 1))
        painter.setBrush(QColor("#2c2c2c"))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor("#dedede"))
        painter.drawText(
            rect.adjusted(12, 5, -12, 0).toRect(),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            painter.fontMetrics().elidedText(
                title, Qt.TextElideMode.ElideRight, max(0, int(rect.width() - 24))
            ),
        )
        area = rect.adjusted(10, painter.fontMetrics().height() + 17, -10, -10)
        painter.save()
        painter.setClipRect(area)
        painter.fillRect(area, QColor("#282828"))
        tile = 16
        for row in range(math.ceil(area.height() / tile)):
            for column in range(math.ceil(area.width() / tile)):
                if (row + column) % 2 == 0:
                    painter.fillRect(
                        QRectF(area.left() + column * tile, area.top() + row * tile, tile, tile),
                        QColor("#353535"),
                    )
        painter.restore()
        return area

    def _draw_image(self, painter, image, area, canvas_size=None, opacity=1.0):
        if image.isNull():
            return
        width, height = canvas_size or (image.width(), image.height())
        ratio = min(area.width() / width, area.height() / height)
        target = QRectF(0, 0, image.width() * ratio, image.height() * ratio)
        target.moveCenter(area.center())
        painter.save()
        painter.setClipRect(area)
        painter.setOpacity(opacity)
        painter.drawImage(target, image, QRectF(image.rect()))
        painter.restore()

    def _empty_label(self, painter, area, text):
        painter.setPen(QColor("#a8a8a8"))
        painter.drawText(area, Qt.AlignmentFlag.AlignCenter, text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.mode == "side":
            width = (bounds.width() - 16) / 2
            for index, image in enumerate(self.images):
                name = "A" if index == 0 else "B"
                title = f"图像 {name} · {image.width()} × {image.height()} 像素"
                rect = QRectF(bounds.left() + index * (width + 16), bounds.top(), width, bounds.height())
                area = self._panel(painter, rect, title)
                if image.isNull():
                    self._empty_label(painter, area, f"暂无图像 {name}")
                else:
                    self._draw_image(painter, image, area)
        else:
            canvas_size = (
                max(1, *(image.width() for image in self.images)),
                max(1, *(image.height() for image in self.images)),
            )
            if self.mode == "overlay":
                area = self._panel(painter, bounds, f"透明叠加 · B 图不透明度 {round(self.opacity * 100)}%")
                self._draw_image(painter, self.images[0], area, canvas_size)
                self._draw_image(painter, self.images[1], area, canvas_size, self.opacity)
                if all(image.isNull() for image in self.images):
                    self._empty_label(painter, area, "暂无可对比的图像")
            else:
                index = int(self.flipped)
                name = "B" if self.flipped else "A"
                area = self._panel(painter, bounds, f"翻页对比 · 当前显示图像 {name}")
                image = self.images[index]
                if image.isNull():
                    self._empty_label(painter, area, f"暂无图像 {name}")
                else:
                    self._draw_image(painter, image, area, canvas_size)
        painter.end()


class CompareDialog(QDialog):
    def __init__(self, image_a: QImage, image_b: QImage, parent=None):
        super().__init__(parent)
        self.setWindowTitle("图片对比")
        self.resize(1040, 720)
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("对比方式"))
        self._mode = QComboBox()
        self._mode.addItem("并排对比", "side")
        self._mode.addItem("透明叠加", "overlay")
        self._mode.addItem("翻页对比", "flip")
        self._mode.setAccessibleName("对比方式")
        toolbar.addWidget(self._mode)
        self._flip = QPushButton("当前 A · 点击切换 B")
        self._flip.setCheckable(True)
        self._flip.setEnabled(False)
        self._flip.setToolTip("翻页模式中切换图像 A 和 B，保持相同缩放与居中位置")
        toolbar.addWidget(self._flip)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        opacity_row = QHBoxLayout()
        self._opacity_label = QLabel("B 图不透明度：50%")
        self._opacity = QSlider(Qt.Orientation.Horizontal)
        self._opacity.setRange(0, 100)
        self._opacity.setValue(50)
        self._opacity.setEnabled(False)
        self._opacity.setAccessibleName("叠加模式的 B 图不透明度")
        self._opacity.setToolTip("0% 完全隐藏 B，100% 完整显示 B；A 始终作为底图")
        opacity_row.addWidget(self._opacity_label)
        opacity_row.addWidget(self._opacity, 1)
        layout.addLayout(opacity_row)
        self._view = _ComparisonView(image_a, image_b, self)
        layout.addWidget(self._view, 1)
        note = QLabel("等比例适配窗口；叠加和翻页按原始像素比例居中对齐。棋盘格表示透明区域。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._mode.currentIndexChanged.connect(self._change_mode)
        self._opacity.valueChanged.connect(self._change_opacity)
        self._flip.toggled.connect(self._change_flip)
        self._change_mode()

    def _change_mode(self, *_):
        mode = self._mode.currentData()
        self._view.mode = mode
        self._opacity.setEnabled(mode == "overlay")
        self._opacity_label.setEnabled(mode == "overlay")
        self._flip.setEnabled(mode == "flip")
        self._view.update()

    def _change_opacity(self, value):
        self._view.opacity = value / 100.0
        self._opacity_label.setText(f"B 图不透明度：{value}%")
        self._view.update()

    def _change_flip(self, checked):
        self._view.flipped = checked
        self._flip.setText("当前 B · 点击切换 A" if checked else "当前 A · 点击切换 B")
        self._view.update()


class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导出图片")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._scale = QDoubleSpinBox()
        self._scale.setRange(0.1, 8.0)
        self._scale.setDecimals(2)
        self._scale.setSingleStep(0.25)
        self._scale.setValue(1.0)
        self._scale.setSuffix(" 倍")
        self._scale.setToolTip("允许 0.10–8.00 倍；1 倍为原始尺寸")
        self._background = QComboBox()
        self._background.addItem("白色（#ffffff）", "#ffffff")
        self._background.addItem("透明", "transparent")
        self._background.addItem("浅灰（#f1f3f5）", "#f1f3f5")
        self._padding = QSpinBox()
        self._padding.setRange(0, 512)
        self._padding.setValue(24)
        self._padding.setSuffix(" 像素")
        self._padding.setToolTip("四周留白允许 0–512 像素")
        form.addRow("缩放倍率", self._scale)
        form.addRow("背景颜色", self._background)
        form.addRow("四周留白", self._padding)
        layout.addLayout(form)
        warning = QLabel(
            "提示：倍率范围为 0.10–8.00，留白范围为 0–512 像素。"
            "尺寸放大 2 倍，像素量与主要图像内存约增至 4 倍；"
            "大画布请降低倍率。透明背景需要 PNG 等支持透明通道的格式，JPEG 不保留透明。"
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #e2bb75; background: #3b3224; padding: 12px; border-radius: 6px;")
        layout.addWidget(warning)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导出")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def scale(self) -> float:
        return self._scale.value()

    @property
    def background(self) -> str:
        return self._background.currentData()

    @property
    def padding(self) -> int:
        return self._padding.value()
