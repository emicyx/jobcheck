"""本地 Mock 门户：模拟「登录页 + 投递列表 JSON API」的招聘官网，用于 M2 全链路演示与测试。

运行：python -m scripts.mock_portal  （127.0.0.1:8901）
"""

from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

app = FastAPI(title="Mock 门户")

SESSION_COOKIE = "mk_session"
SESSION_VALUE = "mock-session-token"

# 门户状态可运行时修改，用于演示「状态变化 → 自动写历史」
_JOBS: list[dict] = [
    {"id": "1001", "positionName": "后端开发工程师", "departmentName": "CSIG",
     "statusText": "简历评估中", "deliverTime": "2026-08-25"},
    {"id": "1002", "positionName": "数据分析", "departmentName": "平台与内容",
     "statusText": "笔试中", "deliverTime": "2026-08-27"},
    {"id": "1003", "positionName": "前端开发工程师", "departmentName": "CSIG",
     "statusText": "已终止", "deliverTime": "2026-08-20"},
]

_LOGIN_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>Mock 招聘门户 - 登录</title>
<style>body{font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#f5f6f8;margin:0}
.card{background:#fff;padding:40px;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.08);text-align:center}
button{padding:10px 28px;border:0;border-radius:8px;background:#223a5e;color:#fff;font-size:15px;cursor:pointer;margin-top:16px}
.ok{color:#2e7d4f;display:none;margin-top:12px}</style></head>
<body><div class="card"><h2>Mock 招聘门户</h2><p>模拟官网登录：点击即视为「输入手机验证码并登录」</p>
<button onclick="doLogin()">登录（模拟）</button><div class="ok" id="ok">✓ 登录成功，可关闭本页</div></div>
<script>async function doLogin(){await fetch('/do-login',{method:'POST'});document.getElementById('ok').style.display='block'}</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def login_page():
    return _LOGIN_PAGE


@app.post("/do-login")
def do_login(response: Response):
    response.set_cookie(SESSION_COOKIE, SESSION_VALUE, httponly=True, samesite="lax")
    return {"ok": True}


def _auth(cookies: dict):
    if cookies.get(SESSION_COOKIE) != SESSION_VALUE:
        raise HTTPException(401, detail={"code": "SESSION_INVALID"})


@app.get("/api/candidate/applications")
def applications(request: Request):
    _auth(request.cookies)
    return {"data": {"list": _JOBS}}


@app.post("/__set_status")
def set_status(job_id: str, status: str):
    """测试辅助：修改某条投递的状态文案，演示自动同步。"""
    for job in _JOBS:
        if job["id"] == job_id:
            job["statusText"] = status
            return {"ok": True, "job": job}
    raise HTTPException(404, "job not found")


@app.post("/__add_job")
def add_job(position_name: str, status: str = "已投递"):
    """测试辅助：新增一条投递，演示自动建卡。"""
    _JOBS.append({
        "id": str(2000 + len(_JOBS)),
        "positionName": position_name,
        "departmentName": "新部门",
        "statusText": status,
        "deliverTime": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
    })
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8901)
