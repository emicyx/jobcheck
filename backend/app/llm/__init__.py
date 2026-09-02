"""LLM 子系统（M4，LLM_DESIGN.md）。

职责边界：LLM 只产出两种数据——配方 JSON 与状态枚举值；日常轮询零 LLM 调用。
一切 LLM 输出必须通过确定性回放验证（validator），不通过不生效。
"""
