**文档目标:** 定义一个只关注单只 A 股股票的 Sigmoid 动态天平双仓策略。策略借鉴系统三文档中的纯策略边界、仓位三态、动态天平、GA 黑盒寻优思想，将 A 股核心仓与波动仓统一到连续目标仓位函数中，在真实 A 股交易约束下输出交易意图，不直接维护真实现金、真实持仓或可卖量。

---

## 0. 核心定位

本策略命名为：**A 股单标的动态天平双仓策略**。

策略 ID 建议为：`ashare_dynamic_balance_dual_bucket`。

它是一个**单标的、双仓位、动态基准、Sigmoid 目标仓位、趋势约束的波动收益策略**。

它不追求全市场选股，而是假设用户已经选定一只长期愿意跟踪的股票。策略的价值在于：

- 初期建仓时保护核心仓，不让普通网格把底仓过早卖掉
- 低位时通过动态天平逐步提高目标仓位，不一次性满仓
- 趋势向上时保留核心仓，不被短期波动轻易洗出
- 围绕动态基准用短期仓做网格收益
- 高位转弱时停止买入，并分批安全出货
- 所有真实交易约束交由执行层统一处理

策略内部只维护**算法状态**，例如趋势状态、动态基准、动态天平输入信号、网格层级、核心仓目标、短期仓目标、建仓阶段、挂单引用等。真实账户状态必须来自运行时组合状态层。

**输入：** 单只股票行情数据 + 当前组合状态快照 + 策略参数

**输出：** `TradeIntent` 与 `RuntimeStatePatch`，包含方向、标的、目标仓位、目标金额、逻辑仓位 bucket、阶段标签、风险原因和算法状态变更

**不输出：** 已经修正过的真实可买数量、真实可卖数量、真实现金余额

### 0.1 与系统三文档的继承关系

本策略继承三份系统文档中的抽象原则，但将交易语义重写为 A 股单票场景。

| 来源 | 继承内容 | A 股化改造 |
|---|---|---|
| [系统架构设计](../../../architecture/系统架构设计.md) | 纯策略、状态快照、回测实盘同构、端侧只执行 | 策略只输出意图，A 股规则由执行层校验 |
| [系统架构设计](../../../architecture/系统架构设计.md) | 仓位三态、底仓与浮仓隔离 | 改为 `locked_core/core/swing` 和建仓期保护 |
| [进化文档](../../../research/进化文档.md) | GA 黑盒接口、多窗口坩埚、challenger/champion 流程 | 对动态天平权重、网格参数、趋势阈值做进化 |
| `Plan[含phase和提示词].md` | Sigmoid 动态天平、粉尘过滤、仓位偏置 | 改为 A 股 T+1、100 股、涨跌停、印花税过滤 |

### 0.2 三层控制接入点

本策略接入环境层、前置风控、仓位调节层和后置订单风控，并遵循[A 股三层协作与执行契约](../../contracts/A股三层协作与执行契约.md)的双阶段风控链路。

| 控制层 | 文档 | 职责 | 是否下单 |
|---|---|---|---|
| 环境层 | [A 股单标的环境层设计](A股单标的环境层设计.md) | 判断大盘、行业、概念、成交量和市场宽度环境，输出 `MarketContextSnapshot` | 否 |
| 前置风控 | [A 股单标的风控层设计](A股单标的风控层设计.md) | 在策略计算前输出 `RiskContextCaps`，限制仓位上限、新增买入、现金缓冲和熔断状态 | 否 |
| 仓位调节层 | [A 股单标的仓位调节层设计](A股单标的仓位调节层设计.md) | 将环境和前置风控上限映射为动态天平参数与仓位边界，输出 `PositionAdjustmentProfile` | 否 |
| 后置订单风控 | [A 股单标的风控层设计](A股单标的风控层设计.md) | 在 `TradeIntent` 转换为 `OrderRequest` 后，校验 T+1、100 股、涨跌停、停牌、资金、可卖量和库存置换 | 否 |

完整数据流采用“前置风险上限 + 后置订单风控”的双阶段风控模型：

```text
Market Data / Portfolio Snapshot / Order Reports / Security Status
    -> DataQualityGate
    -> 环境层 MarketContextSnapshot
    -> 前置风控层 RiskContextCaps
       只输出最大仓位、现金缓冲、禁买/只降风险/熔断等上限
    -> 仓位调节层 PositionAdjustmentProfile
       只调整动态天平的边界、core/swing 拆分和活跃度
    -> Sigmoid 动态天平 Strategy.Step()
       只输出 TradeIntent 与 RuntimeStatePatch
    -> OrderSizer
       将目标仓位/金额转换为候选订单数量和价格
    -> 后置订单风控 OrderRiskDecision
       校验交易时段、停牌、涨跌停、T+1、可卖量、冻结、现金、库存置换
    -> OrderRouter / QMT Agent
    -> miniQMT / BacktestBroker
    -> RuntimeStateManager / BucketLedger
    -> Strategy on_order / on_trade
```

边界原则：

- 环境层只描述环境，不输出买卖方向。
- 前置风控层只输出风险上限和禁区，不读取具体 `TradeIntent`，不校验具体订单股数。
- 仓位调节层只调整动态天平边界和参数，不生成订单。
- 策略层只输出交易意图，不假定成交，不计算真实可卖量。
- 后置订单风控只基于已有 `OrderRequest` 做允许、限额、延迟、拒绝、熔断和库存置换计划，不创造新的交易方向。
- 实盘成交真源只来自 miniQMT 委托和成交回报。
- 回测和实盘必须共用同一套三层确定性计算规则，只允许 broker 外圈不同。

