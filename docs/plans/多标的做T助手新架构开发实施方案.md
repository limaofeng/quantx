# QuantX 多标的做 T 助手新架构开发实施方案

> 状态：`IN_PROGRESS`（P0—P4 已完成；P5 工程已验证、策略准入待确认；P6 隔离开发进行中；P7/P8 隔离开发进行中）<br>
> 版本：2.5<br>
> 日期：2026-09-09<br>
> 目标设计：[多标的做 T 助手新架构设计 v2.2](../architecture/多标的做T助手新架构设计.md)<br>
> 当前基线：[系统架构设计（As-Is）](../architecture/系统架构设计.md)<br>
> 开发实施进度：5 / 9 个阶段门完成（55.6%）

## 1. 目的与使用方式

这次变更横跨交易域、应用层、数据库、Agent 协议、Engine、API、QMT Agent、Web、回测和文档，
必须有独立实施方案。目标架构回答“最终应该是什么”，本方案回答：

- 哪些任务是前置地基，哪些功能必须后置；
- 每个阶段允许改变什么、禁止提前打开什么；
- 阶段如何验收、失败时如何停止和恢复；
- 当前真实开发状态、验证证据和提交在哪里；
- 目标设计、当前架构、交易契约和其他业务设计之间是什么关系。

本方案不是第二份架构设计，也不把未完成事项描述成当前能力。阶段完成后，必须同时更新本方案
状态和相应 As-Is 文档；仅修改 checkbox 而没有代码、测试和证据，不算完成。

### 1.1 状态词汇

| 状态 | 含义 |
|---|---|
| `NOT_STARTED` | 尚未开始，前置条件可能尚未满足 |
| `IN_PROGRESS` | 已有实现工作，尚未通过阶段退出门 |
| `BLOCKED` | 有明确阻塞项，必须记录原因、解除条件和下一动作 |
| `DONE` | 代码、迁移、测试、文档和证据均完成，已提交 |
| `DEFERRED` | 经明确决策移出本次范围，并记录替代路径或后续计划 |

阶段状态只由退出门决定。任务勾选使用 `[x]`，但阶段只有在所有 required 任务和退出门都满足时
才能置为 `DONE`。`BLOCKED` 不用于表达“比较难”，只用于真实依赖或安全条件无法满足。

### 1.2 更新纪律

每个实施提交必须在同一提交中更新本方案：

1. 更新任务 checkbox 和阶段状态；
2. 在“实施证据台账”记录测试命令、结果与 commit；
3. 若行为已经成为当前事实，同步更新 `系统架构设计.md` 和对应工程 README；
4. 若目标契约改变，先修改目标设计和权威交易契约，再改实现；
5. 失败或回滚不得把状态保留为 `DONE`，必须记录恢复到的权威状态。

开发进度按完成的阶段门计算，不按代码行或已勾选子任务估算：

```text
development_progress = DONE_phase_count / 9
```

模型阶段不是 RULE_ONLY 上线的前置，但仍计入本方案总阶段数；需要分别展示“规则核心上线状态”
和“完整方案状态”，避免模型后置让已完成的安全核心看起来未交付。

### 1.3 后续执行批次与成本边界

本方案为 P0–P8 共九阶段，没有 P9。2026-09-09 用户授权 P6–P8 连续开发：默认单代理，
按依赖顺序完成实现、定向验证、必要文档和提交，不在开发批次边界自动结束任务。
各组件先通过最小整链及必要边界测试，稳定后统一工程集成、回归和审核，复用有效证据。
开发完成与阶段 DONE 分别记录；P5/P6/P7 的前置门约束实际运行准入，不阻止后续隔离开发。
P5 本身的正式评估与准入要求不变。

每批启动前固定：输入提交/接口、写入范围、退出条件、必要验证、外部环境依赖和不包含项。
若发现连续开发范围内的必需接线缺口，在当前检查点补入并完成；范围外新工程记录缺口，
不自行扩大目标。交接仅保留提交、接口、验证及剩余项，不要求每批另开任务。
同一检查点更新当前结论，历史细节引用已有提交/日志，不持续追加重复过程报告。

实现、固定数据评估、维护窗口和交易观察分开授权。等待行情、训练或交易日不要求模型
持续运行；不得通过反复查询和审计代替外部证据。未通过评估只报告结果及原因，不自动调参。
首个 P5 实现批次记录可取得的主任务总 token、缓存输入、输出和耗时，作为后续预算参考；
统计不可得时标明未知，不估造数字。预算未约定不等于允许无限扩展范围。

## 2. 文档关系与权威边界

### 2.1 文档关系图

```text
当前运行事实
  系统架构设计 + Engine/QMT/API 工程 README + 当前代码
                         │ 提供迁移起点
                         ▼
目标状态
  多标的做 T 助手新架构设计 v2.2
     ├── 服从 A 股三层执行契约、交易域状态机、自动退出契约
     ├── 保留 持仓做 T V3 的标的规则语义
     └── 与 买入/卖出计划、打板助手设计共享公共执行地基
                         │ 拆分依赖与阶段门
                         ▼
实施追踪
  本开发实施方案
     ├── 阶段状态 / 前置与后置任务
     ├── 迁移、测试、故障注入与上线门
     └── commit / 验证 / 文档更新证据
```

### 2.2 关系矩阵

| 文档 | 职责 | 与本方案的关系 |
|---|---|---|
| [系统架构设计](../architecture/系统架构设计.md) | 当前 As-Is、进程和协议基线 | 未完成阶段仍以它为当前事实；每次切换后原子更新 |
| [多标的做 T 助手新架构设计](../architecture/多标的做T助手新架构设计.md) | 做 T To-Be 与不可破坏不变量 | 本方案的目标契约；实现不得用任务便利性绕过 |
| [A 股三层协作与执行契约](../trading/contracts/A股三层协作与执行契约.md) | 策略、应用、执行和回报硬边界 | 上位交易契约；任何阶段都必须满足 |
| [A 股交易域数据结构与状态机](../trading/contracts/A股交易域数据结构与状态机.md) | 数量、订单、成交、仓位与 T+1 | OrderSizer、容量和回报收敛的权威语义 |
| [A 股自动退出计划与卖出策略契约](../trading/contracts/A股自动退出计划与卖出策略契约.md) | ExitPlan 与 SELL 唯一所有权 | Phase 2 和所有 LIVE 阶段的强前置 |
| [持仓做 T 有状态机会引擎 V3 实施规格](持仓做T有状态机会引擎V3实施规格.md) | 单标的窗口、FSM、candidate 和 rearm | 保留规则语义；本目标设计替换其 StrategyRun 所有权和组合调度方式 |
| [买入/卖出计划新架构设计](../architecture/买入卖出计划新架构设计.md) | ENTRY_PLAN 与公共 ExitPlan 来源 | 平行业务设计；共享 Phase 1/2 地基，不在本方案中假装完成其迁移 |
| [打板助手新架构设计](../architecture/打板助手新架构设计.md) | BOARD_ASSISTANT_EXECUTION | 平行业务设计；共享 Phase 1/2 地基，不复用 T execution |
| [次日上涨概率网页模型训练与 GPU 加速实施计划](次日上涨概率网页模型训练与GPU加速实施计划.md) | 已验证的模型运维原语 | Phase 8 只复用 manifest/lifecycle/artifact/backend，不复用数据和模型 |

### 2.3 冲突判定

1. 判断“当前能否运行”时，以当前代码、`系统架构设计.md` 和工程 README 为准。
2. 判断“做 T 最终契约”时，以目标架构 v2.2 和上位交易契约为准。
3. 判断“下一步做什么、阶段是否完成”时，以本方案为准。
4. V3 规格与目标架构冲突时：V3 的规则/FSM 语义保留，StrategyRun 所有权、逐票准入和状态
   存储由目标架构替代。
5. 共享地基变更必须同时验证现有普通策略、人工命令和 ExitPlan；不能为了做 T 破坏既有 owner。
6. 只有一个权威写路径。阶段性 shadow 可以双读/对比，但不能双 owner、双下单或双状态推进。

## 3. 总体依赖、前置与后置关系

### 3.1 阶段依赖图

```text
P0 基线冻结与契约清点
          │
          ▼
P1 公共 Owner + Agent 协议 1.2 原子升级
          │
          ├──────────────┐
          ▼              ▼
P2 公共安全地基       P3 独立 T 运行时与逐 Tick 内核
          └──────┬───────┘
                 ▼
P4 组合分配、跨域准入与 RULE_ONLY PAPER
                 ▼
P5 共享账户组合回测
                 ▼
P6 LIVE / CANARY / MANUAL_CONFIRM
                 ▼
P7 故障注入、稳定性与 LIVE/AUTO
                 │
                 ▼
P8 模型 SHADOW 与 ACTIVE（非 RULE_ONLY 前置）
```

P2 与 P3 可以在 P1 完成后独立开发，但 P4 必须同时依赖二者。模型数据采集可以在 P4 的 PAPER
稳定后开始，模型 `ACTIVE` 必须依赖 P7 的实盘安全门；模型工作不能阻塞 RULE_ONLY 核心交付。

### 3.2 全局前置门

以下条件未满足时，不开始跨层实现：

- 目标设计、不变量、状态词汇和文档关系已冻结；
- 已清点所有 `run_id/strategy_run_id/owner_type=STRATEGY_RUN` 隐式假设；
- 已建立 legacy 活动候选、审批、订单、batch、ExitPlan 和结果未知命令的只读清单；
- 协议 1.2 切换窗口、停止新命令和结果未知 reconcile 流程已演练；
- ENTRY/EXIT order policy、收盘 policy、跨域 BUY 准入优先级和 LIVE 灰度退出门有明确版本；
- 当前 V3、普通策略、人工命令、ExitPlan 和乱序回报基线测试可重复通过。

### 3.3 全局后置任务

只有在 P7 的故障、性能及 AUTO 观察门通过且 legacy 义务归零后，才允许执行 P7 收尾：

- 删除旧做 T StrategyRun 专用 scheduler、状态 callback、fallback 和旧 GraphQL 入口；
- 把 `系统架构设计.md` 的做 T 主路径改写为 TAssistantExecution As-Is；
- 删除旧协议/旧表字段，而不是长期 nullable 保留；
- 将共享 owner/ExitPlan/admission 地基作为买入计划与打板助手后续迁移的已验证前置；
- 归档本方案为 `COMPLETED`。P8 未做时只能标记“RULE_ONLY 完成、模型阶段 DEFERRED/未完成”。

## 4. 阶段状态总表

| 阶段 | 范围 | 状态 | 强前置 | 退出门摘要 | 证据 |
|---|---|---|---|---|---|
| P0 | 基线冻结与契约清点 | `DONE` | 文档基线 | 清单、policy、只读审计、405 + 9 审计单测基线齐全 | [P0 冻结基线](多标的做T助手P0冻结基线.md) |
| P1 | Owner 与协议 1.2 | `DONE` | P0；P1-01..06 均已完成；PAPER legacy 义务已受控收敛；停服、停服态备份、迁移、标准 full/live 恢复、全量快照对账和隔离恢复演练已通过 | 单一 `ExecutionOwnerRef`、单一 protocol 1.2 payload、既有路径等价；未知/冲突 owner 与结果未知均 fail-closed；无双协议 | [P0 冻结基线](多标的做T助手P0冻结基线.md)；P1-01 `52 passed`；P1-03 `38 passed`；reconciliation `34 passed`；cutover preflight `20 passed`；0046/0047 schema gate 通过；owner 空值 `0`、8 个身份不可变触发器通过；最新 1.2 快照 `PROCESSED`、旧失败快照 `SUPERSEDED`；标准 full/live 冷启动 exit=`0`，`liveTrading=ENABLED`，QMT/marketData/Monitor READY、快照约 3 秒，gateway/schema verify 通过 |
| P2 | 公共 ExitPlan/容量/准入安全地基 | `DONE` | `P1 DONE`；实现、恢复测试与 Windows 运行验收完成 | 无第二真源，故障恢复通过 | 0048/0050 实库与隔离恢复通过；BUY 整链 39 项及根回归，见 §10 |
| P3 | 独立 T runtime 与精确行情归约 | `DONE` | `P1 DONE`；独立 PAPER shadow 实现与验证完成 | 无 StrategyRun、逐 Tick 因果归约、隔离 PAPER shadow 且无订单链写入 | 0049 实库与隔离恢复通过；最终 63/112 项及快照修复 38 项，见 §10 |
| P4 | 分配、PAPER 与跨域准入 | `DONE` | P2 + P3 已核对 | 整批原子、PAPER 闭环、无真实订单 | 六项实现、隔离 PG 闭环/故障门及实际 Caddy/Web 契约检查完成，业务库 0056；证据见 §10 |
| P5 | 共享账户回测 | `IN_PROGRESS` | P4 | 无重复资金/未来数据，结果可重放 | P5-A/B 通过；P5-C 工程准备通过，正式样本与准入门待确认 |
| P6 | LIVE 人工确认灰度 | `IN_PROGRESS` | LIVE 运行依赖 P5；隔离开发已授权 | 唯一 producer、规定闭环、无安全违规 | 源执行排空与订单身份约束基础；见 §10 连续开发检查点 |
| P7 | AUTO 与稳定性 | `IN_PROGRESS` | P6 | 故障注入、恢复、收盘与并发门通过 | 待补 |
| P8 | 模型 SHADOW/ACTIVE | `IN_PROGRESS` | P5；ACTIVE 依赖 P7 | OOS 增量、门禁、人工发布闭环 | 待补 |

### 4.1 P6–P8 连续开发顺序

| 顺序 | 开发范围 | 工程退出条件 | 上线保留项 |
|---|---|---|---|
| 1 | P6 源执行排空、订单 owner 约束、LIVE 确认与公共分配/准入/命令链 | 隔离最小买卖闭环；确认过期/重复、额度变化、排空恢复与原 owner 保留 | P5 准入、维护窗口、CANARY 与交易观察 |
| 2 | P7 故障恢复、并发/性能、AUTO successor 与阻断 | 复用 P6 证据，补足新故障矩阵及 successor 原子失败测试 | AUTO 观察门、legacy 义务归零后清理 |
| 3 | P8 数据/标签/切分、制品、CPU 加载、SHADOW/ACTIVE 门 | 无未来数据、安全制品和故障行为；工程夹具与正式研究结果区分 | 冻结模型指标/预算、正式 OOS 评估、人工发布 |
| 4 | 统一工程验收 | 按最终受影响范围集成、回归、审核；有效证据不重复运行 | 阶段 DONE 仍逐门判定 |

## 5. 分阶段任务清单

### P0：冻结基线与完成契约清点

前置：目标设计 v2.2 与本方案已评审。

- [x] `TTA-P0-01` 冻结旧做 T StrategyRun 的新增功能，只允许安全修复和义务排空。
- [x] `TTA-P0-02` 按 DB、contracts、domain、application、infrastructure、Engine、API、Worker、
  QMT Agent、GraphQL/Web 分组清点所有 run identity 假设和迁移目标。
- [x] `TTA-P0-03` 建立 legacy owner/候选/审批/pending/outbox/order/fill/batch/ExitPlan/未知结果
  只读一致性报告，不修改不确定记录。
- [x] `TTA-P0-04` 冻结 protocol 1.2 owner payload、数据库约束、reason code 和迁移切换步骤。
- [x] `TTA-P0-05` 冻结逐 Tick lag/ring、snapshot freshness、cycle/allocation lease 与 TTL 的硬阈值。
- [x] `TTA-P0-06` 冻结跨域风险增加优先级、ENTRY/EXIT order policy、14:50 ENTRY cutoff、
  最短退出窗口、收盘缓冲、隔夜上限和人工灰度退出门。
- [x] `TTA-P0-07` 保存 V3、普通策略、ExitPlan、T+1、乱序回报和 QMT 断连基线测试证据。

退出门：上述清单均已有 owner、精确代码落点、测试和决策记录，详见[P0 冻结基线](多标的做T助手P0冻结基线.md)。
只读审计命令为 `python ops\t-assistant-p0-audit.py --format markdown`，审计工具单测为 `9 passed`，
13 个基线测试文件为 `405 passed, 8 warnings, 16.20s`（合计 `405 + 9`）。因此 P0 阶段为 `DONE`。
P0 初始冻结时 P1 readiness 为 `false`：开发库当时仍有 2 条 terminal-run `AWAITING_APPROVAL`/缺
candidate identity、2 条 nonterminal intent、1 条 `ERROR` orphan outstanding T ExitPlan。上述 PAPER
legacy 义务已在 P1 gate 中受控收敛；复验 blocker=`0`、P1 readiness=`true`。维护窗口已实际完成
停组件、停服态备份、迁移、标准 full/live 恢复、全量快照对账和备份隔离恢复演练。以上是 P1 中间态
历史记录；随后 P1-02/P1-04..06 已在同一原子发布中完成，当前唯一在线 Agent 控制协议为 1.2。

### P1：公共 `ExecutionOwnerRef` 与协议 1.2 原子升级

前置：P0 `DONE`，维护窗口和结果未知命令处置流程可执行。

- [x] `TTA-P1-01` 在 domain/contracts 建立强类型 `ExecutionOwnerRef` 与明确 owner enum。
- [x] `TTA-P1-02` 原子演进 intent、approval、pending、correlation、outbox、ExitPlan source 和回报
  路由表；回填普通策略/人工命令，删除默认 StrategyRun fallback。
- [x] `TTA-P1-03` 建立最小 `OwnerRuntimeRouter`，未知、冲突或失联 owner 一律 fail-closed。
- [x] `TTA-P1-04` 将 Agent 命令/报告契约切换到 protocol 1.2；先排空未投递 1.1 outbox，未知结果
  只 reconcile，禁止换 payload 重发。
- [x] `TTA-P1-05` 同步 API、Engine、QMT Agent、GraphQL、Web、codegen 和客户端文档。
- [x] `TTA-P1-06` 验证普通 StrategyRun、MANUAL_COMMAND 与 EXIT_PLAN 行为等价、幂等和乱序恢复。

退出门：运行时只使用一个 owner 协议和一个 Agent payload；不存在 metadata-only owner、双写或
默认 StrategyRun fallback。当前 Router 仅注册 `STRATEGY_RUN`、`EXIT_PLAN`、`MANUAL_COMMAND`；
`T_ASSISTANT_EXECUTION`、`ENTRY_PLAN`、`BOARD_ASSISTANT_EXECUTION` 等未注册或未知/冲突 owner
仍 fail-closed，P2/P3 完成前不允许新 T runtime 产生真实订单。

### P2：公共退出、容量与风险增加准入地基

前置：P1 `DONE`。

- [x] `TTA-P2-01` 将 PAPER/LIVE ExitPlan 统一交给独立 `ExitPlanRuntime`，source execution 终态
  不影响原计划恢复。
- [x] `TTA-P2-02` 固化 `AccountCapacityService` 老仓认领公式、obligation watermark、账户锁序、
  bucket priority 和 protected floor；不创建第二套 claim 余额表。
