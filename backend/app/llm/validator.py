"""离线回放验证器（LLM_DESIGN.md §2.4）——反幻觉的核心，纯确定性代码，无 LLM。

拿采样包本身当考卷，用我们自己的提取引擎执行配方草稿。
七条断言全部通过才算验证成功；任一失败返回错误清单（回喂 LLM 自修正）。
同一份引擎在线上跑轮询，因此这里验证通过 ≈ 运行时可提取。
"""

import json
import re
from dataclasses import dataclass, field

from lxml import cssselect, html as lxml_html

from app.domain.normalize import normalize_status
from app.llm import preprocess
from app.llm.extract import ExtractionError, ExtractedRecord, extract_from_embedded, extract_records
from app.llm.heuristics import COMMON_QUERY_PARAMS as _COMMON_PARAMS
from app.llm.schemas import PLACEHOLDER_RE, Condition, DOMSource, PageSource, RecipeGenOutput, RecipeSpec, XHRSource

MAX_RECORDS = 500
MAX_ITEM_SELECTOR_HITS = 200
_ID_KEY_RE = re.compile(r"(?:user|resume|candidate|applicant|account|member|emp|staff)[_]?id", re.I)
_LONG_TOKEN_RE = re.compile(r"\d{6,}")


@dataclass
class Verdict:
    ok: bool
    errors: list[str] = field(default_factory=list)
    records: list[ExtractedRecord] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def fail(self, msg: str) -> None:
        self.errors.append(msg)


def compile_url_pattern(pattern: str) -> re.Pattern:
    """配方 URL 模式 → 正则：* 通配任意串，{{占位符}} 捕获用户特有段。"""
    regex = ""
    i = 0
    for m in PLACEHOLDER_RE.finditer(pattern):
        regex += re.escape(pattern[i:m.start()])
        regex += f"(?P<{m.group(1)}>[^/?#]+)"
        i = m.end()
    regex += re.escape(pattern[i:]).replace(r"\*", ".*")
    return re.compile(regex)


def match_xhr_entries(pattern: str, entries: list[dict]) -> list[dict]:
    """在采样请求-响应对中找出匹配配方的条目（疑似投递列表者优先）。"""
    regex = compile_url_pattern(pattern)
    prepared = {p.url: p.list_score for p in preprocess.filter_network(entries or [])}
    hits = [e for e in (entries or []) if regex.search(str(e.get("url") or "")) and _json_body(e)]
    hits.sort(key=lambda e: prepared.get(str(e.get("url")), -1), reverse=True)
    return hits


def _json_body(entry: dict) -> bool:
    return str(entry.get("response_body") or "").lstrip()[:1] in ("{", "[")


def _condition_matches(cond: Condition, url: str, dom_tree) -> bool:
    if any(u and u in url for u in cond.url_contains):
        return True
    if cond.selector_exists:
        try:
            return bool(cssselect.CSSSelector(cond.selector_exists)(dom_tree))
        except cssselect.SelectorSyntaxError:
            return False
    return False  # status_code 分支只能在运行时判定，离线视为不命中


def collect_user_identifiers(sample_url: str, entries: list[dict]) -> set[str]:
    """从采样中收集「采样用户特有标识值」候选，供参数化断言交叉检测。"""
    found: set[str] = set()

    def add(value: str) -> None:
        value = str(value).strip()
        if len(value) >= 4 and value not in _COMMON_PARAMS:
            found.add(value)

    for entry in entries or []:
        url = str(entry.get("url") or "")
        for key, value in (entry.get("params") or {}).items():
            if str(key).lower() not in _COMMON_PARAMS:
                add(value)
        for token in _LONG_TOKEN_RE.findall(url.split("?")[0]):
            add(token)
        request_body = str(entry.get("request_body") or "")
        if request_body.lstrip()[:1] in ("{", "["):
            try:
                _scan_scalars(json.loads(request_body), add)
            except (json.JSONDecodeError, ValueError):
                pass
        body = str(entry.get("response_body") or "")
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            continue
        _scan_id_keys(data, add, depth=0)

    for token in _LONG_TOKEN_RE.findall(sample_url or ""):
        add(token)
    found.discard("")
    return found


