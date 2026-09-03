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

当前 Agent 控制协议唯一为 `1.2`。业务 owner 不在 Agent wire payload 中传输；
API/Engine 以 `ExecutionOwnerRef(owner_type, owner_id)` 和 execution environment
贯穿 intent、pending、correlation、`trade_command_outbox`、runtime event 及退出计划
source，并在报告收敛前做精确 owner/environment 校验。当前可路由 owner 只有
`STRATEGY_RUN`、`EXIT_PLAN`、`MANUAL_COMMAND`；未知、未注册或冲突 owner fail-closed。
`PLACE_ORDER` wire 固定 10 字段，`CANCEL_ORDER` 固定 6 字段；PLACE 仅允许
`BUY/SELL`、`FIX_PRICE` 和有限正数 `limit_price`。QMT `strategy_name` 固定为空，
remark 为 `qx:` 加 client order id 前 20 字符。
