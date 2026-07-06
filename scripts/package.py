"""Build the installable plugin zip: dist/geosys_sync-<version>.zip."""
import configparser
import os
import sys
import zipfile

_EXCLUDE_DIRS = {'__pycache__'}
_EXCLUDE_EXTS = {'.pyc', '.pyo'}


def read_version(metadata_path):
    parser = configparser.ConfigParser()
    parser.read(metadata_path, encoding='utf-8')
    return parser['general']['version']


def build_zip(plugin_dir, out_dir):
    package_dir = os.path.join(plugin_dir, 'geosys_sync')
    version = read_version(os.path.join(package_dir, 'metadata.txt'))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'geosys_sync-{}.zip'.format(version))
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(package_dir):
            dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
            for fname in files:
                if os.path.splitext(fname)[1] in _EXCLUDE_EXTS:
                    continue
                full = os.path.join(root, fname)
                arc = os.path.join(
                    'geosys_sync', os.path.relpath(full, package_dir))
                zf.write(full, arc.replace(os.sep, '/'))
    return out_path


if __name__ == '__main__':
    plugin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(plugin_dir, 'dist')
    print(build_zip(plugin_dir, dest))
