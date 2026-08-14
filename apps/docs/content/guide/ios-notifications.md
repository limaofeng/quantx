# 普通 APNs 与通知路由

QuantX iOS v1 只使用普通 APNs。推送是提醒和刷新触发器，不是交易状态真源，也不
申请 Critical Alerts。服务端当前已经发布设备注册、类别偏好、注销、随机事件路由
和持久化 outbox 契约；真实 APNs 传输客户端仍由部署 capability 单独启用。

## 权限与绑定

四个根字段均使用 `notification:manage`。原生请求不传资金账号；服务端在敏感写入
事务内重新锁定并校验当前认证会话，然后强制绑定该会话的唯一
`activeAccountId`。事件路由还必须匹配同一用户、同一 `deviceSessionId` 和同一主
账户，另一台设备即使属于同一用户也不能解析该事件 ID。

## 注册与 Token 轮换

Apple 设备 Token 不是固定长度标识。App 每次收到系统注册回调或检测到 Token
变化时，都用当前值幂等调用：

```graphql
mutation IOSRegisterPushDevice($input: RegisterPushDeviceInput!) {
  registerPushDevice(input: $input) {
    id
    deviceInstallId
    appBundleId
    appVersion
    environment
    registeredAt
    preferences {
      category
      enabled
    }
  }
}
```

输入包含 `deviceToken`、`environment`（`SANDBOX` 或 `PRODUCTION`）、
`appBundleId`、`appVersion` 和本次安装生成并保存在本地的 UUID
`deviceInstallId`。响应故意不返回 Token。服务端只持久化加密 Token 与带服务端
密钥的摘要；Token 不得写入 OSLog、GraphQL 变量诊断、Analytics 或崩溃附件。

## 类别偏好

默认值为：

| 类别 | 默认 |
| --- | --- |
| `ACTION_REQUIRED` | 开 |
| `ORDER_UPDATE` | 开 |
| `RISK_SAFETY` | 开 |
| `AUTOMATION_ERROR` | 开 |
| `CONNECTION_DATA` | 关 |

App 内行动和风险事件始终保留；关闭类别只阻止 APNs outbox 创建，不删除业务事件。
调用 `updatePushPreferences` 时可以提交一个或多个不重复类别。空列表、未知类别和
非当前安装均 fail-closed。

## 最小 payload

服务端构造普通 alert，固定使用非敏感文案。自定义业务键严格为：

```json
{
  "aps": {
    "alert": {
      "title": "QuantX 有一项状态更新",
      "body": "打开应用查看当前状态"
    },
    "sound": "default"
  },
  "eventId": "opaque-random-uuid",
  "category": "ACTION_REQUIRED",
  "route": "today.action"
}
```

不得增加账户、证券、金额、价格、数量、方向、订单 ID、策略参数或确认 Token。
outbox 也不复制该业务 payload，更不保存 APNs Token；它只关联随机事件与加密注册
记录，并记录 `PENDING/SENT/RETRY/FAILED/DISCARDED` 投递状态。

## 解锁后解析

用户点击通知时，先完成本地生物解锁，再查询：

```graphql
query IOSNotificationEventRoute($eventId: ID!) {
  notificationEventRoute(eventId: $eventId) {
    eventId
    category
    routeType
    occurredAt
    expiresAt
    expired
  }
}
```

允许路由只有 `TODAY_ACTION`、`TRADING_ORDERS`、`TRADING_SAFETY`、
`QUANT_WORKSPACE` 和 `SYSTEM_STATUS`。客户端随后重新执行目标页面的快照 Query；
`expired=true`、返回 `null`、对象已终结或权限已变化时显示当前真实状态，不重放
通知代表的旧操作。

## 注销与失败恢复

登出前尽力调用 `unregisterPushDevice`。无论远端请求是否成功，本地都清理
Keychain、Apollo Store 和未消费通知路由。服务端注销是幂等的：第一次清除可解密
Token 并把尚未发送的 outbox 标为 `DISCARDED`，重复调用返回未找到，不恢复注册。

APNs 可能延迟、重排、节流或丢失通知，因此 App 冷启动、回前台和网络恢复仍必须
主动查询今日行动、订单、策略与安全快照。不能用 outbox `PENDING`、APNs 成功响应
或通知点击证明交易业务已确认。

## Apple 依据

- [Registering your app with APNs](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns)
- [Establishing a token-based connection to APNs](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns)
- [Sending notification requests to APNs](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)
- [Handling notifications and notification-related actions](https://developer.apple.com/documentation/usernotifications/handling-notifications-and-notification-related-actions)

后续发送器只允许可注入实现：Token-based JWT 使用 ES256，HTTP/2 + TLS 连接到与
注册环境匹配的 APNs 主机，认证配置缺一项即保持关闭。自动化测试必须使用 mock
client，不连接真实 APNs。
