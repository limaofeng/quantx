# QuantX 多标的做 T 助手新架构设计

> 状态：目标架构，待实施<br>
> 版本：1.0<br>
> 日期：2026-09-02<br>
> 适用范围：QuantX Windows Dev、个人单账户、A 股正向做 T

## 1. 结论

新的做 T 助手采用以下结构：

```text
每标的一个 SymbolTEngine
          +
全账户一个 PortfolioTCoordinator
          +
QuantX 现有统一风控、容量预占、订单、退出计划和回报收敛链路
```

这不是一个把所有股票逻辑混在一起的巨大 `MultiSymbolTEngine`，也不是一组可以
各自读取现金、抢占库存和直接下单的自治引擎。核心原则是：

> 标的自己的机会由 `SymbolTEngine` 判断；多个机会如何竞争同一个账户的资金、
> 库存和风险额度，由 `PortfolioTCoordinator` 判断；最终合法数量和是否能下单，
> 仍由统一执行链判断。

本设计保留一个账户级做 T `StrategyRun`，但只把它当作 QuantX 的运行信封：承载
`StrategyBase.step(StrategyInput)`、运行模式、回测身份、状态检查点、回报路由和
`ExitPlanBook` 所有权。它不是多标的领域模型，也不负责账户资金分配。

保留 `StrategyRun` 是从目标边界重新评估后的选择，不是因为现有实现无法修改。
彻底移除它并不能改善多标的信号质量或资金竞争，却会要求同时重建
`TradeIntent` 所有权、退出计划所有权、回测运行、审批恢复和委托/成交回报路由。
如果未来这些公共契约整体替换，做 T 的标的内核和组合协调器仍可原样复用。

LightGBM 作为可插拔的候选排序器引入：

- 规则引擎负责数据健康、形态状态、候选资格和硬门禁；
- LightGBM 只预测候选质量并参与跨标的排序；
- 模型不决定交易合法性、不计算数量、不读取账户现金；
- 先 `SHADOW`，再经走步验证和组合回测后人工晋升为 `ACTIVE`；
- `ACTIVE` 模型不可用时停止新的 ENTRY，不允许静默退回规则排序；
- 已有退出计划不依赖 LightGBM，继续优先执行。

## 2. 设计目标与非目标

### 2.1 目标

1. 同一时段多只持仓出现做 T 机会时，能够统一排名、分配额度和淘汰机会。
2. 每只股票的行情窗口、形态 FSM、候选和冷却状态相互隔离。
3. 任何标的内核都不能直接读取或修改真实现金、可卖量、冻结量和订单状态。
4. 多标的决策使用同一个因果一致的决策快照，而不是把先后到达的行情误认为同时状态。
5. 账户资源变更严格串行，避免两个机会同时认为自己可以使用同一笔现金或老仓库存。
6. PAPER、LIVE 和 BACKTEST 复用同一套标的规则、排序、组合协调和执行语义。
7. 每次候选、排名、淘汰、限额、拒绝、部分成交和退出都可审计、可回放。
8. 在不复制 QuantX 已有交易能力的前提下，复用 QMT Agent、OrderSizer、风控、
   `AccountCapacityService`、`ExitPlanBook`、durable outbox/inbox 和回报收敛。

### 2.2 非目标

- 不支持反向做 T；第一阶段只支持先买后卖的正向做 T。
- 不增加多账户、多租户或账户路由抽象。
- 不让每个标的创建独立 QMT 会话、独立资金池或独立执行服务。
- 不在 Engine、API 或 Worker 中直接导入 `miniqmt` / `xtquant`。
- 不新建做 T 专用 SELL FSM；退出继续由公共 `ExitPlanBook` 管理。
- 不新建第二套现金、库存或订单真源。
- 不使用 Worker RPC 或远程模型服务处理逐 Tick 推理。
- 不在第一阶段引入相关性矩阵、复杂优化器或强化学习。个人账户先使用并发数、总暴露、
  单票和行业集中度等可解释约束。

## 3. 必须保持的 QuantX 硬边界

本设计服从以下现有权威契约：

1. 行情决策只经过 `StrategyBase.step(StrategyInput)`；不得恢复
   `Signal/on_tick/on_bar/generate_signal` 主路径。
2. 策略只输出 `TradeIntent[]` 和算法状态补丁，不访问数据库、网络、文件或 QMT。
3. 策略不计算最终合法数量，不读取真实可用资金、冻结资金和真实可卖量。
4. A 股交易时段、T+1、停牌、涨跌停、整手、零股、现金和可卖量由统一交易域、
   OrderSizer、风控和 Broker 处理。
5. QMT Agent 是唯一 XTData/XTTrading 边界；实盘成交真源只能来自其上报的券商事实。
6. `command_ack` 只表示命令投递或本地处理结果，不得推进成交状态。
7. 委托和成交回报先进入持久化 inbox，再由 Engine 串行、幂等收敛。
8. PostgreSQL 是业务状态真源；Redis 只做唤醒、广播和可重建缓存。
9. 仓位归因继续使用 `locked_core/core/swing`；`locked_core` 默认不作为做 T 卖出来源。
10. 每次不买、少买、卖出、拒绝、延迟、熔断和模型阻断都必须有稳定原因码。

相关权威文档：

- [系统架构设计](系统架构设计.md)
- [A 股三层协作与执行契约](../trading/contracts/A股三层协作与执行契约.md)
- [A 股交易域数据结构与状态机](../trading/contracts/A股交易域数据结构与状态机.md)
- [A 股自动退出计划与卖出策略契约](../trading/contracts/A股自动退出计划与卖出策略契约.md)
- [持仓做 T 有状态机会引擎 V3 实施规格](../plans/持仓做T有状态机会引擎V3实施规格.md)

## 4. 当前设计评估

当前实现已经具备值得保留的基础：

- 一个账户级动态持仓做 T 运行；
- 每标的独立 `instrument_states`；
- 因果有界 Tick 窗口；
- `DataHealth`、回撤反弹和动量加速双 FSM；
- candidate/episode/fingerprint/TTL/rearm；
- CANARY 人工确认与 LIVE 自动执行共用重验链；
- 真实成交激活 `ExitPlan`；
- durable inbox/outbox、T+1 库存置换和 QMT 回报收敛；
- `TTradeBatch`、机会评估、参考画像和候选结果等审计投影。

现有问题不在于“一个运行监控多只股票”，而在于以下职责仍然耦合：

