---
layout: home

hero:
  name: QuantX Developer
  text: Web、原生客户端与第三方 API
  tagline: 从安全会话、显式权限到交易状态收敛，使用与当前 QuantX 服务版本一致的契约构建客户端。
  actions:
    - theme: brand
      text: 选择接入方式
      link: /guide/
    - theme: alt
      text: 阅读 iOS 产品契约
      link: /guide/ios-product-contract

features:
  - title: 个人量化控制中心
    details: 今日、行情、交易、量化和资产围绕个人单一主账户组织，不再把 iOS 定义为只读监控器。
  - title: 可生成的契约
    details: 下载 GraphQL SDL、v2 operation policy、Web 与 Client OpenAPI，接入标准代码生成工具。
  - title: 明确的安全边界
    details: 每个根字段公开权限组合、适用端、稳定性与风险；客户端状态永远不是授权边界。
  - title: 可信的交易语义
    details: 排队、已报、部分成交和全部成交严格区分；只有 miniQMT 委托与成交回报可以推进真实状态。
---

## 推荐阅读顺序

1. 在[接入方式](./guide/)中选择 Web、原生客户端或第三方 API。
2. 按[认证与会话](./guide/authentication)实现 Token 轮换与退出。
3. 从[能力地图](./guide/capabilities)选择稳定且适用于当前客户端的 operation。
4. 写操作先阅读 [Mutation 工作流](./guide/mutations)和
   [委托与成交状态](./concepts/order-lifecycle)。
5. iOS 开发还需阅读 [iOS 产品契约](./guide/ios-product-contract)、
   [iOS 快速开始](./guide/ios-quickstart)和
   [原生客户端会话](./guide/native-session)。

::: warning 当前范围
文档会完整标出 Web 内部能力，但不公开券商凭证、QMT 设备密钥、数据库结构或
可自动执行的真实交易调试步骤。`web-internal` 不构成第三方兼容承诺；目标接口
未出现在当前发布契约前，客户端必须保持相应能力关闭。
:::
