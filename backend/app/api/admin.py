"""管理后台 API（最小版，M4）：LLM 用量记账与配方健康度；M1：快照影子链路观测。
管理后台界面（M3）：overview / users / applications-stats 只读聚合。"""

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user
from app.db.database import get_db
from app.db.models import LLMCall, Application, Portal, Recipe, Snapshot, User
from app.domain.statuses import all_defs
from app.llm import client

router = APIRouter(prefix="/admin", tags=["admin"])


class LLMCallOut(BaseModel):
    id: int
    task: str
    provider: str
    model: str
    prompt_version: str
    tokens_in: int
    tokens_out: int
    cost_cny: float
    latency_ms: int
    ok: bool
    error: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RecipeOut(BaseModel):
    id: int
    portal_id: int
    portal_name: str
    status: str
    source: str
    confidence: float
    attempts: int
    last_errors: list | None
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("/llm-calls", response_model=list[LLMCallOut])
def list_llm_calls(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    return list(db.scalars(select(LLMCall).order_by(LLMCall.id.desc()).limit(100)))


@router.get("/llm-usage")
def llm_usage(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    """月度用量汇总（预算熔断的可见性）。"""
    return {
        "month_cost_cny": round(client.monthly_cost_cny(db), 4),
        "budget_cny": client.settings.llm_monthly_budget_cny,
    }


@router.get("/recipes", response_model=list[RecipeOut])
def list_recipes(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    rows = list(db.scalars(select(Recipe).order_by(Recipe.id.desc()).limit(100)))
    portals = {p.id: p.name for p in db.scalars(select(Portal))}
    out = []
    for r in rows:
        item = RecipeOut.model_validate(r)
        item.portal_name = portals.get(r.portal_id, f"#{r.portal_id}")
        out.append(item)
    return out


# ── 访问时快照（M1 影子链路观测；M3 闸门指标数据源）──────────────


class SnapshotOut(BaseModel):
    id: int
    user_email: str = ""
    url: str
    domain: str
    portal_id: int | None
    portal_name: str | None = None
    parse_status: str
    parse_route: str | None
    parsed_count: int
    login_suspect: bool
    parse_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    rows = list(db.scalars(select(Snapshot).order_by(Snapshot.id.desc()).limit(100)))
    portals = {p.id: p.name for p in db.scalars(select(Portal))}
    users = {u.id: u.email for u in db.scalars(select(User))}
    out = []
    for s in rows:
        item = SnapshotOut.model_validate(s)
        item.user_email = users.get(s.user_id, f"#{s.user_id}")
        item.portal_name = portals.get(s.portal_id) if s.portal_id else None
        out.append(item)
    return out


@router.get("/snapshots/stats")
def snapshot_stats(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    """影子跑数指标：捕获成功率（含 JSON 载荷的比例）、解析成功率、按域分布。"""
    rows = list(db.scalars(select(Snapshot).order_by(Snapshot.id.desc()).limit(1000)))
    by_domain: dict[str, dict] = {}
    by_route: dict[str, int] = {}
    for s in rows:
        d = by_domain.setdefault(s.domain, {"total": 0, "parsed": 0, "capture_ok": 0, "login_suspect": 0})
        d["total"] += 1
        if s.parse_status == "parsed":
            d["parsed"] += 1
            if s.parse_route:
                by_route[s.parse_route] = by_route.get(s.parse_route, 0) + 1
        if s.network:
            d["capture_ok"] += 1
        if s.login_suspect:
            d["login_suspect"] += 1
    total = len(rows)
    return {
        "total": total,
        "parsed": sum(1 for s in rows if s.parse_status == "parsed"),
        "capture_ok": sum(1 for s in rows if s.network),
        "login_suspect": sum(1 for s in rows if s.login_suspect),
        "by_domain": by_domain,
        "by_route": by_route,
    }


@router.post("/snapshots/{snapshot_id}/reparse")
def reparse_snapshot(snapshot_id: int, db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    """干跑重解析（语义继承 samples/retry）：跳过 hints 全量重扫，命中即更新 hints。

    解析 bug 服务端热修后的验证入口——不发插件。
    """
    from app.services.ingest import ingest_snapshot

    snapshot = db.get(Snapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(404, "快照不存在")
    result = ingest_snapshot(db, snapshot, skip_hints=True)
    return {
        "status": result["status"],
        "parsed_count": result.get("parsed_count", 0),
        "route": result.get("route"),
        "portal_id": result.get("portal_id"),
        "note": result.get("note"),
    }


# ── 管理后台界面聚合（只读，M3）────────────────────────────

TREND_DAYS = 14


def _rate(part: int, total: int) -> float | None:
    return round(part / total, 4) if total else None


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    """总览：核心指标卡 + 近 14 天趋势（新增用户/新增投递/快照上报与解析/LLM 成本）。"""
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    start = today - timedelta(days=TREND_DAYS - 1)
    cutoff = datetime.combine(start, time.min)

    days = [(start + timedelta(days=i)).isoformat() for i in range(TREND_DAYS)]
    idx = {d: i for i, d in enumerate(days)}
    new_users = [0] * TREND_DAYS
    new_apps = [0] * TREND_DAYS
    snaps = [0] * TREND_DAYS
    snaps_parsed = [0] * TREND_DAYS
    llm_cost = [0.0] * TREND_DAYS

    for created_at in db.scalars(select(User.created_at).where(User.created_at >= cutoff)):
        key = created_at.date().isoformat()
        if key in idx:
            new_users[idx[key]] += 1
    for created_at in db.scalars(select(Application.created_at).where(Application.created_at >= cutoff)):
        key = created_at.date().isoformat()
        if key in idx:
            new_apps[idx[key]] += 1
    window_snaps = 0
    window_capture_ok = 0
    for created_at, parse_status, network in db.execute(
        select(Snapshot.created_at, Snapshot.parse_status, Snapshot.network).where(Snapshot.created_at >= cutoff)
    ):
        key = created_at.date().isoformat()
        if key not in idx:
            continue
        i = idx[key]
        snaps[i] += 1
        window_snaps += 1
        if parse_status == "parsed":
            snaps_parsed[i] += 1
        if network:
            window_capture_ok += 1
    for created_at, cost in db.execute(
        select(LLMCall.created_at, LLMCall.cost_cny).where(LLMCall.created_at >= cutoff)
    ):
        key = created_at.date().isoformat()
        if key in idx:
            llm_cost[idx[key]] += float(cost)

    return {
        "users_total": db.scalar(select(func.count()).select_from(User)) or 0,
        "applications_total": db.scalar(select(func.count()).select_from(Application)) or 0,
        "snapshots_total": db.scalar(select(func.count()).select_from(Snapshot)) or 0,
        "window": {
            "days": days,
            "new_users": new_users,
            "new_applications": new_apps,
            "snapshots": snaps,
            "snapshots_parsed": snaps_parsed,
            "llm_cost_cny": [round(v, 4) for v in llm_cost],
            "capture_ok_rate": _rate(window_capture_ok, window_snaps),
            "parse_rate": _rate(sum(snaps_parsed), window_snaps),
        },
        "llm": {
            "month_cost_cny": round(client.monthly_cost_cny(db), 4),
            "budget_cny": client.settings.llm_monthly_budget_cny,
        },
    }


@router.get("/users")
def admin_users(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    """用户数据（只读）：注册信息 + 投递/快照/连接站点用量 + 最近活跃。"""
    users = list(db.scalars(select(User).order_by(User.id.desc()).limit(200)))

    def _group(model, *cols):  # user_id → 聚合值
        return {uid: v for uid, v in db.execute(select(model.user_id, *cols).group_by(model.user_id))}

    app_counts = _group(Application, func.count())
    snap_counts = _group(Snapshot, func.count())
    sites = {
        uid: c
        for uid, c in db.execute(
            select(Snapshot.user_id, func.count(func.distinct(Snapshot.portal_id)))
            .where(Snapshot.portal_id.is_not(None))
            .group_by(Snapshot.user_id)
        )
    }
    last_snap = _group(Snapshot, func.max(Snapshot.created_at))
    last_app = _group(Application, func.max(Application.updated_at))

    out = []
    for u in users:
        last_active = max(filter(None, [last_snap.get(u.id), last_app.get(u.id)]), default=None)
        out.append(
            {
                "id": u.id,
                "email": u.email,
                "role": u.role,
                "created_at": u.created_at,
                "applications_count": app_counts.get(u.id, 0),
                "snapshots_count": snap_counts.get(u.id, 0),
                "sites_count": sites.get(u.id, 0),
                "last_active_at": last_active,
            }
        )
    return out


@router.get("/applications-stats")
def applications_stats(db: Session = Depends(get_db), _: User = Depends(get_admin_user)):
    """投递数据总览（只读）：状态分布（状态机序）/ 来源构成 / 热门公司与门户。"""
    status_counts = {
        k: c for k, c in db.execute(select(Application.current_status, func.count()).group_by(Application.current_status))
    }
    portal_ids = [
        pid
        for (pid,) in db.execute(
            select(Application.portal_id)
            .where(Application.portal_id.is_not(None))
            .group_by(Application.portal_id)
            .order_by(func.count().desc())
            .limit(10)
        )
    ]
    portals = {p.id: p.name for p in db.scalars(select(Portal).where(Portal.id.in_(portal_ids)))}
    portal_counts = {
        pid: c
        for pid, c in db.execute(
            select(Application.portal_id, func.count())
            .where(Application.portal_id.is_not(None))
            .group_by(Application.portal_id)
        )
    }
    top_portals = sorted(
        ({"portal_id": pid, "portal_name": portals.get(pid, f"#{pid}"), "count": portal_counts.get(pid, 0)} for pid in portal_ids),
        key=lambda x: -x["count"],
    )
    return {
        "total": db.scalar(select(func.count()).select_from(Application)) or 0,
        "by_source": {
            s: c for s, c in db.execute(select(Application.source, func.count()).group_by(Application.source))
        },
        "by_status": [
            {"key": d["key"], "label": d["label"], "color": d["color"], "count": status_counts.get(d["key"], 0)}
            for d in all_defs()
            if status_counts.get(d["key"], 0) > 0
        ],
        "top_companies": [
            {"company": c, "count": n}
            for c, n in db.execute(
                select(Application.company, func.count()).group_by(Application.company).order_by(func.count().desc()).limit(10)
            )
        ],
        "top_portals": top_portals,
    }
