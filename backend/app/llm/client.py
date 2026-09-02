"""OpenAI 兼容薄客户端（LLM_DESIGN.md §1）+ 用量记账 + 月预算熔断。

- httpx + pydantic 自写薄客户端，不绑死任何厂商 SDK；
- 每次调用落库（任务类型 / tokens / 估算成本 / 耗时）；
- 月预算熔断：超限抛 BudgetExceeded（T1 暂停、T2 由调用方降级为待确认）；
- heuristic 离线提供者：确定性推断配方，零成本，本地演示与测试用，
  同样必须过回放验证——确定性不等于免检。
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import LLMCall

logger = logging.getLogger("jobcheck.llm")

_TIMEOUT = 60.0
_RETRIES = 3
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class BudgetExceeded(Exception):
    """月预算耗尽：T1 应暂停（新站接入转人工/留样重试），不影响已有配方轮询。"""


class LLMError(Exception):
    """上游调用失败（重试耗尽/响应不可解析）。"""


def monthly_cost_cny(db: Session) -> float:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    spent = db.scalar(select(func.coalesce(func.sum(LLMCall.cost_cny), 0.0)).where(LLMCall.created_at >= month_start))
    return float(spent or 0.0)


def _record(
    db: Session, *, task: str, provider: str, model: str, prompt_version: str,
    sample_id: int | None, attempt: int, tokens_in: int, tokens_out: int,
    cost: float, latency_ms: int, ok: bool, error: str | None,
) -> None:
    db.add(
        LLMCall(
            task=task, provider=provider, model=model, prompt_version=prompt_version,
            sample_id=sample_id, attempt=attempt, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_cny=round(cost, 6), latency_ms=latency_ms, ok=ok, error=error[:2000] if error else None,
        )
    )
    db.commit()


def call_json(
    db: Session,
    *,
    task: str,
    system: str,
    user: str,
    prompt_version: str,
    base_url: str,
    api_key: str,
    model: str,
    price_in: float,
    price_out: float,
    sample_id: int | None = None,
    attempt: int = 1,
) -> dict:
    """调用 OpenAI 兼容 /chat/completions，强制 JSON 输出，返回解析后的 dict。

    失败重试 3 次（指数退避）；每次尝试（含失败）都记账。
    """
    if not api_key:
        raise LLMError(f"{task}: 未配置 API key（provider=openai_compatible 需要）")
    if monthly_cost_cny(db) >= settings.llm_monthly_budget_cny:
        raise BudgetExceeded(f"本月 LLM 预算 {settings.llm_monthly_budget_cny} CNY 已用尽")

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error: Exception | None = None
    for retry in range(_RETRIES):
        started = time.monotonic()
        try:
            # trust_env=False：直连，不经系统代理
            resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT, trust_env=False)
            if resp.status_code == 400 and "response_format" in resp.text:
                payload.pop("response_format", None)
                resp = httpx.post(url, json=payload, headers=headers, timeout=_TIMEOUT, trust_env=False)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            tokens_in = int(usage.get("prompt_tokens") or 0)
            tokens_out = int(usage.get("completion_tokens") or 0)
            cost = tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
            _record(
                db, task=task, provider="openai_compatible", model=model,
                prompt_version=prompt_version, sample_id=sample_id, attempt=attempt,
                tokens_in=tokens_in, tokens_out=tokens_out, cost=cost,
                latency_ms=int((time.monotonic() - started) * 1000), ok=True, error=None,
            )
            return _parse_json(content)
        except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError) as e:
            last_error = e
            _record(
                db, task=task, provider="openai_compatible", model=model,
                prompt_version=prompt_version, sample_id=sample_id, attempt=attempt,
                tokens_in=0, tokens_out=0, cost=0.0,
                latency_ms=int((time.monotonic() - started) * 1000), ok=False, error=str(e),
            )
            if retry < _RETRIES - 1:
                time.sleep(2**retry)
    raise LLMError(f"{task}: 调用失败（已重试 {_RETRIES} 次）: {last_error}")


def _parse_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = _FENCE_RE.sub("", text).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    return data