| 当前问题 | 影响 | 目标修正 |
|---|---|---|
| 行情按标的逐 Tick 进入决策 | 同一时段候选缺少一致比较基准 | 使用带 watermark 的 `TDecisionSnapshot` |
| 候选产生后直接进入意图/审批 | 多只股票同时出现机会时缺少统一排名 | 增加持久意图受理后的 `PortfolioTCoordinator` |
| 账户限制表现为单候选布尔门禁 | 只能回答能不能买，不能回答优先买谁、给多少 | 输出可审计 `TAllocationDecision` |
| 策略状态混有候选、订单、成交和退出摘要 | 状态恢复和真源边界不清晰 | 分离机会 FSM、意图、订单、ExitPlan 和批次投影 |
| `TTradeStatus` 同时描述信号与执行 | 容易形成第二套订单/退出状态机 | 执行状态由权威表派生，策略只保留候选与冷却 |
| 规则分只能做单票判断 | 无法量化跨票相对机会质量 | 可选 LightGBM 排序器，规则仍掌握资格与门禁 |
| 单票回测可各自假设可用现金 | 多标的结果可能隐含重复使用现金 | 使用单一共享账户的组合回测时间线 |

## 5. 目标逻辑架构

```text
miniQMT / XTData / XTTrading
            │
            ▼
        QMT Agent
唯一行情与交易本地边界、订单幂等、本地保护
            │  WebSocket
            ▼
API Agent Hub / Market Stream Transport
协议校验、标准化、持久化报告 inbox、Redis 行情传输
            │
            ▼
Engine WholeQuoteHub / Market Data Gateway
stream/generation/sequence 连续性、完整 fence、数据健康
            │
            ├──────────────► ExitPlanBook
            │                活跃退出优先评估
            ▼
TDecisionSnapshotBuilder
冻结决策时点、watermark、市场/行业上下文和标的快照
            │
            ▼
AshareIntradayTAssistantStrategy.step(SNAPSHOT)
运行信封内的薄编排适配器
            │
            ▼
SymbolTEngineRegistry
┌────────────┬────────────┬────────────┐
│ Symbol A   │ Symbol B   │ Symbol C   │  隔离状态、可并行纯计算
└────────────┴────────────┴────────────┘
            │
            ▼
TOpportunity / BUY TradeIntent 提案批次
            │
            ▼
record_trade_intents（整批持久化，PORTFOLIO_PENDING）
            │
            ▼
OpportunityScorer
RULE_ONLY / SHADOW / ACTIVE LightGBM
            │
            ▼
PortfolioTCoordinator
排名、额度、并发数、总暴露、行业集中度、机会淘汰
            │
            ▼
TAllocationDecision
ALLOW / CAP / DELAY / REJECT
            │
            ├── CANARY ──► AWAITING_APPROVAL ──► 实时重验
            └── LIVE ────► 自动受理
                              │
                              ▼
TradeIntentProcessor → OrderSizer → RiskChecker
                              │
                              ▼
AccountCapacityService
账户锁内最终复核现金、老仓库存和本地未覆盖义务
                              │
                              ▼
PendingTradeOrder + Correlation + TradeCommandOutbox
同一事务持久化，随后由 API Hub 投递 QMT Agent
                              │
                              ▼
miniQMT 委托/成交回报
                              │
                              ▼
AgentReportInbox → ReportProcessor → RuntimeStateManager
Portfolio/BucketLedger/ExitPlan/TTradeBatch 投影收敛
```

物理部署不变化：上述做 T 决策、排序和协调都在唯一活跃 Engine 进程内完成，API
仍只负责协议、会话和 GraphQL，QMT Agent 仍是唯一券商边界，Worker 只承担离线训练、
评估和数据任务。

## 6. 运行与所有权边界

### 6.1 `StrategyRun` 只作为运行信封

每个账户和执行环境最多一个活动做 T `StrategyRun`。它负责：

- PAPER/LIVE/BACKTEST 模式和冻结参数；
- `StrategyBase.step()` 调用与状态检查点；
- runtime event 串行消费；
- 人工审批候选的恢复身份；
- 入场来源 `ExitPlan` 的唯一运行绑定；
- 回测版本和结果归属。

它不负责：

- 在股票之间分配现金；
- 计算真实可卖量或最终下单数量；
- 保存券商账户真相；
- 为每只股票复制订单管理器；
- 充当一个包含大量 `if symbol == ...` 的巨型 T 引擎。

不采用“每标的一 `StrategyRun`”。那会把一个账户的退出所有权、审批恢复、共享资金
和回测身份切碎，并增加无收益的运行生命周期数量。

### 6.2 `TTradeGlobalMonitorService`

全局 Monitor 继续是 Engine 内的配置和动态 Universe 管理器，只负责：

- 一个账户的启停、模式、忽略名单和配置版本；
- 从权威持仓快照生成外部 Universe；
- 向运行发送 `RECONCILE`，完成标的加入、draining 和移除；
- 配置变更时阻止旧候选并触发 rewarm；
- 保证活动退出计划完成前运行只能进入 `DRAINING`。

它不运行标的信号，不排名候选，不下单。

### 6.3 `SymbolTEngineRegistry`

Registry 在一个运行内按 `instrument_code` 管理逻辑上的一标的一引擎：

```text
SymbolTEngineRegistry
  000001.SZ -> SymbolTEngine(state_A)
  002594.SZ -> SymbolTEngine(state_B)
  688552.SH -> SymbolTEngine(state_C)
```

每个引擎的输入、窗口、FSM、候选、版本和冷却完全隔离。Registry 只接受 Universe
Provider 给出的标的，不自行选股。

Registry 生命周期：

- `WARMING`：新加入或连续性丢失，构造完整因果窗口；
- `ACTIVE`：数据健康，允许产生机会；
- `DRAINING`：不产生新 ENTRY，但保留审计和活动批次关联；
- `RETIRED`：没有持仓、候选、意图、批次或退出计划后才可清理。

### 6.4 `SymbolTEngine`

`SymbolTEngine` 只回答：

> 在给定因果行情和公共环境上下文下，这只股票现在是否形成做 T ENTRY 机会？

它负责：

- Tick 去重、乱序处理和连续性代际；
- 数据健康与窗口预热；
- 回撤反弹、动量加速等标的内特征；
- 分支 FSM、episode、candidate、fingerprint、TTL、rearm；
- 规则硬门禁和规则分；
- 候选的特征快照及审计证据。

它不得接收或计算：

- 账户可用现金；
- 账户总资产和 T 总额度；
- 其他股票候选；
- 真实可卖量、冻结量和当日买入量；
- 最终下单数量；
- 订单、成交或退出计划的权威状态。

### 6.5 `PortfolioTCoordinator`

Coordinator 是 Engine/application 层的账户级纯协调器。它读取一份不可变的
`PortfolioTDecisionSnapshot`，回答：

> 当前同一决策周期内的有效候选，哪些可以进入审批或执行，各自最多获得多少预算？

它负责：

- 候选过滤与确定性排序；
- T 总资金池和现金缓冲；
- 最大活动批次数；
- 单票 T 上限；
- 总 T 暴露上限；
- 行业集中度；
- 同标的单活动批次；
- 已有待审批、待下单、活动 ENTRY/EXIT 和保护义务；
- 输出每个候选的排名、动作、预算上限和原因。

