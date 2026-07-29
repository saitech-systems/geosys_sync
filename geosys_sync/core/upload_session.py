"""Presigned multipart upload sessions against /api/qgis/v1/uploads/*.

Pure Python: the GDAL work that produces the artifacts lives in
geosys_sync/qgis_adapter/cog_export.py, and the HTTP calls live on
GeosysClient. This module owns the choreography.
"""
import hashlib
import math

# S3 caps a multipart upload at 10000 parts; the server rejects a manifest
# that would need more, so fail here with a message the user can act on.
S3_MAX_PARTS = 10000
# The server accepts at most 100 part numbers per presign call.
PRESIGN_BATCH = 100


def plan_parts(file_size, part_size):
    """Byte ranges for one file's multipart upload.

    Returns [(part_number, offset, length)] with 1-indexed part numbers
    covering the whole file, exactly once, in order.
    """
    if file_size <= 0:
        raise ValueError('file_size must be positive')
    if part_size <= 0:
        raise ValueError('part_size must be positive')
    count = max(1, int(math.ceil(file_size / float(part_size))))
    if count > S3_MAX_PARTS:
        raise ValueError(
            'File needs {} parts; the storage limit is {}'.format(
                count, S3_MAX_PARTS))
    return [(i + 1, i * part_size,
             part_size if i < count - 1 else file_size - i * part_size)
            for i in range(count)]


def sha256_file(path, chunk_size=1024 * 1024):
    """Streaming SHA-256; the server compares it against the stored object."""
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(chunk_size), b''):
            digest.update(block)
    return digest.hexdigest()
