# JobCheck · 秋招投递统一追踪平台

把分散在各公司官网的秋招投递进度，收进一张状态看板。当前为 **M1+M2**：账号体系 + 手动/自动投递记录 + 状态看板 + 时间线 + 标签 + 门户绑定自动追踪。总体设计见 [DESIGN.md](DESIGN.md) 与 [LLM_DESIGN.md](LLM_DESIGN.md)。

## 技术栈

- **后端**：Python 3.12 / FastAPI / SQLAlchemy 2 / SQLite(WAL) / Argon2id / 签名 Cookie 会话
- **前端**：Vue 3 / Vite / TypeScript / Pinia / Naive UI（浅色定制主题）
- **测试**：pytest（后端 20 例）

## 目录结构

```
backend/
  app/
    adapters/      # 适配器框架 + JSON 接口适配器（Moka/自研配置驱动）
    api/           # 路由：auth / applications / tags / account / meta / portals / bindings
    core/          # 配置、密码哈希、会话签名、Cookie AES-GCM 加密
    db/            # SQLAlchemy 模型与引擎（SQLite WAL + 外键）
    domain/        # 统一状态机 + 状态归一化（规则表+兜底）
    services/      # 投递逻辑 / 绑定生命周期 / 同步 diff
    scheduler.py   # APScheduler 轮询（门户级限速+指数退避）
  scripts/         # make_invite / seed_portals / mock_portal(本地演示门户)
  tests/           # pytest（25 例）
frontend/
  src/
    api/           # fetch 封装与接口
    stores/        # pinia：auth / board
    composables/   # useBindFlow（与插件协作的绑定交互流）
    views/         # 登录 / 看板 / 设置
    components/    # 分布条、卡片、表单弹窗、详情抽屉、接入向导
extension/         # Chrome/Edge MV3 插件：登录态捕获（开发者模式加载）
DESIGN.md          # 产品与技术设计（决策记录见 §12）
LLM_DESIGN.md      # LLM 子系统实现规格（M4 自动配方管线）
```

## 自动追踪（M2）

流程：看板「接入追踪」→ 粘贴/选择门户 → 装插件（`extension/` 目录，开发者模式加载）→ 点「去登录」在官网正常登录 → 插件自动捕获 Cookie 回传 → 首次同步自动建卡 → 之后每 6 小时自动轮询；状态变化自动写时间线，登录态失效看板黄条提醒，可一键重登。

本地全链路演示（不需要任何真实公司账号）：

```bash
# 终端 1：后端（8000）
cd backend && python -m uvicorn app.main:app --port 8000
# 终端 2：Mock 门户（8901，模拟一个招聘官网）
cd backend && python -m scripts.mock_portal
# 终端 3：前端（5173）
cd frontend && npm run dev
```

在「接入追踪」里选择「Mock 演示门户」→ 按引导完成绑定；随后可用
`curl -X POST "http://127.0.0.1:8901/__set_status?job_id=1002&status=面试安排中"`
改门户状态，回看板点「立即同步」观察状态变化与时间线。

> 真实门户（小米/Moka 等）的接口配置需用真实账号登录后抓包填入
> `portals.config` 后启用（见 `scripts/seed_portals.py` 中小米示例，当前为 disabled 待验证）。

### 未支持网站：采样接入（M4 前置）

向导里识别不到的网站（或显示"配置生成中"的门户），走采样流程：

1. 在向导点「开始采样」；
2. 用你投递时的账号登录该官网，打开「我的投递 / 应聘进度」页；
3. 点浏览器右上角的 JobCheck 插件图标，插件采集该页裁剪后的 DOM 与 XHR 清单，凭一次性 token 提交到平台；
4. 平台按域名自动关联门户（`samples` 表，管理员可在 `/api/samples` 查看）；
5. 采样用于生成门户配置（当前人工/脚本处理，M4 将由 LLM 管线自动生成并回放验证）。

腾讯（join.qq.com）、网易（campus.163.com）、携程（job.ctrip.com）、去哪儿（campus.qunar.com/飞书）、
小米（hr.xiaomi.com）已在门户库中登记为"配置生成中"，识别时不再是一律拒绝。

## 快速开始

### 后端（端口 8000）

```bash
cd backend
python -m pip install -r requirements.txt
copy .env.example .env        # 修改 SECRET_KEY！首次启动会按 .env 引导管理员账号
python -m uvicorn app.main:app --port 8000
```

### 前端（端口 5173，/api 已代理到 8000）

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173 即可。生产部署用 `npm run build` 产出的 `dist/` 交由 Nginx 托管并反代 `/api`。

### 邀请码

注册需要邀请码（MVP 邀请码制，见 DESIGN.md §12）：

```bash
cd backend
python -m scripts.make_invite --uses 10
```

### 测试

```bash
cd backend
python -m pytest -q
```

## 统一状态机

16 个细分状态（进行阶段 11 + 终态 4 + 待确认），定义在 `backend/app/domain/statuses.py`，前端经 `/api/meta` 取同一份；状态色全站统一——**彩色只用于编码状态**，其余界面保持墨色/灰阶。

## 里程碑进度

- [x] **M1 手动版**：账号（邮箱+密码+邀请码）、投递 CRUD、状态看板、时间线、标签、注销级联
- [x] **M2 自动化**：MV3 浏览器插件登录态捕获、门户绑定（Cookie AES-GCM 加密）、JSON 适配器 + 状态归一化、APScheduler 轮询（限速+退避）、失效检测与重登、接入向导与绑定管理 UI
- [ ] **M3 上线**：飞书招聘适配器、境内部署 + 备案、管理后台最小版
- [ ] **M4 自研覆盖**：自动配方管线（采样 → LLM 生成 → 回放验证 → 确认发布）