def _scan_id_keys(node, add, depth: int) -> None:
    if depth > 2:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if _ID_KEY_RE.search(str(key)) and isinstance(value, (str, int)):
                add(value)
            _scan_id_keys(value, add, depth + 1)
    elif isinstance(node, list):
        for item in node[:5]:
            _scan_id_keys(item, add, depth + 1)


def _scan_scalars(node, add, depth: int = 0) -> None:
    """请求体的标量值视为用户/会话特有（搜索词、id、token 等）。"""
    if depth > 2:
        return
    if isinstance(node, dict):
        for value in node.values():
            _scan_scalars(value, add, depth + 1)
    elif isinstance(node, list):
        for item in node[:10]:
            _scan_scalars(item, add, depth + 1)
    elif isinstance(node, (str, int)) and not isinstance(node, bool):
        add(node)


def replay(output: RecipeGenOutput, sample_url: str, sample_dom: str | None, entries: list[dict]) -> Verdict:
    """对采样包回放配方 + LLM 自述清单，执行断言 1–7。"""
    verdict = Verdict(ok=False)
    recipe = output.recipe
    src = recipe.list_source

    # ── 定位列表数据源并提取（断言 5 在提取中一并检查）──
    data = None
    try:
        if isinstance(src, XHRSource):
            if "#embedded" in src.url_pattern:
                verdict.fail("断言1失败：url_pattern 指向 SSR 内嵌数据块（#embedded），不能作为在线轮询接口")
                return verdict
            hits = match_xhr_entries(src.url_pattern, entries)
            if not hits:
                verdict.fail(f"断言1失败：list_source.url_pattern 未命中采样中任何 JSON 请求（pattern={src.url_pattern!r}）")
                return verdict
            try:
                data = json.loads(str(hits[0].get("response_body")))
            except (json.JSONDecodeError, ValueError) as e:
                verdict.fail(f"断言1失败：匹配到的请求响应体不是合法 JSON: {e}")
                return verdict
            verdict.stats["matched_xhr"] = str(hits[0].get("url"))
            records = extract_records(recipe, data=data)
        elif isinstance(src, PageSource):
            # page 型：数据源是采样里的 #embedded 内嵌块（与运行时 GET 页面后
            # embedded.find_embedded 的定位规则一致，此处用其 JSON 直接回放）
            cand = None
            for entry in entries or []:
                url = str(entry.get("url") or "")
                base = url.split("#", 1)[0]
                if "#embedded" not in url:
                    continue
                if base.rstrip("/") != (src.page_url or "").rstrip("/"):
                    continue
                if src.data_anchor and src.data_anchor not in url:
                    continue
                cand = entry
                break
            if cand is None:
                verdict.fail(
                    f"断言1失败：page 型配方的数据锚未命中采样内嵌数据块"
                    f"（page_url={src.page_url!r}, anchor={src.data_anchor!r}）"
                )
                return verdict
            try:
                data = json.loads(str(cand.get("response_body")))
            except (json.JSONDecodeError, ValueError) as e:
                verdict.fail(f"断言1失败：内嵌数据块不是合法 JSON: {e}")
                return verdict
            verdict.stats["matched_embedded"] = str(cand.get("url"))
            records = extract_from_embedded(recipe, data)
        else:
            assert isinstance(src, DOMSource)
            if not sample_dom:
                verdict.fail("断言1失败：dom 型配方但采样缺少 DOM")
                return verdict
            records = extract_records(recipe, dom_html=sample_dom)
            verdict.stats["item_hits"] = len(records)
    except ExtractionError as e:
        verdict.fail(f"断言1失败：提取引擎执行配方出错：{e}")
        return verdict

    # ── 断言 1：1 ≤ 记录数 ≤ 500 ──
    if not (1 <= len(records) <= MAX_RECORDS):
        verdict.fail(f"断言1失败：提取记录数 {len(records)} 不在 [1, {MAX_RECORDS}]")
        return verdict

    # ── 断言 2：必填字段非空率 100% ──
    empty_title = sum(1 for r in records if not r.job_title)
    empty_status = sum(1 for r in records if not r.status_raw)
    if empty_title or empty_status:
        verdict.fail(f"断言2失败：job_title 空值 {empty_title} 条、status_raw 空值 {empty_status} 条（要求 100% 非空）")
        return verdict

    # ── 断言 3：status_map 覆盖每一个不同原文（或显式留给兜底）──
    bad_patterns = []
    for entry in recipe.status_map:
        try:
            re.compile(entry.pattern)
        except re.error as e:
            bad_patterns.append(f"{entry.pattern!r}: {e}")
    if bad_patterns:
        verdict.fail("断言3失败：status_map 存在非法正则 → " + "; ".join(bad_patterns))
    distinct_raws = sorted({r.status_raw for r in records})
    unmapped_declared = {t.strip() for t in output.unmapped_status_texts}
    for raw in distinct_raws:
        covered = False
        for entry in recipe.status_map:
            try:
                if re.search(entry.pattern, raw, re.IGNORECASE):
                    covered = True
                    break
            except re.error:
                continue  # 非法正则已在上方单独报告
        if not covered and normalize_status(raw) == "pending_confirm" and raw not in unmapped_declared:
            verdict.fail(
                f"断言3失败：原文 {raw!r} 既未被 status_map 覆盖、通用规则不识别、也未显式声明留给兜底"
            )
    for text in unmapped_declared:
        if text not in distinct_raws:
            verdict.fail(f"断言3失败：声明的兜底原文 {text!r} 不在提取结果中（禁止编造）")

    # ── 断言 4：提取结果与 LLM 自述清单逐条一致 ──
    got = sorted((r.job_title or "", r.status_raw or "") for r in records)
    claimed = sorted((o.job_title.strip(), o.status_raw.strip()) for o in output.observations)
    if got != claimed:
        missing = [f"{t}|{s}" for t, s in claimed if (t, s) not in got][:8]
        extra = [f"{t}|{s}" for t, s in got if (t, s) not in claimed][:8]
        verdict.fail(
            f"断言4失败：提取结果与自述清单不一致（提取 {len(got)} 条 vs 自述 {len(claimed)} 条；"
            f"自述有而提取无: {missing}；提取有而自述无: {extra}）"
        )

    # ── 断言 5：dom 选择器特异性 ──
    if isinstance(src, DOMSource):
        if len(records) > MAX_ITEM_SELECTOR_HITS:
            verdict.fail(f"断言5失败：item_selector 命中 {len(records)} 节点，超过 {MAX_ITEM_SELECTOR_HITS}（过泛匹配）")

    # ── 断言 6：登录判定能区分采样包状态 ──
    dom_tree = lxml_html.fromstring(sample_dom or "<html><body></body></html>")
    if not _condition_matches(recipe.auth.login_success, sample_url or "", dom_tree):
        verdict.fail("断言6失败：login_success 条件在采样（已登录页）上不成立，无法据此判定登录成功")
    if _condition_matches(recipe.auth.session_invalid, sample_url or "", dom_tree):
        verdict.fail("断言6失败：session_invalid 条件在采样（已登录页）上成立，登录/失效判定无法区分")

    # ── 断言 7：配方不得烙入采样用户特有标识值；占位符必须声明解析方式 ──
    user_ids = collect_user_identifiers(sample_url, entries)
    recipe_text = json.dumps(recipe.model_dump(mode="json"), ensure_ascii=False)
    for value in sorted(user_ids):
        if value and value in recipe_text:
            verdict.fail(
                f"断言7失败：采样用户特有标识值 {value!r} 出现在配方中，必须参数化为 {{{{占位符}}}} 并在 runtime_params 声明解析方式"
            )
    declared = set(recipe.runtime_params)
    used = recipe.placeholders()
    for name in sorted(used - declared):
        verdict.fail("断言7失败：占位符 {{" + name + "}} 未在 runtime_params 声明运行时解析方式")
    for name in sorted(declared - used):
        verdict.fail("断言7失败：runtime_params 声明了未使用的占位符 {{" + name + "}}")

    verdict.records = records
    verdict.stats.update(
        {
            "records": len(records),
            "distinct_raws": distinct_raws,
            "user_id_candidates": len(user_ids),
        }
    )
    verdict.ok = not verdict.errors
    return verdict
