# A 股动态天平双仓策略实现落地规格与迁移计划

**文档目标：** 将现有 A 股个人量化设计文档转换为可执行的工程落地路线，明确从当前 Python 策略框架迁移到 `StrategyInput -> StrategyBase.step() -> StrategyOutput / TradeIntent -> OrderSizer -> OrderRiskDecision -> BrokerExecutionReport -> RuntimeStateManager / BucketLedger` 的顺序、边界、验收标准和文档清理要求。

---

## 0. 核心结论

本轮迁移采用**一步到位的破坏性接口升级**：

- `StrategyBase.step(input: StrategyInput) -> StrategyOutput` 是唯一策略决策入口。
- `on_bar()`、`on_tick()`、`generate_signal()` 和执行器直接消费 `Signal` 的路径废弃。
- 策略只能输出 `TradeIntent[]` 与 `RuntimeStatePatch`，不能直接输出真实下单数量，不能修改真实现金、真实持仓、可卖量或冻结状态。
- 订单数量、A 股合法性、T+1、涨跌停、停牌、资金、可卖量、库存置换和成交真源统一由交易域、执行层和 broker 状态流处理。
- 现有策略必须迁移到新接口；无法迁移的策略应从注册表移除或标记为暂不可用。

本文件是实施入口。实现前必须先完成本文档列出的文档清理和契约收口，再进入代码重构。

---

## 1. 迁移前代码现状

迁移前后端策略框架的主路径是：

```text
KLine / Tick
    -> StrategyBase.on_bar() / on_tick()
    -> Signal[]
    -> StrategyExecutor._process_signal()
    -> OrderSizer.size_signal()
    -> TradingRiskChecker.validate_order()
    -> Broker.place_order()
    -> StrategyBase.on_order() / on_trade()
```

当前可复用部分：

| 模块 | 可复用内容 | 迁移动作 |
|---|---|---|
| `StrategyContext` | run_id、mode、instruments、parameters、current_time | 保留并作为 `StrategyInput` 构造来源之一 |
| `StrategyStateProxy` | 策略算法状态持久化与广播 | 保留；策略状态变更通过 `RuntimeStatePatch` 或 state proxy 收敛 |
| `OrderSizer` | A 股买入 100 股、卖出可用量、目标金额转股数 | 改为消费 `TradeIntent` / `OrderDraft`，不再消费 `Signal` |
| `TradingRiskChecker` | 价格、数量、交易状态、涨跌停、资金和持仓校验 | 升级为后置 `OrderRiskLayer` 的实现基础 |
| `BrokerBase` | `OrderRequest`、`OrderResponse`、`TradeRecord` 状态流 | 保留，并补齐结构化事件适配 |
| `PullbackGridStrategy` | pending / filled 由订单和成交回调驱动 | 改为在 `step()` 输出网格 `TradeIntent` |
| `AshareSupermarketStrategy` | 多标的买卖、T+1 和卖出优先级思想 | 改为输出 `TradeIntent`，优先级进入 intent 字段 |

当前必须替换部分：

| 旧接口 / 旧语义 | 问题 | 新接口 / 新语义 |
|---|---|---|
| `on_bar(bar) -> Signal[]` | 策略直接绑定行情事件，缺少统一输入快照 | `step(StrategyInput) -> StrategyOutput` |
| `on_tick(tick) -> Signal[]` | tick 与 bar 分支重复，难以保证回测实盘同构 | `StrategyInput.cadence` 区分 `BAR` / `TICK` |
| `Signal` | 语义过薄，不能表达 bucket、风控上下文、状态补丁 | `TradeIntent` |
| `generate_signal()` | 鼓励策略直接生成执行信号 | 删除；使用 `build_intent()` 或直接构造 `TradeIntent` |
| `Dict[str, Any]` 订单/成交回调 | 事件字段不稳定 | `OrderStateEvent` / `TradeExecutionEvent` |

---

## 1.1 当前落地状态

截至本轮实现，后端主路径已经切换为：

```text
KLine / Tick
    -> StrategyInput
    -> StrategyBase.step()
    -> StrategyOutput.trade_intents[]
    -> OrderSizer.draft_intent() / size_intent()
    -> OrderDraft
    -> OrderRiskLayer.evaluate_order()
    -> OrderRiskDecision[ALLOW/CAP/DELAY/REJECT/KILL_SWITCH]
    -> Broker.place_order()
    -> OrderStateEvent / TradeExecutionEvent
```

