# A 股个人量化开发文档索引

**文档目标：** 给后续开发提供一个入口索引，说明每份文档负责什么、代码实现应按什么顺序推进、当前 `A股单标的动态天平双仓策略` 与通用 A 股交易域如何解耦。

---

## 1. 文档分层

### 1.1 系统级

| 文档 | 定位 |
|---|---|
| [系统架构设计](../architecture/系统架构设计.md) | 服务端 / QMT Agent / Lab 三端架构、状态真源、生命周期、miniQMT 执行端 |
| [进化文档](../research/进化文档.md) | A 股 GA 进化黑盒、多窗口坩埚、Ghost DCA、challenger/champion 流程 |

### 1.2 实施级

| 文档 | 定位 |
|---|---|
| [A 股动态天平双仓策略实现落地规格与迁移计划](../plans/A股动态天平双仓策略实现落地规格与迁移计划.md) | 从当前 Python 策略框架迁移到 `StrategyBase.step()`、`TradeIntent`、`OrderRiskDecision` 和 `BucketLedger` 的破坏性实施路线 |
| [A 股建仓/加仓托管计划模块设计与落地规格](../plans/A股建仓加仓托管计划模块设计与落地规格.md) | 固定单标的建仓/加仓托管的 EntryPlan 领域、状态机、授权、GraphQL、前端工作台和分阶段实施规格 |

### 1.3 当前策略级

| 文档 | 定位 |
|---|---|
| [A 股单标的动态天平双仓策略](strategies/dynamic-balance/A股单标的动态天平双仓策略.md) | 当前主线策略公式、动态基准、Sigmoid 天平、core/swing/locked_core、网格逻辑 |
| [A 股单标的打板策略](strategies/A股单标的打板策略.md) | 临近涨停扫板入场、单次意图、防重复、破板/回撤/持有期统一退出计划 |
| [A 股单标的环境层设计](strategies/dynamic-balance/A股单标的环境层设计.md) | 当前策略使用的环境层规则：大盘、行业、概念、宽度、流动性、量价结构 |
| [A 股单标的风控层设计](strategies/dynamic-balance/A股单标的风控层设计.md) | 当前策略使用的风控规则：交易时段、T+1、涨跌停、停牌、熔断、miniQMT 真源 |
| [A 股单标的仓位调节层设计](strategies/dynamic-balance/A股单标的仓位调节层设计.md) | 当前策略使用的仓位 profile：MinPct/MaxPct、core/swing、现金缓冲、beta/gamma |

### 1.4 新补齐的通用交易域文档

| 文档 | 解决的问题 | 策略通用性 |
|---|---|---|
| [A 股三层协作与执行契约](contracts/A股三层协作与执行契约.md) | 三层调用顺序、双阶段风控、策略接入、执行闭环 | 所有 A 股策略共用 |
| [A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md) | schema、枚举、订单状态机、bucket 账本、T+1 置换、审计 | 所有 A 股策略共用；bucket 可选 |
| [A 股自动退出计划与卖出策略契约](contracts/A股自动退出计划与卖出策略契约.md) | 入场成交后的破板、止盈止损、持有期与统一卖出状态机 | 所有带自动退出规则的入场策略共用 |
| [A 股数据源与公司行为契约](contracts/A股数据源与公司行为契约.md) | 数据源映射、数据质量、时点可得、复权、公司行为、证券状态 | 所有 A 股策略共用 |
| [A 股回测 Broker 与成交撮合契约](contracts/A股回测Broker与成交撮合契约.md) | 回测 broker、撮合、成本、T+1、涨跌停、成交约束统计 | 所有 A 股策略共用 |

---

## 2. 推荐开发顺序

破坏性迁移必须先读 [A 股动态天平双仓策略实现落地规格与迁移计划](../plans/A股动态天平双仓策略实现落地规格与迁移计划.md)。本节保留模块顺序，具体接口替换、旧路径删除和验收矩阵以实施级文档为准。

### Phase A：交易域基础结构

先实现通用结构，不写具体策略公式。

1. `InstrumentMaster`
2. `AshareMarketRules`
3. `MarketContextSnapshot`
4. `RiskContextCaps`
5. `PositionAdjustmentProfile`
6. `PortfolioState`
7. `BucketLedger`
8. `TradeIntent`
9. `OrderDraft / OrderRequest / TradeCommand`
10. `OrderState`
11. `BrokerExecutionReport`
12. `DecisionTrace`

验收依据：[A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md)。

### Phase B：数据源与日历

实现数据适配器和本地数据表。

1. 个股日线/分钟线。
2. 指数/行业/概念。
3. 市场宽度。
4. 交易日历。
5. 涨跌停价。
6. 停牌/ST/退市风险。
7. 公司行为。
8. 时点可得复权因子。

验收依据：[A 股数据源与公司行为契约](contracts/A股数据源与公司行为契约.md)。

