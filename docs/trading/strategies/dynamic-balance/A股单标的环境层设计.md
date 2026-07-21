**文档目标:** 定义 A 股单标的策略的环境层。环境层读取大盘、行业、概念、成交量与市场宽度数据，产出确定性的 `MarketContextSnapshot`，用于影响风险偏置、仓位边界与动态天平输入。环境层不产生买卖信号，不下单，不修改持仓。

---

## 0. 核心定位

环境层回答一个问题：**这只股票现在所处的市场环境是否支持冒险？**

单标的策略不能只看个股。A 股个股受到系统性风险、行业轮动、资金风格、涨跌停扩散、成交额变化影响很大。环境层的职责是把这些外部条件压缩成可解释、可回测、可复现的快照。

环境层只做三件事：

- 判断大盘、行业、概念、成交量、市场宽度状态
- 给出环境评分和风险标签
- 为风控层与仓位调节层提供输入

环境层严禁：

- 直接输出 BUY / SELL
- 直接修改 core / swing 仓位
- 直接修改订单状态
- 调用 miniQMT 或任何交易接口
- 使用 AI 主观判断替代确定性规则

---

## 1. 输入数据

### 1.1 大盘指数

v1 至少需要以下指数中的一组或多组：

| 指数 | 用途 |
|---|---|
| 上证指数 | 主板环境与全市场风险参考 |
| 沪深300 | 大盘蓝筹环境 |
| 中证500 | 中盘股环境 |
| 中证1000 | 小盘股环境 |
| 创业板指 | 成长风格环境 |
| 全A指数 | 全市场综合环境，若可用优先使用 |

如果标的是沪深主板大盘股，沪深300权重更高；如果标的是成长股，中证1000和创业板指权重更高。

### 1.2 行业指数

每个股票实例必须绑定一个主行业指数。

优先级：

1. 申万一级或二级行业
2. 中信行业
3. 券商或数据源提供的等价行业指数
4. 无行业指数时，使用全市场指数降级

行业指数比概念指数更重要。行业破位时，个股低位信号要降权。

### 1.3 概念指数

概念指数是可选弱信号。

概念热度只能影响：

- 低位信号置信度的小幅加减
- swing 活跃度
- 高位过热风险

概念热度不能直接触发买入，也不能覆盖大盘和行业的防御判断。

### 1.4 成交量与市场宽度

环境层必须关注成交量结构，而不是只看涨跌。

需要的数据：

- 个股成交量、成交额、换手率
- 行业指数成交量或成交额
- 全市场成交额
- 上涨家数、下跌家数
- 涨停家数、跌停家数
- 连板/炸板数据，若可用

若缺少涨跌家数或涨跌停家数，环境层可以降级运行，但必须标注 `data_quality = DEGRADED`。

---

## 2. 输出结构

环境层输出 `MarketContextSnapshot`。

```json
{
  "instrument_code": "600000.SH",
  "trade_date": "2026-05-09",
  "market_state": "RISK_OFF",
  "sector_state": "WEAK",
  "concept_heat_state": "NEUTRAL",
  "liquidity_state": "SHRINKING",
  "breadth_state": "NEGATIVE",
  "volume_structure": "DISTRIBUTION",
  "context_score": -0.42,
  "risk_tags": ["market_selloff", "sector_breakdown"],
  "data_quality": "OK"
}
```

字段说明：

| 字段 | 取值 | 说明 |
|---|---|---|
| `market_state` | `RISK_ON` / `NEUTRAL` / `RISK_OFF` / `PANIC` | 大盘环境 |
| `sector_state` | `STRONG` / `NEUTRAL` / `WEAK` / `BROKEN` | 行业环境 |
| `concept_heat_state` | `HOT` / `NEUTRAL` / `COLD` / `OVERHEATED` | 概念热度 |
| `liquidity_state` | `EXPANDING` / `NORMAL` / `SHRINKING` / `DRY` | 流动性环境 |
| `breadth_state` | `POSITIVE` / `NEUTRAL` / `NEGATIVE` / `EXTREME_NEGATIVE` | 市场宽度 |
| `volume_structure` | `ACCUMULATION` / `NORMAL` / `DISTRIBUTION` / `BREAKDOWN` | 量价结构 |
| `context_score` | [-1, 1] | 环境总评分，正值支持风险暴露，负值要求防御 |
| `risk_tags` | string[] | 结构化风险原因 |
| `data_quality` | `OK` / `DEGRADED` / `INSUFFICIENT` | 数据质量 |

