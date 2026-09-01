from conftest import make_user

PAYLOAD = {
    "company": "携程",
    "job_title": "前端开发",
    "applied_at": "2026-08-25",
}


def test_tag_crud(auth_client):
    resp = auth_client.post("/api/tags", json={"name": "技术岗", "color": "#6188d8"})
    assert resp.status_code == 201
    tag_id = resp.json()["id"]

    # 重名 409
    assert auth_client.post("/api/tags", json={"name": "技术岗"}).status_code == 409
    # 改名撞车 409
    auth_client.post("/api/tags", json={"name": "产品岗"})
    assert (
        auth_client.patch(f"/api/tags/{tag_id}", json={"name": "产品岗"}).status_code == 409
    )
    # 正常改色
    assert auth_client.patch(f"/api/tags/{tag_id}", json={"color": "#c25a5a"}).status_code == 200
    assert len(auth_client.get("/api/tags").json()) == 2
    # 删除
    assert auth_client.delete(f"/api/tags/{tag_id}").status_code == 200
    assert len(auth_client.get("/api/tags").json()) == 1


def test_account_delete_wrong_password(auth_client):
    auth_client.post("/api/applications", json=PAYLOAD)
    resp = auth_client.request(
        "DELETE", "/api/account", json={"password": "wrong-password"}
    )
    assert resp.status_code == 403
    assert auth_client.get("/api/auth/me").status_code == 200


def test_account_delete_cascades(client, db, invite_code):
    make_user(client, invite_code, "gone@test.com")
    client.post("/api/applications", json=PAYLOAD)
    client.post("/api/tags", json={"name": "T1"})

    resp = client.request("DELETE", "/api/account", json={"password": "password123"})
    assert resp.status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    db.expire_all()
    from app.db.models import Application, Tag

    assert db.query(Application).count() == 0
    assert db.query(Tag).count() == 0
