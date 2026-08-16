# Mutation 工作流

## 调用前

- 从当前会话读取权限和授权账户。
- 在 v2 operation policy 中确认全部权限、适用端、稳定性和风险。
- 对交易操作展示账户、方向、数量、价格类型和风险确认，不用隐藏按钮代替授权。
- 提供字段支持的幂等键、版本号或确认挑战，禁止客户端自行绕过。

## 调用后

普通配置 Mutation 可以使用返回对象更新界面；异步、策略和交易 Mutation 必须继续
查询或订阅服务端状态。交易链路是：

```text
pending order + outbox
  -> Agent command
  -> command_ack（仅投递）
  -> order / execution / delta report
  -> inbox
  -> Engine 收敛
```

因此 Mutation 成功、命令排队或 `command_ack` 都不是成交。只有 QMT 委托与成交
回报经 inbox 持久化并由 Engine 收敛后，订单、成交和持仓状态才可推进。

## 失败与恢复

- `UNAUTHENTICATED`：串行刷新一次；失败则退出。
- `FORBIDDEN`：重新读取会话，禁止自动提权或换账户重试。
- 版本冲突：重新读取对象后由用户确认新状态。
- 网络超时：使用幂等键或查询接口确认结果，交易写入不得盲目重发。
- 风控或 readiness 拒绝：展示服务端原因并保留 request ID，不在客户端降级门禁。
