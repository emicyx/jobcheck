"""GET /api/me/stats：当前用户个人投递统计（看板左侧栏数据源）。"""

from datetime import datetime, timedelta

from app.core.security import hash_password
from app.db.models import Application, User


def test_me_stats_requires_auth(client):
    assert client.get("/api/me/stats").status_code == 401


def _seed(db, user_id):
    now = datetime.utcnow()
    db.add_all(
        [
            Application(user_id=user_id, company="A", job_title="t", applied_at=now.date(), current_status="screening"),
            Application(user_id=user_id, company="B", job_title="t", applied_at=now.date(), current_status="interview_1"),
            Application(user_id=user_id, company="C", job_title="t", applied_at=now.date(), current_status="rejected"),
            # 上个月创建的记录：计入 total，不计入 month_new
            Application(
                user_id=user_id, company="D", job_title="t", applied_at=now.date(),
                current_status="offer", created_at=now.replace(day=1) - timedelta(days=5),
            ),
        ]
    )
    db.commit()


def test_me_stats_aggregates_only_mine(auth_client, db):
    # 另一个用户的记录不应计入（不经 API 注册，避免覆盖当前会话 Cookie）
    other = User(email="other@test.com", password_hash=hash_password("password123"))
    db.add(other)
    db.commit()
    _seed(db, other.id)

    resp = auth_client.get("/api/me/stats")
    assert resp.status_code == 200
    assert resp.json() == {"total": 0, "in_progress": 0, "terminal": 0, "month_new": 0, "by_status": []}


def test_me_stats_counts_and_order(auth_client, db):
    current = db.query(User).filter_by(email="u1@test.com").one()
    _seed(db, current.id)

    data = auth_client.get("/api/me/stats").json()
    assert data["total"] == 4
    assert data["terminal"] == 1
    assert data["in_progress"] == 3
    assert data["month_new"] == 3  # D 创建于上月

    statuses = {s["key"]: s["count"] for s in data["by_status"]}
    assert statuses == {"screening": 1, "interview_1": 1, "rejected": 1, "offer": 1}
    # 状态机顺序：screening(20) < interview_1(50) < offer(90) < rejected(110)
    keys = [s["key"] for s in data["by_status"]]
    assert keys == ["screening", "interview_1", "offer", "rejected"]
