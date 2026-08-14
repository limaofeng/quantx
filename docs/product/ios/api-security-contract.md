# QuantX iOS API、权限与交易安全契约

> 状态：目标契约；“现有”表示可直接复用，“新增/收紧”表示正式发布前必须落地
>
> 对应需求：`IOS-TRD-*`、`IOS-QNT-*`、`IOS-TTR-*`、`IOS-LUB-*`、
> `IOS-NTF-*`、`IOS-SEC-*`、`IOS-REL-*`

## 1. 不可突破的边界

```text
iOS
  -> VPN/私网 HTTPS/WSS
  -> Caddy / FastAPI / Strawberry
  -> Application commands + database outbox
  -> Engine / trading domain / OrderSizer / risk
  -> TradeCommand
  -> outbound QMT Agent
  -> miniQMT
  -> order/execution report
  -> inbox persistence
  -> Engine convergence
  -> iOS query/subscription projection
```

- iOS 只发起业务操作、显示预览和结果，不连接 QMT，不保存券商凭证，不计算最终
  合法数量，不更新真实持仓或 bucket。
- 所有交易写入必须进入统一应用命令和消息箱；API 不同步宣称券商受理或成交。
- `command_ack` 只证明 Agent 收到命令。委托、成交、资金与持仓只能由 QMT 回报
  经 inbox 持久化并由 Engine 收敛后推进。
- 数据库业务表和消息箱是真源；Redis 只作唤醒与广播，WebSocket 只作增量通知。
- 策略仍只输出 `TradeIntent[] + RuntimeStatePatch`，移动端操作不能让策略读取
  账户、数据库、网络或 QMT。

## 2. 传输与原生会话

Release 和 TestFlight 仅允许同源 `HTTPS/WSS`，公共路径固定为 `/auth/session`、
`/graphql` 和 `/docs/`。iOS 不访问 API 内部 `18081`，不关闭 TLS 校验，也不加入
全局 ATS 例外。

### 2.1 设备作用域会话

原生会话已按设备保存最小权限和唯一主账户。`POST /auth/session` 请求为：

```json
{
  "username": "…",
  "password": "…",
  "deviceName": "Personal iPhone",
  "requestedAccountId": "main-account-id",
  "requestedScopes": ["portfolio:read", "market:read", "orders:read"]
}
```

目标响应除现有 Token、用户和 `deviceSessionId` 外，增加：

```json
{
  "activeAccountId": "main-account-id",
  "grantedScopes": ["portfolio:read", "market:read", "orders:read"]
}
```

规则：

1. iOS 必须请求明确 scope；服务端只签发“用户权限 ∩ 设备允许权限 ∩ 请求范围”。
   已知但用户未授权的 scope 会从 grant 中省略；未知或禁止签发给 iOS 的
   宽泛权限使登录失败。
2. v1 只允许绑定一个 `activeAccountId`。用户没有或拥有多个可选账户而未能唯一
   解析时，登录失败，不默认选择第一个。
3. Refresh Token 轮换保持同一设备会话、主账户和 scope，不得借刷新扩权。
4. Access Token 至少绑定 `sub`、`sid`、主账户、scope、签发和过期时间。
5. 单设备或全部设备吊销立即让后续 HTTP/WS 和确认挑战失败。
6. 密码只用于会话创建，不写 Keychain；Access/Refresh Token 只写 Keychain。

原生登录、刷新和 `GET /auth/session` 都返回同一
`activeAccountId/grantedScopes`。兼容 Web 会话仍使用完整用户权限，不得作为
iOS 原生会话或交易权限的回退路径。

### 2.2 Token 与 WebSocket

- HTTP 使用 `Authorization: Bearer <accessToken>`。
- WebSocket 使用 `graphql-transport-ws`，Token 放在
  `connection_init.Authorization`。
- Token 刷新由单一协调任务完成；刷新后关闭旧 WebSocket，查询关键快照，再用
  新 Token 重建订阅。
- 4401 触发一次协调刷新；`FORBIDDEN`、账户不匹配或挑战失败不得盲目重试。

