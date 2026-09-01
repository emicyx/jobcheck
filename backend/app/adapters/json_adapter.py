"""配置驱动的 JSON 接口适配器。

Moka 及一切「HTTP 直连 + JSON 列表」门户共用；门户配置 Schema：

{
  "login_url": "https://.../login",          # 插件打开的登录页
  "session_cookie_names": ["xxx_session"],   # 登录成功的 Cookie 标记
  "list_url": "https://.../api/applications",
  "list_method": "GET",
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
from datetime import date, datetime

import httpx

from app.adapters import AdapterContext, AdapterError, BaseAdapter, RawApplication, SessionInvalidError

_TIMEOUT = 15.0


def _dig(data, path: str):
    node = data
    for part in path.split("."):
        if not part:
            continue
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        # 毫秒/秒时间戳
        ts = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(ts, tz=None).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    return None


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

        try:
            # trust_env=False：绕过系统代理直连（本地 Mock 与国内官网均无需代理）
            resp = httpx.request(
                method, url, cookies=ctx.cookies, timeout=_TIMEOUT,
                follow_redirects=False, trust_env=False,
            )
        except httpx.HTTPError as e:
            raise AdapterError(f"网络请求失败: {e}") from e

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

        items = _dig(data, config.get("list_json_path") or "")
        if items is None and isinstance(data, list):
            items = data
        if isinstance(items, dict):
            items = [items]  # 单对象响应（如腾讯应聘进度接口）视作一条记录
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
                    department=(str(_dig(item, fields["department"])).strip() or None) if fields.get("department") else None,
                    work_location=(str(_dig(item, fields["work_location"])).strip() or None) if fields.get("work_location") else None,
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


# 防御性字段清洗：字段名只允许字母数字下划线，供配置校验用
_FIELD_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def valid_field_name(name: str) -> bool:
    return bool(_FIELD_NAME_RE.match(name or ""))
