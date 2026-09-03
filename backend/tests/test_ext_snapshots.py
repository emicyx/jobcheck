"""M1 影子链路 API 测试：配对（pair-code/pair/me）、快照上报（认证/节流/hash 去重）、
解析落档（hints 优先/失效重推/干跑重解析）、影子模式不落卡与转正落卡（复用 diff）。
"""

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.db.models import Application, DeviceToken, Portal, Snapshot, User
from conftest import make_user

GOLDEN = Path(__file__).parent / "golden_samples"


def _golden(name: str) -> dict:
    # 真实站点测试数据不入库（用户拍板 2026-09-03）：私有 golden 放
    # golden_samples/private/（gitignore），克隆环境缺失时跳过而非失败
    path = next((p for p in (GOLDEN / name, GOLDEN / "private" / name) if p.exists()), None)
    if path is None:
        pytest.skip(f"私有 golden 未提供（真实站点数据不入库）: {name}")
    g = json.loads(path.read_text(encoding="utf-8"))
    return g.get("snapshot") or g.get("sample")


def _admin_client(client, db):
    db.add(User(email="admin@test.com", password_hash=hash_password("password123"), role="admin"))
    db.commit()
    client.post("/api/auth/login", json={"email": "admin@test.com", "password": "password123"})
    return client


def _pair(client) -> str:
    """注册用户登录 → 生成配对码 → 换 Bearer token。"""
    resp = client.post("/api/ext/pair-code")
    assert resp.status_code == 201, resp.text
    code = resp.json()["code"]
    resp = client.post("/api/ext/pair", json={"code": code, "device_label": "test-browser"})
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── 配对 ──────────────────────────────────────────────


def test_pair_flow_requires_login(auth_client):
    resp = auth_client.post("/api/ext/pair-code")
    assert resp.status_code == 201
    assert len(resp.json()["code"]) == 6


def test_pair_code_requires_session(client):
    assert client.post("/api/ext/pair-code").status_code == 401


def test_pair_code_single_use_and_invalid(auth_client, db):
    code = auth_client.post("/api/ext/pair-code").json()["code"]
    # 错码拒绝
    assert auth_client.post("/api/ext/pair", json={"code": "000000"}).status_code == 400
    # 正码换取
    resp = auth_client.post("/api/ext/pair", json={"code": code})
    assert resp.status_code == 201
    token = resp.json()["token"]
    assert len(token) == 48
    # 复用拒绝
    assert auth_client.post("/api/ext/pair", json={"code": code}).status_code == 400
    # token 可用且只存哈希
    assert auth_client.get("/api/ext/me", headers=_bearer(token)).status_code == 200
    row = db.scalar(select(DeviceToken).where(DeviceToken.status == "paired"))
    assert row.token_hash != token and len(row.token_hash) == 64
    assert row.code is None


def test_pair_code_expires(auth_client, db):
    code = auth_client.post("/api/ext/pair-code").json()["code"]
    row = db.scalar(select(DeviceToken).where(DeviceToken.code == code))
    row.expires_at = row.expires_at - timedelta(minutes=30)
    db.commit()
    assert auth_client.post("/api/ext/pair", json={"code": code}).status_code == 400


def test_new_pair_code_invalidates_previous(auth_client, db):
    c1 = auth_client.post("/api/ext/pair-code").json()["code"]
    c2 = auth_client.post("/api/ext/pair-code").json()["code"]
    # 旧码已被作废，新码可用
    assert auth_client.post("/api/ext/pair", json={"code": c1}).status_code == 400
    assert auth_client.post("/api/ext/pair", json={"code": c2}).status_code == 201


# ── 快照上报：认证 / 节流 / 去重 ──────────────────────


def test_snapshot_requires_bearer(auth_client):
    assert auth_client.post("/api/ext/snapshots", json={"url": "https://x.com/a"}).status_code == 401
    assert auth_client.post(
        "/api/ext/snapshots", json={"url": "https://x.com/a"}, headers=_bearer("bad" * 8)
    ).status_code == 401


