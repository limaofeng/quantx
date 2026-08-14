# 权限模型

QuantX 对 GraphQL 根字段默认拒绝。客户端必须同时满足根字段权限、账户/资源归属
和业务状态门禁；页面中是否显示按钮永远不是服务端授权边界。

## 当前部署权限

| 权限 | 当前能力 |
| --- | --- |
| `portfolio:read` | 账户、资产、持仓、自选与组合摘要读取 |
| `market:read` | 标的、行情、K 线、交易日历与研究数据读取 |
| `strategy:read` | 策略实例、运行状态、审计、做 T 与打板投影读取 |
| `orders:read` | 委托、成交、清仓记录和交易事件读取 |
| `system-status:read` | 服务、Agent、任务和必要运行状态读取 |
| `trade:approve` | 已支持的策略/做 T 短时交易意图预览与确认，以及受控实盘动作 |
| `trade:manual` | 两阶段移动手动委托预览/确认与撤单 |
| `trade:direct` | 兼容直写 `placeOrder`；禁止 iOS 使用 |
| `notification:manage` | 当前会话 APNs 注册、类别偏好和随机事件路由解析 |
| `system-config:write` | Web 管理端修改全局非敏感系统配置 |
| `mutation:write` | 现有大多数通用 GraphQL Mutation 的兼容权限 |

每个字段的实际映射以随当前服务发布的
[GraphQL 权限契约](/contracts/graphql-permissions.json)为准。文档中的目标 scope
没有出现在该 JSON 前，客户端不能假定服务端已经支持。

`trade:approve` 独立于 `mutation:write`。只有 `mutation:write` 的会话不能批准
策略或做 T 买入意图；预览/确认仍会检查设备会话、账户、信号和实盘门禁。

## 两层授权

请求同时满足：

1. Principal 拥有根字段要求的权限。
2. 请求中的 `accountId` 及目标资源属于 Principal 的授权账户集合。

客户端拿到账号字符串、曾缓存该账号或在 UI 中显示该账号，都不能绕过第二层。
系统配置权限也不被通用 Mutation 权限自动替代。

## iOS v1 最小权限

iOS 的产品目标已从“只读监控端”调整为个人 A 股量化控制中心。正式开放写入前，
原生会话按设备、单一主账户收缩 scope。iOS v1 专用 scope 为：

| Scope | 目标能力 |
| --- | --- |
| `watchlist:write` | 当前主账户自选维护 |
| `trade:manual` | 两阶段手动下单与撤单 |
| `trade:approve` | 设备绑定的策略/助手/退出意图确认和实盘授权 |
| `liquidation:control` | 清仓与退出计划配置、预览和控制 |
| `strategy:control` | 策略生命周期和移动安全参数 |
| `t-trade:control` | 做 T 配置、启停、忽略列表与熔断 |
| `limit-up:control` | 打板配置、候选偏好与布防 |
| `notification:manage` | 当前设备 APNs Token 与通知偏好 |

通用 `mutation:write` **不满足上述任何 iOS 写能力**。原生会话请求明确的
`requestedScopes` 和唯一 `requestedAccountId`，响应返回实际
`grantedScopes/activeAccountId`；刷新不得扩权。

::: warning 专用 Mutation 迁移限制
设备 scope 已落地，但不代表所有目标 Mutation 都已实现专用权限和交易门禁。
未出现在当前 GraphQL 权限契约的写能力仍必须关闭；不能靠隐藏按钮、
直调 `placeOrder` 或兼容清仓接口临时开放。
:::

## 高风险确认

手动实盘下单、清仓/自动退出授权、助手买入批准和进入实盘模式必须使用：

```text
服务端实时预览
  -> 设备绑定、一次性、短时确认挑战
  -> 用户核对
  -> Face ID / Touch ID
  -> 确认时重新校验全部门禁
```

生物识别是本地逐次确认，不替代服务端权限。确认 Token 只在内存短暂保存，绑定
用户、设备会话、账户、完整输入指纹和过期时间；Mutation 成功只表示业务命令
进入统一执行链路，不表示券商已报或成交。

普通撤单降低风险，不要求逐笔生物确认，但仍需专用 scope、归属、可撤状态和
幂等校验。最终“已撤”只来自券商回报。

## 客户端实现规则

- 登录后按 `grantedScopes` 和服务端 capability 生成可用功能；缺权限时展示明确
  只读/不可用状态，不显示必然失败的危险按钮。
- 每次 Mutation 仍处理 `FORBIDDEN`、账户不匹配、挑战过期和状态竞态，不能因为
  登录时有权限就假定请求会成功。
- 退出登录和设备会话吊销后清除 Token、Apollo Store、订阅、未消费挑战和通知
  路由。
- 权限迁移、接口名称和发布阻断见
  [iOS 产品契约](../guide/ios-product-contract)。
