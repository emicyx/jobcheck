"""端到端验证：模拟插件对 mock 飞书门户的采样提交 → 管线发布 → 向导可绑定。

覆盖两个修复点：
1. 提交含请求-响应对的采样 → 管线应发布门户，identify 返回 enabled（向导可绑定）；
2. 提交空 network 的采样 → 管线应立即失败并给出明确原因（而非无限等待），
   且该失败不触发同域 24h 冷却——随后同域带数据的采样应能正常发布。
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # app 包在 backend/ 下

BASE = os.environ.get("JC_E2E_BASE", "http://127.0.0.1:8000/api")
MOCK = os.environ.get("JC_E2E_MOCK", "http://127.0.0.1:8902")

# 与扩展 collectSamplePage 等价的裁剪：去 script/style，属性白名单
ATTR_KEEP = {"id", "class", "href", "type", "placeholder", "title"}


def req(method: str, path: str, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(r, data=data) as resp:
            return resp.status, json.loads(resp.read().decode()), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}"), e.headers


def login():
    st, user, hdrs = req("POST", "/auth/login", {"email": "admin@jobcheck.dev", "password": "Admin12345"})
    assert st == 200, user
    return hdrs["Set-Cookie"].split(";")[0]


def fetch_mock(url: str, extra_headers: dict | None = None, data: bytes | None = None) -> str:
    headers = {"Cookie": "session_id=mock-feishu-session"}
    headers.update(extra_headers or {})
    r = urllib.request.Request(url, headers=headers, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(r) as resp:
        return resp.read().decode()


def list_network() -> list[dict]:
    """模拟插件主动探测拿到的 POST 请求-响应对（按真实契约带头调用 mock）。"""
    from scripts.mock_feishu_portal import CSRF_VALUE, WEBSITE_PATH

    body = fetch_mock(
        MOCK + "/api/v1/search/user/applications",
        extra_headers={"x-csrf-token": CSRF_VALUE, "website-path": WEBSITE_PATH,
                       "Content-Type": "application/json"},
        data=json.dumps({"page_no": 1, "page_size": 20}).encode(),
    )
    return [{"url": MOCK + "/api/v1/search/user/applications",
             "method": "POST", "params": {},
             "request_body": '{"page_no": 1, "page_size": 20}',
             "response_body": body}]


def trimmed_dom(raw_html: str) -> str:
    from lxml import html as lxml_html, etree

    doc = lxml_html.fromstring(raw_html)
    for el in doc.xpath("//script|//style|//noscript|//svg|//template|//iframe|//link|//meta"):
        el.getparent().remove(el)
    for el in doc.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr not in ATTR_KEEP:
                del el.attrib[attr]
    return etree.tostring(doc, encoding="unicode", method="html")[:550_000]


def submit_sample(token: str, url: str, dom: str, network: list) -> dict:
    st, data, _ = req("POST", "/samples/submit", {"token": token, "url": url, "dom": dom, "resources": [], "network": network})
    assert st == 200, (st, data)
    return data


def wait_pipeline(cookie: str, sample_id: int, timeout: float = 20.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st, mine, _ = req("GET", "/samples/mine", headers={"Cookie": cookie})
        assert st == 200, mine
        latest = next((s for s in mine if s["status"] != "pending"), None)
        if latest and latest["id"] == sample_id and latest["pipeline_status"] in ("published", "failed"):
            return latest
        time.sleep(1)
    return None


def main() -> None:
    from scripts.mock_feishu_portal import WEBSITE_PATH

    cookie = login()
    page_url = MOCK + f"/{WEBSITE_PATH}/position/application"
    network = list_network()
    page_html = fetch_mock(page_url)

    # ── 场景 4（先跑，避免被同域复用短路）：POST 型接口（飞书真实契约形状）
    #    → 指纹实例化发布 + 运行时带 CSRF 头重放 ──
    st, intent4, _ = req("POST", "/samples/intents", headers={"Cookie": cookie})
    s4 = submit_sample(intent4["token"], page_url, trimmed_dom(page_html), network)
    latest4 = wait_pipeline(cookie, s4["id"])
    print(f"场景4 管线 → status={latest4['pipeline_status']} note={latest4['pipeline_note']}")
    assert latest4 and latest4["pipeline_status"] == "published", latest4

    # 发布的门户必须带 POST method + 请求体 + CSRF 头计划，且运行时能真正拉到数据
    import sqlite3
    con = sqlite3.connect("jobcheck.db")
    portal_row = con.execute(
        "SELECT provider_key, config FROM portals WHERE id=?", (latest4["portal_id"],)
    ).fetchone()
    config = json.loads(portal_row[1])
    print(f"场景4 门户 → provider={portal_row[0]} method={config.get('list_method')} "
          f"body={config.get('list_body')} headers={config.get('list_headers')}")
    assert portal_row[0] == "json_adapter"
    assert config.get("list_method") == "POST" and config.get("list_body"), "POST 配置未携带请求体，运行时无法重放"
    assert config.get("list_headers", {}).get("x-csrf-token") == "${cookie:atsx-csrf-token}", "缺 CSRF 头计划"

    from app.adapters.json_adapter import JSONAPIAdapter
    from app.adapters import AdapterContext
    fetched = JSONAPIAdapter().fetch(
        config, AdapterContext(cookies={"session_id": "mock-feishu-session",
                                         "atsx-csrf-token": "mock-csrf-token-xyz"})
    )
    titles = [a.job_title for a in fetched]
    print(f"场景4 运行时重放 → 拉到 {len(titles)} 条: {titles}")
    assert len(fetched) == 3, "POST 配方运行时重放失败"

    # ── 场景 1：正常采样（DOM + POST 请求-响应对）→ 发布/复用 + 向导可绑定 ──
    st, intent, _ = req("POST", "/samples/intents", headers={"Cookie": cookie})
    assert st == 201, intent
    s1 = submit_sample(intent["token"], page_url, trimmed_dom(page_html), network)
    print("场景1 提交 →", s1)

    latest = wait_pipeline(cookie, s1["id"])
    assert latest, "管线 20s 内未出结果（向导会一直等）"
    print(f"场景1 管线 → status={latest['pipeline_status']} note={latest['pipeline_note']}")
    assert latest["pipeline_status"] == "published", latest

    st, portal, _ = req("POST", "/portals/identify", {"url": page_url}, headers={"Cookie": cookie})
    assert st == 200, portal
    print(f"场景1 识别 → {portal['name']} enabled={portal['enabled']} provider={portal['provider_key']}")
    assert portal and portal["enabled"], "向导轮询 identify 拿不到 enabled 门户，无法进入绑定步骤"

    # ── 场景 2：空 network（旧版插件）→ 立即失败、不进冷却 ──
    st, intent2, _ = req("POST", "/samples/intents", headers={"Cookie": cookie})
    s2 = submit_sample(intent2["token"], page_url, trimmed_dom(page_html), [])
    latest2 = wait_pipeline(cookie, s2["id"], timeout=10)
    assert latest2, "空 network 提交后管线无结果（旧版 bug：向导等 8 分钟无提示）"
    print(f"场景2 管线 → status={latest2['pipeline_status']} note={latest2['pipeline_note']} sample_status={latest2['status']}")
    assert latest2["pipeline_status"] == "failed" and "请求-响应对" in (latest2["pipeline_note"] or ""), latest2

    # 同域立刻重试（带数据）：不应被场景 2 的失败冷却
    st, intent3, _ = req("POST", "/samples/intents", headers={"Cookie": cookie})
    s3 = submit_sample(intent3["token"], page_url, trimmed_dom(page_html), network)
    latest3 = wait_pipeline(cookie, s3["id"])
    print(f"场景3 冷却重试 → status={latest3['pipeline_status']} note={latest3['pipeline_note']}")
    assert latest3 and latest3["pipeline_status"] == "published", "空 network 失败不应触发同域冷却"

    print("\n✓ 端到端全部通过：POST 接口全链路可用；采样 → 管线发布 → 向导可绑定；空数据快速失败且不阻塞重试")


if __name__ == "__main__":
    main()
