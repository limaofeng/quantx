# A 股数据源与公司行为契约

**文档目标：** 定义 A 股个人量化系统所需的数据类型、数据源适配、更新频率、数据质量降级、时点可得规则、复权口径、公司行为处理与证券状态处理。本文服务当前 `A股单标的动态天平双仓策略`，同时作为后续 A 股策略的数据域通用契约。

---

## 0. 核心定位

环境层、风控层、仓位调节层和回测 broker 的正确性，取决于数据是否完整、时点是否正确、交易约束是否真实。本文回答：

- 每类 A 股数据从哪个适配器进入系统。
- 每类数据何时更新、如何落库、如何判定过期。
- 缺失数据时系统如何保守降级。
- 指标计算和交易撮合分别使用什么价格口径。
- 分红、送股、转增、配股、除权除息、停牌、ST、退市风险如何影响账本和策略。

本文不指定唯一外部数据供应商。实现时应通过 `DataProviderAdapter` 屏蔽供应商差异。

---

## 1. 数据源分层

### 1.1 数据真源类型

```text
DataSourceType =
    MARKET_DATA_PROVIDER     // 行情与指数数据供应商
  | LOCAL_MARKET_DATABASE    // 本地已同步行情库
  | MINIQMT_QUOTE            // miniQMT 本地行情/证券状态
  | MINIQMT_ACCOUNT          // miniQMT 账户、持仓、委托、成交
  | MANUAL_CONFIG            // 用户或管理员配置
  | BACKTEST_FIXTURE         // 回测固定数据集
```

### 1.2 适配器原则

所有外部数据必须经过适配器进入统一结构：

```text
External Provider
    -> DataProviderAdapter
    -> NormalizedDataModel
    -> Postgres / Local Cache
    -> MarketDataSnapshot
```

业务模块不得直接依赖某个供应商字段名。

### 1.3 数据版本

每次生成决策快照时，应记录数据版本：

```text
source_versions = {
  "instrument_daily_bar": "provider:daily:600000.SH:2026-05-09",
  "sector_index": "provider:sw2:bank:2026-05-09",
  "limit_price": "provider:limit:600000.SH:2026-05-09",
  "security_status": "provider:status:600000.SH:2026-05-09"
}
```

用于回测复现与实盘追责。

---

## 2. 数据类型映射表

| 数据类型 | 用途 | 推荐真源 | 更新频率 | 实盘缺失处理 | 回测要求 |
|---|---|---|---|---|---|
| 个股日线 | 趋势、EMA、ATR、价格分位、成交量分布 | 行情供应商 / 本地行情库 | 每交易日收盘后 | 日线策略暂停或沿用上一日并标记 STALE | 必须时点可得 |
| 个股分钟线 | 盘中 grid、成交触达近似 | 行情供应商 / miniQMT quote | 盘中 1m | 禁用盘中网格 | 不得用未来分钟 |
| 个股 tick / 盘口 | 实盘可成交性、精细撮合 | miniQMT quote / 供应商 | 实时 | 降级为分钟或限价保守 | 可选 |
| 大盘指数 | 环境层市场状态 | 行情供应商 | 日线/分钟 | 环境层 INSUFFICIENT，禁 aggressive | 必须 |
| 行业指数 | 行业强弱和破位 | 申万/中信/供应商映射 | 日线 | 降级使用大盘，禁 aggressive | 推荐必须 |
| 概念指数 | 概念热度和过热 | 供应商映射 | 日线 | 概念中性 | 可选 |
| 市场宽度 | 涨跌家数、涨跌停家数 | 供应商/本地统计 | 盘中/收盘 | DEGRADED | 推荐必须 |
| 交易日历 | 交易日、节假日、时段 | 交易所/供应商/本地表 | 日更 | 禁止自动下单 | 必须 |
| 涨跌停价 | 后置风控、回测撮合 | 供应商/miniQMT | 每交易日/盘中 | 禁止真实下单 | 必须 |
| 停牌状态 | 风控和撮合 | 供应商/miniQMT | 日更/盘中 | 禁止真实下单 | 必须 |
| ST/退市风险 | 强保护 | 证券主数据 | 日更 | 禁止新增买入或人工确认 | 必须 |
| 公司行为 | 复权、账本调整 | 供应商/公告数据 | 事件驱动/日更 | 进入人工确认或暂停 | 必须 |
| 账户资金 | 实盘真实现金 | miniQMT account | 启动/盘中/回报后 | 禁止新增买入 | 回测 broker 模拟 |
| 账户持仓 | 实盘真实持仓和可卖量 | miniQMT account | 启动/盘中/回报后 | 禁止卖出或对账 | 回测 broker 模拟 |
| 委托回报 | 订单状态 | miniQMT order report | 事件/轮询 | 查询补偿，超时对账 | 回测 broker 模拟 |
| 成交回报 | 成交真源 | miniQMT trade report | 事件/轮询 | 不更新成交，等待或对账 | 回测 broker 模拟 |

