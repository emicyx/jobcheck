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

    # 轮询调度
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 60
    portal_min_interval_seconds: int = 60  # 同一门户两次轮询的最小间隔（全平台）


    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
