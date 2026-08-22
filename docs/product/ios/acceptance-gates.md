# QuantX iOS 测试验收与发布门禁

> 适用版本：个人 A 股量化 iOS v1
>
> 原则：所有目标能力统一通过最终产品验收；阶段构建只用于开发，不是正式发布

## 1. 缺陷等级与放行规则

| 等级 | 定义 | 发布规则 |
| --- | --- | --- |
| P0 | 越权、错误账户、重复/错误实盘订单、假成交、敏感密钥泄漏、不可控扩大风险 | 任一未关闭即阻断，已发布版本立即停用相关 capability |
| P1 | 核心流程不可完成、错误风险文案、订单状态不可恢复、严重崩溃/数据丢失、无障碍阻断 | 任一未关闭即阻断候选版本 |
| P2 | 有安全替代路径的功能/布局缺陷、非核心性能退化 | 必须有负责人、目标版本和用户可见限制 |
| P3 | 轻微视觉或文案问题 | 可随版本计划修复 |

“服务端已返回成功”“页面能打开”或“测试被跳过”都不是通过证据。每项稳定需求
必须在[追踪矩阵](traceability.md)中有对应自动化或人工证据。

## 2. 发布门禁

### G0：规格冻结

- PRD、信息架构、视觉、API/安全、路线图和追踪矩阵通过评审。
- 旧只读规格已标记被取代，在线权限和快速开始不再宣称 iOS 禁止 Mutation。
- 每个 P0/P1 功能有稳定需求 ID、接口、权限、测试和验收负责人。

### G1：契约就绪

- 设备 scope、唯一主账户、专用权限和两阶段手工交易/清仓契约全部落地。
- GraphQL 权限 JSON 中 iOS 写字段不再依赖通用 `mutation:write`。
- SDL、权限、Client OpenAPI、Apollo Swift 与 Web 生成类型来自同一服务版本。
- 未认证、缺 scope、跨账户、版本冲突、挑战攻击和幂等冲突测试通过。
- APNs 注册、注销、最小 payload 和会话吊销契约通过测试。

G1 未通过时，相应客户端入口必须编译期或服务端 capability 关闭；不得用客户端
隐藏按钮或直调兼容 Mutation 代替。

### G2：功能与自动化完成

- 五 Tab 和所有 P0 页面无占位、假数据、固定账户或无法到达的操作。
- iOS 单元、集成、UI、无障碍与截图回归通过。
- Python 根边界、相关 API/Engine 测试、Web 检查和双端 codegen 通过。
- 日志扫描、隐私遮罩、Keychain、通知、弱网和状态恢复测试通过。

### G3：模拟盘验收

- 在与目标部署同构的服务链路中完成全部成功、拒绝、部分成功和恢复场景。
- 手动交易、清仓、策略控制、做 T、打板与 Kill Switch 全部只使用 paper 命令，
  真实交易开关在自动化期间保持关闭。
- 做 T 与打板各完成至少 20 个可审计 paper 闭环，覆盖批准、拒绝、过期、部分
  成交、退出、T+1 和故障恢复；无重复命令或状态漂移。

### G4：TestFlight 观察

- 使用真实 iPhone、个人 VPN/私网 HTTPS/WSS 和生产等价证书。
- 连续五个 A 股交易日覆盖冷启动、前后台、网络切换、Token 轮换、推送和订阅
  重建；观察期内无崩溃、P0/P1、账户串读、隐私泄漏或错误成交语义。
- 每日记录版本、设备、网络、会话、Agent/快照新鲜度、异常及处置，不以“未操作”
  代替核心流程观察。

### G5：受控实盘灰度

真实交易不属于默认测试或 CI。只有所有门禁显式满足、用户批准并处于人工监督
窗口时执行：

1. 运行环境满足 `ENV=testing`（危险测试）或受控 production、
   `ENABLE_REAL_TRADING=true`、`QMT_REAL_TRADING_ENABLED=true`、必要的
   `T_TRADE_LIVE_ENABLED=true` 和双侧账户白名单。
2. 唯一 Agent ready、协议 1.1、账户快照小于 90 秒、对账正常、无不可解释外部
   活动、Kill Switch 关闭，且账户实盘窗口/灰度状态允许操作。
3. 先以单标的 100 股执行一笔手动限价买入和对应受控卖出，人工核对每一状态与
   QMT 回报；不为了制造测试强求部分成交。
4. 撤单、批量/条件清仓、做 T 和打板分别在自然满足条件、最大新增风险 100 股的
   Canary 中验收。若自然信号未出现，相关实盘 capability 保持关闭，不能用模拟
   结果宣告该实盘能力完成。
5. 任一重复命令、超可卖量、T+1 违规、未授权自动退出、状态假推进或无法解释的
   对账差异立即触发 Kill Switch，停止灰度并按 P0 处理。

G5 仅允许人工执行，不加入 XCTest、pytest、脚本默认参数或普通开发启动验证。

## 3. 功能验收场景

### 3.1 导航、今日和账户