它不负责：

- 生成新的买卖方向；
- 修改候选形态状态；
- 计算最终股数；
- 把估算的现金或库存当作最终入队凭证；
- 直接创建 QMT 命令。

### 6.6 公共执行组件

以下能力继续复用，不为做 T 复制：

| 能力 | 权威组件 |
|---|---|
| 合法数量和整手 | `OrderSizer` |
| 交易时段、停牌、涨跌停、T+1、订单风控 | `RiskChecker` / 交易域 |
| 最终现金和老仓容量 | `AccountCapacityService` |
| 订单持久化和可靠投递 | `PendingTradeOrder` / `TradeCommandOutbox` |
| 券商下单和本地保护 | QMT Agent |
| 委托与成交事实 | QMT Agent 报告 + `AgentReportInbox` |
| 自动退出 | `ExitPlanBook` / `auto_exit_plans` |
| 仓位归因和置换 | `BucketLedger` / `T1SubstitutionPlan` |
| 一轮做 T 的运营投影 | `TTradeBatch` + `TTradeBatchEvent` |

## 7. 因果一致的决策快照

### 7.1 为什么不能要求“所有股票同一毫秒报价”

多只股票不会在完全相同的源时间产生 Tick。等待每只股票都更新后再决策，会让低活跃
标的阻塞整个账户；直接拿各自最新值又可能把新旧不同的数据伪装成同时状态。

因此这里的一致性定义是：

> 一个决策周期只使用 Engine 已完整应用到同一 market fence 的数据；每个字段都必须
> 在 `decision_time` 之前可得，并显式携带自身 `as_of` 和 freshness。

### 7.2 `TDecisionSnapshot`

目标结构：

```text
TDecisionSnapshot
  cycle_id
  decision_time
  trade_date
  stream_id
  continuity_generation
  fence_sequence
  universe_revision
  config_version
  policy_version
  feature_schema_version
  model_mode / model_version
  market_context
    as_of
    health
    index / breadth / liquidity facts
  sector_contexts
    sector_id -> as_of / health / facts
  symbols
    instrument_code -> SymbolDecisionSnapshot
```

`SymbolDecisionSnapshot` 至少包含：

- 最新已接受 quote 和五档数据；
- `continuity_generation/source_time_ms/tick_ordinal`；
- quote age、缺字段和数据健康；
- 交易时段、涨跌停价、停牌、ST 等静态/时点事实；
- 主行业和概念映射及其版本；
- 外部 Universe 的 `eligible/draining/ignored` 事实；
- 只读的标的画像和参数版本。

它不向 `SymbolTEngine` 暴露现金、真实可卖量和其他标的状态。

### 7.3 构建规则

1. 只从 `WholeQuoteHub` 已完整应用的 Engine fence 构建。
2. 不等待未来 Tick，不向前填充未来行业或指数数据。
3. 每个 symbol 独立判断 freshness；一只股票陈旧不应污染其他股票的窗口。
4. 市场或行业公共上下文缺失时显式为 `INSUFFICIENT/STALE`，不得默认成中性。
5. stream/generation 变化、sequence 缺口或关键消费者 lagging 时，相关窗口失效并 rewarm。
6. 重复或乱序 source identity 不推进状态，也不产生普通数据库写入。
7. ENTRY 可以对连续完整 fence 做有界合并，但必须记录被合并的 fence 范围；
   活跃 EXIT 仍按公共退出计划的关键行情路径优先评估。

### 7.4 决策节奏

实盘以已接受的 `WholeQuoteHub` 完整批次为基础构造周期。若进入背压状态：

- 不允许无限排队后使用过时行情产生 ENTRY；
- 只可把尚未计算、连续且没有 generation 变化的 ENTRY fence 合并到最新完整 fence；
- 合并跨度超过策略窗口允许值时视为连续性丢失并 rewarm；
- 不得用 UI 的 latest-only 行情队列承载交易决策。

## 8. 每周期决策流程

### 8.1 顺序

每个周期固定按以下顺序执行：

```text
1. 冻结 TDecisionSnapshot
2. 对活动 ExitPlan 做优先评估
3. 调用唯一 StrategyBase.step(SNAPSHOT)
4. 在隔离状态副本上计算各 SymbolTEngine
5. 按 instrument_code 确定性合并状态和候选
6. 整批持久化 BUY TradeIntent 提案与候选证据
7. OpportunityScorer 计算规则/模型排序证据
8. PortfolioTCoordinator 生成 TAllocationDecision
9. 淘汰或延迟的意图写终态原因
10. 选中意图进入 CANARY 审批或 LIVE 自动路由
11. OrderSizer、Risk、AccountCapacityService 最终复核
12. 原子创建 pending/correlation/outbox 后才能投递
```

退出先于入场的含义是优先处理风险和本地状态，不是假定 SELL 一定先于 BUY 在券商成交。
任何本地 sequencer 都不能承诺券商侧的成交先后。

### 8.2 标的并行与确定性合并

每个 `SymbolTEngine` 的输入是独立不可变状态，输出是：

```text
SymbolTReduction
  instrument_code
  next_state
  opportunity?
  material_events[]
  diagnostics
```

可以使用有界任务池并行计算，但必须满足：

- 并行任务不修改共享策略状态；
- 不共享可变窗口或 candidate 对象；
- 计算完成后按 `instrument_code`、source identity 确定性归并；
- 同一 symbol 在同一周期最多一个 reducer；
- 任一 reducer 异常只阻断该周期的新 ENTRY，并留下完整故障审计，不能提交半个状态批次；
- 个人账户初始实现可以顺序执行；只有性能数据证明需要时才启用线程池或进程内并行。

### 8.3 `TOpportunity`

`TOpportunity` 是标的内核和组合协调之间的不可变领域值，不是订单：

```text
TOpportunity
  opportunity_id
  candidate_id / candidate_fingerprint
  instrument_code
  path
  observed_at / expires_at
  rule_score
  data_health
  liquidity_features
  market_context_version
  sector_context_version
  policy_version
  feature_schema_version
  profile_version / profile_fingerprint
  feature_vector_hash
  causal_source_identity
  blockers[]
```

禁止在其中放入：

- `requested_qty/min_qty/max_qty`；
- 账户现金和总资产；
- 真实可卖量；
- 最终限价和订单类型；
- 已冻结或已预占状态。

为了继续遵守“策略只输出 `TradeIntent[]`”，薄策略适配器会把通过标的内硬门禁的
`TOpportunity` 映射为标准 BUY `TradeIntent` 提案。`target_amount` 只是冻结配置中的
单机会申请上限，不是实际分配，也不承诺可以成交。候选完整证据放在结构化 metadata
和机会评估记录中。

### 8.4 意图先持久化再协调

