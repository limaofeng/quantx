# QuantX iOS 需求追踪矩阵

> 基线日期：2026-08-15
>
> 本表状态是对旧监控型客户端的起点审计，不是目标版本完成声明。

## 1. 使用规则

每个 PR 必须更新受影响行的接口、权限、验证和状态。`DONE` 需要自动化与适用的
人工证据同时存在；尚未发布的目标接口仍标 `CONTRACT_GAP`，即使客户端可以调用
相近的兼容 Mutation。

### 1.1 测试套件 ID

| ID | 覆盖内容 |
| --- | --- |
| `T-SESSION` | 原生会话、Keychain、刷新、吊销、设备 scope 与主账户 |
| `T-AUTHZ` | GraphQL 根字段权限、账户/资源归属与默认拒绝 |
| `T-NAV` | 五 Tab、路由、深链、登出清栈与设置入口 |
| `T-TODAY` | 今日摘要、行动排序、风险与过期终态 |
| `T-MARKET` | 搜索、自选、行情、图表、盘口、增量和数据质量 |
| `T-ORDER` | 票据、预览确认、幂等、订单状态和撤单竞态 |
| `T-LIQ` | 清仓组、ExitPlan、条件触发、冲突、T+1 和部分成交 |
| `T-STRATEGY` | 生命周期、DRAINING、参数 allowlist/version 与实盘控制 |
| `T-TTRADE` | 做 T 设置、信号、批次、退出、readiness 与 Kill Switch |
| `T-LIMITUP` | 打板候选、布防、批准、T+1 与退出计划 |
| `T-ASSET` | 资产曲线、持仓/bucket、贡献、周期和数据质量 |
| `T-PUSH` | APNs 注册、偏好、payload、解锁路由、过期与注销 |
| `T-SECURITY` | 挑战攻击、敏感扫描、隐私遮罩和凭证边界 |
| `T-RECOVERY` | 弱网、快照/增量、乱序、Token、进程重启和性能时间线 |
| `T-A11Y` | 设备/主题/字号、VoiceOver、对比度、触控和 Reduce Motion |
| `T-RELEASE` | codegen、根测试、构建、TestFlight、paper 与实盘门禁 |

### 1.2 证据 ID

| ID | 发布报告中的证据 |
| --- | --- |
| `E-AUTO` | CI/本地自动化结果与契约 diff |
| `E-UI` | 脱敏截图、视频、Accessibility Audit 与设备矩阵 |
| `E-SEC` | 授权/攻击矩阵、日志扫描和隐私报告 |
| `E-PAPER` | paper 逐事件审计闭环 |
| `E-TF5` | 五个连续交易日 TestFlight 记录 |
| `E-LIVE` | 人工监督 Canary 的意图—命令—券商回报链路 |

## 2. 产品、导航与今日

| 需求 ID | 页面/行为 | 接口或状态源 | 目标权限 | 验证/证据 | 里程碑 | 基线状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `IOS-PLT-001` | 全 App 个人量化闭环 | 本表全部接口 | 按能力拆分 | `T-RELEASE` / `E-AUTO,E-PAPER,E-TF5,E-LIVE` | M6 | `PARTIAL` |
| `IOS-PLT-002` | App/网络/分发 | `/health`、环境配置 | 会话外 | `T-SESSION,T-RELEASE` / `E-AUTO,E-TF5` | M1/M6 | `PARTIAL` |
| `IOS-PLT-003` | 登录与全局账户上下文 | `/auth/session.activeAccountId`（新增） | 会话 scope | `T-SESSION,T-AUTHZ` / `E-AUTO,E-SEC` | M1 | `CONTRACT_GAP` |
| `IOS-PLT-004` | 所有交易状态 | outbox/inbox、Engine、QMT 投影 | 按操作 | `T-ORDER,T-LIQ,T-SECURITY` / `E-AUTO,E-PAPER` | M3 | `PARTIAL` |
| `IOS-NAV-001` | 五 Tab | App Route | 本地 + 读权限 | `T-NAV,T-A11Y` / `E-UI` | M1 | `GAP` |
| `IOS-NAV-002` | 资产 > 设置 | App Route、`GET/DELETE /auth/session` | 会话 | `T-NAV,T-SESSION` / `E-UI` | M1 | `GAP` |
| `IOS-NAV-003` | 类型安全路由/通知深链 | event resolver（新增） | 对象对应读权限 | `T-NAV,T-PUSH,T-AUTHZ` / `E-AUTO,E-UI` | M1/M5 | `GAP` |
| `IOS-TDY-001` | 今日账户摘要 | `currentAccount`、`portfolioSummary` | `portfolio:read` | `T-TODAY,T-ASSET` / `E-AUTO,E-UI` | M2 | `PARTIAL` |
| `IOS-TDY-002` | 行动收件箱 | action inbox Query（新增） | 多读权限按项过滤 | `T-TODAY,T-AUTHZ` / `E-AUTO,E-UI` | M2 | `GAP` |
| `IOS-TDY-003` | 量化/风险摘要 | 策略、助手、`liveSafetyStatus`、alerts | `strategy:read`,`system-status:read` | `T-TODAY,T-STRATEGY` / `E-AUTO,E-UI` | M2 | `PARTIAL` |

