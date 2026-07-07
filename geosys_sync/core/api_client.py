"""HTTP client for the GeosysAI QGIS-sync API (/api/qgis/v1).

Pure Python (requests only). All QGIS/UI concerns live elsewhere.
Token model: 15-min JWT access token + rotating refresh token. On a 401
TOKEN_EXPIRED the client refreshes once and retries the request; revoked or
invalid tokens raise AuthRequiredError so the UI can force a re-login.
"""
import json
import logging
import os

import requests

from geosys_sync.core.errors import (
    ApiError, AuthRequiredError, NetworkError, error_from_response,
)
from geosys_sync.core.models import (
    CogUrl, ManifestEntry, Project, SessionInfo, TokenBundle,
)

log = logging.getLogger(__name__)

API_PREFIX = '/api/qgis/v1'


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
        self._http = session or requests.Session()

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
                               'Could not reach server: {}'.format(e)) from e

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
        the presigned signature IS the credential."""
        try:
            resp = self._http.get(url, stream=True, timeout=self.timeout)
        except requests.RequestException as e:
            raise NetworkError('NETWORK_ERROR',
                               'Download failed: {}'.format(e)) from e
        if resp.status_code >= 400:
            raise ApiError('DOWNLOAD_FAILED',
                           'HTTP {} fetching file'.format(resp.status_code),
                           status=resp.status_code)
        _write_stream(resp, dest_path, progress)
        return dest_path

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
