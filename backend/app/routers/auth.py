from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import CurrentUser, get_current_user, require_csrf
from ..models import Family, FamilyMembership, Invitation, Session as LoginSession, User
from ..schemas import (
    AcceptInvitationRequest,
    AuthView,
    BootstrapRequest,
    FamilyView,
    LoginRequest,
    UserView,
)
from ..security import as_utc, expires_in, hash_password, random_token, token_hash, verify_password

router = APIRouter(prefix="/api/v1", tags=["auth"])
settings = get_settings()


def auth_view(db: Session, user: User, session: LoginSession) -> AuthView:
    memberships = db.execute(
        select(FamilyMembership, Family)
        .join(Family, Family.id == FamilyMembership.family_id)
        .where(FamilyMembership.user_id == user.id, Family.active.is_(True))
    ).all()
    return AuthView(
        user=UserView.model_validate(user),
        families=[
            FamilyView(id=family.id, name=family.name, role=membership.role)
            for membership, family in memberships
        ],
        csrf_token=session.csrf_token,
    )


def create_session(db: Session, user: User, response: Response) -> LoginSession:
    raw_token = random_token()
    login_session = LoginSession(
        user_id=user.id,
        token_hash=token_hash(raw_token),
        csrf_token=random_token(24),
        expires_at=expires_in(days=settings.session_ttl_days),
    )
    db.add(login_session)
    db.flush()
    response.set_cookie(
        "session_id",
        raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_days * 86400,
        path="/",
    )
    return login_session


@router.post("/bootstrap", response_model=AuthView, status_code=status.HTTP_201_CREATED)
def bootstrap(
    payload: BootstrapRequest,
    response: Response,
    bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
    db: Session = Depends(get_db),
) -> AuthView:
    if not settings.bootstrap_token or bootstrap_token != settings.bootstrap_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="初始化令牌无效")
    if db.scalar(select(func.count()).select_from(Family)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="系统已经初始化")
    family = Family(name=payload.family_name.strip())
    user = User(email=str(payload.email).lower(), password_hash=hash_password(payload.password))
    db.add_all([family, user])
    db.flush()
    db.add(FamilyMembership(family_id=family.id, user_id=user.id, role="admin"))
    login_session = create_session(db, user, response)
    db.commit()
    return auth_view(db, user, login_session)


@router.post("/auth/login", response_model=AuthView)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> AuthView:
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if not user or not user.active or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    login_session = create_session(db, user, response)
    db.commit()
    return auth_view(db, user, login_session)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    current: CurrentUser = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> None:
    db.delete(current.session)
    db.commit()
    response.delete_cookie("session_id", path="/")


@router.get("/auth/me", response_model=AuthView)
def me(
    current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> AuthView:
    return auth_view(db, current.user, current.session)


@router.post("/auth/accept-invitation", response_model=AuthView)
def accept_invitation(
    payload: AcceptInvitationRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthView:
    invitation = db.scalar(
        select(Invitation).where(Invitation.token_hash == token_hash(payload.token))
    )
    now = datetime.now(timezone.utc)
    if not invitation or invitation.accepted_at or as_utc(invitation.expires_at) < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="邀请已失效")
    email = invitation.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    if user:
        if not verify_password(user.password_hash, payload.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="该邮箱已有账号，请输入原密码")
        membership = db.scalar(
            select(FamilyMembership).where(
                FamilyMembership.family_id == invitation.family_id,
                FamilyMembership.user_id == user.id,
            )
        )
        if membership:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="已经是该家庭成员")
    else:
        user = User(email=email, password_hash=hash_password(payload.password))
        db.add(user)
        db.flush()
    db.add(FamilyMembership(family_id=invitation.family_id, user_id=user.id, role="admin"))
    invitation.accepted_at = now
    login_session = create_session(db, user, response)
    db.commit()
    return auth_view(db, user, login_session)