---

## 3. 标准数据结构

### 3.1 InstrumentMaster

```text
InstrumentMaster
├── instrument_code          // 600000.SH
├── exchange                 // SSE / SZSE / BSE
├── name
├── listing_date
├── delisting_date?
├── board                    // MAIN / STAR / CHINEXT / BSE
├── lot_size                 // 默认 100
├── price_tick
├── default_limit_pct
├── industry_code
├── industry_name
├── sector_index_code
├── concept_codes[]
├── is_marginable
├── status                   // ACTIVE / SUSPENDED / ST / DELISTING / DELISTED
└── updated_at_ms
```

### 3.2 MarketBar

```text
MarketBar
├── instrument_code
├── interval                 // 1d / 1m / tick
├── open_time_ms
├── close_time_ms
├── open_price_raw
├── high_price_raw
├── low_price_raw
├── close_price_raw
├── volume
├── amount_cny
├── turnover_pct?
├── adj_factor?              // 仅用于指标复权
├── data_quality
└── source_version
```

### 3.3 LimitPriceState

```text
LimitPriceState
├── instrument_code
├── trade_date
├── upper_limit_price
├── lower_limit_price
├── limit_pct
├── is_one_word_limit_up?
├── is_one_word_limit_down?
├── source_version
└── data_quality
```

### 3.4 SecurityStatusSnapshot

```text
SecurityStatusSnapshot
├── instrument_code
├── trade_date
├── is_suspended
├── suspension_reason?
├── is_st
├── is_star_st
├── is_delisting_risk
├── is_delisting_period
├── resumption_date?
├── status_tags[]
└── data_quality
```

### 3.5 MarketBreadthSnapshot

```text
MarketBreadthSnapshot
├── trade_date
├── snapshot_time_ms
├── rising_count
├── falling_count
├── flat_count
├── limit_up_count
├── limit_down_count
├── broken_limit_up_count?
├── consecutive_limit_up_count?
├── total_turnover_cny
├── data_quality
└── source_version
```

---

## 4. 时点可得规则

### 4.1 基本规则

任何决策只能使用决策时点已经可得的数据。

禁止：

- 用收盘后数据指导当天盘中交易。
- 用未来复权因子计算过去指标。
- 用未来行业成分倒推当前行业指数。
- 用未来停牌信息解释当前风控。
- 用日线 high/low 同时判断先买后卖，除非有分钟/tick 序列证明先后顺序。

### 4.2 日线策略时点

日线收盘动作：

```text
T 日收盘后生成的趋势、环境、动态基准
    最早只能影响 T+1 交易日的自动委托
```

若系统配置允许尾盘策略，必须使用尾盘时点已经形成的数据，并在回测中用同样时点切片，不能用完整收盘后确认数据。

### 4.3 盘中策略时点

盘中 1m 决策：

```text
当前 1m bar 未完成时，不得使用该 bar 的 close 作为确定事实。
最新可用数据应为上一根已完成 1m bar，或 tick/盘口实时数据。
```

### 4.4 公司行为时点

公司行为在回测中必须以公告/生效时点处理：

- 指标复权只能使用当时已经生效的复权因子。
- 除权除息日之前不得提前调整持仓数量或成本。
- 送转股到账日或除权日的处理以数据源可验证规则为准，必须在回测日志中标注。

---

## 5. 价格口径

### 5.1 指标价格

用于 EMA、ATR、价格分位、成交量分布等指标的价格，可以使用时点可得的复权序列。

推荐字段：

```text
indicator_close_price = close_price_raw * point_in_time_adj_factor
```

### 5.2 交易价格

所有下单、撮合、涨跌停判断、成交回报和账本成本必须使用未复权真实价格：

```text
execution_price = raw market price
```

### 5.3 禁止混用

禁止用复权价格：

- 计算涨跌停。
- 生成真实委托价格。
- 更新真实成交成本。
- 判断券商持仓盈亏。

---

## 6. 数据质量状态机

### 6.1 单项数据质量

```text
OK
  -> STALE              // 数据过期
  -> DEGRADED           // 弱字段缺失
  -> INSUFFICIENT       // 关键字段缺失
```

### 6.2 快照聚合质量

`MarketDataSnapshot.data_quality` 聚合规则：

```text
若交易日历、停牌状态、涨跌停价、个股行情任一关键数据缺失：INSUFFICIENT
若大盘指数缺失：INSUFFICIENT
若行业指数缺失：DEGRADED
若概念指数缺失：OK 或 DEGRADED，取决于策略是否声明必需
若市场宽度缺失：DEGRADED
若分钟/tick 缺失：日线 OK，盘中网格 DEGRADED 或 INSUFFICIENT
```

