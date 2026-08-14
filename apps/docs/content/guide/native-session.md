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
  "deviceName": "Limao iPhone",
  "requestedAccountId": "account-id",
  "requestedScopes": ["portfolio:read", "market:read", "orders:read"]
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
  "activeAccountId": "account-id",
  "grantedScopes": ["portfolio:read", "market:read", "orders:read"],
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

`requestedScopes` 是原生登录的必填字段。服务端返回“用户当前权限 ∩
iOS 允许权限 ∩ 请求范围”作为 `grantedScopes`：已知但用户未授权的
scope 会被安全省略，未知 scope 以及 `mutation:write`、`trade:direct`、
`system-config:write`、`admin:*` 等宽泛权限会使登录失败。
显式的空数组 `[]` 允许建立仅身份验证、零产品能力的会话；它不等于省略字段。

`requestedAccountId` 必须属于当前用户。它仅在用户恰好授权一个账户时可
省略；零账户或多账户不会默认选择第一个。会话、Access Token 和后续响应
都绑定这一 `activeAccountId`。Refresh Token 轮换保持同一账户与 scope，
用户权限被收回时只会继续收缩，新增用户权限不会扩大既有设备会话。

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
响应同样包含 `activeAccountId/grantedScopes`，客户端恢复会话时必须重新
校验这两个字段。

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
