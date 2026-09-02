"""绑定生命周期：intent 创建 → 插件激活（Cookie 回传+验证）→ 失效/重绑。"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import AdapterContext, AdapterError, SessionInvalidError, get_adapter
from app.core import crypto
from app.db.models import Binding, Portal, User


class BindingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def utcnow() -> datetime:
    # 与 DB 的 naive DateTime 约定保持一致（见 models.utcnow）
    return datetime.now(timezone.utc).replace(tzinfo=None)


def cookies_to_context(cookie_blob: bytes) -> AdapterContext:
    plain = crypto.decrypt_text(cookie_blob)
    items = json.loads(plain)
    return AdapterContext(cookies={c["name"]: c["value"] for c in items})


def persist_refreshed_cookies(db: Session, binding: Binding, refreshed: dict[str, str]) -> None:
    """运行期自愈刷新出的 Cookie 新值合并回加密存储（如飞书 CSRF 轮换）。

    只更新同名值、不增删条目；无变化不写库。
    """
    if not refreshed or binding.cookie_blob is None:
        return
    items = json.loads(crypto.decrypt_text(binding.cookie_blob))
    changed = False
    for entry in items:
        new_value = refreshed.get(entry.get("name"))
        if new_value and new_value != entry.get("value"):
            entry["value"] = new_value
            changed = True
    if changed:
        binding.cookie_blob = crypto.encrypt_text(json.dumps(items, ensure_ascii=False))
        binding.cookie_updated_at = utcnow()
        db.commit()


def create_binding_intent(db: Session, user: User, portal: Portal, binding: Binding | None = None) -> Binding:
    """创建（或为既有绑定续期）一次性登录授权 intent。"""
    if binding is None:
        binding = Binding(user_id=user.id, portal_id=portal.id, status="pending")
        db.add(binding)
    binding.intent_token = secrets.token_hex(16)
    binding.intent_expires_at = utcnow() + timedelta(minutes=15)
    binding.status = "pending" if binding.status in ("expired", "pending") else binding.status
    binding.last_error = None
    db.commit()
    db.refresh(binding)
    return binding


def get_binding_by_token(db: Session, token: str, allow_expired: bool = False) -> Binding | None:
    binding = db.scalar(select(Binding).where(Binding.intent_token == token))
    if binding is None:
        return None
    if binding.intent_expires_at and binding.intent_expires_at < utcnow() and not allow_expired:
        return None
    return binding


def intent_status(db: Session, token: str) -> dict:
    """向导页轮询用：token 即凭证；激活成功后 token 立即过期但行仍在，据此返回终态。"""
    binding = get_binding_by_token(db, token, allow_expired=True)
    if binding is None:
        return {"status": "invalid"}
    if binding.intent_expires_at and binding.intent_expires_at < utcnow():
        # 已被使用（激活时立即过期）或自然超时：以绑定实际状态为准
        return {
            "status": "activated" if binding.status == "active" else "failed",
            "binding_id": binding.id,
            # 激活成功但首次同步失败时（Cookie 可用、门户结构异常），向导据此给出真实反馈
            "synced": binding.status == "active" and binding.last_error is None,
            "detail": binding.last_error,
        }
    return {"status": "pending", "binding_id": binding.id}


def activate_binding(
    db: Session,
    binding: Binding,
    cookies: list[dict],
) -> dict:
    """插件回传 Cookie 后激活：加密落库 + 立即验证（拉一次列表）。"""
    clean: list[dict] = []
    for c in cookies or []:
        name, value = c.get("name"), c.get("value")
        if isinstance(name, str) and isinstance(value, str) and name:
            clean.append({"name": name, "value": value, "domain": c.get("domain", "")})
    if not clean:
        raise BindingError("回传的 Cookie 为空")

    portal = binding.portal
    # 先暂存 Cookie（不入库），探测成功才真正落库烧 token
    cookie_json = json.dumps(clean, ensure_ascii=False)
    ctx_probe = AdapterContext(cookies={c["name"]: c["value"] for c in clean})
    adapter = get_adapter(portal.provider_key)

    from app.services.sync import sync_binding

    # 预探测：Cookie 拉不动列表就不落库、不烧 token，插件可等用户登录完成后自动重试
    try:
        adapter.fetch(portal.config or {}, ctx_probe)
    except SessionInvalidError:
        binding.last_error = "插件已连上，但当前 Cookie 还不足以拉取投递列表（未完成登录？）"
        db.commit()
        # 409 = 可重试：不落库不烧 token，插件等用户完成登录后再次尝试
        raise BindingError("尚未检测到有效登录态：请在官网完成登录，插件会自动重试", status_code=409)
    except AdapterError:
        pass  # 结构性错误（字段映射等）不阻碍激活：落库后由同步环节回报并校准

    # 探测期自愈刷新出的 Cookie（如飞书 CSRF 轮换）并入落库值
    if ctx_probe.refreshed_cookies:
        for entry in clean:
            new_value = ctx_probe.refreshed_cookies.get(entry["name"])
            if new_value:
                entry["value"] = new_value

    binding.cookie_blob = crypto.encrypt_text(cookie_json)
    binding.cookie_updated_at = utcnow()
    # token 立即过期：不可二次激活，但保留在行上供向导轮询读终态
    binding.intent_expires_at = utcnow()

    try:
        summary = sync_binding(db, binding)
    except SessionInvalidError as e:
        # 注意不回滚：Cookie 落库要保留（绑定标记 expired 供诊断），sync 在 fetch 阶段抛错、无脏写
        binding.status = "expired"
        binding.last_error = f"Cookie 验证失败: {e}"
        db.commit()
        raise BindingError("登录态验证失败：平台拿着 Cookie 拉取投递列表被拒，请确认已完成登录后重试")
    except AdapterError as e:
        # Cookie 已收到但门户暂时不可达：先激活，等下轮调度重试（同样不回滚 Cookie）
        binding.status = "active"
        binding.last_error = f"激活时同步失败（稍后自动重试）: {e}"
        binding.next_check_at = utcnow() + timedelta(minutes=5)
        db.commit()
        db.refresh(binding)
        return {"ok": True, "activated": True, "synced": False, "detail": str(e)}

    binding.status = "active"
    db.commit()
    db.refresh(binding)
    return {"ok": True, "activated": True, "synced": True, **summary}
