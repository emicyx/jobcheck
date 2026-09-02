"""本地 Mock 飞书招聘门户：结构与契约 1:1 复刻真实站点（2026-09-01 真实登录态实测校准）。

真实契约（hf7l9aiqzx.jobs.feishu.cn）：
- 应聘记录页 SSR 直出，初始加载不发列表 XHR（真实站因此需要插件主动探测）；
- POST /api/v1/csrf/token            → 匿名可用，种 atsx-csrf-token Cookie（7 天）；
- POST /api/v1/search/user/applications：
    缺 x-csrf-token 头 → 405；未登录 → 401 {"code":99991663,"msg":"not login"}；
    正常 → 200 {"code":0,"data":{"delivery_list":[...]}}；
- 必需头：x-csrf-token（= Cookie 值）、website-path（站点路径首段，本 Mock 为 704852）。

列表项结构按真实响应复刻：id / job_post_info.title（岗位名）/ biz_create_time（字符串毫秒）/
operation_list[]（操作时间线，末项 operation_code 即当前状态：0=已投递 1=评估中 3=笔试中）/
current_stage（真实站与实际进度不符，不使用）。

运行：python -m scripts.mock_feishu_portal  （127.0.0.1:8902）
"""

from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

app = FastAPI(title="Mock 飞书招聘门户")

WEBSITE_PATH = "704852"
SESSION_COOKIE = "session_id"
SESSION_VALUE = "mock-feishu-session"
CSRF_COOKIE = "atsx-csrf-token"
CSRF_VALUE = "mock-csrf-token-xyz"


def _delivery(
    deliv_id: str, title: str, city: str | None, ops: list[int], create_ms: str
) -> dict:
    """按真实结构构造一条投递记录。ops 为操作时间线的 operation_code 序列。"""
    return {
        "id": deliv_id,
        "user_id": "7671157756091238675",
        "portal_type": 0,
        "job_post_info": {
            "id": "9" + deliv_id[-3:],
            "title": title,
            "sub_title": None,
            "description": f"{title} 的职位描述（真实站此处为长文本）",
            "requirement": None,
            "job_category": None,
            "city_info": {"code": "CT_11", "name": city} if city else None,
            "recruit_type": {"id": "201", "name": "正式", "parent": {"id": "2", "name": "校招"}},
            "publish_time": 1785988332984,
        },
        "biz_create_time": create_ms,
        "biz_modify_time": create_ms,
        "referral_method": 0,
        "operation_list": [
            {"operation_code": code, "biz_create_time": int(create_ms) + i * 539353512 * (i + 1)}
            for i, code in enumerate(ops)
        ],
        "current_stage": {"stage_id": 0},
        "portal_delivery_tag": 1,
        "application_id": "7" + deliv_id,
        "volunteer_seq": None,
        "preferred_city_info_list": [{"code": "CT_11", "name": "北京"}],
        "accept_transfer_preferred_city": None,
        "preferred_storefront_list": None,
    }


# 三条投递：末项 operation_code 分别为 3（笔试中）/ 1（评估中）/ 3（笔试中）
_JOBS: list[dict] = [
    _delivery("8001", "服务端开发工程师", "北京", [0, 1, 3], "1786081301858"),
    _delivery("8002", "算法工程师", "北京", [0, 1], "1786254100000"),
    _delivery("8003", "产品培训生", "上海", [0, 1, 3], "1785820900000"),
]

