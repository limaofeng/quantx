---
layout: home

hero:
  name: QuantX Developer
  text: 原生客户端与 API 开发文档
  tagline: 从安全会话、GraphQL 契约到委托成交边界，为 iOS 和第三方客户端提供与当前 Windows 服务版本一致的接入说明。
  actions:
    - theme: brand
      text: 开始开发 iOS 客户端
      link: /guide/ios-quickstart
    - theme: alt
      text: 下载 API 契约
      link: /reference/

features:
  - title: 可生成的契约
    details: 下载 GraphQL SDL、权限映射和原生客户端 OpenAPI，直接接入 Apollo iOS 或其他代码生成工具。
  - title: 明确的安全边界
    details: 认证、账户授权、Token 轮换和 VPN/TLS 要求集中说明，客户端状态永远不是服务端授权边界。
  - title: 可信的交易语义
    details: 排队、已报、部分成交和全部成交严格区分；只有 miniQMT 委托与成交回报可以推进真实状态。
---

## 推荐阅读顺序

1. 从 [iOS 快速开始](./guide/ios-quickstart)确认网络、依赖和只读范围。
2. 实现[原生客户端会话](./guide/native-session)与安全的 Token 轮换。
3. 接入 [GraphQL HTTP](./guide/graphql-http) 查询和
   [GraphQL WebSocket](./guide/graphql-websocket)订阅。
4. 在展示订单、成交和账户数据前阅读[委托与成交状态](./concepts/order-lifecycle)。

::: warning 当前范围
本文档不提供券商凭证配置、QMT 内部管理、数据库结构或真实交易调试步骤。
:::
