# A 股三层协作与执行契约

**文档目标：** 定义 A 股个人量化系统中环境层、风控层、仓位调节层、策略层、执行层与状态管理层之间的统一协作顺序、职责边界、状态真源和异常处理规则。本文是**策略无关的交易域契约**：当前必须服务[A 股单标的动态天平双仓策略](../strategies/dynamic-balance/A股单标的动态天平双仓策略.md)，后续新增其他 A 股策略时也应复用本文约定。

---

## 0. 核心定位

本文回答一个问题：**一次 A 股策略决策从行情快照到成交回报，应该如何在各模块之间确定性流转？**

它不定义任何具体策略公式，不定义个股买卖逻辑，也不决定某只股票是否值得交易。它只定义：

- 环境层、风控层、仓位调节层的调用顺序。
- 策略如何消费公共快照并输出交易意图。
- 风控如何拆分为“前置上下文风控”和“后置订单风控”。
- 订单、成交、bucket 归因、T+1 库存置换如何收敛到统一账本。
- 自动退出计划如何组合不同卖出策略并生成标准卖出意图。
- 实盘 miniQMT 与回测 broker 如何保持状态流同构。
- 后续策略如何接入而不破坏当前双仓策略。

当前主线策略：

```text
ashare_dynamic_balance_dual_bucket
A 股单标的动态天平双仓策略
bucket 模型：locked_core / core / swing
节奏：日线确认趋势与核心仓目标，盘中分钟/tick 触发 swing 网格
```

但本文的公共能力必须允许未来策略使用不同 bucket 模型，例如：

```text
single_bucket_trend       // 单仓趋势策略
core_only_dca             // 只建核心仓策略
swing_only_grid           // 只做短仓网格策略
multi_signal_rebalance    // 多信号再平衡策略，默认仍限定单标的实例
account_holdings_t        // 显式 MULTI + ACCOUNT_HOLDINGS，由外部持仓快照动态维护标的
```

---

## 1. 不可推翻的协作原则

### 1.1 策略只表达意图

策略层只输出 `TradeIntent`。它可以表达：

- 想买还是想卖。
- 属于哪个逻辑 bucket。
- 目标仓位、目标金额或目标数量。
- 触发原因、置信度、状态机标签、网格层级。

策略层不得表达：

- 已修正后的真实可买数量。
- 已修正后的真实可卖数量。
- 真实现金是否足够。
- 今日买入能否卖出。
- miniQMT 是否已经成交。

### 1.2 风控拆成两段

A 股系统必须使用双阶段风控，避免“风控既需要交易意图又要给仓位调节层上限”的顺序冲突。

```text
前置上下文风控 ContextRiskCaps
    在策略 Step() 之前执行
    输入：环境快照 + 组合状态 + 实例风险配置 + 订单/账户健康状态
    输出：最大仓位、最大买入额、是否禁买、是否防御、是否熔断

后置订单风控 OrderRiskDecision
    在策略 TradeIntent 之后执行
    输入：交易意图 + 组合状态 + A 股规则 + 订单状态 + broker 快照
    输出：ALLOW / CAP / DELAY / REJECT / KILL_SWITCH
```

### 1.3 仓位调节层只调整工作空间

仓位调节层只把环境与风险约束映射为策略可用的参数 profile，例如：

- `min_position_pct`
- `max_position_pct`
- `target_cash_buffer_pct`
- `core_share_min / core_share_max`
- `swing_max_pct`
- `balance_beta_multiplier`
- `inventory_gamma_multiplier`
- `grid_step_multiplier`
- `allow_core_buy / allow_swing_buy / allow_swing_sell`

仓位调节层不生成买卖方向，不修改真实持仓，不标记订单成交。

### 1.4 miniQMT 是实盘成交真源

实盘路径中，以下事件都不能改变真实成交状态：

- 策略发出信号。
- SaaS 下发指令。
- QMT Agent 收到指令。
- miniQMT 接受委托。
- 委托状态为已报。

只有 miniQMT 的成交回报可以改变：

