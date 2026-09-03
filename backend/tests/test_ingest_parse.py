"""M1 快照解析 golden 回归（REFACTOR_PLAN §3 M1：四真实站形状作解析回归）。

数据来源见各 golden 文件头注释——真实站载荷是这条链路唯一可信的考卷；
「不要修了一个丢了上一个」：每个历史失败形态都必须有对应回归用例。
"""

import json
from pathlib import Path

import pytest

from app.services.ingest import (
    ParsedPayload,
    brand_from_dom,
    dom_records,
    parse_snapshot_network,
    payload_hash,
    registrable_domain,
    site_key,
)

GOLDEN = Path(__file__).parent / "golden_samples"


def _snapshot(name: str) -> dict:
    # 真实站点测试数据不入库（用户拍板 2026-09-03）：此类 golden 放
    # golden_samples/private/（gitignore），克隆环境缺失时跳过而非失败
    path = next((p for p in (GOLDEN / name, GOLDEN / "private" / name) if p.exists()), None)
    if path is None:
        pytest.skip(f"私有 golden 未提供（真实站点数据不入库）: {name}")
    g = json.loads(path.read_text(encoding="utf-8"))
    return g.get("snapshot") or g.get("sample")


@pytest.mark.parametrize(
    "name,expect_route,expect_path",
    [
        ("feishu_qunar_like.json", "platform", "data.delivery_list"),
        ("xiaomi_feishu_like.json", "platform", "data.delivery_list"),
        ("beisen_trap_like.json", "platform", "Data.*.Submissions.*.Datas"),
        ("moka_like.json", "platform", "data.list"),
        ("ctrip_like.json", "platform", "applyJobAdList"),
        ("oppo_progress_like.json", "platform", "data.*.deliveryPositionRecordList"),
        ("tencent_like.json", "heuristics", "data"),
        # 旧版北森合成 golden（多 tab 拼接/分组列表形状）不因 ingest 引入而回退
        ("beisen_like.json", "platform", "Data.*.Submissions.*.Datas"),
        # 星环实盘：密文条目 + JSON.parse 钩子的 #decrypted 伪条目
        ("moka_encrypted_like.json", "platform", "data.list"),
    ],
)
def test_golden_parse(name, expect_route, expect_path):
    p = parse_snapshot_network(_snapshot(name)["network"])
    assert p is not None, f"{name} 应可解析"
    assert p.route == expect_route
    assert p.list_json_path == expect_path
    assert p.records, "至少一条 title/status 双全的记录"
    for r in p.records:
        assert r.job_title and r.status_raw


def test_feishu_fields_and_dates():
    """飞书形状：嵌套 job_post_info.title + operation_list 末项状态 + 毫秒时间戳。"""
    p = parse_snapshot_network(_snapshot("feishu_qunar_like.json")["network"])
    r = p.records[0]
    assert r.job_title == "AI应用开发工程师（测试开发）"
    assert r.status_raw == "3"
    assert str(r.applied_at) == "2026-08-07"


def test_beisen_trap_prefers_delivery_over_job_ads():
    """M0 盘点 #27 形态：职位列表（GetJobAdPageList）与投递记录双可解析，必须选投递。

    这是「误报率」指标盯的第一形态——职位列表冒充投递记录。
    """
    candidates_net = _snapshot("beisen_trap_like.json")["network"]
    p = parse_snapshot_network(candidates_net)
    assert "GetAllDeliveryRecord" in p.entry_url
    assert "JobAdPageList" not in p.entry_url
    assert p.records[0].job_title == "解决方案工程师-软件方向"
    assert p.records[0].status_raw == "简历初筛"
    assert str(p.records[0].applied_at) == "2026-08-25"


def test_moka_synthetic():
    p = parse_snapshot_network(_snapshot("moka_like.json")["network"])
    assert len(p.records) == 2
    assert p.records[0].job_title == "大数据平台开发工程师"
    assert p.records[0].status_raw == "简历评估中"


