# A 股交易域数据结构与状态机

**文档目标：** 定义 A 股个人量化系统的公共数据结构、枚举、单位约定、订单状态机、bucket 账本不变量、T+1 库存置换生命周期、原因码和审计事件。本文是代码实现的 schema 级契约，服务当前 `A股单标的动态天平双仓策略`，并保持对后续 A 股策略通用。

---

## 0. 设计原则

### 0.1 单位必须显式

A 股系统中最容易混淆的是金额、股数、仓位比例和可卖量。所有字段必须在名称、文档和代码注释中明确单位。

| 后缀 | 单位 | 示例 |
|---|---|---|
| `_cny` | 人民币金额 | `target_amount_cny` |
| `_volume` | 股票股数，整数 | `available_volume` |
| `_price` | 每股价格，人民币 | `limit_price` |
| `_pct` | 比例，0 到 1 | `max_position_pct` |
| `_bps` | 基点，1bp=0.0001 | `slippage_bps` |
| `_ms` | Unix 毫秒时间戳 | `decision_time_ms` |
| `_date` | 交易日，YYYY-MM-DD | `trade_date` |

### 0.2 状态必须可复现

每次策略决策需要能够用保存下来的快照重放，因此结构体应尽量保存：

- 输入快照。
- 中间裁决。
- 输出意图。
- 风控原因。
- 订单状态。
- 成交回报。
- 账本更新。

### 0.3 策略字段与交易域字段分离

策略可以在 `metadata` 中写自己的解释字段，但通用执行层只依赖标准字段，不解析策略内部公式。

---

## 1. 顶层枚举

### 1.1 市场与交易方向

```text
MarketType        = A_SHARE
Direction         = BUY | SELL
InstrumentScope   = SINGLE_INSTRUMENT
DirectionMode     = LONG_ONLY
```

### 1.2 决策节奏

```text
DecisionCadence =
    DAILY_CLOSE       // 日线收盘确认动作
  | PRE_OPEN          // 开盘前准备动作
  | INTRADAY_1M       // 盘中分钟动作
  | INTRADAY_TICK     // 盘中 tick 动作
  | ORDER_EVENT       // 订单/成交事件驱动
  | RECONCILIATION    // 对账动作
```

### 1.3 数据质量

```text
DataQuality =
    OK              // 数据完整且时点可得
  | DEGRADED        // 部分弱数据缺失，可保守运行
  | INSUFFICIENT    // 关键数据缺失，不允许激进决策
  | STALE           // 数据过期，只能保守沿用或禁买
```

### 1.4 bucket 模型

```text
BucketModel =
    NONE
  | SINGLE_ACTIVE
  | CORE_SWING
  | CORE_SWING_LOCKED
  | CUSTOM
```

### 1.5 标准 bucket

```text
Bucket =
    default
  | core
  | swing
  | locked_core
  | custom:<name>
```

`locked_core` 默认不是策略主动方向性卖出来源；若配置允许，只能作为 T+1 库存置换来源，且总量不得下降。

---

## 2. MarketContextSnapshot

环境层输出。

```json
{
  "snapshot_id": "mctx-20260509-600000.SH",
  "instrument_code": "600000.SH",
  "trade_date": "2026-05-09",
  "decision_time_ms": 1778342400000,
  "market_state": "RISK_OFF",
  "sector_state": "WEAK",
  "concept_heat_state": "NEUTRAL",
  "liquidity_state": "SHRINKING",
  "breadth_state": "NEGATIVE",
  "volume_structure": "DISTRIBUTION",
  "context_score": -0.42,
  "risk_tags": ["market_selloff", "sector_breakdown"],
  "data_quality": "OK",
  "source_versions": {
    "market_bar": "daily:2026-05-09",
    "sector_bar": "sw2:2026-05-09",
    "breadth": "2026-05-09:close"
  }
}
```

字段约定：

