from __future__ import annotations

import math
from pathlib import Path

import fitz
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsTextItem, QGraphicsView, QInputDialog, QMenu

from .models import PdfScale, PlanObject


class ObjectGraphicsItem(QGraphicsItem):
    def __init__(self, plan_object: PlanObject) -> None:
        super().__init__()
        self.plan_object = plan_object
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._bbox = self._make_bbox()

    def _make_bbox(self) -> QRectF:
        box = self.plan_object.bbox()
        if not box:
            return QRectF(-50, -50, 100, 100)
        x1, y1, x2, y2 = box
        if abs(x2 - x1) < 1:
            x2 += 1
        if abs(y2 - y1) < 1:
            y2 += 1
        return QRectF(QPointF(x1, -y2), QPointF(x2, -y1)).normalized()

    def boundingRect(self) -> QRectF:
        return self._bbox.adjusted(-80, -80, 80, 80)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        pen = QPen(QColor("#ffcc33") if self.isSelected() else QColor("#69b7ff"), 0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        obj = self.plan_object
        if obj.kind == "CIRCLE" and obj.center and obj.radius is not None:
            cx, cy = obj.center
            painter.drawEllipse(QPointF(cx, -cy), obj.radius, obj.radius)
        elif obj.points:
            for a, b in zip(obj.points, obj.points[1:]):
                painter.drawLine(QPointF(a[0], -a[1]), QPointF(b[0], -b[1]))
        elif obj.center:
            cx, cy = obj.center
            painter.setBrush(QBrush(QColor("#69b7ff")))
            painter.drawEllipse(QPointF(cx, -cy), 35, 35)


class PlanView(QGraphicsView):
    pointClicked = Signal(float, float)
    pointHovered = Signal(float, float)
    objectSelected = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#11161d")))
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.objects: list[PlanObject] = []
        self.pdf_scale = PdfScale()
        self.mm_per_scene_unit = 1.0
        self._pdf_item: QGraphicsPixmapItem | None = None
        self._pdf_scale_points: list[QPointF] = []
        self._target_marker: QGraphicsEllipseItem | None = None
        self._target_label_bg: QGraphicsPathItem | None = None
        self._target_label: QGraphicsSimpleTextItem | None = None
        self.scene.selectionChanged.connect(self._emit_selection)

    def clear_plan(self) -> None:
        self.scene.clear()
        self.objects = []
        self.mm_per_scene_unit = 1.0
        self._pdf_item = None
        self._target_marker = None
        self._target_label_bg = None
        self._target_label = None
        self._draw_axes()

    def load_dxf_objects(self, objects: list[PlanObject]) -> None:
        self.clear_plan()
        self.objects = objects
        for obj in objects:
            self.scene.addItem(ObjectGraphicsItem(obj))
        self._draw_axes()
        self.fitInView(self.scene.itemsBoundingRect().adjusted(-1000, -1000, 1000, 1000), Qt.KeepAspectRatio)

    def load_pdf(self, path: str | Path) -> None:
        self.clear_plan()
        doc = fitz.open(path)
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        self._pdf_item = self.scene.addPixmap(pixmap)
        self._pdf_item.setOpacity(0.75)
        self._pdf_item.setTransformOriginPoint(pixmap.width() / 2, pixmap.height() / 2)
        self._pdf_item.setPos(-pixmap.width() / 2, -pixmap.height() / 2)
        self._draw_axes()
        self.fitInView(self.scene.itemsBoundingRect(), Qt.KeepAspectRatio)

    def set_pdf_scale_mode(self) -> None:
        self._pdf_scale_points = []

    def restore_pdf_scale(self, scale: PdfScale) -> None:
        self.pdf_scale = scale
        if scale.p1_scene and scale.p2_scene and scale.real_distance_mm:
            a = scale.p1_scene
            b = scale.p2_scene
            scene_distance = math.hypot(a[0] - b[0], a[1] - b[1])
            if scene_distance > 0:
                self.mm_per_scene_unit = scale.real_distance_mm / scene_distance

    def selected_object(self) -> PlanObject | None:
        for item in self.scene.selectedItems():
            if isinstance(item, ObjectGraphicsItem):
                return item.plan_object
        return None

    def show_target(self, x_mm: float, y_mm: float) -> None:
        if self._target_marker:
            self.scene.removeItem(self._target_marker)
        if self._target_label:
            self.scene.removeItem(self._target_label)
        if self._target_label_bg:
            self.scene.removeItem(self._target_label_bg)
        x = x_mm / self.mm_per_scene_unit
        y = y_mm / self.mm_per_scene_unit
        self._target_marker = self.scene.addEllipse(-7, -7, 14, 14, QPen(QColor("#ff4d6d"), 2), QBrush(QColor(255, 77, 109, 90)))
        self._target_marker.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self._target_marker.setZValue(9997)
        self._target_marker.setPos(x, -y)
        self._add_target_label(x, -y, x_mm, y_mm)

    def _add_target_label(self, scene_x: float, scene_y: float, x_mm: float, y_mm: float) -> None:
        text = f"X {x_mm:.0f} mm\nY {y_mm:.0f} mm"
        self._target_label = QGraphicsSimpleTextItem(text)
        self._target_label.setBrush(QBrush(QColor("#f5f7fb")))
        self._target_label.setFont(QFont("Arial", 11))
        self._target_label.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        margin = 8
        label_x = scene_x + 16
        label_y = scene_y - 42
        self._target_label.setPos(label_x + margin, label_y + margin)

        rect = self._target_label.boundingRect().adjusted(-margin, -margin, margin, margin)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        self._target_label_bg = QGraphicsPathItem(path)
        self._target_label_bg.setBrush(QBrush(QColor(18, 24, 32, 230)))
        self._target_label_bg.setPen(QPen(QColor("#ff4d6d"), 1))
        self._target_label_bg.setFlag(QGraphicsItem.ItemIgnoresTransformations, True)
        self._target_label_bg.setPos(label_x + margin, label_y + margin)
        self._target_label_bg.setZValue(9998)
        self._target_label.setZValue(9999)
        self.scene.addItem(self._target_label_bg)
        self.scene.addItem(self._target_label)

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        pos = self.mapToScene(event.position().toPoint())
        self.pointHovered.emit(pos.x() * self.mm_per_scene_unit, -pos.y() * self.mm_per_scene_unit)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._context_menu(event)
            return
        if event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.position().toPoint())
            if self._pdf_scale_points is not None and len(self._pdf_scale_points) < 2 and event.modifiers() & Qt.ShiftModifier:
                self._pdf_scale_points.append(pos)
                if len(self._pdf_scale_points) == 2:
                    self._finish_pdf_scale()
                return
            self.pointClicked.emit(pos.x() * self.mm_per_scene_unit, -pos.y() * self.mm_per_scene_unit)
        super().mousePressEvent(event)

    def _context_menu(self, event) -> None:
        menu = QMenu(self)
        scale_action = QAction("Set PDF scale: Shift-click two points", self)
        scale_action.triggered.connect(self.set_pdf_scale_mode)
        menu.addAction(scale_action)
        menu.exec(event.globalPosition().toPoint())

    def _finish_pdf_scale(self) -> None:
        a, b = self._pdf_scale_points
        pixels = math.hypot(a.x() - b.x(), a.y() - b.y())
        value, ok = QInputDialog.getDouble(self, "PDF scale", "Real distance between points, mm:", 1000, 1, 1000000, 1)
        if ok and pixels > 0:
            self.mm_per_scene_unit = value / pixels
            self.pdf_scale.real_distance_mm = value
            self.pdf_scale.p1_scene = (a.x(), -a.y())
            self.pdf_scale.p2_scene = (b.x(), -b.y())
        self._pdf_scale_points = []

    def _emit_selection(self) -> None:
        self.objectSelected.emit(self.selected_object())

    def _draw_axes(self) -> None:
        axis_pen = QPen(QColor("#596272"), 0, Qt.DashLine)
        self.scene.addLine(-100000, 0, 100000, 0, axis_pen)
        self.scene.addLine(0, -100000, 0, 100000, axis_pen)
        label = QGraphicsTextItem("X=0 Y=0")
        label.setDefaultTextColor(QColor("#aab3c2"))
        label.setPos(80, 80)
        self.scene.addItem(label)
