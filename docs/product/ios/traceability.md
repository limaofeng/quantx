# QuantX iOS 需求追踪矩阵

> 实施快照：2026-08-15，目标候选版本 `0.1.0-rc1`
>
> 详细自动化结果、阻断项和人工证据缺口见
> [0.1.0-rc1 发布报告](releases/0.1.0-rc1.md)。本表描述当前实现成熟度，
> 不代表 TestFlight 或实盘放行。

## 1. 状态与证据规则

| 状态 | 含义 |
| --- | --- |
| `IMPLEMENTED_AUTO` | 目标代码/契约已实现，且已有针对性自动化证据；仍可能缺真机、paper、TestFlight 或实盘证据。 |
| `PARTIAL` | 已有可用实现，但需求中的子流程、端到端证据或人工验收仍不完整。 |
| `BLOCKED` | 当前被测试环境、外部服务、凭据、设备或前置门禁阻断。 |
| `PENDING` | 尚未实现，或要求的验收活动尚未开始。 |
| `DONE` | 自动化与所有适用人工证据齐全，并已在发布报告签署。 |

本候选版本没有任何需求标记为 `DONE`。特别是 `IMPLEMENTED_AUTO` 只说明代码和
针对性自动化成立，不能代替模拟盘、真实 APNs、五日 TestFlight 或受控实盘。

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
| `E-AUTO` | 针对性单元/契约测试、构建和契约 diff；本轮已有部分成功证据 |
| `E-UI` | 脱敏截图、视频、Accessibility Audit 与真实设备矩阵；本轮不完整 |
| `E-SEC` | 授权/攻击矩阵、日志扫描和隐私报告；本轮仅部分自动化 |
| `E-PAPER` | paper 逐事件审计闭环；本轮未提供 |
| `E-APNS` | 真实 APNs 沙箱/生产设备送达与路由；本轮未提供 |
| `E-TF5` | 五个连续 A 股交易日 TestFlight 记录；本轮未提供 |
| `E-LIVE` | 人工监督 Canary 的意图—命令—券商回报链路；本轮未提供 |

## 2. 产品、导航与今日

