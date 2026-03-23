from pathlib import Path
from typing import Optional, Dict, List

import os
import processing

from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)

from exporter import prepare_output_dirs, write_manifest, zip_session
from gdb_reader import (
    list_raster_layers,
    list_vector_layers,
    load_raster_layer,
    load_vector_layer,
)


def ensure_raster_crs(raster_layer: QgsRasterLayer) -> QgsCoordinateReferenceSystem:
    """Возвращает CRS растра. Если CRS не определён — назначает WGS84 (EPSG:4326)."""
    crs = raster_layer.crs()
    if not crs.isValid():
        crs = QgsCoordinateReferenceSystem.fromEpsgId(4326)
        raster_layer.setCrs(crs)
    return crs


def transform_geometry(geometry: QgsGeometry, source_crs, target_crs) -> QgsGeometry:
    geom = QgsGeometry(geometry)
    if source_crs.authid() != target_crs.authid():
        ct = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
        geom.transform(ct)
    return geom


def extent_to_geometry(extent) -> QgsGeometry:
    ring = [
        QgsPointXY(extent.xMinimum(), extent.yMinimum()),
        QgsPointXY(extent.xMinimum(), extent.yMaximum()),
        QgsPointXY(extent.xMaximum(), extent.yMaximum()),
        QgsPointXY(extent.xMaximum(), extent.yMinimum()),
        QgsPointXY(extent.xMinimum(), extent.yMinimum()),
    ]
    return QgsGeometry.fromPolygonXY([ring])


def create_memory_layer(
    geometry_type: str,
    crs,
    layer_name: str,
    fields: Optional[QgsFields] = None
) -> QgsVectorLayer:
    uri = "%s?crs=%s" % (geometry_type, crs.authid())
    layer = QgsVectorLayer(uri, layer_name, "memory")
    pr = layer.dataProvider()
    if fields and len(fields) > 0:
        pr.addAttributes(list(fields))
        layer.updateFields()
    return layer


def create_single_feature_layer(
    geometry: QgsGeometry,
    crs,
    layer_name: str,
    attrs: Optional[Dict] = None
) -> QgsVectorLayer:
    fields = QgsFields()
    if attrs:
        for key in attrs.keys():
            fields.append(QgsField(key, QVariant.String))

    geom_type = QgsWkbTypes.displayString(geometry.wkbType())
    layer = create_memory_layer(geom_type, crs, layer_name, fields)

    feat = QgsFeature(layer.fields())
    feat.setGeometry(geometry)
    if attrs:
        for key, value in attrs.items():
            feat[key] = "" if value is None else str(value)

    layer.dataProvider().addFeature(feat)
    layer.updateExtents()
    return layer