def test_moka_encrypted_without_decrypted_is_no_data():
    """星环 2026-09-02 实盘形态：网络层只有密文（data+necromancer）与内嵌清单 → 解析必败。

    固化「为什么需要 JSON.parse 钩子」：密文进不了任何解析路径，明文只存在于页面解密之后。
    """
    net = [e for e in _snapshot("moka_encrypted_like.json")["network"] if "#decrypted" not in e["url"]]
    assert parse_snapshot_network(net) is None


def test_moka_encrypted_decrypted_pseudo_entry_wins():
    """#decrypted 伪条目（JSON.parse 钩子产物）可解析，且候选打分选中它而非密文/噪声条目。"""
    p = parse_snapshot_network(_snapshot("moka_encrypted_like.json")["network"])
    assert p is not None
    assert "#decrypted" in p.entry_url
    assert p.route == "platform" and p.list_json_path == "data.list"
    assert len(p.records) == 2
    assert p.records[0].job_title == "大数据平台开发工程师"
    assert p.records[0].status_raw == "简历评估中"


def test_moka_jobslist_trap_rejected():
    """星环快照 #3 实盘回归（0.5.2 错卡事故）：解密捕获的站点启动配置对象里，
    jobs 职位数组键恰含 title+status（status 全为 open），不得冒充投递记录——
    职位广告键（openedAt/publishedAt/jobCount…）命中 ≥2 即跳过，宁缺毋错。
    """
    assert parse_snapshot_network(_snapshot("moka_jobslist_trap_like.json")["network"]) is None


def test_beisen_hongke_jobad_trap_rejected():
    """虹科快照 #22/#23 实盘回归：GetJobAdPageList 的明文条目与其 #decrypted 解密
    孪生同屏。伪条目 URL 是页面地址（personal/deliveryRecord，deliver 强正 +4），
    曾借页面 URL 压过真身自身的 jobad 负分，20 个职位冒充投递建卡。

    双防线缺一不可：① 北森职位广告键（headcount/postdate/salary…）命中 ≥2 否决，
    与 URL 无关（#decrypted 载荷没有 URL 负特征可用）；② 伪条目不继承页面 URL
    的强/负特征。真投递接口 GetAllDeliveryRecord 未捕获 → 整快照 no_data。
    """
    assert parse_snapshot_network(_snapshot("beisen_hongke_trap_like.json")["network"]) is None


def test_lenovo_jd_detail_trap_rejected():
    """联想快照 #39 实盘回归（2026-09-03）：我的申请页三条 #decrypted 载荷——
    系统字典 / status=null 申请简表 / 职位详情（JD）。JD 详情的
    jobName+status+workPlace 恰好覆盖投递键型，旧版 heuristics 以唯一候选
    身份胜出建错卡（work_location=2、状态数字码落待确认）。

    双防线：① JD 详情键（jobDuties/jobRequirement/educationRequired/isCollect/
    hotFlag）命中 ≥2 判 job_ads；② 语义锚点门槛——无 applied_at 且状态全为
    数字码的候选没有可校验锚，让位 DOM 层（页面渲染了「投递成功」「工作地点：
    上海」）。整快照网络层 no_data → 规则 DOM 可信度 0.25 < 0.5 → LLM 接管。
    """
    assert parse_snapshot_network(_snapshot("lenovo_jd_detail_trap_like.json")["network"]) is None


def test_lenovo_poisoned_hints_replay_invalidated():
    """联想事故的固化通道回归：错误解析已写进门户 hints（旧版引擎把 JD 详情钉为
    上次成功定位），重放不得继续复现错卡——items 形态被识破即作废走全量扫描。"""
    from app.services.ingest import _apply_hints

    snap = _snapshot("lenovo_jd_detail_trap_like.json")
    jd_entry = next(e for e in snap["network"] if "#decrypted-1aezch8" in e["url"])
    hints = {
        "url": jd_entry["url"],
        "list_json_path": "result",
        "field_map": {"job_title": "jobName", "status_raw": "status", "work_location": "workPlace"},
    }
    assert _apply_hints(hints, snap["network"]) is None


