"""Exception hierarchy for the GeosysAI QGIS-sync client."""


class ApiError(Exception):
    """Base error carrying the server's error envelope."""

    def __init__(self, code, message, status=None, detail=None):
        super().__init__('{}: {}'.format(code, message))
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}


class NetworkError(ApiError):
    """Transport failure - server unreachable, DNS, TLS, timeout."""


class AuthRequiredError(ApiError):
    """The user must log in again (revoked/invalid token, no token)."""


class ApprovalRequiredError(ApiError):
    """409 PROJECT_APPROVAL_REQUIRED - offer to submit a project request."""


class SyncConflictError(ApiError):
    """409 SYNC_CONFLICT - dataset changed server-side; pull before push."""

    @property
    def current_etag(self):
        return self.detail.get('current_etag')


class RasterProcessingError(ApiError):
    """409 RASTER_PROCESSING - COG not ready yet; poll /status."""


class RateLimitedError(ApiError):

    @property
    def retry_after(self):
        try:
            return int(self.detail.get('retry_after') or 60)
        except (TypeError, ValueError):
            return 60


_CODE_MAP = {
    'AUTH_REQUIRED': AuthRequiredError,
    'TOKEN_REVOKED': AuthRequiredError,
    'TOKEN_INVALID': AuthRequiredError,
    'PROJECT_APPROVAL_REQUIRED': ApprovalRequiredError,
    'SYNC_CONFLICT': SyncConflictError,
    'RASTER_PROCESSING': RasterProcessingError,
    'RATE_LIMITED': RateLimitedError,
}
# TOKEN_EXPIRED intentionally maps to plain ApiError: the client catches it,
# refreshes once, and retries. Only revoked/invalid tokens force re-login.


def error_from_response(resp):
    """Build the right ApiError subclass from a requests.Response."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    envelope = body.get('error') if isinstance(body, dict) else None
    if not isinstance(envelope, dict):
        # Endpoints outside the QGIS Sync API (e.g. the platform's global 404
        # handler) answer {"error": "<string>"} - keep the text as the message.
        envelope = {'message': envelope} if isinstance(envelope, str) else {}
    code = envelope.get('code') or 'HTTP_{}'.format(resp.status_code)
    message = envelope.get('message') or getattr(resp, 'reason', '') or 'Request failed'
    detail = dict(envelope.get('detail') or {})
    if code == 'RATE_LIMITED' and not detail.get('retry_after'):
        detail['retry_after'] = resp.headers.get('Retry-After')
    cls = _CODE_MAP.get(code, ApiError)
    return cls(code, message, status=resp.status_code, detail=detail)
