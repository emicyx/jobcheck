"""采样包预处理（LLM_DESIGN.md §2.1）——决定 LLM 效果的工程核心。

三件事：
1. DOM 裁剪：去脚本/样式/隐藏节点、属性白名单、空白折叠、200KB 上限；
   超限时先做列表区定位（纯算法：找同构重复子节点最多的容器，投递列表天然是重复卡片）。
2. XHR 筛选排序：只留 JSON 响应，滤埋点，最多 20 条，疑似投递列表者靠前。
3. PII 打码：仅作用于「进入 LLM 的视图」；库里原始采样保留原文，
   验证器回放与运行时提取都用原始数据，打码不影响确定性断言。
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit, parse_qsl

from lxml import etree, html as lxml_html

DOM_MAX_BYTES = 200_000
XHR_MAX_ENTRIES = 20
XHR_BODY_PREVIEW = 4_000

_URL_BLACKLIST_RE = re.compile(r"log|track|beacon|analytics|sentry|monitor|statistic|report", re.I)
_LIST_KEY_RE = re.compile(
    r'"(status|position|apply|job|deliver|resume|candidate|application|process|offer|状态|岗位|职位|投递|申请)[a-z_]*"\s*:', re.I
)

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDCARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

_ATTR_WHITELIST = ("id", "class", "type")
_DROP_TAGS = ("script", "style", "noscript", "svg", "iframe", "template", "link", "meta", "head")


def mask_pii(text: str) -> str:
    """手机号/身份证/邮箱 → 占位符。姓名不处理（打码会破坏字段语义判断，且非高敏）。"""
    if not text:
        return text
    text = _EMAIL_RE.sub("‹pii-email›", text)
    text = _IDCARD_RE.sub("‹pii-id›", text)
    text = _PHONE_RE.sub("‹pii-phone›", text)
    return text


# ── XHR 侧 ───────────────────────────────────────


@dataclass
class PreparedXHR:
    url: str
    method: str
    query: dict[str, str] = field(default_factory=dict)
    body_preview: str = ""  # 前 4KB，PII 已打码
    list_score: int = 0

    def to_prompt(self) -> str:
        qs = "&".join(f"{k}={v}" for k, v in self.query.items())
        head = f"{self.method} {self.url}" + (f" ?{qs}" if qs else "")
        return f"{head}\n{self.body_preview}"


def _looks_json(body: str) -> bool:
    stripped = body.lstrip()[:1]
    return stripped in ("{", "[")


def filter_network(entries: list[dict]) -> list[PreparedXHR]:
    """过滤 + 排序：只留 JSON、滤埋点、疑似投递列表靠前。"""
    prepared: list[PreparedXHR] = []
    seen_urls: set[str] = set()
    for entry in entries or []:
        url = str(entry.get("url") or "")[:1000]
        body = str(entry.get("response_body") or "")
        if not url.startswith("http") or not _looks_json(body):
            continue
        if _URL_BLACKLIST_RE.search(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        query = {k: str(v)[:200] for k, v in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
        prepared.append(
            PreparedXHR(
                url=url,
                method=str(entry.get("method") or "GET").upper(),
                query=query,
                body_preview=mask_pii(body[:XHR_BODY_PREVIEW]),
                list_score=len(_LIST_KEY_RE.findall(body[:20_000])),
            )
        )
    prepared.sort(key=lambda x: x.list_score, reverse=True)
    return prepared[:XHR_MAX_ENTRIES]


# ── DOM 侧 ───────────────────────────────────────


def _prune_tree(doc) -> None:
    for el in doc.xpath("//script|//style|//noscript|//svg|//iframe|//template|//link|//meta|//head"):
        el.getparent().remove(el)
    # 属性白名单：id/class/type + data-*；href 截断保留
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr in _ATTR_WHITELIST or attr.startswith("data-"):
                continue
            if attr == "href":
                el.set("href", el.get("href", "")[:120])
            else:
                del el.attrib[attr]
    # 文本空白折叠
    for el in doc.iter():
        if el.text:
            el.text = " ".join(el.text.split()) + (" " if el.text.endswith((" ", "\n")) else "")
        if el.tail:
            el.tail = " ".join(el.tail.split()) + (" " if el.tail.endswith((" ", "\n")) else "")


def _find_list_container(doc):
    """列表区定位：同构重复子节点最多的容器（≥3 个同 tag+class 的直接子节点）。"""
    best, best_count = None, 0
    for el in doc.iter():
        if not isinstance(el.tag, str) or el.tag in ("p", "br", "span"):
            continue
        groups: dict[tuple, int] = {}
        for child in el:
            if not isinstance(child.tag, str):
                continue
            key = (child.tag, child.get("class") or "")
            groups[key] = groups.get(key, 0) + 1
        repeated = max(groups.values(), default=0)
        if repeated >= 3 and repeated > best_count:
            best, best_count = el, repeated
    return best, best_count


def _skeleton(el, max_depth: int = 3) -> str:
    if max_depth <= 0 or not isinstance(el.tag, str):
        return ""
    attrs = f".{el.get('class')}" if el.get("class") else ""
    inner = "".join(_skeleton(c, max_depth - 1) for c in el)
    return f"<{el.tag}{attrs}>{inner}</{el.tag}>"


def prune_dom(dom_html: str) -> str:
    """裁剪 + 上限控制：超限时做列表区定位（保留列表容器子树 + 页面骨架）。"""
    doc = lxml_html.fromstring(dom_html)
    _prune_tree(doc)
    out = etree.tostring(doc, encoding="unicode", method="html")
    if len(out.encode()) <= DOM_MAX_BYTES:
        return out

    body = doc.find("body") if doc.tag == "html" else doc
    container, count = _find_list_container(doc)
    if container is not None and body is not None:
        kept = (
            _skeleton(body)
            + "\n<!-- JobCheck: 以下为定位到的列表区（页面其余部分已裁剪） -->\n"
            + etree.tostring(container, encoding="unicode", method="html")
        )
        if len(kept.encode()) <= DOM_MAX_BYTES:
            return kept
    return out[:DOM_MAX_BYTES]


# ── 汇总 ─────────────────────────────────────────


@dataclass
class PreparedPackage:
    """进入 LLM 的采样包视图（PII 已打码）。"""

    url: str
    dom: str
    xhrs: list[PreparedXHR]

    def to_prompt(self) -> str:
        parts = [
            "<<<SAMPLE_PACKAGE_BEGIN>>>",
            f"页面 URL: {self.url}",
            "",
            "== 页面 DOM（裁剪后）==",
            self.dom,
            "",
            f"== 本页请求-响应对（JSON，按疑似投递列表排序，共 {len(self.xhrs)} 条）==",
        ]
        for i, xhr in enumerate(self.xhrs, 1):
            parts.append(f"--- XHR #{i} ---")
            parts.append(xhr.to_prompt())
        parts.append("<<<SAMPLE_PACKAGE_END>>>")
        return "\n".join(parts)


def prepare(sample_url: str, dom: str | None, network: list[dict] | None) -> PreparedPackage:
    return PreparedPackage(
        url=sample_url or "",
        dom=mask_pii(prune_dom(dom or "<html><body></body></html>")),
        xhrs=filter_network(network or []),
    )