def test_heuristics_numeric_status_without_anchor_rejected():
    """语义锚点门槛单元回归：数字状态 + 无日期的载荷（联想 JD 详情的键型抽象）
    不产 heuristics 候选；带上 applied_at 或文字状态（真实投递接口的形态）即放行。"""
    numeric_no_date = [
        {"url": "https://x.com/apply#decrypted-1", "method": "POST",
         "response_body": json.dumps({"result": [{"jobName": "AI应用开发工程师", "status": 1, "workPlace": 2}]})}
    ]
    assert parse_snapshot_network(numeric_no_date) is None

    with_date = [
        {"url": "https://x.com/api/apply/list", "method": "POST",
         "response_body": json.dumps({"result": [
             {"jobName": "AI应用开发工程师", "status": 1, "workPlace": 2, "deliverTime": "2026-08-30 10:00:00"}
         ]})}
    ]
    p = parse_snapshot_network(with_date)
    assert p is not None and p.route == "heuristics"
    assert str(p.records[0].applied_at) == "2026-08-30"

    text_status = [
        {"url": "https://x.com/apply#decrypted-2", "method": "POST",
         "response_body": json.dumps({"result": [{"jobName": "AI应用开发工程师", "status": "简历评估中"}]})}
    ]
    p = parse_snapshot_network(text_status)
    assert p is not None and p.records[0].status_raw == "简历评估中"


def test_dom_plausibility_calibration():
    """可信度分校准（2026-09-03 决策：规则层冻结，此分裁决是否请 LLM）：
    真实 golden（多行/单卡带日期）必须高分采信；历史事故形态（单卡无日期、
    多行短标题无日期）必须低于 0.5 交给 LLM。数值锚定防漂移。"""
    from datetime import date

    from app.llm.extract import ExtractedRecord
    from app.services.ingest import dom_plausibility

    # 真实站 golden：规则擅长的形态，高分采信（LLM 开启时也不烧钱）
    assert dom_plausibility(dom_records(_snapshot("moka_yanhun_dom_like.json")["dom"])) >= 0.5
    assert dom_plausibility(dom_records(_snapshot("bilibili_dom_like.json")["dom"])) >= 0.5

    def rec(title, dt):
        return ExtractedRecord(job_title=title, status_raw="已投递", applied_at=dt)

    # 单卡无日期（0.10+0+0.15）：事故高发形态 → LLM 接管
    assert dom_plausibility([rec("数据产品经理", None)]) == pytest.approx(0.25)
    # 多行无日期（0.45+0+0.15）：日期缺失但结构同构，仍采信
    assert dom_plausibility([rec("岗位工程师", None), rec("另一个岗位名", None)]) == pytest.approx(0.60)
    # 多行短标题无日期（0.45+0+0）：导航/菜单形态 → LLM 接管
    assert dom_plausibility([rec("首页", None), rec("职位", None)]) == pytest.approx(0.45)
    # 满分形态：单卡带日期 + 正常标题（0.10+0.4+0.15）
    assert dom_plausibility([rec("大数据平台开发工程师", date(2026, 8, 20))]) == pytest.approx(0.65)
    assert dom_plausibility([]) == 0.0


def test_dom_fallback_extracts_rendered_records():
    """DOM 兜底（星环 Worker 解密/网易未知传输的最终防线）：从渲染后的裁剪 HTML
    找「同签名重复兄弟行」组，状态词典命中状态单元格、最长非日期文本作岗位名。
    """
    html = """
    <html><body>
      <nav><a class="nav-item">首页</a><a class="nav-item">职位</a><a class="nav-item">关于我们</a></nav>
      <div class="apply-list">
        <div class="apply-row"><span class="t">大数据平台开发工程师</span><span class="d">2026-08-20 10:24</span><span class="s">简历评估中</span><span class="loc">上海</span></div>
        <div class="apply-row"><span class="t">后端开发工程师（基础平台）</span><span class="d">2026-08-18 09:00</span><span class="s">笔试</span><span class="loc">杭州</span></div>
        <div class="apply-row"><span class="t">测试开发工程师</span><span class="d">2026-08-15</span><span class="s">已拒绝</span><span class="loc">北京</span></div>
      </div>
      <div class="footer">© 2026 某公司 招聘已结束</div>
    </body></html>
    """
    recs = dom_records(html)
    assert len(recs) == 3
    assert recs[0].job_title == "大数据平台开发工程师"
    assert recs[0].status_raw == "简历评估中"
    assert str(recs[0].applied_at) == "2026-08-20"
    assert recs[2].status_raw == "已拒绝"
    # 导航（无状态词）与页脚（孤立状态词但非行组）都不得成为记录
    titles = {r.job_title for r in recs}
    assert all("首页" not in t and "招聘" not in t for t in titles)


