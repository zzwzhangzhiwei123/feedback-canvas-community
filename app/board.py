"""Infinite editable feedback canvas. PureRef-compatible navigation subset."""
from __future__ import annotations

from copy import deepcopy
import math
import uuid

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView, QGraphicsTextItem, QTextEdit, QMenu

from items import BoardItem, new_record, base64_to_image
from storage import clone_records

K = Qt.Key
M = Qt.KeyboardModifier
B = Qt.MouseButton
PATHS = {"line", "arrow", "pen", "polyline", "bezier"}
INK = PATHS | {"rect", "ellipse", "region", "magnifier"}


def point(x=0, y=0):
    return {"x": float(x), "y": float(y), "in": [0.0, 0.0], "out": [0.0, 0.0]}


def union(items):
    bounds = QRectF()
    for item in items:
        bounds = bounds.united(item.mapRectToScene(item.visual_bounds()))
    return bounds


class TextOverlay(QTextEdit):
    finished = Signal()

    def keyPressEvent(self, e):
        if e.key() == K.Key_Escape or (e.key() in (K.Key_Return, K.Key_Enter) and e.modifiers() & M.ControlModifier):
            self.finished.emit()
            e.accept()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.finished.emit()


class BoardScene(QGraphicsScene):
    def __init__(self,parent=None):
        super().__init__(parent);self.registry={}
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
    def addItem(self,item):
        super().addItem(item)
        if isinstance(item,BoardItem):self.registry[item.record["id"]]=item
    def removeItem(self,item):
        if isinstance(item,BoardItem):self.registry.pop(item.record["id"],None)
        super().removeItem(item)
    def clear(self):
        self.registry.clear();super().clear()

class LiveTextItem(QGraphicsTextItem):
    finished=Signal()
    def keyPressEvent(self,e):
        if e.key()==K.Key_Escape or (e.key() in (K.Key_Return,K.Key_Enter) and e.modifiers() & M.ControlModifier):
            self.finished.emit();e.accept();return
        super().keyPressEvent(e)
    def focusOutEvent(self,e):
        super().focusOutEvent(e);self.finished.emit()
    def paint(self,painter,option,widget=None):
        parent=self.parentItem()
        if parent is not None and parent.record.get("text_outline"):
            from PySide6.QtGui import QAbstractTextDocumentLayout, QPalette
            context=QAbstractTextDocumentLayout.PaintContext()
            context.palette.setColor(QPalette.ColorRole.Text,QColor("white"))
            for dx,dy in ((-.8,0),(.8,0),(0,-.8),(0,.8)):
                painter.save();painter.translate(dx,dy);self.document().documentLayout().draw(painter,context);painter.restore()
        super().paint(painter,option,widget)