通用交易域、订单状态机、数据源、公司行为、回测撮合与审计规则分别见：

- [A 股三层协作与执行契约](../../contracts/A股三层协作与执行契约.md)
- [A 股交易域数据结构与状态机](../../contracts/A股交易域数据结构与状态机.md)
- [A 股数据源与公司行为契约](../../contracts/A股数据源与公司行为契约.md)
- [A 股回测 Broker 与成交撮合契约](../../contracts/A股回测Broker与成交撮合契约.md)

---

## 1. 策略边界

### 1.1 适用市场

v1 只覆盖普通 A 股股票多头交易。

不覆盖：

- 融资融券
- ETF 申赎
- 可转债
- 期权、期货
- T+0 品种
- 做空或融券卖出

### 1.2 单标的原则

一个策略实例只绑定一个 `instrument_code`。

策略不得在运行中自行切换标的。如果要跟踪另一只股票，应创建新的策略实例。

### 1.3 真实状态边界

策略不得维护以下真实交易状态：

- 真实现金余额
- 真实总持仓
- 真实可卖量
- 冻结资金
- 冻结股份
- 今日买入股份
- 订单真实生命周期

这些状态统一由执行层和组合状态中心维护。

策略可以维护以下算法状态：

- 动态基准价
- 趋势状态
- 技术低位评分
- 高位反转评分
- 核心仓目标比例
- 短期仓目标比例
- 当前网格层级
- bucket 级别的算法归因
- 待确认订单引用

---

## 2. 仓位结构

策略把单只股票的目标持仓拆成两个可交易 bucket，并额外保留一个可选的用户封存 bucket。

| bucket | 中文名 | 是否由策略主动交易 | 目标 | 行为 |
|---|---|---|---|---|
| `locked_core` | 用户封存仓 | 默认否 | 用户明确不希望策略方向性卖出的长期筹码 | 只做账本隔离，不参与动态天平；可配置是否允许库存置换 |
| `core` | 核心仓 / 长期仓 | 是 | 承接趋势、低位积累、主升浪收益 | 建仓期只进不出，建成后慢进慢出 |
| `swing` | 波动仓 / 短期仓 | 是 | 围绕动态基准捕捉波动 | 快进快出，接受网格和天平调节 |

这里借鉴原系统的 `DeadStack / FloatStack / ColdSealedStack` 思路，但不照搬语义：

- `locked_core` 对应用户主动封存仓，默认不做方向性卖出；若用户允许库存置换，它可以只作为同标的 T+1 库存替换来源，置换后封存总量不下降。
- `core` 对应策略长期仓，但不是永久只进不出；它在建仓期受保护，在高位转弱时可以分批降低。
- `swing` 对应浮动仓，是网格、止盈、短期再平衡的主要操作对象。

### 2.1 总仓位上限

默认使用稳健上限：

$$TotalPositionPct \le 80\%$$

至少保留 20% 现金缓冲，用于：

- 应对 T+1 导致的隔日处理
- 避免连续下跌时无资金调整
- 避免多笔挂单冻结导致超买
- 保留人工干预空间

`locked_core` 是否计入 80% 上限由实例配置决定。默认计入总风险暴露；如果用户明确声明为外部长期封存资产，可以在报告中单独展示，但执行层仍必须按真实持仓计算集中度风险。

### 2.2 仓位阶段

仓位阶段决定 core 是否允许卖出，以及 swing 的活跃程度。

| 阶段 | 触发条件 | core 行为 | swing 行为 |
|---|---|---|---|
| `BUILDING_CORE` | core 未达到基础仓位，且未触发强风控 | 只进不出，普通网格不得卖 core | 暂停或小额试探 |
| `BALANCED_RUN` | core 已达到基础仓位，趋势未恶化 | 慢进慢出，按动态天平调节 | 正常网格 |
| `DISTRIBUTION` | 高位反转评分触发 | 分批降低目标仓位 | 优先卖出，不再新增 |
| `DEFENSIVE` | 明确下跌或强风控 | 只允许降低或保持 | 禁止补仓 |

“底仓只进不出”只适用于 `BUILDING_CORE` 阶段。它的目的不是永久持有，而是防止初期建仓刚获得的核心仓被短期网格卖掉。

### 2.3 动态天平目标范围

状态机不直接给出固定仓位，而是给 Sigmoid 动态天平提供上下边界。

| 市场状态 | core 目标边界 | swing 目标边界 | 行为 |
|---|---:|---:|---|
| 技术低位 | 40% - 65% | 5% - 15% | 慢慢吃饱，允许低吸 |
| 上升趋势 | 50% - 70% | 5% - 10% | 持有核心仓，少量做波动 |
| 中性震荡 | 30% - 50% | 10% - 20% | 网格收益优先 |
| 高位转弱 | 10% - 40% | 0% - 5% | 停止买入，分批出货 |
| 明确下跌 | 0% - 20% | 0% | 保护现金，等待重建 |

最终仓位由 Sigmoid 动态天平在边界内连续计算，而不是从表格中离散取值。

### 2.4 bucket 归因原则

策略发出的 `TradeIntent` 必须带上顶层 bucket：

```json
{
  "instrument_code": "600000.SH",
  "direction": "BUY",
  "bucket": "core",
  "reason": "dynamic_balance_core_buy",
  "target_position_pct": 0.52,
  "metadata": {
    "phase": "BUILDING_CORE",
    "benchmark_price": 10.25,
    "trend_state": "LOW_ACCUMULATION",
    "target_engine": "SIGMOID_BALANCE"
  }
}
```

