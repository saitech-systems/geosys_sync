from geosys_sync.core.models import (
    CogUrl, ManifestEntry, Project, SessionInfo, TokenBundle,
)

LOGIN_PAYLOAD = {
    'access_token': 'acc', 'refresh_token': 'ref', 'expires_in': 900,
    'token_type': 'Bearer',
    'user': {'id': 42, 'username': 'yash', 'email': 'y@x.com'},
    'org': {'id': 3, 'name': 'SAITECH', 'enable_qgis_sync': True},
    'capabilities': {'can_create_project': True, 'can_upload_vector': True,
                     'can_upload_raster': False},
}

VECTOR_ENTRY = {
    'id': 880, 'name': 'roads', 'kind': 'vector', 'geometry_type': 'LineString',
    'feature_count': 5123, 'epsg': 25832, 'bbox_4326': [6.1, 47.3, 6.4, 47.5],
    'updated_at': '2026-06-20T09:00:00Z', 'sync_etag': '9f2a1c7b4e0d8a31',
    'can_overwrite': True,
    'style': {'symbology_type': 'single', 'style_color': '#cc3333'},
    'download': {'endpoint': '/api/qgis/v1/datasets/880/download'},
}


def test_token_bundle_from_json():
    tb = TokenBundle.from_json(LOGIN_PAYLOAD)
    assert (tb.access_token, tb.refresh_token, tb.expires_in) == ('acc', 'ref', 900)


def test_session_info_capabilities():
    info = SessionInfo.from_json(LOGIN_PAYLOAD)
    assert info.user['username'] == 'yash'
    assert info.can_upload('vector') is True
    assert info.can_upload('raster') is False


def test_project_from_json_minimal():
    p = Project.from_json({'id': 7, 'name': 'P', 'role': 'owner', 'is_owner': True,
                           'epsg_code': None, 'effective_epsg_code': 3857})
    assert p.id == 7 and p.is_owner and p.effective_epsg_code == 3857
    assert p.effective_epsg_def is None


def test_manifest_entry_vector():
    e = ManifestEntry.from_json(VECTOR_ENTRY)
    assert e.kind == 'vector'
    assert e.download_endpoint.endswith('/880/download')
    assert e.style['style_color'] == '#cc3333'
    assert e.cog_status is None
    assert e.style_warnings == []


def test_manifest_entry_raster_with_warnings():
    e = ManifestEntry.from_json({
        'id': 881, 'name': 'dsm', 'kind': 'raster', 'epsg': 25832,
        'band_count': 1, 'cog_status': 'ready', 'sync_etag': '1bd3',
        'can_overwrite': False, 'style': {'raster_opacity': 0.8},
        'download': {'endpoint': '/api/qgis/v1/datasets/881/cog-url'},
        'style_warnings': ['downgraded'],
    })
    assert e.cog_status == 'ready' and e.band_count == 1
    assert e.style_warnings == ['downgraded']


def test_cog_url_from_json():
    c = CogUrl.from_json({'url': 'https://s3/x.tif', 'expires_in': 21600,
                          'variant': 'greyscale', 'epsg': 25832, 'band_count': 1,
                          'bbox_native': [1, 2, 3, 4], 'cog_min': 1.0,
                          'cog_max': 2.0, 'sync_etag': 'ee'})
    assert c.url.startswith('https://') and c.sync_etag == 'ee'
