from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

app_tags = Table(
    "app_tags",
    Base.metadata,
    Column("application_id", Integer, ForeignKey("applications.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


def utcnow() -> datetime:
    # SQLite DateTime 列为 naive，统一存 naive UTC，避免 aware/naive 比较错误
    return datetime.now(timezone.utc).replace(tzinfo=None)


class InviteCode(Base):
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    max_uses: Mapped[int] = mapped_column(default=10)
    used_count: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user | admin
    invite_code_id: Mapped[int | None] = mapped_column(ForeignKey("invite_codes.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    applications: Mapped[list["Application"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    tags: Mapped[list["Tag"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portal_id: Mapped[int | None] = mapped_column(ForeignKey("portals.id", ondelete="SET NULL"), nullable=True)
    binding_id: Mapped[int | None] = mapped_column(ForeignKey("bindings.id", ondelete="SET NULL"), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(8), default="manual")  # manual | auto
    company: Mapped[str] = mapped_column(String(128), index=True)
    job_title: Mapped[str] = mapped_column(String(255))
    department: Mapped[str | None] = mapped_column(String(128), nullable=True)
    work_location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    applied_at: Mapped[datetime] = mapped_column(Date)
    batch: Mapped[str] = mapped_column(String(16), default="正式批")
    current_status: Mapped[str] = mapped_column(String(32), default="applied", index=True)
    raw_status_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)  # manual | recipe | llm
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="applications")
    history: Mapped[list["AppStatusHistory"]] = relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="AppStatusHistory.id"
    )
    tags: Mapped[list["Tag"]] = relationship(secondary=app_tags, lazy="selectin")


class AppStatusHistory(Base):
    __tablename__ = "app_status_hist"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    raw_status_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    application: Mapped["Application"] = relationship(back_populates="history")


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tag_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    color: Mapped[str] = mapped_column(String(16), default="#6188d8")

    user: Mapped["User"] = relationship(back_populates="tags")
    # 只读反查：标签侧从不直接管理投递关联，避免与 Application.tags 写入冲突
    applications: Mapped[list["Application"]] = relationship(secondary=app_tags, viewonly=True)


class Portal(Base):
    """可追踪的招聘门户实例（L1 模板实例或 L2 配方门户）。"""

    __tablename__ = "portals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    company: Mapped[str] = mapped_column(String(128))
    provider_key: Mapped[str] = mapped_column(String(32), default="json_adapter")
    domains: Mapped[list] = mapped_column(JSON, default=list)  # 识别用域名子串列表
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # 适配器配置（登录页/接口/字段映射/状态映射）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)  # 配置是否经真实账号验证
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 全平台限速
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    bindings: Mapped[list["Binding"]] = relationship(back_populates="portal", cascade="all, delete-orphan")


class Binding(Base):
    """用户与门户的登录态绑定，轮询单位。"""

    __tablename__ = "bindings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portal_id: Mapped[int] = mapped_column(ForeignKey("portals.id", ondelete="CASCADE"), index=True)
    cookie_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)  # AES-GCM 加密的 Cookie JSON
    key_version: Mapped[int] = mapped_column(default=1)

    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|active|expired|paused
    interval_hours: Mapped[int] = mapped_column(default=6)

    intent_token: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    intent_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cookie_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=utcnow)

    user: Mapped["User"] = relationship()
    portal: Mapped["Portal"] = relationship(back_populates="bindings")


class Sample(Base):
    """「我的投递」页采样（L2 配方管线原料）：插件在用户已登录页面上采集的 DOM 与 XHR 清单。"""

    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portal_id: Mapped[int | None] = mapped_column(ForeignKey("portals.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dom: Mapped[str | None] = mapped_column(Text, nullable=True)  # 裁剪后的页面 DOM
    resources: Mapped[list | None] = mapped_column(JSON, nullable=True)  # XHR/fetch URL 清单
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|new|used|failed
    token: Mapped[str | None] = mapped_column(String(48), unique=True, nullable=True)  # 一次性提交凭证
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()