### Phase C：状态管理与订单状态机

实现交易事实收敛中心。

1. OrderStateMachine。
2. RuntimeStateManager。
3. BucketLedgerManager。
4. T1SubstitutionManager。
5. ReconciliationManager。
6. DecisionTraceLogger。

验收依据：[A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md)。

### Phase D：环境层、前置风控、仓位调节层

实现策略前的确定性三层。

1. EnvironmentLayer 输出 `MarketContextSnapshot`。
2. ContextRiskLayer 输出 `RiskContextCaps`。
3. PositionAdjustmentLayer 输出 `PositionAdjustmentProfile`。
4. 确认三层可在日线和盘中两种 cadence 下运行。

验收依据：

- [A 股三层协作与执行契约](contracts/A股三层协作与执行契约.md)
- [A 股单标的环境层设计](strategies/dynamic-balance/A股单标的环境层设计.md)
- [A 股单标的风控层设计](strategies/dynamic-balance/A股单标的风控层设计.md)
- [A 股单标的仓位调节层设计](strategies/dynamic-balance/A股单标的仓位调节层设计.md)

### Phase E：当前双仓策略 Step

实现当前策略，但不要把通用 A 股规则写进策略内部。

1. 参数解析。
2. RuntimeState。
3. 动态基准。
4. 低位评分/高位评分/趋势状态机。
5. Sigmoid 动态天平。
6. core/swing 目标拆分。
7. TradeIntent 输出。
8. on_order / on_trade 事件回调。

验收依据：[A 股单标的动态天平双仓策略](strategies/dynamic-balance/A股单标的动态天平双仓策略.md)。

### Phase F：OrderSizer + 后置订单风控

把策略意图转换为合法订单。

1. 目标仓位/金额/股数转订单。
2. 100 股整数倍。
3. 零股清仓。
4. 现金和冻结。
5. T+1 可卖量。
6. T+1 库存置换。
7. 涨跌停、停牌、交易时段。
8. 后置 `OrderRiskDecision`。

验收依据：[A 股三层协作与执行契约](contracts/A股三层协作与执行契约.md)与[A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md)。

### Phase G：BacktestBroker

实现 GA 和回测所需的保守撮合器。

1. EOD 日线撮合。
2. 1m 分钟撮合。
3. T+1 模拟。
4. 涨跌停/停牌。
5. 成本模型。
6. 部分成交。
7. bucket 归因。
8. Ghost DCA A 股基准。
9. 成交约束统计。

验收依据：[A 股回测 Broker 与成交撮合契约](contracts/A股回测Broker与成交撮合契约.md)。

### Phase H：miniQMT QMT Agent

实现实盘执行端。

1. 连接 SaaS。
2. 本地保护检查。
3. miniQMT 下单。
4. 委托查询。
5. 成交查询。
6. 账户/持仓快照。
7. DeltaReport 上报。
8. 重连后完整对账。

验收依据：[系统架构设计](../architecture/系统架构设计.md)、[A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md)。

### Phase I：GA 进化与冠军复核

在回测 broker 稳定后再做。

1. 构造 A 股多窗口坩埚。
2. Ghost DCA 基准。
3. 当前双仓策略 EvolvableStrategy。
4. 参数边界和不可进化硬约束。
5. 适应度加入成交约束统计。
6. challenger/champion 人工晋升。

验收依据：[进化文档](../research/进化文档.md)与[A 股回测 Broker 与成交撮合契约](contracts/A股回测Broker与成交撮合契约.md)。

---

## 3. 当前双仓策略与通用交易域的分工

### 3.1 当前策略负责

```text
动态基准
趋势状态
低位评分
高位反转评分
BalanceSignal
Sigmoid TargetTotalPct
core/swing 目标拆分
grid index
TradeIntent 输出
算法 pending 状态
```

### 3.2 通用交易域负责

```text
A 股交易时段
交易日历
涨跌停
停牌
ST/退市风险
100 股整数倍
零股清仓
现金/冻结
可卖量
T+1
库存置换
订单状态机
成交回报
bucket 账本
公司行为
回测撮合
审计追踪
```

### 3.3 禁止交叉

策略不得实现：

```text
miniQMT 查询
真实现金修正
真实持仓修正
可卖量判断
订单生命周期
假成交标记
公司行为账本调整
```

通用交易域不得实现：

```text
动态基准公式
BalanceSignal 公式
低位评分公式
高位评分公式
具体策略买卖意图
```

---

## 4. 最小可开发闭环

若要尽快形成 v1，可以按以下最小闭环实现：

```text
1. 日线数据 + 交易日历 + 涨跌停 + 停牌
2. PortfolioState + BucketLedger
3. EnvironmentLayer 日线版
4. ContextRiskCaps 日线版
5. PositionAdjustmentProfile 日线版
6. 双仓策略日线 Step，只输出 core 意图
7. OrderSizer + OrderRiskLayer
8. BacktestBroker EOD 保守撮合
9. DecisionTrace
```

