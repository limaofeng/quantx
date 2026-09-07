# QuantX 多标的做 T 助手新架构开发实施方案

> 状态：`IN_PROGRESS`（P0、P1 已完成；P2/P3 实现与聚焦验证后正在最终审计）<br>
> 版本：2.2<br>
> 日期：2026-09-03<br>
> 目标设计：[多标的做 T 助手新架构设计 v2.2](../architecture/多标的做T助手新架构设计.md)<br>
> 当前基线：[系统架构设计（As-Is）](../architecture/系统架构设计.md)<br>
> 开发实施进度：2 / 9 个阶段门完成（22.2%）

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

只有在 P7 完成且 legacy 义务归零后，才允许：

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
| P2 | 公共 ExitPlan/容量/准入安全地基 | `IN_PROGRESS` | `P1 DONE`；P2-01..06 实现与聚焦验证完成，等待最终审计/提交 | 无第二真源，故障恢复通过 | `ExitPlanRuntime`、0048 admission schema、order policy 与聚焦回归 |
| P3 | 独立 T runtime 与精确行情归约 | `IN_PROGRESS` | `P1 DONE`；实现与首轮聚焦验证完成，最终审计整改中 | 无 StrategyRun、逐 Tick 因果归约、隔离 PAPER shadow 且无订单链写入 | 0049 与 P3 首轮聚焦回归 `319 passed`；最终二审 blocker 尚未清零 |
| P4 | 分配、PAPER 与跨域准入 | `NOT_STARTED` | P2 + P3 | 整批原子、PAPER 闭环、无真实订单 | 待补 |
| P5 | 共享账户回测 | `NOT_STARTED` | P4 | 无重复资金/未来数据，结果可重放 | 待补 |
| P6 | LIVE 人工确认灰度 | `NOT_STARTED` | P5 | 唯一 producer、规定闭环、无安全违规 | 待补 |
| P7 | AUTO 与稳定性 | `NOT_STARTED` | P6 | 故障注入、恢复、收盘与并发门通过 | 待补 |
| P8 | 模型 SHADOW/ACTIVE | `NOT_STARTED` | P5；ACTIVE 依赖 P7 | OOS 增量、门禁、人工发布闭环 | 待补 |

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
- [ ] `TTA-P2-04` 建立版本化 `TEntryOrderPolicy/TExitOrderPolicy` 与结果未知禁止 replace 门；
  2026-09-06 复核：FIX_PRICE/30 秒命令有效期、工作单超时撤单、同一活动意图有限替换
  与总窗口恢复已实现并通过聚焦回归，仍待独立链路复审及默认运行基线验收；判定 helper
  或禁止全部替换不能单独证明该任务完成。
- [x] `TTA-P2-05` 只为本阶段已经存在的公共 intent/outbox、ExitPlan、admission batch/item
  加入 owner、身份一致性与唯一约束，迁移前先做影子冲突检查；cycle 表约束归 P3-05，
  尚未创建的 allocation batch/decision 约束归 P4-03，不提前创建 P4 表。
- [ ] `TTA-P2-06` 覆盖部分成交、ORDER/TRADE 乱序、迟到成交、撤单失败、未知结果、退出独立恢复
  与跨域抢锁故障测试；订单生命周期及外部导入发布失败的代级持久化封禁已补聚焦验证，
  仍待扩大回归与最终验收。

退出门：现金/库存真源仍唯一；跨域 BUY 顺序确定；退出、容量和命令恢复不依赖做 T runtime。

### P3：独立做 T 运行时与逐 Tick 状态内核

前置：P1 `DONE`；P2 可并行但 P4 前必须完成。

- [ ] `TTA-P3-01` 实现 `TAssistantConfigVersion/TAssistantExecution`、生命周期、entry readiness、
  rollout stage、events 和 partial unique LIVE producer 约束。
- [ ] `TTA-P3-02` 实现独立 `TAssistantSymbolState` 与逐 Tick `SymbolMarketStateReducer`；每个 accepted
  Tick 按 source identity 恰好归约一次。
