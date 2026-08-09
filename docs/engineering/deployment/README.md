# QuantX 部署与运维

开发环境唯一入口：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 up -Environment dev -Profile full
.\ops\quantx.ps1 up -Environment dev -Profile full -Mode live `
  -AccountId <账户> -ConfirmLive "LIVE:<账户>"
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
.\ops\quantx.ps1 down
```

`web` 启动 Caddy、API、Engine、Vite 和 VitePress；`full` 额外启动 Prefect
Worker 和默认 `data-only` 的 QMT Agent。Prefect Server 由外部管理。开发
`full` 可通过 `-Mode paper/live`
显式选择执行模式；`paper/live` 必须指定账户，`live` 还必须精确确认。API、Engine
和前端仍以 `development` 运行，只有 QMT Agent 子进程在 `live` 时使用受支持的
`ENV=testing` 安全门。数据库、Redis 和 InfluxDB 始终复用开发配置，不额外安装。
`down` 仅停止状态文件记录且启动时间匹配的进程。

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

设备密钥写入 Windows Credential Manager。未登记时 `full` 会明确失败，
而不会退化成未认证连接。`paper` 和 `live` 还要求设置
`QMT_ACCOUNT_WHITELIST`；`live` 额外要求 QMT Agent 子进程使用 `ENV=testing` 或
`ENV=production`、`ENABLE_REAL_TRADING=true` 和
`QMT_REAL_TRADING_ENABLED=true`。production 还要求
`T_TRADE_LIVE_ENABLED=true`；服务端同时检查
`REAL_TRADING_ACCOUNT_ALLOWLIST`、账户灰度阶段、Agent READY、快照新鲜度、
对账状态和自动退出策略授权。

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