执行层需要保留订单和成交的 bucket 归因，用于后续卖出时区分核心仓和短期仓。

### 2.5 T+1 库存置换原则

A 股 T+1 限制的是“今日买入的股份不能当日卖出”，不是限制策略内部的 bucket 标签。同一只股票在券商账户中是可替换库存，因此当 swing 当日买入后又触发止盈卖出时，可以使用已有可卖 core 老仓完成实际卖出，再把当日买入的 swing 份额重新归因给 core。

这不是绕过交易规则，而是合法地卖出账户中本来就可卖的老股份。

默认置换顺序：

1. 优先卖出 `swing_available`
2. 若 swing 因 T+1 不可卖，允许使用 `core_available` 做库存置换
3. 若实例显式开启 `allow_locked_core_substitution`，允许使用 `locked_core_available` 做库存置换
4. 若可置换量不足，则剩余 swing 卖出意图进入等待或被执行层拒绝

置换记账：

```text
old core available shares -> temporarily attributed as swing sellable shares
today swing bought shares -> re-attributed as core unavailable shares
actual broker sell        -> sells old available shares
next trading day          -> re-attributed core shares become available
```

约束：

- 只能在同一标的内部置换。
- 只能使用已可卖的 core 老仓。
- 不能动用 `locked_core` 做方向性卖出；若用户显式开启库存置换，可以用同标的 `locked_core_available` 老仓替换已买入的 swing 仓，但置换后 `locked_core` 总量必须保持不变。
- 置换数量不得超过当日已成交的 swing 买入数量。
- 卖单拒单或撤单时，置换归因必须回滚。
- 部分成交时，只按实际成交数量完成归因置换。
- 置换后 core 的总量不应下降，只是可卖性从老仓转移到今日买入仓，次交易日恢复。

这样可以让策略在不违反 T+1 的前提下，提高 swing 网格的日内回收能力，同时保留 core 建仓目标。

---

## 3. 数据窗口

### 3.1 日线窗口

日线用于判断：

- 长期趋势
- 技术低位
- 高位反转
- 动态基准主方向
- 核心仓目标

默认指标：

| 指标 | 默认周期 | 用途 |
|---|---:|---|
| EMA Fast | 20 日 | 短趋势 |
| EMA Mid | 60 日 | 中期趋势 |
| EMA Slow | 120 日 | 长期趋势 |
| ATR | 14 日 | 波动率、网格间距 |
| 价格分位 | 250 / 750 日 | 技术低位和高位识别 |
| 成交量分布 | 120 / 250 日 | 成交密集区、支撑压力 |

### 3.2 分钟 / tick 窗口

1m 或 tick 用于判断：

- 网格触发
- 是否触达限价
- 日内价格偏离
- 短期成交量放大

推荐实盘路径：

- 日线收盘后更新趋势状态和 core 目标
- 盘中用 1m/tick 触发 swing 网格
- 盘中不频繁改变 core 目标，除非触发强风控

### 3.3 数据缺失处理

实盘中如果关键字段缺失：

- 缺少停牌状态：拒绝真实下单
- 缺少涨跌停价格：拒绝真实下单
- 缺少 price tick：拒绝真实下单
- 缺少成交量：允许运行，但禁用成交量分布评分
- 缺少分钟/tick：只执行日线低频逻辑，不执行盘中网格

回测中可以使用保守推导，但必须在回测报告中标注。

---

## 4. 动态基准

动态基准不是简单均线，而是用于短期网格的中心锚点。

基准由三部分组成：

1. 趋势基准：EMA 组合
2. 波动修正：ATR 偏移
3. 成交量分布修正：成交密集区锚定

### 4.1 趋势基准

基础基准：

$$Base = w_1 \times EMA_{20} + w_2 \times EMA_{60} + w_3 \times EMA_{120}$$

默认：

| 权重 | 默认值 |
|---|---:|
| `w1` | 0.20 |
| `w2` | 0.50 |
| `w3` | 0.30 |

趋势越强，基准越靠近中短期 EMA；趋势越弱，基准越靠近长期 EMA。

### 4.2 ATR 波动修正

用 ATR 判断价格相对基准的有效偏离。

$$ATRPct = ATR_{14} / Close$$

如果 ATR 明显放大，网格间距同步变宽，避免在高波动下过度交易。

默认网格间距：

$$GridStepPct = clamp(ATRPct \times 0.6,\;0.8\%,\;3.0\%)$$

### 4.3 成交量分布修正

在最近 120 / 250 个交易日内，将价格区间切成若干 bins，统计每个价格区间的成交量。

关键价格：

- `volume_poc`：成交量最大的价格区间中心
- `support_zone`：当前价下方最近的成交密集区
- `resistance_zone`：当前价上方最近的成交密集区

基准修正原则：

- 当前价接近低位支撑区时，基准可轻微下移，允许慢慢吃入
- 当前价远离成交密集区向上过度偏离时，基准不得快速上移追涨
- 当前价跌破主要成交密集区后，降低买入强度

### 4.4 基准更新节奏

动态基准必须避免“追着价格跑”。

默认规则：

- 日线收盘后更新主基准
- 盘中只使用上一交易日确认基准
- 当天不因盘中急跌连续下移基准
- 基准单日变化幅度不超过 `1.5 * ATRPct`
- 高位反转状态下，基准只允许持平或下调风险，不允许触发追涨加仓

---

## 5. Sigmoid 动态天平仓位引擎

动态基准负责回答“价格围绕哪里波动”，Sigmoid 动态天平负责回答“当前应该持有多少仓位”。