已落地内容：

- `Signal` / `SignalType` 不再作为 `backend/core` 与 `backend/tests` 主路径接口出现。
- `TradeIntent` 已补齐 `intent_type`、`target_volume`、`trace_id`、`expiry_policy` 等交易域字段。
- `StrategyInput` 已包含 `input_id`、`trace_id`、`decision_time_ms`、`trade_date` 和前置 `risk_caps` 快照。
- `EnvironmentLayer` 已落地，能把参数化环境输入、行情事件字段和当前个股行情合成为 `MarketContextSnapshot`，输出环境状态、评分、风险标签、数据质量和审计指纹。
- `ContextRiskLayer` 已落地，负责在策略 `step()` 前生成 `RiskContextCaps`，覆盖 `RISK_OFF`、`PANIC`、行业破裂、低流动性、数据质量降级和最大回撤熔断。
- `PositionAdjustmentLayer` 已落地，能根据 `market_context`、`risk_caps`、组合快照和策略状态生成 `PositionAdjustmentProfile`。
- `OrderSizer` 生成 `OrderDraft`，保留原始目标、修正后数量、金额和尺寸修正原因。
- 后置风控输出 `OrderRiskDecision`，支持 `ALLOW`、`CAP`、`DELAY`、`REJECT`、`KILL_SWITCH`。
- T+1 库存置换计划已由后置风控输出为 `substitution_plan`，并由 `BucketLedger` 在成交回报确认后提交或回滚。
- `StrategyExecutor` 消费结构化风控决策，只有允许或限额后的订单会进入 broker。

本轮补齐内容：

- `BucketLedger` 已成为独立分桶账本，支持快照持久化、恢复校验、成交提交/回滚、T+1 置换归因和公司行为按 bucket 比例调整。
- `AshareDataContextProvider` 已作为环境层数据上下文入口，统一汇总 `MarketDataSnapshot`、`InstrumentMaster`、交易日历、行业/概念、复权因子指纹和参数化环境输入。
- T+1 置换计划已形成结构化 `SubstitutionPlan`，挂载在 `OrderRiskDecision` / `OrderRequest.metadata` 后由 `BucketLedger` 管理生命周期。

## 2. 目标接口

### 2.1 StrategyBase

```text
StrategyBase
    on_init() -> None
    step(input: StrategyInput) -> StrategyOutput
    on_order(event: OrderStateEvent) -> RuntimeStatePatch | None
    on_trade(event: TradeExecutionEvent) -> RuntimeStatePatch | None
    on_stop() -> None
```

策略实现必须遵守：

- 不读取数据库、broker、系统时间、网络或文件。
- 不调用 miniQMT。
- 不计算真实可卖量、冻结资金或最终合法订单股数。
- 不把信号、已报或下单成功视为成交。
- 所有算法状态变化必须进入 `RuntimeStatePatch` 或 `StrategyStateProxy`。

### 2.2 StrategyInput

`StrategyInput` 是策略唯一输入快照，至少包含：

```text
run_id
strategy_id
timestamp
cadence                 // BAR / TICK / ORDER / TRADE / RECONCILE
instrument_code
market_data             // 当前 bar/tick 与标准 MarketDataSnapshot
portfolio_state         // 真实组合快照，只读
bucket_ledger           // bucket 归因快照，只读
market_context          // 环境层输出
risk_caps               // 前置风控输出
position_profile        // 仓位调节层输出
open_orders             // 未完成订单快照
strategy_state          // 策略算法状态快照
parameters              // 实例参数
```

缺失策略必需字段时，输入构造层必须显式标记数据质量；策略只能输出空意图、风险降低意图或保守意图。

### 2.3 StrategyOutput

`StrategyOutput` 至少包含：

```text
trade_intents: TradeIntent[]
runtime_state_patch: RuntimeStatePatch | None
decision_tags: string[]
trace_payload: dict
```

`StrategyOutput` 不允许包含真实账户字段，例如：

```text
cash
available_cash
long_volume
available_volume
frozen_volume
today_buy_volume
```

### 2.4 TradeIntent

`TradeIntent` 是策略层输出的唯一交易语义：

