"""字段提取公共工具：点路径取值与日期解析。

L1 json_adapter 与 L2 配方执行器/验证器共用——「验证器即测试器」的前提是
离线回放与在线轮询跑的是同一份提取代码（LLM_DESIGN.md §2.4）。
"""

from datetime import date, datetime


def dig(data, path: str):
    """按点路径取值：a.b.0.c；支持 dict 键与 list 下标。路径为空返回原数据。

    路径含 ``+`` 时为多字段拼接语义：子路径分别取值，非空标量以空格连接。
    携程 careers 把状态拆成 phaseInfoCN（阶段）+ statusInfoCN（进度）两个
    字段，单取任一都丢语义（「进行中」落待确认），拼接出「测评 进行中」
    才能命中通用状态规则。

    段形如 ``key=value`` 时为列表过滤语义：当前节点是 dict 数组时选中首个
    ``element[key] == value`` 的元素再继续下探。OPPO 校招把状态藏在流程节点
    数组里（flowProcessTemplateList，flowProcessStatus=THE_ONGOING 为当前
    节点），没有它就无法表达「取进行中的那个节点」。
    """
    if "+" in path:
        parts = []
        for sub in path.split("+"):
            value = dig(data, sub.strip())
            if value is None or isinstance(value, (dict, list)):
                continue
            text = str(value).strip()
            if text:
                parts.append(text)
        return " ".join(parts) if parts else None
    node = data
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list):
            if "=" in part:
                key, _, expected = part.partition("=")
                node = next(
                    (x for x in node if isinstance(x, dict) and str(x.get(key)) == expected),
                    None,
                )
            else:
                try:
                    node = node[int(part)]
                except (ValueError, IndexError):
                    return None
        else:
            return None
    return node


_STAR_FANOUT_LIMIT = 50  # 单层展开元素上限（防病态结构爆炸；分组列表通常 ≤ 个位数）


def dig_list(data, path: str) -> list[dict] | None:
    """列表定位专用点路径：与 dig 同形，但支持 ``*`` 展开段——

    ``Data.Finished.Submissions.*.Datas``：``*`` 作用于数组时展开每个元素、
    作用于 dict 时展开每个值（北森等平台的分组列表：按人/志愿分组，组内才是投递数组）。
    返回拼接后的 dict 列表；dict 节点自动按一条处理（单对象语义）；无有效节点返回 None。
    """
    nodes = [data]
    for part in (path or "").split("."):
        if not part:
            continue
        nxt: list = []
        for node in nodes:
            if part == "*":
                if isinstance(node, list):
                    nxt.extend(node[:_STAR_FANOUT_LIMIT])
                elif isinstance(node, dict):
                    nxt.extend(list(node.values())[:_STAR_FANOUT_LIMIT])
            elif isinstance(node, dict):
                nxt.append(node.get(part))
            elif isinstance(node, list):
                try:
                    nxt.append(node[int(part)])
                except (ValueError, IndexError):
                    pass
        nodes = [n for n in nxt if n is not None]
        if not nodes:
            return None
    out: list[dict] = []
    matched = False
    for node in nodes:
        if isinstance(node, dict):
            matched = True
            out.append(node)
        elif isinstance(node, list):
            matched = True
            out.extend(x for x in node if isinstance(x, dict))
    # 路径命中但为空（翻页到末页）返回 []，路径无效才返回 None——二者语义不同
    return out if matched else None


def parse_date(value) -> date | None:
    """尽力解析投递时间：秒/毫秒时间戳（含字符串形式）或常见日期格式。

    飞书等平台的 biz_create_time 是字符串毫秒时间戳（"1786081301858"）。
    """
    if not value:
        return None
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        try:
            return datetime.fromtimestamp(ts, tz=None).date()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit() and len(text) >= 10:
        ts = int(text) / 1000 if int(text) > 1e12 else int(text)
        try:
            return datetime.fromtimestamp(ts, tz=None).date()
        except (OverflowError, OSError, ValueError):
            return None
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y年%m月%d日",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",  # 北森 DeliveryDate："2026-08-25 13:21"
        "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    return None
