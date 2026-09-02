"""页面内嵌数据提取（SSR 自研站的「接口」就是页面本身）。

与扩展 collectSamplePage 的内嵌捕获保持同一语义（验证器即测试器的前提）：
- JSON 型 script（type=application/json / text/json）：整个文本即 JSON，锚 = script id；
- 可执行 JS 赋值（window.__X__ = {...} / var x = {...}）：平衡括号截取后 JSON 解析，
  锚 = 变量名。只收解析得动的片段，控制噪声。

page 型配方的运行时（recipe_adapter GET 页面后）与采样侧共用这一套定位规则。
"""

import json
import re

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")
_JSON_TYPE_RE = re.compile(r"^(application/(ld\+)?json|text/json)$", re.I)
_ASSIGN_RE = re.compile(r"(?:window\.|self\.|var\s+|let\s+|const\s+)?([A-Za-z_$][\w$]*)\s*=\s*([\[{])")

MAX_EMBEDDED_BYTES = 512 * 1024  # 单个内嵌数据块上限；更大的是业务数据非引导态
MIN_EMBEDDED_CHARS = 100


def _parse_attrs(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for m in _ATTR_RE.finditer(attr_text or ""):
        value = m.group(2) or ""
        if value[:1] in ("\"", "'"):
            value = value[1:-1]
        attrs[m.group(1).lower()] = value
    return attrs


def _balanced_json(text: str, start: int) -> str | None:
    """从 start（指向 { 或 [）平衡括号截取；字符串感知，JSON 解析由调用方把关。"""
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str: str | None = None
    esc = False
    for i in range(start, min(len(text), start + MAX_EMBEDDED_BYTES)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == in_str:
                in_str = None
            continue
        if c in "\"'":
            in_str = c
            continue
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def iter_embedded(html: str) -> list[tuple[str, object]]:
    """产出 (anchor, obj) 列表；JSON 型 script 在前、JS 赋值在后（按文档序）。"""
    out: list[tuple[str, object]] = []
    scripts: list[tuple[dict[str, str], str]] = []
    for m in _SCRIPT_RE.finditer(html or ""):
        attrs = _parse_attrs(m.group(1))
        if attrs.get("src"):
            continue
        text = (m.group(2) or "").strip()
        if len(text) >= MIN_EMBEDDED_CHARS:
            scripts.append((attrs, text))

    for attrs, text in scripts:
        if not _JSON_TYPE_RE.match((attrs.get("type") or "").strip()):
            continue
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, (dict, list)):
            out.append((attrs.get("id") or "", obj))

    for attrs, text in scripts:
        if _JSON_TYPE_RE.match((attrs.get("type") or "").strip()):
            continue
        for am in _ASSIGN_RE.finditer(text):
            frag = _balanced_json(text, am.start(2))
            if not frag or len(frag) < MIN_EMBEDDED_CHARS:
                continue
            try:
                obj = json.loads(frag)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, (dict, list)):
                out.append((am.group(1), obj))
                break  # 每个 script 只取第一个可解析赋值
    return out


def find_embedded(html: str, anchor: str, list_path: str) -> tuple[object | None, list | None]:
    """按锚定位数据对象并取出列表（list_path 点路径）。

    anchor 为空时取第一个 list_path 可定位出 dict 列表的对象；
    命中 anchor 但路径取不出列表也算失败（宁缺毋错，不猜）。
    """
    from app.adapters.fields import dig

    for anc, obj in iter_embedded(html):
        if anchor and anchor not in anc:
            continue
        node = dig(obj, list_path) if list_path else obj
        if isinstance(node, dict):
            node = [node]
        if isinstance(node, list) and node and all(isinstance(x, dict) for x in node[:5]):
            return obj, node
        if anchor:
            continue
    return None, None
