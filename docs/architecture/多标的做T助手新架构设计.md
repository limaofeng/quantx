# QuantX 多标的做 T 助手新架构设计

> 状态：目标架构，待实施<br>
> 版本：2.1<br>
> 日期：2026-09-02<br>
> 适用范围：QuantX Windows Dev、个人单账户、A 股正向做 T

## 1. 结论

新的做 T 助手采用以下结构：

```text
一个账户级 TAssistantConfig
          +
一个可产生实盘 ENTRY 的 LIVE TAssistantExecution
          +
每个 PAPER/BACKTEST 场景一个隔离执行
          +
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

目标架构**不依赖 `StrategyRun`**。`StrategyRun` 的现有实现冻结，只继续服务已经真正属于
“用户启动一个策略实例”的旧功能和迁移中的存量做 T 义务；不再为了做 T、买入/卖出计划或
打板助手向它增加字段、状态、分支和兼容入口。

做 T 自己的稳定配置聚合是 `TAssistantConfig`，一次可恢复执行的业务 owner 是
`TAssistantExecution`。后者承载 PAPER/LIVE/BACKTEST 环境、冻结配置、决策周期、检查点、
审批恢复和回测结果归属，但不充当多标的领域模型，也不保存账户真相。公共交易链统一使用：

```text
ExecutionOwnerRef
  owner_type = T_ASSISTANT_EXECUTION | ENTRY_PLAN | BOARD_ASSISTANT_EXECUTION
             | STRATEGY_RUN | EXIT_PLAN | MANUAL_COMMAND
  owner_id
```

做 T 的 BUY 意图以 `T_ASSISTANT_EXECUTION` 为 owner；真实 BUY 成交建立的退出计划随后成为
SELL 意图的 `EXIT_PLAN` owner，并保留 `source_execution_ref` 回指原做 T 执行。所有来源的
PAPER/LIVE ExitPlan 由独立公共 `ExitPlanRuntime` 调度，source execution 不再为了执行 SELL
长期存活。普通策略仍可使用 `STRATEGY_RUN`，但公共执行、审批、ExitPlan 和回报收敛不再假定
owner 必然是它。

`StrategyBase.step(StrategyInput)` 仍是 LIVE/BACKTEST 共用的纯决策入口；移除的是
`StrategyRun` 所有权依赖，不是策略纯函数边界。`StrategyInput` 和 `TradeIntent` 的目标契约
改为携带 `execution_ref`，由 `TAssistantDecisionRuntime` 调用薄的
`AshareIntradayTAssistantStrategy` 内核。

做 T 是围绕既有持仓完成“先买后卖”的交易过程，不新增第三个仓位桶。账户层以版本化
`TTradingEnvelope` 约束每个标的允许增加的 T 暴露；它由组合层根据持仓策略、账户快照和
本地未覆盖义务计算，绝不进入 `SymbolTEngine` 或模型特征。

LightGBM 作为可插拔的候选排序器引入：

- 规则引擎负责数据健康、形态状态、候选资格和硬门禁；
- LightGBM 只预测候选质量并参与跨标的排序；
- 模型不决定交易合法性、不计算数量、不读取账户现金；
- 先 `SHADOW`，再经走步验证和组合回测后人工晋升为 `ACTIVE`；
- `ACTIVE` 模型不可用时停止新的 ENTRY，不允许静默退回规则排序；
- 已有退出计划不依赖 LightGBM，继续优先执行。

模型能力不另造一套训练系统。做 T 复用 QuantX 已实现的“不可变数据集/训练配置、
DEVELOPMENT 与 FINAL_EVALUATION 隔离、Worker 后台运行、CPU/GPU 资格证据、安全制品、
发布门禁和人工激活”能力，但使用做 T 自己的分钟特征、first-touch 标签、组合回测和
`TModelScore` 契约。训练运行和 `TAssistantExecution` 完全分离；线上只绑定已发布的模型
artifact，不绑定训练 run。

市场资格、模型与规则采用三时间尺度，但不形成多套交易路径：日级点时画像只决定基础可交易
资格，模型只消费**已经结束的 1 分钟 Feature Bar**并预计算跨标的机会质量；V3 规则仍消费
因果有界 Tick，候选进入执行前再用最新已接受 Tick 做确定性重验。第一版使用跨标的共享模型，
模型输出只参与
候选排序，不直接映射仓位、订单参数或退出动作。

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
   `AccountCapacityService`、`ExitPlanRuntime`、durable outbox/inbox 和回报收敛。
9. 做 T 的配置、执行、周期、标的状态和回测结果拥有独立真源，不以 `StrategyRun` 或
   `StrategyRunState.custom_state` 作为目标持久化容器。
10. 与买入/卖出计划和打板助手共享通用 owner、意图受理、执行链、退出计划及模型运维原语，
    同时保持各功能自己的配置、执行和领域状态。

### 2.2 非目标

- 不支持反向做 T；第一阶段只支持先买后卖的正向做 T。
- 不把做 T 创建成 `locked_core/core/swing` 之外的第三个仓位桶。
- 不增加多账户、多租户或账户路由抽象。
- 不让每个标的创建独立 QMT 会话、独立资金池或独立执行服务。
- 不在 Engine、API 或 Worker 中直接导入 `miniqmt` / `xtquant`。
- 不新建做 T 专用 SELL FSM；退出继续由公共 `ExitPlan/ExitPlanRuntime` 管理。
- 不新建第二套现金、库存或订单真源。
- 不使用 Worker RPC 或远程模型服务处理逐 Tick 推理。
- 不让模型直接计算目标仓位、下单数量、订单类型或追价参数。
- 不在第一版引入在线学习、LSTM、Transformer 或端到端 Tick 模型。
- 第一版模型训练只允许人工发起，不做定时自动重训、自动登记或自动发布。
- 不假定 miniQMT 一定提供稳定 Level-2 字段；依赖深度盘口的特征必须受能力清单和质量门禁控制。
- 不在第一阶段引入相关性矩阵、复杂优化器或强化学习。个人账户先使用并发数、总暴露、
  单票和行业集中度等可解释约束。
- 不重写普通策略的 `StrategyRun`；只把公共交易基础设施从“只能由 StrategyRun 拥有”改为
  “可由明确的业务执行 owner 拥有”。
- 不让离线训练 run 充当实盘执行身份，也不因 GPU 训练可用而给 Engine/QMT 增加 GPU 依赖。

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
11. 公共执行事实只依赖稳定 `ExecutionOwnerRef`；`StrategyRun` 只是允许的 owner 类型之一，
    不得再作为做 T 的隐式必填外键。
12. 做 T LIVE 与 BACKTEST 继续调用同一个 `StrategyBase.step(StrategyInput)`、同一个
    `PortfolioTCoordinator` 和同一个 scorer 语义；执行身份由 `TAssistantExecution` 提供。

相关权威文档：

- [系统架构设计](系统架构设计.md)
- [A 股三层协作与执行契约](../trading/contracts/A股三层协作与执行契约.md)
- [A 股交易域数据结构与状态机](../trading/contracts/A股交易域数据结构与状态机.md)
- [A 股自动退出计划与卖出策略契约](../trading/contracts/A股自动退出计划与卖出策略契约.md)
- [持仓做 T 有状态机会引擎 V3 实施规格](../plans/持仓做T有状态机会引擎V3实施规格.md)

## 4. 当前设计评估

当前实现已经具备值得保留的基础：

- 一个账户级动态持仓做 T `StrategyRun`（只作为现状基线，不延续为目标所有者）；
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
| 做 T 配置、执行和恢复绑定 `StrategyRun` | 新功能被策略生命周期、字段和外键牵制 | 新增 `TAssistantExecution`，公共链改用 `ExecutionOwnerRef` |
| 每标的状态集中在 `StrategyRunState.custom_state/instrument_states` | 大 JSON 争用、局部恢复和周期原子性困难 | 独立 `TAssistantSymbolState` + `TDecisionCycle` + material 事件 |
| 行情按标的逐 Tick 进入决策 | 同一时段候选缺少一致比较基准 | 使用带 watermark 的 `TDecisionSnapshot` |
| 候选产生后直接进入意图/审批 | 多只股票同时出现机会时缺少统一排名 | 增加持久意图受理后的 `PortfolioTCoordinator` |
| 账户限制表现为单候选布尔门禁 | 只能回答能不能买，不能回答优先买谁、给多少 | 输出可审计 `TAllocationDecision` |
| 策略状态混有候选、订单、成交和退出摘要 | 状态恢复和真源边界不清晰 | 分离机会 FSM、意图、订单、ExitPlan 和批次投影 |
| `TTradeStatus` 同时描述信号与执行 | 容易形成第二套订单/退出状态机 | 执行状态由权威表派生，策略只保留候选与冷却 |
| 规则分只能做单票判断 | 无法量化跨票相对机会质量 | 可选 LightGBM 排序器，规则仍掌握资格与门禁 |
| 模型输入时点与粒度未冻结 | 形成中 BAR 或逐 Tick 推理容易泄露、抖动且难回放 | 完整 1 分钟评分，Tick 只负责规则和执行重验 |
| 模型输出仅表述为泛化“上涨概率” | 与正向做 T 的成本、路径和止损顺序脱节 | 固定 `p_target_before_stop` 的 `TModelScore` 契约 |
| 只围绕候选结果训练 | 会把 V3 既有筛选偏差学进模型 | 全完整分钟 observation anchors + 成本调整 first-touch 标签 |
| 预先把 LightGBM 当成答案 | 不能证明复杂度带来增量 | 同数据比较 RULE_ONLY、Logistic 和 LightGBM |
| 单票回测可各自假设可用现金 | 多标的结果可能隐含重复使用现金 | 使用单一共享账户的组合回测时间线 |
| 模型训练、登记和线上绑定语义未分层 | 训练成功可能被误解为可以直接实盘 | 复用不可变训练与发布门禁；执行只绑定已发布 artifact |

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
            ├──────────────► ExitPlanRuntime
            │                按 EXIT_PLAN owner 恢复，活动退出优先评估
            │
            ├──────────────► TModelFeatureBarBuilder
            │                只封闭已结束的 1 分钟 Feature Bar
            │                         │
            │                         ▼
            │                InProcessModelScorer
            │                跨标的批量推理、写最新 TModelScoreCache
            ▼
TDecisionSnapshotBuilder
冻结决策时点、watermark、市场/行业上下文和标的快照
            │
            ▼
TAssistantDecisionRuntime
TAssistantExecution 周期调度、检查点和幂等提交
            │
            ▼
AshareIntradayTAssistantStrategy.step(StrategyInput[SNAPSHOT, execution_ref])
无 StrategyRun 的薄纯决策适配器
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
OpportunityScorer
规则分 + 关联同一时点可用的最新 TModelScore
RULE_ONLY / SHADOW / ACTIVE
            │
            ▼
周期状态 + 评分证据 + BUY TradeIntent 原子受理（ALLOCATION_PENDING）
            │
            ▼
PortfolioTCoordinator
排名、额度、并发数、总暴露、行业集中度、机会淘汰
            │
            ▼
TAllocationDecision
ALLOW / CAP / DELAY / REJECT
            │
            ├── CANARY_CONFIRM ► AWAITING_APPROVAL ──► 实时重验
            └── LIVE/AUTO ► 自动受理
                               │
                               ▼
EntryExecutionGate
最新已接受 Tick：ALLOW / DELAY / REJECT
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
AgentReportInbox → ReportProcessor → OwnerRuntimeRouter
Portfolio/BucketLedger/ExitPlan/TTradeBatch 投影收敛
```

物理部署不变化：上述做 T 决策、排序和协调都在唯一活跃 Engine 进程内完成，API
仍只负责协议、会话和 GraphQL，QMT Agent 仍是唯一券商边界，Worker 只承担离线训练、
评估和数据任务。

## 6. 运行与所有权边界

### 6.1 `StrategyRun` 去留评估与最终选择

两种方案的真实代价如下：

| 方案 | 优点 | 缺点 |
|---|---|---|
| 保留做 T `StrategyRun` | 可直接复用现有 `run_id`、检查点、审批、ExitPlan 和回报路由 | 做 T 生命周期被策略生命周期绑住；大 JSON 状态和外键继续扩张；买入/卖出计划、打板助手容易被迫伪装成策略；冻结 `StrategyRun` 无法实现 |
| 独立 `TAssistantExecution` | 业务所有权清晰；配置、周期、标的状态和回测可独立演进；公共执行链可被其他功能复用 | 必须一次性升级意图 owner、ExitPlan 来源、审批恢复、回报路由和回测归属；迁移期要安全排空旧义务 |

最终选择第二种。原因不是信号算法需要一个新名字，而是**运行身份本来就是做 T 业务域的
稳定事实**。让公共链只认识 `StrategyRun`，会让任何非策略功能都承担错误的生命周期语义。

边界冻结如下：

- 不删除普通策略仍在使用的 `StrategyRun` 表和执行器；
- 不再给 `StrategyRun` 增加做 T 专用 cadence、状态、关联表或恢复分支；
- 新做 T 执行不创建 `StrategyRun`，也不把其 id 填入 `strategy_run_id`；
- 存量做 T `StrategyRun` 只允许 `DRAINING` 和回报收敛，不再产生新 ENTRY；
- 旧义务归零后删除做 T 专用桥接代码，不保留长期双 owner 协议。

### 6.2 `TAssistantConfig`：稳定业务配置

`TAssistantConfig` 是一个账户的做 T 助手配置聚合。现有 `t_trade_global_configs` 可原子演进为
它的当前控制/head 表，但必须移除 `strategy_run_id` 依赖。完整结构为：

```text
TAssistantConfigHead                  # t_trade_global_configs
  config_id / account_id / state_version
  enabled
  desired_environment = PAPER | LIVE
  active_config_version_id

TAssistantConfigVersion              # t_assistant_config_versions，append-only
  config_version_id / config_id / version
  config_schema_version / canonical_payload / config_snapshot_hash
  entry_authorization_policy     # CANARY_CONFIRM / AUTO 授权边界
  universe_policy
  symbol_rule_policy
  portfolio_policy
  t_trading_envelope_policy
  entry_execution_gate_policy
  exit_plan_template_policy
  scorer_mode                    # RULE_ONLY / SHADOW / ACTIVE
  model_runtime_binding?
  created_at
```

head 表达当前是否启用、下一活动执行使用 PAPER 还是 LIVE，以及指向哪个 policy 版本；BACKTEST
由独立回测命令选择，不修改 head。版本表保存完整可重放 payload，不能只存 hash 后依赖当前
settings 还原历史。配置是用户意图和版本真源，不表示某个进程正在运行，也不保存行情窗口、
订单、成交或账户余额。任何会改变候选、额度、ExitPlan 或模型排序语义的调整都追加新版本，
再以乐观锁切换 head；payload 只能由 typed command 按 `config_schema_version` 规范化生成，不能
接受任意 JSON；旧候选不得跨版本执行。

