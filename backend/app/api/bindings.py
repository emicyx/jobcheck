from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.database import get_db
from app.db.models import Application, Binding, Portal, User
from app.services import bindings as binding_service

router = APIRouter(prefix="/bindings", tags=["bindings"])


class PortalBrief(BaseModel):
    id: int
    name: str
    company: str

    model_config = {"from_attributes": True}


class BindingOut(BaseModel):
    id: int
    portal: PortalBrief
    status: str
    interval_hours: int
    last_check_at: datetime | None
    next_check_at: datetime | None
    cookie_updated_at: datetime | None
    last_error: str | None
    applications_count: int = 0

    model_config = {"from_attributes": True}


class CreateBindingIn(BaseModel):
    portal_id: int


class ActivateIn(BaseModel):
    token: str = Field(min_length=8, max_length=128)
    cookies: list[dict]


def _binding_out(db: Session, binding: Binding) -> BindingOut:
    count = (
        db.scalar(
            select(func.count(Application.id)).where(Application.binding_id == binding.id)
        )
        or 0
    )
    data = BindingOut.model_validate(binding)
    data.applications_count = count
    return data


def _own_binding(db: Session, user: User, binding_id: int) -> Binding:
    binding = db.get(Binding, binding_id)
    if binding is None or binding.user_id != user.id:
        raise HTTPException(404, "绑定不存在")
    return binding


@router.get("", response_model=list[BindingOut])
def list_bindings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    bindings = list(
        db.scalars(
            select(Binding)
            .where(Binding.user_id == user.id)
            .order_by(Binding.id.desc())
        )
    )
    return [_binding_out(db, b) for b in bindings]


@router.post("", status_code=201)
def create_binding(
    payload: CreateBindingIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    portal = db.get(Portal, payload.portal_id)
    if portal is None or not portal.enabled:
        raise HTTPException(404, "门户不存在或未开放")
    existing = db.scalar(
        select(Binding).where(Binding.user_id == user.id, Binding.portal_id == portal.id)
    )
    binding = binding_service.create_binding_intent(db, user, portal, existing)
    return {
        "id": binding.id,
        "token": binding.intent_token,
        "login_url": portal.config.get("login_url"),
        "session_cookie_names": portal.config.get("session_cookie_names") or [],
        "expires_at": binding.intent_expires_at,
        "is_relogin": existing is not None,
    }


@router.post("/activate")
def activate_binding(payload: ActivateIn, db: Session = Depends(get_db)):
    """插件回传 Cookie 的入口：凭一次性 token 认证，无需用户会话。409=可重试（未完成登录）。"""
    binding = binding_service.get_binding_by_token(db, payload.token)
    if binding is None:
        raise HTTPException(400, "授权已过期，请回到平台重新发起绑定")
    try:
        result = binding_service.activate_binding(db, binding, payload.cookies)
    except binding_service.BindingError as e:
        raise HTTPException(e.status_code, str(e))
    return result


@router.get("/intents/{token}")
def intent_status(token: str, db: Session = Depends(get_db)):
    """向导页轮询绑定进度（token 即凭证，无需用户会话）。"""
    return binding_service.intent_status(db, token)


@router.post("/{binding_id}/refresh")
def refresh_binding(
    binding_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app.adapters import AdapterError, SessionInvalidError
    from app.services.sync import sync_binding

    binding = _own_binding(db, user, binding_id)
    if binding.status not in ("active", "paused"):
        raise HTTPException(400, "绑定当前不可刷新，请先重新登录")
    try:
        summary = sync_binding(db, binding)
        return {"ok": True, **summary}
    except SessionInvalidError:
        binding.status = "expired"
        binding.next_check_at = None
        db.commit()
        raise HTTPException(409, "登录态已失效，请重新登录")
    except AdapterError as e:
        db.rollback()
        raise HTTPException(502, f"抓取失败: {e}")


@router.post("/{binding_id}/relogin")
def relogin_binding(
    binding_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    binding = _own_binding(db, user, binding_id)
    binding_service.create_binding_intent(db, user, binding.portal, binding)
    return {
        "id": binding.id,
        "token": binding.intent_token,
        "login_url": binding.portal.config.get("login_url"),
        "session_cookie_names": binding.portal.config.get("session_cookie_names") or [],
    }


@router.delete("/{binding_id}")
def delete_binding(
    binding_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    binding = _own_binding(db, user, binding_id)
    # 绑定删除后，已同步的投递记录保留（转手动维护），仅断开自动追踪
    for app_row in binding_portal_apps(db, binding.id):
        app_row.binding_id = None
        app_row.source = "manual"
        app_row.confidence = "manual"
    db.delete(binding)
    db.commit()
    return {"ok": True}


def binding_portal_apps(db: Session, binding_id: int):
    return list(db.scalars(select(Application).where(Application.binding_id == binding_id)))
