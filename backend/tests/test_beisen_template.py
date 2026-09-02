"""北森（zhiye.com）平台模板测试：分组列表形态 → 指纹实例化发布 → 运行时重放。

golden 网络包取自 hkaco.zhiye.com（虹科校招）2026-09-01 真实登录态采样（已脱敏）。
北森暴露的三个根因均以「通用机制」修复而非站点特判，本文件逐条锁住这些机制，
下一个分组列表 / 词典缺口 / 空 POST 体的站点不应再需要改代码：

1. dig_list 的 ``*`` 展开段——数组套数组（分组列表 Submissions[*].Datas）、
   dict 值展开（Data.* 跨 tab 拼接）、命中但空 []（翻页末页）与路径无效 None 的语义区分；
2. 通用递归扫描对分组列表的定位（不依赖模板也能找到 Submissions.*.Datas）；
3. 字段词典对 JobAdTitle / DeliveryStatus 等驼峰变体的覆盖；
4. POST 空 JSON 体 ``{}`` 在运行时原样重放（不被 ``or`` 吞掉 body/content-type——
   真实站点会 415，测试桩不校验所以必须在断言里显式锁）；
5. 误命中容错：模板路径在同域配置接口响应上返回 None，不炸管线。
"""

import json
import time
from pathlib import Path

from sqlalchemy import select

from app.adapters import AdapterContext
from app.adapters.fields import dig_list, parse_date
from app.adapters.json_adapter import JSONAPIAdapter
from app.db.models import Portal, Recipe, Sample
from app.llm import fingerprint, heuristics, validator

GOLDEN = json.loads(
    (Path(__file__).parent / "golden_samples" / "beisen_like.json").read_text(encoding="utf-8")
)
PAGE_URL = GOLDEN["sample"]["url"]  # https://hkaco.zhiye.com/personal/deliveryRecord
LIST_URL = "https://hkaco.zhiye.com/api/Submission/GetAllDeliveryRecord"
APPLY_ID = "411364182"  # golden 唯一投递的 ApplyId（int 形态，运行时应转 str portal_key）


def _network() -> list[dict]:
    return json.loads(json.dumps(GOLDEN["sample"]["network"]))  # 深拷贝，用例可自由改动


def _delivery_entry(net: list[dict] | None = None) -> dict:
    return next(e for e in (net or _network()) if "GetAllDeliveryRecord" in e["url"])