| 需求 ID | 页面/行为与实现 | 接口/权限 | 验证/证据 | RC1 状态 |
| --- | --- | --- | --- | --- |
| `IOS-PLT-001` | 已从监控壳层转向个人量化五入口，并实现主要查看、手工交易、清仓和部分量化控制；复盘、部分助手控制及发布闭环仍缺。 | 本表全部接口；按能力拆分 | `T-RELEASE` / `E-AUTO`；缺 `E-PAPER,E-TF5,E-LIVE` | `PARTIAL` |
| `IOS-PLT-002` | SwiftUI/iOS 17+、Apollo 和环境配置已落地；个人 VPN、真实设备和 TestFlight 分发未验证。 | `/health`、HTTPS/WSS 环境配置；会话外 | `T-SESSION,T-RELEASE` / 部分 `E-AUTO`；缺 `E-TF5` | `PARTIAL` |
| `IOS-PLT-003` | 原生会话绑定唯一 `activeAccountId`，客户端对账户集合歧义和跨账户响应 fail-closed。 | `/auth/session`；会话 scope | `T-SESSION,T-AUTHZ` / `E-AUTO` | `IMPLEMENTED_AUTO` |
| `IOS-PLT-004` | 订单、清仓、策略与助手写入继续复用服务端交易域、outbox/inbox 和 QMT 回报真源；尚无全功能 paper/实盘链路证据。 | Engine/QMT 投影；按操作 scope | `T-ORDER,T-LIQ,T-SECURITY` / `E-AUTO`；缺 `E-PAPER,E-LIVE` | `PARTIAL` |
| `IOS-NAV-001` | 固定“今日/行情/交易/量化/资产”五 Tab 已实现，图标改为单色语义。 | `AppTab`；本地 + 读权限 | `T-NAV,T-A11Y` / `E-AUTO`；真实设备矩阵待补 | `IMPLEMENTED_AUTO` |
| `IOS-NAV-002` | 设置、会话、通知偏好和版本从资产页账户入口进入。 | App Route、`GET/DELETE /auth/session`；会话 | `T-NAV,T-SESSION` / `E-AUTO` | `IMPLEMENTED_AUTO` |
| `IOS-NAV-003` | 通知采用强类型 route enum，先解锁、服务端解析再导航；目前主要落到工作区，尚未覆盖每个业务对象详情路由。 | `notificationEventRoute`；对象读权限 | `T-NAV,T-PUSH,T-AUTHZ` / 部分 `E-AUTO` | `PARTIAL` |
| `IOS-TDY-001` | 今日账户摘要、数据时间和风险状态已有真实查询投影，无固定模拟账户。 | `currentAccount`、`portfolioSummary`；`portfolio:read` | `T-TODAY,T-ASSET` / `E-AUTO` | `IMPLEMENTED_AUTO` |
| `IOS-TDY-002` | 行动收件箱按风险排序并区分非权威同步态，但当前由已授权业务快照汇总，缺独立、可分页的服务端 action inbox 投影。 | 策略/助手/订单/安全快照；多读权限 | `T-TODAY,T-AUTHZ` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-TDY-003` | 已展示策略、助手与安全摘要，并区分准备/阻断语义；全链路 Agent、行情、对账摘要仍需真机验证。 | 策略、助手、`liveSafetyStatus`；`strategy:read`,`system-status:read` | `T-TODAY,T-STRATEGY` / 局部 `E-AUTO` | `PARTIAL` |

## 3. 行情

| 需求 ID | 页面/行为与实现 | 接口/权限 | 验证/证据 | RC1 状态 |
| --- | --- | --- | --- | --- |
| `IOS-MKT-001` | 搜索、自选查询以及新增/删除/排序的账户隔离写入已实现；写失败保守恢复。 | `instrumentsConnection`、watchlist Queries/Mutations；`market:read`,`portfolio:read`,`watchlist:write` | `T-MARKET,T-AUTHZ` / `E-AUTO` | `IMPLEMENTED_AUTO` |
| `IOS-MKT-002` | 股票头部、OHLC、量额、涨跌停/停牌和新鲜度展示已实现。 | `instrument`、`latestMarketQuotes`；`market:read` | `T-MARKET` / `E-AUTO` | `IMPLEMENTED_AUTO` |
| `IOS-MKT-003` | 分时、日 K、周 K 与数据质量展示已实现；完整真机图表无障碍逐点读取仍待人工矩阵。 | `klinesPage`、`marketKlines`；`market:read` | `T-MARKET,T-A11Y` / `E-AUTO`；`E-UI` 不完整 | `PARTIAL` |
| `IOS-MKT-004` | 五档、逐笔/行情增量和缺失态已接入，未知值不伪装为零；弱网乱序/午休完整故障注入未验收。 | `marketDepth`、`ticks`、行情 Subscriptions；`market:read` | `T-MARKET,T-RECOVERY` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-MKT-005` | 行情可进入下单且以服务端 `orderEntryCapabilities` 决定报价能力；到持仓、策略、做 T/打板的全对象上下文尚不完整。 | App Route、`orderEntryCapabilities`；`market:read` + 目标权限 | `T-NAV,T-MARKET,T-ORDER` / 局部 `E-AUTO` | `PARTIAL` |

## 4. 交易与卖出