- [x] `TTA-P2-03` 建立 `AccountRiskIncreaseAdmissionSequencer` 与 durable admission batch，覆盖
  做 T、打板、买入计划、普通策略和人工 BUY 的稳定次序；READY 先于账户执行锁持久化，
  batch/claim 提交可见后才按 rank 进入最终账户锁，Engine 启动与后台扫描恢复 READY/PREPARED。
- [x] `TTA-P2-04` 建立版本化 `TEntryOrderPolicy/TExitOrderPolicy` 与结果未知禁止 replace 门；
  2026-09-06 复核：FIX_PRICE/30 秒命令有效期、工作单超时撤单、同一活动意图有限替换
  与总窗口恢复已实现，独立 BUY 整链复验和默认运行基线已完成；判定 helper
  或禁止全部替换不能单独证明该任务完成。
- [x] `TTA-P2-05` 只为本阶段已经存在的公共 intent/outbox、ExitPlan、admission batch/item
  加入 owner、身份一致性与唯一约束，迁移前先做影子冲突检查；cycle 表约束归 P3-05，
  尚未创建的 allocation batch/decision 约束归 P4-03，不提前创建 P4 表。
- [x] `TTA-P2-06` 覆盖部分成交、ORDER/TRADE 乱序、迟到成交、撤单失败、未知结果、退出独立恢复
  与跨域抢锁故障测试；订单生命周期及外部导入发布失败的代级持久化封禁已补聚焦验证，
  扩大回归与最终验收已完成，保留根测试唯一无关基线失败说明。

退出门：现金/库存真源仍唯一；跨域 BUY 顺序确定；退出、容量和命令恢复不依赖做 T runtime。

### P3：独立做 T 运行时与逐 Tick 状态内核

前置：P1 `DONE`；P2 可并行但 P4 前必须完成。

- [x] `TTA-P3-01` 实现 `TAssistantConfigVersion/TAssistantExecution`、生命周期、entry readiness、
  rollout stage、events 和 partial unique LIVE producer 约束。
- [x] `TTA-P3-02` 实现独立 `TAssistantSymbolState` 与逐 Tick `SymbolMarketStateReducer`；每个 accepted
  Tick 按 source identity 恰好归约一次。
- [x] `TTA-P3-03` 实现 delta ring/cursor/generation/gap/lag 处理；覆盖时 fail-closed 并 rewarm。
- [x] `TTA-P3-04` 实现强类型 `StrategyCadence.SNAPSHOT`、输入 validator、symbol patches 和
  `execution_ref`，移除做 T 对 StrategyRunState callback 的依赖。
- [x] `TTA-P3-05` 实现 `decision_key`、cycle identity/attempt 唯一约束、processing fence/lease、
  material 原子提交、续接与 `ABORTED_STALE`。
- [x] `TTA-P3-06` 在隔离 PAPER namespace 与旧 V3 逐 cycle shadow 对比，禁止 approval/outbox。

退出门：已通过最终复审。二审 blocker 的实现整改已覆盖 callback 故障恢复、非 READY
候选延迟、同 source/fence 规则比较、PREPARED startup recovery、逐标的 sequence、config
successor 排空、D-1 profile 和扩大零副作用矩阵；真实 PostgreSQL baseline→0050
隔离升级/触发器负向验证已通过。2026-09-06 复审发现并修复 material 后置校验的半写入问题，
同 execution 周期同步回退热游标的问题已修复并补齐重启与 generation/universe/config 边界测试；
根回归、只读复审及默认运行验收已完成，根测试唯一无关基线失败见 §10。

### P4：组合分配、跨域准入与 RULE_ONLY PAPER

前置：P2、P3 均 `DONE`。

- [x] `TTA-P4-01` 实现 point-in-time `TTradingEnvelope/PortfolioTDecisionSnapshot`。
- [x] `TTA-P4-02` 标准 TradeIntent 以 `ALLOCATION_PENDING` 受理；不新增 trade proposal 协议。
- [x] `TTA-P4-03` 实现 `TAllocationBatch/TAllocationDecision`、allocation batch/decision 唯一约束、
  稳定排序、CAP、claim/lease、TTL、next eligible、整批提交和 supersede；这些表与约束是 P4
  退出门，不由 P2 提前占位。
- [x] `TTA-P4-04` 实现 `EntryExecutionGate` 与最新 Tick、binding、spread、TTL 重验。
- [x] `TTA-P4-05` 将 ALLOW/CAP 候选按排名接入公共 admission、OrderSizer、Risk 和 Capacity；PAPER
  使用隔离 Broker/事实表，不消耗 LIVE 义务。
- [x] `TTA-P4-06` 提供机会、分配、readiness、reason、order/ExitPlan 的 GraphQL/Web 只读投影。

退出门：RULE_ONLY PAPER 多标的闭环可重放；半批、重复 claim、抢锁赢家和环境串写测试全部失败
关闭；没有真实 QMT 订单。

### P5：共享账户组合回测

前置：P4 `DONE`。

- [x] `TTA-P5-01` 每次回测使用 BACKTEST execution、单一时钟、共享现金/库存/费用和 Broker。
- [x] `TTA-P5-02` LIVE/BACKTEST 复用同一 step、reducer、Coordinator、Gate、OrderSizer、Risk、
  Capacity、ExitPlan 和回报收敛语义。
- [x] `TTA-P5-03` 加入多标的同时信号、部分成交、T+1 substitution、截止时间与 overnight carry。
- [ ] `TTA-P5-04` 证明无未来数据、无重复资金、无超老仓、结果 hash 可重放，并对比旧单票假设。

范围：本阶段只交付 RULE_ONLY。复用已实现的公共规则与执行语义，但不得将 PAPER
execution 改名为 BACKTEST、放宽环境隔离检查或复制一套协调/风控规则。先明确 BACKTEST
时钟、账户事实、Broker、容量和回报端口；模型 scorer、校准/OOD 及模型增量指标归 P8。

按顺序分批：

| 批次 | 范围与验收 | 不包含 |
|---|---|---|
| P5-A | BACKTEST 身份、共享账户/时钟/事实边界；真实公共路径双标的最小买卖闭环、守恒与重放 | 模型、Web 新界面、大规模评估 |
| P5-B | 部分成交、T+1 substitution、cutoff、overnight、同刻排序及未来数据负测 | 调整策略以提高收益 |
| P5-C | 冻结数据/费用/滑点和分组后运行评估，保存版本、hash、旧单票对照及准入结论 | 无限调参或因失败更换评估样本 |

P5 行情数据验收口径（2026-09-09 用户确认，覆盖下方历史检查点中的旧字段前置）：

- 历史 Tick 不提供、不要求涨跌停字段，不能据此判缺数据、补采失败或回测不合格。
- 涨跌停价的唯一来源为对应标的、对应交易日的日 K；历史日 K 两字段为空是正常数据状态，
  不要求补造，不要求为了这些字段改换样本日期。后续只在日 K 上采集这些字段。
- BACKTEST 使用 CHECK_WHEN_AVAILABLE.v1：日 K 有可用值时检查，没有时跳过缺失值检查；
  不回退到旧 Tick 字段或当前合约详情。最小价位、Tick 覆盖、身份、时序和其他交易规则照常验收。
- 分区单独记录 daily_price_limits（允许 null）；missing_reference_fields 只记录必需资料缺失。
  历史 null 不降低数据完整性判定，但该回测不能声称验证了对应日期的涨跌停边界。
- 以上仅适用于 P5 历史回测；实时行情和实时风控仍使用现有当日数据与检查规则。

退出门分为两部分，均通过才可标 P5 DONE 并解除 P6 前置：

- 工程门：组合结果可审计、守恒、环境隔离、无未来数据且可重放。
- 策略准入门：按预先冻结的费用、滑点和最坏分组标准出具结论。P0 未给出此门的完整数值，
  不再将其称为“P0 已冻结”。评估数据范围、费用/滑点情景、收益/回撤指标与阈值、最坏分组
  定义及最小样本要求须由用户确认并在 P5-C 前记录版本。未确认可完成 P5-A/B，不能完成
  P5-C 准入或开放 LIVE；评估不通过时保留证据、阻断 P6，不自动进入策略优化。

### P6：LIVE / CANARY / MANUAL_CONFIRM

前置：P5 `DONE`；实盘能力门、唯一 QMT Agent、protocol 1.2 和完整账户快照均 READY。
以上为实际 LIVE 运行前置；P6-A 隔离开发与验证可在 P5 准入等待期间推进。

- [ ] `TTA-P6-01` 在维护窗口停止旧做 T 新 ENTRY，将 legacy owner 置 DRAINING 并冻结义务清单。
- [ ] `TTA-P6-02` 结果未知旧命令仍由原 owner reconcile；旧 ExitPlan 保持 plan owner/source ref。
- [ ] `TTA-P6-03` 创建唯一 `LIVE + CANARY + MANUAL_CONFIRM + RULE_ONLY` execution，低额度、有限标的。
- [ ] `TTA-P6-04` 确认后重新 allocation、跨域 admission、Gate 和容量复核，不复用旧额度。
- [ ] `TTA-P6-05` 验证 14:50 cutoff、撤单/replace、部分成交、overnight carry 和次日原 plan 恢复。
- [ ] `TTA-P6-06` 完成 P0 冻结数量的闭环与观察期；每轮无重复单、超现金、超老仓、T+1 或 owner
  违规，且所有不执行都有 reason code。

按顺序分批：P6-A 完成人工确认后的全链重验、旧 owner 排空/切换和恢复的隔离验证；
P6-B 提交具体维护窗口与低额度 CANARY 操作方案，获明确授权后执行；P6-C 收集交易观察证据。
P6-A 不授权业务库切换或真实订单，P6-C 不使用持续代理循环等待交易日。

P0 §10 已要求的 Engine/API/QMT 断连、乱序回报、lease 过期、撤单未确认、收盘和恢复演练，
由 P6-A 首先完成隔离证据，实际运行边界在获授权窗口验证；不得等待 P7 才完成 P6 必需演练。
P7 对已有且仍有效的证据直接引用，仅增加其新故障、并发、性能和 AUTO 路径。

退出门：按 P0 §10 保留至少 20 闭环、5 交易日、3 标的及全部额度/安全/追溯门，
旧/新 owner 无同时新 ENTRY、未知与隔夜事实安全。数量不足继续收集证据，不制造交易凑数；
观察未结束只记未完成，不让实现任务无限延长。

### P7：故障注入、稳定性与 LIVE/AUTO

前置：P6 `DONE`。
此门约束 AUTO 实际运行；P7-A/B 的隔离开发、故障与性能测试可提前完成。

- [ ] `TTA-P7-01` 故障注入 Engine/API/QMT 断连、进程重启、lease 过期、DB 冲突、Redis 丢唤醒、
  delta gap、乱序/迟到回报和 successor 失败。
- [ ] `TTA-P7-02` 压测逐 Tick reducer、组合触发合并和 model-off 路径；阈值超过时 fail-closed。
- [ ] `TTA-P7-03` 验证跨域 READY BUY 同时到达、账户水位变化和 batch rollback 的确定顺序。
- [ ] `TTA-P7-04` 以新 config/successor 显式切换 AUTO；不原地修改 MANUAL_CONFIRM execution。
- [ ] `TTA-P7-05` 按另行冻结的 AUTO 观察期、回撤/熔断/运营标准验收，并保留一键阻断新 ENTRY。
  P0 §10 是人工灰度到 AUTO 的前置门，不是 AUTO 运行后观察标准，不得冒用。
- [ ] `TTA-P7-06` legacy 做 T 自身 BUY 义务归零后删除专用调度和 fallback；更新 As-Is 文档。

按顺序分批：P7-A 固定故障矩阵，注明已有证据和新增场景；P7-B 验证性能、确定顺序和
AUTO successor/阻断控制；P7-C 在明确授权后切换并收集 AUTO 观察证据；P7-D 在 legacy
自身 BUY 义务归零并完成对账后清理。不得因一次缺陷无限展开故障组合；新增必需场景须说明
受影响不变量，既有证据仅在代码或环境变化导致失效时重跑。

P7-C 前须由用户确认 AUTO 观察交易日/闭环数量、回撤与熔断阈值、运营指标及失败处置，
记录版本；这些值当前待确认。未确认不启动 AUTO 真实运行，不以人工灰度数值自动代替。
隔离实现和测试可先完成，任何未通过门都不得通过删测试或缩短观察期解除。

退出门：所有规定恢复路径无重复订单、无释放后反证穿透、无双 producer；AUTO 可被显式
阻断且退出继续，已冻结的 AUTO 观察门通过。P7-D 属于阶段收尾，仅在前述门及 legacy
义务门都满足后执行；全局后置门不应解释为必须先标 P7 DONE 才能完成 P7-D。

### P8：模型 SHADOW 与 ACTIVE

前置：SHADOW 依赖 P5 和稳定 PAPER 数据；ACTIVE 依赖 P7。
数据/标签、制品、加载和模式控制的隔离开发可提前；不以夹具结果替代模型研究准入。

- [ ] `TTA-P8-01` 冻结完整 1 分钟 Feature Bar、capability manifest、三分类 first-touch label、
  observation anchors、purged walk-forward 和 worst-group 指标。
- [ ] `TTA-P8-02` 复用不可变 dataset/spec/run/artifact/backend 原语，建立做 T 独立 registry/binding。
- [ ] `TTA-P8-03` 同坐标比较 RULE_ONLY、Logistic 与 LightGBM；DEVELOPMENT 锁定后只进行一次 FINAL。
- [ ] `TTA-P8-04` Engine CPU 安全加载并运行 SHADOW；分数不改变 RULE_ONLY 排序，缺失不阻断。
- [ ] `TTA-P8-05` 只有 OOS 组合增量、校准、OOD、制品和人工发布门均通过才创建 ACTIVE successor。
- [ ] `TTA-P8-06` ACTIVE 故障阻断新 ENTRY 且不静默降级；ExitPlan、回报和账户安全链不受影响。

按顺序独立验收：P8-A 数据/标签/切分资格与制品基础；P8-B 冻结候选、指标、试验次数和
停止条件后比较 RULE_ONLY/Logistic/LightGBM；P8-C 安全加载与 SHADOW；P8-D 仅在 P7、
模型准入和人工发布门均通过后创建 ACTIVE successor。FINAL 仍只进行一次。
数据不足或 OOS 不达标就记录失败/阻塞，不自动扩大数据工程、追加模型或反复训练。
模型门的完整阈值和实验预算须在 P8-B 前确认，不以工程实现完成替代研究有效性。
经用户决定可将 P8 标 DEFERRED；它不阻塞已验收的 RULE_ONLY 核心交付。

退出门：模型只改变合格候选排序，不读账户、不直接定量/下单；ACTIVE 证据和人工授权完整。

## 6. 跨阶段迁移与上线门

### 6.1 绝对禁止提前打开的能力

- P1 已完成但 P2/P3 未完成：公共 owner/protocol 地基已上线，仍禁止
  `T_ASSISTANT_EXECUTION` 新 runtime 产生真实订单；未注册或无法证明的 owner 继续 fail-closed。
- P2/P3 未完成：禁止 P4 产生任何真实容量义务。
- P5 未完成：禁止 LIVE。
- P6 未完成：禁止 AUTO。
- P7 未完成：禁止模型 ACTIVE。
- 任一阶段存在结果未知命令、owner 冲突、账户快照不完整或 QMT 非 READY：禁止新 ENTRY。

### 6.2 切换与回滚原则

- 切换是“停止旧 producer → 排空/冻结旧义务 → 验证真源 → 创建新 producer”，不是双跑抢单。
- 已投递命令不通过换 owner、换 payload 或新 intent 重发；只由原 identity reconcile。
- 已激活 ExitPlan 不迁 id、不复制、不改 source，source execution 终态不影响 plan runtime。
- 数据迁移只处理已证明的 owner；不确定记录 fail-closed。
- 回滚只能阻断新的风险增加并恢复到已验证写路径，不能回滚券商已发生的成交事实。

### 6.3 文档后置更新门

只有阶段行为已经在默认 Windows Dev 基线生效，才更新 As-Is 文档。目标设计和本方案可以提前
描述未来状态，但必须保持“目标/计划”标签。protocol 1.2 已切换并成为当前默认基线；本轮同步更新：

- `docs/architecture/系统架构设计.md`；
- `docs/engineering/qmt-agent/README.md`；
- `docs/engineering/engine/README.md`；
- `docs/engineering/api/README.md` 与 API 契约；
- 客户端文档、GraphQL schema/types 和相关测试说明。

## 7. 验证矩阵

| 维度 | 最低验证 | 首次强制阶段 |
|---|---|---|
| 领域纯度 | domain 边界测试、无 DB/网络/QMT 导入、同输入确定输出 | P1/P3 |
| Owner/协议 | schema、payload、幂等、旧义务 reconcile、无 fallback | P1 |
| 行情因果 | accepted Tick 恰好一次、乱序/重复/gap/ring/restart | P3 |
| 原子恢复 | cycle/allocation/admission claim、lease、冲突和半批 | P3/P4 |
| 账户守恒 | 现金、桶级老仓、部分成交、未知结果、迟到反证 | P2/P4 |
| 环境隔离 | PAPER/LIVE/BACKTEST 表、Broker、outbox 和能力门 | P4 |
| 回测因果 | 共享账户、point-in-time、无未来数据、结果可重放 | P5 |
| 实盘安全 | 唯一 producer、QMT READY、截止/隔夜、断连恢复 | P6/P7 |
| 模型 | 三分类校准、purged OOS、OOD、安全制品、SHADOW/ACTIVE | P8 |
| 前端契约 | codegen、check、lint、test、build，无 `as any` | schema 变化阶段 |

按影响范围选择验证，不把命令列表解释为每批必跑：

- Python 先目标测试；涉及 API 时选择相关 API 测试。必要扩大回归才运行 `python -m pytest tests/`；
  全量已覆盖的 API 子集不再重复运行。既有有效证据可引用，但代码/依赖/环境改变影响结论时重跑。
- GraphQL/schema/查询变化才触发实际 Caddy codegen 与 Web 的 check、lint、test:run、build 全套；
  执行命令遵守根 AGENTS.md。提前核对在线 schema 与迁移/重启授权，未获授权时报告阻塞，
  不重复尝试相同旧 schema，不用本地 schema 替代在线验收。
- 纯文档变更检查差异、引用和契约一致性，不运行 Python/Web 全量测试。
- 隔离模拟、真实数据库约束、真实运行观察分别记录，不互相替代。

集成和真实交易测试仍受仓库门禁约束。普通验证不得为了方便启动真实交易；P6/P7 的真实闭环必须
在 `ENV=testing`、账户白名单、`ENABLE_REAL_TRADING=true` 和
`QMT_REAL_TRADING_ENABLED=true` 全部显式满足时执行，并保存脱敏证据。

## 8. 风险与决策台账