然后再扩展：

```text
10. 分钟线
11. swing 网格
12. T+1 库存置换
13. miniQMT QMT Agent
14. 公司行为完整处理
15. GA 多窗口进化
```

---

## 5. 关键测试矩阵

| 模块 | 最关键测试 |
|---|---|
| 数据源 | 缺涨跌停/停牌时实盘拒单 |
| 环境层 | 大盘 PANIC 不允许 aggressive |
| 前置风控 | RISK_OFF 降低 max_position_pct |
| 仓位调节 | profile MaxPct 不突破 RiskContextCaps |
| 策略 | BUILDING_CORE 普通网格不得卖 core |
| OrderSizer | 买入按 100 股修正，零股只允许清仓 |
| 后置风控 | T+1 无老仓拒绝，有老仓生成置换 |
| 订单状态机 | 部分成交只更新部分 bucket |
| bucket 账本 | locked_core 方向性卖出被阻止 |
| 回测 broker | 一字涨停买入不成交，一字跌停卖出不成交 |
| 公司行为 | 送转股按 bucket 比例调整 |
| 对账 | broker 与账本不一致进入 RECONCILE_REQUIRED |
| GA | 不可进化参数不能突破 A 股硬规则 |

---

## 6. 代码目录建议

```text
internal/ashare/domain/
    enums.go
    market_context.go
    risk_caps.go
    position_profile.go
    trade_intent.go
    order_state.go
    portfolio_state.go
    bucket_ledger.go
    market_rules.go
    decision_trace.go

internal/ashare/data/
    provider_adapter.go
    calendar.go
    limit_price.go
    security_status.go
    corporate_action.go
    point_in_time_adjustment.go

internal/ashare/layers/
    environment.go
    context_risk.go
    position_adjustment.go
    order_risk.go

internal/ashare/execution/
    order_sizer.go
    order_router.go
    runtime_state_manager.go
    t1_substitution.go
    reconcile.go

internal/ashare/backtest/
    broker.go
    eod_matcher.go
    minute_matcher.go
    cost_model.go
    ghost_dca.go
    fill_stats.go

internal/strategies/ashare_dynamic_balance_dual_bucket/
    manifest.go
    params.go
    state.go
    benchmark.go
    signal.go
    balance_engine.go
    grid.go
    step.go
    events.go
```

目录名可按现有项目习惯调整，但职责边界不应改变。

---

## 7. 开发时的阅读顺序

### 做 StrategyBase / 执行器迁移

先读：

1. [A 股动态天平双仓策略实现落地规格与迁移计划](../plans/A股动态天平双仓策略实现落地规格与迁移计划.md)
2. [A 股三层协作与执行契约](contracts/A股三层协作与执行契约.md)
3. [A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md)

### 做策略公式

先读：

1. [A 股单标的动态天平双仓策略](strategies/dynamic-balance/A股单标的动态天平双仓策略.md)
2. [A 股单标的仓位调节层设计](strategies/dynamic-balance/A股单标的仓位调节层设计.md)
3. [A 股三层协作与执行契约](contracts/A股三层协作与执行契约.md)

### 做风控和订单

先读：

1. [A 股三层协作与执行契约](contracts/A股三层协作与执行契约.md)
2. [A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md)
3. [A 股单标的风控层设计](strategies/dynamic-balance/A股单标的风控层设计.md)

### 做数据

先读：

1. [A 股数据源与公司行为契约](contracts/A股数据源与公司行为契约.md)
2. [A 股单标的环境层设计](strategies/dynamic-balance/A股单标的环境层设计.md)
3. [系统架构设计](../architecture/系统架构设计.md)

### 做回测和 GA

先读：

1. [A 股回测 Broker 与成交撮合契约](contracts/A股回测Broker与成交撮合契约.md)
2. [进化文档](../research/进化文档.md)
3. [A 股单标的动态天平双仓策略](strategies/dynamic-balance/A股单标的动态天平双仓策略.md)

### 做 miniQMT 实盘

先读：

1. [系统架构设计](../architecture/系统架构设计.md)
2. [A 股交易域数据结构与状态机](contracts/A股交易域数据结构与状态机.md)
3. [A 股三层协作与执行契约](contracts/A股三层协作与执行契约.md)

---

## 8. 最终验收原则

系统能否进入实盘前测试，至少看五点：

1. **策略纯净：** 策略只输出意图，不碰真实交易状态。
2. **成交真实：** 实盘只信 miniQMT，回测模拟同构状态流。
3. **账本守恒：** PortfolioState 与 BucketLedger 永远满足不变量。
4. **约束保守：** 数据缺失、状态异常、涨跌停、停牌、T+1 都不会被乐观绕过。
5. **可解释：** 每次不买、少买、卖出、拒单、熔断，都能从 DecisionTrace 找到原因。
