# REFACTOR_PLAN — 绑定与监控架构重构方案（2026-09-01 定稿，待执行）

> 新会话开工方式：读本文档 + `HANDOFF.md`（环境现场）。本方案已经用户两次方向性拍板：
> ① 监控自动性 = **默认后台自动同步**；② 迁移 = **终点一刀切**（但删除动作由 M3 指标闸门触发，
> 不由信心触发）。方案在两次 ExitPlanMode 审批中未获立即执行，用户要求先落盘、
> 新开会话用干净上下文执行。

## 0. 背景与决策链（为什么重构）

三轮接入史（小米→向导→自研兼容→北森收尾全部完成，85 测试全绿）之后，
用户在星环科技（app.mokahr.com，Moka 平台）再次采样失败（样本 #30），据此判断
**「服务端配方式轮询」这条产品思路整体走不通**，要求完全重构绑定与监控。

样本 #30 失败解剖（证据在 jobcheck.db）：

- 页面确实请求过列表接口：`resources`（performance 条目）里有
  `app.mokahr.com/api/outer/ats-apply/personal-center/applications`；
- 但 `network` 捕获仅 1 条（i18n 内嵌块）——`net-capture.js` 只在页面加载时注入，
  该标签页开在插件重载之前，wrapper 不在场，响应体从未进入缓冲；
- 本该拦截的用户提示被绕过：`background.js:328` 守卫条件是
  `netActive=false && network.length===0`，内嵌块被拼进 network（长度 1）守卫失效；
- 用户实盘探针（页面控制台 fetch 该 URL）：**GET → 405 `{"code":100,"msg":"地址错误"}`**，
  证实 Moka 列表接口是 POST；同时栈帧显示 `net-capture.js:48` 参与包装——该标签页
  wrapper 现在是活的（刷新后重采即可被动捕获真实 POST 契约，星环站当时未重采）。

四站复盘结论：**3.5/4 次失败在采集层（服务端/扩展与站点对话的环节），管线本身
（指纹/七断言/发布）没有输过一场；绑定后的服务端轮询也持续暴露契约脆弱性
（CSRF 轮换、需 405 自愈、每站都要修）**。新架构=保留被证明有效的组件
（访问时捕获+解析），删除反复失败的组件（服务端存 Cookie 重放契约）。

## 1. 调研结论（业界最佳实践，2026-09-01 搜索）

**无一例外：浏览器作为兼容层，数据在用户访问时采集，服务端不存凭证轮询。**

| 产品 | 模式 | 来源 |
|---|---|---|
| Huntr / Teal | 扩展在用户浏览页面时一键捕获职位（保存到看板手动管状态），主推 autofill；无服务端轮询 | huntr.co、tealhq.com |
| JobRight | 只追踪「经它投递」的闭环应用 | jobright.ai |
| 国内（Offer情报局/OfferLink/Offerbiu/塔塔网申） | 全部是「网申自动填表 + 手动进度管理」 | 各官网 |
| Distill Web Monitor | **扩展后台开真实标签页监控动态页面**是产品级成熟机制；动态页必须真实 tab 不能纯 fetch；MV3 service worker 生命周期是已知坑（用 alarms + 即用即关规避） | distill.io 文档/论坛 |

「没人做自动追状态」≠ 不可行：竞品商业重心是填表/简历，手动管状态对其用户够用；
国内秋招用户用飞书多维表格手动追几十个投递点 = 痛点真实存在。难的恰恰是站点兼容，
而扩展方案把兼容交给浏览器（唯一已会说所有站点语言的组件）。
自动追「状态变化」的业界成熟备选通道是**邮件解析**（各家 ATS 都发状态邮件），
列为 roadmap，本期不做。

## 2. 新架构总览

**用户旅程**：装插件 → 平台向导出 6 位配对码 → popup 输入完成配对 → 用户照常逛招聘站
→ 扩展在投递页自动采集上报 → 后端建门户+卡片自动出现。**默认开启后台自动同步**：
扩展每小时 alarms 遍历已连接站点，静默开隐藏标签页刷新投递页（站点自己的 JS 带登录态
拉数据）→ 被动捕获 → 上报 → 关标签页。**Cookie 永不离开浏览器**（现架构要上传全量
Cookie 并 AES 加密存储服务端，一并退役）。

