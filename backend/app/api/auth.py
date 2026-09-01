from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SESSION_COOKIE, get_current_user
from app.core.config import settings
from app.core.security import create_session_token, hash_password, verify_password
from app.db.database import get_db
from app.db.models import InviteCode, User
from app.schemas.auth import LoginIn, RegisterIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _session_response(user: User) -> JSONResponse:
    token = create_session_token(user.id)
    return JSONResponse(
        content=UserOut.model_validate(user).model_dump(mode="json"),
        headers={},
    )


def _set_cookie(response: JSONResponse, token: str) -> JSONResponse:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=False,  # 生产环境经 HTTPS 反代后改为 True（随 M3 部署项）
        path="/",
    )
    return response


def _check_invite(db: Session, code: str) -> InviteCode:
    invite = db.scalar(select(InviteCode).where(InviteCode.code == code.strip()))
    if invite is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码不存在")
    if invite.expires_at is not None and invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码已过期")
    if invite.used_count >= invite.max_uses:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码已达使用上限")
    return invite


@router.post("/register", response_model=UserOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "该邮箱已注册")
    invite = _check_invite(db, payload.invite_code)
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        invite_code_id=invite.id,
    )
    invite.used_count += 1
    db.add(user)
    db.commit()
    db.refresh(user)
    resp = _session_response(user)
    return _set_cookie(resp, create_session_token(user.id))


@router.post("/login", response_model=UserOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "邮箱或密码错误")
    resp = _session_response(user)
    return _set_cookie(resp, create_session_token(user.id))


@router.post("/logout")
def logout():
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