本策略借鉴 `Plan[含phase和提示词].md` 中的 Sigmoid 动态天平思想，但做 A 股单票化改造：

- 原 `FloatBTC` 映射为 `swing` 波动仓。
- 原长期底仓思想映射为 `BUILDING_CORE` 阶段的 core 保护。
- 原 `ColdSealedStack` 映射为用户可选 `locked_core`。
- 原 7x24 现货订单过滤改为 A 股交易时段、T+1、涨跌停、100 股整数倍、印花税过滤。

### 5.1 信号方向约定

动态天平使用一个归一化信号 `BalanceSignal`。

约定：

- `BalanceSignal > 0`：偏空，目标仓位下降。
- `BalanceSignal < 0`：偏多，目标仓位上升。
- `BalanceSignal = 0`：中性，仓位靠近中轴。

组合信号：

$$BalanceSignal = a \times X_{trend} + b \times X_{deviation} + c \times X_{volume} + d \times X_{risk} + e \times X_{phase}$$

其中：

| 信号 | 含义 | 偏多方向 | 偏空方向 |
|---|---|---|---|
| `X_trend` | 趋势结构 | 多头排列、回踩不破 | 空头排列、趋势破坏 |
| `X_deviation` | 价格相对动态基准偏离 | 低于基准且未破位 | 高于基准过远或跌破支撑 |
| `X_volume` | 成交量分布 | 接近支撑密集区、放量止跌 | 高位放量滞涨、跌破 POC |
| `X_risk` | 风控压力 | 风险低、波动收敛 | 跌停风险、ATR 放大 |
| `X_phase` | 仓位阶段 | 建仓期或低位积累 | 出货期或防御期 |

所有输入必须无量纲化，禁止直接把绝对价格作为跨标的可比特征。单标的内部可以使用价格，但进入天平前必须转换为比例、分位、ATR 倍数或评分。

### 5.2 Sigmoid 目标仓位

总目标仓位先由 Sigmoid 给出：

$$InventoryBias = CurrentPositionPct - NeutralPositionPct$$

$$Exponent = \beta \times BalanceSignal + \gamma \times InventoryBias$$

$$RawTargetPct = \frac{1}{1 + e^{Exponent}}$$

再映射到当前状态允许的仓位边界：

$$TargetTotalPct = MinPct + RawTargetPct \times (MaxPct - MinPct)$$

含义：

- 信号越偏多，`Exponent` 越小，目标仓位越接近上限。
- 信号越偏空，`Exponent` 越大，目标仓位越接近下限。
- 当前仓位过高时，`InventoryBias` 会压低目标仓位，避免越涨越满。
- 当前仓位过低时，`InventoryBias` 会抬高目标仓位，帮助低位慢慢补足。

### 5.3 core / swing 拆分

总目标仓位确定后，再拆分为 core 与 swing。

$$CoreShare = clamp(CoreBaseShare + k_{trend} \times TrendStrength - k_{risk} \times DistributionRisk,\;CoreShareMin,\;CoreShareMax)$$

$$TargetCorePct = TargetTotalPct \times CoreShare$$

$$TargetSwingPct = TargetTotalPct - TargetCorePct$$

拆分原则：

- 建仓期：优先满足 core，swing 降低到 0% - 5%。
- 上升趋势：core 占比提高，swing 只做小额增强。
- 中性震荡：swing 占比提高，网格收益优先。
- 高位转弱：swing 优先降到 0，core 再分批下降。
- 明确下跌：swing 为 0，core 只允许降低或保持。

### 5.4 理论订单与楔形过滤

动态天平先计算理论调仓金额：

$$DeltaPct = TargetBucketPct - CurrentBucketPct$$

$$TheoreticalAmount = DeltaPct \times TotalEquity$$

过滤规则：

- 金额低于最小有效订单，不输出交易意图。
- 金额虽小但突破一个完整网格，允许按 A 股最小合法手数输出。
- 预期收益不能覆盖佣金、印花税、滑点、安全边际时，不输出 swing 网格意图。
- 建仓期 core 买入可以放宽网格过滤，但仍必须满足最小订单、100 股、资金、涨跌停规则。

这对应原动态天平中的“粉尘拦截 + 楔形区过滤”，但执行条件改为 A 股真实交易约束。

### 5.5 状态机与天平的关系

状态机不再直接决定仓位，而是决定：

- Sigmoid 输入信号
- 仓位上下边界
- core/swing 拆分比例
- 是否允许买入或卖出
- 是否允许普通网格触发

动态天平负责连续计算目标仓位，避免策略在不同状态之间跳变过猛。

### 5.6 GA 可进化参数

以下参数适合纳入进化系统，由 `EvolvableStrategy` 黑盒接口搜索：

| 参数 | 默认值 | 边界 | 说明 |
|---|---:|---:|---|
| `signal_trend_weight` | 1.0 | [-3, 3] | 趋势信号权重 |
| `signal_deviation_weight` | 1.2 | [-3, 3] | 动态基准偏离权重 |
| `signal_volume_weight` | 0.8 | [-3, 3] | 成交量分布权重 |
| `signal_risk_weight` | 1.5 | [0, 5] | 风控信号权重 |
| `signal_phase_weight` | 1.0 | [-3, 3] | 仓位阶段权重 |
| `balance_beta` | 2.0 | [0.1, 8.0] | 动态天平激进程度 |
| `inventory_gamma` | 1.0 | [0.0, 5.0] | 仓位偏置回归强度 |
| `neutral_position_pct` | 0.45 | [0.2, 0.7] | 中性目标仓位 |
| `core_base_share` | 0.75 | [0.4, 0.95] | core 默认占总仓比例 |
| `grid_atr_multiplier` | 0.60 | [0.2, 1.5] | ATR 网格倍数 |

