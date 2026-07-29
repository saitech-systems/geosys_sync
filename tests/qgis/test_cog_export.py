import pytest

pytestmark = pytest.mark.qgis

from osgeo import gdal, osr   # noqa: E402

from geosys_sync.qgis_adapter import cog_export   # noqa: E402


def _write_tif(path, bands=1, width=64, height=48, epsg=25832, nodata=None):
    drv = gdal.GetDriverByName('GTiff')
    ds = drv.Create(str(path), width, height, bands, gdal.GDT_Float32)
    ds.SetGeoTransform((400000.0, 10.0, 0.0, 5600000.0, 0.0, -10.0))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    for b in range(1, bands + 1):
        band = ds.GetRasterBand(b)
        if nodata is not None:
            band.SetNoDataValue(nodata)
        band.WriteArray(
            __import__('numpy').fromfunction(
                lambda y, x: (x + y * width) % 500, (height, width)))
    ds.FlushCache()
    ds = None
    return str(path)


def test_single_band_produces_cog_hillshade_and_stretch(tmp_path):
    src = _write_tif(tmp_path / 'dsm.tif')
    work = tmp_path / 'work'
    work.mkdir()
    seen = []
    art = cog_export.build_raster_artifacts(src, 25832, str(work),
                                            progress=seen.append)

    assert art.band_count == 1
    assert art.dtype == 'Float32'
    assert art.hillshade_path is not None
    assert art.cog_min is not None and art.cog_max > art.cog_min
    assert seen and seen[0] == 0 and seen[-1] == 100

    ds = gdal.Open(art.cog_path)
    assert ds.GetDriver().ShortName == 'GTiff'
    assert ds.GetRasterBand(1).GetBlockSize()[1] != ds.RasterYSize  # tiled
    assert osr.SpatialReference(ds.GetProjection()).GetAuthorityCode(None) \
        == '25832'
    ds = None


def test_untagged_single_band_gets_a_tagged_original(tmp_path):
    src = _write_tif(tmp_path / 'dsm.tif', nodata=None)
    work = tmp_path / 'work'
    work.mkdir()
    art = cog_export.build_raster_artifacts(src, 25832, str(work))
    # The uploaded original is the tagged copy, not the user's file.
    assert art.original_path != src
    assert gdal.Open(art.original_path).GetRasterBand(1).GetNoDataValue() \
        == -9999.0


def test_tagged_source_is_uploaded_unchanged(tmp_path):
    src = _write_tif(tmp_path / 'dsm.tif', nodata=-32768.0)
    work = tmp_path / 'work'
    work.mkdir()
    art = cog_export.build_raster_artifacts(src, 25832, str(work))
    assert art.original_path == src


def test_multiband_skips_hillshade_and_stretch(tmp_path):
    src = _write_tif(tmp_path / 'ortho.tif', bands=3)
    work = tmp_path / 'work'
    work.mkdir()
    art = cog_export.build_raster_artifacts(src, 25832, str(work))
    assert art.band_count == 3
    assert art.hillshade_path is None
    assert art.cog_min is None and art.cog_max is None
    assert art.original_path == src


def test_bounds_3857_agree_with_a_direct_transform(tmp_path):
    src = _write_tif(tmp_path / 'dsm.tif')
    work = tmp_path / 'work'
    work.mkdir()
    art = cog_export.build_raster_artifacts(src, 25832, str(work))

    s = osr.SpatialReference(); s.ImportFromEPSG(25832)
    d = osr.SpatialReference(); d.ImportFromEPSG(3857)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    d.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr.CoordinateTransformation(s, d)
    # All FOUR corners, not the anti-diagonal pair. Meridian convergence
    # shears the rectangle: about 100km west of UTM32N's central meridian the
    # left edge drifts roughly 14m east as you walk south, so the true minimum
    # x is the TOP-left corner. Sampling only two corners understates the
    # reprojected extent by more than the server's own 1% tolerance, which
    # would make a correct implementation look wrong.
    x0, y0 = 400000.0, 5600000.0 - 480.0
    x1, y1 = 400000.0 + 640.0, 5600000.0
    pts = [tr.TransformPoint(x, y)[:2]
           for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    # A tenth of the server's 1% tolerance. The residual gap is the edge bow
    # between corners, which is sub-millimetre at this extent.
    assert abs(art.bounds_3857[0] - min(xs)) < span * 0.001
    assert abs(art.bounds_3857[3] - max(ys)) < span * 0.001


def test_already_3857_skips_the_transform(tmp_path):
    src = _write_tif(tmp_path / 'web.tif', epsg=3857)
    work = tmp_path / 'work'
    work.mkdir()
    art = cog_export.build_raster_artifacts(src, 3857, str(work))
    assert art.bounds_3857 == [400000.0, 5600000.0 - 480.0,
                               400000.0 + 640.0, 5600000.0]
