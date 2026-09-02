"""飞书招聘平台模板测试：形状识别 → 模板实例化发布 → 运行时提取，外加防误报。

采样数据直接取自 scripts/mock_feishu_portal 的接口返回（TestClient 拉取），
而 Mock 按 2026-09-01 对真实站点的登录态实测契约 1:1 复刻
（delivery_list / job_post_info.title / operation_list 状态时间线），
保证「真实契约 ⇄ Mock ⇄ 指纹模板 ⇄ 实例化 ⇄ 运行时重放」五者一致。
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters import AdapterContext
from app.adapters.json_adapter import JSONAPIAdapter
from app.db.models import Portal, Recipe, Sample
from app.llm import fingerprint
from scripts.mock_feishu_portal import app as feishu_app
from scripts.mock_feishu_portal import WEBSITE_PATH

HOST = "127.0.0.1:8902"
PAGE_URL = f"http://{HOST}/{WEBSITE_PATH}/position/application"
LIST_URL = f"http://{HOST}/api/v1/search/user/applications"


def _feishu_network() -> list[dict]:
    """模拟插件主动探测在真实站上拿到的请求-响应对（POST + 分页体）。"""
    with TestClient(feishu_app) as client:
        client.post("/do-login")
        token = client.post("/api/v1/csrf/token").json()["data"]["token"]
        resp = client.post(
            "/api/v1/search/user/applications",
            headers={
                "x-csrf-token": token,
                "website-path": WEBSITE_PATH,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.text
    return [{
        "url": LIST_URL,
        "method": "POST",
        "params": {},
        "request_body": json.dumps({"page_no": 1, "page_size": 20}),
        "response_body": body,
    }]


def _submit(auth_client, url, dom, network):
    token = auth_client.post("/api/samples/intents").json()["token"]
    resp = auth_client.post(
        "/api/samples/submit",
        json={"token": token, "url": url, "dom": dom, "resources": [], "network": network},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _wait_pipeline(auth_client, sample_id, timeout=10.0):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        mine = auth_client.get("/api/samples/mine").json()
        row = next((m for m in mine if m["id"] == sample_id), None)
        if row and row["pipeline_status"] in ("published", "failed"):
            return row
        time.sleep(0.2)
    raise AssertionError(f"管线超时 sample={sample_id}")


def test_fingerprint_matches_feishu_shape():
    hit = fingerprint.match(_feishu_network())
    assert hit is not None and hit.template.key == "feishu"
    assert hit.method == "POST"
    assert fingerprint.is_instantiable(hit) is None  # 无用户特有查询参数


def test_fingerprint_no_false_positive_on_other_shapes():
    # 腾讯形状（自研）：不得误认成飞书
    from pathlib import Path

    golden = json.loads((Path(__file__).parent / "golden_samples" / "tencent_like.json").read_text(encoding="utf-8"))
    hits = [t.key for t in fingerprint._TEMPLATES if fingerprint.match(golden["sample"]["network"])]
    assert hits == []

    # Moka 形状：认成 moka 而不是飞书
    moka = [{
        "url": "https://hr.example-corp.com/api/outer/candidate/applications?page=1",
        "method": "GET",
        "response_body": json.dumps(
            {"data": {"list": [{"applyId": "a1", "positionName": "后端", "statusText": "评估中"}]}}
        ),
    }]
    hit = fingerprint.match(moka)
    assert hit is not None and hit.template.key == "moka"


def test_template_mapping_extracts_real_shape_and_declares_unknown_codes():
    """模板真实映射：嵌套岗位名 / 数组末项状态码 / 字符串毫秒时间戳；未知码显式留给兜底。"""
    net = _feishu_network()
    hit = fingerprint.match(net)
    data = json.loads(net[0]["response_body"])
    out = fingerprint.build_from_template(hit, data, PAGE_URL, request_body=net[0]["request_body"])
    assert out is not None
    recs = out.observations
    assert [r.job_title for r in recs] == ["服务端开发工程师", "算法工程师", "产品培训生"]
    assert [r.status_raw for r in recs] == ["3", "1", "3"]  # operation_list 末项 code

    recipe = out.recipe
    assert recipe.list_source.list_json_path == "data.delivery_list"
    assert recipe.field_map["job_title"].json_path == "job_post_info.title"
    assert recipe.field_map["status_raw"].json_path == "operation_list.-1.operation_code"
    assert out.unmapped_status_texts == []  # 0/1/3 均在码表内

    # 未知码（如 7=终态类）不在码表 → 必须显式声明留给兜底，验证器断言 3 才放行
    mutated = json.loads(net[0]["response_body"])
    mutated["data"]["delivery_list"][0]["operation_list"].append(
        {"operation_code": 7, "biz_create_time": 1788000000000}
    )
    out2 = fingerprint.build_from_template(hit, mutated, PAGE_URL, request_body=net[0]["request_body"])
    assert out2 is not None and out2.unmapped_status_texts == ["7"]

    # 字符串毫秒时间戳 → 日期（与真实站点页面显示的投递日期一致）
    from app.adapters.fields import parse_date

    assert str(parse_date("1786081301858")) == "2026-08-07"


def test_feishu_pipeline_publishes_and_runtime_extracts(auth_client, db, monkeypatch):
    network = _feishu_network()
    sid = _submit(
        auth_client,
        url=PAGE_URL,
        dom="<html><body><div class='application-list'><div class='apply-item'>岗位</div></div></body></html>",
        network=network,
    )
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published", row["pipeline_note"]

    sample = db.get(Sample, sid)
    portal = db.get(Portal, sample.portal_id)
    assert portal.provider_key == "json_adapter"  # 指纹路径 = L1 模板实例，零 LLM
    assert portal.enabled and portal.config["list_url"] == LIST_URL
    assert portal.config["list_method"] == "POST"
    assert portal.config["list_body"] == {"page_no": 1, "page_size": 20}  # 采样原样，类型不变
    headers = portal.config["list_headers"]
    assert headers["x-csrf-token"] == "${cookie:atsx-csrf-token}"  # 运行时从 Cookie 派生
    assert headers["website-path"] == WEBSITE_PATH  # 实例化时从页面 URL 首段解析
    assert portal.config["list_json_path"] == "data.delivery_list"
    assert portal.config["fields"]["job_title"] == "job_post_info.title"
    assert portal.config["fields"]["status_raw"] == "operation_list.-1.operation_code"
    assert portal.config["fields"]["applied_at"] == "biz_create_time"
    assert "not login" in portal.config["session_invalid_markers"]
    smap = {(e["pattern"], e["status"]) for e in portal.config["status_map"]}
    assert ("^0$", "applied") in smap and ("^3$", "written_test") in smap

    recipe = db.scalar(select(Recipe).where(Recipe.portal_id == portal.id))
    assert recipe.source == "fingerprint"

    # 运行时：照发布的配置重放 POST 接口（桩 HTTP 返回同一个信封）→ 头部按 Cookie 派生 → 提取出投递
    served = json.loads(network[0]["response_body"])
    captured: dict = {}

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = network[0]["response_body"]

        def json(self):
            return served

    def _fake_request(method, url, **kw):
        captured.clear()
        captured.update(kw, method=method, url=url)
        return _FakeResp()

    monkeypatch.setattr("app.adapters.json_adapter.httpx.request", _fake_request)
    # atsx-csrf-token 在 Chromium cookies API 里是 URL 编码形式（尾部 %3D），
    # 运行时必须解码后发送（真实站实测：不解码 → 服务端 405 空 body）
    records = JSONAPIAdapter().fetch(
        portal.config, AdapterContext(cookies={"session_id": "x", "atsx-csrf-token": "tok-1%3D"})
    )
    assert len(records) == 3
    assert records[0].job_title == "服务端开发工程师"
    assert records[0].status_raw == "3"
    assert records[0].work_location == "北京"
    assert records[0].applied_at is not None and str(records[0].applied_at) == "2026-08-07"
    assert records[0].portal_key == "8001"
    # 重放的调用形态必须与真实契约一致
    assert captured["method"] == "POST" and captured["url"] == LIST_URL
    assert captured["json"] == {"page_no": 1, "page_size": 20}
    assert captured["headers"]["x-csrf-token"] == "tok-1="  # %3D 已解码
    assert captured["headers"]["website-path"] == WEBSITE_PATH
    assert captured["headers"]["X-Requested-With"] == "XMLHttpRequest"


def test_feishu_login_expiry_detected(monkeypatch):
    """未登录（401 + not login 信封）→ 判登录态失效，看板黄条引导重登。"""
    import pytest

    from app.adapters import SessionInvalidError

    class _FakeResp:
        status_code = 401
        headers = {"content-type": "application/json"}
        text = '{"code": 99991663, "msg": "not login"}'

        def json(self):
            return {"code": 99991663, "msg": "not login"}

    monkeypatch.setattr("app.adapters.json_adapter.httpx.request", lambda *a, **kw: _FakeResp())
    config = {
        "list_url": LIST_URL,
        "list_method": "POST",
        "list_headers": {"x-csrf-token": "${cookie:atsx-csrf-token}"},
        "session_invalid_markers": ["not login"],
        "list_json_path": "data.delivery_list",
        "fields": {"job_title": "job_post_info.title", "status_raw": "operation_list.-1.operation_code"},
    }
    with pytest.raises(SessionInvalidError):
        JSONAPIAdapter().fetch(config, AdapterContext(cookies={}))


def test_feishu_custom_domain_uses_tenant_name(auth_client, db):
    """真实小米站（2026-09-01 实测）形状：自定义域名 + 内嵌 js-websiteInfo 租户名。

    xiaomi.jobs.f.mioffice.cn 不带飞书域名，靠接口路径 + 响应键结构命中指纹；
    插件同时提交页面内嵌的 text/json 块（tenant_info.tenant_name=小米科技），
    发布的门户应以租户名而非裸域名命名（看板公司列可读）。
    """
    network = _feishu_network()
    # 把 URL 换成自定义域名站（小米形状：website-path=campus）
    network[0]["url"] = "https://xiaomi.jobs.f.mioffice.cn/api/v1/search/user/applications"
    network.append({
        "url": "https://xiaomi.jobs.f.mioffice.cn/campus/position/application#embedded-js-websiteInfo",
        "method": "GET", "params": {}, "request_body": "",
        "response_body": json.dumps({
            "tenant_info": {"tenant_id_md5": "fb3a", "tenant_name": "小米科技"},
            "website_info": {"id": "7", "name": {"zh_cn": "常规校招官网"}, "path": "campus"},
        }),
    })
    sid = _submit(
        auth_client,
        url="https://xiaomi.jobs.f.mioffice.cn/campus/position/application",
        dom="<html><body><div class='application-list'></div></body></html>",
        network=network,
    )
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published", row["pipeline_note"]

    sample = db.get(Sample, sid)
    portal = db.get(Portal, sample.portal_id)
    assert portal.provider_key == "json_adapter"
    assert portal.domains == ["xiaomi.jobs.f.mioffice.cn"]
    # 租户名命名（无内嵌块时回退 host / eTLD+1）
    assert portal.name == "小米科技"
    assert portal.company == "小米科技"
    assert portal.config["list_headers"]["website-path"] == "campus"


def test_feishu_csrf_rotation_self_heal():
    """绑定时存的 atsx-csrf-token 被站点轮换 → 405 → 匿名刷新拿新值重试成功。

    2026-09-01 小米站实测形态：7 天 CSRF cookie 会被轮换，旧值 405 空体；
    POST /api/v1/csrf/token 匿名可用并种新 Cookie。自愈后新值经
    ctx.refreshed_cookies 回写绑定存储，下轮轮询不再触发 405。
    """
    import json as _json

    network = _feishu_network()
    served = _json.loads(network[0]["response_body"])

    calls: list[tuple] = []

    class _Resp:
        def __init__(self, status_code, text="", cookies=None):
            self.status_code = status_code
            self.text = text
            self.headers = {"content-type": "application/json"}
            self.cookies = cookies or {}

        def json(self):
            return _json.loads(self.text)

    def _fake_request(method, url, **kw):
        calls.append((method, url, dict(kw.get("cookies") or {})))
        if url.endswith("/api/v1/csrf/token"):
            return _Resp(200, '{"code":0,"data":{"token":"fresh-tok%3D"}}',
                         cookies={"atsx-csrf-token": "fresh-tok%3D"})
        sent_token = (kw.get("headers") or {}).get("x-csrf-token")
        if sent_token == "stale-tok=":
            return _Resp(405, "")  # 旧 token：405 空体
        assert sent_token == "fresh-tok=", f"重试应携带刷新后的 token，得到 {sent_token!r}"
        return _Resp(200, network[0]["response_body"])

    monkeypatch_fn = pytest.MonkeyPatch()
    monkeypatch_fn.setattr("app.adapters.json_adapter.httpx.request", _fake_request)
    try:
        config = {
            "list_url": LIST_URL, "list_method": "POST",
            "list_body": {"page_no": 1, "page_size": 20},
            "list_headers": {
                "content-type": "application/json",
                "x-csrf-token": "${cookie:atsx-csrf-token}",
                "website-path": WEBSITE_PATH,
            },
            "csrf_refresh": {"url": f"http://{HOST}/api/v1/csrf/token",
                              "method": "POST", "cookie_name": "atsx-csrf-token"},
            "list_json_path": "data.delivery_list",
            "fields": {
                "id": "id", "job_title": "job_post_info.title",
                "status_raw": "operation_list.-1.operation_code",
                "applied_at": "biz_create_time",
            },
            "session_invalid_markers": ["not login"],
        }
        ctx = AdapterContext(cookies={"session_id": "s", "atsx-csrf-token": "stale-tok%3D"})
        records = JSONAPIAdapter().fetch(config, ctx)
        assert len(records) == 3 and records[0].job_title == "服务端开发工程师"
        # 自愈产物可供调用方回写存储
        assert ctx.refreshed_cookies["atsx-csrf-token"] == "fresh-tok%3D"
        # 调用序列：列表(旧token 405) → 刷新 → 列表(新token 200)
        assert [c[0] for c in calls] == ["POST", "POST", "POST"]
        assert calls[0][2]["atsx-csrf-token"] == "stale-tok%3D"
        assert calls[2][2]["atsx-csrf-token"] == "fresh-tok%3D"
    finally:
        monkeypatch_fn.undo()