### 2.1 四层采集器（扩展）

1. net-capture 被动缓冲（已有组件，常驻所有页面 document_start MAIN world）；
2. **投递页检测器**（新增）：URL 特征词（application/delivery/apply/record/
   personal-center…）+ 数据特征（缓冲中出现含状态字段的列表 JSON，字段词典逻辑
   移植自后端 heuristics）双条件防误报；同域节流 ≥10min；
3. **资源回放兜底**（新增）：performance resources 筛候选 → GET→POST{} 递进重放；
   已知平台探测规格（飞书规格自 background.js 移植；Moka POST 契约、北森
   POST {} GetAllDeliveryRecord 契约入库——Moka 具体请求体待重采时校准）；
4. SSR 内嵌块提取（已有组件）。

上报前剔除常见 PII 键（Name/Mobile/Email/Phone）。

### 2.2 后端 ingest（核心简化：删除「配方」概念）

- 扩展上报**原料**（网络条目 JSON 原文），后端**现场解析**（复用
  `llm/heuristics.py` + `llm/extract.py` + `llm/embedded.py`，全部保留）；
- 无七断言发布闸门——数据是用户页面上真实存在的展示内容，不是待重放契约；
  ingest 校验仅为：能定位列表 + title/status 非空；
- 解析成功后 `list_json_path`/`field_map` 落档为 **portal_hints** 挂在 Portal 上，
  同域下次快照优先用、失效自动重推（轻量替代 Recipe）；
- **解析 bug 服务端热修，不再发插件**——直击本轮核心痛点；
- diff/建卡/历史/T2 分类复用 `sync_applications`（sync.py:42-133）抽出的
  `ingest_applications` 纯函数；
- 登录态：后台 tab 采到登录页 → 上报「疑似未登录」→ 连接标 stale → 前端提醒重访。
  CSRF 轮换自愈/Cookie 加密存储/重登引导全部随旧架构消失。

## 3. 实施与验证闸门（终点一刀切，删除由指标触发）

### M0 基线复核（零成本）
盘点 golden 与 DB，记录基线数字（4 站捕获/解析成功记录、旧链路失败清单）。

### M1 影子模式（约 2 天开发，只增不删）
- 后端：`DeviceToken` 模型 + `POST /api/ext/pair-code`（登录态生成配对码）/
  `POST /api/ext/pair`（码换 token）；`Snapshot` 模型（portal/url/payload_hash/
  network 留存最近 N 条）+ `POST /api/ext/snapshots`（Bearer 认证、同域节流、
  hash 去重）；`app/services/ingest.py`（域→Portal upsert 品牌命名沿用→hints 优先/
  heuristics 兜底解析→diff）；`POST /api/admin/snapshots/{id}/reparse`（语义继承
  samples/retry）。**影子模式只解析记录结果，不落卡**；
- 扩展 v0.5.0 起步（先不动 popup/bind）：配对接码、投递页检测器、采集器
  （collectSamplePage 改造 + 资源回放 + PII 清洗）、上报队列（chrome.storage
  持久化、失败退避重试）；
- 测试：四真实站 golden（xiaomi/feishu 形状/beisen/tencent 复刻）作快照解析回归；
  配对/上报/节流/diff/hints 重推 API 测试；全量 pytest 绿；
- **跑数**：真实使用 ≥3 天。指标：捕获成功率、解析成功率、误报率。

### M2 后台自动同步试点（3 天）
- 扩展：`chrome.alarms` 每小时 tick → 虹科单站静默开隐藏 tab（tabs.create
  {active:false}）→ 等 settle（~25s）→ 采集上报 → tabs.remove；
- 观察指标：登录态保持、风控拦截（403/验证码页）、数据完整性、MV3 SW 稳定性
  （ alarms 唤醒可靠性）。