| 字段 | 类型 | 是否可空 | 说明 |
|---|---|---|---|
| `snapshot_id` | string | 否 | 快照唯一 ID |
| `instrument_code` | string | 否 | A 股代码，如 `600000.SH` |
| `trade_date` | string | 否 | 交易日 |
| `decision_time_ms` | int64 | 否 | 生成时间 |
| `context_score` | float64 | 否 | [-1, 1] |
| `risk_tags` | string[] | 可空数组 | 结构化标签 |
| `data_quality` | enum | 否 | OK/DEGRADED/INSUFFICIENT/STALE |
| `source_versions` | object | 可空 | 数据版本，用于复现 |

---

## 3. RiskContextCaps

前置上下文风控输出。它在策略 `Step()` 之前生成，给仓位调节层和策略提供硬边界。

```json
{
  "caps_id": "rcap-20260509-600000.SH",
  "risk_mode": "RISK_REDUCED",
  "max_position_pct_cap": 0.45,
  "max_buy_amount_cny": 8000.0,
  "max_daily_add_pct": 0.03,
  "max_single_order_pct": 0.02,
  "min_cash_buffer_pct": 0.30,
  "allow_core_buy": true,
  "allow_swing_buy": false,
  "allow_sell": true,
  "only_risk_reduction": false,
  "force_profile": "CAUTIOUS",
  "kill_switch": false,
  "reason_codes": ["MARKET_RISK_OFF", "SWING_BUY_DISABLED"],
  "data_quality": "OK"
}
```

### 3.1 risk_mode

```text
RiskMode =
    NORMAL
  | RISK_REDUCED
  | DEFENSIVE_ONLY
  | SELL_ONLY
  | KILL_SWITCHED
```

### 3.2 字段语义

| 字段 | 类型 | 说明 |
|---|---|---|
| `max_position_pct_cap` | float64 | 本次决策允许的总仓位上限，不得超过实例硬上限 |
| `max_buy_amount_cny` | float64 | 本次或当前时段最大新增买入金额 |
| `max_daily_add_pct` | float64 | 当日最大新增仓位比例 |
| `max_single_order_pct` | float64 | 单笔最大仓位变化 |
| `min_cash_buffer_pct` | float64 | 最低现金缓冲 |
| `allow_core_buy` | bool | 是否允许核心仓新增买入 |
| `allow_swing_buy` | bool | 是否允许波动仓新增买入 |
| `allow_sell` | bool | 是否允许卖出 |
| `only_risk_reduction` | bool | 是否只允许降低风险暴露 |
| `force_profile` | string? | 强制仓位 profile，可空 |
| `kill_switch` | bool | 是否进入熔断 |

---

## 4. PositionAdjustmentProfile

仓位调节层输出。

```json
{
  "profile_id": "pap-20260509-600000.SH",
  "profile": "CAUTIOUS",
  "min_position_pct": 0.10,
  "max_position_pct": 0.45,
  "target_cash_buffer_pct": 0.30,
  "core_share_min": 0.75,
  "core_share_max": 0.95,
  "swing_max_pct": 0.05,
  "balance_beta_multiplier": 0.70,
  "inventory_gamma_multiplier": 1.20,
  "grid_step_multiplier": 1.35,
  "allow_core_buy": true,
  "allow_swing_buy": false,
  "allow_swing_sell": true,
  "reason_tags": ["market_risk_off", "reduce_swing_activity"]
}
```

Profile 是参数配置，不是订单。

---

## 5. StrategyInput / StrategyOutput

### 5.1 StrategyInput

```text
StrategyInput
├── input_id: string
├── trace_id: string
├── instance_id: string
├── strategy_id: string
├── instrument_code: string
├── decision_time_ms: int64
├── trade_date: string
├── cadence: DecisionCadence
├── market_data: MarketDataSnapshot
├── portfolio: PortfolioStateSnapshot
├── bucket_ledger: BucketLedgerSnapshot
├── market_context: MarketContextSnapshot
├── risk_caps: RiskContextCaps
├── position_profile: PositionAdjustmentProfile
├── runtime_state: object
├── param_pack: object
└── pending_order_summary: PendingOrderSummary
```

策略可以忽略未使用字段，但不能访问数据库、网络或文件。

### 5.2 StrategyOutput

