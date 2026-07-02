"""Wire style JSON -> QGIS renderer + labels (pull direction)."""
from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsMapLayer, QgsPalLayerSettings,
    QgsRendererCategory, QgsSingleSymbolRenderer, QgsSymbol, QgsTextFormat,
    QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from geosys_sync.core import style_wire


def apply_wire(layer, wire):
    wire = wire or {}
    if layer.type() == QgsMapLayer.RasterLayer:
        opacity = wire.get('raster_opacity')
        if opacity is not None:
            layer.setOpacity(max(0.0, min(1.0, float(opacity))))
        layer.triggerRepaint()
        return

    if (wire.get('symbology_type') == 'categorized'
            and wire.get('symbology_attribute')
            and isinstance(wire.get('symbology_categories'), dict)):
        layer.setRenderer(_categorized_renderer(layer, wire))
    else:
        layer.setRenderer(_single_renderer(layer, wire))
    _apply_labels(layer, wire)
    layer.triggerRepaint()


def _single_renderer(layer, wire):
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    symbol.setColor(QColor(style_wire.normalize_hex(wire.get('style_color'))))
    if hasattr(symbol, 'setWidth') and wire.get('style_width') is not None:
        symbol.setWidth(style_wire.px_to_mm(wire['style_width']))
    return QgsSingleSymbolRenderer(symbol)


def _categorized_renderer(layer, wire):
    categories = []
    for key, hex_color in wire['symbology_categories'].items():
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(QColor(style_wire.normalize_hex(
            hex_color, style_wire.DEFAULT_CATEGORY_COLOR)))
        value = '' if key == style_wire.NULL_CATEGORY_KEY else key
        categories.append(QgsRendererCategory(value, symbol, key))
    return QgsCategorizedSymbolRenderer(wire['symbology_attribute'], categories)


def _apply_labels(layer, wire):
    if not wire.get('label_enabled'):
        layer.setLabelsEnabled(False)
        return
    settings = QgsPalLayerSettings()
    settings.fieldName = wire.get('label_attribute') or ''
    fmt = QgsTextFormat()
    fmt.setColor(QColor(style_wire.normalize_hex(wire.get('label_color'),
                                                 '#ffffff')))
    fmt.setSize(int(wire.get('label_font_size') or 12))
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
