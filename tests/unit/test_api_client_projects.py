import pytest

from geosys_sync.core.api_client import GeosysClient
from geosys_sync.core.errors import ApprovalRequiredError
from geosys_sync.core.models import TokenBundle

BASE = 'https://server.test'
API = BASE + '/api/qgis/v1'


def make_client():
    return GeosysClient(BASE, device_id='qgis-testdevice1',
                        tokens=TokenBundle('acc', 'ref'))


def test_list_projects(requests_mock):
    requests_mock.get(API + '/projects', json={'projects': [
        {'id': 7, 'name': 'P1', 'role': 'owner', 'is_owner': True,
         'epsg_code': None, 'effective_epsg_code': 3857},
        {'id': 8, 'name': 'P2', 'role': 'member', 'is_owner': False,
         'epsg_code': 25832, 'effective_epsg_code': 25832},
    ]})
    projects = make_client().list_projects()
    assert [p.id for p in projects] == [7, 8]
    assert projects[0].is_owner and not projects[1].is_owner


def test_get_project_includes_epsg_def(requests_mock):
    requests_mock.get(API + '/projects/7', json={
        'id': 7, 'name': 'P1', 'role': 'owner', 'is_owner': True,
        'epsg_code': 25832, 'effective_epsg_code': 25832,
        'effective_epsg_def': {'code': 25832, 'proj4': '+proj=utm +zone=32'}})
    p = make_client().get_project(7)
    assert p.effective_epsg_def['code'] == 25832


def test_create_project_ok(requests_mock):
    requests_mock.post(API + '/projects', status_code=201, json={
        'id': 9, 'name': 'New', 'role': 'owner', 'is_owner': True,
        'epsg_code': None, 'effective_epsg_code': 3857})
    p = make_client().create_project('New', 'desc')
    assert p.id == 9
    assert requests_mock.last_request.json() == {'name': 'New', 'description': 'desc'}


def test_create_project_approval_gate(requests_mock):
    requests_mock.post(API + '/projects', status_code=409, json={
        'error': {'code': 'PROJECT_APPROVAL_REQUIRED',
                  'message': 'Project creation requires approval',
                  'detail': {'request_endpoint': '/api/qgis/v1/project-requests',
                             'project_name': 'New'}}})
    with pytest.raises(ApprovalRequiredError) as exc:
        make_client().create_project('New')
    assert exc.value.detail['project_name'] == 'New'


def test_project_requests_roundtrip(requests_mock):
    requests_mock.post(API + '/project-requests', status_code=201,
                       json={'id': 55, 'name': 'New', 'status': 'pending'})
    requests_mock.get(API + '/project-requests', json={'project_requests': [
        {'id': 55, 'name': 'New', 'status': 'pending'}]})
    c = make_client()
    created = c.submit_project_request('New')
    assert created['status'] == 'pending'
    assert c.list_project_requests()[0]['id'] == 55
