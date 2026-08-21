# QuantX 部署与运维

开发环境唯一入口：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 up -Environment dev -Profile full -AccountId <账户>
.\ops\quantx.ps1 up -Environment dev -Profile web -Mode data-only
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
.\ops\quantx.ps1 down
```

普通开发 `up`（包括未显式指定模式的 `-Profile web`）会提升为 `full/live`，
启动 Caddy、API、Engine、Vite、VitePress 和 Prefect Worker，并在登记与运行时
预检通过后启动 QMT Agent。
QMT 预检通过、实盘能力门已开启且完整启动 `dev/full/live` 后，会幂等注册当前用户的
`QuantX-Dev-Daily-Backup` 任务，每天（含周末）16:30 备份 PostgreSQL 与
QMT Agent journal；错过触发时间时会在主机恢复可用后补跑。该任务不会提升权限，
注册失败时实盘备份门禁继续失败关闭。
Prefect Server 由外部管理。开发实盘优先使用 `-AccountId`，未传时从 `QMT_ACCOUNT_WHITELIST`、
`REAL_TRADING_ACCOUNT_ALLOWLIST` 或 `AUTH_BOOTSTRAP_ACCOUNT_IDS` 中自动解析唯一账户；
多个账户仍需显式选择。开发启动不要求 `-ConfirmLive`；`-Mode data-only` 是唯一
显式非实盘入口。API、Engine 和前端仍以 `development` 运行，只有 QMT Agent
子进程在 `live` 时使用受支持的 `ENV=testing` 运行门。数据库、Redis 和 InfluxDB
始终复用开发配置，不额外安装。
除非操作者明确要求纯行情模式，否则启动、恢复和验收不得把 `full/live` 的凭据、
权限或安全审批失败自动降级为 `data-only`。QMT 登记或本地 Python 运行时预检
失败时，开发启动保留 `profile=full` 与 `agentMode=live`，但在创建 API/Engine
进程前把 `ENABLE_REAL_TRADING`、`QMT_REAL_TRADING_ENABLED`、
`T_TRADE_LIVE_ENABLED` 全部设为 `false`，并把
`REAL_TRADING_ACCOUNT_ALLOWLIST` 清空；QMT 子进程不启动，状态记录为
`DEGRADED / BLOCKED`，并向服务进程注入非敏感的
`QMT_AGENT_LAUNCH_STATE=BLOCKED` 与稳定原因码。API、Engine、Web、Worker 和
Caddy 仍正常启动；使用已持久化历史行情的回测不依赖这个本地执行端。该路径
成功返回，但
`/health/ready` 可以继续因 QMT 不可用返回非就绪，`status` 必须明确显示
`liveTrading=DISABLED`，不得报告 QMT `READY`。
QMT 预检成功的默认 `full/live` 会为 API/Engine 显式开启 `ENABLE_REAL_TRADING` 与
`T_TRADE_LIVE_ENABLED` 能力门，并注入同一账户白名单。启动器同时在第一个服务
进程创建前固定 `QMT_AGENT_LAUNCH_STARTED_AT`；命令路由、做 T 就绪、历史补数
设备选择和组件健康只接受不早于该边界的 QMT 心跳，CLI 还要求本次记录的 QMT
进程 PID/启动时间仍匹配，避免上一次启动遗留的 90 秒新鲜心跳冒充本次就绪。这不会
绕过账户白名单、live Agent、快照、对账或 `SHADOW / CANARY / LIVE` 灰度门禁。
显式使用 `-Mode data-only` 时，实盘能力门同样关闭；它不是 QMT 预检失败时的
隐式兜底模式。
`down` 仅停止状态文件记录且启动时间匹配的进程。

启动器会在读取 `.runtime` 状态或创建子进程前，将仓库根目录的 Junction / 符号
链接解析为真实物理路径。即使从工作区目录联接调用 `ops/quantx.ps1`，Vite、API、
Engine、Worker、QMT Agent 与 Caddy 也会统一使用同一个真实根路径，避免 Windows
下因联接路径与依赖真实路径不一致而跳过 TSX 转译并出现白屏。

公开端口只有开发 Caddy 的 `8080`，它监听所有本机 IPv4 接口；本机使用
`http://127.0.0.1:8080`，局域网设备使用
`http://<开发机局域网 IP>:8080`。API 使用 `18081`、Vite 使用 `5250`、
VitePress 使用 `5251`，这些后端端口仍只绑定 `127.0.0.1`。Prefect API
通过 `PREFECT_API_URL` 连接外部服务，默认
`http://192.168.101.4:30420/api`，Worker 使用 `quantx-pool`。在线客户端文档
位于统一入口 `/docs/`，生产环境由 Caddy
直接提供静态文件，不运行 Node 文档进程。PostgreSQL、InfluxDB、Redis 和
Prefect Server 只检查，不由 QuantX 安装或启停。首次从其他设备访问时，需在 Windows
防火墙提示中允许 Caddy 通过专用网络。

