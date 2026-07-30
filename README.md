# GeosysAI Sync - QGIS Plugin

Sync vector and raster layers between QGIS and a GeosysAI project.
Pull server datasets into QGIS (vector GeoPackage, raster COG GeoTIFF) with their styling.
Push QGIS layers back, creating new datasets or overwriting previously synced ones (overwrite-by-identity via the `geosys/dataset_id` layer custom property).

Backend contract: `/api/qgis/v1/*`, implemented in the `sisl-geo-server` repository
(`routes/qgis_sync.py` + `services/qgis_sync.py`). The contract itself is specified in
[`docs/2026-06-27-qgis-sync-plugin-design.md`](docs/2026-06-27-qgis-sync-plugin-design.md).
The server must run with `QGIS_SYNC_API_ENABLED=true` and the user's organization must have `enable_qgis_sync` switched on.

## Layout

- `geosys_sync/core/` - pure Python: API client, wire models, sync planning. No QGIS imports.
- `geosys_sync/qgis_adapter/` - QGIS-touching adapters: custom properties, style mapping, GeoPackage export, layer loading, settings.
- `geosys_sync/ui/` + `plugin.py` - Qt dialogs and the QGIS entry point.

## Development

Run everything from the repository root:

```
pip install -r requirements-dev.txt
pytest                          # pure-python suite (qgis tests auto-skip)
scripts\run_qgis_tests.bat      # qgis-marked tests inside QGIS python
```

## Install into QGIS

```
python scripts/package.py
```

Then in QGIS: Plugins > Manage and Install Plugins > Install from ZIP > `dist/geosys_sync-<version>.zip`.
For a dev loop, symlink `geosys_sync/` into the active profile's `python/plugins/` and use the Plugin Reloader plugin.

## Notes

- The refresh token is stored in QgsSettings when "Stay logged in" is checked (plaintext; QgsAuthManager migration is a known follow-up).
- Server URLs must be `https://`; plain `http://` is only accepted for localhost dev servers, and the same rule applies to download URLs the server hands back.
- Accounts with MFA enabled are prompted for their authenticator/email code after the password (`/auth/mfa-verify` challenge flow, mirroring the desktop API).
- `label_min_zoom` round-trips but is not converted to a QGIS scale-visibility rule (matches spec section 7.4).

## Raster push

Rasters are converted on your machine, not on the server.
The plugin writes a Cloud Optimized GeoTIFF (and, for single-band rasters, a hillshade), uploads them straight to object storage over presigned URLs, and then registers the dataset on the platform.
Nothing but metadata passes through the GeosysAI application servers.

Against a server that does not yet support this, the plugin falls back to uploading the raw GeoTIFF for server-side conversion.

## Raster symbology

A single-band raster's classes sync both ways as the QGIS **Paletted / Unique values** renderer.
Each class carries its pixel value, colour, label, and visibility; a class switched off on either side comes back with its colour and label intact, drawn transparent rather than dropped.
The layer's global opacity syncs alongside it, independently of per-class visibility.

Any other raster renderer pushes opacity only and leaves the dataset's symbology on the platform untouched, because there is nowhere to store a colour ramp or a band combination.
That includes **Singleband pseudocolor**: pushing one reports a note saying the symbology was not uploaded, rather than silently discarding classes the platform may already hold.
The wire contract behind this is [`docs/qgis-plugin-paletted-raster-styles.md`](docs/qgis-plugin-paletted-raster-styles.md).
