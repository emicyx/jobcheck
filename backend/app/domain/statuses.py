"""统一状态机（DESIGN.md §6 细分版）。

单一事实来源：后端做校验与历史记录，前端经 /api/meta/statuses 取同一份定义渲染看板列。
新增状态只改这里；历史记录存 key，重放/改名都不受影响。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StatusDef:
    key: str
    label: str
    group: str  # progress | fallback | terminal | special
    order: int  # 看板列顺序
    color: str  # 浅色主题下的主题色（列头/卡片描边用）


_STATUSES: list[StatusDef] = [
    # ── 进行阶段（有序）──
    StatusDef("applied", "已投递", "progress", 10, "#8ca0b3"),
    StatusDef("screening", "简历评估中", "progress", 20, "#6188d8"),
    StatusDef("assessment", "测评中", "progress", 30, "#4aa8c0"),
    StatusDef("written_test", "笔试中", "progress", 40, "#3e9e8c"),
    StatusDef("interview_1", "一面", "progress", 50, "#d89c2e"),
    StatusDef("interview_2", "二面", "progress", 60, "#d98a2b"),
    StatusDef("interview_3", "三面", "progress", 70, "#d97b28"),
    StatusDef("hr_interview", "HR面/终面", "progress", 80, "#c96a95"),
    # ── 轮次未知兜底（官网文案只说"面试安排中"时使用，不虚构轮次）──
    StatusDef("interview_unknown", "面试中·轮次未知", "fallback", 85, "#c2a23e"),
    StatusDef("offer", "已发Offer", "progress", 90, "#4f9e57"),
    StatusDef("onboarded", "已入职", "progress", 100, "#2e7d4f"),
    # ── 终态 ──
    # 「流程终止」（closed）已并入 rejected：岗位取消/招聘结束/流程终止与被拒
    # 对求职者同为「此路不通」，无区分价值（2026-09-02 合并）；原文语义仍由
    # raw_status_text 保留
    StatusDef("rejected", "已拒绝", "terminal", 110, "#c25a5a"),
    StatusDef("withdrawn", "已撤回", "terminal", 130, "#98907f"),
    StatusDef("expired", "已过期", "terminal", 140, "#7d8590"),
    # ── 特殊 ──
    StatusDef("pending_confirm", "待确认", "special", 150, "#b08a3e"),
]

BY_KEY: dict[str, StatusDef] = {s.key: s for s in _STATUSES}

VALID_KEYS = set(BY_KEY.keys())
DEFAULT_STATUS = "applied"

BATCHES = ["提前批", "正式批", "春招", "实习"]
DEFAULT_BATCH = "正式批"


def is_valid(key: str) -> bool:
    return key in BY_KEY


def label(key: str) -> str:
    return BY_KEY[key].label if key in BY_KEY else key


def all_defs() -> list[dict]:
    return [
        {
            "key": s.key,
            "label": s.label,
            "group": s.group,
            "order": s.order,
            "color": s.color,
        }
        for s in _STATUSES
    ]
