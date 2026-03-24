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
    QApplication,
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
    QgsFeature,
    QgsGeometry,
    QgsLayerTreeLayer,
    QgsLayerTreeModel,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
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
    QgsMapToolEdit,
    QgsMapToolPan,
    QgsRubberBand,
    QgsMapTool,
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


class LineDrawTool(QgsMapTool):
    """Single click — add vertex. Double click — finish line."""
    lineFinished = pyqtSignal(list)  # list[QgsPointXY]

    def __init__(self, canvas: QgsMapCanvas):
        super().__init__(canvas)
        self._points: list[QgsPointXY] = []
        self._pending_pt: QgsPointXY | None = None
        self._rb = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.LineGeometry)
        self._rb.setColor(QColor(255, 0, 0, 180))
        self._rb.setWidth(2)
        # Таймер для различения single/double click
        self._click_timer = QTimer()
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(self._commit_pending)

    def canvasMoveEvent(self, e):
        if self._points:
            pt = self.toMapCoordinates(e.pos())
            if self._rb.numberOfVertices() > len(self._points):
                self._rb.movePoint(pt)
            else:
                self._rb.addPoint(pt)

    def canvasPressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = self.toMapCoordinates(e.pos())
        if self._click_timer.isActive():
            # Это двойной клик — отменяем таймер, финишим
            self._click_timer.stop()
            self._pending_pt = None
            self._finish()
        else:
            # Первый клик — откладываем добавление вершины
            self._pending_pt = pt
            self._click_timer.start()

    def _commit_pending(self):
        """Single click confirmed — add vertex."""
        if self._pending_pt is not None:
            self._points.append(self._pending_pt)
            self._pending_pt = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self._click_timer.stop()
            self._reset()
            self.canvas().unsetMapTool(self)

    def _finish(self):
        pts = list(self._points)
        self._reset()
        self.lineFinished.emit(pts)

    def _reset(self):
        self._points = []
        self._pending_pt = None
        self._rb.reset(QgsWkbTypes.GeometryType.LineGeometry)

    def deactivate(self):
        self._click_timer.stop()
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

    def route_name(self) -> str:
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
        self._draw_tool: LineDrawTool | None = None

        self._build_ui()
        self._load_config_into_ui()

        self.canvas.setDestinationCrs(CRS_3857)
        QTimer.singleShot(200, lambda: self.basemap_combo.setCurrentIndex(1))

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
        form.addRow("Buffer", self.buffer_spin)

        self.route_combo = QComboBox()
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
        self.btn_new_line = QPushButton("⊕ Новая линия")
        self.btn_new_line.clicked.connect(self._start_draw)
        self.btn_new_line.setEnabled(False)
        self.btn_vertex_edit = QPushButton("▦ Вершины")
        self.btn_vertex_edit.clicked.connect(self._start_vertex_edit)
        self.btn_vertex_edit.setEnabled(False)
        self.btn_delete = QPushButton("✘ Удалить")
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_delete.setEnabled(False)
        self.btn_save = QPushButton("💾 Сохранить")
        self.btn_save.clicked.connect(self._save_edits)
        self.btn_save.setEnabled(False)
        self.btn_cancel_edit = QPushButton("↺ Отменить всё")
        self.btn_cancel_edit.clicked.connect(self._cancel_edits)
        self.btn_cancel_edit.setEnabled(False)

        edit_bar.addWidget(self.btn_new_line)
        edit_bar.addWidget(self.btn_vertex_edit)
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
    def _enable_edit_toolbar(self):
        for btn in (self.btn_new_line, self.btn_vertex_edit, self.btn_delete,
                    self.btn_save, self.btn_cancel_edit):
            btn.setEnabled(True)

    def _start_draw(self):
        if self._route_layer is None:
            return
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

    def _start_vertex_edit(self):
        if self._route_layer is None:
            return
        tool = QgsMapToolEdit(self.canvas)
        self.canvas.setMapTool(tool)
        self.info_label.setText("Кликните на линию и перетащивайте вершины.")

    def _delete_selected(self):
        if self._route_layer is None:
            return
        selected = self._route_layer.selectedFeatureIds()
        if not selected:
            self.info_label.setText("Сначала выберите линию кликом.")
            return
        self._route_layer.deleteFeatures(list(selected))
        self.canvas.refresh()
        self.info_label.setText("Удалено %d объектов." % len(selected))

    def _save_edits(self):
        if self._route_layer is None:
            return
        self._route_layer.commitChanges()
        self._route_layer.startEditing()
        self.canvas.setMapTool(self._pan_tool)
        self._refresh_route_combo()
        self.info_label.setText("Изменения сохранены.")

    def _cancel_edits(self):
        if self._route_layer is None:
            return
        self._route_layer.rollBack()
        self._route_layer.startEditing()
        self.canvas.setMapTool(self._pan_tool)
        self.canvas.refresh()
        self.info_label.setText("Изменения отменены.")

    def _refresh_route_combo(self):
        if self._route_layer is None:
            return
        current_fid = self.route_combo.currentData()
        self.route_combo.clear()
        self.route_rows = get_route_choices(
            self._route_layer, self.config["route_name_field"]
        )
        for row in self.route_rows:
            self.route_combo.addItem(row["label"], row["fid"])
        for i in range(self.route_combo.count()):
            if self.route_combo.itemData(i) == current_fid:
                self.route_combo.setCurrentIndex(i)
                break

    # ------------------------------------------------------------------
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
        self.project.removeAllMapLayers()
        self._basemap_layer_id = None
        self._route_layer = None
        self.preview_layers = []
        self.route_combo.clear()
        self.route_rows = []
        self.report_text.clear()
        for btn in (self.btn_new_line, self.btn_vertex_edit, self.btn_delete,
                    self.btn_save, self.btn_cancel_edit):
            btn.setEnabled(False)
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

            self.route_rows = get_route_choices(route_layer, route_name_field)
            for row in self.route_rows:
                self.route_combo.addItem(row["label"], row["fid"])

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
