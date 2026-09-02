# HANDOFF — 交接文档（2026-09-02 更新，新链路已转正为唯一接入方式）

> **新会话继续方式**：读 `REFACTOR_PLAN.md`（方案全文）+ 本文档（现场状态）。
> 当前进度口令：「v0.6.1 已推：applied 并入 screening + 看板『我的数据』侧边栏；
> 星环 0.5.5 重测仍待做；143 passed」。
>
> 前几轮：M0 → M1 → 转正 → v0.5.2/0.5.3/0.5.4（加密捕获迭代）→ v0.5.5（DOM 兜底）
> → dom 纳入哈希（网易 duplicate 事故）→ 网易 DOM 链路首胜 + 炎魂三重事故修复
> → v0.6.1（「已投递」并入 screening + 我的数据侧边栏 + /api/me/stats）。基线 **143 passed**。

## 0.05 v0.6.1：「已投递」状态移除 + 看板「我的数据」侧边栏（2026-09-02 夜，已推 4edf39d）

**决策（用户拍板）**：「已投递」列长期无数据——归一化规则 screening 在 applied 之前
且数字码走门户码表，同步入库几乎总落到更后阶段——无区分价值，**彻底移除该状态**
（非仅删列），并入 screening（仿 closed→rejected 合并先例，原文语义由 raw_status_text 保留）。

- 后端：`statuses.py` 删 applied、`DEFAULT_STATUS=screening`；normalize 规则
  「已投递|投递成功」→ screening；飞书码表 `^0$`→screening（ingest + fingerprint 两处）；
  models 列默认值；`main.py _ensure_columns` 幂等迁移 `UPDATE applications SET
  current_status='screening' WHERE current_status='applied'`（当前库 0 条，防旧部署残留）。
- 新 API `GET /api/me/stats`（`api/me.py`）：当前用户投递统计
  total/in_progress/terminal/month_new/by_status（状态机序、count>0），
  只统计本人、不受看板筛选影响；复用 admin applications_stats 聚合模式 + user_id 过滤。
- 前端：`stages.ts` 删列（看板 5 进行中列）；`AppFormModal` 兜底默认 screening；
  新组件 `SidePanel.vue`（投递总览 / 流程分布（点击即筛选看板，联动终态列展开与滚列）/
  个人账号（邮箱/角色/注册时间/已连接站点/标签数/设置入口）三模块），
  可折叠 + localStorage 记忆（窄屏 <1440 默认收起）；`BoardView` 改 board-body 横向布局，
  StatusBar 顶部状态条保留（用户选择）。
- prompt v2：`status_classify.md` 语义边界里 closed 残留并入 rejected（closed 状态
  早前已并入 rejected，枚举本身是 `{{STATUS_ENUMS}}` 动态注入、自动跟随）。
- 验证：143 passed 全量（含新增 me/stats 3 例：统计正确性 + 多用户隔离）；
  `npm run build` 通过；浏览器实测（真实 admin 数据）：无已投递列、侧边栏数据与顶部
  状态条一致、分布行点击筛选/再点清除、折叠展开刷新后保持、新建表单默认「简历评估中」。
- 期间发现并处理：8000 端口旧后端实例占用（旧代码），已结束，现跑新代码；无插件改动。

## 0.1 炎魂三重事故与修复（2026-09-02 夜，纯服务端，未发插件）

**网易**：DOM 兜底首次真实出卡（AI Agent工程师，route=dom）。

**炎魂**（`app.mokahr.com/campus_apply/yanhun`，快照 #12，真实 DOM 已存
`moka_yanhun_dom_like.json`）三重叠加事故——卡片曾显示
「企业 mokahr.com / 岗位 京公网安备 11010802024479号」：

1. **导航污染**：菜单项「我的简历」命中状态词典「简历」关键词 → 导航区成假
   记录组。修复：状态单元格加导航词一票否决 + ≤10 字长度上限；
2. **真状态缺词**：「初筛」不在词典（规则写的是「筛选」）→ 真实记录组因
   无状态被丢。修复：normalize 补 初筛/复筛；
