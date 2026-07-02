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
