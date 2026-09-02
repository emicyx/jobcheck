"""结构指纹与 recipe 运行时适配器测试。"""

import json

import pytest

from app.adapters import AdapterContext, SessionInvalidError
from app.adapters.recipe_adapter import RecipeAdapter
from app.llm import fingerprint


def test_fingerprint_matches_moka_like_sample():
    network = [
        {
            "url": "https://hr.example-corp.com/api/outer/candidate/applications?page=1",
            "method": "GET",
            "response_body": json.dumps(
                {"data": {"list": [{"applyId": "a1", "positionName": "后端", "statusText": "评估中"}]}}
            ),
        },
    ]
    hit = fingerprint.match(network)
    assert hit is not None and hit.template.key == "moka"
    assert fingerprint.is_instantiable(hit) is None  # 只有公共分页参数，可实例化


def test_fingerprint_no_match_for_self_built_site():
    network = [
        {"url": "https://join.qq.com/api/v1/apply/getApplyProcess", "method": "GET",
         "response_body": "{\"data\": {\"positionInfo\": {\"applyPositionTxt\": \"x\"}}}"},
    ]
    assert fingerprint.match(network) is None


def test_fingerprint_blocks_user_specific_query():
    network = [
        {"url": "https://app.mokahr.com/api/outer/x?resumeId=123456", "method": "GET",
         "response_body": "{\"data\": {\"list\": []}}"},
    ]
    hit = fingerprint.match(network)
    assert hit is not None
    blocker = fingerprint.is_instantiable(hit)
    assert blocker is not None and "resumeId" in blocker


# ── RecipeAdapter 运行时（离线：桩替换 httpio 请求层）────────────────


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = text or json.dumps(json_data if json_data is not None else {})

    def json(self):
        if self._json is None:
            raise json.JSONDecodeError("no json", "", 0)
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=None, response=None)


def _spec_config(url_pattern, fields, *, list_path="data.list", status_map=None, runtime=None, query=None):
    return {
        "recipe": {
            "auth": {
                "login_success": {"url_contains": ["apply"]},
                "session_invalid": {"url_contains": ["login"], "status_code": []},
            },
            "list_source": {
                "type": "xhr",
                "url_pattern": url_pattern,
                "method": "GET",
                "list_json_path": list_path,
                "query": query or {},
                "pagination": {"type": "none"},
            },
            "field_map": {k: {"json_path": v} for k, v in fields.items()},
            "status_map": status_map or [],
            "runtime_params": runtime or {},
            "meta": {"generated_by": "test"},
        }
    }


def test_recipe_adapter_extracts_records(monkeypatch):
    served = {"data": {"list": [
        {"applyId": "1001", "positionName": "后端开发", "statusText": "简历评估中", "deliverTime": "2026-08-20 10:30:00"},
        {"applyId": "1002", "positionName": "数据分析", "statusText": "已终止"},
    ]}}
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url, kw))
        return _FakeResponse(json_data=served)

    monkeypatch.setattr("app.adapters.httpio.httpx.request", fake_request)
    config = _spec_config(
        "https://x.example.com/api/apply/list*",
        {"id": "applyId", "job_title": "positionName", "status_raw": "statusText", "applied_at": "deliverTime"},
    )
    records = RecipeAdapter().fetch(config, AdapterContext(cookies={"sid": "1"}))
    assert len(records) == 2
    assert records[0].job_title == "后端开发" and records[0].status_raw == "简历评估中"
    assert str(records[0].applied_at) == "2026-08-20"
    assert calls[0][1] == "https://x.example.com/api/apply/list"  # * 通配段已剥除
    assert calls[0][2]["cookies"] == {"sid": "1"}


def test_recipe_adapter_session_invalid_on_401(monkeypatch):
    import httpx

    def fake_request(method, url, **kw):
        return _FakeResponse(status_code=401)

    monkeypatch.setattr("app.adapters.httpio.httpx.request", fake_request)
    config = _spec_config("https://x.example.com/api/list", {"job_title": "n", "status_raw": "s"})
    with pytest.raises(SessionInvalidError):
        RecipeAdapter().fetch(config, AdapterContext(cookies={}))


def test_recipe_adapter_runtime_param_resolution(monkeypatch):
    me_served = {"data": {"uid": 99887766}}
    list_served = {"data": {"list": [{"positionName": "后端", "statusText": "评估中"}]}}
    urls = []

    def fake_request(method, url, **kw):
        urls.append(url)
        return _FakeResponse(json_data=me_served if "/api/me" in url else list_served)

    monkeypatch.setattr("app.adapters.httpio.httpx.request", fake_request)

    # 前置接口解析：先 GET /api/me 取 uid，再代入列表 URL
    config = _spec_config(
        "https://x.example.com/api/u/{{user_id}}/list",
        {"job_title": "positionName", "status_raw": "statusText"},
        runtime={"user_id": {"type": "xhr_json", "url_pattern": "https://x.example.com/api/me*",
                             "json_path": "data.uid"}},
    )
    records = RecipeAdapter().fetch(config, AdapterContext(cookies={"sid": "1"}))
    assert records[0].job_title == "后端"
    assert urls[0].startswith("https://x.example.com/api/me")
    assert "99887766" in urls[1]

    # cookie 解析型
    config2 = _spec_config(
        "https://x.example.com/api/u/{{user_id}}/list",
        {"job_title": "positionName", "status_raw": "statusText"},
        runtime={"user_id": {"type": "cookie", "name": "jc_uid"}},
    )
    records = RecipeAdapter().fetch(config2, AdapterContext(cookies={"jc_uid": "42"}))
    assert records[0].job_title == "后端"

    # 缺 Cookie → 登录态失效（引导重登，而非神秘失败）
    with pytest.raises(SessionInvalidError):
        RecipeAdapter().fetch(config2, AdapterContext(cookies={}))


def test_recipe_adapter_pagination(monkeypatch):
    pages = {
        "1": {"data": {"list": [{"positionName": "A", "statusText": "评估中"}]}},
        "2": {"data": {"list": [{"positionName": "B", "statusText": "笔试"}]}},
        "3": {"data": {"list": []}},
    }

    def fake_request(method, url, **kw):
        page = kw.get("params", {}).get("page", "1")
        return _FakeResponse(json_data=pages.get(page, {"data": {"list": []}}))

    monkeypatch.setattr("app.adapters.httpio.httpx.request", fake_request)
    config = _spec_config("https://x.example.com/api/list", {"job_title": "positionName", "status_raw": "statusText"})
    config["recipe"]["list_source"]["pagination"] = {"type": "page_param", "page_param": "page", "max_pages": 5}
    records = RecipeAdapter().fetch(config, AdapterContext(cookies={}))
    assert [r.job_title for r in records] == ["A", "B"]


def test_recipe_adapter_rejects_dom_recipe():
    config = {
        "recipe": {
            "auth": {},
            "list_source": {"type": "dom", "page_url": "https://x.com/apply",
                            "wait_for_selector": ".list", "item_selector": ".card"},
            "field_map": {"job_title": {"selector": ".t", "attr": "text"},
                          "status_raw": {"selector": ".s", "attr": "text"}},
        }
    }
    from app.adapters import AdapterError

    with pytest.raises(AdapterError, match="dom"):
        RecipeAdapter().fetch(config, AdapterContext(cookies={}))
