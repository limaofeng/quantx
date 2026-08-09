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
| 账户资产、持仓、订单 | 首版仅内存 |
| 密码、券商凭证、QMT 配置 | 不保存 |

App 进入后台时暂停订阅并遮蔽任务切换快照中的金额与账号；回到前台后先刷新
关键查询，再恢复订阅。