| 风险 | 影响 | 预防/处置 | 状态 |
|---|---|---|---|
| `strategy_run_id` 跨层耦合范围大 | 漏改导致伪 owner 或回报失联 | P0 分层清点；P1-02/P1-06 已将 intent、pending、correlation、outbox、runtime event 和 ExitPlan source 切换为强类型 owner，并以 owner/environment 冲突反向测试守住边界 | `CLOSED`（`TTA-P1-02`, `TTA-P1-06`） |
| protocol 1.1/1.2 切换存在未知命令 | 重发可能重复下单 | P1-04/P1-05/P1-06 已完成停新命令、未知只 reconcile、唯一 protocol 1.2；最新未知结果为 `0`，无双协议/双 payload | `CLOSED`（`TTA-P1-04..06`） |
| 决策触发合并误丢 Tick | FSM/candidate 与回测不一致 | reducer 与 trigger 分层；逐标的 accepted sequence、global fence、WARMING 候选延迟及 gap/ring/lag 边界已加入聚焦回归，等待 P3 最终复审 | `OPEN` |
| cycle 与 allocation 恢复身份混淆 | 重复提案或半批 | decision key 与 attempt 分离、durable claim/fence；P3 startup PREPARED fenced abort 已加入聚焦回归，P4 allocation 尚未开始 | `OPEN` |
| 做 T 内部排名不覆盖跨域 BUY | 账户锁赢家由调度偶然性决定 | P2 已建立公共 READY 池、稳定 policy 排名、durable batch/claim/fence 和整批 enqueue；直接 LIVE BUY 统一进入 dispatcher | `CLOSED`（`TTA-P2-03`, `TTA-P2-06`） |
| 只看总可卖量破坏底仓 | core/locked_core 被错误置换 | P2 容量真源固定 swing→core（仅高于 protected floor），永不认领 locked_core；账户级 obligation watermark 在最终事务复验 | `CLOSED`（`TTA-P2-02`） |
| 收盘未成交被错误假设为闭环 | 隔夜暴露、重复批次 | cutoff、overnight carry、次日原 plan 恢复 | `OPEN` |
| 模型复杂度提前阻塞安全核心 | 延迟 RULE_ONLY 交付 | P8 后置；SHADOW/ACTIVE 独立门禁 | `OPEN` |
| 当前 legacy owner/ExitPlan/审批义务阻断 P1 | 猜 owner、误终态化或切换中重复下单 | 通过 PAPER-only、精确数量门、`SERIALIZABLE` 事务和完整 durable chain 重验，2 条审批终态化、1 条孤儿计划取消；P0 blocker 已清零 | `CLOSED`（`TTA-P1-GATE-01`） |

新风险必须追加，不能覆盖历史行。关闭风险时记录对应 task、测试和 commit。

## 9. 实施证据台账

| 日期 | Task/阶段 | 状态 | 证据 | 说明 |
|---|---|---|---|---|
| 2026-09-03 | `DOC-001` | `DONE` | [目标设计 v2.2](../architecture/多标的做T助手新架构设计.md) | 补齐不变量、恢复、准入、收盘和数据库约束 |
| 2026-09-03 | `DOC-002` | `DONE` | 本方案 v1.0 | 建立依赖、阶段门、状态和证据台账 |
| 2026-09-03 | `DOC-003` | `DONE` | [文档中心](../README.md)、[交易文档索引](../trading/README.md)、[系统架构](../architecture/系统架构设计.md) | 建立双向索引与 As-Is/To-Be 边界 |
| 2026-09-03 | `TTA-P0-01..07 / P0` | `DONE` | [P0 冻结基线](多标的做T助手P0冻结基线.md)；`python ops\t-assistant-p0-audit.py --format markdown`；审计工具单测 `9 passed`；legacy T intent owner reference invalid=`295`（`292 EXPIRED`、`2 AWAITING_APPROVAL`、`1 FILLED`；run 存在但 owner reference 未通过规则）；13 个基线测试文件 | commit：`46d05bfbb4d00d97144ffa887e619827e5fcf62f`；`405 passed, 8 warnings, 16.20s` + `9 passed`；P1 readiness=false，P1 阻塞事实已记录 |
| 2026-09-03 | `TTA-P1-01` | `DONE` | contracts/domain 单一 `ExecutionOwnerType`、`ExecutionEnvironment` 与 frozen `ExecutionOwnerRef`；普通 StrategyRun/MANUAL_COMMAND adapter；跨包类型 identity 检查；聚焦 Ruff、domain boundary 与 pytest | commit：`97f180dd76d593b98a0180a6bf8f3a7fac8bbb8f`；`52 passed, 8 warnings`；未改 Agent protocol、数据库或运行时下单链，P1 阶段仍未通过退出门 |
| 2026-09-03 | `TTA-P1-03` | `DONE` | 纯 application `OwnerRuntimeRouter/Registry`、不可变事件/目标/结果、六类 owner 精确分派及 owner/environment 冲突反向测试 | commit：`9918d811b5e7d182abb3574fe0c69a2568c4104f`；Ruff 通过，`38 passed, 1 warning`；尚未接入 report processor，接入与恢复等价性归入 P1-02/P1-06 |
| 2026-09-03 | `TTA-P1-GATE-01` | `DONE` | 默认只读、显式确认和精确数量双门禁的 PAPER legacy reconciliation；`SERIALIZABLE` + advisory lock 后在同一事务重验完整 durable chain；聚焦 Ruff 与 pytest | commit：`281ff74a2966df450991fcd2fedf8628e6a2d806`；`34 passed, 8 warnings`；受控 apply：approvals=`2`、plans=`1`；复验 safe=`0`、blocked=`0`、unsettled inbox=`0`；P0 audit blockers=`0`、P1 readiness=`true` |
| 2026-09-03 | `TTA-P1-GATE-02` | `DONE` | protocol 1.2 只读切换 preflight；复用 P0 审计并校验实际 contracts 1.1、冻结目标 1.2、queued/unknown/inbox，事务始终回滚；runbook 步骤仅作确定性模拟 | commit：`7cbe5228fc11e1d4d36a578d6ea83ac12da044e7`；Ruff 通过，`20 passed, 1 warning`；真实库首次在 unsettled inbox=`1` 时 fail-closed，收敛后复验 ready=`true`；`actualCutover=false`，未停服、备份或部署 |
| 2026-09-03 | `TTA-P1-GATE-03` | `DONE` | `ops/quantx.ps1 backup -Environment dev` 权威备份与 `restore-verify` 完整隔离恢复；修复 0045 对 pre-Alembic `create_all` 三表的严格接管和 6 个 naive-UTC 时间列原子转换；收紧 schema `status/check/assert`，结构与 revision 必须同时健康 | commit：`5c7527e7642491439ddaa70dff34edb2c2a6f8a9`；迁移单测 `15 passed`，schema/restore 合同 `17 passed`；6.8GB PostgreSQL 归档两次完整恢复至 `20260902_0045`，最终 missing tables/columns 均为空；QMT journal integrity=`ok`、pending reports=`0`，Monitor=`valid` |
| 2026-09-03 | `TTA-P1-GATE-04` | `DONE` | 实际维护窗口按顺序停止主服务、创建停服态权威备份、将开发库从 `20260901_0044` 迁移至 `20260902_0045`、用标准 full/live 入口恢复，并复跑 verify、P0 audit 与 P1 preflight；停服态备份随后完整隔离恢复 | 主服务停止期间 Monitor 保持在线；恢复后 QMT/marketData READY、protocol=`1.1`、快照新鲜；P0 blocker=`0`、P1 ready=true、queued/unknown/inbox 均为 `0`；隔离恢复 missing tables/columns 均为空，QMT journal integrity=`ok`、pending reports=`0`，Monitor=`valid` |
| 2026-09-03 | `TTA-P1-GATE-05` | `DONE` | 修复标准启动器用完整账户号比较健康接口脱敏账户标识、导致 READY 永不命中的契约错误；新增与 API 一致的纯脱敏函数和错误尾号反向测试，保留服务端完整账户集合校验为精确权威 | ops 契约 `38 passed`、Ruff 与 `git diff --check` 通过；标准 `up -Environment dev -Profile web` 冷启动 exit=`0`，随后 status 显示 full/live、QMT 与 marketData READY，gateway/schema verify 通过 |
| 2026-09-03 | `TTA-P1-02 / TTA-P1-04..06 / P1` | `DONE` | owner migration 已贯穿 intent、pending、correlation、outbox、runtime event 与 ExitPlan source；Agent 命令/报告、API、Engine、QMT Agent、GraphQL/Web 与客户端已原子切换到唯一 protocol 1.2。Router 当前仅注册 `STRATEGY_RUN`、`EXIT_PLAN`、`MANUAL_COMMAND`，未知/未注册/冲突 owner fail-closed；PLACE_ORDER 固定 10 字段、CANCEL_ORDER 固定 6 字段，均为 `FIX_PRICE` 正数限价（PLACE）；QMT `strategy_name=''`、remark 为 `qx:` 加 client order id 前 20 字符；ACK 仅投递，回报 inbox-first | 0046 owner migration、0047 vendor `order_sysid` widen 正式成功，schema head=`0047`；0046 preflight 的 intents=`2879`、pending=`3`、outbox=`4`、plans=`13`、challenges=`29`，owner 空值=`0`，8 个身份不可变触发器通过；迁移前受控修复 1 条 terminal ExitPlan orphan intent；最新 1.2 snapshots=`PROCESSED`、旧失败=`SUPERSEDED`、unknown result=`0`、无双协议；标准 full/live 冷启动 exit=`0`，`liveTrading=ENABLED`，QMT/marketData/Monitor READY，快照约 3 秒，gateway/schema verify 通过；最终恢复点为 `2026-09-03T09:03:06Z`。commit：`de9782c94ce9ef677b30f5397aa3dce98bdf6bcf` |

| 2026-09-03 | `TTA-P2-01..06 / P2` | `IN_PROGRESS` | PAPER/LIVE 公共 `ExitPlanRuntime`；source terminal independence；account-wide capacity/watermark 与桶级保护；五域 READY admission dispatcher、10s lease/3s renew/30s TTL；READY/PREPARED startup/background recovery；public T ExitPlan 角色/批次贯穿；外部成交导入原子登记；不可漂移 TEntry/TExit v1；0048 shadow preflight/FK/trigger | 最终整改聚焦组合 `207 passed`；StrategyExecutor 生命周期/重启扩大组合 `185 passed`；Alembic/schema 既有 `27 passed`；相关 Ruff 与 `git diff --check` 通过。新 T owner 仍未注册，未开放真实订单；阶段保持 `IN_PROGRESS`，等待最终只读复审与提交。 |
| 2026-09-03 | `TTA-P3-01..06 / P3` | `IN_PROGRESS` | 独立 config version/execution/event/symbol state/cycle 与 0049；SNAPSHOT 唯一 `StrategyBase.step()` 路径；WholeQuoteHub CRITICAL PAPER shadow supervisor；共享 opportunity/outcome 强类型 owner；fence-cut、wall-clock TTL/lease、提交重验、ABORTED_STALE、重启/replay/同 source-fence 差异事件及完整无订单链负向测试 | 首轮 P3 聚焦回归 `319 passed`；二审整改后 execution/market/application/migration/repository/WholeQuoteHub/shadow/global-monitor/V3/exit 联合回归 `243 passed`、聚焦 Ruff 通过。仍待真实 PostgreSQL 0049 验证、最终全量回归与只读复审；未开放真实订单，阶段保持 `IN_PROGRESS`。 |

| 2026-09-06 | `TTA-P2-05 / TTA-P3-05` 迁移门 | `IN_PROGRESS` | `QUANTX_RUN_MIGRATION_GATE=true` 下运行 `tests/infrastructure/test_p2_p3_postgresql_migration_gate.py`，真实 PostgreSQL 专用测试库随机 schema 全链 baseline→0050：`1 passed`；0048 聚焦单测 `2 passed`、Ruff 通过 | 配置 JSON 回填、账户绑定、append-only、状态版本、LIVE 唯一 producer、cycle 形状、admission 同事务绑定/重排及订单 attempt 约束均已验证；测试事务回滚且确认随机 schema 不存在。此证据不代表开发库已迁移或默认运行基线已验收，P2/P3 阶段保持未完成。 |

| 2026-09-06 | P2/P3 主任务复验 | `IN_PROGRESS` | `pytest tests/domain tests/application -q -m 'not e2e and not real_trading' -p no:cacheprovider`：269 项通过；P3 execution/market/application/repository/migration/PAPER shadow 六文件：54 项通过；`git diff --check` exit=0 | 使用仓库 `.venv`，真实交易门禁关闭。只证明列明范围；P2 外部导入失败后停止链、跨 attempt 撤换/收敛及全量回归仍在处理中，未进行新 T owner 实盘下单验收。 |

| 2026-09-06 | P2 准入历史与审批契约复验 | `IN_PROGRESS` | 最终 0048 与真实 PostgreSQL gate 组合 `3 passed`、Ruff 通过；`tests/api/unit/gqlapi/test_trade_approval_challenge.py`：`20 passed`、Ruff 通过 | PREPARED 历史仍强制当前绑定；终态后解除绑定及新事务准入通过，历史 item 不可改删、终态 batch 不可复活。审批 fixture 切换固定 TExitOrderPolicy.v1，新增 MARKET、无保护限价、配置版本冒充订单策略版本的拒绝测试。全量回归尚未完成。 |

| 2026-09-06 | P3 material cycle 原子边界整改 | `IN_PROGRESS` | 后置 owner 校验故障注入复现半写入；修复后 execution/market/application/repository/migration/PAPER shadow 六文件主任务复跑 `58 passed` | 完整 symbol/evidence/events/proposals/cycle 与 execution 水位在同一 savepoint；非法 event/proposal 和中途写异常回滚，CAS 冲突仍单独持久 ABORTED_STALE。未以聚焦通过替代默认运行基线与全量验收。 |
| 2026-09-06 | 扩大回归 fixture 契约切换 | `IN_PROGRESS` | 策略测试目录 `178 passed`；`test_account_execution_quarantine_service.py` 主任务复跑 `51 passed`；相关 Ruff 通过 | 三个旧 DummyContext 改用真实 StrategyContext；EXIT_PLAN fixture 不再伪装 StrategyRun 订单身份，生产 owner 约束保持严格。主服务只读 status 全部 STALE，旧在线证据不可复用，后续仍需统一入口恢复验收。 |

| 2026-09-07 | P3 热游标与扩大回归 | `IN_PROGRESS` | P3 六文件主任务复跑 `63 passed`；P3 子任务联合复核 `112 passed`；infrastructure/worker/qmt_agent 排除危险、实盘、E2E 与 integration 标记后 `2050 passed, 1 skipped` | 同代 reconcile 保留 hot cursor/ring，冷启动与窗口变化保守 rewarm；跳过项为显式 PostgreSQL migration gate，已有单独真实库验收，不将 skip 当作完成证据。根测试与默认运行验收仍未结束。 |
| 2026-09-07 | P2 准入恢复日志 | `IN_PROGRESS` | `tests/engine/test_risk_increase_admission_runtime.py`：`3 passed`；相关 Ruff 通过 | 恢复失败日志仅记录异常类型，不输出账户或原始异常文本；失败账户回滚后仍继续扫描，新增日志脱敏与隔离负测。 |

| 2026-09-07 | 根测试首次完整回归 | `IN_PROGRESS` | `.venv/Scripts/python.exe -m pytest tests/ -q --tb=line -m 'not dangerous and not real_trading and not e2e' -p no:cacheprovider --maxfail=15` 跑至 100%，仅 `tests/research/test_boundaries.py::test_runtime_apps_do_not_depend_on_research_package` 失败 | 失败来自 Worker 子进程命令字符串 `quantx_research.cli`，源码与测试均未在本轮修改，且 HEAD 已含该调用；不修改无关研究流程或把本次运行称为全绿。12 个 skip 为离线 MCP 9 项、Windows 研究环境 2 项、显式 PG gate 1 项。此后 P2 最终整改仍须重新验证。 |

| 2026-09-07 | 迁移前主任务复验 | `IN_PROGRESS` | 显式开启真实 PG gate 后，migration gate/0048/0049/Alembic 契约四文件 `26 passed`；生命周期/回报/准入恢复三文件 `33 passed` | 开发库只读事务复查 0048 四项冲突：invalid intent owner、duplicate outbox client、duplicate owner intent、duplicate exit source 均为 0；开发库仍在 0047，备份未完成前不执行升级。此预检不是迁移成功证据。 |

| 2026-09-07 | P2 BUY 最终整链 | `IN_PROGRESS` | 新增真实 BUY 恢复文件 9 项，结合 lifecycle/report 三文件 `39 passed`；本次候选提交 Python 文件统一 Ruff 通过，导入排序调整后 supervision/command dispatch/owner 三文件 `29 passed` | 实际 challenge/dispatcher/sequencer/enqueue，覆盖零/部分成交、stage/PREPARED 提交后进程丢失与新报价恢复、终态历史重排、总 TTL 收敛、LIVE_AUTO 失权、缺失与错 owner 挑战。只替换外部 Agent/行情/账户环境读，不做物理 QMT 投递。 |
| 2026-09-07 | 迁移前权威备份 | `IN_PROGRESS` | `ops/quantx.ps1 backup -Environment dev` exit=0；`.runtime/backups/20260906T160222Z/manifest.json`，PostgreSQL 归档 7,074,601,504 字节；QMT pending reports=0、processing commands=0；Monitor 历史包含在内 | 备份登记成功、未导出设备密钥；隔离 restore-verify 已启动但尚未完成，不把备份成功等同于可恢复性或开发库升级成功。 |

| 2026-09-07 | 存量退出义务预检 | `IN_PROGRESS` | 开发库只读事务按 source/environment/status 汇总 `auto_exit_plans` 非 COMPLETED/CANCELLED 行，结果为空 | 此时没有活动退出计划需要在迁移中重写模板；保留所有历史计划与原始事实。该时点预检不替代启动后的独立退出 runtime、owner 和账户事实验收。 |

| 2026-09-07 | 冻结后根回归 | `IN_PROGRESS` | `.venv/Scripts/python.exe -m pytest tests/ -o 'addopts=-ra --import-mode=importlib' -q --tb=line -m 'not dangerous and not real_trading and not e2e' -p no:cacheprovider --junitxml=.codex_screenshots/p2-p3-root-final.xml`：`5240 passed, 1 failed, 12 skipped, 10 deselected, 34 warnings`，410.68 秒 | 唯一失败仍为已确认存在于 HEAD 的研究边界字符串扫描测试，不涉及本次 P2/P3 变更；未放宽该测试或修改无关研究流程。报告保存在忽略目录，不提交。隔离恢复与默认运行验收仍未完成。 |

| 2026-09-07 | 维护窗口准备 | `IN_PROGRESS` | 标准 `up -Environment dev -Component monitor` exit=0，Monitor RUNNING/readiness=ready；随后标准 `down` exit=0，对九个 stale 主服务 PID 均跳过终止，Monitor 仍 RUNNING/ready | 未杀死未跟踪进程，未启动 QMT 或交易服务；隔离 restore-verify 仍在复制阶段，数据库侧无锁阻塞。开发库仍未升级。 |

后续每条 `DONE` 证据应包含 commit、验证命令及结果摘要；若输出过长，链接到仓库内稳定测试报告，
不粘贴包含账户、设备或券商敏感信息的日志。

