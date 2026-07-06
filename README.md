# GeosysAI Sync - QGIS Plugin

Sync vector and raster layers between QGIS and a GeosysAI project.
Pull server datasets into QGIS (vector GeoPackage, raster COG GeoTIFF) with their styling.
Push QGIS layers back, creating new datasets or overwriting previously synced ones (overwrite-by-identity via the `geosys/dataset_id` layer custom property).

Backend contract: `/api/qgis/v1/*` (see `docs/superpowers/specs/2026-06-27-qgis-sync-plugin-design.md`).
The server must run with `QGIS_SYNC_API_ENABLED=true` and the user's organization must have `enable_qgis_sync` switched on.

## Layout

- `geosys_sync/core/` - pure Python: API client, wire models, sync planning. No QGIS imports.
- `geosys_sync/qgis_adapter/` - QGIS-touching adapters: custom properties, style mapping, GeoPackage export, layer loading, settings.
- `geosys_sync/ui/` + `plugin.py` - Qt dialogs and the QGIS entry point.

## Development

```
pip install -r plugin/requirements-dev.txt
pytest plugin/tests            # pure-python suite (qgis tests auto-skip)
plugin\scripts\run_qgis_tests.bat   # qgis-marked tests inside QGIS python
```

## Install into QGIS

```
python plugin/scripts/package.py
```

Then in QGIS: Plugins > Manage and Install Plugins > Install from ZIP > `plugin/dist/geosys_sync-<version>.zip`.
For a dev loop, symlink `plugin/geosys_sync` into the active profile's `python/plugins/` and use the Plugin Reloader plugin.

## Notes

- The refresh token is stored in QgsSettings when "Stay logged in" is checked (plaintext; QgsAuthManager migration is a known follow-up).
- Raster push sends the layer's source GeoTIFF; the server converts to COG asynchronously (the dialog notes "server is converting").
- `label_min_zoom` round-trips but is not converted to a QGIS scale-visibility rule (matches spec section 7.4).
