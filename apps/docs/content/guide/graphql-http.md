# GraphQL HTTP

QuantX GraphQL 公共端点固定为同源 `/graphql`，只接受 POST。生产环境关闭
GraphiQL 与内省，客户端应使用发布包中的 SDL 进行 codegen。

## 请求

```http
POST /graphql
Authorization: Bearer <access-token>
Content-Type: application/json
X-Request-ID: ios-optional-correlation-id
```

```json
{
  "operationName": "Positions",
  "query": "query Positions($accountId: String!) { positions(accountId: $accountId) { stockCode volume canUseVolume } }",
  "variables": {
    "accountId": "account-id"
  }
}
```

`X-Request-ID` 最长 64 字符，只使用字母、数字和连字符；不提供或不合法时
服务端会生成新的请求编号，并通过响应头返回。

## 认证与账户授权

- HTTP Header 必须使用 `Authorization: Bearer <accessToken>`。
- 每个根字段按权限映射进行默认拒绝授权。
- `accountId` 只是筛选参数，服务端仍会验证它属于当前 Principal。
- 客户端隐藏入口不能替代服务端权限。
- 所有 Mutation 当前统一要求 `mutation:write`。

## 错误结构

GraphQL 业务和授权错误通常仍使用 HTTP 200，并出现在 `errors`：

```json
{
  "data": null,
  "errors": [
    {
      "message": "缺少 Bearer 访问令牌",
      "extensions": {
        "code": "UNAUTHENTICATED",
        "requestId": "request-id",
        "retryable": false
      }
    }
  ]
}
```

客户端按 `extensions.code` 分支，不解析中文 `message`。记录问题时保留
`requestId`，但不得附带 Token、完整持仓或订单原文。

## Codegen 约束

- 使用[发布 SDL](/contracts/graphql-schema.graphql)，不依赖生产内省。
- `.graphql` operation 与生成 Swift 类型一起接受代码审查。
- Schema 更新后重新生成，不使用字典、强制转换或手写模型掩盖差异。
- Generated GraphQL Model 先映射为 App Domain Model，再进入 SwiftUI。
