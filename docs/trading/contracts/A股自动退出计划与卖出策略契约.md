# A 股自动退出计划与卖出策略契约

## 1. 目标

自动卖出是 Engine 的公共交易能力，不属于做 T、打板或某个具体买入策略。
任何入场功能只负责回答“为什么买、买什么”，成交后由统一的
`ExitPlan` 回答“何时卖、卖多少、如何遵守 T+1、如何委托”。

公共能力必须满足：

- 做 T、打板、趋势、网格和条件清仓可以组合不同卖出策略。
- 只有真实买入成交才能激活退出计划，买入意图和 `command_ack` 均不能激活。
- 卖出必须生成标准 `TradeIntent`，继续经过 OrderSizer、后置风控、Broker
  和成交回报收敛。
- 策略状态与退出计划状态分离持久化，策略参数升级不得丢失已成交数量、
  峰值、追踪止盈底线和待成交卖单。
- Engine 重启后可以从 `StrategyRunState.custom_state` 恢复未完成计划。

## 2. 四层卖出策略

一个 `ExitPlanTemplate` 由四类可组合策略组成。

### 2.1 触发策略 `ExitRuleSpec`

触发策略只判断当前是否应该退出，不计算最终合法卖量。

内置策略：

| 策略 | 用途 |
| --- | --- |
| `TARGET_PRICE` | 到达绝对目标价 |
| `STOP_PRICE` | 跌破绝对止损价 |
| `GROSS_TAKE_PROFIT` | 达到毛收益率 |
| `NET_TAKE_PROFIT` | 扣除双边费用后达到净收益率 |
| `TRAILING_NET_PROFIT` | 净收益达到门槛后按动态底线回撤退出 |
| `TRAILING_PRICE_DRAWDOWN` | 从持仓后峰值价格回撤退出 |
| `HARD_STOP` | 按成本收益率强制止损 |
| `TIME_OF_DAY` | 到达日内指定时点退出 |
| `MAX_HOLDING_DAYS` | 达到最大交易日持有期退出 |

同一计划可以配置多条规则。若同一时刻多条规则触发，优先级最高的规则胜出。
规则可以配置 `once=true`，用于分批止盈后不再重复执行同一阶段。

`ExitStrategyRegistry` 使用字符串注册规则，不使用封闭枚举限制业务扩展；
Engine 通过 `StrategyExecutor.register_exit_strategy()` 统一注册，并在运行恢复
时复用同一个注册表。
例如打板功能可以注册 `LIMIT_UP_BREAK`、`AUCTION_WEAKNESS` 或
`OPEN_BOARD_TIMEOUT`，无需修改 `ExitPlanBook` 状态机。

### 2.2 卖出数量策略 `ExitSizingPolicy`

规则触发后，数量策略只基于计划剩余数量计算目标卖量：

- `ALL_REMAINING`：全部剩余数量。
- `PERCENT_REMAINING`：按剩余数量百分比分批退出。
- `FIXED_VOLUME`：固定数量退出。

数量策略负责整手取整和清仓零股表达。真实可卖量、冻结量、资金、涨跌停、
停牌和最终合法数量仍由交易域、OrderSizer、风控与 Broker 决定。

### 2.3 T+1 策略 `ExitT1Policy`

每个退出计划必须显式选择：

- `WAIT_UNTIL_SELLABLE`：当日买入不可卖，等待可卖日；适合打板、趋势持仓。
- `ALLOW_SAME_INSTRUMENT_SUBSTITUTION`：允许使用同标的昨日可卖库存置换；
  仅适合正向做 T。
- `REJECT_IF_UNSELLABLE`：不可卖时拒绝，不自动等待。

T+1 置换不得是系统隐式默认行为。卖出意图会携带
`allow_t1_substitution` 和 `t1_insufficient_action`，由统一风控执行。

### 2.4 委托执行策略 `ExitExecutionPolicy`

执行策略描述触发后的委托偏好：

- 价格参考：买一、卖一、最新价或限价。
- 订单类型：限价或受支持的其他 Broker 类型。
- 最大退出滑点。
- `AUTO` 或 `MANUAL_CONFIRM` 授权模式。

实盘自动卖出必须具有显式 `auto_exit_authorized=true`。未授权的自动计划会
被 Engine 降级为人工确认，不得由入场策略绕过。

## 3. 生命周期

