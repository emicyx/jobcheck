"""T1/T2 生成提供者：统一 heuristic（离线确定性）与 openai_compatible（真实 LLM）。

provider 选择在 Settings（换模型 = 改配置，不改代码）。
heuristic 提供者同样必须过回放验证；classify 的 heuristic 降级为「不分类」。
"""

import json
import logging

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.llm import client, heuristics, prompts
from app.llm.preprocess import PreparedPackage
from app.llm.schemas import ClassifyOutput, RecipeGenOutput

logger = logging.getLogger("jobcheck.llm")


def generate_recipe_draft(
    db: Session,
    pkg: PreparedPackage,
    raw_entries: list[dict],
    sample_url: str,
    feedback: list[str] | None,
    attempt: int,
) -> RecipeGenOutput | None:
    """生成配方草稿。返回 None = 无法生成（管线记录失败）。"""
    provider = settings.llm_recipe_provider
    if provider == "heuristic":
        if feedback:
            return None  # 确定性推断无自修正空间：首轮失败即失败
        for prepared in pkg.xhrs:  # 按疑似投递列表排序依次尝试
            if "#embedded" in prepared.url:
                continue  # SSR 内嵌数据块不可作为接口重放，走下方 page 型兜底
            try:
                body = _raw_body_for(raw_entries, prepared.url)
                if body is None:
                    continue
                output = heuristics.build_recipe(
                    prepared.url, prepared.method, json.loads(body), sample_url,
                    request_body=_raw_request_body_for(raw_entries, prepared.url),
                )
            except (json.JSONDecodeError, ValueError):
                continue
            if output is not None:
                return output
        # SSR 内嵌数据兜底：「记录页直出、无列表 XHR」的自研站——轮询 = GET 页面本身。
        # 正确性仍由回放验证把关（内嵌块即考卷，与运行时 GET 页面共用同一提取引擎）
        for prepared in pkg.xhrs:
            if "#embedded" not in prepared.url:
                continue
            try:
                body = _raw_body_for(raw_entries, prepared.url)
                if body is None:
                    continue
                output = heuristics.build_page_recipe(prepared.url, json.loads(body), sample_url)
            except (json.JSONDecodeError, ValueError):
                continue
            if output is not None:
                return output
        return None
    if provider == "openai_compatible":
        system, version = prompts.recipe_gen_system(feedback)
        data = client.call_json(
            db,
            task="recipe_gen",
            system=system,
            user=pkg.to_prompt(),
            prompt_version=version,
            base_url=settings.llm_recipe_base_url,
            api_key=settings.llm_recipe_api_key,
            model=settings.llm_recipe_model,
            price_in=settings.llm_recipe_price_in,
            price_out=settings.llm_recipe_price_out,
            sample_id=None,
            attempt=attempt,
        )
        try:
            return RecipeGenOutput.model_validate(data)
        except ValidationError as e:
            logger.warning("recipe_gen 输出未过 Schema: %s", e)
            raise client.LLMError(f"输出未过 Schema 校验: {e}") from e
    raise ValueError(f"未知 llm_recipe_provider: {provider}")


def classify_status(db: Session, portal_desc: str, raw_text: str, sample_id: int | None = None) -> ClassifyOutput | None:
    """T2 状态分类。返回 None = 不分类（降级待确认）。"""
    provider = settings.llm_classify_provider
    if provider == "heuristic":
        return None
    if provider == "openai_compatible":
        system, version = prompts.status_classify_system()
        user = f"门户/供应商: {portal_desc}\n状态原文: {raw_text}"
        try:
            data = client.call_json(
                db,
                task="status_classify",
                system=system,
                user=user,
                prompt_version=version,
                base_url=settings.llm_classify_base_url,
                api_key=settings.llm_classify_api_key,
                model=settings.llm_classify_model,
                price_in=settings.llm_classify_price_in,
                price_out=settings.llm_classify_price_out,
                sample_id=sample_id,
            )
        except client.BudgetExceeded:
            return None  # 预算熔断：T2 降级为直接标待确认
        try:
            output = ClassifyOutput.model_validate(data)
        except ValidationError:
            return None
        if output.status == "ambiguous" or output.confidence < 0.7:
            return None  # 不猜
        from app.domain.statuses import is_valid

        return output if is_valid(output.status) else None
    return None


def _raw_body_for(raw_entries: list[dict], url: str) -> str | None:
    for entry in raw_entries:
        if str(entry.get("url") or "") == url:
            return str(entry.get("response_body") or "")
    return None


def _raw_request_body_for(raw_entries: list[dict], url: str) -> str | None:
    for entry in raw_entries:
        if str(entry.get("url") or "") == url:
            return str(entry.get("request_body") or "") or None
    return None