## 3. 最小权限模型

GraphQL 根字段默认拒绝。目标 iOS scope 如下：

| Scope | 能力 | 不包含 |
| --- | --- | --- |
| `portfolio:read` | 主账户、资产、持仓、收益与 bucket 投影 | 任意交易写入 |
| `market:read` | 标的、行情、K 线、盘口、逐笔和交易日历 | 自选写入 |
| `strategy:read` | 策略、做 T、打板、退出计划与审计读取 | 生命周期控制 |
| `orders:read` | 委托、成交、退出/清仓记录和交易事件 | 撤单或下单 |
| `system-status:read` | 必要的 Agent、行情、Engine 与安全状态 | 运维控制 |
| `watchlist:write` | 当前主账户自选新增、删除、排序 | 其他 Mutation |
| `trade:manual` | 手动订单预览/确认与撤单 | 策略意图批准 |
| `trade:approve` | 做 T/打板/策略/退出意图的设备确认、进入实盘授权 | 通用下单 |
| `liquidation:control` | 清仓预览确认、退出计划配置和控制 | 手动买入 |
| `strategy:control` | 策略生命周期及移动安全参数 | 未列入 allowlist 的参数 |
| `t-trade:control` | 做 T 配置、启停、忽略列表和 Kill Switch | 打板控制 |
| `limit-up:control` | 打板配置、布防和取消布防 | 做 T 控制 |
| `notification:manage` | APNs 设备与通知偏好 | 读取或修改交易事实 |

迁移规则：

- 当前 `trade:approve` 的短时买入批准继续复用并收紧到设备 scope。
- 兼容 `placeOrder` 使用隔离的 `trade:direct`，不得签发给 iOS 产品会话。
- `mutation:write` 是 Web/兼容管理权限，**不满足任何 iOS 写操作**。每个 iOS
  Mutation 必须映射到上表的专用 scope。
- `system-config:write`、`admin:*`、Agent 登记和部署权限永不签发给 iOS 产品会话。
- 字段既校验 scope，也校验主账户、资源归属、当前状态和风险门禁。

## 4. GraphQL 能力地图

### 4.1 可复用读取

| 产品域 | 现有 Query/Subscription | 目标补充 |
| --- | --- | --- |
| 今日/资产 | `currentAccount`、`portfolioSummary`、`portfolioOverview`、`positions`、`dailyAssetSnapshotsPage` | 行动收件箱的统一聚合 Query |
| 行情 | `instrumentsConnection`、`instrument`、`latestMarketQuotes`、`klinesPage`、`ticks`、`marketQuotes`、`marketKlines`、`marketTicks`、`marketDepth` | `orderEntryCapabilities` 提供市场/报价能力 |
| 自选 | `watchlist` | 现有增删排序 Mutation 改为 `watchlist:write` |
| 委托成交 | `todayOrders`、`historyOrders`、`todayTrades`、`historyTrades`、`order`、`trade`、`tradingEvents` | 规范化 `canCancel`、状态时间线与事件序列 |
| 卖出管理 | `exitPlans`、`exitPlan`、`exitPlanEvents`、`exitPlanCapabilities`、`exitPlanHoldingCapacity`、`liquidationSummary` | 清仓组预览/确认挑战 |
| 策略 | `strategyInstances`、`strategyInstance`、`strategyPerformance`、`strategyDecisionHistory`、`strategyExecutionTrace`、`strategyInstanceEvents` | 移动参数描述与控制预览 |
| 做 T | `tTradeGlobalMonitor`、批次/信号/事件 Query、`tTradeUpdates`、`validateTTradeLiveReadiness` | 无；写权限需拆分 |
| 打板 | `limitUpBoardAssistant`、`firstBoardPromotionDesk`、`limitUpRadar` 及更新订阅 | 无；写权限需拆分 |

查询字段以发布 SDL 为准。iOS operation 命名使用 `IOS` 前缀并经过 Apollo
codegen；Generated GraphQL Model 必须先映射为校验过的 App Domain Model。

### 4.2 必须新增或收紧的公共接口

