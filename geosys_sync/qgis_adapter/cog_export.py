"""Local COG + hillshade production for raster push.

Port of the desktop app's src/main/convert/raster.ts. The server validates
what we upload (tiled GeoTIFF, CRS equal to the declared EPSG, bounds within
1%) and refuses a single-band raster with no hillshade, so the steps and their
order match the desktop profile exactly. GDAL and numpy both ship with QGIS,
so nothing here needs bundling.

Deliberate plugin-specific hardening over desktop parity: the tagged copy and
the hillshade are ALSO assigned the declared EPSG (`outputSRS=`), not just the
COG. The desktop app can skip this because its epsg always comes from probing
the file itself, so the file's embedded CRS and the declared one can never
disagree. This plugin's epsg comes from the QGIS layer's CRS instead, which a
user can override in Layer Properties without touching the file - the same
divergence the plugin's own CRS-confirmation dialog exists to catch. Do not
"restore" desktop parity here; a maintainer diffing against raster.ts should
read this as intentional, not as drift.

Exceptions are scoped to this module's own GDAL calls via `gdal.ExceptionMgr`
rather than flipped globally with `gdal.UseExceptions()`: this runs inside the
shared QGIS interpreter, and a process-wide flag would change error handling
for every other installed plugin's GDAL calls too.
"""
import os
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from osgeo import gdal, osr

from geosys_sync.core import raster_profile

_COG_OPTIONS = ['COMPRESS=DEFLATE', 'BIGTIFF=YES']
_HILLSHADE_TMP_OPTIONS = ['COMPRESS=LZW', 'TILED=YES']
_HILLSHADE_COG_OPTIONS = ['COMPRESS=DEFLATE', 'BIGTIFF=IF_SAFER']


@dataclass
class RasterArtifacts:
    """Everything one raster push needs to upload and register."""
    original_path: str
    cog_path: str
    hillshade_path: Optional[str]
    band_count: int
    dtype: str
    cog_min: Optional[float]
    cog_max: Optional[float]
    bounds_3857: List[float]


def _report(progress, pct):
    if progress:
        progress(pct)


def build_raster_artifacts(source_path, epsg, work_dir, progress=None):
    """Convert `source_path` into the artifacts the server expects.

    Everything except the untouched original is written under `work_dir`,
    which the caller owns and deletes. `progress` is called with 0-100.
    """
    with gdal.ExceptionMgr(useExceptions=True):
        epsg = int(epsg)
        stem = os.path.splitext(os.path.basename(source_path))[0]
        _report(progress, 0)

        ds = gdal.Open(source_path, gdal.GA_ReadOnly)
        if ds is None:
            raise RuntimeError('GDAL could not open {}'.format(source_path))
        band_count = ds.RasterCount
        band = ds.GetRasterBand(1)
        dtype = gdal.GetDataTypeName(band.DataType)
        nodata = band.GetNoDataValue()
        geotransform = ds.GetGeoTransform()
        width, height = ds.RasterXSize, ds.RasterYSize
        ds = None

        # 1. NoData sentinel. An untagged single-band raster would have its
        # collar read as real data by the hillshade and the stretch, so tag a
        # copy and upload THAT as the original: the stored original must
        # declare the same NoData as everything derived from it. Also assigns
        # the declared EPSG: the user's declared value is authoritative over
        # whatever the file happens to carry (see the module docstring).
        src = source_path
        if band_count == 1 and nodata is None:
            tagged = os.path.join(work_dir, os.path.basename(source_path))
            gdal.Translate(tagged, src,
                           noData=raster_profile.sentinel_for(dtype),
                           outputSRS='EPSG:{}'.format(epsg))
            src = tagged
        _report(progress, 20)

        # 2. COG
        cog_path = os.path.join(work_dir, '{}_COG.tif'.format(stem))
        gdal.Translate(cog_path, src, format='COG',
                       creationOptions=_COG_OPTIONS,
                       outputSRS='EPSG:{}'.format(epsg))
        _report(progress, 60)

        # 3. Hillshade pair, single-band only. Built from the tagged source
        # rather than the COG, matching the desktop. Mandatory: the server
        # refuses a single-band registration without it. The final COG
        # translate also assigns the declared EPSG, same reasoning as the
        # tagged copy above - gdaldem carries whatever SRS the source declares
        # forward, which is only guaranteed to match the declared epsg when
        # step 1 above ran.
        hillshade_path = None
        if band_count == 1:
            hs_tmp = os.path.join(work_dir, 'hs_tmp.tif')
            hillshade_path = os.path.join(work_dir,
                                          '{}_HILLSHADE.tif'.format(stem))
            gdal.DEMProcessing(hs_tmp, src, 'hillshade', zFactor=1.0,
                               azimuth=315.0, altitude=45.0,
                               computeEdges=True, format='GTiff',
                               creationOptions=_HILLSHADE_TMP_OPTIONS)
            gdal.Translate(hillshade_path, hs_tmp, format='COG',
                           creationOptions=_HILLSHADE_COG_OPTIONS,
                           outputSRS='EPSG:{}'.format(epsg))
        _report(progress, 80)

        # 4. Display stretch, single-band only, measured on the finished COG.
        cog_min = cog_max = None
        if band_count == 1:
            cog_min, cog_max = _display_stretch(cog_path)
        _report(progress, 90)

        bounds_3857 = _bounds_3857(epsg, geotransform, width, height)
        _report(progress, 100)

        return RasterArtifacts(original_path=src, cog_path=cog_path,
                               hillshade_path=hillshade_path,
                               band_count=band_count, dtype=dtype,
                               cog_min=cog_min, cog_max=cog_max,
                               bounds_3857=bounds_3857)


def _display_stretch(cog_path):
    """2-98 percentile cut over a decimated read of band 1."""
    with gdal.ExceptionMgr(useExceptions=True):
        ds = gdal.Open(cog_path, gdal.GA_ReadOnly)
        band = ds.GetRasterBand(1)
        w, h = raster_profile.decimated_shape(ds.RasterXSize, ds.RasterYSize)
        values = band.ReadAsArray(buf_xsize=w,
                                  buf_ysize=h).astype('float64').ravel()
        nodata = band.GetNoDataValue()
        ds = None
        keep = np.isfinite(values)
        if nodata is not None:
            keep &= values != nodata
        values = np.sort(values[keep])
        if values.size == 0:
            return None, None
        return raster_profile.stretch_from_sorted(values)


def _bounds_3857(epsg, geotransform, width, height):
    origin_x, pixel_w, _, origin_y, _, pixel_h = geotransform
    if epsg == 3857:
        return raster_profile.raw_bounds(origin_x, origin_y, pixel_w, pixel_h,
                                         width, height)
    src = osr.SpatialReference()
    src.ImportFromEPSG(epsg)
    dst = osr.SpatialReference()
    dst.ImportFromEPSG(3857)
    # GDAL 3 follows each CRS's authority axis order unless told otherwise; a
    # geographic source would otherwise be handed lat/lon and land in the
    # wrong place entirely.
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(src, dst)
    points = raster_profile.densified_edge_points(
        origin_x, origin_y, pixel_w, pixel_h, width, height)
    return raster_profile.bounds_from_points(
        [transform.TransformPoint(x, y)[:2] for x, y in points])
