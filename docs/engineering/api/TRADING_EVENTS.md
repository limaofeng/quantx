# 交易事件与状态收敛

交易事件跨进程流转遵循持久化优先：

1. Engine 持久化 pending 订单和 `TradeCommand` outbox。
2. API Agent Hub 下发命令。
3. Agent 返回 `command_ack`，仅更新投递状态。
4. Agent 上报委托、成交、delta 或快照。
5. API 先按幂等键写入 report inbox，再返回 `report_ack`。
6. Engine 消费 inbox，推进订单、Portfolio、bucket 和审计。

重复、乱序和重连不能生成重复订单。Redis 事件仅用于唤醒与订阅广播，
数据库轮询是恢复路径。
