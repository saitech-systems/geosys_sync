"""Pure helpers for the wire style JSON. No QGIS imports.

Mirrors the server-side validation in utils/qgis_style_map.py: colors are
lowercase #rrggbb, stroke widths are ints 1..20 (platform px). QGIS symbol
widths are millimetres; mm_to_px/px_to_mm convert at 96 dpi.
"""
import re

NULL_CATEGORY_KEY = '(null)'
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