以下名称是 v1 目标公共契约，不能由客户端调用相近的宽泛接口代替：

| 接口 | 类型 | 权限 | 说明 |
| --- | --- | --- | --- |
| `orderEntryCapabilities` | Query | `market:read` | 返回标的市场、支持报价类型、tick、数据时限和风险能力 |
| `previewManualOrder` | Mutation | `trade:manual` | 规范化订单、实时风控并签发短时挑战 |
| `confirmManualOrder` | Mutation | `trade:manual` | 消费挑战并创建 pending order/outbox |
| `cancelOrder` | Mutation（收紧） | `trade:manual` | 增加显式幂等键、归属与 `canCancel` 校验 |
| `previewLiquidation` | Mutation | `liquidation:control` | 对单只/选中/全部范围返回逐持仓计划预览 |
| `confirmLiquidation` | Mutation | `liquidation:control` + `trade:approve` | 消费绑定整个快照与计划组的挑战 |
| `previewExitPlanAuthorization` | Mutation | `liquidation:control` | 预览精确规则、保护量、版本与授权期限 |
| `confirmExitPlanAuthorization` | Mutation | `liquidation:control` + `trade:approve` | 生物确认后授权精确自动退出计划 |
| `strategyInstanceMobileParameters` | Query | `strategy:read` | 返回 allowlist 参数描述和配置版本 |
| `previewStrategyControl` | Mutation | `trade:approve` + `strategy:control` | 进入实盘/实盘启动前返回 readiness 挑战 |
| `confirmStrategyControl` | Mutation | `trade:approve` + `strategy:control` | 消费实盘控制挑战 |
| `registerPushDevice` / `updatePushPreferences` / `unregisterPushDevice` | Mutation | `notification:manage` | 管理当前设备 APNs Token 与类别偏好 |

现有 `placeOrder`、`liquidatePosition`、`liquidateAllPositions` 和只带布尔
`confirm` 的兼容接口不得被 iOS 用作上述两阶段流程的回退。

## 5. 手动订单目标契约

### 5.1 强类型输入

```graphql
enum ManualOrderSide { BUY SELL }
enum ManualOrderPriceType { LIMIT BEST }

input ManualOrderPreviewInput {
  accountId: String
  instrumentCode: String!
  side: ManualOrderSide!
  priceType: ManualOrderPriceType!
  volume: Int!
  limitPrice: Float
  idempotencyKey: String!
}
```

- `LIMIT` 必须提供 `limitPrice`；`BEST` 禁止提供价格。
- `BEST` 的产品文案固定为“对手方最优价”，仅在
  `orderEntryCapabilities` 对沪深证券和当前 Agent 明确返回支持时出现，并由
  服务端映射到 QMT `MARKET_PEER_PRICE_FIRST`。北交所和未知市场 fail-closed；
  iOS 不传 QMT 常量，也不使用含义模糊的 `MARKET`。
- 客户端可以提示整手、tick 和可用量，但 `volume` 只是请求值。最终合法
  数量由 OrderSizer、风控和账户事实决定；零股清仓由服务端规则处理。

### 5.2 预览输出

预览结果必须包含：

- `challengeId`、仅返回一次的 `confirmationToken`、`challengeExpiresAt`。
- 主账户、标的、方向、报价类型、请求量与规范化合法量。
- 限价或参考价、报价时间/序列、涨跌停价、预估金额、费用和滑点策略。
- 可用资金/可卖量快照时间、风险动作 `ALLOW/CAP/REJECT`、原因码和警告。
- 输入指纹版本与 `requestId/traceId`，但不向客户端暴露内部 HMAC。

`REJECT` 不签发可确认挑战。`CAP` 必须清楚展示请求量和下调后的合法量；用户
确认的是规范化结果，而不是原输入。

### 5.3 挑战绑定与确认

挑战最长有效 60 秒，并绑定：用户、设备会话、主账户、动作、证券、方向、报价
类型、规范化价格/数量、行情引用、输入指纹和过期时间。服务端只保存 Token 的
HMAC 摘要，Token 只能使用一次且不得进入日志、数据库业务正文或客户端持久层。

