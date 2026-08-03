import pytest

pytestmark = pytest.mark.qgis


def test_ui_modules_import(qgis_app):
    from geosys_sync.ui.login_dialog import LoginDialog  # noqa: F401
    from geosys_sync.ui.sync_dialog import SyncDialog  # noqa: F401
    from geosys_sync.plugin import GeosysSyncPlugin  # noqa: F401


def test_login_dialog_values(qgis_app):
    from geosys_sync.ui.login_dialog import LoginDialog
    dlg = LoginDialog(server_base='https://s.test/', username='yash')
    dlg.pass_edit.setText('pw')
    dlg.remember.setChecked(True)
    server, identifier, password, remember = dlg.values()
    assert server == 'https://s.test'      # trailing slash stripped
    assert (identifier, password, remember) == ('yash', 'pw', True)


def test_login_dialog_error_label_is_plain_text(qgis_app):
    # Server-supplied error messages must not render as Qt rich text.
    from qgis.PyQt.QtCore import Qt
    from geosys_sync.ui.login_dialog import LoginDialog
    dlg = LoginDialog()
    assert dlg.error_label.textFormat() == Qt.PlainText


def test_mfa_dialog_collects_code(qgis_app):
    from qgis.PyQt.QtCore import Qt
    from geosys_sync.ui.login_dialog import MfaDialog
    dlg = MfaDialog(methods=['totp'])
    dlg.code_edit.setText(' 123456 ')
    assert dlg.code() == '123456'
    assert dlg.error_label.textFormat() == Qt.PlainText


def test_mfa_dialog_email_hint(qgis_app):
    from geosys_sync.ui.login_dialog import MfaDialog
    assert 'email' in MfaDialog(methods=['email']).hint_label.text()
    assert 'authenticator' in MfaDialog(methods=['totp']).hint_label.text()


def test_sync_dialog_status_labels_are_plain_text(qgis_app):
    from qgis.PyQt.QtCore import Qt
    from geosys_sync.ui.sync_dialog import SyncDialog
    dlg = SyncDialog(iface=None)
    assert dlg.status_label.textFormat() == Qt.PlainText
    assert dlg.conn_label.textFormat() == Qt.PlainText


def test_finish_status_keeps_notes_out_of_the_failed_list(qgis_app):
    # A style the platform had to downgrade is not a layer that failed to
    # upload, and must not read like one.
    from geosys_sync.ui.sync_dialog import SyncDialog
    dlg = SyncDialog(iface=None)
    dlg._finish_status('Uploaded 2 layer(s).', ['roads: timed out'],
                       ['dem: pseudocolor not supported'])
    text = dlg.status_label.text()
    assert 'Failed: roads: timed out' in text
    assert 'Note: dem: pseudocolor not supported' in text


def test_finish_status_says_nothing_extra_when_all_is_well(qgis_app):
    from geosys_sync.ui.sync_dialog import SyncDialog
    dlg = SyncDialog(iface=None)
    dlg._finish_status('Downloaded 3 layer(s).', [])
    assert dlg.status_label.text() == 'Downloaded 3 layer(s).'


def _dialog_with_project(cached, fresh=None):
    from geosys_sync.ui.sync_dialog import SyncDialog

    class _Client:
        def get_project(self, _pid):
            return fresh if fresh is not None else cached

    dlg = SyncDialog(iface=None)
    dlg.projects = [cached]
    # Added while dlg.client is still None so the signal can't fire a manifest load.
    dlg.project_combo.addItem(cached.name, cached.id)
    dlg.client = _Client()
    return dlg


def test_crs_confirmation_skipped_on_a_locked_project(qgis_app):
    from geosys_sync.core.models import Project
    locked = Project(id=5, name='P', epsg_locked=True,
                     crs_confirmation_required=False)
    assert _dialog_with_project(locked)._project_awaiting_crs_confirmation() is None


def test_crs_confirmation_uses_fresh_server_state_not_the_cached_row(qgis_app):
    from geosys_sync.core.models import Project
    stale = Project(id=5, name='P', epsg_locked=True,
                    crs_confirmation_required=False)
    fresh = Project(id=5, name='P', epsg_locked=False,
                    crs_confirmation_required=True)
    got = _dialog_with_project(stale, fresh)._project_awaiting_crs_confirmation()
    assert got is not None and got.crs_confirmation_required is True


