from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal, get_db
from ..dependencies import CurrentUser, get_current_user, require_csrf, require_membership
from ..models import (
    Elder,
    ElderDevice,
    DeviceLocateRequest,
    EventLog,
    Family,
    FamilyMembership,
    GeofenceState,
    HomeGeofence,
    Invitation,
    PushSubscription,
    TrackDay,
    TrackPoint,
    User,
)
from ..schemas import (
    ElderCreate,
    ElderPatch,
    ElderView,
    DeviceStatusView,
    EnrollmentView,
    FamilyView,
    HomeGeofencePut,
    HomeGeofenceView,
    InvitationCreate,
    InvitationView,
    LatestLocationView,
    LocateRequestView,
    LocateResultView,
    MemberView,
    PushSubscriptionCreate,
    PushSubscriptionView,
    TrackDayDetail,
    TrackDayView,
)
from ..security import expires_in, random_token, token_hash
from ..tracking import build_segments

router = APIRouter(prefix="/api/v1/families", tags=["families"])
settings = get_settings()
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


def expire_locate_request(db: Session, request: DeviceLocateRequest) -> None:
    if request.state in ACTIVE_LOCATE_STATES and aware(request.expires_at) <= datetime.now(timezone.utc):
        request.state = "expired"
        request.failed_at = datetime.now(timezone.utc)
        request.failure_code = "phone_unresponsive"
        emit_locate_event(db, request)


def locate_request_view(db: Session, request: DeviceLocateRequest) -> LocateRequestView:
    point = db.get(TrackPoint, request.result_track_point_id) if request.result_track_point_id else None
    return LocateRequestView(
        id=request.id,
        state=request.state,
        requested_at=aware(request.requested_at),
        expires_at=aware(request.expires_at),
        delivered_at=aware(request.delivered_at) if request.delivered_at else None,
        completed_at=aware(request.completed_at) if request.completed_at else None,
        failure_code=request.failure_code,
        result=(
            LocateResultView(
                captured_at=aware(point.captured_at),
                latitude=point.latitude,
                longitude=point.longitude,
                accuracy_m=point.accuracy_m,
            )
            if point
            else None
        ),
    )


def get_elder(db: Session, family_id: uuid.UUID, elder_id: uuid.UUID) -> Elder:
    elder = db.scalar(
        select(Elder).where(Elder.id == elder_id, Elder.family_id == family_id, Elder.active.is_(True))
    )
    if not elder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="老人不存在")
    return elder


