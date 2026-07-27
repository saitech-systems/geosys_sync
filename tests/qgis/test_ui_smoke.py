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
