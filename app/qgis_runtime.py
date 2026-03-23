from pathlib import Path
from typing import List
import os
import sys


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


def resolve_qgis_prefix(prefix_path: str) -> Path:
    root = Path(prefix_path).resolve()
    candidates = [
        root / "apps" / "qgis",
        root,
    ]

    for c in candidates:
        if (c / "python").exists() or (c / "bin").exists():
            return c

    return root


def bootstrap_qgis(prefix_path: str) -> None:
    root = Path(prefix_path).resolve()

    qgis_apps = root / "apps" / "qgis"
    share_ngqgis_python = root / "share" / "ngqgis" / "python"
    share_ngqgis_plugins = share_ngqgis_python / "plugins"

    python_candidates = [
        qgis_apps / "python",
        root / "python",
        share_ngqgis_python,
    ]

    plugin_candidates = [
        qgis_apps / "python" / "plugins",
        root / "python" / "plugins",
        share_ngqgis_plugins,
    ]

    bin_candidates = [
        root / "bin",
        qgis_apps / "bin",
    ]

    _add_sys_paths(python_candidates + plugin_candidates)

    existing_bins = [str(p) for p in bin_candidates if p.exists()]
    if existing_bins:
        _prepend_env_path("PATH", existing_bins)

    os.environ["QGIS_PREFIX_PATH"] = str(qgis_apps if qgis_apps.exists() else root)


def init_qgis(prefix_path: str, gui_enabled: bool = True):
    real_prefix = os.environ.get("QGIS_PREFIX_PATH", str(resolve_qgis_prefix(prefix_path)))

    from qgis.core import QgsApplication

    QgsApplication.setPrefixPath(real_prefix, True)
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
