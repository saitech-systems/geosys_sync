import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from package import build_zip, read_version  # noqa: E402

from geosys_sync.core.api_client import PLUGIN_VERSION  # noqa: E402

PLUGIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..'))


def test_read_version_matches_the_version_sent_to_the_server():
    # Asserted against PLUGIN_VERSION, not a literal: the invariant that
    # matters is that the packaged version and the X-QGIS-Plugin-Version
    # header agree, and pinning a literal here only forces an edit per bump.
    v = read_version(os.path.join(PLUGIN_DIR, 'geosys_sync', 'metadata.txt'))
    assert re.match(r'^\d+\.\d+\.\d+$', v)
    assert v == PLUGIN_VERSION


def test_build_zip(tmp_path):
    out = build_zip(PLUGIN_DIR, str(tmp_path))
    assert os.path.basename(out) == 'geosys_sync-{}.zip'.format(PLUGIN_VERSION)
    names = zipfile.ZipFile(out).namelist()
    assert 'geosys_sync/metadata.txt' in names
    assert 'geosys_sync/__init__.py' in names
    # plugins.qgis.org rejects an upload without these two.
    assert 'geosys_sync/LICENSE' in names
    assert 'geosys_sync/icon.svg' in names
    assert not any(n.endswith('.pyc') or '__pycache__' in n for n in names)


def test_metadata_names_the_shipped_icon_and_a_license():
    import configparser
    parser = configparser.ConfigParser()
    parser.read(os.path.join(PLUGIN_DIR, 'geosys_sync', 'metadata.txt'),
                encoding='utf-8')
    g = parser['general']
    assert os.path.isfile(os.path.join(PLUGIN_DIR, 'geosys_sync', g['icon']))
    assert g['license'].startswith('GPL')
    for key in ('name', 'qgisMinimumVersion', 'description', 'about',
                'version', 'author', 'email', 'repository', 'tracker',
                'homepage', 'changelog'):
        assert g[key].strip(), key
