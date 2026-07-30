"""The style-only push: PUT /datasets/<id>/style with no data re-upload."""
import pytest

pytestmark = pytest.mark.qgis

from qgis.core import QgsProject, QgsVectorLayer  # noqa: E402

from geosys_sync.core.errors import ApiError  # noqa: E402
from geosys_sync.core.models import ManifestEntry  # noqa: E402
from geosys_sync.core.sync_plan import PushAction  # noqa: E402
from geosys_sync.qgis_adapter import layer_props  # noqa: E402
from geosys_sync.ui import sync_dialog  # noqa: E402

STYLE_OK = 'Updated the style on {} layer(s).'


class _Task:
    def setProgress(self, _pct):
        pass


class _Client:
    """Records every style push; raises for dataset ids listed in `fail`."""

    tokens = 'tok'                     # _require_connection checks for these
    base_url = 'https://s.test'

    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def get_manifest(self, _project_id):
        return []                      # _push_finished refreshes when done

    def update_style(self, dataset_id, wire):
        self.calls.append((dataset_id, wire))
        if dataset_id in self.fail:
            raise ApiError('SERVER_ERROR', 'boom', status=500)
        return _entry(dataset_id, 'etag-{}'.format(dataset_id))


def _entry(dataset_id, etag, warnings=None):
    return ManifestEntry.from_json({
        'id': dataset_id, 'name': 'ds', 'kind': 'vector', 'sync_etag': etag,
        'style_warnings': warnings or []})


def _action(dataset_id, layer_id='L1', name='roads'):
    return PushAction(layer_id, name, 'vector', 'overwrite',
                      dataset_id=dataset_id, if_match='old')


def _synced_layer(name='roads', dataset_id=7, etag='stale'):
    layer = QgsVectorLayer('Point?crs=EPSG:4326', name, 'memory')
    assert layer.isValid()
    QgsProject.instance().addMapLayer(layer)
    layer_props.write_sync_state(layer, server_base='https://s.test',
                                 project_id=3, dataset_id=dataset_id,
                                 kind='vector', sync_etag=etag)
    return layer


def test_style_worker_pushes_every_job(qgis_app):
    client = _Client()
    jobs = [{'action': _action(7), 'style': {'style_color': '#ff0000'}},
            {'action': _action(8), 'style': {'raster_opacity': 0.5}}]

    results = sync_dialog._style_worker(_Task(), client, jobs)

    assert client.calls == [(7, {'style_color': '#ff0000'}),
                            (8, {'raster_opacity': 0.5})]
    assert [r['entry'].id for r in results] == [7, 8]
    assert all(r['error'] is None for r in results)


def test_style_worker_contains_one_failure_and_keeps_going(qgis_app):
    # A failure must not abort the task: layers already pushed still need
    # _push_finished to write their refreshed etag back.
    client = _Client(fail=[7])
    jobs = [{'action': _action(7), 'style': {}},
            {'action': _action(8), 'style': {}}]

    results = sync_dialog._style_worker(_Task(), client, jobs)

    assert len(results) == 2
    assert isinstance(results[0]['error'], ApiError)
    assert results[1]['error'] is None and results[1]['entry'].id == 8
    assert [c[0] for c in client.calls] == [7, 8]


def test_a_style_push_writes_the_refreshed_etag_onto_the_layer(qgis_app):
    # The server folds the style into sync_etag, so a style-only push changes
    # it. Failing to store the new one leaves the layer reporting "changed on
    # server" and makes the next data overwrite 409 on a stale If-Match.
    layer = _synced_layer(etag='stale')
    dlg = sync_dialog.SyncDialog(iface=None)
    try:
        dlg._push_finished(
            [{'action': _action(7, layer_id=layer.id()),
              'entry': _entry(7, 'fresh'), 'error': None}],
            'https://s.test', 3, ok_text=STYLE_OK)

        assert layer_props.read_sync_state(layer)['sync_etag'] == 'fresh'
        assert dlg.status_label.text() == 'Updated the style on 1 layer(s).'
    finally:
        QgsProject.instance().removeMapLayer(layer.id())


def test_a_style_push_reports_the_servers_own_warnings(qgis_app):
    layer = _synced_layer(name='dem')
    dlg = sync_dialog.SyncDialog(iface=None)
    try:
        dlg._push_finished(
            [{'action': _action(7, layer_id=layer.id(), name='dem'),
              'entry': _entry(7, 'fresh', warnings=['Downgraded to single.']),
              'error': None}],
            'https://s.test', 3, ok_text=STYLE_OK)

        text = dlg.status_label.text()
        assert 'Note: dem: Downgraded to single.' in text
        assert 'Failed' not in text
    finally:
        QgsProject.instance().removeMapLayer(layer.id())


def test_one_layers_broken_style_does_not_sink_the_whole_action(
        qgis_app, monkeypatch):
    # Style extraction reads renderer internals and can raise on an exotic one
    # (a category with no symbol, say). Uncontained, that escapes into the Qt
    # slot: no status, no upload, no explanation.
    good = _synced_layer(name='roads', dataset_id=7)
    bad = _synced_layer(name='weird', dataset_id=8)
    dlg = sync_dialog.SyncDialog(iface=None)
    dlg.client = _Client()
    dlg._push_actions = [_action(7, layer_id=good.id(), name='roads'),
                         _action(8, layer_id=bad.id(), name='weird')]
    real = sync_dialog.style_extract.extract_wire

    def flaky(layer):
        if layer.id() == bad.id():
            raise AttributeError("'NoneType' object has no attribute 'color'")
        return real(layer)

    monkeypatch.setattr(sync_dialog.style_extract, 'extract_wire', flaky)
    monkeypatch.setattr(dlg, '_checked_rows', lambda _t: [0, 1])
    monkeypatch.setattr(dlg, '_current_project_id', lambda: 3)
    # Run the task body inline: QgsTask would finish after the test returns.
    monkeypatch.setattr(dlg, '_start_task',
                        lambda _n, worker, on_done: on_done(worker(_Task())))

    dlg._run_style_push()

    text = dlg.status_label.text()
    assert 'Updated the style on 1 layer(s).' in text
    assert 'weird' in text and 'Failed' in text
    for layer in (good, bad):
        QgsProject.instance().removeMapLayer(layer.id())


def test_the_result_survives_the_manifest_refresh(qgis_app, monkeypatch):
    # _push_finished reloads the manifest to refresh the tables, and that
    # reload used to blank the status label - wiping the summary it had just
    # written. Affects the data push as much as this one.
    layer = _synced_layer()
    dlg = sync_dialog.SyncDialog(iface=None)
    dlg.client = _Client()
    monkeypatch.setattr(dlg, '_current_project_id', lambda: 3)
    try:
        dlg._push_finished(
            [{'action': _action(7, layer_id=layer.id()),
              'entry': _entry(7, 'fresh'), 'error': None}],
            'https://s.test', 3, ok_text=STYLE_OK)

        assert dlg.status_label.text() == 'Updated the style on 1 layer(s).'
    finally:
        QgsProject.instance().removeMapLayer(layer.id())


def test_the_push_tab_offers_a_style_only_button(qgis_app):
    dlg = sync_dialog.SyncDialog(iface=None)
    assert dlg.style_btn.text() == 'Upload style only'
    # Must grey out with the rest while a task runs, or it looks live when it
    # is not: _start_task refuses a second concurrent task.
    assert dlg.style_btn in dlg._busy_widgets()
