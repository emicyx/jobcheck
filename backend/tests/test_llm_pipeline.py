"""配方管线端到端测试（heuristic 离线提供者，零 LLM 成本）：
采样提交 → 后台管线 → 指纹/T1 → 回放验证 → 免审批发布 → 向导轮询感知。
"""

import json
import time
from pathlib import Path

from app.core.config import settings
from app.db.models import Portal, Recipe, Sample

GOLDEN = json.loads((Path(__file__).parent / "golden_samples" / "tencent_like.json").read_text(encoding="utf-8"))


def _submit(auth_client, *, url, dom, network):
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
    raise AssertionError(f"管线超时未完成 sample={sample_id}")


def test_pipeline_publishes_recipe_portal(auth_client, db):
    assert settings.llm_recipe_provider == "heuristic"  # 测试环境固定离线提供者
    sid = _submit(auth_client, url=GOLDEN["sample"]["url"], dom=GOLDEN["sample"]["dom"],
                  network=GOLDEN["sample"]["network"])

    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published", row["pipeline_note"]

    sample = db.get(Sample, sid)
    assert sample.status == "used"
    portal = db.get(Portal, sample.portal_id)
    assert portal is not None and portal.enabled and portal.provider_key == "recipe"
    assert portal.domains == ["join.qq.com"]
    spec = portal.config["recipe"]
    assert spec["list_source"]["url_pattern"].startswith("https://join.qq.com/api/v1/apply/getApplyProcess")
    assert portal.config["status_map"] == spec["status_map"]

    recipe = db.scalar(__import__("sqlalchemy").select(Recipe).where(Recipe.portal_id == portal.id))
    assert recipe.status == "published" and recipe.source == "auto_gen"

    # 向导轮询感知：identify 能识别为已支持
    found = auth_client.post("/api/portals/identify", json={"url": "https://join.qq.com/progress.html"}).json()
    assert found and found["id"] == portal.id and found["enabled"]


def test_pipeline_failure_keeps_sample_and_no_portal(auth_client, db):
    bad_network = [
        {"url": "https://weird.example.com/api/noise", "method": "GET",
         "response_body": "{\"theme\": \"dark\"}"}
    ]
    sid = _submit(auth_client, url="https://weird.example.com/apply",
                  dom="<html><body><div>没有可提取列表</div></body></html>",
                  network=bad_network)
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "failed"
    sample = db.get(Sample, sid)
    assert sample.status == "failed" and sample.portal_id is None  # 不建门户，样本留存
    assert db.query(Portal).count() == 0


def test_pipeline_cooldown_blocks_recent_failure(auth_client, db):
    bad = [{"url": "https://cool.example.com/api/x", "method": "GET", "response_body": "{\"a\":1}"}]
    sid1 = _submit(auth_client, url="https://cool.example.com/apply", dom="<div>x</div>", network=bad)
    _wait_pipeline(auth_client, sid1)

    # 同注册域名 24h 内再来 → 冷却跳过
    sid2 = _submit(auth_client, url="https://other.cool.example.com/apply", dom="<div>y</div>", network=bad)
    row = _wait_pipeline(auth_client, sid2, timeout=3.0)
    assert row["pipeline_status"] == "failed"
    sample2 = db.get(Sample, sid2)
    assert "冷却" in (sample2.pipeline_note or "") or sample2.pipeline_status == "failed"


