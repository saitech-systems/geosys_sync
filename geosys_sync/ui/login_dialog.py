"""Username/password login dialog."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
)

from geosys_sync.core.i18n import tr


class LoginDialog(QDialog):

    def __init__(self, parent=None, server_base='', username=''):
        super().__init__(parent)
        self.setWindowTitle(tr('GeosysAI Sync - Login'))
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.server_edit = QLineEdit(server_base)
        self.server_edit.setPlaceholderText('https://your-server.example.com')
        self.user_edit = QLineEdit(username)
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox(tr('Stay logged in on this machine'))
        self.error_label = QLabel('')
        self.error_label.setStyleSheet('color: #c62828')
        self.error_label.setWordWrap(True)
        # Errors echo server-supplied text; never render it as rich text.
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(tr('Server URL'), self.server_edit)
        form.addRow(tr('Username or email'), self.user_edit)
        form.addRow(tr('Password'), self.pass_edit)
        form.addRow('', self.remember)
        form.addRow(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return (self.server_edit.text().strip().rstrip('/'),
                self.user_edit.text().strip(),
                self.pass_edit.text(),
                self.remember.isChecked())

    def show_error(self, message):
        self.error_label.setText(message)


class MfaDialog(QDialog):
    """Second-factor code prompt shown after the password check succeeds."""

    def __init__(self, parent=None, methods=()):
        super().__init__(parent)
        self.setWindowTitle(tr('GeosysAI Sync - Verification code'))
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.hint_label = QLabel(
            tr('Enter the 6-digit code sent to your email (or a backup code).')
            if 'email' in (methods or ()) else
            tr('Enter the 6-digit code from your authenticator app (or a backup code).'))
        self.hint_label.setWordWrap(True)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText('123456')
        self.error_label = QLabel('')
        self.error_label.setStyleSheet('color: #c62828')
        self.error_label.setWordWrap(True)
        # Errors echo server-supplied text; never render it as rich text.
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.hint_label)
        form.addRow(tr('Code'), self.code_edit)
        form.addRow(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def code(self):
        return self.code_edit.text().strip()

    def show_error(self, message):
        self.error_label.setText(message)
