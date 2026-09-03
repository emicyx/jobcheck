"""同步服务：拉取门户列表 → 归一化 → 与本地 diff → 落库与历史。"""

import random
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import AdapterContext, AdapterError, BaseAdapter, RawApplication, SessionInvalidError, get_adapter
from app.db.models import AppStatusHistory, Application, Binding, Portal, User
from app.domain import statuses
from app.llm import classify
from .bindings import cookies_to_context, persist_refreshed_cookies, utcnow


def sync_binding(db: Session, binding: Binding, adapter: BaseAdapter | None = None) -> dict:
    """执行一次同步。返回摘要；登录态失效时标记 binding 并抛 SessionInvalidError。"""
    portal = binding.portal
    if binding.cookie_blob is None:
        raise SessionInvalidError("绑定没有登录态")

    adapter = adapter or get_adapter(portal.provider_key)
    ctx = cookies_to_context(binding.cookie_blob)
    raw_list = adapter.fetch(portal.config or {}, ctx)
    # 运行期自愈刷新出的 Cookie（如飞书 CSRF 轮换）写回存储，下轮不再依赖旧值
    persist_refreshed_cookies(db, binding, ctx.refreshed_cookies)

    summary = sync_applications(db, binding, raw_list, portal)

    binding.status = "active"
    binding.consecutive_failures = 0
    binding.last_error = None
    binding.last_check_at = utcnow()
    binding.next_check_at = utcnow() + timedelta(
        hours=binding.interval_hours, minutes=random.randint(-20, 20)
    )
    portal.last_polled_at = utcnow()
    db.commit()
    return summary


def sync_applications(
    db: Session, binding: Binding, raw_list: list[RawApplication], portal: Portal
) -> dict:
    return ingest_applications(
        db, user=binding.user, portal=portal, raw_list=raw_list, binding_id=binding.id
    )


