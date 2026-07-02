"""Connection profile persistence. Backed by QgsSettings inside QGIS; unit
tests inject a dict-backed fake.

NOTE: the refresh token is stored plaintext in QgsSettings (like most QGIS
plugin credentials). Moving it into QgsAuthManager is a documented follow-up.
"""
import uuid

_NS = 'geosys_sync/'


class SettingsStore:

    def __init__(self, backend=None):
        if backend is None:
            from qgis.core import QgsSettings
            backend = QgsSettings()
        self._s = backend

    def _get(self, key, default=None):
        v = self._s.value(_NS + key, default)
        return default if v in (None, '') else v

    def _set(self, key, value):
        self._s.setValue(_NS + key, value)

    def device_id(self):
        did = self._get('device_id')
        if not did:
            did = 'qgis-' + uuid.uuid4().hex
            self._set('device_id', did)
        return did

    def save_connection(self, server_base, username):
        self._set('server_base', server_base)
        self._set('username', username)

    def save_refresh_token(self, token):
        self._set('refresh_token', token or '')

    def load(self):
        return {'server_base': self._get('server_base'),
                'username': self._get('username'),
                'refresh_token': self._get('refresh_token')}
