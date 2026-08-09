# A 股回测 Broker 与成交撮合契约

**文档目标：** 定义 A 股个人量化系统的回测 broker、仿真 broker 与实盘 miniQMT 状态流的同构规则。本文服务当前 `A股单标的动态天平双仓策略` 的 GA 与回测，同时作为后续 A 股策略共用的撮合、成本、风控和成交真实性契约。

---

## 0. 核心定位

回测 broker 的职责不是让策略“尽量成交”，而是让策略在历史数据中接受与实盘尽可能一致的约束。

回测 broker 必须模拟：

- A 股交易日历和交易时段。
- T+1。
- 100 股整数倍和零股清仓。
- 涨跌停。
- 停牌。
- ST/退市风险限制。
- 手续费、最低佣金、印花税、过户费、滑点。
- 成交量不足导致的部分成交。
- 委托状态、成交状态、撤单、废单、过期。
- bucket 归因与 T+1 库存置换。

回测 broker 不得把策略信号直接转换为成交。

---

## 1. 与实盘状态流同构

### 1.1 实盘路径

```text
TradeIntent
  -> OrderSizer
  -> OrderRiskDecision
  -> OrderRequest
  -> TradeCommand
  -> QMT Agent
  -> miniQMT
  -> OrderReport
  -> TradeReport
  -> RuntimeStateManager
```

### 1.2 回测路径

```text
TradeIntent
  -> OrderSizer
  -> OrderRiskDecision
  -> OrderRequest
  -> BacktestBroker.SubmitOrder
  -> SimulatedOrderReport
  -> SimulatedTradeReport
  -> RuntimeStateManager
```

两者的差异只在 broker：

| 环节 | 实盘 | 回测 |
|---|---|---|
| 委托接受 | miniQMT 返回 | BacktestBroker 根据规则返回 |
| 成交价格 | miniQMT 成交回报 | 历史数据保守撮合 |
| 成交数量 | miniQMT 成交回报 | 成交量/盘口参与率模拟 |
| 订单状态 | miniQMT 委托状态 | 模拟状态机 |
| 账户快照 | miniQMT 账户 | 回测账户模型 |

---

## 2. Broker 模式

### 2.1 EOD 日线模式

用于低频回测和 GA 快速评估。

特点：

- 决策通常在 T 日收盘后产生。
- 委托最早在 T+1 交易日撮合。
- 日线 high/low 只能证明价格区间曾经出现，不能证明先后顺序。
- 对盘中网格不充分，必须采用保守撮合。

### 2.2 Minute 分钟模式

用于当前双仓策略的 swing 网格回测。

特点：

- 每根已完成 1m bar 后产生决策。
- 下一根或当前可用时点撮合，取决于模拟时钟设计。
- 可使用分钟成交量限制参与率。
- 仍不能使用未来分钟数据。

### 2.3 Tick / 盘口模式

用于高精度仿真。

特点：

- 使用 tick、bid/ask、成交明细和盘口深度。
- 可以更真实模拟涨跌停打开、排队和部分成交。
- 成本高，适合验证冠军参数，不适合每代 GA 全量使用。

---

## 3. 统一订单结构

```text
BacktestOrder
├── client_order_id
├── instance_id
├── instrument_code
├── side
├── bucket
├── order_type              // LIMIT / MARKET_PROTECTED / BEST_EFFORT_LIMIT
├── limit_price
├── volume
├── submitted_at_ms
├── expire_at_ms
├── substitution_plan_id?
├── status
└── trace_id
```

回测 broker 输出与实盘同构的：

```text
SimulatedOrderReport
SimulatedTradeReport
PortfolioSnapshot
PositionSnapshot
```

---

## 4. 交易日历与时段

### 4.1 默认交易时段

```text
09:15 - 09:25 集合竞价，可配置是否允许
09:30 - 11:30 连续竞价
13:00 - 15:00 连续竞价
```

v1 建议：

- 默认只允许连续竞价。
- 回测中如果策略在非交易时段产生意图，应延迟到下一允许时段，而不是立即成交。