class BoardView(QGraphicsView):
    changed = Signal()
    selection_changed = Signal()
    message = Signal(str)
    command = Signal(str)
    files_dropped = Signal(list)
    tool_changed = Signal(str)
    magnifier_region = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board_scene = BoardScene(self)
        self.setScene(self.board_scene)
        self.setSceneRect(-5000000, -5000000, 10000000, 10000000)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.tool = "select"
        self.style = {"stroke": "#ef5252", "stroke_width": 3.0, "fill": "transparent", "text_color": "#273b37", "text_bg": "transparent", "font_family": "Microsoft YaHei", "font_size": 18.0, "bold": False, "align": "left", "opacity": 1.0, "end_arrow": False}
        self.eraser_size = 24.0
        self.continuous = True
        self.select_filter = "all"
        self.annotations_visible = True
        self.keys = set()
        self.gesture = None
        self.start = QPointF()
        self.start_v = QPoint()
        self.last_v = QPoint()
        self.last_pointer = None
        self._pan_origin = None
        self._pan_scroll_start = (0, 0)
        self.orig = {}
        self.active_path = None
        self.draw_item = None
        self.node_item = None
        self.active_node = -1
        self.node_handle = None
        self.box = None
        self.box_selected = []
        self.lens_source = None
        self.lens_rect = None
        self.editor = None
        self.editing_item = None
        self.previous_focus = None
        self._focus_ids = ()
        self._guides = []
        self._wheel_timer = QTimer(self)
        self._wheel_timer.setSingleShot(True)
        self._wheel_timer.setInterval(240)
        self._wheel_timer.timeout.connect(self._commit)
        self.board_scene.selectionChanged.connect(self._selection)
        self.centerOn(0, 0)

    def _selection(self):
        if self.node_item is not None and not self.node_item.isSelected():
            self.node_item = None
            self.active_node = -1
        selected = self.selected()
        if len(selected) == 1 and selected[0].record["kind"] in {"line", "arrow", "polyline", "bezier"} and not selected[0].record.get("locked"):
            self.node_item = selected[0]
        self.viewport().update()
        self.selection_changed.emit()

    def _raise_clicked_image(self, item):
        if item.record["kind"] in {"image", "magnifier"} and not item.record.get("locked"):
            top = max((i.zValue() for i in self.all_items() if i.record["kind"] in {"image", "magnifier"}), default=0)
            if item.zValue() < top: item.setZValue(top + 1)

    def all_items(self):
        return list(self.board_scene.registry.values())

    def selected(self):
        return [i for i in self.board_scene.selectedItems() if isinstance(i, BoardItem)]

    def by_id(self, identifier):
        return self.board_scene.registry.get(identifier)

    def records(self):
        return [i.to_record() for i in sorted(self.all_items(), key=lambda x: (x.zValue(), x.record["id"]))]

    def load_records(self, records):
        self.cancel_gesture()
        self.node_item = None
        self.active_node = -1
        self.board_scene.blockSignals(True)
        self.board_scene.clear()
        for record in records:
            self.board_scene.addItem(BoardItem(record))
        self.board_scene.blockSignals(False)
        self._selection()

    def restore_records(self, records):
        """Restore history without rebuilding unchanged images and stroke masks."""
        targets = {record["id"]: record for record in records}
        self.setUpdatesEnabled(False)
        self.board_scene.blockSignals(True)
        try:
            self.board_scene.clearSelection()
            self.node_item = None
            self.active_node = -1
            for item in self.all_items():
                if item.record["id"] not in targets:
                    self.board_scene.removeItem(item)
            for identifier, record in targets.items():
                item = self.by_id(identifier)
                if item is None:
                    self.board_scene.addItem(BoardItem(record))
                elif item.to_record() != record:
                    item.apply_record(record)
        finally:
            self.board_scene.blockSignals(False)
            self.setUpdatesEnabled(True)
        self.viewport().update()

    def add_records(self, records, select=True):
        created = []
        for record in records:
            item = BoardItem(record)
            self.board_scene.addItem(item)
            created.append(item)
        if select:
            self.select_items(created)
        self.viewport().update()
        return created

    def _expand(self, items, groups=True, attachments=True):
        result = {i.record["id"]: i for i in items if i is not None}
        while True:
            before = len(result)
            group_ids = {i.record.get("group") for i in result.values()} - {None, ""}
            for item in self.all_items():
                r = item.record
                if (groups and r.get("group") in group_ids) or (attachments and r.get("attached_to") in result):
                    if not r.get("hidden"):
                        result[r["id"]] = item
            if len(result) == before:
                return list(result.values())

    def select_items(self, items, extend=False):
        self.board_scene.blockSignals(True)
        if not extend:
            self.board_scene.clearSelection()
        for item in items:
            if item is not None:
                item.setSelected(True)
        self.board_scene.blockSignals(False)
        self._selection()

    def select_all(self):
        self.select_items([i for i in self.all_items() if self._eligible(i)])

    def selection_records(self, expand=True):
        items = self.selected()
        # Groups selected via canvas already have all members selected. Layer selection
        # permits copying an individual member without implicitly merging siblings.
        if expand:
            items = self._expand(items, groups=False)
        return [i.to_record() for i in items if i.isVisible()]

    def _eligible(self, item):
        return (item.isVisible() and not item.record.get("locked")
                and (self.select_filter == "all" or (self.select_filter == "images") == (item.record["kind"] == "image")))

    def _hit(self, pos):
        # Screen-sized tolerance for fine annotation strokes at low canvas zoom.
        q = 4.0 / self.zoom()
        candidates = self.board_scene.items(QRectF(pos.x()-q, pos.y()-q, 2*q, 2*q), Qt.ItemSelectionMode.IntersectsItemShape, Qt.SortOrder.DescendingOrder, self.transform())
        return next((i for i in candidates if isinstance(i, BoardItem) and self._eligible(i)), None)

    def insertion_point(self):
        pos = self.viewport().mapFromGlobal(QCursor.pos())
        if self.viewport().rect().contains(pos):
            self.last_pointer = QPoint(pos)
        else:
            pos = self.last_pointer if self.last_pointer is not None else self.viewport().rect().center()
        return self.mapToScene(pos)

    def zoom(self):
        return max(0.00001, abs(self.transform().m11()))

    def _zoom_at(self, factor, viewport_pos):
        before = self.mapToScene(viewport_pos)
        target = max(0.002, min(100.0, self.zoom() * factor))
        factor = target / self.zoom()
        self.scale(factor, factor)
        after = self.mapToScene(viewport_pos)
        center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(center + before - after)
        self.viewport().update()

    def reset_zoom(self):
        center = self.mapToScene(self.viewport().rect().center())
        self.setTransform(QTransform())
        self.centerOn(center)

    def view_meta(self):
        center = self.mapToScene(self.viewport().rect().center())
        return {"zoom": self.zoom(), "center": [center.x(), center.y()]}

    def restore_view(self, meta):
        zoom = max(0.002, min(100, float(meta.get("zoom", 1))))
        center = meta.get("center", [0, 0])
        self.setTransform(QTransform.fromScale(zoom, zoom))
        self.centerOn(float(center[0]), float(center[1]))

    def focus_items(self, items=None):
        items = [i for i in (self.all_items() if items is None else items) if i.isVisible()]
        if not items:
            self.reset_zoom()
            self.centerOn(0, 0)
            return
        rect = union(items)
        if not rect.isEmpty():
            self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
            if self.zoom() > 10:
                self.setTransform(QTransform.fromScale(10, 10))
                self.centerOn(rect.center())

    def _focus_toggle(self):
        ids = tuple(sorted(i.record["id"] for i in self.selected()))
        if self.previous_focus is not None and self._focus_ids == ids:
            self.restore_view(self.previous_focus)
            self.previous_focus = None
        else:
            self.previous_focus = self.view_meta()
            self._focus_ids = ids
            self.focus_items(self.selected() or None)

    def fitRegion(self, region):
        if region:
            self.focus_items([region])

    def set_tool(self, tool):
        if self.active_path is not None:
            self.finish_path()
        self.finish_text()
        self.tool = tool
        self.node_item = None
        self.active_node = -1
        self.setCursor(Qt.CursorShape.ArrowCursor if tool == "select" else Qt.CursorShape.CrossCursor)
        self.tool_changed.emit(tool)
        self.viewport().update()

    def _commit(self):
        self.update_links()
        self.changed.emit()
        self.viewport().update()

    def _snapshot(self):
        items = self._expand(self.selected(), groups=False)
        self.orig = {i.record["id"]: i.to_record() for i in items if not i.record.get("locked")}
        return [self.by_id(key) for key in self.orig]

    def set_property(self, key, value):
        self.style[key] = value
        backgrounds=[i for i in self.selected() if i.record.get("group_background")]
        if key=="fill" and backgrounds:
            for item in backgrounds:
                if not item.record.get("locked"):
                    record=item.to_record();record["fill"]=value;item.apply_record(record)
            self._commit();self.selection_changed.emit();return
        for item in self.selected():
            if item.record.get("locked") or item.record.get("group_background") or item.record["kind"] == "pen":
                continue
            r = item.to_record()
            r[key] = value
            if r["kind"] == "text" and key in {"font_size", "font_family", "text", "w"}:
                r["h"] = 1
            item.apply_record(r)
        self._commit()
        self.selection_changed.emit()

    def _new(self, kind, pos, **kwargs):
        values = deepcopy(self.style)
        values.update(x=pos.x(), y=pos.y(), z=max([i.zValue() for i in self.all_items()] + [0])+1)
        values.update(kwargs)
        if kind == "bezier":
            values["curve_mode"] = "auto"
        if kind == "region":
            values.update(z=-1000, fill="transparent", text="反馈分区", font_size=14)
        if kind == "number":
            nums = [int(i.record["text"]) for i in self.all_items() if i.record["kind"] == "number" and str(i.record["text"]).isdigit()]
            values.update(text=str(max(nums + [0])+1), w=36, h=36, fill=self.style["stroke"], text_color="#ffffff", font_size=16, stroke_width=0)
        if kind == "redact":
            values.update(fill="#202a29", stroke_width=0, opacity=1)
        if kind == "text":
            values.update(stroke_width=0, text="", h=1)
        item = BoardItem(new_record(kind, **values))
        self.board_scene.addItem(item)
        return item

    def _after_draw(self):
        # Keep the chosen tool. Clear finished nodes so the next press draws anew.
        self.select_items([])
        self.node_item = None
        self.active_node = -1

    def _path_click(self, pos):
        if self.active_path is None:
            self.active_path = self._new(self.tool, pos, points=[point()], w=1, h=1)
            self.select_items([self.active_path])
            self.active_node = 0
        else:
            local = self.active_path.mapFromScene(pos)
            points = self.active_path.record["points"]
            if len(points) > 2 and (self.active_path.mapToScene(QPointF(points[0]["x"], points[0]["y"]))-pos).manhattanLength()*self.zoom() < 12:
                self.active_path.record["closed"] = True
                self.active_path.refresh()
                self.finish_path()
                return
            points.append(point(local.x(), local.y()))
            self.active_node = len(points)-1
            self.active_path.refresh()
        self.node_item = self.active_path
        self.gesture = "path_handle"
        self.viewport().update()
        self.message.emit("点击打点自动连曲线；拖动节点调整；右键收笔，保持当前工具；双击 / Enter 收笔，Esc 取消")

    def finish_path(self):
        if self.active_path is None:
            return
        item = self.active_path
        points = item.record["points"]
        # Qt double-click includes one extra press; discard coincident terminal node.
        if len(points) >= 2 and abs(points[-1]["x"]-points[-2]["x"])+abs(points[-1]["y"]-points[-2]["y"]) < .01:
            points.pop()
        self.active_path = None
        self.gesture = None
        if len(points) < 2:
            self.board_scene.removeItem(item)
            self.node_item = None
        else:
            item.refresh()
            self.node_item = item
            self.select_items([item])
        self._after_draw()
        self._commit()

    def edit_nodes(self, item):
        if item and item.record["kind"] in PATHS - {"pen"} and not item.record.get("locked"):
            self.select_items([item])
            self.node_item = item
            self.active_node = -1
            self.tool = "select"
            self.tool_changed.emit(self.tool)
            self.message.emit("拖动节点 / 控制柄编辑；双击线段加点；Delete 删除选中节点；右键节点可切换平滑 / 尖角")
            self.viewport().update()

    def _node_handles(self):
        item = self.node_item
        if item is None:
            return []
        result = []
        for idx, p in enumerate(item.record["points"]):
            origin = QPointF(p["x"], p["y"])
            if item.record["kind"] == "bezier" and item.record.get("curve_mode") != "auto":
                for tag in ("in", "out"):
                    off = p.get(tag, [0, 0])
                    if abs(off[0])+abs(off[1]) > .001:
                        result.append((idx, tag, item.mapToScene(origin+QPointF(*off))))
            result.append((idx, "node", item.mapToScene(origin)))
        return result

    def _node_at(self, pos):
        handles = self._node_handles()
        # Node anchors take precedence if handles overlap.
        for idx, tag, p in sorted(handles, key=lambda h: h[1] != "node"):
            if (p-pos).manhattanLength()*self.zoom() < 12:
                return idx, tag
        return None

    def _smooth_node(self, smooth):
        item = self.node_item
        if item is None or self.active_node < 0:
            return
        r = item.to_record()
        r["curve_mode"] = "manual"
        nodes = r["points"]
        p = nodes[self.active_node]
        if smooth:
            a = nodes[max(0, self.active_node-1)]
            b = nodes[min(len(nodes)-1, self.active_node+1)]
            dx, dy = (b["x"]-a["x"])/6, (b["y"]-a["y"])/6
            p["in"], p["out"] = [-dx, -dy], [dx, dy]
            p["smooth"] = True
        else:
            p["in"], p["out"] = [0, 0], [0, 0]
            p["smooth"] = False
        item.apply_record(r)
        self._commit()

    def _insert_node(self, scene_pos):
        item = self.node_item
        if item is None:
            return
        nodes = item.record["points"]
        if len(nodes) < 2:
            return
        p = item.mapFromScene(scene_pos)
        best = (float("inf"), 0, .5)
        count = len(nodes) if item.record.get("closed") else len(nodes)-1
        def lerp(a, b, t):
            return a + (b-a)*t
        for idx in range(count):
            a, b = nodes[idx], nodes[(idx+1)%len(nodes)]
            p0, p3 = QPointF(a["x"], a["y"]), QPointF(b["x"], b["y"])
            p1, p2 = p0+QPointF(*a["out"]), p3+QPointF(*b["in"])
            for step in range(1, 80):
                t = step/80
                if item.record["kind"] == "bezier":
                    q = p0*((1-t)**3)+p1*(3*(1-t)**2*t)+p2*(3*(1-t)*t*t)+p3*(t**3)
                else:
                    q = lerp(p0,p3,t)
                dist = (q-p).manhattanLength()
                if dist < best[0]:
                    best = dist, idx, t
        _, idx, t = best
        a, b = nodes[idx], nodes[(idx+1)%len(nodes)]
        p0, p3 = QPointF(a["x"],a["y"]), QPointF(b["x"],b["y"])
        if item.record["kind"] == "bezier":
            p1, p2 = p0+QPointF(*a["out"]), p3+QPointF(*b["in"])
            q0, q1, q2 = lerp(p0,p1,t), lerp(p1,p2,t), lerp(p2,p3,t)
            r0, r1 = lerp(q0,q1,t), lerp(q1,q2,t)
            s = lerp(r0,r1,t)
            a["out"] = [(q0-p0).x(), (q0-p0).y()]
            b["in"] = [(q2-p3).x(), (q2-p3).y()]
            new = point(s.x(),s.y())
            new.update({"in": [(r0-s).x(),(r0-s).y()], "out": [(r1-s).x(),(r1-s).y()], "smooth": True})
        else:
            q = lerp(p0,p3,t)
            new = point(q.x(),q.y())
        nodes.insert(idx+1,new)
        self.active_node = idx+1
        item.refresh()
        self._commit()

    def edit_text(self, item):
        self.finish_text()
        if item is None or item.record.get("locked"):
            return
        if item.record["kind"] not in {"text", "number", "region"}:
            return
        self.select_items([item])
        self.editing_item = item
        if item.record["kind"] == "text":
            editor=LiveTextItem(item)
            editor.setDocument(item._document.clone(editor))
            editor.setDefaultTextColor(QColor(item.record["text_color"]))
            editor.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
            editor.setPos(item._padding,item._padding)
            editor.setZValue(1)
            self.editor=editor;item._live_editor=editor
            def live_update():
                if self.editor is not editor:return
                r=item.to_record();r["text"]=editor.toPlainText();item.apply_record(r)
                self.viewport().update()
            editor.document().contentsChanged.connect(live_update)
            editor.finished.connect(self.finish_text)
            editor.setFocus(Qt.FocusReason.OtherFocusReason)
            cursor=editor.textCursor();cursor.movePosition(cursor.MoveOperation.End);editor.setTextCursor(cursor)
            self.message.emit("画布内实时编辑 · Enter 换行 · Ctrl+Enter 完成")
            return
        editor = TextOverlay(self.viewport())
        self.editor = editor
        font = QFont(item.record["font_family"])
        font.setPointSizeF(max(10, min(36, item.record["font_size"]*self.zoom()*item.scale())))
        font.setBold(item.record["bold"])
        editor.setFont(font)
        editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor.document().setDocumentMargin(0)
        editor.setPlainText(item.record["text"])
        editor.setStyleSheet("QTextEdit { background: #202020; color: #dedede; border: none; padding: 0; selection-background-color: #4b4b4b; }")
        r = self.mapFromScene(item.sceneBoundingRect()).boundingRect()
        x, y = max(0, r.x()), max(0, r.y())
        def fit_text():
            editor.document().adjustSize()
            size = editor.document().size()
            width = max(40, min(self.viewport().width()-x, math.ceil(size.width())+8))
            height = max(30, min(self.viewport().height()-y, math.ceil(size.height())+6))
            editor.setGeometry(x, y, width, height)
        editor.textChanged.connect(fit_text)
        fit_text()
        editor.finished.connect(self.finish_text)
        editor.show()
        editor.setFocus()
        editor.selectAll()
        self.message.emit("直接输入反馈，Enter 换行；Esc / Ctrl+Enter 或点击画布完成编辑")

    def finish_text(self):
        if self.editor is None:
            return
        editor, item = self.editor, self.editing_item
        self.editor = self.editing_item = None
        text = editor.toPlainText()
        editor.blockSignals(True)
        if isinstance(editor,LiveTextItem):
            editor.document().blockSignals(True)
            item._live_editor=None
            editor.setParentItem(None)
            self.board_scene.removeItem(editor)
        editor.hide()
        editor.deleteLater()
        if item and item.scene() == self.board_scene:
            r = item.to_record()
            r["text"] = text
            if r["kind"] == "text":
                r["h"] = 1
            item.apply_record(r)
            self._commit()
        self.setFocus()

    def cancel_gesture(self):
        self.finish_text()
        if self._wheel_timer.isActive():
            self._wheel_timer.stop()
        if self.active_path is not None:
            self.board_scene.removeItem(self.active_path)
            self.active_path = None
        if self.draw_item is not None:
            self.board_scene.removeItem(self.draw_item)
            self.draw_item = None
        for identifier, original in self.orig.items():
            item = self.by_id(identifier)
            if item and self.gesture in {"move", "scale", "resize", "rotate", "node", "crop_pan", "crop_zoom", "flip", "erase", "opacity"}:
                item.apply_record(original)
        self.orig = {}
        self.gesture = None
        self._pan_origin = None
        self.box = None
        self.lens_source = None
        self.lens_rect = None
        self.keys.clear()
        self.viewport().update()

    def _transform_handles(self):
        if not self.selected() or self.node_item is not None:
            return []
        r = union(self.selected())
        return [r.topLeft(),r.topRight(),r.bottomRight(),r.bottomLeft()]

    def _handle_at(self,pos):
        for idx, p in enumerate(self._transform_handles()):
            if (p-pos).manhattanLength()*self.zoom() < 12:
                return idx
        return None

    def _begin_pan(self, viewport_position):
        # Scroll bars already use logical viewport pixels (independent of zoom/DPI).
        # Keep a fixed press-time origin, not an incrementally rounded scene center.
        self._pan_origin = QPointF(viewport_position)
        self._pan_scroll_start = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def _pan_to(self, viewport_position):
        if self._pan_origin is None:
            return
        delta = QPointF(viewport_position) - self._pan_origin
        horizontal, vertical = self._pan_scroll_start
        self.horizontalScrollBar().setValue(horizontal - round(delta.x()))
        self.verticalScrollBar().setValue(vertical - round(delta.y()))
        # QGraphicsView schedules its own scroll repaint. Do not redundantly repaint
        # the whole canvas (or hit-test thousands of objects) for each mouse event.

    def mousePressEvent(self, e):
        if isinstance(self.editor,LiveTextItem) and self.editor.sceneBoundingRect().contains(self.mapToScene(e.position().toPoint())):
            super().mousePressEvent(e);return
        self.setFocus()
        self.finish_text()
        if self._wheel_timer.isActive():
            self._wheel_timer.stop(); self._commit()
        self.start_v = e.position().toPoint()
        self.last_pointer = QPoint(self.start_v)
        self.last_v = self.start_v
        self.start = self.mapToScene(self.start_v)
        mods = e.modifiers()
        ctrl, alt, shift = bool(mods & M.ControlModifier), bool(mods & M.AltModifier), bool(mods & M.ShiftModifier)
        self._guides = []
        self._click_single = None
        self._move_started = False
        if e.button() == B.RightButton:
            if self.active_path is not None or self.tool in {"polyline", "bezier"}:
                self.finish_path()
                self._selection()
                self.gesture = "finish_pen_right"
                self.message.emit("本条路径已完成，可继续绘画；Q 或 Esc 切回选择工具")
                e.accept(); return
            self.gesture = "right"
            self.window_origin = self.window().pos()
            self.screen_origin = e.globalPosition().toPoint()
            self.right_moved = False
            if self.window().isMaximized() or self.window().isFullScreen():
                self._begin_pan(e.position())
            e.accept(); return
        if e.button() == B.MiddleButton or (e.button() == B.LeftButton and alt and not ctrl and not shift):
            self.gesture = "pan"
            self._begin_pan(e.position())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept(); return
        if e.button() != B.LeftButton:
            return
        if ctrl and shift and alt:
            if self.selected():
                self._snapshot()
                self.gesture = "opacity"
            e.accept(); return
        if K.Key_Z in self.keys and not ctrl:
            self.gesture = "zoom"
            e.accept(); return
        if K.Key_C in self.keys and not ctrl:
            targets = [i for i in self.selected() if not i.record.get("locked")]
            if targets:
                self.crop_targets = [i.record["id"] for i in targets]
                self.gesture = "crop"
                self.box = QRectF(self.start,self.start)
                e.accept(); return
            self.message.emit("先选中需要裁切的内容，再按住 C 拖出保留区域")
            e.accept(); return
        if K.Key_V in self.keys and not ctrl:
            images = [i for i in self.selected() if i.record["kind"] == "image" and not i.record.get("locked")]
            if images:
                self.crop_target = images[0]
                self._snapshot()
                self.gesture = "crop_zoom" if shift else "crop_pan"
                e.accept(); return
        if self.tool == "eraser":
            self.orig = {}
            self.gesture = "erase"
            self.erase_last = self.start
            self._erase_segment(self.start, self.start)
            e.accept(); return
        if self.node_item is not None:
            handle = self._node_at(self.start)
            if handle:
                self.active_node,self.node_handle = handle
                self._snapshot()
                self.gesture = "node"
                e.accept(); return
        if self.tool in {"polyline", "bezier"}:
            self._path_click(self.start)
            e.accept(); return
        if self.tool == "text":
            item = self._new("text",self.start,w=260)
            self._after_draw()
            self._commit()
            self.edit_text(item)
            e.accept(); return
        if self.tool == "number":
            item = self._new("number",self.start-QPointF(18,18))
            self.select_items([item]); self._after_draw(); self._commit()
            e.accept(); return
        if self.tool == "magnifier":
            hit = next((i for i in self.board_scene.items(self.start)
                        if isinstance(i, BoardItem) and i.record["kind"] == "image" and i.isVisible()), None)
            if hit:
                self.lens_source = hit
                self.lens_start = hit.mapFromScene(self.start)
                self.lens_rect = QRectF(self.lens_start, self.lens_start)
                self.gesture = "lens"
                self.select_items([hit])
            else:
                self.message.emit("在来源图片上按住左键，拖出要放大的区域")
            e.accept(); return
        if self.tool in {"rect", "ellipse", "line", "arrow", "pen", "region", "redact"}:
            kwargs = {"w": 1, "h": 1}
            if self.tool in PATHS:
                kwargs["points"] = [point(), point()] if self.tool != "pen" else [point()]
            self.draw_item = self._new(self.tool,self.start,**kwargs)
            self.select_items([self.draw_item])
            self.gesture = "draw"
            e.accept(); return
        handles = self._transform_handles()
        handle = self._handle_at(self.start)
        hit = self._hit(self.start)
        if handle is not None and not ctrl:
            # A transform handle acts on the entire selection; it may lie outside any item.
            # Click-to-single selection belongs only to the ordinary item-move branch.
            self._snapshot()
            self.pivot = union(self.selected()).center() if alt else handles[(handle+2)%4]
            self.gesture = "scale"
            self.scale_handle = True
            selected=self.selected()
            if len(selected)==1 and selected[0].record["kind"] in {"rect","ellipse","region","redact"}:
                self.gesture="resize"
                self.resize_item=selected[0]
                self.resize_corner=handle
                self.resize_transform=QTransform(selected[0].sceneTransform())
            e.accept(); return
        if hit:
            # Content is directly editable; the group's background selects the whole group.
            whole_group = bool(hit.record.get("group_background"))
            targets = self._expand([hit],groups=whole_group,attachments=False)
            if shift and not ctrl and not alt and K.Key_Space not in self.keys and not hit.isSelected():
                self.select_items(targets,extend=True)
            elif not hit.isSelected():
                self.select_items(targets)
            if not (ctrl or alt or shift) and not whole_group:
                self._click_single = hit.record["id"]
            self._snapshot()
            self.pivot = union(self.selected()).center()
            self.scale_handle = False
            self.gesture = "scale" if ctrl and alt else "move" if any(i.record["kind"]=="pen" for i in self.selected()) else "rotate" if ctrl else "flip" if alt and shift else "move"
            e.accept(); return
        self.node_item = None
        self.active_node = -1
        self.box_selected = self.selected() if shift else []
        if not shift:
            self.select_items([])
        self.box = QRectF(self.start,self.start)
        self.gesture = "box"
        e.accept()

    def _apply_transform(self, factor=1, angle=0):
        rad = math.radians(angle)
        co, si = math.cos(rad), math.sin(rad)
        for identifier, r in self.orig.items():
            item = self.by_id(identifier)
            if not item:
                continue
            p = QPointF(r["x"],r["y"])-self.pivot
            p = QPointF(p.x()*co-p.y()*si,p.x()*si+p.y()*co)*factor+self.pivot
            item.setPos(p)
            item.setScale(max(.001,min(10000,r.get("scale",1)*factor)))
            item.setRotation(r.get("rotation",0)+angle)

    def _move_snap(self, delta, mods):
        self._guides = []
        if mods & M.ShiftModifier:
            if abs(delta.x()) > abs(delta.y()): delta.setY(0)
            else: delta.setX(0)
        if mods & M.ShiftModifier and K.Key_Space in self.keys and self.orig:
            moving = union([self.by_id(k) for k in self.orig if self.by_id(k)])
            # Snap against candidate edges within 9 screen pixels.
            others = [i for i in self.all_items() if i.record["id"] not in self.orig and i.isVisible() and i.record["kind"] != "region"]
            if others:
                xs = [v for i in others for v in (i.sceneBoundingRect().left(),i.sceneBoundingRect().right())]
                ys = [v for i in others for v in (i.sceneBoundingRect().top(),i.sceneBoundingRect().bottom())]
                # Compute untranslated original bounds from current delta-free item positions.
                original = QRectF()
                for ident,r in self.orig.items():
                    item = self.by_id(ident)
                    box = item.sceneBoundingRect().translated(QPointF(r["x"],r["y"])-item.pos())
                    original = original.united(box)
                r = original.translated(delta)
                dx, vx = min(((b-a,b) for a in (r.left(),r.right()) for b in xs),key=lambda p: abs(p[0]))
                dy, vy = min(((b-a,b) for a in (r.top(),r.bottom()) for b in ys),key=lambda p: abs(p[0]))
                if abs(dx)*self.zoom()<9:
                    delta.setX(delta.x()+dx); self._guides.append(("x",vx))
                if abs(dy)*self.zoom()<9:
                    delta.setY(delta.y()+dy); self._guides.append(("y",vy))
        return delta

    def mouseMoveEvent(self,e):
        if isinstance(self.editor,LiveTextItem):
            super().mouseMoveEvent(e);return
        self.last_pointer = e.position().toPoint()
        pos_v = e.position().toPoint()
        if self.gesture == "pan":
            if (pos_v-self.start_v).manhattanLength()>4:
                self.space_focus_allowed = False
            self._pan_to(e.position())
            self.last_v = pos_v
            e.accept()
            return
        pos = self.mapToScene(pos_v)
        mods = e.modifiers()
        delta = pos-self.start
        g = self.gesture
        if g == "erase":
            self._erase_segment(self.erase_last, pos)
            self.erase_last = pos
            e.accept(); return
        if g and (pos_v-self.start_v).manhattanLength()>4:
            self.space_focus_allowed=False
        if g == "right":
            shift = e.globalPosition().toPoint()-self.screen_origin
            if shift.manhattanLength() > 5:
                self.right_moved = True
            if self.right_moved:
                if self.window().isMaximized() or self.window().isFullScreen():
                    self._pan_to(e.position())
                else:
                    self.window().move(self.window_origin+shift)
            self.last_v = pos_v
            e.accept()
            return
        elif g == "zoom":
            self._zoom_at(math.exp((pos_v.x()-self.last_v.x())*.008),self.start_v)
        elif g == "lens" and self.lens_source:
            self.lens_rect = self._lens_selection(pos)
        elif g in {"box","crop"}:
            self.box = QRectF(self.start,pos).normalized()
            if g == "box" and (pos_v-self.start_v).manhattanLength() > 4:
                candidates = [i for i in self.board_scene.items(self.box,Qt.ItemSelectionMode.IntersectsItemShape) if isinstance(i,BoardItem) and self._eligible(i)]
                self.select_items(self.box_selected+self._expand(candidates,attachments=False))
        elif g == "opacity":
            change = (pos_v.x()-self.start_v.x()) / 300.0
            for identifier, original in self.orig.items():
                item = self.by_id(identifier)
                if item:
                    value = max(0.0, min(1.0, original.get("opacity", 1.0)+change))
                    item.record["opacity"] = value
                    item.setOpacity(value)
            self.selection_changed.emit()
        elif g == "move":
            if not self._move_started and (pos_v-self.start_v).manhattanLength() <= 4:
                e.accept(); return
            self._move_started = True
            delta = self._move_snap(delta,mods)
            for identifier,original in self.orig.items():
                item = self.by_id(identifier)
                if item: item.setPos(original["x"]+delta.x(),original["y"]+delta.y())
        elif g == "scale":
            if self.scale_handle:
                a,b = self.start-self.pivot,pos-self.pivot
                factor = max(.02,math.hypot(b.x(),b.y())/max(.001,math.hypot(a.x(),a.y())))
            else:
                factor = math.exp((pos_v.x()-self.start_v.x())*.006)
            self._apply_transform(max(.01,min(100,factor)))
        elif g == "resize":
            item=self.resize_item
            original=self.orig.get(item.record["id"])
            if original:
                inverse,_=self.resize_transform.inverted()
                local=inverse.map(pos)
                corners=[QPointF(0,0),QPointF(original["w"],0),QPointF(original["w"],original["h"]),QPointF(0,original["h"])]
                anchor=corners[(self.resize_corner+2)%4]
                if mods & M.ShiftModifier:
                    d=local-anchor; ratio=original["w"]/max(1,original["h"])
                    h=max(abs(d.y()),abs(d.x())/max(.001,ratio))
                    local=anchor+QPointF(math.copysign(h*ratio,d.x()),math.copysign(h,d.y()))
                r=QRectF(anchor,local).normalized()
                record=deepcopy(original); origin=self.resize_transform.map(r.topLeft())
                record.update(x=origin.x(),y=origin.y(),w=max(8,r.width()),h=1 if original["kind"]=="text" else max(8,r.height()))
                item.apply_record(record)
        elif g == "rotate":
            a,b = self.start-self.pivot,pos-self.pivot
            angle = math.degrees(math.atan2(b.y(),b.x())-math.atan2(a.y(),a.x()))
            if mods & M.ShiftModifier: angle=round(angle/45)*45
            self._apply_transform(angle=angle)
        elif g == "flip":
            axis = "flip_x" if abs(delta.x())>=abs(delta.y()) else "flip_y"
            for identifier,original in self.orig.items():
                item = self.by_id(identifier)
                if item and original["kind"] in {"image","magnifier"}:
                    r = deepcopy(original)
                    if delta.manhattanLength()*self.zoom()>20: r[axis]=not r.get(axis,False)
                    item.apply_record(r)
        elif g == "draw" and self.draw_item:
            item = self.draw_item
            kind = item.record["kind"]
            if kind in PATHS:
                p = point(delta.x(),delta.y())
                if mods & M.ShiftModifier and kind != "pen":
                    a = round(math.atan2(delta.y(),delta.x())/(math.pi/4))*(math.pi/4)
                    d = math.hypot(delta.x(),delta.y()); p=point(math.cos(a)*d,math.sin(a)*d)
                if kind == "pen":
                    last = item.record["points"][-1]
                    if math.hypot(p["x"]-last["x"],p["y"]-last["y"])*self.zoom()>2:
                        item.record["points"].append(p)
                else: item.record["points"][-1]=p
            else:
                if mods & M.ShiftModifier:
                    side=max(abs(delta.x()),abs(delta.y()))
                    delta=QPointF(math.copysign(side,delta.x()),math.copysign(side,delta.y()))
                r=QRectF(self.start,self.start+delta).normalized()
                item.record.update(x=r.x(),y=r.y(),w=r.width(),h=r.height())
            item.refresh()
        elif g == "path_handle" and self.active_path:
            if self.active_path.record["kind"] == "bezier" and self.active_path.record.get("curve_mode") != "auto":
                local=self.active_path.mapFromScene(pos)
                p=self.active_path.record["points"][self.active_node]
                d=local-QPointF(p["x"],p["y"])
                p["out"]=[d.x(),d.y()]; p["in"]=[-d.x(),-d.y()]; p["smooth"]=True
                self.active_path.refresh()
        elif g == "node" and self.node_item:
            local=self.node_item.mapFromScene(pos)
            p=self.node_item.record["points"][self.active_node]
            if self.node_handle == "node": p.update(x=local.x(),y=local.y())
            else:
                off=local-QPointF(p["x"],p["y"])
                p[self.node_handle]=[off.x(),off.y()]
                if p.get("smooth",True) and not mods & M.AltModifier:
                    other="out" if self.node_handle=="in" else "in"
                    length=math.hypot(*p.get(other,[0,0])); norm=max(.001,math.hypot(off.x(),off.y()))
                    p[other]=[-off.x()*length/norm,-off.y()*length/norm]
            self.node_item.refresh()
        elif g in {"crop_pan","crop_zoom"}:
            item=self.crop_target; r=deepcopy(self.orig[item.record["id"]])
            cx,cy,cw,ch=r.get("crop") or [0,0,1,1]
            if g=="crop_pan":
                local0=item.mapFromScene(self.start); local1=item.mapFromScene(pos)
                d=local1-local0
                cx=max(0,min(1-cw,cx-d.x()/max(1,r["w"])*cw))
                cy=max(0,min(1-ch,cy-d.y()/max(1,r["h"])*ch))
            else:
                factor=math.exp(-(pos_v.x()-self.start_v.x())*.006)
                nw=min(1,max(.01,cw*factor)); nh=min(1,max(.01,ch*factor))
                cx=max(0,min(1-nw,cx+(cw-nw)/2)); cy=max(0,min(1-nh,cy+(ch-nh)/2)); cw,ch=nw,nh
            r["crop"]=[cx,cy,cw,ch]; item.apply_record(r)
        elif self.active_path:
            self.path_preview=pos
        self.last_v=pos_v
        if g or self.active_path:
            self.viewport().update()
        else:
            hit=self._node_at(pos) if self.node_item else self._handle_at(pos)
            if self.tool=="select": self.setCursor(Qt.CursorShape.SizeAllCursor if hit is not None else Qt.CursorShape.ArrowCursor)
        e.accept()

    def mouseReleaseEvent(self,e):
        if isinstance(self.editor,LiveTextItem):
            super().mouseReleaseEvent(e);return
        g=self.gesture
        if g=="erase":
            self._erase_segment(self.erase_last, self.mapToScene(e.position().toPoint()))
            self.gesture=None
            self.orig={}
            self._commit()
            e.accept(); return
        if g=="finish_pen_right":
            self.gesture=None
            e.accept(); return
        if g=="pan" or (g=="right" and self.right_moved and self._pan_origin is not None):
            self._pan_to(e.position())
        self._pan_origin = None
        if g=="right":
            self.gesture=None
            if not self.right_moved:
                pos=self.mapToScene(e.position().toPoint())
                nh=self._node_at(pos) if self.node_item else None
                if nh:
                    self.active_node=nh[0]
                    menu=QMenu(self)
                    menu.addAction("平滑节点",lambda:self._smooth_node(True))
                    menu.addAction("尖角节点",lambda:self._smooth_node(False))
                    menu.addAction("删除节点",self._delete_node)
                    menu.exec(e.globalPosition().toPoint())
                else:
                    hit=self._hit(pos)
                    if hit and not hit.isSelected(): self.select_items(self._expand([hit],groups=bool(hit.record.get("group_background")),attachments=False))
                    self.customContextMenuRequested.emit(e.position().toPoint())
            e.accept(); return
        if g=="lens" and self.lens_source:
            source = self.lens_source
            region = self._lens_selection(self.mapToScene(e.position().toPoint()))
            self.lens_source = None
            self.lens_rect = None
            if region.width() * abs(source.scale()) * self.zoom() >= 3 and region.height() * abs(source.scale()) * self.zoom() >= 3:
                self.magnifier_region.emit(source, region)
                self._after_draw()
            else:
                self.message.emit("请拖出一个区域；单击不会创建中心放大镜")
        if g=="move" and not self._move_started and getattr(self,"_click_single",None):
            hit = self.by_id(self._click_single)
            if hit:
                self.select_items([hit])
                self._raise_clicked_image(hit)
        if g=="box" and (e.position().toPoint()-self.start_v).manhattanLength() <= 4:
            self.select_items(self.box_selected)
        if g=="crop" and self.box:
            self._crop_selection(self.box)
        if g=="draw" and self.draw_item:
            item=self.draw_item
            if (e.position().toPoint()-self.start_v).manhattanLength() <= 3 and item.record["kind"] != "pen":
                self.board_scene.removeItem(item)
                self.select_items([])
            elif item.record["kind"] in {"line","arrow"}:
                self.node_item=item
            self.draw_item=None
            self._after_draw()
        self.gesture=None
        self.box=None
        self._guides=[]
        self.orig={}
        self.setCursor(Qt.CursorShape.ArrowCursor if self.tool=="select" else Qt.CursorShape.CrossCursor)
        if g in {"move","scale","resize","rotate","draw","crop","crop_pan","crop_zoom","node","flip","opacity"} and self.active_path is None:
            self._commit()
        self.viewport().update()
        e.accept()

    def contextMenuEvent(self,e):
        # Handled on release to distinguish PureRef window drag from context click.
        e.accept()

    def mouseDoubleClickEvent(self,e):
        if isinstance(self.editor,LiveTextItem):
            super().mouseDoubleClickEvent(e);return
        if e.button()!=B.LeftButton:
            return
        if self.active_path is not None:
            self.finish_path(); e.accept(); return
        pos=self.mapToScene(e.position().toPoint())
        if self.node_item and self._node_at(pos) is None and self._hit(pos) is self.node_item:
            self._insert_node(pos); self.gesture=None; self.orig={}; e.accept(); return
        hit=self._hit(pos)
        self.gesture=None; self.orig={}
        if hit:
            self.select_items([hit])
            if hit.record["kind"] in {"text","number","region"}: self.edit_text(hit)
            elif hit.record["kind"] in PATHS - {"pen"}: self.edit_nodes(hit)
            elif hit.record["kind"] != "pen": self._focus_toggle()
        e.accept()

    def _crop_selection(self, scene_rect):
        if scene_rect.width()*self.zoom()<3 or scene_rect.height()*self.zoom()<3:
            self.message.emit("裁切区域太小，未修改内容")
            return
        targets=[self.by_id(identifier) for identifier in getattr(self,"crop_targets",[])]
        targets=[i for i in targets if i is not None and not i.record.get("locked")]
        if not targets:return
        batch=str(uuid.uuid4())
        corners=[scene_rect.topLeft(),scene_rect.topRight(),scene_rect.bottomRight(),scene_rect.bottomLeft()]
        for item in targets:
            record=item.to_record()
            polygon=[]
            for corner in corners:
                local=item.mapFromScene(corner);polygon.append([local.x(),local.y()])
            record.setdefault("selection_clips",[]).append({"batch":batch,"polygon":polygon})
            item.apply_record(record)
        self.node_item=None;self.active_node=-1
        self.message.emit("已裁切选中内容；Ctrl+Z 撤销，Ctrl+Shift+C 重置同批裁切")

    def _crop_to_rect(self,item,scene_rect):
        rect=item.mapFromScene(scene_rect).boundingRect().intersected(QRectF(0,0,item.record["w"],item.record["h"]))
        if rect.width()<2 or rect.height()<2: return
        r=item.to_record(); cx,cy,cw,ch=r.get("crop") or [0,0,1,1]
        r.setdefault("uncropped",{"w":r["w"],"h":r["h"]})
        fx=1-rect.right()/r["w"] if r.get("flip_x") else rect.left()/r["w"]
        fy=1-rect.bottom()/r["h"] if r.get("flip_y") else rect.top()/r["h"]
        r["crop"]=[cx+fx*cw,cy+fy*ch,cw*rect.width()/r["w"],ch*rect.height()/r["h"]]
        newpos=item.mapToScene(rect.topLeft())
        r.update(x=newpos.x(),y=newpos.y(),w=rect.width(),h=rect.height())
        item.apply_record(r)

    def _erase_segment(self, start, end):
        radius = self.eraser_size / (2 * self.zoom())
        area = QRectF(start, end).normalized().adjusted(-radius,-radius,radius,radius)
        for item in self.board_scene.items(area, Qt.ItemSelectionMode.IntersectsItemShape):
            if not isinstance(item, BoardItem) or item.record["kind"] != "pen" or not item.isVisible() or item.record.get("locked"):
                continue
            if item.record["id"] not in self.orig:
                self.orig[item.record["id"]] = item.to_record()
            a, b = item.mapFromScene(start), item.mapFromScene(end)
            local_radius = radius / max(.00001, abs(item.scale()))
            item.record.setdefault("erasures", []).append([a.x(),a.y(),b.x(),b.y(),local_radius])
            item.refresh()
        self.viewport().update()

    def wheelEvent(self,e):
        delta=e.angleDelta().y()/120
        if delta==0 and not e.pixelDelta().isNull(): delta=e.pixelDelta().y()/40
        if not e.modifiers() & M.ControlModifier:
            self._zoom_at(math.pow(1.15,delta),e.position().toPoint())
            e.accept(); return
        if self.tool == "eraser":
            self.eraser_size=max(2,min(300,self.eraser_size+delta*2))
            self.message.emit(f"橡皮擦直径 {self.eraser_size:.0f} px · Ctrl+滚轮调节")
            e.accept(); return
        selected=self.selected() if self.tool == "select" else []
        kinds={i.record["kind"] for i in selected}
        key=None
        if selected:
            if kinds <= {"text","number"}: key="font_size"
            elif kinds <= INK - {"pen"}: key="stroke_width"
        elif self.tool in {"text","number"}: key="font_size"
        elif self.tool in INK: key="stroke_width"
        if key:
            def adjusted(value):
                step=1 if key=="font_size" else (.05 if value<2 or (value<=2 and delta<0) else .25 if value<5 else .5)
                low, high=(6,256) if key=="font_size" else (.1,120)
                return round(max(low,min(high,value+delta*step)),3)
            if selected:
                for item in selected:
                    if item.record.get("locked"): continue
                    r=item.to_record();r[key]=adjusted(r.get(key,3))
                    if key=="font_size" and r["kind"]=="text": r["h"]=1
                    item.apply_record(r);self.style[key]=r[key]
                self._wheel_timer.start()
            else:
                self.style[key]=adjusted(self.style.get(key,18 if key=="font_size" else 3))
            self.selection_changed.emit()
            self.message.emit(f"{'字号' if key=='font_size' else '下一笔粗细' if self.tool=='pen' else '线条粗细'} {self.style[key]:.2f} · Ctrl+滚轮调节")
        elif "pen" in kinds:
            self.message.emit("笔迹仅支持移动和整体缩放；修改内容请用橡皮擦后重画")
        e.accept()

    def keyPressEvent(self,e):
        # QTextEdit ignores some control chords; never let those bubble into canvas.
        if self.editor is not None:
            if e.key() == K.Key_S and e.modifiers() & M.ControlModifier:
                self.finish_text()
                self.command.emit("save_as" if e.modifiers() & M.ShiftModifier else "save")
            elif isinstance(self.editor,LiveTextItem):
                super().keyPressEvent(e);return
            e.accept()
            return
        if e.isAutoRepeat():
            return
        self.keys.add(e.key())
        ctrl=bool(e.modifiers() & M.ControlModifier); shift=bool(e.modifiers() & M.ShiftModifier); alt=bool(e.modifiers() & M.AltModifier)
        key=e.key()
        tool_keys={K.Key_Q:"select",K.Key_R:"rect",K.Key_O:"ellipse",K.Key_L:"line",K.Key_A:"arrow",K.Key_B:"pen",K.Key_H:"highlighter",K.Key_P:"polyline",K.Key_U:"bezier",K.Key_N:"number",K.Key_F:"region",K.Key_M:"magnifier",K.Key_D:"redact",K.Key_E:"eraser"}
        if not ctrl and not shift and not alt and key in tool_keys:
            self.command.emit("tool:"+tool_keys[key]);e.accept();return
        if key==K.Key_Space:
            self.space_focus_allowed=not ctrl and not shift
        if key==K.Key_Escape:
            if self.active_path is not None or self.gesture:
                self.cancel_gesture()
            self.node_item=None; self.active_node=-1
            self.set_tool("select")
            self.select_items([])
            self.viewport().update(); e.accept(); return
        if key in (K.Key_Return,K.Key_Enter) and not ctrl:
            if self.active_path: self.finish_path()
            elif len(self.selected())==1:
                item=self.selected()[0]
                if item.record["kind"] in PATHS: self.edit_nodes(item)
                else: self.edit_text(item)
            e.accept(); return
        if key==K.Key_Delete:
            if self.node_item is not None and self.active_node>=0: self._delete_node()
            else: self.delete_selection()
            e.accept(); return
        actions={
            (True,False,False,K.Key_A):self.select_all,
            (True,False,False,K.Key_D):self.duplicate,
            (True,False,False,K.Key_G):self.group_selection,
            (True,True,False,K.Key_G):self.ungroup_selection,
            (False,False,True,K.Key_L):self.toggle_lock,
            (True,True,False,K.Key_T):self.reset_transform,
            (True,True,False,K.Key_C):self.reset_crop,
            (True,False,False,K.Key_Space):lambda:self.focus_items(),
            (True,False,False,K.Key_0):self.reset_zoom,
            (True,False,False,K.Key_P):self.arrange_selection,
            (True,False,True,K.Key_Left):lambda:self.normalize_selection("height"),
            (True,False,True,K.Key_Right):lambda:self.normalize_selection("width"),
            (False,True,True,K.Key_H):lambda:self.flip_selection("x"),
            (False,True,True,K.Key_V):lambda:self.flip_selection("y"),
        }
        token=(ctrl,shift,alt,key)
        if token in actions:
            actions[token](); e.accept(); return
        commands={K.Key_C:"copy",K.Key_V:"paste",K.Key_Z:"redo" if shift else "undo",K.Key_S:"save_as" if shift else "save",K.Key_L:"open",K.Key_I:"import",K.Key_E:"export_selection" if shift else "export",K.Key_F:"fullscreen",K.Key_M:"minimize",K.Key_K:"new",K.Key_N:"new_text"}
        if ctrl and not alt and key in commands:
            if self._wheel_timer.isActive(): self._wheel_timer.stop(); self._commit()
            self.command.emit(commands[key]); e.accept(); return
        if ctrl and shift and key==K.Key_A:
            self.command.emit("always_top"); e.accept(); return
        if ctrl and key in {K.Key_Left,K.Key_Right,K.Key_Up,K.Key_Down}:
            self.align_selection({K.Key_Left:"left",K.Key_Right:"right",K.Key_Up:"top",K.Key_Down:"bottom"}[key]); e.accept(); return
        if key in {K.Key_Up,K.Key_Down} and not ctrl and not alt:
            self.change_z(key==K.Key_Up); e.accept(); return
        # Space focus fires on release so Shift+Space+drag remains neighbor snapping.
        if key==K.Key_Space and not ctrl:
            self.space_moved=False
            e.accept(); return
        if key in {K.Key_Left,K.Key_Right} and not ctrl:
            images=sorted([i for i in self.all_items() if i.record["kind"]=="image"],key=lambda i:i.zValue())
            if images:
                current=next((idx for idx,i in enumerate(images) if i.isSelected()),0)
                item=images[(current+(1 if key==K.Key_Right else -1))%len(images)]
                self.select_items([item]); self.focus_items([item]); self._commit()
            e.accept(); return
        if key in {K.Key_C,K.Key_V,K.Key_Z,K.Key_Alt,K.Key_Shift,K.Key_Control}:
            e.accept(); return
        super().keyPressEvent(e)

    def keyReleaseEvent(self,e):
        if isinstance(self.editor,LiveTextItem):
            super().keyReleaseEvent(e);return
        if e.isAutoRepeat(): return
        if e.key()==K.Key_Space and e.key() in self.keys and getattr(self,"space_focus_allowed",False) and not self.gesture:
            self._focus_toggle()
        self.keys.discard(e.key())
        e.accept()

    def focusOutEvent(self,e):
        self.keys.clear()
        super().focusOutEvent(e)

    def _delete_node(self):
        if self.node_item is None or self.active_node<0: return
        nodes=self.node_item.record["points"]
        if len(nodes)<=2:
            self.message.emit("路径至少保留两个节点；退出节点编辑后可删除整条路径")
            return
        nodes.pop(self.active_node); self.active_node=-1
        self.node_item.refresh(); self._commit()

    def delete_selection(self):
        items=self._expand(self.selected(),groups=False)
        ids={i.record["id"] for i in items if not i.record.get("locked")}
        # The connector belongs to its magnifier, even if it was not selected.
        lens_ids={i.record["id"] for i in items if i.record["kind"]=="magnifier" and i.record["id"] in ids}
        ids.update(i.record["id"] for i in self.all_items() if i.record.get("link_target") in lens_ids)
        for i in list(self.all_items()):
            if i.record["id"] in ids:
                if i is self.node_item: self.node_item=None
                self.board_scene.removeItem(i)
            elif i.record.get("attached_to") in ids:
                i.record["attached_to"]=""
        self.active_node=-1
        self._commit()

    def duplicate(self):
        originals=self.selection_records()
        records=clone_records(originals)
        identifiers={a["id"]:b["id"] for a,b in zip(originals,records)}
        for record in records:
            for key in ("link_source","link_target"):
                if key in record:
                    record[key]=identifiers.get(record[key],"")
        if records:
            z=max([i.zValue() for i in self.all_items()]+[0])+1
            for idx,r in enumerate(records): r["z"]=z+idx
            self.add_records(records); self._commit()

    def group_selection(self):
        selected = self._expand(self.selected(), groups=True)
        content = [i for i in selected if not i.record.get("group_background") and not i.record.get("locked")]
        if len(content) < 2:
            return
        old_backgrounds = [i for i in selected if i.record.get("group_background")]
        color = old_backgrounds[0].record.get("fill", "transparent") if old_backgrounds else self.style.get("fill", "transparent")
        for item in old_backgrounds:
            self.board_scene.removeItem(item)
        group = str(uuid.uuid4())
        top = max([i.zValue() for i in self.all_items()] + [0]) + 2
        for index, item in enumerate(sorted(content, key=lambda i:i.zValue())):
            item.record["group"] = group
            item.setZValue(top+index)
        background = BoardItem(new_record("rect", group=group, group_background=True,
            group_padding=8.0/max(.002,self.zoom()), fill=color, stroke_width=0,
            name="编组底板", z=top-1))
        self.board_scene.addItem(background)
        self.update_group_backgrounds()
        self.select_items(content + [background])
        self._commit()
        self.message.emit("已编组；点击内容单独编辑，拖动底板空白处移动整组；右侧“填充色”可改组底色。")

    def ungroup_selection(self):
        selected = self._expand(self.selected(), groups=True, attachments=False)
        content = []
        for item in selected:
            if item.record.get("group_background"):
                self.board_scene.removeItem(item)
            else:
                item.record["group"] = ""
                content.append(item)
        self.select_items(content)
        self._commit()

    def update_group_backgrounds(self):
        groups = {}
        backgrounds = []
        for item in self.all_items():
            if item.record.get("group_background"):
                backgrounds.append(item)
            elif item.record.get("group") and item.isVisible():
                groups.setdefault(item.record["group"], []).append(item)
        for background in backgrounds:
            if background.record.get("selection_clips"):
                continue
            members = groups.get(background.record.get("group"), [])
            if not members:
                continue
            bounds = QRectF()
            for item in members:
                bounds = bounds.united(item.mapRectToScene(item.visual_bounds()))
            pad = background.record.get("group_padding", 8) * abs(background.scale())
            bounds = bounds.adjusted(-pad, -pad, pad, pad)
            updates = dict(x=bounds.x(), y=bounds.y(), w=bounds.width(), h=bounds.height(),
                           rotation=0, scale=1, group_padding=pad,
                           z=min(i.zValue() for i in members)-.5)
            if any(background.record.get(k) != value for k,value in updates.items()):
                record = background.to_record();record.update(updates);background.apply_record(record)

    def toggle_lock(self):
        items=self.selected()
        target=not all(i.record.get("locked") for i in items)
        for item in items: item.record["locked"]=target
        self._commit()
        self.message.emit("已锁定。可从右键菜单解锁全部对象。" if target else "已解锁")

    def _move_with_children(self,item,delta):
        for child in self._expand([item],groups=False):
            if not child.record.get("locked"): child.setPos(child.pos()+delta)

    def _roots(self):
        selected=[i for i in self.selected() if not i.record.get("locked")]
        ids={i.record["id"] for i in selected}
        return [i for i in selected if i.record.get("attached_to") not in ids]

    def align_selection(self,direction):
        items=self._roots()
        if len(items)<2: return
        bounds=union(items)
        for item in items:
            r=item.sceneBoundingRect()
            dx={"left":bounds.left()-r.left(),"right":bounds.right()-r.right(),"center":bounds.center().x()-r.center().x()}.get(direction,0)
            dy={"top":bounds.top()-r.top(),"bottom":bounds.bottom()-r.bottom(),"middle":bounds.center().y()-r.center().y()}.get(direction,0)
            self._move_with_children(item,QPointF(dx,dy))
        self._commit()

    def normalize_selection(self,axis):
        items=self._roots()
        if len(items)<2: return
        iswidth=axis in {"width","x"}
        sizes=[i.sceneBoundingRect().width() if iswidth else i.sceneBoundingRect().height() for i in items]
        target=sum(sizes)/len(sizes)
        for item,size in zip(items,sizes):
            factor=target/max(.001,size)
            pivot=item.sceneBoundingRect().topLeft()
            for child in self._expand([item],groups=False):
                child.setPos(pivot+(child.pos()-pivot)*factor)
                child.setScale(child.scale()*factor)
        self._commit()

    def arrange_selection(self):
        seeds=self._roots() or [i for i in self.all_items() if self._eligible(i) and i.record["kind"]=="image"]
        units=[]; seen=set()
        for item in sorted(seeds,key=lambda i:i.zValue()):
            if item.record["id"] in seen: continue
            members=self._expand([item],groups=True)
            seen.update(i.record["id"] for i in members)
            if any(i.record.get("locked") for i in members): continue
            bounds=union(members)
            if not bounds.isEmpty(): units.append((members,bounds))
        if len(units)<2: return
        origin=union([i for members,_ in units for i in members]).topLeft()
        from packing import pack_rectangles
        positions=pack_rectangles([(r.width(),r.height()) for _,r in units],8.0)
        for (members,r),(x,y) in zip(units,positions):
            delta=origin+QPointF(x,y)-r.topLeft()
            for item in members: item.setPos(item.pos()+delta)
        self._commit()

    def flip_selection(self,axis):
        key="flip_x" if axis in {"x","horizontal"} else "flip_y"
        for item in self.selected():
            if item.record["kind"] in {"image","magnifier"} and not item.record.get("locked"):
                r=item.to_record(); r[key]=not r.get(key,False); item.apply_record(r)
        self._commit()

    def reset_transform(self):
        for item in self.selected():
            if not item.record.get("locked"):
                r=item.to_record(); r.update(rotation=0,scale=1,flip_x=False,flip_y=False); item.apply_record(r)
        self._commit()

    def reset_crop(self):
        selected=self.selected()
        batches={clip["batch"] for item in selected for clip in item.record.get("selection_clips",[])}
        for item in self.all_items():
            clips=item.record.get("selection_clips",[])
            affected=any(clip["batch"] in batches for clip in clips)
            if item.record.get("locked") or (item not in selected and not affected):continue
            r=item.to_record()
            if clips:
                r["selection_clips"]=[clip for clip in clips if clip["batch"] not in batches]
            if item in selected and item.record["kind"]=="image":
                original=r.pop("uncropped",{})
                if original:r.update(w=original["w"],h=original["h"])
                r["crop"]=None
            item.apply_record(r)
        self._commit()

    def change_z(self,front=True):
        items=self.selected()
        extreme=(max if front else min)([i.zValue() for i in self.all_items()]+[0])
        for idx,item in enumerate(sorted(items,key=lambda i:i.zValue())):
            item.setZValue(extreme+idx+1 if front else extreme-len(items)+idx-1)
        self._commit()

    def _lens_selection(self, scene_pos):
        source = self.lens_source
        bounds = QRectF(0, 0, source.record["w"], source.record["h"])
        end = source.mapFromScene(scene_pos)
        start = self.lens_start
        end = QPointF(max(0, min(bounds.width(), end.x())), max(0, min(bounds.height(), end.y())))
        dx, dy = end.x()-start.x(), end.y()-start.y()
        if self.style.get("magnifier_shape", "circle") == "circle":
            # A square in original image pixels, including cropped source images.
            image = base64_to_image(source.record["image"])
            _, _, cw, ch = source.record.get("crop") or [0, 0, 1, 1]
            sx = image.width()*cw / max(.001, bounds.width())
            sy = image.height()*ch / max(.001, bounds.height())
            size = min(abs(dx)*sx, abs(dy)*sy)
            end = start + QPointF(math.copysign(size/max(.001,sx), dx), math.copysign(size/max(.001,sy), dy))
        return QRectF(start, end).normalized().intersected(bounds)

    def update_links(self):
        self.update_group_backgrounds()
        items = self.all_items()
        by_id = {i.record["id"]: i for i in items}
        for item in items:
            if item.record["kind"] != "magnifier" and not item.record.get("link_source"):
                continue
            r=item.to_record()
            if r["kind"]=="magnifier" and r.get("source_id"):
                source=by_id.get(r["source_id"])
                if source and r["image"]!=source.record["image"]:
                    r["image"]=source.record["image"]; item.apply_record(r)
            elif r.get("link_source") and r.get("link_target"):
                source,target=by_id.get(r["link_source"]),by_id.get(r["link_target"])
                if source and target:
                    crop=target.record.get("crop") or [0,0,1,1]
                    cx,cy,cw,ch=crop
                    sx,sy,sw,sh=source.record.get("crop") or [0,0,1,1]
                    px,py=(cx+cw/2-sx)/max(.001,sw),(cy+ch/2-sy)/max(.001,sh)
                    if source.record.get("flip_x"): px=1-px
                    if source.record.get("flip_y"): py=1-py
                    start=source.mapToScene(QPointF(px*source.record["w"],py*source.record["h"]))
                    end=target.sceneBoundingRect().center()
                    r.update(x=start.x(),y=start.y(),points=[point(),point((end-start).x(),(end-start).y())],scale=1,rotation=0)
                    item.apply_record(r)

    def dragEnterEvent(self,e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage(): e.acceptProposedAction()
        else: e.ignore()

    def dragMoveEvent(self,e):
        e.acceptProposedAction()

    def dropEvent(self,e):
        self.last_pointer = e.position().toPoint()
        paths=[url.toLocalFile() for url in e.mimeData().urls() if url.isLocalFile()]
        if paths: self.files_dropped.emit(paths); e.acceptProposedAction()

    def drawBackground(self,painter,rect):
        painter.fillRect(rect,QColor("#202020"))
        spacing=24
        while spacing*self.zoom()<16: spacing*=4
        while spacing*self.zoom()>80: spacing/=2
        painter.setPen(QPen(QColor("#292929"),0))
        left=math.floor(rect.left()/spacing)*spacing; top=math.floor(rect.top()/spacing)*spacing
        cols=min(180,int(rect.width()/spacing)+2); rows=min(140,int(rect.height()/spacing)+2)
        # Identical dots; one row is built once and translated in C++ (was ~25k Python points per frame).
        row=QPolygonF([QPointF(left+x*spacing,top) for x in range(cols)])
        for y in range(rows):
            painter.drawPoints(row.translated(0,y*spacing))

    def drawForeground(self,painter,rect):
        painter.save()
        zoom=self.zoom()
        pen=QPen(QColor("#cecece"),0)
        painter.setPen(pen); painter.setBrush(Qt.BrushStyle.NoBrush)
        for item in self.selected():
            painter.drawPolygon(item.mapToScene(item.visual_bounds()))
        handles=self._transform_handles()
        painter.setBrush(QColor("#ffffff"))
        for p in handles: painter.drawRect(QRectF(p.x()-4/zoom,p.y()-4/zoom,8/zoom,8/zoom))
        if self.node_item is not None:
            for idx,tag,p in self._node_handles():
                origin=self.node_item.mapToScene(QPointF(self.node_item.record["points"][idx]["x"],self.node_item.record["points"][idx]["y"]))
                if tag!="node":
                    painter.setPen(QPen(QColor("#909090"),0)); painter.drawLine(origin,p)
                painter.setPen(pen)
                painter.setBrush(QColor("#686868") if idx==self.active_node and tag=="node" else QColor("#ffffff"))
                size=4/zoom if tag=="node" else 3/zoom
                if tag=="node": painter.drawRect(QRectF(p.x()-size,p.y()-size,2*size,2*size))
                else: painter.drawEllipse(p,size,size)
        if self.active_path is not None:
            nodes=self.active_path.node_positions()
            for node in nodes:
                p=self.active_path.mapToScene(node)
                painter.setBrush(QColor("#ffffff")); painter.drawEllipse(p,3/zoom,3/zoom)
            if nodes and hasattr(self,"path_preview") and self.gesture!="path_handle":
                pen2=QPen(QColor("#a8a8a8"),0,Qt.PenStyle.DashLine); painter.setPen(pen2)
                painter.drawLine(self.active_path.mapToScene(nodes[-1]),self.path_preview)
        if self.lens_source is not None and self.lens_rect is not None:
            preview = QPainterPath()
            if self.style.get("magnifier_shape", "circle") == "circle":
                preview.addEllipse(self.lens_rect)
            else:
                preview.addRect(self.lens_rect)
            painter.setPen(QPen(QColor("#ffe08a"), 0, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(255, 224, 138, 35))
            painter.drawPath(self.lens_source.mapToScene(preview))
        if self.box is not None:
            painter.setPen(QPen(QColor("#cecece"),0,Qt.PenStyle.DashLine))
            painter.setBrush(QColor(200,200,200,24)); painter.drawRect(self.box)
        painter.setPen(QPen(QColor("#e9945b"),0,Qt.PenStyle.DashLine))
        for axis,value in self._guides:
            painter.drawLine(QPointF(value,rect.top()),QPointF(value,rect.bottom())) if axis=="x" else painter.drawLine(QPointF(rect.left(),value),QPointF(rect.right(),value))
        painter.restore()