```text
intent_id
strategy_id
run_id
instrument_code
direction               // BUY / SELL / HOLD
bucket                  // core / swing / locked_core / none
reason
priority
confidence
target_amount
target_position_pct
limit_price_hint
metadata
created_at
```

约束：

- `BUY` / `SELL` 必须有 `bucket` 和 `reason`。
- `target_amount`、`target_position_pct`、`metadata.requested_volume` 至少提供一种尺寸意图。
- `locked_core` 默认不能方向性卖出，只能在显式允许时作为 T+1 库存置换来源。
- 策略不得把 intent 标记为成交。

### 2.5 RuntimeStatePatch

`RuntimeStatePatch` 只描述算法状态变化：

```text
set: dict
unset: string[]
append_events: dict[]
```

可包含：

- 动态基准。
- 趋势状态。
- 网格 pending 引用。
- 最近成交网格层级。
- 策略内部评分。

不可包含：

- 真实现金。
- 真实持仓。
- 可卖量。
- 冻结资金或冻结股份。
- broker 订单生命周期的最终状态。

---

## 3. 目标执行链路

目标链路固定为：

```text
Market Data / Order Event / Trade Event
    -> StrategyInputBuilder
    -> EnvironmentLayer
    -> ContextRiskLayer
    -> PositionAdjustmentLayer
    -> StrategyBase.step()
    -> TradeIntent[]
    -> OrderSizer
    -> OrderRiskLayer
    -> OrderRouter / Broker
    -> BrokerExecutionReport
    -> RuntimeStateManager
    -> BucketLedger
    -> StrategyBase.on_order() / on_trade()
    -> DecisionTrace
```

职责边界：

| 层 | 负责 | 不负责 |
|---|---|---|
| StrategyInputBuilder | 汇总只读快照 | 生成买卖方向 |
| EnvironmentLayer | 环境状态和数据质量 | 下单、仓位方向 |
| ContextRiskLayer | 前置风险上限 | 校验具体订单股数 |
| PositionAdjustmentLayer | 仓位工作空间 | 生成交易意图 |
| StrategyBase.step | 产生 `TradeIntent` 和算法状态补丁 | 真实订单、现金、可卖量 |
| OrderSizer | 目标金额/仓位转候选股数 | 策略判断 |
| OrderRiskLayer | A 股规则和账户约束裁决 | 创造反向交易 |
| RuntimeStateManager | 订单、成交、冻结和真实组合收敛 | 策略公式 |
| BucketLedger | bucket 归因与 T+1 置换账本 | broker 真源替代 |

---

## 4. 迁移阶段

### Phase 0：文档收口

先完成：

1. 新增本文档。
2. 更新 [A 股个人量化开发文档索引](../trading/README.md)和[系统架构设计](../architecture/系统架构设计.md)，把本文档作为实施入口。
3. 清理 [A 股单标的动态天平双仓策略](../trading/strategies/dynamic-balance/A股单标的动态天平双仓策略.md)末尾重复 `0.2`。
4. 修正 [A 股单标的环境层设计](../trading/strategies/dynamic-balance/A股单标的环境层设计.md)的小节编号。
5. 搜索旧 `Signal` / `on_bar` / `on_tick` 文字，确认哪些是历史说明，哪些必须改为目标契约。

退出标准：

- 文档索引能清楚说明“先读本文档，再实施代码迁移”。
- 主策略文档无重复章节。
- 环境层文档编号连续。

### Phase 1：交易域类型

新增或重写核心类型：

```text
StrategyInput
StrategyOutput
TradeIntent
RuntimeStatePatch
OrderStateEvent
TradeExecutionEvent
OrderRiskDecision
BucketLedgerSnapshot
```

退出标准：

- 类型可独立单元测试。
- `TradeIntent` 必填字段校验覆盖 bucket、reason、尺寸意图。
- `RuntimeStatePatch` 拒绝真实账户字段。

### Phase 2：StrategyBase 破坏性升级

执行：

1. 删除 `Signal`、`SignalType`、`generate_signal()` 主路径。
2. 删除 `on_bar()` / `on_tick()` 抽象要求。
3. 增加 `step(input: StrategyInput) -> StrategyOutput` 抽象方法。
4. 将 `on_order()` / `on_trade()` 参数改为结构化事件。
5. `get_statistics()` 改为统计 intents、state patches、orders，而不是 signals。

