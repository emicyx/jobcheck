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
) -> dict:
    """归一化 → 与本地 diff → 落卡与历史。绑定轮询与快照 ingest 共用的唯一实现；
    binding_id 为空时（快照路径）按 (用户, 门户) 圈定既有卡片。"""
    created = updated = unchanged = 0

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
            # 只补空字段，不覆盖用户手动填写的内容
            if raw.department and not app_row.department:
                app_row.department = raw.department
                changed = True
            if raw.work_location and not app_row.work_location:
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
    return {"fetched": len(raw_list), "created": created, "updated": updated, "unchanged": unchanged}


def _match_by_title(existing: list[Application], raw: RawApplication) -> Application | None:
    for app_row in existing:
        if app_row.job_title == raw.job_title and (app_row.department or None) == (raw.department or None):
            return app_row
    return None
