# QuantX Engine

`apps/engine` 独占策略管理器、自动退出计划、条件清仓、全局做 T、热缓存和
Agent 回报收敛。它使用 PostgreSQL advisory lock 保证同数据库只运行一个
实例，并定期写入组件心跳。

Engine 从 `engine_command_outbox` 和 `agent_report_inbox` 恢复消费：
前者承载 API 发起的策略、做 T 和清仓控制命令，后者承载 Agent 上报的原始
订单、成交、持仓与对账结果。进程重启后会恢复超时的 `PROCESSING` 消息，
并继续从数据库推进。

Redis 只用于唤醒消费者，以及向 API 发布行情、策略与交易事件的订阅通知，
不能作为订单、成交、Portfolio 或 bucket 的状态真源。API 收到交易事件
唤醒后仍会从数据库重新读取投影。订单必须先持久化 pending 状态和
`trade_command_outbox`，才能由 API Hub 下发给 Agent。

自动卖出由 Engine 的 `ExitPlanBook` 统一承载。入场策略在 BUY 意图中附带
`ExitPlanTemplate`，只有真实 BUY 成交回报会激活计划。Engine 在策略
`step()` 之前评估退出规则，将命中的计划转换成标准 SELL `TradeIntent`，
继续经过 OrderSizer、后置风控、Broker 和成交回报收敛。做 T 仅负责入场
信号和退出模板，不再维护独立的自动卖出主路径。完整契约见
[A 股自动退出计划与卖出策略契约](../../trading/contracts/A股自动退出计划与卖出策略契约.md)。

Engine 使用 PostgreSQL advisory lock 保证同一数据库只有一个实例取得执行
权，并持续写入 `runtime_component_heartbeats`，供 API 就绪检查使用。
