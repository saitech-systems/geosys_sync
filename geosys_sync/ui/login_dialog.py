"""Username/password login dialog."""
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
