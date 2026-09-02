"""确定性启发式：从采样响应推断列表位置与字段映射。

两个用途：
1. heuristic 离线「LLM 提供者」——零成本生成配方草稿（本地演示与测试），
   同样要过回放验证，不因确定性而免检；
2. 平台指纹实例化时在模板候选结构中定位列表/字段。

只做保守推断：找不到就返回 None，绝不猜语义（数字码状态等留给运行期沉淀）。
"""

import json
import re

from app.adapters.fields import dig, dig_list
from app.llm.schemas import (
    AuthSpec,
    Condition,
    FieldMapping,
    ObservedApplication,
    PageSource,
    RecipeGenOutput,
    RecipeMeta,
    RecipeSpec,
    StatusMapEntry,
    XHRSource,
)

_TITLE_RE = re.compile(r"(apply)?position(name|title)?|jobname|jobtitle|jobad(name|title)|postname|recruitpost|^title$", re.I)
_STATUS_RE = re.compile(r"^(apply|deliver(y)?|process|curr?ent)?status(name|text|label)?|statusstr", re.I)
_DATE_RE = re.compile(r"(deliver|apply|submit|create|delivery)(time|date)|createdat", re.I)
_DEPT_RE = re.compile(r"department(name)?|deptname|orgname|teamname|businessgroup|bu$", re.I)
_LOC_RE = re.compile(r"(work|job)?location(name)?|cityname|workplace|workcity|city$", re.I)
_ID_RE = re.compile(r"^(apply|application|deliver|delivery|candidate|resume)?id$", re.I)
_URL_RE = re.compile(r"(job)?url|link|href|detailurl", re.I)

# 中文字段词典（央国企/自研站常见中文键）。_norm_leaf 会剔掉 CJK，纯拉丁正则匹配不到，
# 必须对原始键名单独匹配；别名按特异性排序（先长后短），子串包含即可。
_CJK_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("岗位名称", "职位名称", "招聘职位", "工作名称", "岗位", "职位"),
    "status": ("投递状态", "申请状态", "流程状态", "当前状态", "状态", "进度"),
    "date": ("投递时间", "申请时间", "提交时间", "创建时间", "投递日期", "申请日期"),
    "dept": ("所属部门", "事业部", "部门", "组织"),
    "loc": ("工作地点", "工作城市", "意向城市", "工作地", "城市", "地点"),
    "url": ("详情链接", "职位链接", "链接", "详情"),
}

# 明显与用户身份无关的公共查询参数（heuristic 清洗与验证器参数化检测共用）
COMMON_QUERY_PARAMS = {
    "page", "pageno", "pagenum", "pagesize", "size", "limit", "current", "offset",
    "type", "sort", "order", "t", "timestamp", "_t", "lang", "locale", "status", "tab",
}

# 常见列表载体路径（按命中率排序）；"" = 根即列表。
# data.delivery_list 来自飞书 ATSX 真实响应（2026-09-01 实测校准）
_LIST_PATH_CANDIDATES = [
    "data.list", "data.records", "data.data", "data.rows", "data.items",
    "data.application_list", "data.delivery_list", "data.apply_list", "data.applications",
    "data.applyList", "data.applicationList", "data.listData", "data.result",
    "result.list", "result.records", "result.data", "result",
    "list", "records", "rows", "items", "data", "",
]

# 通用递归扫描的深度与打分信号（固定候选路径未命中时的兜底，覆盖任意形状的自研接口）
_GENERIC_MAX_DEPTH = 4
_PATH_HINT_RE = re.compile(r"apply|deliver|application|record|order|mine|center|progress|list|item|rows|entr", re.I)


def _scan_keys(item: dict, depth: int = 0, prefix: str = ""):
    """展平一层嵌套，产出 (点路径, 值)。"""
    for key, value in item.items():
        path = f"{prefix}{key}"
        yield path, value
        if depth < 1 and isinstance(value, dict):
            yield from _scan_keys(value, depth + 1, prefix=f"{path}.")


