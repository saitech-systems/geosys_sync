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


# -- paletted rasters --------------------------------------------------------
# symbology_type 'categorized' has two payload shapes on the wire. A vector
# names a real field in symbology_attribute and maps each value to a bare hex
# string; a raster uses the sentinel 'pixel_value' and maps each pixel value to
# a {color, label, visible} object. See
# docs/qgis-plugin-paletted-raster-styles.md.

PALETTED = {
    'symbology_type': 'categorized',
    'symbology_attribute': 'pixel_value',
    'symbology_categories': {
        '2': {'color': '#2E7D32', 'label': 'Vegetation', 'visible': True},
        '1': {'color': '#1565c0', 'label': 'Water', 'visible': True},
        '3': {'color': '#8d6e63', 'label': 'Built-up', 'visible': False},
    },
    'raster_opacity': 0.8,
}


def test_pixel_value_key_drops_the_point_for_whole_numbers():
    # The platform compares the key against the raw band value, so '3.0'
    # would match nothing.
    assert sw.pixel_value_key(3.0) == '3'
    assert sw.pixel_value_key(3) == '3'
    assert sw.pixel_value_key(0.0) == '0'
    assert sw.pixel_value_key(-1.0) == '-1'


def test_pixel_value_key_keeps_the_point_for_fractions():
    assert sw.pixel_value_key(2.5) == '2.5'
    assert sw.pixel_value_key(-0.25) == '-0.25'


def test_pixel_value_key_rejects_what_cannot_be_a_key():
    assert sw.pixel_value_key(float('nan')) is None
    assert sw.pixel_value_key(float('inf')) is None
    assert sw.pixel_value_key('urban') is None
    assert sw.pixel_value_key(None) is None


def test_parse_pixel_value_accepts_both_spellings():
    assert sw.parse_pixel_value('3') == 3.0
    assert sw.parse_pixel_value('3.0') == 3.0
    assert sw.parse_pixel_value(' 2.5 ') == 2.5
    assert sw.parse_pixel_value('-1') == -1.0


def test_parse_pixel_value_rejects_non_numeric_keys():
    assert sw.parse_pixel_value('urban') is None
    assert sw.parse_pixel_value('') is None
    assert sw.parse_pixel_value(None) is None
    assert sw.parse_pixel_value('nan') is None
    assert sw.parse_pixel_value('inf') is None


def test_paletted_classes_sorted_and_normalized():
    classes = sw.paletted_classes(PALETTED)
    assert [c['value'] for c in classes] == [1.0, 2.0, 3.0]
    assert classes[1]['color'] == '#2e7d32'
    assert classes[1]['label'] == 'Vegetation'
    assert classes[0]['visible'] is True
    assert classes[2]['visible'] is False


def test_paletted_classes_reads_a_degraded_bare_string_entry():
    # The server's _clean_category degrades a malformed object to '#cccccc',
    # so the bare-string shape can legitimately come back on a raster.
    wire = dict(PALETTED, symbology_categories={'4': '#FF0000'})
    assert sw.paletted_classes(wire) == [
        {'value': 4.0, 'color': '#ff0000', 'label': '', 'visible': True}]


def test_paletted_classes_ignores_vector_categorized():
    wire = {'symbology_type': 'categorized', 'symbology_attribute': 'land_use',
            'symbology_categories': {'urban': '#ff0000', '1': '#00ff00'}}
    assert sw.paletted_classes(wire) == []


def test_paletted_classes_ignores_everything_that_is_not_paletted():
    assert sw.paletted_classes(None) == []
    assert sw.paletted_classes({}) == []
    assert sw.paletted_classes({'symbology_type': 'single'}) == []
    assert sw.paletted_classes({'raster_opacity': 0.5}) == []
    assert sw.paletted_classes(dict(PALETTED, symbology_categories=[])) == []
    assert sw.paletted_classes(dict(PALETTED, symbology_categories=None)) == []


def test_paletted_classes_skips_bad_keys_and_falls_back_on_bad_colors():
    wire = dict(PALETTED, symbology_categories={
        'urban': {'color': '#ff0000', 'label': 'nope', 'visible': True},
        '5': {'color': 'red', 'label': '', 'visible': True}})
    classes = sw.paletted_classes(wire)
    assert len(classes) == 1
    assert classes[0]['value'] == 5.0
    assert classes[0]['color'] == sw.DEFAULT_CATEGORY_COLOR


def test_paletted_wire_emits_the_object_shape():
    fields, warnings = sw.paletted_wire([
        {'value': 1.0, 'color': '#1565C0', 'label': 'Water', 'visible': True},
        {'value': 3.0, 'color': '#8d6e63', 'label': 'Built-up',
         'visible': False}])
    assert warnings == []
    assert fields == {
        'symbology_type': 'categorized',
        'symbology_attribute': 'pixel_value',
        'symbology_categories': {
            '1': {'color': '#1565c0', 'label': 'Water', 'visible': True},
            '3': {'color': '#8d6e63', 'label': 'Built-up', 'visible': False}}}


def test_paletted_wire_drops_unrepresentable_values_with_a_warning():
    fields, warnings = sw.paletted_wire([
        {'value': float('nan'), 'color': '#ff0000', 'label': 'NoData',
         'visible': True},
        {'value': 2.5, 'color': '#00ff00', 'label': '', 'visible': True}])
    assert list(fields['symbology_categories']) == ['2.5']
    assert len(warnings) == 1 and 'not uploaded' in warnings[0]


def test_paletted_round_trip_preserves_labels_and_visibility():
    # The failure this guards: normalizing a pulled raster style back to bare
    # hex strings destroys the user's labels and per-class visibility, and the
    # server accepts it silently.
    fields, warnings = sw.paletted_wire(sw.paletted_classes(PALETTED))
    assert warnings == []
    assert fields['symbology_attribute'] == 'pixel_value'
    assert fields['symbology_categories'] == {
        '1': {'color': '#1565c0', 'label': 'Water', 'visible': True},
        '2': {'color': '#2e7d32', 'label': 'Vegetation', 'visible': True},
        '3': {'color': '#8d6e63', 'label': 'Built-up', 'visible': False}}
