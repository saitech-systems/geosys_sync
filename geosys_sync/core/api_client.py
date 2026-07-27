"""HTTP client for the GeosysAI QGIS-sync API (/api/qgis/v1).

Pure Python (requests only). All QGIS/UI concerns live elsewhere.
Token model: 15-min JWT access token + rotating refresh token. On a 401
TOKEN_EXPIRED the client refreshes once and retries the request; revoked or
invalid tokens raise AuthRequiredError so the UI can force a re-login.
"""
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from geosys_sync.core.errors import (
    ApiError, AuthRequiredError, NetworkError, error_from_response,
)
from geosys_sync.core.models import (
    CogUrl, ManifestEntry, MfaChallenge, Project, SessionInfo, TokenBundle,
)

log = logging.getLogger(__name__)

API_PREFIX = '/api/qgis/v1'

# Parallel ranged downloads: part size, worker count, and the minimum file
# size worth splitting. A single TCP stream to a far-away S3 region is
# latency-bound; concurrent ranges recover most of the available bandwidth
# (measured against Wasabi us-central-1: 0.4 MB/s single stream, 5 MB/s
# with 16 ranges).
RANGE_PART_SIZE = 16 * 1024 * 1024
RANGE_WORKERS = 16

_LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
_QUERY_RE = re.compile(r'\?[^\s\'")\]]*')


def _is_local_host(url):
    try:
        return urlsplit(url).hostname in _LOCAL_HOSTS
    except ValueError:
        return False


def server_url_problem(url):
    """Why `url` is unacceptable as a server base URL, or None if it is fine.

    Credentials and tokens travel on every request, so plain http is only
    allowed for loopback hosts (local dev servers)."""
    url = (url or '').strip()
    if not (url.startswith('http://') or url.startswith('https://')):
        return 'Server URL must start with http(s)://'
    try:
        host = urlsplit(url).hostname
    except ValueError:
        host = None
    if not host:
        return 'Server URL has no host name'
    if url.startswith('http://') and host not in _LOCAL_HOSTS:
        return ('Plain http:// sends your password unencrypted - use '
                'https:// (http is only allowed for localhost)')
    return None


def redact_query(text):
    """Strip URL query strings from `text` (presigned S3 URLs carry their
    signature there; error messages must not echo it)."""
    return _QUERY_RE.sub('?...', text or '')


def _content_range_total(header):
    """Total size from a 'bytes 0-0/12345' Content-Range header, else 0."""
    try:
        return int((header or '').rsplit('/', 1)[1])
    except (IndexError, ValueError):
        return 0


def _write_stream(resp, dest_path, progress=None, chunk_size=1024 * 1024):
    parent = os.path.dirname(dest_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        total = int(resp.headers.get('Content-Length') or 0)
    except (TypeError, ValueError):
        total = 0
    done = 0
    with open(dest_path, 'wb') as fh:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)  # total is 0 when the server omits Content-Length


