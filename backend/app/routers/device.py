from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..dependencies import get_current_device
from ..models import (
    DeviceEnrollment,
    DeviceLocateRequest,
    Elder,
    ElderDevice,
    EventLog,
    HomeGeofence,
    TrackPoint,
)
from ..schemas import (
    DeviceConfigView,
    DeviceCommandWait,
    DeviceCommandView,
    DeviceEnrollRequest,
    DeviceEnrollView,
    DeviceLocateFailure,
    HomeGeofenceConfigView,
    LocationPointIn,
    LocationBatchIn,
    LocationBatchResult,
    RejectedPoint,
)
from ..security import random_token, token_hash
from ..tracking import (
    cleanup_before_date,
    evaluate_geofence,
    get_or_create_track_day,
    local_date,
    recompute_track_day,
)

router = APIRouter(prefix="/api/v1/device", tags=["device"])
ACTIVE_LOCATE_STATES = ("pending", "acknowledged")


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def emit_locate_event(db: Session, request: DeviceLocateRequest) -> None:
    db.add(
        EventLog(
            family_id=request.family_id,
            elder_id=request.elder_id,
            event_type="locate-request.updated",
            payload={
                "elderId": str(request.elder_id),
                "requestId": str(request.id),
                "state": request.state,
                "failureCode": request.failure_code,
            },
        )
    )


def expire_locate_request(db: Session, request: DeviceLocateRequest, now: datetime) -> bool:
    if request.state in ACTIVE_LOCATE_STATES and aware(request.expires_at) <= now:
        request.state = "expired"
        request.failed_at = now
        request.failure_code = "phone_unresponsive"
        emit_locate_event(db, request)
        return True
    return False


@router.post("/enroll", response_model=DeviceEnrollView)
def enroll_device(payload: DeviceEnrollRequest, db: Session = Depends(get_db)) -> DeviceEnrollView:
    enrollment = db.scalar(
        select(DeviceEnrollment).where(DeviceEnrollment.code_hash == token_hash(payload.code)).with_for_update()
    )
    now = datetime.now(timezone.utc)
    if (
        not enrollment
        or enrollment.consumed_at
        or aware(enrollment.expires_at) < now
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="绑定码无效或已过期")
    elder = db.scalar(select(Elder).where(Elder.id == enrollment.elder_id).with_for_update())
    if not elder or not elder.active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="老人资料不可用")

    db.execute(
        update(ElderDevice)
        .where(
            (ElderDevice.elder_id == elder.id)
            | (ElderDevice.device_installation_id == payload.installation_id),
            ElderDevice.active.is_(True),
        )
        .values(active=False)
    )
    raw_token = random_token()
    device = ElderDevice(
        elder_id=elder.id,
        device_installation_id=payload.installation_id,
        token_hash=token_hash(raw_token),
        model=payload.model,
        app_version=payload.app_version,
        active=True,
        last_seen_at=now,
    )
    enrollment.consumed_at = now
    db.add(device)
    db.add(
        EventLog(
            family_id=elder.family_id,
            elder_id=elder.id,
            event_type="device.enrolled",
            payload={
                "elderId": str(elder.id),
                "model": payload.model,
                "appVersion": payload.app_version,
            },
        )
    )
    db.commit()
    return DeviceEnrollView(
        device_token=raw_token,
        elder_id=elder.id,
        elder_display_name=elder.display_name,
        timezone=elder.timezone,
    )


@router.get("/config", response_model=DeviceConfigView)
def device_config(
    device: ElderDevice = Depends(get_current_device), db: Session = Depends(get_db)
) -> DeviceConfigView:
    elder = db.get(Elder, device.elder_id)
    if not elder:
        raise HTTPException(status_code=404, detail="老人资料不存在")
    fence = db.scalar(select(HomeGeofence).where(HomeGeofence.elder_id == elder.id))
    return DeviceConfigView(
        elder_id=elder.id,
        elder_display_name=elder.display_name,
        timezone=elder.timezone,
        home_geofence=(
            HomeGeofenceConfigView(
                latitude=fence.latitude,
                longitude=fence.longitude,
                radius_m=fence.radius_m,
                updated_at=fence.updated_at,
            )
            if fence
            else None
        ),
    )