- [ ] `TTA-P3-03` 实现 delta ring/cursor/generation/gap/lag 处理；覆盖时 fail-closed 并 rewarm。
- [ ] `TTA-P3-04` 实现强类型 `StrategyCadence.SNAPSHOT`、输入 validator、symbol patches 和
  `execution_ref`，移除做 T 对 StrategyRunState callback 的依赖。
- [ ] `TTA-P3-05` 实现 `decision_key`、cycle identity/attempt 唯一约束、processing fence/lease、
  material 原子提交、续接与 `ABORTED_STALE`。
- [ ] `TTA-P3-06` 在隔离 PAPER namespace 与旧 V3 逐 cycle shadow 对比，禁止 approval/outbox。

退出门：尚未通过最终复审。二审 blocker 的实现整改已覆盖 callback 故障恢复、非 READY
候选延迟、同 source/fence 规则比较、PREPARED startup recovery、逐标的 sequence、config
successor 排空、D-1 profile 和扩大零副作用矩阵；真实 PostgreSQL baseline→0050
隔离升级/触发器负向验证已通过。2026-09-06 复审发现并修复 material 后置校验的半写入问题，
同 execution 周期同步回退热游标的问题已修复并补齐重启与 generation/universe/config 边界测试；仍须通过最终全量回归、只读复审及默认
运行基线验收，之后才能将本阶段改为 `DONE`。

### P4：组合分配、跨域准入与 RULE_ONLY PAPER

前置：P2、P3 均 `DONE`。

- [ ] `TTA-P4-01` 实现 point-in-time `TTradingEnvelope/PortfolioTDecisionSnapshot`。
- [ ] `TTA-P4-02` 标准 TradeIntent 以 `ALLOCATION_PENDING` 受理；不新增 trade proposal 协议。
- [ ] `TTA-P4-03` 实现 `TAllocationBatch/TAllocationDecision`、allocation batch/decision 唯一约束、
  稳定排序、CAP、claim/lease、TTL、next eligible、整批提交和 supersede；这些表与约束是 P4
  退出门，不由 P2 提前占位。
- [ ] `TTA-P4-04` 实现 `EntryExecutionGate` 与最新 Tick、binding、spread、TTL 重验。
- [ ] `TTA-P4-05` 将 ALLOW/CAP 候选按排名接入公共 admission、OrderSizer、Risk 和 Capacity；PAPER
  使用隔离 Broker/事实表，不消耗 LIVE 义务。
- [ ] `TTA-P4-06` 提供机会、分配、readiness、reason、order/ExitPlan 的 GraphQL/Web 只读投影。

退出门：RULE_ONLY PAPER 多标的闭环可重放；半批、重复 claim、抢锁赢家和环境串写测试全部失败
关闭；没有真实 QMT 订单。

### P5：共享账户组合回测

前置：P4 `DONE`。

- [ ] `TTA-P5-01` 每次回测使用 BACKTEST execution、单一时钟、共享现金/库存/费用和 Broker。
- [ ] `TTA-P5-02` LIVE/BACKTEST 复用同一 step、reducer、Coordinator、Gate、OrderSizer、Risk、
  Capacity、ExitPlan 和回报收敛语义。
- [ ] `TTA-P5-03` 加入多标的同时信号、部分成交、T+1 substitution、截止时间与 overnight carry。
- [ ] `TTA-P5-04` 证明无未来数据、无重复资金、无超老仓、结果 hash 可重放，并对比旧单票假设。

退出门：组合回测结果可审计且守恒；RULE_ONLY 在费用、滑点和最坏分组下达到 P0 冻结的准入门。

### P6：LIVE / CANARY / MANUAL_CONFIRM

前置：P5 `DONE`；实盘能力门、唯一 QMT Agent、protocol 1.2 和完整账户快照均 READY。