### 6.3 `TAssistantExecution`：做 T 自己的可恢复执行身份

`TAssistantExecution` 是做 T 运行时、审批和回测的持久身份：

```text
TAssistantExecution
  execution_id
  config_id / config_version_id / frozen_config_version / config_snapshot_hash
  account_id
  environment = PAPER | LIVE | BACKTEST
  entry_authorization = CANARY_CONFIRM | AUTO
  status = CREATED | WARMING | RUNNING | DRAINING
           | STOPPED | FAILED | RECONCILE_REQUIRED
  policy_version / feature_schema_version
  scorer_mode / model_runtime_binding?
  universe_revision
  last_assigned_cycle_sequence / last_committed_cycle_sequence
  checkpoint_revision
  started_at / drain_requested_at / completed_at
  state_version
```

不能直接用 `config_id` 充当 owner：同一配置可能产生多次 PAPER/BACKTEST，也可能在 LIVE
successor 切换时同时存在 RUNNING 与 DRAINING 执行；只有 execution id 能无歧义归属审批、ENTRY
订单、ExitPlan 来源和 BUY 回报。

它负责：

- 冻结一次执行所用的配置、策略内核、policy、feature schema 和模型 artifact 身份；
- 调度 `StrategyBase.step()` 并给每个 material 决策周期分配稳定序号；
- 串行提交周期状态、material 事件和意图提案；
- 恢复人工审批、DRAINING、下游 ExitPlan 来源关联和自身 BUY 委托/成交回报路由；
- 归属 PAPER/LIVE 报告或共享账户 BACKTEST 版本。

生命周期、successor、RECONCILE、审批恢复和 owner 路由产生的 material 变化同时追加到
`t_assistant_execution_events`；事件以 `(execution_id, event_key)` 幂等，引用原 inbox/report
identity，但不复制或替代券商事实。

它不负责：

- 在股票之间分配现金或计算最终数量；
- 保存真实现金、可卖量、冻结量、委托或成交真相；
- 充当所有标的状态的大 JSON 容器；
- 拥有训练任务或模型注册表。

`STOPPED/FAILED` 只允许在没有未决审批、pending/outbox 和结果未知 BUY 订单后进入。已由真实
BUY fill 激活的 `TTradeBatch/ExitPlan` 是独立下游事实，由公共 `ExitPlanRuntime` 和持久投影继续
收敛，不要求 source execution 保持活动；终态 execution 仍必须可查询，不能硬删除或隐藏下游
义务。运行时自身故障但仍有自身 BUY 义务时只能 `DRAINING` 或 `RECONCILE_REQUIRED`。
`execution_id` 永不复用；一旦被意图、订单、计划或报告引用，终态 execution 只归档不硬删除。

进入 `DRAINING` 的同一事务要阻断新 cycle/approval/EXECUTION_READY 路由，并将尚未形成
pending/outbox 的候选和意图按稳定原因终结；已经创建 durable pending/outbox 或结果未知的命令
继续由原 owner 收敛，不能因 successor 出现而撤销、复制或改挂。

个人单账户下，同一时刻最多有一个能产生真实 ENTRY 的 LIVE 执行。PAPER 与 LIVE 必须使用
隔离的意图、订单、ExitPlan 和投影命名空间，不能共享 pending/outbox；BACKTEST 可以有多个
历史版本，但每个版本都拥有独立 execution id、时钟、Broker 和持久化结果，不接入实盘路由。

`environment`、`entry_authorization` 和模型 `scorer_mode` 是三条独立轴：只有
`environment=LIVE + entry_authorization=AUTO` 才允许免人工确认发送真实订单；
`CANARY_CONFIRM` 表示
LIVE 环境仍需逐笔确认，不再用同一个“LIVE 模式”同时表达券商环境和授权方式。

### 6.4 `ExecutionOwnerRef`：公共链唯一所有权契约

目标域类型为：

```text
ExecutionOwnerType =
  STRATEGY_RUN
  | T_ASSISTANT_EXECUTION
  | ENTRY_PLAN
  | BOARD_ASSISTANT_EXECUTION
  | EXIT_PLAN
  | MANUAL_COMMAND

ExecutionOwnerRef
  owner_type: ExecutionOwnerType
  owner_id: non-empty stable id
```

`ENTRY_PLAN` 和 `BOARD_ASSISTANT_EXECUTION` 分别来自已经完成的独立领域设计；不增加
`MANAGED_PLAN/ASSISTANT/AUTOMATION` 等弱类型 owner。未来新业务 owner 仍需在同一轮代码、契约、
文档和测试中明确加入。

公共契约按以下方式调整：

- 新增执行中性的 `ExecutionEnvironment=PAPER/LIVE/BACKTEST`；`StrategyRunMode` 只保留在普通
  StrategyRun adapter 内，不再渗入做 T domain。
- `StrategyContext.run_id/mode` 改为 `execution_ref/environment`，logger、幂等键和恢复键都使用
  owner ref；普通策略构造 context 时由 adapter 映射原 run。
- `StrategyInput.run_id` 改为强类型 `execution_ref`；`strategy_id` 继续标识纯决策内核。
- `TradeIntent` 移除必填 top-level `run_id`；origin 必须含 `execution_ref` 和 producer identity，
  不能依靠 metadata 中的字符串 owner。普通策略适配器把当前 run 映射为 `STRATEGY_RUN`。
- `TradeIntentRecord`、审批记录、pending、correlation、outbox 和运行事件保存
  `owner_type + owner_id`，不再要求 `strategy_run_id` 非空。
- `TradeCommandPayload` 删除 `strategy_name/strategy_run_id/strategy_order_id`；
  `ExecutionOwnerRef`、producer、intent、batch 和 source execution 保留在服务端 durable
  outbox/correlation，不发送给 QMT Agent。Agent 只依赖 `client_order_id` 和下单所需执行字段，
  回报再由服务端 correlation 找回 owner。这是 breaking Agent contract，目标协议升级为 `1.2`，
  Engine/API/QMT Agent 同步原子切换，不发送 1.1/1.2 双 payload。
- 若 miniQMT API 需要策略名形态的参数，协议只提供不含业务 owner 的稳定 `order_label`；
  `request_metadata` 使用 allowlist，不能把已删除的 run/owner/intent/batch 字段重新塞回去形成旁路。
- ENTRY 成交建立 `ExitPlan` 时保存 `source_execution_ref`。计划产生 SELL 时，SELL 的
  `execution_ref` 为 `EXIT_PLAN/plan_id`，同时保留 source execution、batch 和 role 供回报收敛。
- `TTradeBatch` 保存 `source_execution_ref=T_ASSISTANT_EXECUTION/execution_id`，但仍只是由
  intent/order/trade/ExitPlan 派生的运营投影。
- `OwnerRuntimeRouter` 依据 owner 类型把 ORDER/TRADE/RECONCILE 事件路由到对应执行或计划，
  未知类型、缺失 owner、owner 与相关记录冲突时 fail-closed。

数据库中的多态 owner 字段不伪装成跨表外键；应用层必须校验 owner 类型、目标存在性和状态，
各业务表内部继续使用真实外键。迁移完成后公共链只有这一套强类型 owner 契约。

### 6.5 `TTradeGlobalMonitorService`

全局 Monitor 继续是 Engine 内的配置和动态 Universe 管理器，只负责：

- 一个账户的启停、entry authorization、忽略名单和配置版本；
- 从权威持仓快照生成外部 Universe；
- 向 `TAssistantExecution` 发送 `RECONCILE`，完成标的加入、draining 和移除；
- Universe revision 变化时让受影响标的失效旧候选并 rewarm；
- 配置、policy、schema 或模型 binding 变化时让当前执行停止新 ENTRY，并按冻结新版本创建
  successor，不原地修改 execution；
- 保证 source execution 的未决 BUY 义务完成前只能进入 `DRAINING`；已激活 ExitPlan 独立执行。

它不创建 `StrategyRun`，不运行标的信号，不排名候选，不下单。

运行 Universe 至少是“当前持仓标的 ∪ 当前 execution 未决 BUY intent/order 标的”。全账户已有
batch/ExitPlan 义务通过公共 obligation snapshot 提供给 Coordinator，不要求继续留在 source
registry。日级资格、忽略名单或配置变化只能阻断新 ENTRY，不能隐藏公共交易义务。

### 6.6 `SymbolTEngineRegistry` 与独立标的状态

Registry 在一个 `TAssistantExecution` 内按 `instrument_code` 管理逻辑上的一标的一引擎：

```text
SymbolTEngineRegistry
  000001.SZ -> SymbolTEngine(TAssistantSymbolState A)
  002594.SZ -> SymbolTEngine(TAssistantSymbolState B)
  688552.SH -> SymbolTEngine(TAssistantSymbolState C)
```

每个引擎的输入、窗口、FSM、候选、版本和冷却完全隔离。material 状态检查点写入独立
`TAssistantSymbolState(execution_id, instrument_code, revision)`，而不是写回一个
`StrategyRunState.instrument_states` 大对象。Registry 只接受 Universe Provider 给出的标的，
不自行选股。

Registry 生命周期：

- `WARMING`：新加入或连续性丢失，构造完整因果窗口；
- `ACTIVE`：数据健康，允许产生机会；
- `DRAINING`：不产生新 ENTRY，但保留审计并收敛该 execution 自身未决 BUY 义务；
- `RETIRED`：没有候选、未决 BUY、工作买单或结果未知 owner 事件后即可清理热状态；历史 batch 和
  ExitPlan 仍可保留 source ref，不反向阻止 symbol state 退休。

### 6.7 `SymbolTEngine`

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
- `TTradingEnvelope`、其他股票候选或组合排名；
- 真实可卖量、冻结量和当日买入量；
- 最终下单数量；
- 订单、成交或退出计划的权威状态。

### 6.8 `PortfolioTCoordinator`

Coordinator 是 Engine/application 层的账户级纯协调器。它读取一份不可变的
`PortfolioTDecisionSnapshot`，回答：

> 当前同一决策周期内的有效候选，哪些可以进入审批或执行，各自最多获得多少预算？

它负责：

- 候选过滤与确定性排序；
- T 总资金池、`TTradingEnvelope` 和现金缓冲；
- 最大活动批次数、单票 T 上限、总 T 暴露上限和行业集中度；
- 同标的单活动批次；
- 已有待审批、待下单、活动 ENTRY/EXIT 和保护义务；
- 输出每个候选的排名、动作、预算上限和原因。

它不负责：

- 生成新的买卖方向；
- 修改候选形态状态；
- 计算最终股数；
- 把估算的现金、库存或 envelope 当作最终入队凭证；
- 直接创建 QMT 命令。

### 6.9 `TTradingEnvelope`：账户层做 T 边界，不是新仓位桶

`TTradingEnvelope` 是绑定账户快照的不可变规划值，由 application/portfolio 层构建，只交给
Coordinator、OrderSizer 和容量复核：

```text
TTradingEnvelope
  envelope_id / execution_id / instrument_code
  as_of / account_snapshot_id / input_fingerprint
  config_version / envelope_policy_version
  observed_position_projection
    locked_core / core / swing
  protected_old_position_floor
  max_incremental_t_amount
  planning_entry_volume_ceiling
  planning_replaceable_old_volume_ceiling
  positive_t_eligible
  reason_codes[]
```

其中 volume 和 amount 都只是本次快照下的规划上限，不是可卖量真源，也不形成预占。最终数量
仍由最新完整账户快照、本地未覆盖义务、OrderSizer、Risk 和 `AccountCapacityService` 在账户锁
内重算。`input_fingerprint` 发生变化时必须产生新 envelope 和新 allocation，不能覆盖旧证据。
`protected_old_position_floor` 至少覆盖全部 `locked_core` 和配置要求保留的核心仓，
`planning_replaceable_old_volume_ceiling` 只能从昨日可卖老仓扣除该 floor 及已有保护义务后得到。

做 T 不依赖未来的买入/卖出计划功能才能运行。没有外部持仓计划时，envelope 只按冻结的
`t_trading_envelope_policy`、当前三层持仓和账户义务保守计算；若将来存在版本化持仓边界，
portfolio 层可把它作为一个输入进一步收紧保护底仓，但不能放宽做 T 或公共风控上限，也不能
把该计划变成 `TAssistantExecution` 的 owner。

资格分成两层：

- `TTradabilityProfile`：账户无关、point-in-time 的日级流动性、振幅、价差、历史覆盖和数据能力；
  可供 Universe 和 Symbol 层读取，但只决定新 ENTRY 资格，不移除已有交易义务。
- `TTradingEnvelope`：账户相关的持仓、保护底仓、可规划暴露和本地义务；只能在组合/执行层读取。

第一阶段只支持正向 T，不在 envelope 中预埋反向 T、融券或负目标仓位字段。需要反向 T 时应
另行定义库存、方向、T+1 和退出契约。

### 6.10 公共执行组件

以下能力继续复用，不为做 T 复制：

| 能力 | 权威组件 |
|---|---|
| 纯决策入口 | `StrategyBase.step(StrategyInput)`，输入携带 `ExecutionOwnerRef` |
| 合法数量和整手 | `OrderSizer` |
| 交易时段、停牌、涨跌停、T+1、订单风控 | `RiskChecker` / 交易域 |
| 最终现金和老仓容量 | `AccountCapacityService` |
| 订单持久化和可靠投递 | `PendingTradeOrder` / `TradeCommandOutbox` |
| 券商下单和本地保护 | QMT Agent |
| 委托与成交事实 | QMT Agent 报告 + `AgentReportInbox` |
| owner 路由与恢复 | `OwnerRuntimeRouter` / 强类型 owner repository registry |
| 自动退出 | `ExitPlanRuntime` / `auto_exit_plans`；`ExitPlanBook` 只作纯规则执行器 |
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
  execution_ref = T_ASSISTANT_EXECUTION / execution_id
  cycle_id
  decision_time
  trade_date
  stream_id
  continuity_generation
  fence_sequence
  universe_revision
  config_version / config_snapshot_hash
  policy_version
  feature_schema_version
  scorer_mode / model_runtime_binding_hash? / model_authorization_revision?
  model_id? / model_version? / artifact_sha256?
  model_score_cache_revision? / model_score_visibility_watermark?
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
- 只读的标的画像和参数版本；
- 当前 cache view 下该标的最新 `visible_model_outcome_ref?`（score id/status/revision）；

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
8. snapshot 必须绑定当前 `TAssistantExecution`、冻结配置哈希和 owner；任何不一致都在进入
   `StrategyBase.step()` 前被 builder 拒绝。
