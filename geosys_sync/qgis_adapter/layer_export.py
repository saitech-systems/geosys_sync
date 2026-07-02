"""Local-layer inspection and GeoPackage export (push direction)."""
import os

from qgis.core import QgsMapLayer, QgsProject, QgsVectorFileWriter

from geosys_sync.core.sync_plan import LayerFacts
from geosys_sync.qgis_adapter import layer_props

_RASTER_EXTS = ('.tif', '.tiff', '.geotiff')


def layer_kind(layer):
    if layer.type() == QgsMapLayer.VectorLayer:
        return 'vector'
    if layer.type() == QgsMapLayer.RasterLayer and raster_source_path(layer):
        return 'raster'
    return None


def raster_source_path(layer):
    """Local GeoTIFF path behind a raster layer, or None (WMS/XYZ/VRT etc.)."""
    if layer.type() != QgsMapLayer.RasterLayer or layer.providerType() != 'gdal':
        return None
    path = layer.source().split('|')[0]
    if os.path.isfile(path) and path.lower().endswith(_RASTER_EXTS):
        return path
    return None


def layer_epsg(layer):
    srid = layer.crs().postgisSrid()
    return srid or None


def export_vector_gpkg(layer, dest_path):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'GPKG'
    options.layerName = os.path.splitext(os.path.basename(dest_path))[0]
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, dest_path, QgsProject.instance().transformContext(), options)
    if result[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError('GeoPackage export failed: {}'.format(result[1]))
    return dest_path


def collect_layer_facts(layer):
    state = layer_props.read_sync_state(layer) or {}
    kind = layer_kind(layer)
    if kind == 'raster':
        source = raster_source_path(layer)
    elif kind == 'vector':
        source = layer.source().split('|')[0]
    else:
        source = None
    return LayerFacts(layer_id=layer.id(), name=layer.name(), kind=kind,
                      source_path=source,
                      dataset_id=state.get('dataset_id'),
                      sync_etag=state.get('sync_etag'),
                      server_base=state.get('server_base'),
                      project_id=state.get('project_id'))