def test_snapshot_upload_parses_and_creates_portal(auth_client, db, monkeypatch):
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = _golden("feishu_qunar_like.json")
    resp = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "parsed"
    assert body["parsed_count"] == 1
    assert body["route"] == "platform"
    # 门户 upsert：品牌命名沿用内嵌租户名；平台实证状态码映射落 config
    portal = db.get(Portal, body["portal"]["id"])
    assert portal.name == "去哪儿"
    assert portal.provider_key == "snapshot"
    assert portal.enabled is False
    hints = portal.config["hints"]
    assert hints["url"].endswith("/api/v1/search/user/applications")
    assert hints["list_json_path"] == "data.delivery_list"
    assert {"pattern": "^3$", "status": "written_test"} in portal.config["status_map"]
    # 转正（默认）：解析成功即落卡，飞书数字码 '3' 经 status_map → written_test
    cards = list(db.scalars(select(Application)))
    assert len(cards) == 1
    assert cards[0].job_title == "AI应用开发工程师（测试开发）"
    assert cards[0].current_status == "written_test"
    assert cards[0].company == "去哪儿"
    # 快照记录解析结果
    row = db.get(Snapshot, body["snapshot_id"])
    assert row.parse_status == "parsed" and row.parsed_count == 1
    assert row.domain == "feishu.cn"


def test_shadow_flag_still_supported(auth_client, db, monkeypatch):
    """snapshot_shadow_mode=True 时只解析不落卡（语义保留，供灰度切换）。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    monkeypatch.setattr(settings, "snapshot_shadow_mode", True)
    token = _pair(auth_client)
    resp = auth_client.post("/api/ext/snapshots", json=_golden("moka_like.json"), headers=_bearer(token))
    assert resp.status_code == 201 and resp.json()["status"] == "parsed"
    assert db.scalars(select(Application)).first() is None


def test_oppo_snapshot_full_chain(auth_client, db, monkeypatch):
    """OPPO 校招全链路（2026-09-03 云端实盘异常回归）：状态在流程节点数组里，
    平台规格命中 → 建门户（规格自带品牌名 OPPO，无租户内嵌/DOM title 可用）→
    码表归一化落卡：进行中筛选 / 被拒 / Offer。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    resp = auth_client.post("/api/ext/snapshots", json=_golden("oppo_progress_like.json"), headers=_bearer(token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "parsed" and body["route"] == "platform"
    assert body["parsed_count"] == 3

    portal = db.get(Portal, body["portal"]["id"])
    assert portal.name == "OPPO" and portal.company == "OPPO"
    assert portal.config["hints"]["list_json_path"] == "data.*.deliveryPositionRecordList"

    cards = {c.job_title: c for c in db.scalars(select(Application))}
    assert len(cards) == 3
    assert cards["AI算法工程师"].current_status == "screening"
    assert cards["软件开发工程师（Android）"].current_status == "rejected"
    assert cards["数据分析师"].current_status == "offer"
    assert cards["AI算法工程师"].company == "OPPO"
    assert cards["AI算法工程师"].work_location == "深圳市"


def test_connected_sites_endpoints(auth_client, db, monkeypatch):
    """已连接站点清单：扩展 Bearer 版（自动同步数据源）与前端会话版共用同一实现。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    auth_client.post("/api/ext/snapshots", json=_golden("feishu_qunar_like.json"), headers=_bearer(token))
    auth_client.post("/api/ext/snapshots", json=_golden("beisen_trap_like.json"), headers=_bearer(token))

    ext_resp = auth_client.get("/api/ext/sites", headers=_bearer(token))
    assert ext_resp.status_code == 200
    sites = ext_resp.json()["sites"]
    assert {s["domain"] for s in sites} == {"feishu.cn", "zhiye.com"}
    feishu_site = next(s for s in sites if s["domain"] == "feishu.cn")
    assert feishu_site["name"] == "去哪儿"
    assert feishu_site["url"].startswith("https://hf7l9aiqzx.jobs.feishu.cn")
    assert feishu_site["login_suspect"] is False

    web_resp = auth_client.get("/api/portals/connected")
    assert web_resp.status_code == 200
    assert {s["domain"] for s in web_resp.json()["sites"]} == {"feishu.cn", "zhiye.com"}


def test_snapshot_hash_dedup(auth_client, monkeypatch):
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = _golden("xiaomi_feishu_like.json")
    first = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token))
    assert first.status_code == 201
    again = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token))
    assert again.status_code == 200
    assert again.json()["status"] == "duplicate"
    assert again.json()["snapshot_id"] == first.json()["snapshot_id"]


def test_manual_sync_bypasses_throttle(auth_client, monkeypatch):
    """手动「同步当前页」豁免同站节流（实盘：自动上报刚成功、手动点击即 429 入队）；
    自动路径（manual=False）仍受节流约束（上一用例覆盖）。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 10)
    token = _pair(auth_client)
    snap = _golden("moka_like.json")
    assert auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).status_code == 201
    other = json.loads(json.dumps(snap))
    other["network"][0]["response_body"] = other["network"][0]["response_body"].replace("9001", "9009")
    other["manual"] = True
    # 窗口内、同站、载荷有变化：manual=True 直接放行
    assert auth_client.post("/api/ext/snapshots", json=other, headers=_bearer(token)).status_code == 201