```text
StrategyOutput
├── output_id: string
├── trace_id: string
├── runtime_state_patch: object
├── trade_intents: TradeIntent[]
├── strategy_events: StrategyEvent[]
└── debug_metrics: object
```

`debug_metrics` 只用于回测归因和开发调试，不作为执行层规则输入。

---

## 6. TradeIntent

策略输出的交易意图。

```json
{
  "intent_id": "intent-001",
  "trace_id": "trace-20260509-001",
  "instance_id": "inst-001",
  "strategy_id": "ashare_dynamic_balance_dual_bucket",
  "instrument_code": "600000.SH",
  "side": "BUY",
  "intent_type": "TARGET_POSITION_PCT",
  "target_position_pct": 0.52,
  "target_amount_cny": null,
  "target_volume": null,
  "bucket": "core",
  "confidence": 0.78,
  "priority": "NORMAL",
  "expiry_policy": {
    "type": "SAME_BAR",
    "expire_at_ms": null
  },
  "reason": "dynamic_balance_core_buy",
  "metadata": {
    "phase": "BUILDING_CORE",
    "trend_state": "LOW_ACCUMULATION",
    "benchmark_price": 10.25,
    "balance_signal": -0.63
  }
}
```

### 6.1 intent_type

```text
IntentType =
    TARGET_POSITION_PCT
  | TARGET_AMOUNT
  | TARGET_VOLUME
  | CANCEL_ORDER
```

### 6.2 priority

```text
IntentPriority =
    LOW
  | NORMAL
  | HIGH
  | RISK_REDUCTION
```

风险降低类卖出可使用 `RISK_REDUCTION`，便于风控在同等条件下优先处理。

---

## 7. OrderDraft / OrderRequest / TradeCommand

### 7.1 OrderDraft

OrderSizer 输出的订单草案。

```text
OrderDraft
├── draft_id
├── intent_id
├── side
├── instrument_code
├── bucket
├── order_type              // LIMIT / MARKET_PROTECTED / BEST_EFFORT_LIMIT
├── limit_price
├── raw_target_amount_cny
├── raw_target_volume
├── sized_amount_cny
├── sized_volume
├── size_reason_codes[]
└── trace_id
```

### 7.2 OrderRequest

后置风控允许后的正式订单请求。

```text
OrderRequest
├── order_request_id
├── client_order_id
├── intent_id
├── side
├── instrument_code
├── bucket
├── order_type
├── limit_price
├── volume
├── estimated_amount_cny
├── risk_decision_id
├── substitution_plan_id?
├── expire_at_ms
└── trace_id
```

### 7.3 TradeCommand

SaaS 下发给 LocalAgent 的指令。

```json
{
  "message_type": "command",
  "client_order_id": "inst001-swing-sell-20260509-093500-001",
  "instance_id": "inst-001",
  "instrument_code": "600000.SH",
  "side": "SELL",
  "order_type": "LIMIT",
  "limit_price": 10.38,
  "volume": 800,
  "bucket": "swing",
  "substitution_plan_id": "sub-001",
  "trace_id": "trace-20260509-001"
}
```

TradeCommand 不包含策略公式，不包含券商凭证。

---

## 8. OrderRiskDecision

后置订单风控输出。

```json
{
  "risk_decision_id": "ord-risk-001",
  "action": "CAP",
  "allowed": true,
  "original_volume": 1200,
  "capped_volume": 800,
  "original_amount_cny": 12456.0,
  "capped_amount_cny": 8304.0,
  "reason_code": "POSITION_LIMIT_CAP",
  "reason_detail": "target order exceeds max position under RISK_OFF context",
  "risk_tags": ["market_risk_off", "max_position_reduced"],
  "substitution_plan": null
}
```

### 8.1 action

```text
RiskAction =
    ALLOW
  | CAP
  | DELAY
  | REJECT
  | KILL_SWITCH
```

`CAP` 只能降低金额、数量或目标仓位，不能改变买卖方向。

---

## 9. PortfolioState

真实组合快照。实盘真源为 miniQMT；回测真源为 BacktestBroker。

