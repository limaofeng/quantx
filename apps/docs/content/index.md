---
layout: home

hero:
  name: QuantX Developer
  text: 个人量化客户端与 API 开发文档
  tagline: 从产品契约、安全会话和设备权限到交易确认与成交边界，为 QuantX iOS 提供与当前 Windows 服务版本一致的接入说明。
  actions:
    - theme: brand
      text: 阅读 iOS 产品契约
      link: /guide/ios-product-contract
    - theme: alt
      text: 开始开发客户端
      link: /guide/ios-quickstart

features:
  - title: 个人量化控制中心
    details: 今日、行情、交易、量化和资产围绕个人单一主账户组织，不再把 iOS 定义为只读监控器。
  - title: 可生成的契约
    details: 下载 GraphQL SDL、权限映射和原生客户端 OpenAPI，直接接入 Apollo iOS 或其他代码生成工具。
  - title: 明确的安全边界
    details: 认证、账户授权、Token 轮换和 VPN/TLS 要求集中说明，客户端状态永远不是服务端授权边界。
  - title: 可信的交易语义
    details: 排队、已报、部分成交和全部成交严格区分；只有 miniQMT 委托与成交回报可以推进真实状态。
---

## 推荐阅读顺序

1. 先阅读 [iOS 产品契约](./guide/ios-product-contract)，确认目标能力与当前部署
   capability 的边界。
2. 从 [iOS 快速开始](./guide/ios-quickstart)确认网络、依赖和契约。
3. 实现[原生客户端会话](./guide/native-session)与安全的 Token 轮换。
4. 接入 [GraphQL HTTP](./guide/graphql-http) 查询/受控 Mutation 和
   [GraphQL WebSocket](./guide/graphql-websocket)订阅。
5. 在实现交易前阅读[权限模型](./concepts/permissions)和
   [委托与成交状态](./concepts/order-lifecycle)。

::: warning 当前范围
本文档不提供券商凭证配置、QMT 内部管理、数据库结构或可自动执行的真实交易
调试步骤。目标接口未出现在当前发布契约前，客户端必须保持相应能力关闭。
:::
