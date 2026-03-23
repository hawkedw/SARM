from typing import List

from osgeo import ogr, gdal

from qgis.core import (
    QgsFeatureRequest,
    QgsRasterLayer,
    QgsVectorLayer,
)


def list_vector_layers(gdb_path: str) -> List[str]:
    ds = ogr.Open(gdb_path)
    if ds is None:
        raise RuntimeError(f"Не удалось открыть GDB: {gdb_path}")

    names = []
    for i in range(ds.GetLayerCount()):
        layer = ds.GetLayerByIndex(i)
        if layer is not None:
            names.append(layer.GetName())

    return names


def list_raster_layers(gdb_path: str) -> List[dict]:
    ds = gdal.OpenEx(gdb_path, gdal.OF_RASTER)
    if ds is None:
        return []

    subdatasets = ds.GetSubDatasets()
    result = []

    for source, _desc in subdatasets:
        layer_name = source.rsplit(":", 1)[-1].strip('"')
        result.append({
            "name": layer_name,
            "source": source
        })

    return result


def load_vector_layer(gdb_path: str, layer_name: str) -> QgsVectorLayer:
    source = f"{gdb_path}|layername={layer_name}"
    layer = QgsVectorLayer(source, layer_name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Не удалось загрузить векторный слой: {layer_name}")
    return layer


def load_raster_layer(source: str, layer_name: str) -> QgsRasterLayer:
    layer = QgsRasterLayer(source, layer_name, "gdal")
    if not layer.isValid():
        raise RuntimeError(f"Не удалось загрузить растровый слой: {layer_name}")
    return layer


def get_route_choices(route_layer, name_field: str) -> List[dict]:
    field_idx = route_layer.fields().indexFromName(name_field)
    if field_idx == -1:
        raise RuntimeError(f"В слое {route_layer.name()} нет поля {name_field}")

    request = QgsFeatureRequest().setSubsetOfAttributes([field_idx])
    rows = []

    for feature in route_layer.getFeatures(request):
        name_value = feature[name_field]
        name_text = str(name_value).strip() if name_value is not None else ""
        label = name_text if name_text else f"FID={feature.id()}"
        rows.append({
            "fid": feature.id(),
            "name": name_text,
            "label": f"{label} [FID={feature.id()}]"
        })

    rows.sort(key=lambda x: (x["name"].lower(), x["fid"]))
    return rows
