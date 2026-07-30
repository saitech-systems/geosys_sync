import math

import pytest

from geosys_sync.core import raster_profile as rp


def test_sentinel_matches_the_desktop_profile():
    assert rp.sentinel_for('Float32') == -9999.0
    assert rp.sentinel_for('Float64') == -9999.0
    assert rp.sentinel_for('UInt16') == 0
    assert rp.sentinel_for('Byte') == 0
    assert rp.sentinel_for('Int16') == -9999


def test_percentile_uses_linear_interpolation():
    values = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert rp.percentile(values, 0) == 0.0
    assert rp.percentile(values, 100) == 4.0
    assert rp.percentile(values, 50) == 2.0
    assert rp.percentile(values, 25) == 1.0
    assert rp.percentile(values, 10) == pytest.approx(0.4)


def test_percentile_rejects_empty():
    with pytest.raises(ValueError):
        rp.percentile([], 50)


def test_stretch_is_a_2_98_cut():
    values = [float(i) for i in range(101)]
    lo, hi = rp.stretch_from_sorted(values)
    assert lo == pytest.approx(2.0)
    assert hi == pytest.approx(98.0)


def test_stretch_falls_back_to_min_max_when_the_cut_is_degenerate():
    values = [1.0] * 99 + [7.0]
    lo, hi = rp.stretch_from_sorted(values)
    assert (lo, hi) == (1.0, 7.0)


def test_stretch_falls_back_again_on_a_constant_band():
    lo, hi = rp.stretch_from_sorted([3.0] * 50)
    assert (lo, hi) == (3.0, 4.0)


def test_decimated_shape_passes_small_rasters_through():
    assert rp.decimated_shape(100, 100) == (100, 100)


def test_decimated_shape_caps_samples_and_keeps_aspect():
    w, h = rp.decimated_shape(40000, 10000, cap=4_000_000)
    assert w * h <= 4_000_000
    assert w == 4000 and h == 1000


def test_decimated_shape_never_returns_zero():
    w, h = rp.decimated_shape(10_000_000, 1, cap=100)
    assert w >= 1 and h >= 1


def test_densified_edge_points_walks_all_four_edges():
    pts = rp.densified_edge_points(0.0, 100.0, 1.0, -1.0, 10, 10,
                                   points_per_edge=3)
    assert len(pts) == 12
    assert (0.0, 100.0) in pts     # origin corner
    assert (10.0, 90.0) in pts     # far corner


def test_bounds_from_points_skips_non_finite():
    pts = [(0.0, 0.0), (10.0, 4.0), (float('inf'), 1.0),
           (float('nan'), 2.0), (-3.0, -1.0)]
    assert rp.bounds_from_points(pts) == [-3.0, -1.0, 10.0, 4.0]


def test_bounds_from_points_rejects_an_empty_result():
    with pytest.raises(ValueError):
        rp.bounds_from_points([(float('nan'), float('nan'))])


def test_raw_bounds_normalises_a_north_up_geotransform():
    # pixel_h is negative for a north-up raster: the origin is the TOP-left.
    assert rp.raw_bounds(500.0, 900.0, 2.0, -2.0, 100, 50) == \
        [500.0, 800.0, 700.0, 900.0]
