"""T3 DOM 解析 LLM 层：规则版 dom_records 失败或可信度不足时的接管者（dom_parse.md）。

定位与边界（LLM_DESIGN 的分层哲学延续）：
- 规则先跑（免费毫秒级），ingest.dom_plausibility 打可信度分：高分直接采信，
  低分/无结果交给本层——非模板版式（步骤条/时间线/图标 title/英文）的事实
  主解析器；本层不可用（未配置/超预算/上游故障）时规则结果仍作降级兜底；
- LLM 只负责「提取」（哪段文本是状态/岗位名），语义归一仍走
  normalize → T2 classify 的既有链路；dom LLM 的高置信语义建议以
  StatusRule 形式沉淀（classify._save_rule），下次同步变确定性零成本；
- 反幻觉双保险：status_raw/job_title 要求逐字照抄页面文本，且输出词元必须
  能在页面大纲中回查到，查不到整条丢弃（宁缺毋错：漏一条远好过错一条）；
- 失败安全：provider 未配置 / 预算熔断 / 上游异常 / 输出不过 Schema 一律
  返回 None，绝不阻塞快照主链路；调用超时收紧到 20s×1 次（快照上报在
  请求路径内，扩展只等 30s）；
- 成本控制：DOM 先压成文本大纲（≤llm_dom_max_chars 字符）再进提示词，
  结果按 (dom 哈希, 模型, 提示词版本) LRU 缓存，调用经 client.call_json
  记账并受月预算熔断约束。
"""

import hashlib
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.adapters.fields import parse_date
from app.core.config import settings
from app.llm import client
from app.llm.extract import ExtractedRecord

logger = logging.getLogger("jobcheck.llm.dom")

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_VERSION_RE = re.compile(r"version:\s*([0-9.]+)")

# 单条记录的入档门槛与上限（宁缺毋错）
_RECORD_CONF_MIN = 0.5  # 低于此置信度的记录整条丢弃
_SUGGEST_CONF_MIN = 0.9  # 语义建议沉淀为 StatusRule 的门槛（比记录门槛苛刻）
_MAX_RECORDS = 50
_TITLE_MAX = 120
_STATUS_MAX = 80

# 与 ingest 规则层同一批实盘教训的后过滤（炎魂：页脚备案号成岗位名；
# bilibili：人才库横幅整句成岗位名）——LLM 也要过同一道闸
_NOISE_TITLE_RE = re.compile(r"公网安备|ICP备|备案|Copyright|All Rights|©|人才库|。", re.I)

# ── DOM → 压缩文本大纲 ─────────────────────────────────────────────
# 只保留「有直接文本（或 title 属性）」的元素：一条缩进行 = 一个可见文本块，
# 层级由缩进表达。script/style 等在扩展侧已被剔除，这里再防一层；裁剪 DOM
# 的属性白名单只有 id/class/href/type/title，其中语义有用的就是 class/id/title。

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas",
              "iframe", "link", "meta", "base", "object", "embed"}
_INDENT = "  "
_MAX_DEPTH = 24
_LINE_MAX = 220
_TEXT_MAX = 120
_CLASS_MAX = 60


def _direct_text(el) -> str:
    """元素自身文本（不含子元素内部文本）：el.text + 各子元素的 tail。"""
    parts = []
    if el.text:
        parts.append(el.text)
    for child in el:
        if child.tail:
            parts.append(child.tail)
    return " ".join(" ".join(parts).split())


def _format_line(el, depth: int, text: str, title_attr: str) -> str:
    tag = el.tag.lower()
    cls = "." + ".".join((el.get("class") or "").split())[:_CLASS_MAX]
    parts = [_INDENT * min(depth, _MAX_DEPTH), f"<{tag}"]
    if len(cls) > 1:
        parts.append(cls)
    if el.get("id"):
        parts.append(f"#{el.get('id')}")
    if title_attr:
        parts.append(f" [title={title_attr[:40]}]")
    # 「> 文本」带空格：title 属性与文本不相邻（glm-4-flash 实测会把
    # 「[title=已拒绝]✕」粘成「已拒绝✕」整块照抄，回查反而失败）
    parts.append(">")
    if text:
        parts.append(" " + text[:_TEXT_MAX] + ("…" if len(text) > _TEXT_MAX else ""))
    return "".join(parts)[:_LINE_MAX]