- 冷启动登录后五个 Tab 顺序、标题和图标正确，设置仅从资产账户入口进入。
- 单主账户从会话唯一解析；无账户、多账户歧义和跨账户响应均整页拒绝。
- 今日行动按风险和过期时间排序；已过期待确认只能查看终态。
- 总资产、当日盈亏、资金、市值和时间可追溯到同一快照；隐私模式不改变布局。
- 正常、PREPARING、BLOCKED、Agent 离线和 Kill Switch 不互相误报。

### 3.2 行情

- 代码、名称和拼音搜索覆盖 SH/SZ/BJ；未知/退市/停牌标的保守展示。
- 自选新增、重复、删除、排序、写入失败回滚和跨设备刷新正确。
- 分时午休不连线；日/周 K 无未来数据；断档、陈旧、停牌和涨跌停明确标注。
- 五档与逐笔缺失不显示伪造零值，乱序增量不回滚最新行情。
- 进入下单只预填证券/方向，不自动提交数量或绕过报价 capability。

### 3.3 手动交易和撤单

至少覆盖：

- BUY/SELL、`LIMIT`、沪深受支持的 `BEST`（对手方最优价），以及北交所/未知市场
  fail-closed。
- 合法/非法 tick、零/负/过大数量、买入非整手、卖出零股清仓。
- 资金不足、可卖量不足、T+1、停牌、涨跌停、非交易时段、行情过期。
- `ALLOW`、`CAP`、`REJECT` 预览；CAP 明确展示原量与合法量。
- 生物可用/不可用/取消/失败，挑战过期、使用两次、输入篡改、跨会话/账户/设备。
- 确认网络超时后以幂等键查询原结果，不生成第二订单。
- QUEUED、已投递、券商已报、部分成交、成交、拒单、撤单、过期和 UNKNOWN。
- 撤单不可撤、重复撤、撤单与成交竞态；只有券商回报显示“已撤”。

### 3.4 卖出管理

- 单只、选中、全部范围，以及 `AVAILABLE_NOW` / `UNTIL_SNAPSHOT_CLEARED`。
- `UNALLOCATED_ONLY` / `REPLACE_CANCELLABLE`，包含活动退出计划和待成交 SELL。
- 不可卖、部分可卖、零股、T+1、批量部分失败和确认后新增持仓。
- 全仓预览逐只列出纳入/跳过/冲突；挑战篡改证券集合或保护量失败。
- 条件计划目标价、收益率和动态规则的创建、版本冲突、暂停、恢复、取消、触发。
- 未自动授权进入 `AWAITING_APPROVAL`；精确授权后规则/数量/版本变化使授权失效。
- 部分成交只减少对应保护量，拒单/撤单恢复计划监控且不伪造已退出。

### 3.5 策略、做 T 和打板

- 每种合法生命周期迁移成功；非法迁移、活动退出保护和 DRAINING 正确处理。
- 只渲染 `mobileEditable` 参数；范围、类型、未知 key 和 `expectedVersion` 冲突
  均由服务端拒绝，客户端展示差异。
- 模拟转实盘、实盘启动和恢复重新检查 readiness 并生物确认；门禁变化使挑战失效。
- 做 T 覆盖总开关、模式、忽略列表、资格、信号过期、批准/拒绝、批次、自动退出、
  DRAINING 和 Kill Switch。
- 打板覆盖候选阶段、布防/取消、预算、信号过期、买入批准、T+1 与退出计划。
- 信号批准前没有 Broker BUY；策略停止不丢失已有 ExitPlan。

### 3.6 资产、复盘和通知

- 资产区间曲线处理入金、缺点、估算口径和不完整数据，不把缺失值当零。
- 持仓真实数量/可卖量与 bucket 归因同时展示，bucket 不覆盖券商事实。
- 已关闭周期、策略贡献、费用和 DecisionTrace 可追踪；未知来源标明数据质量。
- APNs 类别偏好生效；payload、锁屏和系统通知中心无证券、账户或金额。
- 点击推送先解锁并重新查询；已处理、过期、吊销会话和错误账户显示真实终态。

## 4. 安全验收

### 4.1 授权矩阵

每个 Query/Mutation/Subscription 至少测试：无 Token、过期 Token、吊销 Token、
缺 scope、只有 `mutation:write`、正确 scope、错误账户、非归属资源和正确请求。
服务端测试直接构造请求，不能只测试按钮是否隐藏。

### 4.2 确认挑战攻击矩阵

- 过期、复用、并发双击、截断、伪造、不同设备会话和登出后使用。
- 替换账户、标的、方向、价格类型、价格、数量、策略 run、intent、计划或版本。
- 预览后行情、资金、可卖量、Agent、对账、账户实盘窗口或 Kill Switch 改变。
- 同幂等键相同输入重放、同键不同输入冲突、客户端超时后的状态查询。
- 服务端持久层、审计、应用日志和错误响应中不存在明文 confirmation token。

### 4.3 客户端隐私

- Keychain 以适当 device-only 可访问级别保存 Token；UserDefaults 和磁盘缓存无
  账户事实或密钥。
