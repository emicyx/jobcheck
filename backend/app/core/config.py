from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    secret_key: str = "dev-secret-change-me"
    database_url: str = "sqlite:///./jobcheck.db"

    admin_email: str | None = None
    admin_password: str | None = None

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    session_ttl_hours: int = 24 * 14  # 会话有效期 14 天

    # 登录态 Cookie 加密密钥（64 位 hex）；未配置时从 SECRET_KEY 派生（仅开发）
    binding_encryption_key: str = ""

    # 轮询调度（旧架构。2026-09-01 用户拍板：新链路即刻转正，旧服务端轮询停用；
    # 代码与数据保留待 M3 清理删除，默认不再启动）
    scheduler_enabled: bool = False
    scheduler_tick_seconds: int = 60
    portal_min_interval_seconds: int = 60  # 同一门户两次轮询的最小间隔（全平台）

    # ── 扩展配对与访问时快照（REFACTOR_PLAN §3 M1）──────────
    pair_code_ttl_minutes: int = 10  # 6 位配对码有效期
    snapshot_throttle_minutes: int = 10  # 同注册域两次上报的最小间隔（与扩展端检测节流对齐）
    snapshot_keep_per_domain: int = 20  # 每个注册域留存的最近快照数（超出裁剪）
    # 2026-09-01 用户拍板「全部接成新路径」：快照直接落卡（原 M1 影子模式被跳过，
    # M3 指标闸门改为事后观测而非删除前置条件）
    snapshot_shadow_mode: bool = False

    # ── LLM 子系统（LLM_DESIGN.md §1）─────────────────────────────
    # 配方生成（T1）：provider = openai_compatible | heuristic（离线启发式，零成本，供本地演示与测试）
    llm_recipe_provider: str = "heuristic"
    llm_recipe_base_url: str = "https://api.deepseek.com"
    llm_recipe_model: str = "deepseek-chat"
    llm_recipe_api_key: str = ""
    # 状态分类（T2）：provider 同上
    llm_classify_provider: str = "heuristic"
    llm_classify_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_classify_model: str = "glm-4-flash"
    llm_classify_api_key: str = ""
    # 记账价格（CNY / 百万 token，用于月预算熔断的估算）
    llm_recipe_price_in: float = 4.0
    llm_recipe_price_out: float = 16.0
    llm_classify_price_in: float = 0.5
    llm_classify_price_out: float = 2.0
    # 月预算熔断：超限后暂停 T1、T2 降级为直接标待确认，不影响已发布配方轮询
    llm_monthly_budget_cny: float = 100.0
    # 配方管线总开关（采样提交后是否自动触发生成）
    recipe_pipeline_enabled: bool = True
    # 同注册域名生成冷却（小时），防重复/恶意采样烧钱（决策 15）
    recipe_cooldown_hours: int = 24


    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
