"""Arithmetic of the raster conversion profile. No GDAL, no QGIS.

Ports the parts of the desktop app's src/main/convert/raster.ts and
convert/stretch.ts that are maths rather than GDAL calls, so both clients
compute the same numbers and so these can be tested without a QGIS runtime.
The GDAL steps live in geosys_sync/qgis_adapter/cog_export.py.
"""
import math

STRETCH_SAMPLE_CAP = 4_000_000
EDGE_POINTS_PER_EDGE = 21


def sentinel_for(dtype):
    """NoData sentinel to stamp on an untagged single-band raster."""
    d = (dtype or '').lower()
    if d.startswith('float'):
        return -9999.0
    if d.startswith('uint') or d == 'byte':
        return 0
    return -9999


def percentile(sorted_values, q):
    """numpy-default (linear interpolation) percentile over a SORTED sequence.

    Sorting and NoData filtering happen in the caller, which has numpy; this
    stays O(1) so it costs nothing on a multi-million-sample band.
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError('percentile of an empty sequence')
    rank = (n - 1) * q / 100.0
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(sorted_values[lo])
    low = float(sorted_values[lo])
    return low + (float(sorted_values[hi]) - low) * (rank - lo)


def stretch_from_sorted(sorted_values):
    """Profile display_stretch: a 2-98 cut with the documented fallbacks."""
    lo = percentile(sorted_values, 2)
    hi = percentile(sorted_values, 98)
    if hi <= lo:  # near-constant band: fall back to the full range
        lo = float(sorted_values[0])
        hi = float(sorted_values[-1])
    if hi <= lo:  # genuinely constant: keep the range non-empty
        hi = lo + 1.0
    return lo, hi


def decimated_shape(width, height, cap=STRETCH_SAMPLE_CAP):
    """Sample shape for the stretch read: at most `cap` pixels, aspect kept."""
    if width * height <= cap:
        return width, height
    scale = math.sqrt(cap / float(width * height))
    return (max(1, int(math.floor(width * scale))),
            max(1, int(math.floor(height * scale))))


def _corners(origin_x, origin_y, pixel_w, pixel_h, width, height):
    return (origin_x, origin_y,
            origin_x + pixel_w * width, origin_y + pixel_h * height)


def densified_edge_points(origin_x, origin_y, pixel_w, pixel_h, width, height,
                          points_per_edge=EDGE_POINTS_PER_EDGE):
    """Points along all four edges of the raster, in its own CRS.

    Corners alone are not enough: a projected edge bows, so transforming only
    the four corners understates the reprojected extent.
    """
    x0, y0, x1, y1 = _corners(origin_x, origin_y, pixel_w, pixel_h,
                              width, height)
    out = []
    for i in range(points_per_edge):
        t = i / float(points_per_edge - 1)
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        out.append((x, y0))
        out.append((x, y1))
        out.append((x0, y))
        out.append((x1, y))
    return out


def bounds_from_points(points):
    """[minx, miny, maxx, maxy] over `points`, ignoring non-finite ones."""
    xs = [float(x) for x, y in points
          if math.isfinite(x) and math.isfinite(y)]
    ys = [float(y) for x, y in points
          if math.isfinite(x) and math.isfinite(y)]
    if not xs:
        raise ValueError('no finite points to bound')
    return [min(xs), min(ys), max(xs), max(ys)]


def raw_bounds(origin_x, origin_y, pixel_w, pixel_h, width, height):
    """Axis-ordered extent straight from the geotransform (no reprojection)."""
    x0, y0, x1, y1 = _corners(origin_x, origin_y, pixel_w, pixel_h,
                              width, height)
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
