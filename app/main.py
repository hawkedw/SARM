from pathlib import Path
import json
import sys
import traceback

from qgis_runtime import bootstrap_qgis

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"

config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
bootstrap_qgis(config["qgis_prefix_path"])

from qgis_runtime import init_qgis, init_processing, shutdown_qgis

QGS_APP = init_qgis(config["qgis_prefix_path"], gui_enabled=True)
init_processing()

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
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
from qgis.core import QgsProject
from qgis.gui import QgsLayerTreeMapCanvasBridge, QgsMapCanvas

from gdb_reader import (
    get_route_choices,
    list_raster_layers,
    list_vector_layers,
    load_raster_layer,
    load_vector_layer,
)
from processor import process_gdb


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path):
        super().__init__()
        self.config_path = config_path
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.project = QgsProject.instance()

        self.setWindowTitle("Topo Cutter MVP")
        self.resize(1500, 900)

        self.route_rows = []
        self.preview_layers = []

        self._build_ui()
        self._load_config_into_ui()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        splitter = QSplitter()
        left = QWidget()
        right = QWidget()

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        root_layout = QHBoxLayout(root)
        root_layout.addWidget(splitter)

        left_layout = QVBoxLayout(left)
        right_layout = QVBoxLayout(right)

        form = QFormLayout()

        self.gdb_edit = QLineEdit()
        gdb_btn = QPushButton("...")
        gdb_btn.clicked.connect(self.choose_gdb)
        gdb_row = self._with_button(self.gdb_edit, gdb_btn)
        form.addRow("Input GDB", gdb_row)

        self.output_edit = QLineEdit()
        out_btn = QPushButton("...")
        out_btn.clicked.connect(self.choose_output)
        out_row = self._with_button(self.output_edit, out_btn)
        form.addRow("Output folder", out_row)

        self.raster_edit = QLineEdit()
        raster_btn = QPushButton("...")
        raster_btn.clicked.connect(self.choose_raster_folder)
        raster_row = self._with_button(self.raster_edit, raster_btn)
        form.addRow("Raster folder", raster_row)

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

        self.canvas = QgsMapCanvas()
        self.canvas.setCanvasColor(Qt.white)
        self.canvas.enableAntiAliasing(True)

        self.bridge = QgsLayerTreeMapCanvasBridge(
            self.project.layerTreeRoot(),
            self.canvas
        )

        right_layout.addWidget(self.canvas)

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
        path = QFileDialog.getExistingDirectory(self, "Выбери папку с TIFF")
        if path:
            self.raster_edit.setText(path)

    def clear_project(self):
        self.project.removeAllMapLayers()
        self.preview_layers = []
        self.route_combo.clear()
        self.route_rows = []
        self.report_text.clear()

    def load_gdb(self):
        try:
            self._save_ui_to_config()
            self.clear_project()

            gdb_path = self.gdb_edit.text().strip()
            route_layer_name = self.config["route_layer_name"]
            route_name_field = self.config["route_name_field"]

            if not gdb_path:
                raise RuntimeError("Не задан путь к GDB")

            vector_layers = list_vector_layers(gdb_path)
            raster_layers = list_raster_layers(gdb_path)

            if route_layer_name not in vector_layers:
                raise RuntimeError("Слой %s не найден" % route_layer_name)

            route_layer = load_vector_layer(gdb_path, route_layer_name)
            self.project.addMapLayer(route_layer)
            self.preview_layers.append(route_layer)

            if self.config.get("load_all_layers_to_map", True):
                for name in vector_layers:
                    if name == route_layer_name:
                        continue
                    try:
                        layer = load_vector_layer(gdb_path, name)
                        self.project.addMapLayer(layer)
                        self.preview_layers.append(layer)
                    except Exception:
                        pass

                for item in raster_layers:
                    try:
                        layer = load_raster_layer(item["source"], item["name"])
                        self.project.addMapLayer(layer)
                        self.preview_layers.append(layer)
                    except Exception:
                        pass

            self.route_rows = get_route_choices(route_layer, route_name_field)
            for row in self.route_rows:
                self.route_combo.addItem(row["label"], row["fid"])

            if route_layer.isValid():
                self.canvas.setExtent(route_layer.extent())
                self.canvas.refresh()

            self.info_label.setText(
                "Загружено: vectors=%d, rasters=%d, routes=%d"
                % (len(vector_layers), len(raster_layers), len(self.route_rows))
            )

            self.report_text.setPlainText(
                "GDB loaded\n"
                "Route layer: %s\n"
                "Available routes: %d\n"
                "Vector layers: %d\n"
                "Raster layers: %d"
                % (route_layer_name, len(self.route_rows), len(vector_layers), len(raster_layers))
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

            QMessageBox.information(
                self,
                "Готово",
                "Архив сформирован:\n%s" % result["zip_path"]
            )

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
        self.report_text.setPlainText(
            "%s\n\n%s" % (ex, traceback.format_exc())
        )
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
