import pytest

pytestmark = pytest.mark.qgis

from qgis.core import (  # noqa: E402
    QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer,
    QgsRendererCategory, QgsRendererRange, QgsSingleSymbolRenderer,
    QgsSymbol, QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402

from geosys_sync.qgis_adapter import style_apply, style_extract  # noqa: E402


def memory_layer(qgis_app, geom='LineString'):
    layer = QgsVectorLayer(
        '{}?crs=EPSG:4326&field=land_use:string'.format(geom), 'test', 'memory')
    assert layer.isValid()
    return layer


def test_single_symbol_roundtrip(qgis_app):
    layer = memory_layer(qgis_app)
    style_apply.apply_wire(layer, {'symbology_type': 'single',
                                   'style_color': '#cc3333', 'style_width': 4,
                                   'label_enabled': False})
    wire, warnings = style_extract.extract_wire(layer)
    assert warnings == []
    assert wire['symbology_type'] == 'single'
    assert wire['style_color'] == '#cc3333'
    assert wire['style_width'] == 4


def test_categorized_roundtrip_with_null_key(qgis_app):
    layer = memory_layer(qgis_app, geom='Polygon')
    style_apply.apply_wire(layer, {
        'symbology_type': 'categorized', 'symbology_attribute': 'land_use',
        'symbology_categories': {'urban': '#ff0000', 'rural': '#00aa00',
                                 '(null)': '#cccccc'}})
    wire, warnings = style_extract.extract_wire(layer)
    assert warnings == []
    assert wire['symbology_attribute'] == 'land_use'
    assert wire['symbology_categories'] == {'urban': '#ff0000',
                                            'rural': '#00aa00',
                                            '(null)': '#cccccc'}


def test_labels_roundtrip(qgis_app):
    layer = memory_layer(qgis_app)
    style_apply.apply_wire(layer, {'symbology_type': 'single',
                                   'style_color': '#cc3333', 'style_width': 2,
                                   'label_enabled': True,
                                   'label_attribute': 'land_use',
                                   'label_color': '#112233',
                                   'label_font_size': 14})
    wire, _ = style_extract.extract_wire(layer)
    assert wire['label_enabled'] is True
    assert wire['label_attribute'] == 'land_use'
    assert wire['label_color'] == '#112233'
    assert wire['label_font_size'] == 14


def test_graduated_downgrades_with_warning(qgis_app):
    layer = memory_layer(qgis_app)
    ranges = []
    for lo, hi, color in ((0, 1, '#111111'), (1, 2, '#222222')):
        sym = QgsSymbol.defaultSymbol(layer.geometryType())
        sym.setColor(QColor(color))
        ranges.append(QgsRendererRange(lo, hi, sym, '{}-{}'.format(lo, hi)))
    layer.setRenderer(QgsGraduatedSymbolRenderer('land_use', ranges))
    wire, warnings = style_extract.extract_wire(layer)
    assert wire['symbology_type'] == 'single'
    assert wire['style_color'] in ('#111111', '#222222')
    assert warnings and 'downgraded' in warnings[0].lower()
    assert wire['style_warnings'] == warnings
