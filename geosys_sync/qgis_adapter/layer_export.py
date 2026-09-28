"""Local-layer inspection and GeoPackage export (push direction)."""
import os

from qgis.core import QgsMapLayer, QgsProject, QgsVectorFileWriter

from geosys_sync.core.i18n import tr
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


def crs_epsg(crs):
    """The CRS's EPSG code as an int, or None when it has no EPSG authority.

    postgisSrid() is not enough: QGIS returns a non-zero srs.db srid for
    non-EPSG authorities too (ESRI:102008 -> 102008, OGC:CRS84 -> 520003159),
    and putting one of those on the wire as an EPSG code is silently wrong.
    """
    authid = crs.authid() or ''
    if not authid.upper().startswith('EPSG:'):
        return None
    try:
        return int(authid.split(':', 1)[1])
    except ValueError:
        return None


def layer_epsg(layer):
    return crs_epsg(layer.crs())


def crs_problem(crs):
    """None if this CRS can be put on the wire, else a short human reason.

    Definitionally "layer_epsg would return None", so the check and the value
    the push depends on can never disagree. The two failures are told apart
    because only one of them is the user forgetting something: a layer can
    carry a perfectly valid custom, raw-WKT or non-EPSG-authority projection
    and still be unpushable, because the wire contract carries an integer
    EPSG and cannot express one without an EPSG authority code.
    """
    if not crs.isValid():
        return tr('layer has no coordinate reference system')
    if crs_epsg(crs) is None:
        return tr('layer CRS has no EPSG code ({})').format(
            crs.description() or tr('custom'))
    return None


def layer_crs_problem(layer):
    """None if the layer's CRS can be pushed, else a short human reason."""
    return crs_problem(layer.crs())


def export_vector_gpkg(layer, dest_path):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'GPKG'
    options.layerName = os.path.splitext(os.path.basename(dest_path))[0]
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, dest_path, QgsProject.instance().transformContext(), options)
    if result[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError(tr('GeoPackage export failed: {}').format(result[1]))
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
