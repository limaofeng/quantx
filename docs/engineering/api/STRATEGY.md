# 策略开发契约

## 唯一接口

所有回测与实盘策略都继承
`quantx_domain.strategies.StrategyBase`，并实现同一个
`step(StrategyInput) -> StrategyOutput` 主路径。

策略只允许输出：

- `TradeIntent[]`
- `RuntimeStatePatch`
- 决策标签与审计载荷

不得恢复旧信号或逐事件回调主路径，不得访问账户、数据库、网络、文件或
QMT。

## 状态与仓位

- 固定标的策略实例只绑定一个 `instrument_code`。
- 仓位归因使用 `locked_core`、`core`、`swing`。
- 策略不能保存真实现金、可卖量、冻结量或最终合法订单数量。
- 缺失数据保守降级；回测不得读取未来数据。
- 不买、少买、卖出、拒单和熔断都必须留下可查询的决策审计。

## 执行

`TradeIntent` 由应用编排、OrderSizer、风控与 broker 转成订单。实盘 broker
只生成持久化 `TradeCommand`；成交真源仅来自 QMT Agent 回报。

详细数据结构见
[../../trading/contracts/A股交易域数据结构与状态机.md](../../trading/contracts/A股交易域数据结构与状态机.md)。
