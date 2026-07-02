import json
from unittest import mock

from geosys_sync.core.errors import (
    ApiError, ApprovalRequiredError, AuthRequiredError, RateLimitedError,
    SyncConflictError, error_from_response,
)


def fake_response(status, payload=None, headers=None):
    resp = mock.Mock()
    resp.status_code = status
    resp.reason = 'ERR'
    resp.headers = headers or {}
    if payload is None:
        resp.json.side_effect = ValueError('no json')
    else:
        resp.json.return_value = payload
    return resp


def envelope(code, message='msg', detail=None):
    return {'error': {'code': code, 'message': message, 'detail': detail or {}}}


def test_token_revoked_maps_to_auth_required():
    err = error_from_response(fake_response(401, envelope('TOKEN_REVOKED')))
    assert isinstance(err, AuthRequiredError)
    assert err.code == 'TOKEN_REVOKED'
    assert err.status == 401


def test_token_expired_stays_plain_api_error():
    err = error_from_response(fake_response(401, envelope('TOKEN_EXPIRED')))
    assert type(err) is ApiError


def test_sync_conflict_exposes_current_etag():
    err = error_from_response(
        fake_response(409, envelope('SYNC_CONFLICT', detail={'current_etag': 'abc'})))
    assert isinstance(err, SyncConflictError)
    assert err.current_etag == 'abc'


def test_approval_required():
    err = error_from_response(fake_response(409, envelope('PROJECT_APPROVAL_REQUIRED')))
    assert isinstance(err, ApprovalRequiredError)


def test_rate_limited_reads_retry_after_header_fallback():
    err = error_from_response(
        fake_response(429, envelope('RATE_LIMITED'), headers={'Retry-After': '120'}))
    assert isinstance(err, RateLimitedError)
    assert err.retry_after == 120


def test_non_json_body_falls_back_to_http_code():
    err = error_from_response(fake_response(502))
    assert err.code == 'HTTP_502'
    assert err.status == 502
