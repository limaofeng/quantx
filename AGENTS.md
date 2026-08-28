# QuantX Agent 记忆文件

进入本项目后先读本文件，再按任务范围阅读对应代码和文档。

## Monorepo 结构

- `apps/api/`：FastAPI、REST、Strawberry GraphQL、Agent WebSocket Hub。
- `apps/docs/`：VitePress 客户端开发文档与发布契约。
- `apps/web/`：Vite + React + TypeScript。
- `apps/engine/`：策略、做 T、条件清仓、热缓存与订单回报收敛。
- `apps/monitor/`：独立可用性、延迟、事故历史与状态页只读 API。
- `apps/worker/`：Prefect flows、tasks、部署入口。
- `apps/qmt-agent/`：唯一允许访问 XTData/XTTrading 的 Windows Agent；业务链路
  只出站，并提供固定只读健康监听。
- `packages/contracts/`：版本化 Agent 协议、DTO 和公共枚举。
- `packages/domain/`：纯交易域、策略、风控、仓位和回测 broker。
- `packages/application/`：用例、端口接口、命令路由和状态推进。
- `packages/infrastructure/`：数据库、Repository、行情适配和持久化消息箱。
- `ops/`：开发 Caddy/统一启动器、容器镜像与 Kubernetes 正式部署清单。
- `docs/engineering/`：按 API、Engine、Worker、QMT Agent、部署组织的工程文档。

Python 命名空间分别为 `quantx_contracts`、`quantx_domain`、
`quantx_application`、`quantx_infrastructure`、`quantx_engine`、
`quantx_monitor`、`quantx_worker` 和 `quantx_qmt_agent`。

## 设计与维护准则

- 所有架构、协议和数据模型必须从 QuantX 的真实需求出发，保持设计合理、
  简洁且边界清晰。项目是个人单账户系统，不得为臆想中的多账户、多租户或
  未确认的未来需求增加字段、状态、分支和抽象。
- 不为旧实现、旧协议或未部署版本默认增加兼容层、双协议、可选字段、降级分支
  或兜底逻辑。契约调整必须在代码、客户端、文档和测试中原子切换到唯一权威
  设计；只有用户明确要求兼容，并明确兼容对象和期限时，才允许引入受控兼容。

## 统一运行方式

当前权威开发基线（2026-08-14）：**Dev 默认即个人单账户实盘**。不要为普通
开发启动显式传 `-Mode`；以下第一条命令必须解析为 `profile=full`、
`agentMode=live`。只有明确需要禁用实盘时，才允许使用
`-Mode data-only`。

开发环境只从仓库根目录运行：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 up -Environment dev -Profile web -Mode data-only
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
.\ops\quantx.ps1 down
.\ops\quantx.ps1 up -Environment dev -Component monitor
.\ops\quantx.ps1 status -Environment dev -Component monitor
```

标准 Dev 实盘重启顺序：

```powershell
.\ops\quantx.ps1 down
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 status
```

- 普通开发 `up`（包括未显式指定模式的 `-Profile web`）统一提升为
  `full/live`，启动 Caddy、API、Engine、Vite、VitePress 和 Prefect Worker；
  QMT Agent 在本机登记与运行时预检通过后启动。Prefect Server 使用外部服务。
- Monitor 使用独立状态文件、SQLite 历史库和生命周期；普通 `up/down` 不启停它，
  以保证主服务离线期间仍能观测。开发时用 `-Component monitor` 单独管理。
- `live` 优先使用 `-AccountId`，未传时从本机环境中的唯一账户配置自动解析。
  `-Mode data-only` 是唯一的显式非实盘入口，并同时关闭服务端实盘能力门。
  数据库、Redis 和 InfluxDB 继续复用开发配置，不为实盘另装一套。
- 除非用户明确要求纯行情模式，否则 Codex、自动化脚本和人工运维不得把普通
  dev 启动、恢复或验收静默降级为 `-Mode data-only`。若 QMT 登记或本地运行时
  预检失败，启动器必须保持 `profile=full`、`agentMode=live`，在 API/Engine
  启动前关闭全部服务端与 Agent 实盘能力门并清空实盘账户允许列表，跳过 QMT
  子进程，以显式 `DEGRADED / BLOCKED` 状态继续启动非 QMT 服务。该状态允许使用
  已持久化历史行情做回测，但不得伪装成 QMT `ready`；恢复 QMT 后必须整体重启。
- 开发 Caddy 是唯一公开入口，监听所有本机 IPv4 接口的 `8080`；局域网设备
  使用 `http://<开发机局域网 IP>:8080`。
- API 只监听 `127.0.0.1:18081`，Vite 使用 `5250`，VitePress 使用
  `5251`。Prefect API 固定通过 `PREFECT_API_URL` 连接外部服务，默认
  `http://192.168.5.6:30420/api`，Worker 使用 `quantx-pool`。
- PostgreSQL、InfluxDB、Redis、Prefect Server 是外部服务，只检查，
  不自动启停。
