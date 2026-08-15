# QuantX 原生 iOS App 开发准备与实施计划

> 文档状态：**已被取代，仅保留历史背景**
>
> 取代日期：2026-08-15
>
> 权威新规格：[QuantX 个人 A 股量化 iOS 产品文档](../product/ios/README.md)
>
> 本文的“只读移动监控端”、旧五 Tab、禁止交易 Mutation 和分阶段发布范围不再
> 作为产品或验收依据。新目标是个人 A 股量化移动控制中心；交易能力仍必须遵守
> 服务端预览、生物确认、最小权限、统一交易域和 QMT 回报真源。
>
> 编制日期：2026-07-21
> 状态校正：2026-07-30，服务端原生会话与 GraphQL HTTP/WS 授权已落地
> 当前环境：Windows，仅编写和维护本文档
> 实施环境：后续在 macOS + Xcode 环境执行

---

## 1. 文档目标

本文定义 QuantX 原生 iOS App 的首版范围、前置条件、技术架构、安全边界、GraphQL 契约、测试标准和分阶段实施顺序，供后续 macOS 开发阶段直接执行。

本轮只新增本文档，不执行以下工作：

- 不创建 `ios/` 或 Xcode 工程。
- 不安装 Swift Package、Apollo iOS 或其他 iOS 依赖。
- 不修改后端、GraphQL Schema 或现有 Web 前端。
- 不运行 Xcode、iOS Simulator、代码签名、TestFlight 或 App Store Connect 操作。
- 不运行任何真实交易或真实交易 E2E 测试。

---

## 2. 当前结论与默认决策

### 2.1 首版定位

首版定位为 **只读移动监控端**，包含：

- 账户资产和组合概览。
- 当前持仓及盈亏。
- 自选或持仓标的行情与基础图表。
- 策略实例及运行状态。
- 今日和历史委托、成交记录。
- 网络、数据更新时间和服务状态提示。

首版明确不包含：

- 手工买入、卖出、撤单、清仓。
- 策略启动、停止、参数修改或实盘模式切换。
- miniQMT、券商账号或交易凭证管理。
- 后台常驻 WebSocket。
- 在本地持久化账户资产、持仓、订单或成交明细。

### 2.2 发布和联网方式

- 分发方式：Apple Developer Program + TestFlight 内测。
- 使用范围：本人或小团队，不以公开 App Store 上架为首版目标。
- 联网方式：iPhone 通过私网/VPN 访问 QuantX 服务。
- 传输要求：测试和发布环境均使用 `HTTPS/WSS`；Release 构建不允许明文 HTTP。
- 后端 8080 端口不直接暴露公网，由反向代理、VPN 或零信任入口提供访问。

### 2.3 平台和技术基线

- UI：SwiftUI。
- 语言：Swift 6。
- 最低系统：iOS 17。
- 构建工具：实施时可用的稳定版 Xcode 26 或更新版本。
- GraphQL 客户端：Apollo iOS 2.x，通过 Swift Package Manager 锁定具体版本。
- 实时订阅：ApolloWebSocket，协议与 Strawberry GraphQL 服务端实际支持的协议保持一致。
- 并发：Swift Concurrency；视图模型或 Feature Store 使用 `@MainActor`。
- 导航：`NavigationStack` 和类型安全的 `navigationDestination`。

---

## 3. 当前项目基础与风险

### 3.1 可复用能力

QuantX 后端已经采用 FastAPI + Strawberry GraphQL，具备以下可复用能力：

- 账户、持仓、投资组合、行情、策略、订单和成交查询。
- GraphQL WebSocket 订阅。
- A 股交易域、策略执行、风控、订单状态和 miniQMT 状态收敛。
- Web 前端已有 Dashboard、Portfolio、Strategies、Trading 等功能，可作为产品需求参考。

### 3.2 不能直接复用的部分

现有 `apps/web/src/features/trading/pages/MobileTradingPage.tsx` 是移动网页原型，不是原生 iOS 产品规格，并包含固定最大数量、模拟盘口、`demo-user` 等占位逻辑。iOS 不复制其中的交易写入能力，只参考信息层级和已有查询。

### 3.3 当前后端准备状态

服务端已经完成原生会话、Refresh Token 单次轮换与吊销、GraphQL HTTP/WS
Bearer 认证、用户账户归属校验、根字段权限和标准错误扩展。WebSocket 使用
`graphql-transport-ws`，在 `connection_init.Authorization` 中发送 Token，
并在 Access Token 过期时以 4401 关闭。

仍需在 iOS 实施阶段完成：

