import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from package import build_zip, read_version  # noqa: E402

PLUGIN_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..'))


def test_read_version():
    v = read_version(os.path.join(PLUGIN_DIR, 'geosys_sync', 'metadata.txt'))
    assert v == '0.2.0'


def test_build_zip(tmp_path):
    out = build_zip(PLUGIN_DIR, str(tmp_path))
    assert os.path.basename(out) == 'geosys_sync-0.2.0.zip'
    names = zipfile.ZipFile(out).namelist()
    assert 'geosys_sync/metadata.txt' in names
    assert 'geosys_sync/__init__.py' in names
    assert not any(n.endswith('.pyc') or '__pycache__' in n for n in names)
