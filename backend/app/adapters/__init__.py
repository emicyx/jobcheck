"""适配器框架（DESIGN.md §5）。

L1 供应商适配器与 L2 配方共用统一接口：给定门户配置与 Cookie，返回原始投递列表。
适配器只读，禁止任何写操作端点（产品红线）。
"""

from dataclasses import dataclass, field
from datetime import date


class AdapterError(Exception):
    """可重试的抓取失败（网络/解析/结构变更）。"""


class SessionInvalidError(Exception):
    """登录态失效（401/跳登录/会话 Cookie 被清）。"""


@dataclass
class RawApplication:
    job_title: str
    status_raw: str
    portal_key: str | None = None  # 门户侧唯一键（优先用于 diff 匹配）
    department: str | None = None
    work_location: str | None = None
    applied_at: date | None = None
    job_url: str | None = None


@dataclass
class AdapterContext:
    cookies: dict[str, str] = field(default_factory=dict)
    # 运行期自愈刷新出的 Cookie 新值（如飞书 CSRF 轮换）：适配器写入，
    # 调用方（sync/activate）负责合并回加密存储，下轮轮询不再依赖旧值
    refreshed_cookies: dict[str, str] = field(default_factory=dict)


class BaseAdapter:
    """所有适配器的基类。fetch 抛 SessionInvalidError 表示绑定过期。"""

    def fetch(self, config: dict, ctx: AdapterContext) -> list[RawApplication]:
        raise NotImplementedError

    def test_session(self, config: dict, ctx: AdapterContext) -> bool:
        """激活绑定时验证 Cookie 可用性：拉取一次列表即视为可用。"""
        self.fetch(config, ctx)
        return True


_REGISTRY: dict[str, type[BaseAdapter]] = {}


def register_adapter(provider_key: str, cls: type[BaseAdapter]) -> None:
    _REGISTRY[provider_key] = cls


def get_adapter(provider_key: str) -> BaseAdapter:
    cls = _REGISTRY.get(provider_key)
    if cls is None:
        raise AdapterError(f"未注册的适配器类型: {provider_key}")
    return cls()


from app.adapters.json_adapter import JSONAPIAdapter  # noqa: E402
from app.adapters.recipe_adapter import RecipeAdapter  # noqa: E402

register_adapter("json_adapter", JSONAPIAdapter)
register_adapter("recipe", RecipeAdapter)
