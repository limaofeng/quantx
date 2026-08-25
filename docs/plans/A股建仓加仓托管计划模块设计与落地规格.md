# A 股建仓/加仓托管计划模块设计与落地规格

**文档状态：** 设计基线，待实现

**基线日期：** 2026-08-20

**产品名称：** 买入管理

**领域名称：** `EntryPlan`

## 1. 核心结论

QuantX 新增产品模块 `EntryPlan`，统一承载无持仓时的建仓和已有持仓时的加仓。
它由固定单标的 `AshareManagedEntryPlanStrategy` 执行，不新增一条绕过
`StrategyBase.step()` 的公共买入引擎。两者不是两套引擎，也不持久化一个容易
失真的“建仓/加仓”枚举：
页面根据计划启动时的真实持仓投影显示“建仓中”或“加仓中”，领域状态机始终
使用同一套计划语义。

`EntryPlan` 与 `ExitPlan` 复用执行基础设施，但不做机械镜像：

- 卖出计划的核心约束是保护数量、T+1 与退出完成。
- 买入计划的核心约束是目标暴露、总预算、分批节奏、现金缓冲、禁止追高和
  自动授权。
- 计划只回答“在什么条件下提出多大的买入意图”，不计算真实可买股数。
- 趋势、回撤和价格阶梯只在 `AshareManagedEntryPlanStrategy.step()` 内评估；
  PAPER、LIVE 和 BACKTEST 调用同一份策略代码。
- 最终数量、资金、100 股、停牌、涨跌停、价格 tick、冻结和 Broker 路由继续
  由 `OrderSizer`、`OrderRiskLayer` 与统一订单状态流裁决。
- 只有 QMT Agent 上报的真实成交回报才能累计已建仓数量和已用预算；
  `TradeIntent`、提交成功、`command_ack` 和委托已报均不是成交。

产品入口新增“买入管理”，固定承载：

1. 建仓/加仓计划。
2. 待确认买入。
3. 买入记录与计划事件。

一期只做固定单标的计划，不自动选股，不做跨标的资金最优分配，不增加多账户、
多租户或兼容旧协议设计。

---

## 2. 与现有架构的关系

### 2.1 直接复用的能力

| 现有能力 | 复用方式 |
|---|---|
| `StrategyBase.step(StrategyInput)` | `AshareManagedEntryPlanStrategy` 的唯一决策入口，回测/模拟/实盘同构 |
| `StrategyRun / StrategyRunState` | 一对一承载产品计划配置、运行生命周期与 `managed_entry_plan` 算法状态 |
| `TradeIntent` | 策略只生成 `BUY`；最终仓位目标先归一成剩余增量，再使用 `TARGET_AMOUNT / TARGET_VOLUME` |
| `StrategyExecutor` | 消费 BUY intent、处理人工确认并进入统一下单编排 |
| `OrderSizer` | 把计划已计算出的本批增量金额或数量转换为 A 股合法订单草案 |
| `ContextRiskLayer` | 在评估前提供禁买、最大仓位、单日加仓、现金缓冲等硬上限 |
| `OrderRiskLayer` | 在具体订单形成后校验资金、冻结、时段、停牌、涨跌停和重复订单 |
| `BucketLedger` | 真实买入成交后归因到 `core` 或 `swing` |
| durable inbox / runtime event | 订单与成交回报幂等收敛，支持乱序、重试和重启恢复 |
| `ExitPlanTemplate` | 可选地随买入意图携带；真实买入成交后才激活卖出保护 |
| GraphQL 预览—确认挑战 | 实盘自动买入授权复用同一安全模式，不以普通复选框代替授权 |

### 2.2 必须新增的能力

| 新增能力 | 责任边界 |
|---|---|
| `ManagedEntryPlanConfig / ManagedEntryPlanState` | 目标、规则、分批、完成条件、pending stage 和可恢复算法状态 |
| `ManagedEntryRuleRegistry / ManagedEntryPlanEvaluator` | 由策略 `step()` 调用，使用严格因果输入判断触发 |
| `AshareManagedEntryPlanStrategy` | 固定标的薄适配器，唯一输出 BUY TradeIntent 与 RuntimeStatePatch |
| 自动建仓精确授权 | 设备、主体、唯一账户、运行/配置版本、预算和价格边界绑定的限时授权 |
| EntryPlan 产品投影 | 从 StrategyRun、StrategyRunState、intent、订单和真实成交生成前端视图 |
| EntryPlan GraphQL | 查询、创建、更新、暂停、取消、预览、授权和人工确认 |
| 买入管理页面 | 计划编辑、状态监控、待确认和事件记录 |

产品层 `plan_id` 是稳定计划身份，`StrategyRun.run_id` 是某个不可变配置版本的运行
身份；GraphQL
把该特定策略运行投影成用户可理解的 EntryPlan，但不把它暴露为通用策略配置页。
`StrategyRun.parameters` 保存权威配置，`StrategyRunState.custom_state` 的
`managed_entry_plan` 系统键保存算法状态；意图、订单、成交和 BucketLedger 继续
使用各自事实表。禁止再建一张同时保存同样动态状态的 `entry_plans` 表。

---

## 3. 产品语义与一期边界

### 3.1 “托管”的准确含义

托管表示系统在用户给定的固定标的、目标暴露、预算和授权范围内持续监控，并在
规则满足时提出或自动路由买入意图。托管不表示：

- 保证获利。
- 自动判断应该买哪只股票。
- 可以突破用户预算或账户风控。
- 可以在行情、账户或 QMT 状态不明时继续买入。
- 可以根据未验证模型自行修改硬风险上限。

计划可以自动识别趋势并调整**本次意图的节奏和建议金额**，但调整始终被计划
硬上限和公共风控向下裁剪。数据缺失只允许减小、延迟或拒绝买入。

### 3.2 建仓与加仓的统一判断

前端按最新组合投影展示：

```text
当前总持仓 == 0  -> 建仓计划
当前总持仓 > 0   -> 加仓计划
```

该标签只是展示语义。外部人工交易可能随时改变真实持仓，因此执行前必须重新读取
最新 `PortfolioState`，不能把创建时标签作为交易事实。

### 3.3 一期支持

- 固定一只 A 股标的。
- 每只标的最多一个活跃 `EntryPlan`。
- 买入 bucket 只允许“核心仓 `core`”或“活跃仓 `swing`”。
- 趋势回撤、价格阶梯、人工触发三类规则。
- 总预算、目标仓位比例或计划新增股数三种目标表达。
- 分批数量、单笔/单日上限、最小触发间隔、最高可买价和有效期。
- 模拟自动、实盘人工确认、实盘自动授权三种执行方式。
- 真实成交后可选自动挂接统一卖出保护计划。

### 3.4 一期明确不做

