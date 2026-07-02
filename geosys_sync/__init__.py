"""GeosysAI Sync - QGIS plugin entry point."""


def classFactory(iface):
    """QGIS plugin factory. Imports lazily so unit tests can import the
    package without a QGIS runtime."""
    from geosys_sync.plugin import GeosysSyncPlugin
    return GeosysSyncPlugin(iface)