### 6.3 降级动作

| 聚合质量 | 环境层 | 风控层 | 仓位调节层 | 策略 |
|---|---|---|---|---|
| `OK` | 正常 | 正常 | 正常 | 正常 |
| `DEGRADED` | 标记风险，弱信号中性 | 限制激进买入 | 不允许 aggressive | 可以低频运行 |
| `INSUFFICIENT` | 不给高风险暴露建议 | 禁止新增买入或拒单 | defensive/cautious | 只输出空或风险降低意图 |
| `STALE` | 沿用需标记 | 超时后禁买 | 保守 | 禁用盘中网格 |

---

## 7. 当前双仓策略数据需求

### 7.1 必需数据

当前 `ashare_dynamic_balance_dual_bucket` 至少需要：

| 数据 | 用途 | 缺失后果 |
|---|---|---|
| 个股日线 close/high/low/volume/amount | EMA、ATR、分位、成交量结构 | 不能运行日线状态机 |
| 交易日历 | T+1、交易时段、回测切片 | 不能自动下单 |
| 涨跌停价 | 后置风控、回测撮合 | 不能真实下单 |
| 停牌状态 | 后置风控、回测撮合 | 不能真实下单 |
| 账户现金和持仓 | 组合状态 | 不能新增买入/卖出 |
| miniQMT 委托与成交 | 实盘状态真源 | 不能更新成交状态 |
| 大盘指数 | 环境层 | 只能保守运行或暂停 |

### 7.2 推荐数据

| 数据 | 用途 | 缺失后果 |
|---|---|---|
| 行业指数 | 行业强弱、破位 | 禁止 aggressive accumulation |
| 市场宽度 | PANIC/RISK_OFF 判断 | 环境降级 |
| 分钟线 | swing 网格 | 禁用盘中网格 |
| tick/盘口 | 精细可成交判断 | 使用保守分钟撮合 |
| 概念指数 | 主题热度/过热 | 概念中性 |

---

## 8. 公司行为处理

### 8.1 公司行为类型

```text
CorporateActionType =
    CASH_DIVIDEND        // 现金分红
  | BONUS_SHARE          // 送股
  | CONVERSION_SHARE     // 转增股
  | RIGHTS_ISSUE         // 配股
  | EX_RIGHT_DIVIDEND    // 除权除息
  | STOCK_SPLIT          // 拆细，A 股少见但保留
  | REVERSE_SPLIT        // 合股，保留
  | CODE_CHANGE          // 代码变更
  | MERGER_RESTRUCTURE   // 重组换股
```

### 8.2 CorporateActionEvent

```text
CorporateActionEvent
├── action_id
├── instrument_code
├── action_type
├── announcement_date
├── record_date
├── ex_date
├── payment_date?
├── share_delivery_date?
├── cash_dividend_per_share?
├── bonus_share_ratio?
├── conversion_share_ratio?
├── rights_issue_price?
├── rights_issue_ratio?
├── raw_payload
├── source_version
└── data_quality
```

### 8.3 处理原则

| 事件 | 价格指标 | 真实账本 | bucket 账本 |
|---|---|---|---|
| 现金分红 | 复权因子调整 | 现金增加或应收记录 | 不改变股数，成本可按规则调整 |
| 送股 | 复权因子调整 | 股数增加，价格除权 | 各 bucket 按比例增加股数 |
| 转增 | 同送股 | 同送股 | 同送股 |
| 配股 | 指标按时点复权 | 默认不自动参与，需人工配置 | 不自动增加，除非实际参与 |
| 除权除息 | 指标价格连续化 | 使用 raw price 交易 | 成本和数量按事件调整 |
| 代码变更 | 新旧代码映射 | 持仓迁移 | bucket 迁移 |
| 重组换股 | 需人工确认 | 进入暂停/对账 | 进入人工处理 |

### 8.4 bucket 数量调整

送股/转增后，各 bucket 按比例调整：

```text
new_bucket_total_volume = old_bucket_total_volume * (1 + share_ratio)
new_bucket_available_volume = old_bucket_available_volume * (1 + share_ratio)
new_bucket_today_buy_volume = old_bucket_today_buy_volume * (1 + share_ratio)
```

实际实现时必须处理整数股和零股：

- 总量以 broker 快照为最终真源。
- bucket 之间的尾差按照最大 bucket 或 core 优先吸收。
- 调整必须写 `CORPORATE_ACTION_APPLIED` 审计事件。

### 8.5 成本调整

成本调整建议使用可复现规则：