```text
PortfolioState
├── portfolio_id
├── instance_id
├── instrument_code
├── trade_date
├── snapshot_time_ms
├── cash_available_cny
├── cash_frozen_cny
├── total_equity_cny
├── market_value_cny
├── total_volume
├── available_volume
├── frozen_volume
├── today_buy_volume
├── today_sell_volume
├── cost_basis_cny
├── last_price
├── source                // MINIQMT / BACKTEST_BROKER / MANUAL_RECONCILE
└── data_quality
```

### 9.1 关键定义

| 字段 | 说明 |
|---|---|
| `total_volume` | broker 账户中该标的总股数 |
| `available_volume` | 当前可卖股数，不含今日买入和冻结 |
| `frozen_volume` | 已被未完成卖单占用的股数 |
| `today_buy_volume` | 今日买入尚不可卖股数 |
| `cash_available_cny` | 可用资金 |
| `cash_frozen_cny` | 未完成买单冻结资金 |

策略不得写这些字段。

---

## 10. BucketLedger

bucket 是算法归因账本，不是券商真实分仓。它必须与 broker 的真实总持仓保持一致。

```text
BucketLedger
├── ledger_id
├── instance_id
├── instrument_code
├── snapshot_time_ms
├── buckets: map<Bucket, BucketState>
├── substitution_flows: T1SubstitutionFlow[]
├── version
└── data_quality
```

### 10.1 BucketState

```text
BucketState
├── bucket
├── total_volume
├── available_volume
├── frozen_volume
├── today_buy_volume
├── pending_buy_volume
├── pending_sell_volume
├── cost_basis_cny
├── avg_cost_price
├── realized_pnl_cny
└── metadata
```

### 10.2 账本总量不变量

任意时刻必须满足：

```text
portfolio.total_volume
  = Σ bucket.total_volume

portfolio.available_volume
  = Σ bucket.available_volume

portfolio.frozen_volume
  = Σ bucket.frozen_volume

portfolio.today_buy_volume
  = Σ bucket.today_buy_volume
```

如果 `locked_core` 被配置为外部封存展示资产，但仍在同一券商账户中，它也必须计入总风险暴露和上述不变量。

### 10.3 pending 不变量

```text
bucket.pending_buy_volume >= 0
bucket.pending_sell_volume >= 0
bucket.frozen_volume >= bucket.pending_sell_volume 的已冻结部分
cash_frozen_cny >= pending_buy_orders 的预估冻结金额
```

### 10.4 locked_core 不变量

默认：

```text
locked_core.total_volume 不得因策略方向性卖出下降
locked_core.available_volume 可以因置换临时下降
locked_core.today_buy_volume 可以因置换临时上升
置换完成后 locked_core.total_volume 必须保持不变
```

若用户人工解封，必须生成独立审计事件，不能由策略自动完成。

---

## 11. T+1 库存置换

### 11.1 T1SubstitutionPlan

```json
{
  "substitution_plan_id": "sub-001",
  "enabled": true,
  "instrument_code": "600000.SH",
  "sell_intent_bucket": "swing",
  "sell_from_bucket": "core",
  "reattribute_today_buy_to_bucket": "core",
  "requested_volume": 800,
  "reserved_volume": 800,
  "status": "RESERVED",
  "reason_code": "SWING_T0_SELL_WITH_CORE_INVENTORY",
  "created_at_ms": 1778342400000
}
```

### 11.2 status

```text
SubstitutionStatus =
    PROPOSED
  | RESERVED
  | SUBMITTED
  | PARTIAL_APPLIED
  | APPLIED
  | PARTIAL_ROLLED_BACK
  | ROLLED_BACK
  | EXPIRED
```

### 11.3 置换前提

必须同时满足：

- 同一 `instrument_code`。
- 卖出意图来自不可卖或不足额的 `swing` 今日买入部分。
- 存在同标的可卖老仓。
- 置换数量不超过当日已成交的 swing 买入数量或当前需要置换的卖出数量。
- 若使用 `locked_core_available`，实例必须显式开启 `allow_locked_core_substitution = true`。
- 置换后被置换 bucket 总量不下降。

### 11.4 应用规则

