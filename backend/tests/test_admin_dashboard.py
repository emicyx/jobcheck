"""管理后台界面聚合接口：overview / users / applications-stats / snapshots stats by_route。"""

from datetime import datetime, timedelta

from app.core.security import hash_password
from app.db.models import LLMCall, Application, Portal, Snapshot, User


def _admin_client(client, db):
    db.add(User(email="admin@test.com", password_hash=hash_password("password123"), role="admin"))
    db.commit()
    resp = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "password123"})
    assert resp.status_code == 200, resp.text
    return client


def _seed(db):
    user = User(email="u1@test.com", password_hash=hash_password("password123"))
    db.add(user)
    db.flush()
    portal = Portal(name="飞书演示", company="飞书", domains=["example.com"])
    db.add(portal)
    db.flush()
    now = datetime.utcnow()
    db.add_all(
        [
            Application(
                user_id=user.id, portal_id=portal.id, source="auto",
                company="飞书", job_title="工程师", applied_at=now.date(), current_status="written_test",
            ),
            Application(
                user_id=user.id, source="manual",
                company="小米", job_title="AI 工程师", applied_at=now.date(), current_status="screening",
            ),
            Application(
                user_id=user.id, source="manual",
                company="小米", job_title="后端", applied_at=now.date(), current_status="rejected",
            ),
            Snapshot(
                user_id=user.id, portal_id=portal.id, url="https://example.com/mine", domain="example.com",
                payload_hash="h1",
                network=[{"url": "https://example.com/api", "response_body": "{}"}],
                parse_status="parsed", parse_route="platform", parsed_count=2,
            ),
            Snapshot(
                user_id=user.id, url="https://other.com/mine", domain="other.com",
                payload_hash="h2", parse_status="no_data",
            ),
            LLMCall(task="status_classify", provider="heuristic", model="", cost_cny=0.0, tokens_in=10, tokens_out=5),
        ]
    )
    db.commit()
    return user


def test_admin_dashboard_requires_admin(auth_client):
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/applications-stats"):
        assert auth_client.get(path).status_code == 403


def test_overview_counts_and_trend(client, db):
    _seed(db)
    _admin_client(client, db)
    data = client.get("/api/admin/overview").json()
    assert data["users_total"] == 2  # u1 + admin
    assert data["applications_total"] == 3
    assert data["snapshots_total"] == 2
    w = data["window"]
    assert len(w["days"]) == 14
    assert sum(w["new_users"]) == 2
    assert sum(w["new_applications"]) == 3
    assert sum(w["snapshots"]) == 2
    assert sum(w["snapshots_parsed"]) == 1
    assert w["parse_rate"] == 0.5
    assert w["capture_ok_rate"] == 0.5
    assert data["llm"]["budget_cny"] >= 0


def test_users_rows_have_usage(client, db):
    user = _seed(db)
    _admin_client(client, db)
    rows = {r["email"]: r for r in client.get("/api/admin/users").json()}
    row = rows["u1@test.com"]
    assert row["applications_count"] == 3
    assert row["snapshots_count"] == 2
    assert row["sites_count"] == 1  # 两条快照只命中同一个门户
    assert row["last_active_at"]
    assert rows["admin@test.com"]["applications_count"] == 0


def test_applications_stats_distribution(client, db):
    _seed(db)
    _admin_client(client, db)
    data = client.get("/api/admin/applications-stats").json()
    assert data["total"] == 3
    assert data["by_source"] == {"auto": 1, "manual": 2}
    statuses = {s["key"]: s["count"] for s in data["by_status"]}
    assert statuses == {"screening": 1, "written_test": 1, "rejected": 1}
    # 状态机顺序：screening(20) < written_test(40) < rejected(110)
    keys = [s["key"] for s in data["by_status"]]
    assert keys.index("screening") < keys.index("written_test") < keys.index("rejected")
    assert data["top_companies"][0] == {"company": "小米", "count": 2}
    assert data["top_portals"][0]["portal_name"] == "飞书演示"


def test_snapshots_stats_by_route(client, db):
    _seed(db)
    _admin_client(client, db)
    data = client.get("/api/admin/snapshots/stats").json()
    assert data["total"] == 2
    assert data["by_route"] == {"platform": 1}
    assert data["by_domain"]["example.com"]["parsed"] == 1
    assert data["by_domain"]["other.com"]["total"] == 1