- 真实持仓。
- 真实现金。
- 冻结资金释放。
- 冻结股份释放。
- bucket 成交归因。
- 网格成交状态。

### 1.5 回测必须模拟同一条状态流

回测 broker 不允许把 `TradeIntent` 直接当成成交。回测路径必须模拟：

```text
TradeIntent
  -> OrderSizer
  -> OrderRiskDecision
  -> OrderRequest
  -> BrokerOrderAccepted / BrokerOrderRejected
  -> BrokerExecutionReport
  -> RuntimeStateManager
  -> Strategy on_order / on_trade
```

否则 GA 会利用假成交漏洞，尤其是涨停买入、跌停卖出、日线 high/low 触达和 T+1 场景。

### 1.6 自动卖出是 Engine 公共能力

做 T、打板、趋势和条件清仓不得各自实现一套自动卖出状态机。入场策略可以
随 BUY `TradeIntent` 提交 `ExitPlanTemplate`，但只有真实买入成交可以激活
计划。Engine 统一评估卖出触发策略、数量策略、T+1 策略与执行策略，再生成
标准 SELL `TradeIntent` 进入本文规定的后置风控和执行链路。

退出计划的详细生命周期、扩展注册方式与审计字段见
[A 股自动退出计划与卖出策略契约](A股自动退出计划与卖出策略契约.md)。

---

## 2. 标准调用顺序

### 2.1 总体数据流

推荐统一数据流如下：

```text
MarketData / Calendar / SecurityStatus / Portfolio / OrderReports
    -> MarketDataAdapter
    -> 环境层 EnvironmentLayer
       输出 MarketContextSnapshot

MarketContextSnapshot + PortfolioState + OrderHealth + InstanceRiskConfig
    -> 前置风控 ContextRiskLayer
       输出 RiskContextCaps

MarketContextSnapshot + RiskContextCaps + StrategyRuntimeState + PortfolioBucketState
    -> 仓位调节层 PositionAdjustmentLayer
       输出 PositionAdjustmentProfile

StrategyInput = {
    instrument bars,
    portfolio snapshot,
    bucket ledger snapshot,
    MarketContextSnapshot,
    RiskContextCaps,
    PositionAdjustmentProfile,
    RuntimeState,
    ParamPack
}
    -> Strategy.Step()
       输出 TradeIntent[] + RuntimeStatePatch

TradeIntent[] + PortfolioState + BucketLedger + AshareMarketRules
    -> OrderSizer
       输出 OrderDraft[]

OrderDraft[] + PortfolioState + BucketLedger + OrderState + BrokerSnapshot
    -> 后置风控 OrderRiskLayer
       输出 OrderRiskDecision[]

OrderRiskDecision[ALLOW/CAP]
    -> OrderRouter
    -> TradeCommand
    -> QMT Agent
    -> miniQMT / BacktestBroker
    -> BrokerExecutionReport
    -> RuntimeStateManager
    -> PortfolioState / BucketLedger / OrderState / DecisionTrace
```

### 2.2 日线收盘动作

日线动作通常在收盘后或下一交易日前执行，用于确认慢变量。

```text
1. 同步当日完整日线、指数、行业、概念、宽度、证券状态、公司行为。
2. 校验数据质量和交易日历。
3. 生成 MarketContextSnapshot。
4. 生成 RiskContextCaps。
5. 生成 PositionAdjustmentProfile。
6. 调用策略日线 Step 或策略 Step 的日线分支。
7. 更新趋势状态、动态基准、低位评分、高位评分、core 目标。
8. 如有 core 调仓意图，进入 OrderSizer + OrderRiskLayer。
9. 记录 DecisionTrace。
```

对当前双仓策略：

- 日线动作主要影响 `core` 目标、动态基准、趋势状态和仓位阶段。
- 日线动作不应频繁改变盘中 swing 的已成交网格状态。
- 如果日线动作产生 core 买卖意图，也必须经过订单状态流。

### 2.3 盘中分钟 / tick 动作

盘中动作用于处理快变量，尤其是 swing 网格。