所有策略输出的候选意图必须先经过统一 `record_trade_intents` 整批持久化，初始状态为
`PORTFOLIO_PENDING`。Coordinator 不处理只存在于内存或日志中的候选。

建议意图生命周期增加：

```text
PORTFOLIO_PENDING
  ├─ ALLOCATED -> AWAITING_APPROVAL / APPROVED
  ├─ CAPPED    -> AWAITING_APPROVAL / APPROVED
  ├─ DELAYED   -> EXPIRED 或在明确重验后重新参与
  └─ REJECTED  -> 终态
```

每次动作保存 `allocation_decision_id`、决策周期、排名、额度、账户快照身份和原因码。
未选中候选不对用户暴露为可确认订单，也不能进入 OrderSizer。

### 8.5 必须原子调整的公共契约

当前代码只有 `BAR/TICK/ORDER/TRADE/RECONCILE` cadence，目标架构需要新增明确的
`StrategyCadence.SNAPSHOT`，不能把账户级冻结快照伪装成某一只股票的普通 `TICK`。
该 cadence 只用于外部 Universe 已经确定的账户级动态标的策略：

- `StrategyInput.market_data` 携带一个 `TDecisionSnapshot`；
- `StrategyInput.instrument_code` 固定为空字符串，不放账户标识；具体标的只出现在
  snapshot item 和输出 `TradeIntent.instrument_code` 中；
- `StrategyInput.market_data_context` 只描述本 cycle 的 stream/generation/fence 健康，
  每票 source identity 和 freshness 保留在各自 snapshot item；
- `StrategyInput` 不携带 `PortfolioTDecisionSnapshot`，保证策略不读取账户；
- 一个 step 返回该 cycle 的全部候选 TradeIntent 和一个原子状态补丁；
- 固定标的策略继续使用原有 `TICK/BAR`，不改变一实例一标的约束。

同时需要原子更新：

- `TradeIntentRecord` 的 `PORTFOLIO_PENDING/ALLOCATED/CAPPED` 生命周期；
- `TAllocationDecision` 持久化和 GraphQL 只读投影；
- Engine 的整批意图受理、协调恢复与 TTL 终结；
- LIVE/BACKTEST 的 snapshot scheduler；
- 相应 contracts、客户端类型、文档和测试。

这些是一次权威协议升级，不保留“逐票直接审批”和“snapshot 组合协调”两条长期入口。

## 9. 组合协调与资金分配

### 9.1 输入快照

`PortfolioTDecisionSnapshot` 由 Engine/application 层从权威投影构建，至少包含：

- 账户执行控制、kill switch 和 reconcile 状态；
- 最新合法完整账户快照 id/hash/as_of；
- 可用资金及快照尚未覆盖的本地 BUY 义务；
- 每标的老仓可卖量及未覆盖的订单、T 批次、保护计划占用；
- 活动 `TTradeBatch`、待审批和待下单意图；
- 当前 T 暴露、当日已实现/未实现 T 损益和可配置熔断；
- 主行业映射和已有 T 暴露；
- 全局、单票和行业上限；
- 现金缓冲和最大并发批次数。

该快照只提供给 Coordinator、OrderSizer 和风控，不传给 `SymbolTEngine`。

### 9.2 确定性算法

初始实现使用可解释的贪心分配，不引入复杂求解器：

1. 移除已过期、数据不健康、同标的已有活动批次或存在 reconcile 的候选。
2. 根据当前 scorer 模式计算 `rank_score`。
3. 使用稳定排序键：

   ```text
   rank_score DESC
   rule_score DESC
   liquidity_quality DESC
   observed_at ASC
   instrument_code ASC
   candidate_id ASC
   ```

4. 计算账户 T 规划额度：全局金额上限、总资产比例上限和现金缓冲中的最小值。
5. 按排名逐个检查最大并发数、单票额度、行业额度、当前义务和剩余规划额度。
6. 为候选输出 `ALLOW/CAP/DELAY/REJECT` 及预算上限。
7. 预算不足一手的保守成本上限时直接拒绝，不生成零股或非法数量。

Coordinator 可以用 quote 和老仓投影估算“是否至少可能完成一手”，但最终数量仍由
OrderSizer 和最终容量事务计算。

### 9.3 `TAllocationDecision`

```text
TAllocationDecision
  decision_id
  cycle_id
  intent_id
  candidate_id
  instrument_code
  rank
  rank_score
  action: ALLOW | CAP | DELAY | REJECT
  requested_amount_ceiling
  allocated_amount_cap
  account_snapshot_id
  portfolio_policy_version
  scorer_mode / model_version
  blockers[]
  created_at
```

`allocated_amount_cap` 是 OrderSizer 的上限输入，不是冻结资金。账户事实可能在下一毫秒
变化，因此最终命令入队时必须重新复核。

### 9.4 组合约束优先级

建议优先级从高到低为：

1. 账户隔离、kill switch、reconcile 和数据完整性；
2. 已有退出、撤单和隔离修复义务；
3. 同标的活动批次和库存保护冲突；
4. 总 T 暴露和最大并发数；
5. 单票和行业集中度；
6. 现金缓冲；
7. 排名和额度优化。

行业采用唯一主行业做硬约束。概念板块存在重叠，第一阶段只做解释或软惩罚，不把多个
概念额度简单相加，以免重复计算风险。

## 10. 审批、OrderSizer 与原子容量预占

### 10.1 CANARY

Coordinator 选中的候选进入 `AWAITING_APPROVAL`，但不提前占用真实资金或老仓库存。
原因是人工确认可能延迟，长期预占会阻塞其他机会。

确认时必须重新校验：

- candidate id/fingerprint/state/config/policy/model 版本；
- TTL 和最新规则硬门禁；
- 最新 quote、允许偏离和行情连续性；
- 最新账户执行控制和完整快照；
- 最新组合额度、同标的活动批次和保护义务；
- OrderSizer、A 股风控和最终容量。

确认不是成交承诺。确认后仍可能被 `CAP/REJECT/RECONCILE_REQUIRED`。

### 10.2 LIVE

LIVE 自动模式只跳过人工点击，不跳过任何候选、组合、风控、容量和最终入队复核。
自动授权必须精确绑定配置、策略/模型版本、账户执行窗口和额度。

### 10.3 不新增第二套 Reservation 真源

参考设计中的 `Cash & Inventory Reservation` 映射到 QuantX 已有
`AccountCapacityService` 和 durable pending/outbox/保护义务，不新增一个会与账户快照
竞争的独立余额账本。

最终 ENTRY 入队事务必须：

1. 锁定 `AccountExecutionControl`，再按现有统一锁序读取标的持仓和相关义务；
2. 重新验证协议 1.1 完整账户快照、新鲜度、hash 和分区完整性；
3. 扣除该快照尚未观察到的本地 pending/outbox、活动批次和退出保护；
4. 由 OrderSizer 得到最终合法整手数量；
5. 校验 BUY 最坏价格和费用下的资金上限；
6. 校验同等数量、未被占用的昨日老仓可卖库存；
7. 在同一事务创建或更新 TradeIntent、PendingTradeOrder、Correlation、
   `TTradeBatch` 运营投影和 `TradeCommandOutbox`；
