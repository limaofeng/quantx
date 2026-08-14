# iOS 快速开始

QuantX 原生 iOS 是个人 A 股量化移动控制中心，面向本人单一主账户。目标能力包括
今日、行情、交易、量化和资产；客户端只启用当前部署 Schema、权限和 capability
已经支持的部分，不使用相近的兼容 Mutation 绕过尚未完成的安全契约。

先阅读[iOS 产品契约](./ios-product-contract)和[权限模型](../concepts/permissions)。

## 基线

| 项目 | 要求 |
| --- | --- |
| UI | SwiftUI，五 Tab：今日/行情/交易/量化/资产 |
| 语言 | Swift 6 |
| 最低系统 | iOS 17 |
| GraphQL | Apollo iOS 2.x，仓库锁定版本为准 |
| 传输 | Staging/Release 仅允许 HTTPS/WSS |
| 网络 | TestFlight 经 VPN/私网，不直接暴露 Windows 8080 到公网 |
| 账户 | 会话唯一解析的单一主账户 |

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

不要在 Release 构建中加入全局 ATS 例外，也不要绕过证书校验。Debug 可按仓库
配置连接受控私网 HTTP/WS，但它不能作为 TestFlight TLS 验收证据。

## 2. 添加 Apollo iOS

通过 Swift Package Manager 使用仓库验证并锁定的 Apollo iOS 2.x 版本。应用
Target 使用 `Apollo` 产品，生成的 Schema 类型放入独立目录。

下载[当前 GraphQL SDL](/contracts/graphql-schema.graphql)，或直接使用 monorepo 的
发布快照，再运行仓库脚本：

```bash
cd apps/ios
./scripts/install-apollo-cli.sh
./scripts/codegen.sh
```

Schema 由 QuantX 服务生成。客户端不得手工修改生成类型、手写 JSON 字典或使用
强制转换来掩盖不兼容。

## 3. 建立安全会话

1. 调用 `POST /auth/session`。
2. Access Token 与 Refresh Token 只写入 Keychain；密码不保存。
3. GraphQL HTTP 使用 `Authorization: Bearer <accessToken>`。
4. WebSocket 在 `connection_init.Authorization` 中发送同一 Access Token。
5. Token 即将过期时只允许一个刷新任务执行，其他请求等待同一结果。
6. 校验授权账户能唯一解析为主账户；出现跨账户响应时整页拒绝展示。

原生登录必须请求明确的 `requestedScopes`，并在多账户时传入
`requestedAccountId`。登录、刷新与会话恢复都必须校验响应中匹配的
`grantedScopes/activeAccountId`；未授权能力按服务端返回的 grant 降级。

完整流程见[原生客户端会话](./native-session)。

## 4. 先查询快照，再建立订阅

App 登录、冷启动、回到前台、Token 轮换或 WebSocket 重连时：

```text
验证/刷新会话
  -> 查询主账户、持仓、订单、策略和待办快照
  -> 记录服务端时间与版本
  -> 建立所需订阅
  -> 只合并更新的增量
```

订阅不是状态真源。断线期间可能错过事件，因此不能仅凭订阅恢复账户状态。失败
刷新可保留最后有效内存快照并标记 stale，但不完整或跨账户快照不得清空/覆盖
可信状态。

## 5. 首个验收查询

先验证没有写入风险的主账户查询：

```graphql
query IOSCurrentAccount {
  currentAccount {
    id
    totalAsset
    cash
    updateTime
  }
}
```

字段以[当前 Schema 参考](../reference/graphql-api/)为准。服务端会再次验证显式
传入其他查询的 `accountId` 是否属于当前用户；客户端保存的账号不是授权依据。

## 6. 启用产品能力

按依赖顺序实现：

1. 会话、主账户、Design System、五 Tab 与通用状态。
2. 今日、行情和资产的可信读取面。
3. 手动交易、撤单和卖出管理的专用 scope 与两阶段接口。
4. 策略、做 T、打板控制和移动参数 allowlist。
5. APNs、弱网恢复、可观测性和统一发布门禁。

每次启动和会话刷新后同时读取权限与服务端 capability。目标接口未出现在当前
SDL/权限 JSON 时，不编译或不展示对应入口；不得把现有 `placeOrder`、布尔
`confirm` 清仓接口或通用 `mutation:write` 当作移动安全流程。

## 7. 受控 Mutation 模式

所有新增实盘风险采用：

```text
preview Mutation
  -> 展示服务端规范化结果、数据时间、费用、风控和过期时间
  -> Face ID / Touch ID
  -> confirm Mutation（一次性挑战 + 幂等键）
  -> 显示 QUEUED 并查询订单/计划
```

确认令牌只保存在内存。挑战过期、使用过、账户/输入/门禁变化时重新预览，不自动
重放。确认成功不能写成“下单成功”或“成交”；详情状态以
[委托与成交状态](../concepts/order-lifecycle)为准。

当前已部署的做 T/策略买入意图批准已经使用该模式和独立 `trade:approve`，可作为
实现基线；手动下单、清仓、策略控制和 APNs 必须等待其目标公共契约落地。

## 8. 验证

```bash
cd apps/ios
xcodegen generate
xcodebuild -project QuantX.xcodeproj -scheme QuantX \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

日常自动化、真实后端只读 Scheme 和真实交易灰度是三个不同层次。普通测试始终
关闭真实交易；TestFlight 和 Canary 的完整门禁见仓库
`docs/product/ios/acceptance-gates.md`。

## 下一步

- [原生客户端会话](./native-session)
- [GraphQL HTTP](./graphql-http)
- [GraphQL WebSocket](./graphql-websocket)
- [权限模型](../concepts/permissions)
- [委托与成交状态](../concepts/order-lifecycle)