9. scorer 与 snapshot 并发时，以冻结的 `model_score_cache_revision` 为可见边界；快照创建后
   才完成的分数只能进入下一 cycle，不能改变当前 cycle 的候选排名。

### 7.4 决策节奏

实盘以已接受的 `WholeQuoteHub` 完整批次为基础构造周期。若进入背压状态：

- 不允许无限排队后使用过时行情产生 ENTRY；
- 只可把尚未计算、连续且没有 generation 变化的 ENTRY fence 合并到最新完整 fence；
- 合并跨度超过策略窗口允许值时视为连续性丢失并 rewarm；
- 不得用 UI 的 latest-only 行情队列承载交易决策。

### 7.5 三时间尺度数据路径

规则、模型和执行使用同一条已接受行情流，但各自采用适合其职责的时间粒度：

| 层 | 输入粒度 | 职责 | 禁止行为 |
|---|---|---|---|
| 基础市场资格 | 上一可用交易日及当时已知的 point-in-time 日级画像 | 流动性、振幅、价差、历史覆盖和数据能力资格 | 读取账户持仓/现金，或用今天才知道的分类回填历史 |
| V3 标的规则 | 已接受 Tick + 点时市场/行业上下文 | 窗口、FSM、硬门禁、候选 | 等待未来 Tick 或读取账户 |
| 模型评分 | 已封闭的 1 分钟 Feature Bar | 估计候选在冻结 horizon 内的相对质量 | 使用正在形成的分钟或逐 Tick 远程推理 |
| 执行重验 | 最新已接受 Tick + 候选/模型绑定 | 判断候选是否仍可进入公共执行链 | 读取账户、产生新方向、直接定量或替代风控 |

`TModelFeatureBarBuilder` 从 `WholeQuoteHub` 的连续 Tick 构造专用 Feature Bar。它不是
普通 OHLCV 的简单别名，还可包含该分钟内基于实际可用字段计算的 spread、深度、成交活跃度、
价格路径和数据覆盖率统计。只有 watermark 已越过分钟结束、对应 generation 连续且关键字段
质量合格时，Feature Bar 才能从 `FORMING` 原子转换为 `COMPLETE`。`FORMING`、未来补齐或
使用后验修正的数据一律不能进入在线评分。

完整 Bar 至少携带以下可重放身份：

```text
TModelFeatureBar
  feature_bar_id
  instrument_code
  interval_start / interval_end / market_session
  stream_id / continuity_generation / source_fence_range
  feature_schema_version / capability_manifest_version
  feature_values / feature_coverage / feature_vector_hash
  market_context_as_of / sector_context_as_of
  status = COMPLETE
```

滚动特征只能由当前及更早的完整 Bar 构造，并额外生成 `feature_window_hash`；重启恢复或回测
若不能重建相同 hash，则对应分数无效。

模型在每个完整分钟结束后，对当时 Universe 中满足基础数据质量的标的做一次进程内批量推理，
把最新结果按一个原子 revision 写入可重建的 `TModelScoreCache`。每次批量发布产生稳定
`score_cache_revision`、输入 Feature Bar manifest hash 和可见 score id 集合；batch manifest 对
每个计划评分的标的都记录 `VALID` 或明确的 `UNAVAILABLE/ERROR`，不能以静默缺行伪装成完整
批次。系统级推理失败不发布新 revision。任一候选只允许关联 snapshot 已冻结且满足以下条件的分数：

```text
score.model_as_of <= opportunity.observed_at
score.source_bar_end <= opportunity.observed_at
opportunity.observed_at - score.model_as_of <= model_score_max_age
score.feature_schema_version == snapshot.feature_schema_version
score.model_version == snapshot.model_version
score.cache_revision <= snapshot.model_score_cache_revision
score.artifact_sha256 == snapshot.artifact_sha256
score.model_authorization_revision == snapshot.model_authorization_revision
score.score_id == snapshot.symbol[instrument].visible_model_outcome_ref.score_id
```

候选只能读取该标的在冻结 cache view 下的**最新可见 outcome**。如果较新的批次明确记录
`UNAVAILABLE/ERROR`，不得回退到更早的 VALID score；SHADOW 记录缺失，ACTIVE 按稳定原因阻断。

市场、行业和画像可以使用更慢的时点数据，但必须携带独立 `as_of`、版本和 freshness，不能
通过分钟聚合把过期公共上下文伪装成新鲜数据。V3 规则不等待下一分钟模型更新；模型分数陈旧时，
`SHADOW` 只记录不可用，`ACTIVE` 则阻断该候选的新 ENTRY。

### 7.6 `TDecisionCycle` 与 material 原子提交

`cycle_id` 不是日志标签，而是稳定、可幂等恢复的决策身份；`cycle_sequence` 才表达 execution
内的单调顺序：

```text
TDecisionCycle
  cycle_id / execution_id / cycle_sequence
  snapshot_hash / fence_range
  score_cache_revision / model_runtime_binding_hash
  evaluated_symbol_count / material_symbol_count / proposed_intent_count
  status = PREPARED | COMMITTED | ABORTED
  input_manifest_hash / output_manifest_hash
  committed_at / abort_reason
```

`cycle_id` 由 execution、fence/source identity、冻结配置/binding 和 canonical decision payload
hash 构成；该 payload hash 明确排除 cycle id、trace id 等自引用/诊断字段。完全相同输入的重试
命中同一 cycle，任一事实水位变化都产生新 id。

发现 material 输出后，由 execution 的 durable allocator 在一个小事务中分配一次
`cycle_sequence` 并插入带 input manifest 的 `PREPARED` cycle；序号允许因回滚或崩溃出现空洞，
但绝不复用。后续 material 事务把同一行转成 `COMMITTED`。恢复时超时的 `PREPARED` 行转为
`ABORTED`，不能拿同一序号或 cycle id 配另一份 snapshot。

无 material 变化的普通行情周期只保留有界内存诊断，不强制逐 Tick 写库。只要发生候选创建、
候选状态转换、material 数据健康转换或 TradeIntent 提案，就必须在一个数据库事务中：

1. 以预期 revision 校验并更新本周期发生 material 变化的 `TAssistantSymbolState`；
2. 追加机会/状态证据和对应 `TModelScore` 绑定；
3. 整批受理该周期的 `TradeIntent`；
4. 条件更新 PREPARED cycle 的输出 manifest 和 `COMMITTED` 事实；
5. 推进 `TAssistantExecution.last_committed_cycle_sequence`。

任一步失败都不得提交任何标的状态或意图。尤其禁止先把 candidate 状态推进为“已提案”，崩溃后
却没有可恢复的 intent。revision 冲突或事实水位变化时，旧 cycle 记为 `ABORTED`，使用新 cycle、
新 snapshot 和新 fingerprint 重算，不能覆盖原证据。

## 8. 每周期决策流程

### 8.1 顺序

每个周期固定按以下顺序执行：

```text
1. 冻结 TDecisionSnapshot
2. 公共 ExitPlanRuntime 对活动 ExitPlan 做优先评估
3. TAssistantDecisionRuntime 以 execution_ref 调用唯一 StrategyBase.step(SNAPSHOT)
4. step 内在隔离状态副本上计算各 SymbolTEngine
5. 按 instrument_code 确定性合并状态和候选
6. OpportunityScorer 关联规则分与 snapshot 可见的最新 TModelScore
7. 原子提交 material 标的状态、评分证据与 BUY TradeIntent 提案
8. PortfolioTCoordinator 生成 TAllocationDecision
9. 淘汰或延迟的意图写终态原因
10. 选中意图进入 CANARY_CONFIRM 审批或 LIVE/AUTO 自动路由
11. EntryExecutionGate 用最新已接受 Tick 做最后机会重验
12. OrderSizer、Risk、AccountCapacityService 最终复核
13. 原子创建 pending/correlation/outbox 后才能投递
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

为了继续遵守“策略只输出 `TradeIntent[]` 和算法状态补丁”，薄策略适配器会把通过标的内硬门禁的
`TOpportunity` 映射为标准 BUY `TradeIntent` 提案。`target_amount` 只是冻结配置中的
单机会申请上限，不是实际分配，也不承诺可以成交。候选完整证据放在结构化 metadata
和机会评估记录中。意图的强类型 origin 为：

```text
execution_ref = T_ASSISTANT_EXECUTION / execution_id
producer_id = ashare-intraday-t-assistant
opportunity_id / candidate_id / cycle_id
```

### 8.4 意图先持久化再协调

所有策略输出的候选意图必须先经过统一 `record_trade_intents` 整批持久化，初始状态为
`ALLOCATION_PENDING`。若冻结的 ACTIVE binding 下模型分数不可用，意图与证据仍在同一 cycle
事务中持久化，但直接以 `REJECTED/MODEL_*` 终结，不进入 Coordinator。Coordinator 不处理只
存在于内存或日志中的候选，也不处理 scorer 已阻断的意图。

意图生命周期不复制 `ALLOW/CAP/DELAY/REJECT`。这些是一次组合裁决的动作，属于不可变的
`TAllocationDecision`，不是订单或意图状态。目标生命周期为：

```text
ALLOCATION_PENDING
  ├─ ALLOW/CAP  -> AWAITING_APPROVAL | EXECUTION_READY
  ├─ DELAY      -> ALLOCATION_PENDING（旧 decision 终结，新 attempt 才能重验）
  └─ REJECT     -> REJECTED

AWAITING_APPROVAL -> EXECUTION_READY | EXPIRED | REJECTED
EXECUTION_READY   -> EXECUTION_PENDING | EXPIRED | REJECTED
```

每次 allocation attempt 保存新的 `allocation_decision_id`、决策周期、排名、额度、账户快照身份、
envelope 指纹、义务 watermark 和原因码。`CAP` 只改变 `allocated_amount_cap`。账户事实、模型分数
或候选绑定变化时必须新建 decision，不能原地改写旧 decision。
未选中候选不对用户暴露为可确认订单，也不能进入 OrderSizer。

### 8.5 必须原子调整的公共契约

当前代码只有 `BAR/TICK/ORDER/TRADE/RECONCILE` cadence，目标架构需要新增明确的
`StrategyCadence.SNAPSHOT`，不能把账户级冻结快照伪装成某一只股票的普通 `TICK`。
该 cadence 只用于外部 Universe 已经确定的账户级动态标的策略：

- `StrategyInput.execution_ref` 固定为当前 `TAssistantExecution`，不再要求 `run_id`；
- `StrategyInput.market_data` 在 Python 类型和运行时 validator 中都必须是
  `TDecisionSnapshot`，不能接收任意 dict/`Any` 后再猜字段；
- 策略元数据固定 `INSTRUMENT_SCOPE=MULTI`、`INSTRUMENT_UNIVERSE_MODE=ACCOUNT_HOLDINGS`，
  `StrategyInput.instrument_code=None`；具体标的只出现在 snapshot item 和输出
  `TradeIntent.instrument_code` 中，不再用空字符串表达账户级输入；
- `StrategyInput.market_data_context` 只描述本 cycle 的 stream/generation/fence 健康，
  每票 source identity 和 freshness 保留在各自 snapshot item；
- `StrategyInput` 不携带 `PortfolioTDecisionSnapshot`，保证策略不读取账户；
- 一个 step 返回该 cycle 的全部候选 TradeIntent 和按标的拆分的
  `SymbolRuntimeStatePatch(instrument_code, expected_revision, patch)`；执行级 patch 只能保存
  账户无关的算法水位，不能再写一个完整 `instrument_states` map；
- `StrategyOutput` 为 MULTI/SNAPSHOT 增加 typed `symbol_state_patches[]`；现有
  `runtime_state_patch` 只表达 execution 级算法水位，二者都继续经过禁止账户真相的递归校验；
- 做 T 内核不得通过 `StrategyStateProxy` callback 把状态旁路写入 `StrategyRunState`；runtime
  从 `TAssistantSymbolState` 构造只读输入，只应用 step 明确返回的 patch；
- 固定标的策略继续使用原有 `TICK/BAR`，不改变一实例一标的约束。

同时需要原子更新：

- `StrategyInput`、`TradeIntentOrigin` 与公共关联表的 `ExecutionOwnerRef`；
- `TradeIntentRecord` 的 `ALLOCATION_PENDING` 生命周期，`CAP` 只留在 allocation decision；
- `TAllocationDecision` 持久化和 GraphQL 只读投影；
- `TAssistantExecution/TAssistantSymbolState/TDecisionCycle` 的条件写入、整批意图受理、
  协调恢复与 TTL 终结；
- LIVE/BACKTEST 的 snapshot scheduler；
- 相应 contracts、客户端类型、文档和测试。

这些是一次权威协议升级，不保留“逐票直接审批”和“snapshot 组合协调”两条长期入口。

## 9. 组合协调与资金分配

### 9.1 输入快照

`PortfolioTDecisionSnapshot` 由 Engine/application 层从权威投影构建，至少包含：

- `execution_ref`、cycle id、配置/策略/模型绑定和 snapshot hash；
- 账户执行控制、kill switch 和 reconcile 状态；
- 最新合法完整账户快照 id/hash/as_of；
- 本周期每标的 `TTradingEnvelope` 及其 input fingerprint；
- 可用资金及快照尚未覆盖的本地 BUY 义务；
- 每标的老仓可卖量及未覆盖的订单、T 批次、保护计划占用；
- 活动 `TTradeBatch`、待审批和待下单意图；
- 当前 T 暴露、当日已实现/未实现 T 损益和可配置熔断；
- 主行业映射和已有 T 暴露；
- 全局、单票和行业上限；
- 现金缓冲和最大并发批次数。

快照还必须固化 `local_obligation_watermark`，覆盖未被账户快照观察到的 pending、outbox、
活动批次、审批和 ExitPlan 义务。Coordinator 重试时若账户 snapshot id、envelope fingerprint 或
obligation watermark 任一变化，必须建立新的 allocation attempt，不能复用旧计算结果。

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
  execution_id / allocation_attempt
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
  account_snapshot_hash
  t_trading_envelope_id / envelope_input_fingerprint
  local_obligation_watermark
  portfolio_input_fingerprint
  portfolio_policy_version
  scorer_mode / model_version
  model_score_id / model_as_of
  blockers[]
  created_at
```

`allocated_amount_cap` 是 OrderSizer 的上限输入，不是冻结资金。账户事实可能在下一毫秒
变化，因此最终命令入队时必须重新复核。decision 一经写入不可修改；同一 intent 的后续重验
使用递增 `allocation_attempt` 和新 decision id。