8. 事务提交后才允许 API Hub 投递。

这个“预占”是 QuantX 对本地未被券商快照覆盖义务的持久化扣减，不伪装成券商冻结。

### 10.4 释放规则

- BUY 部分成交：只按真实成交量消费现金并建立等量退出保护，未成交部分继续占用。
- 明确拒绝/撤销且有权威零成交证明：释放未成交现金和库存义务。
- 已投递但结果不确定：保持占用并进入 `RECONCILE_REQUIRED`。
- 委托 `FILLED` 先于 TRADE 到达：不得提前释放，等待成交报告收敛。
- 迟到成交：先计入真实成交，再使错误/隔离状态 fail-closed，禁止重复下单。
- 精确幂等重试：返回原 pending/outbox，不重复占用。

## 11. 一轮正向做 T 的生命周期

### 11.1 不另建重复 `TRound` 真源

“一轮做 T”是重要业务概念，但 QuantX 已经有多个各自权威的事实：

- BUY/SELL `TradeIntentRecord`；
- `PendingTradeOrder` 和 `StrategyOrderCorrelation`；
- QMT 委托与成交回报；
- `BucketLedger` 和 `T1SubstitutionPlan`；
- `auto_exit_plans`；
- `TTradeBatch` 运营投影。

因此目标架构把 `TTradeBatch` 明确定义为一轮正向做 T 的可重建运营投影，而不是新增
另一个拥有订单、成交和退出状态的 `TRound` 聚合。UI 可以把它展示为“做 T 轮次”。

### 11.2 生命周期

```text
CANDIDATE
  -> PORTFOLIO_PENDING
  -> ALLOCATED / AWAITING_APPROVAL
  -> ENTRY_QUEUED
  -> ENTRY_SUBMITTED
  -> ENTRY_PARTIAL / ENTRY_FILLED
  -> ExitPlan PENDING_ENTRY / ACTIVE
  -> EXIT_PENDING / PARTIALLY_EXITED
  -> COMPLETED
```

异常分支包括：

```text
REJECTED
EXPIRED
CANCEL_REQUESTED
RECONCILE_REQUIRED
ERROR
```

`TTradeBatch.status` 只能由上述权威事实派生，不得反向推进订单或 ExitPlan。

### 11.3 正向 T+1 置换

1. ENTRY 只能使用尚未被其他订单、T 批次或保护计划占用的昨日老仓可卖量作为上限。
2. BUY 实际成交后，新买股份进入 `swing` 归因并在当日不可卖。
3. 对应 `ExitPlan` 只保护 BUY 实际成交数量，T+1 策略使用
   `ALLOW_SAME_INSTRUMENT_SUBSTITUTION`。
4. EXIT SELL 使用同标的老仓完成合法置换；BucketLedger 按实际成交量记录
   `swing -> core` 等置换流水。
5. `locked_core` 默认不能作为普通做 T 退出来源。
6. BUY 部分成交只建立等量保护；SELL 部分成交只完成等量置换。
7. 外部卖出导致库存不足时进入账户和计划 reconcile，不能自动缩减或猜测修复。

### 11.4 单标的并发规则

第一阶段固定同一标的最多一个未完成 T 批次，包括：

- 等待审批；
- ENTRY pending/partial/结果不确定；
- 已成交但 ExitPlan 未完成；
- EXIT pending/partial/结果不确定。

多批次并行会显著增加成本基准、库存认领和迟到回报归属复杂度，当前没有足够收益支持。

## 12. 退出管理

做 T 新架构不修改公共退出原则：

1. ENTRY BUY 意图附带冻结的 `ExitPlanTemplate`。
2. 只有真实 BUY 成交回报激活保护数量。
3. PAPER/LIVE 的 `auto_exit_plans` 是唯一持久化退出真源。
4. 同一做 T `StrategyRun` 的 `ExitPlanBook` 在 ENTRY 决策前优先评估活动计划。
5. 命中规则后产生标准 SELL `TradeIntent`，继续经过 OrderSizer、风控和 Broker。
6. SELL 的 owner 固定为 `EXIT_PLAN`，并保留 run、batch 和 role 用于回报收敛。
7. 运行停止新 ENTRY 时进入 `DRAINING`，活动计划完成前不得普通停止。

LightGBM 不参与退出触发。已有风险保护不能因模型、候选池、Coordinator 或训练服务
异常而停止。

## 13. 并发、串行和优先级

### 13.1 可以并行的部分

- 不同 `SymbolTEngine` 的纯特征和 FSM reduction；
- LightGBM 对同一候选批次的向量化推理；
- 不改变状态的诊断和 UI 投影构建。

### 13.2 必须串行的部分

- 同一 symbol 的 source identity 消费；
- 一个决策周期的状态归并和 TradeIntent 整批受理；
- 一个账户的组合分配提交；
- 审批、配置变更和候选失效；
- 账户容量最终复核、pending/outbox 创建；
- 同一 `StrategyRun` 的 runtime event 应用；
- QMT 回报对 Portfolio、BucketLedger、ExitPlan 和批次投影的收敛。

### 13.3 账户任务优先级

同一账户本地调度建议使用：

1. 隔离、对账、撤单和紧急停止；
2. 活动 ExitPlan 的 SELL；
3. 已批准 ENTRY 的最终容量复核和入队；
4. 新候选的组合协调；
5. 配置、Universe 和普通检查点。

进程内 `t_trade_account_coordination_lock` 用于把配置与审批线性化，但不是持久化真源。
最终交易安全仍依赖数据库事务、账户控制行锁和既定全链锁序。

## 14. LightGBM 设计

### 14.1 定位

LightGBM 的目标不是替代 V3 规则，而是提高“多个合格候选中谁更值得优先使用有限资金”
的排序质量。

推荐模式：

| 模式 | 规则资格 | 模型计算 | 是否影响排序 | 模型故障行为 |
|---|---|---|---|---|
| `RULE_ONLY` | 是 | 否 | 使用 rule score | 不适用 |
| `SHADOW` | 是 | 是 | 否，只记录比较 | 记录故障，规则路径不受影响 |
| `ACTIVE` | 是 | 是 | 是 | 阻止新 ENTRY，禁止静默降级 |

模式切换是显式配置变更，需要新 `config_version`、授权指纹和审计记录。

### 14.2 模型不能做什么

- 不能绕过 `DataHealth` 和规则硬门禁；
- 不能把不合格样本变成候选；
- 不能读取账户现金、持仓额度、活动批次或用户是否确认；
- 不能输出最终数量、订单类型或下单价格；
- 不能直接触发 SELL；
- 不能在线自学习并立即修改实盘模型；
- 不能通过网络 RPC 逐 Tick 推理。

