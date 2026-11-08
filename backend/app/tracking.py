from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Elder,
    EventLog,
    FamilyMembership,
    GeofenceState,
    HomeGeofence,
    NotificationOutbox,
    TrackDay,
    TrackPoint,
)


MAX_GAP = timedelta(minutes=10)


def utc_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def local_date(captured_at: datetime, timezone_name: str) -> date:
    return captured_at.astimezone(ZoneInfo(timezone_name)).date()


def get_or_create_track_day(db: Session, elder_id: uuid.UUID, day: date) -> TrackDay:
    track_day = db.scalar(
        select(TrackDay).where(TrackDay.elder_id == elder_id, TrackDay.local_date == day)
    )
    if not track_day:
        track_day = TrackDay(elder_id=elder_id, local_date=day)
        db.add(track_day)
        db.flush()
    return track_day


def recompute_track_day(db: Session, track_day: TrackDay) -> None:
    points = list(
        db.scalars(
            select(TrackPoint)
            .where(TrackPoint.track_day_id == track_day.id)
            .order_by(TrackPoint.captured_at, TrackPoint.sequence)
        )
    )
    valid = [point for point in points if point.valid_for_safety]
    distance = 0.0
    gap_count = 0
    for previous, current in zip(valid, valid[1:]):
        if utc_aware(current.captured_at) - utc_aware(previous.captured_at) > MAX_GAP:
            gap_count += 1
            continue
        distance += haversine_m(
            previous.latitude,
            previous.longitude,
            current.latitude,
            current.longitude,
        )
    track_day.total_point_count = len(points)
    track_day.valid_point_count = len(valid)
    track_day.started_at = valid[0].captured_at if valid else None
    track_day.ended_at = valid[-1].captured_at if valid else None
    track_day.distance_m = round(distance, 1)
    track_day.gap_count = gap_count


def build_segments(points: list[TrackPoint]) -> tuple[list[dict], int]:
    valid = [point for point in points if point.valid_for_safety]
    skipped = len(points) - len(valid)
    segments: list[dict] = []
    for point in valid:
        serialized = {
            "sequence": point.sequence,
            "capturedAt": point.captured_at,
            "latitude": point.latitude,
            "longitude": point.longitude,
            "accuracyM": point.accuracy_m,
            "speedMps": point.speed_mps,
            "batteryPct": point.battery_pct,
            "isCharging": point.is_charging,
        }
        if not segments:
            segments.append({"gapBeforeSeconds": None, "points": [serialized]})
            continue
        previous = segments[-1]["points"][-1]["capturedAt"]
        gap = utc_aware(point.captured_at) - utc_aware(previous)
        if gap > MAX_GAP:
            segments.append({"gapBeforeSeconds": int(gap.total_seconds()), "points": [serialized]})
        else:
            segments[-1]["points"].append(serialized)
    return segments, skipped


def evaluate_geofence(db: Session, elder: Elder, point: TrackPoint) -> None:
    if not point.valid_for_safety:
        return
    fence = db.scalar(select(HomeGeofence).where(HomeGeofence.elder_id == elder.id))
    if not fence:
        return
    state = db.scalar(select(GeofenceState).where(GeofenceState.elder_id == elder.id))
    if not state:
        state = GeofenceState(elder_id=elder.id)
        db.add(state)
        db.flush()
    elif state.last_valid_location_at:
        last_valid = state.last_valid_location_at
        if last_valid.tzinfo is None:
            last_valid = last_valid.replace(tzinfo=timezone.utc)
        if point.captured_at <= last_valid:
            return

    distance = haversine_m(fence.latitude, fence.longitude, point.latitude, point.longitude)
    state.last_valid_location_at = point.captured_at
    was_outside = state.status == "outside"

    if distance > fence.radius_m:
        state.consecutive_outside += 1
        state.consecutive_inside = 0
        if state.consecutive_outside >= 2 and not was_outside:
            state.status = "outside"
            state.outside_since = point.captured_at
            state.last_reminded_at = point.captured_at
            enqueue_geofence_notification(db, elder, point.captured_at, "outside")
    elif distance <= 4500:
        state.consecutive_inside += 1
        state.consecutive_outside = 0
        if state.consecutive_inside >= 2 and state.status != "inside":
            state.status = "inside"
            state.outside_since = None
            state.last_reminded_at = None
            if was_outside:
                db.add(
                    EventLog(
                        family_id=elder.family_id,
                        elder_id=elder.id,
                        event_type="geofence.inside",
                        payload={"elderId": str(elder.id), "elderDisplayName": elder.display_name},
                    )
                )
    else:
        # The hysteresis band preserves the current state but breaks a consecutive run.
        state.consecutive_outside = 0
        state.consecutive_inside = 0


def enqueue_geofence_notification(
    db: Session, elder: Elder, event_at: datetime, kind: str, repeat: bool = False
) -> None:
    bucket = event_at.astimezone(timezone.utc).strftime("%Y%m%d%H" if repeat else "%Y%m%d%H%M%S%f")
    label = "仍在住宅范围外" if repeat else "已离开住宅范围"
    db.add(
        NotificationOutbox(
            family_id=elder.family_id,
            elder_id=elder.id,
            kind=f"geofence.{kind}",
            title=f"{elder.display_name}{label}",
            body=f"{elder.display_name}的位置已连续两次超过住宅 5 公里范围。",
            payload={
                "elderId": str(elder.id),
                "elderDisplayName": elder.display_name,
                "eventAt": event_at.isoformat(),
            },
            dedupe_key=f"geofence:{elder.id}:{kind}:{bucket}",
        )
    )
    db.add(
        EventLog(
            family_id=elder.family_id,
            elder_id=elder.id,
            event_type=f"geofence.{kind}",
            payload={"elderId": str(elder.id), "elderDisplayName": elder.display_name},
        )
    )


def cleanup_before_date() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=89)


def family_admin_user_ids(db: Session, family_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(FamilyMembership.user_id).where(
                FamilyMembership.family_id == family_id,
                FamilyMembership.role == "admin",
            )
        )
    )