def test_snapshot_domain_throttle(auth_client):
    token = _pair(auth_client)
    snap = _golden("moka_like.json")
    assert auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).status_code == 201
    # 同域不同载荷：默认节流窗口内拒绝
    other = json.loads(json.dumps(snap))
    other["network"][0]["response_body"] = other["network"][0]["response_body"].replace("9001", "9009")
    resp = auth_client.post("/api/ext/snapshots", json=other, headers=_bearer(token))
    assert resp.status_code == 429


def test_snapshot_prune_keeps_recent(auth_client, db, monkeypatch):
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    monkeypatch.setattr(settings, "snapshot_keep_per_domain", 2)
    token = _pair(auth_client)
    snap = _golden("moka_like.json")
    for i in range(3):
        payload = json.loads(json.dumps(snap))
        payload["network"][0]["response_body"] = payload["network"][0]["response_body"].replace("9001", str(9000 + i))
        assert auth_client.post("/api/ext/snapshots", json=payload, headers=_bearer(token)).status_code == 201
    assert len(list(db.scalars(select(Snapshot)))) == 2


def test_moka_encrypted_snapshot_creates_cards(auth_client, db, monkeypatch):
    """星环（Moka 响应加密）全链路：密文 + #decrypted 伪条目上报 → 建门户、落卡、进已连接站点。

    这是「加密站点录入一次即出卡」的回归闸门：钩子若失效（伪条目消失），
    上报会变 no_data，本用例第一个断言就会拦下。
    """
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    resp = auth_client.post("/api/ext/snapshots", json=_golden("moka_encrypted_like.json"), headers=_bearer(token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "parsed" and body["parsed_count"] == 2
    assert body["route"] == "platform"
    portal = db.get(Portal, body["portal"]["id"])
    assert portal.domains == ["app.mokahr.com"]
    assert portal.config["hints"]["url"].endswith("#decrypted")
    assert portal.config["hints"]["list_json_path"] == "data.list"
    cards = list(db.scalars(select(Application)))
    assert len(cards) == 2
    assert {c.job_title for c in cards} == {"大数据平台开发工程师", "后端开发工程师（基础平台）"}
    sites = auth_client.get("/api/ext/sites", headers=_bearer(token)).json()["sites"]
    assert "mokahr.com" in {s["domain"] for s in sites}


def test_duplicate_upload_heals_deleted_cards(auth_client, db, monkeypatch):
    """删卡后同数据再同步（去哪儿实盘场景）：duplicate 不再短路，重放 diff 自愈补建。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = _golden("feishu_qunar_like.json")
    first = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).json()
    assert first["ingest"]["created"] == 1

    # 用户在看板删除该卡（走正式删除接口，级联历史）
    card = db.scalars(select(Application)).first()
    assert auth_client.delete(f"/api/applications/{card.id}").status_code in (200, 204)
    assert db.scalars(select(Application)).first() is None

    # 同数据再上报：payload 哈希相同 → duplicate，但 diff 重放应补建缺失卡片
    again = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token))
    assert again.status_code == 200
    body = again.json()
    assert body["status"] == "duplicate"
    assert body["ingest"]["created"] == 1
    cards = list(db.scalars(select(Application)))
    assert len(cards) == 1 and cards[0].job_title == "AI应用开发工程师（测试开发）"


def test_dom_fallback_full_pipeline(auth_client, db, monkeypatch):
    """网络原料全密文/无条目时，裁剪 DOM 兜底：建门户 + 落卡（route=dom）。
    星环（Worker 解密）与网易（未知传输）实盘场景的全链路保障。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    payload = {
        "url": "https://app.mokahr.com/campus_apply/transwarp/3196#/candidateHome/applications",
        "network": [
            {
                "url": "https://app.mokahr.com/api/outer/ats-apply/personal-center/applications",
                "method": "POST",
                "request_body": "{}",
                "response_body": '{"data": "D2sYoWg+", "necromancer": "f312"}',
            }
        ],
        "dom": (
            "<html><body><div class=\"apply-list\">"
            "<div class=\"row\"><span>大数据平台开发工程师</span><span>2026-08-20</span><span>简历评估中</span></div>"
            "<div class=\"row\"><span>后端开发工程师（基础平台）</span><span>2026-08-18</span><span>笔试</span></div>"
            "</div></body></html>"
        ),
    }
    resp = auth_client.post("/api/ext/snapshots", json=payload, headers=_bearer(token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "parsed" and body["route"] == "dom" and body["parsed_count"] == 2
    cards = list(db.scalars(select(Application)))
    assert len(cards) == 2
    assert {c.current_status for c in cards} == {"screening", "written_test"}
    sites = auth_client.get("/api/ext/sites", headers=_bearer(token)).json()["sites"]
    assert "mokahr.com" in {s["domain"] for s in sites}


def test_dom_added_to_unchanged_network_is_new_snapshot(auth_client, db, monkeypatch):
    """网易实盘回归：network 不变、首次带 dom 上报不得判 duplicate（哈希须含 dom），
    否则新 dom 被 duplicate 短路丢掉、回放的旧快照又没有 dom。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    net = [
        {
            "url": "https://campus.game.163.com/api/campuspc/apply/find",
            "method": "POST",
            "request_body": "{}",
            "response_body": '{"data": "enc", "necromancer": "x"}',
        }
    ]
    first = auth_client.post(
        "/api/ext/snapshots",
        json={"url": "https://campus.game.163.com/app/personal/apply", "network": net},
        headers=_bearer(token),
    )
    assert first.status_code == 201 and first.json()["status"] == "no_data"

    dom = (
        "<html><body><div class=\"list\">"
        "<div class=\"row\"><span>游戏服务器开发工程师</span><span>2026-08-25</span><span>简历筛选</span></div>"
        "<div class=\"row\"><span>游戏客户端开发工程师</span><span>2026-08-24</span><span>已投递</span></div>"
        "</div></body></html>"
    )
    second = auth_client.post(
        "/api/ext/snapshots",
        json={"url": "https://campus.game.163.com/app/personal/apply", "network": net, "dom": dom},
        headers=_bearer(token),
    )
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["status"] == "parsed" and body["route"] == "dom" and body["parsed_count"] == 2
    assert len(list(db.scalars(select(Application))) ) == 2

    # 完全相同载荷再报 → duplicate，自愈回放的是带 dom 的最新快照（删卡可补建）
    card = db.scalars(select(Application)).first()
    auth_client.delete(f"/api/applications/{card.id}")
    third = auth_client.post(
        "/api/ext/snapshots",
        json={"url": "https://campus.game.163.com/app/personal/apply", "network": net, "dom": dom},
        headers=_bearer(token),
    )
    assert third.status_code == 200
    assert third.json()["status"] == "duplicate"
    assert third.json()["ingest"]["created"] == 1
    assert len(list(db.scalars(select(Application)))) == 2


def test_moka_multi_tenant_separate_portals(auth_client, db, monkeypatch):
    """炎魂实盘回归：同注册域（mokahr.com）的两个 Moka 租户必须分成两个门户、
    品牌名取自 DOM title、互不挤占节流窗口。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 10)  # 用默认节流考验隔离
    token = _pair(auth_client)

    def _up(url, title, job):
        dom = (
            f"<html><head><title>{title} - 校园招聘</title></head><body>"
            f'<div class="list"><div class="row"><span>{job}</span><span>2026-08-26</span><span>初筛</span></div>'
            f'<a class="lic" href="http://beian">京公网安备 11010802024479号</a></body></html>'
        )
        return auth_client.post(
            "/api/ext/snapshots",
            json={"url": url, "network": [], "dom": dom},
            headers=_bearer(token),
        )

    r1 = _up(
        "https://app.mokahr.com/campus_apply/yanhun/24017#/candidateHome/applications",
        "炎魂网络",
        "AI应用开发工程师（2027届）",
    )
    # 同域另一租户紧跟其后：不得被 429 节流（site_key 隔离）
    r2 = _up(
        "https://app.mokahr.com/campus_apply/transwarp/3196#/candidateHome/applications",
        "星环科技",
        "大数据平台开发工程师",
    )
    assert r1.status_code == 201 and r1.json()["status"] == "parsed", r1.text
    assert r2.status_code == 201 and r2.json()["status"] == "parsed", r2.text

    portals = list(db.scalars(select(Portal).where(Portal.provider_key == "snapshot")))
    assert len(portals) == 2
    by_name = {p.name: p for p in portals}
    assert set(by_name) == {"炎魂网络", "星环科技"}
    assert by_name["炎魂网络"].config["site_key"] == "app.mokahr.com/yanhun"
    assert by_name["星环科技"].config["site_key"] == "app.mokahr.com/transwarp"
    # 备案页脚不得成为卡片
    cards = list(db.scalars(select(Application)))
    assert {c.job_title for c in cards} == {"AI应用开发工程师（2027届）", "大数据平台开发工程师"}
    sites = auth_client.get("/api/ext/sites", headers=_bearer(token)).json()["sites"]
    assert {s["name"] for s in sites} == {"炎魂网络", "星环科技"}


# ── 解析优先级：平台规格（校准）> hints（缓存）> 全量扫描 ──


def test_calibrated_spec_directly_hits_on_revisit(auth_client, db, monkeypatch):
    """有平台规格的站点：重访由规格直接命中（无需 hints，更不需全量扫描）。
    规格优先于缓存是门户自愈的前提——见携程污染 hints 回归。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = json.loads(json.dumps(_golden("beisen_trap_like.json")))
    first = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).json()
    assert first["route"] == "platform"

    snap2 = json.loads(json.dumps(snap))
    snap2["network"][1]["response_body"] = snap2["network"][1]["response_body"].replace(
        "简历初筛", "笔试中"
    )
    second = auth_client.post("/api/ext/snapshots", json=snap2, headers=_bearer(token)).json()
    assert second["route"] == "platform"
    assert second["preview"][0]["status_raw"] == "笔试中"


def test_hints_cache_on_uncalibrated_site(auth_client, db, monkeypatch):
    """无平台规格的站点（腾讯 join.qq）：hints 缓存优先于全量扫描；
    站点改版（hints 路径失效）自动重推，扫描兜底并刷新 hints。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = json.loads(json.dumps(_golden("tencent_like.json")))
    first = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).json()
    assert first["route"] == "heuristics"

    snap2 = json.loads(json.dumps(snap))
    snap2["network"][0]["response_body"] = snap2["network"][0]["response_body"].replace(
        "后端开发工程师（腾讯云）", "前端开发工程师（腾讯云）"
    )
    second = auth_client.post("/api/ext/snapshots", json=snap2, headers=_bearer(token)).json()
    assert second["route"] == "hints"
    assert second["preview"][0]["job_title"] == "前端开发工程师（腾讯云）"

    # hints 路径失效：自动重推，全量扫描兜底并刷新 hints
    portal = db.get(Portal, first["portal"]["id"])
    portal.config = {**portal.config, "hints": {**portal.config["hints"], "list_json_path": "Gone.Nowhere"}}
    db.commit()
    snap3 = json.loads(json.dumps(snap2))
    snap3["network"][0]["response_body"] = snap3["network"][0]["response_body"].replace(
        "前端开发工程师（腾讯云）", "测试开发工程师（腾讯云）"
    )
    third = auth_client.post("/api/ext/snapshots", json=snap3, headers=_bearer(token)).json()
    assert third["route"] == "heuristics"
    refreshed = db.get(Portal, first["portal"]["id"]).config["hints"]
    assert refreshed["list_json_path"] == "data"


def test_platform_spec_heals_poisoned_hints(auth_client, db, monkeypatch):
    """携程实盘回归（快照 #27 事故）：hints 曾被旧版引擎钉上语义残缺的单字段映射
    （statusInfoCN →「进行中」落待确认），且该映射是「功能性」的——照样能提取出
    记录、永远不会因失效重推被淘汰。平台规格必须压过缓存，把门户映射纠正回来。"""
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = json.loads(json.dumps(_golden("ctrip_like.json")))
    first = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).json()
    portal_id = first["portal"]["id"]

    # 模拟旧引擎钉上的残缺 hints（06:22 事故现场）
    portal = db.get(Portal, portal_id)
    portal.config = {**portal.config, "hints": {
        "url": "https://careers.ctrip.com/api/hrrecruit/getApplyJobRecord",
        "list_json_path": "applyJobAdList",
        "field_map": {"job_title": "jobTitle", "status_raw": "statusInfoCN",
                      "work_location": "city", "applied_at": "applyTime"},
    }}
    db.commit()

    snap2 = json.loads(json.dumps(snap))
    snap2["network"][-1]["response_body"] = snap2["network"][-1]["response_body"].replace(
        "进行中", "已通过"
    )
    again = auth_client.post("/api/ext/snapshots", json=snap2, headers=_bearer(token)).json()
    assert again["route"] == "platform"  # 规格压过被污染的 hints
    assert again["preview"][0]["status_raw"] == "测评 已通过"  # 拼接映射，而非单字段「已通过」
    healed = db.get(Portal, portal_id).config["hints"]["field_map"]
    assert healed["status_raw"] == "phaseInfoCN+statusInfoCN"


def test_admin_reparse_updates_result(auth_client, client, db, monkeypatch):
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = _golden("feishu_qunar_like.json")
    created = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).json()
    sid = created["snapshot_id"]
    _admin_client(client, db)
    # 人为弄坏结果 + hints，干跑重解析应跳过 hints 恢复
    row = db.get(Snapshot, sid)
    row.parse_status = "no_data"
    row.parsed_count = 0
    portal = db.get(Portal, row.portal_id)
    portal.config = {**portal.config, "hints": {**portal.config["hints"], "field_map": {}}}
    db.commit()
    resp = client.post(f"/api/admin/snapshots/{sid}/reparse")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "parsed"
    assert resp.json()["route"] == "platform"  # skip_hints → 全量扫描
    row = db.get(Snapshot, sid)
    assert row.parse_status == "parsed" and row.parsed_count == 1


