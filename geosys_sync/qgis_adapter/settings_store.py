"""Connection profile persistence.

Plain settings (server URL, username, device id) live in QgsSettings. The
remembered refresh token is a credential, so it goes into the QGIS
authentication database (QgsAuthManager, encrypted under the user's master
password) and never into QgsSettings. Unit tests inject dict-backed fakes for
both stores.

Reading the authentication database can prompt for the master password, so
it is only opened when a session was actually remembered (a plain
`has_remembered_session` flag says so). A user who never ticks "Stay logged
in" is never asked for a master password.

Versions before 0.4.0 kept the refresh token plaintext in QgsSettings; `load`
migrates such a token into the authentication database once and deletes the
plaintext copy either way.
"""
import logging
import uuid

log = logging.getLogger(__name__)

_NS = 'geosys_sync/'
_TOKEN_KEY = 'geosys_sync/refresh_token'


class AuthManagerSecrets:
    """The QGIS authentication database as a tiny key/value secret store.

    Storing needs the master password: QGIS prompts for it (or asks the user
    to create one) the first time per session. A store that fails - the auth
    system is disabled, or the user dismissed the prompt - returns False so
    the caller can stop promising a remembered session it cannot keep.
    """

    def __init__(self):
        from qgis.core import QgsApplication
        self._am = QgsApplication.authManager()

    def _usable(self):
        return self._am is not None and not self._am.isDisabled()

    def get(self, key):
        if not self._usable():
            return None
        try:
            value = self._am.authSetting(key, None, True)
        except Exception:  # auth db locked/corrupt: behave as "nothing saved"
            log.debug('authSetting(%s) failed', key, exc_info=True)
            return None
        return value or None

    def set(self, key, value):
        if not self._usable():
            return False
        try:
            return bool(self._am.storeAuthSetting(key, value, True))
        except Exception:
            log.debug('storeAuthSetting(%s) failed', key, exc_info=True)
            return False

    def remove(self, key):
        if not self._usable():
            return False
        try:
            return bool(self._am.removeAuthSetting(key))
        except Exception:
            log.debug('removeAuthSetting(%s) failed', key, exc_info=True)
            return False


class SettingsStore:

    def __init__(self, backend=None, secrets=None):
        if backend is None:
            from qgis.core import QgsSettings
            backend = QgsSettings()
        self._s = backend
        self._secrets = secrets  # built lazily: only a remembered login needs it

    def _secret_store(self):
        if self._secrets is None:
            self._secrets = AuthManagerSecrets()
        return self._secrets

    def _get(self, key, default=None):
        v = self._s.value(_NS + key, default)
        return default if v in (None, '') else v

    def _set(self, key, value):
        self._s.setValue(_NS + key, value)

    def _flag(self, key):
        return str(self._get(key, '')).lower() in ('true', '1')

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
        """Remember `token` in the authentication database, or forget the
        remembered one when `token` is falsy. Returns False when a token could
        not be stored securely - it is then not stored anywhere."""
        self._set('refresh_token', '')  # never keep a plaintext copy
        if not token:
            if self._flag('has_remembered_session'):
                self._secret_store().remove(_TOKEN_KEY)
            self._set('has_remembered_session', False)
            return True
        stored = self._secret_store().set(_TOKEN_KEY, token)
        self._set('has_remembered_session', bool(stored))
        return stored

    def _refresh_token(self):
        legacy = self._get('refresh_token')
        if legacy:
            # Pre-0.4.0 plaintext token: move it, and drop the plaintext copy
            # whether or not the move succeeded. This one load still returns
            # it so the session resumes; if the move failed, next time the
            # user simply logs in again.
            self._set('refresh_token', '')
            self._set('has_remembered_session',
                      self._secret_store().set(_TOKEN_KEY, legacy))
            return legacy
        if not self._flag('has_remembered_session'):
            return None  # never opens the auth db, so never prompts
        return self._secret_store().get(_TOKEN_KEY)

    def load(self):
        return {'server_base': self._get('server_base'),
                'username': self._get('username'),
                'refresh_token': self._refresh_token()}