- 不得绕过统一入口单独手工启动 QMT Agent，否则同一设备的重复 Agent 会争用
  会话并可能触发行情分片冲突。完整 Dev 实盘验收的 `status` 必须显示
  `Runtime profile=full`、`agentMode=live`、唯一账户、`liveTrading=ENABLED`、
  QMT Agent `ready`、协议 `1.1` 和小于 90 秒的新鲜快照；降级启动则必须显示
  `DEGRADED`、QMT `BLOCKED` 与 `liveTrading=DISABLED`。
- 不得恢复根目录旧启动脚本或 API 内的子进程管理。
- `ops/quantx.ps1` 只支持开发环境，不得恢复 Windows production install/WinSW
  服务端部署路径。

### Windows QMT 节点硬边界

Windows 环境只负责 QMT Agent，不负责 API、Engine、Worker、Web、Docs、Caddy、
Monitor 或任何 Mac/集群服务。在 Windows 上处理 QMT Agent 启动、重启或验收时：

- 只允许从仓库根目录使用 `ops/quantx-agent.ps1`；不得运行
  `ops/quantx.ps1 up/down` 启停主服务栈，也不得绕过统一入口手工启动 Agent。
- 不得通过 SSH 或远程脚本检查、启动、停止或重启 Mac/集群服务。远端服务异常
  应如实报告，只有用户明确扩大任务范围时才能操作远端服务。
- Windows 启动验收必须确认本机计划任务、Supervisor、唯一 Agent 子进程和
  `0.0.0.0:18084` 的 `/health/live` 正常，并排除旧版或重复 Agent 进程。
- `/health/ready`、控制连接、对账、行情流和服务端实盘能力门依赖远端服务；它们
  不可用时不得反复重启 Agent，也不得转而启动 Windows Monitor 或其他服务端组件。
- `quantx-agent.ps1 up` 幂等维护 `Private` profile 的 TCP `18084` 入站规则，
  `RemoteAddress=Any`，不绑定 Mac/Monitor IP。首次创建或规则修复需要管理员
  PowerShell；正确规则已存在时普通重启无需提升权限。

权威 Windows Dev 命令：

```powershell
.\ops\quantx-agent.ps1 doctor -Environment dev
.\ops\quantx-agent.ps1 up -Environment dev
.\ops\quantx-agent.ps1 restart -Environment dev
.\ops\quantx-agent.ps1 status -Environment dev
.\ops\quantx-agent.ps1 logs -Environment dev
.\ops\quantx-agent.ps1 down -Environment dev
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:18084/health/live
```

QMT Agent 默认按本机登记的个人单账户 `live` 配置启动。显式 `-AccountId` 只用于
登记或切换已确认的唯一账户；不得为了通过检查静默降级。券商凭据、设备密钥和
XTQuant 运行时只保留在 Windows 本机。

## 正式部署标准

- 正式服务端只运行在 Kubernetes：Gateway、API、Market Data Service、Monitor、
  Engine、Worker 和 AI Runtime 必须保持独立工作负载与生命周期。
- Windows 不作为正式服务端宿主；因 XTQuant 限制，只有集群外 QMT Agent 执行
  节点保留 Windows。交易控制、行情、历史上传和报告链路只向 Kubernetes 公共
  HTTPS/WSS 入口建立出站连接；唯一入站例外是供 Monitor 使用的固定只读健康端点
  `0.0.0.0:18084`，不得扩展为交易或通用管理接口。
- 生产入口是 `ops/k8s/base` 与 `ops/containers`。禁止使用 `quantx up`、工作区
  源码、可编辑安装或本机 Caddy 模拟正式部署。
- PostgreSQL、Redis、InfluxDB 和 Prefect Server 继续由外部平台管理。Kubernetes
  只部署 QuantX 自有工作负载，不在基础清单中安装这些依赖。
- API Kubernetes readiness 使用 `/health/service-ready`；交易业务 readiness
  继续使用 `/health/ready`。不得因 QMT 或行情业务门禁失败把恢复入口从 Service
  摘除。
- Monitor 在生产环境使用单副本 StatefulSet 与独立 PVC；它必须直接探测行情
  服务 `/health/ready` 和 Windows QMT Agent `/health/ready`，并在第一次完整探测
  完成后才对外 ready。QMT Agent 状态仍与 API 会话/对账语义组合，观测结果不得
  回写交易门禁。

## 进程和依赖边界

- API 只管理 HTTP/GraphQL、数据库、Agent 会话与订阅桥接。
- Engine 独占策略管理器、条件清仓、全局做 T、热缓存和回报收敛，并使用
  PostgreSQL 租约保证单实例。
- Worker 独立连接 Prefect Server；API 重启不得停止 Worker。
- `quantx_domain` 禁止依赖数据库、文件、网络、FastAPI、Prefect 和 QMT。
- QMT Agent 只依赖 contracts，不导入服务端 ORM、Repository 或策略。
- `apps/api`、`apps/engine`、`apps/worker` 禁止导入 `miniqmt` 或 `xtquant`。
- Redis 只用于唤醒与广播，数据库消息箱和业务表才是状态真源。