def test_admin_snapshot_list_and_stats(auth_client, client, db, monkeypatch):
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    auth_client.post("/api/ext/snapshots", json=_golden("moka_like.json"), headers=_bearer(token))
    empty = {"url": "https://nowhere.example.com/mine", "network": [
        {"url": "https://nowhere.example.com/api/track", "method": "POST", "response_body": "{\"ok\":1}"}
    ]}
    auth_client.post("/api/ext/snapshots", json=empty, headers=_bearer(token))
    _admin_client(client, db)
    rows = client.get("/api/admin/snapshots").json()
    assert len(rows) == 2
    assert {r["parse_status"] for r in rows} == {"parsed", "no_data"}
    assert rows[0]["user_email"] == "u1@test.com"
    stats = client.get("/api/admin/snapshots/stats").json()
    assert stats["total"] == 2 and stats["parsed"] == 1
    assert stats["by_domain"]["mokahr.com"]["parsed"] == 1


def test_login_suspect_recorded(auth_client, db, monkeypatch):
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    token = _pair(auth_client)
    snap = json.loads(json.dumps(_golden("moka_like.json")))
    snap["login_suspect"] = True
    body = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).json()
    assert "疑似未登录" in body["note"]
    assert db.get(Snapshot, body["snapshot_id"]).login_suspect is True