```text
1. 读取上一交易日确认的动态基准和日线状态。
2. 读取当前分钟/tick 行情、盘口、涨跌停、停牌、交易时段。
3. 刷新组合状态、可卖量、冻结状态、未完成订单。
4. 环境层可使用盘中宽度/流动性增量；缺失时沿用最近确认快照并标记 stale。
5. 前置风控确认是否允许盘中新增买入或只允许卖出。
6. 仓位调节层输出盘中 profile 修正。
7. 策略根据 grid index 输出 swing 意图。
8. 后置风控处理 T+1、100 股、涨跌停、可卖量、现金和库存置换。
9. 下发订单或延迟/拒绝。
10. 记录 DecisionTrace。
```

实时 tick 动作只能在中央行情完成唯一三阶段就绪契约后执行：
sequence 1 全量快照和 sequence 2 切换前连续性屏障均保持
`SYNCING`；Agent 收到 sequence 2 ACK 后才切换有序捕获，并强制
发送 sequence 3 readiness-confirm（可为空）；只有 sequence 3 原子提交
后才能进入 `READY`，真实增量从 sequence 4 开始。每条 tick 必须
使用保留亚秒精度的券商源时间；非法、超前或积压过期的批次不得
刷新 `READY` 或 freshness lease。

需要跨重启识别形态的策略，必须把有界、因果的滑动观察窗写入
PAPER/LIVE RuntimeState；恢复样本必须再按当前 tick 过滤到
`[current - lookback, current]`，禁止使用未来样本。BACKTEST 不持久化该窗口。

对当前双仓策略：

- `BUILDING_CORE` 阶段普通网格不得卖出 core。
- swing 买入必须让位于 core 建仓节奏。
- swing 卖出不足时可触发合法 T+1 库存置换，但只能由风控与账本层处理。

### 2.4 订单回报动作

订单回报动作由 miniQMT 或回测 broker 事件驱动，不由策略主动驱动。

```text
1. 收到委托回报或成交回报。
2. 用 broker_order_id / client_order_id 定位 OrderState。
3. 更新订单状态和冻结状态。
4. 对成交回报按成交数量更新 PortfolioState 与 BucketLedger。
5. 对部分成交只应用部分 bucket 归因和部分库存置换。
6. 对拒单、撤单、废单释放冻结并回滚未成交的置换流水。
7. 调用 Strategy on_order / on_trade 更新算法状态，例如 pending 网格、最近成交层级。
8. 记录 BrokerExecutionReport 和 DecisionTrace 补充事件。
```

实盘回报必须先写入 durable inbox，再以稳定业务键唯一生成
runtime event。该事件对 TradeIntent/T 批次的投影只允许在唯一事件
首次落库的同一事务内执行。Engine 应用事件后，必须把事件 marker、
资金、持仓、策略状态与退出计划作为一个原子快照提交，然后才能
把 runtime event 标记为 `APPLIED`。回调失败必须回滚本次内存效果；
快照结果不确定或未成功时，运行时必须安装同业务键屏障，在安全重试
收敛前不得消费新 tick/kline 或确认新交易意图。

同一策略运行的 runtime event 必须按 `(created_at, event_id)` 的唯一顺序处理。
运行处于暂停、停止或尚未启动且没有串行消费者时，事件保持 `PENDING`，不得增加
失败次数或阻塞其他运行；恢复后继续从该运行最早未应用事件收敛。暂停或停止前必须
证明不存在待审批意图、活动委托、资金/持仓预留与 durable barrier，停止顺序必须
保证最终策略回调先于最终 RuntimeState 快照，Broker 断开只能发生在快照之后。

RuntimeState 更新必须使用数据库版本条件的原子 CAS。每次 Engine 快照应携带
manager-owned attempt token；数据库提交成功但客户端结果未知时，只有权威记录中的
token 匹配才能采纳该版本。若其他合法写入先赢得 CAS，Engine 必须保留本地 dirty
状态、合并对方归属字段，并基于权威新版本继续保存，禁止覆盖事件 marker 或预留。