- [ ] `TTA-P6-01` 在维护窗口停止旧做 T 新 ENTRY，将 legacy owner 置 DRAINING 并冻结义务清单。
- [ ] `TTA-P6-02` 结果未知旧命令仍由原 owner reconcile；旧 ExitPlan 保持 plan owner/source ref。
- [ ] `TTA-P6-03` 创建唯一 `LIVE + CANARY + MANUAL_CONFIRM + RULE_ONLY` execution，低额度、有限标的。
- [ ] `TTA-P6-04` 确认后重新 allocation、跨域 admission、Gate 和容量复核，不复用旧额度。
- [ ] `TTA-P6-05` 验证 14:50 cutoff、撤单/replace、部分成交、overnight carry 和次日原 plan 恢复。
- [ ] `TTA-P6-06` 完成 P0 冻结数量的闭环与观察期；每轮无重复单、超现金、超老仓、T+1 或 owner
  违规，且所有不执行都有 reason code。

退出门：人工灰度证据满足门槛；旧/新 owner 没有同时产生新 ENTRY；结果未知和隔夜事实均安全。

### P7：故障注入、稳定性与 LIVE/AUTO

前置：P6 `DONE`。

- [ ] `TTA-P7-01` 故障注入 Engine/API/QMT 断连、进程重启、lease 过期、DB 冲突、Redis 丢唤醒、
  delta gap、乱序/迟到回报和 successor 失败。
- [ ] `TTA-P7-02` 压测逐 Tick reducer、组合触发合并和 model-off 路径；阈值超过时 fail-closed。
- [ ] `TTA-P7-03` 验证跨域 READY BUY 同时到达、账户水位变化和 batch rollback 的确定顺序。
- [ ] `TTA-P7-04` 以新 config/successor 显式切换 AUTO；不原地修改 MANUAL_CONFIRM execution。
- [ ] `TTA-P7-05` 完成 P0 冻结的 AUTO 观察期、回撤/熔断/运营验收，并保留一键阻断新 ENTRY。
- [ ] `TTA-P7-06` legacy 做 T 自身 BUY 义务归零后删除专用调度和 fallback；更新 As-Is 文档。

退出门：所有恢复路径无重复订单、无释放后反证穿透、无双 producer；AUTO 可被显式阻断且退出继续。

### P8：模型 SHADOW 与 ACTIVE

前置：SHADOW 依赖 P5 和稳定 PAPER 数据；ACTIVE 依赖 P7。

- [ ] `TTA-P8-01` 冻结完整 1 分钟 Feature Bar、capability manifest、三分类 first-touch label、
  observation anchors、purged walk-forward 和 worst-group 指标。
- [ ] `TTA-P8-02` 复用不可变 dataset/spec/run/artifact/backend 原语，建立做 T 独立 registry/binding。
- [ ] `TTA-P8-03` 同坐标比较 RULE_ONLY、Logistic 与 LightGBM；DEVELOPMENT 锁定后只进行一次 FINAL。
- [ ] `TTA-P8-04` Engine CPU 安全加载并运行 SHADOW；分数不改变 RULE_ONLY 排序，缺失不阻断。
- [ ] `TTA-P8-05` 只有 OOS 组合增量、校准、OOD、制品和人工发布门均通过才创建 ACTIVE successor。
- [ ] `TTA-P8-06` ACTIVE 故障阻断新 ENTRY 且不静默降级；ExitPlan、回报和账户安全链不受影响。

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

按改动范围执行，至少包括：

```powershell
python -m pytest tests/
python -m pytest tests/api/unit/

$env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```

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

当前结论：**P0、P1 已完成；P2/P3 完整备份的隔离迁移验证已通过，本批提交迁移相关文件，不代表 P2/P3 整体完成。** P1 原子切换后，Agent 控制协议为 `1.2`；
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
  **不修改开发/实盘库，不启停交易服务，不进入 P4。** 实际数据库迁移及 Windows 运行验收为下一阶段；
  P2/P3 阶段仍保持 `IN_PROGRESS`，不得提前开放新 T owner 真实订单。

## 11. 变更记录

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