- 自动选股、账户级候选池轮动或跨标的评分。
- 同一标的多个活跃买入计划竞争。
- 买入 `locked_core`；封存必须是成交后的独立人工归因操作。
- 马丁格尔、无限补仓、亏损自动放大金额。
- 机器学习在线自改阈值或不可解释的黑盒追涨。
- 计划预算的长期资金冻结；只有真实待成交订单冻结资金。
- 隐式实盘授权、静默从人工确认升级为自动执行。
- 为旧接口保留双协议或兼容适配器。

---

## 4. 领域模型

### 4.1 `ManagedEntryPlanConfig`

```text
ManagedEntryPlanConfig
├── template_version
├── instrument_code
├── bucket                         // core | swing
├── target_policy: EntryTargetPolicy
├── trigger_rules: EntryRuleSpec[]
├── pacing_policy: EntryPacingPolicy
├── execution_policy: EntryExecutionPolicy
├── completion_policy: EntryCompletionPolicy
└── exit_plan_template?            // 可选成交后卖出保护
```

配置保存在该运行的 `StrategyRun.parameters.managed_entry_plan`。
`instrument_code` 创建后不可修改。真实成交发生后 `bucket` 不可修改。`swing`
计划在实盘自动模式下必须配置 `exit_plan_template`；`core` 可以不配置，但前端
必须明确显示“成交后无自动卖出保护”。

### 4.2 `EntryTargetPolicy`

```text
EntryTargetMode =
    TARGET_POSITION_PCT
  | INCREMENTAL_AMOUNT_CNY
  | ADDITIONAL_VOLUME

EntryTargetPolicy
├── mode
├── target_position_pct?
├── incremental_amount_cny?
├── additional_volume?
├── max_total_amount_cny           // 所有模式必填的累计人民币授权上限
├── max_position_pct               // 始终必填的绝对仓位上限
└── baseline_snapshot

EntryBaselineSnapshot
├── position_volume
├── market_value_cny
├── total_asset_cny
├── reference_price
└── account_snapshot_version
```

约束：

- 三种目标字段只能有一个生效。
- `max_total_amount_cny` 对所有模式必填，防止净值增长使百分比计划无限扩张。
- `max_position_pct` 是硬上限，不得因趋势增强而提高。
- `INCREMENTAL_AMOUNT_CNY` 表示本计划最多累计新增投入的人民币金额，不表示
  创建时冻结资金。
- `ADDITIONAL_VOLUME` 表示本计划累计新增股数，避免把“最终总持仓”和“本次新增”
  混为一谈；每批仍映射为标准 `TARGET_VOLUME` TradeIntent，并由 `OrderSizer` 按
  100 股规范化。
- 目标不得小于已真实成交的累计结果；提高目标需要重新进行实盘自动授权。
- 外部人工买入使真实仓位达到目标时，计划可以完成但不得把外部成交记为计划成交；
  外部卖出不得扩大创建时 `max_total_amount_cny` 已授权的最大新增风险。

`TARGET_POSITION_PCT` 在产品配置中表达**成交后的最终总仓位**。当前
`OrderSizer` 直接用 `total_asset × target_position_pct` 作为本次订单金额，尚未
扣除现有持仓和待成交买单，不能直接供重复分批计划使用。ManagedEntryPlanStrategy
必须先用本次 `StrategyInput` 的权威只读快照计算剩余缺口：

```text
target_market_value = total_equity_cny × target_position_pct
current_market_value = latest_position_volume × executable_price
pending_buy_exposure = Σ 未完成 BUY 的剩余数量 × 保护限价
remaining_gap_cny = max(0,
  target_market_value - current_market_value - pending_buy_exposure)
this_tranche_cny = min(remaining_gap_cny, pacing/risk/plan caps)
```

`EntryGapCalculator` 只得到本批剩余增量，并输出标准 `TARGET_AMOUNT` 或
`TARGET_VOLUME` TradeIntent；它不替代最终资金和合法股数裁决。在全局
`TARGET_POSITION_PCT` 语义另行原子修正前，EntryPlan 禁止把该字段直接透传给
现有 OrderSizer，也不得依赖后置风控碰巧把重复整笔目标裁小。
若真实仓位已达到或超过目标，缺口固定为零并完成计划；EntryPlan 永远不会为了
回到目标而生成反向 SELL。

### 4.3 `EntryRuleSpec`

```text
EntryRuleSpec
├── rule_id
├── rule_type                      // 注册表字符串
├── priority
├── parameters
├── once
└── enabled
```

同一计划的触发规则为 OR，优先级最高的匹配规则产生本轮决策。公共安全门禁始终
为 AND，包括数据质量、交易时段、计划上限、待成交锁、前置风控和最高可买价。
前端不向普通用户展示 OR/AND 表达式或原始 JSON。

一期规则：

| 规则 | 用途 | 一期 |
|---|---|---|
| `TREND_PULLBACK_CONFIRMATION` | 日线趋势成立，盘中回撤后企稳反弹时分批买入 | 是 |
| `PRICE_LADDER` | 到达预设价格档位时按一次性层级分批买入 | 是 |
| `MANUAL_TRIGGER` | 用户明确触发一批，仍重新经过实时风控 | 是 |
| `BREAKOUT_CONFIRMATION` | 放量突破后确认，带追高保护 | 二期评估 |
| `TIME_SCHEDULED` | 指定交易日/时点定额买入 | 二期评估 |

`EntryRuleRegistry` 使用字符串注册，不用封闭枚举限制后续策略扩展；一期公开
能力列表必须由服务端 GraphQL capabilities 返回，前端不得硬编码一套不同参数。

### 4.4 趋势回撤规则

`TREND_PULLBACK_CONFIRMATION` 分成三个确定性阶段：

```text
日线趋势门禁
  -> 盘中回撤观察
  -> 反弹/量价确认
  -> 生成一批 BUY TradeIntent
```

规则只使用当前决策时点及之前的数据：

- 日线趋势可使用快慢 EMA、斜率、价格相对均线和环境评分。
- 盘中窗口记录最近高点、低点、回撤、反弹、VWAP 偏离和成交额速度。
- 数据窗口、指标版本和参数包版本必须进入 `DecisionTrace`。
- 强趋势可提高本批建议系数，弱趋势可降低或暂停，但不得突破单笔、单日、预算、
  目标仓位、现金缓冲和最高可买价。
- 参数预设由服务端版本化提供“稳健/均衡/积极”，上线默认只允许模拟模式；
  未经固化回放与样本外验证不得把某组数值宣称为普适最优。

可持久化的算法状态包括：因果观察窗、趋势状态、最近高低点、候选触发时间和
冷却时间。不得持久化或修改真实现金、真实持仓和冻结资金。

### 4.5 价格阶梯规则

```text
PriceLadderLevel
├── level_id
├── trigger_price
├── tranche_value                 // 金额、目标比例增量或股数之一
├── once = true
└── priority
```

- 同一价格档位只允许成功形成一次待处理意图。
- “成功形成”不等于完成；规则在真实成交量大于 0 后按成交部分累计。
- 买单拒绝、过期或零成交撤单后可以按冷却策略重新武装，不能静默永久跳过。
- 修改阶梯时不得删除已有真实成交事实。
- 当前价格已远低于多个档位时，不允许同一 tick 一次性叠加多个买单；按优先级
  只处理一档，待真实订单终态后重新评估。