部分成交时，只按成交数量应用：

```text
actual broker sell:
    sell_from_bucket.available_volume -= filled_volume
    sell_from_bucket.total_volume     -= filled_volume

reattribution:
    sell_intent_bucket.today_buy_volume      -= filled_volume
    sell_intent_bucket.total_volume          -= filled_volume
    reattribute_bucket.today_buy_volume      += filled_volume
    reattribute_bucket.total_volume          += filled_volume
```

结果：

```text
被置换 bucket total_volume 不因置换下降
swing 真实止盈/回收按成交数量完成
今日买入属性转移到被置换 bucket，次交易日恢复可卖
```

### 11.5 回滚规则

| 事件 | 处理 |
|---|---|
| 卖单拒单 | 置换计划全部回滚，释放预留可卖量 |
| 卖单撤单 | 未成交部分回滚，已成交部分保留 |
| 部分成交 | 只应用成交数量，剩余继续 pending 或回滚 |
| 订单过期 | 未成交部分回滚 |
| 对账发现不一致 | 进入 `RECONCILE_REQUIRED` |

---

## 12. 订单状态机

### 12.1 状态图

```text
NEW_INTENT
  -> SIZED
  -> ORDER_RISK_ALLOWED
  -> LOCAL_PRECHECK_PASSED
  -> SUBMITTED
  -> ACCEPTED
  -> PARTIALLY_FILLED
  -> FILLED

NEW_INTENT
  -> SIZED
  -> ORDER_RISK_CAPPED
  -> LOCAL_PRECHECK_PASSED
  -> SUBMITTED ...

NEW_INTENT -> ORDER_RISK_DELAYED
NEW_INTENT -> ORDER_RISK_REJECTED
NEW_INTENT -> KILL_SWITCHED

SUBMITTED -> BROKER_REJECTED
ACCEPTED  -> CANCEL_REQUESTED -> CANCELED
ACCEPTED  -> EXPIRED
PARTIALLY_FILLED -> CANCEL_REQUESTED -> PARTIALLY_CANCELED
任何状态 -> RECONCILE_REQUIRED -> RECONCILED
```

### 12.2 OrderState

```text
OrderState
├── order_state_id
├── client_order_id
├── broker_order_id
├── instance_id
├── instrument_code
├── side
├── bucket
├── status
├── requested_volume
├── accepted_volume
├── filled_volume
├── remaining_volume
├── limit_price
├── avg_fill_price
├── frozen_cash_cny
├── frozen_volume
├── fees_cny
├── tax_cny
├── substitution_plan_id?
├── reason_codes[]
├── created_at_ms
├── updated_at_ms
└── trace_id
```

### 12.3 状态副作用

| 状态/事件 | 现金 | 持仓 | bucket | 策略网格状态 |
|---|---|---|---|---|
| `SIZED` | 不冻结 | 不冻结 | 不变 | 不变 |
| `ORDER_RISK_ALLOWED` | 可预估占用 | 可预估占用 | 可预留 | 不变 |
| `SUBMITTED` 买单 | 冻结预估现金 | 不变 | pending_buy 增加 | pending |
| `SUBMITTED` 卖单 | 不变 | 冻结可卖量 | pending_sell 增加 | pending |
| `ACCEPTED` | 保持冻结 | 保持冻结 | 保持 pending | pending |
| `PARTIALLY_FILLED` | 按成交释放/扣减 | 按成交更新 | 按成交归因 | 按成交更新部分状态 |
| `FILLED` | 释放剩余冻结 | 完整更新 | 完整归因 | 更新成交网格 |
| `BROKER_REJECTED` | 释放冻结 | 释放冻结 | 回滚 pending/置换 | 不标记成交 |
| `CANCELED` | 释放未成交冻结 | 释放未成交冻结 | 回滚未成交部分 | 可重新评估 |
| `EXPIRED` | 同撤单 | 同撤单 | 同撤单 | 可重新评估 |
| `RECONCILED` | 以 broker 为准 | 以 broker 为准 | 生成修正流水 | 记录对账事件 |

---

## 13. BrokerExecutionReport

