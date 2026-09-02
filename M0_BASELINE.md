# M0 基线盘点（2026-09-01，重构会话开工时记录）

> 依据 REFACTOR_PLAN §3 M0：盘点 golden 与 DB，记录基线数字。本文件是 M1 影子模式
> 跑数前的「旧链路成绩单」，M3 闸门指标（捕获 ≥90%、解析 ≥90%）以此对照。

## 1. 测试基线

- `python -m pytest -q` → **85 passed**（本会话开工复核，与 HANDOFF §3 一致）。
- golden 文件：`tests/golden_samples/beisen_like.json`（北森形状脱敏复刻）、
  `tests/golden_samples/tencent_like.json`（腾讯形状复刻）。

## 2. DB 现状（backend/jobcheck.db）

**门户（8 行）**：

| id | 名称 | 平台 | enabled | 说明 |
|---|---|---|---|---|
| 1 | Mock 演示门户 | json_adapter | ✓ | 本地演示（8901） |
| 2 | 小米校招 | （空壳） | ✗ | 早期 Moka 独立域占位，从未采到契约 |
| 3 | 腾讯校招 | json_adapter | ✓ | 手工校准（单投递进度模型） |
| 4/5 | 网易 / 携程 | （空壳） | ✗ | 自研站占位 |
| 6 | 去哪儿校招 | 飞书指纹实例化 | ✓ | 样本 #23 |
| 8 | 小米科技 | 飞书指纹实例化 | ✓ | 样本 #25（品牌名来自 js-websiteInfo） |
| 9 | 虹科 | 北森指纹实例化 | ✓ | 样本 #29 |

**绑定（5 行）**：1=Mock（4 连败，目标端口已停）；2=腾讯；3=去哪儿；4=小米；5=虹科，
后四个 active，6h 轮询，是旧架构「服务端存 Cookie 重放」的全部存量。

**配方（3 行 published）**：#2→门户6（飞书）、#3→门户8（飞书）、#4→门户9（北森），
全部 source=fingerprint、confidence=0.8。

## 3. 样本历史（四站战绩 + 失败清单）

**成功链路（4 站中 3 站）**：

| 站点 | 样本 | 结果 | 采集层 | 管线层 |
|---|---|---|---|---|
| 去哪儿（飞书 hf7l9aiqzx.jobs.feishu.cn） | #23 | published | 被动缓冲+页面探测 | 飞书指纹命中 |
| 小米（xiaomi.jobs.f.mioffice.cn，飞书 ATS） | #25 | published | 同上 | 飞书指纹命中 |
| 虹科（hkaco.zhiye.com，北森） | #29 | published | 被动缓冲 | 北森指纹命中 |
| 腾讯（join.qq.com） | #1-#3（早期，无 network） | 手工校准入库 | — | 人工 |

**失败清单（旧链路输掉的全部场次，共 12 行 failed）**：

| 样本 | 站点/形态 | 失败原因 | 新架构对策 |
|---|---|---|---|
| #8/#9/#10/#12/#15/#16/#22 | 飞书（7 次） | 列表 XHR 未进缓冲（SSR 直出+缓存）、探测规格未校准 | 已解决（探测规格+指纹）；访问时捕获范式天然覆盖 |
| #27 | 北森（虹科） | 管线失败——当时北森指纹/多 tab 拼接尚未入库。**注意：其 network 里实际已含完整 GetAllDeliveryRecord 响应**（本会话核实），失败是模板缺席所致，非采集层丢失 | heuristics 现场解析不依赖预置模板；#27 数据将作 golden 回归 |
| #30 | 星环（app.mokahr.com，Moka） | 标签页开在插件重载前，wrapper 不在场（network 仅 1 条内嵌块）；netActive 守卫被内嵌块长度绕过 | 检测器数据特征双条件 + 资源回放兜底 + 守卫不再依赖 netActive 单一信号 |
| #5/#6/#7/#11/#14/#21/#24/#26/#28 | （pending 空壳） | 向导发起后未回采 | — |

**用户实盘探针结论（样本 #30 诊断，勿删）**：Moka 列表接口
`app.mokahr.com/api/outer/ats-apply/personal-center/applications` 为 **POST**（GET→405
`{"code":100,"msg":"地址错误"}`）；该标签页 wrapper 重载后已活跃（栈帧含
net-capture.js），刷新重采即可被动捕获真实契约。

## 4. 四站真实载荷离线干跑（本会话验证，M1 解析策略依据）

对 DB 中真实样本的 network 直接跑「平台规格解析 + heuristics」组合：

| 样本 | 策略 | 结果 |
|---|---|---|
| #23 去哪儿（飞书） | 平台规格（list_json_path=data.delivery_list + operation_list.-1） | ✓ 1 条（status=3） |
| #25 小米（飞书） | 平台规格 | ✓ 1 条（status=1，南京，2026-08-18） |
| #29 虹科（北森） | 平台规格（Data.*.Submissions.*.Datas） | ✓ 1 条（简历初筛，2026-08-25） |
| tencent_like golden | 纯 heuristics | ✓（positionInfo.applyPositionTxt 嵌套可推断） |
| #30 星环（Moka） | —（缓冲里没有列表数据） | ✗（预期内；待扩展资源回放补采） |

**消歧风险（golden 用例已覆盖）**：#27 的缓冲里 `GetJobAdPageList`（职位列表，
20 条，status='1'）与 `GetAllDeliveryRecord`（真实投递）**两者都能**通过
heuristics 定位——ingest 必须按 URL 投递特征排序选优，否则误取职位列表。
这正是「误报率」指标要盯的第一形态。

## 5. M1 影子模式验收口径（预登记）

- 捕获成功率 = 上报快照中含 ≥1 条可解析 JSON 载荷的比例（对照：旧链路首采 7 连败的飞书）；
- 解析成功率 = parse_status=parsed 的快照比例；
- 误报率 = parsed 但记录语义错误（职位列表冒充投递、字段张冠李戴）的比例，
  靠 admin snapshots 列表人工抽检 + golden 回归防回归；
- 影子模式不落卡（`snapshot_shadow_mode=true`），看板零改动、零回归。
