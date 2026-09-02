# QuantX 多标的做 T 助手新架构开发实施方案

> 状态：`PLANNED`（设计与追踪基线已建立，开发尚未开始）<br>
> 版本：1.0<br>
> 日期：2026-09-03<br>
> 目标设计：[多标的做 T 助手新架构设计 v2.2](../architecture/多标的做T助手新架构设计.md)<br>
> 当前基线：[系统架构设计（As-Is）](../architecture/系统架构设计.md)<br>
> 开发实施进度：0 / 9 个阶段门完成（0%）

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
| P0 | 基线冻结与契约清点 | `NOT_STARTED` | 文档基线 | 清单、policy、基线测试齐全 | 待补 |
| P1 | Owner 与协议 1.2 | `NOT_STARTED` | P0 | 单 owner/单 payload，既有路径等价 | 待补 |
| P2 | 公共 ExitPlan/容量/准入安全地基 | `NOT_STARTED` | P1 | 无第二真源，故障恢复通过 | 待补 |
| P3 | 独立 T runtime 与精确行情归约 | `NOT_STARTED` | P1 | 无 StrategyRun、新旧规则 shadow 等价 | 待补 |
| P4 | 分配、PAPER 与跨域准入 | `NOT_STARTED` | P2 + P3 | 整批原子、PAPER 闭环、无真实订单 | 待补 |
| P5 | 共享账户回测 | `NOT_STARTED` | P4 | 无重复资金/未来数据，结果可重放 | 待补 |
| P6 | LIVE 人工确认灰度 | `NOT_STARTED` | P5 | 唯一 producer、规定闭环、无安全违规 | 待补 |
| P7 | AUTO 与稳定性 | `NOT_STARTED` | P6 | 故障注入、恢复、收盘与并发门通过 | 待补 |
| P8 | 模型 SHADOW/ACTIVE | `NOT_STARTED` | P5；ACTIVE 依赖 P7 | OOS 增量、门禁、人工发布闭环 | 待补 |

## 5. 分阶段任务清单

### P0：冻结基线与完成契约清点

前置：目标设计 v2.2 与本方案已评审。

- [ ] `TTA-P0-01` 冻结旧做 T StrategyRun 的新增功能，只允许安全修复和义务排空。
- [ ] `TTA-P0-02` 按 DB、contracts、domain、application、infrastructure、Engine、API、Worker、
  QMT Agent、GraphQL/Web 分组清点所有 run identity 假设和迁移目标。
- [ ] `TTA-P0-03` 建立 legacy owner/候选/审批/pending/outbox/order/fill/batch/ExitPlan/未知结果
  只读一致性报告，不修改不确定记录。
- [ ] `TTA-P0-04` 冻结 protocol 1.2 owner payload、数据库约束、reason code 和迁移切换步骤。
- [ ] `TTA-P0-05` 冻结逐 Tick lag/ring、snapshot freshness、cycle/allocation lease 与 TTL 的硬阈值。
- [ ] `TTA-P0-06` 冻结跨域风险增加优先级、ENTRY/EXIT order policy、14:50 ENTRY cutoff、
  最短退出窗口、收盘缓冲、隔夜上限和人工灰度退出门。
- [ ] `TTA-P0-07` 保存 V3、普通策略、ExitPlan、T+1、乱序回报和 QMT 断连基线测试证据。

退出门：上述清单均有 owner、精确代码落点、测试和决策记录；没有 `TBD` 会改变后续安全语义。

### P1：公共 `ExecutionOwnerRef` 与协议 1.2 原子升级

前置：P0 `DONE`，维护窗口和结果未知命令处置流程可执行。

- [ ] `TTA-P1-01` 在 domain/contracts 建立强类型 `ExecutionOwnerRef` 与明确 owner enum。
- [ ] `TTA-P1-02` 原子演进 intent、approval、pending、correlation、outbox、ExitPlan source 和回报
  路由表；回填普通策略/人工命令，删除默认 StrategyRun fallback。
- [ ] `TTA-P1-03` 建立最小 `OwnerRuntimeRouter`，未知、冲突或失联 owner 一律 fail-closed。
- [ ] `TTA-P1-04` 将 Agent 命令/报告契约切换到 protocol 1.2；先排空未投递 1.1 outbox，未知结果
  只 reconcile，禁止换 payload 重发。
- [ ] `TTA-P1-05` 同步 API、Engine、QMT Agent、GraphQL、Web、codegen 和客户端文档。
- [ ] `TTA-P1-06` 验证普通 StrategyRun、MANUAL_COMMAND 与 EXIT_PLAN 行为等价、幂等和乱序恢复。

退出门：运行时只产生一个 owner 协议和一个 Agent payload；不存在 metadata-only owner、双写或
默认 StrategyRun；尚未允许新 T owner 下真实订单。

### P2：公共退出、容量与风险增加准入地基

前置：P1 `DONE`。

- [ ] `TTA-P2-01` 将 PAPER/LIVE ExitPlan 统一交给独立 `ExitPlanRuntime`，source execution 终态
  不影响原计划恢复。
- [ ] `TTA-P2-02` 固化 `AccountCapacityService` 老仓认领公式、obligation watermark、账户锁序、
  bucket priority 和 protected floor；不创建第二套 claim 余额表。