_LOGIN_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Mock 飞书招聘门户 - 登录</title>
<style>body{font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#f5f6f8;margin:0}
.card{background:#fff;padding:40px;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}
button{padding:10px 28px;border:0;border-radius:8px;background:#3370ff;color:#fff;font-size:15px;cursor:pointer;margin-top:16px}
.ok{color:#2e7d4f;display:none;margin-top:12px}</style></head>
<body><div class="card"><h2>Mock 飞书招聘门户</h2><p>模拟官网登录：点击即视为「手机验证码登录成功」</p>
<button onclick="doLogin()">登录（模拟）</button><div class="ok" id="ok">✓ 登录成功，可关闭本页</div></div>
<script>async function doLogin(){await fetch('/do-login',{method:'POST'});document.getElementById('ok').style.display='block'}</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def login_page():
    return _LOGIN_PAGE


@app.post("/do-login")
def do_login(response: Response):
    response.set_cookie(SESSION_COOKIE, SESSION_VALUE, httponly=True, samesite="lax")
    return {"code": 0, "msg": "success"}


def _auth(cookies: dict):
    if cookies.get(SESSION_COOKIE) != SESSION_VALUE:
        # 飞书风格未登录响应：401 + code/msg 信封（msg 同时是实例化配置里的失效标记）
        raise HTTPException(401, detail={"code": 99991663, "msg": "not login"})


@app.get(f"/{WEBSITE_PATH}/position/application", response_class=HTMLResponse)
def mine_apply(request: Request):
    """「应聘记录」页：登录后可见。真实站此页 SSR 直出记录且不发列表 XHR；
    本 Mock 让页面脚本按真实前端同款契约（CSRF 引导 + POST）拉列表，
    插件的被动捕获或主动探测都能拿到同一条请求-响应对。"""
    _auth(request.cookies)
    cards = "".join(
        '<div class="apply-item"><span class="job-title">' + j["job_post_info"]["title"] + '</span>'
        '<span class="status-text">' + str(j["operation_list"][-1]["operation_code"]) + '</span></div>'
        for j in _JOBS
    )
    page = (
        '<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>应聘记录</title></head>\n'
        '<body><div id="app" class="portal-page"><h1>应聘记录</h1>\n'
        '<div class="application-list">' + cards + '</div></div>\n'
        '<script>\n'
        'const csrf = (document.cookie.match(/(?:^|;\\s*)atsx-csrf-token=([^;]+)/) || [])[1];\n'
        'const pull = (token) => fetch("/api/v1/search/user/applications", {\n'
        '  method: "POST", credentials: "include",\n'
        '  headers: {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest",\n'
        '    "x-csrf-token": token, "website-path": "' + WEBSITE_PATH + '"},\n'
        '  body: JSON.stringify({page_no: 1, page_size: 20})\n'
        '}).then(r => r.json());\n'
        'pull(csrf || "").catch(() =>\n'
        '  fetch("/api/v1/csrf/token", {method: "POST", credentials: "include"})\n'
        '    .then(r => r.json()).then(j => pull(j.data.token)));\n'
        '</script></body></html>'
    )
    return HTMLResponse(page)


@app.post("/api/v1/csrf/token")
def csrf_token(response: Response):
    """匿名刷新 CSRF：种 Cookie（7 天）并在响应体返回同值 token。"""
    response.set_cookie(CSRF_COOKIE, CSRF_VALUE, samesite="lax", max_age=7 * 86400)
    return {"code": 0, "data": {"token": CSRF_VALUE}, "message": "ok", "error": None}


@app.post("/api/v1/search/user/applications")
def search_applications(request: Request):
    """投递记录查询（真实契约：405 缺 CSRF / 401 未登录 / 200 信封）。"""
    if request.headers.get("x-csrf-token") != CSRF_VALUE:
        raise HTTPException(405, detail="csrf token missing or invalid")
    _auth(request.cookies)
    return {"code": 0, "data": {"delivery_list": _JOBS}}


@app.post("/__set_status")
def set_status(delivery_id: str, operation_code: int):
    """测试辅助：给某条投递追加一步操作，演示「状态变化 → 自动写历史」。"""
    for job in _JOBS:
        if job["id"] == delivery_id:
            job["operation_list"].append(
                {"operation_code": operation_code,
                 "biz_create_time": int(datetime.now().timestamp() * 1000)}
            )
            return {"ok": True, "job": job}
    raise HTTPException(404, "application not found")


@app.post("/__add_job")
def add_job(job_title: str, operation_code: int = 0):
    """测试辅助：新增一条投递，演示自动建卡。"""
    _JOBS.append(_delivery(
        str(9000 + len(_JOBS)), job_title, "北京", [0, max(operation_code, 0)],
        str(int((datetime.now() - timedelta(days=1)).timestamp() * 1000)),
    ))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8902)
