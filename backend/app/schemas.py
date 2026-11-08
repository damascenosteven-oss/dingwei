from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class BootstrapRequest(ApiModel):
    family_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


class LoginRequest(ApiModel):
    email: EmailStr
    password: str


class AcceptInvitationRequest(ApiModel):
    token: str
    password: str = Field(min_length=10, max_length=200)


class UserView(ApiModel):
    id: uuid.UUID
    email: str


class FamilyView(ApiModel):
    id: uuid.UUID
    name: str
    role: str


class AuthView(ApiModel):
    user: UserView
    families: list[FamilyView]
    csrf_token: str


class MemberView(ApiModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: str


class InvitationCreate(ApiModel):
    email: EmailStr


class InvitationView(ApiModel):
    id: uuid.UUID
    email: str
    invite_token: str
    expires_at: datetime


class ElderCreate(ApiModel):
    display_name: str = Field(min_length=1, max_length=80)
    timezone: str = "Asia/Shanghai"


class ElderPatch(ApiModel):
    display_name: str = Field(min_length=1, max_length=80)


class ElderView(ApiModel):
    id: uuid.UUID
    family_id: uuid.UUID
    display_name: str
    timezone: str
    active: bool


class EnrollmentView(ApiModel):
    code: str
    expires_at: datetime


class DeviceEnrollRequest(ApiModel):
    code: str
    installation_id: str = Field(min_length=8, max_length=100)
    model: str | None = Field(default=None, max_length=120)
    app_version: str | None = Field(default=None, max_length=40)


class DeviceEnrollView(ApiModel):
    device_token: str
    elder_id: uuid.UUID
    elder_display_name: str
    timezone: str


class HomeGeofenceConfigView(ApiModel):
    latitude: float
    longitude: float
    radius_m: int
    updated_at: datetime


class DeviceConfigView(ApiModel):
    elder_id: uuid.UUID
    elder_display_name: str
    timezone: str
    moving_interval_seconds: int = 30
    stationary_interval_seconds: int = 180
    config_version: int = 2
    home_geofence: HomeGeofenceConfigView | None = None


class DeviceStatusView(ApiModel):
    state: Literal["unbound", "stopped", "bound_waiting_location", "online", "stale"]
    model: str | None
    app_version: str | None
    bound_at: datetime | None
    last_seen_at: datetime | None
    last_location_at: datetime | None
    background_location_granted: bool | None
    battery_optimization_ignored: bool | None
    location_services_enabled: bool | None
    last_service_error: str | None


class LocateResultView(ApiModel):
    captured_at: datetime
    latitude: float
    longitude: float
    accuracy_m: float


class LocateRequestView(ApiModel):
    id: uuid.UUID
    state: Literal["pending", "acknowledged", "completed", "failed", "expired"]
    requested_at: datetime
    expires_at: datetime
    delivered_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None
    result: LocateResultView | None = None


class DeviceCommandWait(ApiModel):
    app_version: str | None = Field(default=None, max_length=40)
    background_location_granted: bool
    battery_optimization_ignored: bool
    location_services_enabled: bool
    service_error: str | None = Field(default=None, max_length=120)


class DeviceCommandView(ApiModel):
    id: uuid.UUID
    type: Literal["locate_now"] = "locate_now"
    expires_at: datetime


class DeviceLocateFailure(ApiModel):
    failure_code: Literal[
        "permission_missing",
        "location_disabled",
        "location_timeout",
        "low_accuracy",
        "mock_location",
        "network_error",
        "service_error",
    ]


class LocationPointIn(ApiModel):
    coordinate_system: Literal["GCJ02", "WGS84"] = "GCJ02"
    sequence: int = Field(ge=1)
    captured_at: datetime
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(ge=0, le=100_000)
    speed_mps: float | None = Field(default=None, ge=0, le=500)
    battery_pct: int = Field(ge=0, le=100)
    is_charging: bool = False
    is_mock: bool = False
    locate_request_id: uuid.UUID | None = None

    @field_validator("captured_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("capturedAt must contain a timezone")
        return value


class IdentifiedPoint(ApiModel):
    model_config = ConfigDict(extra="allow")
    sequence: int = Field(ge=1)


class LocationBatchIn(ApiModel):
    points: list[LocationPointIn | IdentifiedPoint] = Field(min_length=1, max_length=500)
    app_version: str | None = Field(default=None, max_length=40)


class RejectedPoint(ApiModel):
    sequence: int
    reason: str


class LocationBatchResult(ApiModel):
    last_accepted_at: datetime | None
    accepted_sequences: list[int]
    duplicate_sequences: list[int]
    rejected: list[RejectedPoint]


class LatestLocationView(ApiModel):
    sequence: int
    coordinate_system: Literal["GCJ02", "WGS84"]
    received_at: datetime
    captured_at: datetime
    latitude: float
    longitude: float
    accuracy_m: float
    speed_mps: float | None
    battery_pct: int
    is_charging: bool
    stale: bool


class TrackDayView(ApiModel):
    date: date
    started_at: datetime | None
    ended_at: datetime | None
    valid_point_count: int
    total_point_count: int
    distance_m: float
    gap_count: int


class TrackPointView(ApiModel):
    sequence: int
    captured_at: datetime
    latitude: float
    longitude: float
    accuracy_m: float
    speed_mps: float | None
    battery_pct: int
    is_charging: bool


class TrackSegmentView(ApiModel):
    gap_before_seconds: int | None = None
    points: list[TrackPointView]


class TrackDayDetail(ApiModel):
    summary: TrackDayView
    segments: list[TrackSegmentView]
    skipped_point_count: int


class HomeGeofencePut(ApiModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class HomeGeofenceView(ApiModel):
    elder_id: uuid.UUID
    latitude: float
    longitude: float
    radius_m: int
    status: str
    last_valid_location_at: datetime | None
    updated_at: datetime


class PushKeys(ApiModel):
    p256dh: str
    auth: str


class PushSubscriptionCreate(ApiModel):
    endpoint: str
    keys: PushKeys


class PushSubscriptionView(ApiModel):
    id: uuid.UUID
    vapid_public_key: str