- 使用只读权限账号，不把客户端隐藏入口当成写权限边界。
- Staging 的 VPN、HTTPS/WSS、证书与弱网恢复验收。
- iOS Keychain、刷新并发去重、日志脱敏和任务切换隐私遮罩。
- 若需要按设备收缩权限，另行增加原生会话 scope 机制；当前会话继承用户权限。

当前客户端契约和接入边界通过 `/docs/` 在线发布。

---

## 4. 开发前准备清单

### 4.1 硬件与账号

- [ ] 一台能够运行当前稳定版 Xcode 的 Mac，优先 Apple Silicon。
- [ ] 至少一台真实 iPhone，用于 VPN、弱网、通知、前后台切换和性能验证。
- [ ] Apple Developer Program 账号。
- [ ] 确定开发团队、Bundle ID、App 名称和签名主体。
- [ ] 在 App Store Connect 建立 App 和 TestFlight 内测组。
- [ ] 准备 App 图标、启动体验、隐私政策地址和支持邮箱。

### 4.2 服务环境

至少准备三套独立配置：

| 环境 | 用途 | 数据要求 | 网络要求 |
|---|---|---|---|
| Debug | 本机和模拟器开发 | Mock 或测试数据 | 可使用本地开发地址，ATS 例外只允许出现在 Debug |
| Staging | TestFlight 验收 | 隔离的模拟盘或只读镜像 | VPN + HTTPS/WSS |
| Production | 后续正式使用 | 真实账户只读数据 | VPN/零信任入口 + HTTPS/WSS |

每个环境通过 `.xcconfig` 管理非敏感配置，例如 GraphQL HTTP URL、WebSocket URL和日志级别。密钥、Token 和证书私钥不得写入仓库或 `.xcconfig`。

### 4.3 产品准备

- [ ] 明确目标用户只有本人还是包含小团队成员。
- [ ] 为每类用户定义可访问账户范围。
- [ ] 明确资产、盈亏和 A 股颜色习惯；首版默认“红涨绿跌”。
- [ ] 确定首页最重要的四项指标：总资产、可用资金、当日盈亏、持仓市值。
- [ ] 确定策略异常、Agent 离线和数据过期的展示优先级。
- [ ] 准备脱敏的测试账户、持仓、委托、成交和策略运行样本。

---

## 5. 目标 App 信息架构

### 5.1 主导航

首版使用五个主入口：

1. **首页**：账户摘要、当日盈亏、资产分布、服务状态、主要持仓。
2. **持仓**：持仓列表、可用数量、成本、现价、市值、盈亏和占比。
3. **策略**：策略实例、运行模式、状态、标的、最近更新时间和异常原因。
4. **委托成交**：今日/历史委托、成交记录、状态筛选和详情。
5. **设置**：环境、当前用户、会话管理、生物识别解锁、隐私和版本信息。

### 5.2 二级页面

- 股票详情：行情摘要、分时或 K 线、持仓摘要、相关委托成交。
- 策略详情：运行状态、指标、最近决策摘要、日志和数据更新时间。
- 委托详情：券商委托状态、已成交数量、成交明细和状态说明。
- 服务状态：GraphQL、WebSocket、行情、QMT Agent 和 miniQMT 状态，只展示后端确认的事实。

### 5.3 展示约束

- “已提交”“已报”“部分成交”“全部成交”必须分别展示，不能把提交成功写成成交。
- 所有实时或准实时数据都显示最后更新时间和 stale 状态。
- 未知、延迟或缺失状态按保守方式展示，不推断账户安全或订单已完成。
- 金额和账户编号在 App 切入后台时从系统任务切换快照中遮蔽。
- 涨跌不能只靠颜色表达，同时显示正负号、方向图标或文字。

---

## 6. iOS 技术架构

### 6.1 计划目录

macOS 实施阶段创建以下结构：

```text
ios/
├── QuantX.xcodeproj
├── QuantX/
│   ├── App/
│   ├── Core/
│   │   ├── Auth/
│   │   ├── GraphQL/
│   │   ├── Networking/
│   │   ├── Security/
│   │   ├── DesignSystem/
│   │   └── Utilities/
│   ├── Features/
│   │   ├── Dashboard/
│   │   ├── Portfolio/
│   │   ├── Strategies/
│   │   ├── Orders/
│   │   ├── Stocks/
│   │   └── Settings/
│   ├── GraphQL/
│   │   ├── Operations/
│   │   └── Generated/
│   ├── Resources/
│   └── PrivacyInfo.xcprivacy
├── QuantXTests/
└── QuantXUITests/
```

### 6.2 状态与数据流

