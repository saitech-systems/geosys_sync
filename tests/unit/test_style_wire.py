from geosys_sync.core import style_wire as sw


def test_normalize_hex_accepts_and_lowercases():
    assert sw.normalize_hex('#CC3333') == '#cc3333'


def test_normalize_hex_rejects_garbage():
    assert sw.normalize_hex('red') == sw.DEFAULT_COLOR
    assert sw.normalize_hex(None, '#000000') == '#000000'
    assert sw.normalize_hex('#abcd', None) is None


def test_rgb_to_hex():
    assert sw.rgb_to_hex(204, 51, 51) == '#cc3333'


def test_mm_px_roundtrip():
    assert sw.mm_to_px(0.26) == 1          # QGIS default hairline
    assert sw.mm_to_px(100) == 20          # clamped high
    assert sw.mm_to_px('junk') == 2        # fallback
    assert 0.5 < sw.px_to_mm(2) < 0.6      # 2px ~ 0.53mm


def test_dominant_color_weighted():
    weighted = [('#ff0000', 1.0), ('#00ff00', 2.0), ('#FF0000', 1.5)]
    assert sw.dominant_color(weighted) == '#ff0000'  # 2.5 beats 2.0


def test_dominant_color_empty_falls_back():
    assert sw.dominant_color([]) == sw.DEFAULT_COLOR
    assert sw.dominant_color([('junk', 5)]) == sw.DEFAULT_COLOR


def test_category_key_null_convention():
    assert sw.category_key(None) == '(null)'
    assert sw.category_key('') == '(null)'
    assert sw.category_key(5) == '5'
    assert sw.category_key('urban') == 'urban'
