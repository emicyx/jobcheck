# HANDOFF — 交接文档（2026-09-03 更新，新链路已转正为唯一接入方式）

> **新会话继续方式**：读 `REFACTOR_PLAN.md`（方案全文）+ 本文档（现场状态）。
> 当前进度口令：「仓库整理 + 文档全量同步（v0.6.3 二次提交待推）；
> DOM 解析链路重构：可信度分门控 + 状态护栏（v0.6.3）；
> T3 LLM DOM 解析层落地（dom_parse v2，glm-4-flash 在线标定通过，key 已配置）；
> 扩展自动同步降频 3h（v0.6.1 扩展版，存量闹钟自动迁移，待重载插件）；
> OPPO 校招平台规格落地；星环 0.5.5 重测仍待做；177 passed」。
>
> 前几轮：M0 → M1 → 转正 → v0.5.2/0.5.3/0.5.4（加密捕获迭代）→ v0.5.5（DOM 兜底）
> → dom 纳入哈希（网易 duplicate 事故）→ 网易 DOM 链路首胜 + 炎魂三重事故修复
> → v0.6.1（「已投递」并入 screening + 我的数据侧边栏 + /api/me/stats）
> → v0.6.2（mock 门户删除）→ OPPO 规格（2026-09-03）。基线 **177 passed**。

## 0.0 仓库整理与文档全量同步（2026-09-03，二次提交待推）

v0.6.3（`3e4ab17`）推送后的整理轮：仓库文件整洁性 + 全部文档对齐现状。

- `.gitignore` 补 `frontend/node_modules/`、`frontend/dist/`（此前仅靠用户全局
  配置忽略，换机克隆无效）；
- **README 全量重组**：新增「现行链路详解：扩展快照式同步」章节（用户旅程/
  五层解析/LLM 配置/T3），置于旧架构段之前；旧架构段落标题明确标注
  （M2 服务端轮询已停用 / M4 随停用 / jc-e2e 待 M3 重写）+ 指引现行接入方式；
  头部描述改为快照架构（原「M1+M2+M4 管线已通」为旧架构表述）；目录结构树
  补 dom_parse/smoke_llm_dom/HANDOFF/REFACTOR_PLAN 等；测试基线 177；
  里程碑补「快照链路转正」「T3 LLM DOM 解析层」两项已完成 + M3 清理清单；
- extension/README 标题改 v0.6 措辞；PROJECT_REPORT 日期行/摘要表更新 +
  新增「增补：v0.6.2 → v0.6.3」节（旧 §4.3 四层描述以增补为准），HTML 顶部
  加横幅指向 md 增补节；
- 仓库无多余文件需删（根目录 5 文档 + 3 目录 + 启动脚本；`.pytest_cache`/
  `__pycache__`/`.zcode`/私有 golden 均已 ignore）。

## 0.00 DOM 解析链路重构：可信度分门控 + 状态护栏（2026-09-03，待提交）

**附：真实 LLM 在线标定（glm-4-flash）三连坑与修复（同日）**。用户配置 key 后
`python -m scripts.smoke_llm_dom.py` 冒烟三连跑暴露两个真实形态（假 LLM 测不出，
这正是在线标定的价值）：

- **① 图标字符粘进状态照抄**：大纲把 `[title=已拒绝]✕` 相邻渲染，模型返回
  `已拒绝✕`，词元回查整块查不到 → 记录全拒。修复：大纲改 `<tag [title=x]> 文本`
  带空格分隔；词元切分放宽为「任何非 `\w` 字符都是分隔符」（装饰符号 ✕◦✓ 不该
  惩罚照抄忠实度）+ 短码（≤4 字符）整串回查兜底（数字状态「3」）；
- **② 图标符号本身当 status_raw**：提示词 v2 明令禁止后模型仍返回 `✕`/`◦`
  （语义放进 status 建议）——**提示词压不住模型的照抄倾向，歧义必须在表示层
  消解**：大纲对「纯符号文本 + 有 title 属性」的元素直接以 title 作文本
  （`<span.ico> 已拒绝`，装饰符号剔除）；后过滤同时拒绝纯符号 status_raw 双保险；
- 冒烟最终通过：两记录状态/日期/部门全对，语义建议（已拒绝→rejected、
  Interviewing→interview_unknown）正确。新增 2 回归（词元装饰符号容忍、
  纯符号拒绝）；另发现用户本地新增 3 个 ingest 用例（work_location 重同步覆盖/
  标题匹配部门未知/品牌噪声标题），与重构共存全绿。基线 → **177 passed**。