```text
BrokerExecutionReport
├── report_id
├── source                 // MINIQMT / BACKTEST_BROKER
├── client_order_id
├── broker_order_id
├── report_type            // ORDER / TRADE / CANCEL / SNAPSHOT
├── broker_status
├── instrument_code
├── side
├── filled_volume
├── fill_price
├── fill_amount_cny
├── commission_cny
├── stamp_tax_cny
├── transfer_fee_cny
├── trade_time_ms
├── raw_payload
└── trace_id
```

`raw_payload` 用于排查，但业务逻辑只读取标准字段。

---

## 14. AshareMarketRules

```text
AshareMarketRules
├── instrument_code
├── trade_date
├── is_trading_day
├── sessions[]
├── allow_call_auction
├── lot_size                  // 默认 100
├── allow_odd_lot_liquidation // true
├── price_tick
├── upper_limit_price
├── lower_limit_price
├── is_suspended
├── is_st
├── is_delisting_risk
├── max_order_volume
├── min_order_amount_cny
└── data_quality
```

实盘缺少 `upper_limit_price / lower_limit_price / is_suspended / price_tick` 时，后置风控必须拒绝真实下单。

---

## 15. CostModel

```text
CostModel
├── commission_pct
├── min_commission_cny
├── stamp_tax_pct_sell
├── transfer_fee_pct
├── slippage_bps
├── safety_margin_bps
└── model_version
```

卖出成本必须包含印花税。网格收益过滤应至少满足：

```text
expected_grid_profit_pct
  > 2 * commission_pct
  + stamp_tax_pct_sell
  + transfer_fee_pct
  + slippage_pct
  + safety_margin_pct
```

---

## 16. DecisionTrace

每次决策必须保存完整审计快照。

```text
DecisionTrace
├── trace_id
├── instance_id
├── strategy_id
├── instrument_code
├── cadence
├── decision_time_ms
├── input_hash
├── MarketContextSnapshot
├── RiskContextCaps
├── PositionAdjustmentProfile
├── StrategyStateBefore
├── TradeIntent[]
├── OrderDraft[]
├── OrderRiskDecision[]
├── OrderRequest[]
├── BrokerExecutionReport[]
├── LedgerUpdate[]
├── StrategyStateAfter
├── reason_tags[]
└── result_summary
```

### 16.1 input_hash

`input_hash` 应基于关键输入快照稳定生成，便于回测和实盘重放。

建议包含：

- 行情窗口版本。
- 组合快照版本。
- 订单状态版本。
- 参数包版本。
- 环境快照版本。

---

## 17. 原因码

### 17.1 环境与前置风控

```text
MARKET_RISK_ON
MARKET_NEUTRAL
MARKET_RISK_OFF
MARKET_PANIC
SECTOR_STRONG
SECTOR_WEAK
SECTOR_BROKEN
LIQUIDITY_DRY
BREADTH_EXTREME_NEGATIVE
DATA_DEGRADED
DATA_INSUFFICIENT
SWING_BUY_DISABLED
CORE_BUY_CAPPED
SELL_ONLY_MODE
KILL_SWITCH_TRIGGERED
```

### 17.2 订单风控

```text
OUT_OF_SESSION
CALL_AUCTION_DISABLED
SUSPENDED
ST_RESTRICTED
DELISTING_RISK
LIMIT_UP_BUY_BLOCKED
LIMIT_DOWN_SELL_BLOCKED
INVALID_PRICE_TICK
INVALID_LOT_SIZE
MIN_ORDER_AMOUNT_NOT_MET
INSUFFICIENT_CASH
INSUFFICIENT_POSITION
T1_UNAVAILABLE
T1_SUBSTITUTION_APPLIED
T1_SUBSTITUTION_NOT_ALLOWED
POSITION_LIMIT_CAP
DAILY_ADD_LIMIT_CAP
SINGLE_ORDER_LIMIT_CAP
LOW_LIQUIDITY
ORDER_DUPLICATE_GRID
PENDING_ORDER_EXISTS
AGENT_OFFLINE
BROKER_REJECTED
REPORT_TIMEOUT
RECONCILE_REQUIRED
```