终态委托回报不得覆盖部分成交事实。例如 `CANCELLED + executed_volume>0`
必须继续等待并幂等应用对应 TRADE 回报，恢复真实 T 批次和退出计划；
不得仅根据委托终态清空 pending 并将已成交仓位当作零。

委托终态与成交回报分属独立消息。`FILLED`，或携带累计成交量的
`CANCELLED / REJECTED / EXPIRED`，若其报告量领先于该 intent 已收敛的真实 TRADE，
TradeIntent、T 批次、策略状态和退出计划必须统一进入 `RECONCILE_REQUIRED`，保留
pending intent 与期望累计量。每笔 TRADE 只按唯一执行 ID 累计；未追平时继续门控，
追平后才根据委托终态和剩余活跃仓派生 `FILLED / CANCELLED`、`OPEN / EXIT_PARTIAL /
CLOSED`。委托回报本身不得被当作成交事件。

### 2.5 对账动作

对账动作用于修复本地账本与 broker 真实快照差异。

```text
1. QMT Agent 重连、每日收盘后、系统启动后、异常回报后触发。
2. 拉取 miniQMT 资金、持仓、可卖量、冻结、未完成委托、当日成交。
3. 与 SaaS PortfolioState / BucketLedger / OrderState 比较。
4. 在容忍范围内自动修正冻结和可卖量。
5. 发现持仓数量、现金、成交流水无法解释的差异时进入 RECONCILE_REQUIRED。
6. 必要时触发 KILL_SWITCH，等待人工确认。
```

对账必须识别订单来源。在只读的 `SHADOW` 准备阶段，券商快照中没有 QuantX
`PendingTradeOrder / client_order_id` 的委托与成交应分类为 `EXTERNAL_BROKER_*`，
作为 QMT 客户端手工交易正常入账，而不是直接形成阻断。以下情况仍属于阻断：

- QuantX 已知的工作中委托从券商完整快照中消失；
- 完整快照不新鲜、不完整、哈希或协议不合法；
- `CANARY / LIVE` 账户实盘窗口中出现未关联的外部活动；
- 外部成交入账后，真实持仓仍无法与账户事实收敛。

准备就绪只证明账户观察链路可持续收敛；自动执行授权还必须独立满足备份、
白名单、实盘开关、无外部活动和灰度确认等门禁。

---

## 3. 层级职责边界

### 3.1 MarketDataAdapter

MarketDataAdapter 是环境层与策略层之前的数据适配器。

职责：

- 拉取或读取 A 股行情、指数、行业、概念、宽度、交易日历、涨跌停、证券状态、公司行为。
- 保证数据按时点可得，禁止未来数据泄露。
- 统一价格口径：指标可以用时点可得复权序列，交易撮合必须用未复权真实价格。
- 输出 `MarketDataSnapshot`。

不做：

- 不计算买卖信号。
- 不修改组合状态。
- 不替代环境层判断。

### 3.2 EnvironmentLayer

环境层职责：

- 把大盘、行业、概念、宽度、流动性、量价结构压缩成 `MarketContextSnapshot`。
- 输出 `context_score` 和 `risk_tags`。
- 对数据缺失进行保守降级。

不做：

- 不输出 `BUY / SELL`。
- 不决定 target position。
- 不调用 broker。

### 3.3 ContextRiskLayer

前置上下文风控职责：

- 根据环境和账户健康状态输出全局约束。
- 决定是否进入防御、是否禁买、是否只允许卖出。
- 给仓位调节层提供硬上限。

输出：`RiskContextCaps`。

典型字段：

```json
{
  "risk_mode": "RISK_REDUCED",
  "max_position_pct_cap": 0.45,
  "max_buy_amount_cny": 8000,
  "max_daily_add_pct": 0.03,
  "allow_core_buy": true,
  "allow_swing_buy": false,
  "allow_sell": true,
  "force_profile": "CAUTIOUS",
  "kill_switch": false,
  "reason_codes": ["MARKET_RISK_OFF", "SWING_BUY_DISABLED"]
}
```

### 3.4 PositionAdjustmentLayer

