"""扩展配对与访问时快照 API（REFACTOR_PLAN §3 M1 影子模式）。

- POST /api/ext/pair-code：登录态生成 6 位配对码（10 分钟有效，一次性）；
- POST /api/ext/pair：码换 Bearer token（token 仅存 sha256）；
- POST /api/ext/snapshots：Bearer 认证上报快照（同注册域节流 + payload hash 去重），
  后端现场解析（services/ingest），影子模式只记录结果不落卡；
- GET  /api/ext/me：扩展侧查询配对状态。

Cookie 永不离开浏览器：本组接口不接收任何 Cookie 字段。
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.db.models import DeviceToken, Snapshot, User
from app.services import ingest as ingest_mod

router = APIRouter(prefix="/ext", tags=["ext"])

NETWORK_MAX = 40
NETWORK_BODY_MAX = 262_144  # 与插件 v0.5 捕获上限对齐（256KB）
RESOURCES_MAX = 50


def _utcnow() -> datetime:
    from app.services.bindings import utcnow

    return utcnow()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
                "truncated": bool(entry.get("truncated")) or len(str(entry.get("response_body") or "")) > NETWORK_BODY_MAX,
            }
        )
    return cleaned


# ── 配对 ──────────────────────────────────────────────


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    device_label: str | None = Field(default=None, max_length=128)


class PairCodeOut(BaseModel):
    code: str
    expires_at: datetime


@router.post("/pair-code", response_model=PairCodeOut, status_code=201)
def create_pair_code(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """生成 6 位配对码；同账号此前未用的码立即作废（一码一流）。"""
    now = _utcnow()
    for row in db.scalars(
        select(DeviceToken).where(DeviceToken.user_id == user.id, DeviceToken.status == "pending")
    ):
        row.status = "expired"
        row.code = None
    token = DeviceToken(
        user_id=user.id,
        code=f"{secrets.randbelow(1000000):06d}",
        status="pending",
        expires_at=now + timedelta(minutes=settings.pair_code_ttl_minutes),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return PairCodeOut(code=token.code, expires_at=token.expires_at)


@router.post("/pair", status_code=201)
def pair(payload: PairIn, db: Session = Depends(get_db)):
    """码换 token：一次性，过期/复用一律拒绝（不提示具体原因，防枚举探测）。"""
    row = db.scalar(
        select(DeviceToken).where(DeviceToken.code == payload.code, DeviceToken.status == "pending")
    )
    if row is None:
        raise HTTPException(400, "配对码无效或已使用")
    if row.expires_at and row.expires_at < _utcnow():
        row.status = "expired"
        row.code = None
        db.commit()
        raise HTTPException(400, "配对码已过期，请回平台重新生成")
    bearer = secrets.token_hex(24)
    row.status = "paired"
    row.code = None  # 一次性：用后即焚
    row.token_hash = _sha256(bearer)
    row.device_label = payload.device_label
    row.paired_at = _utcnow()
    db.commit()
    return {"token": bearer, "email": row.user.email}


def get_device_session(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
) -> tuple[User, DeviceToken]:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer 凭证")
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(401, "缺少 Bearer 凭证")
    row = db.scalar(select(DeviceToken).where(DeviceToken.token_hash == _sha256(token)))
    if row is None or row.status != "paired":
        raise HTTPException(401, "设备凭证无效，请重新配对")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "账号不存在")
    row.last_seen_at = _utcnow()
    return user, row


@router.get("/me")
def device_me(session: tuple[User, DeviceToken] = Depends(get_device_session)):
    user, row = session
    return {
        "email": user.email,
        "device_label": row.device_label,
        "paired_at": row.paired_at,
        "last_seen_at": row.last_seen_at,
    }


@router.get("/sites")
def device_sites(db: Session = Depends(get_db), session: tuple[User, DeviceToken] = Depends(get_device_session)):
    """已连接站点清单：扩展每小时后台自动同步（隐藏 tab 回访最新快照页）的数据源。"""
    user, _device = session
    return {"sites": ingest_mod.list_connected_sites(db, user.id)}


# ── 快照上报 ─────────────────────────────────────────


class SnapshotIn(BaseModel):
    url: str = Field(min_length=4, max_length=500)
    network: list[dict] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    dom: str | None = Field(default=None, max_length=600_000)  # 裁剪渲染 HTML（DOM 兜底原料）
    manual: bool = False  # 手动「同步当前页」：豁免同站节流（人手点击天然限频）
    login_suspect: bool = False


@router.post("/snapshots", status_code=201)
def upload_snapshot(
    payload: SnapshotIn,
    db: Session = Depends(get_db),
    session: tuple[User, DeviceToken] = Depends(get_device_session),
):
    user, _device = session
    from urllib.parse import urlparse

    host = urlparse(payload.url).netloc.lower()
    domain = ingest_mod.registrable_domain(host) or host
    now = _utcnow()
    phash = ingest_mod.payload_hash(payload.network, payload.dom)

    # 同注册域节流 + hash 去重：按 site_key 隔离——Moka 多租户同注册域
    # （星环/炎魂都在 mokahr.com），互不挤占节流窗口、互不去重
    site = ingest_mod.site_key(payload.url)
    cands = list(
        db.scalars(
            select(Snapshot)
            .where(Snapshot.user_id == user.id, Snapshot.domain == domain)
            .order_by(Snapshot.id.desc())
            .limit(settings.snapshot_keep_per_domain)
        )
    )
    latest = next((s for s in cands if ingest_mod.site_key(s.url or "") == site), None)
    if latest is not None:
        if latest.payload_hash == phash:
            # 数据未变化 ≠ 看板完整：用户可能删过卡片。对已有快照重放解析+
            # ingest diff（幂等：存在的卡走 unchanged，缺失的卡补建），不新建快照行。
            healed = ingest_mod.ingest_snapshot(db, latest)
            out = {
                "status": "duplicate",
                "snapshot_id": latest.id,
                "parsed_count": healed.get("parsed_count", 0),
            }
            if healed.get("ingest"):
                out["ingest"] = healed["ingest"]
            if healed.get("note"):
                out["note"] = healed["note"]
            return JSONResponse(status_code=200, content=out)
        # 同站节流只约束自动采集（防检测器漏判/重放风暴）；手动同步是用户明确
        # 意图且天然限频，豁免——否则测试/重试场景里「自动上报刚成功、手动点击
        # 就撞 429 入队干等 11 分钟」（实盘 9/14 次 429 皆此因）
        if not payload.manual and latest.created_at and latest.created_at > now - timedelta(
            minutes=settings.snapshot_throttle_minutes
        ):
            raise HTTPException(429, "该站点上报过于频繁，稍后再试")

    network = _clean_network(payload.network)
    snapshot = Snapshot(
        user_id=user.id,
        url=payload.url[:500],
        domain=domain,
        payload_hash=phash,
        network=network,
        resources=[str(r)[:500] for r in payload.resources[:RESOURCES_MAX]],
        dom=payload.dom,
        login_suspect=payload.login_suspect,
        parse_status="pending",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    result = ingest_mod.ingest_snapshot(db, snapshot)

    _prune_snapshots(db, user.id, domain)
    out = {"status": result["status"], "snapshot_id": snapshot.id, "parsed_count": result["parsed_count"]}
    if result.get("route"):
        out["route"] = result["route"]
    if result.get("portal_id"):
        out["portal"] = {"id": result["portal_id"], "name": result["portal_name"]}
        out["preview"] = result.get("preview")
    if result.get("ingest"):
        out["ingest"] = result["ingest"]
    if result.get("note"):
        out["note"] = result["note"]
    return out


def _prune_snapshots(db: Session, user_id: int, domain: str) -> None:
    """每域只留存最近 N 条（原料体积大，历史价值低于 golden）。"""
    rows = list(
        db.scalars(
            select(Snapshot)
            .where(Snapshot.user_id == user_id, Snapshot.domain == domain)
            .order_by(Snapshot.id.desc())
            .limit(settings.snapshot_keep_per_domain + 1)
        )
    )
    for stale in rows[settings.snapshot_keep_per_domain:]:
        db.delete(stale)
    db.commit()
