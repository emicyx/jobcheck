"""LLM 预处理单元测试：PII 打码 / XHR 筛选排序 / DOM 裁剪与列表区定位。"""

import json
from pathlib import Path

from app.llm import preprocess

GOLDEN = json.loads((Path(__file__).parent / "golden_samples" / "tencent_like.json").read_text(encoding="utf-8"))


def test_mask_pii():
    text = "手机 13800138000 邮箱 dxw@example.com 证件 110101199003077758"
    masked = preprocess.mask_pii(text)
    assert "13800138000" not in masked
    assert "dxw@example.com" not in masked
    assert "110101199003077758" not in masked
    assert "‹pii-phone›" in masked and "‹pii-email›" in masked and "‹pii-id›" in masked
    # 岗位类文本不受影响
    assert preprocess.mask_pii("后端开发工程师 简历筛选中") == "后端开发工程师 简历筛选中"


def test_filter_network_ranking_and_blacklist():
    prepared = preprocess.filter_network(GOLDEN["sample"]["network"])
    urls = [p.url for p in prepared]
    # 埋点上报被黑名单过滤，非 JSON 被过滤
    assert len(prepared) == 1
    assert "getApplyProcess" in urls[0]
    assert prepared[0].method == "GET"
    assert prepared[0].list_score > 0


def test_filter_network_ranks_list_like_first():
    entries = [
        {"url": "https://x.com/api/config", "method": "GET", "response_body": "{\"theme\":\"dark\"}"},
        {"url": "https://x.com/api/apply/list", "method": "GET",
         "response_body": "{\"data\":[{\"positionName\":\"A\",\"statusText\":\"评估中\"}]}"},
    ]
    prepared = preprocess.filter_network(entries)
    assert "apply/list" in prepared[0].url
    assert "config" in prepared[1].url


def test_prune_dom_strips_noise_and_masks_in_package():
    dom = (
        "<html><head><script>evil()</script><style>.a{}</style></head>"
        "<body><div id=\"app\" data-v-123 onclick=\"hack()\"><p>后端开发</p></div></body></html>"
    )
    out = preprocess.prune_dom(dom)
    assert "<script" not in out and "<style" not in out
    assert "onclick" not in out  # 属性白名单外被删
    assert "data-v-123" in out  # data-* 保留
    assert "后端开发" in out

    pkg = preprocess.prepare("https://x.com/apply", dom, [])
    assert "‹pii-phone›" in pkg.dom or "13800138000" not in pkg.dom
    text = pkg.to_prompt()
    assert "<<<SAMPLE_PACKAGE_BEGIN>>>" in text and "<<<SAMPLE_PACKAGE_END>>>" in text


def test_prune_dom_localizes_list_region_when_oversize():
    cards = "".join(
        f'<div class="job-card"><span class="title">岗位{i}</span><span class="st">评估中</span></div>'
        for i in range(50)
    )
    noise = '<div class="ad">' + "x" * 300_000 + "</div>"
    dom = f"<html><body>{noise}<div class=\"apply-list\">{cards}</div></body></html>"
    out = preprocess.prune_dom(dom)
    assert len(out.encode()) <= preprocess.DOM_MAX_BYTES
    assert "岗位49" in out  # 列表区被保留
    assert "apply-list" in out
