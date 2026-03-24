from pathlib import Path
from typing import List
import os
import sys


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
GIS_DIR = ROOT_DIR / "gis"
QGIS_DIR = GIS_DIR / "apps" / "qgis"
PY_DIR = GIS_DIR / "apps" / "Python312"


def _prepend_env_path(var_name: str, values: List[str]) -> None:
    existing = os.environ.get(var_name, "")
    parts = [v for v in values if v]
    if existing:
        parts.append(existing)
    os.environ[var_name] = os.pathsep.join(parts)


def _add_sys_paths(paths: List[Path]) -> None:
    for p in paths:
        if p.exists():
            s = str(p)
            if s not in sys.path:
                sys.path.insert(0, s)


def bootstrap_qgis(prefix_path: str = None) -> None:
    """prefix_path игнорируется — все пути берутся относительно APP_DIR"""
    python_paths = [
        QGIS_DIR / "python",
        QGIS_DIR / "python" / "plugins",
        PY_DIR / "Lib" / "site-packages",
    ]
    _add_sys_paths(python_paths)

    bin_paths = [str(QGIS_DIR / "bin"), str(GIS_DIR / "bin")]
    _prepend_env_path("PATH", bin_paths)

    os.environ.setdefault("QGIS_PREFIX_PATH", str(QGIS_DIR))
    os.environ.setdefault("GDAL_DATA", str(GIS_DIR / "apps" / "gdal" / "share" / "gdal"))
    os.environ.setdefault("PROJ_LIB", str(GIS_DIR / "share" / "proj"))


def init_qgis(prefix_path: str = None, gui_enabled: bool = True):
    from qgis.core import QgsApplication
    QgsApplication.setPrefixPath(str(QGIS_DIR), True)
    app = QgsApplication([], gui_enabled)
    app.initQgis()
    return app


def init_processing() -> None:
    from qgis.core import QgsApplication
    from qgis.analysis import QgsNativeAlgorithms
    from processing.core.Processing import Processing

    Processing.initialize()
    registry = QgsApplication.processingRegistry()
    if registry.providerById("native") is None:
        registry.addProvider(QgsNativeAlgorithms())


def shutdown_qgis(app) -> None:
    if app is not None:
        app.exitQgis()
