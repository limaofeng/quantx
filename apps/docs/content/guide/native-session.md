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
    "permissions": ["portfolio:read", "market:read", "orders:read"],
    "authorizedAccountIds": ["account-id"]
  }
}
```

响应包含 `Cache-Control: no-store`。Token、密码和完整响应不得进入应用日志。

## 会话权限与个人单账户

登录请求只接受用户名、密码和可选设备名。账户与权限不是客户端可选项；提交
已废弃的账户或 scope 字段会直接返回 `422`。

服务端要求当前用户恰好授权一个资金账户，并把设备会话和 Access Token 绑定到
该账户；零账户或多账户都会使登录失败。响应只通过
`user.authorizedAccountIds` 返回这一唯一账户，不重复返回“当前账户”字段。

`user.permissions` 是该设备会话唯一的能力真源，取“用户当前权限 ∩ iOS v1
能力白名单”。`mutation:write`、`trade:direct`、`system-config:write`、
`admin:*` 等宽泛权限不会进入原生会话。Refresh Token 轮换保持同一账户和权限
上限：用户权限被收回时会收缩，新增用户权限不会扩大既有设备会话。

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
客户端恢复会话时必须重新校验 `user.authorizedAccountIds` 恰好包含一个账户，
且 `user.permissions` 只包含原生能力白名单内的权限。

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

## 0016 升级提示

0016 之前的原生和 Web 会话在数据库中无法可靠区分。迁移因此会一次性将
所有尚未撤销的旧会话标记 `revoked_at`，升级后原生和 Web 用户都需要重新登录。
迁移不删除会话或审计记录，也不会改写已撤销会话原有的撤销时间。
