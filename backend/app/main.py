from contextlib import asynccontextmanager
import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import account, applications, auth, bindings, meta, portals, samples, tags
from app.core.config import settings
from app.core.security import hash_password
from app.db.database import Base, SessionLocal, engine
from app.db.models import User
from app import scheduler as poll_scheduler


def _ensure_columns() -> None:
    """轻量迁移：create_all 不会给已存在的表加列，这里补齐 applications 的追踪列。"""
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(applications)"))}
        for ddl in (
            "ALTER TABLE applications ADD COLUMN portal_id INTEGER",
            "ALTER TABLE applications ADD COLUMN binding_id INTEGER",
        ):
            col = ddl.split("ADD COLUMN ")[1].split()[0]
            if col not in cols:
                conn.execute(text(ddl))
        conn.commit()


def _bootstrap_admin(db: Session) -> None:
    """首次启动引导：用户表为空且配置了管理员邮箱/密码时创建 admin。"""
    if not settings.admin_email or not settings.admin_password:
        return
    if db.query(User).count() > 0:
        return
    db.add(
        User(
            email=settings.admin_email.lower(),
            password_hash=hash_password(settings.admin_password),
            role="admin",
        )
    )
    db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    _ensure_columns()
    with SessionLocal() as db:
        _bootstrap_admin(db)
    poll_scheduler.start_scheduler(_)
    yield
    poll_scheduler.stop_scheduler(_)


app = FastAPI(title="JobCheck API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api")
app.include_router(applications.router, prefix="/api")
app.include_router(tags.router, prefix="/api")
app.include_router(account.router, prefix="/api")
app.include_router(meta.router, prefix="/api")
app.include_router(portals.router, prefix="/api")
app.include_router(bindings.router, prefix="/api")
app.include_router(samples.router, prefix="/api")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "jobcheck-api"}


_EXTENSION_DIR = Path(__file__).resolve().parents[2] / "extension"


@app.get("/api/extension/download")
def download_extension():
    """打包下载浏览器插件（公开接口，zip 内含 extension/ 目录）。"""
    if not _EXTENSION_DIR.is_dir():
        raise HTTPException(404, "插件目录不存在")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(_EXTENSION_DIR.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(_EXTENSION_DIR.parent))
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="jobcheck-extension.zip"'},
    )