### 4.6 `EntryPacingPolicy`

```text
EntryPacingPolicy
├── tranche_count
├── max_single_intent_amount_cny
├── max_daily_filled_amount_cny
├── max_orders_per_day
├── min_interval_seconds
├── max_open_orders = 1
├── cooldown_after_reject_seconds
└── trend_adjustment_enabled
```

计划只计算本批**期望意图**，趋势调整后的金额必须满足：

```text
expected_tranche
  <= remaining_plan_budget
  <= max_single_intent_amount_cny
  <= RiskContextCaps.max_buy_amount_cny
```

具体可买数量和可用现金仍由执行层确定。`max_open_orders` 一期固定为 1，不做
可配置并行订单。

每次触发还必须计算单调递减的计划/授权容量：

```text
remaining_authorized_cny
  = max_total_amount_cny
  - 本计划真实成交金额
  - 本计划工作中 BUY 的保护价预留

expected_tranche_cny = min(
  remaining_authorized_cny,
  target_gap_cny,
  max_single_intent_amount_cny,
  daily_remaining_cny,
  RiskContextCaps.max_buy_amount_cny,
  position_cap_remaining_cny,
  liquidity_cap_cny
)
```

策略使用这些快照只表达更保守的期望金额；OrderRiskLayer 仍以最新资金、冻结、
佣金和 Broker 事实做最终 CAP/REJECT。

### 4.7 `EntryExecutionPolicy`

```text
EntryEnvironment = PAPER | LIVE
EntryAuthorizationMode = MANUAL_CONFIRM | AUTO

EntryExecutionPolicy
├── environment                     // PAPER | LIVE
├── authorization_mode              // MANUAL_CONFIRM | AUTO
├── price_reference               // ASK1_PROTECTED_LIMIT | LATEST_PROTECTED_LIMIT
├── max_slippage_bps
├── max_price_deviation_bps
└── approval_ttl_ms
```

- 一期不使用无保护市价单。
- 执行环境与授权方式是两个独立维度；前端可显示为三张场景卡，但不得在契约中
  混成一个难以审计的枚举。
- 实盘人工确认意图进入 `AWAITING_APPROVAL`，确认时重新读取卖一价、资金、持仓、
  计划版本和所有风控条件。
- 实盘自动模式必须经过设备绑定的预览—确认挑战，授权快照精确绑定当前用户主体、
  设备会话、唯一解析的 Broker 账户、标的、bucket、计划目标、累计人民币额度、
  单笔/单日上限、最高可买价、有效期和配置版本。
- 任一绑定字段变化都会使旧授权失效。
- Kill Switch、对账不一致、账户权限丢失、授权过期或不可解释的外部买入会撤销
  自动授权并降级为人工确认；本计划自身的合法成交只单调消费授权余额，不应每成
  交一批就使授权无条件失效。
- LIVE AUTO 必须在 StrategyExecutor 准备入队前和 TradeCommandService 原子写入
  outbox 前各复核一次。买入是增险行为，不得复用 SELL 的“风险降低自动授权”豁免。
- 配置中不保存可直接写入的 `auto_entry_authorized` 布尔值；前端复选框只能表达
  用户希望申请 AUTO。`EntryPlanView.authorizationState` 必须由未撤销、未过期且
  指纹完全匹配的授权 grant 推导。

### 4.8 `EntryCompletionPolicy`

```text
EntryCompletionPolicy
├── expire_at_ms?
├── max_buy_price
├── stop_when_target_reached = true
├── stop_when_budget_exhausted = true
└── cancel_unsubmitted_on_expiry = true
```

最高可买价是计划级硬门禁。行情超过该价格时显示“等待价格回到允许范围”，不能
因为趋势强势自动抬高。到期只阻止新意图；已经可能到达 Broker 的订单必须进入
`DRAINING` 并等待真实回报或对账，不能直接标成已取消。

---

## 5. 计划状态机

### 5.1 状态

```text
EntryPlanStatus =
    ARMED               // 尚无成交，正在监控
  | ACCUMULATING        // 已有真实成交，继续监控剩余目标
  | AWAITING_APPROVAL   // 已有人工确认意图，尚未创建 Broker 命令
  | ENTRY_PENDING       // 已路由订单、待成交或回报尚未收敛
  | PAUSED              // 人工暂停，不产生新意图
  | DRAINING            // 已请求取消/到期，但已有可能产生副作用的订单
  | COMPLETED           // 目标达到或预算耗尽
  | EXPIRED             // 到期且无待收敛订单
  | CANCELLED           // 人工取消且无待收敛订单
  | ERROR               // 需要人工处理的计划错误
```

这是 `EntryPlanView.phase` 的产品投影，不新增一套可独立写入的数据库状态枚举：
`StrategyRun.status` 表示运行生命周期，`managed_entry_plan.phase` 表示算法阶段，
待确认意图、订单与成交事实共同决定最终展示状态。

客户端编辑中的未保存内容是本地草稿，不为服务端增加 `DRAFT` 状态。
`createEntryPlan` 默认创建 PAUSED StrategyRun；模拟或实盘人工模式只有在用户明确
选择“保存并启动”时才原子进入 `ARMED`。实盘自动计划必须先保持 PAUSED，完成
授权挑战后才能进入 `ARMED`。

### 5.2 主路径

```text
create / authorize
  -> ARMED
  -> rule matched
  -> AWAITING_APPROVAL（人工）或 ENTRY_PENDING（已精确授权自动）
  -> confirm + realtime risk（人工）
  -> ENTRY_PENDING
  -> real partial/full BUY trade
  -> ACCUMULATING
  -> next rule / tranche
  -> ENTRY_PENDING
  -> target reached
  -> COMPLETED
```

异常路径：

```text
AWAITING_APPROVAL + reject/expire
  -> ARMED or ACCUMULATING

ENTRY_PENDING + proven pre-broker reject/no fill terminal
  -> ARMED or ACCUMULATING

ARMED / ACCUMULATING + pause
  -> PAUSED

cancel/expiry + no broker side effect
  -> CANCELLED / EXPIRED

cancel/expiry + delivered/accepted/partial order
  -> DRAINING
  -> terminal reports and trade reconciliation
  -> CANCELLED / EXPIRED / COMPLETED

unexplained broker or ledger mismatch
  -> ERROR + RECONCILE_REQUIRED
```

### 5.3 防重复不变量

- 同一计划任意时刻最多一个未完成 `pending_intent_id`。
- `AWAITING_APPROVAL / QUEUED / DELIVERED / SUBMITTED / ACCEPTED /
  PARTIALLY_FILLED / RECONCILE_REQUIRED` 都阻止同计划新意图。
