# QuantX iOS

QuantX 原生 iOS 客户端最低支持 iOS 17，定位为个人 A 股量化移动控制中心。主导航按用户任务组织为“今日 / 行情 / 交易 / 量化 / 资产”，设置从今日或资产页的账户入口进入。当前具备登录、Keychain 恢复、Token 刷新、后台隐私遮蔽、账户与持仓、证券搜索、自选、实时行情、K 线、五档盘口、委托成交、策略、做T助手和打板助手。所有页面只展示真实服务端数据；缺少安全接口或权限时明确显示不可用，不生成模拟账户事实或假成交。

移动端不会直接访问 QMT。助手买入确认继续使用独立 `trade:approve` 权限、短时服务端预览及 Face ID/Touch ID；确认只表示意图重新进入统一交易域与风控链路。手动交易只允许接入独立 `trade:manual` 两阶段契约，绝不调用遗留 `placeOrder` 绕过预览。委托投递、券商受理与成交严格区分，最终事实只认 QMT Agent 回报经 Engine 持久化和收敛后的结果。Debug 可连接配置的私网 HTTP/WS 开发服务；Staging 与 Release 仍只允许 HTTPS/WSS。

## 环境

- Xcode 26.2 或当前稳定版
- Swift 6
- XcodeGen 2.46+
- Apollo iOS 2.1.2（精确锁定）

## 生成与构建

```bash
cd apps/ios
xcodegen generate
xcodebuild -project QuantX.xcodeproj -scheme QuantX -configuration Debug -destination 'generic/platform=iOS Simulator' build
```

完整模拟器测试使用当前可用设备，例如：

```bash
cd apps/ios
xcodebuild -project QuantX.xcodeproj -scheme QuantX -configuration Debug \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' test
```

当前回归基线覆盖五入口信息架构、真实认证 JSON 字段映射、Token 并发轮换、成功与失败恢复、后台隐私遮罩与本地锁定、远端登出失败后的本机清理、GraphQL 模型映射、行情代码主键、无时区历史数据兼容、空账户/无持仓、委托部分成交与未知券商状态、助手权限隔离与数值校验、独立交易权限、短时确认过期、生物识别先于 mutation、健康服务失败、数据过期、Debug HTTP/WS 账户连接和今日页无障碍审计。

真实开发后端只读集成使用单独 Scheme，必须由后端显式启用开发临时会话。该测试只读取账户、策略、委托成交与做T投影，完成后注销临时会话，不调用 Mutation：

```bash
cd apps/ios
xcodebuild -project QuantX.xcodeproj -scheme QuantXRealBackend \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:QuantXTests/RealBackendReadOnlyTests test
```

真实开发后端的 UI 端到端验收使用独立的 `QuantXRealBackendUI` Scheme。它会创建仅驻留内存的临时会话，在模拟器依次验证今日、资产、做T助手和打板助手，保留验收截图并在结束时注销会话；该路径同样不会调用交易 Mutation：

```bash
cd apps/ios
xcodebuild -project QuantX.xcodeproj -scheme QuantXRealBackendUI \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
  -only-testing:QuantXUITests/RealBackendUITests test
```

日常 `QuantX` Scheme 会安全跳过以上两项真实后端测试。只有显式选择真实后端 Scheme 时，测试运行器才会注入临时会话；该启动入口仅编译进 Debug 构建，不写入 Keychain，也不进入 Staging/Release 包。

深色/浅色和 Dynamic Type 无障碍矩阵使用真实模拟器系统设置运行。先从 `xcrun simctl list devices available` 获取 UDID，再执行：

```bash
cd apps/ios
./scripts/ui-accessibility-check.sh <simulator-udid>
```

脚本会在浅色/深色普通字号下运行 Xcode 的对比度、文字截断、点击区域、元素描述和语义审计；在无障碍最大字号下则滚动验证安全说明、服务状态、首版边界和 Tab 导航仍然可达。结束或失败时都会恢复原有模拟器外观与字号。

2026-07-23 已在 iPhone 16e、iPhone 17 Pro 和 iPhone 17 Pro Max Simulator 上分别完成四象限矩阵，三种尺寸均通过。

## 开发准备诊断

在运行 codegen、开启账户连接或准备 TestFlight 前执行：

```bash
cd apps/ios
./scripts/readiness-check.sh
```

也可显式指定候选后端入口：

```bash
./scripts/readiness-check.sh https://quantx.example.internal
```

脚本只执行无凭证只读探测，不停止 8080 监听进程，也不修改配置。它会核对健康状态、认证路由、公开 OpenAPI 的会话字段，以及 GraphQL HTTP/`graphql-transport-ws` 匿名访问默认拒绝；RFC1918 与 localhost 候选地址会绕过系统 HTTP 代理，避免把代理错误页误判成后端响应。摘要分别给出 `codegen`、`account_data` 和 `testflight` 状态：全部就绪返回 0，存在阻断返回 2，参数无效返回 64。

