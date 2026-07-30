"""Paletted raster symbology, end to end through the QGIS renderer.

The contract these guard is docs/qgis-plugin-paletted-raster-styles.md: a
paletted raster style must survive pull -> QGIS -> push with its class labels
and per-class visibility intact, and a raster whose renderer the platform
cannot store must not push symbology at all.
"""
import pytest

pytestmark = pytest.mark.qgis

import numpy  # noqa: E402
from osgeo import gdal, osr  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsColorRampShader, QgsPalettedRasterRenderer, QgsRasterLayer,
    QgsRasterShader, QgsSingleBandGrayRenderer,
    QgsSingleBandPseudoColorRenderer,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402

from geosys_sync.qgis_adapter import style_apply, style_extract  # noqa: E402

PALETTED_WIRE = {
    'symbology_type': 'categorized',
    'symbology_attribute': 'pixel_value',
    'symbology_categories': {
        '1': {'color': '#1565c0', 'label': 'Water', 'visible': True},
        '2': {'color': '#2e7d32', 'label': 'Vegetation', 'visible': True},
        '3': {'color': '#8d6e63', 'label': 'Built-up', 'visible': False},
    },
    'raster_opacity': 0.8,
}


def classified_layer(qgis_app, tmp_path, bands=1, name='classes.tif'):
    """A small single-band classified raster on disk, opened as a layer."""
    path = str(tmp_path / name)
    ds = gdal.GetDriverByName('GTiff').Create(path, 8, 8, bands, gdal.GDT_Byte)
    ds.SetGeoTransform((400000.0, 10.0, 0.0, 5600000.0, 0.0, -10.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(25832)
    ds.SetProjection(srs.ExportToWkt())
    for b in range(1, bands + 1):
        ds.GetRasterBand(b).WriteArray(
            numpy.fromfunction(lambda y, x: (x % 3) + 1, (8, 8)))
    ds.FlushCache()
    ds = None
    layer = QgsRasterLayer(path, 'classes', 'gdal')
    assert layer.isValid()
    return layer


def test_paletted_wire_builds_a_paletted_renderer(qgis_app, tmp_path):
    layer = classified_layer(qgis_app, tmp_path)
    style_apply.apply_wire(layer, PALETTED_WIRE)

    renderer = layer.renderer()
    assert isinstance(renderer, QgsPalettedRasterRenderer)
    assert renderer.usesBands() == [1]   # 'pixel_value' means band 1
    classes = sorted(renderer.classes(), key=lambda c: c.value)
    assert [c.value for c in classes] == [1.0, 2.0, 3.0]
    assert [c.label for c in classes] == ['Water', 'Vegetation', 'Built-up']
    assert classes[0].color.name() == '#1565c0'
    assert round(layer.opacity(), 3) == 0.8


def test_hidden_class_becomes_transparent_not_dropped(qgis_app, tmp_path):
    layer = classified_layer(qgis_app, tmp_path)
    style_apply.apply_wire(layer, PALETTED_WIRE)

    by_value = {c.value: c for c in layer.renderer().classes()}
    assert by_value[3.0].color.alpha() == 0          # rendered transparent
    assert by_value[3.0].color.name() == '#8d6e63'   # colour kept
    assert by_value[3.0].label == 'Built-up'         # label kept
    assert by_value[1.0].color.alpha() == 255


def test_paletted_roundtrip_keeps_labels_and_visibility(qgis_app, tmp_path):
    # The regression this locks down: a pull-then-push that normalises the
    # object entries to bare hex strings. The server accepts that silently and
    # the user's labels and per-class visibility are gone for good.
    layer = classified_layer(qgis_app, tmp_path)
    style_apply.apply_wire(layer, PALETTED_WIRE)

    wire, warnings = style_extract.extract_wire(layer)

    assert warnings == []
    assert wire['symbology_type'] == 'categorized'
    assert wire['symbology_attribute'] == 'pixel_value'
    assert (wire['symbology_categories']
            == PALETTED_WIRE['symbology_categories'])
    assert wire['raster_opacity'] == 0.8


def test_whole_number_keys_carry_no_decimal_point(qgis_app, tmp_path):
    # QGIS class values are floats; a '3.0' key matches nothing on the
    # platform, whose renderer compares the key to the raw band value.
    layer = classified_layer(qgis_app, tmp_path)
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, [
        QgsPalettedRasterRenderer.Class(2.0, QColor('#ff0000'), 'Two'),
        QgsPalettedRasterRenderer.Class(2.5, QColor('#00ff00'), 'Half'),
    ]))

    wire, _ = style_extract.extract_wire(layer)

    assert sorted(wire['symbology_categories']) == ['2', '2.5']


def test_an_empty_paletted_renderer_does_not_wipe_the_platforms_classes(
        qgis_app, tmp_path):
    # The server writes whatever dict it is handed, so pushing {} would clear
    # the dataset's classes. A renderer with nothing in it is not a request
    # to do that.
    layer = classified_layer(qgis_app, tmp_path)
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, []))

    wire, warnings = style_extract.extract_wire(layer)

    assert wire == {'raster_opacity': 1.0}
    assert warnings == []


def test_a_greyscale_raster_pushes_opacity_only(qgis_app, tmp_path):
    # Nothing for the platform to store, so its symbology must be left alone.
    layer = classified_layer(qgis_app, tmp_path)
    layer.setRenderer(QgsSingleBandGrayRenderer(layer.dataProvider(), 1))
    layer.setOpacity(0.5)

    wire, warnings = style_extract.extract_wire(layer)

    assert wire == {'raster_opacity': 0.5}
    assert warnings == []


def test_pseudocolor_warns_and_pushes_no_symbology(qgis_app, tmp_path):
    layer = classified_layer(qgis_app, tmp_path)
    ramp = QgsColorRampShader(1, 3)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(1, QColor('#000000'), '1'),
        QgsColorRampShader.ColorRampItem(3, QColor('#ffffff'), '3')])
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    layer.setRenderer(
        QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader))

    wire, warnings = style_extract.extract_wire(layer)

    assert 'symbology_categories' not in wire
    assert 'symbology_type' not in wire
    assert warnings and 'pseudocolor' in warnings[0].lower()
    assert wire['style_warnings'] == warnings


def test_paletted_on_another_band_warns(qgis_app, tmp_path):
    layer = classified_layer(qgis_app, tmp_path, bands=3, name='multi.tif')
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 2, [
        QgsPalettedRasterRenderer.Class(1.0, QColor('#1565c0'), 'Water')]))

    wire, warnings = style_extract.extract_wire(layer)

    # Still uploaded - dropping it would silently discard the user's classes.
    assert wire['symbology_categories'] == {
        '1': {'color': '#1565c0', 'label': 'Water', 'visible': True}}
    assert warnings and 'band 2' in warnings[0]


def test_a_vector_style_never_reaches_a_raster_renderer(qgis_app, tmp_path):
    # 'pixel_value' is a sentinel, not a field: a vector categorized style
    # arriving on a raster must not be resolved against anything.
    layer = classified_layer(qgis_app, tmp_path)
    layer.setRenderer(QgsSingleBandGrayRenderer(layer.dataProvider(), 1))
    style_apply.apply_wire(layer, {
        'symbology_type': 'categorized', 'symbology_attribute': 'land_use',
        'symbology_categories': {'urban': '#ff0000'}, 'raster_opacity': 0.9})

    assert isinstance(layer.renderer(), QgsSingleBandGrayRenderer)
    assert round(layer.opacity(), 3) == 0.9
