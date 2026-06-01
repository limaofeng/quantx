**文档目标:** 定义 A 股单标的策略的风控层。风控层读取交易意图、组合状态、A 股交易规则、环境快照、订单状态和 miniQMT 成交回报，产出确定性的 `RiskContextCaps` 与 `OrderRiskDecision`。风控层可以允许、限额、延迟、拒绝或触发熔断，但不生成交易意图，不修改策略算法状态。

---

## 0. 核心定位

风控层回答一个问题：**这个交易意图在当前真实市场和账户状态下是否允许执行？**

风控层不是策略，不判断“要不要买这只股票”。策略已经给出了意图，风控层只判断：

- 是否符合 A 股交易规则
- 是否符合账户和持仓约束
- 是否符合环境风险约束
- 是否符合订单生命周期和成交回报事实
- 是否需要限额、延迟、拒绝或熔断

风控层严禁：

- 生成新的买卖方向
- 把拒单伪装成成交
- 修改策略网格成交状态
- 绕过 miniQMT 成交回报
- 用 AI 决策覆盖确定性规则

---

## 1. 输入与输出

风控层分为两个阶段：**前置风险上限** 与 **后置订单风控**。这样既能在策略计算前限制仓位空间，又能在具体订单生成后校验 A 股真实交易约束。

### 1.1 前置风控输入

前置风控输入：

- `PortfolioState`
- `PositionBucketState`
- `MarketContextSnapshot`
- `AshareMarketRules`
- `OrderState` 摘要
- `BrokerExecutionReport` 摘要
- 策略实例风险配置

前置风控不需要 `TradeIntent`，因为它发生在策略输出意图之前。

### 1.2 前置风控输出：`RiskContextCaps`

```json
{
  "risk_mode": "RISK_REDUCED",
  "kill_switch_active": false,
  "max_position_pct": 0.50,
  "max_new_buy_pct_today": 0.04,
  "max_new_buy_amount_today": 20000,
  "min_cash_buffer_pct": 0.30,
  "allow_buy": true,
  "allow_sell": true,
  "allow_intraday_swing_buy": false,
  "only_reduce_position": false,
  "reason_codes": ["RISK_CONTEXT_CAP"],
  "risk_tags": ["market_risk_off"]
}
```

`RiskContextCaps` 供仓位调节层和策略读取，用于约束动态天平边界、现金缓冲和买入活跃度。

### 1.3 后置订单风控输入

后置订单风控输入：

- `TradeIntent`
- `OrderRequest`
- `PortfolioState`
- `PositionBucketState`
- `MarketContextSnapshot`
- `RiskContextCaps`
- `AshareMarketRules`
- `OrderState`
- `BrokerExecutionReport`
- 策略实例风险配置

### 1.4 后置订单风控输出：`OrderRiskDecision`

```json
{
  "action": "CAP",
  "allowed": true,
  "final_amount": 8000,
  "final_volume": 800,
  "reason_code": "POSITION_LIMIT_CAP",
  "reason_detail": "target order exceeds max position under RISK_OFF context",
  "risk_tags": ["market_risk_off", "max_position_reduced"],
  "substitution_plan": null
}
```

`action` 取值：

| action | 含义 |
|---|---|
| `ALLOW` | 允许按原订单继续 |
| `CAP` | 允许但降低金额、数量或目标仓位 |
| `DELAY` | 暂不执行，等待可卖量、交易时段或环境恢复 |
| `REJECT` | 拒绝本次订单 |
| `KILL_SWITCH` | 触发实例级风控，暂停策略交易 |



通用字段、订单状态机、bucket 不变量和 T+1 置换提交/回滚规则见：

```text
A股三层协作与执行契约.md
A股交易域数据结构与状态机.md
```

---

## 2. 基础 A 股规则

### 2.1 交易时段

实盘下单必须位于允许交易时段。

默认：

- 09:15 - 09:25：集合竞价，谨慎支持
- 09:30 - 11:30：连续竞价
- 13:00 - 15:00：连续竞价
- 其他时间：拒绝或延迟

是否允许集合竞价由实例配置控制。v1 推荐只允许连续竞价。

### 2.2 数量规则

- 买入必须为 100 股整数倍
- 普通卖出以 100 股整数倍为主
- 清仓时允许卖出零股
- 不满足最小成交金额时拒绝
- 超过最大申报数量时拆单或限额

### 2.3 价格规则

必须校验：

- price tick
- 涨停价
- 跌停价
- 停牌状态
- 是否一字涨停或一字跌停
- 是否存在有效盘口或可成交量

实盘 miniQMT 下单后，成交结果只以委托和成交回报为准。

---

## 3. T+1 与库存置换

### 3.1 基础 T+1

A 股限制今日买入股份当日卖出。风控层必须区分：