3. **Moka 多租户**：星环/炎魂同 host（app.mokahr.com）并成一个门户。修复：
   `site_key()`（Moka URL 租户段，如 `app.mokahr.com/yanhun`）分门户 +
   品牌取自 DOM `<title>`（炎魂网络 - 校园招聘 → 炎魂网络）+ 同域节流/去重
   按 site_key 隔离。北森/飞书子域名本身即租户，不受影响。

**真实数据复验**：reparse #12 → 门户「炎魂网络」+ 卡片「AI应用开发工程师
（2027届）｜初筛→screening｜2026-08-26」，错卡与 mokahr.com 门户已删。

**待办**：星环用 0.5.5 重测（上次同步 05:05 无 dom，是 0.5.4 采集）；
预期 DOM 兜底出卡、门户按 transwarp 租户建档。

## 0.11 四测证据与 v0.5.5（2026-09-02 深夜）· DOM 兜底

**快照 #7/#8（星环）**：0.5.4 三钩子全开，解密槽位与 #4 完全相同（4 个），
applications 明文依然缺席；candidateInfo（主线程解析）持续可捕获——判定
**applications 解密在 Web Worker**，postMessage 回主线程为克隆对象，页面侧
任何钩子原理上不可见。**快照 #6（网易 campus.game.163.com）**：fetch/XHR
文本捕获为零（resources 证明 `api/campuspc/apply/find` 被调用），仅导航菜单
经 JSON.parse 进了一条解密槽——传输不明（arraybuffer XHR 是嫌疑之一）。

**v0.5.5 决策：启用 DOM 兜底**（REFACTOR_PLAN 预案的降级路线）——渲染出来的
记录永远在 DOM 里，与加密方式/传输层完全无关：

- 扩展：`collectSnapshotPage` 采集裁剪 DOM（≤400KB，剔 script/style/媒体，
  属性白名单 id/class/href/type/title）；XHR arraybuffer 响应解码记录；
- 后端：`Snapshot.dom` 列（`_ensure_columns` 已加迁移）；`ingest.dom_records()`
  ——lxml 找「同标签+同 class 重复兄弟行」组，行内单元格按状态词典
  （normalize_status 命中即状态列）/日期正则/最长非日期文本（岗位名）推断，
  单行组需 ≥4 单元格防误报，多组竞争按 行数>嵌套深度>标题总长 取优；
  route=`dom`；hints 对 dom 不生效（伪 URL 永不命中网络条目，每次全量扫描）；
- 测试：3 个解析级（多行列表/单卡/纯噪声）+ 1 个 API 级全链路（密文 network +
  dom → route=dom 建卡 2 张入已连接站点），基线 124 passed。

**五测闸门**：重载插件（v0.5.5）→ 刷新星环投递页（等列表渲染完）→ 手动同步；
网易同理（确认页面上确实有投递记录）。期望 route=dom 出卡。若仍 no_data：
查最新快照 dom 列内容——渲染列表结构就在里面，服务端调整行组识别参数即可
（不发插件）。注意星环同域 10 分钟节流（429 入队后 ~11 分钟自动重试，
popup 显示「已入队」属正常）。

**五测反馈修复（纯服务端，不发插件）**：网易重试显示「数据与上次一致」——
`payload_hash` 只算 network 不算 dom，network 未变即判 duplicate，新上报的
dom 被短路丢弃、自愈回放的旧快照又没有 dom。修复：dom 参与哈希
（`payload_hash(network, dom)`），dom 有变化即新快照；回归
`test_dom_added_to_unchanged_network_is_new_snapshot` 同时覆盖「带 dom 重复
上报的自愈回放」。基线 **125 passed**。插件无需重载，直接重试同步即可
（同域 10 分钟节流仍适用，撞上显示「已入队」等 ~11 分钟自动到达后看板见分晓）。

## 0.12 星环三测复盘与 v0.5.4（2026-09-02 晚）

**快照 #4 证据**：多槽位捕获成功——4 个 `#decrypted-*` 槽位（组织架构/部门列表/
候选人信息/职位分组，均为 API 信封 {code,msg,data} 形态），说明加密 API 的解密
确实走 JSON.parse（candidateInfo 密文 196B ↔ 槽位明文 profile 互证）；
**唯独 applications 的解密对象缺席**。两个候选解释（四测可分辨）：