不纳入染色体、只作为实例级出生点或风控配置的参数：

- 单票最大仓位
- 最低现金缓冲
- 用户封存仓数量
- 最大允许回撤
- 单日最大新增仓位
- 实盘交易开关

进化引擎只看到采样、变异、交叉、评估等抽象动词，不应 import 具体策略字段或理解 core/swing 语义。

## 6. 趋势与位置状态机

策略主状态分为五类。

| 状态 | 中文名 | 含义 |
|---|---|---|
| `LOW_ACCUMULATION` | 技术低位积累 | 处于相对低位，允许慢慢加核心仓 |
| `UPTREND` | 上升趋势 | 趋势健康，核心仓持有为主 |
| `NEUTRAL` | 中性震荡 | 趋势不明，短仓网格为主 |
| `HIGH_DISTRIBUTION` | 高位分批出货 | 高位转弱，停止买入并降低仓位 |
| `DOWNTREND` | 明确下跌 | 保护现金，禁止网格抄底 |

### 6.1 技术低位评分

技术低位不是单一指标，而是组合评分。

默认维度：

| 维度 | 条件示例 | 作用 |
|---|---|---|
| 价格分位 | close 处于 250 / 750 日低分位 | 判断相对便宜 |
| 距离支撑 | close 接近成交密集支撑区 | 判断承接位置 |
| 跌幅消化 | 偏离 EMA60 但未失控 | 避免追在半山腰 |
| 波动收敛 | ATRPct 从高位回落 | 判断恐慌衰减 |
| 放量止跌 | 下跌后放量但价格不再新低 | 判断可能有资金承接 |

低位评分范围为 0 到 100。

默认：

- `score >= 70`：允许提高 core 目标
- `score 50 - 70`：允许小额试探
- `score < 50`：不因低位逻辑加仓

### 6.2 高位反转评分

高位反转同样使用组合评分。

默认维度：

| 维度 | 条件示例 | 作用 |
|---|---|---|
| 价格高分位 | close 处于 250 日高分位 | 判断位置偏高 |
| 远离基准 | close 高于 benchmark 多个 ATR | 判断过热 |
| 高位放量 | 放量但价格滞涨或长上影 | 判断出货迹象 |
| 趋势破坏 | EMA20 下穿 EMA60 或 close 跌破 EMA60 | 判断趋势转弱 |
| 跌破密集区 | 跌破近期 volume_poc | 判断筹码松动 |

默认：

- `score >= 75`：进入 `HIGH_DISTRIBUTION`
- `score >= 90`：允许更快降低 core
- `score < 60`：不触发出货状态

### 6.3 下跌状态保护

以下条件触发 `DOWNTREND`：

- EMA20、EMA60、EMA120 空头排列
- close 跌破 EMA120 且未快速收复
- 跌破主要成交密集支撑区
- ATRPct 放大且持续创新低

进入 `DOWNTREND` 后：

- 禁止 swing 买入
- 禁止因网格下跌而补仓
- core 只允许降低或保持，不允许提高
- 只有趋势修复后才允许重新积累

---

## 7. 核心仓逻辑

核心仓负责长期方向，不参与频繁网格。

### 7.1 core 目标仓位计算

core 目标不再由状态机直接离散指定，而是由动态天平先算出 `TargetTotalPct`，再按趋势强度和风险状态拆分。

```text
TargetTotalPct = sigmoid_balance(BalanceSignal, CurrentPositionPct, StateBounds)
CoreShare      = core_share(TrendStrength, DistributionRisk, Phase)
TargetCorePct  = TargetTotalPct * CoreShare
```

状态机只提供边界和禁区：

- `LOW_ACCUMULATION`：提高 core 上限，允许目标仓位缓慢靠近上沿。
- `UPTREND`：提高 core share，让主升浪收益主要由 core 承接。
- `NEUTRAL`：降低 core share，释放更多 swing 空间。
- `HIGH_DISTRIBUTION`：压低 core 上限，并触发分批降仓节奏。
- `DOWNTREND`：core 只允许降低或保持，不允许提高。

实际输出必须再经过总仓位上限：

$$core\_target + swing\_target \le 80\%$$

### 7.2 慢慢吃饱

低位加 core 不一次性完成。

建仓期规则：

- 当 `phase = BUILDING_CORE` 且 core 未达到基础仓位时，普通网格不得卖出 core。
- 建仓期允许 core 只进不出，但强风控、跌破关键支撑、进入 `DOWNTREND` 可以打断建仓。
- 建仓完成后，core 从“只进不出”切换为“慢进慢出”。

默认节奏：

- 单日 core 增加不超过总资产的 5%
- 单次买入不超过总资产的 2%
- 连续下跌时，每下一个 ATR 层级才允许下一笔加仓
- 若当日已经有 swing 买入，core 买入要降低优先级
- 动态天平目标仓位提高后，也必须按节奏分多次靠近目标

### 7.3 分批出货

高位反转不直接清仓，除非触发强风控。

默认节奏：

- 第一阶段：停止 swing 买入
- 第二阶段：卖出全部或大部分 swing
- 第三阶段：下调动态天平的 core 上限和 core share
- 第四阶段：按目标差额分批卖出 core
- 第五阶段：若趋势继续破坏，进入 `DEFENSIVE`，core 只允许继续降低或保持

