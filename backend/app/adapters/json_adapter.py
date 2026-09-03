"""配置驱动的 JSON 接口适配器。

Moka 及一切「HTTP 直连 + JSON 列表」门户共用；门户配置 Schema：

{
  "login_url": "https://.../login",          # 插件打开的登录页
  "session_cookie_names": ["xxx_session"],   # 登录成功的 Cookie 标记
  "list_url": "https://.../api/applications",
  "list_method": "GET",                       # POST 型接口配 list_body
  "list_body": {"page_no": "1", "page_size": "20"},       # POST 的 JSON 请求体
  "list_headers": {"x-csrf-token": "${cookie:atsx-csrf-token}"},  # 头值可引用会话 Cookie
  "list_json_path": "data.list",             # 响应 JSON 中列表的点路径
  "fields": {                                 # 列表项字段 → RawApplication
    "id": "applyId", "job_title": "positionName", "status_raw": "statusText",
    "department": "departmentName", "applied_at": "deliverTime", "job_url": "jobUrl"
  },
  "session_invalid_markers": ["SESSION_INVALID", "请登录"],  # 响应中的失效信号
  "status_map": [{"pattern": "评估中", "status": "screening"}]
}
"""

import json
import re

import httpx

from app.adapters import AdapterContext, AdapterError, BaseAdapter, RawApplication, SessionInvalidError
from app.adapters.fields import dig as _dig
from app.adapters.fields import dig_list as _dig_list
from app.adapters.fields import parse_date as _parse_date
from app.adapters.httpio import resolve_headers as _resolve_headers

_TIMEOUT = 15.0


def _opt(value) -> str | None:
    """取值转非空字符串；JSON null/缺失保持 None（不产生 "None" 字符串）。"""
    if value is None:
        return None
    return str(value).strip() or None


class JSONAPIAdapter(BaseAdapter):
    def fetch(self, config: dict, ctx: AdapterContext) -> list[RawApplication]:
        url = config.get("list_url")
        if not url:
            raise AdapterError("门户配置缺少 list_url")
        method = (config.get("list_method") or "GET").upper()

        # 会话 Cookie 缺失直接判失效，省一次请求
        required = config.get("session_cookie_names") or []
        if required and not all(name in ctx.cookies for name in required):
            raise SessionInvalidError("会话 Cookie 缺失")

        # trust_env=False：绕过系统代理直连（国内招聘官网无需代理）；
        # list_headers 的 ${cookie:NAME} 引用在 httpio.resolve_headers 里派生
        cookies = dict(ctx.cookies)
        resp = self._send(config, method, url, cookies)

        # CSRF 轮换自愈（飞书 ATSX 实测：绑定时存的 atsx-csrf-token 会被站点轮换，
        # 旧值 → 405 空体；匿名 POST 刷新端点拿新值重试一次即可恢复，无需用户重绑）
        if resp.status_code == 405 and config.get("csrf_refresh"):
            fresh = self._refresh_csrf(config["csrf_refresh"], ctx)
            if fresh:
                cookies.update(fresh)
                resp = self._send(config, method, url, cookies)

        if resp.status_code in (401, 403):
            raise SessionInvalidError(f"HTTP {resp.status_code}")
        # 未跟随的重定向到登录页 = 失效
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if any(k in location.lower() for k in ("login", "signin", "auth")):
                raise SessionInvalidError("重定向到登录页")
            raise AdapterError(f"意外重定向: {location[:120]}")

        text = resp.text or ""
        for marker in config.get("session_invalid_markers") or []:
            if marker and marker in text[:2000]:
                raise SessionInvalidError(f"响应含失效标记: {marker}")

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            if "<html" in text[:200].lower():
                raise SessionInvalidError("返回了登录 HTML 页") from e
            raise AdapterError(f"响应不是 JSON: {text[:120]}") from e

        items = _dig_list(data, config.get("list_json_path") or "")
        if items is None and isinstance(data, list):
            items = [x for x in data if isinstance(x, dict)]
        if not isinstance(items, list):
            top_keys = sorted(data.keys())[:20] if isinstance(data, dict) else type(data).__name__
            raise AdapterError(
                f"列表路径无效 {config.get('list_json_path')!r}；响应顶层键: {top_keys}；样例: {str(data)[:300]}"
            )

        fields = config.get("fields") or {}
        result: list[RawApplication] = []
        first_item: dict | None = None
        for item in items:
            if not isinstance(item, dict):
                continue
            if first_item is None:
                first_item = item
            title = _dig(item, fields.get("job_title", ""))
            status = _dig(item, fields.get("status_raw", ""))
            if not title or not status:
                continue  # 缺关键字段的脏数据跳过，不猜
            result.append(
                RawApplication(
                    job_title=str(title).strip(),
                    status_raw=str(status).strip(),
                    portal_key=str(_dig(item, fields["id"])) if fields.get("id") and _dig(item, fields["id"]) is not None else None,
                    department=_opt(_dig(item, fields["department"])) if fields.get("department") else None,
                    work_location=_opt(_dig(item, fields["work_location"])) if fields.get("work_location") else None,
                    applied_at=_parse_date(_dig(item, fields["applied_at"])) if fields.get("applied_at") else None,
                    job_url=(str(_dig(item, fields["job_url"])) or None) if fields.get("job_url") and _dig(item, fields["job_url"]) else None,
                )
            )
        if not result and first_item is not None:
            # 自描述校准：把真实结构带回错误信息，管理员据此修正字段映射，无需接触用户 Cookie
            keys = sorted(first_item.keys())[:20]
            raise AdapterError(
                f"字段映射未命中：列表 {len(items)} 项；首项键: {keys}；"
                f"样例: {json.dumps(first_item, ensure_ascii=False)[:400]}"
            )
        return result

    def _send(self, config: dict, method: str, url: str, cookies: dict):
        # 空 JSON 体 {} 必须原样发送（北森契约：POST GetAllDeliveryRecord 体为 {}，
        # 丢掉 body/content-type 会被服务端 415 拒绝）——只缺配置键才不发
        body = config.get("list_body")
        try:
            return httpx.request(
                method, url, cookies=cookies, timeout=_TIMEOUT,
                follow_redirects=False, trust_env=False,
                json=body if body is not None else None,
                headers=_resolve_headers(config.get("list_headers"), cookies),
            )
        except httpx.HTTPError as e:
            raise AdapterError(f"网络请求失败: {e}") from e

    def _refresh_csrf(self, plan: dict, ctx: AdapterContext) -> dict[str, str] | None:
        """匿名刷新 CSRF（飞书契约：POST 端点种新 Cookie 且响应体回同值）。

        成功则记入 ctx.refreshed_cookies 供调用方回写存储；返回用于重试的
        {cookie_name: 新值}（保持 URL 编码原样，发送时由 resolve_headers 解码）。
        """
        try:
            resp = httpx.request(
                (plan.get("method") or "POST").upper(), plan["url"],
                cookies=ctx.cookies, timeout=_TIMEOUT,
                follow_redirects=False, trust_env=False,
                headers={"Content-Type": "application/json"},
                content=plan.get("body") or "{}",
            )
        except httpx.HTTPError:
            return None  # 刷新失败不放大：维持原 405 错误语义
        name = plan.get("cookie_name") or ""
        if not name or resp.status_code != 200:
            return None
        value = resp.cookies.get(name)
        if not value:
            return None
        ctx.refreshed_cookies[name] = value
        return {name: value}


# 防御性字段清洗：字段名只允许字母数字下划线，供配置校验用
_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def valid_field_name(name: str) -> bool:
    return bool(_FIELD_NAME_RE.match(name or ""))
