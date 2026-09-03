"""LLM DOM 解析层连通性冒烟：配好 .env 的 LLM_DOM_* 后运行。

用法：cd backend && python -m scripts.smoke_llm_dom

真实调用一次 LLM（计入 llm_calls 记账；glm-4-flash 计价下单次约几厘钱），
用规则层认不出的非模板 DOM（状态在图标 title 属性 + 英文文案）验证整条链路：
大纲压缩 → 提示词装配 → 输出校验 → 反幻觉后过滤。
"""

from app.core.config import settings
from app.db.database import SessionLocal
from app.llm.dom_parse import dom_outline, parse_dom_snapshot

# 规则版 dom_records 对此 DOM 返回空（itertext 抽不到 title 属性值），
# 必须由 LLM 接管——正好验证门控后的完整 LLM 路径
DOM = (
    "<html><head><title>冒烟科技招聘</title></head><body>"
    "<nav><a class=\"nav\">首页</a><a class=\"nav\">职位</a><a class=\"nav\">关于我们</a></nav>"
    "<div class=\"my-apps\">"
    "<div class=\"card\"><a class=\"t\">AI平台工程师（2027届）</a><span class=\"d\">平台部</span>"
    "<span>投递时间：2026-09-01</span><span class=\"ico\" title=\"已拒绝\">✕</span></div>"
    "<div class=\"card\"><a class=\"t\">大数据开发工程师</a><span class=\"d\">数据部</span>"
    "<span>投递时间：2026-09-02</span><span class=\"ico\" title=\"Interviewing\">◦</span></div>"
    "</div>"
    "<div class=\"footer\">© 2026 冒烟科技 京ICP备0000000号</div>"
    "</body></html>"
)


def main() -> int:
    if settings.llm_dom_provider != "openai_compatible" or not settings.llm_dom_api_key:
        print("✗ 未启用：.env 需要 LLM_DOM_PROVIDER=openai_compatible 和 LLM_DOM_API_KEY")
        return 2
    print(f"provider={settings.llm_dom_provider} model={settings.llm_dom_model} base={settings.llm_dom_base_url}")
    print("── DOM 大纲（实际送入提示词的部分）──")
    print(dom_outline(DOM))
    print("── 解析结果 ──")
    db = SessionLocal()
    try:
        result = parse_dom_snapshot(db, DOM, "https://smoke.example.com/apply")
    finally:
        db.close()
    if result is None:
        print("✗ 返回 None：上游失败/预算熔断/输出被后过滤全拒——看上方日志与 llm_calls 表最近一行")
        return 1
    for r in result.records:
        print(f"  ✓ {r.job_title} | {r.status_raw} | {r.applied_at} | {r.department}")
    print(f"  语义建议（高置信将沉淀 StatusRule）: {result.suggestions}")
    print(f"  reason: {result.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