同一 cycle/attempt 的全部 decisions 与对应 intent 状态转换在一个事务中提交，并条件校验
`portfolio_input_fingerprint` 和 intent version。任何冲突都不得留下“前几个已 ALLOW、后几个
没有 decision”的半批结果；旧 attempt 终结后以新 portfolio snapshot 重新计算。

### 9.4 组合约束优先级

第一版固定优先级从高到低为：

1. 账户隔离、kill switch、reconcile 和数据完整性；
2. 已有退出、撤单和隔离修复义务；
3. 同标的活动批次和库存保护冲突；
4. 总 T 暴露和最大并发数；
5. 单票和行业集中度；
6. 现金缓冲；
7. 排名和额度优化。

行业采用唯一主行业做硬约束。概念板块存在重叠，第一阶段只做解释或软惩罚，不把多个
概念额度简单相加，以免重复计算风险。

### 9.5 LIVE 最终准入必须保持排名顺序

Coordinator 的冻结分配不等于最终容量提交。一个 cycle 中被 `ALLOW/CAP` 的 LIVE 候选必须按
冻结排序键进入一个有序批次事务：

1. 先按 rank 顺序完成 Gate，失效候选留下明确终态；不在批内用新分数或新候选补位；
2. 一次锁定账户控制、相关持仓和义务，校验 account snapshot、全部 envelope 与 obligation
   watermark 仍等于 allocation 输入；
3. 在同一事务内按 rank 顺序执行 OrderSizer/Risk/Capacity；每个已受理候选产生的本地义务立即
   进入本事务的后续候选计算，但这只是事务内计算，不是第二套余额真源；
4. 整批创建带 `admission_batch_id/rank` 的 intent transition、pending/correlation/outbox 和必要
   batch 投影后一次提交；同账户 outbox 按该顺序投递，但不承诺券商成交顺序；
5. 外部事实或版本冲突使整批回滚，并用新 snapshot/allocation attempt 重算；
6. 不能把候选扔给互相独立的异步任务，让抢锁顺序决定赢家；幂等重放命中原批次结果。

CANARY_CONFIRM 等待人工期间不长期占用资金。确认后必须使用最新账户事实、envelope、模型绑定和组合
约束创建新 allocation attempt；不能把数分钟前的排名或额度当成最终准入凭证。

## 10. 审批、OrderSizer 与原子容量预占

### 10.1 CANARY_CONFIRM

Coordinator 选中的候选进入 `AWAITING_APPROVAL`，但不提前占用真实资金或老仓库存。
原因是人工确认可能延迟，长期预占会阻塞其他机会。

确认时必须重新校验：

- execution ref、candidate id/fingerprint/state/config/policy/model 版本；
- TTL 和最新规则硬门禁；
- 最新 quote、允许偏离和行情连续性；
- 最新账户执行控制和完整快照；
- 最新组合额度、同标的活动批次和保护义务；
- OrderSizer、A 股风控和最终容量。

确认不是成交承诺。确认后仍可能被 `CAP/REJECT/RECONCILE_REQUIRED`。

### 10.2 LIVE/AUTO

`environment=LIVE + entry_authorization=AUTO` 只跳过人工点击，不跳过任何候选、组合、风控、
容量和最终入队复核。
自动授权必须精确绑定 `TAssistantExecution`、配置、决策内核/模型版本、账户执行窗口和额度。

### 10.3 `EntryExecutionGate`

CANARY_CONFIRM 确认或 LIVE/AUTO 自动受理后、进入 `TradeIntentProcessor` 前，统一执行一个
轻量且确定性的
`EntryExecutionGate`。它使用最新已接受 Tick 判断“原候选现在是否仍值得送入公共执行链”，
只输出：

```text
ALLOW | DELAY | REJECT
```

第一版检查项包括：

- candidate/intent TTL、fingerprint、config/policy/model 绑定是否仍有效；
- quote 是否新鲜、generation 是否连续、相对候选参考价是否超出允许偏离；
- spread 是否异常扩大，当前盘口是否仍满足冻结的最低可执行性条件；
- `ACTIVE` 下绑定的 `TModelScore` 在当前时点是否仍新鲜且 schema/model 版本一致；
- 只有能力清单声明字段存在且质量合格时，才使用深度或 imbalance 条件。

Gate 不能改变 BUY/SELL 方向，不能重新生成候选，不能输出最终数量、订单类型或追价价格，
也不能替代 OrderSizer、RiskChecker 和 `AccountCapacityService`。`DELAY` 只能持续到原 intent
TTL；收到后续行情后必须从候选有效性、模型分数和组合额度重新走完整路径，不能在 Gate 内
循环追价。若候选形成后出现了新的完整分钟分数，不得把新分数静默替换进旧 allocation；必须
生成新的 scorer/allocation 证据或使旧候选失效。

每个数据源版本都要冻结 `MarketDataCapabilityManifest`，区分 required/optional 字段。required
字段缺失时按稳定原因码 `DELAY/REJECT`；optional 字段缺失时只停用显式依赖它的规则，禁止
伪造零深度、零 imbalance 或用未来数据补齐。

### 10.4 不新增第二套 Reservation 真源

参考设计中的 `Cash & Inventory Reservation` 映射到 QuantX 已有
`AccountCapacityService` 和 durable pending/outbox/保护义务，不新增一个会与账户快照
竞争的独立余额账本。

最终 ENTRY 入队批次事务必须：

1. 锁定 `AccountExecutionControl`，再按现有统一锁序读取标的持仓和相关义务；
2. 重新验证协议 1.1 完整账户快照、新鲜度、hash 和分区完整性；
3. 扣除该快照尚未观察到的本地 pending/outbox、活动批次和退出保护；
4. 由 OrderSizer 得到最终合法整手数量；
5. 校验 BUY 最坏价格和费用下的资金上限；
6. 校验同等数量、未被占用的昨日老仓可卖库存；
7. 在同一事务创建或更新带 `ExecutionOwnerRef` 的 TradeIntent、PendingTradeOrder、Correlation、
   `TTradeBatch` 运营投影和 `TradeCommandOutbox`；
8. 事务提交后才允许 API Hub 投递。

这个“预占”是 QuantX 对本地未被券商快照覆盖义务的持久化扣减，不伪装成券商冻结。

### 10.5 释放规则

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
- `PendingTradeOrder` 和公共 `OrderCorrelation`；
- QMT 委托与成交回报；
- `BucketLedger` 和 `T1SubstitutionPlan`；
- `auto_exit_plans`；
- `TTradeBatch` 运营投影。

因此目标架构把 `TTradeBatch` 明确定义为一轮正向做 T 的可重建运营投影，而不是新增
另一个拥有订单、成交和退出状态的 `TRound` 聚合。UI 可以把它展示为“做 T 轮次”。

### 11.2 生命周期

```text
CANDIDATE
  -> ALLOCATION_PENDING
  -> AWAITING_APPROVAL / EXECUTION_READY
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
每个 batch 保存 `source_execution_ref=T_ASSISTANT_EXECUTION/execution_id`；它不需要也不得伪造
`strategy_run_id`。

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
4. 公共 `ExitPlanRuntime` 按 `EXIT_PLAN/plan_id` 恢复并评估活动计划；
   `source_execution_ref` 只作来源审计、冲突检查和结果投影。
5. 命中规则后产生标准 SELL `TradeIntent`，继续经过 OrderSizer、风控和 Broker。
6. SELL 的 owner 固定为 `EXIT_PLAN/plan_id`，并保留 `source_execution_ref`、batch 和 role
   用于回报收敛。
7. `TAssistantExecution` 停止新 ENTRY 时进入 `DRAINING`，自身未决 BUY 义务归零后可以
   `STOPPED`；活动 ExitPlan 由自身 owner 继续，任何 successor 都不得接管或复制该计划。

目标做 T 内核不再依赖策略类上的 `OWNS_RUNTIME_EXIT_PLAN_BOOK`，也不在 PAPER/LIVE source
execution 内保存 ExitPlan 热缓存。`ExitPlanRuntime` 调用公共纯退出策略，以
`ExecutionOwnerRef(EXIT_PLAN, plan_id)` 生成 SELL；普通 StrategyRun 的真实 BUY fill 同样只作为
source ref，不再形成另一条退出执行路径。

LightGBM 不参与退出触发。已有风险保护不能因模型、候选池、Coordinator 或训练服务
异常而停止。

## 13. 并发、串行和优先级

### 13.1 可以并行的部分

- 不同 `SymbolTEngine` 的纯特征和 FSM reduction；
- `InProcessModelScorer` 对同一完整分钟 Universe 的向量化推理；
- 不改变状态的诊断和 UI 投影构建。

### 13.2 必须串行的部分

- 同一 symbol 的 source identity 消费；
- 一个决策周期的状态归并和 TradeIntent 整批受理；
- 一个账户的组合分配提交；
- 审批、配置变更和候选失效；
- 账户容量最终复核、pending/outbox 创建；
- 同一 `ExecutionOwnerRef` 的 runtime event 应用；
- QMT 回报对 Portfolio、BucketLedger、ExitPlan 和批次投影的收敛。

### 13.3 账户任务优先级

同一账户本地调度固定使用：

1. 隔离、对账、撤单和紧急停止；
2. 活动 ExitPlan 的 SELL；
3. 已批准 ENTRY 的最终容量复核和入队；
4. 新候选的组合协调；
5. 配置、Universe 和普通检查点。

进程内 `t_trade_account_coordination_lock` 用于把配置与审批线性化，但不是持久化真源。
最终交易安全仍依赖数据库事务、账户控制行锁和既定全链锁序。

## 14. 模型评分设计（LightGBM 主候选）

### 14.1 定位

模型的目标不是替代 V3 规则，而是提高“多个合格候选中谁更值得优先使用有限资金”的
排序质量。LightGBM 是第一版首选的非线性候选，因为它适合中等规模表格特征、推理快且
容易固化；它不是预先指定的永久 champion，必须在同一数据契约下稳定胜过规则排序和
Logistic Regression 才能晋升。

第一版的权威预测语义是：正向做 T ENTRY 后，在冻结 horizon 内，成本调整后的目标 barrier
先于下行 barrier 被触达的校准概率；它不是没有执行含义的 `p_up` 或通用涨跌方向。模型只
比较已经通过规则资格的候选质量，不改变正向做 T 的方向定义。

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

在线模型输入固定为已封闭的 1 分钟 `TModelFeatureBar`，特征必须在
`source_bar_end/model_as_of` 时可得，并固定 `feature_schema_version`、字段顺序、缺失值
语义和归一化参数。原始 Tick 只用于构造分钟内统计，不直接触发一次模型推理。可使用：

- 价格收益、路径回撤/反弹、速度、加速度、VWAP 偏离和 realized volatility；
- 分钟内成交活跃度、spread 分布、盘口变化和数据覆盖率；
- 数据健康、窗口完整度和可用字段 bitmap；
- 标的点时历史流动性和当前可执行性；
- 大盘、行业和概念的时点环境；
- 日内时间、距午休/收盘时间；
- 只使用决策前数据计算的参考画像。

跨标的输入优先使用无量纲、可比较的收益、波动、流动性分位数或相对量，不把不同价格和
成交规模直接混在一起。任何 spread/depth/imbalance 特征都必须受
`MarketDataCapabilityManifest` 控制；数据源不提供或质量不足时，应使用不含该字段的新
schema/model 版本，不能用常数伪造“正常盘口”。

禁止特征：

- 未来价格、正在形成或未来才完整的 BAR、未来复权因子；
- 最终是否成交、最终成交价或实际分配金额；
- 当前账户可用现金、排序名次和 active batch 压力；
- 人工是否点击确认；
- 第一版中的原始 `instrument_code`、名称或可让模型直接记忆标的身份的字段；
- 训练期之后才产生的模型、策略或画像版本信息。

排除账户特征可以避免模型把历史资金分配策略学成“机会质量”，并使同一分数可跨账户
状态和回测场景比较。

### 14.4 `TModelScore` 契约

模型输出必须是稳定、可审计的值对象，不能只在 metadata 中塞一个含义不明的浮点数：

```text
TModelScore
  score_id
  instrument_code
  score_cache_revision / model_runtime_binding_hash
  model_as_of
  source_feature_bar_id / source_bar_end
  feature_window_hash
  horizon_seconds
  p_target_before_stop
  score_status
  feature_coverage
  out_of_distribution_status
  feature_schema_version
  label_spec_version
  model_id / model_version / model_type
  artifact_manifest_sha256
  model_authorization_revision
  calibration_version
