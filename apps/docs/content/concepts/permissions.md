# 权限模型

QuantX 对 GraphQL 根字段采用默认拒绝。当前主要权限如下：

| 权限 | 能力 |
| --- | --- |
| `portfolio:read` | 账户、资产、持仓、自选与组合摘要 |
| `market:read` | 标的、行情、K 线、交易日历与研究数据 |
| `strategy:read` | 策略实例、运行状态、审计与做 T 监控 |
| `orders:read` | 委托、成交、清仓记录和交易事件订阅 |
| `system-status:read` | 服务、Agent、任务和运维状态 |
| `system-config:write` | 修改 AI Runtime 等全局非敏感系统配置 |
| `mutation:write` | 所有 GraphQL Mutation |

每个字段的当前映射见
[GraphQL 权限契约](/contracts/graphql-permissions.json)。

## 两层授权

请求同时满足：

1. Principal 拥有根字段要求的权限。
2. 请求中的 `accountId` 属于 Principal 的授权账户集合。

客户端拿到某个账号字符串、曾经缓存该账号或在 UI 中显示该账号，都不能绕过
第二层校验。

`system-config:write` 是独立的高权限写入能力，不被通用
`mutation:write` 自动替代。开发环境的自动登录用户会按 bootstrap 配置增量获得
该权限；生产用户必须通过既有用户权限管理流程显式授予。

## iOS 首版约束

iOS 首版产品范围仍是只读。客户端不得定义或调用 Mutation operation。
服务端最终能力取决于登录用户拥有的权限；因此用于 iOS 的账号应只授予所需
读权限，不能依靠客户端隐藏按钮实现权限收缩。

::: warning 当前限制
原生会话登录目前返回用户现有权限，没有单独的“按设备请求更小 scope”
参数。若同一用户拥有 `mutation:write`，Token 也会继承该权限。
:::