### 14.3 特征

特征必须在决策时点可得，并固定 `feature_schema_version`。可使用：

- V3 回撤、反弹、速度、加速度、VWAP 偏离、量能和盘口特征；
- 数据健康和窗口完整度；
- 标的历史流动性和当前 spread/depth；
- 大盘、行业和概念的时点环境；
- 日内时间、距午休/收盘时间；
- 只使用决策前数据计算的参考画像。

禁止特征：

- 未来价格、未来完整 BAR、未来复权因子；
- 最终是否成交、最终成交价或实际分配金额；
- 当前账户可用现金、排序名次和 active batch 压力；
- 人工是否点击确认；
- 训练期之后才产生的模型、策略或画像版本信息。

排除账户特征可以避免模型把历史资金分配策略学成“机会质量”，并使同一分数可跨账户
状态和回测场景比较。

### 14.4 标签与样本

当前 `t_trade_candidate_outcomes` 适合评估已形成候选的 60/300/900 秒表现，但只用
候选或已执行样本训练会产生选择偏差。训练数据必须覆盖所有满足基础数据质量的、按
固定规则抽样的 observation anchor，包括没有形成候选的负样本。

每个模型版本必须冻结：

- anchor 抽样规则；
- 预测 horizon；
- 保守成交、手续费、印花税、过户费和滑点模型；
- 正样本阈值；
- 未成交、停牌、涨跌停和数据缺失的标签处理；
- 重叠样本去重或权重规则。

第一阶段建议预测：在冻结 horizon 和保守执行假设下，ENTRY 后可实现净正 edge 的
校准概率。后续如需预测预期净 edge 或 MAE，必须使用独立标签和版本，不能在同一个
字段中改变语义。

### 14.5 训练与验证

训练运行在 Worker/Research，不在 Engine：

1. 生成因果 feature/label 数据集和 manifest；
2. 使用按时间切分的 purged walk-forward；embargo 至少覆盖最大标签 horizon；
3. 在每个验证窗内独立校准概率，禁止用全量数据校准；
4. 同时训练简单基线，例如 Logistic Regression；
5. 比较 Brier/log loss、AUC、Precision@K/NDCG@K；
6. 使用共享账户组合回测比较费用后收益、回撤、换手、容量拒绝和行业集中度；
7. 只有相对规则排序和简单基线有稳定增益，才允许成为 challenger；
8. 人工审核后才能从 challenger 晋升 champion/ACTIVE。

不能只用随机 train/test split，也不能只报告分类准确率。

### 14.6 模型制品

复用 QuantX 已有安全模型制品方法，但不复用下一日选股模型本身。制品至少包含：

```text
model_id / model_version
model_type = LIGHTGBM
feature_schema_version / feature_order
label_spec_version
training_data_cutoff
walk_forward_windows
calibration_type / calibration_parameters
validation_metrics
portfolio_backtest_metrics
policy_compatibility
artifact_sha256
status = CHALLENGER | CHAMPION | RETIRED
```

Engine 只加载经校验的 LightGBM 原生安全格式和显式校准参数，不加载任意 pickle。
启动、配置变更和模型晋升时预加载；逐周期推理只调用内存 scorer。

### 14.7 排序融合

规则资格永远先执行。排序分采用版本化、可回放的明确公式，例如：

```text
RULE_ONLY:
  rank_score = normalized_rule_score

SHADOW:
  execution_rank_score = normalized_rule_score
  shadow_ml_score = calibrated_probability

ACTIVE:
  rank_score = versioned_blend(
      normalized_rule_score,
      calibrated_probability,
      explicit_liquidity_penalty,
      explicit_cost_penalty,
  )
```

融合权重属于模型/portfolio policy 版本，不允许在运行中隐式变化。每个 material candidate
保存规则分、模型原始分、校准分、最终 rank score 和有限的 top feature contribution；
普通 Tick 不逐笔计算或持久化 SHAP。

## 15. 回测设计

### 15.1 共享账户，而不是每票独立资金

多标的做 T 回测必须只有一个 `BacktestPortfolio`：

- 所有标的共享现金、冻结和 T 总暴露；
- 所有 BUY/SELL 共享订单队列和容量；
- 老仓库存、当日买入和 T+1 按标的记录；
- 同一时刻多个候选经过同一个 Coordinator；
- 费用、滑点、涨跌停、停牌、部分成交和不可成交由 `BacktestBroker` 统一模拟。

禁止把每只股票独立回测后简单相加，因为那会重复使用现金、并发额度和行业额度。

### 15.2 统一时间线

历史数据先合并成稳定时间线，再构建与实盘相同的 `TDecisionSnapshot`：

```text
decision_time
market event type priority
source sequence
instrument_code
stable source identity
```

每次撮合、ExitPlan 评估和策略决策后，必须等待 Broker 回报串行收敛，再处理下一行情。
相同完成时刻遵守现有 Tick/BAR 因果顺序。

### 15.3 模型的时间因果

- 固定模型回测要求 `training_data_cutoff < backtest_start`；
- walk-forward 回测只允许在每个训练窗结束后生成下一窗模型；
- 任何模型、校准器、画像和行业映射都按当时可用版本加载；
- 缺失当时版本时阻断该段模型回测，不得用当前模型回填历史。

### 15.4 结果

除现有批次和收益指标外，组合回测必须输出：

- 每周期候选数、选中数和淘汰原因；
- rule/ML 排名一致率和 Top-K 命中；
- 资金使用率、现金缓冲和容量拒绝率；
- 最大并发批次、单票和行业暴露；
- 费用后每轮 PnL、持有时间、MFE/MAE；
- 因模型排序相对规则排序产生的增量收益和增量回撤；
- 数据健康、模型不可用和期末未闭环数量。

## 16. 状态真源与持久化

### 16.1 真源矩阵

| 数据 | 真源 | 备注 |
|---|---|---|
| 标的行情窗口和 FSM 热状态 | Engine 内存 | 可重建；检查点只保存保守恢复投影 |
| 做 T 配置与版本 | `t_trade_global_configs` | 单账户唯一配置 |
| 标的 Universe | 权威持仓快照 + Monitor 投影 | 策略不得自行选股 |
| material 机会证据 | `t_trade_opportunity_evaluations` | append-only、幂等 event key |
| 参考画像 | `t_trade_instrument_profiles` | 点时版本化 |
| 候选结果 | `t_trade_candidate_outcomes` | 用于候选评估，不单独充当完整训练集 |
| TradeIntent | `strategy_trade_intents` | 候选提案也必须先受理 |
| 组合分配决策 | 新增持久化 `t_trade_allocation_decisions` | 一条候选一条动作和原因 |
| 最终账户容量 | 完整 QMT 快照 + 本地未覆盖义务 | `AccountCapacityService` 事务计算 |
| pending/outbox | 对应持久化业务表 | 命令可靠投递真源 |
| 委托/成交 | QMT 报告 + inbox/业务表 | 唯一实盘成交真源 |
| 自动退出 | `auto_exit_plans` | `ExitPlanBook` 只是运行热缓存 |
| 仓位归因 | `BucketLedger` | locked_core/core/swing |
| 做 T 轮次展示 | `TTradeBatch` + 事件 | 可重建运营投影，不反向驱动真源 |
| LightGBM 模型 | 版本化模型制品与 manifest | Engine 内存只读加载 |