```text
SwiftUI View
  -> @MainActor Feature Store
  -> Repository / Use Case
  -> Apollo HTTP Query 或 WebSocket Subscription
  -> Generated GraphQL Model
  -> App Domain Model
  -> View State
```

要求：

- SwiftUI View 不直接拼接 GraphQL 请求。
- Generated GraphQL Model 不直接扩散到所有界面，先映射为稳定的 App Domain Model。
- 查询快照是恢复和重连真源，WebSocket 只提供增量更新。
- App 回到前台时先刷新关键查询，再重建订阅。
- 首版只使用内存缓存；退出登录时清空 Apollo Store 和所有内存状态。

### 6.3 后台行为

iOS App 进入后台后通常会被挂起，因此：

- 不依赖后台 WebSocket 保持监控。
- 进入后台时关闭或暂停订阅。
- 回到前台时重新认证、查询快照并重建订阅。
- APNs 告警作为后续阶段实现，不把普通后台模式滥用为常驻连接。
- 推送只携带事件类型和事件 ID，不携带资产金额、账号或订单明细。

---

## 7. 后端认证与公共接口

### 7.1 会话接口

当前已经提供独立 REST 会话接口：

```text
POST   /auth/session
POST   /auth/session/refresh
DELETE /auth/session
GET    /auth/session
```

会话返回最少包含：

- 短期 Access Token。
- 可轮换 Refresh Token。
- Access Token 过期时间。
- 当前用户 ID、显示名和权限集合。
- 当前设备会话 ID。

服务端要求：

- Refresh Token 只保存哈希或不可逆标识。
- 支持单设备会话吊销和全部设备退出。
- 登录、刷新和失败尝试需要限流与审计。
- 不允许使用默认的 `change-this-secret-key` 进入 Staging 或 Production。

### 7.2 GraphQL 认证

- HTTP 请求使用 `Authorization: Bearer <access_token>`。
- WebSocket 在 `connection_init` 参数中发送同一 Access Token。
- GraphQL Context 必须提供认证后的 Principal、授权账户集合和权限集合。
- 未认证请求默认拒绝，不以客户端传入的 `accountId` 作为授权依据。
- `accountId` 可继续作为筛选参数，但必须验证其属于当前 Principal。
- 标准错误扩展至少包含 `code`、`requestId`、`retryable`，不得向客户端返回敏感异常堆栈。

### 7.3 首版权限

```text
portfolio:read
market:read
strategy:read
orders:read
system-status:read
```

首版 Token 不包含 `trade:write`、`strategy:control` 或 `admin:*` 权限。即使客户端被篡改，也不能通过首版会话调用交易写接口。

---

## 8. GraphQL 契约与 Codegen

### 8.1 单一契约

仓库与发布包提供自动生成的 Schema 快照：

```text
apps/docs/public/contracts/graphql-schema.graphql
```

该文件由实际 QuantX 后端 Schema 生成，不手工维护。Web 前端和 iOS 都以它作为可审查的契约快照。

### 8.2 Schema 变更工作流

任何后端 Strawberry 类型、Resolver、Query、Mutation 或 Subscription 变化，必须在同一轮完成：

1. 后端主机只通过统一 `ops/quantx.ps1` 管理；API 私有端口为 `18081`，Caddy
   公共端口为 `8080`。macOS/iOS 工作区不在本地另启后端。
2. 从当前 macOS/iOS 工作区确认远端 Caddy
   `http://192.168.5.6:8080/health/live` 健康；只有在后端主机本机运行命令时才使用
   `127.0.0.1:8080`。
3. 前端以 `CODEGEN_GRAPHQL_ENDPOINT=http://192.168.5.6:8080/graphql` 运行
   `npm run codegen`；不得绕过 Caddy 使用 API 私有端口 `18081`。
4. 运行 `npm run docs:contracts`，刷新 SDL、权限和 Client OpenAPI。
5. 运行 Apollo iOS codegen，生成 Swift 类型。
6. 运行前端 `npm run check` 和 iOS 单元测试。
7. 审查契约 diff，禁止用 `as any`、手写 JSON 字典或 Swift 强制转换掩盖契约错误。

### 8.3 首版优先复用的查询

- `currentAccount`
- `positions`
- `portfolioSummary`
- `todayOrders` / `historyOrders`
- `todayTrades` / `historyTrades`
- `instruments`
- `klines` / `ticks`
- 策略模板、策略实例和策略运行状态相关查询
- `marketQuotes`、策略日志和策略行情等必要订阅

除认证、授权和标准错误结构外，首版优先复用现有 GraphQL 能力，不为移动端复制一套独立业务接口。