- ① 用户在星环可能只有 **1 条投递**：list 数组长度 1，被 0.5.3 的「≥2 字典」门
  挡掉（最可能）；
- ② 该接口解密走原生 `resp.json()`（C++ 解析，不经 JSON.parse）。

**v0.5.4**：宽松门 ≥1 字典（每字典仍 ≥3 键防噪声）+ Response.json 包装 +
槽位 4→6。

**去哪儿删卡场景修复（后端）**：`POST /api/ext/snapshots` 的 payload 哈希
duplicate 分支不再短路返回，改为对已有快照**重放 ingest diff 自愈**——数据未变
≠ 看板完整，删过的卡幂等补建（回归：`test_duplicate_upload_heals_deleted_cards`）；
popup duplicate 时显示「补建了 N 张缺失的卡片」。

**四测闸门**：重载插件（v0.5.4）→ 刷新星环投递页 → 停几秒手动同步。
- 期望「已识别 N 条」且看板出卡；
- 若仍「未识别到投递数据」→ 查最新快照 6 个 `#decrypted-*` 槽位：投递对象在
  → 服务端加解析规则（不发插件）；投递对象仍不在 → 解密不走 JSON.parse 也
  不走 Response.json（如 Web Worker），届时启用 DOM 提取兜底方案。

## 0. 必须遵守的纪律（不变）

- 「不要修了一个丢了上一个」：每个真实失败形态沉淀 golden/回归用例；
- 任何改动后 `cd backend && python -m pytest -q` 全绿（当前基线 **143 passed**）；
- 既有真实流程改动后要线上复验；看板/手动记录/状态机/T2 分类零回归。

## 0.15 星环二测事故与 v0.5.3（2026-09-02 下午）

**现象**：插件「已识别 15 条」但全是职位列表（status 全为 open → 看板 15 张「待确认」），
真实投递缺席。**证据（快照 #3，已回滚保留 network）**：

- 钩子捕获的 105KB 解密对象是站点启动配置（`__INITIAL_STATE__` 形态）：
  顶层 `jobs` 数组（15 个职位）键含 title+status+createdAt——heuristics 映射
  `title/status/createdAt` 为 job_title/status_raw/applied_at，误判建卡；
- 真实投递缺席原因（二选一或叠加，三测数据可分辨）：① 0.5.2 的英文 title/status
  词典预判没放行真实投递对象（键形未知，可能中文键）；② 所有解密对象共用单一
  `#decrypted` URL，push 去重保留最后者——配置对象顶掉了先解析的投递对象。

**v0.5.3 修复**：

- 扩展 net-capture：宽松形状门（任何 ≥2 字典、≥3 键的数组即候选）+ 内容前缀哈希
  分槽（`#decrypted-<tag>`）LRU 保留最近 4 个不同对象——扩展不再做「像不像投递」
  预判，判断全部交给后端；预算 1500 节点防热路径开销；
- 后端 ingest：`_looks_like_job_ads()`（openedAt/publishedAt/closedAt/pointTo/
  recommendationBonus/jobCount/departmentType/hireMode/mjCode 命中 ≥2 即跳过，
  heuristics 候选路径）+ golden `moka_jobslist_trap_like.json`（真实事故形状
  trimmed，敏感键 candidateAccount/csrfToken 剥离）；
- 数据修复：15 张错卡 + app_tags/app_status_hist 级联 + 门户 10 已删，快照 #3
  回滚为 no_data（network 留存待投递形状校准）；
- 复验：全量 119 passed；**未裁剪的原始快照 #3 network 过新解析器 → None**。

**三测闸门**：重载插件（v0.5.3）→ 刷新星环投递页 → 手动同步。期望 popup
「已识别 N 条」（N = 真实投递数）且看板卡片状态可读；若仍 no_data，查最新快照
network 里 4 个 `#decrypted-*` 槽位内容——真实投递形状就在其中，届时在服务端
加 platform spec/heuristics 校准（不发插件），并按真实形状更新
`moka_encrypted_like.json`。

