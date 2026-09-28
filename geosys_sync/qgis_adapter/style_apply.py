"""Wire style JSON -> QGIS renderer + labels (pull direction)."""
from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsMapLayer, QgsPalettedRasterRenderer,
    QgsPalLayerSettings, QgsRendererCategory, QgsSingleSymbolRenderer,
    QgsSymbol, QgsTextFormat, QgsVectorLayerSimpleLabeling,
)
from qgis.PyQt.QtGui import QColor

from geosys_sync.core import style_wire


def apply_wire(layer, wire):
    wire = wire or {}
    if layer.type() == QgsMapLayer.LayerType.RasterLayer:
        _apply_raster(layer, wire)
        return

    if (wire.get('symbology_type') == 'categorized'
            and wire.get('symbology_attribute')
            and isinstance(wire.get('symbology_categories'), dict)):
        layer.setRenderer(_categorized_renderer(layer, wire))
    else:
        layer.setRenderer(_single_renderer(layer, wire))
    _apply_labels(layer, wire)
    layer.triggerRepaint()


def _apply_raster(layer, wire):
    """Paletted classes (if any) plus the whole-layer alpha.

    A style with no paletted classes leaves the renderer alone: the platform
    stores no other raster symbology, so replacing whatever QGIS picked for the
    file (greyscale stretch, multiband RGB) would lose information rather than
    apply any.
    """
    renderer = _paletted_renderer(layer, style_wire.paletted_classes(wire))
    if renderer is not None:
        layer.setRenderer(renderer)
    opacity = wire.get('raster_opacity')
    if opacity is not None:
        layer.setOpacity(max(0.0, min(1.0, float(opacity))))
    layer.triggerRepaint()


def _paletted_renderer(layer, classes):
    provider = layer.dataProvider()
    if not classes or provider is None or provider.bandCount() < 1:
        return None
    entries = []
    for cls in classes:
        color = QColor(cls['color'])
        if not cls['visible']:
            # Hidden means transparent, not absent: the class keeps its colour
            # and label so the user can switch it back on.
            color.setAlpha(0)
        entries.append(QgsPalettedRasterRenderer.Class(
            cls['value'], color, cls['label']))
    # Band 1 always: 'pixel_value' means the raw value of band 1 and the wire
    # carries no band number.
    return QgsPalettedRasterRenderer(provider, 1, entries)


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