- `total_volume`
- `available_volume`
- `today_buy_volume`
- `frozen_volume`
- bucket 级别的可卖量

当 `swing` 当日买入后触发止盈，不能直接卖出今日买入的 swing 股份。

### 3.2 库存置换顺序

同一股票库存可替换。若账户中存在同标的可卖老仓，允许做 T+1 库存置换。

默认顺序：

1. 使用 `swing_available`
2. 使用 `core_available`
3. 若 `allow_locked_core_substitution = true`，使用 `locked_core_available`
4. 不足部分 `DELAY` 或 `REJECT`

### 3.3 置换计划

风控层输出 `substitution_plan`：

```json
{
  "enabled": true,
  "sell_from_bucket": "core",
  "reattribute_buy_to_bucket": "core",
  "volume": 800,
  "reason": "swing_t0_sell_with_core_inventory"
}
```

语义：

- 实际卖出老的可卖股份
- 将今日 swing 买入股份归因到被置换 bucket
- 被置换 bucket 总量不下降
- 可卖性转移到今日买入批次，次交易日恢复

### 3.4 回滚规则

- 卖单拒单：置换计划全部回滚
- 卖单撤单：未成交部分回滚
- 部分成交：只按成交数量完成置换
- 外部成交回报延迟：保持 pending，不更新策略网格成交状态

---

## 4. 环境风险约束

风控层读取环境层输出。

| 环境 | 风控动作 |
|---|---|
| `RISK_ON` | 默认放行，仍检查账户规则 |
| `NEUTRAL` | 默认放行 |
| `RISK_OFF` | 限制买入金额，提高现金缓冲 |
| `PANIC` | 禁止 swing 买入，必要时延迟 core 买入 |
| 行业 `BROKEN` | 禁止高置信低吸，限制新增仓位 |
| 流动性 `DRY` | 拒绝大额订单或延迟 |

环境风险不直接强制卖出，但可以：

- 拒绝新增买入
- 限制下单金额
- 提高可成交性要求
- 触发防御 profile

---

## 5. 回撤与熔断

风控层维护实例级风险阈值。

触发 `KILL_SWITCH` 的情况：

- 单标的持仓连续跌停且无法卖出
- 实例净值回撤超过最大允许回撤
- miniQMT 长时间无成交/委托回报
- 账户状态与本地 RuntimeState 严重不一致
- 停牌、ST、退市风险标签触发强保护

熔断后：

- 禁止新增买入
- 保留已有卖出意图的人工确认入口
- 记录审计日志
- 等待人工恢复或环境修复

---

## 6. miniQMT 成交真源

实盘路径必须只信 miniQMT。

规则：

- 下单成功不等于成交
- 已报不等于成交
- 部分成交只按成交数量更新
- 废单、拒单、撤单必须释放冻结
- 策略不得在发出信号后自行标记 grid filled

状态流：

```text
TradeIntent
  -> RiskDecision
  -> OrderRequest
  -> miniQMT order accepted/rejected
  -> miniQMT execution report
  -> RuntimeStateManager
  -> strategy on_order/on_trade
```

回测和仿真 broker 必须模拟这个状态流，不能直接把信号当成交。

---

## 7. 回测与仿真风控

回测/仿真必须防止假成交。

强规则：

- 涨停买入默认不成交，除非盘口/成交量数据证明打开
- 跌停卖出默认不成交，除非盘口/成交量数据证明打开
- 停牌不成交
- 非交易时段不成交
- 日线 high/low 触达不代表一定成交，需按配置撮合
- 成交量不足时必须部分成交或不成交

这部分是回测真实性要求，实盘由 miniQMT 回报天然约束。

---

## 8. 结构化原因码

风控层必须返回结构化 reason。

常用 reason：

| reason_code | 含义 |
|---|---|
| `SUSPENDED` | 停牌 |
| `OUT_OF_SESSION` | 非交易时段 |
| `LIMIT_UP_BUY_BLOCKED` | 涨停买入受限 |
| `LIMIT_DOWN_SELL_BLOCKED` | 跌停卖出受限 |
| `T1_UNAVAILABLE` | T+1 不可卖 |
| `T1_SUBSTITUTION_APPLIED` | 已使用库存置换 |
| `INSUFFICIENT_CASH` | 现金不足 |
| `INSUFFICIENT_POSITION` | 持仓不足 |
| `POSITION_LIMIT_CAP` | 仓位上限限额 |
| `RISK_CONTEXT_CAP` | 环境风险限额 |
| `LOW_LIQUIDITY` | 流动性不足 |
| `KILL_SWITCH_TRIGGERED` | 熔断 |

---

## 9. 与其他层的契约

### 9.1 对环境层

风控层只读取环境快照，不重新计算大盘或行业状态。

### 9.2 对仓位调节层

风控层可以输出：

