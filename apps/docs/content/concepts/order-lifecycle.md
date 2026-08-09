# 委托与成交状态

客户端必须把策略意图、指令投递、券商受理和真实成交视为不同事实。

## 状态链路

```text
TradeIntent
  -> 服务端风控与 OrderSizer
  -> pending Order + outbox
  -> Agent command
  -> command_ack
  -> miniQMT 委托回报
  -> order_report / execution_report
  -> inbox 持久化
  -> Engine 收敛订单、成交、持仓与 bucket
```

## 展示规则

| 状态 | 可以表达 | 不可以表达 |
| --- | --- | --- |
| pending / queued | 已排队、等待下发 | 已报、已成交 |
| `command_ack` | Agent 已收到指令 | 券商已受理、成交 |
| accepted / submitted | 券商已报 | 成交完成 |
| `partial_filled` | 已成交指定部分数量 | 全部成交 |
| filled | miniQMT 已确认全部成交 | — |
| rejected / canceled | 拒单或已撤 | 自动推断资金/持仓已完全恢复 |

所有部分成交页面同时显示委托数量、已成交数量、剩余数量、最后更新时间和
成交明细。

## 成交真源

- 策略信号不是成交。
- GraphQL Mutation 成功不是成交。
- `command_ack` 只表示投递。
- miniQMT 委托与成交回报经 inbox 持久化、Engine 收敛后，才可以改变真实
  订单、成交和持仓状态。
- Agent 断线或服务端重启时，客户端不得把 pending 自动改成成功或失败。

## bucket 名称

内部归因与用户展示：

| 内部值 | 客户端文案 |
| --- | --- |
| `locked_core` | 封存仓 |
| `core` | 核心仓 |
| `swing` | 活跃仓 |

bucket 是算法归因，真实持仓、可卖量和 T+1 约束始终以 miniQMT 回报为准。
