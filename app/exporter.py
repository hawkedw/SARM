from pathlib import Path
from typing import Dict, Tuple
import json
import re
import zipfile


def slugify(value: str) -> str:
    value = value or "route"
    value = value.strip()
    value = re.sub(r"[^\w\-]+", "_", value, flags=re.UNICODE)
    value = re.sub(r"_+", "_", value)
    return value.strip("_") or "route"


def prepare_output_dirs(output_root: str, route_name: str) -> Dict[str, Path]:
    root = Path(output_root)
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = root / f"{stamp}_{slugify(route_name)}"

    vector_dir = session_dir / "vector"
    raster_dir = session_dir / "raster"
    report_dir = session_dir / "report"

    for p in (session_dir, vector_dir, raster_dir, report_dir):
        p.mkdir(parents=True, exist_ok=True)

    return {
        "session_dir": session_dir,
        "vector_dir": vector_dir,
        "raster_dir": raster_dir,
        "report_dir": report_dir
    }


def write_manifest(manifest: Dict, report_dir: Path) -> Tuple[str, str]:
    json_path = report_dir / "manifest.json"
    txt_path = report_dir / "report.txt"

    json_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    lines = []
    lines.append(f"GDB: {manifest['gdb_path']}")
    lines.append(f"Маршрут: {manifest['route_name']}")
    lines.append(f"FID маршрута: {manifest['route_fid']}")
    lines.append(f"Буфер, м: {manifest['buffer_m']}")
    lines.append("")
    lines.append("Сводка:")
    for key, value in manifest["summary"].items():
        lines.append(f"  - {key}: {value}")
    lines.append("")
    lines.append("Слои:")

    for item in manifest["layers"]:
        lines.append(
            f"  - {item['layer']} | {item['type']} | {item['status']} | "
            f"in={item.get('input_count', '-')} | out={item.get('output_count', '-')} | "
            f"path={item.get('output_path', '')}"
        )

    txt_path.write_text("\n".join(lines), encoding="utf-8")
    return str(json_path), str(txt_path)


def zip_session(session_dir: Path) -> str:
    zip_path = session_dir.with_suffix(".zip")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in session_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(session_dir))

    return str(zip_path)
