"""Read/write the geosys/* custom properties carrying sync identity.

Duck-typed: works on any object exposing customProperty/setCustomProperty/
removeCustomProperty (a real QgsMapLayer, or a fake in unit tests). QGIS
persists custom properties in the .qgz project file, so sync identity
survives project save/reopen.
"""
from datetime import datetime, timezone

_PREFIX = 'geosys/'
_KEYS = ('server_base', 'project_id', 'dataset_id', 'kind',
         'sync_etag', 'last_sync_at')


def write_sync_state(layer, server_base, project_id, dataset_id, kind, sync_etag):
    layer.setCustomProperty(_PREFIX + 'server_base', server_base)
    layer.setCustomProperty(_PREFIX + 'project_id', int(project_id))
    layer.setCustomProperty(_PREFIX + 'dataset_id', int(dataset_id))
    layer.setCustomProperty(_PREFIX + 'kind', kind)
    layer.setCustomProperty(_PREFIX + 'sync_etag', sync_etag or '')
    layer.setCustomProperty(_PREFIX + 'last_sync_at',
                            datetime.now(timezone.utc).isoformat())


def read_sync_state(layer):
    """Return the sync-state dict, or None when the layer was never synced.
    Values may come back as strings from the project file - coerce ints."""
    raw = {k: layer.customProperty(_PREFIX + k) for k in _KEYS}
    if raw.get('dataset_id') in (None, ''):
        return None
    for int_key in ('project_id', 'dataset_id'):
        try:
            raw[int_key] = int(raw[int_key])
        except (TypeError, ValueError):
            return None
    return raw


def clear_sync_state(layer):
    for k in _KEYS:
        layer.removeCustomProperty(_PREFIX + k)