```

`p_target_before_stop` 是第一版唯一存在、可参与排序的线上模型语义。预期净 edge、MFE 和 MAE
只作为离线标签/诊断，不预建 nullable runtime 字段。将来只有在独立模型头和真实使用场景通过
评审后才升级契约，不能把同一字段在不同版本中改成另一种含义。
不定义笼统 `confidence`；概率校准、特征覆盖率、分布外状态和 freshness 分开表达。

`source_bar_end` 表示特征数据截止时点，`model_as_of` 表示分数完成计算并对决策路径可见的
时点；二者不能混为一个时间戳，否则回测会忽略真实推理延迟。

`score_status` 只描述推理是否成功及特征是否足够；freshness 和 schema compatibility 在候选
关联时计算。`out_of_distribution_status` 独立表达 `IN_DISTRIBUTION/WARN/BLOCK`，第一版
ACTIVE 只接受 `IN_DISTRIBUTION`，不能把 OOD 警告折成一个看似精确的低概率。

`TModelScore` 不能携带账户金额、目标仓位、订单价格或 BUY/SELL 动作。Coordinator 只接收
`score_status=VALID` 且满足第 7.5 节时点约束的分数；其他状态保留审计，但不得参与 ACTIVE
排序。

### 14.5 标签与样本

当前 `t_trade_candidate_outcomes` 适合评估已形成候选的 60/300/900 秒表现，但只用
候选或已执行样本训练会产生选择偏差。训练数据必须覆盖所有满足基础数据质量的、按
固定规则抽样的 observation anchor，包括没有形成候选的负样本。

第一版 observation anchor 固定为每个合格 `COMPLETE` Feature Bar 的可用时点，与在线评分
一一对应；不把每个 Tick 当成独立训练样本。候选在 `model_score_max_age` 内关联最近 anchor
的分数，candidate outcome 仅用于事后比较规则筛选效果，不能反过来定义训练样本集合。

每个模型版本必须冻结：

- anchor 抽样规则；
- 唯一主预测 horizon；
- ENTRY 可执行价格、目标 barrier、下行 barrier 和 barrier 触达顺序；
- 手续费、印花税、过户费、spread 和滑点模型；
- 未成交、停牌、涨跌停、午休/收盘和数据缺失的标签处理；
- 重叠样本去重或权重规则。

第一阶段主标签使用成本调整的 first-touch/barrier 定义：从 anchor 后第一个可执行 BUY
价格开始，目标 barrier 至少覆盖往返费用、spread、滑点和最小业务 edge；下行 barrier
表达候选失效或最大容忍不利路径。在 horizon 内按真实事件顺序分类：

```text
TARGET_FIRST    # 先触达净目标
STOP_FIRST      # 先触达下行 barrier
NO_TOUCH        # horizon 内均未触达
UNAVAILABLE     # 无法建立可执行入口或路径数据不可靠
```

只有 `TARGET_FIRST` 是主正类；其余类别的训练权重和纳入规则写入 `label_spec_version`。
若同一聚合 BAR 内目标和止损都被触达，必须用有序 Tick 还原先后；无法还原时主训练集标记
`UNAVAILABLE`，并在悲观的 `STOP_FIRST` 敏感性回测中单独报告，禁止默认目标先到。

未来最大上涨幅度 MFE 或未来收盘涨跌本身都不能作为“可实现做 T 成功”的充分标签，因为
它们忽略路径顺序、可执行价格和成本。MFE/MAE 仍作为辅助回归目标和诊断指标保留。第一版
只晋升一个冻结的主 horizon；60/300/900 秒结果可继续用于研究和稳定性分析，不能事后选择
表现最好的 horizon 再宣称为线上目标。

### 14.6 跨标的共享模型

第一版采用一个覆盖当前可交易 Universe 的 pooled model，而不是“每票训练一个模型”。单票
日内有效样本通常过少，独立模型容易记住少量行情阶段，且不同票分数不可直接比较；共享模型
更符合 PortfolioTCoordinator 的跨票排序目标。

共享训练必须满足：

- 用收益、波动、成交活跃度和流动性等相对尺度归一化，不依赖绝对股价或成交量级；
- 行业、指数和画像均使用 point-in-time 版本，不用今天的分类回填历史；
- 第一版不输入原始 `instrument_code`，避免模型靠身份记忆历史均值；
- 验证同时报告全体、逐标的、行业、流动性桶和市场 regime 指标，并关注 worst-group；
- 新标的、低覆盖标的和分布外样本通过 `feature_coverage/OOD` 保守阻断或降级，不猜测分数。

只有在某一标的积累了预先规定的独立 OOS 样本，且单票校准在多个时间窗稳定优于共享模型时，
才允许评估单票校准层或专用模型；它仍必须输出同一 `TModelScore` 契约，不能另开执行路径。

### 14.7 第一版模型基准

所有候选模型必须使用同一 observation anchors、Feature Bar、标签、时间切分、校准和共享账户
组合回测，禁止为某个算法单独选择更有利的数据窗。第一版只比较三个明确基准：

| 层级 | 模型 | 作用与准入条件 |
|---|---|---|
| 规则对照 | RULE_ONLY | 证明模型相对现有可解释规则是否真的带来组合增量 |
| 概率基线 | Logistic Regression | 验证特征是否有稳定线性信息，是 LightGBM 的最低比较对象 |
| 非线性候选 | LightGBM | 只有稳定赢过规则与 Logistic 才可进入 SHADOW/ACTIVE 评审 |

不预建其他模型 adapter、状态或配置。若 Logistic 或 RULE_ONLY 在组合费用后更稳定，应保持
简单方案；将来增加模型家族必须由新的证据和独立契约变更驱动。

### 14.8 训练与验证

训练运行在 Worker/Research，不在 Engine：

1. 生成不可变、因果的 feature/label 数据集、数据能力清单和 manifest；
2. 预检覆盖、泄漏、资源、requested/resolved backend，并锁定 training spec hash；
3. `DEVELOPMENT` 为全部模型冻结同一训练/验证 observation id，使用 purged walk-forward，
   embargo 至少覆盖最大标签 horizon；
4. 在每个验证窗内独立校准概率，禁止用全量数据校准；
5. 用户锁定开发配置后，`FINAL_EVALUATION` 才能访问一次冻结测试 observation ids；
6. 比较 Brier/log loss、AUC、Precision@K/NDCG@K 和校准曲线；
7. 分别报告标的、行业、流动性桶、市场 regime、时间窗和 worst-group 稳定性；
8. 使用共享账户组合回测比较费用后收益、回撤、换手、容量拒绝和行业集中度；
9. 检查特征覆盖、分布漂移、排名漂移、CPU 推理延迟和不可用比例；
10. 只有成功 FINAL_EVALUATION 且安全制品、门禁与冻结测试证据完整，才允许人工登记；
11. 只有相对规则排序和 Logistic 基线有跨窗、跨组稳定增益，才具备 ACTIVE 资格；
12. 人工审核和新配置/执行绑定后才会影响实盘排序。

不能只用随机 train/test split、单一总样本 AUC 或分类准确率决定晋升。模型若靠少数高频标的
贡献全部收益、在某个行业或 regime 显著失效，即使总体指标更高也不能直接 ACTIVE。

### 14.9 做 T 模型制品与运行时绑定

复用 QuantX 已有安全模型制品方法，但不复用次日上涨模型、标签、数据表或日级候选。做 T
制品至少包含：

```text
model_id / model_version
model_type
feature_schema_version / feature_order
label_spec_version
primary_horizon_seconds
market_data_capability_manifest_version
universe_policy / out_of_distribution_policy
training_data_cutoff
walk_forward_windows
calibration_type / calibration_parameters
validation_metrics / group_stability_metrics
portfolio_backtest_metrics
policy_compatibility
artifact_sha256
```

Engine 只加载与 `model_type` 对应的 allowlist 安全格式、显式特征顺序和校准参数，不加载
任意 pickle。没有安全、确定性运行时格式的 benchmark 只能停留在 Research。启动、配置变更
和模型晋升时预加载并完成固定样本自检；在线只在完整分钟结束后调用进程内 scorer。

FINAL training run 保存完整 `gate_conclusion=BLOCKED/SHADOW_ELIGIBLE/ACTIVE_ELIGIBLE`。
只有允许登记的结果才创建 `TModelVersion`；注册表引用不可变 artifact，复制其
`SHADOW_ELIGIBLE/ACTIVE_ELIGIBLE` 结论，并另存可变 `registry_stage` 和 `state_version`。
阶段切换只更新注册表授权，不重写 artifact 或训练证据。

训练 run 不是线上 identity。`TAssistantConfig` 只绑定：

```text
TModelRuntimeBinding
  model_id / model_version
  registry_stage
  registry_authorization_revision
  artifact_manifest_sha256
  feature_schema_version / label_spec_version / calibration_version
  portfolio_policy_compatibility_hash
  runtime_self_test_manifest_hash / self_test_tolerance_policy_version
  binding_hash
```

一个执行只读取启动时冻结的 binding。新模型激活、模型模式切换或 schema 变化必须先验证制品并
产生新配置版本，再创建能产生新 ENTRY 的 successor `TAssistantExecution`；旧执行进入
`DRAINING`，只完成自身未决 BUY 义务，已有 ExitPlan 由公共 runtime 独立继续。不得在运行中把
相同 model id 的文件原地替换，也不得让 scorer cache 跨 binding revision 复用。

binding 冻结不等于忽略安全撤销。每次批量评分和 snapshot 构建都要验证注册表当前
authorization revision：`SHADOW` 只能绑定允许影子运行的版本，`ACTIVE` 必须仍是唯一 ACTIVE
且 gate 为 `ACTIVE_ELIGIBLE`。模型被 `SUSPENDED/RETIRED` 或 revision 改变后，原 execution
立即停止新 ENTRY；它只能等待显式新配置/successor，不能继续使用旧授权，也不能热换模型。
这里“停止新 ENTRY”的边界是禁止再创建新的 durable pending/outbox；已经提交的 outbox 或结果
未知委托继续按公共订单契约收敛，不能因模型撤权而删除命令或假定零成交。

### 14.10 复用现有模型能力的边界

QuantX 已经在次日上涨概率训练工作台中实现了以下可复用能力：

- 认证、不可变的数据集 manifest 和 SHA-256；
- 不可变训练 spec、稳定 hash 和环境要求 hash；
- `DEVELOPMENT` 与 `FINAL_EVALUATION` 分离，记录冻结测试访问证据；
- Web/API 只提交与观察，Worker/Prefect 在隔离子进程中训练；
- 阶段/完成单元进度、幂等提交、取消、并发限制和重启恢复；
- requested/resolved backend、脱敏环境证据和 CPU/GPU 资格验证；
- 安全 artifact manifest、人工登记、乐观锁阶段切换和发布门禁。

参考当前权威实现说明：

- [次日上涨概率网页模型训练与 GPU 加速实施计划](../plans/次日上涨概率网页模型训练与GPU加速实施计划.md)
- [次日上涨概率只读候选工程契约](../engineering/api/NEXT_DAY_SELECTION.md)

实现基线已经存在于 `quantx_domain.stock_selection_training`、
`quantx_application.stock_selection_training`、`stock_selection_training_flow.py` 和
`stock_selection_artifacts.py`。做 T 应从这些实现抽取已验证的强类型公共原语，不能复制一套
名字不同但语义相同的训练生命周期和安全加载器。

做 T 首期只抽取下列小而稳定的公共 building blocks，不建立一个包含任意任务 JSON 的“万能
模型平台”：

```text
ImmutableDatasetManifest
ImmutableTrainingSpec / stable_hash
TrainingRunLifecycle / Progress / Cancellation
DevelopmentFinalIsolation
ModelGateConclusion
SafeArtifactManifestVerifier
ModelBackendQualificationEvidence
PublishedModelIdentity / RuntimeSelfTest
```

次日上涨与做 T 各自保留强类型数据集、标签、指标、注册表和 GraphQL 投影。做 T 新增自己的：

```text
TModelDatasetVersion
TModelTrainingSpec
TModelTrainingRun
TModelVersion
```

其中 dataset version 固化 Feature Bar schema、observation ids、capability manifest、point-in-time
Universe、标签和成本版本；training spec 固化 DEVELOPMENT/FINAL、purged walk-forward、embargo、
模型网格、校准、评估、requested/resolved backend 和 seed；training run 只表达排队、执行、取消、
证据和结果，不拥有任何实盘 execution。

training run 状态固定为 `QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED`，训练 phase 单独表达
`PREFLIGHT`、`DATASET_BUILD`、`WALK_FORWARD`、`FINAL_FIT`、`CALIBRATION`、`FROZEN_TEST`、
`PORTFOLIO_EVALUATION` 和 `ARTIFACT_PUBLISH`。取消请求是独立事实，不再增加一组混杂状态。

职责边界固定为：

| 组件 | 做 T 模型职责 | 禁止 |
|---|---|---|
| Web | 预检、提交、进度、报告、比较、人工登记/晋升 | 传服务器路径、任意参数 JSON 或直接激活交易 |
| API | typed contract、幂等命令、状态真源和脱敏投影 | 训练、加载模型进行交易推理 |
| Worker/Research | 数据集、训练、校准、walk-forward、冻结测试、组合评估、安全制品 | 读取实盘账户、访问 XTData/QMT、下单或修改执行配置 |
| Engine | 验证并只读加载已发布 artifact，完整分钟 CPU 批量推理 | 启动训练、在线学习、写回模型或 RPC 逐 Tick 推理 |
| QMT Agent | 无模型职责 | 接收训练命令或加载模型 |

训练数据只来自已持久化、可审计的 QuantX 历史行情与点时资料；Worker/Research 不直接访问
XTData/QMT，也不为缺失历史使用当前截面回填。

做 T 训练与现有模型训练共用一个本机高资源队列：同时最多一个高资源 run；full/live 连续交易
时段只允许提交并保持 `QUEUED`，不启动训练子进程。取消只在数据分片、fold、模型家族和制品等
安全检查点生效；进度使用 phase 与 `completed_units/total_units`，不伪造耗时百分比。API、Engine
或 QMT 重启不能改变数据库 training run 真源。

模型对比只允许 coordinate hash 完全相同：dataset manifest、observation ids、时间切分、Universe、
feature/label/cost/evaluation 版本任一不同都返回 mismatch，不能只挑一个指标宣称胜负。CPU/GPU
backend 作为环境证据展示，不改变实验坐标。

### 14.11 三组状态不能混用

模型需要三组正交状态：

| 状态轴 | 值 | 回答的问题 |
|---|---|---|
| 训练证据结论 | `BLOCKED / SHADOW_ELIGIBLE / ACTIVE_ELIGIBLE` | 证据是否具备登记或 ACTIVE 资格 |
| 注册表阶段 | `CANDIDATE / SHADOW / ACTIVE / SUSPENDED / RETIRED` | 人工已把哪个发布 artifact 放到哪个阶段 |
| 做 T scorer 模式 | `RULE_ONLY / SHADOW / ACTIVE` | 本次 `TAssistantExecution` 是否计算模型、是否影响排序 |

`ACTIVE_ELIGIBLE` 不会自动变为 registry `ACTIVE`，训练成功也不会修改做 T 配置。registry
`ACTIVE` 只有被新 `TModelRuntimeBinding` 和新执行显式采用后才影响交易。Research 中的
challenger/champion 只是模型比较角色，不作为第四组线上生命周期状态。

只有 `FINAL_EVALUATION + SUCCEEDED` 才可登记 `CANDIDATE`；`BLOCKED` 不可登记，
`SHADOW_ELIGIBLE` 最高只能进入 `SHADOW`，`ACTIVE_ELIGIBLE` 才可由人工晋升 `ACTIVE`。
做 T 注册表最多一个 ACTIVE；激活新版本必须在一个事务中暂停旧 ACTIVE，并使用 state version
防止并发覆盖。单独的注册表 mutation 不得静默修改任何正在运行的 execution。

若当前 RUNNING execution 绑定了旧 ACTIVE，禁止只切注册表后继续运行。要么先显式停止新 ENTRY，
要么使用“模型激活 + 新配置 + predecessor DRAINING + successor WARMING”的组合用例和统一锁序；
不能出现新模型已 ACTIVE、旧 execution 仍按旧授权继续入场的窗口。

评分批次读取 registry revision 后再发布 cache；若阶段在推理期间变化，本批次失败且不发布
部分 score revision。`RULE_ONLY` 不需要 model binding；`SHADOW/ACTIVE` 必须满足上述阶段与
门禁映射。

做 T 发布门禁必须版本化，并至少覆盖：

- artifact、数据泄漏、point-in-time Universe、Feature Bar/标签覆盖和冻结测试有效性；
- Brier/log loss、校准误差、Top-K/rank lift 及按交易日 bootstrap 的置信区间；
- 标的、行业、流动性、regime、时间窗和 worst-group 稳定性；
- 相对 RULE_ONLY 和 Logistic 的费用后共享账户增量收益、回撤、换手和容量拒绝；
- Engine CPU 推理延迟、不可用率、OOD、schema 和固定样本自检。

门禁阈值属于版本化系统 policy，Web 不能为某次运行降低。冻结测试被重复查看、历史 Universe
证据不完整或组合评估不足时最多 `SHADOW_ELIGIBLE`，不得伪装成无偏 ACTIVE 证据。

### 14.12 GPU 只用于经资格验证的离线训练

做 T 复用现有 `CPU/AUTO/GPU_REQUIRED` 后端解析、Windows LightGBM OpenCL 构建证据和 GPU
资格验证代码，但必须用做 T 自己的黄金数据面板重新确认概率、Top-K、组合门禁和性能。规则为：

- Logistic 始终使用 CPU；requested/resolved GPU 只影响 LightGBM 训练；
- Windows Dev 只接受经认证的 LightGBM OpenCL GPU wheel，不引入 CUDA/WSL 第二运行形态；
- `CPU` 从不初始化 GPU；`GPU_REQUIRED` 不满足资格时预检失败；
- `AUTO` 只有资格证据、显存预算、样本规模和可重复加速收益都合格时才解析为 GPU；
- resolved backend 在提交前明示并写入不可变 spec，排队后 GPU 失败使该 run 失败，不在同一
  run 中静默改用 CPU；
- CPU/GPU 使用相同数据、切分、seed 和显式 LightGBM 参数，GPU 训练结果必须能由 CPU Engine
  安全加载并通过固定样本与质量容差；
- qualification evidence 必须绑定 LightGBM/OpenCL wheel SHA、编译能力、设备、重复性、CPU
  reload、端到端耗时和峰值显存；初始预算沿用“峰值显存不超过可用显存 80%、可重复端到端
  加速至少 20%”的公共门禁，概率/Top-K/组合容差由做 T 黄金面板版本化且不得翻转发布结论；
- 当前环境若报告 `GPU_UNAVAILABLE_BUILD`，正常使用 CPU，不影响 full/live 和已发布模型。

GPU 不进入 Engine、API 或 QMT Agent。线上分钟级 pooled inference 默认使用 CPU；只有真实
SLA 数据证明 CPU 无法满足且有独立架构评审时，才设计 GPU inference，不预埋运行时降级分支。

### 14.13 安全制品和原子发布

运行目录只接受 manifest 索引、路径位于允许根目录且 SHA-256/字节数匹配的普通文件；拒绝
路径穿越、符号链接/联接点、Pickle/Joblib、非有限数、非法预处理尺度、越界校准器和未在
allowlist 中的模型格式。首期只允许安全 JSON 与 LightGBM 文本模型；训练面板 Parquet 不由
Engine 反序列化为模型。
Web/GraphQL 只返回 artifact id、hash 和白名单化证据，不返回本地根目录、文件路径、设备序列号、
账户信息或原始 Worker 异常。

人工切换 registry 阶段使用 `state_version` 乐观锁。创建 successor execution 前必须：

1. 加载并完整校验 manifest、artifact、schema、calibration 和 policy compatibility；
2. 用固定样本运行 CPU self-test；输入/期望证据由 manifest hash 固定，预测在版本化数值容差内
   一致，且排序、OOD 和门禁结论不得翻转；
3. 预热 scorer 并建立新的空 cache revision；
4. 按统一锁序锁定注册表、当前配置和活动 execution，在同一事务中切换 registry ACTIVE、写入
   新配置/binding、将 predecessor 置为 DRAINING，并创建 successor WARMING；
5. successor 完成行情和 scorer 预热后才进入 RUNNING；此前保持无新 ENTRY，旧退出继续运行。

任何步骤失败都保持旧配置/执行不变；若旧执行已经进入 DRAINING，则 fail-closed，不临时回切
未审核模型。ACTIVE runtime artifact 缺失或损坏时阻断新 ENTRY，已有退出完全不受影响。

### 14.14 排序融合

规则资格永远先执行。排序分采用版本化、可回放的明确公式，例如：

```text
RULE_ONLY:
  rank_score = normalized_rule_score

