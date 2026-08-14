# 契约下载

以下文件随当前 Windows 发布包生成，不依赖生产环境的 Swagger、GraphiQL
或 GraphQL 内省。

| 契约 | 用途 | 下载 |
| --- | --- | --- |
| GraphQL SDL | Apollo iOS codegen 和 Schema diff | [graphql-schema.graphql](/contracts/graphql-schema.graphql) |
| GraphQL 权限 | 根字段权限审查 | [graphql-permissions.json](/contracts/graphql-permissions.json) |
| Client OpenAPI | 原生会话和健康检查代码生成 | [openapi-client.json](/contracts/openapi-client.json) |

## Client OpenAPI 范围

只包含：

- `POST /auth/session`
- `POST /auth/session/refresh`
- `GET /auth/session`
- `DELETE /auth/session`
- `/health`、`/health/live`、`/health/ready`、`/health/components`

明确排除浏览器 Cookie 会话、QMT Agent 登记与设备密钥、metrics、开发端点
和内部管理接口。

## GraphQL 参考

在线可搜索的字段和类型页面见[GraphQL Schema 参考](./graphql-api/)。
完整 Schema 包含 Query、Mutation 和 Subscription，以便标准工具正确生成类型。
iOS 按[权限模型](../concepts/permissions)和服务端 capability 只启用已发布的专用
能力；通用 `mutation:write` 不得替代移动端交易安全契约。
