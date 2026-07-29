import hashlib

import pytest

from geosys_sync.core import upload_session as us
from geosys_sync.core.errors import ApiError, NetworkError


def test_plan_parts_splits_evenly():
    assert us.plan_parts(300, 100) == [(1, 0, 100), (2, 100, 100), (3, 200, 100)]


def test_plan_parts_short_final_part():
    assert us.plan_parts(250, 100) == [(1, 0, 100), (2, 100, 100), (3, 200, 50)]


def test_plan_parts_single_part_smaller_than_part_size():
    assert us.plan_parts(10, 100) == [(1, 0, 10)]


def test_plan_parts_covers_the_whole_file():
    parts = us.plan_parts(1_234_567, 64 * 1024)
    assert sum(length for _n, _o, length in parts) == 1_234_567
    assert parts[0][1] == 0
    assert parts[-1][1] + parts[-1][2] == 1_234_567


def test_plan_parts_refuses_more_than_the_s3_limit():
    with pytest.raises(ValueError) as e:
        us.plan_parts(us.S3_MAX_PARTS + 1, 1)
    assert '10000' in str(e.value)


def test_plan_parts_accepts_exactly_the_s3_limit():
    """The boundary is inclusive: 10000 parts is legal, 10001 is not."""
    parts = us.plan_parts(us.S3_MAX_PARTS, 1)
    assert len(parts) == us.S3_MAX_PARTS
    assert parts[-1] == (us.S3_MAX_PARTS, us.S3_MAX_PARTS - 1, 1)


@pytest.mark.parametrize('size,part', [(0, 100), (100, 0), (-1, 100)])
def test_plan_parts_rejects_nonsense(size, part):
    with pytest.raises(ValueError):
        us.plan_parts(size, part)


def test_sha256_file_matches_hashlib(tmp_path):
    p = tmp_path / 'blob.bin'
    payload = b'geosys' * 5000
    p.write_bytes(payload)
    assert us.sha256_file(str(p)) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_streams_in_chunks(tmp_path):
    p = tmp_path / 'blob.bin'
    payload = bytes(range(256)) * 1000
    p.write_bytes(payload)
    assert us.sha256_file(str(p), chunk_size=7) == \
        hashlib.sha256(payload).hexdigest()


class FakeClient:
    """Records the session choreography without touching the network."""

    def __init__(self, part_size=100, fail_put_on=None):
        self.part_size = part_size
        self.fail_put_on = fail_put_on   # part number that raises once
        self.initiated = None
        self.presigned = []
        self.puts = []
        self.completed = []
        self.registered = None
        self.aborted = []
        self._failed_once = set()

    def initiate_upload(self, project_id, dataset_name, files, **kw):
        self.initiated = {'project_id': project_id,
                          'dataset_name': dataset_name, 'files': files, **kw}
        return {'upload_session_id': 'sess-1',
                'part_size_bytes': self.part_size,
                'files': [{'role': f['role'], 'file_index': i}
                          for i, f in enumerate(files)]}

    def presign_parts(self, session_id, role, file_index, part_numbers):
        self.presigned.append((role, list(part_numbers)))
        return [{'part_number': n, 'url': 'https://s3.test/{}/{}'.format(role, n),
                 'expires_in': 60} for n in part_numbers]

    def put_part(self, url, file_path, offset, length):
        if self.fail_put_on is not None and url.endswith(str(self.fail_put_on)) \
                and self.fail_put_on not in self._failed_once:
            self._failed_once.add(self.fail_put_on)
            raise NetworkError('NETWORK_ERROR', 'flaky')
        self.puts.append((url, offset, length))
        return '"etag-{}"'.format(offset)

    def complete_file(self, session_id, role, file_index, parts):
        self.completed.append((role, [p['part_number'] for p in parts]))
        return {'state': 'uploading'}

    def register_upload(self, session_id, payload):
        self.registered = payload
        return 'ENTRY'

    def abort_upload(self, session_id):
        self.aborted.append(session_id)


@pytest.fixture
def artifacts(tmp_path):
    cog = tmp_path / 'd_COG.tif'
    cog.write_bytes(b'C' * 250)
    original = tmp_path / 'd.tif'
    original.write_bytes(b'O' * 40)
    return [('cog', str(cog)), ('original', str(original))]


PAYLOAD = {'epsg': 25832, 'band_count': 1}


def test_driver_runs_the_whole_choreography(artifacts):
    client = FakeClient(part_size=100)
    seen = []
    out = us.push_raster_session(
        client, project_id=7, dataset_name='dsm', files=artifacts,
        register_payload=PAYLOAD, crs_confirmed=True,
        progress=lambda done, total: seen.append((done, total)))

    assert out == 'ENTRY'
    manifest = client.initiated['files']
    assert [m['role'] for m in manifest] == ['cog', 'original']
    assert manifest[0]['size'] == 250 and manifest[1]['size'] == 40
    assert all(len(m['sha256']) == 64 for m in manifest)
    assert client.initiated['crs_confirmed'] is True
    # 250 bytes at a 100-byte part size is three parts; 40 bytes is one.
    assert client.completed == [('cog', [1, 2, 3]), ('original', [1])]
    assert len(client.puts) == 4
    assert client.registered == PAYLOAD
    assert client.aborted == []
    assert seen[-1] == (290, 290)


def test_driver_forwards_the_overwrite_target(artifacts):
    client = FakeClient()
    us.push_raster_session(client, project_id=7, dataset_name='dsm',
                           files=artifacts, register_payload=PAYLOAD,
                           dataset_id=881, if_match='etag-1',
                           style={'raster_opacity': 0.5})
    assert client.initiated['dataset_id'] == 881
    assert client.initiated['if_match'] == 'etag-1'
    assert client.initiated['style'] == {'raster_opacity': 0.5}


def test_driver_retries_a_part_once(artifacts):
    client = FakeClient(part_size=100, fail_put_on=2)
    us.push_raster_session(client, project_id=7, dataset_name='dsm',
                           files=artifacts, register_payload=PAYLOAD)
    assert client.completed == [('cog', [1, 2, 3]), ('original', [1])]
    assert client.aborted == []


def test_driver_aborts_the_session_when_a_file_fails(artifacts, monkeypatch):
    client = FakeClient(part_size=100)

    def boom(*a, **kw):
        raise ApiError('UPLOAD_FAILED', 'storage is down')

    monkeypatch.setattr(client, 'put_part', boom)
    with pytest.raises(ApiError):
        us.push_raster_session(client, project_id=7, dataset_name='dsm',
                               files=artifacts, register_payload=PAYLOAD)
    assert client.aborted == ['sess-1']
    assert client.registered is None


def test_driver_presigns_in_batches_of_100(tmp_path):
    big = tmp_path / 'big_COG.tif'
    big.write_bytes(b'x' * 150)
    client = FakeClient(part_size=1)
    us.push_raster_session(client, project_id=7, dataset_name='dsm',
                           files=[('cog', str(big))], register_payload=PAYLOAD)
    batches = [nums for role, nums in client.presigned]
    assert [len(b) for b in batches] == [100, 50]
