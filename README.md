# JobCheck · 秋招投递统一追踪平台

把分散在各公司官网的秋招投递进度，收进一张状态看板。当前为 **M1+M2+M4（管线已通）**：账号体系 + 手动/自动投递记录 + 状态看板（左侧「我的数据」个人统计侧边栏）+ 时间线 + 标签 + 门户绑定自动追踪 + **自动配方管线**（采样 → 指纹/LLM 生成 → 回放验证 → 免审批发布）。总体设计见 [DESIGN.md](DESIGN.md) 与 [LLM_DESIGN.md](LLM_DESIGN.md)。

## 技术栈

- **后端**：Python 3.12 / FastAPI / SQLAlchemy 2 / SQLite(WAL) / Argon2id / 签名 Cookie 会话 / lxml
- **前端**：Vue 3 / Vite / TypeScript / Pinia / Naive UI（浅色定制主题）/ Chart.js（仅管理后台，懒加载）
- **测试**：pytest（后端 143 例，含 golden 样本回归）

## 目录结构

```
backend/
  app/
    adapters/      # 适配器框架 + L1 JSON 适配器 + L2 配方执行器（共享 httpio/fields）
    api/           # 路由：auth / applications / tags / account / me / meta / portals / bindings / samples / admin / ext
    core/          # 配置、密码哈希、会话签名、Cookie AES-GCM 加密
    db/            # SQLAlchemy 模型与引擎（SQLite WAL + 外键）
    domain/        # 统一状态机 + 状态归一化（规则表+兜底）
    llm/           # M4 配方管线：schemas/preprocess/validator/fingerprint/heuristics/
                   #        client(记账+熔断)/providers/pipeline/classify + prompts/
    services/      # 投递逻辑 / 绑定生命周期 / 同步 diff
    scheduler.py   # APScheduler 轮询（门户级限速+指数退避）
  scripts/         # make_invite / seed_portals / mock_portal(本地演示门户)
  tests/           # pytest（143 例）+ golden_samples/
frontend/
  src/
    api/           # fetch 封装与接口
    stores/        # pinia：auth / board
    composables/   # useBindFlow（与插件协作的绑定交互流）
    views/         # 登录 / 看板 / 设置 / 管理
    components/    # 我的数据侧边栏、分布条、卡片、表单弹窗、详情抽屉、接入向导
extension/         # Chrome/Edge MV3 插件：登录态捕获 + fetch/XHR 采样（≥0.4，开发者模式加载）
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

### 飞书招聘平台模板（零 AI 成本接入）

内置了飞书招聘的"结构指纹"模板：用户在任意飞书系招聘站（去哪儿 `campus.qunar.com`、
`jobs.feishu.cn` 或企业自定义域名）的「我的投递」页采样一次，平台自动认出这是飞书 →
直接套模板生成配置 → 用采样数据考试验证 → 通过即自动上架，全程不调 AI。
识别依据：飞书域名 + 接口路径形状 + 数据字段名（`application_list`/`job_title` 等蛇形命名），
字段映射在真实采样数据上推断并被回放验证——模板认错或网站改版都会被考试拦下，不会给错数据。

本地全流程演示（模拟"接入一个用飞书的新公司"）：

```bash
# 终端 4：Mock 飞书门户（8902，模拟一个飞书招聘形状的官网）
cd backend && python -m scripts.mock_feishu_portal
```

浏览器访问 `http://127.0.0.1:8902` 点登录 → 进「我的投递」页（`/mine/apply`）→
回平台向导粘贴 `http://127.0.0.1:8902/mine/apply` → 开始采样 → 插件采集 →
管线指纹命中飞书模板自动发布 → 向导自动变为可绑定。改状态演示：
`curl -X POST "http://127.0.0.1:8902/__set_status?application_id=8002&status=面试安排中"`。

> 真实飞书站的求职者侧接口没有公开文档，模板的键名信号按飞书开放平台的实体命名构造；
> 首个真实采样（如去哪儿）到位后校准，识别不了或验证不过会自动走 AI 生成路径，不影响使用。

### 未支持网站：自动配方管线（M4）

向导里识别不到的网站（或显示"配置生成中"的门户），走采样流程：

> **⚠️ 架构重构进行中（2026-09-01 决策）**：以下「采样→配方→服务端轮询」链路经四个真实站点
> 验证暴露出结构性脆弱（采集时机依赖 + 服务端重放契约每站都要修），已决定重构为
> 「扩展端快照式同步」（浏览器作为兼容层，Cookie 不出浏览器）。方案与验证闸门见
> [REFACTOR_PLAN.md](REFACTOR_PLAN.md)，现场状态见 [HANDOFF.md](HANDOFF.md)。
> 本节描述的当前架构仍在运行，重构按 M0→M3 里程碑推进、指标达标后一刀切切换。