class GeosysClient:

    def __init__(self, base_url, device_id, device_name=None, session=None,
                 tokens=None, on_tokens_changed=None, timeout=60):
        self.base_url = base_url.rstrip('/')
        self.device_id = device_id
        self.device_name = device_name or 'QGIS'
        self.tokens = tokens
        self.on_tokens_changed = on_tokens_changed
        self.timeout = timeout
        self._http = session or self._default_session()

    @staticmethod
    def _default_session():
        # The stock pool keeps 10 connections per host; ranged downloads run
        # RANGE_WORKERS threads against one S3 host, so size the pool to match
        # or every extra thread pays a fresh TLS handshake per part.
        sess = requests.Session()
        adapter = HTTPAdapter(pool_maxsize=RANGE_WORKERS + 2)
        sess.mount('https://', adapter)
        sess.mount('http://', adapter)
        return sess

    # -- plumbing -----------------------------------------------------------

    def _url(self, path):
        return '{}{}{}'.format(self.base_url, API_PREFIX, path)

    def _set_tokens(self, bundle):
        self.tokens = bundle
        if self.on_tokens_changed:
            self.on_tokens_changed(bundle)

    def _raw(self, method, path, auth=True, headers=None, **kw):
        headers = dict(headers or {})
        if auth:
            if not self.tokens or not self.tokens.access_token:
                raise AuthRequiredError('AUTH_REQUIRED', 'Not logged in')
            headers['Authorization'] = 'Bearer {}'.format(self.tokens.access_token)
        kw.setdefault('timeout', self.timeout)
        try:
            return self._http.request(method, self._url(path), headers=headers, **kw)
        except requests.RequestException as e:
            raise NetworkError('NETWORK_ERROR',
                               'Could not reach server: {}'.format(
                                   redact_query(str(e)))) from e

    def _request(self, method, path, auth=True, _retried=False, **kw):
        resp = self._raw(method, path, auth=auth, **kw)
        if resp.status_code < 400:
            return resp
        err = error_from_response(resp)
        if auth and not _retried and err.code == 'TOKEN_EXPIRED':
            self.refresh_tokens()
            return self._request(method, path, auth=auth, _retried=True, **kw)
        raise err

    # -- auth ---------------------------------------------------------------

    def login(self, identifier, password):
        """Returns SessionInfo on success, or MfaChallenge when the account
        needs a second factor (finish with verify_mfa)."""
        body = {'password': password, 'device_id': self.device_id,
                'device_name': self.device_name}
        if '@' in identifier:
            body['email'] = identifier
        else:
            body['username'] = identifier
        resp = self._raw('POST', '/auth/login', auth=False, json=body)
        if resp.status_code >= 400:
            raise error_from_response(resp)
        data = resp.json()
        if data.get('mfa_required'):
            return MfaChallenge.from_json(data)
        self._set_tokens(TokenBundle.from_json(data))
        return SessionInfo.from_json(data)

    def verify_mfa(self, pending_token, code):
        """Complete an MFA challenge; stores tokens and returns SessionInfo."""
        resp = self._raw('POST', '/auth/mfa-verify', auth=False,
                         json={'pending_token': pending_token, 'code': code,
                               'device_id': self.device_id,
                               'device_name': self.device_name})
        if resp.status_code >= 400:
            raise error_from_response(resp)
        data = resp.json()
        self._set_tokens(TokenBundle.from_json(data))
        return SessionInfo.from_json(data)

    def refresh_tokens(self):
        if not self.tokens or not self.tokens.refresh_token:
            raise AuthRequiredError('AUTH_REQUIRED', 'No refresh token; log in again')
        resp = self._raw('POST', '/auth/refresh', auth=False,
                         json={'refresh_token': self.tokens.refresh_token})
        if resp.status_code >= 400:
            err = error_from_response(resp)
            raise AuthRequiredError(err.code, 'Session expired; log in again',
                                    status=err.status, detail=err.detail)
        self._set_tokens(TokenBundle.from_json(resp.json()))

    def resume(self, refresh_token):
        """Restore a remembered session: rotate the refresh token, fetch /me."""
        self.tokens = TokenBundle(access_token='', refresh_token=refresh_token)
        self.refresh_tokens()
        return self.me()

    def logout(self):
        if self.tokens:
            try:
                self._request('POST', '/auth/logout',
                              json={'refresh_token': self.tokens.refresh_token})
            except ApiError:
                log.debug('logout revoke failed; clearing local tokens anyway')
        self._set_tokens(None)

    def me(self):
        resp = self._request('GET', '/auth/me')
        return SessionInfo.from_json(resp.json())

    # -- projects -----------------------------------------------------------

    def list_projects(self):
        resp = self._request('GET', '/projects')
        return [Project.from_json(p) for p in resp.json().get('projects', [])]

    def get_project(self, project_id):
        resp = self._request('GET', '/projects/{}'.format(project_id))
        return Project.from_json(resp.json())

    def create_project(self, name, description=''):
        resp = self._request('POST', '/projects',
                             json={'name': name, 'description': description})
        return Project.from_json(resp.json())

    def submit_project_request(self, name):
        resp = self._request('POST', '/project-requests', json={'name': name})
        return resp.json()

    def list_project_requests(self):
        resp = self._request('GET', '/project-requests')
        return resp.json().get('project_requests', [])

    # -- datasets: manifest + downloads --------------------------------------

    def get_manifest(self, project_id):
        resp = self._request('GET', '/projects/{}/datasets'.format(project_id))
        return [ManifestEntry.from_json(d)
                for d in resp.json().get('datasets', [])]

    def download_vector(self, dataset_id, dest_path, progress=None):
        resp = self._request('GET', '/datasets/{}/download'.format(dataset_id),
                             stream=True)
        _write_stream(resp, dest_path, progress)
        return resp.headers.get('X-Geosys-Sync-Etag', '')

    def get_cog_url(self, dataset_id, variant='greyscale'):
        resp = self._request('GET', '/datasets/{}/cog-url'.format(dataset_id),
                             params={'variant': variant})
        return CogUrl.from_json(resp.json())

    def download_file(self, url, dest_path, progress=None):
        """Fetch an absolute (presigned S3) URL. No Authorization header -
        the presigned signature IS the credential. Large files are fetched
        as parallel byte ranges when the host honours Range requests.

        Only https URLs are fetched (http for loopback hosts) - the URL is
        server-supplied, and this client must not be steerable at arbitrary
        plain-http or non-HTTP destinations."""
        if not (url.startswith('https://')
                or (url.startswith('http://') and _is_local_host(url))):
            raise ApiError('DOWNLOAD_BLOCKED',
                           'Refusing non-https download URL from server')
        probe = self._plain_get(url, headers={'Range': 'bytes=0-0'})
        if probe.status_code != 206:
            # Host ignored the Range header; the probe already carries the
            # whole body, so just stream it.
            _write_stream(probe, dest_path, progress)
            return dest_path
        total = _content_range_total(probe.headers.get('Content-Range'))
        probe.close()
        if total >= 2 * RANGE_PART_SIZE:
            self._download_ranged(url, dest_path, total, progress)
        else:
            _write_stream(self._plain_get(url), dest_path, progress)
        return dest_path

    def _plain_get(self, url, headers=None):
        try:
            resp = self._http.get(url, stream=True, timeout=self.timeout,
                                  headers=headers)
        except requests.RequestException as e:
            raise NetworkError('NETWORK_ERROR',
                               'Download failed: {}'.format(
                                   redact_query(str(e)))) from e
        if resp.status_code >= 400:
            raise ApiError('DOWNLOAD_FAILED',
                           'HTTP {} fetching file'.format(resp.status_code),
                           status=resp.status_code)
        return resp

    def _download_ranged(self, url, dest_path, total, progress):
        """Download `url` into `dest_path` as concurrent byte-range parts.

        Worker threads only do requests + file writes (never touch QGIS);
        the progress callback fires on the calling thread. On any failure
        the preallocated file is removed - a full-size half-written file
        would look complete."""
        parts = [(start, min(start + RANGE_PART_SIZE, total) - 1)
                 for start in range(0, total, RANGE_PART_SIZE)]
        lock = threading.Lock()
        done = [0]

        def fetch_part(part):
            start, end = part
            for attempt in (0, 1):
                written = 0
                try:
                    resp = self._http.get(
                        url, stream=True, timeout=self.timeout,
                        headers={'Range': 'bytes={}-{}'.format(start, end)})
                    if resp.status_code != 206:
                        raise ApiError(
                            'DOWNLOAD_FAILED',
                            'HTTP {} fetching file'.format(resp.status_code),
                            status=resp.status_code)
                    with open(dest_path, 'r+b') as fh:
                        fh.seek(start)
                        for chunk in resp.iter_content(chunk_size=256 * 1024):
                            fh.write(chunk)
                            written += len(chunk)
                            with lock:
                                done[0] += len(chunk)
                    return
                except requests.RequestException as e:
                    with lock:
                        done[0] -= written
                    if attempt:
                        raise NetworkError(
                            'NETWORK_ERROR',
                            'Download failed: {}'.format(
                                redact_query(str(e)))) from e

        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest_path, 'wb') as fh:
            fh.truncate(total)
        try:
            with ThreadPoolExecutor(
                    max_workers=min(RANGE_WORKERS, len(parts))) as pool:
                futures = [pool.submit(fetch_part, p) for p in parts]
                while True:
                    for f in futures:
                        exc = f.exception() if f.done() else None
                        if exc is not None:
                            for g in futures:
                                g.cancel()
                            raise exc
                    if progress:
                        with lock:
                            n = done[0]
                        progress(n, total)
                    if all(f.done() for f in futures):
                        break
                    time.sleep(0.2)
        except BaseException:
            try:
                os.remove(dest_path)
            except OSError:
                pass
            raise
        if progress:
            progress(total, total)

    def raster_status(self, dataset_id):
        resp = self._request('GET', '/datasets/{}/status'.format(dataset_id))
        return resp.json()

    # -- datasets: uploads ----------------------------------------------------

    def create_dataset(self, project_id, name, kind, file_path,
                       epsg=None, style=None):
        fields = {'name': name, 'kind': kind}
        if epsg is not None:
            fields['epsg'] = str(epsg)
        if style:
            fields['style'] = json.dumps(style)
        resp = self._upload('POST', '/projects/{}/datasets'.format(project_id),
                            file_path, fields)
        return ManifestEntry.from_json(resp.json())

    def overwrite_dataset(self, dataset_id, file_path, kind,
                          epsg=None, style=None, if_match=None):
        fields = {}
        if kind == 'raster' and epsg is not None:
            fields['epsg'] = str(epsg)
        if style:
            fields['style'] = json.dumps(style)
        resp = self._upload('PUT', '/datasets/{}/data'.format(dataset_id),
                            file_path, fields, if_match=if_match)
        return ManifestEntry.from_json(resp.json())

    def update_style(self, dataset_id, wire):
        resp = self._request('PUT', '/datasets/{}/style'.format(dataset_id),
                             json=wire)
        return ManifestEntry.from_json(resp.json())

    def _upload(self, method, path, file_path, fields, if_match=None):
        """Multipart upload with a single refresh-and-retry on TOKEN_EXPIRED.
        The retry reopens the file: a consumed handle cannot be re-sent, so
        _request's generic retry is disabled here (_retried=True)."""
        headers = {'If-Match': if_match} if if_match else None
        last_err = None
        for attempt in (0, 1):
            with open(file_path, 'rb') as fh:
                files = {'file': (os.path.basename(file_path), fh,
                                  'application/octet-stream')}
                try:
                    return self._request(method, path, files=files, data=fields,
                                         headers=headers, _retried=True)
                except ApiError as e:
                    if attempt == 0 and e.code == 'TOKEN_EXPIRED':
                        self.refresh_tokens()
                        last_err = e
                        continue
                    raise
        raise last_err