### 4.2 节假日与非交易日

非交易日：

- 不撮合。
- 未过期订单保持 pending 或按策略配置过期。
- T+1 可卖性按交易日推进，不按自然日推进。

---

## 5. 数量规则

### 5.1 买入

买入必须满足：

```text
buy_volume % 100 == 0
buy_volume >= 100
estimated_amount_cny >= min_order_amount_cny
```

若目标金额不足一手：

- core 建仓意图：可延迟或累计，不能强行成交。
- swing 网格意图：通常过滤。

### 5.2 卖出

普通卖出默认 100 股整数倍。

清仓时允许零股：

```text
if sell_volume == total_available_volume and total_available_volume < 100:
    allow odd lot liquidation
```

非清仓零股卖出默认拒绝。

---

## 6. 价格规则

### 6.1 price tick

所有限价必须按 `price_tick` 对齐。

```text
BUY limit_price 向下或按配置取保守 tick
SELL limit_price 向上或按配置取保守 tick
```

建议 OrderSizer 先修正，OrderRiskLayer 再校验。

### 6.2 涨跌停

买入价格不得超过涨停价，卖出价格不得低于跌停价。

```text
BUY blocked if limit_price >= upper_limit_price and conservative_limit_buy_block = true
SELL blocked if limit_price <= lower_limit_price and conservative_limit_sell_block = true
```

### 6.3 一字板

日线或分钟显示一字涨停：

```text
high == low == upper_limit_price
```

默认：

- 买入不成交。
- 卖出如果卖价小于等于涨停价，可以按规则成交，但要受成交量参与率约束。

日线或分钟显示一字跌停：

```text
high == low == lower_limit_price
```

默认：

- 卖出不成交。
- 买入可以成交但策略通常不应在强风控中新增买入。

---

## 7. 日线撮合规则

### 7.1 触达不等于成交

日线 `low <= limit_price <= high` 只能说明价格可能触达，不保证成交。必须通过配置选择保守策略。

### 7.2 推荐日线撮合策略

```text
DailyFillPolicy = CONSERVATIVE_NEXT_OPEN_OR_LIMIT
```

买入：

1. 若当日停牌，不成交。
2. 若一字涨停，不成交。
3. 若开盘价高于买入限价，默认不成交；除非 low 后续触达，按保守触达策略。
4. 若开盘价低于或等于限价，可按 min(open, limit_price + slippage) 成交。
5. 若仅 high/low 区间触达，按 conservative_touch_policy 决定是否成交。

卖出：

1. 若当日停牌，不成交。
2. 若一字跌停，不成交。
3. 若开盘价低于卖出限价，默认不成交；除非 high 后续触达，按保守触达策略。
4. 若开盘价高于或等于限价，可按 max(open, limit_price - slippage) 成交。
5. 若仅 high/low 区间触达，按 conservative_touch_policy 决定是否成交。

### 7.3 conservative_touch_policy

```text
NEVER_FILL_ON_TOUCH        // 仅 high/low 触达不成交，最保守
FILL_WITH_PENALTY          // 触达成交但加大滑点并限制数量
FILL_IF_CLOSE_CONFIRMS     // 收盘方向确认才成交
```

GA 默认建议：`FILL_WITH_PENALTY` 或 `NEVER_FILL_ON_TOUCH`，避免策略利用日线顺序漏洞。

### 7.4 日线成交量限制

即使价格满足，也必须限制成交量：

```text
max_fill_volume = floor(day_volume * participation_cap_pct)
```

默认：

```text
participation_cap_pct = 0.5% ~ 2%
```

个人单标的策略通常订单较小，但仍应模拟低流动性风险。

---

## 8. 分钟撮合规则

### 8.1 输入

每根分钟 bar：

```text
open/high/low/close/volume/amount
limit_up/down
suspended flag
```

可选：bid/ask、盘口深度。

### 8.2 限价撮合

买入：

