import os

import pytest

from geosys_sync.core import api_client
from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import ApiError, RasterProcessingError
from geosys_sync.core.models import TokenBundle

BASE = 'https://server.test'
API = BASE + '/api/qgis/v1'


def make_client():
    return GeosysClient(BASE, device_id='qgis-testdevice1',
                        tokens=TokenBundle('acc', 'ref'))


def test_get_manifest(requests_mock):
    requests_mock.get(API + '/projects/7/datasets', json={
        'project_id': 7,
        'datasets': [
            {'id': 880, 'name': 'roads', 'kind': 'vector', 'sync_etag': 'aa',
             'can_overwrite': True, 'style': {},
             'download': {'endpoint': '/api/qgis/v1/datasets/880/download'}},
            {'id': 881, 'name': 'dsm', 'kind': 'raster', 'sync_etag': 'bb',
             'can_overwrite': False, 'cog_status': 'ready', 'style': {},
             'download': {'endpoint': '/api/qgis/v1/datasets/881/cog-url'}},
        ]})
    entries = make_client().get_manifest(7)
    assert [e.id for e in entries] == [880, 881]
    assert entries[1].cog_status == 'ready'


def test_download_vector_writes_file_and_returns_etag(requests_mock, tmp_path):
    requests_mock.get(API + '/datasets/880/download', content=b'GPKGBYTES',
                      headers={'X-Geosys-Sync-Etag': 'etag880'})
    dest = str(tmp_path / 'roads.gpkg')
    etag = make_client().download_vector(880, dest)
    assert etag == 'etag880'
    assert open(dest, 'rb').read() == b'GPKGBYTES'


def test_download_vector_reports_progress(requests_mock, tmp_path):
    requests_mock.get(API + '/datasets/880/download', content=b'x' * 10,
                      headers={'X-Geosys-Sync-Etag': 'e',
                               'Content-Length': '10'})
    seen = []
    make_client().download_vector(880, str(tmp_path / 'r.gpkg'),
                                  progress=lambda done, total: seen.append((done, total)))
    assert seen and seen[-1] == (10, 10)


def test_download_progress_total_zero_when_no_content_length(requests_mock, tmp_path):
    requests_mock.get('https://s3.example/f.tif?sig=1', content=b'y' * 5)
    seen = []
    make_client().download_file('https://s3.example/f.tif?sig=1',
                                str(tmp_path / 'f.tif'),
                                progress=lambda done, total: seen.append((done, total)))
    assert seen[-1][0] == 5


def test_get_cog_url(requests_mock):
    requests_mock.get(API + '/datasets/881/cog-url', json={
        'url': 'https://s3.wasabi/x.tif?sig=1', 'expires_in': 21600,
        'variant': 'greyscale', 'epsg': 25832, 'band_count': 1,
        'bbox_native': [0, 0, 1, 1], 'cog_min': 1, 'cog_max': 2,
        'sync_etag': 'bb'})
    cog = make_client().get_cog_url(881)
    assert cog.url.startswith('https://s3.wasabi/')
    assert requests_mock.last_request.qs['variant'] == ['greyscale']


def test_get_cog_url_processing_raises(requests_mock):
    requests_mock.get(API + '/datasets/881/cog-url', status_code=409, json={
        'error': {'code': 'RASTER_PROCESSING', 'message': 'converting'}})
    with pytest.raises(RasterProcessingError):
        make_client().get_cog_url(881)


def ranged_s3(data):
    """requests_mock callback that honours Range headers like S3 does."""
    def cb(request, context):
        rng = request.headers.get('Range')
        if not rng:
            context.headers['Content-Length'] = str(len(data))
            return data
        start, end = (int(v) for v in rng.split('=')[1].split('-'))
        context.status_code = 206
        context.headers['Content-Range'] = 'bytes {}-{}/{}'.format(
            start, end, len(data))
        return data[start:end + 1]
    return cb


def test_download_file_parallel_ranges_reassemble(requests_mock, tmp_path,
                                                  monkeypatch):
    monkeypatch.setattr(api_client, 'RANGE_PART_SIZE', 8)
    data = bytes(range(256)) * 2  # 512 bytes -> 64 parts of 8
    requests_mock.get('https://s3.wasabi/big.tif', content=ranged_s3(data))
    dest = str(tmp_path / 'big.tif')
    seen = []
    make_client().download_file('https://s3.wasabi/big.tif', dest,
                                progress=lambda d, t: seen.append((d, t)))
    assert open(dest, 'rb').read() == data
    assert seen[-1] == (len(data), len(data))


def test_download_file_small_ranged_host_single_stream(requests_mock, tmp_path):
    # Host supports ranges but the file is below the split threshold:
    # probe (206) then one plain GET.
    data = b'small-cog-bytes'
    requests_mock.get('https://s3.wasabi/small.tif', content=ranged_s3(data))
    dest = str(tmp_path / 'small.tif')
    make_client().download_file('https://s3.wasabi/small.tif', dest)
    assert open(dest, 'rb').read() == data
    assert len(requests_mock.request_history) == 2


def test_download_file_ranged_part_error_removes_file(requests_mock, tmp_path,
                                                      monkeypatch):
    monkeypatch.setattr(api_client, 'RANGE_PART_SIZE', 8)
    data = b'z' * 64

    def cb(request, context):
        rng = request.headers.get('Range', '')
        if rng == 'bytes=8-15':  # second part: expired-signature style 403
            context.status_code = 403
            return b''
        return ranged_s3(data)(request, context)

    requests_mock.get('https://s3.wasabi/bad.tif', content=cb)
    dest = str(tmp_path / 'bad.tif')
    with pytest.raises(ApiError):
        make_client().download_file('https://s3.wasabi/bad.tif', dest)
    assert not os.path.exists(dest)


def test_download_file_uses_no_auth_header(requests_mock, tmp_path):
    requests_mock.get('https://s3.wasabi/x.tif', content=b'TIF')
    dest = str(tmp_path / 'x.tif')
    make_client().download_file('https://s3.wasabi/x.tif', dest)
    assert 'Authorization' not in requests_mock.last_request.headers
    assert open(dest, 'rb').read() == b'TIF'


def test_raster_status(requests_mock):
    requests_mock.get(API + '/datasets/881/status', json={
        'id': 881, 'kind': 'raster', 'cog_status': 'processing', 'error': None})
    assert make_client().raster_status(881)['cog_status'] == 'processing'