## GraphQL 与前端

GraphQL/API schema 或前端查询变化后，必须在同一轮执行：

```powershell
$env:CODEGEN_GRAPHQL_ENDPOINT="http://127.0.0.1:8080/graphql"
npm run codegen
npm run check
npm run lint
npm run test:run
npm run build
```

不得用 `as any` 掩盖契约不一致。前端 URQL HTTP 和 WebSocket 都默认使用
同 host 的 `/graphql`。

## 测试

根边界测试：

```powershell
python -m pytest tests/
```

API 测试可从根工作区按需运行：

```powershell
python -m pytest tests/api/unit/
python -m pytest tests/api/integration/
```

单元测试优先，集成测试谨慎。E2E/真实交易测试默认禁止。真实交易必须同时
显式满足 `ENV=testing`、`ENABLE_REAL_TRADING=true`、账户白名单和
`QMT_REAL_TRADING_ENABLED=true`，且不得在 production 运行。

Codex 生成的截图、trace 和 video 放在根目录 `.codex_screenshots/`，默认不
提交。

## 任务完成与提交

- Codex 确认任务完整达成并完成必要验证后，必须立即 `git commit` 本次改动，
  不得让已确认完成的代码继续处于未提交状态。
- 提交必须按实际功能合理拆分，并使用专业、清晰的 commit message；只有用户
  明确要求暂不提交时才允许例外。
- `git status`、`git diff`、`git show`、`git log`、`git rev-parse`、
  `git ls-files` 等只读检查可由主代理和任意子代理直接执行。除专用 Git 子代理
  本人外，主代理和实现子代理不得执行会改变 Git 状态的命令，也不得自行暂存或
  提交改动。
- 所有获授权的 Git 状态变更必须交给专用子代理执行；创建该子代理时固定使用
  `model="gpt-5.6-luna"` 和 `fork_turns="1"`。该专用子代理直接执行任务，
  不得为同一 Git 操作再次委派子代理。模型不可用或账户无权限时必须明确报告
  阻塞，不得换用其他模型，也不得由主代理代为执行。
- 常规任务完成时，主代理必须先完成最终审核与验证，再向专用 Git 子代理提供
  已批准的精确文件列表、验证结果和提交范围。该子代理只能检查差异、暂存批准
  文件并创建提交，不得修改实现、吸收无关改动、跳过 hooks、amend 或 push。
- 使用专用 Git 子代理不扩大操作权限。`push`、`merge`、`rebase`、`reset`、
  `checkout`、`restore`、`stash`、`cherry-pick`、分支/标签/worktree 变更及其他
  破坏性或远程操作，仍须任务本身明确授权并遵守现有安全规则；未获授权时禁止
  执行。

## 交易系统硬约束

- 回测与实盘调用同一个 `StrategyBase.step(StrategyInput)`。
- 禁止恢复 `Signal/on_bar/on_tick/generate_signal` 主路径。
- 策略只输出 `TradeIntent[]` 和算法状态补丁，不得访问账户、数据库、
  网络、文件或 QMT。
- 策略不得计算真实可卖量、冻结资金或最终合法订单数量。
- A 股合法性、T+1、涨跌停、停牌、资金、可卖量、整手与零股清仓由交易域、
  风控、OrderSizer、Broker 和状态流处理。
- 实盘成交真源只能来自 QMT Agent 上报的委托与成交回报。
- `command_ack` 只表示投递，不得推进成交；回报必须先持久化 inbox，再由
  Engine 收敛。
- 固定标的策略实例只绑定一个 `instrument_code`；账户级策略也不得自行
  选股或读取账户。
- 仓位归因使用 `locked_core/core/swing`，用户展示为
  “封存仓/核心仓/活跃仓”。
- 回测不得使用任何未来数据；缺失数据只能保守降级。
- 每次不买、少买、卖出、拒单、熔断都必须可审计。
- 券商账号、密码、QMT 配置和设备密钥不得进入服务端数据库、日志、异常
  堆栈或网络消息；设备密钥存 Windows Credential Manager。

## 文档阅读顺序

交易域、执行链路或策略接口：

1. `docs/plans/A股动态天平双仓策略实现落地规格与迁移计划.md`
2. `docs/trading/contracts/A股三层协作与执行契约.md`
3. `docs/trading/contracts/A股交易域数据结构与状态机.md`

实盘、QMT Agent 或多进程：

1. `docs/architecture/系统架构设计.md`
2. `docs/engineering/qmt-agent/README.md`
3. `docs/engineering/engine/README.md`
4. `docs/trading/contracts/A股三层协作与执行契约.md`

API、测试或部署：

1. `docs/engineering/api/README.md`
2. `docs/engineering/api/API.md`
3. `docs/engineering/api/TESTING_GUIDE.md`
4. `docs/engineering/deployment/README.md`

前端：

1. `docs/engineering/web/UI_UX_DESIGN_SYSTEM.md`
2. `apps/web/package.json`
3. `apps/web/src/core/graphql/client.ts`
4. 目标 feature 目录。