### 16.2 普通 Tick 写入原则

普通窗口推进和无变化诊断不写 PostgreSQL。立即持久化的 material 事实包括：

- 数据健康或 FSM 的重要转换；
- candidate 创建、过期、抑制和审批绑定；
- TradeIntent；
- scorer/Coordinator 决策；
- 审批、风险和容量裁决；
- pending/outbox；
- 委托、成交、ExitPlan 和批次事件；
- 模型模式或版本变更。

BACKTEST 的普通热状态和无意图 material 评估继续使用 `DAY_BATCH`，但真正候选、意图
和模拟成交必须即时成为幂等事实。

### 16.3 建议补充的关联字段

为了端到端回放，相关投影和事件应能够关联：

```text
cycle_id
opportunity_id
candidate_id / fingerprint
intent_id
allocation_decision_id
strategy_run_id
t_batch_id
exit_plan_id
client_order_id / broker_order_id
policy / config / feature / profile / model versions
account_snapshot_id
trace_id
```

不要求每张表复制所有字段，但必须通过稳定外键或业务键无歧义连接。

## 17. 故障与恢复语义

| 故障 | ENTRY 行为 | EXIT 行为 | 恢复 |
|---|---|---|---|
| 行情 stream/generation 变化 | 清空相关窗口，停止新候选 | 仅在新鲜行情恢复后继续评估 | 完整 fence + rewarm |
| 单票 quote 陈旧 | 只阻断该票 | 该票退出暂停，不用旧价触发 | 新鲜 Tick 后恢复 |
| 市场/行业上下文陈旧 | 按 policy 阻断或保守降级并审计 | 不改变既有退出规则 | 新版本上下文 |
| Engine 关键消费者 lagging | 阻断新 ENTRY | 保持可见并 fail-closed | resync 后 rewarm |
| 账户快照陈旧/不完整 | 全部拒绝 | 不猜测可卖量；必要时 reconcile | 新合法完整快照 |
| Coordinator 异常 | 整周期不提交分配 | 不影响已有退出 | 同 cycle 幂等重试或终态拒绝 |
| `ACTIVE` 模型缺失/校验失败 | 全部 `MODEL_UNAVAILABLE`，不静默降级 | 不影响 | 修复同版本或显式切 RULE_ONLY |
| 提案已持久化、协调前崩溃 | 不路由 | 不影响 | TTL 内用最新事实重验，否则过期 |
| 容量事务提交前崩溃 | 没有 outbox，不得下单 | 不影响 | 幂等重试 |
| outbox 已投递、结果未知 | 保持占用并 reconcile | 同公共订单契约 | QMT 快照/回报证明 |
| ORDER 先于 TRADE | 不提前释放 | 不提前完成 | 等成交累计量收敛 |
| 迟到成交或释放后反证 | 账户隔离，禁止新 ENTRY | 精确计划 sticky ERROR | 显式修复和新快照 |
| Engine 重启且有活动 ExitPlan | 禁止新建替代运行 | 恢复同一 run 所有权 | durable inbox + plan reload |

任何恢复都不能通过创建第二个 StrategyRun、第二个 ExitPlan 或重发不同 intent 来“绕过”
不确定状态。

## 18. 审计、可观测性与 UI 投影

### 18.1 一次机会的审计链

```text
market fence
  -> TDecisionSnapshot/cycle_id
  -> Symbol feature + FSM evaluation
  -> candidate/opportunity
  -> rule score / ml score
  -> portfolio rank/allocation
  -> approval/revalidation
  -> sizing/risk/capacity
  -> intent/order/outbox
  -> QMT order/trade reports
  -> ExitPlan
  -> TTradeBatch result
```

任何页面上的“没有执行”都必须落在明确类别：数据不健康、规则不合格、模型不可用、
排名淘汰、组合额度不足、审批过期、OrderSizer 为零、风险拒绝、容量拒绝或 Broker 失败。

### 18.2 运行指标

至少监控：

- decision cycle lag、耗时和合并 fence 数；
- 每 symbol quote age、window warmup 和 data health；
- 每周期 evaluated/candidate/selected/rejected 数；
- scorer 模式、模型版本、推理耗时和错误数；
- rule 与 ML 排名漂移、score 分布漂移；
- Coordinator 额度使用、行业暴露和淘汰原因；
- 审批过期和确认后容量拒绝；
- active batch、ExitPlan、reconcile 和 sticky error；
- outbox 投递、ORDER/TRADE 延迟和乱序收敛。

### 18.3 UI 建议

做 T 助手页面应把三类信息分开：

1. **机会**：标的、路径、数据健康、rule/ML 分、排名、TTL、淘汰原因；
2. **组合**：总额度、已规划/已占用、现金缓冲、并发、单票和行业暴露；
3. **轮次**：ENTRY、ExitPlan、成交、净收益、异常和 reconcile。

不得把“模型高分”“已分配预算”“已批准”和“已成交”用一个状态或同一种颜色表示。

## 19. 代码落点建议

不新增微服务，优先在现有边界内重构：

```text
packages/domain/src/quantx_domain/trading/
  t_trade_opportunity_engine.py      # 继续作为纯 SymbolTEngine reducer
  t_trade_portfolio_coordinator.py   # 新增纯组合协调算法和契约
  t_trade_scoring.py                 # scorer 输入/输出和值对象，不做 I/O

packages/application/src/quantx_application/t_trade_v3/
  contracts.py                       # snapshot/opportunity/allocation 契约
  ports.py                           # scorer、证据和分配存储端口
  use_cases.py                       # 决策周期受理、协调和恢复用例

packages/infrastructure/src/quantx_infrastructure/
  services/t_trade_lightgbm_scorer.py
  repositories/t_trade_allocation_decision_repository.py
  models/t_trade_allocation_decision.py

apps/engine/src/quantx_engine/
  t_trade_decision_snapshot.py       # 从 WholeQuoteHub 构建冻结快照
  t_trade_decision_runtime.py        # 周期、排序、协调和路由编排
  t_trade_global_monitor.py          # 只保留配置/Universe/生命周期
```

`AshareIntradayTAssistantStrategy` 应逐步收敛为薄适配器：批量调用纯标的 reducer、合并
算法状态、输出候选 BUY TradeIntent 和退出模板。账户事实、组合排名、真实成交量和
ExitPlan 状态机不得继续堆入该类。

