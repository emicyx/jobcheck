"""配方提取引擎：验证器（离线回放）与运行时适配器共用的唯一实现。

输入配方 + 数据（XHR 响应 JSON 或页面 DOM），输出提取记录。
不跳过缺必填字段的记录——是否容忍由调用方决定（验证器要 100% 非空，
运行时适配器过滤脏数据）。这正是「同一份代码既管质量又跑回归」的落点。
"""

from dataclasses import dataclass
from datetime import date

from lxml import cssselect, html as lxml_html

from app.adapters.fields import dig, dig_list, parse_date
from app.llm.schemas import DOMSource, FieldMapping, PageSource, RecipeSpec, XHRSource


@dataclass
class ExtractedRecord:
    job_title: str | None
    status_raw: str | None
    portal_key: str | None = None
    department: str | None = None
    work_location: str | None = None
    applied_at: date | None = None
    job_url: str | None = None


def _clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _map_field(mapping: FieldMapping | None):
    if mapping is None:
        return None
    if mapping.json_path:
        return ("json", mapping.json_path)
    if mapping.selector:
        return ("dom", mapping.selector, mapping.attr)
    return None


def records_from_items(field_map: dict[str, FieldMapping], items) -> list[ExtractedRecord]:
    """对列表项执行 JSON 字段映射——xhr 型与 page 型（内嵌数据）共用的唯一实现。"""
    fmap = {k: _map_field(v) for k, v in field_map.items()}
    records: list[ExtractedRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        def take(key: str):
            m = fmap.get(key)
            if not m or m[0] != "json":
                return None
            return dig(item, m[1])

        records.append(
            ExtractedRecord(
                job_title=_clean(take("job_title")),
                status_raw=_clean(take("status_raw")),
                portal_key=(str(take("id")) if take("id") is not None else None),
                department=_clean(take("department")),
                work_location=_clean(take("work_location")),
                applied_at=parse_date(take("applied_at")),
                job_url=_clean(take("job_url")),
            )
        )
    return records


def extract_from_json(recipe: RecipeSpec, data) -> list[ExtractedRecord]:
    """对 XHR 响应 JSON 执行配方：list_json_path 定位列表，字段用相对点路径提取。

    单对象响应自动按一条记录处理（腾讯「单申请进度」模型的既有语义）。
    """
    src = recipe.list_source
    assert isinstance(src, XHRSource)  # 调用方保证
    items = dig_list(data, src.list_json_path)
    if items is None:  # 路径无效；命中但为空（翻页末页）合法返回 0 条
        raise ExtractionError(
            f"list_json_path 定位失败: {src.list_json_path!r}"
        )
    return records_from_items(recipe.field_map, items)


def extract_from_embedded(recipe: RecipeSpec, data) -> list[ExtractedRecord]:
    """对 page 型配方的内嵌数据对象执行提取（验证器回放走这里，与运行时同引擎）。

    data 是已定位到的内嵌数据对象（采样侧来自 #embedded 条目，运行时来自
    embedded.find_embedded 对页面 HTML 的解析）；list_json_path 相对该对象。
    """
    src = recipe.list_source
    assert isinstance(src, PageSource)
    items = dig_list(data, src.list_json_path) if src.list_json_path else (
        [data] if isinstance(data, dict) else [x for x in data if isinstance(x, dict)] if isinstance(data, list) else None
    )
    if items is None:
        raise ExtractionError(
            f"list_json_path 定位失败: {src.list_json_path!r}"
        )
    return records_from_items(recipe.field_map, items)


def extract_from_page(recipe: RecipeSpec, html_text: str) -> list[ExtractedRecord]:
    """page 型配方的运行时入口：GET 到的页面 HTML → 按锚定位内嵌数据 → 提取。"""
    from app.llm.embedded import find_embedded

    src = recipe.list_source
    assert isinstance(src, PageSource)
    _, items = find_embedded(html_text, src.data_anchor, src.list_json_path)
    if items is None:
        raise ExtractionError(
            f"页面内嵌数据定位失败（anchor={src.data_anchor!r}, path={src.list_json_path!r}）——疑似网站改版或登录态失效"
        )
    return records_from_items(recipe.field_map, items)


def extract_from_dom(recipe: RecipeSpec, dom_html: str) -> list[ExtractedRecord]:
    """对页面 DOM 执行配方：item_selector 逐卡片提取。

    仅用于离线验证与后续 Playwright 运行时；在线 HTTP 轮询不渲染 DOM。
    """
    src = recipe.list_source
    assert isinstance(src, DOMSource)
    doc = lxml_html.fromstring(dom_html)
    try:
        item_sel = cssselect.CSSSelector(src.item_selector)
        field_sels = {
            k: cssselect.CSSSelector(v.selector)
            for k, v in recipe.field_map.items()
            if v.selector
        }
    except cssselect.SelectorSyntaxError as e:
        raise ExtractionError(f"非法 CSS 选择器: {e}") from e

    nodes = item_sel(doc)[: src.item_limit]

    def read(el, key: str) -> str | None:
        mapping = recipe.field_map.get(key)
        if mapping is None or not mapping.selector:
            return None
        found = field_sels[key](el)
        if not found:
            return None
        target = found[0]
        if mapping.attr == "text":
            return _clean(target.text_content())
        return _clean(target.get(mapping.attr))

    records: list[ExtractedRecord] = []
    for el in nodes:
        records.append(
            ExtractedRecord(
                job_title=read(el, "job_title"),
                status_raw=read(el, "status_raw"),
                portal_key=read(el, "id"),
                department=read(el, "department"),
                work_location=read(el, "work_location"),
                applied_at=parse_date(read(el, "applied_at")),
                job_url=read(el, "job_url"),
            )
        )
    return records


def extract_records(recipe: RecipeSpec, data=None, dom_html: str | None = None) -> list[ExtractedRecord]:
    if isinstance(recipe.list_source, XHRSource):
        return extract_from_json(recipe, data)
    if dom_html is None:
        raise ExtractionError("dom 型配方需要提供页面 DOM")
    return extract_from_dom(recipe, dom_html)


class ExtractionError(Exception):
    """提取结构与配方不符（选择器/路径定位失败等）。"""