- [ ] `TTA-P2-03` 建立 `AccountRiskIncreaseAdmissionSequencer` 与 durable admission batch，覆盖
  做 T、打板、买入计划、普通策略和人工 BUY 的稳定次序。
- [ ] `TTA-P2-04` 建立版本化 `TEntryOrderPolicy/TExitOrderPolicy` 与结果未知禁止 replace 门。
- [ ] `TTA-P2-05` 加入 owner、LIVE producer、cycle/allocation、intent/outbox 和 ExitPlan 唯一约束，
  迁移前先做影子冲突检查。
- [ ] `TTA-P2-06` 覆盖部分成交、ORDER/TRADE 乱序、迟到成交、撤单失败、未知结果、退出独立恢复
  与跨域抢锁故障测试。

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
- [ ] `TTA-P3-05` 实现 `decision_key`、唯一 cycle attempt、processing fence/lease、material 原子
  提交、续接与 `ABORTED_STALE`。
- [ ] `TTA-P3-06` 在隔离 PAPER namespace 与旧 V3 逐 cycle shadow 对比，禁止 approval/outbox。

退出门：新 runtime 不创建 StrategyRun；决策触发合并不丢 Tick/FSM/candidate 转换；重启不会重复
提案；规则差异都有明确评审结论。

### P4：组合分配、跨域准入与 RULE_ONLY PAPER

前置：P2、P3 均 `DONE`。

- [ ] `TTA-P4-01` 实现 point-in-time `TTradingEnvelope/PortfolioTDecisionSnapshot`。
- [ ] `TTA-P4-02` 标准 TradeIntent 以 `ALLOCATION_PENDING` 受理；不新增 trade proposal 协议。
- [ ] `TTA-P4-03` 实现 `TAllocationBatch/TAllocationDecision`、稳定排序、CAP、claim/lease、TTL、
  next eligible、整批提交和 supersede。
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

- P1 未完成：禁止新 T owner 进入公共下单链。
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
描述未来状态，但必须保持“目标/计划”标签。protocol 1.2 切换后需同时更新：

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
| `strategy_run_id` 跨层耦合范围大 | 漏改导致伪 owner 或回报失联 | P0 分层清点，P1 原子升级和 fallback 反向测试 | `OPEN` |
| protocol 1.1/1.2 切换存在未知命令 | 重发可能重复下单 | 停新命令、排空未投递、未知只 reconcile | `OPEN` |
| 决策触发合并误丢 Tick | FSM/candidate 与回测不一致 | reducer 与 trigger 分层、cursor/gap/ring 强测试 | `OPEN` |
| cycle 与 allocation 恢复身份混淆 | 重复提案或半批 | decision key 与 attempt 分离、durable claim/fence | `OPEN` |
| 做 T 内部排名不覆盖跨域 BUY | 账户锁赢家由调度偶然性决定 | 公共 risk-increase admission sequencer | `OPEN` |
| 只看总可卖量破坏底仓 | core/locked_core 被错误置换 | bucket priority、protected floor、事务内审计 | `OPEN` |
| 收盘未成交被错误假设为闭环 | 隔夜暴露、重复批次 | cutoff、overnight carry、次日原 plan 恢复 | `OPEN` |
| 模型复杂度提前阻塞安全核心 | 延迟 RULE_ONLY 交付 | P8 后置；SHADOW/ACTIVE 独立门禁 | `OPEN` |

新风险必须追加，不能覆盖历史行。关闭风险时记录对应 task、测试和 commit。

## 9. 实施证据台账

| 日期 | Task/阶段 | 状态 | 证据 | 说明 |
|---|---|---|---|---|
| 2026-09-03 | `DOC-001` | `DONE` | [目标设计 v2.2](../architecture/多标的做T助手新架构设计.md) | 补齐不变量、恢复、准入、收盘和数据库约束 |
| 2026-09-03 | `DOC-002` | `DONE` | 本方案 v1.0 | 建立依赖、阶段门、状态和证据台账 |
| 2026-09-03 | `DOC-003` | `DONE` | [文档中心](../README.md)、[交易文档索引](../trading/README.md)、[系统架构](../architecture/系统架构设计.md) | 建立双向索引与 As-Is/To-Be 边界 |

后续每条 `DONE` 证据应包含 commit、验证命令及结果摘要；若输出过长，链接到仓库内稳定测试报告，
不粘贴包含账户、设备或券商敏感信息的日志。

## 10. 当前状态与下一动作

当前结论：**设计与开发追踪基线已完成；实现尚未开始，当前运行仍是系统架构文档描述的
StrategyRun + protocol 1.1 As-Is。**

下一动作固定为 P0，不直接开始建表或修改协议：

1. 生成分层 identity 假设清单；
2. 生成 legacy 活动义务只读一致性报告；
3. 冻结协议 1.2、order/admission/close policy 和硬阈值；
4. 记录当前基线测试结果；
5. P0 评审通过后，才创建 P1 的精确代码变更清单。

## 11. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-09-03 | 初版；建立 9 阶段依赖、前后置任务、状态词汇、验证矩阵、风险和证据台账 |
