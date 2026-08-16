# Web 快速开始

QuantX Web 使用同源会话。Refresh Token 只存在于 `HttpOnly`、`SameSite=Strict`
Cookie；JavaScript 只持有短期 Access Token。

## 登录与刷新

```text
POST   /auth/web/session
POST   /auth/web/session/refresh
DELETE /auth/web/session
```

请求必须来自配置允许的 Origin。`/auth/web/session/development` 仅在 development
且显式启用自动登录时存在，任何发布客户端都不能依赖它。

刷新成功后更新内存 Access Token，并使用新 Token 重建 GraphQL WebSocket。
退出时无论服务端请求是否成功，都清空缓存、订阅和本地用户状态。

完整 REST 请求与响应模型见 [Web OpenAPI](/contracts/openapi-web.json)。业务查询、
写入和订阅见 [GraphQL 参考](../reference/graphql-api/)。

::: warning 稳定性
标记为 `web-internal` 的 operation 会被完整记录，便于 QuantX Web 开发，但不承诺
第三方兼容。开发自动登录标记为 `development-only`。
:::