- `DELIVERED / SUBMITTED / ACCEPTED / PARTIALLY_FILLED` 均阻止新触发。
- 业务幂等键至少包含 `plan_id + config_version + rule_id + trigger_sequence`。
- `intent_id` 与 outbox idempotency key 从稳定业务键确定性生成，禁止每个 tick
  仅靠随机 UUID 和内存 pending 去重。
- 委托终态先于成交明细到达时保留 pending，直到成交明细已应用或对账证明零成交。
- Engine 重启后先恢复计划、意图、订单和回报屏障，再消费新行情。
- 相同成交业务键只能累计一次计划数量、金额和 bucket 归因。
- 同标的存在未完成 SELL、持续清仓或无法解释的外部订单时，禁止产生新的 BUY。
- 全局自动买入门、Kill Switch、`allow_buy=false`、只减仓模式、行情未 READY 或
  durable barrier 任一成立时都必须失败关闭。

---

## 6. 标准执行链路

```text
MarketData / OrderEvent / TradeEvent / ReconcileCommand
  -> StrategyInputBuilder
  -> AshareManagedEntryPlanStrategy.step(StrategyInput)
  -> ManagedEntryPlanEvaluator
  -> EntryDecision
  -> BUY TradeIntent(owner_type=STRATEGY_RUN, owner_id=run_id)
  -> StrategyExecutor
  -> OrderSizer
  -> OrderRiskLayer
  -> AWAITING_APPROVAL 或 TradeCommand
  -> QMT Agent / BacktestBroker
  -> durable inbox
  -> BrokerExecutionReport / TradeExecutionEvent
  -> RuntimeStateManager
  -> PortfolioState + BucketLedger
  -> AshareManagedEntryPlanStrategy.on_order()/on_trade()
  -> RuntimeStatePatch(managed_entry_plan)
  -> 可选 ExitPlanBook.register_entry_fill()
```

### 6.1 `EntryEvaluationContext`

```text
EntryEvaluationContext
├── decision_time_ms
├── trade_date
├── instrument_code
├── market_data_snapshot
├── market_context_snapshot
├── risk_context_caps
├── position_adjustment_profile
├── portfolio_snapshot             // 只读
├── bucket_ledger_snapshot         // 只读
├── pending_order_summary          // 只读
└── data_quality
```

评估器可以读取这些快照决定是否提出意图，但不得写账户或自行判定最终合法股数。
缺少最新账户快照、证券状态、涨跌停价或关键行情时，实盘禁止新增买入。

### 6.2 策略适配边界

- `AshareManagedEntryPlanStrategy` 是真实固定标的策略，不是 API 层伪造的运行。
- 它的 `step()` 只负责把标准 `StrategyInput` 交给纯评估器，并输出 BUY
  `TradeIntent[] + RuntimeStatePatch`。
- 评估器不得访问数据库、网络、文件、Broker 或 QMT；真实账户字段只读且不能
  写入 RuntimeStatePatch。
- 人工触发先持久化一个 plan command，由 Engine 构造新的标准 StrategyInput
  再调用 `step()`；GraphQL resolver 不得直接创建 BUY intent。
- 不恢复 `on_tick / on_bar / generate_signal` 主路径。
- 该 StrategyRun 只绑定一个 `instrument_code`，不得从账户持仓或行情全表自行选股。

### 6.3 与卖出计划衔接

若模板包含 `exit_plan_template`：

1. BUY intent 只携带模板快照和来源标识，不激活卖出计划。
2. 每条真实 BUY `TradeExecutionEvent` 按实际成交量调用统一退出计划登记。
3. 部分成交只保护已成交数量。
4. 买单拒绝、撤单和 `command_ack` 不创建受保护数量。
5. 一期按 `stage_id` 为每个真实成交批次建立独立 ExitPlan，保留各自真实成本、
   峰值和追踪底线；不得把新加仓静默并入旧追踪止盈并重算其基线。
6. EntryPlan 取消后，已经激活的 ExitPlan 继续存在；买入自动授权不自动授予卖出
   权限，ExitPlan 仍执行自己的精确授权契约。

未来若引入聚合退出，必须先定义加权成本、峰值、动态底线和部分成交的确定性
迁移规则并完成回放验证，不能为了减少记录而默认聚合。

---

## 7. 持久化与 Engine 编排

### 7.1 数据库真源

不新增重复的 `entry_plans` 动态状态表。唯一真源映射为：

```text
StrategyRun
├── id                            // 当前不可变 StrategyRun.run_id
├── strategy_id                   // ashare_managed_entry_plan
├── instruments                   // 长度固定为 1
├── mode                          // BACKTEST | PAPER | LIVE
├── status                        // PENDING/RUNNING/PAUSED/终态
└── parameters.managed_entry_plan // ManagedEntryPlanConfig + config_version

StrategyRunState.custom_state.managed_entry_plan
├── phase
├── pending_intent_id?
├── pending_client_order_id?
├── pending_stage_id?
├── pending_requested_volume
├── pending_filled_volume
├── reserved_amount_cny
├── order_terminal_seen
├── trade_reconciled
├── rule_state
├── rule_activation_counts
├── completed_rule_ids
├── daily_order_counts
├── daily_filled_amounts
├── last_fill_at_ms?
├── retry_after_ms?
├── last_decision
└── data_quality

现有事实表
├── strategy_trade_intents
├── pending_trade_orders / outbox
├── broker order/trade durable inbox
├── StrategyDecisionTrace
├── PortfolioState
├── BucketLedger
└── ExitPlan

新增 entry_plan_authorization_grants
├── authorization_id
├── run_id
├── config_version
├── plan_fingerprint
├── instrument_code
├── bucket
├── rule_fingerprint
├── user_principal_id
├── device_session_id
├── resolved_account_fingerprint
├── max_total_amount_cny
├── max_single_order_amount_cny
├── max_daily_amount_cny
├── max_position_pct
├── max_buy_price
├── max_slippage_bps
├── max_price_deviation_bps
├── authorized_account_snapshot_version
├── plan_valid_until
├── authorized_at
├── expires_at
├── revoked_at?
└── revocation_reason?
```

QuantX 是个人单账户系统，GraphQL 输入不增加可任选的 `accountId`，新授权表也
不引入多账户或租户归属模型。授权仍必须绑定运行时唯一解析账户的安全指纹，防止
凭证或账户上下文变化后沿用旧授权。若唯一账户不可解析，计划只能查看和编辑，
禁止激活或执行。

产品 `EntryPlanView` 从上述真源组合生成。真实已买数量、金额和均价从关联成交
事实汇总，不以算法状态中的缓存字段替代。事件时间线由 DecisionTrace、意图、
订单、成交和授权审计归一化生成，不落第二份可独立推进的计划事件状态机。数据库
业务表是状态真源；Redis 只允许唤醒和广播。

RuntimeState 中的 `pending_filled_volume / daily_filled_amounts` 只是携带已应用成交
业务键的算法派生计数，用于规则阶段和节奏恢复；它们不得写入 PortfolioState、
不得绕过成交事实重算账户，也不能作为前端“真实已买”的唯一来源。

### 7.2 乐观并发和编辑限制

