import os

import pytest

pytestmark = pytest.mark.qgis

from qgis.core import (  # noqa: E402
    QgsCoordinateReferenceSystem, QgsFeature, QgsGeometry, QgsPointXY,
    QgsProject, QgsVectorLayer,
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


# A CRS QGIS can draw perfectly well but which has no authority code.
CUSTOM_PROJ = ('+proj=tmerc +lat_0=0 +lon_0=9.123456 +k=1 +x_0=1234567 '
               '+y_0=0 +ellps=bessel +units=m +no_defs')


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


def test_load_pulled_dataset_replace_bad_file_raises(qgis_app, tmp_path):
    dest = str(tmp_path / 'pulled.gpkg')
    layer_export.export_vector_gpkg(point_layer(qgis_app), dest)
    entry = ManifestEntry(id=881, name='pulled', kind='vector', sync_etag='e3')
    loaded = layer_load.load_pulled_dataset(entry, dest, 'https://s.test', 7)
    assert loaded.isValid()
    try:
        # re-pull pointing at a nonexistent file must fail loudly, not leave
        # the layer silently invalid with fresh sync props
        with pytest.raises(RuntimeError):
            layer_load.load_pulled_dataset(
                entry, str(tmp_path / 'missing.gpkg'), 'https://s.test', 7)
    finally:
        QgsProject.instance().removeAllMapLayers()


def test_layer_crs_problem_none_for_a_normal_epsg_layer(qgis_app):
    assert layer_export.layer_crs_problem(point_layer(qgis_app)) is None


def test_layer_crs_problem_reports_a_missing_crs(qgis_app):
    layer = point_layer(qgis_app)
    layer.setCrs(QgsCoordinateReferenceSystem())
    assert layer_export.layer_crs_problem(layer) == (
        'layer has no coordinate reference system')


def test_layer_crs_problem_reports_an_authority_less_crs(qgis_app):
    # QGIS knows exactly where this data sits, but there is no EPSG code to
    # put on the wire, so it is unpushable for a different reason.
    layer = point_layer(qgis_app)
    layer.setCrs(QgsCoordinateReferenceSystem.fromProj(CUSTOM_PROJ))
    assert layer.crs().isValid()      # guard: the proj string really parsed
    problem = layer_export.layer_crs_problem(layer)
    assert problem is not None and 'no EPSG code' in problem


def test_crs_problem_rejects_a_non_epsg_authority(qgis_app):
    # QGIS hands back a non-zero srs.db srid for ESRI/IGNF/OGC codes too, so
    # anything gating on that number would wave 102008 onto the wire as if it
    # were an EPSG code.
    crs = QgsCoordinateReferenceSystem('ESRI:102008')
    assert crs.isValid()              # guard: the authority really resolved
    assert layer_export.crs_epsg(crs) is None
    problem = layer_export.crs_problem(crs)
    assert problem is not None and 'no EPSG code' in problem
