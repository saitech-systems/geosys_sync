"""QGIS renderer -> wire style JSON (push direction)."""
from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer, QgsMapLayer,
    QgsRuleBasedRenderer, QgsSingleSymbolRenderer,
)

from geosys_sync.core import style_wire


def extract_wire(layer):
    """Return (wire_dict, warnings) for a QGIS map layer."""
    if layer.type() == QgsMapLayer.RasterLayer:
        return {'raster_opacity': round(layer.opacity(), 3)}, []

    warnings = []
    renderer = layer.renderer()
    if isinstance(renderer, QgsSingleSymbolRenderer):
        wire = _from_single(renderer)
    elif isinstance(renderer, QgsCategorizedSymbolRenderer):
        wire = _from_categorized(renderer)
    elif isinstance(renderer, QgsGraduatedSymbolRenderer):
        weighted = [(r.symbol().color().name(), 1.0) for r in renderer.ranges()
                    if r.symbol()]
        warnings.append('Graduated renderer downgraded to a single color.')
        wire = {'symbology_type': 'single',
                'style_color': style_wire.dominant_color(weighted),
                'style_width': 2}
    elif isinstance(renderer, QgsRuleBasedRenderer):
        weighted = [(r.symbol().color().name(), 1.0)
                    for r in renderer.rootRule().children() if r.symbol()]
        warnings.append('Rule-based renderer downgraded to a single color.')
        wire = {'symbology_type': 'single',
                'style_color': style_wire.dominant_color(weighted),
                'style_width': 2}
    else:
        warnings.append('{} downgraded to a single color.'.format(
            type(renderer).__name__))
        wire = {'symbology_type': 'single',
                'style_color': style_wire.DEFAULT_COLOR, 'style_width': 2}

    wire.update(_extract_labels(layer))
    if warnings:
        wire['style_warnings'] = list(warnings)
    return wire, warnings


def _symbol_width_px(symbol):
    width_fn = getattr(symbol, 'width', None)  # line symbols only
    if callable(width_fn):
        return style_wire.mm_to_px(width_fn())
    return 2


def _from_single(renderer):
    symbol = renderer.symbol()
    return {'symbology_type': 'single',
            'style_color': style_wire.normalize_hex(symbol.color().name()),
            'style_width': _symbol_width_px(symbol)}


def _from_categorized(renderer):
    categories = {}
    for cat in renderer.categories():
        key = style_wire.category_key(cat.value())
        categories[key] = style_wire.normalize_hex(
            cat.symbol().color().name(), style_wire.DEFAULT_CATEGORY_COLOR)
    return {'symbology_type': 'categorized',
            'symbology_attribute': renderer.classAttribute(),
            'symbology_categories': categories}


def _extract_labels(layer):
    if not (layer.labelsEnabled() and layer.labeling()):
        return {'label_enabled': False}
    settings = layer.labeling().settings()
    fmt = settings.format()
    return {'label_enabled': True,
            'label_attribute': settings.fieldName,
            'label_color': style_wire.normalize_hex(fmt.color().name(), '#ffffff'),
            'label_font_size': int(fmt.size())}