Debug 构建不再以 TLS 部署验收作为真实数据联调的前置条件；只要私网后端提供认证契约和账户只读权限，即可通过 HTTP/WS 加载真实数据。`account_data` 与 `testflight` 摘要仍用于判断 Staging/Release 发布就绪度，不会阻止 Debug 联调。

GraphQL Schema 与 Swift 类型使用 Apollo CLI 生成：

```bash
cd apps/ios
./scripts/install-apollo-cli.sh
./scripts/codegen.sh
```

Apollo codegen 直接读取同一 monorepo 中发布的 `apps/docs/public/contracts/graphql-schema.graphql` 并生成 Swift 类型，不创建根目录兼容快照，不依赖远端 introspection，也不会发送 Bearer 令牌。若 CI 或发布包需要覆盖契约，可通过 `QUANTX_GRAPHQL_SCHEMA_FILE` 指定本地 SDL，或通过 `QUANTX_GRAPHQL_SCHEMA_URL` 下载公开 SDL。`codegen.sh` 的账号型默认值安全门会在发现问题时立即失败。

## 配置边界

- `Config/Debug.xcconfig` 指向 `http://192.168.5.6:8080/`，启用真实账户数据；明文 ATS 例外只在 Debug Info.plist 中生效。
- Staging 与 Release 必须替换成真实的 `HTTPS/WSS` 私网入口；占位域名不会连接任何服务。
- `.xcconfig` 只能放非敏感设置。Token 只允许进入 Keychain，不能写入仓库、日志或 UserDefaults。
- Debug 默认将 `QUANTX_ACCOUNT_DATA_ENABLED` 设为 `YES`；Staging/Release 是否启用仍由各自部署验收决定。
- `SessionClient` 仅在 Debug 环境允许通过配置的 HTTP 地址登录；Staging 与 Release 仍拒绝非 HTTPS 认证地址。
- 账户摘要、组合汇总和每条持仓会再次进行客户端 `accountId` 范围一致性校验；发现跨账户数据时整页拒绝展示。
- 行情搜索使用 `Instrument.id` 作为带市场后缀的统一 `stockCode`；六位 `instrumentId` 仅用于展示，不能作为交易请求标的。自选、批量报价、K 线与 WebSocket 行情均校验证券代码和有限数值。
- 委托与成交查询按当前账户发起；成交结果会再次校验 `accountId`，未知券商方向或状态保持“未知”，不会推断为卖出或成交完成。
- 兼容后端遗留的 Asia/Shanghai 无时区数据库时间；新 API 输出会携带明确时区，客户端不会按设备时区猜测。
- 做T助手校验账户作用域、可用量关系、生产就绪门禁、Kill Switch、信号与批次数值；打板助手仅处理明确识别的打板策略实例、待确认意图和退出计划。
- 普通会话和 `mutation:write` 不足以批准交易；只有同时具备 `trade:approve`、本地解锁状态和生物识别能力的会话才能请求确认。未授权用户看到明确的只读状态。
- 交易确认使用两阶段 GraphQL Mutation：服务端先按当前意图创建最长 60 秒、绑定用户/设备会话/账户/策略运行/意图指纹的预览；App 仅在内存保存原始令牌，完成 Face ID/Touch ID 后提交确认。服务端令牌只存 HMAC 摘要且只能使用一次。
- Mutation 确认成功只表示意图进入 Engine 的统一风控与执行链，最终委托、成交和持仓状态仍以 QMT Agent 回报及 Engine 收敛为准。
- 账户事实只从 Apollo 生成类型映射到内存 Domain Model，不写入磁盘，也不使用运行时模拟资产数据。
- 数据源更新时间无法解析时显示“未知”；超过阈值时明确显示延迟或过期，刷新失败会标注并保留上次成功的内存快照。

### 本机签名与环境覆盖

不要直接把 Apple Team ID 或真实私网域名写进仓库配置。按目标环境复制示例文件：

```bash
cd apps/ios/Config
cp Debug.local.xcconfig.example Debug.local.xcconfig
cp Staging.local.xcconfig.example Staging.local.xcconfig
cp Release.local.xcconfig.example Release.local.xcconfig
```

只编辑需要使用的 `.local.xcconfig`。这些文件已被 Git 忽略，并在对应环境配置末尾通过可选 include 加载。Staging 的账户数据开关只有在 `readiness-check.sh https://实际后端` 验证通过后才能改为 `YES`；Debug 默认启用当前私网 HTTP/WS 后端的真实只读数据。
