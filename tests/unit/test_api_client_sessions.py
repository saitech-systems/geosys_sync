import pytest

from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import ApiError, SyncConflictError
from geosys_sync.core.models import TokenBundle

BASE = 'https://server.test'
API = BASE + '/api/qgis/v1'

ENTRY = {'id': 881, 'name': 'dsm', 'kind': 'raster', 'sync_etag': 'e2',
         'can_overwrite': True, 'style': {}, 'cog_status': 'ready',
         'band_count': 1,
         'download': {'endpoint': '/api/qgis/v1/datasets/881/cog-url'}}

FILES = [{'role': 'cog', 'filename': 'd_COG.tif', 'size': 10, 'sha256': 'a' * 64}]


def make_client():
    return GeosysClient(BASE, device_id='qgis-testdevice1',
                        tokens=TokenBundle('acc', 'ref'))


@pytest.fixture
def blob(tmp_path):
    p = tmp_path / 'part.bin'
    p.write_bytes(b'0123456789ABCDEF')
    return str(p)


def test_initiate_sends_a_create_session(requests_mock):
    requests_mock.post(API + '/uploads/initiate', status_code=201, json={
        'upload_session_id': 's1', 'part_size_bytes': 1024,
        'files': [{'role': 'cog', 'file_index': 0}]})
    out = make_client().initiate_upload(7, 'dsm', FILES, crs_confirmed=True)
    assert out['upload_session_id'] == 's1'
    body = requests_mock.last_request.json()
    assert body['kind'] == 'raster'
    assert body['project_crs_confirmed'] is True
    assert body['files'] == FILES
    assert 'dataset_id' not in body
    assert 'If-Match' not in requests_mock.last_request.headers


def test_initiate_sends_the_overwrite_target_and_etag(requests_mock):
    requests_mock.post(API + '/uploads/initiate', status_code=201, json={
        'upload_session_id': 's1', 'part_size_bytes': 1024, 'files': []})
    make_client().initiate_upload(7, 'dsm', FILES, dataset_id=881,
                                  if_match='abc123',
                                  style={'raster_opacity': 0.5})
    body = requests_mock.last_request.json()
    assert body['dataset_id'] == 881
    assert body['style'] == {'raster_opacity': 0.5}
    assert requests_mock.last_request.headers['If-Match'] == 'abc123'


def test_every_request_carries_the_plugin_version(requests_mock):
    requests_mock.get(API + '/auth/me', json={'user': {}, 'org': {},
                                              'capabilities': {}})
    make_client().me()
    assert requests_mock.last_request.headers['X-QGIS-Plugin-Version']


def test_presign_parts_returns_the_urls(requests_mock):
    requests_mock.post(API + '/uploads/s1/presign-parts', json={'parts': [
        {'part_number': 1, 'url': 'https://s3.test/1', 'expires_in': 3600}]})
    parts = make_client().presign_parts('s1', 'cog', 0, [1])
    assert parts[0]['url'] == 'https://s3.test/1'
    assert requests_mock.last_request.json() == {
        'role': 'cog', 'file_index': 0, 'part_numbers': [1]}


def test_presign_parts_waits_out_a_rate_limit(requests_mock, monkeypatch):
    slept = []
    monkeypatch.setattr('geosys_sync.core.api_client.time.sleep', slept.append)
    requests_mock.post(API + '/uploads/s1/presign-parts', [
        {'status_code': 429, 'json': {'error': {
            'code': 'RATE_LIMITED', 'message': 'slow down',
            'detail': {'retry_after': 3}}}},
        {'status_code': 200, 'json': {'parts': [
            {'part_number': 1, 'url': 'https://s3.test/1', 'expires_in': 60}]}},
    ])
    parts = make_client().presign_parts('s1', 'cog', 0, [1])
    assert parts[0]['part_number'] == 1
    assert slept == [3]


def test_put_part_sets_content_length_and_no_authorization(requests_mock, blob):
    requests_mock.put('https://s3.test/1', headers={'ETag': '"abc"'})
    etag = make_client().put_part('https://s3.test/1', blob, 4, 6)
    assert etag == '"abc"'
    req = requests_mock.last_request
    # A bearer token must never reach a server-supplied storage host, and the
    # body must be length-delimited: S3 rejects chunked transfer encoding.
    assert 'Authorization' not in req.headers
    assert req.headers['Content-Length'] == '6'


def test_put_part_refuses_a_non_https_url(blob):
    with pytest.raises(ApiError) as e:
        make_client().put_part('http://evil.test/1', blob, 0, 4)
    assert e.value.code == 'UPLOAD_BLOCKED'


def test_put_part_fails_loudly_without_an_etag(requests_mock, blob):
    requests_mock.put('https://s3.test/1', status_code=200)
    with pytest.raises(ApiError) as e:
        make_client().put_part('https://s3.test/1', blob, 0, 4)
    assert e.value.code == 'UPLOAD_FAILED'


def test_complete_file_and_abort(requests_mock):
    requests_mock.post(API + '/uploads/s1/complete-file',
                       json={'id': 's1', 'state': 'uploading'})
    out = make_client().complete_file('s1', 'cog', 0,
                                      [{'part_number': 1, 'etag': '"a"'}])
    assert out['state'] == 'uploading'
    requests_mock.delete(API + '/uploads/s1', json={'id': 's1',
                                                    'state': 'aborted'})
    assert make_client().abort_upload('s1')['state'] == 'aborted'


def test_register_returns_a_manifest_entry(requests_mock):
    requests_mock.post(API + '/uploads/s1/register', status_code=201, json=ENTRY)
    entry = make_client().register_upload('s1', {'epsg': 25832,
                                                 'band_count': 1})
    assert entry.id == 881
    assert entry.sync_etag == 'e2'
    assert entry.cog_status == 'ready'


def test_etag_mismatch_is_a_sync_conflict(requests_mock):
    requests_mock.post(API + '/uploads/initiate', status_code=409, json={
        'error': {'code': 'ETAG_MISMATCH', 'message': 'changed on the server'}})
    with pytest.raises(SyncConflictError):
        make_client().initiate_upload(7, 'dsm', FILES, dataset_id=881,
                                      if_match='stale')