执行层会根据 A 股 T+1 可卖量决定当天实际可卖数量。

策略不得因为“想卖但不可卖”而伪造已卖出状态。

---

## 8. 短期仓网格逻辑

短期仓围绕动态基准做波动收益，但它不是独立于动态天平的第二套系统。

动态天平决定 `TargetSwingPct`，网格只决定 swing 仓位靠近目标时的触发价格和节奏。也就是说，网格只能在动态天平允许的 swing 目标空间内交易。

### 8.1 网格层级

网格层级按当前价相对动态基准的偏离计算。

$$GridIndex = floor((Price - Benchmark) / (Benchmark \times GridStepPct))$$

示例：

- `GridIndex = -1`：低于基准一个网格
- `GridIndex = -2`：低于基准两个网格
- `GridIndex = +1`：高于基准一个网格
- `GridIndex = +2`：高于基准两个网格

### 8.2 swing 买入

允许买入的前置条件：

- 状态不是 `DOWNTREND`
- 状态不是强 `HIGH_DISTRIBUTION`
- `TargetSwingPct > CurrentSwingPct`
- 当前总目标仓位未超过 80%
- 当前价格未处于涨停买入不可成交状态
- 与上一笔 swing 买入至少间隔一个有效网格
- 预期网格收益覆盖手续费、印花税、滑点
- 如果处于 `BUILDING_CORE`，swing 买入必须让位于 core 建仓

买入强度：

- 技术低位：允许较大 swing 买入
- 中性震荡：标准 swing 买入
- 上升趋势：小额 swing 买入
- 高位转弱：禁止新增 swing

### 8.3 swing 卖出

卖出触发：

- 价格回到基准上方
- 当前层级高于最近买入层级至少 1 个网格
- `TargetSwingPct < CurrentSwingPct`
- 高位反转进入分批出货
- core 需要保护，先卖 swing

卖出原则：

- 优先卖 swing bucket
- 不主动卖 core bucket 来完成普通 swing 止盈
- `BUILDING_CORE` 阶段禁止普通网格卖 core，除非触发强风控
- 但如果存在当日已成交的 swing 买入，可用 core 可卖老仓做 T+1 库存置换，置换后 core 总量不下降
- `locked_core` 不参与普通 swing 方向性卖出；仅当实例开启 `allow_locked_core_substitution` 时，才可参与 T+1 库存置换
- 如果 swing 可卖量不足，执行层返回结构化拒单或部分成交
- 策略等待成交回报更新网格状态

### 8.4 网格成交状态

网格状态只能由订单/成交事件驱动。

不得在信号生成时直接标记网格已完成。

订单状态处理：

| 订单结果 | 策略行为 |
|---|---|
| 拒单 | 不更新网格层级，记录拒单原因 |
| 已报 | 标记 pending，不重复发同层订单 |
| 部分成交 | 按成交量更新 bucket 算法归因 |
| 全部成交 | 更新最近成交层级 |
| 撤单 | 释放 pending，可按新价格重新判断 |

---

## 9. 交易意图接口

策略只能输出 `TradeIntent` 与 `RuntimeStatePatch`。`TradeIntent` 只表达交易意图，不表达真实下单数量，也不假定成交。

### 9.1 core 买入意图

```json
{
  "direction": "BUY",
  "instrument_code": "600000.SH",
  "bucket": "core",
  "reason": "dynamic_balance_core_buy",
  "target_position_pct": 0.55,
  "confidence": 0.78,
  "metadata": {
    "phase": "BUILDING_CORE",
    "trend_state": "LOW_ACCUMULATION",
    "low_score": 82,
    "benchmark_price": 10.25,
    "balance_signal": -0.63,
    "target_engine": "SIGMOID_BALANCE"
  }
}
```

### 9.2 swing 买入意图

```json
{
  "direction": "BUY",
  "instrument_code": "600000.SH",
  "bucket": "swing",
  "reason": "grid_buy",
  "target_amount": 12000,
  "confidence": 0.64,
  "metadata": {
    "grid_index": -2,
    "grid_step_pct": 0.012,
    "benchmark_price": 10.25,
    "target_swing_pct": 0.12,
    "target_engine": "SIGMOID_BALANCE"
  }
}
```

### 9.3 swing 卖出意图

```json
{
  "direction": "SELL",
  "instrument_code": "600000.SH",
  "bucket": "swing",
  "reason": "grid_take_profit",
  "target_amount": 12000,
  "confidence": 0.66,
  "metadata": {
    "grid_index": 1,
    "benchmark_price": 10.25,
    "target_swing_pct": 0.06,
    "target_engine": "SIGMOID_BALANCE"
  }
}
```

### 9.4 高位分批出货意图

```json
{
  "direction": "SELL",
  "instrument_code": "600000.SH",
  "bucket": "core",
  "reason": "dynamic_balance_distribution",
  "target_position_pct": 0.35,
  "confidence": 0.81,
  "metadata": {
    "phase": "DISTRIBUTION",
    "trend_state": "HIGH_DISTRIBUTION",
    "distribution_score": 84,
    "balance_signal": 0.71,
    "target_engine": "SIGMOID_BALANCE"
  }
}
```

---

## 10. 执行层契约

策略只表达意图，执行层负责真实下单。

执行链路：

```text
StrategyOutput / TradeIntent
    -> OrderSizer
    -> RiskChecker
    -> OrderRouter
    -> Broker
    -> RuntimeStateManager
    -> Strategy on_order/on_trade
```

### 10.1 OrderSizer

负责把目标仓位 / 目标金额转换为 A 股合法订单。