### 17.3 账本与置换

```text
BUCKET_AVAILABLE_INSUFFICIENT
LOCKED_CORE_DIRECTIONAL_SELL_BLOCKED
LOCKED_CORE_SUBSTITUTION_DISABLED
SUBSTITUTION_RESERVED
SUBSTITUTION_PARTIAL_APPLIED
SUBSTITUTION_APPLIED
SUBSTITUTION_ROLLED_BACK
LEDGER_INVARIANT_BROKEN
BROKER_LEDGER_MISMATCH
```

---

## 18. 审计事件类型

```text
MARKET_CONTEXT_CREATED
RISK_CONTEXT_CAPS_CREATED
POSITION_PROFILE_CREATED
STRATEGY_STEP_EXECUTED
TRADE_INTENT_CREATED
ORDER_SIZED
ORDER_RISK_DECIDED
ORDER_SUBMITTED
ORDER_ACCEPTED
ORDER_PARTIALLY_FILLED
ORDER_FILLED
ORDER_REJECTED
ORDER_CANCELED
ORDER_EXPIRED
T1_SUBSTITUTION_RESERVED
T1_SUBSTITUTION_APPLIED
T1_SUBSTITUTION_ROLLED_BACK
PORTFOLIO_RECONCILED
KILL_SWITCH_TRIGGERED
KILL_SWITCH_RELEASE_REQUESTED
KILL_SWITCH_RELEASED
CORPORATE_ACTION_APPLIED
SECURITY_STATUS_CHANGED
DATA_QUALITY_DEGRADED
```

---

## 19. Kill Switch 状态

### 19.1 实例状态

```text
InstanceStatus =
    STOPPED
  | RUNNING
  | ERROR
  | KILL_SWITCHED
  | DELETED
```

### 19.2 熔断后动作

进入 `KILL_SWITCHED` 后：

- 禁止新增买入。
- 取消或冻结未下发买单。
- 保留风险降低卖出意图，但需要按实例配置决定是否人工确认。
- 停止 GA 参数自动应用。
- 强制记录 `DecisionTrace`。
- 等待人工恢复或对账恢复。

### 19.3 人工恢复流程

```text
1. 操作员查看熔断原因、账户快照、未完成订单、最近成交、bucket 账本。
2. 执行一次 broker 全量对账。
3. 确认是否撤销未完成订单。
4. 确认是否允许只卖不买观察期。
5. 写入恢复审计事件。
6. 状态从 KILL_SWITCHED -> STOPPED 或 RUNNING。
```

默认恢复到 `STOPPED`，由用户再次启动。

---

## 20. 测试计划

### 20.1 数据结构测试

- 所有金额字段必须以 `_cny` 结尾。
- 所有股数字段必须以 `_volume` 结尾。
- 所有比例字段必须以 `_pct` 结尾。
- JSON 编解码后枚举值不丢失。
- `input_hash` 对相同输入稳定。

### 20.2 账本不变量测试

- 买入全成后 bucket 总量与 portfolio 总量一致。
- 卖出全成后 bucket 总量与 portfolio 总量一致。
- 部分成交后只更新部分数量。
- 拒单后 pending 和冻结完全释放。
- 撤单后未成交部分回滚。
- `locked_core` 方向性卖出被拒绝。

### 20.3 T+1 置换测试

- swing 今日买入不可卖时，有 core 老仓则输出置换计划。
- 置换部分成交只应用成交数量。
- 置换拒单全部回滚。
- `allow_locked_core_substitution=false` 时不得使用 locked_core。
- 使用 locked_core 置换后 `locked_core.total_volume` 不下降。

### 20.4 订单状态机测试

- `NEW_INTENT -> FILLED` 完整路径。
- `NEW_INTENT -> ORDER_RISK_REJECTED` 路径。
- `SUBMITTED -> BROKER_REJECTED` 释放冻结。
- `ACCEPTED -> PARTIALLY_FILLED -> PARTIALLY_CANCELED`。
- `REPORT_TIMEOUT -> RECONCILE_REQUIRED`。

