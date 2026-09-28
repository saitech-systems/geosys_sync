# GeosysAI Sync - QGIS Plugin

Licensed under the GNU General Public License v2.0 or later (`geosys_sync/LICENSE`), as the official QGIS plugin repository requires.

Sync vector and raster layers between QGIS and a GeosysAI project.
Pull server datasets into QGIS (vector GeoPackage, raster COG GeoTIFF) with their styling.
Push QGIS layers back, creating new datasets or overwriting previously synced ones (overwrite-by-identity via the `geosys/dataset_id` layer custom property).

Backend contract: `/api/qgis/v1/*`, implemented and specified in the `sisl-geo-server`
repository (`routes/qgis_sync.py` + `services/qgis_sync.py`).
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
The same zip is what gets uploaded to plugins.qgis.org; it carries the plugin folder at its root with `metadata.txt`, `LICENSE` and `icon.svg` inside it and no compiled files.
For a dev loop, symlink `geosys_sync/` into the active profile's `python/plugins/` and use the Plugin Reloader plugin.

## Notes

- With "Stay logged in" checked, the refresh token is stored in the QGIS authentication database (`QgsAuthManager`, encrypted under the master password), never in plain settings.
  QGIS asks for the master password the first time a session is remembered; dismissing that prompt means the session is not remembered and the dialog says so.
  A plain login never opens the authentication database, so it never prompts.
  A token that a version before 0.4.0 left in plain `QgsSettings` is moved into the authentication database on the next start and the plaintext copy is deleted either way.
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

On the wire this is `symbology_type: 'categorized'` with the sentinel `symbology_attribute: 'pixel_value'`, mapping each pixel value to a `{color, label, visible}` object - as opposed to a vector, which names a real field and maps each value to a bare hex string.
`tests/unit/test_style_wire.py` and `tests/qgis/test_raster_style_roundtrip.py` are the executable form of that contract.

## Style-only sync

**Upload style only**, on the push tab, sends the checked layers' symbology and labels and nothing else.
No data moves, so restyling a multi-gigabyte raster costs one small request rather than a full re-upload.

It applies to vectors and rasters alike, and needs a layer that is already synced to the current project - the server writes the style onto an existing dataset, so a layer that has never been uploaded reports that it must be uploaded first.
Ownership rules are the same as for an overwrite.

## Layer projections

A layer is only pushed once QGIS knows where it sits.
The upload carries an integer EPSG code, so before anything is exported the plugin checks every layer you have checked on the push tab and opens the QGIS projection picker for any that cannot supply one.

Two cases reach that prompt.
A layer with no coordinate system at all is the common one - a shapefile with no `.prj`, a delimited-text layer added without a CRS.
The other is a layer whose projection QGIS understands but cannot name as an EPSG code: a custom or raw-WKT CRS on a hand-built VRT, or a CRS from another authority such as `ESRI:102008`, `IGNF:LAMB93` or `OGC:CRS84`.
QGIS draws all of those correctly, but the wire carries an integer EPSG code and none of them can be expressed as one, so they are reported separately rather than uploaded as `null` or under a local id that means something different off this machine.

Picking a coordinate system sets it on the QGIS layer, exactly as Layer Properties would, and the file on disk is left alone.
It re-labels the layer rather than reprojecting it: the coordinates do not move, only their declared meaning changes, so pick the coordinate system the data is already in.
Both push pipelines already write in the layer's declared CRS, so that is enough for the upload to be correct.
The picker opens with nothing selected on purpose, since a pre-filled guess accepted by reflex would upload mislabeled geometry that no later check can catch.
Cancelling the picker skips that one layer and reports it; the rest of the push continues.

This applies to the data push only.
Pulled layers keep whatever coordinate system their downloaded file declares, and a style-only push moves no data and so never asks.

## Languages

The plugin speaks English, French, Spanish, German and Dutch, and follows the language QGIS itself runs in (Settings > Options > General), so it never differs from the menus around it.
Every user-facing string goes through `tr()` in `geosys_sync/core/i18n.py`; the English text is the key, and `geosys_sync/i18n/<code>.json` holds the other four languages.
`tests/unit/test_i18n.py` reads the source and fails on any string missing from any language, on a stale entry, or on a translation whose placeholders differ from its key, so a new string cannot ship untranslated.
Text that comes from the server (error messages, dataset names) is shown as sent.