def _wait_pipeline(auth_client, sample_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = next(
            (m for m in auth_client.get("/api/samples/mine").json() if m["id"] == sample_id), None
        )
        if row and row["pipeline_status"] in ("published", "failed"):
            return row
        time.sleep(0.2)
    raise AssertionError(f"管线超时 sample={sample_id}")


# ── 机制 1：dig_list 的 * 展开段与空值语义 ──────────────────────────


def test_dig_list_star_semantics():
    # * 作用于数组：展开元素（数组套数组 = 北森分组列表形状）
    assert dig_list({"l": [[{"t": 1}, {"t": 2}]]}, "l.*") == [{"t": 1}, {"t": 2}]
    # * 作用于 dict：展开值（信封里多 tab 键 → 拼接）
    assert dig_list({"d": {"a": {"t": 1}, "b": {"t": 2}}}, "d.*") == [{"t": 1}, {"t": 2}]
    # 常规点路径不变；dict 节点自动按一条处理（单对象语义）
    assert dig_list({"data": {"t": 1}}, "data") == [{"t": 1}]
    assert dig_list([{"t": 1}], "") == [{"t": 1}]
    # 命中但为空（翻页末页）返回 []，路径无效才返回 None——二者语义不同
    assert dig_list({"data": {"list": []}}, "data.list") == []
    assert dig_list({"data": {}}, "data.list") is None
    assert dig_list({"data": {"list": [{"t": 1}]}}, "data.list.x") is None


def test_dig_list_multi_tab_grouped_concat():
    """分组列表 + 多 tab 信封：Data.* 跨 tab 拼接；空 tab 不影响其他 tab。"""
    item_a = {"ApplyId": 1, "JobAdTitle": "A", "DeliveryStatus": "简历初筛", "DeliveryDate": "2026-08-25 13:21"}
    item_b = {"ApplyId": 2, "JobAdTitle": "B", "DeliveryStatus": "已投递", "DeliveryDate": "2026-08-26 09:00"}
    data = {"Code": 200, "Data": {
        "Processing": {"TotalCount": 1, "Submissions": [{"Name": "u", "Datas": [item_b]}]},
        "Finished": {"TotalCount": 1, "Submissions": [{"Name": "u", "Datas": [item_a]}]},
    }}
    got = dig_list(data, "Data.*.Submissions.*.Datas")
    assert sorted(i["JobAdTitle"] for i in got) == ["A", "B"]  # 跨 tab 拼接（顺序不保证）

    data["Data"]["Processing"]["Submissions"] = []  # 一个 tab 空：另一 tab 照常出数
    got = dig_list(data, "Data.*.Submissions.*.Datas")
    assert [i["JobAdTitle"] for i in got] == ["A"]

    data["Data"]["Finished"]["Submissions"] = []  # 全部组为空 → 无有效节点
    assert dig_list(data, "Data.*.Submissions.*.Datas") is None


# ── 指纹识别：真实形状 + 自定义域名站 ──────────────────────────────


def test_fingerprint_matches_beisen_shape():
    hit = fingerprint.match(_network())
    assert hit is not None and hit.template.key == "beisen"
    assert hit.matched_url == LIST_URL  # 分值最高的是投递接口，不是同域配置接口
    assert hit.method == "POST" and hit.request_body == "{}"
    assert fingerprint.is_instantiable(hit) is None


def test_fingerprint_custom_domain_beisen_site():
    """自定义域名北森站（无 zhiye.com）：接口路径(3) + 响应键结构(3) = 6 ≥ 阈值 4。"""
    net = _network()
    for e in net:
        e["url"] = e["url"].replace("hkaco.zhiye.com", "campus.hongke-example.com")
    hit = fingerprint.match(net)
    assert hit is not None and hit.template.key == "beisen"
    assert hit.matched_url.endswith("/api/Submission/GetAllDeliveryRecord")


# ── 模板实例化：真实映射 + 回放验证 + 兜底路径 ───────────────────────


def test_template_mapping_extracts_grouped_list_and_normalizes_status():
    net = _network()
    entry = _delivery_entry(net)
    hit = fingerprint.match(net)
    data = json.loads(entry["response_body"])

    out = fingerprint.build_from_template(hit, data, PAGE_URL, request_body=entry["request_body"])
    assert out is not None
    assert out.recipe.list_source.list_json_path == "Data.*.Submissions.*.Datas"
    fmap = {k: v.json_path for k, v in out.recipe.field_map.items()}
    assert fmap["job_title"] == "JobAdTitle"
    assert fmap["status_raw"] == "DeliveryStatus"
    assert fmap["applied_at"] == "DeliveryDate"
    assert fmap["id"] == "ApplyId"
    assert [(o.job_title, o.status_raw) for o in out.observations] == [
        ("解决方案工程师-软件方向", "简历初筛")
    ]
    # status_map 留空：中文状态原文由通用归一化识别（简历初筛→screening），
    # 不落入「待确认」声明——未识别原文才需要显式留给兜底
    assert out.unmapped_status_texts == []
    assert str(parse_date("2026-08-25 13:21")) == "2026-08-25"  # DeliveryDate 格式

    # 模板产物照常过离线回放七断言（同一提取引擎，非自说自话）
    verdict = validator.replay(out, PAGE_URL, GOLDEN["sample"]["dom"], net)
    assert verdict.ok, verdict.errors


def test_generic_heuristic_locates_grouped_list_without_template():
    """不依赖模板：通用递归扫描 + 字段词典也能定位分组列表（下一个平台的保险）。"""
    data = json.loads(_delivery_entry()["response_body"])
    assert heuristics.derive_list_json_path(data) == "Data.Finished.Submissions.*.Datas"
    items = heuristics.locate_list(data)
    assert items and items[0]["JobAdTitle"] == "解决方案工程师-软件方向"
    fmap = heuristics.guess_field_map(items[0])
    assert fmap["job_title"] == "JobAdTitle"  # 词典覆盖 jobad(title) 驼峰变体
    assert fmap["status_raw"] == "DeliveryStatus"  # 词典覆盖 delivery status
    assert fmap["applied_at"] == "DeliveryDate"

    entry = _delivery_entry()
    out = heuristics.build_recipe(
        LIST_URL, "POST", data, PAGE_URL, request_body=entry["request_body"]
    )
    assert out is not None
    assert out.recipe.list_source.method == "POST" and out.recipe.list_source.body == {}
    verdict = validator.replay(out, PAGE_URL, GOLDEN["sample"]["dom"], _network())
    assert verdict.ok, verdict.errors


def test_template_tolerates_config_endpoint_false_hit():
    """误命中容错：同域配置接口（GetPageGlobalModules）响应上模板/启发式均返回 None，
    不产出错配方也不炸管线（回退 T1 是既定行为）。"""
    entry = next(e for e in _network() if "GetPageGlobalModules" in e["url"])
    beisen = next(t for t in fingerprint._TEMPLATES if t.key == "beisen")
    hit = fingerprint.FingerprintHit(
        template=beisen, score=6, matched_url=entry["url"],
        response_body=entry["response_body"], method="GET", request_body="",
    )
    data = json.loads(entry["response_body"])
    assert fingerprint.build_from_template(hit, data, PAGE_URL) is None
    assert heuristics.build_recipe(entry["url"], "GET", data, PAGE_URL) is None


# ── 全链路：采样提交 → 指纹路径发布 → 运行时重放 ────────────────────


def test_beisen_pipeline_publishes_and_runtime_replays(auth_client, db, monkeypatch):
    net = _network()
    token = auth_client.post("/api/samples/intents").json()["token"]
    resp = auth_client.post(
        "/api/samples/submit",
        json={"token": token, "url": PAGE_URL, "dom": GOLDEN["sample"]["dom"],
              "resources": [], "network": net},
    )
    assert resp.status_code == 200, resp.text
    row = _wait_pipeline(auth_client, resp.json()["id"])
    assert row["pipeline_status"] == "published", row["pipeline_note"]

    sample = db.get(Sample, resp.json()["id"])
    portal = db.get(Portal, sample.portal_id)
    assert portal.provider_key == "json_adapter"  # 指纹路径 = L1 模板实例，零 LLM
    assert portal.enabled and portal.domains == ["hkaco.zhiye.com"]
    # 门户以 BGlobal tenantInfo.Abbreviation 命名（可读品牌名，而非裸域名）
    assert portal.name == "虹科" and portal.company == "虹科"
    cfg = portal.config
    assert cfg["list_url"] == LIST_URL and cfg["list_method"] == "POST"
    assert cfg["list_body"] == {}  # 空 JSON体原样保留（类型不变）
    assert cfg["list_json_path"] == "Data.*.Submissions.*.Datas"
    assert cfg["fields"] == {
        "id": "ApplyId", "job_title": "JobAdTitle",
        "status_raw": "DeliveryStatus", "applied_at": "DeliveryDate",
    }
    assert cfg["status_map"] == []  # 中文状态运行期归一化
    recipe = db.scalar(select(Recipe).where(Recipe.portal_id == portal.id))
    assert recipe.source == "fingerprint"

    # 运行时重放：照发布配置 POST 接口（桩返回 golden 信封）→ 分组列表提取出投递
    served = json.loads(_delivery_entry(net)["response_body"])
    captured: dict = {}

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = _delivery_entry(net)["response_body"]

        def json(self):
            return served

    def _fake_request(method, url, **kw):
        captured.clear()
        captured.update(kw, method=method, url=url)
        return _FakeResp()

    monkeypatch.setattr("app.adapters.json_adapter.httpx.request", _fake_request)
    records = JSONAPIAdapter().fetch(cfg, AdapterContext(cookies={"__beisen": "sess"}))
    assert len(records) == 1
    r = records[0]
    assert r.job_title == "解决方案工程师-软件方向"
    assert r.status_raw == "简历初筛"
    assert str(r.applied_at) == "2026-08-25"
    assert r.portal_key == APPLY_ID
    # 重放形态与采样契约一致：空 JSON 体 {} 必须作为 body 发送
    #（{} 是 falsy，`or None` 写法会丢 body/content-type → 真实站点 415）
    assert captured["method"] == "POST" and captured["url"] == LIST_URL
    assert captured["json"] == {}