仓位调节层职责：

- 把 `MarketContextSnapshot` + `RiskContextCaps` + 策略状态映射为 profile。
- 输出动态天平边界和参数乘数。
- 给策略提供“当前能活动多大”的工作空间。

对当前双仓策略，它直接影响：

- `MinPct / MaxPct`
- `NeutralPositionPct`
- `balance_beta / inventory_gamma`
- `CoreShareMin / CoreShareMax`
- `SwingMaxPct`
- `GridStepPct`

对未来策略，它可以只输出策略声明支持的字段。策略不支持的字段应被忽略，而不是强行解释。

### 3.5 StrategyLayer

策略层职责：

- 消费标准 `StrategyInput`。
- 输出 `TradeIntent[]` 与 `RuntimeStatePatch`。
- 更新算法状态，但不得更新真实持仓、现金和订单状态。

当前双仓策略必须满足：

- `TradeIntent.metadata.bucket` 必须为 `core` 或 `swing`。
- `locked_core` 默认不由策略主动卖出。
- `BUILDING_CORE` 阶段普通网格不得卖 core。
- 网格成交状态只能由 `on_order / on_trade` 事件更新。

### 3.6 OrderSizer

OrderSizer 职责：

- 把目标仓位、目标金额或目标数量转换成 A 股合法订单草案。
- 处理 100 股整数倍、零股清仓、价格 tick、最小订单金额、单笔最大比例、现金预占用。

OrderSizer 只做尺寸与格式修正，不做环境判断和策略判断。

### 3.7 OrderRiskLayer

后置订单风控职责：

- 校验交易时段。
- 校验停牌、ST、退市风险、涨跌停、盘口可成交性。
- 校验现金、冻结、持仓、可卖量、T+1。
- 生成库存置换计划。
- 输出 `OrderRiskDecision`。

它可以改变订单数量或拒绝订单，但不得改变交易方向。

### 3.8 OrderRouter / QMT Agent

OrderRouter 和 QMT Agent 职责：

- 把 SaaS 侧 `TradeCommand` 转换为本地 miniQMT 下单参数。
- 执行本地保护检查。
- 上报委托状态、成交状态、账户快照。

QMT Agent 不含策略代码，不生成新交易意图。

### 3.9 RuntimeStateManager

RuntimeStateManager 职责：

- 消费 broker 事件。
- 更新订单状态、冻结状态、真实组合快照、bucket 账本。
- 调用策略 `on_order / on_trade` 更新算法状态。
- 生成审计快照。

RuntimeStateManager 是交易事实收敛中心。

---

## 4. 公共策略接入契约

### 4.1 StrategyManifest

每个策略必须声明自己的能力，而不是让执行层猜测。

```json
{
  "strategy_id": "ashare_dynamic_balance_dual_bucket",
  "market": "A_SHARE",
  "instrument_scope": "SINGLE_INSTRUMENT",
  "direction_mode": "LONG_ONLY",
  "bucket_model": "CORE_SWING_LOCKED",
  "decision_cadence": ["DAILY_CLOSE", "INTRADAY_1M", "ORDER_EVENT"],
  "requires_environment_layer": true,
  "requires_position_adjustment_profile": true,
  "supports_context_risk_caps": true,
  "supports_t1_substitution": true,
  "supported_intent_types": ["TARGET_POSITION_PCT", "TARGET_AMOUNT", "TARGET_VOLUME"],
  "data_dependencies": [
    "INSTRUMENT_DAILY_BAR",
    "INSTRUMENT_INTRADAY_BAR",
    "MARKET_INDEX_DAILY_BAR",
    "SECTOR_INDEX_DAILY_BAR",
    "TRADING_CALENDAR",
    "LIMIT_PRICE",
    "SECURITY_STATUS"
  ]
}
```

### 4.2 bucket_model 枚举

