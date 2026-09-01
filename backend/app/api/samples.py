"""采样管线 API（L2 配方生成的前置，DESIGN.md §5）。

流程：用户在向导发起采样（intent）→ 到目标官网登录并打开「我的投递」页 →
点插件图标采集（DOM + XHR 清单）凭一次性 token 提交 → 管理员/LLM 据此生成门户配置。
"""

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_current_user
from app.db.database import get_db
from app.db.models import Portal, Sample, User

router = APIRouter(prefix="/samples", tags=["samples"])

DOM_MAX_CHARS = 600_000
RESOURCES_MAX = 50


class SampleOut(BaseModel):
    id: int
    url: str | None
    status: str
    portal_id: int | None
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SampleDetail(SampleOut):
    user_email: str = ""
    dom: str | None
    resources: list | None


class SubmitIn(BaseModel):
    token: str = Field(min_length=8, max_length=64)
    url: str = Field(min_length=4, max_length=500)
    dom: str = Field(min_length=10)
    resources: list[str] = Field(default_factory=list)


class PatchIn(BaseModel):
    status: str | None = Field(default=None, pattern="^(pending|new|used|failed)$")
    note: str | None = None
    portal_id: int | None = None


def _utcnow() -> datetime:
    from app.services.bindings import utcnow

    return utcnow()


@router.post("/intents", status_code=201)
def create_intent(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sample = Sample(
        user_id=user.id,
        status="pending",
        token=secrets.token_hex(16),
        token_expires_at=_utcnow() + timedelta(minutes=30),
    )
    db.add(sample)
    db.commit()
    db.refresh(sample)
    return {"id": sample.id, "token": sample.token, "expires_at": sample.token_expires_at}


@router.post("/submit")
def submit_sample(payload: SubmitIn, db: Session = Depends(get_db)):
    """插件提交入口：凭一次性 token 认证，无需用户会话。"""
    sample = db.scalar(select(Sample).where(Sample.token == payload.token))
    if sample is None or sample.status != "pending":
        raise HTTPException(400, "采样凭证无效或已使用")
    if sample.token_expires_at and sample.token_expires_at < _utcnow():
        raise HTTPException(400, "采样凭证已过期，请回到平台重新发起")

    # 尝试关联门户（按域名匹配，含未启用门户）
    from urllib.parse import urlparse

    host = urlparse(payload.url).netloc.lower()
    portal_id = None
    for portal in db.scalars(select(Portal)):
        if any(d.lower() in host for d in portal.domains or []):
            portal_id = portal.id
            break

    sample.url = payload.url[:500]
    sample.dom = payload.dom[:DOM_MAX_CHARS]
    sample.resources = [str(r)[:500] for r in payload.resources[:RESOURCES_MAX]]
    sample.status = "new"
    sample.portal_id = portal_id
    sample.token = None  # 一次性凭证用后即焚
    sample.token_expires_at = None
    db.commit()
    return {"ok": True, "id": sample.id}


@router.get("/mine", response_model=list[SampleOut])
def my_samples(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return list(
        db.scalars(
            select(Sample).where(Sample.user_id == user.id).order_by(Sample.id.desc()).limit(20)
        )
    )


@router.get("", response_model=list[SampleOut])
def list_samples(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    return list(db.scalars(select(Sample).order_by(Sample.id.desc()).limit(100)))


@router.get("/{sample_id}", response_model=SampleDetail)
def get_sample(sample_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    sample = db.get(Sample, sample_id)
    if sample is None:
        raise HTTPException(404, "采样不存在")
    detail = SampleDetail.model_validate(sample)
    detail.user_email = sample.user.email
    return detail


@router.patch("/{sample_id}", response_model=SampleOut)
def patch_sample(
    sample_id: int,
    payload: PatchIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    sample = db.get(Sample, sample_id)
    if sample is None:
        raise HTTPException(404, "采样不存在")
    if payload.status is not None:
        sample.status = payload.status
    if payload.note is not None:
        sample.note = payload.note
    if payload.portal_id is not None:
        if db.get(Portal, payload.portal_id) is None:
            raise HTTPException(400, "门户不存在")
        sample.portal_id = payload.portal_id
    db.commit()
    db.refresh(sample)
    return sample