- 所有更新、暂停、恢复、取消都携带 `config_version`。
- `instrument_code` 创建后不可变。
- 有真实成交后 `bucket` 不可变。
- 有 `ENTRY_PENDING` 时禁止修改规则、目标和执行参数，只允许请求暂停或取消；
  订单收敛后再编辑。
- 降低目标不得低于已成交结果。
- 提高目标、预算、最高可买价、单笔/单日上限或延长有效期会使实盘自动授权失效。

### 7.3 StrategyManager / StrategyExecutor 恢复

- 不新增 `EntryPlanMonitor`；行情、订单、成交和对账 cadence 继续由
  StrategyManager/StrategyExecutor 驱动固定标的 StrategyRun。
- 恢复 `RUNNING / PAUSED / PENDING` 的 managed-entry 运行及其 RuntimeState。
- `AWAITING_APPROVAL` 使用现有意图事实恢复原始 TTL；到期后终结意图，不重建
  确认窗口。
- 运行状态、RuntimeState、Portfolio、BucketLedger、ExitPlan 与 event marker
  按现有 durable runtime 原子快照提交。
- 策略或计划错误不能吞掉；进入 `ERROR / RECONCILE_REQUIRED` 并保留最近输入、
  原因码和恢复入口。
- 禁止在账户查询失败后使用空账户，也禁止人工构造 `is_trading=true`、
  `suspended=false` 的“安全行情”。缺权威账户、证券状态、价格 tick 或涨跌停价
  时以 `ACCOUNT_SNAPSHOT_UNAVAILABLE / SECURITY_STATUS_UNAVAILABLE` 失败关闭。

Engine 启动时必须按以下顺序恢复：

```text
StrategyRun / RuntimeState / TradeIntent / PendingTradeOrder / outbox / 预留
  -> 重放未应用 durable order/trade event
  -> 拉取 Broker 全量资金、持仓、冻结、委托和成交
  -> 收敛 pending 与计划进度
  -> 对账 READY + 行情 READY + 无 durable barrier
  -> 才允许评估新触发
```

不能先恢复行情评估，再异步补计划 pending；`SUBMITTED / ACCEPTED` 状态不确定时
进入 `RECONCILE_REQUIRED`，不得靠超时自动补发。

### 7.4 计划容量与真实资金冻结

- `AWAITING_APPROVAL` 只占用计划阶段和授权容量，不伪造成 Broker 冻结现金；
  人工确认时重新检查真实可用资金。
- 已进入原子 outbox/路由的买单按保护限价、剩余数量和费用预留完整资金。
- 部分成交只消费实际成交金额，未成交部分继续预留。
- 拒单、撤单或过期只在权威终态且迟到成交已收敛后释放未成交预留。
- 计划总预算不在创建时长期冻结；多标的计划竞争时，当前权威现金、工作中 BUY
  和公共风险优先级决定本轮 CAP/DELAY/REJECT，所有结果必须可审计。
- 计划行锁、活跃意图、工作中订单、授权余额和 outbox 创建必须处于可恢复事务
  边界，数据库唯一业务键是并发 tick 的最后防线。

---

## 8. GraphQL 契约设计

GraphQL 使用唯一当前账户上下文，不在新接口重复暴露 `accountId`。

### 8.1 Query

```graphql
entryPlans(instrumentCode: String, statuses: [EntryPlanStatus!]): [EntryPlan!]!
entryPlan(planId: ID!): EntryPlan
entryPlanCapabilities: EntryPlanCapabilities!
entryPlanEvents(planId: ID!, limit: Int = 100): [EntryPlanEvent!]!
entryPlanFundingPreview(input: EntryPlanDraftInput!): EntryPlanFundingPreview!
pendingEntryIntents(instrumentCode: String): [EntryIntent!]!
entryAutomationStatus: EntryAutomationStatus!
```

`entryPlanCapabilities` 返回规则卡、预设、字段定义、单位、范围、默认值和帮助文本。
前端不维护另一份参数真源。

```graphql
ruleTypes {
  ruleType
  label
  category
  description
  suitableFor
  warning
  fields {
    key
    label
    type
    unit
    required
    min
    max
    step
    helpText
    advanced
  }
  presets {
    presetId
    label
    summary
    parameters
  }
}
```

动态参数可以在后端配置/运行参数中序列化，但前端只能按 capability 字段编辑，
不能提供原始 JSON 文本框。

### 8.2 Mutation

```graphql
createEntryPlan(input: CreateEntryPlanInput!): EntryPlan!
updateEntryPlan(input: UpdateEntryPlanInput!): EntryPlan!
setEntryPlanEnabled(planId: ID!, enabled: Boolean!, configVersion: Int!): EntryPlan!
cancelEntryPlan(planId: ID!, configVersion: Int!): EntryPlan!
evaluateEntryPlanNow(planId: ID!): EntryPlan!
setEntryAutomationPaused(paused: Boolean!, reason: String!): EntryAutomationStatus!

previewEntryPlanAuthorization(input: EntryPlanAuthorizationPreviewInput!):
  EntryPlanAuthorizationPreviewResult!
confirmEntryPlanAuthorization(input: EntryPlanAuthorizationConfirmationInput!):
  EntryPlanAuthorizationConfirmationResult!

previewEntryIntent(intentId: ID!): EntryIntentPreviewResult!
confirmEntryIntent(input: EntryIntentConfirmationInput!): EntryIntentConfirmationResult!
rejectEntryIntent(intentId: ID!): EntryIntentConfirmationResult!
```

创建输入使用强类型嵌套 input，不接受任意 JSON 字符串。`updateEntryPlan` 必须追加
不可变配置版本、创建新的 `StrategyRun`，并将稳定 `planId` 原子切换到新 `runId`，
不保留原地修改运行配置或旧协议分支。创建输入可显式携带
`startImmediately`，只允许 PAPER 或 LIVE+MANUAL_CONFIRM；LIVE+AUTO 必须拒绝
直接启动并先完成授权挑战。

### 8.3 Subscription

```graphql
entryPlanUpdated(planId: ID): EntryPlanUpdate!
entryIntentUpdated(instrumentCode: String): EntryIntentUpdate!
```

Subscription 只做状态通知。页面断线重连后必须用 Query 拉取数据库真源，不能把
WebSocket 消息本身当成完整计划状态。

### 8.4 前端生成与校验

