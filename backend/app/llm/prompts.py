"""提示词加载：md 文件入库、进 git、带版本号（版本号写在文件首行注释里）。"""

import re
from functools import lru_cache
from pathlib import Path

from app.domain.statuses import BY_KEY

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_VERSION_RE = re.compile(r"version:\s*([0-9.]+)")


def status_enum_doc() -> str:
    lines = []
    for s in sorted(BY_KEY.values(), key=lambda x: x.order):
        lines.append(f"- {s.key} = {s.label}（{s.group}）")
    return "\n".join(lines)


@lru_cache(maxsize=4)
def _load(name: str) -> tuple[str, str]:
    path = _PROMPTS_DIR / name
    text = path.read_text(encoding="utf-8")
    m = _VERSION_RE.search(text.splitlines()[0] if text else "")
    version = m.group(1) if m else "0"
    return text, version


def recipe_gen_system(feedback: list[str] | None = None) -> tuple[str, str]:
    """返回 (system_prompt, version)。feedback 非空 = 自修正轮。"""
    text, version = _load("recipe_gen.md")
    text = text.replace("{{STATUS_ENUMS}}", status_enum_doc())
    fb = "\n".join(f"- {e}" for e in feedback) if feedback else "（首轮生成，无）"
    text = text.replace("{{FEEDBACK}}", fb)
    return text, version


def status_classify_system() -> tuple[str, str]:
    text, version = _load("status_classify.md")
    text = text.replace("{{STATUS_ENUMS}}", status_enum_doc())
    return text, version