上述文件名是目标职责建议，不要求为目录美观进行无价值拆分。若现有模块能清晰承担同一
职责，可以原地重构；同一能力只能保留一条权威路径。

## 20. 迁移路线

### 阶段 0：冻结契约和基线

- 固定当前 V3 规则、候选、审批、TTradeBatch 和 ExitPlan 行为测试。
- 为当前实盘/回测记录补齐 cycle、candidate、intent、batch、plan 关联基线。
- 明确新意图状态和 `TAllocationDecision` schema。
- 不改变活动批次和退出计划。

### 阶段 1：快照与标的内核解耦

- 引入 `TDecisionSnapshotBuilder` 和 account T universe snapshot。
- 把当前每标的 V3 逻辑收敛为无共享可变状态的 reducer。
- `StrategyBase.step(SNAPSHOT)` 成为 LIVE/BACKTEST 同一批量入口。
- 规则输出与旧实现逐事件对比，保持规则等价。

### 阶段 2：组合协调影子运行

- 持久化所有 material 候选和 allocation shadow decision。
- 旧路径仍决定是否进入审批；新 Coordinator 只比较，不执行。
- 验证排序确定性、额度计算和重启恢复，不建立双订单路径。

### 阶段 3：Coordinator 成为唯一候选准入

- 原子切换为 `PORTFOLIO_PENDING -> TAllocationDecision -> 审批/路由`。
- 删除旧的“候选直接审批”路径和重复账户布尔门禁。
- CANARY 逐笔人工确认，确认时走最新组合和最终容量重验。

### 阶段 4：共享账户组合回测

- 统一多标的时间线、现金、库存、费用、T+1 和回报收敛。
- 对照 LIVE 的 snapshot、coordinator、OrderSizer、Risk 和 ExitPlan。
- 通过无重复资金、无超老仓、无未来数据验收。

### 阶段 5：LightGBM SHADOW

- 生成全 observation anchor 训练集和严格 walk-forward 结果。
- Engine 内安全加载 champion artifact，记录 shadow score。
- 至少覆盖不同波动环境和足够完整 T 轮次后再评估晋升。

### 阶段 6：LightGBM ACTIVE 灰度

- 人工晋升模型和配置版本；先低额度 CANARY，再受控 LIVE。
- ACTIVE 不可用时阻断新 ENTRY。
- 退出、回报收敛和账户安全链与模型完全解耦。

### 切换规则

- 不长期保留旧/新两套候选准入协议。
- 每个阶段完成后原子更新代码、契约、GraphQL、文档和测试。
- 旧未确认候选在配置或 feature schema 切换时失效。
- 已成交 `TTradeBatch`、BucketLedger 和 ExitPlan 必须由同一运行安全完成。
- 活动退出未完成时不得通过创建新 run 逃避 `DRAINING`。

## 21. 测试与验收

### 21.1 领域测试

- 两个 symbol 使用相同 Tick 序列时状态完全独立。
- 一个 symbol 的乱序、gap、rewarm 不改变其他 symbol 状态。
- 同一快照输入得到字节级稳定的候选排序和原因码。
- 候选不包含现金、可卖量和最终数量。
- Coordinator 对相同输入输出稳定；tie-break 与输入容器顺序无关。
- 行业、并发、总暴露、现金缓冲和单票上限的 ALLOW/CAP/REJECT 正确。
- LightGBM 只能改变合格候选之间的排序，不能使硬门禁失败者入选。

### 21.2 应用与并发测试

- 同周期多个 TradeIntent 先整批持久化，再产生 allocation decision。
- 两个候选竞争同一份现金时只有账户事务允许的数量进入 outbox。
- 两只股票竞争总并发最后一个名额时结果确定且可审计。
- 配置更新、人工确认和市场周期并发时不存在旧候选穿透。
- Coordinator 崩溃恢复不会重复审批或重复路由。
- ACTIVE 模型失败不静默切换，所有新 ENTRY 有稳定阻断原因。
- ExitPlan SELL 在本地调度上优先于新 ENTRY。

### 21.3 执行与回报测试

- BUY 部分成交只激活等量 ExitPlan 和库存置换义务。
- ORDER `FILLED` 先到不提前释放容量。
- REJECT/CANCEL 只有权威零成交证明才释放。
- 迟到成交使账户和计划 fail-closed，不生成第二笔订单。
- 同标的活动批次存在时新候选被拒绝。
- 外部卖出侵占老仓时进入 reconcile，不自动缩量。
- QMT `command_ack` 不推进 ENTRY/EXIT 成交。

### 21.4 回测测试

- 多标的共享现金，不能重复使用同一笔资金。
- 同时事件的稳定排序与实盘 snapshot 规则一致。
- T+1、涨跌停、停牌、费用、滑点和部分成交都走公共 Broker。
- 每次行情后先收敛回报再处理下一事件。
- 训练 cutoff 和 feature availability 无未来泄露。
- RULE_ONLY、SHADOW、ACTIVE 使用固定 artifact 可重复得到相同结果。

### 21.5 上线验收

必须同时满足：

1. 没有任何 SymbolTEngine 读取账户或调用执行服务。
2. 没有第二套现金/库存余额和第二套 SELL FSM。
3. 所有候选均能还原同一个 cycle snapshot。
4. 所有未执行候选都有明确淘汰原因。
5. 最终命令入队重新校验完整 QMT 快照和本地未覆盖义务。
6. 至少完成规定数量的 CANARY 闭环且无重复单、超现金、超老仓或 T+1 违规。
7. 回测和实盘使用同一个 `StrategyBase.step(SNAPSHOT)`、Coordinator 和 scorer 语义。
8. Engine/QMT 断线、乱序回报和重启恢复测试通过。
9. 活动退出计划在所有 ENTRY/模型故障场景下仍保持唯一所有者和可恢复性。

## 22. 最终架构判断

“每标的一 T Engine + 全账户统一协调”方向是合理的，但必须按 QuantX 边界做三项修正：

1. `SymbolTEngine` 只输出机会质量，不能申请具体股数、读取现金或下单。
2. `PortfolioTCoordinator` 只做排名和预算上限，最终合法数量及容量仍由公共执行链决定。
3. 所谓 ExecutionManager、ReservationManager 和 TRound 不应在 QuantX 中复制成第二套真源，
   而应分别映射到现有 `TradeIntentProcessor/OrderSizer/Risk`、
   `AccountCapacityService`、`TTradeBatch + ExitPlan + durable order facts`。

按本设计落地后，系统获得真正的多标的机会竞争和共享资金调度，同时继续保留 QuantX
最重要的安全属性：策略纯净、账户状态唯一、订单可靠投递、QMT 回报为真、T+1 合法、
退出唯一所有者和全链可审计。