SHADOW:
  execution_rank_score = normalized_rule_score
  shadow_ml_score = p_target_before_stop

ACTIVE:
  rank_score = versioned_blend(
      normalized_rule_score,
      p_target_before_stop,
  )
```

融合权重属于模型/portfolio policy 版本，不允许在运行中隐式变化。标签已经显式计入成本时，
不得再无依据重复扣一次 cost penalty；如未来引入预期净 edge 或当前可执行性惩罚，必须说明其
独立信息、冻结公式并升级 policy 版本。

每个 material candidate 保存规则分、`score_id`、模型原始分、校准概率、最终 rank score 和
有限的 top feature contribution；普通 Tick 不逐笔计算或持久化 SHAP。模型分数不直接映射
`target_amount` 或数量，额度仍由 Coordinator 和公共执行链决定。

## 15. 回测设计

### 15.1 共享账户，而不是每票独立资金

多标的做 T 回测必须只有一个 `BacktestPortfolio`：

- 每个回测版本创建独立 `TAssistantExecution(environment=BACKTEST)`，不创建 `StrategyRun`；
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

回测必须复用实盘的 `TModelFeatureBarBuilder` 和 `InProcessModelScorer`：有序 Tick 驱动 V3
规则、EntryExecutionGate、Broker 撮合和 barrier 路径；只有 watermark 越过分钟结束后才产生
`COMPLETE` Feature Bar 和新 `TModelScore`。同一时间戳的 minute-complete、snapshot 和 broker
事件使用冻结的优先级，确保一个候选能否看到该分钟分数与实盘一致。不得用回测框架预先计算的
整日 BAR 表直接注入在线决策。

### 15.3 模型的时间因果

- 固定模型回测要求 `training_data_cutoff < backtest_start`；
- walk-forward 回测只允许在每个训练窗结束后生成下一窗模型；
- 任何模型、校准器、画像和行业映射都按当时可用版本加载；
- 每次评分只读取当时已经 `COMPLETE` 的 Feature Bar，形成中的分钟不可见；
- 标签生成器属于离线评估路径，未来 barrier 结果不得进入 runtime snapshot 或 scorer；
- 缺失当时版本时阻断该段模型回测，不得用当前模型回填历史。

### 15.4 结果

除现有批次和收益指标外，组合回测必须输出：

- 每周期候选数、选中数和淘汰原因；
- rule/ML 排名一致率和 Top-K 命中；
- `p_target_before_stop` 的校准、分组稳定性、OOD 和模型不可用比例；
- 资金使用率、现金缓冲和容量拒绝率；
- 最大并发批次、单票和行业暴露；
- 费用后每轮 PnL、持有时间、MFE/MAE；
- EntryExecutionGate 的 ALLOW/DELAY/REJECT 数量、延迟后过期率和避免的不利成交；
- 因模型排序相对规则排序产生的增量收益和增量回撤；
- 数据健康、模型不可用和期末未闭环数量。

### 15.5 回测身份与结果真源

`TAssistantBacktestVersion` 保存 execution id、冻结 config/policy/model binding、数据 manifest、
时间线规则、Broker 参数、代码版本和结果 manifest。训练用组合评估必须引用明确 backtest
version；训练 run 只引用结果，不能拥有或修改回测事实。

LIVE、PAPER、BACKTEST 都通过同一 `StrategyBase.step(StrategyInput)`，区别只来自冻结的
`execution_ref/environment`、时钟、Broker 和外部事实适配器。BACKTEST owner 不得进入实盘审批、
pending、outbox 或 QMT 路由；PAPER 也不得与 LIVE 共用这些记录的唯一键或容量义务。

## 16. 状态真源与持久化

### 16.1 真源矩阵

| 数据 | 真源 | 备注 |
|---|---|---|
| 标的行情窗口和 FSM 热状态 | Engine 内存 | 可重建；检查点只保存保守恢复投影 |
| 做 T 配置与版本 | `t_trade_global_configs` head + append-only `t_assistant_config_versions` | 单账户唯一 head；无 `strategy_run_id` |
| 做 T 执行身份与生命周期 | `t_assistant_executions` | PAPER/LIVE/BACKTEST 独立 owner |
| 做 T execution material 事件 | append-only `t_assistant_execution_events` | owner 路由与生命周期审计；不替代 inbox |
| 标的 material 算法状态 | `t_assistant_symbol_states` | `(execution_id, instrument_code)` 独立 revision |
| material 决策周期 | `t_assistant_decision_cycles` | snapshot/output manifest 与原子提交事实 |
| 标的 Universe | 权威持仓快照 + Monitor 投影 | 策略不得自行选股 |
| 做 T 规划边界 | append-only `t_trade_envelopes` | 只持久化 material snapshot；不是余额或预占真源 |
| 完整 1 分钟 Feature Bar | 因果行情历史 + 固定 builder/schema 生成 | 在线热缓存可重建，`FORMING` 不可评分 |
| 最新 `TModelScoreCache` | Engine 内存，由 model artifact + 完整 Feature Bar 重建 | 只读 cache view，不是持久化真源 |
| material 模型分数证据 | append-only `t_trade_model_scores` | 只保存候选绑定或审计要求的 score/outcome |
| material 机会证据 | `t_trade_opportunity_evaluations` | append-only、幂等 event key |
| 参考画像 | `t_trade_instrument_profiles` | 点时版本化 |
| 候选结果 | `t_trade_candidate_outcomes` | 用于候选评估，不单独充当完整训练集 |
| TradeIntent | 公共 `trade_intents`（由现有 `strategy_trade_intents` 原子迁移） | 强类型 owner；候选提案也必须先受理 |
| 组合分配决策 | 新增持久化 `t_trade_allocation_decisions` | 一条候选一条动作和原因 |
| 最终账户容量 | 完整 QMT 快照 + 本地未覆盖义务 | `AccountCapacityService` 事务计算 |
| pending/outbox | 对应持久化业务表 | 命令可靠投递真源 |
| 委托/成交 | QMT 报告 + inbox/业务表 | 唯一实盘成交真源 |
| 自动退出 | `auto_exit_plans` + 公共 `ExitPlanRuntime` | source execution 不保存 PAPER/LIVE 热缓存 |
| 仓位归因 | `BucketLedger` | locked_core/core/swing |
| 做 T 轮次展示 | `TTradeBatch` + 事件 | 可重建运营投影，不反向驱动真源 |
| 做 T 回测版本/结果 | `t_assistant_backtest_versions` + result manifest | 引用 BACKTEST execution；不接入实盘路由 |
| 做 T 模型训练 | `t_trade_model_dataset_versions` / `t_trade_model_training_specs` / `t_trade_model_training_runs` | Worker 真源；与交易 execution 分离 |
| 评分模型 | `t_trade_model_versions` + 版本化制品与 manifest | Engine 只读加载冻结 binding |

### 16.2 普通 Tick 写入原则

普通窗口推进和无变化诊断不写 PostgreSQL。立即持久化的 material 事实包括：

- 数据健康或 FSM 的重要转换；
- candidate 创建、过期、抑制和审批绑定；
- TradeIntent；
- material candidate 关联的 `TModelScore`、scorer/Coordinator 决策；
- EntryExecutionGate 的 `ALLOW/DELAY/REJECT`；
- 审批、风险和容量裁决；
- pending/outbox；
- 委托、成交、ExitPlan 和批次事件；
- scorer mode、model binding 或 registry authorization 变更。

在线不逐 Tick 持久化 Feature Bar、模型分数或 SHAP。SHADOW 的全 observation-anchor 分数和
BACKTEST 的普通热状态、无意图 material 评估可以使用 `DAY_BATCH`，但真正候选绑定的分数、
意图、Gate 决策和模拟成交必须即时成为幂等事实。

### 16.3 端到端关联字段

为了端到端回放，相关投影和事件应能够关联：

```text
cycle_id
cycle_sequence / execution_id
execution_owner_type / execution_owner_id
producer_id / execution_environment
opportunity_id
candidate_id / fingerprint
model_score_id / source_feature_bar_id
model_runtime_binding_hash / score_cache_revision
intent_id
allocation_decision_id
entry_gate_decision_id
admission_batch_id / admission_rank
t_batch_id
exit_plan_id
source_execution_owner_type / source_execution_owner_id
client_order_id / broker_order_id
policy / config_version_id / feature / profile / model versions
account_snapshot_id
envelope_input_fingerprint / local_obligation_watermark
trace_id
```

不要求每张表复制所有字段，但必须通过稳定外键或业务键无歧义连接。

`strategy_run_id` 只允许保留在尚未排空的 legacy `STRATEGY_RUN` 记录中；任何新
`T_ASSISTANT_EXECUTION` 记录都不能同时写一个伪造或兼容用 run id。公共表完成 owner 迁移后，
删除“缺失 owner 时默认 STRATEGY_RUN”的 default 和 fallback。

### 16.4 执行环境隔离

- LIVE intent/order/outbox 只能由 LIVE execution 创建，且必须通过实盘能力门。
- PAPER 使用独立 Broker 和事实表/命名空间，不消耗 LIVE `AccountCapacityService` 义务。
- BACKTEST 只写回测存储，不创建公共实盘 approval、pending 或 outbox。
- environment 是 execution 的冻结字段；不能通过更新一行把 PAPER/BACKTEST 原地变成 LIVE。
- owner、environment 与目标表不匹配时在 repository 和路由边界双重拒绝。

## 17. 故障与恢复语义

| 故障 | ENTRY 行为 | EXIT 行为 | 恢复 |
|---|---|---|---|
| 行情 stream/generation 变化 | 清空相关窗口，停止新候选 | 仅在新鲜行情恢复后继续评估 | 完整 fence + rewarm |
| 单票 quote 陈旧 | 只阻断该票 | 该票退出暂停，不用旧价触发 | 新鲜 Tick 后恢复 |
| 市场/行业上下文陈旧 | 按 policy 阻断或保守降级并审计 | 不改变既有退出规则 | 新版本上下文 |
| 1 分钟 Feature Bar 未封闭/不连续 | 不生成新模型分数；规则路径按自身健康度运行 | 不影响 | 完整新分钟 + rewarm |
| Engine 关键消费者 lagging | 阻断新 ENTRY | 保持可见并 fail-closed | resync 后 rewarm |
| 账户快照陈旧/不完整 | 全部拒绝 | 不猜测可卖量；必要时 reconcile | 新合法完整快照 |
| Coordinator 异常 | 整周期不提交分配 | 不影响已有退出 | 同 cycle 幂等重试或终态拒绝 |
| `ACTIVE` 模型缺失/校验失败 | 全部 `MODEL_UNAVAILABLE`，不静默降级 | 不影响 | 修复同版本，或以新配置创建 RULE_ONLY successor |
| `ACTIVE` 分数陈旧/schema 不匹配/OOD | 只阻断受影响候选，保存稳定原因 | 不影响 | 合法新分数，或新配置的 RULE_ONLY successor |
| 数据源 required 盘口字段缺失 | 依 capability policy `DELAY/REJECT`，不伪造数值 | 不影响既有退出规则 | 字段恢复或切换经过验证的无该字段 schema |
| EntryExecutionGate 发现价差/价格偏离 | `DELAY/REJECT`，不得追价或直接改量 | 不影响 | TTL 内重新走 scorer/Coordinator，否则过期 |
| 提案已持久化、协调前崩溃 | 不路由 | 不影响 | TTL 内用最新事实重验，否则过期 |
| 容量事务提交前崩溃 | 没有 outbox，不得下单 | 不影响 | 幂等重试 |
| outbox 已投递、结果未知 | 保持占用并 reconcile | 同公共订单契约 | QMT 快照/回报证明 |
| ORDER 先于 TRADE | 不提前释放 | 不提前完成 | 等成交累计量收敛 |
| 迟到成交或释放后反证 | 账户隔离，禁止新 ENTRY | 精确计划 sticky ERROR | 显式修复和新快照 |
| Engine 重启且有活动 ExitPlan | 禁止新建替代计划或改挂来源 | 按 `EXIT_PLAN/plan_id` 独立恢复 | durable inbox + ExitPlanRuntime + plan reload |
| execution 状态检查点落后于 material cycle | 不从旧状态重复提案 | 不影响 | 以已提交 cycle/intent 为准重放 symbol state |
| successor 启动失败 | predecessor 已 DRAINING 时继续阻断新 ENTRY | 公共 ExitPlanRuntime 正常运行 | 修复 binding 后创建新 successor，不原地篡改 |
| 模型训练/FINAL 失败 | 不改变当前 execution 或 registry | 不影响 | 修复后创建新训练 run；不能接管交易 execution |

任何恢复都不能通过创建第二个 `TAssistantExecution` 接管未决 ENTRY、创建第二个 ExitPlan 或
重发不同 intent 来“绕过”不确定状态。legacy 做 T `StrategyRun` 的未决 BUY 也必须由原 owner
排空，不能把结果未知的旧义务转挂到新 execution；已激活 ExitPlan 保留自身 owner 和原 source
ref，由公共 runtime 继续。

## 18. 审计、可观测性与 UI 投影

### 18.1 一次机会的审计链

```text
ExecutionOwnerRef / TAssistantExecution
  -> market fence
  -> TDecisionSnapshot/cycle_id/output manifest
  -> Symbol feature + FSM evaluation
  -> candidate/opportunity
  -> complete Feature Bar / TModelScore
  -> rule score / scorer evidence
  -> TTradingEnvelope / portfolio rank/allocation
  -> approval/revalidation
  -> EntryExecutionGate
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
- execution 状态、owner 路由错误、cycle commit/abort 和 symbol revision 冲突；
- 每 symbol quote age、window warmup 和 data health；
- 每周期 evaluated/candidate/selected/rejected 数；
- Feature Bar complete/invalid 数、完成延迟和字段覆盖率；
- scorer 模式、模型版本、score freshness、推理耗时、OOD 和错误数；
- rule 与 ML 排名漂移、概率校准和 score 分布漂移；
- Coordinator 额度使用、行业暴露和淘汰原因；
- EntryExecutionGate 的动作、价格偏离、spread 异常和 TTL 过期；
- 审批过期和确认后容量拒绝；
- active batch、ExitPlan、reconcile 和 sticky error；
- outbox 投递、ORDER/TRADE 延迟和乱序收敛。
- 模型训练 DEVELOPMENT/FINAL、backend、门禁和 artifact 状态单独监控，不混入交易运行状态。

