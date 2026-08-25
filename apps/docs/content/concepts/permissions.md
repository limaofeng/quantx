# 权限模型

QuantX 对 GraphQL 根字段采用默认拒绝。每个字段必须在服务端 operation policy
显式登记；客户端还必须满足账户/资源归属和业务状态门禁。页面中是否显示按钮
永远不是服务端授权边界。

## 当前部署权限

| 权限 | 当前能力 |
| --- | --- |
| `portfolio:read` | 账户、资产、持仓、自选与组合摘要 |
| `portfolio:write` | 修改组合偏好等 Web 领域能力 |
| `watchlist:write` | 原生会话维护当前主账户自选 |
| `market:read` | 标的、行情、K 线、交易日历与研究数据 |
| `market:write` | 维护交易日历与市场研究数据 |
| `strategy:read` | 策略实例、运行状态、审计与做 T 监控 |
| `strategy:write` | 修改策略、回测、做 T 与助手状态 |
| `strategy:control` | 原生会话控制策略生命周期与安全参数 |
| `account-execution:control` | 独立控制账户增仓授权、受控窗口与账户紧急停止 |
| `t-trade:control` | 原生会话控制做 T 配置、启停与助手内暂停 |
| `limit-up:control` | 原生会话控制打板配置、候选偏好与布防 |
| `orders:read` | 委托、成交、清仓记录和交易事件订阅 |
| `orders:write` | 创建交易命令、退出计划、清仓与撤单 |
| `trade:manual` | 两阶段移动手动委托预览、确认与撤单 |
| `liquidation:control` | 清仓和退出计划的设备绑定预览与控制 |
| `notification:manage` | 当前设备 APNs 注册、偏好与通知路由 |
| `system-status:read` | 服务、Agent、任务和运维状态 |
| `operations:write` | 操作部署、流程与运营告警 |
| `agent:manage` | 创建/取消安全交接与撤销 Agent |
| `system-config:write` | 修改 AI Runtime 等全局非敏感系统配置 |
| `assistant:read` / `assistant:write` | AI 对话、运行和非交易工具审批 |
| `trade:approve` | 高风险交易、账户执行控制与功能灰度操作的附加确认授权 |

每个字段的当前映射见
[v2 operation policy](/contracts/graphql-operation-policies.v2.json)。旧的
`graphql-permissions.json` 已弃用，不再包含字段映射。

## 两层授权

请求同时满足：

1. Principal 拥有根字段 `requiredPermissions` 中的全部权限。
2. 请求中的 `accountId` 及目标资源属于 Principal 的授权账户集合。

客户端拿到账号字符串、曾缓存该账号或在 UI 中显示该账号，都不能绕过第二层。
系统配置权限也不被其他写权限自动替代。

`trade:approve` 不替代领域或控制权限。例如退出意图确认同时要求
`orders:write` 和 `trade:approve`，账户增仓授权同时要求
`account-execution:control` 和 `trade:approve`，做 T 实盘激活同时要求
`strategy:write` 和 `trade:approve`。

旧 `mutation:write` 已停用。升级迁移会为原先拥有它的用户补齐领域写权限和
已发布的控制权限并移除旧值；`trade:manual` 与 `trade:approve` 仍需显式授予。
之后管理员可以按最小权限重新收缩。

## iOS v1 最小权限

iOS 的产品目标是个人 A 股量化控制中心。原生会话按设备收缩 scope，账户则从
唯一的用户账户授权关系实时解析。iOS v1 专用 scope 为：

| Scope | 目标能力 |
| --- | --- |
| `watchlist:write` | 当前主账户自选维护 |
| `trade:manual` | 两阶段手动下单与撤单 |
| `trade:approve` | 设备绑定的策略/助手/退出意图确认和实盘授权 |
| `liquidation:control` | 清仓与退出计划配置、预览和控制 |
| `strategy:control` | 策略生命周期和移动安全参数 |
| `account-execution:control` | 账户增仓授权、受控窗口与账户紧急停止 |
| `t-trade:control` | 做 T 配置、启停、忽略列表与助手内暂停 |
| `limit-up:control` | 打板配置、候选偏好与布防 |
| `notification:manage` | 当前设备 APNs Token 与通知偏好 |

已停用的 `mutation:write` **不满足上述任何 iOS 写能力**。原生登录不接受账户或
scope 选择；服务端实时解析用户唯一授权账户，并在 `user.permissions` 返回“用户
权限 ∩ iOS 能力白名单”。`user.authorizedAccountIds` 必须恰好包含该账户，设备
会话不重复保存账户，刷新不得扩权。

## 客户端范围

operation policy 使用 `audiences` 和 `stability` 区分 `web`、`native`、
`third-party`。第三方只依赖同时标记 `third-party` 与 `supported` 的字段；
`web-internal` 虽然公开记录，但不构成第三方兼容承诺。

::: warning 当前限制
未出现在当前 v2 operation policy 的写能力必须保持关闭；不能靠隐藏按钮、直调
`placeOrder` 或兼容清仓接口临时开放。第三方集成应使用独立限权用户。
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

- 登录后按 `user.permissions` 和服务端 capability 生成可用功能；缺权限时展示明确
  只读/不可用状态，不显示必然失败的危险按钮。
- 每次 Mutation 仍处理 `FORBIDDEN`、账户不匹配、挑战过期和状态竞态，不能因为
  登录时有权限就假定请求会成功。
- 退出登录和设备会话吊销后清除 Token、Apollo Store、订阅、未消费挑战和通知
  路由。
- 权限迁移、接口名称和发布阻断见
  [iOS 产品契约](../guide/ios-product-contract)。