```text
现金分红后：
    avg_cost_price_new = max(0, avg_cost_price_old - cash_dividend_per_share)

送转股后：
    avg_cost_price_new = total_cost_old / new_total_volume

配股参与后：
    total_cost_new = total_cost_old + rights_issue_price * rights_issue_volume
    avg_cost_price_new = total_cost_new / total_volume_new
```

如 broker 成本口径与系统口径不同，报告中应同时展示 broker 成本和系统成本，策略逻辑不应依赖无法复现的 broker 成本。

### 8.6 配股默认规则

配股涉及资金缴款和人工选择，默认规则：

- 策略不自动参与配股。
- 发现配股事件时生成提醒和审计。
- 若用户人工确认参与，作为外部现金流和公司行为处理。
- 未确认时不得由策略自动冻结资金。

---

## 9. 证券状态处理

### 9.1 停牌

停牌状态：

- 禁止所有买入和卖出委托。
- 未完成订单应查询 broker 状态并取消或等待。
- 策略不得把无法卖出当作已降低风险。
- 回测中停牌期间价格不可成交，净值可用上一可交易价格或停牌价格口径，需标注。

### 9.2 长期停牌

长期停牌触发：

- 环境层标记 `security_suspended_long`。
- 风控层限制新增买入。
- 实例可以进入 `KILL_SWITCHED` 或 `PAUSED_BY_SECURITY_STATUS`。
- 需要人工决定是否继续持有、暂停策略或迁移实例。

### 9.3 复牌首日

复牌首日可能出现异常波动。默认规则：

- 禁止 aggressive profile。
- swing 买入默认关闭。
- 卖出是否允许取决于涨跌停和流动性。
- 回测 broker 应使用复牌当日真实涨跌停和成交量约束。

### 9.4 ST / *ST

当证券新增 ST / *ST 标签：

- 禁止新增买入。
- 仓位调节进入 `DEFENSIVE` 或 `DISTRIBUTION`。
- 是否卖出取决于实例配置和是否可成交。
- 触发审计事件 `SECURITY_STATUS_CHANGED`。
- 可选触发 KILL_SWITCH，等待人工确认。

### 9.5 退市风险 / 退市整理期

退市风险处理：

- 禁止新增买入。
- 默认触发 KILL_SWITCH 或人工确认。
- 风险降低卖出需要考虑跌停不可成交。
- 策略不得持续网格补仓。

### 9.6 股票代码变更

代码变更时：

- 建立 `old_code -> new_code` 映射。
- PortfolioState 和 BucketLedger 迁移到新代码。
- 历史行情查询保留旧代码映射。
- 决策 trace 中记录迁移事件。

---

## 10. 数据落库建议

建议至少维护以下表或等价结构：

```text
instrument_master
market_bars
index_bars
sector_mapping
concept_mapping
market_breadth_snapshots
trading_calendar
limit_price_states
security_status_snapshots
corporate_action_events
adjustment_factors_point_in_time
account_snapshots
position_snapshots
order_reports
trade_reports
```

关键索引：

```text
(instrument_code, interval, open_time_ms) unique
(index_code, interval, open_time_ms) unique
(instrument_code, trade_date) for limit/security/corporate action
(trade_date, snapshot_time_ms) for breadth
```

---

## 11. 回测数据集要求

每个回测窗口必须包含：

```text
BacktestDataset
├── instrument_bars_raw
├── instrument_bars_adjusted_for_indicator
├── market_index_bars
├── sector_index_bars
├── concept_index_bars?
├── market_breadth
├── trading_calendar
├── limit_price_states
├── security_status_snapshots
├── corporate_actions
└── point_in_time_adjustment_factors
```

若数据不足，应在回测报告中输出：

```text
data_warnings[]
missing_required_fields[]
missing_optional_fields[]
span_short_flags[]
```

GA 适应度应惩罚或剔除数据质量不足的评估结果，避免优化到数据漏洞。

---

## 12. 开发验收清单

### 12.1 数据完整性

- [ ] 缺涨跌停价时实盘拒绝下单。
- [ ] 缺停牌状态时实盘拒绝下单。
- [ ] 缺大盘指数时环境层不输出高风险暴露建议。
- [ ] 缺行业指数时不允许进入 aggressive profile。
- [ ] 缺分钟线时盘中网格禁用。

### 12.2 时点可得

- [ ] T 日收盘数据不会影响 T 日盘中决策。
- [ ] 回测不使用未来复权因子。
- [ ] 日线 high/low 不被用于推断盘中先后顺序。
- [ ] 公司行为只在生效日之后影响账本。

### 12.3 公司行为

- [ ] 现金分红调整现金或成本并写审计。
- [ ] 送转股按 bucket 比例调整数量。
- [ ] 配股默认不自动参与。
- [ ] ST/退市风险禁止新增买入。
- [ ] 代码变更迁移账本并保留历史映射。