## 3. 行情

| 需求 ID | 页面/行为 | 接口或状态源 | 目标权限 | 验证/证据 | 里程碑 | 基线状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `IOS-MKT-001` | 搜索/自选/持仓行情 | `instrumentsConnection`、`watchlist`、watchlist Mutations | `market:read`,`portfolio:read`,`watchlist:write` | `T-MARKET,T-AUTHZ` / `E-AUTO,E-UI` | M2 | `GAP` |
| `IOS-MKT-002` | 股票行情头部 | `instrument`、`latestMarketQuotes`、`marketQuotes` | `market:read` | `T-MARKET` / `E-AUTO,E-UI` | M2 | `GAP` |
| `IOS-MKT-003` | 分时/日 K/周 K | `klinesPage`、`marketKlines` | `market:read` | `T-MARKET,T-A11Y` / `E-AUTO,E-UI` | M2 | `GAP` |
| `IOS-MKT-004` | 五档/逐笔/市场状态 | `marketDepth`、`ticks`、`marketTicks` | `market:read` | `T-MARKET,T-RECOVERY` / `E-AUTO,E-UI` | M2 | `GAP` |
| `IOS-MKT-005` | 行情到交易/量化上下文 | App Route、`orderEntryCapabilities`（新增） | `market:read` + 目标权限 | `T-NAV,T-MARKET,T-ORDER` / `E-AUTO,E-UI` | M2/M3 | `GAP` |

## 4. 交易与卖出

| 需求 ID | 页面/行为 | 接口或状态源 | 目标权限 | 验证/证据 | 里程碑 | 基线状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `IOS-TRD-001` | 手动买卖票据 | `previewManualOrder/confirmManualOrder`（新增） | `trade:manual` | `T-ORDER,T-AUTHZ` / `E-AUTO,E-PAPER,E-LIVE` | M3 | `CONTRACT_GAP` |
| `IOS-TRD-002` | 限价/沪深对手方最优价（北交所拒绝） | `orderEntryCapabilities`（新增） | `market:read`,`trade:manual` | `T-MARKET,T-ORDER` / `E-AUTO,E-PAPER` | M3 | `CONTRACT_GAP` |
| `IOS-TRD-003` | 预览—生物—确认 | 手工挑战服务（新增） | `trade:manual` | `T-ORDER,T-SECURITY` / `E-AUTO,E-SEC,E-LIVE` | M3 | `CONTRACT_GAP` |
| `IOS-TRD-004` | 订单状态时间线 | orders/trades、`tradingEvents` | `orders:read` | `T-ORDER,T-RECOVERY` / `E-AUTO,E-PAPER,E-LIVE` | M3 | `PARTIAL` |
| `IOS-TRD-005` | 撤单及竞态 | `cancelOrder`（收紧）、订单回报 | `trade:manual`,`orders:read` | `T-ORDER,T-AUTHZ` / `E-AUTO,E-PAPER,E-LIVE` | M3 | `CONTRACT_GAP` |
| `IOS-TRD-006` | 单只/选中清仓 | `previewLiquidation/confirmLiquidation`（新增） | `liquidation:control`,`trade:approve` | `T-LIQ,T-SECURITY` / `E-AUTO,E-PAPER,E-LIVE` | M3 | `CONTRACT_GAP` |
| `IOS-TRD-007` | 全仓清仓 | 同上，`scope=ALL` | `liquidation:control`,`trade:approve` | `T-LIQ,T-SECURITY` / `E-AUTO,E-PAPER,E-LIVE` | M3 | `CONTRACT_GAP` |
| `IOS-TRD-008` | 条件/自动退出 | ExitPlan APIs、plan authorization（新增） | `liquidation:control`,`trade:approve` | `T-LIQ,T-SECURITY` / `E-AUTO,E-PAPER,E-LIVE` | M3 | `CONTRACT_GAP` |
| `IOS-TRD-009` | 风控原因与审计 | preview result、reason codes、trace | 对应交易权限 | `T-ORDER,T-LIQ,T-SECURITY` / `E-AUTO,E-SEC` | M3 | `PARTIAL` |
| `IOS-TRD-010` | 委托/成交/卖出管理 | today/history orders/trades、ExitPlan Queries | `orders:read` | `T-ORDER,T-LIQ` / `E-AUTO,E-UI` | M3 | `PARTIAL` |