### 18.3 UI 投影

做 T 助手页面应把三类信息分开：

1. **机会**：标的、路径、数据健康、rule/ML 分、模型 as-of/覆盖率、排名、TTL、淘汰原因；
2. **组合**：总额度、已规划/已占用、现金缓冲、并发、单票和行业暴露；
3. **轮次**：ENTRY、ExitPlan、成交、净收益、异常和 reconcile。

页面顶部另行展示 `TAssistantExecution`、冻结 config/model binding、RUNNING/DRAINING 状态和
source owner；研究页展示训练 run 与发布门禁。两者不能共用一个“运行成功”状态。

不得把“模型高分”“已分配预算”“已批准”和“已成交”用一个状态或同一种颜色表示。

## 19. 代码落点

不新增微服务，优先在现有边界内重构：

```text
packages/domain/src/quantx_domain/trading/
  execution_owner.py                # ExecutionOwnerRef 与当前明确 owner 类型
  t_assistant_execution.py          # 配置/执行/周期/标的状态生命周期
  t_trade_opportunity_engine.py      # 继续作为纯 SymbolTEngine reducer
  t_trade_portfolio_coordinator.py   # 新增纯组合协调算法和契约
  t_trading_envelope.py              # 账户快照绑定的只读规划边界
  t_trade_scoring.py                 # FeatureBar/TModelScore/scorer 值对象，不做 I/O
  t_trade_entry_execution_gate.py    # 纯 ALLOW/DELAY/REJECT 重验

packages/application/src/quantx_application/t_trade_v3/
  contracts.py                       # snapshot/opportunity/allocation 契约
  ports.py                           # scorer、证据和分配存储端口
  execution_use_cases.py             # 创建/接续/draining/recovery
  decision_cycle_use_cases.py        # 周期原子受理、协调和恢复

packages/application/src/quantx_application/trading/
  owner_runtime_router.py            # 公共 owner 事件路由，不含做 T 算法

packages/application/src/quantx_application/model_ops/
  manifests.py                       # 不可变 manifest/spec/hash 公共原语
  training_lifecycle.py              # DEVELOPMENT/FINAL、进度、取消、门禁
  artifact_verification.py           # 安全制品与 runtime self-test 端口

packages/infrastructure/src/quantx_infrastructure/
  services/t_trade_lightgbm_scorer.py
  services/t_trade_model_artifact_loader.py
  repositories/t_assistant_config_repository.py
  repositories/t_assistant_execution_repository.py
  repositories/t_assistant_execution_event_repository.py
  repositories/t_assistant_decision_cycle_repository.py
  repositories/t_assistant_symbol_state_repository.py
  repositories/t_trade_envelope_repository.py
  repositories/t_trade_allocation_decision_repository.py
  repositories/t_trade_model_score_repository.py
  repositories/t_assistant_backtest_repository.py
  models/t_assistant_config_version.py
  models/t_assistant_execution.py
  models/t_assistant_execution_event.py
  models/t_assistant_decision_cycle.py
  models/t_assistant_symbol_state.py
  models/t_trade_envelope.py
  models/t_trade_allocation_decision.py
  models/t_trade_model_score.py
  models/t_assistant_backtest_version.py
  models/t_trade_model_training.py

apps/research/src/quantx_research/
  t_trade_model_dataset.py           # 做 T 专用 observation/label 数据集
  t_trade_model_training.py          # 训练、校准、验证、组合评估与制品

apps/worker/src/quantx_worker/prefector/flows/
  t_trade_model_training_flow.py     # 隔离后台训练、取消和证据收敛

apps/engine/src/quantx_engine/
  t_trade_decision_snapshot.py       # 从 WholeQuoteHub 构建冻结快照
  t_trade_model_runtime.py           # 完整分钟 builder、批量推理和 score cache
  t_assistant_decision_runtime.py    # 无 StrategyRun 的周期、排序、协调编排
  t_trade_global_monitor.py          # 只保留配置/Universe/生命周期
```

`AshareIntradayTAssistantStrategy` 应逐步收敛为薄适配器：批量调用纯标的 reducer、合并
算法状态、输出候选 BUY TradeIntent 和退出模板。账户事实、组合排名、真实成交量和
ExitPlan 状态机不得继续堆入该类。

公共 owner 和 model-ops 模块只容纳已经被两个真实功能验证的最小原语。做 T 的 Feature Bar、
标签、Coordinator、执行生命周期和 GraphQL 不下沉成泛化框架；次日上涨模型也不迁入做 T
表。避免为了“复用”制造弱类型 `feature_json/task_type` 总表。

上述文件名表达目标职责，不要求为目录美观进行无价值拆分。若现有模块能清晰承担同一
职责，可以原地重构；同一能力只能保留一条权威路径。

## 20. 迁移路线

### 阶段 0：冻结 `StrategyRun` 做 T 扩展并建立迁移基线

- 冻结当前做 T `StrategyRun` 的 schema 和功能；除安全修复外，不再在其上实现新能力。
- 固定 V3 规则、候选、审批、TTradeBatch、ExitPlan、T+1 置换和乱序回报测试。
- 清点所有隐式 `run_id/strategy_run_id/owner_type=STRATEGY_RUN` 假设，包括 intent、审批、
  correlation、pending/outbox、ExitPlan、回报路由、Worker 画像和 GraphQL。
- 为存量活动候选、订单、batch、plan 和结果未知义务建立可查询清单及 owner 一致性检查。
- 明确切换开关只能停止旧路径新 ENTRY，不能停止旧 ExitPlan 和回报收敛。

### 阶段 1：公共 `ExecutionOwnerRef` 原子升级

- 先冻结新命令创建并排空所有未投递 1.1 outbox；已投递但结果未知的命令必须通过 QMT
  快照/回报完成 reconcile。不能重写或用 1.2 payload 重发一条可能已经到达券商的 1.1 命令。
- 在 domain 中加入强类型 `ExecutionOwnerRef` 及当前六种明确 owner。
- 将 `StrategyInput`、`TradeIntentOrigin`、意图、审批、correlation、pending/outbox、ExitPlan
  source 和回报路由切换为 owner ref。
- 现有普通策略记录按原 `run_id` 回填为 `STRATEGY_RUN`，人工命令和 ExitPlan 使用各自 owner；
  不改变其业务行为。
- 将 `strategy_trade_intents`、`strategy_order_correlations` 原子重命名/演进为公共
  `trade_intents`、`order_correlations`，删除默认 `owner_type=STRATEGY_RUN` 和 owner 缺失
  fallback。
- 建立 `OwnerRuntimeRouter/Registry`；未知 owner、owner 冲突和 source ref 缺失全部 fail-closed。
- 同一发布中更新数据库、`packages/contracts`、domain/application/infrastructure、Engine、API、
  QMT Agent、GraphQL、Web、文档和测试；Agent 协议原子升级为 1.2，不长期支持新旧两套 owner
  写入或双 payload。

完成标志：普通 StrategyRun、人工命令和 ExitPlan 全部通过新 owner 契约，行为等价；公共链已能
接受 `T_ASSISTANT_EXECUTION`，但尚未让新做 T 路径下单。

### 阶段 2：建立独立做 T 执行域和快照内核

- 把 `t_trade_global_configs` 演进为无 `strategy_run_id` 的 config head，新增 append-only
  `t_assistant_config_versions`；旧 `mode` 明确迁移为 `desired_environment`，旧 settings 先按 typed
  schema 规范化为一个完整初始版本再切换 head；旧 `LIVE_AUTO` 映射为 `AUTO`，其他人工确认
  路径映射为 `CANARY_CONFIRM`，`BACKTEST_AUTO` 不进入活动 config。
- 新增 `TAssistantExecution`、append-only execution events、`TAssistantSymbolState`、
  `TDecisionCycle` 和 envelope 持久化。
- 引入 `TDecisionSnapshotBuilder`、`StrategyCadence.SNAPSHOT` 和强类型 snapshot validator。
- 把每标的 V3 逻辑收敛为无共享可变状态的 reducer；输出按标的 revision 的算法 patch。
- 将做 T 内核中的 `context.run_id/input.run_id/strategy_run_id` identity 和 metadata 全部替换为
  `execution_ref`，candidate/intent 幂等键改由 execution + causal fingerprint 构成。
- 禁用做 T 的 `StrategyStateProxy -> StrategyRunState` 持久化 callback 和
  `OWNS_RUNTIME_EXIT_PLAN_BOOK` 所有权暗示。
- `TAssistantDecisionRuntime` 通过 `execution_ref` 调用 `StrategyBase.step(SNAPSHOT)`，不创建
  StrategyRun。
- material 标的状态、机会证据、score 绑定、意图提案和 cycle manifest 按第 7.6 节原子提交。
- 与旧实现逐 cycle 比较规则结果；新路径使用隔离的 PAPER execution/intent 记录 shadow evidence，
  不创建 LIVE approval/order/outbox，也不计入 LIVE 容量义务。

### 阶段 3：组合协调与执行门影子运行

- 构建 point-in-time `TTradabilityProfile`、账户绑定 `TTradingEnvelope` 和
  `PortfolioTDecisionSnapshot`。
- 持久化 allocation shadow decision，验证排序确定性、额度、账户水位指纹和重启恢复。
- 引入 `EntryExecutionGate` 的 shadow action，覆盖 quote、spread、TTL、schema 和模型绑定重验。
- 旧路径仍是唯一新 ENTRY 来源；新路径不得持有真实容量或创建第二个订单。

### 阶段 4：新 owner 成为唯一做 T ENTRY 来源

切换在一个维护窗口按以下顺序完成：

1. 禁止旧做 T `StrategyRun` 产生新 candidate/intent，并将其标记 `DRAINING`；
2. 取消或终结尚未批准且可安全失效的旧候选，冻结旧 owner 义务清单；
3. 未决 BUY 订单和结果未知命令继续由原 `STRATEGY_RUN` owner 收敛；已成交 batch 对应的
   ExitPlan 保留原 source ref，但由 `EXIT_PLAN/plan_id` 和公共 ExitPlanRuntime 独立收敛，
   不迁 id、不复制 plan、不改挂 source；
4. 账户完整快照、inbox、pending/outbox 和 owner 一致性检查通过后，创建唯一能产生新 ENTRY 的
   `TAssistantExecution`；
5. 新 Coordinator 以 `ALLOCATION_PENDING -> TAllocationDecision -> 审批/EXECUTION_READY`
   成为唯一准入路径，CANARY_CONFIRM 确认后仍重新 allocation 和最终容量复核；
6. 旧 owner 的义务作为新 Coordinator 可见的本地占用；同标的旧活动 batch 存在时，新候选拒绝；
7. 旧做 T owner 自身 BUY 义务归零后删除 StrategyRun 专用做 T 调度、状态和 fallback；下游
   ExitPlan 不阻止旧 run 终态，但必须继续对新 Coordinator 可见。

短期允许一个只 DRAINING 的旧 owner 与一个可产生 ENTRY 的新 owner 并存；不允许两个 owner
同时产生新 ENTRY，也不允许新 owner 接管结果未知的旧订单。