### M3 闸门与一刀切
**达标线**：捕获 ≥90%、解析 ≥90%、M2 三天无风控/无登录失效异常 → 执行一刀切。

一刀切清单：
- **删后端**：`app/scheduler.py`、`app/adapters/{json_adapter,recipe_adapter,
  httpio}.py`（**保留 fields.py** 的 dig/dig_list/parse_date）、`app/llm/{pipeline,
  fingerprint,validator,preprocess,prompts}.py`、`llm/schemas.py` 配方部分、
  `Recipe`/`Sample` 模型与 api/samples.py、admin/recipes、services/bindings.py 的
  Cookie 加密/激活探测、`core/crypto.py`、config 的 scheduler/recipe/llm_recipe 组；
- **保留**：heuristics/extract/embedded/classify(T2)/domain/{statuses,normalize}/
  applications 手动 CRUD 全链路/portals.identify/`_ensure_columns` 迁移骨架/
  llm/client.py（T2 记账共用）；
- `Binding` 瘦身为「用户↔门户连接」（去 cookie/interval 族字段）；现有卡片不动
  （复用删除降级逻辑转 manual 语义）；约 1400 行轮询架构测试删除
  （test_llm_pipeline/test_feishu_template/test_beisen_template/
  test_recipe_runtime/test_page_recipe/test_m2_bindings 主体/test_heuristics 旧形态/
  test_llm_preprocess/test_llm_validator），拆出保留 normalize/classify 断言，
  影子链路测试转正；
- **扩展**：删 bind（Cookie 探测上传）与 sample 两流程；popup 重写（配对状态/
  已连接站点列表/「同步当前页」手动按钮/自动同步每站开关）；manifest 去
  `cookies` 权限；
- **前端**：ConnectWizard → Onboarding 弹窗（装插件+配对码+引导「去投递页逛一圈」
  → 轮询卡片出现即成功）；Settings 增「已连接站点」管理（stale 提醒重访）；
  看板零改动（BoardView/board store/AppCard/AppFormModal/DetailDrawer 全保留）。

**不达标降级路线**：默认访问同步 + 手动「同步当前页」按钮（这一档已解决全部已观测
失败形态，含 Moka 类 wrapper 时机问题），后台自动同步转可选开关；邮件解析列 roadmap。

### 收尾
- e2e 重写：mock 门户 + `frontend/public/jc-e2e.html` 驱动「访问→自动上报→卡片」
  全链路（e2e_verify_sample_flow 语义迁移）；
- 真实站复验（用户配合）：小米/去哪儿/虹科/星环四站卡片正确；
- **验收标准：连续 3 个新真实站点零改动出卡**；
- 文档重写：README 工作原理章节、LLM_DESIGN 配方章节归档说明、HANDOFF 更新。

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| 投递页检测误报 | URL+数据双条件；误报仅浪费一次上报，后端解析不过即丢 |
| 检测漏报 | popup「同步当前页」手动按钮兜底 |
| MV3 SW 被杀 | chrome.alarms 官方持久机制 + storage 上报队列退避重试 |
| 后台 tab 内存 | 门户串行错峰、即用即关 |
| M2 风控未知数 | 单站试点隔离，闸门前不扩大 |
| 状态时效 | 默认每小时后台同步 ≈ 旧架构 6h 轮询精度，浏览器在线即生效 |
| PII | 扩展端剔除常见 PII 键；服务端不再存任何 Cookie（优于现状） |

## 5. 新会话开工顺序建议

1. 读本文档 + HANDOFF.md（环境现场）；
2. M0：跑基线盘点（golden 清单、DB 样本历史）；
3. M1 后端（DeviceToken/Snapshot/ingest/单测）→ M1 扩展（检测器/上报）→ 影子跑数；
4. M2 试点 → M3 闸门 → 一刀切 → 收尾。

## 6. 必须延续的纪律（HANDOFF §0 原文）

每个真实失败形态沉淀 golden/回归用例；任何改动后全量 pytest 全绿才算完成；
「不要修了一个丢了上一个」——重构期间手动记录/看板/状态机/T2 分类零回归。