**决策（用户拍板「非特殊模板网页接入 LLM 解析」+ 探寻最佳实践）**：
规则解析在新站接入时多多少少有问题（事故史即证据），但不删规则全走 LLM——
网络层（结构化 JSON）规则可靠且免费毫秒级，保留规则优先；DOM 层改为
**规则先跑 + 可信度分门控**：`dom_plausibility()`（行数同构 0.45/单卡 0.10、
日期覆盖 0.4、标题长度合规 0.15，阈值 0.5）——高分直接采信规则（快/省/
跨快照确定），失败/低分交给 LLM 接管，**LLM 不可用（未配置/超预算/上游故障）
时低分规则结果仍作降级兜底**（行为不劣于改造前）。**规则层冻结**：不再为新站
人工补词典/正则，非模板版式由 LLM 负责——「简单」来自停止猫鼠游戏，不来自删代码。

- 「规则成功但数据可疑」的判定问题就此闭环：低分即裁决，parse_note 落
  「规则可信度 0.25 < 0.50，LLM 接管」/「LLM 不可用降级采信」可观测；
- **状态护栏**（`sync.ingest_applications(suspect_guard=)`，仅 dom/llm_dom 路由
  启用，网络层与绑定轮询不受影响）：状态机逆跳（offer→筛选，几乎必是选错行/
  字段错位）与解析退化（已知状态→待确认，解析丢了语义）不覆盖已知状态——
  **整条跳过**（状态/原文/日期必须同源，不得一半新一半旧），guarded 计数进
  parse_note「拦截可疑状态变更 N 条」；真实重新投递通常产生新记录新卡，
  误拦率低，被拦更新由下次同步重试；
- **请求路径超时收紧**：`client.call_json` 支持 timeout/retries 覆写，
  dom_parse 用 20s×1 次（默认 60s×3 最坏 ~3min 会拖死上报——扩展只等 30s，
  这也是 T3 上线时就存在的隐患，本轮一并修复）；
- 校准锚定：yanhun/bilibili golden ≥0.5（LLM 开启也不烧钱）；单卡无日期 0.25、
  多行短标题 0.45、多行无日期 0.60（数值写进测试防漂移）。
  新增 6 用例（低分被接管/降级兼容/上游故障/护栏逆跳+退化+正向放行/guard 关闭
  保持旧行为/超时参数）。基线 163 → **169 passed**。

## 0.01 扩展自动同步降频 1h → 3h（2026-09-03，v0.6.1，待提交）

**决策（用户拍板）**：`jc-autosync` 由每小时降为每 3 小时——控站点风控压力与
后端解析成本（T3 LLM DOM 兜底层上线后的量级考量；也把全量 LLM 解析的月预算
压力降一个量级）。轮转逻辑不变（每 tick 回访一站，N 站点即每 3N 小时全量刷一遍）。

- `background.js`：闹钟周期 60 → 180 分钟；**存量安装自动迁移**——守卫式创建
  从「存在即跳过」改为比对 `periodInMinutes`，与 180 不一致才重建（旧 60 分钟
  闹钟跨浏览器重启持久存在，只改 create 参数迁移不到）；周期字段缺失视为匹配，
  宁可少迁移也不反复重建（0.5.1「闹钟被每分钟唤醒反复重置、永不触发」事故教训）。
  SW 启动补跑阈值 55 → 175 分钟（周期 − 5 分钟惯例）；
- 文案同步：popup 提示、SettingsView 引导文、extension/README、REFACTOR_PLAN
  现状描述；manifest 0.6.0 → 0.6.1 + changelog 条目；
- **发版动作**：`chrome://extensions` 重载插件（v0.6.1）即生效，重载后首个
  周期内完成旧闹钟迁移；后端/前端无逻辑改动（前端仅文案，随下次构建发布）。


## 0.02 T3 DOM 兜底 LLM 解析层（2026-09-03，待提交）

**动机**：规则解析（平台规格/hints/启发式/规则版 dom_records）在新站接入时
经常因版式与规格差异解析失败——规则版 dom_records 靠「同签名重复兄弟行 +
状态词典」，对非模板版式天然乏力：状态藏在图标 `title` 属性（itertext 抽不到）、
步骤条/时间线（当前节点 ≠ 状态词）、英文文案（词典只认中文）、阶段+进度拆两处。
用户拍板引入 LLM 层解析未知网页 DOM。解析顺序变为：
**平台规格 → hints → 启发式 → DOM 层（规则 + 可信度分门控）**——
规则高分采信、失败/低分 LLM 接管、LLM 不可用规则降级兜底（§0.00 重构定稿）。

