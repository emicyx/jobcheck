"""T3 DOM 兜底 LLM 解析回归（dom_parse v1）。

覆盖：大纲压缩（只留文本块/预算截断）、provider 关闭零成本、假 LLM 的
全链路（提示词装配 → Schema 校验 → 后过滤 → 提取记录）、反幻觉词元回查、
职位列表页不采信、上游异常/预算熔断降级 None、结果缓存、语义建议沉淀
StatusRule、API 级全链路（route=llm_dom 建档落卡）。
"""

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Application, Portal, StatusRule
from app.llm import client, dom_parse
from app.llm.dom_parse import (
    DomParseOutput,
    DomParseResult,
    dom_outline,
    parse_dom_snapshot,
)

# 规则版 dom_records 认不出、LLM 应能认出的典型非模板版式：
# 状态藏在图标 title 属性 + 英文状态文案（itertext 抽不到属性值，
# normalize 词典也命中不了英文）——这正是 T3 层存在的意义
_NON_TEMPLATE_DOM = (
    "<html><head><title>某某科技招聘</title></head><body>"
    "<nav><a class=\"nav-item\">首页</a><a class=\"nav-item\">职位</a>"
    "<a class=\"nav-item\">关于我们</a></nav>"
    "<div class=\"my-applications\">"
    "<div class=\"app-card\">"
    "<a class=\"job-link\" href=\"/job/1001\">AI平台开发工程师（2027届）</a>"
    "<span class=\"dept\">基础架构部</span>"
    "<span class=\"time\">投递时间：2026-08-25</span>"
    "<span class=\"status-ico\" title=\"已拒绝\">✕</span>"
    "</div>"
    "<div class=\"app-card\">"
    "<a class=\"job-link\" href=\"/job/1002\">大数据开发工程师</a>"
    "<span class=\"dept\">数据平台部</span>"
    "<span class=\"time\">投递时间：2026-08-28</span>"
    "<span class=\"status-ico\" title=\"Written Test\">✎</span>"
    "</div>"
    "</div>"
    "<div class=\"footer\">©2026 某某科技 京ICP备12345号</div>"
    "</body></html>"
)

_LLM_OUTPUT = {
    "page_type": "applications",
    "records": [
        {
            "job_title": "AI平台开发工程师（2027届）",
            "status_raw": "已拒绝",
            "applied_at": "2026-08-25",
            "department": "基础架构部",
            "work_location": "",
            "status": "rejected",
            "confidence": 0.95,
        },
        {
            "job_title": "大数据开发工程师",
            "status_raw": "Written Test",
            "applied_at": "2026-08-28",
            "department": "数据平台部",
            "work_location": "",
            "status": "written_test",
            "confidence": 0.92,
        },
    ],
    "reason": "两卡片各含岗位/部门/时间/title 状态",
}


@pytest.fixture(autouse=True)
def _reset_cache():
    dom_parse._CACHE.clear()
    yield
    dom_parse._CACHE.clear()


@pytest.fixture()
def llm_on(monkeypatch):
    """开启 openai_compatible 提供者（调用本身再被替换为假实现，不触网）。"""
    monkeypatch.setattr(settings, "llm_dom_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_dom_api_key", "test-key")