---

## 9. 安全与隐私要求

### 9.1 iPhone 本地

- Access Token 和 Refresh Token 只存 Keychain。
- UserDefaults 只保存主题、排序、脱敏开关等非敏感偏好。
- 支持 Face ID / Touch ID 本地解锁，但生物识别不替代服务端认证。
- 日志禁止包含 Token、账户号、完整持仓、订单原文和券商返回原文。
- App 切入后台时显示隐私遮罩。
- 登出时清除 Token、内存缓存、订阅和用户相关状态。

### 9.2 服务端和传输

- 只允许 TLS 连接，证书校验失败时不得降级继续访问。
- Release 不使用全局 ATS 例外，不关闭证书校验。
- 券商账号、交易密码、资金密码和 QMT 配置只保留在本地 Windows 执行端。
- Redis 只作缓存，不作为认证、交易状态或订单状态真源。
- miniQMT 委托与成交回报仍是实盘成交真源。

### 9.3 Apple 隐私要求

- 在 App Target 中加入有效的 `PrivacyInfo.xcprivacy`。
- 盘点 App 和第三方 SDK 使用的数据类型及 Required Reason API。
- 在 App Store Connect 填写与实际行为一致的隐私标签。
- 若未来支持 App 内创建账号，必须同时提供可发起账号删除的入口。

---

## 10. UI 与设计系统基线

- 采用原生 SwiftUI 组件、系统字体和 SF Symbols，不直接复刻桌面 Web 布局。
- 默认提供深色主题，兼顾 OLED 屏幕和低光环境；同时保证浅色模式可用。
- A 股默认红涨绿跌，并提供可访问性冗余信息。
- 数字使用等宽数字特性，金额、价格、数量和百分比对齐。
- 支持 Dynamic Type、VoiceOver、Reduce Motion 和高对比度。
- 触控区域不小于 iOS 推荐尺寸；危险状态与普通状态有清晰层级。
- 图表首版优先使用 Swift Charts 完成资产和分时趋势；K 线若能力不足，再实现独立 Canvas 组件，避免过早引入大型图表 SDK。

---

## 11. 分阶段实施顺序

### 阶段 0：macOS 环境就绪

- 安装稳定版 Xcode 和命令行工具。
- 登录 Apple Developer 账号。
- 建立 Bundle ID、签名和 TestFlight App。
- 确认 Mac 可以通过 VPN 访问 Staging GraphQL HTTP 和 WebSocket。

### 阶段 1：后端移动访问安全化

- 实现会话接口、JWT 签发、刷新和吊销。
- 为 GraphQL HTTP/WS 接入 Principal 和权限。
- 完成账户归属校验、限流、审计和错误结构。
- 建立共享 Schema 快照和双端 codegen 流程。

阶段验收：未认证、越权或缺少读权限的请求无法读取任何真实账户数据。

### 阶段 2：iOS 工程骨架

- 创建 SwiftUI App、环境配置和模块目录。
- 接入 Apollo iOS、HTTP、WebSocket 和 codegen。
- 实现登录、Keychain、Token 刷新、登出和本地生物识别解锁。
- 完成导航、主题、加载、空状态、错误和数据过期组件。

阶段验收：模拟器和真机都能安全登录、刷新 Token、恢复前台并退出登录。

### 阶段 3：只读业务 MVP

- 首页与账户概览。
- 持仓列表和详情。
- 策略实例和运行状态。
- 委托、成交列表和详情。
- 股票行情、分时/K 线和实时增量。
- 服务连接状态和 stale 提示。

阶段验收：所有展示数据都能追溯到后端查询或订阅，客户端不模拟账户事实。

### 阶段 4：可靠性与 TestFlight

- 完成弱网、断网、VPN 切换和 Token 过期处理。
- 验证 WebSocket 重连后的快照恢复。
- 完成隐私清单、日志脱敏、任务切换遮罩和无障碍检查。
- 上传 TestFlight，至少连续观察五个交易日。

阶段验收：无崩溃、无账户串读、无敏感日志、无把委托状态误写为成交的问题。

### 阶段 5：后续增强

- APNs 告警。
- 小组件或 Live Activity 的只读状态展示。
- iPad 自适应布局。
- 经独立安全和合规评审后的交易写入能力。

---

## 12. 测试与验收标准

### 12.1 后端

- [ ] 登录成功、失败、限流、刷新、轮换和吊销测试。
- [ ] HTTP 与 WebSocket 未认证拒绝测试。
- [ ] 不同用户/账户之间的越权访问测试。
- [ ] 过期 Token、被吊销 Token 和权限不足测试。
- [ ] GraphQL Schema 快照和 Web/iOS 双端 codegen 校验。
- [ ] 错误响应不泄漏异常堆栈或敏感字段。