def _norm_leaf(path: str) -> str:
    """键名归一化：去点/下划线/横线并小写——让 job_title、jobTitle、job-title 同等匹配。"""
    return re.sub(r"[^a-z0-9]", "", path.rsplit(".", 1)[-1].lower())


def _raw_leaf(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def _pick(candidates: list[tuple[str, bool]], regex: re.Pattern, cjk: tuple[str, ...] = ()) -> str | None:
    """在标量候选键里挑最佳匹配：精确正则全匹配优先，其次包含，最后中文别名；浅路径优先。

    只考虑标量叶子（bool 标记）——「positionInfo」这类容器键名撞上正则时，
    必须让位给真正的值路径「positionInfo.applyPositionTxt」。
    """
    scalars = [path for path, scalar_ok in candidates if scalar_ok]
    full = [path for path in scalars if regex.fullmatch(_norm_leaf(path))]
    if full:
        return sorted(full, key=lambda p: p.count("."))[0]
    partial = [path for path in scalars if regex.search(_norm_leaf(path))]
    if partial:
        return sorted(partial, key=lambda p: p.count("."))[0]
    cjk_hits = [path for path in scalars if any(alias in _raw_leaf(path) for alias in cjk)]
    if cjk_hits:
        return sorted(cjk_hits, key=lambda p: p.count("."))[0]
    return None


def _item_candidates(item: dict) -> list[tuple[str, bool]]:
    return [
        (path, isinstance(value, (str, int, float)) and not isinstance(value, bool))
        for path, value in _scan_keys(item)
    ]


def guess_field_map(item: dict) -> dict[str, str] | None:
    """从列表项推断字段映射；job_title 与 status_raw 缺一即放弃。"""
    candidates = _item_candidates(item)

    title = _pick(candidates, _TITLE_RE, _CJK_ALIASES["title"])
    status = _pick(candidates, _STATUS_RE, _CJK_ALIASES["status"])
    if not title or not status:
        return None
    mapping = {"job_title": title, "status_raw": status}
    for key, regex, cjk in (
        ("id", _ID_RE, ()), ("department", _DEPT_RE, _CJK_ALIASES["dept"]),
        ("work_location", _LOC_RE, _CJK_ALIASES["loc"]),
        ("applied_at", _DATE_RE, _CJK_ALIASES["date"]), ("job_url", _URL_RE, _CJK_ALIASES["url"]),
    ):
        hit = _pick(candidates, regex, cjk)
        if hit:
            mapping[key] = hit
    return mapping


# ── 通用递归列表定位（固定候选路径的兜底）────────────────────


def _generic_list_candidates(data) -> list[tuple[str, list]]:
    """收集全部 dict 数组候选（深度 ≤4）。

    沿 dict 下降；遇数组时若本身是 dict 数组则收集为候选，否则以首元素代表形状
    继续下探并记 ``*`` 展开段——覆盖「分组列表」形态（北森：Submissions[*].Datas[]，
    按人/志愿分组，组内才是投递数组）。
    """
    out: list[tuple[str, list]] = []

    def consider(value, path: str, depth: int) -> None:
        if depth > _GENERIC_MAX_DEPTH:
            return
        if isinstance(value, list):
            if value and all(isinstance(x, dict) for x in value[:5]):
                out.append((path, value))
            # dict 数组也要穿透（北森分组列表：Submissions[*] 组内才是 Datas[]）——
            # 收集为候选（多数站到此为止）与以首元素下探找内层列表，两者并行不互斥
            if value and isinstance(value[0], (dict, list)):
                descend(value[0], f"{path}.*", depth + 1)
        elif isinstance(value, dict):
            descend(value, path, depth + 1)

    def descend(node, path: str, depth: int) -> None:
        if depth > _GENERIC_MAX_DEPTH or not isinstance(node, (dict, list)):
            return
        if isinstance(node, dict):
            for key, value in node.items():
                consider(value, f"{path}.{key}" if path else key, depth)
        elif isinstance(node, list):
            consider(node, f"{path}.*", depth)

    consider(data, "", 0)
    return out


def _list_signal_score(path: str, items: list) -> float:
    """投递列表特征分：状态字段是最强信号（推荐职位列表没有逐条申请状态）。"""
    candidates = _item_candidates(items[0])
    score = 2.0 if _PATH_HINT_RE.search(path) else 0.0
    if _pick(candidates, _STATUS_RE, _CJK_ALIASES["status"]):
        score += 3.0
    if _pick(candidates, _TITLE_RE, _CJK_ALIASES["title"]):
        score += 2.0
    if _pick(candidates, _ID_RE):
        score += 1.0
    score += min(len(items), 100) * 0.01 - path.count(".") * 0.1
    return score


def _is_list_node(node) -> bool:
    return isinstance(node, list) and node and all(isinstance(x, dict) for x in node[:5])


def _node_at(data, path: str) -> list | None:
    # dig_list 统一语义：dict 自动按一条处理（单对象）、* 段展开（分组列表）
    return dig_list(data, path)


def _acceptable(data, path: str) -> list | None:
    """路径候选接受条件：能定位出 dict 列表，且首项可推断出 title+status 字段映射。

    没有这一层，「data 为 dict」的固定候选会抢占真正的嵌套列表
    （如 data.pageData.applyRecords），单对象语义反而挡住通用扫描。
    """
    node = _node_at(data, path)
    if not _is_list_node(node):
        return None
    return node if guess_field_map(node[0]) else None


def derive_list_json_path(data) -> str | None:
    """定位响应中的投递列表：固定校准路径优先（字段可映射才接受），未命中则通用递归打分扫描。"""
    if isinstance(data, dict):
        for path in _LIST_PATH_CANDIDATES:
            if _acceptable(data, path) is not None:
                return path
        generic = sorted(
            _generic_list_candidates(data),
            key=lambda c: _list_signal_score(c[0], c[1]),
            reverse=True,
        )
        for path, _items in generic:
            if _acceptable(data, path) is not None:
                return path
        return None
    if _is_list_node(data):
        return ""
    return None


def locate_list(data) -> list[dict] | None:
    """定位列表节点：单对象自动按一条处理（derive_list_json_path 的节点版本）。"""
    if isinstance(data, dict):
        path = derive_list_json_path(data)
        if path is None:
            return None
        node = _node_at(data, path)
        return node if _is_list_node(node) else None
    if _is_list_node(data):
        return data
    return None


def build_recipe(url_pattern: str, method: str, data, sample_url: str, request_body: str | None = None) -> RecipeGenOutput | None:
    """heuristic 提供者：从单个响应构造配方草稿 + 自述清单（自述即提取，天然一致）。

    只处理能确定性推断的部分；数字码等未知语义一律声明留给兜底。
    URL 清洗：非公共查询参数（用户特有 id/token）从 url_pattern 剥除——
    烙进配方的用户标识过不了回放验证的参数化断言。
    POST 型接口：请求体解析为 dict 后原样带入配方（运行时按同样形状重放）。
    """
    path = derive_list_json_path(data)
    if path is None:
        return None
    items = locate_list(data)
    if not items:
        return None
    fmap = guess_field_map(items[0])
    if not fmap:
        return None

    body: dict[str, str] | None = None
    if method == "POST":
        if not request_body:
            return None  # POST 而无请求体：运行时无法重放，不做（宁缺毋错）
        try:
            parsed = json.loads(request_body)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        body = {str(k): str(v) for k, v in parsed.items()}

    base, safe_query = sanitize_url(url_pattern)
    recipe = RecipeSpec(
        auth=AuthSpec(
            login_success=Condition(url_contains=[_path_hint(sample_url)]),
            session_invalid=Condition(url_contains=["login", "signin", "passport"]),
        ),
        list_source=XHRSource(
            url_pattern=base,
            method=method,
            list_json_path=path,
            query=safe_query,
            body=body,
        ),
        field_map={
            key: FieldMapping(json_path=value) for key, value in fmap.items()
        },
        status_map=[],
        meta=RecipeMeta(generated_by="heuristic"),
    )

    from app.llm.extract import extract_from_json

    records = extract_from_json(recipe, data)
    unmapped = sorted({
        r.status_raw for r in records
        if r.status_raw and _needs_fallback(r.status_raw)
    })
    return RecipeGenOutput(
        recipe=recipe,
        observations=[ObservedApplication(job_title=r.job_title or "", status_raw=r.status_raw or "") for r in records],
        unmapped_status_texts=unmapped,
        confidence=0.5,
    )


def build_page_recipe(entry_url: str, data, sample_url: str) -> RecipeGenOutput | None:
    """从 SSR 内嵌数据块构造 page 型配方：运行时 GET 页面 → 按锚提取同一数据。

    适用「记录页直出、不发列表 XHR」的自研站（腾讯校准实测形态）。
    仅当确定性推断成立（列表可定位 + 字段可映射）才生成；
    正确性仍由回放验证把关——内嵌块即考卷，与运行时共用同一提取引擎。
    """
    base = str(entry_url).split("#", 1)[0]
    frag = str(entry_url).split("#embedded-", 1)[1] if "#embedded-" in entry_url else ""
    # 扩展的锚格式：#embedded-js-<变量名>（可执行 JS 赋值）或 #embedded-<script id>；
    # 纯数字序号（无 id 的 JSON 型 script）不作为锚——运行时退回「第一个可定位的数据对象」
    if frag.startswith("js-"):
        anchor = frag[3:]
    else:
        anchor = frag if frag and not frag.isdigit() else ""

    path = derive_list_json_path(data)
    items = locate_list(data)
    if path is None or not items:
        return None
    fmap = guess_field_map(items[0])
    if not fmap:
        return None

    recipe = RecipeSpec(
        auth=AuthSpec(
            login_success=Condition(url_contains=[_path_hint(sample_url)]),
            session_invalid=Condition(url_contains=["login", "signin", "passport"]),
        ),
        list_source=PageSource(page_url=base, data_anchor=anchor, list_json_path=path),
        field_map={key: FieldMapping(json_path=value) for key, value in fmap.items()},
        status_map=[],
        meta=RecipeMeta(generated_by="heuristic:page"),
    )

    from app.llm.extract import extract_from_embedded

    records = extract_from_embedded(recipe, data)
    unmapped = sorted({
        r.status_raw for r in records
        if r.status_raw and _needs_fallback(r.status_raw)
    })
    return RecipeGenOutput(
        recipe=recipe,
        observations=[ObservedApplication(job_title=r.job_title or "", status_raw=r.status_raw or "") for r in records],
        unmapped_status_texts=unmapped,
        confidence=0.45,
    )


def _needs_fallback(raw: str) -> bool:
    from app.domain.normalize import normalize_status

    return normalize_status(raw) == "pending_confirm"


def _path_hint(sample_url: str) -> str:
    from urllib.parse import urlsplit

    segments = [p for p in urlsplit(sample_url or "").path.split("/") if p]
    # 纯数字段是租户/站点 ID（如飞书官网路径 /704852/position/application 的 704852）：
    # 非用户特有，但会误触验证器的「采样用户标识」断言；剔除后判定条件跨租户更通用
    meaningful = [p for p in segments if not p.isdigit()]
    return "/".join(meaningful[:2]) or "apply"


def sanitize_url(url: str) -> tuple[str, dict[str, str]]:
    """剥除用户特有查询参数，保留公共分页参数。返回 (清洗后 URL, 安全参数)。"""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    safe = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() in COMMON_QUERY_PARAMS]
    base = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe), ""))
    return base, dict(safe)
