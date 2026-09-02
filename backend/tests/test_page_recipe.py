"""SSR 自研站（记录直出、无列表 XHR）→ page 型配方全链路测试。

2026-09 用户反馈「自研站采样失败：未找到可提取的投递列表数据」的主形态：
数据内嵌在页面可执行 JS（window.__INITIAL_STATE__ = {...}）里。
覆盖：插件 v0.4.13 形状的 #embedded-js 条目 → 管线发布 page 型配方 →
运行时 GET 页面重放提取 → 改版/锚丢失时明确报错（不静默给错数据）。
"""

import json
import time

import pytest

from app.adapters import AdapterContext, AdapterError
from app.adapters.recipe_adapter import RecipeAdapter
from app.db.models import Portal, Sample

PAGE_URL = "https://ssr.example.cn/myapply"

INITIAL_STATE = {
    "user": {"name": "张三"},
    "applyPage": {"records": [
        {"positionName": "后端开发工程师", "applyStatusText": "简历评估中",
         "deliverTime": "2026-08-01", "applyId": "A1"},
        {"positionName": "测试开发工程师", "applyStatusText": "笔试中",
         "deliverTime": "2026-08-10", "applyId": "A2"},
    ]},
}

PAGE_HTML = (
    '<!doctype html><html><head><title>应聘记录</title></head><body><div id="app"></div>\n'
    f'<script>window.__INITIAL_STATE__ = {json.dumps(INITIAL_STATE, ensure_ascii=False)};</script>\n'
    '<script src="https://cdn.example.cn/app.js"></script>\n'
    "</body></html>"
)


def _submit(auth_client, *, network, dom="<html><body><div>应聘记录</div></body></html>", url=PAGE_URL):
    token = auth_client.post("/api/samples/intents").json()["token"]
    resp = auth_client.post(
        "/api/samples/submit",
        json={"token": token, "url": url, "dom": dom, "resources": [], "network": network},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _wait_pipeline(auth_client, sample_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        mine = auth_client.get("/api/samples/mine").json()
        row = next((m for m in mine if m["id"] == sample_id), None)
        if row and row["pipeline_status"] in ("published", "failed"):
            return row
        time.sleep(0.2)
    raise AssertionError(f"管线超时 sample={sample_id}")


def _embedded_entry():
    """插件 v0.4.13 对可执行 JS 内嵌数据的捕获形状。"""
    return [{
        "url": PAGE_URL + "#embedded-js-__INITIAL_STATE__",
        "method": "GET", "params": {}, "request_body": "",
        "response_body": json.dumps(INITIAL_STATE, ensure_ascii=False),
    }]


def test_ssr_page_recipe_full_loop(auth_client, db, monkeypatch):
    sid = _submit(auth_client, network=_embedded_entry())
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published", row["pipeline_note"]

    sample = db.get(Sample, sid)
    portal = db.get(Portal, sample.portal_id)
    assert portal.provider_key == "recipe"
    src = portal.config["recipe"]["list_source"]
    assert src["type"] == "page"
    assert src["page_url"] == PAGE_URL
    assert src["data_anchor"] == "__INITIAL_STATE__"
    # applyPage.records 不在固定候选路径里：由通用递归扫描定位
    assert src["list_json_path"] == "applyPage.records"

    # 运行时：GET 页面（桩返回同款 SSR HTML）→ 按锚提取内嵌数据
    captured: dict = {}

    class _FakeResp:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = PAGE_HTML

        def json(self):
            raise TypeError("not json")

    def _fake_get(url, **kw):
        captured.clear()
        captured.update(kw, url=url)
        return _FakeResp()

    monkeypatch.setattr("app.adapters.recipe_adapter.httpx.get", _fake_get)
    records = RecipeAdapter().fetch(portal.config, AdapterContext(cookies={"session": "tok"}))
    assert [r.job_title for r in records] == ["后端开发工程师", "测试开发工程师"]
    assert records[0].status_raw == "简历评估中"
    assert records[0].applied_at is not None and str(records[0].applied_at) == "2026-08-01"
    assert records[0].portal_key == "A1"
    assert captured["url"] == PAGE_URL

    # 网站改版 / 锚丢失 → 明确报 AdapterError（不静默给错数据）
    class _ChangedResp(_FakeResp):
        text = "<html><body>改版了</body></html>"

    monkeypatch.setattr(
        "app.adapters.recipe_adapter.httpx.get", lambda url, **kw: _ChangedResp()
    )
    with pytest.raises(AdapterError):
        RecipeAdapter().fetch(portal.config, AdapterContext(cookies={"session": "tok"}))


def test_page_recipe_login_redirect_marks_session_invalid(monkeypatch, auth_client, db):
    sid = _submit(auth_client, network=_embedded_entry())
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published", row["pipeline_note"]
    portal = db.get(Portal, db.get(Sample, sid).portal_id)

    from app.adapters import SessionInvalidError

    class _LoginResp:
        status_code = 302
        headers = {"location": "https://ssr.example.cn/login?next=/myapply"}
        text = ""

    monkeypatch.setattr(
        "app.adapters.recipe_adapter.httpx.get", lambda url, **kw: _LoginResp()
    )
    with pytest.raises(SessionInvalidError):
        RecipeAdapter().fetch(portal.config, AdapterContext(cookies={"session": "expired"}))


def test_validator_rejects_wrong_anchor():
    """数据锚对不上采样内嵌块 → 断言 1 拦下，宁缺毋错。"""
    from app.llm import heuristics, validator

    data = json.loads(_embedded_entry()[0]["response_body"])
    output = heuristics.build_page_recipe(_embedded_entry()[0]["url"], data, PAGE_URL)
    assert output is not None
    output.recipe.list_source.data_anchor = "not_exist_anchor"
    verdict = validator.replay(output, PAGE_URL, "<html><body></body></html>", _embedded_entry())
    assert not verdict.ok
    assert "数据锚" in verdict.errors[0]


def test_embedded_extraction_mirrors_extension_semantics():
    """python 侧内嵌解析与扩展捕获语义一致：window.__X__ 赋值 + JSON 型 script。"""
    from app.llm.embedded import find_embedded, iter_embedded

    site_json = json.dumps({"aaa": [{"b": 1, "pad": "x" * 120}]}, ensure_ascii=False)
    page_data = json.dumps(
        {"apply": {"rows": [{"x": 1, "pad": "y" * 160}]}}, ensure_ascii=False
    )
    html = (
        "<html><body>"
        f'<script type="application/json" id="js-site">{site_json}</script>'
        '<script>var noise = "small";</script>'
        f"<script>window.__PAGE_DATA__ = {page_data};</script>"
        "</body></html>"
    )
    anchors = [a for a, _ in iter_embedded(html)]
    assert anchors == ["js-site", "__PAGE_DATA__"]
    _, rows = find_embedded(html, "__PAGE_DATA__", "apply.rows")
    assert rows and rows[0]["x"] == 1
    obj, lst = find_embedded(html, "", "aaa")
    assert lst and lst[0]["b"] == 1 and obj == {"aaa": [{"b": 1, "pad": "x" * 120}]}
