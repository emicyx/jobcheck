"""门户种子：Mock 演示门户 + 小米(Moka，待抓包验证)。

运行：python -m scripts.seed_portals
"""

from app.db.database import Base, SessionLocal, engine
from app.db.models import Portal

MOCK_PORTAL = dict(
    name="Mock 演示门户",
    company="演示公司",
    provider_key="json_adapter",
    domains=["localhost:8901", "127.0.0.1:8901"],
    enabled=True,
    verified=True,
    note="本地演示用：python -m scripts.mock_portal 启动后可用",
    config={
        "login_url": "http://127.0.0.1:8901/",
        "session_cookie_names": ["mk_session"],
        "list_url": "http://127.0.0.1:8901/api/candidate/applications",
        "list_method": "GET",
        "list_json_path": "data.list",
        "fields": {
            "id": "id",
            "job_title": "positionName",
            "status_raw": "statusText",
            "department": "departmentName",
            "applied_at": "deliverTime",
        },
        "session_invalid_markers": ["SESSION_INVALID"],
        "status_map": [
            {"pattern": "简历评估", "status": "screening"},
            {"pattern": "笔试", "status": "written_test"},
            {"pattern": "已终止", "status": "rejected"},
        ],
    },
)

# 真实 Moka 候选人端接口路径需登录后抓包确认，验证前保持 disabled。
XIAOMI_PORTAL = dict(
    name="小米校招",
    company="小米",
    provider_key="json_adapter",
    domains=["hr.xiaomi.com", "campus.hr.xiaomi.com", "app.mokahr.com/xiaomi"],
    enabled=False,
    verified=False,
    note="Moka 独立域名部署；list_url/字段映射待真实账号抓包后填入并启用",
    config={
        "login_url": "https://hr.xiaomi.com/campus",
        "session_cookie_names": [],
        "list_url": "",
        "list_method": "GET",
        "list_json_path": "data.list",
        "fields": {
            "id": "id",
            "job_title": "positionName",
            "status_raw": "statusText",
        },
        "status_map": [],
    },
)

def _pending(name, company, domains, login_url, note):
    """未配置完的门户：可被识别（已列入支持名单），但配置生成前不开放绑定。"""
    return dict(
        name=name,
        company=company,
        provider_key="json_adapter",
        domains=domains,
        enabled=False,
        verified=False,
        note=note,
        config={
            "login_url": login_url,
            "session_cookie_names": [],
            "list_url": "",
            "list_method": "GET",
            "list_json_path": "data.list",
            "fields": {"id": "id", "job_title": "positionName", "status_raw": "statusText"},
            "status_map": [],
        },
    )


# 腾讯：2026-09-01 用真实 Cookie 探测校准。
# join.qq.com 是「单申请进度」模型（一人一条有效投递，无列表接口）：
# getApplyProcess 返回单个对象，适配器会把 dict 自动包装为一条记录。
# currentStatus.status 码值：0=未发起（无岗位名，记录会被跳过）、2=简历筛选中（实测）、4=流程结束（前端源码注释）；
# 其余码值未验证，不猜，落到 pending_confirm 待确认列显示原始码。
TENCENT_PORTAL = dict(
    name="腾讯校招",
    company="腾讯",
    provider_key="json_adapter",
    domains=["join.qq.com", "careers.tencent.com"],
    enabled=True,
    verified=False,
    note="配置已按真实接口校准（单投递进度模型）；状态码未覆盖的部分会显示在「待确认」列",
    config={
        "login_url": "https://join.qq.com/progress.html",
        "session_cookie_names": [],
        "list_url": "https://join.qq.com/api/v1/apply/getApplyProcess",
        "list_method": "GET",
        "list_json_path": "data",
        "fields": {
            "id": "resumeId",
            "job_title": "positionInfo.applyPositionTxt",
            "status_raw": "currentStatus.status",
        },
        "session_invalid_markers": ["请登录", "not login"],
        "status_map": [
            {"pattern": "^2$", "status": "screening"},
            {"pattern": "^4$", "status": "rejected"},
        ],
    },
)

PENDING_PORTALS = [
    _pending("网易校招", "网易", ["campus.163.com", "hr.163.com", "campus.game.163.com", "leihuo.163.com"],
             "https://campus.163.com/", "自研系统；待采样生成配置（投递页：登录后「个人中心」）"),
    _pending("携程校招", "携程", ["campus.ctrip.com", "careers.ctrip.com", "job.ctrip.com"],
             "https://job.ctrip.com/", "自研系统；待采样生成配置（投递页：登录后「申请记录」）"),
    _pending("去哪儿校招", "去哪儿", ["campus.qunar.com", "jobs.feishu.cn"],
             "https://campus.qunar.com/",
             "飞书招聘；已内置飞书平台模板（结构指纹），采样后自动实例化接入——"
             "本地可先用 Mock 飞书门户验证（python -m scripts.mock_feishu_portal，127.0.0.1:8902）"),
]

SEEDS = [MOCK_PORTAL, XIAOMI_PORTAL, TENCENT_PORTAL, *PENDING_PORTALS]


def main() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        for seed in SEEDS:
            existing = db.query(Portal).filter_by(company=seed["company"]).first()
            if existing:
                for key, value in seed.items():
                    setattr(existing, key, value)
                print(f"已更新: {seed['company']}")
            else:
                db.add(Portal(**seed))
                print(f"已创建: {seed['company']}")
        db.commit()


if __name__ == "__main__":
    main()
