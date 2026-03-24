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

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QComboBox,
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
    QgsLayerTreeLayer,
    QgsLayerTreeModel,
    QgsLineSymbol,
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
    QgsMapToolPan,
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

        map_splitter = QSplitter(Qt.Orientation.Horizontal)

        self.canvas = QgsMapCanvas()
        self.canvas.setCanvasColor(QColor(255, 255, 255))
        self.canvas.enableAntiAliasing(True)

        self.bridge = QgsLayerTreeMapCanvasBridge(
            self.project.layerTreeRoot(),
            self.canvas
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
        self.project.removeAllMapLayers()
        self._basemap_layer_id = None
        self.preview_layers = []
        self.route_combo.clear()
        self.route_rows = []
        self.report_text.clear()
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