GraphQL schema、resolver 和前端操作必须同一轮原子切换，并运行：

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```

禁止使用 `as any` 绕过生成类型。

---

## 9. 前端产品设计

### 9.1 路由与导航

新增独立路由：

```text
/entry-plans
页面标题：买入管理
导航位置：主导航“持仓”之后、“做 T 助手”之前，建议 `order: 25`
主题色：emerald / cyan，卖出管理继续使用 red
```

不把建仓功能塞进通用“策略管理”：底层虽然是一对一 StrategyRun，用户操作的却是
受限的建仓产品契约，不应看到任意策略参数和运行控制；也不放进卖出管理，避免
买卖授权和危险操作混在一个工作区。

页面复用 `PortfolioStudioShell / StudioWorkbench` 的左右工作台结构：

```text
┌──────────────────────────────────────────────────────────────────────┐
│ 买入管理  可用资金 / 今日已买 / 待确认  [暂停全部自动买入] [刷新]│
├──────────────┬───────────────────────────────────────────────────────┤
│ 标的与计划   │ [建仓/加仓计划] [待确认买入] [买入记录]              │
│              ├───────────────────────────────────────────────────────┤
│ 搜索股票     │ 计划摘要卡 / 编辑器 / 事件时间线                      │
│ 605499.SH    │                                                       │
│ 计划状态     │                                                       │
│ 当前持仓     │                                                       │
│ 最新行情     │                                                       │
├──────────────┴───────────────────────────────────────────────────────┤
│ QMT / 行情新鲜度 | 风控模式 | 自动授权状态 | 最近真实回报          │
└──────────────────────────────────────────────────────────────────────┘
```

左侧不是“全部持仓列表”，因为建仓标的可能尚无持仓。它展示已创建计划的标的，并
提供证券搜索创建入口。搜索结果必须来自证券主数据，不允许用户输入任意字符串后
直接进入实盘授权。

“暂停全部自动买入”是单账户公共安全门，不等于取消各计划，也不撤销可能已到达
Broker 的订单。它必须持久化、立即阻止新的 EntryPlan 自动意图、保留人工计划和
逐笔确认查看能力，并记录启停原因与操作者事件。恢复后各计划重新基于最新行情和
账户状态评估，禁止补发暂停期间错过的历史触发。

### 9.2 一级模式

| 模式 | 内容 |
|---|---|
| 建仓/加仓计划 | 活跃、暂停、待收敛计划和创建入口 |
| 待确认买入 | `AWAITING_APPROVAL` 意图、TTL、当前价格偏离和实时风控预览 |
| 买入记录 | 真实买入成交、拒绝、少买、延迟、撤单和计划事件 |

标签采用 Radix Tabs/语义化 `role=tablist`，不能用只能鼠标点击的普通 `div`。

### 9.3 创建流程

采用单页分区编辑器和右侧实时摘要，不做长向导，也不展示原始 JSON：

```text
① 买什么             股票、当前持仓、core/swing
② 买到多少           目标仓位/总预算/计划新增股数、最高可买价
③ 什么时候买         策略卡 + 稳健/均衡/积极预设
④ 怎么分批           批次数、单笔/单日上限、最小间隔
⑤ 成交后怎么办       可选统一卖出保护模板
⑥ 如何执行           模拟 / 实盘人工确认 / 实盘自动授权
```

桌面布局：

```text
┌────────────────────────────────────┬─────────────────────────┐
│ 配置区域                           │ 计划摘要                │
│                                    │ 目标：30%               │
│ [股票] [归因仓]                    │ 当前：12%               │
│ [目标方式卡片]                     │ 最多新增：18%           │
│ [策略卡片]                         │ 单笔上限：¥10,000       │
│ [分批和硬边界]                     │ 最高买价：¥128.00       │
│ [成交后保护]                       │ 现金缓冲：20%           │
│ [执行授权]                         │ 状态：需要实盘授权      │
│                                    │                         │
│ [保存并保持暂停] [预览授权并启动]  │ 风险/数据质量提示       │
└────────────────────────────────────┴─────────────────────────┘
```

两个操作按钮必须真实存在且文案与行为一致：

- `保存并保持暂停`：创建/更新 PAUSED StrategyRun，不会监控、不会生成买入意图。
- 模拟模式显示 `保存并启动模拟`。
- 实盘人工模式显示 `保存并开始监控`，命中后仍需逐笔确认。
- 实盘自动模式显示 `预览授权并启动`，完成挑战前不会启动。

不得在提示文字中声称存在一个页面上没有的“仅保存”按钮。

### 9.4 策略卡片

策略选择使用自定义卡片选择器，不用原生 `<select>`：

```text
┌ 趋势回撤建仓 · 推荐 ───────────────────────────────────────┐
│ 上涨趋势成立时等待回撤企稳，避免直接追逐瞬时拉升。          │
│ 适合：趋势股的分批核心仓建仓。                              │
│ 系统自动：趋势评分、回撤确认、量价强弱和节奏调整。          │
└─────────────────────────────────────────────────────────────┘

┌ 价格阶梯建仓 ──────────────────────────────────────────────┐
│ 到达你设定的价格档位时逐档买入，每档只处理一批。            │
│ 适合：已经明确可接受价格区间的计划。                        │
└─────────────────────────────────────────────────────────────┘

┌ 人工触发 ──────────────────────────────────────────────────┐
│ 系统保存预算、价格和风控边界，由你决定每批启动时点。        │
│ 适合：先验证资金与执行链路。                                │
└─────────────────────────────────────────────────────────────┘
```

卡片必须包含名称、效果、适用场景、系统自动做什么、仍需用户决定什么。高频字段
直接显示；EMA 周期、趋势斜率、观察窗等放入“专业参数”，并由服务端 capability
生成表单。普通模式没有 JSON 编辑器。

### 9.5 计划摘要卡

每张卡必须一眼回答：

- 正在建仓还是加仓。
- 当前状态和是否允许自动执行。
- 当前持仓、目标、真实已买和剩余计划量。
- 总预算、已用预算、单日剩余额度。
- 当前策略、最近一次为什么买/为什么没买。
- 是否有待确认、待成交或回报未收敛订单。
- 下一次最早可触发时间和计划有效期。
- 成交后是否有卖出保护。

主要操作：编辑、暂停触发、恢复、立即检查、取消。`ENTRY_PENDING / DRAINING`
时编辑按钮禁用并说明原因。暂停与撤单必须分开：

- `暂停触发`：不再产生新 BUY，当前工作中委托保持原状并继续收敛。
- `暂停并撤销买单`：停止新触发并发出幂等撤单请求，计划进入 DRAINING。

取消使用确认对话框，明确“停止新买入”和“已有委托仍需等待真实回报”的区别。
取消计划不会卖出已经买入的股份，也不会取消已经激活的 ExitPlan。

### 9.6 待确认买入

待确认卡展示：

```text
股票 / bucket / 触发策略 / 信号时间 / 剩余 TTL
触发参考价 / 当前卖一价 / 价格偏离
期望金额 / OrderSizer 候选数量 / 最新风控动作
计划累计预算 / 本日累计成交 / 现金缓冲
[拒绝] [确认并重新风控]
```

确认按钮不能直接复用旧预览。点击后先请求最新预览；若价格、数量、风险、计划
版本或 QMT 状态变化，必须展示新结果并要求再次确认。TTL 到期立即禁用确认，
不自动追价。

### 9.7 状态、颜色与反馈

保持现有深色金融终端：

- 页面背景沿用 `#080d18 / #0b1120`，不引入另一套全局主题。
- 买入主强调使用 emerald/cyan；上涨行情的红色仍遵循现有行情语义，不能仅靠
  红绿表达操作状态。