- 登录、刷新、GraphQL、APNs、错误、崩溃、Analytics 和 OSLog 全链路做敏感扫描。
- App 进入后台和任务切换器前覆盖敏感内容；通知、Spotlight、剪贴板和分享入口
  不泄漏数据。
- 登出即清除 Keychain、Apollo Store、内存快照、订阅、通知路由和未消费挑战。
- iOS 二进制、配置和网络记录不包含 QMT 路径、券商账号/密码或 Agent 设备密钥。

## 5. 数据与可靠性验收

- 快照 + 增量覆盖断线、重连、重复、乱序、序列间隙、旧事件、未知对象和 Token
  轮换；任何情况都不把终态回滚或把 pending 推成成交。
- 查询失败或不完整快照保留最后有效内存状态并标记时间/原因；跨账户快照不保留。
- App 在编辑票据、预览、LocalAuthentication、确认和结果查询各阶段进入后台，
  恢复后不会自动提交或复用过期挑战。
- VPN/Wi-Fi/蜂窝切换、DNS 失败、TLS 失败、API 重启、Engine 重启、Agent 重连和
  APNs 丢失均有可恢复路径；TLS 失败不降级 HTTP。
- 未知枚举、无时区遗留时间、未来时间、负数量、异常金额和汇总不一致保守降级。

### 5.1 性能门槛

在指定真实 iPhone、稳定 VPN/Wi-Fi、无调试器的 Release 候选上记录：

| 指标 | 门槛 |
| --- | --- |
| 点击到本地可见反馈 | p95 ≤ 100ms |
| 已认证主要页面快照到首个可信内容 | p95 ≤ 2s |
| API 收到订阅事件到前台 UI 可见 | p95 ≤ 1s |
| 通知点击到解锁后终态页面（网络正常） | p95 ≤ 3s |
| 五日观察 | 0 崩溃、0 watchdog、0 P0/P1 |

Broker 回报耗时不设虚假客户端 SLA，但必须记录客户端、API、Engine、Agent 和
QMT 时间点，能定位延迟阶段。超过业务阈值的订单显示回报延迟并触发查询/对账。

## 6. UI 与无障碍验收

- 最小 375×667pt、当前 6.1 英寸目标机和最大 Pro Max 均验证浅色/深色。
- 标准字号、最大 Accessibility Dynamic Type、Increase Contrast、Button Shapes、
  Reduce Motion 和 VoiceOver 完成全部 P0 流程。
- Xcode Accessibility Audit 的对比度、截断、点击区域、描述和语义问题为零；
  经审查豁免必须在报告中逐项说明。
- 中文长文案、最大金额/价格/数量、未知值、空列表、千条列表和批量部分失败不
  产生横向滚动、遮挡、不可达按钮或错误阅读顺序。
- 图表提供可访问摘要与逐点读取；涨跌、风险、买卖和状态不依赖颜色。
- App Icon、启动、权限说明、隐私清单、版本和错误文案不存在占位域名或开发文案。

## 7. 自动化命令基线

所有命令从仓库根目录运行。自动化强制关闭真实交易：

```powershell
$env:ENV="testing"
$env:ENABLE_REAL_TRADING="false"
$env:QMT_REAL_TRADING_ENABLED="false"
python -m ruff check apps packages tests
python -m pytest tests/ -m "not dangerous and not real_trading and not e2e"
```

GraphQL 或前端 operation 变化时，按仓库契约通过 Caddy 执行：

当前 macOS/iOS 开发工作区连接远端开发后端 `192.168.5.6:8080`；只有命令运行在
后端主机本机时才使用 `127.0.0.1:8080`。两者都必须经过 Caddy，不得改用 API
私有端口 `18081`。

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT="http://192.168.5.6:8080/graphql"
npm run codegen
npm run docs:contracts
npm run check
npm run lint
npm run test:run
npm run build
```

iOS 至少执行：

```bash
cd apps/ios
./scripts/codegen.sh
xcodebuild -project QuantX.xcodeproj -scheme QuantX \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
./scripts/ui-accessibility-check.sh <simulator-udid>
```

文档单独验证：

```bash
npm run build:docs
```

真实后端只读 Scheme 不证明写入闭环；真实交易也不得加入上述命令。

## 8. 验收证据与签署

每个发布候选建立一份 `docs/product/ios/releases/<version>.md` 报告，至少链接：

- 需求追踪矩阵冻结版本和已知限制。
- Schema/权限/OpenAPI 与 Apollo/Web codegen diff 审查。
- pytest、Web、iOS 单元/UI、无障碍和文档构建结果。
- 深浅色、设备矩阵、关键流程和隐私遮罩截图；截图使用脱敏数据。
- 弱网/重连、五日 TestFlight、性能时间线和 APNs 验收记录。
- paper 闭环和受控实盘逐笔记录，包括意图、挑战、命令、券商回报与审计 ID。
- 安全、产品、交易域和发布负责人的签署日期。

证据中的 Token、完整账号、证券持仓和券商原文必须脱敏。缺少任一适用门禁证据
时，矩阵状态保持 `PARTIAL/BLOCKED`，不能用口头确认改为 `DONE`。
