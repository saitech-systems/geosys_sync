import os

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