台账中的 `TTA-P1-GATE-02`、`TTA-P1-GATE-04` 行记录的是切换前维护演练时的历史事实，
其中的 `actualCutover=false` 与 `protocol=1.1` 不代表当前 As-Is；历史原始载荷和失败原因
仍按审计要求保留。当前基线以本行 closeout 证据及下文“当前状态与下一动作”为准。

## 10. 当前状态与下一动作

### P6–P8 连续开发检查点（2026-09-09）

- 授权：单代理连续开发，统一工程验收；实际 LIVE/AUTO/ACTIVE 门保持顺序。P8 候选沿用
  RULE_ONLY、Logistic、LightGBM，用户同意先整理指标/预算建议，后续确认。
  已整理[模型评估冻结草案](多标的做T助手P8模型评估冻结草案.md)：数据与切分、6 组参数、
  最多 19 次基础拟合/校准、一次 FINAL、组合增量/校准/OOD/性能建议均为待确认，尚未运行。
- 已完成基础：Engine `T_ASSISTANT_DRAIN_ENTRY` 通过同一事务阻断新 T LIVE 源、终结没有
  订单事实的意图；pending/correlation/outbox、成交投影及原 owner 保留，ExitPlan 不改动。
  故障回滚、重启重试、scope/时间异常与 RECONCILE_REQUIRED 保持阻断已覆盖。
- 0060 补齐 pending/correlation 的 T owner 身份形状：必须有 intent，不能伪造 StrategyRun
  或 strategy order。保留旧 owner 约束，无数据重写。业务库尚未应用此迁移。
- 新 T schema 2 确认信封已绑定原 execution、候选/policy/schema 和退出模板/保护量；
  已消费确认→累计真实成交见证→精确退出授权的隔离链通过。来源已 STOPPED 仍可保护退出；
  source/schema/candidate 漂移拒绝。legacy 原 schema 1 信封维持其原义务，不对新 owner 回退。
- 退出授权派生、账户锁顺序、PAPER receipt、API 授权四文件 **73 项通过**；API SQLite/假设备
  fixture 显式模拟 Windows 平台判据，解决原用例在 macOS 先被平台门阻断的问题，生产
  平台门及真实交易开关未修改。此范围没有进行真实交易或完整 LIVE 入场整链验证。
- 验证：Conda `quantx` 下 live-drain、exit/entry command dispatch、execution owner persistence
  四文件 **72 项通过**，JUnit `.codex_screenshots/p6-live-drain-unit.xml`；
  `QUANTX_RUN_MIGRATION_GATE=true` 下 `test_t_assistant_order_identity_migration.py`
  **1 项通过**，真实 PostgreSQL 随机 schema、旧约束→0060、合法/非法 owner 形状、事务
  回滚及 schema 删除确认。此证据不替代完整迁移链、生产并发或实盘验收。
- LIVE 决策周期、候选证据、意图 intake 与分配仓储已按 execution 环境精确绑定；0061
  扩展 LIVE 分配约束，保留不可变材料、版本/租约保护，并要求排空先撤销 source、再取消
  无订单意图且同事务写审计。59 项相关单测、4 项完整迁移链 PostgreSQL 测试通过，
  包括 PAPER 回归、LIVE 分配后排空、缺失审计整事务回滚与直接 SQL 绕过拒绝。
  PostgreSQL 仅使用专用测试库随机 schema，结束确认删除；业务库未应用 0060/0061。
- API 确认服务已支持独立 T LIVE schema 2 预览/消费，按 head→execution→intent 加锁，
  新操作复核启用状态、当前 config、MANUAL_CONFIRM 与 READY；排空后仅允许已消费操作
  返回原结果。来源漂移、配置停用/切换、排空和终态重试方向检查等 **80 项相关 API 测试通过**。
  尚未新增 GraphQL 入口；Engine 确认与重新分配进展如下。
- `T_ASSISTANT_APPROVE_ENTRY` 已消费同一账户/执行/意图的 schema 2 凭据并写不可变审计，
  将原意图送回 ALLOCATION_PENDING；版本和原始金额不改动。新 attempt 使用确认后的
  account/obligation/envelope cut，重新算出的 CAP 可以降低额度；配置停用/切换继续阻断。
  API 只允许绑定该专用命令，并从 Principal 填写 actor/device，不能路由旧 run 命令。
- 0062 要求确认审计与重入分配同事务，禁止直接进入 EXECUTION_READY；数据库延迟约束
  复核确认后的快照。相同业务时刻连续写入也显式保留业务时间，避免 ORM onupdate 漂移。
  **104 项相关测试通过**（JUnit `.codex_screenshots/p6-confirmation-unit.xml`），包含
  API 预览→消费→真实消息箱→Engine→重新分配的 SQLite 集成、终态重试、审计失败回滚。
  **4 项 PostgreSQL 完整迁移链测试通过**：重新分配/SQL 绕过拒绝、旧 cut 回滚、并发重复
  确认、确认与排空竞争；仅专用测试库随机 schema，业务库未应用 0062。
- P8 隔离基础已实现 COMPLETE 分钟特征、逐路径含成本三分类标签、purged walk-forward
  与完整主 horizon 的 embargo；安全 JSON CPU 制品、Logistic/数值 LightGBM 导出与原库
  概率一致性、路径/哈希/大小/树拓扑检查；完整 TModelScore 与批量原子缓存。
  RULE_ONLY 无模型调用，SHADOW 保持规则顺序，ACTIVE 缺失/过期/OOD/超时/异常阻断 ENTRY。
  微型合成拟合仅为数值一致性单测，不是冻结草案中的正式实验；尚无研究结果或发布资格。
- P7 AUTO successor 准备服务已实现 head CAS、精确配置/审批绑定、旧源排空、新源 WARMING
  与同事务审计；重试及末尾异常整体回滚已有隔离验证。旧 pending/outbox 不迁移 owner。
  服务未注册公开命令；审批事件使用合成夹具，真实 P6 准入审计及人工发布入口仍待接入。
- P7/P8 基础与 LIVE 排空联合 **69 项通过**，Conda `quantx`、聚焦 Ruff 通过；
  JUnit `.codex_screenshots/p7-p8-foundation.xml`。测试全部为隔离工程证据，无 QMT 实盘，
  不代表 P6/P7/P8 阶段退出门通过。P8 基础提交 `8eece5a6`。
- 当前合并含两个同名 `20260909_0060` 迁移（历史下载配置、做 T 身份）；已询问业务库
  应用情况，待确认后修复编号链。此前 PostgreSQL 证据对应合并前迁移图，不能替代当前图验收。
- LIVE 公共容量服务现在复核显式 snapshot id/hash，并在刷新 ORM 对象前保存原水位，
  修复同一 identity-map 对象自比漏过快照变更的问题。容量 LIVE/PAPER **39 项通过**；
  确认、跨域准入与 runtime 相关 **48 项通过**（PAPER 夹具切换当前撮合协议 v2 后，
  其文件 20 项重跑通过）。未修改生产门禁或撮合约束。
- P6 账户级归因和组合读取已实现：显式分层基线写入现有 append-only execution event，
  后续重确认必须绑定上一版，保留完整哈希链；启动时禁止未知/未完成订单和冻结量。
  重启后按原 correlation、APPLIED TRADE 与券商成交重放共享 BucketLedger，不自动填补差额，
  不迁 owner。新 execution 可读取旧源停止后仍存在的账户归因与成交义务。
- `LiveTValuationReader` 复核全账户 T 批次、原 owner、实际成交和数量守恒；日内盈亏以
  明确前交易日收盘窗口重估隔夜头寸，使用批次冻结费用，按每个券商订单收最低佣金，
  部分成交不重复收费，撤换单独立计费。费用标记 `RULE_ESTIMATE`，不冒充实际交割费用。
- `LivePortfolioSnapshotReader` 已串联归因、估值、公共容量及未提交分配义务；冻结配置、
  周期、快照（严格小于 90 秒）、账户控制、时间和行业证据缺失均阻断，保护底仓与已接受
  订单现金不重复扣减。当前/前收盘估值已接行情供应器；尚未接入 LIVE
  supervisor 或公开基线审批；人工确认界面见下项，不据此开放真实 ENTRY。
- 上述新组件初轮 **43 项通过**；联合 PAPER、容量、确认、估值及 envelope 回归
  **154 项通过、3 项显式 PostgreSQL 门跳过**，JUnit
  `.codex_screenshots/p6-live-portfolio-regression.xml`。时间门补强及新增负测另作受影响复验。
  未新增数据库表/迁移，复用已有 append-only 事件；当前迁移冲突仍须单独解决。
- 独立 LIVE 人工确认已接入 GraphQL 与 Web 工作区：查询仅当前账户、当前配置的 LIVE
  owner；预览/消费同时检查做 T 控制、退出保护控制及确认权限，复用设备绑定的 challenge。
  重试保留原 token，持久化命令结果未明确时禁止创建下一次确认；确认成功仅显示待重新分配，
  不冒充下单或成交。页面核对同一 owner、意图和自动退出保护，切换账户清除预览。
- 确认/API/权限联合 **41 项通过**，新增异常与空队列 **3 项通过**；新页面 **6 项通过**。
  Web 全集首轮 **894 项通过、1 项旧工具栏定位断言失败**；同步第四个工作区后该文件
  **3 项定向复验通过**。本机 Caddy 实际契约 codegen、根 check/lint/build 与包预算通过；
  lint 保留一个无关的既有 Fast Refresh warning。开发机缺失的 ESLint 原生依赖已本地补齐。
  已通过统一入口重载 macOS dev/full/paper，未开启实盘或访问生产数据服务。
  证据：`.codex_screenshots/p6-live-confirmation.xml`、`p6-live-graphql-tests.log`、
  `p6-live-panel-tests.log`、`p6-live-toolbar-tests.log` 及同目录 codegen/check/lint/build 日志。
- LIVE 行情估值供应器已接入组合读取：当前价格冻结 WholeQuoteHub 同一 stream/generation/
  sequence 切面，校验源时间与采集时间；前收盘由冻结日历确定交易日，再读取持久化 Tick
  的 15:00 至最多 5 秒窗口。逐项验证 source_time_ms、ordinal、存储时间、价格及排序，
  每标的最多 1000 条，达到上限拒绝截断结果；历史读取不阻塞 Engine 事件循环。
  缺失前收盘保持缺失，由实际隔夜批次决定是否阻断；不使用 lastClose 或账户快照价格替代。
  历史读取期间 stream/generation 改变即阻断，同源后续行情不修改已冻结价格。
  行情、真实组合读取和成交估值联合 **43 项通过**，聚焦 Ruff 通过；
  JUnit `.codex_screenshots/p6-live-market-marks.xml`。未运行外部行情下载或实盘进程。
- LIVE 决策执行器已复用共享 StrategyBase.step 与周期 prepare/claim/commit，保持独立
  LIVE/RULE_ONLY 环境绑定；候选证据及标准意图按 LIVE 落库，不写 PAPER 对照事件。
  prepare 与 commit 分别按 head→execution 加锁，复核当前配置、环境和 legacy producer
  指针，配置关闭/切换不得产生旧源候选。恢复入口先检查环境及 owner，再读取或终结周期。
  真实候选、延迟释放、并发头变化与 PAPER 回归联合 **26 项通过**；恢复补强后相关
  **22 项通过**（含新增 1 项），聚焦 Ruff 通过。证据 `.codex_screenshots/p6-live-decision.xml`
  及 `p6-live-decision-recovery.log`。LIVE 行情 supervisor 接线见下项，真实入场命令 handler 尚未注册。
- LIVE supervisor 已注册 Engine 监控生命周期，使用 WholeQuoteHub 的 CRITICAL 批量订阅，
  只绑定已存在的独立 LIVE source；按冻结参数与历史参考 profile 构造共享决策快照。
  重启重新预热，日常协调保留 hot cursor/ring；配置、universe 或 durable state 变化
  使相应标的重新预热。订阅和绑定失败不会创建实盘执行或订单。
- 监控协调器按持久化独立 LIVE lineage 分流，禁止恢复/新建 legacy producer；已有 legacy
  仅撤销新入场并继续原退出。账户快照失效撤销 LIVE 绑定；停用、旧配置及多余源复用排空服务，
  不迁移 pending/correlation/ExitPlan 归属。排空前再次锁定配置头，拒绝过期的配置观察。
  联合 LIVE/PAPER 决策及监控回归 **62 项通过**；移除绑定的内存清理与排空 **17 项通过**，
  聚焦 Ruff 通过。证据 `.codex_screenshots/p6-live-supervisor.xml` 与
  `p6-live-supervisor-drain.log`。仅隔离工程验证，未重启生产或创建 LIVE source。
- 首次 LIVE CANARY 准备服务已实现：要求原 PAPER source 的
  `LIVE_CANARY_RELEASE_APPROVED` 审批，精确绑定配置 hash、P5 PASSED 证据 hash、actor、
  维护窗口、有限标的和总金额；检查 head CAS、无 legacy 指针及无既有 LIVE source。
  原子更新 head 并创建 MANUAL_CONFIRM/CANARY/RULE_ONLY/WARMING 执行，保存审批 hash，
  不自动 READY，不改原订单 owner。重试可越过已结束窗口返回原结果，但必须保持原授权身份；
  末尾审计失败连同 head 更新整体回滚。此服务尚未注册公开审批/发布命令。
- CANARY 冻结配置显式要求 `universe_policy.allowed_stock_codes`，与审批名单一致且不得
  同时列入 ignored_stock_codes；订阅层过滤、决策 prepare/commit 再校验范围。
  supervisor 测试改为通过真实首次准入服务创建 LIVE source，保留合成审批与隔离数据库。
  准入、supervisor、决策和候选证据 **33 项通过**；重试补强及 PAPER 回归 **27 项通过**
  （含新增 1 项），聚焦 Ruff 通过。证据 `.codex_screenshots/p6-live-canary-admission.xml`
  及 `p6-live-admission-final.log`。未运行正式 P5 评估，未伪造真实审批或创建业务库 LIVE source。
- 首次 CANARY READY 激活已接入 LIVE supervisor：只有热内存及持久化标的均完成预热、
  已提交周期与配置/行情/状态 hash 一致、原审批仍在维护窗口内，才读取共享账户健康与实际
  LIVE 组合快照。唯一 Agent、协议、账户控制版本和快照身份必须一致；风险阻断保持 WARMING。
  估值 I/O 后再次检查行情代次和时效，READY 状态与审计在同一事务写入；健康读取失败仅记录
  脱敏原因，重复查询限频，冷启动不凭旧数据库 ACTIVE 状态激活。
  联合准入/监督/决策/PAPER 回归 59 项通过，时序边界补强后受影响 24 项通过，Ruff 通过。
  证据 `.codex_screenshots/p6-live-readiness.xml`、`p6-live-readiness-final.xml`。
  激活测试使用真实隔离准入/状态仓库、合成审批与 mock 组合读取；不等同完整真实账户整链验收。
  尚未创建业务库 LIVE source，RUNNING 重启后入场仍须后续 handler 的重新预热门禁。
- Engine 已注册 `T_ASSISTANT_PREPARE_LIVE_CANARY`：严格限定命令字段，绑定账户、
  PAPER source、目标配置、既有审批 event key/hash 和 head CAS；拒绝随命令注入审批内容。
  重投复用原执行，失败不改变 PAPER 配置头，不创建 LIVE source。
  准入/READY/排空联合 46 项通过，证据 `.codex_screenshots/p6-live-release-command.xml`。
  尚未暴露 API 发布入口或实现正式 P5 证据审批写入；此命令只消费已有审批。
- P5 报告新增冻结 evaluation、准入 policy、数据 qualification 的哈希关联；读取入口
  要求外部提供预期报告 hash，校验报告/输入各自完整性及跨文件身份，拒绝换入另一份输入、
  修改覆盖结果或给无 policy 的结果声明准入。所有结果（含 DATA_BLOCKED）保留关联。
  此入口只验证文件关联，不把自声明 hash 当作授权，不替代运行事实与正式阈值审批。
  评估/覆盖 21 项测试通过，无 policy 的实际合成比较读取补验 1 项通过，Ruff 通过。
  证据 `.codex_screenshots/p6-evaluation-evidence.xml`、`p6-evaluation-unconfirmed.log`。
- 正式结论复核入口已实现：要求外部审核的报告 hash 与 policy hash，重建冻结准入策略，
  按各场景报告指标重新计算失败原因并严格核对 PASS/FAIL、p6_allowed 和原因集合。
  拒绝 DATA_BLOCKED、缺失已审核 policy、非法样本数量及非有限指标；场景序列化排序不改变结论。
  17 项定向测试通过，含真实合成比较生成的 PASS/FAIL、多场景与修改后重新哈希的矛盾报告，
  证据 `.codex_screenshots/p6-admission-conclusion.xml`，Ruff 通过。
  仅复核“报告指标→冻结阈值→结论”，尚不证明指标与 broker 事实一致，不据此创建发布审批。
- 评估复核已逐个读取组合/单标的对照的实际 SQLite 结果：单一读取事务校验版本未变化、
  STOPPED 执行、结果 hash、全量 frame 哈希链、最终水位与 result.json 一致性。
  报告显式保存 scenario_index；严格核对场景完整性、对照标的、执行 UUID 与冻结配置/滑点。
  28 项测试通过，含有效报告下组合或对照事实被修改、末帧删除、未完成执行和导出篡改；
  证据 `.codex_screenshots/p6-comparison-results.xml`，Ruff 通过。
  此验证证明被引用结果和事实链完整，不替代报告指标从事实重新推导，公开审批尚未开放。
- 组合指标复核已从冻结初始账户与持久化 FILL/ORDER/估值帧重建输入，复用统一指标算法
  重算收益/回撤、分组、平仓/未平仓批次、费用及现金守恒误差，并与报告完整 metrics 比较。
  拒绝重复成交、负计划剩余量、计划串标的与重算不一致；不访问服务或重跑正式历史评估。
  23 项测试通过，含 PASS/FAIL、修改费用后重新哈希、零成交未平仓场景；Ruff 通过。
  证据 `.codex_screenshots/p6-persisted-metrics.xml`。校验已覆盖存储事实到指标，尚未将
  外部审核身份/目标 LIVE 配置绑定并写入公开发布审批，不把合成样本视为正式准入。
- P5→LIVE 目标配置绑定入口已实现：先验证报告结论、事实链和指标重算，要求正式 PASS，
  再校验 RULE_ONLY/MANUAL_CONFIRM/CANARY、策略/特征版本、完整参数与信号策略一致。
  CANARY 名单须包含于已评估标的；组合、执行门、每个标的 envelope 的经济策略精确一致。
  返回绑定报告/policy/evaluation/目标配置 hash 的材料；不自行写审批或创建执行。
  7 项合成评估整链测试通过，涵盖目标参数/额度/门/范围/授权漂移，Ruff 通过。
  证据 `.codex_screenshots/p6-release-evidence.xml`。审核身份与维护窗口的审批持久化仍待接入。
