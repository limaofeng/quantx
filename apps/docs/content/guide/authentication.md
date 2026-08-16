# 认证与会话

## 三种客户端模型

| 客户端 | Refresh Token | Access Token | 刷新入口 |
| --- | --- | --- | --- |
| Web | HttpOnly Cookie | JavaScript 内存 | `/auth/web/session/refresh` |
| 原生 | Keychain/Keystore 等安全存储 | 内存 | `/auth/session/refresh` |
| 第三方 | 服务端密钥存储 | 进程内存 | `/auth/session/refresh` |

Access Token 只包含用户和设备会话标识。服务端在每次请求时从数据库读取当前
权限、账户授权和会话撤销状态，因此撤权不依赖旧 Token 过期。

Refresh Token 每次使用后立即轮换。客户端必须把 Access/Refresh Token 作为一个
原子状态更新；检测到旧 Refresh Token 重放时，服务端会撤销对应设备会话。

## 权限与账户

授权同时要求：

1. Principal 满足 operation policy 的全部 `requiredPermissions`。
2. 请求中的 `accountId` 属于 `authorizedAccountIds`。
3. 交易写入继续通过业务风控、实盘能力门和账户灰度检查。

权限不足返回 `FORBIDDEN`，会话缺失、过期或撤销返回 `UNAUTHENTICATED`。客户端按
错误 code 分支，不解析中文 message。