```text
if low <= limit_price and not limit_up_blocked:
    fill_price = min(limit_price, open_or_next_trade_price) + slippage
else:
    no fill
```

卖出：

```text
if high >= limit_price and not limit_down_blocked:
    fill_price = max(limit_price, open_or_next_trade_price) - slippage
else:
    no fill
```

若只有 OHLCV，没有先后顺序，不能在同一根分钟 bar 内同时完成“先买后卖”的套利链条。

### 8.3 成交量参与率

```text
max_fill_volume = floor(minute_volume * participation_cap_pct)
```

默认：

```text
participation_cap_pct = 5% ~ 10%
```

不足部分保持 pending 或部分成交后剩余等待下一 bar。

### 8.4 挂单有效期

当前双仓策略推荐：

| 订单类型 | 默认有效期 |
|---|---|
| swing 网格买入 | 当前 bar 或 1-3 分钟 |
| swing 止盈卖出 | 当前 bar 或直到价格失效 |
| core 建仓买入 | 当日有效或下一交易日前重新评估 |
| 风险降低卖出 | 当日有效，但跌停不可假成交 |

过期订单进入 `EXPIRED`，释放冻结，策略可在下一 tick 重新评估。

---

## 9. Tick / 盘口撮合规则

若有盘口数据，按以下顺序撮合：

买入：

```text
可成交价 <= buy_limit_price
优先使用 ask1/ask2... 深度
成交数量不超过盘口可用量和参与率上限
```

卖出：

```text
可成交价 >= sell_limit_price
优先使用 bid1/bid2... 深度
成交数量不超过盘口可用量和参与率上限
```

若盘口显示涨停买入排队但无卖盘，买入不成交。

若盘口显示跌停卖出排队但无买盘，卖出不成交。

---

## 10. T+1 模拟

### 10.1 可卖量推进

交易日结束后：

```text
today_buy_volume -> available_volume
pending/frozen 按未完成订单状态处理
```

非交易日不推进 T+1。

### 10.2 当日买入不可卖

若策略当日买入 swing 后又触发卖出：

1. 后置风控先检查 `swing_available_volume`。
2. 不足时检查同标的可卖老仓。
3. 按置换顺序生成 `T1SubstitutionPlan`。
4. broker 实际卖出老仓。
5. RuntimeStateManager 按成交数量重新归因今日买入。

### 10.3 无老仓时

无可置换老仓时：

```text
OrderRiskDecision = DELAY 或 REJECT
reason_code = T1_UNAVAILABLE
```

策略不得标记 swing 止盈完成。

---

## 11. bucket 归因模拟

### 11.1 买入成交

买入成交后：

```text
bucket.total_volume += filled_volume
bucket.today_buy_volume += filled_volume
bucket.cost_basis_cny += fill_amount + fees
portfolio.cash_available_cny -= fill_amount + fees
```

### 11.2 卖出成交

卖出成交后：

```text
bucket.total_volume -= filled_volume
bucket.available_volume -= filled_volume from sold bucket
bucket.realized_pnl_cny += sell_amount - allocated_cost - fees - taxes
portfolio.cash_available_cny += sell_amount - fees - taxes
```

### 11.3 部分成交

部分成交只更新成交数量。剩余数量保持 pending 或在订单过期/撤单后释放。

### 11.4 拒单/撤单

拒单/撤单不得更新成交归因，不得更新最近成交网格。

---

## 12. 成本模型

### 12.1 成本字段

```text
CostModel
├── commission_pct
├── min_commission_cny
├── stamp_tax_pct_sell
├── transfer_fee_pct
├── slippage_bps
├── safety_margin_bps
```

### 12.2 买入成本

```text
commission = max(fill_amount_cny * commission_pct, min_commission_cny)
transfer_fee = fill_amount_cny * transfer_fee_pct
cash_delta = -(fill_amount_cny + commission + transfer_fee)
```

### 12.3 卖出成本