@router.get("", response_model=list[FamilyView])
def list_families(
    current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[FamilyView]:
    rows = db.execute(
        select(FamilyMembership, Family)
        .join(Family, Family.id == FamilyMembership.family_id)
        .where(FamilyMembership.user_id == current.user.id, Family.active.is_(True))
    ).all()
    return [FamilyView(id=family.id, name=family.name, role=m.role) for m, family in rows]


@router.get("/{family_id}/members", response_model=list[MemberView])
def list_members(
    family_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MemberView]:
    require_membership(family_id, current, db)
    rows = db.execute(
        select(FamilyMembership, User)
        .join(User, User.id == FamilyMembership.user_id)
        .where(FamilyMembership.family_id == family_id)
    ).all()
    return [
        MemberView(id=m.id, user_id=user.id, email=user.email, role=m.role) for m, user in rows
    ]


@router.post("/{family_id}/invitations", response_model=InvitationView)
def create_invitation(
    family_id: uuid.UUID,
    payload: InvitationCreate,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> InvitationView:
    require_membership(family_id, current, db, admin=True)
    raw_token = random_token(24)
    invitation = Invitation(
        family_id=family_id,
        email=str(payload.email).lower(),
        token_hash=token_hash(raw_token),
        expires_at=expires_in(days=7),
    )
    db.add(invitation)
    db.commit()
    return InvitationView(
        id=invitation.id,
        email=invitation.email,
        invite_token=raw_token,
        expires_at=invitation.expires_at,
    )


@router.get("/{family_id}/elders", response_model=list[ElderView])
def list_elders(
    family_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Elder]:
    require_membership(family_id, current, db)
    return list(
        db.scalars(
            select(Elder)
            .where(Elder.family_id == family_id, Elder.active.is_(True))
            .order_by(Elder.created_at)
        )
    )


@router.post("/{family_id}/elders", response_model=ElderView, status_code=status.HTTP_201_CREATED)
def create_elder(
    family_id: uuid.UUID,
    payload: ElderCreate,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Elder:
    require_membership(family_id, current, db, admin=True)
    try:
        ZoneInfo(payload.timezone)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="无效时区") from exc
    elder = Elder(
        family_id=family_id,
        display_name=payload.display_name.strip(),
        timezone=payload.timezone,
    )
    db.add(elder)
    db.commit()
    return elder


@router.patch("/{family_id}/elders/{elder_id}", response_model=ElderView)
def update_elder(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    payload: ElderPatch,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> Elder:
    require_membership(family_id, current, db, admin=True)
    elder = get_elder(db, family_id, elder_id)
    elder.display_name = payload.display_name.strip()
    db.add(
        EventLog(
            family_id=family_id,
            elder_id=elder.id,
            event_type="elder.updated",
            payload={"elderId": str(elder.id), "elderDisplayName": elder.display_name},
        )
    )
    db.commit()
    return elder


@router.post("/{family_id}/elders/{elder_id}/device-enrollments", response_model=EnrollmentView)
def create_device_enrollment(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> EnrollmentView:
    from ..models import DeviceEnrollment

    require_membership(family_id, current, db, admin=True)
    elder = get_elder(db, family_id, elder_id)
    raw_code = f"{int.from_bytes(uuid.uuid4().bytes[:4], 'big') % 1_000_000:06d}"
    enrollment = DeviceEnrollment(
        elder_id=elder.id,
        code_hash=token_hash(raw_code),
        expires_at=expires_in(minutes=10),
    )
    db.add(enrollment)
    db.commit()
    return EnrollmentView(code=raw_code, expires_at=enrollment.expires_at)


@router.get(
    "/{family_id}/elders/{elder_id}/device-status",
    response_model=DeviceStatusView,
)
def get_device_status(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeviceStatusView:
    require_membership(family_id, current, db)
    get_elder(db, family_id, elder_id)
    device = db.scalar(
        select(ElderDevice)
        .where(ElderDevice.elder_id == elder_id, ElderDevice.active.is_(True))
        .order_by(ElderDevice.created_at.desc())
        .limit(1)
    )
    if not device:
        was_bound = db.scalar(select(ElderDevice.id).where(ElderDevice.elder_id == elder_id).limit(1))
        return DeviceStatusView(
            state="stopped" if was_bound else "unbound",
            model=None,
            app_version=None,
            bound_at=None,
            last_seen_at=None,
            last_location_at=None,
            background_location_granted=None,
            battery_optimization_ignored=None,
            location_services_enabled=None,
            last_service_error=None,
        )
    latest_valid_point = db.scalar(
        select(TrackPoint)
        .where(
            TrackPoint.device_id == device.id,
            TrackPoint.valid_for_safety.is_(True),
        )
        .order_by(TrackPoint.captured_at.desc(), TrackPoint.sequence.desc())
        .limit(1)
    )
    now = datetime.now(timezone.utc)
    last_seen = (
        device.last_seen_at
        if device.last_seen_at and device.last_seen_at.tzinfo
        else device.last_seen_at.replace(tzinfo=timezone.utc)
        if device.last_seen_at
        else None
    )
    if not latest_valid_point:
        device_state = "bound_waiting_location"
    elif last_seen and now - min(aware(latest_valid_point.captured_at), last_seen) <= timedelta(minutes=10):
        device_state = "online"
    else:
        device_state = "stale"
    return DeviceStatusView(
        state=device_state,
        model=device.model,
        app_version=device.app_version,
        bound_at=device.created_at,
        last_seen_at=device.last_seen_at,
        last_location_at=latest_valid_point.captured_at if latest_valid_point else None,
        background_location_granted=device.background_location_granted,
        battery_optimization_ignored=device.battery_optimization_ignored,
        location_services_enabled=device.location_services_enabled,
        last_service_error=device.last_service_error,
    )


@router.post(
    "/{family_id}/elders/{elder_id}/locate-requests",
    response_model=LocateRequestView,
    status_code=status.HTTP_201_CREATED,
)
def create_locate_request(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> LocateRequestView:
    require_membership(family_id, current, db)
    get_elder(db, family_id, elder_id)
    device = db.scalar(
        select(ElderDevice)
        .where(ElderDevice.elder_id == elder_id, ElderDevice.active.is_(True))
        .order_by(ElderDevice.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if not device:
        raise HTTPException(status_code=409, detail="老人手机尚未绑定")

    now = datetime.now(timezone.utc)
    active_requests = list(
        db.scalars(
            select(DeviceLocateRequest).where(
                DeviceLocateRequest.elder_id == elder_id,
                DeviceLocateRequest.state.in_(ACTIVE_LOCATE_STATES),
            )
        )
    )
    for item in active_requests:
        expire_locate_request(db, item)
    if any(item.state in ACTIVE_LOCATE_STATES for item in active_requests):
        db.commit()
        raise HTTPException(status_code=409, detail="已有一次立即定位正在进行")

    latest = db.scalar(
        select(DeviceLocateRequest)
        .where(DeviceLocateRequest.elder_id == elder_id)
        .order_by(DeviceLocateRequest.requested_at.desc())
        .limit(1)
    )
    if latest:
        elapsed = (now - aware(latest.requested_at)).total_seconds()
        if elapsed < 60:
            retry_after = max(1, math.ceil(60 - elapsed))
            raise HTTPException(
                status_code=429,
                detail="立即定位操作过于频繁",
                headers={"Retry-After": str(retry_after)},
            )

    request = DeviceLocateRequest(
        family_id=family_id,
        elder_id=elder_id,
        device_id=device.id,
        requested_by_user_id=current.user.id,
        state="pending",
        requested_at=now,
        expires_at=now + timedelta(seconds=45),
    )
    db.add(request)
    db.flush()
    emit_locate_event(db, request)
    db.commit()
    return locate_request_view(db, request)


@router.get(
    "/{family_id}/elders/{elder_id}/locate-requests/{request_id}",
    response_model=LocateRequestView,
)
def get_locate_request(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    request_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LocateRequestView:
    require_membership(family_id, current, db)
    get_elder(db, family_id, elder_id)
    request = db.scalar(
        select(DeviceLocateRequest).where(
            DeviceLocateRequest.id == request_id,
            DeviceLocateRequest.family_id == family_id,
            DeviceLocateRequest.elder_id == elder_id,
        )
    )
    if not request:
        raise HTTPException(status_code=404, detail="立即定位请求不存在")
    before = request.state
    expire_locate_request(db, request)
    if request.state != before:
        db.commit()
    return locate_request_view(db, request)


@router.get("/{family_id}/elders/{elder_id}/latest", response_model=LatestLocationView | None)
def latest_location(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LatestLocationView | None:
    require_membership(family_id, current, db)
    get_elder(db, family_id, elder_id)
    point = db.scalar(
        select(TrackPoint).join(ElderDevice, ElderDevice.id == TrackPoint.device_id)
        .where(
            ElderDevice.active.is_(True),
            TrackPoint.elder_id == elder_id,
            TrackPoint.valid_for_safety.is_(True),
        )
        .order_by(TrackPoint.captured_at.desc(), TrackPoint.sequence.desc())
        .limit(1)
    )
    if not point:
        return None
    now = datetime.now(timezone.utc)
    captured = point.captured_at if point.captured_at.tzinfo else point.captured_at.replace(tzinfo=timezone.utc)
    return LatestLocationView(
        sequence=point.sequence,
        coordinate_system=point.coordinate_system,
        received_at=aware(point.received_at),
        captured_at=captured,
        latitude=point.latitude,
        longitude=point.longitude,
        accuracy_m=point.accuracy_m,
        speed_mps=point.speed_mps,
        battery_pct=point.battery_pct,
        is_charging=point.is_charging,
        stale=now - captured > timedelta(minutes=10),
    )


@router.get("/{family_id}/elders/{elder_id}/track-days", response_model=list[TrackDayView])
def list_track_days(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TrackDayView]:
    require_membership(family_id, current, db)
    get_elder(db, family_id, elder_id)
    elder = get_elder(db, family_id, elder_id)
    today = datetime.now(ZoneInfo(elder.timezone)).date()
    from_date = from_date or today - timedelta(days=89)
    to_date = to_date or today
    if to_date < from_date or (to_date - from_date).days > 90:
        raise HTTPException(status_code=422, detail="轨迹日期范围最多为 90 天")
    days = db.scalars(
        select(TrackDay)
        .where(
            TrackDay.elder_id == elder_id,
            TrackDay.local_date >= from_date,
            TrackDay.local_date <= to_date,
        )
        .order_by(TrackDay.local_date.desc())
    )
    return [
        TrackDayView(
            date=day.local_date,
            started_at=day.started_at,
            ended_at=day.ended_at,
            valid_point_count=day.valid_point_count,
            total_point_count=day.total_point_count,
            distance_m=day.distance_m,
            gap_count=day.gap_count,
        )
        for day in days
    ]


@router.get("/{family_id}/elders/{elder_id}/track-days/{track_date}", response_model=TrackDayDetail)
def get_track_day(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    track_date: date,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_membership(family_id, current, db)
    get_elder(db, family_id, elder_id)
    day = db.scalar(
        select(TrackDay).where(TrackDay.elder_id == elder_id, TrackDay.local_date == track_date)
    )
    if not day:
        raise HTTPException(status_code=404, detail="该日没有轨迹")
    points = list(
        db.scalars(
            select(TrackPoint)
            .where(TrackPoint.track_day_id == day.id)
            .order_by(TrackPoint.captured_at, TrackPoint.sequence)
        )
    )
    segments, skipped = build_segments(points)
    summary = TrackDayView(
        date=day.local_date,
        started_at=day.started_at,
        ended_at=day.ended_at,
        valid_point_count=day.valid_point_count,
        total_point_count=day.total_point_count,
        distance_m=day.distance_m,
        gap_count=day.gap_count,
    )
    return {"summary": summary, "segments": segments, "skippedPointCount": skipped}


@router.put("/{family_id}/elders/{elder_id}/home-geofence", response_model=HomeGeofenceView)
def put_home_geofence(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    payload: HomeGeofencePut,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> HomeGeofenceView:
    require_membership(family_id, current, db, admin=True)
    elder = get_elder(db, family_id, elder_id)
    fence = db.scalar(select(HomeGeofence).where(HomeGeofence.elder_id == elder.id))
    if not fence:
        fence = HomeGeofence(elder_id=elder.id, latitude=payload.latitude, longitude=payload.longitude)
        db.add(fence)
    else:
        fence.latitude = payload.latitude
        fence.longitude = payload.longitude
    state = db.scalar(select(GeofenceState).where(GeofenceState.elder_id == elder.id))
    if not state:
        state = GeofenceState(elder_id=elder.id)
        db.add(state)
    else:
        state.status = "unknown"
        state.consecutive_inside = 0
        state.consecutive_outside = 0
        state.last_valid_location_at = None
    db.flush()
    db.add(
        EventLog(
            family_id=family_id,
            elder_id=elder.id,
            event_type="home-geofence.updated",
            payload={"elderId": str(elder.id)},
        )
    )
    db.commit()
    return HomeGeofenceView(
        elder_id=elder.id,
        latitude=fence.latitude,
        longitude=fence.longitude,
        radius_m=fence.radius_m,
        status=state.status,
        last_valid_location_at=state.last_valid_location_at,
        updated_at=fence.updated_at,
    )


@router.get("/{family_id}/elders/{elder_id}/home-geofence", response_model=HomeGeofenceView | None)
def get_home_geofence(
    family_id: uuid.UUID,
    elder_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HomeGeofenceView | None:
    require_membership(family_id, current, db)
    get_elder(db, family_id, elder_id)
    fence = db.scalar(select(HomeGeofence).where(HomeGeofence.elder_id == elder_id))
    if not fence:
        return None
    state = db.scalar(select(GeofenceState).where(GeofenceState.elder_id == elder_id))
    return HomeGeofenceView(
        elder_id=elder_id,
        latitude=fence.latitude,
        longitude=fence.longitude,
        radius_m=fence.radius_m,
        status=state.status if state else "unknown",
        last_valid_location_at=state.last_valid_location_at if state else None,
        updated_at=fence.updated_at,
    )


@router.post("/{family_id}/push-subscriptions", response_model=PushSubscriptionView)
def create_push_subscription(
    family_id: uuid.UUID,
    payload: PushSubscriptionCreate,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> PushSubscriptionView:
    require_membership(family_id, current, db)
    subscription = db.scalar(
        select(PushSubscription).where(
            PushSubscription.family_id == family_id,
            PushSubscription.user_id == current.user.id,
            PushSubscription.endpoint == payload.endpoint,
        )
    )
    if not subscription:
        subscription = PushSubscription(
            family_id=family_id,
            user_id=current.user.id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
        )
        db.add(subscription)
    else:
        subscription.p256dh = payload.keys.p256dh
        subscription.auth = payload.keys.auth
    db.commit()
    return PushSubscriptionView(id=subscription.id, vapid_public_key=settings.vapid_public_key)


@router.delete("/{family_id}/push-subscriptions/{subscription_id}", status_code=204)
def delete_push_subscription(
    family_id: uuid.UUID,
    subscription_id: uuid.UUID,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    require_membership(family_id, current, db)
    subscription = db.scalar(
        select(PushSubscription).where(
            PushSubscription.id == subscription_id,
            PushSubscription.family_id == family_id,
            PushSubscription.user_id == current.user.id,
        )
    )
    if subscription:
        db.delete(subscription)
        db.commit()


@router.get("/{family_id}/events/stream")
async def stream_events(
    family_id: uuid.UUID,
    request: Request,
    after: int = 0,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    require_membership(family_id, current, db)

    async def generator():
        cursor = after
        while not await request.is_disconnected():
            with SessionLocal() as event_db:
                events = list(
                    event_db.scalars(
                        select(EventLog)
                        .where(EventLog.family_id == family_id, EventLog.id > cursor)
                        .order_by(EventLog.id)
                        .limit(100)
                    )
                )
                for event in events:
                    cursor = event.id
                    data = json.dumps(event.payload, ensure_ascii=False, default=str)
                    yield f"id: {event.id}\nevent: {event.event_type}\ndata: {data}\n\n"
            if not events:
                yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(generator(), media_type="text/event-stream")