- 发布审批持久化服务已实现：完整 P5/配置绑定核验放在线程中执行，事务锁定 head/source，
  检查账户、head CAS、原 PAPER 配置和无既有 LIVE source；记录审核人/引用、报告/policy/
  evaluation/目标配置 hash、名单、额度与维护窗口。审批本身不创建执行或修改 LIVE 开关。
  同一审批键只接受原材料；精确重试可在窗口结束/执行已创建后返回，审计失败回滚。
  合成评估→真实隔离审批记录→现有命令创建 WARMING 整链及负例共 31 项通过，Ruff 通过。
  证据 `.codex_screenshots/p6-release-approval.xml`。公开端身份认证与二次确认仍未接入，
  actor 和 review_reference 由未来受信调用方提供，本服务不将普通参数视作用户实际授权。
- Engine 注册 `T_ASSISTANT_CONFIRM_LIVE_RELEASE`，命令只能携带 challenge_id；核对
  已消费控制面挑战、原 outbox message/aggregate/payload、账户与消费时效。审核人取挑战行，
  不接受参数注入。评估 UUID 只解析到显式 `T_ASSISTANT_EVALUATION_ROOT` 的直接子目录。
  完整证据核验、审批写入及 WARMING 创建位于同一保存点；失败整体回滚，重投复用原结果。
  7 项隔离测试通过，含原命令重投、未消费/过期挑战、篡改命令、注入 actor、目录越界、
  维护窗口未开始时审批回滚；Ruff 通过，证据 `.codex_screenshots/p6-confirmed-release.xml`。
  API 侧挑战签发/消费尚待接入，当前测试挑战为合成记录；环境未配置评估根目录时拒绝命令。
- API 发布确认服务已完成签发/消费：复用原生唯一账户、trade:approve/t-trade:control
  权限和实时会话复验；60 秒 HMAC 凭据绑定完整请求，数据库不保存原始 token。
  消费时重查配置头，消费状态与 outbox 同事务；重试只返回原命令，配置变化/失权/过期不入队。
  Engine 按挑战表的上海本地时间语义读取，修复跨进程八小时时差。
  API→outbox→Engine→WARMING 合成整链及联合负例 15 项通过，配置变化/回滚补强后 API
  定向 10 项通过，Ruff 通过；证据 `p6-api-release-confirmation.xml`、`p6-api-release-final.xml`
  均位于 `.codex_screenshots/`。测试替换会话查库结果；公开 GraphQL 与客户端契约尚未接入。
- 原生 GraphQL 已暴露发布预览/确认，操作策略明确 native + t-trade:control/trade:approve；
  输出绑定目标/报告/policy hash、窗口和确认凭据，确认返回队列命令 ID，不声明发布成功。
  客户端 .gql、生成类型及公开 SDL/v2 operation policy 已同步。相关 Python 16 项及接口级
  1 项通过；本地 Caddy `/health/live` 与新类型 introspection 通过，根目录 codegen/check/
  lint/test:run/build 全通过（165 文件、895 测试；仅原有 Fast Refresh 警告）。
  证据 `.codex_screenshots/p6-release-{schema,graphql,codegen,check,lint,test-run,build}.log`。
  为恢复实际端点：开发库已备份 `p6-pre-schema-development.dump`（4909945 字节、归档目录可读），
  0059→0069 迁移完成；0063 对已存在 history settings 做精确结构接管，3 项测试及实际迁移通过，
  独立提交 `18db38c7`。本机忽略文件补内部随机 token，标准 dev/full/paper 服务已恢复。
  未接发布结果查询/操作界面，未创建业务审批或启用实盘，发布服务仍需正式评估根目录配置。
- 发布结果查询已完成：限定原用户/原设备并复验会话，命令成功须核对审批 hash、目标执行
  及创建事件，冲突返回 UNKNOWN；失败原因脱敏。相关 Python 17 项、Ruff 通过。
  本地 Caddy 新类型检查与 Web codegen/check/lint/test:run/build 通过（895 测试）。
  iOS 新增发布预览/确认/状态操作并同步公开 SDL；审批归属改用 executionOwner，
  缺失估值显示“暂无估值”，旧账户动作禁止发送到已移除的做 T 枚举接口。
  iOS 模拟器构建及 TTradeControlRepositoryTests/TTradeControlStoreTests 定向单测通过。
  证据 `.codex_screenshots/p6-status-*`、`p6-release-status-graphql.log`。
  原生发布界面及账户动作新接口仍待接入；未进行业务发布或实盘操作。
- 原生发布客户端与会话 Store 已接线：预览精确核对账户、配置、证据摘要和窗口；
  确认前后复验设备上下文并要求独立生物确认。入队与发布完成分别展示；确认响应丢失时
  保留原挑战供状态查询，未有终态前禁止新预览覆盖。锁定/换设备清除确认凭据。
  iOS 构建和 21 项控制流程单测通过，含响应丢失、锁定/设备变化和不完整成功拒绝。
  证据 `.codex_screenshots/p6-release-native-{build,test-final}.log`。
  控制页已接已有预览的证据核对/生物确认和发布状态卡片。只读引用与确认 token 分离，
  锁定清除 token，原设备解锁后可查询同一操作；换设备/退出清除引用。新增恢复单测后
  22 项通过，界面构建通过，证据 `p6-release-native-recovery-{test,build}.log`。
  尚需可选发布目标/证据来源入口；当前引用只保留内存，进程重启后的服务端操作发现仍待补。
- 剩余开发顺序：发布目标/证据来源与 LIVE 发布界面、原生账户动作接口适配、RUNNING 恢复入场门禁、
  分配/准入/Gate/Sizer/命令与回报接线→legacy 切换及 successor 发布接线→P7 新故障/性能→P8 数据持久化、registry 与运行接线。
  当前仍无新 T LIVE 入场 handler，P6-01..06 不据此勾选，P7/P8 工程尚未完成。
- 提交定位：本检查点与 `feat(engine): add isolated live T entry drain` 同提交；后续只更新
  本检查点的当前结论，不重复追加整轮报告。没有业务库切换、E2E 或真实订单。
  排空基础提交 `6ec95815`；模型草案 `9062cd8f`；退出授权基础与
  `feat(trading): bind T execution confirmations to exit protection` 同提交。

交接基线（P3 收尾时）：**P0—P3 已完成；P2/P3 代码、隔离迁移、实际业务库 0050 和清空功能数据后的 Windows 运行验收已完成。当批止于 P3。当前 P4 进展见本节末检查点。** P1 原子切换后，Agent 控制协议为 `1.2`；
`ExecutionOwnerRef` 已贯穿 intent、pending、correlation、outbox、runtime event 和 ExitPlan
source，`strategy_run_id` 仅作为 StrategyRun 的可选一致性见证，不能再作为默认 owner fallback。
当前 Router 仅注册 `STRATEGY_RUN`、`EXIT_PLAN`、`MANUAL_COMMAND`；未知、未注册或 owner/environment
冲突均 fail-closed。P3 的 `T_ASSISTANT_EXECUTION` 已成为隔离 PAPER shadow owner，但仍未
注册命令 handler；`ENTRY_PLAN`、`BOARD_ASSISTANT_EXECUTION` 也尚未成为可产生真实订单的
runtime。P1 运行证据、owner 空值=`0`、快照
收敛和 full/live 冷启动结果见上方 closeout 台账；维护演练中的 1.1/`actualCutover=false`
是历史记录，不覆盖当前 As-Is。

本批检查点（2026-09-07）：

- 复用已有 5240 项通过的根回归、39 项 BUY/生命周期/回报组合及 26 项迁移定向检查；
  根回归唯一失败为已确认的研究边界基线问题，不重新开展全阶段审计。
- 原完整恢复的数据导入成功，随后 schema 升级失败，旧工具已清理现场且未保存可续接 ID。
  复用 `.runtime/backups/20260906T160222Z`，没有重新备份；新验证 ID 为
  `fba35e9d6f5d4ca6`，阶段及脱敏日志位于 `.runtime/restore-verifications/<ID>/`。
  首次句柄 `37358` exit=1，数据已完整导入，0049 因 `opportunity_owner_unproven` 失败并保留现场。
  修复后使用 `-RestoreVerificationId fba35e9d6f5d4ca6` 续跑（句柄 `40892`、exit=0），
  没有重复导入或生成备份；最终 `status=passed`、`phase=complete`、`postgresVerified=true`，
  隔离库已自动清理（`retained=false`）。
- 只读定位发现 77632 条历史机会评估引用 32 个不存在的运行，0049 owner 预检不能通过。
  当前未找到对应回测、意图、运行回报、批次、退出计划或性能快照见证；不猜测环境、
  用户已明确授权删除策略机会诊断。0049 仅清理无候选、无待执行意图且无交易关联的孤立诊断，
  不构造 owner、不建立归档或兼容层；其余无法证明归属的记录仍 fail-closed。
  精确预览与实际清理均为 77632 条，原始备份保留可恢复数据；业务库未执行该删除。
- 完整验收日志：隔离库到 `20260906_0050`，schema `ok=true`，缺表/缺列均为空；
  QMT journal `integrity=ok`、pending reports=0、processing commands=0；Monitor `valid`。
  定向 PostgreSQL 负测覆盖候选、待执行意图、交易关联保护与清理幂等性。
  本批最终四文件定向回归 `26 passed`，候选提交 Python 文件 Ruff 与 `git diff --check` 均通过；
  只读复查实际业务库仍为 `20260903_0047`，本批没有对它实施迁移。
- 本批提交范围为迁移、对应 ORM 契约、定向测试和本检查点；其余 P2/P3 运行时与用户既有改动不混入。
  隔离批次提交为 `70c590fba`；当批没有修改业务库或启停交易服务，不代表 P2/P3 整体完成。

当前运行验收批次：

- 用户已确认继续实际业务库 `quantx` 迁移及标准 full/live 冷启动验收；不进入 P4，
  不开放新 T owner 真实下单，不执行真实交易测试，保留用户无关改动。
- 统一 `migrate -Environment dev` 句柄 `61526` exit=0：业务库已到 `20260906_0050`，
  缺表/缺列为空，孤立机会诊断实际删除 77632 条；迁移前完整备份为
  `.runtime/backups/20260907T052803Z`。脱敏日志为
  `.runtime/reports/p2-p3-deployment/migration-20260907-132801.log`。
- 首次标准 full/live 启动成功，QMT/行情 ready、协议 1.2、快照 23 秒；后续发现
  PAPER shadow 快照映射与可变缓存共用引用，后续 delta 覆盖来源序号导致 callback 失败。
  已用定向测试复现并隔离快照映射，行情中心与 PAPER shadow 两文件 `38 passed`。
- 用户暂停本任务后，在独立清理任务授权清空 53 张功能表（1929743 条），同时清理
  279 条相关控制命令与 5 条功能确认；保留券商事实、账户资产与原始回报。
  清理证据 `.runtime/reports/feature-data-reset-20260907/result.json`；不得重跑 reset.py
  或恢复旧业务配置来验收。清理后主服务停止、Monitor 独立在线；当前重新启动验收，
  不复用清理前在线状态，不重复全量备份/恢复，也不创建真实交易填充功能数据。
- 清理后标准启动句柄 `75493` exit=0，日志
  `.runtime/reports/p2-p3-deployment/startup-after-reset-20260907.log`：
  full/live、唯一账户、实盘门禁开启、QMT/行情 ready、协议 1.2、快照 3.223 秒。
  Caddy 健康复查 Engine/marketConsumption ready、runtimeSafety READY、ownerAudit passed。
  公共 ExitPlanRuntime、admission dispatcher 和 PAPER shadow CRITICAL consumer 均启动；
  schema check 再验通过，135 表、无缺表缺列。只自动登记 8 个策略定义，运行实例、
  T 配置/execution/cycle、退出计划、trade intent 均为 0；没有恢复旧配置或产生新 T 订单。
- 最终验证复用上方根回归 `5240 passed, 1 failed`（唯一失败为无关研究边界基线）、
  BUY 整链 39 项、迁移 26 项；最后快照修复定向两文件 38 项通过，全部候选 Python
  Ruff 通过；补齐首次订阅缓存引用负测后两文件共 39 项通过。PAPER 有绑定的行为由隔离测试证明，空配置线上仅证明正常启动与零订单，
  不将空配置运行冒充真实候选对比或真实交易测试。运行时与文档作为同一收尾批次提交。

### P4 当前检查点（2026-09-07，应用层契约批次）

- 已核对 P2/P3 阶段表、§10 运行及隔离迁移记录与提交
  `70c590fba`、`4c43f9ee7`、`fb309e115`；P4 前置成立。页首先前 2/9 为未同步统计，
  按阶段表纠正为 4/9，没有重新开展 P2/P3 全量或实盘验收。
- 新增 `packages/application/src/quantx_application/t_trade_v3/portfolio_snapshot.py`：
  immutable evidence cut、envelope、portfolio snapshot；带时区时点和 UTC hash，保护
  locked_core/core floor、未覆盖义务、全账户行业聚合及有限 Decimal；派生上限不可单独覆盖。
- 新增同目录 `portfolio_allocation.py`：标准已受理 intent 的只读引用输入、稳定六字段排序、
  ALLOW/CAP/DELAY/REJECT、行业/现金/总额/并发/单票约束、最低交易规划量、TTL 与 next eligible。
  计算结果没有资金预占或最终股数；decision cycle 与递增 allocation attempt 分离。
- 新增同目录 `entry_execution_gate.py`：重新读取的 accepted Tick、config/policy/schema/
  fingerprint/capability binding、generation/ring、TTL、价差/价格偏离与能力质量重验。
  仅 RULE_ONLY PAPER，未开放 LIVE、模型模式或真实订单。
- 对应 `tests/application/test_t_portfolio_snapshot.py`、`test_t_portfolio_allocation.py`、
  `test_t_entry_execution_gate.py` 覆盖到达顺序全排列、非候选行业存量、CAP 原因、延期到期、
  输入篡改/未来时点/混合环境与 owner、Gate 最新行情和缺失能力；复审后补齐
  原 next eligible 不提前、候选规则/Gate 独立 policy 版本、NaN/布尔时点与序号拒绝、
  账户熔断具体原因码。
  `.venv/Scripts/python.exe -m pytest tests/application tests/domain/test_t_assistant_execution.py
  tests/domain/test_t_assistant_market_state.py -o 'addopts=-ra --import-mode=importlib' -q
  --tb=short -p no:cacheprovider`：**273 passed**；六个新增 Python 文件 Ruff 与 format --check 通过。
  首次扩大命令误指不存在的 `tests/domain/test_risk_increase_admission.py`，未收集测试；
  已纠正命令，不计入通过证据。
- 本批仅应用层契约验证，**六项 P4 task 均未勾选 DONE**。尚需：同一数据库事务切面 adapter、
  cycle 内标准 `ALLOCATION_PENDING` 原子受理、allocation 持久化/唯一约束/lease/CAS/recovery、
  PostgreSQL 隔离验证、公共有序 admission 与 PAPER Broker/事实/ExitPlan 闭环，以及 GraphQL/Web。
  尚未改 GraphQL，未执行 codegen/Web 检查；未迁移业务库、启停服务、执行 E2E 或真实交易。
- 接入审计已定位：公共 intent repository 现有 intake 内部 commit，不能直接放入 cycle savepoint；
  admission 为 LIVE adapter，同 producer 排名尚不携带 allocation rank；公共 ExitPlan SELL 路由
  仍读取 LIVE Position/TradingService，PAPER 必须先实现隔离事实与 Broker adapter 才能接线。
  不把本批纯函数测试当作环境隔离或 PostgreSQL 原子性证据，不提前更新 As-Is 已生效行为。
- 当前工作树已有其他 API/Web、Worker、快照和退出历史改动，非本任务所有；本批只批准上述
  六个新文件与本方案，由专用 Git 子代理提交，不混入其他文件。提交记录由 Git 历史定位
  `feat(t-assistant): add P4 portfolio planning and entry gate contracts`。

### P4 标准受理与数据库约束批次（2026-09-07）

- 上批提交：`d4cfb1b9dc1a4fceaec862819525e3978cdbcd83`，应用层快照/排序/Gate，
  273 项通过；P4 整体仍为 IN_PROGRESS。
- `trade_intent_intake.py` 提取唯一标准 serializer，`RuntimeStateManager` 委托该实现。
  `TradeIntentRepository.accept_intents_idempotent` 在调用者会话内 savepoint/flush，
  不提交外层事务。原 standalone create adapter 共用同一受理逻辑，仅承担外层 commit。
- P3 cycle 与 Engine 改为直接传标准 `TradeIntent`，在原 material savepoint 中原子写
  `ALLOCATION_PENDING` 及 allocation cycle/version；manifest 只保存 accepted intent ID/hash，
  不再保存第二套 proposal JSON。强校验 PAPER、BUY、owner/origin/cycle、candidate/evidence
  和 policy/schema，晚失败回滚全部 state/evidence/intents。标准 Float 数值规范化保证数据库
  round trip 后受理 hash 不漂移，后续 allocation 复用共享 initial material projection。
- 新增 `t_allocation_batches/t_allocation_decisions` ORM 与 0051：唯一 cycle/attempt、单个
  PREPARED、完整 claim/terminal 形状、CAP/DELAY 约束、deferred count/rank/intent 整批绑定、
  append-only history；标准 intent 增加 cycle/version/current decision/next eligible。
  阻断初始借用 decision、pending 直跳、owner/environment/cycle 串写和受理材料篡改；
  ALLOW/CAP 按冻结 MANUAL_CONFIRM/AUTO 分别进入 AWAITING_APPROVAL/EXECUTION_READY。
- 不改变历史 baseline 指纹；仅把新增表/列加入其 post-baseline 排除映射。
  首次隔离迁移因尚未更新排除映射被 baseline hash gate 拒绝，修复后指纹校验保持原值。
- 最新真实 PostgreSQL 验证：`QUANTX_RUN_MIGRATION_GATE=true` +
  `.venv/Scripts/python.exe -m pytest tests/infrastructure/test_p4_postgresql_migration_gate.py
  -o 'addopts=-ra --import-mode=importlib' -q --tb=short -p no:cacheprovider`：
  **1 passed**（21.80 秒，句柄 71358 exit=0）；专用测试库随机 schema，完整 baseline→0051，
  全部事务回滚并断言 schema 不存在。不是业务库迁移，也不是多连接 recovery 验收。
- 定向组合：`test_t_intent_atomic_intake.py`、`test_t_assistant_runtime_repository.py`、
  `test_trade_intent_acceptance.py`、`test_runtime_state_manager_v3_recovery.py`、
  `tests/engine/unit/test_t_assistant_paper_shadow_runtime.py`、`test_alembic_contract.py`，
  使用仓库 .venv、`-o 'addopts=-ra --import-mode=importlib' -q --tb=short -p no:cacheprovider`：
  **76 passed**。包含真实候选生成标准 intent、外层回滚、两意图 flush 后晚异常全回滚、
  精确 retry/冲突和 serializer 数据库 round trip；相关 Python Ruff 通过。
