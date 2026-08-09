# Engine 执行架构

策略执行已从 API 进程迁移到独立 `apps/engine`。

```text
StrategyInput
  -> StrategyBase.step
  -> TradeIntent
  -> application orchestration / risk / sizing
  -> pending Order + TradeCommand outbox
  -> API Agent Hub
  -> QMT Agent
  -> report inbox
  -> Engine convergence
```

Engine 使用 PostgreSQL 租约保证本机只有一个活跃实例。API、Worker 或
Agent 重启不会终止 Engine；Engine 重启后从 outbox/inbox 和业务表恢复。

实现与运行说明见 [../engine/README.md](../engine/README.md)，交易不变量见
[../../trading/contracts/A股三层协作与执行契约.md](../../trading/contracts/A股三层协作与执行契约.md)。
