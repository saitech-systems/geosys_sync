import os

import pytest

pytestmark = pytest.mark.qgis

from qgis.core import (  # noqa: E402
    QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer,
)

from geosys_sync.core.models import ManifestEntry  # noqa: E402
from geosys_sync.qgis_adapter import layer_export, layer_load, layer_props  # noqa: E402


def point_layer(qgis_app, name='pts'):
    layer = QgsVectorLayer('Point?crs=EPSG:4326&field=name:string', name, 'memory')
    feat = QgsFeature(layer.fields())
    feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(6.1, 47.3)))
    feat['name'] = 'a'
    layer.dataProvider().addFeatures([feat])
    return layer


def test_layer_kind_vector(qgis_app):
    assert layer_export.layer_kind(point_layer(qgis_app)) == 'vector'


def test_export_vector_gpkg(qgis_app, tmp_path):
    dest = str(tmp_path / 'pts.gpkg')
    out = layer_export.export_vector_gpkg(point_layer(qgis_app), dest)
    assert os.path.isfile(out)
    reread = QgsVectorLayer(out, 'reread', 'ogr')
    assert reread.isValid() and reread.featureCount() == 1


def test_layer_epsg(qgis_app):
    assert layer_export.layer_epsg(point_layer(qgis_app)) == 4326


def test_collect_layer_facts_reads_sync_props(qgis_app):
    layer = point_layer(qgis_app)
    layer_props.write_sync_state(layer, 'https://s.test', 7, 880, 'vector', 'e1')
    facts = layer_export.collect_layer_facts(layer)
    assert facts.dataset_id == 880 and facts.kind == 'vector'
    assert facts.server_base == 'https://s.test' and facts.sync_etag == 'e1'


def test_load_pulled_dataset_new_and_replace(qgis_app, tmp_path):
    dest = str(tmp_path / 'pulled.gpkg')
    layer_export.export_vector_gpkg(point_layer(qgis_app), dest)
    entry = ManifestEntry(id=880, name='pulled', kind='vector', sync_etag='e2',
                          style={'symbology_type': 'single',
                                 'style_color': '#cc3333', 'style_width': 2})
    loaded = layer_load.load_pulled_dataset(entry, dest, 'https://s.test', 7)
    assert loaded.isValid()
    assert layer_load.find_layer_by_dataset(880) is loaded
    state = layer_props.read_sync_state(loaded)
    assert state['sync_etag'] == 'e2'
    # second pull replaces in place, no duplicate layer
    again = layer_load.load_pulled_dataset(entry, dest, 'https://s.test', 7)
    assert again is loaded
    QgsProject.instance().removeAllMapLayers()