## 0.2 本轮（2026-09-02 上午）诊断与修复摘要

**星环实测「插件显示已上报、看板无卡」的根因链**（证据在 jobcheck.db 快照 #1）：

1. 采集层正常——`personal-center/applications` POST 已捕获（0.5.1 修的 wrapper 时机问题未复发）；
2. 但 Moka 响应体加密：`{"data":"D2sYoWg+…","necromancer":"f312…"}`（AES-256-CBC，
   官方文档证实只加密 data 字段），后端解析 → `no_data` → 不建卡；
3. popup 只看 HTTP 2xx 显示「✓ 已上报」，把失败静默吞掉；
4. 连带发现：`jc-autosync` 闹钟自 0.5.1 起**从未触发**（顶层 `alarms.create` 被 SW
   每分钟唤醒反复重置周期 + onAlarm 无分支）——「每小时自动同步」此前不存在。

**v0.5.2 修复**（详细变更见 `extension/README.md` 更新记录）：

- net-capture `JSON.parse` 只读包装：解密后明文以 `#decrypted` 伪条目入缓冲
  （PAGE_URL_RE 门控 + 800 节点预算防热路径开销），检测器/解析/PII/队列全复用；
- 自动同步接线：闹钟守卫式创建 + onAlarm 分支 + SW 启动补跑（≥55min），
  `jcLastAutosync` 时间戳防重复补跑；
- 结果透传：`flushQueue` 存后端 `status/parsed_count/note`，popup 按真实结果渲染
  （已识别 N 条 / 数据无变化 / 未识别到投递数据 / 已入队）；
- 看板新增 login_suspect 黄条（board store `connectedSites`/`staleSites`）；
- 后端零生产代码改动，新增 golden `moka_encrypted_like.json` + 3 个回归用例
  （密文-only→no_data 固化、#decrypted 伪条目胜出、API 级全链路建卡）。

**待用户复验（星环四步闸门）**：重载插件（确认 v0.5.2）→ 刷新星环投递页 →
popup 显示「已识别 N 条」+ 看板出卡 + 设置页站点「正常」→ 退出星环登录验证
三处失效提示（popup/设置页/看板黄条）→ 重新登录验证恢复。真实解密字段形状
若与 golden 假设（data.list + positionName 族）不符，heuristics 兜底应能覆盖，
不行则按快照实际内容调整（解析在服务端热修，不发插件）。

## 0.5 决策记录：为什么跳过了 M3 闸门（2026-09-01 用户拍板）

原方案是「影子跑数 ≥3 天 + M2 试点 3 天 → 指标达标 → 才删旧架构」。用户否决：
**旧路径逐站手动接入、反复修 bug，完全无法使用，全部接成新路径**。因此：

- `snapshot_shadow_mode=False`（快照解析后直接建卡/更新）；
- `scheduler_enabled=False`（旧服务端轮询停用，代码与数据保留待清理）；
- M2 后台自动同步（每小时隐藏 tab 回访）提前并入扩展 v0.5.1；
- M3 指标（捕获/解析成功率）降级为**事后观测**（`GET /api/admin/snapshots/stats` 仍可用），
  删除旧代码从「闸门触发」变为「纯清理任务」，不再有前置条件。

## 1. 现在接入一个新企业的操作（用户侧）

1. 平台「设置 → 扩展同步」→ **生成配对码**（6 位，10 分钟有效）；
2. 浏览器装/更新插件 v0.5.1（`chrome://extensions` 加载 `extension/` 目录并重载），
   点 JobCheck 图标 → 空闲面板输入配对码（显示「✓ 已配对 · 自动采集中」）；
3. 打开该企业招聘站「我的投递 / 应聘进度」页——几秒后卡片自动出现；
   检测漏报时点 popup 里的「**同步当前页**」手动兜底；
4. 之后每小时插件静默回访一次已连接站点刷状态；某站显示「疑似未登录」时去该站
   重新登录一次即可恢复。

## 2. 转正改动了什么（本轮新增代码）