- 仍未完成：allocation repository/lease/recovery（独立子任务正在实现）、真实 PG 并发重启
  故障矩阵、权威 portfolio snapshot adapter、公共排序 admission、隔离 PAPER 账户/Broker/
  成交/ExitPlan 整链及 GraphQL/Web。无业务库写入、服务启停、E2E、真实 QMT 投递。
  本批不将 P4 task 勾为 DONE，不更新 As-Is 已部署行为；下一批复用上述证据。
- 本批批准范围为上述标准受理 8 文件、allocation ORM/注册/注释/intent 字段、baseline
  排除映射、0051 migration、真实 PG gate 测试与本检查点。allocation repository 后续文件
  不纳入本批。提交由专用 Git 子代理执行，历史主题
  `feat(t-assistant): persist atomic intent intake and allocation constraints`。

### P4 allocation 恢复与数量上限批次（2026-09-07）

- 上批提交 `8e3c543ee62ad36c2900176dae75790e6921356c`；本批新增
  `repositories/t_allocation_repository.py`、`services/t_allocation_serialization.py`
  与对应测试。prepare/claim/renew/commit/expire/supersede/recovery 只 flush；整批提交在
  savepoint 内重算分配、先落全部 decision，再推进全部 intent version/status。
  eligible 集合、原始意图材料、最新 RUNNING/READY、source/intent 双 TTL 均复验；
  RULE_ONLY 固定 `rank_score = rule_score / 100`，rule score 绑定原机会证据。
- 主审修复：P3 与 allocation 共用 execution→cycle 锁序；原始受理 hash 只包含不可变
  producer 材料，排除合法 notes/risk/order annotations。0052 增量迁移保护全部对应
  producer metadata 与请求字段，避免执行中的兄弟 intent 阻断 DELAY 后续 attempt。
- 共享 `OrderSizer.draft_intent(..., allocated_amount_cap=...)` 使用含佣金及过户费的
  现金上限限制合法整手，不修改原请求；保留具体缩量/不足一手原因。未传 cap 的既有调用
  语义保持不变；更高申报门槛仍由公共 RiskChecker 校验。
- 验证：`QUANTX_RUN_MIGRATION_GATE=true`、仓库 `.venv/Scripts/python.exe -m pytest
  tests/infrastructure/test_p4_allocation_postgresql.py -o 'addopts=-ra --import-mode=importlib'
  -q --tb=short -p no:cacheprovider --maxfail=1`：**5 passed**（100.28 秒，36741 exit=0）。
  每例专用测试库随机 schema，baseline→0052；覆盖三连接 prepare/claim 唯一赢家、
  新进程对象接管旧 lease/fence、半批异常后重试、DELAY 第二 attempt/CAP 历史保留、
  兄弟 intent 注释、producer 篡改拒绝、TTL/supersede/环境串写及真实 P3 prepare 交叉锁。
  每例结束后仅清理自己的测试 schema 并确认不存在，未写业务库。
- 定向扩大回归：application 的 snapshot/allocation/Gate 三文件、infrastructure 的
  allocation/atomic intake/runtime repository/intent acceptance/runtime recovery/Alembic
  六文件、Engine PAPER shadow 与新 sizing 文件共 **298 passed**；另既有
  `tests/domain/test_positive_t_order_capacity.py` **5 passed**。均使用上述 pytest 参数；
  本批十个 Python 文件 Ruff 通过。首次 PG 的 3/4 项证据被上述最终五项整改复验替代。
- 当前验证只证明 allocation 持久化、恢复和共享 sizing 接口；账户 snapshot/义务真源
  adapter、公共 admission 最终排名、隔离 PAPER ledger/Broker/成交/ExitPlan 和只读
  GraphQL/Web 尚未完成，P4 不标 DONE。未改本任务 GraphQL、未启停服务或真实下单。
  下一批 `paper_execution.py` 与 `paper_broker_matching.py` 正在实现，不纳入本批提交。
  本批提交主题为 `feat(t-assistant): recover fenced allocations and enforce sizing caps`。

### P4 PAPER 账本组件验收检查点（2026-09-07，整链仍未完成）

- 已提交 allocation 恢复/sizing 批次：`e6a962963ed4303fa468144a24dca24ec80edd98`。
- 新增有界 `paper_broker_matching.py`：复用 BacktestBroker 严格五档撮合、费用与 T+1，
  显式 await，无随机后台任务或 Broker callback。checkpoint schema 2 仅保留经济状态、
  活动委托和每标的最新行情，历史幂等由 PAPER 事实表承担；1000 Tick 与 40 个终态订单
  的测试阻止历史进入检查点持续膨胀。
- 同一个公共 admission sequencer/repository 已支持 PAPER execution scope；LIVE/PAPER
  查询、attempt、fingerprint 隔离；T 组按已提交 allocation 的 group/rank 排序，prepare/
  commit 使用相同来源。修复长寿命 Session 的 intent 过期缓存，以及真实 PG 暴露的
  item insert 早于 intent binding update 的 flush 顺序，未放宽数据库绑定约束。
- 0053 与 `models/paper_execution.py` 建立独立账户、event、order、fill 事实表及
  PAPER admission scope；包括 seed 不可变、revision/hash 链、receipt 双向引用与新 BUY
  当前授权检查。最新 `QUANTX_RUN_MIGRATION_GATE=true` 下运行
  `tests/infrastructure/test_paper_scope_postgresql.py`：**3 passed**（77.66 秒，26916 exit=0），
  证明 baseline→0053、公共 PAPER admission、正向 order/两次 partial fill 的分事务恢复、
  历史幂等、回报 sink 异常回滚、空成交 quote 事件链、半账拒绝、缺失 fill receipt/
  失效和过期授权拒绝，及 LIVE 五张事实/控制表零写入；测试 schema 已清理确认。
  首次 68428 因上述 flush 顺序失败，此后已修；不将此用例称为真实订单/成交/ExitPlan闭环。
- matcher/ledger/PAPER admission/既有 LIVE admission/Alembic/Engine admission 六文件
  最终组合复验 **93 passed**（9.50 秒，24154 exit=0），相关 Ruff 通过。
  使用仓库 .venv 与前述 pytest 参数。最终 ledger 修复后 PG 正向订单/成交/恢复用例
  定向复验 **1 passed**（26.30 秒，98108 exit=0），其余未受影响的 PG 证据复用上述结果。
- `paper_execution_ledger.py` 在同一 savepoint 中落 PAPER facts、账户/桶账并 await
  必填异步 receipt sink，失败全回滚。测试 sink 只证明事务边界，尚非公共 ExitPlan 闭环。
  sizing/risk 输入复用真实 OrderDraft/OrderRiskDecision，不自造 evidence 协议。
- 审计暴露的新 BUY 原授权 TTL 与 Decimal CAP 精确边界已修复并复核；0053 已增加
  新 BUY 提交时间必须处于 decision 与 admission 有效窗，历史订单更新不追溯撤销。
  ledger 账户互斥尚不是最终排名门，不能宣称锁顺序已经保证 admission 排名。
- 账本组件已通过最终审核，批准提交；最终排名门与公共 receipt sink 未据此验收。
- 下一步：把 sink 接入公共 intent /
  TTradeBatch / ExitPlan 收敛，补权威 portfolio snapshot reader、最终按 rank 的公共
  admission/Capacity/Gate runtime，再完成 GraphQL/Web 与实际 Caddy codegen 门。
  P4 保持 IN_PROGRESS；本轮未启停服务、操作业务库或执行真实交易。
  公共 plan 持久化核心正在按 execution owner 解耦：复用同一 AutoExitPlanService /
  ExitPlanBook / CAS，T PAPER 不伪造 StrategyRun、不从 LIVE Position 取数。卖出 receipt
  先应用订单累计成交再应用逐笔成交；后续买入 partial fill 必须保留计划 pending/暂停状态。

### P4 公共 PAPER 回报收敛检查点（2026-09-07，整链仍未完成）

- 上述账本组件已提交 `9760209761bf9f0f1c65c627b36fc2f137c4aee7`。
- `AutoExitPlanService` 新公共 execution-owner 核心由旧 StrategyRun adapter 和 T PAPER
  共用；T PAPER 锁序为 execution→PaperAccount→同源 plans，使用同一公共 plan/CAS/event。
  非 StrategyRun 不制造 run_id，模板与列的 source 三字段由权威 ref/environment 派生并校验。
- 新 `PaperReceiptConvergence` 在 ledger savepoint 内更新标准 intent、公共 TTradeBatch 与
  ExitPlan。首笔实际买入成交激活保护，后续成交按 event key 幂等扩充；部分买入撤单仍保留
  OPEN 暴露；卖出按订单累计 barrier 后逐笔成交收敛，完成时 plan/batch 同步终结。
  买入原策略 source 停止不终止已有保护。尚未接 Engine 的最终按排名 dispatcher。
- 修复共享 plan 后续 entry fill 覆盖 pending/暂停/错误状态；成交时间 hash 统一 UTC、
  交易日按上海时区；sink 和 matcher 使用同一冻结费用政策并拒绝不一致模板；fill 列与
  receipt 逐项对账，NUMERIC(24,8) 比较使用显式 HALF_UP，不依赖默认 HALF_EVEN。
- 公共服务导入暴露 baseline 的懒加载 optional `divid_factors` 导致 fingerprint 随导入顺序
  变化；历史 clone 显式排除原本不由 baseline 创建的该表，原 fingerprint 保持不变。
  `test_alembic_contract` 增加真实 late-import 回归，未改写历史 schema/hash。
- 最终组件/旧服务/锁/Engine runtime/domain/matcher 七文件回归 **133 passed**（4.73 秒）；
  baseline/ledger/标准 allocation helper 回归 **67 passed**（10.79 秒，53045 exit=0）。
  首次扩大锁测试的 fixture 缺 source 三字段，已补完整契约并复验，生产没有 fallback。
- `QUANTX_RUN_MIGRATION_GATE=true`，`test_paper_scope_postgresql.py` 的 public 两用例
  **2 passed**（61.46 秒，54665 exit=0）：真实迁移约束下 BUY/fill→公共 plan→SELL/fill→
  plan COMPLETED/batch CLOSED，且实际公共 plan 写入后的故障使 ledger/fills/公共投影整笔回滚；
  LIVE 五张事实/控制表零写入，隔离 schema 已清理。NUMERIC 半值边界修复后的单例
  最终复验 **1 passed**（26.94 秒，45582 exit=0），直接比较真实 NUMERIC CAST，相关 Ruff
  全部通过。审计整改已复核，主代理批准本组件批次提交。
- 以上是组件闭环，不是 RULE_ONLY 多标的完整运行时验收。剩余：权威 PIT portfolio reader
  与行业来源、日损失和控制来源；最新 EntryGate、最终按 rank 的公共 admission/Capacity/
  Sizer/Risk、公共 PAPER ExitPlanRuntime 及 TradeIntentProcessor 隔离路由；多标的实际
  StrategyBase.step 重放与 GraphQL/Web/Caddy codegen 门。P4 六项任务仍未整体勾选。
  现有 P3 reference profile 没有主行业及分类时点，不能默认 UNKNOWN 为中性或逐票独立行业；
  BacktestBroker.daily_pnl 恒为零，不能当日损失真源。新 reader 必须显式取得这些证据。

### P4 公共容量与组合事实读取检查点（2026-09-07，整链仍未完成）

- 公共回报收敛批次已提交 `c8832c7b65d5c0115fdc6d68e74fcb294fc16ce7`。
- 同一个 `AccountCapacityService.read` 已扩展显式 PAPER execution/account scope，
  不接收 LIVE control，不从 LIVE 余额/库存/投递表回退；保持 execution→PaperAccount→
  同源事实锁序。已成交资金不重复预留，`paper_pending_buy_cash` 按剩余限价金额和未付
  累计费用计算；batch/plan/同计划 pending SELL 认领旧仓一次，STOPPED source 仍可退出。
  未下单 READY/APPROVAL 的 cap 只属于协调规划，未伪造成最终冻结资金。
- 新 PAPER capacity 与既有 LIVE capacity 主代理复验 **35 passed**（4.57 秒）；
  `QUANTX_RUN_MIGRATION_GATE=true` 下 `test_paper_scope_postgresql.py -k capacity_after_partial`
  **1 passed**（27.24 秒，83484 exit=0），真实 PG 上验证部分成交/撤单现金与公共保护认领。
  相关 Ruff、审计与主代理最终审核通过，批准本容量组件提交。没有业务库或实盘操作。
- 进行中的下一部分：`PaperPortfolioSnapshotReader` 从冻结配置、PAPER facts 和同一公共
  Capacity 构造完整 cut。明确行业分类与完整交易日历作为配置内版本化 PIT 证据；按
  上海交易日以明确上一交易日收盘 mark 重置当日 T 成本账，逐成交计费用与成本释放，
  不把种子旧仓、历史浮盈或 broker 的固定 daily_pnl=0 当当日 T 盈亏。
  新 reference/日内估值纯计算已有 **16 项定向测试通过**，reader 尚在实现，未据此验收。
  最终 ranked dispatcher / 最新 Gate / runtime PAPER route / 多标的重放 / GraphQL-Web
  仍未完成；P4 保持 IN_PROGRESS。
- 公共容量批次已提交 `4bf6638ad5fd70f1c07420bf47750a94d50e83dc`。新增最终账本排名门
  在 execution/account 锁内验证同 admission 连续 rank、当前 intent 绑定和前置实际委托；
  前置只有 ROUTED/FILLED 等投影状态而无订单不能放行，仅明确拒绝/过期/取消可跳过。
  历史 event/order 幂等仍先于新授权检查。主代理新排名/账本/公共回报复验 **41 passed**
  （9.65 秒，88084 exit=0）；真实 PG 两连接故意令低优先级先取得 execution 锁，仍被排名门
  拒绝，随后高优先级受理、刷新账户水位后低优先级才可受理：**1 passed**（27.32 秒，
  24029 exit=0）。相关 Ruff 与最终审核通过，批准排名门组件提交，未称 dispatcher 完成。

### P4 权威组合读取验收检查点（2026-09-07，整链仍未完成）

- 排名门已提交 `19c424abaa498aded0cae485f87588f9e9f66e79`。本批新增
  `portfolio_reference.py`、`daily_t_valuation.py`、`PaperPortfolioSnapshotReader` 及对应测试，
  从真实 PAPER 账本/公共义务、冻结配置、显式行业与交易日历证据构造标准组合快照。
- cut 使用来源可用性时间；当前控制/intent/batch/plan 的未来变更不能穿越旧 cut。
  移除伪造 batch 身份；已平仓历史保留归因但不再强求新行情或无关行业映射。
  未提交 allocation/claim 不污染自身输入，真实 read→prepare→跨会话 claim→commit 可恢复。
- 行情事件改为明确引用与正式收盘窗口查询，26 个历史事件下只加载 3 个必要事件。
  0054 增加对应 scope/type/time 索引；没有业务数据库迁移。CAS `updated_at` 修为 UTC
  可用性，避免上海 naive 时间误作未来八小时；该 repository 中其他历史删除改动不属本任务。
- 主代理组合回归 **158 passed、2 skipped**（9.89 秒，61347 exit=0）；跳过的两个 PG gate
  已单独在真实隔离 schema 从 baseline→0054 运行：**2 passed**（51.10 秒，71128 exit=0），
  覆盖真实剩余义务/部分成交/日损益，以及原始待分配→prepare→重启恢复→claim→ALLOW提交，
  并核对实际索引定义。最初 PG fixture 的 aware→naive 绑定错误已按列类型修复；schema已清理。
  相关 Ruff、审计整改与主代理最终审核通过，批准本读取组件提交。
- P4 仍 IN_PROGRESS。下一批要把这些组件接入公共按排名 dispatcher、最新 EntryExecutionGate
  与 Sizer/Risk/Capacity 最终事务；补 PAPER ExitPlanRuntime/TradeIntentProcessor 路由及停止
  source 后仍需撮合退出的行情接线，再完成实际 StrategyBase.step 多标的重放与 GraphQL/Web。

### P4 公共卖出数量边界检查点（2026-09-07，运行时仍在接线）

- 权威组合读取批次已提交 `844ccd3b1cc7e442058de8ecf44f644b0956f487`。
  接入 PAPER 公共退出时发现：把保护后的业务可用量传作券商真实可卖量，会让
  `normalize_sell_volume` 错把保护 cap 当零股清仓例外。例如真实可卖 1000、保护后仅余
  50 股时，不应据此产生 50 股订单。
- 公共 `OrderSizer.draft_intent` 增加独立 `sell_volume_cap`，先限制请求，再使用真实券商
  可卖量执行原有整手/零股规则；保留原请求，输出 cap 与券商可卖量审计证据。
  真实可卖 1000/cap50 得到 0，真实可卖 50/cap50 仍可清仓 50，cap150 得到 100。
- 主代理最终审核及新卖出 cap/既有买入分配 cap 定向验证 **43 passed**（0.75 秒，
  命令 `pytest tests/domain/test_order_sizer_sell_capacity.py tests/domain/test_t_allocation_order_sizing.py`，
  使用统一隔离测试参数），相关 Ruff 通过。批准这两个独立域文件与本检查点提交。
- P4 保持 IN_PROGRESS；公共 PAPER 退出、最终入场 review 与组合协调器尚未整体批准。
  当前整链整改包括：候选引用其原始不可变评估（覆盖多 Tick 和 deferred release），以及
  跨标的行情 source time 与账户事件可用性时钟分离。停止 BUY source 后的存续退出行情
  接线已在推进，尚需上述时钟契约及最终多标的重放验收。没有业务库、服务启停或实盘操作。

### P4 原候选证据与最终入场检查点（2026-09-07，组件验收）

- 公共卖出 cap 已提交 `c63d77e6a6b458aca346dc82bfc549236c4ad388`。
  本批修复实际策略同轮早期 Tick latch、后续 Tick 评分改变，以及 WARMING 候选无新 Tick
  释放时的来源绑定。每个候选唯一保存 `T_OPPORTUNITY_CANDIDATE_FROZEN`，载荷包含
  candidate/evaluation/tick/cursor；标准 cycle 的 accepted_intents 引用其 key/hash。
  原 source/evaluation 时点与 TTL 保持不变，展示类 latest_evaluation 不再充当分配证据。
- `PaperAllocationCoordinator` 从该不可变来源、权威 PAPER snapshot 和公共分配仓储完成
  prepare/claim/commit；不接收外部排序/数量，不替代公共分配算法。真实 StrategyBase.step
  两场景均按原流动性进入分配，原末 Tick 信号重写 fixture 已移除。
- `PaperEntryExecutionReview` 使用同一原候选证据校验冻结 binding，检验最新 accepted Tick
  与完整盘口，再调用公共 Capacity/Sizer/严格 Risk/隔离 Ledger 及真实 receipt sink。
  校验实际交易时段、原来源/当前 symbol 可用性、严格整数 witness；规范化 state 后统一 hash。
  历史受理通过原 ORDER receipt 恢复，停源/过期/新 Tick 不重新授权也不重复下单。