| bucket_model | 说明 | 适用策略 |
|---|---|---|
| `NONE` | 策略不使用 bucket，执行层只维护真实持仓 | 简单趋势策略 |
| `SINGLE_ACTIVE` | 只有一个主动交易 bucket | 普通单仓策略 |
| `CORE_SWING` | 核心仓 + 波动仓 | 网格增强、趋势增强 |
| `CORE_SWING_LOCKED` | 封存仓 + 核心仓 + 波动仓 | 当前动态天平双仓策略 |
| `CUSTOM` | 策略自定义 bucket，但必须实现映射接口 | 后续高级策略 |

### 4.3 StrategyInput 标准字段

所有策略共享的基础输入：

```text
StrategyInput
├── instance_id
├── strategy_id
├── instrument_code
├── decision_time
├── cadence
├── market_data_snapshot
├── portfolio_snapshot
├── bucket_ledger_snapshot
├── market_context_snapshot
├── risk_context_caps
├── position_adjustment_profile
├── runtime_state
├── param_pack
└── pending_order_summary
```

策略可以忽略不需要的字段，但不能要求直接访问数据库或 broker。

### 4.4 TradeIntent 标准字段

```text
TradeIntent
├── intent_id
├── instance_id
├── strategy_id
├── instrument_code
├── side                         // BUY / SELL
├── intent_type                  // TARGET_POSITION_PCT / TARGET_AMOUNT / TARGET_VOLUME
├── target_position_pct
├── target_amount_cny
├── target_volume
├── bucket                       // core / swing / locked_core / default / custom
├── confidence
├── priority                     // LOW / NORMAL / HIGH / RISK_REDUCTION
├── expiry_policy
├── reason
├── metadata
└── trace_id
```

当前双仓策略要求：

- core 调仓必须带 `bucket = core`。
- swing 网格必须带 `bucket = swing`。
- 普通方向性卖出不得带 `bucket = locked_core`。
- 高位防御卖出 core 时 `priority = RISK_REDUCTION`。

---

## 5. 状态所有权

| 状态 | 真源 | 可写模块 | 策略是否可写 | 说明 |
|---|---|---|---|---|
| `MarketContextSnapshot` | 环境层 | EnvironmentLayer | 否 | 每次决策可复现 |
| `RiskContextCaps` | 前置风控 | ContextRiskLayer | 否 | 约束仓位调节与策略 |
| `PositionAdjustmentProfile` | 仓位调节层 | PositionAdjustmentLayer | 否 | 策略只消费 |
| `RuntimeState` | 策略算法状态 | Strategy / RuntimeStateManager | 是，限算法状态 | 不含真实现金持仓 |
| `PortfolioState` | miniQMT / broker 快照 | RuntimeStateManager | 否 | 真实资金与持仓 |
| `BucketLedger` | 成交归因账本 | RuntimeStateManager | 否 | 策略只能请求 bucket |
| `OrderState` | broker 事件状态流 | RuntimeStateManager | 否 | 下单、撤单、成交、拒单 |
| `DecisionTrace` | 审计系统 | 各层追加 | 否 | 只追加，不覆盖 |
| `ParamPack` | 基因库 / 实例配置 | Instance / Evolution | 否 | 策略读取参数 |

---

## 6. 冲突处理规则

### 6.1 保守优先级

当多个模块给出冲突结论时，按以下优先级处理：

```text
KILL_SWITCH
  > 法规与交易所/交易所等价规则（交易时段、停牌、T+1、涨跌停、100股）
  > 账户事实（现金、可卖量、冻结、真实持仓）
  > 实例硬风控（最大仓位、最大回撤、现金缓冲）
  > 前置上下文风控 RiskContextCaps
  > 仓位调节 Profile
  > 策略 TradeIntent
  > 用户偏好软参数
```

### 6.2 只允许向保守方向降级

数据缺失、状态不一致、Agent 离线、回报延迟时，只能：

- 降低买入额度。
- 降低仓位上限。
- 禁止 swing 买入。
- 延迟订单。
- 触发人工确认。

不能：

- 提高买入额度。
- 提高仓位上限。
- 把未知状态视为安全。
- 把未成交订单视为已成交。

### 6.3 风控不得反向交易

如果策略输出 BUY，后置风控可以：