- `app/llm/dom_parse.py`（新模块，M3 清理不受影响——不依赖 pipeline/
  fingerprint/validator/preprocess/prompts）：
  - `dom_outline()`：裁剪 DOM → 文本大纲（只留有直接文本/title 属性的元素，
    一行一块、缩进=层级、class/id/title 标注）；真实 golden 实测 ~4× 压缩
    （炎魂 5821→1395、bilibili 5245→1523），预算 `LLM_DOM_MAX_CHARS=60000`
    截断带标记；
  - `parse_dom_snapshot()`：provider 装配（`LLM_DOM_PROVIDER`，默认 heuristic
    = 层关闭零成本）→ 大纲 → `client.call_json(task="dom_parse")`（记账/
    重试/月预算熔断全部复用 T1/T2 基建）→ pydantic 宽松 Schema → 后过滤；
  - 后过滤（宁缺毋错）：page_type≠applications 整体不采信（职位列表页带
    records 也不建卡）、逐条 conf≥0.5、title/status 非空、备案/版权/人才库
    噪声正则（沿用炎魂/bilibili 事故同款）、≤50 条、**反幻觉词元回查**
    （status_raw/job_title 按分隔符切块，任一块须能在页面大纲中找到，
    整块查不到=编造，整条丢弃）；
  - 结果缓存：`(dom sha256, model, prompt_version)` 进程内 LRU（128），
    同 DOM 重复解析（自愈回放）只调一次 LLM；
- `prompts/dom_parse.md` v1：状态识别方法论沉淀成提示词——负面清单
  （职位广告/导航/表头/页脚/页面级横幅/操作按钮）、状态六形态（徽章/步骤条
  当前节点/时间线最新条/阶段+进度组合/title 属性/英文）、**status_raw 逐字
  照抄不改写**（语义判断留给下游）、宁缺毋错（漏一条远好过错一条）、
  `{{STATUS_ENUMS}}` 从状态机单一事实源动态注入；
- 语义沉淀：LLM 高置信建议（conf≥0.9）在门户建档后经
  `deposit_suggestions()` 写成门户级 StatusRule（复用 T2 的
  `classify._save_rule`，仅当既有规则解析不出时沉淀，绝不覆盖人工/实证规则）
  ——英文文案/生僻状态原文下次同步确定性命中，零成本；route=`llm_dom`
  （parse_note 带「沉淀状态规则 N 条」）；
- 配置：`LLM_DOM_PROVIDER/BASE_URL/MODEL/API_KEY/PRICE_IN/OUT/MAX_CHARS`
  （默认 glm-4-flash 计价 0.5/2 CNY/百万 token）；
- 测试 `test_llm_dom_parse.py` 15 例：大纲压缩（保留/截断/噪声形态）、
  provider 关闭零调用、假 LLM 全链路（提示词装配含枚举注入 + URL + title
  属性）、缓存单次调用、上游异常/预算熔断降级 None、职位列表页不采信、
  反幻觉/噪声/低置信逐条丢弃、沉淀只写未解析原文、API 全链路
  （非模板 DOM：状态在 title 属性 + 英文文案 → route=llm_dom 建档落卡 +
  Written Test 经沉淀规则归一 written_test）、规则层可解时不烧 LLM。
  基线 148 → **163 passed**。
- **未做（后续可加）**：真实 LLM 在线标定（当前只有假 LLM 回归）；云端启用
  需在 .env 配 `LLM_DOM_PROVIDER=openai_compatible` + `LLM_DOM_API_KEY`。

## 0.03 OPPO 校招解析规格：流程节点状态 + dig 过滤段（2026-09-03，待提交）

**云端实盘异常**（用户部署到云服务器后报 OPPO 解析异常，
`careers.oppo.com/university/oppo/center/history`）：非环境问题——本地同样
解析不了，根因是引擎缺 OPPO 规格。按官网前端 bundle 逆向校准（无真实账号
采样，字段名/节点码均出自渲染代码）：

- 接口 `GET /api/delivery/queryAllDeliveryProgressList` →
  `data[].deliveryPositionRecordList[]`；**投递条目上没有平铺状态字段**——
  状态在 `flowProcessTemplateList` 流程节点数组里（`flowProcessStatus`：
  PASS 已过 / THE_ONGOING 当前 / NOT_PASS 被拒 / DID_NOT_ARRIVE 未到），
  页面状态文案由前端计算，网络层三层解析（平台规格→hints→启发式）全落空，
  只能掉进 DOM 兜底抽渲染文本（已实证 `parse_snapshot_network` 返回 None）。
- `fields.dig` 新增过滤段语义：路径段 `key=value` 在 dict 数组上选中首个
  `element[key]==value` 的元素再下探——否则表达不了「取进行中的那个节点」；
  `+` 拼接子路径现在 strip（带空格的拼接路径此前静默取 None）。
- OPPO 规格 status_raw 拼接链：`NOT_PASS 标记 > THE_ONGOING 节点码 >
  末节点码`（全 PASS 即流程走完），靠 status_map 先到先得拼出终语义，
  **NOT_PASS 规则必须排在阶段规则之前**（被拒时当前节点码仍会拼进串）；
  `?type=old` 旧接口（deliveryDynamicsList）与社招接口（平铺
  publishName/firstAcceptNode）形状不同，未实证不猜。
