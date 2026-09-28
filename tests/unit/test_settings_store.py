from geosys_sync.qgis_adapter.settings_store import SettingsStore


class FakeSettings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value


class FakeSecrets:
    """Stands in for the QGIS authentication database. `accept=False` models
    a disabled auth system or a dismissed master-password prompt."""

    def __init__(self, accept=True):
        self.data = {}
        self.accept = accept
        self.reads = 0

    def get(self, key):
        self.reads += 1
        return self.data.get(key)

    def set(self, key, value):
        if not self.accept:
            return False
        self.data[key] = value
        return True

    def remove(self, key):
        return self.data.pop(key, None) is not None


def _store(accept=True):
    settings, secrets = FakeSettings(), FakeSecrets(accept)
    return SettingsStore(backend=settings, secrets=secrets), settings, secrets


def test_device_id_generated_once_and_stable():
    s, _, _ = _store()
    did = s.device_id()
    assert did.startswith('qgis-') and len(did) >= 12
    assert s.device_id() == did


def test_connection_roundtrip():
    s, settings, secrets = _store()
    s.save_connection('https://s.test', 'yash')
    assert s.save_refresh_token('refX') is True
    loaded = s.load()
    assert loaded == {'server_base': 'https://s.test', 'username': 'yash',
                      'refresh_token': 'refX'}
    # The credential lives in the auth database only.
    assert secrets.data == {'geosys_sync/refresh_token': 'refX'}
    assert 'refX' not in str(settings.data)


def test_clear_refresh_token():
    s, _, secrets = _store()
    s.save_refresh_token('refX')
    s.save_refresh_token(None)
    assert s.load()['refresh_token'] is None
    assert secrets.data == {}


def test_a_refused_secure_store_keeps_the_token_nowhere():
    s, settings, secrets = _store(accept=False)
    assert s.save_refresh_token('refX') is False
    assert s.load()['refresh_token'] is None
    assert 'refX' not in str(settings.data)
    assert secrets.data == {}


def test_a_user_who_never_remembered_never_opens_the_auth_database():
    """Reading the auth database can prompt for the master password, so a
    plain login (no "stay logged in") must not touch it at all."""
    s, _, secrets = _store()
    s.save_connection('https://s.test', 'yash')
    assert s.load()['refresh_token'] is None
    assert secrets.reads == 0


def test_a_pre_0_4_plaintext_token_is_migrated_once():
    settings, secrets = FakeSettings(), FakeSecrets()
    settings.setValue('geosys_sync/refresh_token', 'oldtok')
    s = SettingsStore(backend=settings, secrets=secrets)
    assert s.load()['refresh_token'] == 'oldtok'      # this session still resumes
    assert settings.data['geosys_sync/refresh_token'] == ''  # plaintext gone
    assert secrets.data['geosys_sync/refresh_token'] == 'oldtok'
    assert s.load()['refresh_token'] == 'oldtok'      # now served from the auth db


def test_a_plaintext_token_is_deleted_even_when_the_move_fails():
    settings, secrets = FakeSettings(), FakeSecrets(accept=False)
    settings.setValue('geosys_sync/refresh_token', 'oldtok')
    s = SettingsStore(backend=settings, secrets=secrets)
    assert s.load()['refresh_token'] == 'oldtok'
    assert settings.data['geosys_sync/refresh_token'] == ''
    assert s.load()['refresh_token'] is None           # nothing remembered any more