def write_geojson(layer: QgsVectorLayer, output_path: str) -> None:
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GeoJSON"
    options.fileEncoding = "UTF-8"

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        output_path,
        QgsProject.instance().transformContext(),
        options
    )
    if result[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError("Ошибка записи GeoJSON: %s" % output_path)


def get_processing_crs(route_layer: QgsVectorLayer, processing_crs_epsg: Optional[int]):
    if processing_crs_epsg:
        crs = QgsCoordinateReferenceSystem.fromEpsgId(processing_crs_epsg)
    else:
        crs = route_layer.crs()

    if not crs.isValid():
        raise RuntimeError("Некорректный processing CRS")

    if crs.isGeographic():
        raise RuntimeError(
            "Для буфера в метрах нужен projected CRS. "
            "Укажи processing_crs_epsg в config.json."
        )

    return crs


def reproject_vector_if_needed(layer: QgsVectorLayer, target_crs):
    if layer.crs().authid() == target_crs.authid():
        return layer

    result = processing.run(
        "native:reprojectlayer",
        {
            "INPUT": layer,
            "TARGET_CRS": target_crs,
            "OUTPUT": "memory:"
        }
    )
    return result["OUTPUT"]


def count_intersections(vector_layer: QgsVectorLayer, buffer_geom: QgsGeometry) -> int:
    request = QgsFeatureRequest().setFilterRect(buffer_geom.boundingBox())
    count = 0

    for feature in vector_layer.getFeatures(request):
        geom = feature.geometry()
        if geom and not geom.isEmpty() and geom.intersects(buffer_geom):
            count += 1

    return count


def raster_intersects_buffer(raster_layer: QgsRasterLayer, buffer_geom: QgsGeometry, processing_crs) -> bool:
    raster_crs = ensure_raster_crs(raster_layer)
    raster_geom = extent_to_geometry(raster_layer.extent())
    raster_geom_proc = transform_geometry(raster_geom, raster_crs, processing_crs)
    print("[DEBUG] raster CRS:", raster_crs.authid())
    print("[DEBUG] raster extent (orig):", raster_layer.extent().toString())
    print("[DEBUG] raster extent (proc):", raster_geom_proc.boundingBox().toString())
    print("[DEBUG] buffer bbox:", buffer_geom.boundingBox().toString())
    print("[DEBUG] intersects:", raster_geom_proc.intersects(buffer_geom))
    return raster_geom_proc.intersects(buffer_geom)


def clip_vector_layer(vector_layer: QgsVectorLayer, buffer_layer: QgsVectorLayer):
    result = processing.run(
        "native:clip",
        {
            "INPUT": vector_layer,
            "OVERLAY": buffer_layer,
            "OUTPUT": "memory:"
        }
    )
    return result["OUTPUT"]


def clip_raster_layer(
    raster_layer: QgsRasterLayer,
    buffer_layer: QgsVectorLayer,
    output_path: str,
    processing_crs
):
    raster_crs = ensure_raster_crs(raster_layer)
    params = {
        "INPUT": raster_layer.source(),
        "MASK": buffer_layer,
        "SOURCE_CRS": raster_crs,
        "TARGET_CRS": processing_crs,
        "NODATA": None,
        "ALPHA_BAND": False,
        "CROP_TO_CUTLINE": True,
        "KEEP_RESOLUTION": True,
        "SET_RESOLUTION": False,
        "X_RESOLUTION": None,
        "Y_RESOLUTION": None,
        "MULTITHREADING": False,
        "OPTIONS": "",
        "DATA_TYPE": 0,
        "EXTRA": "",
        "OUTPUT": output_path
    }
    processing.run("gdal:cliprasterbymasklayer", params)


def process_gdb(
    gdb_path: str,
    output_root: str,
    route_layer_name: str,
    route_name_field: str,
    route_fid: int,
    buffer_m: float,
    processing_crs_epsg: Optional[int] = None,
    input_raster_folder: Optional[str] = None
) -> Dict:
    route_layer = load_vector_layer(gdb_path, route_layer_name)
    processing_crs = get_processing_crs(route_layer, processing_crs_epsg)

    route_feature = route_layer.getFeature(route_fid)
    if not route_feature.isValid():
        raise RuntimeError("Маршрут FID=%d не найден" % route_fid)

    route_name = route_feature[route_name_field]
    route_name = "" if route_name is None else str(route_name)

    route_geom_proc = transform_geometry(route_feature.geometry(), route_layer.crs(), processing_crs)
    buffer_geom = route_geom_proc.buffer(buffer_m, 16)

    route_mem = create_single_feature_layer(
        route_geom_proc,
        processing_crs,
        "selected_route",
        {"name": route_name}
    )
    buffer_mem = create_single_feature_layer(
        buffer_geom,
        processing_crs,
        "route_buffer",
        {"name": route_name}
    )

    dirs = prepare_output_dirs(output_root, route_name)

    write_geojson(route_mem, str(dirs["report_dir"] / "selected_route.geojson"))
    write_geojson(buffer_mem, str(dirs["report_dir"] / "buffer.geojson"))

    vector_layer_names = list_vector_layers(gdb_path)
    raster_layers_in_gdb = list_raster_layers(gdb_path)

    manifest = {
        "gdb_path": gdb_path,
        "route_name": route_name,
        "route_fid": route_fid,
        "buffer_m": buffer_m,
        "processing_crs": processing_crs.authid(),
        "layers": [],
        "summary": {}
    }

    # -------- ВЕКТОРНЫЕ СЛОИ ИЗ GDB --------
    for layer_name in vector_layer_names:
        if layer_name == route_layer_name:
            continue

        item = {
            "layer": layer_name,
            "type": "vector",
            "status": "unknown",
            "input_count": 0,
            "output_count": 0,
            "output_path": ""
        }

        try:
            src_layer = load_vector_layer(gdb_path, layer_name)
            item["input_count"] = int(src_layer.featureCount())

            work_layer = reproject_vector_if_needed(src_layer, processing_crs)
            intersect_count = count_intersections(work_layer, buffer_geom)

            if intersect_count == 0:
                item["status"] = "no_overlap"
            else:
                clipped = clip_vector_layer(work_layer, buffer_mem)
                out_count = int(clipped.featureCount())
                item["output_count"] = out_count

                if out_count == 0:
                    item["status"] = "no_overlap"
                else:
                    out_path = dirs["vector_dir"] / ("%s.geojson" % layer_name)
                    write_geojson(clipped, str(out_path))
                    item["status"] = "clipped"
                    item["output_path"] = str(out_path)

        except Exception as ex:
            item["status"] = "error: %s" % ex

        manifest["layers"].append(item)

    # -------- РАСТРЫ ИЗ GDB (если есть) --------
    for raster_info in raster_layers_in_gdb:
        layer_name = raster_info["name"]
        item = {
            "layer": layer_name,
            "type": "raster",
            "status": "unknown",
            "input_count": 1,
            "output_count": 0,
            "output_path": ""
        }

        try:
            raster_layer = load_raster_layer(raster_info["source"], layer_name)

            if not raster_intersects_buffer(raster_layer, buffer_geom, processing_crs):
                item["status"] = "no_overlap"
            else:
                out_path = dirs["raster_dir"] / ("%s.tif" % layer_name)
                clip_raster_layer(raster_layer, buffer_mem, str(out_path), processing_crs)

                if out_path.exists() and out_path.stat().st_size > 0:
                    item["status"] = "clipped"
                    item["output_count"] = 1
                    item["output_path"] = str(out_path)
                else:
                    item["status"] = "no_overlap"

        except Exception as ex:
            item["status"] = "error: %s" % ex

        manifest["layers"].append(item)

    # -------- ВНЕШНИЕ TIFF ИЗ ПАПКИ --------
    if input_raster_folder:
        raster_dir_in = Path(input_raster_folder)
        if raster_dir_in.exists():
            for fname in os.listdir(str(raster_dir_in)):
                if not fname.lower().endswith((".tif", ".tiff")):
                    continue

                src_path = raster_dir_in / fname
                layer_name = src_path.stem

                item = {
                    "layer": layer_name,
                    "type": "raster",
                    "status": "unknown",
                    "input_count": 1,
                    "output_count": 0,
                    "output_path": ""
                }

                try:
                    raster_layer = QgsRasterLayer(str(src_path), layer_name)
                    print("[DEBUG] loading:", fname)
                    print("[DEBUG] isValid:", raster_layer.isValid())
                    print("[DEBUG] CRS:", raster_layer.crs().authid())
                    print("[DEBUG] extent:", raster_layer.extent().toString())
                    if not raster_layer.isValid():
                        item["status"] = "error: invalid_raster"
                    elif not raster_intersects_buffer(raster_layer, buffer_geom, processing_crs):
                        item["status"] = "no_overlap"
                    else:
                        out_path = dirs["raster_dir"] / ("%s.tif" % layer_name)
                        clip_raster_layer(raster_layer, buffer_mem, str(out_path), processing_crs)

                        if out_path.exists() and out_path.stat().st_size > 0:
                            item["status"] = "clipped"
                            item["output_count"] = 1
                            item["output_path"] = str(out_path)
                        else:
                            item["status"] = "no_overlap"
                except Exception as ex:
                    item["status"] = "error: %s" % ex

                manifest["layers"].append(item)

    # -------- СВОДКА --------
    vector_items = [x for x in manifest["layers"] if x["type"] == "vector"]
    raster_items = [x for x in manifest["layers"] if x["type"] == "raster"]

    manifest["summary"] = {
        "vector_total": len(vector_items),
        "vector_clipped": len([x for x in vector_items if x["status"] == "clipped"]),
        "vector_no_overlap": len([x for x in vector_items if x["status"] == "no_overlap"]),
        "vector_errors": len([x for x in vector_items if str(x["status"]).startswith("error")]),
        "raster_total": len(raster_items),
        "raster_clipped": len([x for x in raster_items if x["status"] == "clipped"]),
        "raster_no_overlap": len([x for x in raster_items if x["status"] == "no_overlap"]),
        "raster_errors": len([x for x in raster_items if str(x["status"]).startswith("error")]),
    }

    manifest_json, manifest_txt = write_manifest(manifest, dirs["report_dir"])
    zip_path = zip_session(dirs["session_dir"])

    return {
        "manifest": manifest,
        "manifest_json": manifest_json,
        "manifest_txt": manifest_txt,
        "zip_path": zip_path,
        "session_dir": str(dirs["session_dir"])
    }
