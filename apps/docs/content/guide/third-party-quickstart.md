# 第三方 API 快速开始

第三方集成使用独立、限权的 QuantX 用户，不复用日常管理员账号。只依赖 operation
policy 同时标记为 `third-party` 和 `supported` 的字段。

## 最小接入流程

1. 通过 `POST /auth/session` 获取 Access Token 与一次性轮换 Refresh Token。
2. 下载当前版本的 GraphQL SDL 和 v2 operation policy，并生成强类型客户端。
3. 从 `/auth/session` 读取实际权限与授权账户，再启用对应功能。
4. 为 Mutation 使用业务幂等键；超时后先查询状态，不盲目重发交易命令。
5. 保存 GraphQL `extensions.requestId`，排障材料不得包含 Token、完整持仓或订单原文。

第三方客户端不得使用 Web Cookie 会话、开发自动登录、QMT Agent 凭据接口、
`web-internal` 或 `experimental` operation。

::: danger 交易写入
`orders:write` 可以创建真实交易相关命令。涉及人工交易意图确认的字段还同时要求
`trade:approve`。权限通过不代表风控、账户范围、灰度和 QMT 就绪检查通过。
:::