def dom_outline(html_text: str, max_chars: int | None = None) -> str | None:
    """裁剪 HTML → 文本大纲（预算内截断）。无文本块/解析失败返回 None。"""
    import lxml.html

    try:
        root = lxml.html.fromstring(html_text or "")
    except Exception:
        return None
    budget = max_chars if max_chars is not None else settings.llm_dom_max_chars
    lines: list[str] = []
    total = 0
    overflow = False

    def walk(el, depth: int) -> None:
        nonlocal total, overflow
        if overflow:
            return
        for child in el:
            if not isinstance(child.tag, str) or child.tag.lower() in _SKIP_TAGS:
                continue
            text = _direct_text(child)
            title_attr = " ".join((child.get("title") or "").split())
            if title_attr and text and re.fullmatch(r"[^\w]+", text):
                # 图标元素：可见文本是纯装饰符号（✕◦✓），语义在 title 属性——
                # 大纲以 title 作该元素文本。glm-4-flash 实测两连坑：把符号粘进
                # 状态照抄（「已拒绝✕」）、或直接把符号当 status_raw（「✕」），
                # 提示词约束压不住，歧义必须在表示层消解
                text, title_attr = title_attr, ""
            if text or title_attr:
                line = _format_line(child, depth, text, title_attr)
                if total + len(line) + 1 > budget:
                    overflow = True
                    return
                lines.append(line)
                total += len(line) + 1
            walk(child, depth + 1)

    walk(root, 0)
    if not lines:
        return None
    if overflow:
        lines.append(f"…[DOM 大纲超出 {budget} 字符预算，剩余内容已截断]")
    return "\n".join(lines)


# ── 提示词加载（版本号写在文件首行注释；枚举从状态机单一事实源动态注入）──────

def _status_enum_doc() -> str:
    from app.domain.statuses import all_defs

    return "\n".join(
        f"- {d['key']} = {d['label']}（{d['group']}）" for d in sorted(all_defs(), key=lambda x: x["order"])
    )


@lru_cache(maxsize=2)
def _load_system_prompt() -> tuple[str, str]:
    text = (_PROMPTS_DIR / "dom_parse.md").read_text(encoding="utf-8")
    m = _VERSION_RE.search(text.splitlines()[0] if text else "")
    version = m.group(1) if m else "0"
    return text.replace("{{STATUS_ENUMS}}", _status_enum_doc()), version


# ── 输出 Schema 与后过滤 ───────────────────────────────────────────
# 字段一律宽松默认值（缺失不炸），质量门槛由后过滤把关——LLM 输出的
# 常见偏差是漏字段而不是多字段，炸在校验上只会白白浪费一次调用。

from pydantic import BaseModel


class DomRecord(BaseModel):
    job_title: str = ""
    status_raw: str = ""
    applied_at: str = ""
    department: str = ""
    work_location: str = ""
    status: str | None = None
    confidence: float = 0.0


class DomParseOutput(BaseModel):
    page_type: str = "other"  # applications | job_ads | login | other
    records: list[DomRecord] = []
    reason: str = ""


@dataclass
class DomParseResult:
    records: list[ExtractedRecord] = field(default_factory=list)
    suggestions: list[tuple[str, str, float]] = field(default_factory=list)  # (status_raw, 建议status, confidence)
    reason: str = ""


# 反幻觉词元切分：任何非文字字符（空白/标点/装饰符号 ✕◦✓）都是分隔符。
# glm-4-flash 实测会把相邻图标字符粘进状态照抄（「已拒绝✕」「Interviewing◦」），
# 照抄忠实度不该被装饰字符惩罚——切散后任一词元（≥2 字符）能在大纲中回查到
# 即视为「确实来自页面」。拼接形态（「测评 进行中」）的分块天然分开出现。
_CHUNK_SPLIT_RE = re.compile(r"[^\w]+")


def _verbatim_in(text: str, outline: str) -> bool:
    chunks = [c for c in _CHUNK_SPLIT_RE.split(text) if len(c) >= 2]
    if any(c in outline for c in chunks):
        return True
    return len(text) <= 4 and text in outline  # 短码（如数字状态「3」）按整串回查


def _clean_str(value: str, cap: int) -> str:
    return " ".join(str(value or "").split())[:cap]


