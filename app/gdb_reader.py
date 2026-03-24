from pathlib import Path
from typing import List

from osgeo import ogr, gdal

from qgis.core import (
    QgsFeatureRequest,
    QgsRasterLayer,
    QgsVectorLayer,
)


def list_vector_layers(gdb_path: str) -> List[str]:
    drv = ogr.GetDriverByName("OpenFileGDB")
    ds = drv.Open(gdb_path, 0) if drv else ogr.Open(gdb_path)
    if ds is None:
        raise RuntimeError("Не удалось открыть GDB: %s" % gdb_path)
    names = []
    for i in range(ds.GetLayerCount()):
        layer = ds.GetLayerByIndex(i)
        if layer is not None:
            names.append(layer.GetName())
    return names


def list_raster_layers(gdb_path: str) -> List[dict]:
    try:
        gdal.UseExceptions()
        drv = gdal.GetDriverByName("OpenFileGDB")
        if drv:
            ds = gdal.OpenEx(gdb_path, gdal.OF_RASTER, allowed_drivers=["OpenFileGDB"])
        else:
            ds = gdal.OpenEx(gdb_path, gdal.OF_RASTER)
        if ds is None:
            return []
        subdatasets = ds.GetSubDatasets()
        result = []
        for source, _desc in subdatasets:
            layer_name = source.rsplit(":", 1)[-1].strip('"')
            result.append({"name": layer_name, "source": source})
        return result
    except Exception:
        return []


def load_vector_layer(gdb_path: str, layer_name: str) -> QgsVectorLayer:
    source = "%s|layername=%s" % (gdb_path, layer_name)
    layer = QgsVectorLayer(source, layer_name, "ogr")
    if not layer.isValid():
        raise RuntimeError("Не удалось загрузить векторный слой: %s" % layer_name)
    return layer


def load_raster_layer(source: str, layer_name: str) -> QgsRasterLayer:
    layer = QgsRasterLayer(source, layer_name, "gdal")
    if not layer.isValid():
        raise RuntimeError("Не удалось загрузить растровый слой: %s" % layer_name)
    return layer


def get_route_choices(route_layer, name_field: str) -> List[dict]:
    field_idx = route_layer.fields().indexFromName(name_field)
    if field_idx == -1:
        raise RuntimeError("В слое %s нет поля %s" % (route_layer.name(), name_field))
    request = QgsFeatureRequest().setSubsetOfAttributes([field_idx])
    rows = []
    for feature in route_layer.getFeatures(request):
        name_value = feature[name_field]
        name_text = str(name_value).strip() if name_value is not None else ""
        label = name_text if name_text else "FID=%d" % feature.id()
        rows.append({
            "fid": feature.id(),
            "name": name_text,
            "label": "%s [FID=%d]" % (label, feature.id())
        })
    rows.sort(key=lambda x: (x["name"].lower(), x["fid"]))
    return rows