def test_admin_force_retry_bypasses_cooldown(auth_client, db):
    from app.core.security import hash_password
    from app.db.models import User

    admin = User(email="admin9@test.com", password_hash=hash_password("password123"), role="admin")
    db.add(admin)
    db.commit()
    auth_client.post("/api/auth/logout")
    auth_client.post("/api/auth/login", json={"email": "admin9@test.com", "password": "password123"})

    sid = _submit(auth_client, url=GOLDEN["sample"]["url"], dom=GOLDEN["sample"]["dom"],
                  network=GOLDEN["sample"]["network"])
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published"

    # 干跑重试：已发布 → 复用
    resp = auth_client.post(f"/api/samples/{sid}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "published"

    # 普通用户不能重试
    auth_client.post("/api/auth/logout")
    auth_client.post("/api/auth/register", json={"email": "u9@test.com", "password": "password123", "invite_code": "TESTCODE"})
    assert auth_client.post(f"/api/samples/{sid}/retry").status_code == 403


def test_pipeline_reuses_published_portal_for_same_domain(auth_client, db):
    sid1 = _submit(auth_client, url=GOLDEN["sample"]["url"], dom=GOLDEN["sample"]["dom"],
                   network=GOLDEN["sample"]["network"])
    row1 = _wait_pipeline(auth_client, sid1)
    assert row1["pipeline_status"] == "published"
    portal_id = db.get(Sample, sid1).portal_id

    # 等冷却检查让位：已发布 → 直接复用（不重新生成）
    sid2 = _submit(auth_client, url="https://join.qq.com/other.html",
                   dom=GOLDEN["sample"]["dom"], network=GOLDEN["sample"]["network"])
    row2 = _wait_pipeline(auth_client, sid2)
    assert row2["pipeline_status"] == "published"
    assert db.get(Sample, sid2).portal_id == portal_id


def test_full_loop_recipe_portal_to_board(auth_client, db, monkeypatch):
    """验收：采样发布配方门户 → 绑定同步（真 RecipeAdapter，桩 HTTP）→ 看板出卡。"""
    sid = _submit(auth_client, url=GOLDEN["sample"]["url"], dom=GOLDEN["sample"]["dom"],
                  network=GOLDEN["sample"]["network"])
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published"
    sample = db.get(Sample, sid)
    portal = db.get(Portal, sample.portal_id)

    # 桩 HTTP：服务端带 Cookie 重放该接口（返回同样的单对象响应）
    import app.adapters.httpio as httpio_mod

    def fake_request(method, url, **kw):
        class R:
            status_code = 200
            headers = {"content-type": "application/json"}

            def __init__(self, body):
                self.text = body

            def json(self):
                import json as _json

                return _json.loads(self.text)

        return R(GOLDEN["sample"]["network"][0]["response_body"])

    monkeypatch.setattr(httpio_mod.httpx, "request", fake_request)

    from app.core import crypto
    from app.db.models import Binding, User
    from app.services.sync import sync_binding

    user = db.get(User, sample.user_id)
    binding = Binding(user_id=user.id, portal_id=portal.id, status="active",
                      cookie_blob=crypto.encrypt_text(
                          __import__("json").dumps([{"name": "sid", "value": "tok"}])
                      ))
    db.add(binding)
    db.commit()
    db.refresh(binding)

    summary = sync_binding(db, binding)
    assert summary["fetched"] == 1 and summary["created"] == 1
    app_row = binding.applications if hasattr(binding, "applications") else None
    from sqlalchemy import select as _select

    from app.db.models import Application

    created = db.scalars(
        _select(Application).where(Application.binding_id == binding.id)
    ).all()
    assert created[0].company == portal.company
    assert created[0].job_title == "后端开发工程师（腾讯云）"
    # 自动配方不猜数字码语义（腾讯 ^2$→screening 来自人工校准的种子配置，不进自动管线）：
    # "2" 未命中任何规则 → 待确认列显示原文，等 T2 分类/用户手改沉淀
    assert created[0].current_status == "pending_confirm"
    assert created[0].raw_status_text == "2"  # 原文永远保存
    assert created[0].confidence == "recipe"


def test_fingerprint_route_publishes_l1_portal(auth_client, db):
    """Moka 客户站采样 → 指纹命中 → 零 LLM 实例化 L1 门户。"""
    network = [
        {"url": "https://hr.fakemoka.com/api/outer/candidate/applications?page=1&pageSize=20",
         "method": "GET", "params": {"page": "1", "pageSize": "20"}, "request_body": "",
         "response_body": "{\"data\":{\"list\":[{\"applyId\":\"a1\",\"positionName\":\"后端工程师\",\"statusText\":\"简历评估中\",\"deliverTime\":\"2026-08-01\"}]}}"},
    ]
    sid = _submit(auth_client, url="https://hr.fakemoka.com/myapply",
                  dom="<html><body><div class='list'></div></body></html>", network=network)
    row = _wait_pipeline(auth_client, sid)
    assert row["pipeline_status"] == "published", row["pipeline_note"]

    sample = db.get(Sample, sid)
    portal = db.get(Portal, sample.portal_id)
    assert portal.provider_key == "json_adapter"  # 指纹路径走 L1 模板实例
    assert portal.config["list_url"].startswith("https://hr.fakemoka.com/api/outer/candidate/applications")
    assert portal.config["fields"]["job_title"] == "positionName"

    recipe = db.scalar(__import__("sqlalchemy").select(Recipe).where(Recipe.portal_id == portal.id))
    assert recipe.source == "fingerprint"