def test_dom_fallback_single_record_card():
    """单条投递的卡片式页面（单行组需 ≥4 单元格防误报）。"""
    html = """
    <html><body>
      <div class="card"><h3 class="pos">SRE 工程师</h3><span class="time">投递时间：2026-08-27</span>
      <span class="st">已投递</span><span class="dept">基础架构部</span></div>
      <div class="banner">站点公告：简历筛选进行中</div>
    </body></html>
    """
    recs = dom_records(html)
    assert len(recs) == 1
    assert recs[0].job_title == "SRE 工程师"
    assert recs[0].status_raw == "已投递"
    assert str(recs[0].applied_at) == "2026-08-27"


def test_dom_fallback_noise_only():
    """无状态词行组（表头/导航/职位卡）与空输入一律返回空。"""
    html = """
    <html><body>
      <table><tr><th>岗位名称</th><th>投递时间</th><th>状态</th></tr></table>
      <ul><li class="job">Java 开发</li><li class="job">Go 开发</li></ul>
    </body></html>
    """
    assert dom_records(html) == []
    assert dom_records("") == []
    assert dom_records("<html><broken") == []


def test_ctrip_phase_status_join():
    """携程实盘（快照 #17/#18）：状态拆 phaseInfoCN 阶段 + statusInfoCN 进度两字段。

    启发式按字段名只能选中 statusInfoCN，单取「进行中」无业务语义、所有规则
    不命中（曾整卡落待确认）；平台规格拼接「测评 进行中」后命中通用规则 →
    assessment，失败形态「测评 未通过」→ rejected（未通过规则优先）。
    同屏的 getJobAd 推荐职位列表不得在候选排序中胜出。
    """
    from app.domain.normalize import normalize_status

    p = parse_snapshot_network(_snapshot("ctrip_like.json")["network"])
    assert p is not None and p.route == "platform" and p.list_json_path == "applyJobAdList"
    assert "getApplyJobRecord" in p.entry_url and "getJobAd" not in p.entry_url
    r = p.records[0]
    assert r.job_title.startswith("大数据平台开发工程师")
    assert r.status_raw == "测评 进行中"
    assert str(r.applied_at) == "2026-09-01"
    assert r.work_location == "上海"
    assert normalize_status(r.status_raw) == "assessment"
    assert normalize_status("测评 未通过") == "rejected"
    assert normalize_status("面试 进行中") == "interview_unknown"


def test_yanhun_real_dom_golden():
    """炎魂真实裁剪 DOM（快照 #12 原文）：单条投递卡片页。三重事故回归——
    ① 导航「我的简历」不得命中状态词典（曾致导航区成假记录组）；
    ② 页脚「京公网安备…」不得成为岗位名；
    ③ 真实状态「初筛」必须识别（规则此前只写了「筛选」匹配不上）。
    """
    snap = _snapshot("moka_yanhun_dom_like.json")
    recs = dom_records(snap["dom"])
    assert len(recs) == 1
    assert recs[0].job_title == "AI应用开发工程师（2027届）"
    assert recs[0].status_raw == "初筛"
    assert str(recs[0].applied_at) == "2026-08-26"
    assert brand_from_dom(snap["dom"]) == "炎魂网络"