退出标准：

- 所有 `StrategyBase` 子类不能再通过旧接口运行。
- 策略注册仍以继承 `StrategyBase` 为准。

### Phase 3：执行器迁移

执行：

1. 行情事件进入 `StrategyInputBuilder`。
2. `StrategyExecutor` 调用 `strategy.step(strategy_input)`。
3. `_process_signal()` 改为 `_process_trade_intent()`。
4. `OrderSizer.size_signal()` 改为 `draft_intent()` / `size_intent()`。
5. `TradingRiskChecker` 升级为 `OrderRiskLayer` 的实现基础，输出结构化 `OrderRiskDecision`。
6. broker 回报先更新 `RuntimeStateManager` 和 `BucketLedger`，再回调策略。

退出标准：

- 回测和实盘路径都不再直接消费 `Signal`。
- 拒单、撤单、部分成交、全部成交都通过事件驱动策略算法状态。

### Phase 4：现有策略迁移

`PullbackGridStrategy`：

- `step()` 在 `cadence=TICK` 时处理回撤确认和卖出触发。
- `cadence=BAR` 时只更新趋势 EMA 和趋势状态。
- 网格触发输出 `TradeIntent`，pending 状态由订单事件确认。
- filled 状态只由成交事件确认。

`AshareSupermarketStrategy`：

- `step()` 在 bar 输入下更新盒体、候选买入、止盈止损和再平衡逻辑。
- 买卖信号改为 `TradeIntent`。
- 卖出原因映射到 `priority` 和 `metadata.sell_reason`。
- 多标的支持保留，`instrument_scope=MULTI`。

退出标准：

- 两个策略不再实现 `on_bar()` / `on_tick()`。
- 两个策略不再调用 `generate_signal()`。

### Phase 5：测试与文档同步

测试迁移：

- 重写 `test_strategy_base.py`，覆盖 `step()`、`TradeIntent`、`RuntimeStatePatch`。
- 重写 `test_pullback_grid.py` 和 `test_ashare_supermarket.py`。
- 更新执行器和策略管理器测试，移除旧 `Signal` 断言。

文档同步：

- 更新 `backend/docs/STRATEGY.md`、`backend/docs/MODULES.md`、`backend/docs/EXAMPLES.md` 中旧接口示例。
- `Signal` 只允许作为历史迁移说明出现，不作为当前开发示例。

退出标准：

- `rg -n "generate_signal|on_bar\\(|on_tick\\(|SignalType|from core.strategies.base import Signal" backend/core backend/tests` 不应命中主路径代码。
- 策略相关单元测试和执行器链路测试通过。

---

## 5. 验收矩阵

| 场景 | 验收点 |
|---|---|
| 行情事件 | 能构造完整 `StrategyInput` 并调用 `step()` |
| 买入 intent | 必须带 bucket、reason、尺寸意图 |
| 卖出 intent | 必须经过可卖量、T+1、涨跌停、停牌校验 |
| 拒单 | 只通过 `OrderStateEvent` 清理 pending，不标记成交 |
| 部分成交 | 只按实际成交数量更新 bucket |
| 全部成交 | 策略算法状态由 `TradeExecutionEvent` 更新 |
| `locked_core` | 默认不方向性卖出 |
| 数据缺失 | 只能保守降级，不得输出更激进意图 |
| 回测 | 不允许把 intent 直接视为成交 |
| 实盘 | 只信 miniQMT 委托和成交回报 |

---

## 6. 明确不做

本迁移不做：

- 不保留长期 Legacy `StrategyBase`。
- 不提供旧 `Signal` 到新 `TradeIntent` 的长期兼容层。
- 不让策略直接计算真实合法订单数量。
- 不让策略直接读写真实账户状态。
- 不在 LocalAgent 中运行策略代码。

---

## 7. 实施顺序

推荐提交顺序：

1. 文档收口提交。
2. 交易域类型提交。
3. `StrategyBase` 与执行器主链路提交。
4. `PullbackGridStrategy` 迁移提交。
5. `AshareSupermarketStrategy` 迁移提交。
6. 测试与后端文档同步提交。

每个提交都必须能说明：

- 改了哪条契约。
- 旧路径是否已删除。
- 哪些测试覆盖了新行为。
- 是否仍存在待迁移引用。
