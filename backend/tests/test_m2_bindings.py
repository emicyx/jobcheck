from datetime import date

import pytest

from app.adapters import AdapterError, BaseAdapter, RawApplication, SessionInvalidError
from app.core import crypto
from app.db.models import Portal
from app.domain.normalize import normalize_status


def test_crypto_roundtrip():
    blob = crypto.encrypt_text('{"cookies": [{"name": "a", "value": "b"}]}')
    assert isinstance(blob, bytes)
    assert crypto.decrypt_text(blob) == '{"cookies": [{"name": "a", "value": "b"}]}'
    with pytest.raises(ValueError):
        crypto.decrypt_text(blob[:-3])  # 篡改后 GCM 校验失败


def test_normalize_priority_and_fallback():
    portal_map = [{"pattern": "评估中", "status": "screening"}]
    assert normalize_status("简历评估中", portal_map) == "screening"      # 门户规则
    assert normalize_status("面试安排中") == "interview_unknown"          # 通用兜底，不虚构轮次
    assert normalize_status("二面进行中") == "interview_2"
    assert normalize_status("恭喜获得offer") == "offer"
    assert normalize_status("等待神秘流程") == "pending_confirm"           # 不猜
    assert normalize_status("") == "pending_confirm"


# ── 绑定全链路（FakeAdapter，离线）──────────────────────────────

class FakeAdapter(BaseAdapter):
    def __init__(self):
        self.apps: list[RawApplication] = []
        self.invalid = False

    def fetch(self, config, ctx):
        if self.invalid:
            raise SessionInvalidError("401")
        if not ctx.cookies.get("mk_session"):
            raise SessionInvalidError("cookie 缺失")
        return list(self.apps)


@pytest.fixture()
def fake_adapter(monkeypatch):
    fake = FakeAdapter()
    import app.services.sync as sync_mod

    monkeypatch.setattr(sync_mod, "get_adapter", lambda provider_key: fake)
    return fake


@pytest.fixture()
def portal(db):
    row = Portal(
        name="Mock 演示门户",
        company="演示公司",
        provider_key="json_adapter",
        domains=["localhost:8901"],
        enabled=True,
        verified=True,
        config={
            "login_url": "http://127.0.0.1:8901/",
            "session_cookie_names": ["mk_session"],
            "status_map": [{"pattern": "简历评估", "status": "screening"}],
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_binding_full_flow(auth_client, db, portal, fake_adapter):
    fake_adapter.apps = [
        RawApplication(job_title="后端开发工程师", status_raw="简历评估中", portal_key="1001",
                       department="CSIG", applied_at=date(2026, 8, 25)),
        RawApplication(job_title="数据分析", status_raw="笔试中", portal_key="1002"),
    ]

    # 1. 发起绑定，拿到一次性 token
    resp = auth_client.post("/api/bindings", json={"portal_id": portal.id})
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    assert resp.json()["login_url"] == "http://127.0.0.1:8901/"
    binding_id = resp.json()["id"]

    # 2. 插件回传 Cookie 激活（无用户会话，凭 token）
    resp = auth_client.post(
        "/api/bindings/activate",
        json={"token": token, "cookies": [{"name": "mk_session", "value": "abc", "domain": "127.0.0.1"}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["synced"] is True
    assert resp.json()["created"] == 2

    # 3. 投递记录自动生成：source=auto，状态按门户规则归一化
    apps = auth_client.get("/api/applications").json()
    assert len(apps) == 2
    assert all(a["source"] == "auto" for a in apps)
    by_title = {a["job_title"]: a for a in apps}
    assert by_title["后端开发工程师"]["current_status"] == "screening"
    assert by_title["后端开发工程师"]["raw_status_text"] == "简历评估中"
    assert by_title["数据分析"]["current_status"] == "written_test"  # 通用兜底规则
    assert by_title["后端开发工程师"]["company"] == "演示公司"

    # 4. intent 轮询终态
    assert auth_client.get(f"/api/bindings/intents/{token}").json()["status"] == "activated"

    # 5. 门户状态变化 → 刷新 → 新增 + 历史写入
    fake_adapter.apps[1].status_raw = "一面进行中"
    fake_adapter.apps.append(RawApplication(job_title="产品经理", status_raw="已投递", portal_key="1003"))
    resp = auth_client.post(f"/api/bindings/{binding_id}/refresh")
    assert resp.status_code == 200
    assert resp.json()["created"] == 1 and resp.json()["updated"] == 1

    apps = auth_client.get("/api/applications").json()
    by_title = {a["job_title"]: a for a in apps}
    assert by_title["数据分析"]["current_status"] == "interview_1"

    detail = auth_client.get(f"/api/applications/{by_title['数据分析']['id']}").json()
    assert len(detail["history"]) == 2
    # history 按时间升序，最新一条在末尾
    assert detail["history"][-1]["from_status"] == "written_test"
    assert detail["history"][-1]["to_status"] == "interview_1"

    # 6. 删除绑定：记录保留并转手动
    assert auth_client.delete(f"/api/bindings/{binding_id}").status_code == 200
    apps = auth_client.get("/api/applications").json()
    assert len(apps) == 3
    assert all(a["source"] == "manual" for a in apps)


def test_binding_expired_and_relogin(auth_client, db, portal, fake_adapter):
    fake_adapter.apps = [RawApplication(job_title="岗位A", status_raw="已投递", portal_key="1")]

    resp = auth_client.post("/api/bindings", json={"portal_id": portal.id})
    token, binding_id = resp.json()["token"], resp.json()["id"]

    # 激活成功
    resp = auth_client.post(
        "/api/bindings/activate",
        json={"token": token, "cookies": [{"name": "mk_session", "value": "good"}]},
    )
    assert resp.status_code == 200

    # 登录态失效 → 刷新返回 409，绑定标记 expired
    fake_adapter.invalid = True
    resp = auth_client.post(f"/api/bindings/{binding_id}/refresh")
    assert resp.status_code == 409

    bindings = auth_client.get("/api/bindings").json()
    assert bindings[0]["status"] == "expired"

    # 重新登录：拿新 token 再次激活
    resp = auth_client.post(f"/api/bindings/{binding_id}/relogin")
    assert resp.status_code == 200
    new_token = resp.json()["token"]
    fake_adapter.invalid = False
    resp = auth_client.post(
        "/api/bindings/activate",
        json={"token": new_token, "cookies": [{"name": "mk_session", "value": "good2"}]},
    )
    assert resp.status_code == 200
    assert auth_client.get("/api/bindings").json()[0]["status"] == "active"


def test_activate_bad_token_and_empty_cookies(auth_client, portal, fake_adapter):
    resp = auth_client.post(
        "/api/bindings/activate",
        json={"token": "nonexistent-token", "cookies": [{"name": "x", "value": "y"}]},
    )
    assert resp.status_code == 400

    resp = auth_client.post("/api/bindings", json={"portal_id": portal.id})
    token = resp.json()["token"]
    resp = auth_client.post("/api/bindings/activate", json={"token": token, "cookies": []})
    assert resp.status_code == 400