def _post_filter(output: DomParseOutput, outline: str) -> DomParseResult | None:
    from app.domain.statuses import VALID_KEYS

    if output.page_type != "applications":
        return None  # 职位列表/登录页等：即使带了 records 也一律不采信
    records: list[ExtractedRecord] = []
    suggestions: list[tuple[str, str, float]] = []
    for r in output.records[:_MAX_RECORDS]:
        title = _clean_str(r.job_title, _TITLE_MAX)
        status_raw = _clean_str(r.status_raw, _STATUS_MAX)
        if not title or not status_raw:
            continue
        if not re.search(r"\w", status_raw):
            continue  # 纯符号不是文案（glm-4-flash 实测：把图标 ✕◦ 当 status_raw）
        if r.confidence < _RECORD_CONF_MIN:
            continue
        if _NOISE_TITLE_RE.search(title):
            continue
        # 反幻觉：字段必须能在页面大纲里回查到（整块都查不到 = 编造）
        if not _verbatim_in(title, outline) or not _verbatim_in(status_raw, outline):
            continue
        applied = parse_date(_clean_str(r.applied_at, 40)) if r.applied_at else None
        records.append(
            ExtractedRecord(
                job_title=title,
                status_raw=status_raw,
                applied_at=applied,
                department=_clean_str(r.department, 80) or None,
                work_location=_clean_str(r.work_location, 80) or None,
            )
        )
        if r.status in VALID_KEYS and r.confidence >= _SUGGEST_CONF_MIN:
            suggestions.append((status_raw, r.status, r.confidence))
    if not records:
        return None
    return DomParseResult(records=records, suggestions=suggestions, reason=_clean_str(output.reason, 200))


# ── 结果缓存：同一 dom + 模型 + 提示词版本只调一次 ─────────────────────
# 进程内 LRU 即可（单进程部署；快照节流已把同域重复上报压到 10 分钟以上，
# 缓存主要防「重复上报 + 删卡自愈回放」这类确定性重复解析）。

_CACHE: OrderedDict[tuple[str, str, str], DomParseResult | None] = OrderedDict()
_CACHE_MAX = 128


def parse_dom_snapshot(db: Session, dom: str | None, page_url: str = "") -> DomParseResult | None:
    """LLM 解析裁剪 DOM。不可用/失败/无记录一律返回 None（调用方落 no_data）。"""
    if settings.llm_dom_provider != "openai_compatible":
        return None  # heuristic（默认）：离线零成本，层关闭
    if not dom or not dom.strip():
        return None
    system, version = _load_system_prompt()
    key = (hashlib.sha256(dom.encode("utf-8")).hexdigest(), settings.llm_dom_model, version)
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]

    outline = dom_outline(dom)
    if not outline:
        return None  # 页面没有任何文本块，LLM 也无米下锅（不缓存：dom 已确定无文本）
    user = f"页面 URL: {page_url}\nDOM 大纲（<标签 .类名> 元素文本，缩进=层级）:\n{outline}"
    try:
        data = client.call_json(
            db,
            task="dom_parse",
            system=system,
            user=user,
            prompt_version=version,
            base_url=settings.llm_dom_base_url,
            api_key=settings.llm_dom_api_key,
            model=settings.llm_dom_model,
            price_in=settings.llm_dom_price_in,
            price_out=settings.llm_dom_price_out,
            # 解析发生在快照上报的 HTTP 请求内（扩展只等 30s）：单次尝试、20s 封顶，
            # 失败即返回 None 走降级——绝不让 LLM 拖死上报
            timeout=20.0,
            retries=1,
        )
    except (client.LLMError, client.BudgetExceeded) as e:
        logger.warning("dom_parse 调用失败（放弃本轮，不影响规则层）: %s", e)
        return None
    try:
        output = DomParseOutput.model_validate(data)
    except ValidationError as e:
        logger.warning("dom_parse 输出未过 Schema: %s", e)
        return None
    result = _post_filter(output, outline)
    _CACHE[key] = result
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result


def deposit_suggestions(db: Session, portal, suggestions: list[tuple[str, str, float]]) -> int:
    """高置信语义建议沉淀为门户级 StatusRule：LLM 学到的映射变确定性规则，
    下次同站点同步不再花一分钱（复用 T2 的规则沉淀机制，规则表查询本就优先）。
    只在既有规则解析不出（pending_confirm）时沉淀，绝不覆盖人工/实证规则。"""
    from app.domain.normalize import normalize_status
    from app.llm.classify import _save_rule

    status_map = (portal.config or {}).get("status_map")
    count = 0
    for raw, status, conf in suggestions:
        if normalize_status(raw, status_map) != "pending_confirm":
            continue  # 规则已能解析，不需要 LLM 建议
        _save_rule(db, portal, raw, status, note=f"dom解析LLM建议 conf={conf:.2f}")
        count += 1
    return count
