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
