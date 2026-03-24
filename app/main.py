from pathlib import Path
import json
import sys
import traceback

from qgis_runtime import bootstrap_qgis

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"

config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
bootstrap_qgis()

from qgis_runtime import init_qgis, init_processing, shutdown_qgis

QGS_APP = init_qgis(gui_enabled=True)
init_processing()

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsFeature,
    QgsGeometry,
    QgsLayerTreeLayer,
    QgsLayerTreeModel,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsSimpleLineSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import (
    QgsLayerTreeMapCanvasBridge,
    QgsLayerTreeView,
    QgsMapCanvas,
    QgsMapToolPan,
    QgsRubberBand,
    QgsMapTool,
    QgsVertexMarker,
)

from gdb_reader import (
    get_route_choices,
    list_raster_layers,
    list_vector_layers,
    load_raster_layer,
    load_vector_layer,
)
from processor import process_gdb


BASEMAPS = [
    {"name": "— нет подложки —", "url": None},
    {"name": "OSM",          "url": "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0&crs=EPSG:3857"},
    {"name": "Esri Imagery", "url": "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}&zmax=19&zmin=0&crs=EPSG:3857"},
    {"name": "Esri Topo",    "url": "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}&zmax=19&zmin=0&crs=EPSG:3857"},
]

CRS_3857 = QgsCoordinateReferenceSystem("EPSG:3857")
RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".ecw", ".jp2", ".vrt"}

_GEOM_ORDER = {
    QgsWkbTypes.GeometryType.PointGeometry:   0,
    QgsWkbTypes.GeometryType.LineGeometry:    1,
    QgsWkbTypes.GeometryType.PolygonGeometry: 2,
}

RENDER_POINTS = QgsUnitTypes.RenderUnit.RenderPoints
SELECT_TOLERANCE_PX = 8
VERTEX_SNAP_PX = 12


def _apply_route_lines_style(layer: QgsVectorLayer):
    black = QgsSimpleLineSymbolLayer(QColor(0, 0, 0))
    black.setWidth(2.8)
    black.setWidthUnit(RENDER_POINTS)
    red = QgsSimpleLineSymbolLayer(QColor(220, 0, 0))
    red.setWidth(2.8)
    red.setWidthUnit(RENDER_POINTS)
    symbol = QgsLineSymbol()
    symbol.deleteSymbolLayer(0)
    symbol.appendSymbolLayer(black)
    symbol.appendSymbolLayer(red)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))