| 需求 ID | 页面/行为与实现 | 接口/权限 | 验证/证据 | RC1 状态 |
| --- | --- | --- | --- | --- |
| `IOS-TRD-001` | PAPER-first 手工 BUY/SELL 票据、服务端预览和确认已实现；没有 paper/券商逐事件验收。 | `previewManualOrder/confirmManualOrder`；`trade:manual` | `T-ORDER,T-AUTHZ` / `E-AUTO`；缺 `E-PAPER,E-LIVE` | `IMPLEMENTED_AUTO` |
| `IOS-TRD-002` | 仅允许 `LIMIT` 与服务端声明的沪深 `BEST`；北交所/未知市场隐藏并拒绝，不回退通用市价。 | `orderEntryCapabilities`；`market:read`,`trade:manual` | `T-MARKET,T-ORDER` / `E-AUTO` | `IMPLEMENTED_AUTO` |
| `IOS-TRD-003` | LIVE 每次执行预览—核对—生物—确认，响应模式和挑战上下文精确绑定。 | 手工订单 challenge；`trade:manual` | `T-ORDER,T-SECURITY` / `E-AUTO`；缺 `E-LIVE` | `IMPLEMENTED_AUTO` |
| `IOS-TRD-004` | 已区分已排队、投递、券商状态、部分/全部成交、拒单等事实层；未完成 paper 与真实 Agent 回报时间线验收。 | orders/trades、`tradingEvents`；`orders:read` | `T-ORDER,T-RECOVERY` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-TRD-005` | 仅对最新可撤活动委托开放撤单；使用稳定幂等键、处理超时重试及成交竞态，撤单不要求生物确认。 | `cancelOrder`、订单回报；`trade:manual`,`orders:read` | `T-ORDER,T-AUTHZ` / `E-AUTO`；缺 `E-PAPER,E-LIVE` | `IMPLEMENTED_AUTO` |
| `IOS-TRD-006` | 单只/选中清仓、`AVAILABLE_NOW`/持续快照范围、冲突/T+1/部分结果已实现；LIVE 逐次生物确认。 | `previewLiquidation/confirmLiquidation`；`liquidation:control`,`trade:approve` | `T-LIQ,T-SECURITY` / `E-AUTO`；缺 `E-PAPER,E-LIVE` | `IMPLEMENTED_AUTO` |
| `IOS-TRD-007` | 全仓仅在二级高风险入口，预览逐只列入/跳过/冲突且挑战绑定确认快照。 | 同上，`scope=ALL`；同上 | `T-LIQ,T-SECURITY` / `E-AUTO`；缺 `E-PAPER,E-LIVE` | `IMPLEMENTED_AUTO` |
| `IOS-TRD-008` | 服务端已实现规则/保护量/配置版本精确实盘授权及失效降级；iOS 列表、详情、容量/审计和逐次生物授权已通过目标自动化，但 v1 创建/编辑规则界面仍缺。 | ExitPlan APIs、plan authorization；`liquidation:control`,`trade:approve` | `T-LIQ,T-SECURITY` / iOS `23/23` 与后端 `E-AUTO`；`E-PAPER,E-LIVE` 不完整 | `PARTIAL` |
| `IOS-TRD-009` | 订单/清仓预览可显示限量、拒绝、冲突和阻断原因；跨全部风控来源的统一可理解下一步及关联 ID 尚未齐全。 | preview reason codes、trace；对应交易权限 | `T-ORDER,T-LIQ,T-SECURITY` / 局部 `E-AUTO,E-SEC` | `PARTIAL` |
| `IOS-TRD-010` | 委托、成交和卖出计划读取面已存在；ExitPlan 客户端列表/详情已落地，完整历史筛选与创建/编辑自动化仍缺。 | orders/trades、ExitPlan Queries；`orders:read`,`liquidation:control` | `T-ORDER,T-LIQ` / 局部 `E-AUTO` | `PARTIAL` |

## 5. 量化控制中心

| 需求 ID | 页面/行为与实现 | 接口/权限 | 验证/证据 | RC1 状态 |
| --- | --- | --- | --- | --- |
| `IOS-QNT-001` | 策略实例、模式、生命周期和部分状态/审计读取已实现；完整收益风险、DecisionTrace 与 ExitPlan 联动详情未齐全。 | strategy instance/performance/trace Queries；`strategy:read` | `T-STRATEGY` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-QNT-002` | PAPER pause/resume 与 LIVE pause/start/resume/clone 控制已接入；显式 stop、DRAINING 及退出保护联动仍是产品缺口。 | pause/resume + live control APIs；`strategy:control` | `T-STRATEGY,T-AUTHZ` / 局部 `E-AUTO`；缺 `E-PAPER` | `PARTIAL` |
| `IOS-QNT-003` | 移动参数 allowlist、类型/范围与 `expectedVersion` 已有针对性测试；冲突后保留草稿、展示差异和安全重提正在最终集成验证。 | `strategyInstanceMobileParameters`、update parameters；`strategy:read`,`strategy:control` | `T-STRATEGY,T-AUTHZ` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-QNT-004` | LIVE start/resume/clone 使用服务端 readiness 预览、逐次生物确认和精确 challenge；未做实盘。 | `preview/confirmStrategyControl`；`strategy:control`,`trade:approve` | `T-STRATEGY,T-SECURITY` / `E-AUTO`；缺 `E-LIVE` | `IMPLEMENTED_AUTO` |
| `IOS-TTR-001` | 服务端与 iOS 已有 monitor/readiness、BEGIN/CANARY/LIVE 控制和停止新增入场；安全参数、范围、忽略列表等完整移动设置仍缺。 | `tTradeGlobalMonitor`、control APIs；`strategy:read`,`t-trade:control` | `T-TTRADE,T-AUTHZ` / 局部 `E-AUTO`；缺 `E-PAPER` | `PARTIAL` |
| `IOS-TTR-002` | 做 T 待确认信号沿用服务端预览、短时挑战和逐次生物确认；未取得 20 个 paper 或受控实盘证据。 | TTrade approval APIs；`strategy:read`,`trade:approve` | `T-TTRADE,T-SECURITY` / `E-AUTO`；缺 `E-PAPER,E-LIVE` | `IMPLEMENTED_AUTO` |
| `IOS-TTR-003` | 批次/事件投影与设备绑定 Kill Switch 契约已实现；Kill 可在普通 readiness 改变后继续降险，账户/会话/原因改变则拒绝。完整 DRAINING/退出 UI 与 paper 链路未完成。 | batch/event Queries、TTrade controls；`strategy:read`,`t-trade:control`,`trade:approve` | `T-TTRADE,T-RECOVERY` / 局部 `E-AUTO`；缺 `E-PAPER,E-LIVE` | `PARTIAL` |
| `IOS-LUB-001` | 已有候选/助手投影和基础控制读取；完整候选阶段、预算、布防/取消移动工作区与 paper 证据未完成。 | radar/assistant Queries、arm/disarm；`strategy:read`,`market:read`,`limit-up:control` | `T-LIMITUP,T-AUTHZ` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-LUB-002` | 已有助手意图预览和生物确认基础；完整打板专用风险展示、paper 20 闭环和实盘均缺。 | strategy intent approval；`strategy:read`,`trade:approve` | `T-LIMITUP,T-SECURITY` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-LUB-003` | 服务端已有 T+1/退出计划事实，移动端仅部分投影；部分退出、失败、readiness 的完整闭环未验收。 | strategy ExitPlan/assistant projection；`strategy:read`,`orders:read` | `T-LIMITUP,T-LIQ` / 局部 `E-AUTO` | `PARTIAL` |

## 6. 资产与通知

| 需求 ID | 页面/行为与实现 | 接口/权限 | 验证/证据 | RC1 状态 |
| --- | --- | --- | --- | --- |
| `IOS-AST-001` | 账户、总资产、资金、市值和盈亏摘要已实现；可选区间资产/收益曲线、入金口径和缺点说明尚未实现。 | account/portfolio snapshots；`portfolio:read` | `T-ASSET,T-RECOVERY` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-AST-002` | 持仓数量、可卖量、成本、现价、市值、盈亏和占比已展示；冻结与“封存仓/核心仓/活跃仓”归因未进入当前资产 UI。 | positions、bucket ledger；`portfolio:read`,`strategy:read` | `T-ASSET` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-AST-003` | 策略贡献、已关闭周期、费用和完整决策复盘工作区尚未实现。 | performance、closed cycles、execution trace；`portfolio:read`,`strategy:read` | `T-ASSET` / 无完整证据 | `PENDING` |
| `IOS-NTF-001` | 显式设置页授权、设备 Token 轮换/注销及五类偏好已实现；真实 APNs 注册和设备送达未验证。 | push device/preferences APIs；`notification:manage` | `T-PUSH,T-AUTHZ` / `E-AUTO`；缺 `E-APNS,E-TF5` | `IMPLEMENTED_AUTO` |
| `IOS-NTF-002` | 服务端 ES256/HTTP2 投递、最小隐私 payload、持久 lease/retry/410 失效和业务事件投影已实现；发送器默认关闭且未连接真实 APNs。 | notification event/outbox/APNs sender；服务端 + `notification:manage` | `T-PUSH,T-SECURITY` / `E-AUTO,E-SEC`；缺 `E-APNS` | `IMPLEMENTED_AUTO` |
| `IOS-NTF-003` | 客户端先解锁，再按事件 ID 和当前会话解析终态；过期、未知、401、账户/scope 变化 fail-closed。没有真实通知点击证据。 | `notificationEventRoute`、目标 Query；对象读权限 | `T-PUSH,T-NAV,T-SESSION` / `E-AUTO`；缺 `E-APNS,E-TF5` | `IMPLEMENTED_AUTO` |

## 7. 安全、体验、可靠性与发布

| 需求 ID | 页面/行为与实现 | 接口/权限 | 验证/证据 | RC1 状态 |
| --- | --- | --- | --- | --- |
| `IOS-SEC-001` | 原生会话按设备、唯一账户和专用 scope 签发；iOS 不使用 `mutation:write` 解锁写 UI。 | native session、GraphQL permission map；专用 scopes | `T-SESSION,T-AUTHZ` / `E-AUTO,E-SEC` | `IMPLEMENTED_AUTO` |
| `IOS-SEC-002` | Repository/Store 对唯一账户、资源归属及会话缩权 fail-closed。 | session active account + account resolvers；全部账户 scope | `T-SESSION,T-AUTHZ` / `E-AUTO,E-SEC` | `IMPLEMENTED_AUTO` |
| `IOS-SEC-003` | 手工订单、清仓、ExitPlan、策略与做 T 控制使用短时 HMAC、精确绑定、一次性消费与操作幂等。 | approval challenge、outbox/idempotency；交易写 scope | `T-ORDER,T-LIQ,T-SECURITY` / `E-AUTO,E-SEC` | `IMPLEMENTED_AUTO` |
| `IOS-SEC-004` | 已实现的 LIVE 下单、清仓、自动退出授权、策略/助手与做 T 高风险控制均逐次生物确认；真实设备生物矩阵未验收。 | LocalAuthentication + confirm APIs；专用控制 scope | `T-SECURITY,T-ORDER,T-STRATEGY` / `E-AUTO`；`E-UI,E-LIVE` 缺 | `IMPLEMENTED_AUTO` |
| `IOS-SEC-005` | 架构保持 iOS/服务端与 miniQMT、券商凭证隔离；改动文件有敏感扫描证据，但完整二进制/崩溃链路扫描未签署。 | 依赖边界；无 iOS QMT 权限 | `T-SECURITY,T-RELEASE` / 局部 `E-AUTO,E-SEC` | `PARTIAL` |
| `IOS-SEC-006` | 多个预览/确认结果保留 request/operation/event ID；客户端—API—Engine—Agent—QMT 的统一时间线仍缺。 | request/trace/business event IDs；对应 scope | `T-SECURITY,T-RECOVERY` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-SEC-007` | capability、readiness、snapshot freshness、受控窗口和 Kill Switch 已进入关键新增风险路径；所有助手/策略的完整端到端验证仍缺。 | live safety/readiness/Kill；多只读 + 控制 scope | `T-ORDER,T-STRATEGY,T-RECOVERY` / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-UX-001` | 设计 token、深浅色、Dynamic Type、VoiceOver 语义、44pt 和 Reduce Motion 已覆盖关键壳层/卡片；完整真实设备与 Accessibility Audit 未完成。iOS 26 系统浮动 TabBar 仍有对比警告。 | Design System；本地 | `T-A11Y` / 局部 `E-AUTO`；`E-UI` 缺 | `PARTIAL` |
| `IOS-UX-002` | 红涨绿跌、等宽数字、单位与未知值保守格式化已建立。 | Design System/formatters；本地 | `T-A11Y,T-ASSET,T-MARKET` / `E-AUTO` | `IMPLEMENTED_AUTO` |
| `IOS-UX-003` | 核心页面已有加载、空、错、旧、权限和部分成功状态；千条列表、全部未知枚举和所有写流程组合矩阵未验收。 | Feature state models；对应权限 | 各功能套件 / 局部 `E-AUTO` | `PARTIAL` |
| `IOS-REL-001` | 会话/Token/行情/推送已实现快照优先及部分重建；VPN/蜂窝切换、进程重启、乱序故障注入和五日观察未执行。 | GraphQL HTTP/WS、Feature Stores；各读权限 | `T-RECOVERY` / 局部 `E-AUTO`；缺 `E-TF5` | `PARTIAL` |
| `IOS-REL-002` | 尚无覆盖客户端、API、Engine、Agent、QMT 的端到端性能时间线和签署报告。 | telemetry/关联 ID；脱敏诊断 | `T-RECOVERY,T-RELEASE` / 无完整证据 | `PENDING` |
| `IOS-REL-003` | Keychain、隐私遮罩、内存挑战和最小通知内容已实现；完整日志/崩溃/后台截图/诊断包扫描未完成。 | Keychain、OSLog、privacy shield；本地/会话 | `T-SECURITY,T-A11Y` / 局部 `E-AUTO,E-SEC` | `PARTIAL` |
| `IOS-RLS-001` | 公共 SDL/权限、iOS 生成物和远端 Caddy Web codegen 已验证且无生成 diff；完整 pytest/Ruff 仍未全绿。 | SDL、permission JSON、Apollo/Web；构建权限 | `T-RELEASE` / 双端 codegen `E-AUTO`；其余见 RC 报告阻断 | `BLOCKED` |
| `IOS-RLS-002` | 尚未生成可签署的 TestFlight 候选，也未开始连续五个 A 股交易日观察。 | Release candidate + production-like services；设备 scope | `T-RELEASE,T-RECOVERY` / 缺 `E-TF5` | `PENDING` |
| `IOS-RLS-003` | 全功能 paper、做 T/打板各 20 闭环均未提供；受控 100 股实盘因前置门禁未满足而禁止执行。 | 全交易链路与安全门禁；显式实盘权限 | `T-RELEASE,T-SECURITY` / 缺 `E-PAPER,E-LIVE` | `BLOCKED` |

## 8. 当前门禁摘要

| 门禁 | RC1 状态 | 结论 |
| --- | --- | --- |
| G0 规格冻结 | `PARTIAL` | 文档体系和稳定 ID 已建立，`npm run build:docs` 已通过；仍待最终评审与签署。 |
| G1 契约就绪 | `PARTIAL` | 核心专用 scope、两阶段交易/清仓、策略、ExitPlan、做 T 和通知契约已落地；iOS 静态公开 SDL codegen 与远端 Caddy Web codegen 均通过且无 diff，最终契约评审/签署待补。 |
| G2 功能与自动化 | `BLOCKED` | 存在明确产品缺口，完整 pytest collection、可运行测试集和全量 Ruff 未全绿，真实设备 UI/无障碍矩阵未完成。 |
| G3 模拟盘 | `PENDING` | 没有全功能 paper 证据；做 T/打板各 20 个审计闭环未执行。 |
| G4 TestFlight | `PENDING` | 五个连续 A 股交易日观察未开始。 |
| G5 受控实盘 | `BLOCKED` | G0–G4 未全部通过，未执行任何真实交易；相关 capability 必须保持关闭。 |

## 9. 更新检查

修改本表后必须确认：

1. PRD 中每个 `IOS-<DOMAIN>-NNN` 在本表恰好出现一次。
2. 每行至少包含页面/行为、接口/状态源、权限、测试和证据。
3. 新接口同时出现在 API 安全契约、SDL/权限 diff 和路线图中。
4. 只有在最新 `docs/product/ios/releases/<version>.md` 同时具有自动化与所有适用
   人工签署证据时才可标 `DONE`。
5. 被替换需求不删除；在 PRD 和本表标记 `RETIRED` 并链接新 ID。