- `ARMED/ACCUMULATING` 使用图标 + 文案 + 颜色。
- `ENTRY_PENDING` 使用蓝色进度语义。
- `PAUSED/EXPIRED` 使用 slate/amber。
- `ERROR/RECONCILE_REQUIRED` 使用 rose，并提供可执行恢复入口。
- 创建、授权、暂停、取消都显示 loading、成功或错误反馈，不允许点击后无状态。

所有图标统一使用 `lucide-react`，不使用 emoji；hover 只改变颜色、边框或阴影，
不使用造成布局跳动的缩放动画。

### 9.8 响应式和无障碍

- 1440px：左侧计划栏 + 双列编辑器 + 固定摘要。
- 1024px：左侧可收缩，编辑器与摘要保持双列。
- 768px：摘要移到配置顶部，表单单列。
- 375px：模式横向可滚动，所有输入满宽，无页面级横向滚动。
- 所有输入有可见 `<label>` 和唯一 id，不能只使用 placeholder。
- 关键说明正文至少使用现有 `text-xs` 可读字号，交互目标最小 44×44px。
- 卡片选择使用 `RadioGroup`/`role=radio`，支持方向键和空格选择。
- 弹层具备焦点圈、Escape 关闭和焦点归还。
- 实时更新使用 `aria-live=polite`，错误摘要使用 `role=alert`。
- 颜色不是唯一状态提示；图标和状态文字必须同时存在。
- 动画尊重 `prefers-reduced-motion`。

### 9.9 前端组件建议

```text
apps/web/src/features/entry-plans/
├── pages/
│   └── EntryPlansPage.tsx
├── components/
│   ├── EntryPlanStudioShell.tsx
│   ├── EntryPlanSidebar.tsx
│   ├── EntryPlanModeTabs.tsx
│   ├── EntryPlanList.tsx
│   ├── EntryPlanCard.tsx
│   ├── EntryPlanEditor.tsx
│   ├── EntryStrategyPicker.tsx
│   ├── EntryTargetEditor.tsx
│   ├── EntryPacingEditor.tsx
│   ├── EntryExecutionEditor.tsx
│   ├── EntryPlanSummary.tsx
│   ├── EntryAuthorizationDialog.tsx
│   ├── PendingEntryIntentCard.tsx
│   └── EntryPlanEventTimeline.tsx
├── hooks/
│   ├── queries.gql
│   ├── mutations.gql
│   ├── subscriptions.gql
│   ├── useEntryPlans.ts
│   └── useEntryPlanActions.ts
├── model/
│   ├── draft.ts
│   ├── capabilities.ts
│   └── validation.ts
└── index.ts
```

表单局部状态使用 `react-hook-form + zod`；URQL 只保存服务端状态。不要把整个编辑
草稿放入全局 store。能力元数据转换为结构化字段组件，禁止用 `as any` 或拼接
原始 JSON。股票搜索复用现有证券主数据搜索能力，未持有标的也可选择；Radix、
Lucide、React Hook Form、Zod 和 Testing Library 已满足一期需要，不新增 UI 依赖
或另一套全局字体。

---

## 10. 原因码与审计事件

### 10.1 计划原因码

```text
ENTRY_PLAN_PAUSED
ENTRY_PLAN_EXPIRED
ENTRY_PLAN_TARGET_REACHED
ENTRY_PLAN_BUDGET_EXHAUSTED
ENTRY_PLAN_PRICE_CEILING_BLOCKED
ENTRY_PLAN_INTERVAL_COOLDOWN
ENTRY_PLAN_PENDING_EXISTS
ENTRY_PLAN_OVERLAP
ENTRY_PLAN_OPPOSITE_ORDER_PENDING
ENTRY_PLAN_AUTHORIZATION_REQUIRED
ENTRY_PLAN_AUTHORIZATION_STALE
ENTRY_PLAN_ACCOUNT_SNAPSHOT_UNAVAILABLE
ENTRY_PLAN_SECURITY_STATUS_UNAVAILABLE
ENTRY_PLAN_DATA_STALE
ENTRY_PLAN_TREND_NOT_CONFIRMED
ENTRY_PLAN_PULLBACK_NOT_CONFIRMED
ENTRY_PLAN_LADDER_NOT_REACHED
ENTRY_PLAN_DAILY_LIMIT_REACHED
ENTRY_PLAN_CASH_BUFFER_BLOCKED
ENTRY_PLAN_RECONCILE_REQUIRED
```

公共 `OUT_OF_SESSION / SUSPENDED / LIMIT_UP_BUY_BLOCKED / INSUFFICIENT_CASH /
POSITION_LIMIT_CAP / DAILY_ADD_LIMIT_CAP / AGENT_OFFLINE` 等原因继续由现有风控产生，
不得复制一套同义码。

### 10.2 计划事件

```text
ENTRY_PLAN_CREATED
ENTRY_PLAN_UPDATED
ENTRY_PLAN_AUTHORIZED
ENTRY_PLAN_AUTHORIZATION_REVOKED
ENTRY_PLAN_ARMED
ENTRY_PLAN_PAUSED
ENTRY_PLAN_RESUMED
ENTRY_PLAN_EVALUATED
ENTRY_RULE_MATCHED
ENTRY_INTENT_CREATED
ENTRY_INTENT_AWAITING_APPROVAL
ENTRY_INTENT_CONFIRMED
ENTRY_INTENT_REJECTED
ENTRY_ORDER_SUBMITTED
ENTRY_ORDER_PARTIALLY_FILLED
ENTRY_ORDER_FILLED
ENTRY_ORDER_CANCELED
ENTRY_PLAN_COMPLETED
ENTRY_PLAN_EXPIRED
ENTRY_PLAN_CANCEL_REQUESTED
ENTRY_PLAN_CANCELLED
ENTRY_PLAN_RECONCILE_REQUIRED
```

每次不买、少买、延迟、拒绝和熔断都写入结构化原因、输入版本、价格、预期/裁剪
金额和关联 `trace_id`。前端“买入记录”从真实意图、订单和成交投影生成，不能把
计划评估事件伪装成成交。

---

## 11. 测试与验收矩阵

### 11.1 领域单元测试

- 模板拒绝多个同时生效的目标字段。
- 目标仓位只生成扣除现有持仓和工作中 BUY 后的正缺口，不透传整笔目标金额。
- `locked_core` 目标被拒绝。
- 趋势规则只使用决策时点及之前的数据。
- 价格阶梯同一层不会因连续 tick 重复产生意图。
- pending 存在时所有规则只记录“不买”，不产生第二个意图。
- 真实部分成交只累计实际数量和金额。
- 委托终态先到时 pending 不提前释放。
- 拒单/零成交撤单按冷却策略重新武装。
- 目标达到、预算耗尽和到期状态转换正确。
- DRAINING 在真实回报收敛前不会进入 CANCELLED/EXPIRED。
- 重放相同成交业务键不会重复累计。
- 公司行为按权威因子调整新增股数目标和未触发价格阶梯，并保留审计事件。

