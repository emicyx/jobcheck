"""状态归一化：门户原文 → 统一状态机（DESIGN.md §6）。

优先级：门户配置 status_map > 通用兜底规则 > 待确认（显示原文，不猜）。
"""

import re

from app.domain.statuses import BY_KEY

# 通用兜底规则：跨门户常见文案（按优先级排序，先匹配先得）
_GENERIC_RULES: list[tuple[str, str]] = [
    (r"offer|录用|入职通知", "offer"),
    # 「不匹配」类是拒绝语义（bilibili 实盘「初筛阶段不匹配」曾被「初筛」关键词
    # 抢先进了简历评估）；「人才库」= 落库不推进，与感谢信同为软拒绝；
    # 「流程终止/岗位取消/招聘结束」原为独立 closed 状态，2026-09-02 并入（无区分价值）
    (r"已拒绝|不合适|未通过|淘汰|感谢信|不匹配|人才库|流程终止|已终止|已关闭|岗位取消|招聘结束", "rejected"),
    (r"已撤回|取消投递", "withdrawn"),
    (r"终面|hr面|交叉面", "hr_interview"),
    (r"三面|第三轮", "interview_3"),
    (r"二面|第二轮", "interview_2"),
    (r"一面|第一轮", "interview_1"),
    (r"面试", "interview_unknown"),  # 只说"面试中"时用轮次未知兜底
    (r"笔试", "written_test"),
    (r"测评|人才评估|在线测评", "assessment"),
    (r"评估|筛选|初筛|复筛|简历", "screening"),
    (r"已投递|投递成功", "applied"),
    (r"已入职", "onboarded"),
]


def normalize_status(raw_text: str, portal_status_map: list[dict] | None = None) -> str:
    text = (raw_text or "").strip()
    if not text:
        return "pending_confirm"

    rules: list[tuple[str, str]] = []
    for entry in portal_status_map or []:
        pattern, status = entry.get("pattern"), entry.get("status")
        if pattern and status in BY_KEY:
            rules.append((pattern, status))
    rules.extend(_GENERIC_RULES)

    for pattern, status in rules:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return status
        except re.error:
            continue  # 配置里的坏正则跳过，不让轮询挂掉
    return "pending_confirm"