@router.post("/commands:wait", response_model=DeviceCommandView | None)
async def wait_for_command(
    payload: DeviceCommandWait,
    timeout_seconds: int = Query(default=25, ge=1, le=25, alias="timeoutSeconds"),
    device: ElderDevice = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> DeviceCommandView | Response:
    now = datetime.now(timezone.utc)
    device.last_seen_at = now
    device.last_command_poll_at = now
    device.background_location_granted = payload.background_location_granted
    device.battery_optimization_ignored = payload.battery_optimization_ignored
    device.location_services_enabled = payload.location_services_enabled
    device.last_service_error = payload.service_error
    if payload.app_version:
        device.app_version = payload.app_version
    db.commit()

    deadline = time.monotonic() + timeout_seconds
    while True:
        with SessionLocal.begin() as command_db:
            current_time = datetime.now(timezone.utc)
            requests = list(
                command_db.scalars(
                    select(DeviceLocateRequest)
                    .where(
                        DeviceLocateRequest.device_id == device.id,
                        DeviceLocateRequest.state.in_(ACTIVE_LOCATE_STATES),
                    )
                    .order_by(DeviceLocateRequest.requested_at)
                )
            )
            for request in requests:
                expire_locate_request(command_db, request, current_time)
            pending = next((item for item in requests if item.state == "pending"), None)
            if pending:
                return DeviceCommandView(id=pending.id, expires_at=aware(pending.expires_at))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        await asyncio.sleep(min(1.0, remaining))


def get_device_locate_request(
    db: Session, device: ElderDevice, request_id: uuid.UUID
) -> DeviceLocateRequest:
    request = db.scalar(
        select(DeviceLocateRequest).where(
            DeviceLocateRequest.id == request_id,
            DeviceLocateRequest.device_id == device.id,
        )
    )
    if not request:
        raise HTTPException(status_code=404, detail="立即定位请求不存在")
    return request


@router.post("/locate-requests/{request_id}/ack")
def acknowledge_locate_request(
    request_id: uuid.UUID,
    device: ElderDevice = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    lock_sharing_device(db, device)
    request = get_device_locate_request(db, device, request_id)
    now = datetime.now(timezone.utc)
    if not expire_locate_request(db, request, now) and request.state == "pending":
        request.state = "acknowledged"
        request.delivered_at = now
        emit_locate_event(db, request)
    device.last_seen_at = now
    db.commit()
    return {"state": request.state}


@router.post("/locate-requests/{request_id}/fail")
def fail_locate_request(
    request_id: uuid.UUID,
    payload: DeviceLocateFailure,
    device: ElderDevice = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    lock_sharing_device(db, device)
    request = get_device_locate_request(db, device, request_id)
    now = datetime.now(timezone.utc)
    if not expire_locate_request(db, request, now) and request.state in ACTIVE_LOCATE_STATES:
        request.state = "failed"
        request.failed_at = now
        request.failure_code = payload.failure_code
        emit_locate_event(db, request)
    device.last_seen_at = now
    device.last_service_error = payload.failure_code
    db.commit()
    return {"state": request.state, "failureCode": request.failure_code}


def lock_sharing_device(db: Session, device: ElderDevice) -> ElderDevice:
    active_device = db.scalar(
        select(ElderDevice).where(ElderDevice.id == device.id, ElderDevice.active.is_(True))
        .with_for_update()
    )
    if active_device is None:
        raise HTTPException(status_code=401, detail="已停止共享")
    return active_device


@router.post("/stop-sharing", status_code=204)
def stop_sharing(device: ElderDevice = Depends(get_current_device), db: Session = Depends(get_db)):
    lock_sharing_device(db, device)
    device.active = False
    now = datetime.now(timezone.utc)
    for request in db.scalars(select(DeviceLocateRequest).where(
        DeviceLocateRequest.device_id == device.id,
        DeviceLocateRequest.state.in_(ACTIVE_LOCATE_STATES),
    ).with_for_update()):
        request.state = "failed"
        request.failed_at = now
        request.failure_code = "sharing_stopped"
        emit_locate_event(db, request)
    db.commit()
    return Response(status_code=204)


@router.post("/locations:batch", response_model=LocationBatchResult)
def upload_locations(
    payload: LocationBatchIn,
    device: ElderDevice = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> LocationBatchResult:
    device = lock_sharing_device(db, device)
    elder = db.get(Elder, device.elder_id)
    if not elder or not elder.active:
        raise HTTPException(status_code=404, detail="老人资料不存在")

    now = datetime.now(timezone.utc)
    cutoff = cleanup_before_date()
    accepted: list[int] = []
    duplicates: list[int] = []
    rejected: list[RejectedPoint] = []
    touched_days = {}
    accepted_points: list[TrackPoint] = []
    seen_sequences: set[int] = set()

    valid_items = []
    for item in payload.points:
        if isinstance(item, LocationPointIn):
            valid_items.append(item)
        else:
            try:
                valid_items.append(LocationPointIn.model_validate(item.model_dump()))
            except ValidationError as error:
                fields = ", ".join(str(e["loc"][0]) for e in error.errors(include_input=False))
                rejected.append(RejectedPoint(sequence=item.sequence, reason=f"缺失或无效的字段 {fields}"))

    for item in sorted(valid_items, key=lambda value: (value.captured_at, value.sequence)):
        if item.sequence in seen_sequences:
            duplicates.append(item.sequence)
            continue
        seen_sequences.add(item.sequence)
        captured_at = item.captured_at.astimezone(timezone.utc)
        point_date = local_date(captured_at, elder.timezone)
        if captured_at > now + timedelta(minutes=5):
            rejected.append(RejectedPoint(sequence=item.sequence, reason="采集时间超过未来 5 分钟"))
            continue
        if point_date < cutoff:
            rejected.append(RejectedPoint(sequence=item.sequence, reason="超出 90 天保留范围"))
            continue
        exists = db.scalar(
            select(TrackPoint.id).where(
                TrackPoint.device_id == device.id,
                TrackPoint.sequence == item.sequence,
            )
        )
        if exists:
            duplicates.append(item.sequence)
            continue
        day = get_or_create_track_day(db, elder.id, point_date)
        touched_days[day.id] = day
        point = TrackPoint(
            track_day_id=day.id,
            elder_id=elder.id,
            device_id=device.id,
            sequence=item.sequence,
            captured_at=captured_at,
            received_at=now,
            coordinate_system=item.coordinate_system,
            latitude=item.latitude,
            longitude=item.longitude,
            accuracy_m=item.accuracy_m,
            speed_mps=item.speed_mps,
            battery_pct=item.battery_pct,
            is_charging=item.is_charging,
            is_mock=item.is_mock,
            valid_for_safety=not item.is_mock and item.accuracy_m <= 200,
        )
        db.add(point)
        db.flush()
        accepted.append(item.sequence)
        accepted_points.append(point)
        if item.locate_request_id and point.valid_for_safety:
            locate_request = db.get(DeviceLocateRequest, item.locate_request_id)
            if (
                locate_request
                and locate_request.device_id == device.id
                and not expire_locate_request(db, locate_request, now)
                and locate_request.state in ACTIVE_LOCATE_STATES
                and aware(locate_request.requested_at) <= captured_at
                and captured_at <= aware(locate_request.expires_at)
            ):
                locate_request.state = "completed"
                locate_request.completed_at = now
                locate_request.result_track_point_id = point.id
                locate_request.failure_code = None
                emit_locate_event(db, locate_request)

    for day in touched_days.values():
        recompute_track_day(db, day)

    if rejected:
        db.add(EventLog(
            family_id=elder.family_id, elder_id=elder.id, event_type="location.rejected",
            payload={"deviceId":str(device.id), "sequences":[p.sequence for p in rejected],
                     "reasons":[p.reason for p in rejected]},
        ))
    if accepted_points:
        for point in sorted(accepted_points, key=lambda value: value.captured_at):
            evaluate_geofence(db, elder, point)
        latest = max(accepted_points, key=lambda point: point.captured_at)
        db.add(
            EventLog(
                family_id=elder.family_id,
                elder_id=elder.id,
                event_type="location.updated",
                payload={
                    "elderId": str(elder.id),
                    "elderDisplayName": elder.display_name,
                    "capturedAt": latest.captured_at.isoformat(),
                    "batteryPct": latest.battery_pct,
                },
            )
        )
    device.last_seen_at = now
    if payload.app_version:
        device.app_version = payload.app_version
    db.commit()
    return LocationBatchResult(
        last_accepted_at=now if accepted else None,
        accepted_sequences=accepted,
        duplicate_sequences=duplicates,
        rejected=rejected,
    )