1. 在向导点「开始采样」；
2. 用你投递时的账号登录该官网，打开「我的投递 / 应聘进度」页；
3. 点浏览器右上角的 JobCheck 插件图标，插件采集该页裁剪后的 DOM 与**该页的 JSON 请求-响应对**（v0.4 起包装 fetch/XHR 捕获响应体），凭一次性 token 提交到平台；
4. 平台自动运行配方管线：
   - **结构指纹**（免 LLM）：命中 Moka/飞书/北森平台模板 → 参数化实例化 L1 配置 → 采样回放验证 → 发布；
   - 未命中（判定自研）→ **T1 生成**（LLM 或离线启发式）→ **确定性回放验证**（七断言：记录数/字段非空/状态覆盖/自述一致/选择器特异性/登录判定可区分/用户标识参数化）→ 验证不过自动修正（≤2 轮）；
   - 自研站兼容性（2026-09-01 强化）：列表定位支持**任意嵌套形状**（通用递归打分扫描，"有逐条申请状态"是最强信号，不会被推荐职位列表带偏）与**中文字段键**（岗位名称/投递状态等）；路径支持 `*` 展开段的**分组列表**（北森实测：`Data.*.Submissions.*.Datas`——按人/志愿分组、组内才是逐条投递，多 tab 信封自动拼接）；SSR 直出站（记录内嵌页面 JS、无列表接口）自动生成 **page 型配方**——轮询 = GET 页面本身，按锚（如 `__INITIAL_STATE__`）提取内嵌数据，无需浏览器运行时；
   - 验证通过 → **免审批发布**，向导轮询感知（约 8s）后即可绑定，绑定即真实拉取（开箱验货）；
   - 验证不过 → 不建门户，向导提示转手动记录，样本留存（`samples` 表），管理后台可干跑重试；
5. 治理：同注册域名去重复用、单门户 24h 冷却（含失败）、月预算熔断（超限暂停生成、T2 降级待确认，不影响已发布配方轮询）；
6. 运行期沉淀：轮询遇到规则表未命中的状态原文 → T2 兜底分类一次并写回规则表（同一原文全平台只调一次）。

**LLM 配置**：默认 `LLM_PROVIDER=heuristic`（离线确定性推断，零成本，适合本地演示与测试，同样必须过回放验证）。接入真实模型只需在 `backend/.env` 里切换为 `openai_compatible` 并填入 DeepSeek/GLM/Qwen 等任意 OpenAI 兼容接口的 base_url/model/api_key（换模型 = 改配置，不改代码）。用量与成本在 `/api/admin/llm-calls` 可查。

### 真实站点全链路驱动页（后端侧测试用）

对真实招聘站做全链路验收（不碰 Mock）时，可打开前端自带的
`http://localhost:5173/jc-e2e.html`：它与向导走**同一套 postMessage 契约**武装插件
（采样 intent / 绑定 intent），其余环节全部由后端 API 驱动——登录 → 武装采样 →
到目标站「我的投递」页点插件「采集当前页面」→ 轮询 `samples/mine` 看管线结果 →
identify 拿门户 → 武装绑定（插件带现有 Cookie 自动激活）→ 轮询 intent 终态。
2026-09-01 用它对小米校招（`xiaomi.jobs.f.mioffice.cn`，飞书 ATS 自定义域名站）
完成了首个真实站点验收：指纹模板命中 → 发布 → 绑定 → 服务端真实拉取投递 → 看板建卡。

腾讯（join.qq.com）、网易（campus.163.com）、携程（job.ctrip.com）、去哪儿（campus.qunar.com/飞书）、
小米（hr.xiaomi.com）已在门户库中登记为"配置生成中"，采样后管线会直接更新这些门户的配置并启用。

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

14 个细分状态（进行阶段 9 + 面试轮次未知兜底 1 + 终态 3 + 待确认 1；「已投递」已于 2026-09-02 并入「简历评估中」），定义在 `backend/app/domain/statuses.py`，前端经 `/api/meta` 取同一份；状态色全站统一——**彩色只用于编码状态**，其余界面保持墨色/灰阶。

## 里程碑进度

- [x] **M1 手动版**：账号（邮箱+密码+邀请码）、投递 CRUD、状态看板、时间线、标签、注销级联
- [x] **M2 自动化**：MV3 浏览器插件登录态捕获、门户绑定（Cookie AES-GCM 加密）、JSON 适配器 + 状态归一化、APScheduler 轮询（限速+退避）、失效检测与重登、接入向导与绑定管理 UI
- [ ] **M3 上线**：境内部署 + 备案；~~管理后台界面~~（已完成：管理员登录后导航栏「管理」进入 `/admin`——概览趋势/快照链路健康度（含干跑重解析）/用户数据/投递数据/LLM 用量，纯只读监控）；~~飞书招聘适配器~~（已用平台模板方式实现，见上）
- [x] **M4 自动配方管线（核心闭环）**：插件抓包升级（fetch/XHR 响应体）、两级模板匹配（域名 → 结构指纹，含飞书/Moka/北森模板）、T1 配方生成（OpenAI 兼容 + 离线 heuristic 提供者）、确定性回放验证（七断言 + 参数化检测）、免审批发布 + 治理（同域去重/24h 冷却/月预算熔断）、T2 状态兜底分类与规则表沉淀、golden 样本回归
- [ ] **M4 余项**：真实 LLM 提供者线上标定（腾讯/网易/携程首批采样）、dom 型配方的 Playwright 运行时、用户手改状态沉淀映射候选（管理后台界面已完成，见 M3）