**后端**（114 passed，2 个测试改写语义：落卡断言翻转 + shadow 开关语义保留）：
- `config.py`：`snapshot_shadow_mode=False`、`scheduler_enabled=False`（默认值，.env 未覆盖）；
- `ingest.py`：`PlatformParseSpec` 增加 `status_map`，新建快照门户时写入
  `config["status_map"]`（飞书 0/1/3 实证码表——否则数字状态全落「待确认」；
  已有门户配置不动）；`list_connected_sites()`（用户已连接站点：最新快照作回访入口）；
- `api/ext.py`：`GET /api/ext/sites`（Bearer，扩展自动同步数据源）；
  `api/portals.py`：`GET /api/portals/connected`（会话，前端展示）。

**扩展 v0.5.1**：
- popup 已配对面板：「同步当前页」按钮（`jc.syncNow`，绕过扩展域节流）；
- `jc-autosync` alarm（每小时）：拉站点清单 → 轮转选一站 → 静默隐藏 tab →
  等 complete + 25s settle（storage 心跳防 SW 被杀）→ 直接 executeScript 采集 →
  上报 → 关 tab。每 tick 一站串行错峰（防风控，N 站点即每 N 小时全量刷一遍）。

**前端**：
- `api/index.ts`：`extApi.createPairCode/connectedSites`；
- `SettingsView.vue`：顶部新增「扩展同步」面板（配对码大字展示+倒计时+三步引导+
  已连接站点列表含「疑似未登录」提醒和直达投递页按钮）；旧「自动追踪」面板标注
  「已停用」。`npm run build` 通过（项目本身无类型检查脚本）。

**冒烟（真实服务器）**：pair → 上报去哪儿 golden → `parsed, route=platform,
portal=去哪儿校招`，`ingest {created:0, updated:0, unchanged:1}`——**与旧轮询建的
同一张卡正确合并（按 portal_key/title 匹配，零重复）**，卡片状态 written_test；
sites 双端点正常；冒烟数据已清理。

## 3. 环境现场快照（本轮会话结束）

- **后端**：127.0.0.1:8000 在跑（v0.6.1 代码）；mock 飞书 8902、前端 5173 当前未跑
  （验证时临时起过 vite，验证完已停）；
- **DB**：portal 6/8 现在带正确 hints（冒烟副产物，与真实契约一致）；binding 3/4/5
  数据保留但不再轮询；samples/recipes 历史未动；
- **git**：初始提交 → v0.6.0（扩展快照链路）→ v0.6.1（侧边栏 + applied 移除），
  均已推 origin/main，工作区干净（2026-09-02 v0.6.1 会话末）。

## 4. 剩余工作（旧代码清理，无功能风险，按序机械执行）

REFACTOR_PLAN §3 M3 删除清单照旧，但已无闸门前置：
1. 真实站点验证（**验收标准不变：连续 3 个新真实站点零改动出卡**）——先做这个；
2. 删后端：scheduler/adapters(json,recipe,httpio)/llm(pipeline,fingerprint,validator,
   preprocess,prompts)/schemas 配方部分/Recipe+Sample 模型/api/samples/admin recipes/
   bindings Cookie 族/core/crypto/config 轮询组；保留清单见 REFACTOR_PLAN；
   约 1400 行旧测试删除，拆出 normalize/classify 断言；
3. 扩展：删 bind/sample 流程，popup 重写为纯配对面板，manifest 去 `cookies` 权限；
4. 前端：ConnectWizard → Onboarding 弹窗（装插件+配对码+引导），删 useBindFlow；
5. e2e 重写（jc-e2e.html 驱动「访问→上报→卡片」）+ 文档重写。

## 5. 关键校验命令

```bash
cd backend
python -m pytest -q                    # 全量回归（当前基线 143 passed）
# 后端已在跑；手动重启：杀 8000 进程后 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 指标观测（事后）：管理员登录后 GET /api/admin/snapshots/stats
# 管理员：admin@jobcheck.dev / Admin12345
# 星环失败现场：snapshots 表 id=1（no_data，密文条目），samples 表 #30-32（旧架构失败史）
```
