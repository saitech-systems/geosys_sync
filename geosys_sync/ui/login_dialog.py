"""Username/password login dialog."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
)


class LoginDialog(QDialog):

    def __init__(self, parent=None, server_base='', username=''):
        super().__init__(parent)
        self.setWindowTitle('GeosysAI Sync - Login')
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.server_edit = QLineEdit(server_base)
        self.server_edit.setPlaceholderText('https://your-server.example.com')
        self.user_edit = QLineEdit(username)
        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        self.remember = QCheckBox('Stay logged in on this machine')
        self.error_label = QLabel('')
        self.error_label.setStyleSheet('color: #c62828')
        self.error_label.setWordWrap(True)
        # Errors echo server-supplied text; never render it as rich text.
        self.error_label.setTextFormat(Qt.PlainText)
        form.addRow('Server URL', self.server_edit)
        form.addRow('Username or email', self.user_edit)
        form.addRow('Password', self.pass_edit)
        form.addRow('', self.remember)
        form.addRow(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
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
        self.setWindowTitle('GeosysAI Sync - Verification code')
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        hint = ('sent to your email' if 'email' in (methods or ())
                else 'from your authenticator app')
        self.hint_label = QLabel(
            'Enter the 6-digit code {} (or a backup code).'.format(hint))
        self.hint_label.setWordWrap(True)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText('123456')
        self.error_label = QLabel('')
        self.error_label.setStyleSheet('color: #c62828')
        self.error_label.setWordWrap(True)
        # Errors echo server-supplied text; never render it as rich text.
        self.error_label.setTextFormat(Qt.PlainText)
        form.addRow(self.hint_label)
        form.addRow('Code', self.code_edit)
        form.addRow(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def code(self):
        return self.code_edit.text().strip()

    def show_error(self, message):
        self.error_label.setText(message)