def _fake_llm(output, calls: list | None = None):
    """假 call_json：output 为单个返回值 / 异常，或按调用顺序消费的列表。"""

    def _call(db, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        item = output[0] if isinstance(output, list) else output
        if isinstance(output, list):
            output.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return _call


# ── DOM 大纲压缩 ────────────────────────────────────────────


def test_dom_outline_keeps_text_blocks_and_title_attr():
    """只保留有直接文本/title 属性的元素；class/id/title 进行标注；script 剔除；
    图标元素（纯符号文本）以 title 属性作语义文本（符号歧义在表示层消解）。"""
    html = (
        "<html><head><title>某某科技招聘</title></head><body>"
        "<script>var x = '简历评估中';</script>"
        "<div class=\"card css-module-x-y\" id=\"c1\">"
        "<a class=\"title\" href=\"/j/1\">AI平台开发工程师</a>"
        "<span class=\"ico\" title=\"已拒绝\">✕</span>"
        "<span class=\"hint\" title=\"进度说明\">测评 进行中</span>"
        "  投递时间：2026-08-25 "
        "</div></body></html>"
    )
    outline = dom_outline(html)
    assert outline is not None
    assert "<title>" in outline and "某某科技招聘" in outline
    assert "简历评估中" not in outline  # script 内文本不进大纲
    assert "<a.title>" in outline and "AI平台开发工程师" in outline
    assert "<span.ico> 已拒绝" in outline  # 图标符号 ✕ 被替换为 title 语义
    assert "✕" not in outline
    assert "[title=进度说明]" in outline and "测评 进行中" in outline  # 正常文本+title 才标注属性
    assert "投递时间：2026-08-25" in outline  # 空白折叠
    assert ".css-module-x-y" in outline and "#c1" in outline


def test_dom_outline_budget_truncation():
    html = "<html><body>" + "".join(f"<div>岗位工程师条目{i}</div>" for i in range(100)) + "</body></html>"
    outline = dom_outline(html, max_chars=300)
    assert outline is not None
    assert len(outline) < 600
    assert "已截断" in outline
    assert "条目0" in outline  # 从头保留


def test_dom_outline_noise_shapes():
    assert dom_outline("") is None
    assert dom_outline("<html><broken") is None
    assert dom_outline("<html><body><div class='a'></div></body></html>") is None  # 无文本块


# ── 解析入口：provider / 异常 / 缓存 ──────────────────────────


def test_parse_dom_disabled_by_default(db, monkeypatch):
    """heuristic（默认）：层关闭，零调用零成本（monkeypatch 隔离 .env 的真实配置）。"""
    calls: list = []
    monkeypatch.setattr(settings, "llm_dom_provider", "heuristic")
    monkeypatch.setattr(client, "call_json", _fake_llm(_LLM_OUTPUT, calls))
    assert parse_dom_snapshot(db, _NON_TEMPLATE_DOM, "https://x.com/apply") is None
    assert calls == []


def test_parse_dom_happy_path(db, llm_on, monkeypatch):
    """全链路：提示词注入状态枚举与页面 URL，输出过校验，记录含日期/部门。"""
    calls: list = []
    monkeypatch.setattr(client, "call_json", _fake_llm(_LLM_OUTPUT, calls))
    result = parse_dom_snapshot(db, _NON_TEMPLATE_DOM, "https://x.com/apply")
    assert isinstance(result, DomParseResult)
    assert len(result.records) == 2
    r = result.records[0]
    assert r.job_title == "AI平台开发工程师（2027届）"
    assert r.status_raw == "已拒绝"
    assert str(r.applied_at) == "2026-08-25"
    assert r.department == "基础架构部"
    (kwargs,) = calls
    assert kwargs["task"] == "dom_parse"
    assert kwargs["prompt_version"] == "2"
    assert kwargs["timeout"] == 20.0 and kwargs["retries"] == 1  # 请求路径内：20s 单次，扩展只等 30s
    assert "- rejected = 已拒绝" in kwargs["system"]  # 状态枚举动态注入
    assert "https://x.com/apply" in kwargs["user"]
    assert "<span.status-ico> 已拒绝" in kwargs["user"]  # 图标元素以 title 语义进大纲
    # 高置信建议进入沉淀队列：已拒绝走通用规则可解析（不沉淀），英文需沉淀
    assert ("Written Test", "written_test", 0.92) in result.suggestions
    assert ("已拒绝", "rejected", 0.95) in result.suggestions  # 沉淀与否由 deposit 阶段裁决


def test_parse_dom_cached_per_dom(db, llm_on, monkeypatch):
    """同一 DOM 重复解析（自愈回放场景）只调一次 LLM。"""
    calls: list = []
    monkeypatch.setattr(client, "call_json", _fake_llm(_LLM_OUTPUT, calls))
    first = parse_dom_snapshot(db, _NON_TEMPLATE_DOM)
    second = parse_dom_snapshot(db, _NON_TEMPLATE_DOM)
    assert first is not None and second is not None
    assert len(calls) == 1
    assert {r.job_title for r in second.records} == {r.job_title for r in first.records}


def test_parse_dom_upstream_failures_return_none(db, llm_on, monkeypatch):
    for err in (client.LLMError("boom"), client.BudgetExceeded("超预算")):
        monkeypatch.setattr(client, "call_json", _fake_llm(err))
        assert parse_dom_snapshot(db, _NON_TEMPLATE_DOM) is None


def test_parse_dom_invalid_schema_returns_none(db, llm_on, monkeypatch):
    monkeypatch.setattr(client, "call_json", _fake_llm({"foo": "bar"}))
    # page_type 默认 other → 无记录
    assert parse_dom_snapshot(db, _NON_TEMPLATE_DOM) is None


# ── 后过滤：宁缺毋错 ────────────────────────────────────────


def test_post_filter_rejects_job_ads_page():
    """职位列表/登录页：即使模型违规带了 records 也不采信。"""
    output = DomParseOutput.model_validate({**_LLM_OUTPUT, "page_type": "job_ads"})
    assert dom_parse._post_filter(output, "outline 大数据开发工程师 Written Test") is None


def test_post_filter_drops_unqualified_records():
    outline = "AI平台开发工程师 已拒绝 大数据开发工程师 Written Test"
    output = DomParseOutput.model_validate(
        {
            "page_type": "applications",
            "records": [
                {"job_title": "", "status_raw": "已拒绝", "confidence": 0.9},  # 无岗位名
                {"job_title": "AI平台开发工程师", "status_raw": "已拒绝", "confidence": 0.3},  # 低置信
                {"job_title": "京公网安备11010802024479号", "status_raw": "已拒绝", "confidence": 0.9},  # 噪声
                {  # 反幻觉：页面根本没有这条记录
                    "job_title": "虚构的算法工程师",
                    "status_raw": "面试中",
                    "confidence": 0.99,
                },
                {  # 反幻觉：状态原文不在页面
                    "job_title": "AI平台开发工程师",
                    "status_raw": "Offer",
                    "confidence": 0.99,
                },
                {  # 合格记录
                    "job_title": "AI平台开发工程师",
                    "status_raw": "已拒绝",
                    "applied_at": "2026-08-25",
                    "status": "rejected",
                    "confidence": 0.95,
                },
            ],
        }
    )
    result = dom_parse._post_filter(output, outline)
    assert result is not None
    assert len(result.records) == 1
    assert result.records[0].job_title == "AI平台开发工程师"
    assert str(result.records[0].applied_at) == "2026-08-25"
    # 低置信建议不进沉淀队列
    assert result.suggestions == [("已拒绝", "rejected", 0.95)]


def test_verbatim_check_tolerates_decorative_glyphs():
    """glm-4-flash 实测回归：模型把相邻图标字符粘进状态照抄（「已拒绝✕」
    「Interviewing◦」——大纲里 [title=…] 与图标文本同行），词元切分须把装饰
    符号当分隔符，不得惩罚照抄忠实度；编造内容仍被拒。"""
    outline = "<span.ico [title=已拒绝]> ✕\n  <span.ico [title=Interviewing]> ◦"
    assert dom_parse._verbatim_in("已拒绝✕", outline)
    assert dom_parse._verbatim_in("Interviewing◦", outline)
    assert dom_parse._verbatim_in("已拒绝", outline)
    assert dom_parse._verbatim_in("3", "状态 3")  # 短码整串回查兜底
    assert not dom_parse._verbatim_in("面试中", outline)  # 页面没有的内容仍被拒
    assert not dom_parse._verbatim_in("✕✕", outline)  # 纯符号串无词元不通过


def test_post_filter_rejects_pure_symbol_status():
    """glm-4-flash 实测第二形态：图标符号本身被当作 status_raw（✕/◦，语义
    放进 status 建议）——纯符号原文无意义且会沉淀 ^✕$ 类脆弱规则，整条丢弃。"""
    output = DomParseOutput.model_validate(
        {
            "page_type": "applications",
            "records": [
                {"job_title": "AI平台工程师", "status_raw": "✕", "status": "rejected", "confidence": 0.9},
                {"job_title": "大数据开发工程师", "status_raw": "Interviewing", "status": "interview_unknown", "confidence": 0.9},
            ],
        }
    )
    result = dom_parse._post_filter(output, "AI平台工程师 ✕ 大数据开发工程师 Interviewing")
    assert result is not None and len(result.records) == 1
    assert result.records[0].status_raw == "Interviewing"
    assert result.suggestions == [("Interviewing", "interview_unknown", 0.9)]


def test_post_filter_all_dropped_is_none():
    output = DomParseOutput.model_validate({"page_type": "applications", "records": []})
    assert dom_parse._post_filter(output, "x") is None


def test_post_filter_caps_records():
    records = [
        {"job_title": "岗位工程师", "status_raw": "已拒绝", "confidence": 0.9} for _ in range(80)
    ]
    output = DomParseOutput.model_validate({"page_type": "applications", "records": records})
    result = dom_parse._post_filter(output, "岗位工程师 已拒绝")
    assert result is not None
    assert len(result.records) == dom_parse._MAX_RECORDS


# ── 语义建议沉淀 StatusRule ─────────────────────────────────


def test_deposit_suggestions_only_when_unresolved(db):
    """既有规则可解析的不沉淀（不覆盖通用/实证规则）；解析不出的沉淀为门户规则。"""
    portal = Portal(name="测试门户", company="测试", provider_key="snapshot", domains=["x.com"], config={})
    db.add(portal)
    db.flush()

    count = dom_parse.deposit_suggestions(
        db, portal, [("已拒绝", "rejected", 0.95), ("Written Test", "written_test", 0.92)]
    )
    assert count == 1  # 「已拒绝」通用规则已覆盖
    rules = list(db.scalars(select(StatusRule).where(StatusRule.scope_type == "portal")))
    assert len(rules) == 1
    assert rules[0].pattern == "^Written\\ Test$"
    assert rules[0].mapped_status == "written_test"
    assert rules[0].source == "llm"


# ── API 级全链路（与 test_ext_snapshots 的 dom 兜底用例同构）──────────


def _pair(client) -> str:
    resp = client.post("/api/ext/pair-code")
    assert resp.status_code == 201, resp.text
    resp = client.post("/api/ext/pair", json={"code": resp.json()["code"], "device_label": "t"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def test_llm_dom_full_pipeline(auth_client, db, monkeypatch):
    """非模板版式（状态在 title 属性/英文文案）：规则层全落空 → LLM 兜底
    建档落卡（route=llm_dom）；英文状态经沉淀的 StatusRule 正确归一。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    calls: list = []
    monkeypatch.setattr(client, "call_json", _fake_llm(_LLM_OUTPUT, calls))
    monkeypatch.setattr(settings, "llm_dom_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_dom_api_key", "test-key")

    token = _pair(auth_client)
    payload = {
        "url": "https://hire.xmock.cn/campus/my-applications",
        "network": [
            {
                "url": "https://hire.xmock.cn/api/apply/list",
                "method": "POST",
                "request_body": "{}",
                "response_body": '{"code": 0, "data": {"enc": "cipher"}}',
            }
        ],
        "dom": _NON_TEMPLATE_DOM,
    }
    resp = auth_client.post(
        "/api/ext/snapshots",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "parsed"
    assert body["route"] == "llm_dom"
    assert body["parsed_count"] == 2
    assert "沉淀状态规则 1 条" in body["note"]
    assert len(calls) == 1

    cards = list(db.scalars(select(Application)))
    assert len(cards) == 2
    by_title = {c.job_title: c for c in cards}
    assert by_title["AI平台开发工程师（2027届）"].current_status == "rejected"
    assert by_title["AI平台开发工程师（2027届）"].raw_status_text == "已拒绝"
    assert by_title["大数据开发工程师"].current_status == "written_test"
    assert by_title["大数据开发工程师"].raw_status_text == "Written Test"
    assert str(by_title["大数据开发工程师"].applied_at) == "2026-08-28"

    portal = db.scalar(select(Portal).where(Portal.provider_key == "snapshot"))
    assert portal is not None and portal.name == "某某科技"  # DOM title 品牌兜底


def test_llm_dom_not_used_when_rules_dom_succeeds(auth_client, db, monkeypatch):
    """规则版 dom_records 可解析的版式不烧 LLM：零调用，route=dom。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    calls: list = []
    monkeypatch.setattr(client, "call_json", _fake_llm(_LLM_OUTPUT, calls))
    monkeypatch.setattr(settings, "llm_dom_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_dom_api_key", "test-key")

    token = _pair(auth_client)
    dom = (
        "<html><body><div class=\"list\">"
        "<div class=\"row\"><span>大数据平台开发工程师</span><span>2026-08-20</span><span>简历评估中</span></div>"
        "<div class=\"row\"><span>后端开发工程师</span><span>2026-08-18</span><span>笔试</span></div>"
        "</div></body></html>"
    )
    resp = auth_client.post(
        "/api/ext/snapshots",
        json={"url": "https://hire2.xmock.cn/apply", "network": [], "dom": dom},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "parsed" and body["route"] == "dom"
    assert calls == []  # 规则层成功，LLM 一次都没调


# ── 可信度分门控：规则「成功但可疑」的裁决 ──────────────────────
# 单卡无日期（trust=0.10+0+0.15=0.25 < 0.5）——炎魂/bilibili 事故的共同形态
_LOW_TRUST_DOM = (
    "<html><head><title>某站招聘</title></head><body>"
    "<div class=\"card\">"
    "<span class=\"t\">数据产品经理</span><span class=\"loc\">北京</span>"
    "<span class=\"dept\">产研部</span><span class=\"st\">已投递</span>"
    "</div></body></html>"
)

_LOW_TRUST_LLM = {
    "page_type": "applications",
    "records": [
        {
            "job_title": "数据产品经理",
            "status_raw": "已投递",
            "applied_at": "2026-09-02",
            "department": "产研部",
            "work_location": "北京",
            "status": "screening",
            "confidence": 0.93,
        }
    ],
    "reason": "单卡片：岗位/地点/部门/状态",
}


def test_low_trust_rules_overruled_by_llm(auth_client, db, monkeypatch):
    """规则能出记录但可信度不足 → LLM 接管（注意里的裁决依据可见）。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    calls: list = []
    monkeypatch.setattr(client, "call_json", _fake_llm(_LOW_TRUST_LLM, calls))
    monkeypatch.setattr(settings, "llm_dom_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_dom_api_key", "test-key")

    token = _pair(auth_client)
    resp = auth_client.post(
        "/api/ext/snapshots",
        json={"url": "https://hire3.xmock.cn/apply", "network": [], "dom": _LOW_TRUST_DOM},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "parsed" and body["route"] == "llm_dom"
    assert len(calls) == 1
    assert "规则可信度 0.25" in body["note"] and "LLM 接管" in body["note"]
    card = db.scalars(select(Application)).first()
    assert card.current_status == "screening"
    assert str(card.applied_at) == "2026-09-02"  # LLM 补上了规则漏掉的日期


def test_low_trust_rules_degrade_gracefully(auth_client, db, monkeypatch):
    """LLM 未配置（默认 heuristic）：低分规则结果仍作降级兜底，行为不劣于改造前。"""
    from app.services.ingest import dom_plausibility, dom_records

    monkeypatch.setattr(settings, "llm_dom_provider", "heuristic")  # 隔离 .env 的真实配置

    recs = dom_records(_LOW_TRUST_DOM)
    assert recs and dom_plausibility(recs) == pytest.approx(0.25)

    token = _pair(auth_client)
    resp = auth_client.post(
        "/api/ext/snapshots",
        json={"url": "https://hire4.xmock.cn/apply", "network": [], "dom": _LOW_TRUST_DOM},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "parsed" and body["route"] == "dom"
    assert "LLM 不可用降级采信" in body["note"]
    assert len(list(db.scalars(select(Application)))) == 1


def test_low_trust_rules_kept_when_llm_fails(auth_client, db, monkeypatch):
    """LLM 配置了但上游故障：降级采信规则结果，不丢数据不抛错。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    monkeypatch.setattr(client, "call_json", _fake_llm(client.LLMError("upstream down")))
    monkeypatch.setattr(settings, "llm_dom_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_dom_api_key", "test-key")

    token = _pair(auth_client)
    resp = auth_client.post(
        "/api/ext/snapshots",
        json={"url": "https://hire5.xmock.cn/apply", "network": [], "dom": _LOW_TRUST_DOM},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "parsed" and body["route"] == "dom"
    assert "LLM 不可用降级采信" in body["note"]


# ── 状态护栏：dom 路由的逆跳/解析退化不覆盖已知状态 ─────────────────


def _card_dom(status_text: str, date_text: str) -> str:
    return (
        "<html><body><div class=\"card\">"
        f"<span>AI平台工程师</span><span>投递时间：{date_text}</span>"
        f"<span>{status_text}</span><span>平台部</span>"
        "</div></body></html>"
    )


def test_suspect_guard_blocks_regression_and_degradation(auth_client, db, monkeypatch):
    """两连拍：offer → 筛选（逆跳，dom 路由）与 offer → 待确认（解析退化，
    llm_dom 路由——规则路径的状态单元格必命中词典，退化只能来自 LLM 提取的
    无映射原文）都被拦截，状态与原文保持同源；正向变更（offer → 已入职）放行。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    # 解析退化用例的 LLM 输出：状态原文 B7 无映射、无建议（置信 0.9）
    degenerate_llm = {
        "page_type": "applications",
        "records": [
            {"job_title": "AI平台工程师", "status_raw": "B7", "applied_at": "2026-09-02",
             "department": "", "work_location": "", "status": None, "confidence": 0.9}
        ],
        "reason": "状态在 title 属性",
    }
    calls: list = []
    monkeypatch.setattr(client, "call_json", _fake_llm([degenerate_llm], calls))
    monkeypatch.setattr(settings, "llm_dom_provider", "openai_compatible")
    monkeypatch.setattr(settings, "llm_dom_api_key", "test-key")

    token = _pair(auth_client)
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://hire6.xmock.cn/apply"

    first = auth_client.post(
        "/api/ext/snapshots", json={"url": url, "network": [], "dom": _card_dom("已发Offer", "2026-08-20")}, headers=headers
    )
    assert first.status_code == 201 and first.json()["route"] == "dom"
    assert calls == []  # 高可信（0.65）不烧 LLM
    card = db.scalars(select(Application)).first()
    assert card.current_status == "offer" and card.raw_status_text == "已发Offer"

    # 逆跳：offer → 筛选（选错行/字段错位的典型形态）
    second = auth_client.post(
        "/api/ext/snapshots", json={"url": url, "network": [], "dom": _card_dom("简历评估中", "2026-09-01")}, headers=headers
    )
    assert second.status_code == 201
    assert "拦截可疑状态变更 1 条" in second.json()["note"]
    assert card.current_status == "offer"
    assert card.raw_status_text == "已发Offer"  # 整条跳过：原文不得与新状态混写
    assert len(card.history) == 1

    # 解析退化：offer → 待确认（状态在 title 属性的版式，规则失败走 LLM，
    # LLM 提取的 B7 无映射无建议 → 不得覆盖已知 offer）
    dom3 = (
        "<html><body><div class=\"card\">"
        "<a class=\"job-link\">AI平台工程师</a><span class=\"time\">投递时间：2026-09-02</span>"
        "<span class=\"ico\" title=\"B7\">✕</span>"
        "</div></body></html>"
    )
    third = auth_client.post(
        "/api/ext/snapshots", json={"url": url, "network": [], "dom": dom3}, headers=headers
    )
    assert third.status_code == 201 and third.json()["route"] == "llm_dom"
    assert len(calls) == 1
    assert "拦截可疑状态变更 1 条" in third.json()["note"]
    assert card.current_status == "offer" and card.raw_status_text == "已发Offer"

    # 正向变更放行：offer → 已入职
    fourth = auth_client.post(
        "/api/ext/snapshots", json={"url": url, "network": [], "dom": _card_dom("已入职", "2026-09-03")}, headers=headers
    )
    assert fourth.status_code == 201
    assert "拦截" not in fourth.json()["note"]
    assert card.current_status == "onboarded"
    assert len(card.history) == 2


def test_suspect_guard_off_keeps_old_behavior(db):
    """guard 关闭（默认）：逆跳照旧写入——网络层路由与绑定轮询不受影响。"""
    from app.adapters import RawApplication
    from app.core.security import hash_password
    from app.db.models import Portal, User
    from app.services.sync import ingest_applications

    user = User(email="guard@test.com", password_hash=hash_password("password123"))
    portal = Portal(name="护栏门户", company="测试", provider_key="snapshot", domains=["guard.com"], config={})
    db.add_all([user, portal])
    db.flush()
    raw = lambda status: [RawApplication(job_title="AI工程师", status_raw=status)]  # noqa: E731

    ingest_applications(db, user=user, portal=portal, raw_list=raw("已发Offer"))
    card = db.scalars(select(Application)).first()
    assert card.current_status == "offer"

    summary = ingest_applications(db, user=user, portal=portal, raw_list=raw("简历评估中"))
    assert summary["guarded"] == 0 and summary["updated"] == 1
    assert card.current_status == "screening"  # 旧行为：无护栏时逆跳直接写入
