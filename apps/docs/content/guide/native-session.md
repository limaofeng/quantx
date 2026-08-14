# 原生客户端会话

原生客户端使用显式 Access Token 与可轮换 Refresh Token。浏览器使用的
HttpOnly Cookie 会话不是 iOS 接口。

## 登录

```http
POST /auth/session
Content-Type: application/json
```

```json
{
  "username": "developer",
  "password": "password",
  "deviceName": "Limao iPhone"
}
```

成功响应：

```json
{
  "accessToken": "<short-lived-token>",
  "refreshToken": "<rotating-token>",
  "accessTokenExpiresAt": "2026-07-30T12:00:00Z",
  "refreshTokenExpiresAt": "2026-08-06T11:55:00Z",
  "tokenType": "Bearer",
  "deviceSessionId": "session-id",
  "user": {
    "id": "user-id",
    "username": "developer",
    "displayName": "Developer",
    "permissions": ["portfolio:read"],
    "authorizedAccountIds": ["account-id"]
  }
}
```

响应包含 `Cache-Control: no-store`。Token、密码和完整响应不得进入应用日志。

## 设备 scope 与单一主账户

iOS v1 交易版本的目标会话会增加可选请求字段
`requestedScopes/requestedAccountId`，并返回实际
`grantedScopes/activeAccountId`。服务端只签发用户权限、设备允许范围和请求
scope 的交集；Refresh Token 轮换保持同一 scope 和主账户，不得扩权。

这些字段尚未出现在当前 Client OpenAPI 时，客户端继续按现有响应安全登录，但
必须关闭依赖设备 scope 的手动下单、清仓、策略控制和通知写入。不能依靠客户端
隐藏入口弥补会话继承用户全部权限的问题。

v1 不提供账户切换。授权账户无法唯一解析、响应对象属于其他账户或刷新后的主
账户发生变化时，清除业务状态并要求重新建立会话。

## 刷新与单次轮换

```http
POST /auth/session/refresh
Content-Type: application/json
```

```json
{
  "refreshToken": "<current-refresh-token>"
}
```

刷新成功后，旧 Refresh Token 立即失效。客户端必须以原子方式把新
Access Token 与新 Refresh Token 一起写入 Keychain。

### 并发规则

- 所有业务请求共享一个会话协调器。
- 同一时间只允许一个刷新任务。
- 并发请求等待该任务，不重复发送旧 Refresh Token。
- 刷新返回未认证时清除 Token、Apollo Store、订阅和用户状态。
- 网络超时不能直接重放旧 Refresh Token；先确认当前协调器状态。

## 查询当前会话

```http
GET /auth/session
Authorization: Bearer <access-token>
```

用于验证 Access Token、读取用户权限和授权账户，不返回 Refresh Token。

## 登出与吊销

```http
DELETE /auth/session
Authorization: Bearer <access-token>
```

吊销全部设备：

```http
DELETE /auth/session?allDevices=true
Authorization: Bearer <access-token>
```

本地登出无论服务端请求是否成功，都应清除 Keychain、缓存和订阅；若服务端
吊销失败，应向用户说明远端会话可能仍有效。

## 本地保存边界

| 数据 | 保存位置 |
| --- | --- |
| Access/Refresh Token | Keychain |
| 主题、排序、脱敏开关 | UserDefaults |
| 账户资产、持仓、订单 | 仅内存；失败刷新可保留并标记 stale |
| 短时交易确认令牌 | 仅内存，过期/后台/登出即丢弃 |
| 密码、券商凭证、QMT 配置 | 不保存 |

App 进入后台时暂停订阅并遮蔽任务切换快照中的金额与账号；回到前台后先刷新
关键查询，再恢复订阅。
