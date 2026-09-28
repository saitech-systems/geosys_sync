"""Pure helpers for the wire style JSON. No QGIS imports.

Mirrors the server-side validation in utils/qgis_style_map.py: colors are
lowercase #rrggbb, stroke widths are ints 1..20 (platform px). QGIS symbol
widths are millimetres; mm_to_px/px_to_mm convert at 96 dpi.
"""
import math
import re

from geosys_sync.core.i18n import tr

NULL_CATEGORY_KEY = '(null)'
# Sentinel that symbology_attribute carries for a paletted raster: "categorised
# by the raw value of band 1". It is not a field name and must never be
# resolved against a layer's fields.
PIXEL_VALUE_ATTRIBUTE = 'pixel_value'
DEFAULT_COLOR = '#3388ff'
DEFAULT_CATEGORY_COLOR = '#cccccc'
_HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
_MM_TO_PX = 96.0 / 25.4


def normalize_hex(value, fallback=DEFAULT_COLOR):
    if isinstance(value, str) and _HEX_RE.match(value.strip()):
        return value.strip().lower()
    return fallback


def rgb_to_hex(r, g, b):
    return '#{:02x}{:02x}{:02x}'.format(int(r) & 255, int(g) & 255, int(b) & 255)


def mm_to_px(width_mm, fallback=2):
    try:
        return max(1, min(20, round(float(width_mm) * _MM_TO_PX)))
    except (TypeError, ValueError):
        return fallback


def px_to_mm(width_px):
    try:
        return max(0.1, float(width_px) / _MM_TO_PX)
    except (TypeError, ValueError):
        return 0.5


def dominant_color(weighted, fallback=DEFAULT_COLOR):
    """weighted: iterable of (hex_color, weight). Returns the color with the
    largest summed weight; invalid colors are dropped."""
    totals = {}
    for color, weight in weighted or []:
        h = normalize_hex(color, None)
        if h is None:
            continue
        totals[h] = totals.get(h, 0.0) + float(weight)
    if not totals:
        return fallback
    return max(totals.items(), key=lambda kv: kv[1])[0]


def category_key(value):
    """The server's '(null)' convention for the NULL/else category."""
    if value is None or value == '':
        return NULL_CATEGORY_KEY
    return str(value)


# -- paletted rasters --------------------------------------------------------
# 'categorized' has two payload shapes on the wire, sharing the same three
# columns. A vector names a real field in symbology_attribute and maps each
# value to a bare hex string. A raster uses the PIXEL_VALUE_ATTRIBUTE sentinel
# and maps each pixel value to a {color, label, visible} object - the
# platform's equivalent of the QGIS Paletted/Unique values renderer.
#
# Both directions here are lossless on the object shape. That matters: the
# server accepts bare strings on these columns whatever the layer kind, so a
# client that pulls a paletted raster and pushes back a vector-shaped
# "normalisation" destroys the user's labels and per-class visibility with no
# error and nothing to indicate what happened.


def pixel_value_key(value):
    """The wire key for one paletted class value, or None if it cannot be one.

    Whole numbers lose the decimal point ('3', never '3.0'), because the
    platform's renderer compares the key against the raw band value and '3.0'
    matches nothing. Fractional values keep it ('2.5').
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return str(int(number))
    return str(number)


def parse_pixel_value(key):
    """A wire key back to a float band value, or None if it is not numeric.

    Accepts both spellings a key can arrive in ('3' and '3.0').
    """
    try:
        number = float(str(key).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _paletted_class(value, entry):
    if isinstance(entry, dict):
        return {'value': value,
                'color': normalize_hex(entry.get('color'),
                                       DEFAULT_CATEGORY_COLOR),
                'label': str(entry.get('label') or ''),
                # Only an explicit false hides a class, matching the server's
                # _clean_category. Hidden means transparent, not dropped.
                'visible': entry.get('visible') is not False}
    # A bare string is what the server degrades a malformed object entry to, so
    # it can legitimately come back on a raster. Read it the way the platform's
    # own consumers do: colour only, no label, visible.
    return {'value': value,
            'color': normalize_hex(entry, DEFAULT_CATEGORY_COLOR),
            'label': '', 'visible': True}


def paletted_classes(wire):
    """A wire style's paletted raster classes, sorted by pixel value.

    Returns [] for anything that is not a paletted raster style - a vector
    categorized style, a plain raster style, junk - so callers can fall through
    to their own default handling.
    """
    if not isinstance(wire, dict):
        return []
    if wire.get('symbology_type') != 'categorized':
        return []
    if wire.get('symbology_attribute') != PIXEL_VALUE_ATTRIBUTE:
        return []
    categories = wire.get('symbology_categories')
    if not isinstance(categories, dict):
        return []
    classes = []
    for key, entry in categories.items():
        value = parse_pixel_value(key)
        if value is None:
            continue  # not a pixel value; the platform could not match it
        classes.append(_paletted_class(value, entry))
    classes.sort(key=lambda c: c['value'])
    return classes


def paletted_wire(classes):
    """Build the symbology wire fields for a paletted raster.

    classes: iterable of {value, color, label, visible} dicts.
    Returns (wire_fields, warnings). raster_opacity is the caller's business -
    it is a whole-layer alpha, independent of per-class visibility.
    """
    categories = {}
    dropped = 0
    for cls in classes:
        key = pixel_value_key(cls.get('value'))
        if key is None:
            dropped += 1
            continue
        categories[key] = {
            'color': normalize_hex(cls.get('color'), DEFAULT_CATEGORY_COLOR),
            'label': str(cls.get('label') or ''),
            'visible': bool(cls.get('visible', True))}
    warnings = []
    if dropped:
        warnings.append(tr('{} raster class(es) without a finite pixel value were not uploaded.')
                        .format(dropped))
    return {'symbology_type': 'categorized',
            'symbology_attribute': PIXEL_VALUE_ATTRIBUTE,
            'symbology_categories': categories}, warnings