```text
commission = max(fill_amount_cny * commission_pct, min_commission_cny)
stamp_tax = fill_amount_cny * stamp_tax_pct_sell
transfer_fee = fill_amount_cny * transfer_fee_pct
cash_delta = fill_amount_cny - commission - stamp_tax - transfer_fee
```

### 12.4 网格收益过滤

swing 网格必须满足：

```text
expected_edge_pct
  > 2 * commission_pct
  + stamp_tax_pct_sell
  + 2 * transfer_fee_pct
  + slippage_pct
  + safety_margin_pct
```

否则不应输出或不应执行该网格意图。

---

## 13. 滑点模型

### 13.1 固定滑点

```text
buy_fill_price  = raw_fill_price * (1 + slippage_bps / 10000)
sell_fill_price = raw_fill_price * (1 - slippage_bps / 10000)
```

### 13.2 波动滑点

可选：

```text
slippage_pct = base_slippage_pct + k * ATRPct
```

### 13.3 流动性滑点

可选：

```text
slippage_pct = base + k * order_volume / bar_volume
```

GA 快速评估可用固定滑点；冠军复核建议使用流动性滑点。

---

## 14. 涨跌停特殊规则

### 14.1 涨停买入

默认规则：

- 一字涨停买入不成交。
- 非一字涨停但价格到涨停，若无盘口/成交量证明打开，保守不成交或极低成交率。
- 连续涨停期间不得假设买入成功。

### 14.2 跌停卖出

默认规则：

- 一字跌停卖出不成交。
- 非一字跌停但价格到跌停，若无盘口/成交量证明打开，保守不成交或极低成交率。
- 连续跌停期间不得假设风险已释放。

### 14.3 涨跌停打开

若分钟/tick 数据显示打开：

```text
limit_up_opened = low < upper_limit_price
limit_down_opened = high > lower_limit_price
```

仍需按成交量参与率限制成交。

---

## 15. 停牌与复牌

### 15.1 停牌

停牌期间：

- 不接受新订单。
- 不成交。
- 未完成订单按 broker 状态处理，回测中可设置为过期。
- 净值价格使用上一可交易价或指定估值口径。

### 15.2 复牌

复牌首日：

- 使用当日真实涨跌停规则。
- 可配置更高滑点和更低参与率。
- 默认禁止 aggressive 买入。

---

## 16. 公司行为在回测中的处理

### 16.1 指标序列

指标可以使用时点可得复权序列，保持 EMA/ATR/分位连续。

### 16.2 交易与账本

交易必须使用 raw price。公司行为发生时，broker 需要输出 `CorporateActionApplied` 事件，RuntimeStateManager 更新：

- 持仓数量。
- 可卖数量。
- bucket 数量。
- 成本口径。
- 现金分红。

### 16.3 禁止未来复权

回测窗口内只允许使用窗口时点之前已经生效的调整因子。

---

## 17. Ghost DCA A 股基准

当前进化文档要求策略跑赢 Ghost DCA。A 股版 Ghost DCA 应按 A 股规则实现。

### 17.1 基准行为

```text
1. 初始资金在首个可交易日买入标的。
2. 每自然月首个交易日注入 monthly_inject_cny。
3. 注入资金按 A 股 lot 规则买入。
4. 若涨停、停牌或资金不足一手，则资金留存到后续交易日。
5. 不卖出，除非公司行为导致强制变化。
```

### 17.2 成本

Ghost DCA 也必须计入：

- 买入佣金。
- 最低佣金。
- 过户费。
- 滑点。

它不产生卖出印花税，除非基准被要求在期末清算；默认期末以持仓市值计权益，不卖出。

### 17.3 ROI

ROI 使用 Modified Dietz，剔除月度注资的现金流影响。

---

## 18. 成交约束统计

回测报告必须输出成交约束统计，便于判断策略是否依赖不可成交机会。

