"""L2 配方执行器：运行「已发布配方」（RecipeSpec），与验证器共用提取引擎。

- xhr 型：服务端带 Cookie 重放接口（首选，便宜稳定）；占位符按 runtime_params 解析
  （cookie 名 / 前置接口取值），分页 page_param 逐页拉取；
- dom 型：需要 Playwright 无头浏览器（长尾兜底），当前部署未包含，
  明确报错引导重新采样或改 xhr——不静默给错数据。
"""

from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import httpx

from app.adapters import AdapterContext, AdapterError, BaseAdapter, RawApplication, SessionInvalidError
from app.adapters import httpio
from app.llm.extract import ExtractionError, extract_from_json, extract_from_page
from app.llm.schemas import PLACEHOLDER_RE, DOMSource, PageSource, RecipeSpec, RuntimeParamCookie, RuntimeParamXHR, XHRSource

_PAGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_LOGIN_URL_HINTS = ("login", "signin", "auth", "passport", "sso")

class RecipeAdapter(BaseAdapter):
    def fetch(self, config: dict, ctx: AdapterContext) -> list[RawApplication]:
        spec_dict = (config or {}).get("recipe")
        if not spec_dict:
            raise AdapterError("门户配置缺少 recipe")
        try:
            spec = RecipeSpec.model_validate(spec_dict)
        except Exception as e:  # noqa: BLE001 配方损坏不应炸轮询循环
            raise AdapterError(f"配方解析失败: {e}") from e

        src = spec.list_source
        if isinstance(src, PageSource):
            records = self._fetch_page(spec, ctx)
        elif isinstance(src, DOMSource):
            raise AdapterError("该门户配方为 dom 型，需要浏览器运行时（当前部署未启用），请重新采样生成 xhr 型配方")
        else:
            records = self._fetch_xhr(spec, ctx)

        return [
            RawApplication(
                job_title=r.job_title or "",
                status_raw=r.status_raw or "",
                portal_key=r.portal_key,
                department=r.department,
                work_location=r.work_location,
                applied_at=r.applied_at,
                job_url=r.job_url,
            )
            for r in records
        ]

    def _fetch_page(self, spec: RecipeSpec, ctx: AdapterContext) -> list:
        """page 型：GET 页面本身（SSR 直出数据在 HTML 内嵌 script 里，无需 JS 渲染）。"""
        src = spec.list_source
        assert isinstance(src, PageSource)
        try:
            resp = httpx.get(
                src.page_url, cookies=ctx.cookies, timeout=15.0,
                follow_redirects=False, trust_env=False,
                headers={"User-Agent": _PAGE_UA},
            )
        except httpx.HTTPError as e:
            raise AdapterError(f"网络请求失败: {e}") from e
        if resp.status_code in (401, 403):
            raise SessionInvalidError(f"HTTP {resp.status_code}")
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if any(k in location.lower() for k in _LOGIN_URL_HINTS):
                raise SessionInvalidError("重定向到登录页")
            raise AdapterError(f"意外重定向: {location[:120]}")
        text = resp.text or ""
        for marker in spec.auth.session_invalid.url_contains:
            if marker and marker in text[:2000]:
                raise SessionInvalidError(f"响应含失效标记: {marker}")
        try:
            records = extract_from_page(spec, text)
        except ExtractionError as e:
            raise AdapterError(f"配方提取失败（疑似网站改版）: {e}") from e
        return [r for r in records if r.job_title and r.status_raw]

    def _fetch_xhr(self, spec: RecipeSpec, ctx: AdapterContext) -> list:
        src = spec.list_source
        assert isinstance(src, XHRSource)
        auth = spec.auth
        params = self._resolve_runtime_params(spec, ctx)

        # URL：占位符替换 + 去 * 通配段；配方自带 query 与运行 query 合并
        base = PLACEHOLDER_RE.sub(lambda m: str(params.get(m.group(1), "")), src.url_pattern)
        base = base.split("*")[0]
        parts = urlsplit(base)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update({k: str(v) for k, v in src.query.items()})
        base = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

        invalid_codes = [c for c in auth.session_invalid.status_code]
        records: list = []
        page = src.pagination.start_page
        # POST 型接口的请求体：占位符替换；翻页参数写入体（query 仅作补充）。
        # 空 JSON 体 {} 是合法契约（如北森 GetAllDeliveryRecord）——保持存在，不被 or 吞掉
        src_body = (
            {k: PLACEHOLDER_RE.sub(lambda m: str(params.get(m.group(1), "")), str(v)) for k, v in src.body.items()}
            if src.body is not None else None
        )
        for _ in range(src.pagination.max_pages):
            page_query = dict(query)
            page_body = dict(src_body) if src_body is not None else None
            if src.pagination.type == "page_param":
                if page_body is not None:
                    page_body[src.pagination.page_param] = str(page)
                else:
                    page_query[src.pagination.page_param] = str(page)
            _, _, data = httpio.request_json(
                base,
                method=src.method,
                cookies=ctx.cookies,
                invalid_markers=auth.session_invalid.url_contains,
                invalid_status_codes=invalid_codes,
                extra_query=page_query,
                json_body=page_body,
            )
            try:
                batch = extract_from_json(spec, data)
            except ExtractionError as e:
                raise AdapterError(f"配方提取失败（疑似网站改版）: {e}") from e
            fresh = [r for r in batch if r.job_title and r.status_raw]
            records.extend(fresh)
            if not fresh or src.pagination.type != "page_param":
                break
            page += 1

        return records

    def _resolve_runtime_params(self, spec: RecipeSpec, ctx: AdapterContext) -> dict[str, str]:
        values: dict[str, str] = {}
        for name, rp in spec.runtime_params.items():
            if isinstance(rp, RuntimeParamCookie):
                value = ctx.cookies.get(rp.name)
                if not value:
                    raise SessionInvalidError(f"缺少 Cookie {rp.name}（请重新登录）")
                values[name] = value
            elif isinstance(rp, RuntimeParamXHR):
                url = PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), "")), rp.url_pattern)
                _, _, data = httpio.request_json(url, method=rp.method, cookies=ctx.cookies)
                from app.adapters.fields import dig

                value = dig(data, rp.json_path)
                if value in (None, ""):
                    raise SessionInvalidError(f"前置接口未能取到 {name}（登录态可能已失效）")
                values[name] = str(value)
        return values
