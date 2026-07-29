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
