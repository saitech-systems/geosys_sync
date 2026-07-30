import os
from types import SimpleNamespace

from geosys_sync.core import sync_plan
from geosys_sync.core.models import ManifestEntry
from geosys_sync.core.sync_plan import (
    LayerFacts, plan_pull, plan_push, sanitize_filename,
)

SERVER = 'https://server.test'


def entry(dsid, kind='vector', etag='e1', can_overwrite=True, name='roads'):
    return ManifestEntry(id=dsid, name=name, kind=kind, sync_etag=etag,
                         can_overwrite=can_overwrite)


def facts(layer_id='L1', name='roads', kind='vector', dsid=None, etag=None,
          server=SERVER, project=7, path=None):
    return LayerFacts(layer_id=layer_id, name=name, kind=kind,
                      source_path=path, dataset_id=dsid, sync_etag=etag,
                      server_base=server, project_id=project)


def test_sanitize_filename():
    assert sanitize_filename('Roads / Main (v2)') == 'Roads_Main_v2_'
    assert sanitize_filename('') == 'layer'


def test_plan_pull_new_dataset_targets_dest_dir():
    actions = plan_pull([entry(880)], [], dest_dir='/sync')
    assert len(actions) == 1
    a = actions[0]
    assert a.mode == 'new' and a.changed is True
    assert a.target_path == os.path.join('/sync', 'roads_880.gpkg')


def test_plan_pull_raster_gets_tif_extension():
    a = plan_pull([entry(881, kind='raster', name='dsm')], [], '/sync')[0]
    assert a.target_path.endswith('dsm_881.tif')


def test_plan_pull_existing_layer_reuses_path_and_flags_changed():
    local = facts(dsid=880, etag='OLD', path='/data/roads.gpkg')
    a = plan_pull([entry(880, etag='NEW')], [local], '/sync')[0]
    assert a.mode == 'replace'
    assert a.target_path == '/data/roads.gpkg'
    assert a.changed is True and a.local_layer_id == 'L1'


def test_plan_pull_unchanged_when_etags_match():
    local = facts(dsid=880, etag='SAME', path='/data/roads.gpkg')
    a = plan_pull([entry(880, etag='SAME')], [local], '/sync')[0]
    assert a.changed is False


CAPS = {'can_upload_vector': True, 'can_upload_raster': False}


def test_plan_push_never_synced_creates():
    a = plan_push([facts()], {}, CAPS, SERVER, 7)[0]
    assert a.mode == 'create' and a.blocked_reason is None


def test_plan_push_synced_same_target_overwrites_with_ifmatch():
    f = facts(dsid=880, etag='e0')
    a = plan_push([f], {880: entry(880)}, CAPS, SERVER, 7)[0]
    assert a.mode == 'overwrite' and a.dataset_id == 880 and a.if_match == 'e0'


def test_plan_push_different_server_or_project_creates():
    f = facts(dsid=880, etag='e0', server='https://other.test')
    a = plan_push([f], {880: entry(880)}, CAPS, SERVER, 7)[0]
    assert a.mode == 'create' and a.dataset_id is None


def test_plan_push_deleted_on_server_falls_back_to_create():
    f = facts(dsid=999, etag='e0')
    a = plan_push([f], {}, CAPS, SERVER, 7)[0]
    assert a.mode == 'create'


def test_plan_push_not_owner_blocked():
    f = facts(dsid=880, etag='e0')
    a = plan_push([f], {880: entry(880, can_overwrite=False)}, CAPS, SERVER, 7)[0]
    assert a.mode == 'overwrite' and a.blocked_reason


def test_plan_push_missing_capability_blocked():
    f = facts(kind='raster', name='dsm')
    a = plan_push([f], {}, CAPS, SERVER, 7)[0]
    assert a.blocked_reason and 'raster' in a.blocked_reason


def test_plan_push_unsupported_kind_blocked():
    f = facts(kind=None, name='pointcloud')
    a = plan_push([f], {}, CAPS, SERVER, 7)[0]
    assert a.blocked_reason


def test_uses_cog_flow_only_for_rasters_on_a_capable_server():
    caps = {'can_upload_raster': True, 'can_upload_raster_cog': True}
    assert sync_plan.uses_cog_flow('raster', caps) is True
    assert sync_plan.uses_cog_flow('vector', caps) is False


def test_uses_cog_flow_is_false_against_an_older_server():
    """No capability means server-side conversion; the plugin must fall back."""
    assert sync_plan.uses_cog_flow('raster', {'can_upload_raster': True}) is False
    assert sync_plan.uses_cog_flow('raster', {}) is False
    assert sync_plan.uses_cog_flow('raster', None) is False


def artifacts(hillshade_path='/work/hs.tif'):
    """A fake RasterArtifacts - plan_raster_upload only needs attributes,
    not the real GDAL-produced dataclass (duck-typed, per layer_props.py)."""
    return SimpleNamespace(
        cog_path='/work/dsm_COG.tif', original_path='/data/dsm.tif',
        hillshade_path=hillshade_path, band_count=1, dtype='Float32',
        cog_min=10.0, cog_max=99.5, bounds_3857=[0.0, 1.0, 2.0, 3.0])


def test_plan_raster_upload_always_ships_cog_and_original():
    files, _, _, _ = sync_plan.plan_raster_upload(
        'create', None, None, 4326, artifacts())
    roles = [role for role, _ in files]
    assert 'cog' in roles and 'original' in roles


def test_plan_raster_upload_includes_hillshade_only_when_present():
    """Guards against a multi-band raster wrongly getting a hillshade role,
    or a single-band one silently losing it."""
    with_hs, _, _, _ = sync_plan.plan_raster_upload(
        'create', None, None, 4326, artifacts(hillshade_path='/work/hs.tif'))
    without_hs, _, _, _ = sync_plan.plan_raster_upload(
        'create', None, None, 4326, artifacts(hillshade_path=None))
    assert 'hillshade_cog' in [role for role, _ in with_hs]
    assert 'hillshade_cog' not in [role for role, _ in without_hs]


def test_plan_raster_upload_create_forwards_neither_id_nor_etag():
    """A create must never look like an overwrite to the server."""
    _, _, dataset_id, if_match = sync_plan.plan_raster_upload(
        'create', 880, 'e0', 4326, artifacts())
    assert dataset_id is None and if_match is None


def test_plan_raster_upload_overwrite_forwards_both_id_and_etag():
    """An inverted ternary here would silently drop if_match or dataset_id
    on every raster overwrite, turning it into an unconditional create."""
    _, _, dataset_id, if_match = sync_plan.plan_raster_upload(
        'overwrite', 880, 'e0', 4326, artifacts())
    assert dataset_id == 880 and if_match == 'e0'


def test_plan_raster_upload_register_payload_carries_artifact_fields():
    _, payload, _, _ = sync_plan.plan_raster_upload(
        'create', None, None, 4326, artifacts())
    assert payload == {
        'epsg': 4326, 'band_count': 1, 'dtype': 'Float32',
        'cog_min': 10.0, 'cog_max': 99.5,
        'bounds_3857': [0.0, 1.0, 2.0, 3.0],
    }
