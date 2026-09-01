def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_register_and_me(auth_client):
    resp = auth_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "u1@test.com"
    assert resp.json()["role"] == "user"


def test_register_bad_invite(client):
    resp = client.post(
        "/api/auth/register",
        json={"email": "x@test.com", "password": "password123", "invite_code": "NOPE"},
    )
    assert resp.status_code == 400
    assert "邀请码" in resp.json()["detail"]


def test_register_duplicate_email(client, invite_code):
    first = client.post(
        "/api/auth/register",
        json={"email": "dup@test.com", "password": "password123", "invite_code": invite_code},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/auth/register",
        json={"email": "dup@test.com", "password": "password456", "invite_code": invite_code},
    )
    assert second.status_code == 400


def test_register_weak_password(client, invite_code):
    resp = client.post(
        "/api/auth/register",
        json={"email": "weak@test.com", "password": "short", "invite_code": invite_code},
    )
    assert resp.status_code == 422


def test_login_logout_flow(client, invite_code):
    client.post(
        "/api/auth/register",
        json={"email": "l@test.com", "password": "password123", "invite_code": invite_code},
    )
    # 密码错误
    resp = client.post(
        "/api/auth/login", json={"email": "l@test.com", "password": "wrong-pass"}
    )
    assert resp.status_code == 401
    # 登录成功
    resp = client.post(
        "/api/auth/login", json={"email": "l@test.com", "password": "password123"}
    )
    assert resp.status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    # 登出后会话失效
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401


def test_invite_usage_limit(client, db):
    db.add(__import__("app.db.models", fromlist=["InviteCode"]).InviteCode(code="ONETIME", max_uses=1))
    db.commit()
    body = {"password": "password123", "invite_code": "ONETIME"}
    assert client.post("/api/auth/register", json={**body, "email": "a@test.com"}).status_code == 200
    resp = client.post("/api/auth/register", json={**body, "email": "b@test.com"})
    assert resp.status_code == 400
    assert "上限" in resp.json()["detail"]