---

## 3. 大盘环境判断

### 3.1 趋势结构

使用无量纲指标判断：

- 收盘价相对 EMA20 / EMA60 / EMA120 的位置
- EMA20、EMA60、EMA120 的排列
- 最近 N 日收益率分位
- ATRPct 或波动率分位

状态规则：

| 状态 | 条件示例 | 策略含义 |
|---|---|---|
| `RISK_ON` | 指数在 EMA60 上方，EMA20 向上，市场宽度为正 | 允许正常低吸与 swing |
| `NEUTRAL` | 指数震荡，无明显方向 | 保持默认天平参数 |
| `RISK_OFF` | 指数跌破 EMA60，成交额放大或宽度转负 | 降低仓位上限 |
| `PANIC` | 放量急跌、跌停扩散、ATR 放大 | 禁止 swing 补仓，进入防御 |

### 3.2 大盘放量杀跌

以下情况标记 `market_selloff`：

- 指数单日跌幅超过近期波动阈值
- 成交额高于 20 日均值明显放大
- 下跌家数显著多于上涨家数
- 跌停家数扩散

影响：

- 降低个股低吸评分
- 降低 `max_position_pct`
- 降低 `balance_beta`
- 提高现金缓冲

---

## 4. 行业环境判断

行业层回答：**个股所属行业是否支持这只股票的交易逻辑？**

### 4.1 行业相对强弱

计算行业相对大盘强弱：

```text
sector_relative_strength = sector_return_Nd - market_return_Nd
```

推荐窗口：

- 5 日：短期资金偏好
- 20 日：月度行业趋势
- 60 日：中期行业位置

状态规则：

| 状态 | 条件示例 | 行为 |
|---|---|---|
| `STRONG` | 行业强于大盘，价格在 EMA60 上方 | 个股回踩可提高置信度 |
| `NEUTRAL` | 行业与大盘接近 | 不额外调整 |
| `WEAK` | 行业弱于大盘，趋势转弱 | 降低 swing 活跃度 |
| `BROKEN` | 行业跌破中长期支撑并放量 | 禁止高置信低吸 |

### 4.2 行业破位

行业破位时，即使个股处于技术低位，也不能直接视为便宜。

触发条件：

- 行业指数跌破 EMA120
- 跌破近期成交量密集区
- 行业放量下跌
- 行业内多数成分股走弱

输出标签：

- `sector_breakdown`
- `sector_liquidity_exit`
- `sector_underperforming`

---

## 5. 概念热度判断

概念层是弱信号。

### 5.1 可用场景

概念热度可以用于：

- 判断短期资金是否关注该股票
- 调节 swing 活跃度
- 识别高位过热

### 5.2 禁止场景

概念热度不得用于：

- 单独触发买入
- 覆盖大盘 `PANIC`
- 覆盖行业 `BROKEN`
- 让策略追涨停板

### 5.3 过热风险

若概念指数短期快速上涨、成交额放大、涨停扩散但标的高位滞涨，则输出：

- `concept_overheated`
- `theme_distribution_risk`

仓位调节层应降低新增买入，风控层可限制追高订单。

---

## 6. 成交量结构

成交量必须结合价格位置解释。

| 结构 | 条件示例 | 含义 |
|---|---|---|
| `ACCUMULATION` | 低位放量止跌，价格不再创新低 | 可能有承接 |
| `NORMAL` | 成交量接近均值，价格结构平稳 | 无特殊偏置 |
| `DISTRIBUTION` | 高位放量滞涨或长上影 | 可能出货 |
| `BREAKDOWN` | 放量跌破支撑或成交密集区 | 筹码松动 |

环境层只标记结构，不决定买卖。低位放量承接可提高低吸可信度；放量破位必须降低低吸可信度。

---

## 7. 评分模型

