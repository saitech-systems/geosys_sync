import pytest

qgis = pytest.importorskip('qgis')  # entire directory needs a QGIS runtime

from qgis.core import QgsApplication  # noqa: E402


@pytest.fixture(scope='session')
def qgis_app():
    app = QgsApplication([], False)
    app.initQgis()
    yield app
    app.exitQgis()
