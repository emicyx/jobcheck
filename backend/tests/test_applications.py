from datetime import date

from conftest import make_user

PAYLOAD = {
    "company": "腾讯",
    "job_title": "后端开发工程师",
    "department": "CSIG",
    "work_location": "深圳",
    "applied_at": "2026-08-20",
    "batch": "提前批",
    "current_status": "screening",
    "note": "官网直投",
}


def test_meta(auth_client):
    resp = auth_client.get("/api/meta")
    assert resp.status_code == 200
    body = resp.json()
    keys = {s["key"] for s in body["statuses"]}
    assert {"applied", "screening", "interview_1", "offer", "rejected", "pending_confirm"} <= keys
    assert body["batches"] == ["提前批", "正式批", "春招", "实习"]


def test_create_and_detail(auth_client):
    resp = auth_client.post("/api/applications", json=PAYLOAD)
    assert resp.status_code == 201, resp.text
    app_id = resp.json()["id"]
    assert resp.json()["source"] == "manual"
    assert resp.json()["current_status"] == "screening"

    detail = auth_client.get(f"/api/applications/{app_id}").json()
    assert len(detail["history"]) == 1
    assert detail["history"][0]["from_status"] is None
    assert detail["history"][0]["to_status"] == "screening"


def test_update_status_creates_history(auth_client):
    app_id = auth_client.post("/api/applications", json=PAYLOAD).json()["id"]

    resp = auth_client.patch(
        f"/api/applications/{app_id}", json={"current_status": "written_test"}
    )
    assert resp.status_code == 200
    assert resp.json()["current_status"] == "written_test"

    detail = auth_client.get(f"/api/applications/{app_id}").json()
    assert len(detail["history"]) == 2
    assert detail["history"][-1]["from_status"] == "screening"
    assert detail["history"][-1]["to_status"] == "written_test"
    assert detail["history"][-1]["raw_status_text"] == "笔试中"

    # 状态未变化时不产生重复历史
    auth_client.patch(f"/api/applications/{app_id}", json={"current_status": "written_test"})
    detail = auth_client.get(f"/api/applications/{app_id}").json()
    assert len(detail["history"]) == 2


def test_invalid_status_and_batch_rejected(auth_client):
    resp = auth_client.post("/api/applications", json={**PAYLOAD, "current_status": "nonsense"})
    assert resp.status_code == 422
    resp = auth_client.post("/api/applications", json={**PAYLOAD, "batch": "冬招"})
    assert resp.status_code == 422


def test_list_filters(auth_client):
    auth_client.post("/api/applications", json=PAYLOAD)
    auth_client.post(
        "/api/applications",
        json={**PAYLOAD, "company": "网易", "job_title": "游戏策划", "batch": "正式批"},
    )

    assert len(auth_client.get("/api/applications").json()) == 2
    assert len(auth_client.get("/api/applications", params={"company": "腾讯"}).json()) == 1
    assert len(auth_client.get("/api/applications", params={"batch": "正式批"}).json()) == 1
    assert len(auth_client.get("/api/applications", params={"q": "游戏"}).json()) == 1
    assert len(auth_client.get("/api/applications", params={"status": "screening"}).json()) == 2


def test_tag_attach_and_filter(auth_client):
    tag = auth_client.post("/api/tags", json={"name": "高优", "color": "#d97b28"}).json()
    auth_client.post("/api/applications", json=PAYLOAD)
    app2 = auth_client.post(
        "/api/applications", json={**PAYLOAD, "company": "网易", "tag_ids": [tag["id"]]}
    ).json()
    assert {t["name"] for t in app2["tags"]} == {"高优"}

    hits = auth_client.get("/api/applications", params={"tag_id": tag["id"]}).json()
    assert len(hits) == 1
    assert hits[0]["company"] == "网易"


def test_delete(auth_client):
    app_id = auth_client.post("/api/applications", json=PAYLOAD).json()["id"]
    assert auth_client.delete(f"/api/applications/{app_id}").status_code == 200
    assert auth_client.get(f"/api/applications/{app_id}").status_code == 404


def test_owner_isolation(client, invite_code):
    make_user(client, invite_code, "a@test.com")
    app_id = client.post("/api/applications", json=PAYLOAD).json()["id"]

    # 换另一个用户登录（TestClient 共享 cookie，重新登录覆盖）
    make_user(client, invite_code, "b@test.com")
    assert client.get(f"/api/applications/{app_id}").status_code == 404
    assert client.patch(f"/api/applications/{app_id}", json={"current_status": "offer"}).status_code == 404
    assert client.delete(f"/api/applications/{app_id}").status_code == 404


def test_apps_require_auth(client):
    assert client.get("/api/applications").status_code == 401