```text
入场策略输出 BUY TradeIntent + ExitPlanTemplate
  -> OrderSizer / Risk / Broker
  -> 真实 BUY TradeExecutionEvent
  -> ExitPlanBook.register_entry_fill()
  -> ACTIVE
  -> 行情到达，Engine 统一评估 ExitRuleSpec
  -> 生成 SELL TradeIntent
  -> OrderSizer / Risk / Broker
  -> 真实 SELL TradeExecutionEvent
  -> PARTIALLY_EXITED / COMPLETED
```

主要状态：

- `PENDING_ENTRY`：只有模板，尚无真实买入成交。
- `ACTIVE`：已有受保护数量，等待卖出规则触发。
- `EXIT_PENDING`：卖出意图、委托或成交回报尚未收敛。
- `PARTIALLY_EXITED`：已部分退出，仍有剩余数量。
- `COMPLETED`：计划数量全部退出。
- `PAUSED / CANCELLED / ERROR`：人工或异常状态。

委托回报和成交回报可能乱序。`FILLED` 委托先到时，计划必须保留 pending
上下文，直到对应成交数量收敛后才完成一次性规则并释放 pending，防止重复
卖出。

## 4. 状态所有权

`StrategyBase` 只拥有信号和入场业务状态。Engine 的 `ExitPlanBook` 使用
`auto_exit_plan_book` 系统键独立持久化，不注入策略自有状态快照。

策略可以：

- 在 BUY `TradeIntent.metadata.exit_plan_template` 中附带退出模板。
- 从 `StrategyInput.exit_plans` 读取只读投影，用于 UI 和自身业务状态展示。
- 输出 `ExitPlanCommand` 更新规则、暂停、恢复或取消计划。

策略不得：

- 直接把行情触发当成卖出成交。
- 修改计划的真实已成交数量。
- 自行计算真实可卖量或绕过统一风控。
- 因入场功能停止而静默丢弃仍有剩余数量的退出计划。

承载活跃退出计划的运行必须保持退出监控。Engine 会拒绝普通暂停或停止
请求；产品层停止入场功能时，应进入 `DRAINING`，停止新 BUY，待计划完成
后再停止运行。仅 Engine 进程关闭时允许强制释放运行，计划状态已经持久化并
将在恢复后继续。做 T 服务也会拒绝停止仍有活跃批次的运行。

## 5. 当前功能映射

### 5.1 正向做 T

入场：

- 回撤后企稳反弹信号。
- 手工确认买入。

退出计划：

- `TRAILING_NET_PROFIT`。
- 可选 `HARD_STOP`。
- 可选 `TIME_OF_DAY` 或 `MAX_HOLDING_DAYS`。
- `ALL_REMAINING`。
- `ALLOW_SAME_INSTRUMENT_SUBSTITUTION`。

做 T 策略不再直接输出自动 SELL。它只创建/更新计划并消费退出投影，SELL
由 Engine 公共运行时生成。

### 5.2 条件清仓

现有持仓级条件清仓通过适配器复用：

- 目标收益率映射为 `GROSS_TAKE_PROFIT`。
- 目标价格映射为 `TARGET_PRICE`。
- 全部、百分比、固定数量映射为 `ExitSizingPolicy`。

其触发和数量语义与退出计划一致；旧 API 与订单模型继续作为兼容入口。

### 5.3 打板

`AshareLimitUpBoardStrategy` 在临近涨停、尚未封死时生成一次 `swing` BUY
意图；已经封板、缺少涨停价、一字板、风控禁买、已有持仓或已有活跃退出
计划时保守观望。真实买入成交后创建独立模板：

```text
LIMIT_UP_BREAK(priority=1000, sizing=ALL_REMAINING)
TRAILING_PRICE_DRAWDOWN(priority=700, sizing=PERCENT_REMAINING)
MAX_HOLDING_DAYS(priority=600, sizing=ALL_REMAINING)
T1=WAIT_UNTIL_SELLABLE
execution=BID_PROTECTED_LIMIT
```

打板模块只新增自身触发策略和模板配置，不复制订单状态机、T+1、自动授权、
持久化或成交收敛代码。

## 6. 审计要求

每次自动退出必须能从意图与状态中还原：

- `exit_plan_id`、`exit_rule_id`、规则类型和触发原因。
- 来源功能、来源批次、策略运行和配置版本。
- 触发时价格、净收益、峰值、追踪底线和持有交易日。
- 请求数量、已成交数量、剩余数量。
- T+1 策略、风险动作、执行价格参考和授权模式。

被延迟、拒绝、取消、部分成交和重试都必须保留原计划，不得伪造为已退出。