# ── 转正路径：影子开关关闭 → 复用 ingest_applications 落卡 diff ──


def test_shadow_off_creates_and_updates_cards(auth_client, db, monkeypatch):
    monkeypatch.setattr(settings, "snapshot_throttle_minutes", 0)
    monkeypatch.setattr(settings, "snapshot_shadow_mode", False)
    token = _pair(auth_client)
    snap = json.loads(json.dumps(_golden("beisen_trap_like.json")))

    first = auth_client.post("/api/ext/snapshots", json=snap, headers=_bearer(token)).json()
    assert first["status"] == "parsed"
    assert first["ingest"]["created"] == 1
    cards = list(db.scalars(select(Application)))
    assert len(cards) == 1
    assert cards[0].job_title == "解决方案工程师-软件方向"
    assert cards[0].current_status == "screening"  # 简历初筛 → 通用规则
    assert cards[0].source == "auto"

    # 状态变化 → 走更新路径，写历史
    snap2 = json.loads(json.dumps(snap))
    snap2["network"][1]["response_body"] = snap2["network"][1]["response_body"].replace("简历初筛", "已拒绝")
    second = auth_client.post("/api/ext/snapshots", json=snap2, headers=_bearer(token)).json()
    assert second["ingest"]["updated"] == 1
    cards = list(db.scalars(select(Application)))
    assert len(cards) == 1  # 不重复建卡
    assert cards[0].current_status == "rejected"
    history = cards[0].history
    assert [(h.from_status, h.to_status) for h in history] == [(None, "screening"), ("screening", "rejected")]