```text
BacktestFillStats
├── total_intents
├── total_orders_submitted
├── total_orders_rejected
├── total_orders_delayed
├── total_orders_filled
├── total_orders_partially_filled
├── rejected_by_limit_up_buy
├── rejected_by_limit_down_sell
├── rejected_by_suspended
├── rejected_by_t1
├── rejected_by_cash
├── rejected_by_position
├── substitution_applied_count
├── substitution_rolled_back_count
├── avg_fill_ratio
├── missed_profit_due_to_unfilled?
└── max_pending_order_age_bars
```

GA 适应度可以惩罚：

- 大量涨停买入未成交。
- 大量跌停卖出未成交。
- 过高订单拒绝率。
- 过高部分成交残留。
- 过度依赖 T+1 置换。

---

## 19. 当前双仓策略回测要求

### 19.1 必测场景

当前 `ashare_dynamic_balance_dual_bucket` 必须覆盖：

- 低位横盘：core 慢慢吃饱。
- 单边上涨：core 持有，swing 少量止盈。
- 箱体震荡：swing 贡献波动收益。
- 单边下跌：停止网格补仓，core 降低或保持。
- 高位放量转弱：优先清 swing，再分批降 core。
- 当日 swing 买入后触发卖出：有老仓则置换，无老仓则 T+1 延迟/拒绝。
- 连续涨停：买入不假成交。
- 连续跌停：卖出不假成交。
- 停牌：订单不成交，策略不伪造降仓。
- 部分成交：只更新部分 grid 与 bucket。

### 19.2 grid 状态要求

回测中：

- `TradeIntent` 产生时不得更新 grid filled。
- `OrderAccepted` 只能标记 pending。
- `TradeReport` 才能更新最近成交 grid index。
- 拒单、撤单、过期不得更新成交 grid。

---

## 20. 配置建议

### 20.1 快速 GA 配置

```json
{
  "broker_mode": "EOD_OR_1M_CONSERVATIVE",
  "daily_touch_policy": "FILL_WITH_PENALTY",
  "participation_cap_pct_daily": 0.01,
  "participation_cap_pct_minute": 0.05,
  "slippage_bps": 10,
  "safety_margin_bps": 5,
  "allow_call_auction": false,
  "order_ttl_bars": 3
}
```

### 20.2 冠军复核配置

```json
{
  "broker_mode": "MINUTE_OR_TICK_STRICT",
  "daily_touch_policy": "NEVER_FILL_ON_TOUCH",
  "participation_cap_pct_daily": 0.005,
  "participation_cap_pct_minute": 0.03,
  "slippage_model": "LIQUIDITY_ATR",
  "allow_call_auction": false,
  "order_ttl_bars": 1
}
```

---

## 21. 开发验收清单

### 21.1 基础撮合

- [ ] 非交易时段不成交。
- [ ] 停牌不成交。
- [ ] 买入必须 100 股整数倍。
- [ ] 普通卖出必须 100 股整数倍。
- [ ] 清仓允许零股。
- [ ] 成交量不足时部分成交或不成交。

### 21.2 涨跌停

- [ ] 一字涨停买入不成交。
- [ ] 一字跌停卖出不成交。
- [ ] 涨跌停打开后仍受成交量限制。
- [ ] 连续涨停/跌停不产生假成交。

### 21.3 T+1 与 bucket

- [ ] 今日买入不可卖。
- [ ] 有老仓时可生成置换计划。
- [ ] 无老仓时延迟或拒绝。
- [ ] 部分成交只部分置换。
- [ ] 拒单/撤单回滚未成交置换。

### 21.4 成本与净值

- [ ] 买入计佣金和过户费。
- [ ] 卖出计佣金、印花税和过户费。
- [ ] 最低佣金生效。
- [ ] 滑点按方向不利计算。
- [ ] Modified Dietz ROI 剔除注资影响。

### 21.5 同构性

- [ ] 回测 broker 输出与 miniQMT 路径同构的 OrderReport/TradeReport。
- [ ] RuntimeStateManager 同时可消费实盘和回测事件。
- [ ] 策略 Step 内无 `isBacktest` 分支。
- [ ] 相同数据和参数回测两次结果完全一致。