环境总评分：

```text
context_score =
    w_market  * market_score
  + w_sector  * sector_score
  + w_concept * concept_score
  + w_liq     * liquidity_score
  + w_breadth * breadth_score
  + w_volume  * volume_structure_score
```

默认权重：

| 权重 | 默认值 | 说明 |
|---|---:|---|
| `w_market` | 0.30 | 大盘环境 |
| `w_sector` | 0.25 | 行业环境 |
| `w_concept` | 0.05 | 概念热度 |
| `w_liq` | 0.15 | 流动性 |
| `w_breadth` | 0.15 | 市场宽度 |
| `w_volume` | 0.10 | 成交量结构 |

输出范围 clamp 到 [-1, 1]。

解释：

- `context_score >= 0.35`：环境支持风险暴露
- `-0.35 < context_score < 0.35`：环境中性
- `context_score <= -0.35`：环境要求降低风险
- `context_score <= -0.65`：系统性风险，应进入防御 profile

---

## 8. 数据缺失与降级

降级规则：

| 缺失数据 | 降级行为 |
|---|---|
| 缺行业指数 | 使用大盘 + 个股成交量 |
| 缺概念指数 | `concept_heat_state = NEUTRAL` |
| 缺涨跌家数 | 市场宽度降级，使用指数和成交额 |
| 缺成交量 | `data_quality = INSUFFICIENT`，禁用成交量结构评分 |
| 缺大盘指数 | 环境层不可用，风控层进入保守模式 |

缺少行业或概念数据不应阻塞策略运行；缺少大盘数据时，不能给出高风险暴露建议。

---

## 9. 与其他层的契约

### 9.1 给风控层

风控层使用：

- `market_state`
- `sector_state`
- `liquidity_state`
- `breadth_state`
- `risk_tags`
- `data_quality`

用于判断是否拒绝、延迟或限额。

### 9.2 给仓位调节层

仓位调节层使用：

- `context_score`
- `market_state`
- `sector_state`
- `volume_structure`
- `concept_heat_state`

用于调节动态天平参数和仓位边界。

### 9.3 给策略层

策略层只读取环境快照，不直接读取环境层内部指标。策略可将环境标签写入 `TradeIntent.metadata` 或 `StrategyOutput.decision_tags`，方便回测归因和实盘审计。

---


## 10. 数据源映射与复现要求

环境层本身只做确定性评分，但评分能否落地取决于数据源质量。所有输入数据必须在决策时点可得，并通过 `source_fingerprint` 或等价字段进入审计快照。

最低要求：

- 大盘指数、行业指数、个股行情必须记录数据来源和更新时间。
- 概念指数、涨跌家数、涨跌停家数缺失时只能降级，不能让环境更激进。
- 回测中不得使用未来行业分类、未来停牌、未来公司行为或未来复权因子。
- 实盘缺少停牌状态、涨跌停价或交易日历时，环境层可输出快照，但后置订单风控必须拒绝自动下单。

完整数据源映射、交易日历、证券状态、复权和公司行为处理见：

[A 股数据源与公司行为契约](../../contracts/A股数据源与公司行为契约.md)

---

## 11. 测试计划

- 大盘放量下跌时输出 `RISK_OFF` 或 `PANIC`
- 行业强于大盘时输出 `STRONG`
- 行业破位时输出 `BROKEN`
- 个股低位但行业破位时，环境评分不得为强正
- 概念过热时只输出风险标签，不触发买入
- 缺行业数据时降级运行
- 缺大盘数据时环境层不可给出高风险暴露建议
- 低位放量止跌识别为 `ACCUMULATION`
- 高位放量滞涨识别为 `DISTRIBUTION`
- 放量跌破支撑识别为 `BREAKDOWN`


---

## 12. 开发落地补齐

环境层的具体数据源、公司行为和数据质量处理，统一以[A 股数据源与公司行为契约](../../contracts/A股数据源与公司行为契约.md)为准。本文只定义环境判断规则，开发实现时必须额外满足以下要求。

### 12.1 数据源映射

