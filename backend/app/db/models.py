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
    current_status: Mapped[str] = mapped_column(String(32), default="screening", index=True)
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
    """「我的投递」页采样（L2 配方管线原料）：插件在用户已登录页面上采集的 DOM 与请求-响应对。"""

    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portal_id: Mapped[int | None] = mapped_column(ForeignKey("portals.id", ondelete="SET NULL"), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dom: Mapped[str | None] = mapped_column(Text, nullable=True)  # 裁剪后的页面 DOM
    resources: Mapped[list | None] = mapped_column(JSON, nullable=True)  # XHR/fetch URL 清单（旧格式兼容）
    network: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 请求-响应对：{url,method,params,response_body}
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|new|used|failed
    token: Mapped[str | None] = mapped_column(String(48), unique=True, nullable=True)  # 一次性提交凭证
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 配方管线结果：generating→(published|failed)；published 时 portal_id 指向新建门户
    pipeline_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pipeline_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class Recipe(Base):
    """L2 自动配方：LLM 从采样生成的声明式提取配置（LLM_DESIGN.md 附录 A）。"""

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    portal_id: Mapped[int] = mapped_column(ForeignKey("portals.id", ondelete="CASCADE"), index=True)
    spec: Mapped[dict] = mapped_column(JSON)  # RecipeSpec（auth/list_source/field_map/status_map/meta）
    confidence: Mapped[float] = mapped_column(default=0.0)  # 仅作徽标与监控分维度，不决定发布
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|validated|published|expired
    source: Mapped[str] = mapped_column(String(16), default="auto_gen")  # auto_gen|fingerprint|manual|admin
    version: Mapped[int] = mapped_column(default=1)
    created_by_sample_id: Mapped[int | None] = mapped_column(ForeignKey("samples.id", ondelete="SET NULL"), nullable=True)
    attempts: Mapped[int] = mapped_column(default=1)  # 生成尝试次数（含自修正）
    last_errors: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 验证失败原因（干跑重试参考）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=utcnow)

    portal: Mapped["Portal"] = relationship()


class StatusRule(Base):
    """状态规则表：原文文案 → 统一状态。跨门户沉淀（llm 兜底/用户手改候选），全平台复用。"""

    __tablename__ = "status_rules"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_key", "pattern", name="uq_status_rule"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(8), default="portal")  # portal|provider|generic
    scope_key: Mapped[str] = mapped_column(String(64), default="")  # portal.id 或 provider_key；generic 为空
    pattern: Mapped[str] = mapped_column(String(255))  # 正则（不区分大小写）
    mapped_status: Mapped[str] = mapped_column(String(32))
    priority: Mapped[int] = mapped_column(default=100)
    source: Mapped[str] = mapped_column(String(16), default="keyword")  # keyword|llm|user_sediment|manual
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DeviceToken(Base):
    """扩展与账号的配对凭证：平台生成 6 位码 → 扩展凭码换取 Bearer token。

    token 只存 sha256（DB 泄漏不泄露可用凭证）；配对码一次性、10 分钟有效。
    """

    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str | None] = mapped_column(String(8), index=True, nullable=True)  # 6 位数字；配对后置空防重放
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | paired
    device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 配对码有效期
    paired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()


class Snapshot(Base):
    """扩展在用户访问投递页时被动捕获并上报的快照（REFACTOR_PLAN §2.2）。

    上报「原料」（网络条目原文），后端现场解析；解析定位落档 portal hints。
    影子模式（snapshot_shadow_mode）只解析记录结果，不创建卡片。
    """

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portal_id: Mapped[int | None] = mapped_column(ForeignKey("portals.id", ondelete="SET NULL"), nullable=True, index=True)
    url: Mapped[str] = mapped_column(String(500))
    domain: Mapped[str] = mapped_column(String(255), index=True)  # 注册域：节流与留存清理的键
    payload_hash: Mapped[str] = mapped_column(String(64), index=True)  # 网络条目归一化哈希（去重）
    network: Mapped[list | None] = mapped_column(JSON, nullable=True)  # 与 samples.network 同构
    resources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    login_suspect: Mapped[bool] = mapped_column(Boolean, default=False)  # 扩展判定疑似未登录

    parse_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending | parsed | no_data
    parse_route: Mapped[str | None] = mapped_column(String(16), nullable=True)  # hints | platform | heuristics | embedded
    parse_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_count: Mapped[int] = mapped_column(default=0)
    list_json_path: Mapped[str | None] = mapped_column(String(255), nullable=True)  # 解析命中定位（ hints 落档依据）
    field_map: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dom: Mapped[str | None] = mapped_column(Text, nullable=True)  # 裁剪渲染 HTML：网络三层钩子失败时的 DOM 兜底原料
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship()
    portal: Mapped["Portal"] = relationship()


class LLMCall(Base):
    """LLM 用量记账：每次调用一行，月预算熔断据此计算（LLM_DESIGN.md §1）。"""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(primary_key=True)
    task: Mapped[str] = mapped_column(String(32), index=True)  # recipe_gen|status_classify
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[str] = mapped_column(String(16), default="")
    sample_id: Mapped[int | None] = mapped_column(ForeignKey("samples.id", ondelete="SET NULL"), nullable=True)
    attempt: Mapped[int] = mapped_column(default=1)
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost_cny: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
