"""画布图元的本地几何、绘制与 JSON 记录转换。"""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from functools import lru_cache
from collections import OrderedDict
import math
import uuid
import os
import assets_runtime as assets

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QImageWriter,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPalette,
    QPen,
    QTextDocument,
    QTextOption,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

__all__ = ["BoardItem", "new_record", "image_to_base64", "base64_to_image"]

_KINDS = {
    "image", "text", "rect", "ellipse", "line", "arrow", "pen",
    "polyline", "bezier", "number", "region", "redact", "magnifier",
}
_PATH_KINDS = {"line", "arrow", "pen", "polyline", "bezier"}
_SIZES = {
    "image": (320, 200), "text": (220, 40), "rect": (180, 110),
    "ellipse": (160, 100), "line": (160, 0), "arrow": (160, 0),
    "pen": (160, 100), "polyline": (160, 100), "bezier": (160, 100),
    "number": (36, 36), "region": (400, 260), "redact": (180, 72),
    "magnifier": (180, 180),
}
_ALIGNMENT = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "center": Qt.AlignmentFlag.AlignHCenter,
    "right": Qt.AlignmentFlag.AlignRight,
}


def _finite(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _color(value, default="transparent"):
    color = QColor(value) if isinstance(value, str) else QColor()
    return color if color.isValid() else QColor(default)


_fonts_checked = False


def ensure_fonts():
    """Offscreen Windows Qt has no font discovery; read installed fonts, never bundle them."""
    global _fonts_checked
    if _fonts_checked:
        return
    _fonts_checked = True
    if os.name == "nt" and not QFontDatabase.families():
        root = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts")
        for filename in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "simkai.ttf", "arial.ttf", "arialbd.ttf"):
            path = os.path.join(root, filename)
            if os.path.isfile(path):
                QFontDatabase.addApplicationFont(path)


def new_record(kind, **kwargs):
    """创建独立的 JSON 记录；放大镜用 magnifier_shape 选择 circle 或 rect。"""
    if kind not in _KINDS:
        raise ValueError(f"不支持的图元类型：{kind}")
    width, height = _SIZES[kind]
    record = {
        "id": str(uuid.uuid4()), "kind": kind,
        "x": 0.0, "y": 0.0, "rotation": 0.0, "scale": 1.0,
        "z": -100.0 if kind == "region" else 0.0,
        "w": float(width), "h": float(height),
        "stroke": "#ef4444", "fill": "transparent", "stroke_width": 3.0,
        "opacity": 1.0, "text": "", "font_family": "Microsoft YaHei UI",
        "font_size": 18.0, "bold": False, "text_color": "#202938",
        "text_bg": "transparent", "align": "left", "text_outline": False,
        "corner_radius": 0.0,
        "points": [], "closed": False, "end_arrow": False,
        "arrow_style": "standard", "curve_mode": "manual",
        "image": "", "crop": None, "flip_x": False, "flip_y": False,
        "locked": False, "hidden": False, "group": "", "attached_to": "",
    }
    if kind in {"image", "text"}:
        record["stroke_width"] = 0.0
    elif kind == "number":
        record.update(text="1", fill="#ef4444", text_color="#ffffff",
                      font_size=16.0, bold=True, align="center", stroke_width=0.0)
    elif kind == "region":
        record.update(text="区域", stroke="#64748b", stroke_width=2.0,
                      font_size=14.0, text_bg="#e6ffffff")
    elif kind == "redact":
        record.update(fill="#000000", stroke="#000000", stroke_width=0.0)
    elif kind == "magnifier":
        record.update(source_id="", magnifier_shape="circle")
    record.update(deepcopy(kwargs))
    if kind in _PATH_KINDS and "points" not in kwargs:
        record["points"] = [
            {"x": 0.0, "y": 0.0, "in": [0.0, 0.0], "out": [0.0, 0.0]},
            {"x": _finite(record["w"]), "y": _finite(record["h"]),
             "in": [0.0, 0.0], "out": [0.0, 0.0]},
        ]
    return record


