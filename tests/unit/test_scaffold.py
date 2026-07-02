def test_core_package_imports_without_qgis():
    import geosys_sync.core  # noqa: F401


def test_class_factory_is_lazy():
    # __init__ must not import qgis at module import time
    import geosys_sync
    assert callable(geosys_sync.classFactory)