## 5. 量化控制中心

| 需求 ID | 页面/行为 | 接口或状态源 | 目标权限 | 验证/证据 | 里程碑 | 基线状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `IOS-QNT-001` | 策略列表/详情/审计 | strategy instance/performance/decision/trace Queries | `strategy:read` | `T-STRATEGY` / `E-AUTO,E-UI` | M4 | `PARTIAL` |
| `IOS-QNT-002` | 生命周期/DRAINING | pause/resume/stop instance（收紧） | `strategy:control` | `T-STRATEGY,T-AUTHZ` / `E-AUTO,E-PAPER` | M4 | `CONTRACT_GAP` |
| `IOS-QNT-003` | 安全参数与版本 | `strategyInstanceMobileParameters`（新增）、update parameters（收紧） | `strategy:read`,`strategy:control` | `T-STRATEGY,T-AUTHZ` / `E-AUTO,E-UI` | M4 | `CONTRACT_GAP` |
| `IOS-QNT-004` | 进入实盘/实盘启动 | `preview/confirmStrategyControl`（新增） | `strategy:control`,`trade:approve` | `T-STRATEGY,T-SECURITY` / `E-AUTO,E-PAPER,E-LIVE` | M4 | `CONTRACT_GAP` |
| `IOS-TTR-001` | 做 T 配置/范围/readiness | `tTradeGlobalMonitor`、设置/控制 Mutations（收紧） | `strategy:read`,`t-trade:control` | `T-TTRADE,T-AUTHZ` / `E-AUTO,E-UI,E-PAPER` | M4 | `PARTIAL` |
| `IOS-TTR-002` | 做 T 信号批准 | preview/confirm TTrade approval | `strategy:read`,`trade:approve` | `T-TTRADE,T-SECURITY` / `E-AUTO,E-PAPER,E-LIVE` | M4 | `PARTIAL` |
| `IOS-TTR-003` | 批次/退出/熔断 | batch/event Queries、`tTradeUpdates`、Kill Switch | `strategy:read`,`t-trade:control`,`trade:approve` | `T-TTRADE,T-RECOVERY` / `E-AUTO,E-PAPER,E-LIVE` | M4 | `PARTIAL` |
| `IOS-LUB-001` | 候选/阶段/布防 | radar/assistant Queries、arm/disarm（收紧） | `strategy:read`,`market:read`,`limit-up:control` | `T-LIMITUP,T-AUTHZ` / `E-AUTO,E-UI,E-PAPER` | M4 | `PARTIAL` |
| `IOS-LUB-002` | 打板买入批准 | preview/confirm strategy intent approval | `strategy:read`,`trade:approve` | `T-LIMITUP,T-SECURITY` / `E-AUTO,E-PAPER,E-LIVE` | M4 | `PARTIAL` |
| `IOS-LUB-003` | T+1/退出/readiness | strategy exit plans、assistant projection | `strategy:read`,`orders:read` | `T-LIMITUP,T-LIQ` / `E-AUTO,E-PAPER,E-LIVE` | M4 | `PARTIAL` |

## 6. 资产与通知

| 需求 ID | 页面/行为 | 接口或状态源 | 目标权限 | 验证/证据 | 里程碑 | 基线状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `IOS-AST-001` | 账户与资产/收益曲线 | account/portfolio/daily snapshots | `portfolio:read` | `T-ASSET,T-RECOVERY` / `E-AUTO,E-UI` | M2 | `PARTIAL` |
| `IOS-AST-002` | 持仓/可卖量/bucket | positions、strategyBucketLedger | `portfolio:read`,`strategy:read` | `T-ASSET` / `E-AUTO,E-UI` | M2 | `PARTIAL` |
| `IOS-AST-003` | 策略贡献/复盘 | performance、closed cycles、execution trace | `portfolio:read`,`strategy:read` | `T-ASSET` / `E-AUTO,E-UI` | M2 | `GAP` |
| `IOS-NTF-001` | APNs 类别与偏好 | push device/preferences Mutations（新增） | `notification:manage` | `T-PUSH,T-AUTHZ` / `E-AUTO,E-TF5` | M5 | `GAP` |
| `IOS-NTF-002` | 最小隐私 payload | notification event publisher（新增） | 服务端 + `notification:manage` | `T-PUSH,T-SECURITY` / `E-AUTO,E-SEC,E-UI` | M5 | `GAP` |
| `IOS-NTF-003` | 解锁深链与真实终态 | event resolver（新增）、目标 Query | 对象对应读权限 | `T-PUSH,T-NAV,T-SESSION` / `E-AUTO,E-TF5` | M5 | `GAP` |

