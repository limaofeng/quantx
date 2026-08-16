# 原生客户端快速开始

原生客户端使用显式 Access Token 与轮换 Refresh Token。通用流程见
[认证与会话](./authentication)，请求模型见
[Client OpenAPI](/contracts/openapi-client.json)。

1. `POST /auth/session` 登录，并把两种 Token 原子写入平台安全存储。
2. GraphQL HTTP 使用 `Authorization: Bearer <access-token>`。
3. GraphQL WebSocket 在 `connection_init.Authorization` 中发送同一 Token。
4. 刷新后重建订阅；登出后清除 Token、GraphQL 缓存和账户数据。

只选择 operation policy 中包含 `native` 的字段。平台专属的 iOS 17、Apollo iOS、
Keychain 和后台恢复建议见 [iOS 实施指南](./ios-quickstart)。
