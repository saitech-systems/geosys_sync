"""Add or refresh pulled layers in the QGIS project. MAIN THREAD ONLY."""
from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer

from geosys_sync.core.i18n import tr
from geosys_sync.qgis_adapter import layer_props, style_apply


def find_layer_by_dataset(dataset_id, project=None):
    project = project or QgsProject.instance()
    for layer in project.mapLayers().values():
        state = layer_props.read_sync_state(layer)
        if state and state.get('dataset_id') == dataset_id:
            return layer
    return None


def load_pulled_dataset(entry, path, server_base, project_id):
    """entry: core.models.ManifestEntry; path: the freshly downloaded file."""
    existing = find_layer_by_dataset(entry.id)
    provider = 'ogr' if entry.kind == 'vector' else 'gdal'
    if existing is not None:
        existing.setDataSource(path, entry.name, provider)
        if not existing.isValid():
            raise RuntimeError(tr('Could not open {}').format(path))
        layer = existing
    else:
        if entry.kind == 'vector':
            layer = QgsVectorLayer(path, entry.name, provider)
        else:
            layer = QgsRasterLayer(path, entry.name, provider)
        if not layer.isValid():
            raise RuntimeError(tr('Could not open {}').format(path))
        QgsProject.instance().addMapLayer(layer)
    style_apply.apply_wire(layer, entry.style)
    layer_props.write_sync_state(layer, server_base=server_base,
                                 project_id=project_id, dataset_id=entry.id,
                                 kind=entry.kind, sync_etag=entry.sync_etag)
    return layer