### 阶段 5：共享账户组合回测

- 每个版本创建 `TAssistantExecution(environment=BACKTEST)`，统一多标的时间线、现金、库存、费用、
  T+1 和回报收敛。
- 旧 StrategyRun 做 T 回测只读保留原历史身份，不改挂到新 execution，也不作为新模型门禁证据。
- 对照 LIVE 的 snapshot、step、Coordinator、Gate、OrderSizer、Risk 和 ExitPlan。
- 通过无重复资金、无超老仓、无未来数据、执行环境隔离和结果可重放验收。

### 阶段 6：模型能力复用、训练与 SHADOW

- 抽取已经由次日上涨训练验证过的 manifest/spec/lifecycle/gate/artifact/backend 公共原语。
- 新建做 T 强类型 dataset/spec/training run/model registry，不复用次日上涨数据和模型。
- 冻结完整 1 分钟 Feature Bar、能力清单、first-touch 标签和全 observation-anchor 数据集。
- 通过 DEVELOPMENT -> 锁定配置 -> 一次 FINAL_EVALUATION，在相同 purged walk-forward 窗口
  比较 RULE_ONLY、Logistic 和 LightGBM。
- GPU 仅在做 T 黄金面板重新资格验证后作为离线 resolved backend；在线 Engine 保持 CPU。
- Engine 安全加载人工登记的 SHADOW artifact，记录带 as-of、cache revision、coverage 和 OOD
  的 shadow score，不改变规则排序。

### 阶段 7：模型 ACTIVE 灰度

- 只有 `ACTIVE_ELIGIBLE` 且人工进入 registry ACTIVE 的模型才可创建 ACTIVE binding。
- 先创建低额度 `CANARY_CONFIRM` successor execution，完成规定闭环后再以新配置创建受控
  `entry_authorization=AUTO` successor。
- ACTIVE artifact、分数、schema、binding 或 freshness 不合法时阻断受影响的新 ENTRY，禁止
  同 execution 静默退回 RULE_ONLY。
- 退出、回报收敛和账户安全链与训练和 scorer 故障完全解耦。

### 切换规则

- 不长期保留旧/新两套 owner、候选准入或状态写入协议。
- 每个阶段完成后原子更新代码、契约、GraphQL、文档和测试。
- 旧未确认候选在配置或 feature schema 切换时失效。
- 已成交 `TTradeBatch`、BucketLedger 和 ExitPlan 必须保持原 plan owner、source ref 和事实身份；
  source execution 终态不能删除或接管下游计划。
- 活动退出不阻止创建 successor，但必须进入全账户 obligation snapshot，不能通过新 execution
  重复占用同标的库存或复制 ExitPlan。
- 不为新做 T execution 生成假的 StrategyRun，也不把训练 run id 写入交易表。
- 数据迁移必须先验证精确目标和 owner 一致性；任何不确定记录进入 reconcile，不能猜测回填。

## 21. 测试与验收

### 21.1 领域测试

- `TAssistantConfig` 与 `TAssistantExecution` 生命周期独立，创建做 T execution 不创建
  `StrategyRun`。
- config version payload/hash 可重放且不可修改；head 乐观锁切换不会改变旧 execution 的冻结版本。
- 同一账户最多一个能产生真实 ENTRY 的 LIVE execution；DRAINING predecessor 只能完成自身旧
  BUY 义务，ExitPlan 由公共 runtime 独立完成。
- `ExecutionOwnerRef` 拒绝空 id、未知类型和 owner/目标冲突，普通 StrategyRun 适配后行为等价。
- 两个 symbol 使用相同 Tick 序列时状态完全独立。
- 一个 symbol 的乱序、gap、rewarm 不改变其他 symbol 状态。
- 同一快照输入得到字节级稳定的候选排序和原因码。
- 候选不包含现金、可卖量和最终数量。
- Coordinator 对相同输入输出稳定；tie-break 与输入容器顺序无关。
- 行业、并发、总暴露、现金缓冲和单票上限的 ALLOW/CAP/REJECT 正确。
- `TTradingEnvelope` 绑定账户快照和 input fingerprint，不能进入 SymbolTEngine 或模型输入；
  它的 planning ceiling 不能被当作最终可卖量或预占。
- LightGBM 只能改变合格候选之间的排序，不能使硬门禁失败者入选。

### 21.2 模型数据与验证测试

- `FORMING` 分钟永远不能生成在线分数；watermark 和 generation 满足后只封闭一次。
- 相同 Tick、capability manifest 和 schema 在 LIVE/BACKTEST 生成相同 Feature Bar hash。
- 候选只能关联 `model_as_of/source_bar_end <= observed_at` 且未过期的同版本分数。
- snapshot 冻结 score cache revision 后，随后完成的异步分数不能进入当前 cycle。
- SHADOW 分数缺失不改变规则排序；ACTIVE 缺失、陈旧、schema mismatch 或 OOD 稳定阻断。
- first-touch 标签用有序 Tick 判定 barrier 先后；无法判定的同 BAR 双触达为 `UNAVAILABLE`，
  并存在独立悲观敏感性回测。
- MFE/MAE 只作为离线指标，不能写入或改义 `p_target_before_stop`。
- Logistic 与 LightGBM 使用完全相同的 observation ids、时间窗、embargo 和成本。
- 跨标的模型不含原始 symbol identity，并输出逐标的/行业/regime/worst-group 稳定性。
- miniQMT 不具备可选 Level-2 字段时选择经过验证的无该字段 schema，不生成伪造数值。
- dataset/spec/hash 不可变；DEVELOPMENT 不能登记，FINAL_EVALUATION 不能静默重复使用冻结测试。
- `BLOCKED/SHADOW_ELIGIBLE/ACTIVE_ELIGIBLE`、registry stage 与 scorer mode 不可混写或自动推进。
- manifest SHA/size、路径、symlink、Pickle/Joblib、非有限数和 runtime self-test 校验失败时不可登记。
- CPU/AUTO/GPU_REQUIRED 解析可重放；排队后的 GPU 失败不静默转 CPU，GPU 训练模型可由 CPU
  Engine 安全加载。

### 21.3 应用与并发测试

- material cycle 的 symbol state、机会证据和多个 TradeIntent 要么整批提交，要么全部不提交。
- candidate 状态不能在 intent 缺失时单独推进；revision 冲突创建新 cycle，不覆盖旧证据。
- 两个候选竞争同一份现金时只有账户事务允许的数量进入 outbox。
- 两只股票竞争总并发最后一个名额时结果确定且可审计。
- LIVE 最终准入严格保持冻结排名顺序，不能由并发抢锁决定赢家。
- CANARY_CONFIRM 确认后使用新 account/envelope/obligation snapshot 创建新 allocation attempt。
- 配置更新、人工确认和市场周期并发时不存在旧候选穿透。
- Coordinator 崩溃恢复不会重复审批或重复路由。
- ACTIVE 模型失败不静默切换，所有新 ENTRY 有稳定阻断原因。
- 候选形成后出现新分钟分数时，旧 allocation 不会被静默换分；必须重走 scorer/Coordinator。
- ExitPlan SELL 在本地调度上优先于新 ENTRY。
- OwnerRuntimeRouter 将做 T、EntryPlan、打板助手、普通策略、人工命令和 ExitPlan 报告精确
  路由；未知 owner 不默认成 StrategyRun。

### 21.4 执行与回报测试

- EntryExecutionGate 的 `DELAY` 不超过 intent TTL，重试重新校验候选、模型和组合额度。
- Gate 不能改变方向、数量、订单类型或绕过 OrderSizer/Risk/Capacity。
- quote 陈旧、价格偏离、spread 异常和 required 字段缺失产生稳定动作与原因码。
- BUY 部分成交只激活等量 ExitPlan 和库存置换义务。
- ORDER `FILLED` 先到不提前释放容量。
- REJECT/CANCEL 只有权威零成交证明才释放。
- 迟到成交使账户和计划 fail-closed，不生成第二笔订单。
- 同标的活动批次存在时新候选被拒绝。
- 外部卖出侵占老仓时进入 reconcile，不自动缩量。
- QMT `command_ack` 不推进 ENTRY/EXIT 成交。
- protocol 1.2 command 不携带 StrategyRun 或业务 owner；服务端 correlation 缺失/冲突时拒绝
  路由，QMT 回报仍只表达券商事实。

### 21.5 回测测试

- 多标的共享现金，不能重复使用同一笔资金。
- 同时事件的稳定排序与实盘 snapshot 规则一致。
- T+1、涨跌停、停牌、费用、滑点和部分成交都走公共 Broker。
- 每次行情后先收敛回报再处理下一事件。
- 训练 cutoff 和 feature availability 无未来泄露。
- 完整分钟、模型评分、Tick 规则、Gate 和 Broker 事件顺序与 LIVE 一致。
- RULE_ONLY、SHADOW、ACTIVE 使用固定 artifact 可重复得到相同结果。
- PAPER、LIVE、BACKTEST 的 intent/order/ExitPlan/outbox 命名空间隔离，environment 不能原地升级。

### 21.6 迁移测试

- owner backfill 后普通策略、人工命令和 ExitPlan 的既有行为等价。
- Engine/API/QMT Agent 原子切到 protocol 1.2；不存在 1.1 command 加 metadata owner 的旁路。
- 新 `T_ASSISTANT_EXECUTION` 全链没有伪造 `strategy_run_id`，owner 缺失不触发 legacy fallback。
- 切换窗口后 legacy 做 T owner 不再产生新 ENTRY；其 BUY ORDER/TRADE 由原 owner 收敛，已有
  ExitPlan 由 `EXIT_PLAN/plan_id` 收敛并保留 legacy source ref。
- 结果未知的旧 pending/outbox、部分成交 batch 和 ExitPlan 不会被改挂、复制或由新 owner 重发。
- DRAINING 旧 owner 的本地义务对新 Coordinator 可见，同标的冲突被拒绝。
- 旧义务归零后删除做 T StrategyRun 专用路径，数据库和应用不再双写 owner。

### 21.7 上线验收

必须同时满足：

1. 新做 T 配置、execution、cycle、symbol state、intent、batch 和 ExitPlan 来源不依赖
   `StrategyRun/strategy_run_id`。
2. 公共执行链使用强类型 `ExecutionOwnerRef`，没有 metadata-only owner、默认 StrategyRun 或
   owner 缺失 fallback。
3. 没有任何 SymbolTEngine 或模型读取账户、envelope 或调用执行服务。
4. material cycle 的状态、证据和意图原子提交；所有候选均能还原 snapshot 与 score watermark。
5. 没有第二套现金/库存余额、Reservation 真源和 SELL FSM。
6. 所有未执行候选都有明确淘汰原因，`CAP` 只存在于 allocation decision。
7. 最终命令按确定排名进入账户锁，并重新校验完整 QMT 快照和本地未覆盖义务。
8. 至少完成规定数量的 CANARY_CONFIRM 闭环且无重复单、超现金、超老仓或 T+1 违规。
9. 回测和实盘使用同一个 `StrategyBase.step(SNAPSHOT)`、Coordinator、Gate 和 scorer 语义。
10. PAPER/LIVE/BACKTEST 隔离；切换后唯一 READY QMT Agent 使用 protocol 1.2，Engine/QMT
    断线、乱序回报和重启恢复测试通过。
11. 活动退出计划在所有 ENTRY、切换和模型故障场景下仍保持原 plan owner、source ref、唯一计划
    和可恢复性，且不依赖 source execution 存活。
12. DEVELOPMENT/FINAL、发布门禁、安全制品和人工 binding 闭环通过；训练 run 不改变交易配置。
13. ACTIVE 只读取完整分钟、冻结 cache revision 的 CPU 模型分数；模型不直接定仓或下单。
14. legacy 做 T owner 已排空并移除专用入口；普通 StrategyRun 策略不受独立做 T 架构影响。

## 22. 最终架构判断

“每标的一 T Engine + 全账户统一协调”方向是合理的，但必须按 QuantX 边界做以下修正：

1. 做 T 由 `TAssistantConfig + TAssistantExecution` 拥有，不再依赖 `StrategyRun`；公共链只依赖
   `ExecutionOwnerRef`。
2. `SymbolTEngine` 只输出机会质量，不能申请具体股数、读取现金或下单。
3. `TTradingEnvelope` 只在组合/执行层表达账户快照下的规划边界，不形成第三仓位桶或第二真源。
4. 模型只用完整 1 分钟特征输出可校准的候选质量，Tick 继续负责 V3 规则和执行重验；模型
   不直接定仓、不替代硬门禁。
5. `PortfolioTCoordinator` 只做排名和预算上限，最终合法数量及容量仍由公共执行链决定。
6. 所谓 ExecutionManager、ReservationManager 和 TRound 不应在 QuantX 中复制成第二套真源，
   而应分别映射到现有 `TradeIntentProcessor/OrderSizer/Risk`、
   `AccountCapacityService`、`TTradeBatch + ExitPlan + durable order facts`。
7. 模型训练复用现有不可变数据/spec、DEVELOPMENT/FINAL、Worker、GPU 资格、安全 artifact 和
   发布门禁能力；做 T 保留自己的特征、标签、组合评估、注册表与 runtime binding。

对应的独立设计见：

- [买入/卖出计划独立领域新架构设计](买入卖出计划新架构设计.md)
- [打板助手独立领域新架构设计](打板助手新架构设计.md)

三个领域只复用以下公共地基：

| 可复用 | 各功能必须独立拥有 |
|---|---|
| `ExecutionOwnerRef`、OwnerRuntimeRouter、TradeIntent 受理 | 配置聚合与 plan/execution 生命周期 |
| 审批、OrderSizer、Risk、AccountCapacityService | 决策输入、领域状态、原因码和 UI 语义 |
| pending/outbox/inbox、QMT 报告收敛 | 候选/计划/打板规则与业务约束 |
| `ExitPlanRuntime`、BucketLedger、T+1、审计关联 | 各自的回测结果与发布授权 |
| 模型训练公共原语、安全制品和发布门禁 | 各自的特征、标签、模型指标和 runtime binding |

买入计划使用 `ENTRY_PLAN/plan_id`，打板助手使用
`BOARD_ASSISTANT_EXECUTION/execution_id`；它们不应复用 `TAssistantExecution`，更不应回到
`StrategyRun`。所有真实 BUY fill 创建的 ExitPlan 再以 `EXIT_PLAN/plan_id` 独立执行。

按本设计落地后，系统获得真正的多标的机会竞争和共享资金调度，同时继续保留 QuantX
最重要的安全属性：策略纯净、账户状态唯一、订单可靠投递、QMT 回报为真、T+1 合法、
退出唯一所有者和全链可审计。