- `PlatformParseSpec` 增加 `brand` 兜底名（OPPO 响应无租户内嵌、无 DOM 时
  门户命名不再落 host）；**存量门户自愈**：upsert 刷新 hints 时同步补写
  实证 status_map（dom 期建的门户拿到码表，下次同步恢复）。
- golden `oppo_progress_like.json`（bundle 逆向还原，占位数据）+ 5 用例：
  golden 参数化 / 状态拼接链 / dig 过滤段 / 存量门户补写 / API 全链路
  （3 卡：screening/rejected/offer）。基线 143 → **148 passed**。
- **云端恢复步骤**：部署新代码 → 重访 OPPO 历史页（或 popup「同步当前页」，
  同域 10 分钟节流照常）→ 平台规格命中自动建档「OPPO」；若异常期已建
  dom 错卡，删 OPPO 门户及其卡片后重同步（错卡无 portal_key，标题对不上
  会留重复卡）。插件无需更新。

## 0.04 v0.6.2：删除全部 mock 招聘门户，以真实站为基准（2026-09-02，待提交）

**决策（用户拍板）**：mock 招聘网站全部删除（含飞书契约 mock），平台只面向真实
招聘站；飞书模板回归测试改用固化 golden 样本，覆盖零损失。

- 删脚本：`scripts/mock_portal.py`（8901 纯演示）、`scripts/mock_feishu_portal.py`
  （8902 契约复刻，数据已固化）、`scripts/e2e_verify_sample_flow.py`
  （旧架构手工验证，依赖 8902，随旧链路废弃）。
- 固化 `tests/golden_samples/feishu_like.json`：从 mock `_delivery()` 原样导出
  （8001 服务端/8002 算法/8003 产品培训生 + operation_list 时间线，数据确定性）；
  `test_feishu_template.py` 的 `_feishu_network()` 改读 golden，`WEBSITE_PATH="704852"`
  内联，**全部断言不变**（CSRF 自愈用例本就只用响应体 + httpx 桩），7 用例照常通过。
- 测试中性化：`test_m2_bindings` fixture「Mock 演示门户/演示公司」→
  「测试门户/测试公司」（FakeAdapter 离线，值无实际作用）；
  `test_admin_dashboard`「飞书演示」→「飞书测试门户」。
- `seed_portals.py` 删 MOCK_PORTAL（只留真实门户种子）；`start_dev.bat` 只起后端+前端。
- DB 清理：portal 1（Mock 演示门户）+ 演示公司 3 条投递 + 4 条状态历史已删
  （删前复核无绑定/标签关联）；现存 9 门户 / 9 条投递全部真实。
- 文档：README 删 8901/8902 本地演示段（飞书模板段改为真实站描述 + golden 说明）、
  REFACTOR_PLAN e2e 行去 mock、适配器注释去「本地 Mock」措辞。
- M0_BASELINE 为历史基线记录，其中 Mock 行/绑定 1 记录按历史保留。
- jc-e2e.html / 扩展 / 前端源码零 mock 依赖，未动。

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
- 任何改动后 `cd backend && python -m pytest -q` 全绿（当前基线 **177 passed**）；
- 既有真实流程改动后要线上复验；看板/手动记录/状态机/T2 分类零回归；
- **真实站点测试数据不入库**（2026-09-03 用户拍板）：站点专属 golden/快照
  放 `backend/tests/golden_samples/private/`（gitignore），测试加载器
  双目录查找、缺失即 skip——克隆环境不失败，本地全量回归照常。

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
4. 之后插件每 3 小时静默回访一次已连接站点刷状态（0.6.1 起降频，原每小时）；某站显示「疑似未登录」时去该站
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

- **后端**：127.0.0.1:8000 在跑；前端 5173 当前未跑（验证时临时起过，完已停）；
  mock 门户脚本已删（v0.6.2），不再有 8901/8902；
- **DB**：portal 6/8 现在带正确 hints（冒烟副产物，与真实契约一致）；binding 3/4/5
  数据保留但不再轮询；samples/recipes 历史未动；
- **git**：初始提交 → v0.6.0（扩展快照链路）→ v0.6.1（侧边栏 + applied 移除，已推）；
  v0.6.2（mock 删除）已完成未提交，待用户确认后提交推送。

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
python -m pytest -q                    # 全量回归（当前基线 177 passed）
# 后端已在跑；手动重启：杀 8000 进程后 python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 指标观测（事后）：管理员登录后 GET /api/admin/snapshots/stats
# 管理员：admin@jobcheck.dev / Admin12345
# 星环失败现场：snapshots 表 id=1（no_data，密文条目），samples 表 #30-32（旧架构失败史）
```
