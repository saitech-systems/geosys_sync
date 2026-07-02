import os
import sys

import pytest

# Make `import geosys_sync` work: plugin/ is the package parent.
_PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

try:
    import qgis  # noqa: F401
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False


def pytest_collection_modifyitems(config, items):
    if HAS_QGIS:
        return
    skip = pytest.mark.skip(
        reason='qgis not importable; run plugin/scripts/run_qgis_tests.bat')
    for item in items:
        if 'qgis' in item.keywords:
            item.add_marker(skip)
