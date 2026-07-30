"""Presigned multipart upload sessions against /api/qgis/v1/uploads/*.

Pure Python: the GDAL work that produces the artifacts lives in
geosys_sync/qgis_adapter/cog_export.py, and the HTTP calls live on
GeosysClient. This module owns the choreography.
"""
import hashlib
import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from geosys_sync.core.errors import ApiError, NetworkError

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


log = logging.getLogger(__name__)

# Matches api_client.RANGE_WORKERS: the client's connection pool is sized for
# this many concurrent transfers to one storage host.
DEFAULT_WORKERS = 16


def _batched(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def push_raster_session(client, *, project_id, dataset_name, files,
                        register_payload, dataset_id=None, if_match=None,
                        style=None, crs_confirmed=False, progress=None,
                        workers=DEFAULT_WORKERS):
    """Run one raster upload session end to end and return its ManifestEntry.

    `files` is [(role, path)]. Pass `dataset_id` plus `if_match` to replace an
    existing raster. `progress` is called as progress(done_bytes, total_bytes).

    Any failure aborts the session server-side before re-raising, so a
    half-transferred push never leaves the user's storage quota consumed by
    parts nobody will finish.
    """
    manifest = [{'role': role, 'filename': os.path.basename(path),
                 'size': os.path.getsize(path), 'sha256': sha256_file(path)}
                for role, path in files]
    session = client.initiate_upload(
        project_id, dataset_name, manifest, dataset_id=dataset_id,
        if_match=if_match, style=style, crs_confirmed=crs_confirmed)
    session_id = session['upload_session_id']
    part_size = int(session['part_size_bytes'])
    # One file per role for rasters (cog / original / hillshade_cog), so the
    # role is enough to find the server's index for it.
    index_of = {f['role']: f['file_index'] for f in session['files']}
    total = sum(m['size'] for m in manifest)
    state = {'done': 0}
    lock = threading.Lock()

    try:
        for (role, path), meta in zip(files, manifest):
            _upload_file(client, session_id, role, index_of[role], path,
                         meta['size'], part_size, workers, state, total, lock,
                         progress)
        return client.register_upload(session_id, register_payload)
    except BaseException:
        try:
            client.abort_upload(session_id)
        except Exception:
            log.debug('could not abort upload session %s', session_id)
        raise


def _upload_file(client, session_id, role, file_index, path, size, part_size,
                 workers, state, total, lock, progress):
    parts = plan_parts(size, part_size)
    etags = {}

    def send(part, url):
        number, offset, length = part
        for attempt in (0, 1):
            try:
                etags[number] = client.put_part(url, path, offset, length)
                break
            except NetworkError:
                if attempt:
                    raise
            except ApiError as e:
                # Storage 5xx is worth one retry; a 4xx will not fix itself.
                if attempt or not (e.status and e.status >= 500):
                    raise
        with lock:
            state['done'] += length
            if progress:
                progress(state['done'], total)

    # Presign only the next PRESIGN_BATCH parts, right before sending them,
    # rather than every part in the file up front: the server's presigned
    # URLs expire well before a large file finishes uploading at typical
    # upload speeds, so early batches would go stale while later ones were
    # still transferring. Parts within a batch still upload in parallel.
    for batch in _batched(parts, PRESIGN_BATCH):
        numbers = [p[0] for p in batch]
        urls = {item['part_number']: item['url']
                for item in client.presign_parts(session_id, role, file_index,
                                                 numbers)}
        with ThreadPoolExecutor(max_workers=min(workers, len(batch))) as pool:
            futures = [pool.submit(send, p, urls[p[0]]) for p in batch]
            for future in futures:
                future.result()  # re-raises the first part failure

    client.complete_file(session_id, role, file_index,
                         [{'part_number': n, 'etag': etags[n]}
                          for n in sorted(etags)])
