"""MFA challenge flow: login can return a challenge instead of tokens;
verify_mfa exchanges pending_token + code for the real session."""
import pytest

from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import ApiError
from geosys_sync.core.models import MfaChallenge

BASE = 'https://server.test'
API = BASE + '/api/qgis/v1'

LOGIN_OK = {
    'access_token': 'acc1', 'refresh_token': 'ref1', 'expires_in': 900,
    'token_type': 'Bearer',
    'user': {'id': 42, 'username': 'yash'},
    'org': {'id': 3, 'name': 'SAITECH', 'enable_qgis_sync': True},
    'capabilities': {'can_upload_vector': True},
}
MFA_CHALLENGE = {'mfa_required': True, 'pending_token': 'pend1',
                 'methods': ['totp']}


def make_client(**kw):
    return GeosysClient(BASE, device_id='qgis-testdevice1', **kw)


def test_login_returns_challenge_without_tokens(requests_mock):
    requests_mock.post(API + '/auth/login', status_code=202, json=MFA_CHALLENGE)
    changes = []
    c = make_client(on_tokens_changed=changes.append)
    result = c.login('yash', 'pw')
    assert isinstance(result, MfaChallenge)
    assert result.pending_token == 'pend1'
    assert result.methods == ['totp']
    assert c.tokens is None          # no tokens before the second factor
    assert changes == []


def test_verify_mfa_stores_tokens_and_returns_session(requests_mock):
    requests_mock.post(API + '/auth/mfa-verify', json=LOGIN_OK)
    changes = []
    c = make_client(on_tokens_changed=changes.append)
    info = c.verify_mfa('pend1', '123456')
    assert c.tokens.access_token == 'acc1'
    assert info.user['username'] == 'yash'
    assert changes and changes[0].refresh_token == 'ref1'
    body = requests_mock.last_request.json()
    assert body == {'pending_token': 'pend1', 'code': '123456',
                    'device_id': 'qgis-testdevice1', 'device_name': 'QGIS'}


def test_verify_mfa_bad_code_raises(requests_mock):
    requests_mock.post(API + '/auth/mfa-verify', status_code=401, json={
        'error': {'code': 'MFA_INVALID_CODE', 'message': 'Invalid code'}})
    c = make_client()
    with pytest.raises(ApiError) as exc:
        c.verify_mfa('pend1', '000000')
    assert exc.value.code == 'MFA_INVALID_CODE'
    assert c.tokens is None