Prefect Worker 的本机 CLI 状态位于 `.runtime/prefect`，CLI 和 Worker 固定
使用 UTF-8。不要把该运行时目录提交到仓库。

生产部署不使用可编辑源码。完整发布、版本化安装、迁移、备份、回滚、紧急停止
与影子/CANARY 步骤见
[PRODUCTION_RUNBOOK.md](./PRODUCTION_RUNBOOK.md)。`bootstrap` 仅供开发环境；
生产 `install` 必须提供带校验和的 release zip。
升级到 `20260815_0016` 会 fail-closed 撤销无法区分的旧 native/Web 会话，
不删除会话或审计记录；发布前必须按运行手册预告用户重新登录。

WinSW 2.12 使用 bundled mode：每个服务目录内都放置同名的 XML 与 wrapper，
例如 `quantx-api.xml` 对应 `quantx-api.exe`。Caddy、API、Engine、Worker 和
QMT Agent 因而可以独立安装、重启和滚动日志，不共享 wrapper 进程。

## QMT Agent 登记前置条件

`full` profile 不会生成或替用户保存设备凭证。首次运行前，在 Web 的
“QMT Agent 管理”页面创建一次性登记码，再在本机执行：

```powershell
python -m quantx_qmt_agent.main enroll `
  --api-url https://127.0.0.1:8080 `
  --code <一次性登记码>
python -m quantx_qmt_agent.main status
```

设备密钥写入 Windows Credential Manager。开发 `full/live` 未登记时不会尝试
未认证连接，也不会改写为 `data-only`；统一启动器会跳过 QMT 子进程并进入上述
fail-closed 降级状态。生产安装与 QMT 服务启用仍保持硬失败。
`paper` 和 `live` 还要求设置
`QMT_ACCOUNT_WHITELIST`；`live` 额外要求 QMT Agent 子进程使用 `ENV=testing` 或
`ENV=production`、`ENABLE_REAL_TRADING=true` 和
`QMT_REAL_TRADING_ENABLED=true`。production 还要求
`T_TRADE_LIVE_ENABLED=true`；服务端同时检查
`REAL_TRADING_ACCOUNT_ALLOWLIST`、账户灰度阶段、Agent READY、快照新鲜度、
对账状态和自动退出策略授权。

实盘安全状态分层展示：`PREPARING` 表示 `SHADOW` 下账户观察、QMT 手工交易
分类和完整快照对账已经就绪，但自动交易仍因备份、外部活动或灰度授权保持
关闭；`READY` 才表示可申请进入受控 Canary；`BLOCKED` 表示观察/对账链路
本身不安全。QMT 手工活动在准备阶段不会单独制造 `BLOCKED`。

受控窗口建立后，以当时完整快照中的历史外部委托/成交作为审计基线；只有
基线之后新增的外部活动才使窗口失效并暂停自动执行。开发环境允许账户从
`SHADOW` 直接进入 `LIVE`，但必须同时满足新鲜受控窗口、24 小时内成功备份、
全部 readiness 门禁、`trade:approve` 权限，并精确确认
`LIVE:<账户>`。生产环境仍禁止 `SHADOW` 直升 `LIVE`，继续使用既有 Canary
流程。建立窗口、启用、暂停和失败尝试均写入追加式审计事件。

## 端口与进程安全

`up` 遇到端口冲突只报告 PID 与可用的命令行信息，不终止未记录在
`.runtime/state` 中的进程。`down` 会校验 PID 与进程启动时间，只按依赖
倒序停止本次 QuantX 运行记录中的进程。旧的或由其他方式启动的 QuantX
实例必须由其原启动方式停止。

## 验收命令

```powershell
.\.runtime\tools\caddy\caddy.exe validate `
  --config .\ops\caddy\Caddyfile.dev --adapter caddyfile
.\.runtime\tools\caddy\caddy.exe validate `
  --config .\ops\caddy\Caddyfile.prod --adapter caddyfile
python -m pytest tests/infrastructure/test_ops_contract.py
```

GraphQL 契约验收必须在 `web` profile 已经就绪后，通过
`http://127.0.0.1:8080/graphql` 执行；不要绕过 Caddy 指向 API 内部端口。
