"""Server-URL validation, presigned-query redaction, and the https-only
download guard (public-release hardening)."""
import requests
import pytest

from geosys_sync.core.api_client import (
    GeosysClient, redact_query, server_url_problem,
)
from geosys_sync.core.errors import ApiError, NetworkError
from geosys_sync.core.models import TokenBundle


def make_client(base='https://server.test'):
    return GeosysClient(base, device_id='qgis-testdevice1',
                        tokens=TokenBundle('acc', 'ref'))


# -- server_url_problem ------------------------------------------------------

def test_https_url_accepted():
    assert server_url_problem('https://geo.example.com') is None


def test_https_url_with_port_and_path_accepted():
    assert server_url_problem('https://geo.example.com:8443/app') is None


def test_http_localhost_accepted_for_dev():
    assert server_url_problem('http://localhost:5000') is None
    assert server_url_problem('http://127.0.0.1:5000') is None
    assert server_url_problem('http://[::1]:5000') is None


def test_http_remote_host_rejected():
    problem = server_url_problem('http://geo.example.com')
    assert problem and 'https' in problem.lower()


def test_non_http_scheme_rejected():
    assert server_url_problem('ftp://geo.example.com') is not None
    assert server_url_problem('file:///etc/passwd') is not None
    assert server_url_problem('geo.example.com') is not None


def test_garbage_url_rejected():
    assert server_url_problem('') is not None
    assert server_url_problem('https://') is not None


# -- redact_query ------------------------------------------------------------

def test_redact_query_strips_presigned_signature():
    msg = ('Max retries exceeded with url: /f.tif'
           '?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIA123 (nodename)')
    out = redact_query(msg)
    assert 'X-Amz-Signature' not in out
    assert 'AKIA123' not in out
    assert '/f.tif' in out


def test_redact_query_leaves_plain_text_alone():
    assert redact_query('Connection refused') == 'Connection refused'


# -- download errors do not echo the signed query ----------------------------

def test_download_failure_message_omits_signature(requests_mock, tmp_path):
    url = 'https://s3.wasabi/x.tif?X-Amz-Signature=secret123'
    requests_mock.get(url, exc=requests.exceptions.ConnectionError(
        'boom fetching https://s3.wasabi/x.tif?X-Amz-Signature=secret123'))
    with pytest.raises(NetworkError) as exc_info:
        make_client().download_file(url, str(tmp_path / 'x.tif'))
    assert 'secret123' not in str(exc_info.value)


# -- https-only download guard -----------------------------------------------

def test_download_file_rejects_plain_http_remote(requests_mock, tmp_path):
    requests_mock.get('http://s3.wasabi/x.tif', content=b'TIF')
    with pytest.raises(ApiError):
        make_client().download_file('http://s3.wasabi/x.tif',
                                    str(tmp_path / 'x.tif'))
    assert not requests_mock.called


def test_download_file_rejects_file_scheme(tmp_path):
    with pytest.raises(ApiError):
        make_client().download_file('file:///etc/passwd',
                                    str(tmp_path / 'x.tif'))


def test_download_file_allows_http_localhost(requests_mock, tmp_path):
    requests_mock.get('http://127.0.0.1:9000/x.tif', content=b'TIF')
    dest = str(tmp_path / 'x.tif')
    make_client().download_file('http://127.0.0.1:9000/x.tif', dest)
    assert open(dest, 'rb').read() == b'TIF'
