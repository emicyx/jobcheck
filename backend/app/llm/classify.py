"""T2 状态兜底分类（LLM_DESIGN.md §3）：未命中规则的原文 → LLM 分类一次 → 写回规则表。

解析顺序（sync 服务入口）：db 规则表（portal > provider > generic，按优先级）
→ 门户配方 status_map → 通用兜底 → LLM 分类（缓存写回）→ 待确认。
LLM 故障/预算熔断一律降级为待确认，绝不阻塞轮询主链路。
"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Portal, StatusRule
from app.domain import normalize as normalize_mod
from app.llm import providers

logger = logging.getLogger("jobcheck.llm.classify")


def resolve_status(db: Session, portal: Portal | None, raw_text: str) -> str:
    """把原文解析为统一状态；未命中时触发一次 LLM 兜底分类并缓存规则。"""
    text = (raw_text or "").strip()
    if not text:
        return "pending_confirm"

    # 1) 规则表（含此前 LLM 沉淀与用户手改候选转正的规则）
    rule = _match_rule(db, portal, text)
    if rule:
        return rule

    # 2) 门户配方自带映射 + 3) 通用兜底
    norm = normalize_mod.normalize_status(text, (portal.config or {}).get("status_map") if portal else None)
    if norm != "pending_confirm":
        return norm

    # 4) LLM 兜底分类（每个未命中原文全平台只调一次，结果落规则表）
    try:
        output = providers.classify_status(db, portal.name if portal else "未知门户", text)
    except Exception as e:  # noqa: BLE001 LLM 故障不阻塞同步
        logger.warning("status_classify 失败（降级待确认）: %s", e)
        return "pending_confirm"
    if output is None:
        return "pending_confirm"
    # 门槛双保险（provider 内已过滤）：低置信/ambiguous/非法枚举一律不猜
    from app.domain.statuses import is_valid

    if output.confidence < 0.7 or output.status == "ambiguous" or not is_valid(output.status):
        return "pending_confirm"

    _save_rule(db, portal, text, output.status, note=output.reason)
    return output.status


def _match_rule(db: Session, portal: Portal | None, text: str) -> str | None:
    if portal is not None:
        scopes = [("portal", str(portal.id)), ("provider", portal.provider_key), ("generic", "")]
    else:
        scopes = [("generic", "")]
    rules = list(
        db.scalars(select(StatusRule).where(StatusRule.enabled.is_(True)).order_by(StatusRule.priority))
    )
    for scope in scopes:  # 精确 scope 优先于泛化 scope
        for rule in rules:
            if (rule.scope_type, rule.scope_key) != scope:
                continue
            try:
                if re.search(rule.pattern, text, re.IGNORECASE):
                    return rule.mapped_status
            except re.error:
                continue
    return None


def _save_rule(db: Session, portal: Portal | None, raw_text: str, status: str, *, note: str | None) -> None:
    """(scope, 原文规范化) 唯一键写入：精确匹配原文串（正则元字符全部转义）。"""
    pattern = "^" + re.escape(" ".join(raw_text.split())) + "$"
    scope_type, scope_key = ("portal", str(portal.id)) if portal is not None else ("generic", "")
    exists = db.scalar(
        select(StatusRule).where(
            StatusRule.scope_type == scope_type,
            StatusRule.scope_key == scope_key,
            StatusRule.pattern == pattern,
        )
    )
    if exists:
        return
    db.add(
        StatusRule(
            scope_type=scope_type,
            scope_key=scope_key,
            pattern=pattern[:255],
            mapped_status=status,
            priority=50,
            source="llm",
            enabled=True,
            note=(note or "")[:255],
        )
    )
    db.commit()
