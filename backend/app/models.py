from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def uuid4() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now().astimezone()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Family(TimestampMixin, Base):
    __tablename__ = "families"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FamilyMembership(TimestampMixin, Base):
    __tablename__ = "family_memberships"
    __table_args__ = (UniqueConstraint("family_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    family: Mapped[Family] = relationship()
    user: Mapped[User] = relationship()


class Elder(TimestampMixin, Base):
    __tablename__ = "elders"
    __table_args__ = (Index("ix_elders_family_active", "family_id", "active"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(80))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ElderDevice(TimestampMixin, Base):
    __tablename__ = "elder_devices"
    __table_args__ = (
        Index("ix_elder_devices_elder_active", "elder_id", "active"),
        Index("ix_elder_devices_installation", "device_installation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    elder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elders.id", ondelete="CASCADE"))
    device_installation_id: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    model: Mapped[str | None] = mapped_column(String(120))
    app_version: Mapped[str | None] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_command_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    background_location_granted: Mapped[bool | None] = mapped_column(Boolean)
    battery_optimization_ignored: Mapped[bool | None] = mapped_column(Boolean)
    location_services_enabled: Mapped[bool | None] = mapped_column(Boolean)
    last_service_error: Mapped[str | None] = mapped_column(String(120))


class TrackDay(TimestampMixin, Base):
    __tablename__ = "track_days"
    __table_args__ = (
        UniqueConstraint("elder_id", "local_date"),
        Index("ix_track_days_elder_date", "elder_id", "local_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    elder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elders.id", ondelete="CASCADE"))
    local_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_point_count: Mapped[int] = mapped_column(Integer, default=0)
    total_point_count: Mapped[int] = mapped_column(Integer, default=0)
    distance_m: Mapped[float] = mapped_column(Float, default=0)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)


class TrackPoint(TimestampMixin, Base):
    __tablename__ = "track_points"
    __table_args__ = (
        UniqueConstraint("device_id", "sequence"),
        Index("ix_track_points_day_time", "track_day_id", "captured_at"),
        Index("ix_track_points_elder_time", "elder_id", "captured_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    track_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("track_days.id", ondelete="CASCADE"))
    elder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elders.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elder_devices.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float] = mapped_column(Float)
    speed_mps: Mapped[float | None] = mapped_column(Float)
    battery_pct: Mapped[int] = mapped_column(Integer)
    is_charging: Mapped[bool] = mapped_column(Boolean, default=False)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    coordinate_system: Mapped[str] = mapped_column(String(8), server_default="GCJ02")
    valid_for_safety: Mapped[bool] = mapped_column(Boolean, default=True)


class DeviceLocateRequest(TimestampMixin, Base):
    __tablename__ = "device_locate_requests"
    __table_args__ = (
        Index("ix_locate_requests_device_state", "device_id", "state"),
        Index("ix_locate_requests_elder_requested", "elder_id", "requested_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    elder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elders.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elder_devices.id", ondelete="CASCADE")
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(20), default="pending")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(40))
    result_track_point_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("track_points.id", ondelete="SET NULL")
    )


class HomeGeofence(TimestampMixin, Base):
    __tablename__ = "home_geofences"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    elder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elders.id", ondelete="CASCADE"), unique=True
    )
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    radius_m: Mapped[int] = mapped_column(Integer, default=5000)


class GeofenceState(TimestampMixin, Base):
    __tablename__ = "geofence_states"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    elder_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("elders.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    consecutive_outside: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_inside: Mapped[int] = mapped_column(Integer, default=0)
    last_valid_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outside_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(TimestampMixin, Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Invitation(TimestampMixin, Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceEnrollment(TimestampMixin, Base):
    __tablename__ = "device_enrollments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    elder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elders.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PushSubscription(TimestampMixin, Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("family_id", "user_id", "endpoint"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(Text)
    auth: Mapped[str] = mapped_column(Text)


class NotificationOutbox(TimestampMixin, Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (UniqueConstraint("dedupe_key"), Index("ix_outbox_pending", "sent_at", "available_at"))

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    elder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("elders.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(180))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class EventLog(Base):
    __tablename__ = "event_log"
    __table_args__ = (Index("ix_event_family_id", "family_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"))
    elder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("elders.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