def test_bilibili_real_dom_golden():
    """bilibili 真实裁剪 DOM（快照 #30 原文）：异构双卡片投递记录页。四重回归——
    ①「初筛阶段不匹配」是拒绝语义（曾被「初筛」关键词抢进简历评估栏）；
    ② 人才库提示横幅（整句带句号）不得成为岗位名；
    ③「已撤回」是合法状态（曾被导航词表「撤回」误杀，第二行整行丢失）；
    ④ 多日期并存取「投递」日（字典序取早曾误取「2026-08-07 发布」）。
    """
    from app.domain.normalize import normalize_status

    recs = dom_records(_snapshot("bilibili_dom_like.json")["dom"])
    assert len(recs) == 2
    by_status = {r.status_raw: r for r in recs}
    rejected = by_status["初筛阶段不匹配"]
    assert rejected.job_title == "【主站】AI 创作项目工程师【2027届】"
    assert str(rejected.applied_at) == "2026-08-11"
    withdrawn = by_status["已撤回"]
    assert withdrawn.job_title == "AI-Native 开发工程师（后端方向）【2027届】"
    assert str(withdrawn.applied_at) == "2026-08-05"
    # 岗位名未被人才库横幅污染
    assert all("人才库" not in r.job_title and "联系" not in r.job_title for r in recs)
    # 语义归一：不匹配类 → rejected（优先于初筛/简历关键词）；已撤回 → withdrawn
    assert normalize_status("初筛阶段不匹配") == "rejected"
    assert normalize_status("简历评估不匹配") == "rejected"
    assert normalize_status("已进入人才库") == "rejected"
    assert normalize_status("初筛通过") == "screening"  # 通过初筛仍属筛选阶段
    assert normalize_status("已撤回") == "withdrawn"


def test_oppo_flow_node_status():
    """OPPO 校招（2026-09-03 云端实盘异常）：投递条目上没有平铺状态字段——
    状态藏在 flowProcessTemplateList 流程节点数组（THE_ONGOING 当前 / NOT_PASS
    被拒 / PASS 已过 / DID_NOT_ARRIVE 未到），页面状态文案由前端计算。此前的
    点路径语法表达不了「取进行中的节点」，三层网络解析全部落空（no_data 或
    掉进 DOM 兜底抽渲染文本）。

    规格拼接链：被拒标记 > 当前节点码 > 末节点码（全 PASS 即流程走完），
    靠 status_map 先到先得拼出终语义——NOT_PASS 必须排在阶段规则之前。
    """
    from app.domain.normalize import normalize_status

    p = parse_snapshot_network(_snapshot("oppo_progress_like.json")["network"])
    assert p is not None and p.route == "platform"
    assert "queryAllDeliveryProgressList" in p.entry_url
    by_title = {r.job_title: r for r in p.records}
    assert len(p.records) == 3

    ongoing = by_title["AI算法工程师"]
    assert ongoing.status_raw == "SCREENING ENTRY"
    assert ongoing.work_location == "深圳市"

    rejected = by_title["软件开发工程师（Android）"]
    assert rejected.status_raw == "NOT_PASS ENTRY"

    offer = by_title["数据分析师"]
    assert offer.status_raw == "OFFER ENTRY"

    portal_map = p.status_map
    assert normalize_status(ongoing.status_raw, portal_map) == "screening"
    assert normalize_status(rejected.status_raw, portal_map) == "rejected"
    assert normalize_status(offer.status_raw, portal_map) == "offer"
    # 末节点兜底：全 PASS（流程走完入职）取末节点码
    assert normalize_status("ENTRY", portal_map) == "onboarded"
    assert normalize_status("WRITTEN_EXAMINATION ENTRY", portal_map) == "written_test"
    # 被拒时当前节点码仍拼在串里，NOT_PASS 规则必须压过阶段规则
    assert normalize_status("NOT_PASS WRITTEN_EXAMINATION ENTRY", portal_map) == "rejected"


