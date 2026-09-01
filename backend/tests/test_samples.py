from app.db.models import Portal


def _seed_pending_portal(db):
    db.add(Portal(
        name="腾讯校招", company="腾讯", provider_key="json_adapter",
        domains=["join.qq.com"], enabled=False, verified=False,
        config={"login_url": "https://join.qq.com/"},
    ))
    db.commit()


def test_sample_flow(auth_client, db):
    _seed_pending_portal(db)

    # 1. 发起采样意图
    resp = auth_client.post("/api/samples/intents")
    assert resp.status_code == 201
    token = resp.json()["token"]

    # 2. 插件提交（凭 token，自动按域名关联门户）
    dom = '<html><body><div class="apply-list"><div class="item">后端开发 简历评估中</div></div></body></html>'
    resp = auth_client.post(
        "/api/samples/submit",
        json={
            "token": token,
            "url": "https://join.qq.com/post.html",
            "dom": dom,
            "resources": ["https://join.qq.com/api/apply/list"],
        },
    )
    assert resp.status_code == 200, resp.text
    sample_id = resp.json()["id"]

    # 3. 用户能看到自己的采样
    mine = auth_client.get("/api/samples/mine").json()
    assert len(mine) == 1 and mine[0]["status"] == "new"
    assert mine[0]["url"] == "https://join.qq.com/post.html"

    # 4. token 一次性
    resp = auth_client.post(
        "/api/samples/submit",
        json={"token": token, "url": "https://join.qq.com/x", "dom": dom, "resources": []},
    )
    assert resp.status_code == 400

    # 5. 普通用户不能访问管理接口
    assert auth_client.get("/api/samples").status_code == 403
    assert auth_client.get(f"/api/samples/{sample_id}").status_code == 403


def test_admin_can_review_sample(auth_client, db, monkeypatch):
    from app.db.models import User
    from app.core.security import hash_password

    _seed_pending_portal(db)
    # 造一个管理员（直接入库，绕过 bootstrap 的空表条件）
    admin = User(email="admin2@test.com", password_hash=hash_password("password123"), role="admin")
    db.add(admin)
    db.commit()
    # 同一个 TestClient 会话：先登出再登管理员
    auth_client.post("/api/auth/logout")
    auth_client.post("/api/auth/login", json={"email": "admin2@test.com", "password": "password123"})

    token = auth_client.post("/api/samples/intents").json()["token"]
    auth_client.post(
        "/api/samples/submit",
        json={"token": token, "url": "https://join.qq.com/mine", "dom": "<div>岗位A 待处理</div>", "resources": []},
    )

    # 登出，用普通用户提交过的采样换管理员查看
    lst = auth_client.get("/api/samples").json()
    assert len(lst) >= 1
    detail = auth_client.get(f"/api/samples/{lst[0]['id']}").json()
    assert "岗位A" in detail["dom"]
    assert detail["resources"] == []
    assert detail["user_email"] == "admin2@test.com"  # 管理员自己采的

    # 标记已处理
    resp = auth_client.patch(f"/api/samples/{lst[0]['id']}", json={"status": "used", "note": "已生成配方"})
    assert resp.status_code == 200 and resp.json()["status"] == "used"


def test_identify_returns_disabled_portal(auth_client, db):
    _seed_pending_portal(db)
    resp = auth_client.post("/api/portals/identify", json={"url": "https://join.qq.com/post.html"})
    assert resp.status_code == 200
    body = resp.json()
    assert body is not None and body["company"] == "腾讯"
    assert body["enabled"] is False  # 前端据此显示「配置生成中」

    resp = auth_client.post("/api/portals/identify", json={"url": "https://unknown-site.com"})
    assert resp.json() is None