| 环境字段 | 必要数据 | 缺失处理 |
|---|---|---|
| `market_state` | 大盘指数、全市场成交额、市场宽度 | 缺大盘指数时 `data_quality=INSUFFICIENT` |
| `sector_state` | 行业指数、行业映射、行业相对强弱 | 缺行业时禁止进入 `STRONG` |
| `concept_heat_state` | 概念指数或题材热度 | 缺失视为 `NEUTRAL` |
| `liquidity_state` | 个股成交额、全市场成交额、可选盘口 | 缺成交量时禁用流动性加分 |
| `breadth_state` | 上涨/下跌家数、涨跌停统计 | 缺失时 `data_quality=DEGRADED` |
| `volume_structure` | 个股量价结构、成交密集区 | 缺成交量时 `NORMAL` 或禁用评分 |

### 12.2 输出稳定性

环境状态不应因单个 tick 抖动频繁切换。建议：

- 日线环境只在完整日线确认后更新。
- 盘中环境只允许新增风险标签，不允许盘中从 `PANIC` 直接切回 `RISK_ON`。
- `PANIC -> RISK_OFF -> NEUTRAL -> RISK_ON` 至少需要连续确认窗口。
- 环境层应输出 `previous_state` 和 `state_changed_reason`，方便审计。

### 12.3 当前双仓策略的最低要求

`ashare_dynamic_balance_dual_bucket` 至少需要：

- 大盘环境用于决定是否禁止 swing 补仓。
- 行业状态用于降低低位承接评分。
- 流动性状态用于扩大网格间距或拒绝大额订单。
- 市场宽度用于识别系统性恐慌。
- 成交量结构用于区分低位承接与高位出货。

缺少行业或概念可以运行；缺少大盘、停牌状态或涨跌停价不得真实下单。

---

## 13. 与通用 A 股交易域契约的关系

本文只定义当前单标的策略所需的环境判断规则。工程落地时，环境层的输入数据、数据质量、时点可得、公司行为和证券状态处理，以以下通用文档为准：

- [A 股三层协作与执行契约](../../contracts/A股三层协作与执行契约.md)
- [A 股数据源与公司行为契约](../../contracts/A股数据源与公司行为契约.md)
- [A 股交易域数据结构与状态机](../../contracts/A股交易域数据结构与状态机.md)

环境层输出的 `MarketContextSnapshot` 必须进入统一 `DecisionTrace`。若关键数据缺失，环境层只能向保守方向降级，不能因为数据缺失而输出更激进的环境结论。

---

## 14. 当前工程落地状态

已落地到代码主路径：

- `MarketContextSnapshot`：环境层输出快照，字段覆盖 `market_state`、`sector_state`、`concept_heat_state`、`liquidity_state`、`breadth_state`、`volume_structure`、`context_score`、`risk_tags`、`data_quality`、`source_fingerprint`。
- `EnvironmentLayer`：读取 `environment_context`、`market_context`、行情事件字段和当前 `MarketDataSnapshot`，用确定性规则生成环境快照。
- `StrategyExecutor._build_market_context()`：已改为调用 `EnvironmentLayer`，并把快照写入 `StrategyInput.market_context`。
- `ContextRiskLayer` 与 `PositionAdjustmentLayer`：继续消费环境快照；`PANIC`、`RISK_OFF`、`BROKEN`、`DRY`、`INSUFFICIENT` 等状态会传导到风控 caps 和仓位 profile。
- 审计字段：环境快照包含 `source_fingerprint`、`previous_state`、`state_changed_reason` 和输入字段列表，便于回测复现。

当前支持的注入方式：

- `parameters.environment_context`
- `parameters.market_context`
- 行情事件上的同名字段，例如 `market_return_1d`、`sector_return_20d`、`advancing_count`、`limit_down_count`、`volume_ratio`
- 当前标的 `MarketDataSnapshot` 的价格、成交量、成交额、停牌/交易状态

仍由后续数据层补齐：

- 自动获取大盘指数、行业指数、概念指数和市场宽度数据。
- 行业映射、概念映射和数据源版本指纹。
- 盘中状态稳定窗口的持久化确认。当前已实现 `PANIC -> RISK_ON` 的快速恢复保护，但尚未持久化多窗口确认。