- 是否进入防御 profile
- 最大允许买入金额
- 最大允许目标仓位
- 是否禁止 swing 买入
- 是否只允许卖出

### 9.3 对策略层

策略层消费风控结果和订单结果，但不得自行修正真实账户状态。

---

## 10. 测试计划

- 停牌拒绝
- 非交易时段拒绝或延迟
- 涨停买入拒绝或不成交
- 跌停卖出拒绝或不成交
- T+1 无老仓时延迟或拒绝
- T+1 有 core 老仓时输出置换计划
- `allow_locked_core_substitution=false` 时不得使用 locked_core
- `allow_locked_core_substitution=true` 时可使用 locked_core_available，但封存总量不下降
- miniQMT 未成交时不得更新网格成交状态
- 部分成交只更新部分数量
- 拒单释放冻结并回滚置换
- PANIC 环境禁止 swing 买入
- 最大回撤触发 KILL_SWITCH


---

## 11. 开发落地补齐

风控层的公共执行细节，以 `A股三层协作与执行契约.md`、`A股回测Broker与成交撮合契约.md` 和 `A股交易域数据结构与状态机.md` 为准。

### 11.1 双阶段职责边界

| 阶段 | 发生时机 | 输出 | 不做什么 |
|---|---|---|---|
| 前置风控 | 策略 `Step()` 前 | `RiskContextCaps` | 不校验具体股数，不做 T+1 置换 |
| 后置订单风控 | `OrderSizer` 后 | `OrderRiskDecision` | 不生成买卖信号，不假定成交 |

### 11.2 订单状态机接入

风控层必须识别以下订单状态：

```text
INTENT_CREATED -> SIZED -> POST_RISK_ALLOWED/CAPPED/DELAYED/REJECTED
-> SUBMITTED -> ACCEPTED -> PARTIALLY_FILLED/FILLED/CANCELED/REJECTED/EXPIRED
-> RECONCILED
```

任何非成交状态都不得触发策略网格成交更新。

### 11.3 BucketLedger 接入

风控层输出库存置换计划时，只是计划，不是账本修改。只有当 `BrokerExecutionReport` 确认卖出成交后，`BucketLedger` 才能应用置换。

### 11.4 当前双仓策略的额外硬约束

- `BUILDING_CORE` 阶段普通 swing 卖出不得方向性消耗 core。
- swing 当日买入后触发卖出，只能通过合法老仓置换。
- `locked_core` 默认不参与置换，除非实例显式开启。
- 高位出货可以卖 core，但必须经过后置风控和可卖量校验。

---

## 12. 与通用 A 股交易域契约的关系

本文定义当前单标的策略的风控规则。工程落地时，风控层必须拆分为两段：

```text
前置上下文风控 RiskContextCaps
    在策略 Step() 前执行，输出最大仓位、最大买入额、是否禁买、是否防御、是否熔断。

后置订单风控 OrderRiskDecision
    在 TradeIntent 和 OrderSizer 之后执行，校验具体订单的交易时段、停牌、涨跌停、T+1、现金、可卖量、100 股规则和库存置换。
```

订单状态机、bucket 账本不变量、T+1 置换生命周期、原因码和审计事件，以以下通用文档为准：

- `A股三层协作与执行契约.md`
- `A股交易域数据结构与状态机.md`
- `A股回测Broker与成交撮合契约.md`

风控层不得生成新的策略方向，不得把拒单、撤单、已报或部分成交伪装成完整成交。

---

## 13. 当前工程落地状态

已落地到代码主路径：

- `RiskContextCaps`：前置风控快照，字段覆盖风险模式、熔断、最大仓位、新增买入额度、现金缓冲、买卖许可、swing 买入许可和 T+1 置换开关。
- `ContextRiskLayer`：在 `StrategyExecutor` 构造 `StrategyInput` 前运行，读取组合快照、`market_context`、策略参数和运行状态，输出 `risk_caps`。
- `OrderRiskLayer`：在 `OrderSizer` 后运行，输出结构化 `OrderRiskDecision`，支持 `ALLOW`、`CAP`、`DELAY`、`REJECT`、`KILL_SWITCH`。
- `OrderRiskDecision.substitution_plan`：当 swing 当日仓无法直接卖出但存在同标的 core 老仓时，输出 T+1 库存置换计划。
- `StrategyExecutor`：只对 `ALLOW` / `CAP` 决策继续冻结资源并下发 broker；`DELAY` / `REJECT` / `KILL_SWITCH` 只回调结构化订单事件，不进入 broker。

仍由后续账本模块接管：

- `BucketLedger` 对置换计划的提交、部分成交提交、拒单/撤单回滚。
- 独立 `SubstitutionPlan` 持久状态机。
- miniQMT 长时间无回报的真实心跳监控服务。当前已预留 `broker_report` / `order_state` 输入和熔断字段。
