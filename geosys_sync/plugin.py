"""QGIS plugin entry point - toolbar action + dialog lifecycle."""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.svg')


class GeosysSyncPlugin:

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        self.action = QAction(QIcon(ICON_PATH), 'GeosysAI Sync',
                              self.iface.mainWindow())
        self.action.setToolTip('Sync layers with a GeosysAI project')
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu('&GeosysAI Sync', self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu('&GeosysAI Sync', self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def run(self):
        from geosys_sync.ui.sync_dialog import SyncDialog
        if self.dialog is None:
            self.dialog = SyncDialog(self.iface)
            self.dialog.setWindowIcon(QIcon(ICON_PATH))
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