## 7. 安全、体验、可靠性与发布

| 需求 ID | 页面/行为 | 接口或状态源 | 目标权限 | 验证/证据 | 里程碑 | 基线状态 |
| --- | --- | --- | --- | --- | --- | --- |
| `IOS-SEC-001` | 设备最小权限 | native session、GraphQL permission map | 全部专用 scope | `T-SESSION,T-AUTHZ` / `E-AUTO,E-SEC` | M1 | `CONTRACT_GAP` |
| `IOS-SEC-002` | 单主账户/跨账户拒绝 | session active account、所有账户 Resolver | 所有账户 scope | `T-SESSION,T-AUTHZ` / `E-AUTO,E-SEC` | M1 | `PARTIAL` |
| `IOS-SEC-003` | 挑战绑定/幂等 | approval challenge、outbox/idempotency | 交易写 scope | `T-ORDER,T-LIQ,T-SECURITY` / `E-AUTO,E-SEC` | M3 | `PARTIAL` |
| `IOS-SEC-004` | 逐次生物确认 | LocalAuthentication + confirm Mutations | `trade:manual/approve` 等 | `T-SECURITY,T-ORDER,T-STRATEGY` / `E-AUTO,E-UI,E-LIVE` | M3/M4 | `PARTIAL` |
| `IOS-SEC-005` | QMT/券商隔离 | 依赖边界、二进制/日志扫描 | 无 iOS 权限 | `T-SECURITY,T-RELEASE` / `E-AUTO,E-SEC` | M1-M6 | `PARTIAL` |
| `IOS-SEC-006` | 审计与关联 ID | request/trace/business event IDs | 对应业务 scope | `T-SECURITY,T-RECOVERY` / `E-AUTO,E-SEC` | M3/M5 | `PARTIAL` |
| `IOS-SEC-007` | 风险阻断 | live safety、readiness、Kill Switch、freshness | 多只读 + 控制 scope | `T-ORDER,T-STRATEGY,T-RECOVERY` / `E-AUTO,E-PAPER,E-LIVE` | M3/M4 | `PARTIAL` |
| `IOS-UX-001` | 主题/字号/VoiceOver | Design System | 本地 | `T-A11Y` / `E-UI` | M1-M6 | `PARTIAL` |
| `IOS-UX-002` | 红涨绿跌/数字格式 | Design System、formatters | 本地 | `T-A11Y,T-ASSET,T-MARKET` / `E-AUTO,E-UI` | M1/M2 | `PARTIAL` |
| `IOS-UX-003` | 全状态覆盖 | Feature state model | 对应读写权限 | 各功能套件 / `E-AUTO,E-UI` | M1-M5 | `PARTIAL` |
| `IOS-REL-001` | 快照优先/订阅恢复 | GraphQL HTTP/WS、Feature Store | 各读权限 | `T-RECOVERY` / `E-AUTO,E-TF5` | M2/M5 | `PARTIAL` |
| `IOS-REL-002` | 端到端时间线 | client/API/Engine/Agent telemetry | 脱敏诊断 | `T-RECOVERY,T-RELEASE` / `E-AUTO,E-TF5,E-LIVE` | M5 | `GAP` |
| `IOS-REL-003` | 日志/截图/后台隐私 | Keychain、OSLog、privacy shield | 本地/会话 | `T-SECURITY,T-A11Y` / `E-AUTO,E-SEC,E-UI` | M1/M5 | `PARTIAL` |
| `IOS-RLS-001` | 双端 codegen/自动化 | SDL、permission JSON、OpenAPI、Apollo/Web | 构建权限 | `T-RELEASE` / `E-AUTO` | 每轮 | `PARTIAL` |
| `IOS-RLS-002` | 五日 TestFlight | Release candidate + production-like services | 目标设备 scope | `T-RELEASE,T-RECOVERY` / `E-TF5` | M6 | `GAP` |
| `IOS-RLS-003` | paper 后受控实盘 | 全交易链路与安全门禁 | 显式实盘权限 | `T-RELEASE,T-SECURITY` / `E-PAPER,E-LIVE` | M6 | `GAP` |

## 8. 更新检查

修改本表后执行以下一致性检查：

1. PRD 中每个 `IOS-<DOMAIN>-NNN` 在本表恰好出现一次。
2. 每行至少有页面/行为、接口/状态源、权限、测试和证据。
3. 新接口同时出现在 API 安全契约、SDL/权限 diff 和路线图里。
4. 标为 `DONE` 的行能在最新 `docs/product/ios/releases/<version>.md` 找到证据。
5. 被替换需求不删除；在 PRD 和本表标记 `RETIRED` 并链接新 ID。
