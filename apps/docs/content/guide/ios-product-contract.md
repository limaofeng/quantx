# iOS 产品契约

QuantX iOS 的目标产品是 **个人 A 股量化移动控制中心**，不是只读监控器。它面向
本人单一主账户，通过 TestFlight 和 VPN/私网使用，底部固定为“今日、行情、交易、
量化、资产”。

完整权威规格保存在仓库
[`docs/product/ios/`](https://github.com/limaofeng/quantx/tree/main/docs/product/ios)：

- PRD 与稳定需求 ID；
- 信息架构、交互和视觉设计系统；
- API、设备权限、交易确认与 APNs 安全契约；
- 开发路线、需求追踪矩阵和发布门禁。

旧的“只读移动监控端”计划已被取代。当前客户端已有的账户、持仓、策略、委托
成交、做 T/打板投影和受控买入批准是迁移基线，不代表目标版本已经全部完成。

## v1 能力

- 今日行动、风险、账户和量化摘要。
- 搜索、自选、持仓行情、分时/K 线、五档和逐笔。
- 手动限价及服务端明确支持的最优价买卖、撤单和订单状态追踪。
- 单只、选中、全仓与条件退出计划。
- 策略生命周期、移动安全参数、做 T、打板和 Kill Switch。
- 资产曲线、持仓归因、策略贡献、交易复盘和关键 APNs。

策略代码开发、完整回测/进化、数据源、部署、Agent 登记和券商凭证管理不进入
iOS v1。

## 能力发现

客户端必须以当前部署的
[GraphQL Schema](../reference/graphql-api/)和
[权限契约](/contracts/graphql-permissions.json)为准。目标文档中的新接口或专用
scope 未出现在发布契约前，对应功能必须保持不可用；不得调用相近的宽泛 Mutation
或使用客户端隐藏入口模拟安全边界。

当前已部署的助手买入批准使用 `trade:approve`、最长 60 秒的设备绑定挑战和
Face ID/Touch ID。发布权限 JSON 包含 `trade:manual` 时，iOS 可接入同样两阶段
的手动委托；兼容 `placeOrder` 的 `trade:direct` 永不作为移动端回退权限。确认
只让命令进入统一风控，不表示券商已报或成交。

## 交易安全

所有新增实盘风险遵守同一流程：

```text
服务端实时预览
  -> 用户核对规范化结果
  -> Face ID / Touch ID
  -> 消费一次性设备绑定挑战
  -> pending order / outbox
  -> Agent 投递
  -> QMT 委托与成交回报
  -> inbox 持久化
  -> Engine 收敛
```

iOS 不连接 QMT、不计算最终合法数量、不保存券商凭证。Mutation 成功和
`command_ack` 均不能显示为券商受理或成交。详细状态见
[委托与成交状态](../concepts/order-lifecycle)。

## 发布口径

正式产品必须统一通过全功能 paper、自动化/无障碍、五个连续交易日 TestFlight
和受控实盘灰度。真实交易永不进入普通 CI 或默认测试；尚未完成 Canary 的能力
由服务端 capability 保持关闭。
