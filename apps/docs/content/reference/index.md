# 契约下载

以下文件从当前 Windows Dev 工作区生成，不依赖运行时 Swagger、GraphiQL 或
GraphQL 内省。

| 契约 | 用途 | 下载 |
| --- | --- | --- |
| GraphQL SDL | 各平台 codegen 和 Schema diff | [graphql-schema.graphql](/contracts/graphql-schema.graphql) |
| GraphQL operation policy v2 | 权限组合、受众、稳定性与风险 | [graphql-operation-policies.v2.json](/contracts/graphql-operation-policies.v2.json) |
| Client OpenAPI | 原生/第三方会话与健康检查 | [openapi-client.json](/contracts/openapi-client.json) |
| Web OpenAPI | Web Cookie 会话与健康检查 | [openapi-web.json](/contracts/openapi-web.json) |

## REST 契约范围

Client OpenAPI 包含：

- `POST /auth/session`
- `POST /auth/session/refresh`
- `GET /auth/session`
- `DELETE /auth/session`
- `/health`、`/health/live`、`/health/ready`、`/health/components`
- `/health/runtime/market-data`

Web OpenAPI 另包含 `/auth/web/session`、刷新、登出和开发自动登录；后者明确标为
development-only。两份契约都排除 QMT Agent 密钥交换、metrics 和内部管理接口。

## GraphQL 参考

在线可搜索的字段和类型页面见[GraphQL Schema 参考](./graphql-api/)。
完整 Schema 包含 Query、Mutation 和 Subscription，以便标准工具正确生成类型。
客户端必须结合[权限模型](../concepts/permissions)、服务端 capability 和 v2
operation policy 选择可用字段；已停用的 `mutation:write` 不得替代移动端交易
安全契约。