```graphql
input ManualOrderConfirmationInput {
  challengeId: String!
  confirmationToken: String!
}
```

iOS 只在内存持有 Token。用户点击确认后先执行 LocalAuthentication；成功后发送
确认。客户端不传 `biometricPassed: true` 之类可伪造字段。确认时服务端重新校验
挑战、会话、行情、资金、可卖量、T+1、停牌、涨跌停、受控窗口、Agent、对账和
Kill Switch；任一事实改变到超出服务端策略时使挑战失效并要求新预览。

`idempotencyKey` 在预览时绑定进挑战和后续 TradeCommand；确认不允许重新提交
业务输入或替换幂等键。成功结果只包含 `clientOrderId`、`status=QUEUED`、时间和
追踪 ID。同一挑战的网络重试返回原命令结果而不重复下单；相同幂等键用于不同
载荷时返回 `IDEMPOTENCY_CONFLICT`。

## 6. 撤单、清仓和退出计划

### 6.1 撤单

目标 `CancelOrderInput` 增加 `idempotencyKey`。服务端校验主账户、委托归属、
QMT 最新 `canCancel` 和状态；响应 `QUEUED` 只表示撤单命令已排队。实际
`CANCELED`、部分成交或全部成交继续由券商回报推进。

撤单降低而非新增风险，不要求生物确认；但仍要求本地已解锁、`trade:manual`、
会话和幂等校验。

### 6.2 清仓组

`previewLiquidation` 的强类型范围固定为：

- `SINGLE`：一个证券。
- `SELECTED`：明确证券集合。
- `ALL`：预览时主账户全部持仓。

完成策略固定为 `AVAILABLE_NOW` 或 `UNTIL_SNAPSHOT_CLEARED`；冲突策略固定为
`UNALLOCATED_ONLY` 或 `REPLACE_CANCELLABLE`。预览返回逐证券总量、可卖量、保护
量、T+1、冲突计划、待成交 SELL、纳入/跳过原因和快照版本。

`confirmLiquidation` 的挑战绑定完整证券集合、每只最大保护量、完成/冲突策略和
快照版本。确认后为每只证券建立独立 `MANUAL_LIQUIDATION` ExitPlan，以
`groupId` 关联；所有 SELL 仍经过统一 TradeIntent、OrderSizer、风控、Broker 和
回报收敛。确认后的新增持仓不自动加入。

### 6.3 条件与自动退出

创建/修改规则本身不代表自动实盘授权。`previewExitPlanAuthorization` 返回账户、
证券、保护量、规则、T+1 策略、委托策略、配置版本、有效期和风险警告；确认挑战
绑定这些字段。任一规则、保护量、版本或账户事实的安全相关变更使授权失效。

未授权的实盘 SELL 意图进入 `AWAITING_APPROVAL`，使用现有
`previewExitIntent/confirmExitIntent` 逐次确认。已精确授权的自动计划可在授权
范围内触发而不等待手机在线，但每个订单仍重新经过实时风控。客户端不能用
`autoExitAuthorized=true` 布尔值自行越权。

## 7. 策略、做 T 与打板控制

### 7.1 策略生命周期和参数

- 普通暂停、恢复、停止入场使用 `strategy:control`，并由服务端状态机决定是否
  转为 `DRAINING`。iOS 不本地改状态。
- 模拟转实盘、实盘启动或提高实盘风险使用
  `previewStrategyControl/confirmStrategyControl`，同时要求 `trade:approve`。
- `strategyInstanceMobileParameters` 只返回明确 allowlist 字段：key、类型、单位、
  min/max/step、是否立即生效、风险级别和 `configVersion`。
- `updateStrategyInstanceParameters` 必须增加 `expectedVersion`，拒绝未列入 allowlist
  的 key 和版本冲突。即使底层暂用 JSON 传输，服务端也按描述强类型校验，iOS
  不构造任意字典参数。

### 7.2 做 T

- 读取沿用当前投影和 readiness。
- 设置、忽略列表、启停与协调使用 `t-trade:control`；入场批准、建立受控窗口、
  激活实盘和 Kill Switch 继续要求 `trade:approve` 和设备绑定挑战。
