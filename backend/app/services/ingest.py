"""访问时快照 ingest（REFACTOR_PLAN §2.2）：扩展上报原料（网络条目原文），
后端现场解析 → 域→Portal upsert → 解析定位落档 portal hints。

解析顺序：已知平台规格（真实采样校准过的 list_json_path/field_map）→
portal hints（同域上次成功定位，失效自动重推）→ 确定性启发式全量扫描
（复用 llm/heuristics，含 SSR 内嵌块）。多条可解析候选按 URL 投递特征
排序选优——职位列表/筛选项等「长得像列表」的载荷不得冒充投递记录
（M0 盘点 #27：GetJobAdPageList 与 GetAllDeliveryRecord 双双可解析）。

影子模式（snapshot_shadow_mode=True）只解析记录结果，不创建卡片；
转正时复用 sync.ingest_applications（与旧轮询同一份 diff/建卡/历史代码）。
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import RawApplication
from app.adapters.fields import dig_list, parse_date
from app.db.models import Portal, Snapshot
from app.llm import heuristics
from app.llm.extract import ExtractedRecord, records_from_items
from app.llm.schemas import FieldMapping

# 内地常见二级后缀：eTLD+1 需要 +2 级（与 pipeline 同实现；M3 删 pipeline 后本模块自持）
_SECOND_LEVEL_TLDS = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "co.uk", "com.hk", "com.tw", "com.sg", "com.au",
}


def registrable_domain(host: str) -> str:
    host = (host or "").lower().strip(".")
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _SECOND_LEVEL_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


# ── 已知平台解析规格（数值经真实登录态采样校准，来源见各条注释）──────
# 与 llm/fingerprint.PlatformTemplate 的差别：这里只保留「如何解析响应」，
# 不携带任何重放契约（CSRF/请求头/Cookie）——新架构不重放。
# M3 删除 fingerprint.py 后本表自持。

@dataclass(frozen=True)
class PlatformParseSpec:
    key: str
    url_signals: tuple[tuple[re.Pattern, int], ...]  # (URL 正则, 命中分)
    key_signals: tuple[str, ...]  # 响应键名（重合 1 个 +1 分，封顶 3）
    threshold: int
    list_json_path: str
    field_map: tuple[tuple[str, str], ...]  # (字段, 相对列表项点路径)
    # 已实证的状态码映射（写入新建门户 config.status_map 供归一化使用；
    # 未验证的码不映射，落「待确认」由运行期沉淀——宁缺毋错）
    status_map: tuple[tuple[str, str], ...] = ()


PLATFORM_SPECS: list[PlatformParseSpec] = [
    PlatformParseSpec(
        # 飞书招聘（ATSX）：2026-09-01 hf7l9aiqzx.jobs.feishu.cn / xiaomi.jobs.f.mioffice.cn 实测。
        # status_raw 取 operation_list 末项（时间线当前状态）；数字码由状态归一化/规则表处理。
        key="feishu",
        url_signals=(
            (re.compile(r"([a-z0-9-]+\.)*(feishu\.cn|feishu\.net|larksuite\.com|larkoffice\.com)", re.I), 4),
            (re.compile(r"/search/user/applications", re.I), 3),
        ),
        key_signals=("delivery_list", "job_post_info", "operation_list", "biz_create_time", "application_list"),
        threshold=4,
        list_json_path="data.delivery_list",
        field_map=(
            ("id", "id"),
            ("job_title", "job_post_info.title"),
            ("status_raw", "operation_list.-1.operation_code"),
            ("applied_at", "biz_create_time"),
            ("work_location", "job_post_info.city_info.name"),
        ),
        # operation_code 码表（与页面时间线逐条对齐验证）；其余码（2/4/5…）不映射
        # ^0$（已投递）映射 screening：applied 状态已于 2026-09-02 并入
        status_map=(("^0$", "screening"), ("^1$", "screening"), ("^3$", "written_test")),
    ),
    PlatformParseSpec(
        # 北森招聘：2026-09-01 hkaco.zhiye.com 实测。分组列表：* 段展开（多 tab 拼接）。
        key="beisen",
        url_signals=(
            (re.compile(r"([a-z0-9-]+\.)*(zhiye\.com|italent\.cn|yingjiesheng\.com)", re.I), 4),
            (re.compile(r"/api/Submission/GetAllDeliveryRecord", re.I), 3),
        ),
        key_signals=("Submissions", "JobAdTitle", "DeliveryStatus", "DeliveryDate", "ApplyId", "applyRecord"),
        threshold=4,
        list_json_path="Data.*.Submissions.*.Datas",
        field_map=(
            ("id", "ApplyId"),
            ("job_title", "JobAdTitle"),
            ("status_raw", "DeliveryStatus"),
            ("applied_at", "DeliveryDate"),
        ),
    ),
    PlatformParseSpec(
        # 携程招聘：2026-09-02 careers.ctrip.com 实测（快照 #17/#18）。
        # 状态拆两字段：phaseInfoCN=阶段（测评/笔试/面试…）+ statusInfoCN=进度
        # （进行中/未通过…）。启发式按字段名只能选中 statusInfoCN，单取「进行中」
        # 无语义落待确认——拼接后走通用规则：「测评 进行中」→ assessment，
        # 「测评 未通过」→ rejected（未通过规则优先级更高）。
        key="ctrip",
        url_signals=(
            (re.compile(r"([a-z0-9-]+\.)*(ctrip\.com|trip\.com)", re.I), 4),
            (re.compile(r"/api/hrrecruit/getApplyJobRecord", re.I), 4),
        ),
        key_signals=("applyJobAdList", "phaseInfoCN", "statusInfoCN", "mokaApplicationId"),
        threshold=4,
        list_json_path="applyJobAdList",
        field_map=(
            ("id", "mokaApplicationId"),
            ("job_title", "jobTitle"),
            ("status_raw", "phaseInfoCN+statusInfoCN"),
            ("applied_at", "applyTime"),
            ("work_location", "cityName"),
        ),
    ),
    PlatformParseSpec(
        # Moka：URL+键名信号识别（键名来自种子配置与模板信号；list 路径按 Moka 通行形态，
        # 真实契约待星环站重采校准——heuristic 兜底可覆盖多数形状，本规格仅提高命中把握）。
        key="moka",
        url_signals=(
            (re.compile(r"([a-z0-9-]+\.)*mokahr\.com", re.I), 4),
            (re.compile(r"/api/outer/", re.I), 4),
        ),
        key_signals=("positionName", "applyPositionName", "statusText", "deliverTime", "applyId"),
        threshold=4,
        list_json_path="data.list",
        field_map=(
            ("id", "applyId"),
            ("job_title", "positionName"),
            ("status_raw", "statusText"),
            ("applied_at", "deliverTime"),
        ),
    ),
]

# ── 候选排序信号：投递列表 vs 职位/筛选项列表的 URL 特征 ──────────────
# 强正：投递动作词；强负：职位列表/站点配置词。数据信号（applied_at、文字状态）辅助。
_URL_STRONG_RE = re.compile(
    r"deliver|application|submission|progress|candidate|personal[-_/]?center|/mine|apply", re.I
)
_URL_NEGATIVE_RE = re.compile(
    r"jobad|jobs?/(v\d+/)?(list|search)|departments?|callingcode|privacy|/filters?|recommend|banner|qrcode|group-by-job|website/jobs",
    re.I,
)
_NUMERIC_STATUS_RE = re.compile(r"^[\d.\s-]+$")

# 职位广告/部门分组列表的键特征（星环快照 #3 实盘：站点启动配置里的 jobs 数组
# 键恰含 title+status，曾冒充投递建了 15 张错卡，status 全为 open → 全落待确认）。
# 投递记录不带「发布/开关职位」类键；#decrypted 载荷没有 URL 负特征可用，只能靠键判。
# 虹科快照 #22 追加北森 GetJobAdPageList 家族：招聘人数/发布与截止时间/收藏与
# 已投标记/投递上限/JD 三件套——投递记录携带申请状态与申请时间，不会带发布侧属性。
_JOB_AD_KEY_RE = re.compile(
    r"^(published_?at|opened_?at|closed_?at|point_?to|recommendation_?bonus"
    r"|job_?count|department_?type|hire_?mode|mj_?code"
    r"|head_?count|post_?date(int)?|end_?time(int)?|favorites_?status"
    r"|is_?collect(ed)?|is_?delivered|submission_?limit|salary|duty|require|welfare)$"
)


def _looks_like_job_ads(items: list) -> bool:
    if not items or not isinstance(items[0], dict):
        return False
    keys = {str(k).lower() for k in items[0].keys()}
    return sum(1 for k in keys if _JOB_AD_KEY_RE.match(k)) >= 2


@dataclass
class ParsedPayload:
    entry_url: str
    list_json_path: str
    field_map: dict[str, str]
    records: list  # ExtractedRecord（job_title/status_raw 均非空）
    route: str  # platform | heuristics | embedded
    score: float
    status_map: list[dict] | None = None  # 平台规格自带的状态码映射（建新门户时落 config）


def _valid_records(records) -> list:
    """ingest 校验仅为：title/status 非空（数据是用户页面上真实存在的展示内容）。"""
    return [r for r in records if r.job_title and r.status_raw]


def _candidate_score(url: str, field_map: dict[str, str], records: list, *, platform: bool) -> float:
    score = 3.0 if platform else 0.0
    # #decrypted/#embedded 伪条目的 URL 是页面地址，只说明「人在哪」不说明「载荷是什么」
    # （虹科实盘：deliveryRecord 页面上 GetJobAdPageList 的解密孪生借页面 URL 的
    # deliver 强正 +4 压过其真身自身的 jobad 负分，20 个职位冒充投递建卡）——
    # 伪条目只按内容信号打分，强/负特征都不继承
    if "#decrypted" not in url and "#embedded" not in url:
        if _URL_STRONG_RE.search(url):
            score += 4.0
        if _URL_NEGATIVE_RE.search(url):
            score -= 4.0
    if "applied_at" in field_map:
        score += 1.5
    if any(not _NUMERIC_STATUS_RE.match(r.status_raw or "") for r in records):
        score += 1.5  # 文字状态（如「简历初筛」）是投递记录的强信号，职位列表多为数字码
    score += min(len(records), 20) * 0.05
    return score


def _iter_json_entries(network: list[dict]):
    """产出 (entry, parsed_json)；跳过非 JSON/截断/解析失败的条目。"""
    for entry in network or []:
        url = str(entry.get("url") or "")
        body = str(entry.get("response_body") or "")
        if not url.startswith("http") or body.lstrip()[:1] not in ("{", "["):
            continue
        if entry.get("truncated"):
            continue  # 截断的 JSON 解析必败
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            continue
        yield entry, data


def _platform_candidates(network: list[dict]) -> list[ParsedPayload]:
    out: list[ParsedPayload] = []
    for entry, data in _iter_json_entries(network):
        url = str(entry.get("url") or "")
        if "#embedded" in url:
            continue
        for spec in PLATFORM_SPECS:
            score = 0
            for regex, points in spec.url_signals:
                if regex.search(url):
                    score += points
            score += min(sum(1 for k in spec.key_signals if f'"{k}"' in str(entry.get("response_body") or "")[:20_000]), 3)
            if score < spec.threshold:
                continue
            items = dig_list(data, spec.list_json_path)
            if not items:
                continue
            fmap = dict(spec.field_map)
            records = _valid_records(records_from_items({k: FieldMapping(json_path=v) for k, v in fmap.items()}, items))
            if not records:
                continue
            out.append(
                ParsedPayload(
                    entry_url=url,
                    list_json_path=spec.list_json_path,
                    field_map=fmap,
                    records=records,
                    route="platform",
                    score=_candidate_score(url, fmap, records, platform=True),
                    status_map=[{"pattern": p, "status": s} for p, s in spec.status_map] or None,
                )
            )
    return out


def _heuristics_candidates(network: list[dict]) -> list[ParsedPayload]:
    out: list[ParsedPayload] = []
    for entry, data in _iter_json_entries(network):
        url = str(entry.get("url") or "")
        path = heuristics.derive_list_json_path(data)
        if path is None:
            continue
        items = heuristics.locate_list(data)
        if not items:
            continue
        if _looks_like_job_ads(items):
            continue  # 职位广告/分组聚合冒充投递（星环 jobs 陷阱），宁缺毋错
        fmap = heuristics.guess_field_map(items[0])
        if not fmap:
            continue
        records = _valid_records(records_from_items({k: FieldMapping(json_path=v) for k, v in fmap.items()}, items))
        if not records:
            continue
        out.append(
            ParsedPayload(
                entry_url=url,
                list_json_path=path,
                field_map=fmap,
                records=records,
                route="embedded" if "#embedded" in url else "heuristics",
                score=_candidate_score(url, fmap, records, platform=False),
            )
        )
    return out


def parse_snapshot_network(network: list[dict] | None) -> ParsedPayload | None:
    """全部候选统一打分取最优；无候选返回 None。"""
    candidates = _platform_candidates(network or []) + _heuristics_candidates(network or [])
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.score)


# ── DOM 兜底提取（v0.5.5）────────────────────────────
# 网络三层钩子（fetch/XHR 文本、JSON.parse、Response.json）都拿不到明文时启用
# （星环实盘：解密在 Web Worker 里，postMessage 回主线程的是结构化克隆对象；
# 网易实盘：传输方式不明，fetch/XHR 包装零捕获）。渲染出来的记录一定在 DOM 里：
# 裁剪 HTML 中找「同签名重复兄弟行」组（同标签+同 class 的直接子元素），
# 行内单元格按 状态词典/日期/最长文本 推断字段——与站点加密方式和传输无关。

_DATE_CELL_RE = re.compile(r"\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}|\d{1,2}-[A-Za-z]{3}|今天|昨天")
_DOM_CELL_MAX = 80
# 状态单元格防污染（炎魂实盘：导航菜单「我的简历」命中状态词典「简历」关键词，
# 整片导航区被当成了带状态的记录组）：导航/操作词一票否决 + 状态文案长度上限。
# 注意「撤回」不可入表——bilibili 实盘合法状态「已撤回」曾被误杀致整行丢失；
# 裸「撤回」「撤回投递」按钮本身不命中任何归一化规则，天然安全。
_NAVISH_CELL_RE = re.compile(
    r"我的|个人|中心|记录|返回|编辑|修改|隐私|政策|协议|登录|注册|退出|主页|首页|关于|联系|更多|展开|收起"
)
# 岗位名排除备案/版权噪声（炎魂实盘：页脚「京公网安备…号」成了 job_title）；
# 「人才库/。」排除整句提示横幅（bilibili 实盘：「你的简历已被录入公司人才库啦，
# …与你联系。」成了 job_title）——岗位名不含句号。
_NOISE_TITLE_RE = re.compile(r"公网安备|ICP备|备案|Copyright|All Rights|©|人才库|。", re.I)


def _row_cells(row, limit: int = 24) -> list[str]:
    """叶子元素的规范化文本（≤80 字）作为行单元格——长段落/JD 全文自然被排除。"""
    cells: list[str] = []
    for el in row.iter():
        if not isinstance(el.tag, str) or len(el):
            continue
        text = " ".join(" ".join(el.itertext()).split())
        if text and len(text) <= _DOM_CELL_MAX:
            cells.append(text)
        if len(cells) >= limit:
            break
    return cells


def _cell_date(cell: str):
    """单元格取日期：先整串解析，再子串提取（「投递时间：2026-08-27」形态）。"""
    d = parse_date(cell)
    if d:
        return d
    m = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", cell)
    if m:
        return parse_date(f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    return None


_APPLY_DATE_HINT_RE = re.compile(r"投递|申请|deliver|apply", re.I)


def _record_from_cells(cells: list[str]) -> ExtractedRecord | None:
    from app.domain.normalize import normalize_status

    status = next(
        (
            c
            for c in cells
            if len(c) <= 10 and not _NAVISH_CELL_RE.search(c) and normalize_status(c) != "pending_confirm"
        ),
        None,
    )
    if status is None:
        return None  # 没有可识别的状态文案，多半不是投递记录（导航/表头/职位卡）
    dates = {c for c in cells if _DATE_CELL_RE.search(c)}
    others = [
        c for c in cells if c != status and c not in dates and len(c) >= 2 and not _NOISE_TITLE_RE.search(c)
    ]
    if not others:
        return None
    # 行内多日期并存时优先带投递/申请字样的（bilibili 实盘：「2026-08-11 投递」
    # 与「2026-08-07 发布」同卡，按字典序取早曾误取发布日期），再退回最早日期
    apply_dates = sorted(c for c in dates if _APPLY_DATE_HINT_RE.search(c)) or sorted(dates)
    applied = _cell_date(apply_dates[0]) if apply_dates else None
    return ExtractedRecord(
        job_title=max(others, key=len),  # 非状态非日期的最长文本通常是岗位名
        status_raw=status,
        applied_at=applied,
    )


def _dom_record_groups(html_text: str) -> list[tuple[list[ExtractedRecord], int]]:
    import lxml.html

    try:
        root = lxml.html.fromstring(html_text)
    except Exception:
        return []
    depths: dict[int, int] = {}

    def _walk(el, d):
        depths[id(el)] = d
        for c in el:
            if isinstance(c.tag, str):
                _walk(c, d + 1)

    _walk(root, 0)
    groups: list[tuple[list[ExtractedRecord], int]] = []
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        kids = [c for c in parent if isinstance(c.tag, str)]
        if not (1 <= len(kids) <= 300):
            continue
        by_sig: dict[tuple, list] = {}
        for c in kids:
            by_sig.setdefault((c.tag, (c.get("class") or "")[:60]), []).append(c)
        # 同一父容器下不同签名的行组合并为一组：异构卡片列表（bilibili 实盘：
        # 一张展开带状态详情、一张收起带「重新投递」，class 不同）按签名分组后
        # 只取一组会把兄弟投递整行丢掉；组内每行仍须独立通过状态+标题校验
        parent_recs: list[ExtractedRecord] = []
        for _sig, rows in by_sig.items():
            if not (1 <= len(rows) <= 200):
                continue
            # 单行组提高门槛（≥4 单元格）：防页脚/公告里的孤立状态词误报
            min_cells = 4 if len(rows) == 1 else 2
            recs: list[ExtractedRecord] = []
            for row in rows:
                cells = _row_cells(row)
                if len(cells) < min_cells:
                    recs = []
                    break
                rec = _record_from_cells(cells)
                if rec is None:
                    recs = []
                    break
                recs.append(rec)
            if recs:
                parent_recs.extend(recs)
        if parent_recs:
            groups.append((parent_recs, depths.get(id(parent), 0)))
    return groups


def dom_records(html_text: str) -> list[ExtractedRecord]:
    """所有合法行组取最优：行数多 > 嵌套深（更具体的列表容器，防整页 body
    这种粗粒度容器在平手时压过真实卡片/表格）> 标题总长。"""
    groups = _dom_record_groups(html_text or "")
    if not groups:
        return []
    return max(
        groups,
        key=lambda g: (len(g[0]), g[1], sum(len(r.job_title or "") for r in g[0])),
    )[0]


# ── Portal 定位 / upsert / hints ────────────────────────────────

# 多租户 ATS：所有客户共用一个 host（Moka 全在 app.mokahr.com），门户必须按
# URL 路径里的租户段区分（/campus_apply/{org}/{site} → org），否则星环/炎魂
# 这类同用 Moka 的公司会并成一个门户（炎魂实盘：企业名落成 mokahr.com）。
_MULTI_TENANT_HOST_RE = re.compile(r"(^|\.)mokahr\.com$", re.I)


def site_key(url: str) -> str:
    p = urlparse(url or "")
    host = (p.netloc or "").lower()
    if not host:
        return ""
    if _MULTI_TENANT_HOST_RE.search(host):
        segs = [s for s in (p.path or "").split("/") if s]
        tenant = ""
        if len(segs) > 1 and segs[0] in ("campus_apply", "apply", "campus"):
            tenant = segs[1]
        elif segs:
            tenant = segs[0]
        if tenant and not tenant.isdigit():
            return f"{host}/{tenant}"
    return host


_BRAND_SUFFIX_RE = re.compile(r"(校园招聘|社会招聘|校招|招聘|校园|官网|官方网站|求职)$")


def brand_from_dom(dom: str | None) -> str | None:
    """从裁剪 DOM 的 <title> 提取品牌名（「炎魂网络 - 校园招聘」→ 炎魂网络）。

    扩展裁剪只保留 id/class/href/type/title 属性，meta 多半已被剥掉，title 是
    最稳定信号；已剔除备案/版权等噪声标题。
    """
    if not dom:
        return None
    import lxml.html

    try:
        root = lxml.html.fromstring(dom)
    except Exception:
        return None
    title = " ".join(" ".join(root.xpath("//title/text()")).split())
    if not title or _NOISE_TITLE_RE.search(title):
        return None
    brand = title
    for sep in (" - ", " – ", " — ", "_", "|", "·", ":", "："):
        if sep in title:
            brand = title.split(sep)[0]
            break
    brand = _BRAND_SUFFIX_RE.sub("", brand.strip()).strip(" -–—_|·")
    if not (2 <= len(brand) <= 20) or _NOISE_TITLE_RE.search(brand):
        return None
    return brand


def find_portal_by_site(db: Session, url: str) -> Portal | None:
    """门户定位：先精确匹配 site_key（多租户隔离），再按 host 兜底（旧门户兼容）；
    host 命中但租户键不同的（Moka 另一租户）不算同一门户。"""
    key = site_key(url)
    host = urlparse(url or "").netloc.lower()
    if not host:
        return None
    for portal in db.scalars(select(Portal).order_by(Portal.enabled.desc(), Portal.id)):
        cfg_key = (portal.config or {}).get("site_key")
        if key and cfg_key == key:
            return portal
        for d in portal.domains or []:
            if d and d.lower() in host:
                if cfg_key and key and cfg_key != key:
                    break  # 同 host 不同租户（Moka 多租户），继续找
                return portal
    return None


def find_portal_by_host(db: Session, host: str) -> Portal | None:
    if not host:
        return None
    portals = list(db.scalars(select(Portal).order_by(Portal.enabled.desc(), Portal.id)))
    for portal in portals:
        for d in portal.domains or []:
            if d and d.lower() in host:
                return portal
    return None


def extract_brand_name(network: list[dict] | None) -> str | None:
    """从内嵌 JSON 提取租户/品牌名作门户显示名（飞书 js-websiteInfo /
    北森 BGlobal，与 pipeline._extract_tenant_name 同逻辑；M3 后本模块自持）。"""
    for entry in network or []:
        url = str(entry.get("url") or "")
        body = str(entry.get("response_body") or "")
        if "#embedded" not in url or not body.lstrip().startswith("{"):
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        tenant = (data.get("tenant_info") or {}).get("tenant_name")
        if isinstance(tenant, str) and tenant.strip():
            return tenant.strip()
        info = data.get("tenantInfo")
        if isinstance(info, dict):
            for key in ("Abbreviation", "Alias"):
                value = info.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def hints_from_portal(portal: Portal) -> dict | None:
    hints = (portal.config or {}).get("hints")
    return hints if isinstance(hints, dict) and hints.get("url") and hints.get("list_json_path") else None


def _apply_hints(hints: dict, network: list[dict]) -> ParsedPayload | None:
    """按上次成功定位重放解析；条目缺失/提取失败自动作废（返回 None 走全量扫描）。"""
    for entry, data in _iter_json_entries(network or []):
        if str(entry.get("url") or "") != hints.get("url"):
            continue
        items = dig_list(data, str(hints["list_json_path"]))
        if not items:
            return None
        fmap = {str(k): str(v) for k, v in (hints.get("field_map") or {}).items()}
        if not fmap.get("job_title") or not fmap.get("status_raw"):
            return None
        records = _valid_records(records_from_items({k: FieldMapping(json_path=v) for k, v in fmap.items()}, items))
        if not records:
            return None
        return ParsedPayload(
            entry_url=str(hints["url"]),
            list_json_path=str(hints["list_json_path"]),
            field_map=fmap,
            records=records,
            route="hints",
            score=0.0,
        )
    return None


def upsert_portal_from_snapshot(
    db: Session,
    url: str,
    payload: ParsedPayload,
    network: list[dict] | None,
    dom: str | None = None,
) -> Portal:
    """URL→Portal（多租户感知）：已有门户只刷新 hints；新门户以品牌名/域名命名、
    enabled=False（影子期不进旧绑定向导）。品牌优先级：网络内嵌租户名 > DOM
    title（炎魂实盘）> host。"""
    host = urlparse(url or "").netloc.lower()
    portal = find_portal_by_site(db, url)
    hints = {
        "url": payload.entry_url,
        "list_json_path": payload.list_json_path,
        "field_map": payload.field_map,
    }
    if portal is None:
        domain = registrable_domain(host)
        brand = extract_brand_name(network) or brand_from_dom(dom)
        key = site_key(url)
        config = {"hints": hints}
        if key and key != host:
            config["site_key"] = key  # 多租户 ATS 的租户隔离键（如 app.mokahr.com/yanhun）
        if payload.status_map:
            # 平台已实证的状态码映射（如飞书 0/1/3），否则数字码全落「待确认」
            config["status_map"] = payload.status_map
        portal = Portal(
            name=brand or host,
            company=brand or domain,
            provider_key="snapshot",
            domains=[host],
            enabled=False,
            verified=False,
            note="扩展访问时快照自动建档",
            config=config,
        )
        db.add(portal)
    else:
        config = dict(portal.config or {})
        config["hints"] = hints
        portal.config = config
    db.flush()
    return portal


def payload_hash(network: list[dict] | None, dom: str | None = None) -> str:
    """上报原料归一化哈希（同域同数据重访不上报重复解析）。

    dom 必须参与哈希（网易实盘教训）：网络条目不变而渲染 DOM 变了（首次带 dom
    上报、或状态更新后重渲染）都应视为新快照，否则 duplicate 短路会把新 dom
    整个丢掉、回放的旧快照又没有 dom。
    """
    canon = [
        {"url": str(e.get("url") or ""), "method": str(e.get("method") or ""), "response_body": str(e.get("response_body") or "")}
        for e in sorted(network or [], key=lambda e: str(e.get("url") or ""))
    ]
    if dom:
        canon.append({"url": "#dom", "method": "GET", "response_body": dom})
    return hashlib.sha256(json.dumps(canon, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _to_raw_applications(payload: ParsedPayload) -> list[RawApplication]:
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
        for r in payload.records
    ]


def ingest_snapshot(db: Session, snapshot: Snapshot, *, skip_hints: bool = False) -> dict:
    """解析快照并落档结果。影子模式下不创建卡片；关闭后走 ingest_applications
    （与旧轮询同一份 diff/建卡/历史代码，返回摘要写进 parse_note）。"""
    from app.core.config import settings

    host = urlparse(snapshot.url or "").netloc.lower()
    payload = None
    route = None

    portal = find_portal_by_site(db, snapshot.url)
    # 解析优先级：平台规格（真实采样校准）> hints（同域上次成功定位）> 全量扫描。
    # 校准必须压过缓存：hints 只是「上次成功」的记忆，可能被旧版引擎钉上语义
    # 残缺的映射（携程实盘：拼接语法上线前的 statusInfoCN 单字段，「进行中」
    # 落回待确认），只有校准规格能在下次访问时把门户纠正回来。
    candidates = _platform_candidates(snapshot.network or [])
    if candidates:
        payload = max(candidates, key=lambda c: c.score)
    if payload is None and portal is not None and not skip_hints:
        hints = hints_from_portal(portal)
        if hints is not None:
            payload = _apply_hints(hints, snapshot.network or [])
    if payload is None:
        payload = parse_snapshot_network(snapshot.network)
    if payload is None and snapshot.dom:
        recs = dom_records(snapshot.dom)
        if recs:
            payload = ParsedPayload(
                entry_url=snapshot.url + "#dom",
                list_json_path="dom",
                field_map={},
                records=recs,
                route="dom",
                score=0.0,
            )
    if payload is None:
        snapshot.parse_status = "no_data"
        snapshot.parse_route = None
        snapshot.parse_note = _no_data_note(snapshot.network)
        snapshot.parsed_count = 0
        db.commit()
        return {"status": "no_data", "parsed_count": 0, "route": None, "note": snapshot.parse_note}

    route = payload.route
    portal = upsert_portal_from_snapshot(db, snapshot.url, payload, snapshot.network, dom=snapshot.dom)
    snapshot.portal_id = portal.id
    snapshot.parse_status = "parsed"
    snapshot.parse_route = route
    snapshot.list_json_path = payload.list_json_path
    snapshot.field_map = payload.field_map
    snapshot.parsed_count = len(payload.records)

    note = f"{route} 命中 {payload.entry_url}（{payload.list_json_path}），提取 {len(payload.records)} 条"
    summary: dict = {}
    if not settings.snapshot_shadow_mode:
        from app.services.sync import ingest_applications

        summary = ingest_applications(
            db, user=snapshot.user, portal=portal, raw_list=_to_raw_applications(payload)
        )
        note += f"；落卡 created={summary.get('created', 0)} updated={summary.get('updated', 0)}"
    if snapshot.login_suspect:
        note += "；扩展上报疑似未登录"
    snapshot.parse_note = note
    db.commit()
    return {
        "status": "parsed",
        "parsed_count": len(payload.records),
        "route": route,
        "portal_id": portal.id,
        "portal_name": portal.name,
        "list_json_path": payload.list_json_path,
        "field_map": payload.field_map,
        "ingest": summary or None,
        "note": note,
        "preview": [
            {"job_title": r.job_title, "status_raw": r.status_raw} for r in payload.records[:3]
        ],
    }


def _no_data_note(network: list[dict] | None) -> str:
    entries = [e for e in network or [] if str(e.get("response_body") or "").lstrip()[:1] in ("{", "[")]
    if not entries:
        return "快照内无可解析的 JSON 载荷（缓冲为空或均为静态资源）"
    truncated = sum(1 for e in entries if e.get("truncated"))
    note = f"{len(entries)} 条 JSON 载荷均未能定位出投递列表（title+status 双全的记录）"
    if truncated:
        note += f"，其中 {truncated} 条响应体截断"
    return note


def list_connected_sites(db: Session, user_id: int) -> list[dict]:
    """用户的「已连接站点」：有快照记录的门户（最新快照作回访入口）。

    扩展每小时自动同步（M2 隐藏 tab 回访）与前端 Settings 展示共用。
    """
    from app.db.models import Snapshot

    snapshots = list(
        db.scalars(
            select(Snapshot)
            .where(Snapshot.user_id == user_id, Snapshot.portal_id.is_not(None))
            .order_by(Snapshot.id.desc())
            .limit(200)
        )
    )
    latest_by_portal: dict[int, Snapshot] = {}
    for s in snapshots:
        latest_by_portal.setdefault(s.portal_id, s)

    out: list[dict] = []
    for portal_id, snap in sorted(latest_by_portal.items()):
        portal = db.get(Portal, portal_id)
        if portal is None:
            continue
        out.append(
            {
                "portal_id": portal_id,
                "name": portal.name,
                "domain": snap.domain,
                "url": snap.url,
                "parsed_count": snap.parsed_count,
                "parse_status": snap.parse_status,
                "login_suspect": bool(snap.login_suspect),
                "last_at": snap.created_at.isoformat() if snap.created_at else None,
            }
        )
    return out
