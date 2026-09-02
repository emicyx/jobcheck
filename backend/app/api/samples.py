"""采样管线 API（DESIGN.md §5 / LLM_DESIGN.md）。

流程：用户在向导发起采样（intent）→ 到目标官网登录并打开「我的投递」页 →
点插件图标采集（DOM + 请求-响应对）凭一次性 token 提交 → 配方管线自动运行：
结构指纹（免 LLM）→ 未命中则 T1 生成 → 回放验证 → 免审批发布，向导轮询感知。
"""

import secrets
import threading
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.db.models import Portal, Sample, User

router = APIRouter(prefix="/samples", tags=["samples"])

DOM_MAX_CHARS = 600_000
RESOURCES_MAX = 50
NETWORK_MAX = 40
NETWORK_BODY_MAX = 262_144  # 与插件 v0.4.13 的捕获上限对齐（256KB）


class SampleOut(BaseModel):
    id: int
    url: str | None
    status: str
    portal_id: int | None
    pipeline_status: str | None
    pipeline_note: str | None
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
    # M4：fetch/XHR 包装捕获的请求-响应对（插件 ≥0.4）
    network: list[dict] = Field(default_factory=list)


class PatchIn(BaseModel):
    status: str | None = Field(default=None, pattern="^(pending|new|used|failed)$")
    note: str | None = None
    portal_id: int | None = None


def _utcnow() -> datetime:
    from app.services.bindings import utcnow

    return utcnow()


def _clean_network(entries: list[dict]) -> list[dict]:
    cleaned = []
    for entry in entries[:NETWORK_MAX]:
        if not isinstance(entry, dict):
            continue
        cleaned.append(
            {
                "url": str(entry.get("url") or "")[:1000],
                "method": str(entry.get("method") or "GET").upper()[:8],
                "params": {str(k): str(v)[:300] for k, v in (entry.get("params") or {}).items()} if isinstance(entry.get("params"), dict) else {},
                "request_body": str(entry.get("request_body") or "")[:4000],
                "response_body": str(entry.get("response_body") or "")[:NETWORK_BODY_MAX],
                # 响应体是否因超上限被截断（截断的 JSON 解析必败，管线据此给出升级插件提示）
                "truncated": bool(entry.get("truncated")) or len(str(entry.get("response_body") or "")) > NETWORK_BODY_MAX,
            }
        )
    return cleaned


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
    sample.network = _clean_network(payload.network)
    sample.status = "new"
    sample.portal_id = portal_id
    sample.token = None  # 一次性凭证用后即焚
    sample.token_expires_at = None
    db.commit()

    # 配方管线自动运行（后台执行，向导轮询 samples/mine 感知结果）。
    # 请求-响应对缺失时同样进入管线：立即以明确原因失败并呈现给向导，
    # 而不是静默跳过导致向导无限等待"正在生成"。
    if settings.recipe_pipeline_enabled:
        _run_pipeline_async(sample.id)
    return {"ok": True, "id": sample.id}


def _run_pipeline_async(sample_id: int) -> None:
    import logging

    from app.db.database import SessionLocal
    from app.llm.pipeline import run_pipeline

    logger = logging.getLogger("jobcheck.samples")

    def worker() -> None:
        try:
            with SessionLocal() as session:
                run_pipeline(session, sample_id)
        except Exception as e:  # noqa: BLE001 后台线程不能向外抛
            logger.exception("配方管线后台执行失败 sample=%s: %s", sample_id, e)
            try:
                with SessionLocal() as session:
                    failed = session.get(Sample, sample_id)
                    if failed and failed.pipeline_status == "generating":
                        failed.pipeline_status = "failed"
                        failed.pipeline_note = f"管线内部错误: {e}"
                        session.commit()
            except Exception:
                logger.exception("标记失败状态时出错 sample=%s", sample_id)

    threading.Thread(target=worker, daemon=True, name=f"recipe-pipeline-{sample_id}").start()


@router.post("/{sample_id}/retry", response_model=dict)
def retry_pipeline(
    sample_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
):
    """管理后台干跑重试：对历史样本强制重跑管线（绕过冷却，同步返回结果）。"""
    from app.llm.pipeline import run_pipeline

    sample = db.get(Sample, sample_id)
    if sample is None:
        raise HTTPException(404, "采样不存在")
    if sample.status == "pending":
        raise HTTPException(400, "该采样还没有提交内容")
    result = run_pipeline(db, sample_id, force=True)
    return {
        "status": result.status,
        "portal_id": result.portal_id,
        "note": result.note,
        "errors": result.errors,
        "route": result.route,
    }


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