- 主代理最终审核和 16 文件组合回归 **349 passed、2 skipped**（27.03 秒，11520 exit=0）。
  两项独立 PG gate：actual deferred 策略→原候选引用→真实组合读取→分配提交
  **1 passed**（25.54 秒，76582 exit=0）；公共退出真实受理/恢复/成交及故障回滚
  **1 passed**（55.17 秒，73153 exit=0，两个 schema）。均为 baseline→0054 的随机隔离
  schema，清理成功，无业务库迁移。相关 Ruff 与原候选/协调器独立审计通过。
- 批次仅验收候选证据、组合协调器和最终 review 组件。review 的拒绝/延期仍由后续 dispatcher
  持久审计；`WAIT_PREDECESSOR` 等待前置，`REBUILD_CANDIDATE` 必须撤旧 grant 后全链重建，
  不能每 Tick 复用旧分配。最终 ranked runtime、显式 PAPER seed/readiness、跨标的 source time
  与受理时间分离、实际多标的完整重放及 GraphQL/Web 尚未完成，P4 继续 IN_PROGRESS。

### P4 公共 PAPER 退出路由检查点（2026-09-07，组件验收）

- 原候选/组合协调器/最终 review 已提交 `49707bad3b3a7a14e6d14262e82eb2ba65dfd872`。
  公共 `ExitPlanRuntime` 的 T PAPER 评估与确认使用同 execution 的持仓及已接受完整 QUOTE，
  不先读取 LIVE Position。PAPER context.timestamp 是评估时点，原行情时间继续用于新鲜度，
  不把较早行情时间回填为卖单提交时间。
- `TradeIntentProcessor` 在 TradingService/Agent 路由前明确分流到公共 Capacity/Sizer/严格
  Risk/PaperLedger，使用真实券商可卖量与独立 sell cap、execution 冻结 core floor。
  同一事务的真实 receipt sink 独占委托/成交/计划收敛；成功后不再人为补写 PENDING。
  恢复只认同 scope PaperOrder，来源 STOPPED 不阻止已承诺的公共退出。
- 主代理已复核前节 **349 passed** 中的公共链及 Engine 新旧测试，复用最终 PG
  **1 passed/55.17 秒/73153 exit=0** 的真实公共受理、重复恢复、成交关闭和 core floor
  合法订单进入 sink 后故障回滚证据。另补闭市 RESERVED 延期公共事件，固定业务键保证重复
  调用仅一条 `EXIT_INTENT_DEFERRED`，保留保护与 RESERVED、零卖单；主代理定向 **1 passed**
  （1.62 秒）。相关 Ruff 与最终审核通过，批准公共退出 8 个代码/测试文件与本检查点提交。
- 尚未据此验收行情调度。跨标的 source time 与本地受理时间需要下一原子协议批次；旧来源
  存续经济义务的 Engine 行情 pump、最终 ranked entry dispatcher 及多标的整链仍在推进。
  未完成 GraphQL/Web，P4 保持 IN_PROGRESS，没有业务库/服务启停/真实交易操作。

### P4 PAPER 行情双时钟检查点（2026-09-07）

- `paper-strict-book-v2` 保留原始行情源时间，显式传入受理时间；账户、订单超时与成交事实按
  受理时间推进，成交只消费下单后、同交易日的新源时间流动性。跨标的延迟行情不重打源时间。
  同源时间的新批次可更新盘口和 TTL，但返回并持久化
  `PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY`，不重复成交。UTC/+08 同一时刻重试得到同一事实。
- 0055 增加 `quote_source_at`、源时间索引、事件/成交双时钟约束和 v2 policy 门。
  旧 PAPER 账户非空时在任何 DDL 前拒绝升级，不改写 seed、checkpoint 或不可变哈希链；
  本轮仅在随机隔离 schema 验证，未操作业务库或启停服务。
- 验证：matcher/双时钟/账本/真实回报定向 `61 passed, 2 skipped`；主代理补验实际策略候选、
  最终 Gate、分配、估值、公共退出、容量及 seed 边界 `85 passed, 5 skipped`。
  跳过项为显式 PG 门。PG 双时钟及升级拒绝门 `2 passed`（句柄 55776，3 schema），
  reader/公共退出/scope 门 `3 passed`（75752，4 schema）；同源整改后定向 PG `1 passed`
  （58347，2 schema，52.90s），均 exit 0 且隔离 schema 清理完成。日志见
  `.codex_screenshots/p4-paper-clocks-postgresql.log`、
  `p4-paper-clocks-integration-postgresql.log`、`p4-paper-same-source-postgresql.log`。
- P4 保持 `IN_PROGRESS`。运行时 seed/readiness、停用收敛、排名派单恢复与候选反馈正在接线；
  全链多标的重放和 GraphQL/Web 只读投影仍需最终验收，不将组件验证写为阶段完成。

### P4 候选反馈输入检查点（2026-09-07）

- 标准 PAPER BUY 意图的完成、拒绝、过期、撤销、已路由和待确认状态，按同一
  execution/account/candidate fingerprint/source time 读取为 `CandidateControl`。
  读取强制刷新 ORM 缓存，拒绝未来可用时间、重复候选受理和跨绑定证据。
- 控制量进入 `SymbolDecisionSnapshot` 与 decision manifest hash，经唯一
  `StrategyBase.step`/symbol reducer 写回；无新 Tick 时也可终结待释放候选并生成材料审计，
  重试不重复事件，不在派单器直接改 symbol state。snapshot builder 同时提供内存中
  最新 Tick、ring generation 和 accepted sequence 的原子只读 witness。
- 验证：领域边界与实际 Engine 回馈 `27 passed`（5.43s），相关 Ruff 通过。
  本批是运行时接线的组件边界；P4 仍 `IN_PROGRESS`，完整 supervisor/派单恢复和
  GraphQL/Web 退出门尚未宣告完成。

### P4 排名运行时与完整 PAPER 重放检查点（2026-09-07）

- Engine 将标准候选接入实际组合分配、公共排名 admission、最新 Gate、Sizer/Risk/Capacity、
  PAPER 账本及公共 ExitPlan 回报链。分配/准入/订单保持一个原子事务；原始 TTL 清理使用
  独立维护事务，即使后续缺少新鲜估值也保留已确认到期的事实。无 seed 也会清理过期意图。
- PREPARED admission 恢复原完整集合、claim/lease 和原 rank；纯 admission 凭证不改变
  经济水位，真实金额/义务变化仍阻断。残批记录稳定 BLOCKED 原因，原批 TTL 后才通过公共
  expired/supersede 协议继续。0056 同时验证 pending 原始 TTL/撤销、真实订单后续状态和
  无订单 Gate 终结审计，拒绝伪状态；没有为同事务的合法 ROUTED 而取消完整批次约束。
- `paper_seed` 是冻结 config payload 中唯一可选的显式初始化材料；一旦提供，必须完整包含
  `snapshot_id/as_of/cash/non_trading_asset_value/positions/bucket_checkpoint`，positions
  按 `Position` 的完整字段给出。hash 由该材料计算，未来时点、布尔数量、缺字段、重启改写
  seed 均拒绝；也可恢复已由同一 ledger 初始化的原账户。缺 seed、完整盘口或冻结策略证据
  时保留具体 readiness 原因，不自动复制 LIVE 资产或构造默认账户。
- 停用/替代先持久化 DRAINING，再取消未下单候选及实际 PAPER BUY；部分成交仍保留公共
  ExitPlan，未知 BUY 保持 RECONCILE_REQUIRED。无 producer 的退出仍消费实际行情并收敛。
  受理时钟在账户锁后读取；同源时间新批次不重复成交。可预期的缺价/lease 阻断有持久审计，
  不停止全局 CRITICAL 行情消费者；未知账本损坏继续失败关闭。
- 验证：30 个相关 Python 文件 `492 passed, 8 skipped`（69.74s），跳过为单独运行的 PG 门；
  相关 Ruff 通过。实际双标的完整 PG replay `1 passed`（70188，27.86s），经真正
  Strategy.step → 分配/排名 → 两次部分 BUY 成交 → 原移动止盈 → SELL → 两个批次与计划
  关闭，最终 4 orders/6 fills，含重启、重复投递、双时钟、现金费用守恒、T+1 和 LIVE/QMT SQL
  访问拦截。0056 pending TTL/撤销门 `1 passed`（61940）；同事务 Gate 终结及伪状态负测
  `3 passed`（62915，109.90s）；所有随机 schema 清理确认。日志分别为
  `.codex_screenshots/p4-final-targeted-python.log`、`p4-rule-only-replay-postgresql.log`、
  `p4-intent-terminal-controls-pg.log`、`p4-gate-terminal-postgresql.log`。
- GraphQL 7 个授权只读字段、稳定分页及 Web PAPER 页签已实现；API 定向 7 项、组件及工具栏
  定向 10 项通过。实际 `http://127.0.0.1:8080/graphql` 的 `npm run codegen`、`npm run check`、
  `npm run lint`、`npm run build` 均通过。codegen 禁止部分输出；PAPER 两条操作在同一实际
  Schema 下单独生成，随只读页签加载，公共 GraphQL 包与页面均保持既有包体预算。
  Web 完整 `test:run --maxWorkers=4` 为 `157 files / 866 tests passed`；默认并发曾有一次
  App appearance 异步加载超时，单项及上述完整低并发复验通过，未修改无关功能。
  之后仅调整查询生成模块归属，相关 10 项、typecheck/lint/build 再通过；复用其余有效证据。
  lint 保留既有 `TTradeDecisionAuditTable.tsx` fast-refresh warning，0 errors。
- 用户明确授权后执行统一 `down → migrate → up -Environment dev -Profile web → status`。
  迁移前备份为 `.runtime/backups/20260907T135507Z`；0050→0056 成功且 schema check 为 current。
  full/live 服务已重启，Engine owner audit 与独立 PAPER consumer 启动成功。QMT 启动观测为
  ready、协议 1.2、单设备/单账户、新鲜快照约 2.5 秒；行情供给显示 unavailable，未据此宣称
  实盘接单就绪或执行真实交易。本次未清理业务数据，未自动创建 PAPER seed。
  维护和 Web 证据见 `.codex_screenshots/p4-authorized-*.log`、`p4-graphql-codegen.log`、
  `p4-web-check.log`、`p4-web-lint.log`、`p4-web-test-run-bounded.log`、`p4-web-build.log`。
- 核心提交 `4bcb3edf`（双时钟）、`fde76dfc`（候选反馈）、`6eff542a`（运行时闭环）；
  只读投影与最终退出记录为本检查点所在提交。P4 六项与工程退出门完成，标为 DONE。
  这不是整个方案或策略收益的最终验收；没有开展 E2E、真实订单或 P5–P8 工作。
- P5 可复用：`StrategyBase.step`、组合快照/allocator、EntryExecutionGate、公共 admission、
  OrderSizer/Risk/Capacity、ExitPlan 与回报语义；参考实际双标的 replay 和 0056 负测。
  PAPER ledger 的 execution scope、双时钟和显式 seed 不可改名冒充 BACKTEST；下一任务仅按
  P5-A 明确 BACKTEST 端口与共享账户时钟，不自动扩展评估或模型范围。

### P5-A 共享账户最小整链检查点（2026-09-07）

- 前置：P4 完整退出记录已在 `f96f41749` 提交，复用 §10 既有验收。用户授权连续完成
  P5-A/B/C，批次只作验证/提交边界；本次始终单代理，不进入 P6。
- 接口：`BacktestRequest` / `execute_backtest` 每次新建 BACKTEST execution 与冻结版本；
  `TAssistantBacktestStore` 仅使用独立本地目录，保存 config/data/code/Broker/时间线/初始资产
  manifest、串行收敛帧 hash 链及结果。恢复核对冻结输入并重放已提交前缀，不重写既有事实。
- `TAssistantBacktestRuntime` 复用 StrategyBase.step/reducer、allocator、公共 admission 排序、
  Gate、OrderSizer/Risk、老仓 claim、日内估值和 ExitPlanBook；共用一个 BacktestBroker 和
  BucketLedger。纯候选投影和库存 claim 从 PAPER 数据库适配器移出后两端引用同一实现。
  Gate 新增明确绑定 frozen BACKTEST execution 的入口；原 PAPER 入口仍拒绝 BACKTEST。
- 验证：实际双标的规则信号→买入→原止盈退出，共4订单，现金/费用与库存守恒；逆序输入
  稳定重排后逐帧一致，独立 execution 经济 hash 一致，同 execution 恢复结果一致。
  相关回归185通过/1跳过（既有隔离PG迁移门）；费用适配后受影响167项通过。
  日志 `.codex_screenshots/p5-a-targeted.log`、`p5-a-final.log`；未操作业务库、服务或真实交易。
- P5-C：用户明确“先完成工程，暂不确认策略准入阈值”；先前建议不视为已确认标准。
  数据获取纳入功能范围，不再要求用户预先提供离线文件。P5-C实际准入与P6仍阻断。
- 提交：本检查点所在 `feat(t-assistant): add isolated shared-account backtest execution`。
  剩余：P5-B 边界与中断恢复验收，数据获取/评估准备；TTA-P5-02整阶段复用门待边界验证后勾选。
- 成本：本任务总token、缓存输入、非缓存输入、输出token与精确起始耗时均无可用任务级计量，
  记为未知；不以账户共享额度百分比换算token。

### P5-B 边界检查点（2026-09-07）

- P5-A 提交 `d10e99e68`。P5-B 实际公共链路新增13项定向测试全部通过：同时信号稳定排名、
  现金不足只选一票、部分成交累计最低费用只收一次、老仓置换后核心仓归因保持、恰好100股
  老仓不重复占用、保护核心仓禁止入场、14:50前撤BUY再撮合、截止后不入场、隔夜日历/收盘
  mark/库存结算、异环境与异execution/未知ExitPlan拒单、未来profile/行情及重复源身份拒绝。
- 在部分BUY已收敛和SELL已提交两个位置注入中断，恢复逐帧核对原hash链，最终均4订单/6成交；
  输入代码版本变化拒绝恢复。日志 `.codex_screenshots/p5-b-boundaries.log`。本批仅新增边界测试，
  复用P5-A公共规则回归；无需数据库/服务/E2E/真实交易。
- 提交：本检查点所在 `test(t-assistant): verify shared-account backtest boundaries`。
  剩余：P5-C数据获取与评估准备、旧单票假设对照；实际策略准入阈值用户暂不确认，P5/P6门不开放。

### P5-C 工程准备与最终交接（2026-09-07，P5 未 DONE）

- 已完成：历史数据获取接口 `acquire_backtest_dataset` 注入现有 HistoricalMarketDataService
  与 TradingDateHelper，逐标的/交易日遍历严格分页，冻结原始五档、源身份、日历、
  成交量口径、延迟、分区hash与源遍历结果。缺字段、重复/乱序、缺涨跌停、源中断保留
  INCOMPLETE 证据，禁止补造或回放；异常只保留安全错误码，不保存连接细节。
  存储遍历/字段检查不冒充已确认的统计样本完整性门；完整性阈值仍属正式评估条件。
- 接口：`FrozenBacktestDataset` 可直接传入 `execute_backtest` / `evaluate_backtest_comparison`，
  按日流式合并，单票对照使用同一冻结数据的明确子集；普通小样本仍可传 BacktestTick 序列。
  `BacktestRequest` 显式冻结配置、现金/桶库存、日历、前收盘mark、行业/profile及公共策略参数；
  每次生成独立BACKTEST execution。参数/数据/代码变化拒绝恢复，实际实现文件hash自动记录。
- 结果真源：每execution独立本地 `facts.sqlite3`，含 `t_assistant_executions`、
  `t_assistant_backtest_versions`、帧hash链、结果及失败表，SQLite事务提交已收敛帧；环境CHECK
  禁止改为LIVE。`result.json`是数据库结果manifest的审阅导出，不是第二套业务账本。
  恢复重放并核对已提交前缀，源损坏/运行失败保留证据。未修改或连接业务库，未新增业务迁移。
- 公共必要适配：BacktestBroker 显式五档不利滑点（默认0，PAPER行为保持），价格按最小价位
  向不利方向取整并遵守委托限价；部分成交后的资金预留只加未付费用，不重复预留最低佣金。
  现有交易时段分类移至纯公共函数，两端复用；非连续交易时段不撮合，不将午休报价当成交。
  过期估值记录 ALLOCATION_BLOCKED 并继续行情；未来或损坏证据仍拒绝。
- 评估准备：预先保存情景、数据、代码、分组和显式策略准入policy，执行RULE_ONLY组合及
  每票独享全部初始现金的旧假设对照，输出重复现金额、相对不做T的增量收益/回撤、费用、
  已闭环与期末未闭环数量。单票与日历季度分组使用共同初始组合权益分母，保存按市值计量
  的增量收益/回撤及闭环PnL/样本数。未确认policy返回NOT_EVALUATED；阈值失败保存FAIL并
  阻断P6，不调参、不改样本。合成夹具的阈值只用于验证失败分支，绝非用户策略准入授权。
- 验证：最终回测整链/边界/数据获取/评估准备26项通过（`p5-final-local.log`）；公共Broker
  与累计费用预留41项通过（`p5-fee-reservation.log`）；新增午休与既有边界14项通过。
  Ruff通过。复用P5-A的185通过/1既有PG门跳过和费用/Sizer的167项证据，不重复全量回归。
  所有日志在 `.codex_screenshots/`。中断恢复、数据库环境约束及篡改拒绝均在本地隔离存储验证。
- 提交：P5-A `d10e99e68`；P5-B `6d171da84`；P5-C工程准备为本检查点所在
  `feat(t-assistant): prepare frozen portfolio backtest evaluation` 提交。
- **未通过门**：用户明确“先完成工程，暂不确认策略准入阈值”。未把建议区间、标的、费用、
  滑点、最坏分组与门槛当作授权；未访问实际业务数据源，也未执行正式历史样本策略评估。
  TTA-P5-04工程守恒/重放/旧假设对照已验证，正式冻结样本对照与准入仍待P5-C，因此该任务
  保留未勾选，P5保持IN_PROGRESS，**不得进入P6**。本任务未开展P6–P8、Web界面、E2E或真实交易，
  未启停交易服务，工作树中其他任务改动保持原样。
- 成本：总token、缓存/非缓存输入、输出token均无任务级计量，仍记未知，不作估算。

### P5-C 正式预检增量（2026-09-08）

- 用户要求继续剩余内容，遇阻立即反馈、不反复重试。仍保持单代理；此前评估阈值未确认、
  不操作业务库的限制不因一般“继续”指令被视为自动解除。已集中请求确认正式口径和历史
  研究数据只读访问范围；截至本检查点尚未收到答复，未访问业务数据源或启动全年回测。
- 修复正式准入入口的两项缺口：收益比较操作GT/GTE必须逐情景显式冻结；增加连续交易
  时段分钟覆盖率预检，各标的均达显式分钟覆盖阈值才将该日计为合格日，再校验共同合格日
  数及完整日比例。覆盖指标版本为continuous-minute-coverage.v1，分钟阈值与日比例均无默认。
  观察分钟覆盖不冒充交易所逐Tick完整性证明；具体正式标准仍待用户确认。
