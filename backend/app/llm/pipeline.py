"""配方生成管线（DESIGN.md §5 / LLM_DESIGN.md §2）：采样 → 指纹 → T1 生成 → 回放验证 → 免审批发布。

治理（决策 15）：同注册域名去重、单门户 24h 冷却（含失败）、月预算熔断。
发布即生效（无人工审批）：验证通过 → 门户 enabled，向导轮询感知，所有用户可绑定；
验证不过 → 不建门户，样本留存，管理后台可干跑重试。
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Portal, Recipe, Sample
from app.llm import fingerprint as fingerprint_mod
from app.llm import heuristics, preprocess, providers, validator
from app.llm.schemas import RecipeGenOutput, XHRSource

logger = logging.getLogger("jobcheck.llm.pipeline")

# 内地常见二级后缀：eTLD+1 需要 +2 级
_SECOND_LEVEL_TLDS = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "co.uk", "com.hk", "com.tw", "com.sg", "com.au",
}


def registrable_domain(host: str) -> str:
    """eTLD+1（内置常见二级后缀表，不引第三方库）。"""
    host = (host or "").lower().strip(".")
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in _SECOND_LEVEL_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@dataclass
class PipelineResult:
    status: str  # published | failed | skipped
    portal_id: int | None = None
    recipe_id: int | None = None
    note: str = ""
    errors: list[str] = field(default_factory=list)
    route: str = ""  # fingerprint | generation

    @property
    def ok(self) -> bool:
        return self.status == "published"


def run_pipeline(db: Session, sample_id: int, *, force: bool = False) -> PipelineResult:
    sample = db.get(Sample, sample_id)
    if sample is None:
        return PipelineResult("failed", note="采样不存在")

    entries = sample.network or []
    host = urlparse(sample.url or "").netloc.lower()
    domain = registrable_domain(host)

    if not settings.recipe_pipeline_enabled and not force:
        return PipelineResult("skipped", note="配方管线未启用")
    if not entries:
        return _fail(db, sample, "采样缺少请求-响应对（请更新插件后重新采样）", cooldown_exempt=True)

    # 同域复用：已有 enabled 门户 → 后来采样的用户零成本命中
    existing = _find_portal_by_host(db, host)
    if existing is not None and existing.enabled:
        sample.portal_id = existing.id
        sample.pipeline_status = "published"
        sample.pipeline_note = f"已复用门户「{existing.name}」"
        db.commit()
        return PipelineResult("published", portal_id=existing.id, note=f"复用已发布门户 {existing.name}", route="reuse")

    # 冷却：同注册域名近期在生成/已发布/刚失败 → 拒绝（force 绕过，供后台干跑）
    if not force:
        cooldown_note = _check_cooldown(db, domain, sample.id)
        if cooldown_note:
            return _fail(db, sample, cooldown_note)

    sample.pipeline_status = "generating"
    sample.pipeline_note = None
    db.commit()

    # ── 第 0 级：结构指纹（免 LLM）──────────────────────
    hit = fingerprint_mod.match(entries)
    if hit is not None:
        blocker = fingerprint_mod.is_instantiable(hit)
        if blocker is None:
            result = _try_fingerprint(db, sample, hit, host)
            if result is not None:
                return result
            logger.info("指纹命中 %s 但实例化回放失败，转 T1 生成", hit.template.key)
        else:
            logger.info("指纹命中 %s 但 %s，转 T1 生成", hit.template.key, blocker)

    # ── T1：生成 → 回放验证 → 自修正（≤2 轮）────────────
    pkg = preprocess.prepare(sample.url or "", sample.dom, entries)
    feedback: list[str] = []
    last_errors: list[str] = []
    output: RecipeGenOutput | None = None
    verdict = None
    for attempt in range(1, 4):
        try:
            output = providers.generate_recipe_draft(db, pkg, entries, sample.url or "", feedback or None, attempt)
        except providers.client.BudgetExceeded:
            return _fail(db, sample, "本月 LLM 预算已用尽：样本已留存，可在管理后台稍后重试")
        except providers.client.LLMError as e:
            last_errors = [str(e)]
            continue
        if output is None:
            captured = "；".join(
                f"{urlparse(str(e.get('url') or '')).netloc}{urlparse(str(e.get('url') or '')).path}"
                for e in entries[:3]
                if str(e.get("url") or "").startswith("http")
            )
            # 资源清单（页面加载过的接口类 URL）：SSR 站点排障关键证据
            res = [str(r) for r in (sample.resources or []) if str(r).startswith("http")]
            loaded = "；".join(
                f"{urlparse(u).netloc}{urlparse(u).path}" for u in res[:5]
            )
            note = (
                "无法从该采样生成配方：未找到可提取的投递列表数据。"
                f"本次捕获 {len(entries)} 条接口（{captured or '无'}），"
                "其中没有投递列表数据——若页面记录可见，可能是数据内嵌在页面里或走了缓存。"
            )
            truncated = sum(1 for e in entries if e.get("truncated"))
            if truncated:
                note += f"注意：{truncated} 条接口响应体超过捕获上限被截断（可能恰好包含列表数据），请更新插件到 v0.4.13+ 后重新采样。"
            if loaded:
                note += f"页面加载过的接口类资源：{loaded}。"
            note += "可尝试从站内首页点击进入「应聘记录」页（而非直接打开链接）后重新采样；若仍失败，该站点需人工接入"
            return _fail(db, sample, note)
        verdict = validator.replay(output, sample.url or "", sample.dom, entries)
        errors = list(verdict.errors)
        if output.recipe.list_source.type == "dom" and not errors:
            errors.append("当前部署不支持 dom 型配方的在线轮询（无 Playwright 运行时），请改用 xhr 数据源")
        if not errors:
            return _publish(db, sample, output, verdict.stats.get("records", 0), attempts=attempt, host=host)
        feedback = errors
        last_errors = errors

    return _fail(
        db, sample,
        "配方生成未通过回放验证：" + "；".join(last_errors[:5]),
        errors=last_errors,
    )


# ── 内部实现 ──────────────────────────────────────


def _check_cooldown(db: Session, domain: str, current_sample_id: int) -> str | None:
    from app.services.bindings import utcnow

    cutoff = utcnow() - timedelta(hours=settings.recipe_cooldown_hours)
    recent = db.scalars(
        select(Sample)
        .where(Sample.id != current_sample_id)
        .where(Sample.pipeline_status.is_not(None))
        .where(Sample.created_at >= cutoff)
        .order_by(Sample.id.desc())
        .limit(50)
    )
    for other in recent:
        other_host = urlparse(other.url or "").netloc.lower()
        if registrable_domain(other_host) == domain and domain:
            if other.pipeline_status == "published":
                return f"该网站的配方已发布，无需重复采样"
            if other.pipeline_status == "generating":
                return "该网站正在生成配方，请稍后在向导里查看结果"
            # 只有消耗过生成尝试的失败（status=failed）才冷却；
            # 缺请求-响应对等输入性失败（status 保持 new）允许立即重试。
            # 失败冷却本身只为防 LLM 烧钱——heuristic 离线提供者零成本，不拦
            if (
                other.pipeline_status == "failed"
                and other.status == "failed"
                and settings.llm_recipe_provider != "heuristic"
            ):
                return f"该网站近期生成失败，{settings.recipe_cooldown_hours}h 内自动冷却（可先手动记录）"
    return None


def _find_portal_by_host(db: Session, host: str) -> Portal | None:
    if not host:
        return None
    portals = list(db.scalars(select(Portal).order_by(Portal.enabled.desc(), Portal.id)))
    for portal in portals:
        for d in portal.domains or []:
            if d and d.lower() in host:
                return portal
    return None


def _try_fingerprint(db: Session, sample: Sample, hit: fingerprint_mod.FingerprintHit, host: str):
    """指纹实例化：把命中接口按 L1 json_adapter 配置实例化，回放通过才发布。

    字段映射由确定性启发式在真实响应上推断（模板键名信号只用于「认出平台」），
    回放不通过就放弃，转 T1——模板永远不会静默给错数据。
    """
    try:
        data = json.loads(hit.response_body)
    except ValueError:
        return None
    # 模板自带真实校准映射（如飞书的嵌套字段/数字状态码）优先；无则启发式推断
    output = fingerprint_mod.build_from_template(
        hit, data, sample.url or "", request_body=hit.request_body or None
    )
    if output is None:
        output = heuristics.build_recipe(
            hit.matched_url, hit.method, data, sample.url or "",
            request_body=hit.request_body or None,
        )
    if output is None:
        return None
    verdict = validator.replay(output, sample.url or "", sample.dom, sample.network or [])
    if not verdict.ok:
        return None

    src = output.recipe.list_source
    assert isinstance(src, XHRSource)
    csrf_plan = None
    if hit.template.csrf_refresh:
        from urllib.parse import urljoin

        path, cookie_name = hit.template.csrf_refresh
        csrf_plan = {"url": urljoin(hit.matched_url, path), "method": "POST", "cookie_name": cookie_name}
    config = {
        "login_url": f"{_scheme(sample.url)}//{host}/",
        "session_cookie_names": [],
        "list_url": hit.matched_url,
        "list_method": hit.method,
        "list_json_path": src.list_json_path,
        # POST 型接口：请求体按采样原样保留（数字/字符串类型不变，服务端可能强校验），
        # 平台头（CSRF 等）一并实例化，运行时照此重放
        "list_body": (json.loads(hit.request_body) if hit.method == "POST" and hit.request_body else None),
        "list_headers": fingerprint_mod.instantiate_headers(hit.template, sample.url or "") or None,
        # CSRF 轮换自愈：405 时匿名刷新重试（飞书 atsx-csrf-token 会被站点轮换）
        "csrf_refresh": csrf_plan,
        "fields": {k: v.json_path for k, v in output.recipe.field_map.items() if v.json_path},
        "session_invalid_markers": list(hit.template.invalid_markers),
        "status_map": [{"pattern": e.pattern, "status": e.status} for e in output.recipe.status_map],
    }
    portal = _upsert_portal(db, host, provider_key="json_adapter", config=config,
                            note=f"结构指纹命中 {hit.template.label}，参数化实例化并经采样回放验证",
                            brand=_extract_tenant_name(sample))
    recipe = Recipe(
        portal_id=portal.id,
        spec=output.recipe.model_dump(mode="json"),
        confidence=0.8,
        status="published",
        source="fingerprint",
        created_by_sample_id=sample.id,
    )
    db.add(recipe)
    db.flush()
    _mark_published(db, sample, portal, f"指纹命中{hit.template.label}，零 LLM 成本接入")
    return PipelineResult("published", portal_id=portal.id, recipe_id=recipe.id,
                          note=f"指纹命中 {hit.template.label}", route="fingerprint")


def _scheme(url: str | None) -> str:
    return (urlparse(url or "").scheme or "https") + ":"


def _extract_tenant_name(sample: Sample) -> str | None:
    """从采样的内嵌 JSON 提取租户/品牌名，用作门户显示名。

    飞书招聘（ATSX）站点在页面里内嵌 js-websiteInfo 块（type=text/json），
    带 tenant_info.tenant_name（如自定义域名站 xiaomi.jobs.f.mioffice.cn → 小米科技）；
    北森（zhiye.com）内嵌 BGlobal 块带 tenantInfo.Abbreviation（hkaco → 虹科）。
    插件会把它作为 #embedded 条目提交；这里只做确定性读取，取不到就回退域名命名。
    """
    for entry in sample.network or []:
        url = str(entry.get("url") or "")
        body = str(entry.get("response_body") or "")
        if "#embedded" not in url or not body.lstrip().startswith("{"):
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        tenant = (data.get("tenant_info") or {}).get("tenant_name")
        if isinstance(tenant, str) and tenant.strip():
            return tenant.strip()
        info = data.get("tenantInfo")
        if isinstance(info, dict):
            for key in ("Abbreviation", "Alias"):  # 品牌简称优先于工商全称
                value = info.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _publish(db: Session, sample: Sample, output: RecipeGenOutput, records: int, attempts: int, host: str) -> PipelineResult:
    spec = output.recipe.model_dump(mode="json")
    config = {
        "recipe": spec,
        # status_map 提到顶层供归一化直接使用（与 L1 配置同构）
        "status_map": [{"pattern": e.pattern, "status": e.status} for e in output.recipe.status_map],
        "login_url": f"{_scheme(sample.url)}//{host}/",
        "session_cookie_names": [],
    }
    portal = _upsert_portal(db, host, provider_key="recipe", config=config,
                            note=f"自动配方（{output.recipe.meta.generated_by}，回放验证通过，提取 {records} 条）",
                            brand=_extract_tenant_name(sample))
    recipe = Recipe(
        portal_id=portal.id,
        spec=spec,
        confidence=output.confidence,
        status="published",
        source="auto_gen",
        created_by_sample_id=sample.id,
        attempts=attempts,
    )
    db.add(recipe)
    db.flush()
    _mark_published(db, sample, portal, f"配方已自动发布（{attempts} 次尝试，置信度 {output.confidence:.2f}）")
    return PipelineResult("published", portal_id=portal.id, recipe_id=recipe.id,
                          note="回放验证通过，配方已发布", route="generation")


def _upsert_portal(db: Session, host: str, *, provider_key: str, config: dict, note: str, brand: str | None = None) -> Portal:
    portal = _find_portal_by_host(db, host)
    if portal is None:
        domain = registrable_domain(host)
        portal = Portal(
            # 自定义域名站（如飞书 ATS 的 *.mioffice.cn）优先用采样内嵌的租户名，
            # 看板公司一列才有可读品牌（小米科技），而不是裸域名
            name=brand or host,
            company=brand or domain,
            provider_key=provider_key,
            domains=[host],
            enabled=True,
            verified=False,
        )
        db.add(portal)
    portal.provider_key = provider_key
    portal.config = config
    portal.enabled = True
    portal.note = note
    db.flush()
    return portal


def _mark_published(db: Session, sample: Sample, portal: Portal, note: str) -> None:
    sample.portal_id = portal.id
    sample.status = "used"
    sample.pipeline_status = "published"
    sample.pipeline_note = note
    db.commit()


def _fail(db: Session, sample: Sample, note: str, errors: list[str] | None = None, *, cooldown_exempt: bool = False) -> PipelineResult:
    sample.pipeline_status = "failed"
    sample.pipeline_note = note
    # cooldown_exempt：未进入生成环节的失败（如缺少请求-响应对）不计入冷却——
    # status 保持 new，_check_cooldown 只冷却真正消耗过生成尝试的失败样本
    if sample.status == "new" and not cooldown_exempt:
        sample.status = "failed"  # 留存供后台干跑重试，不删
    db.commit()
    return PipelineResult("failed", note=note, errors=errors or [])