- 已有 `previewTTradeEntryApproval/confirmTTradeEntryApproval` 作为预览确认基线；
  挑战继续绑定 run/intent/account/device，确认后只重新进入统一风控。
- 有活动批次/退出计划时停止请求由服务端拒绝或转 DRAINING，客户端不得清除投影。

### 7.3 打板

- 设置、候选偏好和布防使用 `limit-up:control`；布防不等于下单。
- 入场批准沿用 `previewStrategyTradeIntentApproval` /
  `confirmStrategyTradeIntentApproval`，要求 `trade:approve` 和生物确认。
- 雷达、门禁、预算、T+1 和退出计划均来自服务端投影。页面访问不创建独立全市场
  行情订阅，不复制 Engine 的候选逻辑。

## 8. 快照、订阅和状态合并

每个可订阅资源必须带稳定对象 ID、服务端时间和可比较的 sequence/version。合并
规则固定为：

1. 冷启动、回前台、Token 轮换和重连先查询快照，记录快照版本。
2. 只接收版本新于当前对象的增量；重复事件幂等忽略。
3. 检测到序列间隙、未知对象或服务端提示重同步时重新查询，不自行补状态。
4. 增量不得把终态回滚为旧中间态；无法比较时标记未知并重新取数。
5. 查询失败时保留最后有效内存快照并标记 stale；不完整快照不得清空原对象集合。
6. 交易事件只作为刷新提示；订单/成交详情仍从数据库投影重新读取。

## 9. APNs 契约

### 9.0 当前服务端基础（2026-08-15）

M5 服务端基础已落地，公共 GraphQL 契约为：

- `registerPushDevice(input)`：绑定当前 `deviceSessionId` 和唯一主账户，按安装实例
  幂等注册或轮换 Token；
- `updatePushPreferences(input)`：只修改当前会话、当前安装的类别偏好；
- `unregisterPushDevice(input)`：失效当前安装、清除可解密 Token，并把待发送项标为
  `DISCARDED`；
- `notificationEventRoute(eventId)`：解锁后按随机事件 ID 解析类别、非敏感路由、
  发生/过期时间；事件必须同时匹配当前用户、设备会话和主账户。

四个根字段均要求 `notification:manage`；兼容 Web Mutation 只沿用现有
`mutation:write` 迁移规则，原生会话不接受该宽权限回退。服务端通过 0017 迁移
持久化加密 Token、类别偏好、随机事件和无业务 payload 的 outbox；旧库若只存在
部分表、约束或索引会 fail-closed。

当前切片不连接真实 APNs，也不把 outbox 的 `PENDING` 表示为送达。后续发送器必须
以可注入客户端接入，并在 ES256 Key ID、Team ID、Bundle ID、环境、HTTP/2 与 TLS
配置全部有效时才启用；只发送普通 alert，不申请或模拟 Critical Alerts。

### 9.1 设备注册

APNs Token 绑定当前 `deviceSessionId`、安装实例 ID、环境（sandbox/production）、
App 版本和通知偏好。Token 轮换时 upsert；登出和会话吊销时注销或失效。服务端
以加密敏感字段保存 Token，并只以带服务端密钥的摘要进行轮换识别；GraphQL 响应、
普通日志、异常和 outbox 均不包含 Token。客户端不假设 Token 固定长度，并在系统
回调获得新 Token 后重新注册。

类别与默认值固定为：

| GraphQL 类别 | 默认 APNs |
| --- | --- |
| `ACTION_REQUIRED` | 开 |
| `ORDER_UPDATE` | 开 |
| `RISK_SAFETY` | 开 |
| `AUTOMATION_ERROR` | 开 |
| `CONNECTION_DATA` | 关 |

### 9.2 Payload

```json
{
  "aps": {
    "alert": {
      "title": "QuantX 有一项待处理事项",
      "body": "打开应用查看当前状态"
    },
    "sound": "default"
  },
  "eventId": "opaque-random-id",
  "category": "ACTION_REQUIRED",
  "route": "today.action"
}
```