class VertexEditTool(QgsMapTool):
    def __init__(self, canvas: QgsMapCanvas, layer: QgsVectorLayer, fids: list):
        super().__init__(canvas)
        self._layer = layer
        self._fids = fids
        self.setCursor(Qt.CursorShape.ArrowCursor)

        self._hover_marker = QgsVertexMarker(canvas)
        self._hover_marker.setColor(QColor(0, 0, 255))
        self._hover_marker.setFillColor(QColor(0, 0, 255, 80))
        self._hover_marker.setIconType(QgsVertexMarker.IconType.ICON_BOX)
        self._hover_marker.setIconSize(10)
        self._hover_marker.setVisible(False)

        self._drag_rb = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PointGeometry)
        self._drag_rb.setColor(QColor(255, 0, 0, 200))
        self._drag_rb.setIconSize(8)

        self._dragging = False
        self._drag_fid = None
        self._drag_vertex_idx = None

    def _canvas_to_layer(self, pt):
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        layer_crs = self._layer.crs()
        if canvas_crs == layer_crs:
            return pt
        return QgsCoordinateTransform(canvas_crs, layer_crs, QgsProject.instance()).transform(pt)

    def _layer_to_canvas(self, pt):
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        layer_crs = self._layer.crs()
        if canvas_crs == layer_crs:
            return pt
        return QgsCoordinateTransform(layer_crs, canvas_crs, QgsProject.instance()).transform(pt)

    def _find_nearest_vertex(self, canvas_pt):
        tol = VERTEX_SNAP_PX * self.canvas().mapUnitsPerPixel()
        best = None
        best_dist = tol
        for fid in self._fids:
            feat = self._layer.getFeature(fid)
            if not feat.isValid():
                continue
            idx = 0
            for v in feat.geometry().vertices():
                vpt = self._layer_to_canvas(QgsPointXY(v.x(), v.y()))
                dist = canvas_pt.distance(vpt)
                if dist < best_dist:
                    best_dist = dist
                    best = (fid, idx, vpt)
                idx += 1
        return best

    def canvasMoveEvent(self, e):
        pt = self.toMapCoordinates(e.pos())
        if self._dragging:
            self._drag_rb.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self._drag_rb.addPoint(pt)
            self._hover_marker.setCenter(pt)
            return
        result = self._find_nearest_vertex(pt)
        if result:
            self._hover_marker.setCenter(result[2])
            self._hover_marker.setVisible(True)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._hover_marker.setVisible(False)
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        result = self._find_nearest_vertex(self.toMapCoordinates(e.pos()))
        if result:
            self._dragging = True
            self._drag_fid, self._drag_vertex_idx, vpt = result
            self._drag_rb.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self._drag_rb.addPoint(vpt)

    def canvasReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        new_pt = self._canvas_to_layer(self.toMapCoordinates(e.pos()))
        feat = self._layer.getFeature(self._drag_fid)
        geom = QgsGeometry(feat.geometry())
        geom.moveVertex(new_pt.x(), new_pt.y(), self._drag_vertex_idx)
        self._layer.changeGeometry(self._drag_fid, geom)
        self.canvas().refresh()
        self._dragging = False
        self._drag_fid = None
        self._drag_vertex_idx = None
        self._drag_rb.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self._hover_marker.setVisible(False)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._dragging = False
            self._drag_rb.reset(QgsWkbTypes.GeometryType.PointGeometry)
            self._hover_marker.setVisible(False)

    def deactivate(self):
        self._drag_rb.reset(QgsWkbTypes.GeometryType.PointGeometry)
        self._hover_marker.setVisible(False)
        super().deactivate()


class SelectLineTool(QgsMapTool):
    selectionChanged = pyqtSignal()

    def __init__(self, canvas, route_layer):
        super().__init__(canvas)
        self._layer = route_layer
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self.toMapCoordinates(e.pos())
        tol = SELECT_TOLERANCE_PX * self.canvas().mapUnitsPerPixel()
        rect = QgsRectangle(pt.x() - tol, pt.y() - tol, pt.x() + tol, pt.y() + tol)
        canvas_crs = self.canvas().mapSettings().destinationCrs()
        layer_crs = self._layer.crs()
        if canvas_crs != layer_crs:
            rect = QgsCoordinateTransform(canvas_crs, layer_crs, QgsProject.instance()).transformBoundingBox(rect)
        self._layer.selectByRect(rect, QgsVectorLayer.SelectBehavior.SetSelection)
        self.selectionChanged.emit()


class LineDrawTool(QgsMapTool):
    lineFinished = pyqtSignal(list)

    def __init__(self, canvas):
        super().__init__(canvas)
        self._points = []
        self._rb = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._rb.setColor(QColor(255, 0, 0, 200))
        self._rb.setWidth(2)

    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self.toMapCoordinates(e.pos())
        self._points.append(pt)
        self._rb.addPoint(pt)

    def canvasDoubleClickEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._points:
            self._points.pop()
            self._rb.removeLastPoint()
        if len(self._points) >= 2:
            self._finish()

    def canvasMoveEvent(self, e):
        if not self._points:
            return
        pt = self.toMapCoordinates(e.pos())
        if self._rb.numberOfVertices() > len(self._points):
            self._rb.movePoint(pt)
        else:
            self._rb.addPoint(pt)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._reset()
            self.canvas().unsetMapTool(self)

    def _finish(self):
        pts = list(self._points)
        self._reset()
        self.lineFinished.emit(pts)

    def _reset(self):
        self._points = []
        self._rb.reset(QgsWkbTypes.GeometryType.LineGeometry)

    def deactivate(self):
        self._reset()
        super().deactivate()


class RouteNameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый маршрут")
        self.setFixedWidth(320)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Название маршрута:"))
        self.name_edit = QLineEdit()
        layout.addWidget(self.name_edit)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def route_name(self):
        return self.name_edit.text().strip()


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.project = QgsProject.instance()
        self.project.setCrs(CRS_3857)

        self.setWindowTitle("Topo Cutter MVP")
        self.resize(1600, 900)

        self.route_rows = []
        self.preview_layers = []
        self._basemap_layer_id = None
        self._route_layer: QgsVectorLayer | None = None
        self._draw_tool = None
        self._vertex_tool = None
        self._buffer_rb: QgsRubberBand | None = None  # резинка буфера

        self._build_ui()
        self._load_config_into_ui()

        self.canvas.setDestinationCrs(CRS_3857)
        QTimer.singleShot(200, lambda: self.basemap_combo.setCurrentIndex(1))

    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        right = QWidget()
        main_splitter.addWidget(left)
        main_splitter.addWidget(right)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setSizes([320, 1280])

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(4, 4, 4, 4)
        root_layout.addWidget(main_splitter)

        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)

        form = QFormLayout()
        self.gdb_edit = QLineEdit()
        gdb_btn = QPushButton("...")
        gdb_btn.setFixedWidth(28)
        gdb_btn.clicked.connect(self.choose_gdb)
        form.addRow("Input GDB", self._with_button(self.gdb_edit, gdb_btn))

        self.output_edit = QLineEdit()
        out_btn = QPushButton("...")
        out_btn.setFixedWidth(28)
        out_btn.clicked.connect(self.choose_output)
        form.addRow("Output folder", self._with_button(self.output_edit, out_btn))

        self.raster_edit = QLineEdit()
        raster_btn = QPushButton("...")
        raster_btn.setFixedWidth(28)
        raster_btn.clicked.connect(self.choose_raster_folder)
        form.addRow("Raster folder", self._with_button(self.raster_edit, raster_btn))

        self.buffer_spin = QDoubleSpinBox()
        self.buffer_spin.setRange(1, 1000000)
        self.buffer_spin.setDecimals(2)
        self.buffer_spin.setSuffix(" m")
        self.buffer_spin.valueChanged.connect(self._on_buffer_changed)
        form.addRow("Buffer", self.buffer_spin)

        self.route_combo = QComboBox()
        self.route_combo.currentIndexChanged.connect(self._on_route_combo_changed)
        form.addRow("Route", self.route_combo)

        self.load_btn = QPushButton("Load GDB")
        self.load_btn.clicked.connect(self.load_gdb)
        self.run_btn = QPushButton("Run")
        self.run_btn.clicked.connect(self.run_export)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.load_btn)
        btn_row.addWidget(self.run_btn)

        self.info_label = QLabel("Готово")
        self.info_label.setWordWrap(True)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)

        left_layout.addLayout(form)
        left_layout.addLayout(btn_row)
        left_layout.addWidget(self.info_label)
        left_layout.addWidget(self.report_text, 1)

        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        basemap_row = QHBoxLayout()
        basemap_row.addWidget(QLabel("Подложка:"))
        self.basemap_combo = QComboBox()
        for bm in BASEMAPS:
            self.basemap_combo.addItem(bm["name"])
        self.basemap_combo.currentIndexChanged.connect(self._switch_basemap)
        basemap_row.addWidget(self.basemap_combo)
        basemap_row.addStretch()
        right_layout.addLayout(basemap_row)

        edit_bar = QHBoxLayout()

        self.btn_pan = QPushButton("🖐 Навигация")
        self.btn_pan.clicked.connect(self._activate_pan)
        self.btn_pan.setEnabled(False)

        self.btn_select = QPushButton("→ Выборка")
        self.btn_select.clicked.connect(self._activate_select)
        self.btn_select.setEnabled(False)

        self.btn_new_line = QPushButton("⊕ Новая линия")
        self.btn_new_line.clicked.connect(self._start_draw)
        self.btn_new_line.setEnabled(False)

        self.chk_vertex_edit = QCheckBox("Вершины")
        self.chk_vertex_edit.setToolTip(
            "Режим редактирования вершин.\n"
            "Наведите на вершину — подсветится.\n"
            "Зажмите и перетащите — переместите."
        )
        self.chk_vertex_edit.setEnabled(False)
        self.chk_vertex_edit.stateChanged.connect(self._on_vertex_edit_toggled)

        self.btn_delete = QPushButton("✘ Удалить")
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_delete.setEnabled(False)

        self.btn_save = QPushButton("💾 Сохранить")
        self.btn_save.clicked.connect(self._save_edits)
        self.btn_save.setEnabled(False)

        self.btn_cancel_edit = QPushButton("↺ Отменить всё")
        self.btn_cancel_edit.clicked.connect(self._cancel_edits)
        self.btn_cancel_edit.setEnabled(False)

        edit_bar.addWidget(self.btn_pan)
        edit_bar.addWidget(self.btn_select)
        edit_bar.addWidget(self.btn_new_line)
        edit_bar.addWidget(self.chk_vertex_edit)
        edit_bar.addWidget(self.btn_delete)
        edit_bar.addStretch()
        edit_bar.addWidget(self.btn_save)
        edit_bar.addWidget(self.btn_cancel_edit)
        right_layout.addLayout(edit_bar)

        map_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.canvas = QgsMapCanvas()
        self.canvas.setCanvasColor(QColor(255, 255, 255))
        self.canvas.enableAntiAliasing(True)

        self.bridge = QgsLayerTreeMapCanvasBridge(
            self.project.layerTreeRoot(), self.canvas
        )
        self._layer_tree_model = QgsLayerTreeModel(self.project.layerTreeRoot())
        self._layer_tree_model.setFlag(QgsLayerTreeModel.Flag.AllowNodeReorder)
        self._layer_tree_model.setFlag(QgsLayerTreeModel.Flag.AllowNodeRename, False)
        self._layer_tree_model.setFlag(QgsLayerTreeModel.Flag.AllowNodeChangeVisibility)

        self.layer_tree_view = QgsLayerTreeView()
        self.layer_tree_view.setModel(self._layer_tree_model)
        self.layer_tree_view.setMinimumWidth(180)

        map_splitter.addWidget(self.layer_tree_view)
        map_splitter.addWidget(self.canvas)
        map_splitter.setStretchFactor(0, 0)
        map_splitter.setStretchFactor(1, 1)
        map_splitter.setSizes([220, 1060])
        right_layout.addWidget(map_splitter, 1)

        self._pan_tool = QgsMapToolPan(self.canvas)
        self.canvas.setMapTool(self._pan_tool)
        self.canvas.setWheelFactor(2.0)

    # ------------------------------------------------------------------
    # Буфер
    # ------------------------------------------------------------------
    def _clear_buffer_rb(self):
        if self._buffer_rb is not None:
            self._buffer_rb.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            self._buffer_rb = None

    def _draw_buffer(self, fid: int):
        """Build buffer in metres in EPSG:3857 and draw as rubber band."""
        self._clear_buffer_rb()
        if self._route_layer is None:
            return
        feat = self._route_layer.getFeature(fid)
        if not feat.isValid():
            return

        # Преобразуем геометрию в 3857 (метры)
        layer_crs = self._route_layer.crs()
        tr = QgsCoordinateTransform(layer_crs, CRS_3857, self.project)
        geom_3857 = QgsGeometry(feat.geometry())
        geom_3857.transform(tr)

        buf_m = self.buffer_spin.value()
        buf_geom = geom_3857.buffer(buf_m, 32)  # 32 сегмента дуги

        rb = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        rb.setColor(QColor(30, 120, 255, 60))       # полупрозрачная заливка
        rb.setStrokeColor(QColor(30, 120, 255, 180))  # контур
        rb.setWidth(2)
        rb.setToGeometry(buf_geom, None)  # геометрия уже в CRS канваса
        self._buffer_rb = rb

    def _on_buffer_changed(self, _value):
        fid = self.route_combo.currentData()
        if fid is not None:
            self._draw_buffer(fid)

    # ------------------------------------------------------------------
    # Route combo
    # ------------------------------------------------------------------
    def _on_route_combo_changed(self, index):
        if self._route_layer is None:
            return
        fid = self.route_combo.currentData()
        if fid is None:
            # заглушка "— выберите —"
            self._route_layer.removeSelection()
            self._clear_buffer_rb()
            self.canvas.refresh()
            return

        # Выбрать фичер
        self._route_layer.selectByIds([fid])

        # Zoom to extent с паддингом
        feat = self._route_layer.getFeature(fid)
        if feat.isValid():
            layer_crs = self._route_layer.crs()
            tr = QgsCoordinateTransform(layer_crs, CRS_3857, self.project)
            geom_3857 = QgsGeometry(feat.geometry())
            geom_3857.transform(tr)
            bbox = geom_3857.boundingBox()
            # паддинг = буфер + 20%
            pad = self.buffer_spin.value() * 1.2
            bbox.grow(max(pad, bbox.width() * 0.1, bbox.height() * 0.1))
            self.canvas.setExtent(bbox)

        # Отрисовать буфер
        self._draw_buffer(fid)
        self.canvas.refresh()

    # ------------------------------------------------------------------
    def _enable_edit_toolbar(self):
        for btn in (self.btn_pan, self.btn_select, self.btn_new_line,
                    self.btn_save, self.btn_cancel_edit):
            btn.setEnabled(True)
        self.chk_vertex_edit.setEnabled(False)
        self.chk_vertex_edit.setChecked(False)
        self.btn_delete.setEnabled(False)

    def _activate_pan(self):
        if self._route_layer:
            self._route_layer.removeSelection()
        self._reset_vertex_edit()
        self._update_selection_buttons()
        self.canvas.setMapTool(self._pan_tool)
        self.info_label.setText("Навигация по карте.")

    def _activate_select(self):
        if self._route_layer is None:
            return
        self._reset_vertex_edit()
        tool = SelectLineTool(self.canvas, self._route_layer)
        tool.selectionChanged.connect(self._on_selection_changed)
        self.canvas.setMapTool(tool)
        self.info_label.setText("Кликните на линию маршрута для выборки.")

    def _on_selection_changed(self):
        if self._route_layer is None:
            return
        has_sel = self._route_layer.selectedFeatureCount() > 0
        self.chk_vertex_edit.setEnabled(has_sel)
        self.btn_delete.setEnabled(has_sel)
        if not has_sel:
            self._reset_vertex_edit()
        if has_sel:
            names = [f[self.config["route_name_field"]] or "(no name)"
                     for f in self._route_layer.selectedFeatures()]
            self.info_label.setText("Выбрано: %s" % ", ".join(str(n) for n in names))
        else:
            self.info_label.setText("Ничего не выбрано.")

    def _update_selection_buttons(self):
        has_sel = bool(self._route_layer and self._route_layer.selectedFeatureCount() > 0)
        self.chk_vertex_edit.setEnabled(has_sel)
        self.btn_delete.setEnabled(has_sel)

    def _on_vertex_edit_toggled(self, state):
        if state == Qt.CheckState.Checked.value:
            if self._route_layer is None or self._route_layer.selectedFeatureCount() == 0:
                self.chk_vertex_edit.blockSignals(True)
                self.chk_vertex_edit.setChecked(False)
                self.chk_vertex_edit.blockSignals(False)
                self.info_label.setText("Сначала выберите линию.")
                return
            fids = list(self._route_layer.selectedFeatureIds())
            self._vertex_tool = VertexEditTool(self.canvas, self._route_layer, fids)
            self.canvas.setMapTool(self._vertex_tool)
            self.info_label.setText(
                "Режим вершин: наведите на вершину (подсветится), зажмите и перетащите. "
                "Потом ‘💾 Сохранить’."
            )
        else:
            self._vertex_tool = None
            self.canvas.setMapTool(self._pan_tool)
            self.info_label.setText("Режим вершин отключён.")

    def _reset_vertex_edit(self):
        if self.chk_vertex_edit.isChecked():
            self.chk_vertex_edit.blockSignals(True)
            self.chk_vertex_edit.setChecked(False)
            self.chk_vertex_edit.blockSignals(False)
            self._vertex_tool = None

    def _start_draw(self):
        if self._route_layer is None:
            return
        self._route_layer.removeSelection()
        self._reset_vertex_edit()
        self._update_selection_buttons()
        tool = LineDrawTool(self.canvas)
        tool.lineFinished.connect(self._on_line_finished)
        self._draw_tool = tool
        self.canvas.setMapTool(tool)
        self.info_label.setText("Клик — вершина. Двойной клик — завершить. Esc — отмена.")

    def _on_line_finished(self, points: list):
        self.canvas.setMapTool(self._pan_tool)
        self._draw_tool = None
        if len(points) < 2:
            self.info_label.setText("Нужно минимум 2 вершины.")
            return
        dlg = RouteNameDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self.info_label.setText("Создание отменено.")
            return
        name = dlg.route_name()
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        layer_crs = self._route_layer.crs()
        transform = QgsCoordinateTransform(canvas_crs, layer_crs, self.project)
        layer_points = [transform.transform(pt) for pt in points]
        geom = QgsGeometry.fromPolylineXY(layer_points)
        feat = QgsFeature(self._route_layer.fields())
        feat.setGeometry(geom)
        feat[self.config["route_name_field"]] = name
        self._route_layer.addFeature(feat)
        self.canvas.refresh()
        self.info_label.setText("Линия «%s» добавлена. Не забудьте сохранить." % name)

    def _delete_selected(self):
        if self._route_layer is None:
            return
        selected = self._route_layer.selectedFeatureIds()
        if not selected:
            self.info_label.setText("Сначала выберите линию.")
            return
        self._reset_vertex_edit()
        self._clear_buffer_rb()
        self._route_layer.deleteFeatures(list(selected))
        self.canvas.refresh()
        self._update_selection_buttons()
        self.info_label.setText("Удалено %d объектов." % len(selected))

    def _save_edits(self):
        if self._route_layer is None:
            return
        self._reset_vertex_edit()
        self._route_layer.commitChanges()
        self._route_layer.startEditing()
        self.canvas.setMapTool(self._pan_tool)
        self._update_selection_buttons()
        self._refresh_route_combo()
        # Перерисовать буфер если маршрут выбран
        fid = self.route_combo.currentData()
        if fid is not None:
            self._draw_buffer(fid)
        self.info_label.setText("Изменения сохранены.")

    def _cancel_edits(self):
        if self._route_layer is None:
            return
        self._reset_vertex_edit()
        self._route_layer.rollBack()
        self._route_layer.startEditing()
        self.canvas.setMapTool(self._pan_tool)
        self.canvas.refresh()
        self._update_selection_buttons()
        fid = self.route_combo.currentData()
        if fid is not None:
            self._draw_buffer(fid)
        self.info_label.setText("Изменения отменены.")

    def _refresh_route_combo(self):
        if self._route_layer is None:
            return
        current_fid = self.route_combo.currentData()
        self.route_combo.blockSignals(True)
        self.route_combo.clear()
        self.route_combo.addItem("— выберите маршрут —", None)
        self.route_rows = get_route_choices(
            self._route_layer, self.config["route_name_field"]
        )
        for row in self.route_rows:
            self.route_combo.addItem(row["label"], row["fid"])
        self.route_combo.blockSignals(False)
        # Восстановить выбор
        for i in range(self.route_combo.count()):
            if self.route_combo.itemData(i) == current_fid:
                self.route_combo.setCurrentIndex(i)
                return
        self.route_combo.setCurrentIndex(0)

    def _add_layer_ordered(self, layer, visible: bool = True):
        self.project.addMapLayer(layer, False)
        root = self.project.layerTreeRoot()
        node = QgsLayerTreeLayer(layer)
        node.setItemVisibilityChecked(visible)
        is_vector = isinstance(layer, QgsVectorLayer)
        geom_order = _GEOM_ORDER.get(
            layer.geometryType() if is_vector else None, 3
        ) if is_vector else 3
        children = root.children()
        insert_pos = len(children)
        for i, child in enumerate(children):
            if not isinstance(child, QgsLayerTreeLayer):
                continue
            child_layer = child.layer()
            if child_layer is None:
                continue
            if child_layer.id() == self._basemap_layer_id:
                insert_pos = i
                break
            child_is_vec = isinstance(child_layer, QgsVectorLayer)
            child_order = _GEOM_ORDER.get(
                child_layer.geometryType() if child_is_vec else None, 3
            ) if child_is_vec else 3
            if child_order > geom_order:
                insert_pos = i
                break
        root.insertChildNode(insert_pos, node)
        self.preview_layers.append(layer)

    def _switch_basemap(self, index):
        if self._basemap_layer_id is not None:
            self.project.removeMapLayer(self._basemap_layer_id)
            self._basemap_layer_id = None
        bm = BASEMAPS[index]
        if bm["url"] is None:
            self.canvas.refresh()
            return
        layer = QgsRasterLayer(bm["url"], bm["name"], "wms")
        if not layer.isValid():
            self.info_label.setText("Не удалось загрузить подложку: %s" % bm["name"])
            return
        self.project.addMapLayer(layer, False)
        self._basemap_layer_id = layer.id()
        root = self.project.layerTreeRoot()
        root.insertChildNode(-1, QgsLayerTreeLayer(layer))
        self.canvas.refresh()

    def _with_button(self, line_edit, button):
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, 1)
        layout.addWidget(button)
        return w

    def _load_config_into_ui(self):
        self.gdb_edit.setText(self.config.get("input_gdb_path", ""))
        self.output_edit.setText(self.config.get("output_root", ""))
        self.raster_edit.setText(self.config.get("input_raster_folder", ""))
        self.buffer_spin.setValue(float(self.config.get("default_buffer_m", 500)))

    def _save_ui_to_config(self):
        self.config["input_gdb_path"] = self.gdb_edit.text().strip()
        self.config["output_root"] = self.output_edit.text().strip()
        self.config["input_raster_folder"] = self.raster_edit.text().strip()
        self.config["default_buffer_m"] = float(self.buffer_spin.value())
        self.config_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def choose_gdb(self):
        path = QFileDialog.getExistingDirectory(self, "Выбери .gdb каталог")
        if path:
            self.gdb_edit.setText(path)

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Выбери выходной каталог")
        if path:
            self.output_edit.setText(path)

    def choose_raster_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Выбери папку с растрами")
        if path:
            self.raster_edit.setText(path)

    def _load_rasters_from_folder(self, folder_path: str) -> int:
        folder = Path(folder_path)
        if not folder.exists():
            return 0
        count = 0
        for f in sorted(folder.rglob("*")):
            if f.suffix.lower() in RASTER_EXTENSIONS:
                layer = QgsRasterLayer(str(f), f.stem, "gdal")
                if layer.isValid():
                    self._add_layer_ordered(layer, visible=False)
                    count += 1
        return count

    def clear_project(self):
        if self._route_layer and self._route_layer.isEditable():
            self._route_layer.rollBack()
        self._clear_buffer_rb()
        self.project.removeAllMapLayers()
        self._basemap_layer_id = None
        self._route_layer = None
        self.preview_layers = []
        self.route_combo.blockSignals(True)
        self.route_combo.clear()
        self.route_combo.blockSignals(False)
        self.route_rows = []
        self.report_text.clear()
        self._vertex_tool = None
        for btn in (self.btn_pan, self.btn_select, self.btn_new_line,
                    self.btn_delete, self.btn_save, self.btn_cancel_edit):
            btn.setEnabled(False)
        self.chk_vertex_edit.setEnabled(False)
        self.chk_vertex_edit.setChecked(False)
        idx = self.basemap_combo.currentIndex()
        if idx > 0:
            self._switch_basemap(idx)

    def load_gdb(self):
        try:
            self._save_ui_to_config()
            self.clear_project()
            gdb_path = self.gdb_edit.text().strip()
            raster_folder = self.raster_edit.text().strip()
            route_layer_name = self.config["route_layer_name"]
            route_name_field = self.config["route_name_field"]
            if not gdb_path:
                raise RuntimeError("Не задан путь к GDB")
            vector_names = list_vector_layers(gdb_path)
            raster_items = list_raster_layers(gdb_path)
            if route_layer_name not in vector_names:
                raise RuntimeError("Слой %s не найден" % route_layer_name)
            route_layer = None
            for name in vector_names:
                try:
                    layer = load_vector_layer(gdb_path, name)
                    if name == route_layer_name:
                        _apply_route_lines_style(layer)
                        route_layer = layer
                    self._add_layer_ordered(layer, visible=True)
                except Exception:
                    pass
            for item in raster_items:
                try:
                    layer = load_raster_layer(item["source"], item["name"])
                    self._add_layer_ordered(layer, visible=True)
                except Exception:
                    pass
            folder_raster_count = 0
            if raster_folder:
                folder_raster_count = self._load_rasters_from_folder(raster_folder)
            if route_layer is None:
                raise RuntimeError("Не удалось загрузить %s" % route_layer_name)
            self._route_layer = route_layer
            self._route_layer.startEditing()
            self._enable_edit_toolbar()

            # Заполняем комбо: сначала заглушка, потом маршруты
            self.route_combo.blockSignals(True)
            self.route_combo.addItem("— выберите маршрут —", None)
            self.route_rows = get_route_choices(route_layer, route_name_field)
            for row in self.route_rows:
                self.route_combo.addItem(row["label"], row["fid"])
            self.route_combo.setCurrentIndex(0)
            self.route_combo.blockSignals(False)

            # Zoom на весь слой
            transform = QgsCoordinateTransform(route_layer.crs(), CRS_3857, self.project)
            extent_3857 = transform.transformBoundingBox(route_layer.extent())
            extent_3857.grow(extent_3857.width() * 0.1)
            self.canvas.setExtent(extent_3857)
            self.canvas.refresh()

            self.info_label.setText(
                "Загружено: vectors=%d, rasters_gdb=%d, rasters_folder=%d, routes=%d"
                % (len(vector_names), len(raster_items), folder_raster_count, len(self.route_rows))
            )
            self.report_text.setPlainText(
                "GDB: %s\nRoute layer: %s\nRoutes: %d\nVectors: %d\nRasters (GDB): %d\nRasters (folder): %d"
                % (gdb_path, route_layer_name, len(self.route_rows),
                   len(vector_names), len(raster_items), folder_raster_count)
            )
        except Exception as ex:
            self.show_error(ex)

    def run_export(self):
        try:
            self._save_ui_to_config()
            gdb_path = self.gdb_edit.text().strip()
            output_root = self.output_edit.text().strip()
            input_rasters = self.raster_edit.text().strip()
            route_fid = self.route_combo.currentData()
            if not gdb_path:
                raise RuntimeError("Не задан путь к GDB")
            if not output_root:
                raise RuntimeError("Не задан output folder")
            if route_fid is None:
                raise RuntimeError("Не выбран маршрут")
            result = process_gdb(
                gdb_path=gdb_path,
                output_root=output_root,
                route_layer_name=self.config["route_layer_name"],
                route_name_field=self.config["route_name_field"],
                route_fid=int(route_fid),
                buffer_m=float(self.buffer_spin.value()),
                processing_crs_epsg=self.config.get("processing_crs_epsg"),
                input_raster_folder=input_rasters or None
            )
            manifest = result["manifest"]
            self.report_text.setPlainText(self.format_manifest(manifest))
            self.info_label.setText("Готово. ZIP: %s" % result["zip_path"])
            QMessageBox.information(self, "Готово", "Архив сформирован:\n%s" % result["zip_path"])
        except Exception as ex:
            self.show_error(ex)

    def format_manifest(self, manifest: dict) -> str:
        lines = []
        lines.append("GDB: %s" % manifest["gdb_path"])
        lines.append("Route: %s [FID=%d]" % (manifest["route_name"], manifest["route_fid"]))
        lines.append("Buffer: %s m" % manifest["buffer_m"])
        lines.append("Processing CRS: %s" % manifest["processing_crs"])
        lines.append("")
        lines.append("Summary:")
        for k, v in manifest["summary"].items():
            lines.append("  %s: %s" % (k, v))
        lines.append("")
        lines.append("Layers:")
        for item in manifest["layers"]:
            lines.append(
                "  %s | %s | %s | in=%s | out=%s"
                % (item["layer"], item["type"], item["status"],
                   item.get("input_count", "-"), item.get("output_count", "-"))
            )
        return "\n".join(lines)

    def show_error(self, ex: Exception):
        self.info_label.setText("Ошибка")
        self.report_text.setPlainText("%s\n\n%s" % (ex, traceback.format_exc()))
        QMessageBox.critical(self, "Ошибка", str(ex))


def main():
    window = MainWindow(CONFIG_PATH)
    window.show()
    return QGS_APP.exec()


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    finally:
        shutdown_qgis(QGS_APP)
    sys.exit(exit_code)