### 12.2 iOS 单元与集成测试

- [ ] GraphQL 模型到 Domain Model 的映射。
- [ ] 金额、价格、数量、盈亏和时间格式化。
- [ ] Token 刷新并发去重、失败登出和会话恢复。
- [ ] WebSocket 断线、重连、重复事件和乱序事件。
- [ ] 快照刷新与订阅增量合并。
- [ ] 空账户、无持仓、无订单、服务不可用和数据过期。

### 12.3 真机和 UI

- [ ] 深色/浅色、Dynamic Type、VoiceOver 和 Reduce Motion。
- [ ] Wi-Fi/蜂窝/VPN 切换、弱网和完全断网。
- [ ] App 前后台切换、系统回收后冷启动和 Token 过期。
- [ ] 任务切换器中不暴露敏感金额或账号。
- [ ] 不同尺寸 iPhone 无截断、溢出或不可点击区域。
- [ ] TestFlight 连续运行五个交易日。

### 12.4 交易安全

- 首版不调用任何交易 Mutation。
- 不执行真实交易 E2E。
- 不把真实交易测试加入默认测试、CI 或普通验证流程。
- 不把策略信号、pending、accepted 或下单成功展示为成交。

---

## 13. 未来开放交易写入的独立门槛

只有只读版本稳定后，才允许另立计划评估交易功能。至少需要：

- 独立的 `trade:write` 和 `strategy:control` 权限。
- 下单前服务端预览，返回合法数量、预估金额、风险原因和报价时间。
- 用户确认时提交服务端签发的短期确认令牌和幂等键。
- Face ID/Touch ID 只作为本地二次确认，最终权限仍由服务端判断。
- 严格处理 T+1、可卖量、100 股整数倍、涨跌停、停牌、资金和冻结。
- Kill Switch、设备会话吊销、异常告警和完整 DecisionTrace。
- 委托状态、部分成交和全部成交严格以 miniQMT 回报为准。
- 公开上架前完成主体、券商授权、金融服务资质和适用地区法律审查。

未经上述评审，不在 iOS 中加入隐藏交易入口或通过客户端开关启用写操作。

---

## 14. macOS 开工检查表

后续切换到 macOS 环境后，按以下顺序开始：

1. [ ] 更新本文档中的 Xcode、iOS SDK 和 Apple 提交要求。
2. [ ] 确认 Apple Developer 账号、Bundle ID 和签名可用。
3. [ ] 确认 Staging 后端已完成认证，且只能通过 VPN + TLS 访问。
4. [ ] 确认 GraphQL Schema 与现有 Web 查询一致。
5. [ ] 创建 `ios/QuantX.xcodeproj`、测试 Targets 和 `.xcconfig`。
6. [ ] 接入并锁定 Apollo iOS 版本。
7. [ ] 先完成登录和只读账户查询，再开发其他页面。
8. [ ] 在真实 iPhone 上验证前后台切换和网络恢复。
9. [ ] 完成安全、隐私和 TestFlight 验收后再讨论后续能力。

---

## 15. 参考资料

- Apple Developer Program：<https://developer.apple.com/programs/whats-included/>
- Apple App 提交要求：<https://developer.apple.com/app-store/submitting/>
- App Review Guidelines：<https://developer.apple.com/app-store/review/guidelines/>
- App Transport Security：<https://developer.apple.com/documentation/security/preventing-insecure-network-connections>
- Keychain Services：<https://developer.apple.com/documentation/security/keychain-services>
- Privacy Manifest：<https://developer.apple.com/documentation/bundleresources/privacy-manifest-files>
- iOS 后台执行：<https://developer.apple.com/documentation/xcode/configuring-background-execution-modes>
- APNs 服务端：<https://developer.apple.com/documentation/usernotifications/setting-up-a-remote-notification-server>
- Apollo iOS：<https://www.apollographql.com/docs/ios>
- Apollo iOS Codegen：<https://www.apollographql.com/docs/ios/code-generation/introduction>

项目内相关文档：

- [系统架构设计](../architecture/系统架构设计.md)
- [A 股交易域数据结构与状态机](../trading/contracts/A股交易域数据结构与状态机.md)
- [A 股三层协作与执行契约](../trading/contracts/A股三层协作与执行契约.md)
- [API 架构](../engineering/api/ARCHITECTURE.md)
- [API 文档](../engineering/api/API.md)
