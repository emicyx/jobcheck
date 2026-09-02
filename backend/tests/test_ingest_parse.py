"""M1 快照解析 golden 回归（REFACTOR_PLAN §3 M1：四真实站形状作解析回归）。

数据来源见各 golden 文件头注释——真实站载荷是这条链路唯一可信的考卷；
「不要修了一个丢了上一个」：每个历史失败形态都必须有对应回归用例。
"""

import json
from pathlib import Path

import pytest

from app.services.ingest import (
    brand_from_dom,
    dom_records,
    parse_snapshot_network,
    payload_hash,
    registrable_domain,
    site_key,
)

GOLDEN = Path(__file__).parent / "golden_samples"


def _snapshot(name: str) -> dict:
    g = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
    return g.get("snapshot") or g.get("sample")


@pytest.mark.parametrize(
    "name,expect_route,expect_path",
    [
        ("feishu_qunar_like.json", "platform", "data.delivery_list"),
        ("xiaomi_feishu_like.json", "platform", "data.delivery_list"),
        ("beisen_trap_like.json", "platform", "Data.*.Submissions.*.Datas"),
        ("moka_like.json", "platform", "data.list"),
        ("ctrip_like.json", "platform", "applyJobAdList"),
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
