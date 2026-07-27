import json

import pytest

from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import SyncConflictError
from geosys_sync.core.models import TokenBundle

BASE = 'https://server.test'
API = BASE + '/api/qgis/v1'

ENTRY = {'id': 902, 'name': 'roads', 'kind': 'vector', 'sync_etag': 'new1',
         'can_overwrite': True, 'style': {},
         'download': {'endpoint': '/api/qgis/v1/datasets/902/download'}}


def make_client():
    return GeosysClient(BASE, device_id='qgis-testdevice1',
                        tokens=TokenBundle('acc', 'ref'))


@pytest.fixture
def gpkg(tmp_path):
    p = tmp_path / 'roads.gpkg'
    p.write_bytes(b'GPKG')
    return str(p)


def test_create_vector_dataset(requests_mock, gpkg):
    requests_mock.post(API + '/projects/7/datasets', status_code=201, json=ENTRY)
    entry = make_client().create_dataset(7, 'roads', 'vector', gpkg,
                                         style={'symbology_type': 'single'})
    assert entry.id == 902
    body = requests_mock.last_request.text
    assert 'name="kind"' in body and 'vector' in body
    assert 'name="style"' in body


def test_create_raster_dataset_202(requests_mock, gpkg):
    raster_entry = dict(ENTRY, kind='raster', cog_status='processing')
    requests_mock.post(API + '/projects/7/datasets', status_code=202,
                       json=raster_entry)
    entry = make_client().create_dataset(7, 'dsm', 'raster', gpkg, epsg=25832)
    assert entry.cog_status == 'processing'
    assert 'name="epsg"' in requests_mock.last_request.text


def test_create_raster_sends_crs_confirmation_when_confirmed(requests_mock, gpkg):
    """The project's first raster is refused (409 PROJECT_CRS_UNCONFIRMED)
    unless this field rides along with the multipart body."""
    requests_mock.post(API + '/projects/7/datasets', status_code=202,
                       json=dict(ENTRY, kind='raster'))
    make_client().create_dataset(7, 'dsm', 'raster', gpkg, epsg=25832,
                                 crs_confirmed=True)
    body = requests_mock.last_request.text
    assert 'name="project_crs_confirmed"' in body
    assert 'true' in body.split('name="project_crs_confirmed"', 1)[1]


def test_create_dataset_omits_crs_confirmation_by_default(requests_mock, gpkg):
    """No acknowledgement is invented on the user's behalf."""
    requests_mock.post(API + '/projects/7/datasets', status_code=202,
                       json=dict(ENTRY, kind='raster'))
    make_client().create_dataset(7, 'dsm', 'raster', gpkg, epsg=25832)
    assert 'name="project_crs_confirmed"' not in requests_mock.last_request.text


def test_create_vector_dataset_never_sends_crs_confirmation(requests_mock, gpkg):
    requests_mock.post(API + '/projects/7/datasets', status_code=201, json=ENTRY)
    make_client().create_dataset(7, 'roads', 'vector', gpkg)
    assert 'name="project_crs_confirmed"' not in requests_mock.last_request.text


def test_overwrite_sends_if_match(requests_mock, gpkg):
    requests_mock.put(API + '/datasets/902/data', json=ENTRY)
    make_client().overwrite_dataset(902, gpkg, 'vector', if_match='old1')
    assert requests_mock.last_request.headers['If-Match'] == 'old1'


def test_overwrite_conflict_raises(requests_mock, gpkg):
    requests_mock.put(API + '/datasets/902/data', status_code=409, json={
        'error': {'code': 'SYNC_CONFLICT', 'message': 'stale',
                  'detail': {'current_etag': 'srv9'}}})
    with pytest.raises(SyncConflictError) as exc:
        make_client().overwrite_dataset(902, gpkg, 'vector', if_match='old1')
    assert exc.value.current_etag == 'srv9'


def test_upload_retries_once_after_token_expiry(requests_mock, gpkg):
    requests_mock.put(API + '/datasets/902/data', [
        {'status_code': 401,
         'json': {'error': {'code': 'TOKEN_EXPIRED', 'message': 'x'}}},
        {'json': ENTRY},
    ])
    requests_mock.post(API + '/auth/refresh', json={
        'access_token': 'acc2', 'refresh_token': 'ref2',
        'expires_in': 900, 'token_type': 'Bearer'})
    entry = make_client().overwrite_dataset(902, gpkg, 'vector')
    assert entry.id == 902
    # both attempts carried the full file body
    puts = [r for r in requests_mock.request_history if r.method == 'PUT']
    assert len(puts) == 2 and 'GPKG' in puts[1].text


def test_update_style(requests_mock):
    requests_mock.put(API + '/datasets/902/style', json=ENTRY)
    entry = make_client().update_style(902, {'style_color': '#ff0000'})
    assert entry.id == 902
    assert requests_mock.last_request.json() == {'style_color': '#ff0000'}