### 11.2 Engine / Infrastructure 测试

- StrategyManager 租约和稳定业务键保证一个 managed-entry 运行不会重复产生意图。
- RuntimeState、组合、BucketLedger、ExitPlan 与 event marker 在同一 durable 快照提交。
- `owner_type=STRATEGY_RUN`、`owner_id=run_id`，并通过元数据中的稳定 `plan_id`
  正确进入现有 StrategyExecutor。
- EntryGapCalculator 输出剩余增量，OrderSizer 对不足一手和整手修正给出明确原因。
- 资金、冻结、停牌、涨停、数据陈旧和 QMT 离线均阻止实盘买入。
- 缺账户或证券状态快照时明确失败关闭，不能使用空账户或构造安全行情。
- 同标的未完成 SELL、持续清仓或外部订单冲突时不产生新 BUY。
- `command_ack` 不推进计划成交。
- durable inbox 重放不重复更新 EntryPlan 与 BucketLedger。
- 重启恢复后先安装 pending 屏障，再处理新行情。
- 外部人工买入通过对账进入真实持仓，但不伪造成计划成交；无法解释差异进入
  `RECONCILE_REQUIRED`。
- 带退出模板的部分成交只创建对应数量的保护。

### 11.3 API / GraphQL 测试

- capabilities 是规则字段和预设的唯一前端真源。
- 创建/更新强类型输入校验单位、范围和互斥字段。
- `config_version` 冲突返回结构化错误。
- 自动授权精确绑定配置快照；扩大风险后旧授权失效。
- StrategyExecutor 和 TradeCommandService 两道门都拒绝过期、撤销或不匹配的
  自动建仓授权。
- 人工确认过期、价格偏离或风险变化时不能路由旧意图。
- 单账户接口不接受任意账户 ID 越权参数。

### 11.4 前端测试

- 策略类型没有原生 `<select>`，卡片可用键盘选择。
- 选择策略后显示用途、适用场景和自动处理内容。
- “保存并保持暂停”和启动/授权按钮始终与实际行为一致。
- 普通模式不展示 JSON；专业参数仍有 label、单位、范围和帮助。
- 目标、预算、已用、剩余、最高买价和授权状态摘要实时更新。
- `ENTRY_PENDING / DRAINING` 禁止结构编辑并显示原因。
- 授权预览变化时要求再次确认。
- 全局暂停自动买入后所有计划停止产生新自动意图，恢复时不会补发历史触发。
- Subscription 断线后 Query 能恢复权威状态。
- 375/768/1024/1440px 无页面级横向滚动。
- loading、empty、error、stale data、QMT blocked 和 reconcile 状态都有明确界面。

### 11.5 一期业务验收

- 无显式启动或授权时零 Broker 买单。
- 一只股票同时最多一个活跃买入计划。
- 任意计划同时最多一个未完成买入意图/订单。
- 自动趋势调整不能突破任何硬上限或最高可买价。
- 数据缺失只能减少、延迟或拒绝买入。
- 模拟、实盘人工和实盘自动走相同意图—尺寸—风控—回报链路。
- 买入部分成交后只累计实际成交，并正确写入目标 bucket。
- 有待收敛订单时暂停/取消不会伪造终态。
- 所有不买、少买、拒单、成交和授权动作可从 DecisionTrace 与计划事件复现。
- 实盘 E2E 不作为默认验收；先完成模拟固化回放和人工确认灰度。

---

## 12. 实施顺序

### Phase 0：契约收口

1. 评审本文并确定一期规则与字段。
2. 更新交易文档索引。
3. 明确 EntryPlan 产品投影、ManagedEntryPlanStrategy、ExitPlan 与
   StrategyExecutor 的唯一边界。

### Phase 1：纯领域模型

1. `entry_plan.py`：配置、阶段、规则注册表和纯评估器。
2. `ashare_managed_entry_plan.py`：固定标的 StrategyBase 薄适配器。
3. 原因码、RuntimeStatePatch、序列化和因果状态机单元测试。

建议落点：

```text
packages/domain/src/quantx_domain/trading/entry_plan.py
packages/domain/src/quantx_domain/strategies/ashare_managed_entry_plan.py
tests/domain/trading/test_entry_plan.py
tests/engine/unit/strategies/test_ashare_managed_entry_plan.py
```

### Phase 2：运行持久化与共享执行

1. 使用 StrategyRun/StrategyRunState 作为计划配置和算法状态真源。
2. 新增精确授权 grant、Repository、产品投影 Service 与 migration。
3. StrategyManager/StrategyExecutor 注册 managed-entry 策略，并在
   TradeCommandService 增加自动建仓授权原子门禁。
4. 回报收敛、重启恢复、每批 ExitPlan 衔接和审计测试。

建议落点：

```text
packages/application/src/quantx_application/entry_plans.py
packages/infrastructure/src/quantx_infrastructure/models/entry_plan_authorization.py
packages/infrastructure/src/quantx_infrastructure/repositories/entry_plan_authorization_repository.py
packages/infrastructure/src/quantx_infrastructure/services/entry_plan_projection_service.py
packages/infrastructure/src/quantx_infrastructure/services/entry_plan_authorization_service.py
apps/engine/src/quantx_engine/strategy_executor.py
apps/engine/src/quantx_engine/strategy_manager.py
```

### Phase 3：GraphQL 原子切换

1. Strawberry types、resolver、schema 和 operation policy。
2. 授权挑战与人工确认。
3. `.gql` 操作、codegen 和生成类型。
4. API 单元测试与 schema 合同更新。

### Phase 4：前端买入管理

1. 路由、Studio shell 和计划列表。
2. capabilities 驱动的卡片编辑器。
3. 计划摘要、待确认、授权挑战与事件时间线。
4. 响应式、无障碍和组件测试。

### Phase 5：模拟灰度

1. 趋势回撤和价格阶梯固化回放。
2. 模拟观察至少覆盖不同趋势、震荡、涨停、停牌、断线和数据陈旧场景。
3. 实盘只开放人工确认、单标的、单次 100 股白名单。
4. 无重复下单、假成交、超预算或状态恢复问题后，单独评审自动授权开放。

---

## 13. 完成定义

只有同时满足以下条件，模块才可称为完成：

- 领域、持久化、Engine、GraphQL、前端和测试使用同一份唯一契约。
- 建仓/加仓使用统一 EntryPlan，没有重复状态机。
- 前端不要求用户理解原始 JSON 或布尔表达式。
- 自动识别趋势有可解释状态、版本化参数和严格因果测试。
- 实盘自动买入具有精确授权、硬上限、暂停、取消和对账恢复能力。
- 计划不会把意图、ACK、委托已报或部分成交伪造为完整成交。
- 与现有 `ExitPlan`、OrderSizer、风控、BucketLedger 和 QMT 回报链路完整衔接。
- 必要 codegen、类型检查、lint、测试和构建全部通过。