def test_dig_filter_segment():
    """dig 过滤段（key=value）：列表中取首个匹配 dict 再下探——OPPO 流程节点
    「当前进行中的那个」依赖此语义；无匹配/非列表节点返回 None。"""
    from app.adapters.fields import dig

    data = {
        "nodes": [
            {"code": "A", "state": "PASS", "label": "已过"},
            {"code": "B", "state": "THE_ONGOING", "label": "当前"},
            {"code": "C", "state": "DID_NOT_ARRIVE", "label": "未来"},
        ]
    }
    assert dig(data, "nodes.state=THE_ONGOING.label") == "当前"
    assert dig(data, "nodes.state=PASS.code") == "A"  # 首个匹配
    assert dig(data, "nodes.state=MISSING.label") is None  # 无匹配
    assert dig(data, "nodes.state=THE_ONGOING")["code"] == "B"  # 过滤段可直接为末段
    assert dig(data, "single.state=X") is None  # 非列表节点上的过滤段
    assert dig(data, "nodes.state=PASS.label+nodes.state=THE_ONGOING.label") == "已过 当前"
    assert dig(data, "nodes.-1.code") == "C"  # 负下标（既有语义不回归）


def test_platform_status_map_backfills_existing_portal(db):
    """存量门户自愈：dom/heuristics 期建的门户没有实证码表，平台规格命中后
    补写 status_map（云端 OPPO 门户在规格上线前建档的场景），下次同步即恢复。"""
    from app.services.ingest import upsert_portal_from_snapshot

    url = "https://careers.oppo.com/university/oppo/center/history"
    dom_payload = ParsedPayload(
        entry_url=url + "#dom", list_json_path="dom", field_map={}, records=[], route="dom", score=0.0
    )
    portal = upsert_portal_from_snapshot(db, url, dom_payload, None, dom=None)
    assert "status_map" not in portal.config

    payload = parse_snapshot_network(_snapshot("oppo_progress_like.json")["network"])
    upsert_portal_from_snapshot(
        db, url, payload, _snapshot("oppo_progress_like.json")["network"], dom=None
    )
    assert any(e["pattern"] == "NOT_PASS" for e in portal.config["status_map"])
    assert portal.config["hints"]["list_json_path"] == "data.*.deliveryPositionRecordList"


def test_multi_tenant_site_key():
    """Moka 多租户：星环/炎魂同注册域（mokahr.com）必须按 URL 租户段分门户；
    飞书/北森子域名本身即租户，不受影响。"""
    assert site_key("https://app.mokahr.com/campus_apply/yanhun/24017#/x") == "app.mokahr.com/yanhun"
    assert site_key("https://app.mokahr.com/campus_apply/transwarp/3196#/x") == "app.mokahr.com/transwarp"
    assert site_key("https://hf7l9aiqzx.jobs.feishu.cn/704852/position") == "hf7l9aiqzx.jobs.feishu.cn"
    assert site_key("https://hkaco.zhiye.com/social") == "hkaco.zhiye.com"


def test_no_data_shapes():
    """空缓冲 / 纯噪声 / 截断条目：一律 None（宁缺毋错），不抛异常。"""
    assert parse_snapshot_network([]) is None
    assert parse_snapshot_network(None) is None
    noise = [
        {"url": "https://x.com/api/track", "method": "POST", "response_body": "{\"code\":0,\"data\":[]}"},
        {"url": "https://x.com/api/big", "method": "GET", "response_body": "{\"data\":{\"list\":[", "truncated": True},
        {"url": "https://x.com/static.js", "method": "GET", "response_body": "not json"},
    ]
    assert parse_snapshot_network(noise) is None


def test_payload_hash_stable_and_order_insensitive():
    a = [{"url": "https://x.com/a", "method": "GET", "response_body": "{}"},
         {"url": "https://x.com/b", "method": "POST", "response_body": "{\"k\":1}"}]
    assert payload_hash(a) == payload_hash(list(reversed(a)))
    assert payload_hash(a) != payload_hash([a[0]])
    assert payload_hash(None) == payload_hash([])


def test_registrable_domain():
    assert registrable_domain("hf7l9aiqzx.jobs.feishu.cn") == "feishu.cn"
    assert registrable_domain("xiaomi.jobs.f.mioffice.cn") == "mioffice.cn"
    assert registrable_domain("campus.163.com.cn") == "163.com.cn"
    assert registrable_domain("") == ""


