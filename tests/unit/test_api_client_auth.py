import pytest

from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import ApiError, AuthRequiredError, NetworkError
from geosys_sync.core.models import TokenBundle

BASE = 'https://server.test'
API = BASE + '/api/qgis/v1'

LOGIN_OK = {
    'access_token': 'acc1', 'refresh_token': 'ref1', 'expires_in': 900,
    'token_type': 'Bearer',
    'user': {'id': 42, 'username': 'yash'},
    'org': {'id': 3, 'name': 'SAITECH', 'enable_qgis_sync': True},
    'capabilities': {'can_create_project': True, 'can_upload_vector': True,
                     'can_upload_raster': False},
}
REFRESH_OK = {'access_token': 'acc2', 'refresh_token': 'ref2',
              'expires_in': 900, 'token_type': 'Bearer'}


def make_client(**kw):
    return GeosysClient(BASE, device_id='qgis-testdevice1', **kw)


def test_login_posts_device_and_stores_tokens(requests_mock):
    requests_mock.post(API + '/auth/login', json=LOGIN_OK)
    changes = []
    c = make_client(on_tokens_changed=changes.append)
    info = c.login('yash', 'pw')
    assert c.tokens.access_token == 'acc1'
    assert info.can_upload('vector') is True
    body = requests_mock.last_request.json()
    assert body['username'] == 'yash'
    assert body['device_id'] == 'qgis-testdevice1'
    assert changes and changes[0].refresh_token == 'ref1'


def test_login_email(requests_mock):
    requests_mock.post(API + '/auth/login', json=LOGIN_OK)
    make_client().login('y@x.com', 'pw')
    body = requests_mock.last_request.json()
    assert body.get('email') == 'y@x.com'
    assert 'username' not in body


def test_login_bad_credentials_raises(requests_mock):
    requests_mock.post(API + '/auth/login', status_code=401, json={
        'error': {'code': 'AUTH_INVALID_CREDENTIALS', 'message': 'bad'}})
    with pytest.raises(ApiError) as exc:
        make_client().login('yash', 'wrong')
    assert exc.value.code == 'AUTH_INVALID_CREDENTIALS'


def test_expired_access_token_refreshes_once_and_retries(requests_mock):
    c = make_client(tokens=TokenBundle('old', 'ref1'))
    requests_mock.get(API + '/auth/me', [
        {'status_code': 401,
         'json': {'error': {'code': 'TOKEN_EXPIRED', 'message': 'x'}}},
        {'json': LOGIN_OK},
    ])
    requests_mock.post(API + '/auth/refresh', json=REFRESH_OK)
    info = c.me()
    assert info.user['id'] == 42
    assert c.tokens.access_token == 'acc2'
    # second /auth/me call carried the refreshed token
    assert requests_mock.request_history[-1].headers['Authorization'] == 'Bearer acc2'


def test_revoked_refresh_forces_relogin(requests_mock):
    c = make_client(tokens=TokenBundle('old', 'refX'))
    requests_mock.get(API + '/auth/me', status_code=401, json={
        'error': {'code': 'TOKEN_EXPIRED', 'message': 'x'}})
    requests_mock.post(API + '/auth/refresh', status_code=401, json={
        'error': {'code': 'TOKEN_REVOKED', 'message': 'x'}})
    with pytest.raises(AuthRequiredError):
        c.me()


def test_request_without_tokens_raises_auth_required():
    with pytest.raises(AuthRequiredError):
        make_client().me()


def test_network_failure_wrapped(requests_mock):
    import requests as _requests
    requests_mock.post(API + '/auth/login', exc=_requests.ConnectTimeout)
    with pytest.raises(NetworkError):
        make_client().login('yash', 'pw')


def test_logout_revokes_and_clears(requests_mock):
    requests_mock.post(API + '/auth/logout', status_code=204)
    changes = []
    c = make_client(tokens=TokenBundle('acc', 'ref1'), on_tokens_changed=changes.append)
    c.logout()
    assert c.tokens is None
    assert changes[-1] is None
