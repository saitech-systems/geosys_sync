"""Pure planning logic for pull/push. Decides create-vs-overwrite, target
paths, and changed flags. No QGIS, no network."""
import os
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LayerFacts:
    """What the QGIS adapter reports about one local layer."""
    layer_id: str
    name: str
    kind: Optional[str]              # 'vector' | 'raster' | None (unsyncable)
    source_path: Optional[str]
    dataset_id: Optional[int] = None
    sync_etag: Optional[str] = None
    server_base: Optional[str] = None
    project_id: Optional[int] = None


@dataclass
class PullAction:
    entry: object                    # core.models.ManifestEntry
    mode: str                        # 'new' | 'replace'
    target_path: str
    changed: bool
    local_layer_id: Optional[str] = None


@dataclass
class PushAction:
    layer_id: str
    name: str
    kind: str
    mode: str                        # 'create' | 'overwrite'
    dataset_id: Optional[int] = None
    if_match: Optional[str] = None
    blocked_reason: Optional[str] = None


def sanitize_filename(name):
    cleaned = re.sub(r'[^A-Za-z0-9_.-]+', '_', (name or '').strip())
    return (cleaned or 'layer')[:80]


def plan_pull(entries, local_facts, dest_dir):
    by_dsid = {f.dataset_id: f for f in local_facts if f.dataset_id}
    actions = []
    for e in entries:
        local = by_dsid.get(e.id)
        ext = '.gpkg' if e.kind == 'vector' else '.tif'
        default_path = os.path.join(
            dest_dir, '{}_{}{}'.format(sanitize_filename(e.name), e.id, ext))
        if local is not None:
            actions.append(PullAction(
                entry=e, mode='replace',
                target_path=local.source_path or default_path,
                changed=local.sync_etag != e.sync_etag,
                local_layer_id=local.layer_id))
        else:
            actions.append(PullAction(entry=e, mode='new',
                                      target_path=default_path, changed=True))
    return actions


def plan_push(local_facts, manifest_by_id, capabilities, server_base, project_id):
    actions = []
    for f in local_facts:
        if f.kind not in ('vector', 'raster'):
            actions.append(PushAction(
                f.layer_id, f.name, f.kind or 'unsupported', 'create',
                blocked_reason='Only vector and raster layers can sync'))
            continue
        if not capabilities.get('can_upload_{}'.format(f.kind)):
            actions.append(PushAction(
                f.layer_id, f.name, f.kind, 'create',
                blocked_reason='Your account has no {} upload permission'.format(f.kind)))
            continue
        same_target = bool(f.dataset_id) and f.server_base == server_base \
            and f.project_id == project_id
        server_entry = manifest_by_id.get(f.dataset_id) if same_target else None
        if server_entry is None:
            # never synced here, synced elsewhere, or deleted server-side
            actions.append(PushAction(f.layer_id, f.name, f.kind, 'create'))
        elif not server_entry.can_overwrite:
            actions.append(PushAction(
                f.layer_id, f.name, f.kind, 'overwrite', dataset_id=f.dataset_id,
                blocked_reason='You do not own this dataset on the server'))
        else:
            actions.append(PushAction(
                f.layer_id, f.name, f.kind, 'overwrite',
                dataset_id=f.dataset_id, if_match=f.sync_etag))
    return actions


def uses_cog_flow(kind, capabilities):
    """True when a raster push should convert locally and upload to storage.

    Older servers do not advertise the capability; the plugin then keeps
    posting the raw GeoTIFF for server-side conversion.
    """
    return kind == 'raster' and bool(
        (capabilities or {}).get('can_upload_raster_cog'))


def plan_raster_upload(mode, dataset_id, if_match, epsg, artifacts):
    """Decide what a converted raster push uploads and registers.

    Duck-typed on `artifacts`: works on any object exposing cog_path,
    original_path, hillshade_path, band_count, dtype, cog_min, cog_max and
    bounds_3857 (a real qgis_adapter.cog_export.RasterArtifacts, or a fake
    in unit tests) - the same pattern layer_props.py uses for QgsMapLayer.

    Returns (files, register_payload, dataset_id, if_match):
    - `files` is [(role, path)], always 'cog' and 'original', plus
      'hillshade_cog' only when the artifacts carry a hillshade.
    - `register_payload` is the dict the server's register endpoint needs.
    - `dataset_id`/`if_match` are forwarded only for an overwrite; a create
      gets None for both, so the server never mistakes it for a replace.
    """
    files = [('cog', artifacts.cog_path),
             ('original', artifacts.original_path)]
    if artifacts.hillshade_path:
        files.append(('hillshade_cog', artifacts.hillshade_path))
    register_payload = {
        'epsg': epsg,
        'band_count': artifacts.band_count,
        'dtype': artifacts.dtype,
        'cog_min': artifacts.cog_min,
        'cog_max': artifacts.cog_max,
        'bounds_3857': artifacts.bounds_3857,
    }
    overwrite = mode == 'overwrite'
    return (files, register_payload,
            dataset_id if overwrite else None,
            if_match if overwrite else None)