_recent_images = OrderedDict()

def cache_image(encoded, image):
    _recent_images[encoded] = QImage(image)
    _recent_images.move_to_end(encoded)
    while len(_recent_images) > 4:
        _recent_images.popitem(last=False)

def image_to_base64(image: QImage) -> str:
    """将 QImage 无损编码为 PNG；不创建需要 QApplication 的 QPixmap。"""
    if not isinstance(image, QImage) or image.isNull():
        return ""
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return ""
    try:
        writer = QImageWriter(buffer, b"png")
        writer.setCompression(1)  # Fast lossless PNG; preserve every input pixel.
        if not writer.write(image):
            return ""
        encoded = base64.b64encode(bytes(data)).decode("ascii")
        cache_image(encoded, image)
        return encoded
    finally:
        buffer.close()


def _decoded_png(encoded: str) -> QImage:
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or header.lower() != "data:image/png;base64":
            return QImage()
    try:
        data = base64.b64decode("".join(encoded.split()), validate=True)
    except (ValueError, binascii.Error):
        return QImage()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return QImage()
    return QImage.fromData(data, "PNG")


def base64_to_image(encoded: str) -> QImage:
    """解码失败返回空图像；返回隐式共享副本以保护有界缓存。"""
    if not isinstance(encoded, str) or not encoded:
        return QImage()
    if assets.is_ref(encoded): return assets.image(encoded)
    return QImage(_recent_images[encoded]) if encoded in _recent_images else assets.cached_decode(encoded, lambda: _decoded_png(encoded))


