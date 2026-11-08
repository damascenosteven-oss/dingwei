from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import ElderDevice, FamilyMembership, Session as LoginSession, User
from .security import as_utc, token_hash


@dataclass
class CurrentUser:
    user: User
    session: LoginSession


def get_current_user(
    db: Session = Depends(get_db),
    session_id: str | None = Cookie(default=None),
) -> CurrentUser:
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    login_session = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == token_hash(session_id))
    )
    now = datetime.now(timezone.utc)
    if not login_session or as_utc(login_session.expires_at) < now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期")
    user = db.get(User, login_session.user_id)
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return CurrentUser(user=user, session=login_session)


def require_csrf(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> CurrentUser:
    if request.method not in {"GET", "HEAD", "OPTIONS"} and csrf_token != current.session.csrf_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")
    return current


def require_membership(
    family_id: uuid.UUID,
    current: CurrentUser,
    db: Session,
    admin: bool = False,
) -> FamilyMembership:
    membership = db.scalar(
        select(FamilyMembership).where(
            FamilyMembership.family_id == family_id,
            FamilyMembership.user_id == current.user.id,
        )
    )
    if not membership or (admin and membership.role != "admin"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="家庭不存在")
    return membership


def get_current_device(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ElderDevice:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="设备未认证")
    device = db.scalar(
        select(ElderDevice).where(
            ElderDevice.token_hash == token_hash(authorization.removeprefix("Bearer ").strip()),
            ElderDevice.active.is_(True),
        )
    )
    if not device:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="设备令牌无效")
    return device
