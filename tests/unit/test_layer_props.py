from geosys_sync.qgis_adapter import layer_props


class FakeLayer:
    def __init__(self):
        self.props = {}

    def setCustomProperty(self, k, v):
        self.props[k] = v

    def customProperty(self, k, default=None):
        return self.props.get(k, default)

    def removeCustomProperty(self, k):
        self.props.pop(k, None)


def test_roundtrip():
    layer = FakeLayer()
    layer_props.write_sync_state(layer, 'https://s.test', 7, 880, 'vector', 'e1')
    state = layer_props.read_sync_state(layer)
    assert state['dataset_id'] == 880 and state['project_id'] == 7
    assert state['kind'] == 'vector' and state['sync_etag'] == 'e1'
    assert state['last_sync_at']  # stamped


def test_read_returns_none_when_never_synced():
    assert layer_props.read_sync_state(FakeLayer()) is None


def test_ints_survive_string_storage():
    # QGIS project files persist custom properties as strings
    layer = FakeLayer()
    layer_props.write_sync_state(layer, 'https://s.test', 7, 880, 'vector', 'e1')
    layer.props = {k: str(v) for k, v in layer.props.items()}
    state = layer_props.read_sync_state(layer)
    assert state['dataset_id'] == 880 and state['project_id'] == 7


def test_clear():
    layer = FakeLayer()
    layer_props.write_sync_state(layer, 'https://s.test', 7, 880, 'vector', 'e1')
    layer_props.clear_sync_state(layer)
    assert layer_props.read_sync_state(layer) is None