必须处理：

- 买入 100 股整数倍
- 卖出允许零股清仓
- 最小申报数量
- 最大申报数量
- 价格 tick
- 账户资金
- 冻结资金
- 目标仓位到订单数量转换

### 10.2 RiskChecker

必须校验：

- 停牌
- 非交易时段
- 涨跌停
- T+1 可卖量
- T+1 库存置换额度
- 现金和冻结资金
- bucket 可卖量
- 单票总仓位上限
- 风控黑名单

### 10.3 RuntimeStateManager

需要记录：

- `total_volume`
- `available_volume`
- `frozen_volume`
- `today_buy_volume`
- `cost`
- `cash`
- `frozen_cash`
- 成交批次
- bucket 归因
- bucket 库存置换流水
- 订单占用

### 10.4 Broker

回测 broker：

- 日线用 high/low 判断限价是否触达
- tick/分钟用盘口、最新价、成交量近似撮合
- 涨停买入默认不成交
- 跌停卖出默认不成交
- 支持部分成交和挂单未成交

实盘 broker：

- 本地先做同一套规则校验
- 调用真实交易接口
- 外部拒单后释放冻结
- 成交回报同步回 RuntimeStateManager

---

## 11. 风控规则

### 11.1 总仓位风控

默认：

| 项目 | 默认值 |
|---|---:|
| 单票最大仓位 | 80% |
| 最低现金缓冲 | 20% |
| 单日最大新增仓位 | 10% |
| 单笔最大新增仓位 | 5% |
| swing 最大仓位 | 20% |

### 11.2 交易成本约束

网格收益必须覆盖交易成本。

最低要求：

$$GridStepPct > 2 \times CommissionPct + StampTaxPct + SlippagePct + SafetyMargin$$

如果不满足，禁止触发 swing 网格。

### 11.3 连续下跌保护

以下情况禁止继续网格买入：

- 连续 3 日收盘价低于 EMA60
- 跌破 EMA120 且 ATRPct 放大
- 跌破主要成交密集支撑区
- 当日跌停或接近跌停
- 进入 `DOWNTREND`

### 11.4 高位保护

以下情况禁止新增买入：

- 进入 `HIGH_DISTRIBUTION`
- 高位反转评分超过 75
- 高位放量滞涨
- 当日长上影且成交量显著放大
- 短期涨幅过大并远离动态基准

---

## 12. 参数默认值

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `max_position_pct` | 0.80 | 单票最大仓位 |
| `cash_buffer_pct` | 0.20 | 最低现金缓冲 |
| `allow_locked_core_substitution` | false | 是否允许 locked_core 只用于 T+1 库存置换 |
| `core_min_pct` | 0.00 | 核心仓最低目标 |
| `core_max_pct` | 0.70 | 核心仓最高目标 |
| `swing_max_pct` | 0.20 | 短期仓最高目标 |
| `base_core_position_pct` | 0.40 | 建仓期基础核心仓目标 |
| `neutral_position_pct` | 0.45 | 动态天平中性仓位 |
| `balance_beta` | 2.00 | 动态天平激进系数 |
| `inventory_gamma` | 1.00 | 仓位偏置回归强度 |
| `core_base_share` | 0.75 | core 默认占总目标仓位比例 |
| `signal_trend_weight` | 1.00 | 趋势信号权重 |
| `signal_deviation_weight` | 1.20 | 动态基准偏离权重 |
| `signal_volume_weight` | 0.80 | 成交量分布权重 |
| `signal_risk_weight` | 1.50 | 风控信号权重 |
| `signal_phase_weight` | 1.00 | 仓位阶段权重 |
| `ema_fast_period` | 20 | 短趋势 EMA |
| `ema_mid_period` | 60 | 中趋势 EMA |
| `ema_slow_period` | 120 | 长趋势 EMA |
| `atr_period` | 14 | ATR 周期 |
| `grid_atr_multiplier` | 0.60 | ATR 网格倍数 |
| `min_grid_step_pct` | 0.008 | 最小网格间距 |
| `max_grid_step_pct` | 0.030 | 最大网格间距 |
| `low_score_buy_threshold` | 70 | 低位加仓阈值 |
| `distribution_threshold` | 75 | 高位出货阈值 |
| `downtrend_stop_threshold` | 80 | 下跌保护阈值 |
| `daily_core_add_limit_pct` | 0.05 | 单日 core 最大增加 |
| `single_order_limit_pct` | 0.05 | 单笔最大仓位变化 |
| `volume_profile_days` | 120 | 成交量分布窗口 |
| `price_quantile_days` | 750 | 价格分位窗口 |

---

## 13. 生命周期

### 13.1 初始化

策略实例创建时：

1. 绑定唯一 `instrument_code`
2. 加载历史日线数据
3. 计算 EMA、ATR、价格分位、成交量分布
4. 初始化动态基准
5. 初始化动态天平参数和信号权重
6. 初始化状态为 `NEUTRAL`
7. 初始化阶段为 `BUILDING_CORE` 或 `BALANCED_RUN`
8. 从组合状态中心读取当前真实持仓快照
9. 按当前持仓建立初始 bucket 算法归因

如果已有真实持仓但没有 bucket 归因，默认归入 `core`。

### 13.2 日线收盘动作

每日收盘后：

1. 更新技术指标
2. 更新成交量分布
3. 更新动态基准
4. 计算低位评分和高位反转评分
5. 切换趋势状态
6. 切换仓位阶段
7. 构造动态天平输入信号
8. 计算 `TargetTotalPct`、`TargetCorePct`、`TargetSwingPct`
9. 生成必要的 core 调仓意图

