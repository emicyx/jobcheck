"""适配器共享 HTTP 层：带 Cookie 的 JSON 请求 + 登录态失效检测语义。

L1 json_adapter 与 L2 recipe_adapter 共用同一套判定：
401/403 → 失效；重定向到登录页 → 失效；响应含失效标记 / 返回登录 HTML → 失效。
"""

import json
import re
from urllib.parse import unquote

import httpx

from app.adapters import AdapterError, SessionInvalidError

_TIMEOUT = 15.0
_LOGIN_URL_HINTS = ("login", "signin", "auth", "passport", "sso")
_COOKIE_REF_RE = re.compile(r"^\$\{cookie:([A-Za-z0-9_.-]+)\}$")


def resolve_headers(headers: dict[str, str] | None, cookies: dict[str, str] | None) -> dict[str, str] | None:
    """解析配置头：值形如 ``${cookie:NAME}`` 时用当前会话 Cookie 值替换并 URL 解码。

    飞书 ATSX 的 x-csrf-token 就是 atsx-csrf-token Cookie 的值（Chromium cookies
    API 给的是 URL 编码形式，如尾部 ``%3D``；前端读取同样做 decodeURIComponent），
    绑定时连 Cookie 一起采集，运行时这里派生，无需额外存储。
    """
    if not headers:
        return None
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        m = _COOKIE_REF_RE.match(str(value))
        resolved[key] = unquote((cookies or {}).get(m.group(1), "")) if m else str(value)
    return resolved


def request_json(
    url: str,
    *,
    method: str = "GET",
    cookies: dict[str, str] | None = None,
    invalid_markers: list[str] | None = None,
    invalid_status_codes: list[int] | None = None,
    extra_query: dict[str, str] | None = None,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, object]:
    """发起请求并解析 JSON。返回 (status, text, json)。失效抛 SessionInvalidError。"""
    try:
        # trust_env=False：绕过系统代理直连（本地 Mock 与国内官网均无需代理）
        resp = httpx.request(
            method, url, cookies=cookies or {}, timeout=_TIMEOUT,
            params=extra_query, follow_redirects=False, trust_env=False,
            json=json_body, headers=resolve_headers(headers, cookies),
        )
    except httpx.HTTPError as e:
        raise AdapterError(f"网络请求失败: {e}") from e

    code = resp.status_code
    if code in (401, 403) or code in (invalid_status_codes or []):
        raise SessionInvalidError(f"HTTP {code}")
    if code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location", "")
        if any(k in location.lower() for k in _LOGIN_URL_HINTS):
            raise SessionInvalidError("重定向到登录页")
        raise AdapterError(f"意外重定向: {location[:120]}")

    text = resp.text or ""
    for marker in invalid_markers or []:
        if marker and marker in text[:2000]:
            raise SessionInvalidError(f"响应含失效标记: {marker}")

    try:
        return code, text, resp.json()
    except json.JSONDecodeError as e:
        if "<html" in text[:200].lower():
            raise SessionInvalidError("返回了登录 HTML 页") from e
        raise AdapterError(f"响应不是 JSON: {text[:120]}") from e