def test_numeric_work_location_overwritten_on_resync(db):
    """存量错卡自愈通道（联想实盘）：workPlace=2 这类地点 ID 落进卡片后，
    后续正确地名（LLM/规则从 DOM 提取）必须能覆盖纯数字残留；用户手填的
    真实地名仍不被覆盖。"""
    from sqlalchemy import select

    from app.adapters import RawApplication
    from app.core.security import hash_password
    from app.db.models import Application, Portal, User
    from app.services.sync import ingest_applications

    user = User(email="loc@test.com", password_hash=hash_password("password123"))
    portal = Portal(name="测试门户", company="测试", provider_key="snapshot", domains=["loc.com"], config={})
    db.add_all([user, portal])
    db.flush()
    ingest_applications(
        db, user=user, portal=portal,
        raw_list=[RawApplication(job_title="AI应用开发工程师", status_raw="1", work_location="2")],
    )
    card = db.scalars(select(Application)).first()
    assert card.work_location == "2"

    ingest_applications(
        db, user=user, portal=portal,
        raw_list=[RawApplication(job_title="AI应用开发工程师", status_raw="投递成功", work_location="上海")],
    )
    assert card.work_location == "上海"

    ingest_applications(
        db, user=user, portal=portal,
        raw_list=[RawApplication(job_title="AI应用开发工程师", status_raw="投递成功", work_location="北京市")],
    )
    assert card.work_location == "上海"  # 用户可见的真实地名不被自动覆盖


def test_title_match_ignores_unknown_department(db):
    """标题去重键的未知让位（联想实盘衍生）：卡片 department 为空是「上次没提取到」，
    不是「没有部门」——LLM 路径带部门的记录必须匹配上（heuristics→LLM 路径切换
    曾重复建卡）；两侧都有部门且不同才分开（同名岗位不同部门）。"""
    from sqlalchemy import select

    from app.adapters import RawApplication
    from app.core.security import hash_password
    from app.db.models import Application, Portal, User
    from app.services.sync import ingest_applications

    user = User(email="match@test.com", password_hash=hash_password("password123"))
    portal = Portal(name="匹配门户", company="测试", provider_key="snapshot", domains=["match.com"], config={})
    db.add_all([user, portal])
    db.flush()
    # 首次：heuristics 形态（无部门）
    ingest_applications(db, user=user, portal=portal, raw_list=[
        RawApplication(job_title="AI应用开发工程师", status_raw="1", work_location="2")
    ])
    # 二次：LLM 形态（带部门）——必须更新既有卡而非新建
    summary = ingest_applications(db, user=user, portal=portal, raw_list=[
        RawApplication(job_title="AI应用开发工程师", status_raw="简历评估中…", department="IDG,ISG")
    ])
    cards = list(db.scalars(select(Application)))
    assert summary["created"] == 0 and summary["updated"] == 1
    assert len(cards) == 1
    assert cards[0].department == "IDG,ISG"  # 空部门被补上，键从此稳定
    # 两侧都有部门且不同：同名不同岗位，分开建卡
    ingest_applications(db, user=user, portal=portal, raw_list=[
        RawApplication(job_title="AI应用开发工程师", status_raw="已投递", department="另一事业部")
    ])
    assert len(list(db.scalars(select(Application)))) == 2


def test_brand_from_dom_generic_title_rejected():
    """个人中心通用标题不当品牌（联想实盘：<title>我的申请 成了门户名与卡片
    company）；真实品牌标题不受影响。"""
    assert brand_from_dom("<html><head><title>我的申请</title></head><body></body></html>") is None
    assert brand_from_dom("<html><head><title>我的收藏</title></head><body></body></html>") is None
    assert brand_from_dom("<html><head><title>个人中心</title></head><body></body></html>") is None
    assert brand_from_dom("<html><head><title>炎魂网络 - 校园招聘</title></head><body></body></html>") == "炎魂网络"
