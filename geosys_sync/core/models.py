"""Typed wire objects for the /api/qgis/v1 contract. Pure Python, no QGIS."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int = 900
    token_type: str = 'Bearer'

    @classmethod
    def from_json(cls, data):
        return cls(access_token=data['access_token'],
                   refresh_token=data['refresh_token'],
                   expires_in=int(data.get('expires_in') or 900),
                   token_type=data.get('token_type') or 'Bearer')


@dataclass
class MfaChallenge:
    """202 login response: the account needs a second factor before tokens."""
    pending_token: str
    methods: list = field(default_factory=list)

    @classmethod
    def from_json(cls, data):
        return cls(pending_token=data['pending_token'],
                   methods=list(data.get('methods') or []))


@dataclass
class SessionInfo:
    user: dict
    org: dict
    capabilities: dict

    @classmethod
    def from_json(cls, data):
        return cls(user=data.get('user') or {}, org=data.get('org') or {},
                   capabilities=data.get('capabilities') or {})

    def can_upload(self, kind):
        return bool(self.capabilities.get('can_upload_{}'.format(kind)))


@dataclass
class Project:
    id: int
    name: str
    role: str = 'member'
    is_owner: bool = False
    epsg_code: Optional[int] = None
    effective_epsg_code: Optional[int] = None
    effective_epsg_def: Optional[dict] = None
    # True once the project holds raster data: its map CRS is frozen and the
    # server refuses any change. crs_confirmation_required is the inverse -
    # the next raster upload must carry an explicit acknowledgement.
    epsg_locked: bool = True
    crs_confirmation_required: bool = False

    @classmethod
    def from_json(cls, data):
        # Older servers send neither flag; default to "locked, nothing to
        # confirm" so the plugin never invents a freeze warning it can't back up.
        locked = bool(data.get('epsg_locked', True))
        return cls(id=data['id'], name=data.get('name') or '',
                   role=data.get('role') or 'member',
                   is_owner=bool(data.get('is_owner')),
                   epsg_code=data.get('epsg_code'),
                   effective_epsg_code=data.get('effective_epsg_code'),
                   effective_epsg_def=data.get('effective_epsg_def'),
                   epsg_locked=locked,
                   crs_confirmation_required=bool(
                       data.get('crs_confirmation_required', not locked)))


@dataclass
class ManifestEntry:
    id: int
    name: str
    kind: str
    sync_etag: str = ''
    can_overwrite: bool = False
    geometry_type: Optional[str] = None
    feature_count: Optional[int] = None
    epsg: Optional[int] = None
    bbox_4326: Optional[list] = None
    updated_at: Optional[str] = None
    style: dict = field(default_factory=dict)
    download_endpoint: Optional[str] = None
    cog_status: Optional[str] = None
    band_count: Optional[int] = None
    style_warnings: list = field(default_factory=list)

    @classmethod
    def from_json(cls, data):
        return cls(id=data['id'], name=data.get('name') or '',
                   kind=data.get('kind') or 'vector',
                   sync_etag=data.get('sync_etag') or '',
                   can_overwrite=bool(data.get('can_overwrite')),
                   geometry_type=data.get('geometry_type'),
                   feature_count=data.get('feature_count'),
                   epsg=data.get('epsg'),
                   bbox_4326=data.get('bbox_4326'),
                   updated_at=data.get('updated_at'),
                   style=data.get('style') or {},
                   download_endpoint=(data.get('download') or {}).get('endpoint'),
                   cog_status=data.get('cog_status'),
                   band_count=data.get('band_count'),
                   style_warnings=list(data.get('style_warnings') or []))


@dataclass
class CogUrl:
    url: str
    expires_in: int
    variant: str
    sync_etag: str = ''
    epsg: Optional[int] = None
    band_count: Optional[int] = None
    bbox_native: Optional[list] = None
    cog_min: Optional[float] = None
    cog_max: Optional[float] = None

    @classmethod
    def from_json(cls, data):
        return cls(url=data['url'],
                   expires_in=int(data.get('expires_in') or 0),
                   variant=data.get('variant') or 'greyscale',
                   sync_etag=data.get('sync_etag') or '',
                   epsg=data.get('epsg'),
                   band_count=data.get('band_count'),
                   bbox_native=data.get('bbox_native'),
                   cog_min=data.get('cog_min'),
                   cog_max=data.get('cog_max'))
