"""QGIS renderer -> wire style JSON (push direction)."""
from qgis.core import (
    QgsCategorizedSymbolRenderer, QgsGraduatedSymbolRenderer, QgsMapLayer,
    QgsPalettedRasterRenderer, QgsRuleBasedRenderer,
    QgsSingleBandPseudoColorRenderer, QgsSingleSymbolRenderer,
)

from geosys_sync.core.i18n import tr
from geosys_sync.core import style_wire


def extract_wire(layer):
    """Return (wire_dict, warnings) for a QGIS map layer."""
    if layer.type() == QgsMapLayer.RasterLayer:
        return _from_raster(layer)

    warnings = []
    renderer = layer.renderer()
    if isinstance(renderer, QgsSingleSymbolRenderer):
        wire = _from_single(renderer)
    elif isinstance(renderer, QgsCategorizedSymbolRenderer):
        wire = _from_categorized(renderer)
    elif isinstance(renderer, QgsGraduatedSymbolRenderer):
        weighted = [(r.symbol().color().name(), 1.0) for r in renderer.ranges()
                    if r.symbol()]
        warnings.append(tr('Graduated renderer downgraded to a single color.'))
        wire = {'symbology_type': 'single',
                'style_color': style_wire.dominant_color(weighted),
                'style_width': 2}
    elif isinstance(renderer, QgsRuleBasedRenderer):
        weighted = [(r.symbol().color().name(), 1.0)
                    for r in renderer.rootRule().children() if r.symbol()]
        warnings.append(tr('Rule-based renderer downgraded to a single color.'))
        wire = {'symbology_type': 'single',
                'style_color': style_wire.dominant_color(weighted),
                'style_width': 2}
    else:
        warnings.append(tr('{} downgraded to a single color.').format(
            type(renderer).__name__))
        wire = {'symbology_type': 'single',
                'style_color': style_wire.DEFAULT_COLOR, 'style_width': 2}

    wire.update(_extract_labels(layer))
    if warnings:
        wire['style_warnings'] = list(warnings)
    return wire, warnings


def _paletted_band(renderer):
    """The band a paletted renderer reads.

    inputBand() replaced the deprecated band() in QGIS 3.38; the plugin
    supports 3.28, so prefer the new name and fall back to the old one.
    """
    input_band = getattr(renderer, 'inputBand', None)
    return input_band() if callable(input_band) else renderer.band()


def _from_raster(layer):
    """Raster styles carry a whole-layer alpha, plus paletted classes if the
    layer has the one renderer the platform can store.

    Every other raster renderer sends opacity only, deliberately leaving the
    dataset's symbology on the server untouched: there is nowhere to put a
    colour ramp or a band combination, and pushing a vector-shaped
    "normalisation" of it would silently destroy paletted classes that are
    already there.
    """
    wire = {'raster_opacity': round(layer.opacity(), 3)}
    renderer = layer.renderer()
    warnings = []
    if isinstance(renderer, QgsPalettedRasterRenderer):
        # classes() flattens a multi-value class into one entry per value,
        # sharing its colour and label - exactly the per-value shape the wire
        # wants. Round-tripping such a class comes back unmerged, which renders
        # identically; the platform has no concept of merged classes.
        fields, warnings = style_wire.paletted_wire(
            {'value': cls.value,
             'color': style_wire.normalize_hex(
                 cls.color.name(), style_wire.DEFAULT_CATEGORY_COLOR),
             'label': cls.label,
             # QColor.name() drops alpha, so read visibility from the colour
             # itself: apply_wire encodes a hidden class as alpha 0.
             'visible': cls.color.alpha() > 0}
            for cls in renderer.classes())
        # Only send classes if there are any. The server writes whatever dict
        # it is handed, so an empty one would clear the dataset's classes, and
        # a renderer with nothing in it is not a request to do that.
        if fields['symbology_categories']:
            wire.update(fields)
            band = _paletted_band(renderer)
            if band != 1:
                warnings.append(tr(
                    'Paletted renderer reads band {}; the platform categorises band 1, so the classes may not match its pixel values.'
                ).format(band))
    elif isinstance(renderer, QgsSingleBandPseudoColorRenderer):
        # Worth naming: a user who ramps a DEM and pushes it would otherwise
        # see no symbology change on the platform and no reason why.
        warnings.append(tr(
            'Singleband pseudocolor symbology is not supported by the platform and was not uploaded. Use Paletted / Unique values to sync raster classes.'))
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