payload 和锁屏文案禁止包含账号、金额、证券代码/名称、价格、数量、策略参数、
订单 ID、确认 Token 或买卖方向。App 解锁后使用 `eventId` 重新读取，并校验事件
属于当前主账户。APNs 送达不是业务确认，不改变服务端事件状态。

允许的 `route` 仅为 `today.action`、`trading.orders`、`trading.safety`、
`quant.workspace` 和 `system.status`。payload 的自定义业务键严格只有
`eventId/category/route`；标题和正文使用统一非敏感文案。事件过期后 Resolver
仍可返回 `expired=true` 供客户端显示真实过期状态，但不得重放旧操作。

实现依据：Apple 的
[App 注册 APNs](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns)、
[Token 连接](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns)、
[发送请求](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)
和[通知交互处理](https://developer.apple.com/documentation/usernotifications/handling-notifications-and-notification-related-actions)。

## 10. 错误、审计与敏感数据

REST `detail` 和 GraphQL `errors[].extensions` 至少包含稳定 `code`、
`requestId`、`retryable`。交易相关目标错误码包括：

| 错误码 | 客户端处理 |
| --- | --- |
| `UNAUTHENTICATED` | 协调刷新一次；失败登出 |
| `FORBIDDEN` | 不重试，显示缺失能力 |
| `ACCOUNT_SCOPE_MISMATCH` | 整页拒绝并清理 Feature 状态 |
| `CHALLENGE_EXPIRED/USED/INPUT_MISMATCH` | 丢弃 Token，重新预览 |
| `VERSION_CONFLICT` | 刷新配置，展示差异 |
| `IDEMPOTENCY_CONFLICT` | 停止重试并显示诊断 ID |
| `QUOTE_TYPE_UNSUPPORTED` | 移除该报价类型，重新编辑 |
| `DATA_STALE/AGENT_OFFLINE/RECONCILE_REQUIRED/KILL_SWITCH_ACTIVE` | 禁止新增实盘风险，展示门禁 |
| `ORDER_NOT_CANCELABLE` | 刷新订单并显示券商当前状态 |
| `RISK_REJECTED` | 展示原因码和允许的下一步，不自动改方向 |

允许记录：operation 名称、错误码、脱敏对象类型、`requestId/traceId`、客户端版本、
网络类型和时间。禁止记录：密码、Token、确认令牌、完整账号、GraphQL variables
原文、完整持仓/订单、券商响应、QMT 配置和 APNs payload 正文。

每次预览、确认、拒绝、限量、撤单、策略控制、自动授权、熔断和失败尝试都写入
追加式审计，能够还原用户/设备会话、账户、输入指纹、策略/计划版本、风控原因、
命令和最终券商回报；审计不得包含可重放密钥。

## 11. 兼容与发布阻断

| 当前能力 | 目标差距 | 发布决策 |
| --- | --- | --- |
| 会话继承用户全部权限 | 缺设备 scope 与唯一主账户绑定 | 交易 TestFlight 候选阻断 |
| 大多数 Mutation 只要求 `mutation:write` | 缺最小权限拆分 | iOS 不调用这些写入 |
| `placeOrder` 直接提交 | 缺手工下单两阶段确认 | 不提供手工交易入口 |
| 清仓兼容接口使用布尔 `confirm` | 缺组级绑定预览 | iOS 只读卖出管理，直至新契约落地 |
| 策略参数是通用 JSON | 缺移动 allowlist 和版本冲突 | iOS 只展示，不编辑 |
| 已有助手买入预览/确认 | 权限仍需设备 scope 化 | 可复用实现，但不得宣告最终安全门完成 |
| 无 APNs 设备契约 | 无通知闭环 | 通知阶段阻断 |

接口落地后，必须刷新 SDL、权限 JSON、Client OpenAPI、在线文档和 Apollo Swift
类型，并通过 Web/iOS 双端 codegen。禁止为赶进度在 iOS 中调用兼容直写接口、
手写 JSON、使用强制转换或把服务端错误吞掉。