def ingest_applications(
    db: Session,
    *,
    user: User,
    portal: Portal,
    raw_list: list[RawApplication],
    binding_id: int | None = None,
    suspect_guard: bool = False,
) -> dict:
    """归一化 → 与本地 diff → 落卡与历史。绑定轮询与快照 ingest 共用的唯一实现；
    binding_id 为空时（快照路径）按 (用户, 门户) 圈定既有卡片。

    suspect_guard（DOM 提取路径启用）：解析可信度不足的数据不得让卡片状态
    「逆跳」（offer → 筛选，几乎必是选错行/字段错位）或「退化」（已知状态 →
    待确认，解析丢了语义）——两者都保留原状态、跳过写入并计数，网络层路由
    （platform/hints/heuristics）与绑定轮询不受影响。真实重新投递通常产生
    新记录/新卡，误拦率低；被拦的更新由下次同步重试，parse_note 可见计数。"""
    created = updated = unchanged = guarded = 0

    if binding_id is not None:
        match_filter = (
            Application.user_id == user.id,
            Application.binding_id == binding_id,
        )
    else:
        match_filter = (
            Application.user_id == user.id,
            Application.portal_id == portal.id,
        )
    existing = list(db.scalars(select(Application).where(*match_filter)))
    by_key: dict[str, Application] = {}
    for app_row in existing:
        key = (app_row.extra or {}).get("portal_key")
        if key:
            by_key[str(key)] = app_row

    for raw in raw_list:
        norm = classify.resolve_status(db, portal, raw.status_raw)
        app_row = by_key.get(raw.portal_key) if raw.portal_key else None
        if app_row is None:
            # 无门户唯一键时按（岗位+部门）匹配，避免重复建卡
            app_row = _match_by_title(existing, raw)

        if app_row is None:
            app_row = Application(
                user_id=user.id,
                binding_id=binding_id,
                portal_id=portal.id,
                source="auto",
                confidence="recipe",
                company=portal.company,
                job_title=raw.job_title,
                department=raw.department,
                work_location=raw.work_location,
                applied_at=raw.applied_at or date.today(),
                batch=statuses.DEFAULT_BATCH,
                current_status=norm,
                raw_status_text=raw.status_raw,
                extra={"portal_key": raw.portal_key} if raw.portal_key else {},
            )
            app_row.history.append(
                AppStatusHistory(from_status=None, to_status=norm, raw_status_text=raw.status_raw)
            )
            db.add(app_row)
            existing.append(app_row)
            if raw.portal_key:
                by_key[raw.portal_key] = app_row
            created += 1
        else:
            if (
                norm != app_row.current_status
                and suspect_guard
                and _is_status_regression(app_row.current_status, norm)
            ):
                # 可疑解析整条跳过：状态与原文/日期必须同源，不得一半新一半旧
                guarded += 1
                continue
            changed = False
            if norm != app_row.current_status:
                app_row.history.append(
                    AppStatusHistory(
                        from_status=app_row.current_status,
                        to_status=norm,
                        raw_status_text=raw.status_raw,
                    )
                )
                app_row.current_status = norm
                changed = True
            if raw.status_raw != app_row.raw_status_text:
                app_row.raw_status_text = raw.status_raw
                changed = True
            # 只补空字段，不覆盖用户手动填写的内容；纯数字地点例外——那是
            # 解析残留的地点 ID（联想实盘 workPlace=2 落进卡片），用户手填
            # 地点不会是纯数字，允许被正确地名覆盖
            if raw.department and not app_row.department:
                app_row.department = raw.department
                changed = True
            if raw.work_location and (
                not app_row.work_location or str(app_row.work_location).strip().isdigit()
            ):
                app_row.work_location = raw.work_location
                changed = True
            # 门户键后补：首录时无 id 映射、靠 title 匹配上的卡，拿到稳定键后固化，
            # 之后不再依赖岗位名不变去重（title 变了会建重复卡）
            if raw.portal_key and not (app_row.extra or {}).get("portal_key"):
                extra = dict(app_row.extra or {})
                extra["portal_key"] = raw.portal_key
                app_row.extra = extra
                changed = True
            if raw.applied_at and app_row.applied_at != raw.applied_at:
                app_row.applied_at = raw.applied_at
                changed = True
            if not app_row.binding_id:
                app_row.binding_id = binding_id
                app_row.portal_id = portal.id
                changed = True
            if changed:
                updated += 1
            else:
                unchanged += 1
            app_row.last_synced_at = utcnow()

    for app_row in existing:
        app_row.last_synced_at = utcnow()

    db.flush()
    return {
        "fetched": len(raw_list),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "guarded": guarded,
    }


def _is_status_regression(old_key: str, new_key: str) -> bool:
    """DOM 提取路径的状态护栏判定：新状态相对旧状态是「逆跳」或「解析退化」。

    - 旧状态未知（pending_confirm）→ 任何已知状态都是进展，放行；
    - 新状态未知 → 退化：解析丢了语义，不得覆盖已知状态（宁保留旧值）；
    - 其余按状态机 order 判逆序（offer → screening 类）。
    """
    from app.domain.statuses import BY_KEY

    if old_key == new_key or old_key not in BY_KEY or new_key not in BY_KEY:
        return False
    if old_key == "pending_confirm":
        return False
    if new_key == "pending_confirm":
        return True
    return BY_KEY[new_key].order < BY_KEY[old_key].order


def _match_by_title(existing: list[Application], raw: RawApplication) -> Application | None:
    for app_row in existing:
        if app_row.job_title != raw.job_title:
            continue
        # 卡片侧 department 为空 = 上次解析没提取到（未知），不代表「没有部门」：
        # 让位给 raw 侧（heuristics 无部门 → LLM 带部门的路径切换不重复建卡，
        # 联想实盘：同一投递建了两张卡）。命中后空部门由下方补空逻辑写入。
        if app_row.department and raw.department and app_row.department != raw.department:
            continue
        return app_row
    return None