- 不合格数据写入data-qualification.json与DATA_BLOCKED报告，在任何情景运行前停止；
  不通过删标的/删日期、调参或重试改变结果。合成用例验证零收益的GT/GTE差异、缺票和
  稀疏日阻断，确认不会调用回测情景入口。
- 验证：15项受影响预检与评估测试一次通过；Ruff通过。日志
  `.codex_screenshots/p5-formal-preflight.log`。复用其余有效工程证据，未进行全量回归。
- 提交：本检查点所在 `fix(t-assistant): gate formal backtests on explicit data qualification`。
  当前未通过门仍为正式口径/访问范围确认及其后的真实数据评估，P5保持IN_PROGRESS，P6阻断。

### P5-C 2026年8月缓存获取（2026-09-08）

- 用户确认本轮取数区间2026-08-01～2026-08-31；标的为当前持仓8只加分众传媒、招商银行、
  平安银行，共11只：000001.SZ、000543.SZ、002027.SZ、002594.SZ、302132.SZ、600036.SH、
  605499.SH、688213.SH、688552.SH、688577.SH、689009.SH。仅使用持仓标的，不复制真实账户
  数量、现金或成本。此前一年/200交易日建议不适用于本轮；策略准入数值仍未确认。
- 正式程序入口 `ops/t-assistant-backtest-data.py`：经HistoricalMarketDataService严格分页读取
  持久化Tick缓存和TradingDateHelper日历；本进程关闭Influx自动重试，首个源错误即停。
  支持首票/首日probe和单个缺口的标准行情队列补采，不直接导入或调用miniqmt/xtquant。
- 实际发现并修复：历史缓存的price_tick/up_stop_price/down_stop_price为缺失值，并含集合
  竞价零成交价。原入口把原始归档与可执行校验混用，首条即失败。新增显式原始归档模式，
  缺失数值保存为null，保留last_close及原始盘口，标记REFERENCE_REQUIRED；执行入口仍拒绝
  缺参考资料的归档，未推测或填造涨跌停价。回放使用公共连续交易时段分类。
- 单日验证：皖能电力2026-08-03从缓存完整读取4597条；4503条连续交易时段记录缺参考字段。
  整月盘点一次完成：21交易日×11标的=231分区，162分区有记录，69分区为空，共722131条。
  这里“有记录”不等于完整有效交易日。各标的数据条数/有记录日数：
  000001 0/0；000543 57894/13；002027 77816/16；002594 93237/19；302132 85107/19；
  600036 0/0；605499 95395/19；688213 93657/19；688552 71046/19；688577 55547/19；689009 92432/19。
- 冻结缓存目录 `.runtime/backtests/p5-202608/datasets/8a22c19e-09e3-412d-b4cb-579901907d56`，
  manifest hash `a27f848adbd1594706d3808ef28501f987a47bf97054904184bb3a0120e933e1`；
  status=INCOMPLETE，所有计划分区均已尝试，无未盘点分区。原始JSON与manifest不提交Git。
- 缺口试采：平安银行2026-08-31缓存为空，使用标准durable行情请求，关闭FAILED重开/换代。
  请求 `d3ffe20a-f46f-4a5e-8912-ec8b2da36a84` 首次等待60秒超时，续等同一幂等请求120秒
  仍未完成；最后观察为DELIVERED，Agent日志确认joined queued or active upload，未创建
  第二条请求。无上传/入库结果，不能据此断言历史期限限制。不再重发或扩大补采，保留句柄。
- 验证：获取/评估相关17项通过，标准行情队列及禁止失败重试相关16项通过；Ruff通过。
  证据 `.codex_screenshots/p5-august-cache-acquisition.log`、`p5-august-supplement-*.log`、
  `p5-cache-acquisition-tests.log`、`p5-cache-gateway-tests.log`。
- 提交：本检查点所在 `feat(t-assistant): acquire August tick archives through cache gateway`。
  阻碍：69个缓存缺口、历史执行参考字段缺失、补采请求未返回；正式回测/准入未运行，P5仍
  IN_PROGRESS、P6阻断。未启停服务、未执行真实交易，其他工作树改动保留。

### P5 数据引用与手动获取交接（2026-09-08）

- 用户要求先改造重复存储并完善程序取数，随后自行在现有 UI 获取数据，完成后再验收 P5。
  本轮未查询或补采真实行情、未触发下载任务、未启停服务；11标的/2026年8月范围不变。
- `BacktestDataset` / dataset v2 默认 REFERENCE：仅保存数据源、分区范围、条数、hash、
  参考字段缺失和完整性证据。按日从 HistoricalMarketDataService 异步分页读取，校验整日所有
  入选分区后才交给公共回测链路；源变化报 BACKTEST_SOURCE_CHANGED。相同清单复用目录。
- CLI `ops/t-assistant-backtest-data.py` 默认不导出行情正文；显式 `--freeze` 才保存共享
  `objects/<content-hash>.json`，重叠数据集与多次 execution 复用同一分区。旧 v1 归档保留作
  历史证据，不新增兼容读取；引用模式源数据变更后不能恢复旧内容，需重新盘点生成新版本。
- 现有 `/settings/data/market-data` → daily-market-data-sync → durable Agent 请求 →
  InfluxDB 路径继续使用。修复长区间 Tick 只拆标的仍超单次记录预算的问题：Tick 按交易日、
  单标的拆分（同时选择的1d/1m同范围），维持两请求并发及稳定幂等批次。核对每标的/周期
  入库摘要，空 Tick 不能被非空日线掩盖；错误保留日期、标的和请求ID，失败请求不自动重开。
- UI 使用“手工代码”填写11标的、2026-08-01～2026-08-31、选择 Tick；仅取行情时关闭
  日级指标计算（8月31个自然日超过该功能的30天补算限制）。源历史可用范围仍由供应端决定，
  获取成功只代表返回数据已入库，不证明全天覆盖或历史涨跌停/最小价位等执行参考字段齐全。
- 验证：数据获取/评估原有17项通过；引用重放、快照复用、公共回测边界与 Worker 扩展回归
  84项通过；审核修正与新增边界10项通过，UI表单/标的范围7项通过，Ruff/定向ESLint通过。证据见
  `.codex_screenshots/p5-data-reference-tests.log`、`p5-reference-regression.log`、
  `p5-data-review-fixes.log`、`p5-data-ui-tests.log`。
- 提交：本检查点所在 `refactor(backtest): reference persisted data and bound tick acquisition`。
  P5保持IN_PROGRESS；待用户手动获取后重新盘点缺口、补齐历史执行参考数据并执行正式评估。
  策略准入阈值仍未确认，P6继续阻断，不把本次工程改造算作策略验收通过。

## 11. 变更记录

### P5 开发环境验收预检（2026-09-09）

- 当前仍为 P5 `IN_PROGRESS`：P0–P4 已完成；工程证据不替代正式样本及策略准入门。
- 修复取数 CLI 强制 `ENV=testing` 的环境隔离缺口：默认显式加载 development 配置，
  macOS 仅允许 development，复用运行入口的本地端点与 `_dev` 数据库校验；导入客户端前
  完成校验并关闭全部实盘开关和账户白名单。Windows 如需测试数据须显式指定
  `--environment testing`，不提供 production 入口。
- 在本机独立开发数据服务只读盘点既定 2026-08-01～2026-08-31 的 11 标的：
  21 交易日、231 分区全部 `EMPTY_SOURCE`，Tick 总数 0，未盘点分区 0。
  本机结果与此前 Windows 缓存证据分别记录，不据此覆盖历史 722131 条盘点结果。
- 引用清单：`.runtime/backtests/p5-202608-dev/25742e71e82fdca7411683faad8cc9cbdd691341e6fd88ff4db993d0bbb5e1b9/`；
  hash 为目录名，状态 `INCOMPLETE`；日志 `.codex_screenshots/p5-dev-data-preflight.log`，
  CLI exit=2 为数据不完整退出。没有访问生产数据服务、补采、启动服务或执行正式回测。
- 环境隔离、缓存获取、引用读取与数据资格预检 16 项通过，Ruff 通过；日志
  `.codex_screenshots/p5-dev-environment-tests.log`。既有公共回测工程证据继续有效。
- 阻碍已立即告知用户：开发库缺少正式样本；模拟初始账户、费用/滑点、指标及分组阈值、
  样本和覆盖率要求仍待明确。待开发数据就绪并确认口径后冻结版本、运行评估与旧单票对照；
  不勾选 TTA-P5-04，不开放 P6。

### P5 开发端通过生产行情接口取数（2026-09-09）

- 用户授权测试跨环境行情链路。两个单日分区（皖能电力 8 月 3 日、平安银行 8 月 31 日）
  已由生产导出为 READY 并在开发端 LOCAL_VERIFIED，合计 9468 条 Tick；此前本地全部为空
  的状态已改变，尚未重新盘点全部 231 分区。平安银行旧源请求现已有验证结果。
- 生产原始分片仍缺有效历史涨跌停价，P5 回读保持 REFERENCE_REQUIRED；没有发现接口或
  Agent 报错，全新 QMT 补采分支未验证。详见[开发行情链路验收](多标的做T助手P5开发行情链路验收.md)。
- 未修改生产服务、访问生产数据库或绕过补采时段；P5 正式数据门及策略准入仍未通过。

### P5 日 K 涨跌停口径切换验收（2026-09-09）

- 已授权合并远程 `efecd8903`（合并提交 `e9500d9f`），历史 Tick 不再承载涨跌停字段，
  P5 只关联对应交易日日 K；历史未采集保留为空，不要求生产补造或修补历史 Tick。
- 合并后 224 项相关测试及定向 Ruff 通过；新版本生产导出到开发 LOCAL_VERIFIED 实测通过：
  皖能电力 8 月 3 日，1 条日 K、4597 条 Tick，Tick 原始分片无涨跌停字段。
- 旧日 K 参考值为空，P5 仍 REFERENCE_REQUIRED；正式评估需有参考值的样本及确认口径。
  招商银行旧补采探针 09:47 仍 WAITING_SOURCE、无报错，未绕过 16:00 派发门。
  详情与证据见[开发行情链路验收](多标的做T助手P5开发行情链路验收.md)末节，P5 不标 DONE。

### P5 正式样本区间调整与可用日期盘点（2026-09-09）

- 用户明确同意：正式评估改为开始采集日 K 涨跌停价后、参考资料齐全的交易日，仍为原
  11 标的。原 2026 年 8 月区间只保留工程取数证据，不再作为待补齐的正式准入样本。
  尚未冻结具体日期、最小样本量、模拟账户、费用/滑点及准入阈值，不据此开放 P6。
- 本地只读盘点 2026-08-01～2026-09-09：仅皖能电力有 1 条日 K，两个参考值均为空，
  其他 10 标的没有日 K；11 标的共同具备两个有效参考值的日期数为 0。
  此结论限于本地开发库，不能当作生产数据库的全量盘点。证据
  `.codex_screenshots/p5-daily-reference-inventory.log`。
- 新方案于 9 月 9 日接入；当天尚未收盘，最早候选日期为 9 月 9 日，但必须待实际收盘
  采集并导入后验证，不能预先宣称该日已可用。需要每个标的同日日 K 两参考值及 Tick 覆盖
  同时合格；仅有合约详情、日 K 或 Tick 任一种均不足以冻结正式样本。
- 原 8 月缓存重新盘点：231 分区全部尝试，3 分区有数据、228 分区为空，共 14650 条 Tick，
  manifest hash `4857db4fd2e1eaedf7564860fd29c4d931cc075048ffdb1e79c1fd125ba9ea2a`，
  INCOMPLETE；不继续为已放弃的正式区间批量补采。日志
  `.codex_screenshots/p5-current-data-inventory.log`。
- 复用合并后 224 项有效证据，补验共享账户与边界 15 项全部通过（0.99s），日志
  `.codex_screenshots/p5-merged-runtime-boundaries.log`。未重复全量测试、改策略或改准入门。
- 招商银行旧 WAITING_SOURCE 问题已解除：10:40:14 原/新版均 READY，新版开发端
  LOCAL_VERIFIED、5182 条回读 verified，源请求 `7c3d8f68-a041-4b18-b174-9c77cadecd08`。
  这证明该请求取数闭环，不单凭分片状态推断新 QMT 原生补采过程；详细日志
  `.codex_screenshots/p5-gap-readonly-recheck.log`。旧本地 QUEUED 不作为新版未入库的证据。
- 当前 P5 阻碍：正式可用日期尚无已验证样本，准入数值仍未确认。保留 IN_PROGRESS。

### P5 取消缺失涨跌停价前置条件（2026-09-09，用户明确授权）

- 用户要求“去掉这个涨跌条件，继续验收”，覆盖此前因日 K 涨跌停资料缺失而等待新日期的
  前置要求。BACKTEST 采用 `CHECK_WHEN_AVAILABLE.v1`：日 K 有值仍检查，缺值允许回测，
  不回退历史 Tick、不推算价格。仅取消缺失即阻断，不删除公共涨跌停规则或修改实时风控。
- 数据准备仍要求有效最小价位、来源身份、时序、盘口等；日 K 缺值继续记录在分区
  missing_reference_fields，但不再触发 REFERENCE_REQUIRED。最小价位缺失仍阻断。
  数据清单与结果明确记录该检查策略，不能把缺值样本解释为已验证涨跌停边界。
- 40 项定向测试通过，含无日 K 涨跌停价的真实公共买卖闭环及两次 economic_hash 一致，
  覆盖原始引用与严格输入两种数据准备方式；定向 Ruff 通过。
- 实际皖能电力 2026-08-03 的 4597 条 Tick 数据准备通过，status=FROZEN，
  4503 条连续时段缺涨跌停价仍如实记录。manifest hash
  `d0a75451e845ea16b7088655a41a1d14ba274945d3a6c44c633785fa51278c7d`。
- 证据：`.codex_screenshots/p5-optional-limits-tests.log`、
  `p5-optional-limits-real-data.log`。本次不是正式历史样本策略准入评估。
- 不再要求为涨跌停字段等待新采集日期；正式样本具体区间仍须冻结。原 11 标的全样本数据
  覆盖尚不足（上一盘点 231 分区只有 3 个非空），模拟账户、费用/滑点、样本与收益/回撤
  阈值尚未确认。P5 保留 IN_PROGRESS，不将字段条件取消等同阶段通过。

2026-09-07 补丁复盘整改：行情缓存及消费水位仅在来源校验和 lineage 装饰完成后发布，
覆盖 delta/恢复快照校验期间的新订阅；删除直接写入原始 Tick 的旧 helper。
PAPER shadow 协调与周期处理互斥，遗留 PREPARED 清理仅在首次绑定执行，日常协调不抢占
刚准备的周期。行情中心、PAPER shadow、Engine supervision 三文件共 57 项测试通过，
相关 Ruff 与差异格式检查通过；本次仅修改代码并提交，未重启实盘服务、未执行交易。

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-09-03 | 初版；建立 9 阶段依赖、前后置任务、状态词汇、验证矩阵、风险和证据台账 |
| 1.1 | 2026-09-03 | P0 冻结、分层清单、只读审计和基线证据完成；进度更新为 1/9，P1 因存量 owner/ExitPlan/审批义务及 protocol 1.2 切换演练未完成而阻塞。 |
| 1.2 | 2026-09-03 | 完成 P1-01：contracts/domain 使用单一强类型 owner 契约并保留普通策略与人工命令适配；未提前切换数据库、Agent protocol 或实盘链路，P1 阶段仍因存量义务与切换演练阻塞。 |
| 1.3 | 2026-09-03 | 完成 P1-03 最小 OwnerRuntimeRouter/Registry 及 fail-closed 反向测试；组件尚未接入当前 protocol 1.1 回报链，P1 阶段仍保持阻塞。 |
| 1.4 | 2026-09-03 | 完成默认只读、PAPER-only、精确数量门禁的 legacy reconciliation 工具及真实库 dry-run；尚未执行数据修复，P1 仍由存量义务与 protocol 1.2 切换演练阻塞。 |
| 1.5 | 2026-09-03 | 受控收敛 2 条 PAPER stale approval 与 1 条 PAPER orphan ExitPlan；复验 P0 blocker 为零、P1 readiness=true，P1 仅余 protocol 1.2 切换演练门。 |
| 1.6 | 2026-09-03 | 完成 protocol 1.2 只读切换 preflight 与瞬时 inbox fail-closed 复验；真实库最终 ready=true，但未把模拟步骤记为实际停服、备份或部署，P1 仍等待实际维护窗口演练。 |
| 1.7 | 2026-09-03 | 完成权威备份的两次全量隔离恢复验证；0045 严格接管 pre-Alembic 三表并原子修正 naive-UTC 时间列，schema gate 改为结构与 revision 双门。恢复至 head、QMT journal 和 Monitor 均通过；实际停服/部署/恢复后全量快照对账仍未执行。 |
| 1.8 | 2026-09-03 | 完成实际维护窗口的停服态备份、迁移、标准 full/live 恢复、全量快照对账与隔离恢复；修复启动器将完整账户号与健康接口脱敏值比较而永不命中 READY 的契约错误。维护演练门解除，P1 转为 `IN_PROGRESS`；protocol 1.2 仍待 P1-02/P1-04..06 原子实现和真实切换。 |
| 1.9 | 2026-09-03 | P1 closeout：P1-02/P1-04..06 完成，owner identity 贯穿公共持久化与回报链，唯一 Agent protocol 1.2 已在标准 full/live 基线生效；0046/0047、快照收敛、无未知结果、无双协议与冷启动证据已登记。P2/P3 前置解除，下一步转入 P2/P3。 |
| 2.0 | 2026-09-03 | P2-01..06 实现与聚焦验证完成：公共 ExitPlanRuntime、账户容量水位、跨域 READY admission、不可漂移 T order v1、0048 迁移前影子检查及故障恢复测试已落地；阶段等待最终审计与提交，不提前开放新 T owner 真实订单。 |
| 2.1 | 2026-09-03 | 记录 P3 首轮实现与 319 项聚焦回归；最终二审仍发现 callback 恢复、WARMING 候选、同 fence 比较、startup recovery、逐标的 sequence、successor 排空和完整零副作用矩阵等 blocker，因此阶段保持 `IN_PROGRESS`，不提前宣称 closeout。 |
| 2.2 | 2026-09-03 | 阶段归属纠偏：P2-05 只覆盖当期已存在的公共 intent/outbox、ExitPlan 与 admission 约束；cycle uniques 明确归 P3-05，尚未创建的 allocation batch/decision uniques 明确归 P4-03。这不是省略约束，而是避免 P2 为勾选任务提前创建 P4 表。同时消除 T 退出模板读取任意滑点参数和用 config version 冒充 order-policy version 的遗留，模板、公共路由、精确授权与最终命令门统一绑定 `TExitOrderPolicy.v1`。P2 仍保持 `IN_PROGRESS`。 |

| 2.3 | 2026-09-07 | 明确 P0–P8 编号、单批执行与成本边界；拆分 P5 工程/策略准入，模型能力后置 P8；解除 P6/P7 演练及清理依赖交叉，标明待确认门槛；按影响范围选择验证。既有阶段状态与实盘授权不变。 |