class BoardItem(QGraphicsObject):
    """不包含拖动、节点操作或选中装饰的可选中图元。"""

    def __init__(self, record):
        super().__init__()
        self.record = {}
        self._bounds = QRectF()
        self._rect = QRectF()
        self._path = QPainterPath()
        self._hit_path = QPainterPath()
        self._arrow = QPainterPath()
        self._nodes = []
        self._document = None
        self._image = QImage()
        self._source_rect = QRectF()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.apply_record(record)

    def setZValue(self, value):
        # Persist the original order while Qt renders separate background/image/annotation bands.
        self._logical_z = _finite(value)
        record = self.record
        tier = 0 if record.get("group_background") else 1 if record.get("kind") in {"image","magnifier"} else 2
        super().setZValue(tier * 1000000.0 + math.atan(self._logical_z / 1000.0) * 100000.0)

    def zValue(self):
        return getattr(self, "_logical_z", 0.0)

    def to_record(self):
        self.record.update(
            x=float(self.pos().x()), y=float(self.pos().y()),
            rotation=float(self.rotation()), scale=float(self.scale()),
            z=float(self.zValue()), opacity=float(self.opacity()),
            hidden=not self.isVisible(),
        )
        return deepcopy(self.record)

    def apply_record(self, record):
        values = deepcopy(record)
        kind = values.pop("kind", "rect")
        updated = new_record(kind, **values)
        self.prepareGeometryChange()
        self.record = updated
        self._rebuild()
        self.setPos(_finite(updated["x"]), _finite(updated["y"]))
        self.setRotation(_finite(updated["rotation"]))
        self.setScale(_finite(updated["scale"], 1.0))
        self.setZValue(_finite(updated["z"]))
        self.setOpacity(max(0.0, min(1.0, _finite(updated["opacity"], 1.0))))
        self.setVisible(not bool(updated["hidden"]))
        self.update()

    def refresh(self):
        self.apply_record(self.record)

    def boundingRect(self):
        return QRectF(self._bounds)

    def shape(self):
        return QPainterPath(self._hit_path)

    def visual_bounds(self):
        """Painted extent, without the invisible minimum hit-test padding."""
        bounds = self._path.boundingRect().united(self._arrow.boundingRect())
        margin = self._stroke_width / 2.0 if self._stroke.alpha() > 0 else 0.0
        bounds=bounds.adjusted(-margin,-margin,margin,margin)
        if self._selection_clip is not None:
            shape=QPainterPath();shape.addRect(bounds)
            return shape.intersected(self._selection_clip).boundingRect()
        return bounds

    def node_positions(self):
        return [QPointF(point) for point, _, _ in self._nodes]

    def _font(self):
        family = self.record.get("font_family") or "Microsoft YaHei UI"
        font = QFont(str(family))
        font.setPointSizeF(max(1.0, _finite(self.record["font_size"], 18.0)))
        font.setBold(bool(self.record["bold"]))
        return font

    def _make_document(self, width):
        document = QTextDocument()
        document.setUndoRedoEnabled(False)
        document.setDocumentMargin(0.0)
        document.setDefaultFont(self._font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap if self.record["kind"] == "text" else QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        option.setAlignment(_ALIGNMENT.get(self.record["align"], _ALIGNMENT["left"]))
        document.setDefaultTextOption(option)
        # 始终按纯文本处理，避免把反馈内容误解释成 HTML。
        document.setPlainText(str(self.record["text"]))
        if self.record["kind"] == "text":
            document.setTextWidth(-1)
        else:
            document.setTextWidth(max(0.1, width))
        return document

    def _auto_curve_handles(self):
        # Interpolating cubic spline: anchors remain on the curve. Chord-weighted
        # tangents limit overshoot when adjacent spans have very different lengths.
        nodes = self.record.get("points", [])
        if len(nodes) < 2:
            return
        closed = self.record.get("closed", False)
        for i, node in enumerate(nodes):
            p = QPointF(node["x"], node["y"])
            prev = nodes[(i-1) % len(nodes)] if closed or i else node
            nxt = nodes[(i+1) % len(nodes)] if closed or i+1 < len(nodes) else node
            a, b = QPointF(prev["x"], prev["y"]), QPointF(nxt["x"], nxt["y"])
            before, after = math.hypot((p-a).x(), (p-a).y()), math.hypot((b-p).x(), (b-p).y())
            tangent = (b-a) / max(1e-9, before+after)
            incoming, outgoing = tangent * (-before/3), tangent * (after/3)
            node["in"] = [incoming.x(), incoming.y()]
            node["out"] = [outgoing.x(), outgoing.y()]

    def _read_nodes(self):
        result = []
        nodes = self.record.get("points")
        if not isinstance(nodes, (list, tuple)):
            return result
        for node in nodes:
            if not isinstance(node, dict):
                continue
            offsets = []
            for key in ("in", "out"):
                value = node.get(key)
                if isinstance(value, (list, tuple)) and len(value) >= 2:
                    offsets.append(QPointF(_finite(value[0]), _finite(value[1])))
                else:
                    offsets.append(QPointF())
            result.append((QPointF(_finite(node.get("x")), _finite(node.get("y"))),
                           offsets[0], offsets[1]))
        return result

    def _add_segment(self, start, end):
        p0, _, outgoing = start
        p1, incoming, _ = end
        if self.record["kind"] == "bezier" or not outgoing.isNull() or not incoming.isNull():
            self._path.cubicTo(p0 + outgoing, p1 + incoming, p1)
        else:
            self._path.lineTo(p1)

    def _build_node_path(self):
        if not self._nodes:
            return
        self._path.moveTo(self._nodes[0][0])
        for start, end in zip(self._nodes, self._nodes[1:]):
            self._add_segment(start, end)
        if self.record["closed"] and len(self._nodes) > 1:
            self._add_segment(self._nodes[-1], self._nodes[0])
            self._path.closeSubpath()
        if len(self._nodes) == 1:
            # 单次落笔也应产生可见且可命中的圆点。
            radius = max(0.5, self._stroke_width / 2.0)
            self._path.addEllipse(self._nodes[0][0], radius, radius)
        if len(self._nodes) > 1 and (self.record["kind"] == "arrow" or self.record["end_arrow"]):
            self._build_arrow()

    def _build_arrow(self):
        width = self._stroke_width
        total = self._path.length()
        if width <= 0 or total <= 1e-8:
            return
        style = self.record.get("arrow_style", "standard")
        if style in {"tapered", "outline", "tapered_round", "outline_round"} and self.record["kind"] == "arrow":
            start,end=self._nodes[0][0],self._nodes[-1][0]
            delta=end-start;distance=math.hypot(delta.x(),delta.y())
            if distance<=1e-8:return
            u=delta/distance;n=QPointF(-u.y(),u.x())
            factor=min(1.0,distance/max(.001,width*9))
            head=width*6*factor;wing=width*2.5*factor;neck=width*.95*factor
            base=end-u*head
            tail = width * .18 * factor if style.endswith("_round") else 0.0
            self._arrow.moveTo(start+n*tail)
            self._arrow.lineTo(base+n*neck)
            self._arrow.lineTo(base+n*wing)
            self._arrow.lineTo(end)
            self._arrow.lineTo(base-n*wing)
            self._arrow.lineTo(base-n*neck)
            if tail:
                self._arrow.lineTo(start-n*tail)
                self._arrow.quadTo(start-u*tail*1.4, start+n*tail)
            self._arrow.closeSubpath()
            self._shaft=QPainterPath()
            return
        head_length = width * (5.5 if style == "slim" else 4.0)
        half_width = width * (1.15 if style == "slim" else 1.7)
        double = style == "double"
        # Scale down short arrows as a whole head, never let two heads overlap.
        factor = min(1.0, total / (head_length * (2.2 if double else 1.1)))
        head_length *= factor
        half_width *= factor
        def head(nodes):
            end, incoming, _ = nodes[-1]
            previous, _, outgoing = nodes[-2]
            candidates = [-incoming, end-(previous+outgoing)]
            candidates.extend(end-node[0] for node in reversed(nodes[:-1]))
            direction = next((v for v in candidates if math.hypot(v.x(), v.y()) > 1e-8), None)
            if direction is None:
                return
            unit = direction / math.hypot(direction.x(), direction.y())
            base = end - unit * head_length
            normal = QPointF(-unit.y(), unit.x()) * half_width
            self._arrow.moveTo(end)
            self._arrow.lineTo(base + normal)
            if style == "chevron":
                self._arrow.lineTo(end - unit * head_length * .65)
            self._arrow.lineTo(base - normal)
            self._arrow.closeSubpath()
        nodes = self._nodes + self._nodes[:1] if self.record["closed"] else self._nodes
        head(nodes)
        if double:
            head([(p, outgoing, incoming) for p, incoming, outgoing in reversed(nodes)])
        # The shaft must stop behind the point: a round cap at the tip made it blunt.
        trim = min(.49 if double else .95, head_length * .72 / total)
        self._shaft = self._path.trimmed(trim if double else 0, 1-trim)

    def _build_image(self):
        key = self.record["image"]
        if getattr(self, "_image_key", None) != key:
            self._image = base64_to_image(key)
            self._image_key = key
            self._preview = assets.image(key, preview=True) if assets.is_ref(key) else (
                self._image.scaled(1536,1536,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.FastTransformation)
                if max(self._image.width(),self._image.height()) > 1536 else self._image)
        self._source_rect = QRectF()
        if self._image.isNull():
            return
        x, y, width, height = 0.0, 0.0, 1.0, 1.0
        crop = self.record.get("crop")
        if isinstance(crop, (list, tuple)) and len(crop) == 4:
            x, y, width, height = (_finite(value) for value in crop)
        if width <= 0 or height <= 0:
            return
        left, top = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        right = max(0.0, min(1.0, x + width))
        bottom = max(0.0, min(1.0, y + height))
        if right > left and bottom > top:
            iw, ih = self._image.width(), self._image.height()
            self._source_rect = QRectF(left * iw, top * ih,
                                      (right - left) * iw, (bottom - top) * ih)

    def _rebuild(self):
        record = self.record
        kind = record["kind"]
        width = max(0.0, _finite(record["w"]))
        height = max(0.0, _finite(record["h"]))
        self._stroke_width = max(0.0, _finite(record["stroke_width"], 3.0))
        self._stroke = _color(record["stroke"], "#ef4444")
        self._fill = _color(record["fill"])
        self._text_color = _color(record["text_color"], "#202938")
        self._text_bg = _color(record["text_bg"])
        if kind == "bezier" and record.get("curve_mode") == "auto":
            self._auto_curve_handles()
        self._nodes = self._read_nodes()
        self._path = QPainterPath()
        self._arrow = QPainterPath()
        self._shaft = None
        self._document = None
        self._source_rect = QRectF()
        self._padding = (1.0 if record.get("text_outline") else 0.0) if kind == "text" else min(6.0, width / 4.0)
        self._title_rect = QRectF()
        if kind in {"text", "region"}:
            self._document = self._make_document(width - 2.0 * self._padding)
            document_height = self._document.size().height()
            if kind == "text":
                width = max(1.0, math.ceil(self._document.idealWidth() + 2.0 * self._padding))
                height = max(1.0, math.ceil(document_height + 2.0 * self._padding))
            elif record["text"]:
                self._title_rect = QRectF(
                    self._padding, self._padding,
                    max(0.0, width - 2.0 * self._padding),
                    min(document_height, max(0.0, height - 2.0 * self._padding)),
                )
        record["w"], record["h"] = width, height
        self._rect = QRectF(0.0, 0.0, width, height)
        if kind in _PATH_KINDS:
            self._build_node_path()
        elif kind == "number":
            diameter = min(width, height)
            self._path.addEllipse(QRectF((width - diameter) / 2.0,
                                         (height - diameter) / 2.0, diameter, diameter))
        elif kind == "ellipse" or (kind == "magnifier" and record["magnifier_shape"] != "rect"):
            if kind == "magnifier":
                diameter = min(width, height)
                self._rect = QRectF((width - diameter) / 2.0,
                                    (height - diameter) / 2.0, diameter, diameter)
            self._path.addEllipse(self._rect)
        elif kind == "rect" and (radius := _finite(record.get("corner_radius"))) > 0:
            self._path.addRoundedRect(self._rect, radius, radius)
        else:
            self._path.addRect(self._rect)
        if kind in {"image", "magnifier"}:
            # source_id 的解析归视图负责；本图元只绘制记录保留的原图。
            self._build_image()
            if kind == "magnifier" and not self._source_rect.isEmpty():
                if record["magnifier_shape"] == "rect":
                    height = width * self._source_rect.height() / self._source_rect.width()
                else:
                    height = width
                record["h"] = height
                self._rect = QRectF(0, 0, width, height)
                self._path = QPainterPath()
                if record["magnifier_shape"] == "rect":
                    self._path.addRect(self._rect)
                else:
                    self._path.addEllipse(self._rect)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(8.0, self._stroke_width + 4.0))
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._hit_path = stroker.createStroke(self._path)
        has_fill = self._fill.alpha() > 0
        if kind in {"image", "magnifier", "text", "number", "redact"} or has_fill:
            self._hit_path = self._hit_path.united(self._path)
        if kind in _PATH_KINDS and len(self._nodes) == 1:
            self._hit_path = self._hit_path.united(self._path)
        self._hit_path = self._hit_path.united(self._arrow)
        if not self._title_rect.isEmpty():
            title_path = QPainterPath()
            title_path.addRect(self._title_rect)
            self._hit_path = self._hit_path.united(title_path)
        self._bounds = self._hit_path.boundingRect().adjusted(-1.0, -1.0, 1.0, 1.0)
        entries=tuple(tuple(v) for v in record.get("erasures", [])) if kind=="pen" else ()
        previous=getattr(self,"_eraser_entries",())
        if not hasattr(self,"_eraser_path") or entries[:len(previous)]!=previous:
            self._eraser_path=QPainterPath();previous=()
        for x1,y1,x2,y2,radius in entries[len(previous):]:
            segment=QPainterPath(QPointF(x1,y1));segment.lineTo(QPointF(x2,y2))
            cutter=QPainterPathStroker();cutter.setWidth(radius*2)
            cutter.setCapStyle(Qt.PenCapStyle.RoundCap)
            cut=cutter.createStroke(segment)
            caps=QPainterPath();caps.addEllipse(QPointF(x1,y1),radius,radius)
            cut=cut.united(caps)
            caps=QPainterPath();caps.addEllipse(QPointF(x2,y2),radius,radius)
            cut=cut.united(caps)
            self._eraser_path=self._eraser_path.united(cut)
        self._eraser_entries=entries
        if kind=="pen":
            self._hit_path=self._hit_path.subtracted(self._eraser_path)

        self._selection_clip=None
        for entry in record.get("selection_clips",[]):
            polygon=entry["polygon"]
            clip=QPainterPath(QPointF(*polygon[0]))
            for vertex in polygon[1:]:clip.lineTo(QPointF(*vertex))
            clip.closeSubpath()
            self._selection_clip=clip if self._selection_clip is None else self._selection_clip.intersected(clip)
        if self._selection_clip is not None:
            self._hit_path=self._hit_path.intersected(self._selection_clip)
            self._bounds=self._hit_path.boundingRect()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape,self._selection_clip is not None)

    def _pen(self):
        if self._stroke_width <= 0 or self._stroke.alpha() == 0:
            return QPen(Qt.PenStyle.NoPen)
        pen = QPen(self._stroke, self._stroke_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if self.record["kind"] == "region":
            pen.setStyle(Qt.PenStyle.DashLine)
        return pen

    def _paint_document(self, painter, rect):
        if self._document is None or rect.isEmpty():
            return
        outlined = self.record["kind"] == "text" and bool(self.record.get("text_outline"))
        clip = rect.adjusted(-1.0, -1.0, 1.0, 1.0) if outlined else rect
        painter.save()
        painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
        painter.translate(rect.topLeft())
        context = QAbstractTextDocumentLayout.PaintContext()
        context.clip = clip.translated(-rect.topLeft())
        layout = self._document.documentLayout()
        if outlined:
            context.palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
            context.palette.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
            for dx, dy in ((-0.8, 0.0), (0.8, 0.0), (0.0, -0.8), (0.0, 0.8),
                           (-0.6, -0.6), (-0.6, 0.6), (0.6, -0.6), (0.6, 0.6)):
                painter.save()
                painter.translate(dx, dy)
                layout.draw(painter, context)
                painter.restore()
        context.palette.setColor(QPalette.ColorRole.Text, self._text_color)
        context.palette.setColor(QPalette.ColorRole.WindowText, self._text_color)
        layout.draw(painter, context)
        painter.restore()

    def _paint_image(self, painter):
        painter.save()
        painter.setClipPath(self._path, Qt.ClipOperation.IntersectClip)
        if self._image.isNull() or self._source_rect.isEmpty():
            painter.fillRect(self._rect, QColor("#f1f5f9"))
            painter.setPen(QPen(QColor("#94a3b8"), 1.0))
            painter.drawLine(self._rect.topLeft(), self._rect.bottomRight())
            painter.drawLine(self._rect.topRight(), self._rect.bottomLeft())
            painter.setFont(self._font())
            painter.setPen(QColor("#475569"))
            painter.drawText(self._rect, Qt.AlignmentFlag.AlignCenter, "图像不可用")
        else:
            center = self._rect.center()
            painter.translate(center)
            painter.scale(-1.0 if self.record["flip_x"] else 1.0,
                          -1.0 if self.record["flip_y"] else 1.0)
            painter.translate(-center)
            source = QRectF(self._source_rect)
            if self.record["kind"] == "magnifier":
                # Uniform scaling; a circular mask crops excess instead of stretching.
                ratio = self._rect.width() / max(.001, self._rect.height())
                center = source.center()
                if source.width() / source.height() > ratio:
                    source.setWidth(source.height() * ratio)
                else:
                    source.setHeight(source.width() / ratio)
                source.moveCenter(center)
            bitmap = self._image
            preview = getattr(self, "_preview", QImage())
            screen = painter.worldTransform().mapRect(self._rect)
            ratio = max(screen.width()/max(1,source.width()), screen.height()/max(1,source.height()))
            if not getattr(self,"export_render",False) and not preview.isNull() and ratio < preview.width()/max(1,self._image.width())*1.3:
                sx,sy=preview.width()/self._image.width(),preview.height()/self._image.height()
                source=QRectF(source.x()*sx,source.y()*sy,source.width()*sx,source.height()*sy)
                bitmap=preview
            painter.drawImage(self._rect, bitmap, source)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(self._pen())
        painter.drawPath(self._path)

    def _paint_number(self, painter):
        painter.setPen(self._pen())
        painter.setBrush(self._fill)
        painter.drawPath(self._path)
        circle = self._path.boundingRect()
        painter.save()
        painter.setClipPath(self._path, Qt.ClipOperation.IntersectClip)
        if self._text_bg.alpha() > 0:
            painter.fillPath(self._path, self._text_bg)
        font = self._font()
        metrics = QFontMetricsF(font)
        text = str(self.record["text"])
        factor = min(1.0, circle.width() * 0.78 / max(1.0, metrics.horizontalAdvance(text)),
                     circle.height() * 0.78 / max(1.0, metrics.height()))
        font.setPointSizeF(max(1.0, font.pointSizeF() * factor))
        painter.setFont(font)
        painter.setPen(self._text_color)
        painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def paint(self, painter, option, widget=None):
        painter.save()
        try:
            if self._selection_clip is not None:
                painter.setClipPath(self._selection_clip, Qt.ClipOperation.IntersectClip)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            kind = self.record["kind"]
            if kind in {"image", "magnifier"}:
                self._paint_image(painter)
            elif kind == "text":
                if self._text_bg.alpha() > 0:
                    painter.fillRect(self._rect, self._text_bg)
                if getattr(self,"_live_editor",None) is None:
                    self._paint_document(painter, self._rect.adjusted(
                        self._padding, self._padding, -self._padding, -self._padding))
            elif kind == "number":
                self._paint_number(painter)
            elif kind == "redact":
                # 遮挡颜色始终实心；图元整体透明度仍遵循契约中的 opacity。
                color = QColor(self._fill) if self._fill.alpha() > 0 else QColor("#000000")
                color.setAlpha(255)
                painter.fillRect(self._rect, color)
            else:
                if kind == "pen" and not self._eraser_path.isEmpty():
                    clip=QPainterPath();clip.addRect(self._bounds)
                    painter.setClipPath(clip.subtracted(self._eraser_path), Qt.ClipOperation.IntersectClip)
                painter.setPen(self._pen())
                painter.setBrush(self._fill if self._fill.alpha() > 0 else Qt.BrushStyle.NoBrush)
                if kind in _PATH_KINDS and len(self._nodes) == 1:
                    if self._stroke_width > 0:
                        painter.fillPath(self._path, self._stroke)
                else:
                    painter.drawPath(self._shaft if self._shaft is not None else self._path)
                if not self._arrow.isEmpty():
                    if self.record.get("arrow_style") in {"outline", "outline_round"} and kind == "arrow":
                        pen=QPen(self._stroke,self._stroke_width*.38)
                        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
                        painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawPath(self._arrow)
                    else:
                        painter.fillPath(self._arrow, self._stroke)
                if kind == "region" and not self._title_rect.isEmpty():
                    if self._text_bg.alpha() > 0:
                        painter.fillRect(self._title_rect, self._text_bg)
                    self._paint_document(painter, self._title_rect)
        finally:
            painter.restore()
