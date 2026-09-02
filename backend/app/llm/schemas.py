"""配方 Schema（LLM_DESIGN.md 附录 A 的 pydantic 实现版）。

实现取舍（相对附录草案）：
- 字段路径用「相对点路径」（如 ``positionInfo.applyPositionTxt``，相对列表项），
  与 L1 json_adapter 的 fields 语义完全一致，提取引擎共用一份代码；
  不用 ``$[*].xxx`` 绝对 JSONPath——列表定位统一由 ``list_json_path`` 承担。
- 占位符 ``{{name}}`` 可出现在 xhr.url_pattern 与 xhr.query 的值中，
  必须在 runtime_params 声明运行时解析方式（cookie / xhr_json 前置接口），
  否则回放验证判失败（防把首个用户的身份烙进全平台复用的配方）。
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.statuses import VALID_KEYS

SCHEMA_VERSION = "1"
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")

FIELD_KEYS = ("job_title", "status_raw", "id", "department", "work_location", "applied_at", "job_url")


class Condition(BaseModel):
    """页面状态判定条件（各分支为 AND 关系；空条件视为不参与判定）。"""

    url_contains: list[str] = Field(default_factory=list, max_length=5)
    selector_exists: str | None = Field(default=None, max_length=300)
    status_code: list[int] = Field(default_factory=list, max_length=8)


class AuthSpec(BaseModel):
    login_success: Condition = Field(default_factory=Condition)
    session_invalid: Condition = Field(default_factory=Condition)


class Pagination(BaseModel):
    type: Literal["none", "page_param"] = "none"
    page_param: str = Field(default="page", max_length=32)
    start_page: int = Field(default=1, ge=1)
    max_pages: int = Field(default=1, ge=1, le=10)


class XHRSource(BaseModel):
    type: Literal["xhr"] = "xhr"
    url_pattern: str = Field(min_length=8, max_length=1000)  # 可含 {{占位符}}；通配 * 只允许出现在尾部
    method: Literal["GET", "POST"] = "GET"
    list_json_path: str = Field(default="", max_length=200)  # 响应中列表的点路径；空 = 根
    query: dict[str, str] = Field(default_factory=dict)  # 值可含 {{占位符}}
    body: dict[str, str] | None = None  # POST 型接口的 JSON 请求体（值可含 {{占位符}}）
    pagination: Pagination = Field(default_factory=Pagination)


class DOMSource(BaseModel):
    type: Literal["dom"] = "dom"
    page_url: str = Field(min_length=8, max_length=1000)
    wait_for_selector: str = Field(min_length=1, max_length=300)
    item_selector: str = Field(min_length=1, max_length=300)
    item_limit: int = Field(default=200, ge=1, le=200)


class PageSource(BaseModel):
    """SSR 直出页配方：轮询 = GET 页面本身（带 Cookie），从 HTML 内嵌数据提取。

    与 dom 型的区别：page 型不要 JS 渲染——数据就在初始 HTML 的内嵌 script 里
    （type=application/json 块或 window.__X__ = {...} 赋值），httpx 直取即可轮询；
    dom 型才需要 Playwright。data_anchor 定位内嵌 script（变量名 / script id，
    空则取第一个 list_json_path 可定位的数据对象）。
    """

    type: Literal["page"] = "page"
    page_url: str = Field(min_length=8, max_length=1000)
    data_anchor: str = Field(default="", max_length=200)
    list_json_path: str = Field(default="", max_length=200)


class FieldMapping(BaseModel):
    json_path: str | None = Field(default=None, max_length=200)  # xhr：相对列表项的点路径
    selector: str | None = Field(default=None, max_length=300)  # dom：项内 CSS 选择器
    attr: str = Field(default="text", max_length=64)  # text | href | 自定义属性名
    required: bool = True


class StatusMapEntry(BaseModel):
    pattern: str = Field(min_length=1, max_length=255)
    status: str
    priority: int = Field(default=100, ge=1, le=999)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in VALID_KEYS:
            raise ValueError(f"status 必须是统一状态机枚举值，收到 {v!r}")
        return v


class RuntimeParamCookie(BaseModel):
    type: Literal["cookie"] = "cookie"
    name: str = Field(min_length=1, max_length=128)


class RuntimeParamXHR(BaseModel):
    """前置接口取值：先请求 url_pattern，从响应按点路径取占位符的值。"""

    type: str = Field(default="xhr_json", pattern="^xhr_json$")
    url_pattern: str = Field(min_length=8, max_length=1000)
    method: Literal["GET", "POST"] = "GET"
    json_path: str = Field(min_length=1, max_length=200)


class RecipeMeta(BaseModel):
    generated_by: str = Field(default="llm", max_length=64)
    sample_id: int | None = None
    schema_version: str = SCHEMA_VERSION


class RecipeSpec(BaseModel):
    """L2 配方：纯声明式 JSON，解释器只支持白名单原语，输出天然沙箱。"""

    model_config = {"validate_assignment": True}

    auth: AuthSpec = Field(default_factory=AuthSpec)
    list_source: XHRSource | DOMSource | PageSource = Field(discriminator="type")
    field_map: dict[str, FieldMapping]
    status_map: list[StatusMapEntry] = Field(default_factory=list)
    runtime_params: dict[str, RuntimeParamCookie | RuntimeParamXHR] = Field(default_factory=dict)
    meta: RecipeMeta = Field(default_factory=RecipeMeta)

    @field_validator("field_map")
    @classmethod
    def _check_fields(cls, v: dict) -> dict:
        unknown = set(v) - set(FIELD_KEYS)
        if unknown:
            raise ValueError(f"未知字段名: {sorted(unknown)}；允许: {FIELD_KEYS}")
        for must in ("job_title", "status_raw"):
            if must not in v:
                raise ValueError(f"field_map 缺少必填字段 {must}")
        return v

    def placeholders(self) -> set[str]:
        """配方中出现的全部占位符名。"""
        found: set[str] = set()
        src = self.list_source
        if isinstance(src, XHRSource):
            found |= set(PLACEHOLDER_RE.findall(src.url_pattern))
            for value in src.query.values():
                found |= set(PLACEHOLDER_RE.findall(value))
        return found


class ObservedApplication(BaseModel):
    """LLM 自述清单的一条投递（供验证器逐条比对，反幻觉断言 4）。"""

    job_title: str
    status_raw: str
    department: str | None = None


class RecipeGenOutput(BaseModel):
    """T1 一次生成的完整产出：配方 + 自述清单 + 显式留给兜底的原文。"""

    recipe: RecipeSpec
    observations: list[ObservedApplication] = Field(min_length=1)
    unmapped_status_texts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ClassifyOutput(BaseModel):
    """T2 状态分类输出：confidence < 0.7 或 ambiguous 落待确认，不猜。"""

    status: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=500)