- 允许。
- 限额。
- 延迟。
- 拒绝。
- 触发熔断。

但不得把 BUY 改为 SELL。

如果需要强制减仓，必须由独立的风险处置流程产生 `RiskReductionIntent`，并明确人工确认或自动风控权限。

---

## 7. 异常与降级

### 7.1 数据缺失

| 缺失类型 | 默认动作 |
|---|---|
| 缺大盘指数 | 环境层 `INSUFFICIENT`，前置风控进入保守，禁止 aggressive |
| 缺行业指数 | 行业降级为大盘，不允许进入 aggressive accumulation |
| 缺概念指数 | 概念中性，不阻塞 |
| 缺涨跌停价 | 实盘禁止下单，回测使用保守推导并标注 |
| 缺停牌状态 | 实盘禁止下单 |
| 缺分钟/tick | 禁用盘中网格，只允许日线低频逻辑 |
| 缺 broker 账户快照 | 禁止新增买入，只允许对账 |

### 7.2 Agent 离线

Agent 离线时：

- SaaS 不下发新订单。
- 不把待下发指令保留为无限期有效订单。
- 记录 `AGENT_OFFLINE` trace。
- 下次 tick 重新基于最新状态决策。

### 7.3 订单回报延迟

`command_ack` 只证明命令已投递或 Agent 已完成本地前置检查，不得把
订单推进为已受理、部分成交或已成交。只有仍为 `QUEUED`、从未投递，
且持久化链路能证明无 broker order id、无成交、无矛盾关联时，过期或
Agent 明确的下单前拒绝才能终结为 `EXPIRED / REJECTED`。已进入
`DELIVERED`却未收到 ACK、拒绝原因不能证明下单前失败，或出现任何
券商副作用证据时，必须保留全部关联并进入 `RECONCILE_REQUIRED`。

当订单处于 `SUBMITTED / ACCEPTED / PARTIAL_FILLED` 且超过超时阈值未收到完整回报：

- 禁止同一 bucket、同一方向、同一网格层级重复发单。
- 可以查询 broker 委托状态。
- 如果查询失败，进入 `ORDER_REPORT_STALE`。
- 超过更高阈值触发对账或 KILL_SWITCH。

### 7.4 账本与 broker 不一致

若 broker 快照显示真实持仓与本地账本不一致：

1. 先尝试用未处理成交、撤单、冻结释放解释。
2. 解释成功则生成 `RECONCILED` 事件。
3. 无法解释则进入 `RECONCILE_REQUIRED`。
4. 重大差异触发 `KILL_SWITCH`。

后续协议 1.1 权威完整快照成功收敛后，较早的完整快照处理死信可以进入
`SUPERSEDED`，同时自动解决与这些死信一一对应的运行告警。原始载荷、失败
原因与处理次数必须保留用于审计；不得删除历史记录或把普通增量报告无条件
视为已被取代。

---

## 8. 当前双仓策略适配要求

### 8.1 必须支持的公共能力

当前 `A股单标的动态天平双仓策略` 依赖以下公共能力：

| 能力 | 是否必须 | 说明 |
|---|---|---|
| `MarketContextSnapshot` | 是 | 大盘、行业、概念、流动性、宽度、量价结构 |
| `RiskContextCaps` | 是 | 给仓位调节层的最大仓位和禁买约束 |
| `PositionAdjustmentProfile` | 是 | 动态天平边界、core/swing 拆分、beta/gamma、grid step |
| `BucketLedger` | 是 | locked_core / core / swing 归因 |
| `T1SubstitutionPlan` | 是 | swing 当日买入后可使用老仓置换 |
| `OrderStateMachine` | 是 | 网格成交状态必须由回报驱动 |
| `BacktestBroker` | 是 | GA 和回测不能假成交 |
| `DecisionTrace` | 是 | 回测归因和实盘追责 |

### 8.2 策略内禁止事项

当前双仓策略不得：

