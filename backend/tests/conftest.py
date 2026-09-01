import os
import tempfile

# 必须在导入 app 模块之前设置环境（config 在导入时读取）
_TMP = tempfile.mkdtemp(prefix="jobcheck-test-")
os.environ["DATABASE_URL"] = ("sqlite:///" + _TMP + "/test.db").replace("\\", "/")
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_EMAIL"] = ""
os.environ["ADMIN_PASSWORD"] = ""
os.environ["SCHEDULER_ENABLED"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db.database import Base, SessionLocal, engine, get_db  # noqa: E402
from app.db.models import Application, InviteCode, Tag  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        yield session


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def invite_code(db) -> str:
    code = InviteCode(code="TESTCODE", max_uses=10)
    db.add(code)
    db.commit()
    return code.code


@pytest.fixture()
def auth_client(client, invite_code):
    resp = client.post(
        "/api/auth/register",
        json={"email": "u1@test.com", "password": "password123", "invite_code": invite_code},
    )
    assert resp.status_code == 200, resp.text
    return client


def make_user(client, invite_code, email, password="password123"):
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "invite_code": invite_code},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
