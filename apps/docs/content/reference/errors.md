# 错误与恢复

## REST 错误

原生会话 REST 错误位于 `detail`：

```json
{
  "detail": {
    "code": "UNAUTHENTICATED",
    "message": "会话不存在或已过期",
    "requestId": "request-id",
    "retryable": false
  }
}
```

## GraphQL 错误

GraphQL 错误元数据位于 `errors[].extensions`，字段同样包括 `code`、
`requestId` 和 `retryable`。

## 客户端处理

| 条件 | 处理 |
| --- | --- |
| `UNAUTHENTICATED` | 协调一次 Token 刷新；刷新失败则登出 |
| `FORBIDDEN` | 不重试，提示权限或账户范围不足 |
| `retryable: true` | 使用有上限的退避重试 |
| 网络超时 | 保留旧快照并标记 stale，不推断操作成功 |
| WebSocket 4401 | 刷新 Token、重建连接、重新查询快照 |

日志只记录 operation 名称、错误码、`requestId` 和必要的网络状态，不记录
Token、密码、账户完整数据或 GraphQL variables 原文。
