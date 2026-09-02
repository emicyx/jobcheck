"""平台结构指纹匹配（LLM_DESIGN.md §2.0）——免 LLM 的第一级接入。

采样包先对平台指纹库（人工维护的常量条目，决策 13：自动产物永不回流）打分：
XHR URL 正则命中分 + 响应键名结构重合度分。达阈值 → 参数化实例化平台配置 →
由管线用该采样包回放验证一次（防平台改版）→ 通过才入库 enabled。

指纹只负责「认出平台」，字段映射仍由确定性启发式在真实响应上推断并被回放验证，
因此模板键名信号不全也不会给错数据（验证不过就走 T1 生成）。
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, parse_qsl

from app.llm.heuristics import COMMON_QUERY_PARAMS


@dataclass(frozen=True)
class PlatformTemplate:
    key: str  # moka | feishu | beisen
    label: str
    url_signals: tuple[tuple[re.Pattern, int], ...]  # (XHR URL 正则, 命中分)
    key_signals: tuple[str, ...]  # 响应键名（重合 1 个 +1 分，封顶 3）
    threshold: int
    invalid_markers: tuple[str, ...] = ()  # 实例化配置带的响应体失效标记（无则靠 401/跳登录检测）
    # 实例化 L1 配置时附带的请求头（POST + CSRF 型平台需要）：
    # 值支持 ${cookie:NAME}（运行时由会话 Cookie 派生）与 ${path_segment:N}
    # （实例化时从采样页 URL 取第 N 段路径，如飞书官网的 website-path=704852）。
    request_headers: tuple[tuple[str, str], ...] = ()
    # 平台级字段映射（真实采样校准的点路径，相对列表项；-1 为数组末项）。
    # 携带时实例化直接采用，不再靠启发式猜字段（回放验证照常把关）。
    list_json_path: str = ""
    field_map: tuple[tuple[str, str], ...] = ()
    status_map: tuple[tuple[str, str], ...] = ()  # (原文/码 pattern, 统一状态 key)
    # CSRF 轮换自愈计划：(刷新端点相对路径, cookie 名)。运行时 405（旧 token 被轮换）
    # 时匿名 POST 刷新端点拿新值重试；2026-09-01 小米站实测：绑定存的 token 会被站点轮换
    csrf_refresh: tuple[str, str] | None = None


_TEMPLATES: list[PlatformTemplate] = [
    PlatformTemplate(
        key="moka",
        label="Moka 招聘系统",
        url_signals=(
            (re.compile(r"(https?://)?([a-z0-9-]+\.)*mokahr\.com", re.I), 4),
            (re.compile(r"/api/outer/", re.I), 4),
        ),
        key_signals=("positionName", "applyPositionName", "statusText", "deliverTime", "applyId"),
        threshold=4,
        invalid_markers=("SESSION_INVALID",),
    ),
    # 飞书招聘（ATSX saas-career）：官网域 jobs.feishu.cn，支持企业自定义域名（如 campus.qunar.com），
    # 因此识别信号 = 飞书域（强）∨ 接口路径形状 + 响应键结构（自定义域名站靠这条路径命中）。
    #
    # 契约与字段映射经 2026-09-01 在 hf7l9aiqzx.jobs.feishu.cn（去哪儿校招）用真实登录态实测校准：
    # - 前端 bundle 端点表：searchApplication = {origin}/api/v1/search/user/applications（POST，
    #   JSON 体分页）；应聘记录页 SSR 直出、初始加载不发列表 XHR，靠插件页内主动探测采集；
    # - 必需头 x-csrf-token（= atsx-csrf-token Cookie 值，可匿名 POST /api/v1/csrf/token 刷新）
    #   + website-path（站点路径首段）；缺 CSRF → 405，未登录 → 401（空体）；
    # - 响应：{"code":0,"data":{"delivery_list":[...]}}；
    #   列表项：id（投递 id）、job_post_info.title（岗位名）、biz_create_time（字符串毫秒）、
    #   operation_list[]（操作时间线，末项 operation_code 即当前状态）、current_stage（实测与
    #   实际进度不符，不用）；
    # - operation_code 码表（与页面时间线逐条对齐验证）：0=已投递、1=评估中、3=笔试中；
    #   其余码（2/4/5…）未验证不映射，落看板「待确认」由运行期沉淀。
    PlatformTemplate(
        key="feishu",
        label="飞书招聘",
        url_signals=(
            (re.compile(r"(https?://)?([a-z0-9-]+\.)*(feishu\.cn|feishu\.net|larksuite\.com|larkoffice\.com)", re.I), 4),
            (re.compile(r"/search/user/applications", re.I), 3),
            (re.compile(r"/api/v\d+/portal|/portal/api/|apply[_/]?list|application[_/]?list", re.I), 3),
        ),
        key_signals=("delivery_list", "job_post_info", "operation_list", "biz_create_time", "application_list"),
        threshold=4,
        invalid_markers=("not login",),
        request_headers=(
            ("content-type", "application/json"),
            ("X-Requested-With", "XMLHttpRequest"),
            ("x-csrf-token", "${cookie:atsx-csrf-token}"),
            ("website-path", "${path_segment:1}"),
        ),
        list_json_path="data.delivery_list",
        field_map=(
            ("id", "id"),
            ("job_title", "job_post_info.title"),
            ("status_raw", "operation_list.-1.operation_code"),
            ("applied_at", "biz_create_time"),
            ("work_location", "job_post_info.city_info.name"),
        ),
        status_map=(("^0$", "screening"), ("^1$", "screening"), ("^3$", "written_test")),
        csrf_refresh=("/api/v1/csrf/token", "atsx-csrf-token"),
    ),
    PlatformTemplate(
        key="beisen",
        label="北森招聘",
        url_signals=(
            (re.compile(r"(https?://)?([a-z0-9-]+\.)*(zhiye\.com|italent\.cn|yingjiesheng\.com)", re.I), 4),
            (re.compile(r"/api/Submission/GetAllDeliveryRecord", re.I), 3),
        ),
        key_signals=("Submissions", "JobAdTitle", "DeliveryStatus", "DeliveryDate", "ApplyId", "applyRecord"),
        threshold=4,
        # 契约与字段映射经 2026-09-01 在 hkaco.zhiye.com（虹科校招）真实登录态采样校准：
        # POST /api/Submission/GetAllDeliveryRecord（空 JSON 体）→
        # {"Code":200,"Data":{"Finished":{"TotalCount":1,"Submissions":[{...,"Datas":[...]}]}}}
        # 分组列表形态：Submissions 按人/志愿分组，组内 Datas 才是逐条投递——
        # list_json_path 的 * 段展开（Data.* 兼容进行中/已完成多个 tab，全部拼接）；
        # 列表项：JobAdTitle（岗位名）/DeliveryStatus（中文状态原文，运行期归一化）/
        # DeliveryDate（"2026-08-25 13:21"）/ApplyId
        list_json_path="Data.*.Submissions.*.Datas",
        field_map=(
            ("id", "ApplyId"),
            ("job_title", "JobAdTitle"),
            ("status_raw", "DeliveryStatus"),
            ("applied_at", "DeliveryDate"),
        ),
    ),
]

@dataclass
class FingerprintHit:
    template: PlatformTemplate
    score: int
    matched_url: str
    response_body: str
    method: str = "GET"
    request_body: str = ""  # POST 型命中接口的请求体（实例化时原样带入配方重放）


def match(network: list[dict] | None) -> FingerprintHit | None:
    """对采样请求-响应对打分；达阈值返回命中，未命中（判定自研）返回 None。"""
    best: FingerprintHit | None = None
    for entry in network or []:
        url = str(entry.get("url") or "")
        body = str(entry.get("response_body") or "")
        if not url.startswith("http") or body.lstrip()[:1] not in ("{", "["):
            continue
        if "#embedded" in url:
            continue  # SSR 内嵌数据块：可作素材，不可实例化为轮询接口
        for template in _TEMPLATES:
            score = 0
            for regex, points in template.url_signals:
                if regex.search(url):
                    score += points
            score += min(sum(1 for k in template.key_signals if f'"{k}"' in body[:20_000]), 3)
            if score >= template.threshold and (best is None or score > best.score):
                best = FingerprintHit(
                    template=template, score=score, matched_url=url, response_body=body,
                    method=str(entry.get("method") or "GET").upper(),
                    request_body=str(entry.get("request_body") or ""),
                )
    return best


def is_instantiable(hit: FingerprintHit) -> str | None:
    """实例化前置检查：命中 URL 的查询串不得携带用户特有参数（无法参数化即拒绝）。"""
    query = dict(parse_qsl(urlsplit(hit.matched_url).query, keep_blank_values=True))
    suspicious = [k for k in query if k.lower() not in COMMON_QUERY_PARAMS]
    if suspicious:
        return f"命中接口带疑似用户参数 {suspicious}，无法安全实例化模板"
    return None


_PATH_SEGMENT_RE = re.compile(r"^\$\{path_segment:(\d+)\}$")


def build_from_template(
    hit: FingerprintHit, data, sample_url: str, request_body: str | None = None
) -> "object | None":
    """模板自带真实校准的字段映射时，直接构建配方草稿（不走启发式猜字段）。

    自述清单由提取引擎对真实响应生成（自描述，天然一致）；未映射的状态原文
    显式声明留给兜底（验证器断言 3 的要求——未知码不声明即拒绝发布，不猜）。
    模板缺 field_map/list_json_path 或提取失败返回 None，回退启发式路径。
    """
    t = hit.template
    if not t.field_map or not t.list_json_path:
        return None
    from app.llm.extract import extract_from_json
    from app.llm.heuristics import _path_hint, sanitize_url
    from app.llm.schemas import (
        AuthSpec,
        Condition,
        FieldMapping,
        ObservedApplication,
        RecipeGenOutput,
        RecipeMeta,
        RecipeSpec,
        StatusMapEntry,
        XHRSource,
    )

    base, safe_query = sanitize_url(hit.matched_url)
    body: dict[str, str] | None = None
    if hit.method == "POST":
        if not request_body:
            return None  # POST 而无请求体：运行时无法重放
        try:
            parsed = json.loads(request_body)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        body = {str(k): str(v) for k, v in parsed.items()}

    recipe = RecipeSpec(
        auth=AuthSpec(
            login_success=Condition(url_contains=[_path_hint(sample_url)]),
            session_invalid=Condition(url_contains=["login", "signin", "passport"]),
        ),
        list_source=XHRSource(
            url_pattern=base,
            method=hit.method,
            list_json_path=t.list_json_path,
            query=safe_query,
            body=body,
        ),
        field_map={k: FieldMapping(json_path=v) for k, v in t.field_map},
        status_map=[StatusMapEntry(pattern=p, status=s) for p, s in t.status_map],
        meta=RecipeMeta(generated_by=f"template:{t.key}"),
    )
    try:
        records = extract_from_json(recipe, data)
    except Exception:
        # 模板路径在该响应上定位失败（如误命中同域的配置/样式接口）——
        # 交回启发式与 T1 路径，不让单个坏命中炸掉整条管线
        return None
    if not records:
        return None
    from app.domain.normalize import normalize_status

    covered = [re.compile(p) for p, _ in t.status_map]
    unmapped = sorted(
        {
            r.status_raw
            for r in records
            if r.status_raw and not any(c.search(r.status_raw) for c in covered)
            and normalize_status(r.status_raw) == "pending_confirm"
        }
    )
    return RecipeGenOutput(
        recipe=recipe,
        observations=[
            ObservedApplication(job_title=r.job_title or "", status_raw=r.status_raw or "")
            for r in records
        ],
        unmapped_status_texts=unmapped,
        confidence=0.8,
    )


def instantiate_headers(template: PlatformTemplate, sample_url: str) -> dict[str, str]:
    """模板头计划 → 实例化配置的头。

    ``${path_segment:N}`` 在此刻解析（站点路径是门户级常量，如飞书的 704852）；
    ``${cookie:NAME}`` 原样保留，运行时由 httpio.resolve_headers 从会话 Cookie 派生。
    """
    out: dict[str, str] = {}
    for key, value in template.request_headers:
        m = _PATH_SEGMENT_RE.match(value)
        if m:
            parts = [p for p in urlsplit(sample_url or "").path.split("/") if p]
            idx = int(m.group(1))
            out[key] = parts[idx - 1] if 1 <= idx <= len(parts) else ""
        else:
            out[key] = value
    return out