- 直接读取 miniQMT。
- 直接读取数据库。
- 自行修正 100 股手数。
- 自行判定真实可卖量。
- 在信号生成时标记网格已成交。
- 因 T+1 不可卖而自行修改 core/swing 账本。
- 把 `locked_core` 当作普通方向性卖出来源。

### 8.3 策略事件回调

为保证 pending 和网格状态正确，当前双仓策略至少需要以下事件回调：

```text
on_order_accepted(order_state)
on_order_rejected(order_state, reason_code)
on_order_canceled(order_state)
on_trade_filled(execution_report, applied_bucket_attribution)
on_trade_partially_filled(execution_report, applied_bucket_attribution)
on_reconcile(reconcile_event)
```

事件回调只更新算法状态，例如：

- pending 网格层级。
- 最近成交 grid index。
- 最近成交基准。
- 拒单冷却时间。
- 部分成交后的剩余待处理意图。

不得更新真实持仓和现金。

---

## 9. 后续策略接入方式

新增策略只需要实现以下内容：

1. `StrategyManifest`。
2. 参数解析 `ParamPack`。
3. 纯函数 `Step(StrategyInput) -> StrategyOutput`。
4. 可选 `on_order / on_trade` 事件处理。
5. 若参与 GA，则实现 `EvolvableStrategy` 适配器。
6. 声明 bucket 模型和所需公共数据。

不需要重复实现：

- A 股交易时段。
- 100 股规则。
- T+1。
- 涨跌停/停牌。
- 订单状态机。
- bucket 账本。
- 回测 broker。
- 数据质量降级。
- 公司行为处理。
- 审计追踪。

---

## 10. 开发验收清单

### 10.1 架构验收

- [ ] 风控已拆分为 `ContextRiskCaps` 和 `OrderRiskDecision`。
- [ ] 仓位调节层消费的是前置风控约束，不消费具体订单状态。
- [ ] 后置风控消费具体 `TradeIntent / OrderDraft`。
- [ ] 策略不直接写真实持仓、现金、可卖量。
- [ ] miniQMT 成交回报是实盘成交真源。
- [ ] 回测 broker 模拟订单状态流。

### 10.2 当前双仓策略验收

- [ ] `TradeIntent` 必须带 bucket。
- [ ] `BUILDING_CORE` 普通网格不得卖 core。
- [ ] swing T+1 不可卖时，风控层可输出置换计划。
- [ ] `locked_core` 默认不参与方向性卖出。
- [ ] 部分成交只更新部分网格与 bucket 归因。
- [ ] 拒单/撤单不更新网格成交状态。

### 10.3 通用策略验收

- [ ] 新策略可以声明 `bucket_model = NONE` 或 `SINGLE_ACTIVE`。
- [ ] 公共执行层不依赖 `core/swing` 字段硬编码。
- [ ] 策略不支持的 profile 字段可被忽略。
- [ ] 风控规则不依赖具体策略公式。

---

## 11. 与其他文档的关系

本文是协作总契约。细节拆分如下：

| 文档 | 负责内容 |
|---|---|
| [A 股交易域数据结构与状态机](A股交易域数据结构与状态机.md) | 结构体、枚举、订单状态机、bucket 账本不变量、原因码 |
| [A 股数据源与公司行为契约](A股数据源与公司行为契约.md) | 数据源映射、数据质量、时点可得、公司行为、证券状态 |
| [A 股回测 Broker 与成交撮合契约](A股回测Broker与成交撮合契约.md) | 回测撮合、成本模型、T+1 模拟、涨跌停/停牌、成交约束统计 |
| [A 股单标的动态天平双仓策略](../strategies/dynamic-balance/A股单标的动态天平双仓策略.md) | 当前主线策略的公式、状态机、core/swing 逻辑 |
| [A 股单标的环境层设计](../strategies/dynamic-balance/A股单标的环境层设计.md) | 环境层计算规则 |
| [A 股单标的风控层设计](../strategies/dynamic-balance/A股单标的风控层设计.md) | 风控规则细节 |
| [A 股单标的仓位调节层设计](../strategies/dynamic-balance/A股单标的仓位调节层设计.md) | profile 与动态天平参数调节 |