# A CRS QGIS can draw perfectly well but which has no authority code.
CUSTOM_PROJ = ('+proj=tmerc +lat_0=0 +lon_0=9.123456 +k=1 +x_0=1234567 '
               '+y_0=0 +ellps=bessel +units=m +no_defs')


def _memory_layer(name, crs='EPSG:4326'):
    from qgis.core import QgsProject, QgsVectorLayer
    layer = QgsVectorLayer('Point?crs={}'.format(crs), name, 'memory')
    QgsProject.instance().addMapLayer(layer)
    return layer


def _push_action(layer):
    from geosys_sync.core.sync_plan import PushAction
    return PushAction(layer_id=layer.id(), name=layer.name(), kind='vector',
                      mode='create')


def test_resolve_push_crs_passes_a_projected_layer_through(qgis_app):
    from qgis.core import QgsProject
    from geosys_sync.ui.sync_dialog import SyncDialog
    layer = _memory_layer('ok')
    try:
        dlg = SyncDialog(iface=None)
        dlg._ask_layer_crs = lambda *a: pytest.fail('must not prompt')
        resolved, failed = dlg._resolve_push_crs([_push_action(layer)])
        assert failed == []
        assert [pair[1] for pair in resolved] == [layer]
    finally:
        QgsProject.instance().removeAllMapLayers()


def test_resolve_push_crs_skips_a_cancelled_layer_and_keeps_the_rest(qgis_app):
    # One unprojected layer must not cost the upload of the good ones.
    from qgis.core import QgsCoordinateReferenceSystem, QgsProject
    from geosys_sync.ui.sync_dialog import SyncDialog
    good = _memory_layer('good')
    bad = _memory_layer('bad')
    bad.setCrs(QgsCoordinateReferenceSystem())
    try:
        dlg = SyncDialog(iface=None)
        dlg._ask_layer_crs = lambda layer, reason: None   # user cancelled
        resolved, failed = dlg._resolve_push_crs(
            [_push_action(bad), _push_action(good)])
        assert [pair[1] for pair in resolved] == [good]
        assert failed == ['bad: layer has no coordinate reference system']
    finally:
        QgsProject.instance().removeAllMapLayers()


def test_resolve_push_crs_applies_the_picked_crs(qgis_app):
    from qgis.core import QgsCoordinateReferenceSystem, QgsProject
    from geosys_sync.qgis_adapter import layer_export
    from geosys_sync.ui.sync_dialog import SyncDialog
    layer = _memory_layer('fixme')
    layer.setCrs(QgsCoordinateReferenceSystem())
    try:
        dlg = SyncDialog(iface=None)
        picked = QgsCoordinateReferenceSystem('EPSG:32632')
        dlg._ask_layer_crs = lambda l, reason: picked
        resolved, failed = dlg._resolve_push_crs([_push_action(layer)])
        assert failed == [] and len(resolved) == 1
        assert layer_export.layer_epsg(layer) == 32632
    finally:
        QgsProject.instance().removeAllMapLayers()


def test_resolve_push_crs_rejects_an_authority_less_pick(qgis_app):
    # Picking another CRS with no EPSG code leaves us exactly where we
    # started, and must not mutate the user's layer on the way out.
    from qgis.core import QgsCoordinateReferenceSystem, QgsProject
    from geosys_sync.ui.sync_dialog import SyncDialog
    layer = _memory_layer('fixme')
    layer.setCrs(QgsCoordinateReferenceSystem())
    try:
        dlg = SyncDialog(iface=None)
        dlg._ask_layer_crs = lambda l, reason: (
            QgsCoordinateReferenceSystem.fromProj(CUSTOM_PROJ))
        resolved, failed = dlg._resolve_push_crs([_push_action(layer)])
        assert resolved == []
        assert len(failed) == 1 and 'no EPSG code' in failed[0]
        assert not layer.crs().isValid()   # the rejected pick was not applied
    finally:
        QgsProject.instance().removeAllMapLayers()


def test_resolve_push_crs_reports_a_layer_that_left_the_project(qgis_app):
    from qgis.core import QgsProject
    from geosys_sync.ui.sync_dialog import SyncDialog
    layer = _memory_layer('gone')
    action = _push_action(layer)
    QgsProject.instance().removeAllMapLayers()
    dlg = SyncDialog(iface=None)
    dlg._ask_layer_crs = lambda *a: pytest.fail('must not prompt')
    resolved, failed = dlg._resolve_push_crs([action])
    assert resolved == []
    assert failed == ['gone: layer is no longer in the project']
