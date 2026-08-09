# iOS 快速开始

QuantX 原生 iOS 首版定位为只读监控端。客户端可以读取账户、持仓、行情、
策略、委托和成交，但不应包含手工交易、撤单、策略控制或实盘模式切换入口。

## 基线

| 项目 | 要求 |
| --- | --- |
| UI | SwiftUI |
| 语言 | Swift 6 |
| 最低系统 | iOS 17 |
| GraphQL | Apollo iOS 2.x |
| 传输 | Release 仅允许 HTTPS/WSS |
| 网络 | VPN 或零信任入口，不直接暴露 Windows 8080 |

服务端只运行在 Windows。Caddy 是唯一公开入口，API 内部端口 `18081` 不是
客户端稳定地址。

## 1. 配置环境地址

每套环境只配置一个公开基地址，例如：

```text
https://quantx.example.internal
```

由它派生：

```text
REST        https://quantx.example.internal/auth/session
GraphQL     https://quantx.example.internal/graphql
WebSocket   wss://quantx.example.internal/graphql
Docs        https://quantx.example.internal/docs/
```

不要在 Release 构建中加入全局 ATS 例外，也不要绕过证书校验。

## 2. 添加 Apollo iOS

通过 Swift Package Manager 锁定团队验证过的 Apollo iOS 2.x 版本。应用
Target 使用 `Apollo` 产品；生成的 Schema 类型可以放入独立模块。

```swift
.package(
    url: "https://github.com/apollographql/apollo-ios.git",
    .upToNextMajor(from: "2.0.0")
)
```

下载[当前 GraphQL SDL](/contracts/graphql-schema.graphql)，保存到 iOS
工程的契约目录，再使用 Apollo SPM 插件生成类型：

```bash
swift package --disable-sandbox apollo-initialize-codegen-config
swift package --disable-sandbox apollo-generate
```

Schema 文件由 QuantX 发布包生成，客户端不得手工修改生成类型来掩盖不兼容。

## 3. 建立安全会话

1. 调用 `POST /auth/session`。
2. Access Token 与 Refresh Token 只写入 Keychain。
3. GraphQL HTTP 使用 `Authorization: Bearer <accessToken>`。
4. WebSocket 在 `connection_init.Authorization` 中发送同一 Access Token。
5. Token 即将过期时只允许一个刷新任务执行，其他请求等待同一结果。

完整流程见[原生客户端会话](./native-session)。

## 4. 先查询快照，再建立订阅

App 登录、冷启动、回到前台或 WebSocket 重连时：

```text
验证/刷新会话
  -> 查询账户、持仓、订单和策略快照
  -> 记录快照更新时间
  -> 建立需要的订阅
  -> 将增量合并到快照
```

订阅不是状态真源。断线期间可能错过事件，因此不能仅凭订阅恢复账户状态。

## 5. 首个验收查询

先从没有写入能力的查询开始：

```graphql
query CurrentAccount {
  currentAccount {
    id
    totalAsset
    cash
  }
}
```

字段以[当前 Schema 参考](../reference/graphql-api/)为准。服务端会再次验证
显式传入其他查询的 `accountId` 是否属于当前用户；客户端保存的账户列表
不是授权依据。

## 下一步

- [GraphQL HTTP](./graphql-http)
- [GraphQL WebSocket](./graphql-websocket)
- [权限模型](../concepts/permissions)
- [委托与成交状态](../concepts/order-lifecycle)