### 13.3 盘中动作

盘中每个 tick 或 1m bar：

1. 读取上一日确认基准
2. 读取日线确认的动态天平目标
3. 计算当前 grid index
4. 判断 swing 买卖触发
5. 检查 pending 订单，避免重复发单
6. 输出 swing 交易意图

### 13.4 订单回报动作

收到订单回报：

- 更新 pending 状态
- 记录拒单原因
- 已报订单不重复发同层信号
- 撤单后允许重新评估

### 13.5 成交回报动作

收到成交回报：

- 按 bucket 更新算法归因
- 更新最近成交 grid index
- 更新最近成交基准
- 部分成交只按实际成交量更新
- 不修改真实现金和真实持仓

---

## 14. 回测要求

回测必须验证真实 A 股限制，而不是理想化成交。

### 14.1 日线回测

日线回测撮合：

- 买入限价必须满足 `low <= limit_price <= high`
- 卖出限价必须满足 `low <= limit_price <= high`
- 涨停买入默认不成交
- 跌停卖出默认不成交
- 成交价可配置为限价、开盘价、均价或保守滑点价

### 14.2 分钟 / tick 回测

分钟 / tick 回测撮合：

- 使用最新价、盘口、成交量近似成交
- 支持部分成交
- 支持挂单延迟
- 支持撤单
- 不允许用未来价格判断当前成交

### 14.3 重点场景

必须覆盖：

- 单边上涨：core 持有，swing 少量止盈
- 箱体震荡：swing 高频贡献收益
- 单边下跌：停止网格补仓，降低 core
- 低位横盘：逐步提高 core
- 高位放量转弱：停止买入并分批出货
- 当日买入后触发卖出：若存在可置换老仓则库存置换，否则 T+1 拒绝或延迟
- 连续涨停：买入挂单不应假成交
- 连续跌停：卖出意图不能假成交

---

## 15. 测试计划

### 15.1 单元测试

- 动态基准计算正确
- Sigmoid 动态天平在偏多信号下提高目标仓位
- Sigmoid 动态天平在偏空信号下降低目标仓位
- 仓位偏置能阻止仓位持续向极端漂移
- 状态边界能限制动态天平输出范围
- `BUILDING_CORE` 阶段普通网格不得卖出 core
- ATR 网格间距被 min/max 限制
- 成交量分布能识别 POC、支撑、压力
- 低位评分能触发 `LOW_ACCUMULATION`
- 高位评分能触发 `HIGH_DISTRIBUTION`
- 下跌保护能触发 `DOWNTREND`
- 总仓位目标不超过 80%
- 单日 core 加仓不超过限制
- swing 买入必须间隔有效网格
- swing 卖出不得默认侵占 core bucket

### 15.2 执行链路测试

- 买入数量按 100 股整数倍修正
- 卖出允许零股清仓
- T+1 当日买入不可卖
- 当日 swing 买入后触发卖出时，可使用 core 老仓做库存置换
- `allow_locked_core_substitution=false` 时，`locked_core` 不得被库存置换使用
- `allow_locked_core_substitution=true` 时，`locked_core_available` 可做库存置换但封存总量必须保持不变
- 库存置换卖单部分成交时，只按成交数量完成归因转换
- 库存置换卖单拒单时，归因必须回滚
- 停牌拒单
- 涨停买入拒绝或不成交
- 跌停卖出拒绝或不成交
- 部分成交后 bucket 归因正确
- 拒单后网格不得标记成交

### 15.3 策略场景测试

- 技术低位慢慢吃饱
- 初期建仓期 core 只进不出
- core 建仓完成后切换到慢进慢出
- 趋势上涨持有核心仓
- 震荡行情短仓反复买卖
- 高位反转优先卖 swing
- 高位反转后分批降低 core
- 明确下跌时禁止网格补仓
- 可卖量不足时保留待处理状态

---

## 16. 实现建议

建议新增策略类：

```text
core/strategies/ashare_dynamic_balance_dual_bucket_strategy.py
```

策略实现不包含通用 A 股交易规则。

需要的通用能力应放入交易域层：

- bucket 归因
- bucket 可卖量查询
- bucket 阶段标签
- 目标仓位转订单
- T+1 校验
- 涨跌停校验
- 停牌校验
- 成交回写

如果当前交易域层暂不支持 bucket 归因，应先扩展通用组合状态模型，再实现该策略。

---

## 17. 设计结论

该策略可行，但必须满足三个条件：

1. 动态天平只能计算目标仓位，真实下单数量必须由执行层按 A 股规则校验
2. 动态基准不能追涨杀跌，必须有更新节奏和 ATR 限制
3. 网格不能脱离趋势状态和天平目标，否则会在下跌趋势中持续摊薄亏损
4. 建仓期 core 可以只进不出，但建仓完成后必须允许高位转弱分批降仓
5. 长短仓必须只是算法归因，真实持仓和可卖量必须由执行层统一管理

推荐 v1 先实现为保守版本：

- 单标的
- 总仓不超过 80%
- 日线判断趋势
- 1m/tick 执行短仓网格
- Sigmoid 动态天平计算总目标仓位
- 状态机只提供边界、阶段和禁区
- 初期建仓保护 core，不被普通网格卖出
- 技术低位慢慢加 core
- 高位转弱分批降仓
- 明确下跌禁止补仓

在该版本通过回测和纸面交易验证后，再考虑引入基本面估值、机器学习评分、更复杂的成交量结构模型，或把更多动态天平权重交给 GA 进化。
