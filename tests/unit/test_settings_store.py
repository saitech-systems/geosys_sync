from geosys_sync.qgis_adapter.settings_store import SettingsStore


class FakeSettings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, value):
        self.data[key] = value


def test_device_id_generated_once_and_stable():
    s = SettingsStore(backend=FakeSettings())
    did = s.device_id()
    assert did.startswith('qgis-') and len(did) >= 12
    assert s.device_id() == did


def test_connection_roundtrip():
    s = SettingsStore(backend=FakeSettings())
    s.save_connection('https://s.test', 'yash')
    s.save_refresh_token('refX')
    loaded = s.load()
    assert loaded == {'server_base': 'https://s.test', 'username': 'yash',
                      'refresh_token': 'refX'}


def test_clear_refresh_token():
    s = SettingsStore(backend=FakeSettings())
    s.save_refresh_token('refX')
    s.save_refresh_token(None)
    assert s.load()['refresh_token'] is None
