"""Which language QGIS is running in.

QGIS resolves its UI language from Settings > Options > General: when the
override flag is set, `locale/userLocale` (e.g. 'fr_FR'); otherwise the
system locale. The plugin follows the same answer, so it never shows a
different language from the menus around it.
"""
from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QLocale

from geosys_sync.core.i18n import normalize_language


def qgis_language():
    settings = QgsSettings()
    override = str(settings.value('locale/overrideFlag', False)).lower() in ('true', '1')
    if override:
        code = settings.value('locale/userLocale', '')
        if code:
            return normalize_language(code)
    return normalize_language(QLocale.system().name())
