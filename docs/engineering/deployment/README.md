# Windows Dev 运行与运维

QuantX 是个人单账户项目，生产/实盘运行端为 Windows，当前开发环境也为 Windows。
当前启动器只实现 `dev` 配置；生产运行端不代表支持 `-Environment production`。
后续计划迁移开发环境到 macOS，届时单独确定服务拓扑、测试数据隔离与启动流程；
QMT 和券商运行时仍留在 Windows。当前不提供 macOS 启动器、WinSW、Kubernetes
或 release 安装路径。跨机器访问使用实际运行端的 Caddy 地址，不能将远端地址
替换成开发机的 localhost。平台边界以根 `AGENTS.md` 为准。

## 唯一启动入口

从仓库根目录运行：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
.\ops\quantx.ps1 down
```

普通 `up` 会解析为 `full/live`，启动 Caddy、API、Market Gateway、Engine、
Vite、VitePress、Prefect Worker，并在 QMT 登记和运行时预检通过后启动同机
QMT Agent。只有明确需要关闭实盘连接时才使用：

Prefect Worker 及其隔离启动的 Research 子进程固定使用仓库 `.venv`；启动前会
校验 `prefect`、`quantx_worker` 和 `quantx_research` 均可导入。QMT Agent 继续使用
包含券商依赖的 `xtquant-demo` 环境，Worker/Research 不使用该环境，以免研究/GPU
依赖污染券商运行时；`.venv` 缺失或依赖不完整时先在仓库根目录执行 `uv sync`。

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web -Mode data-only
```

Market Gateway 先启动，启动器仅等待其 `/health/live` 后继续启动 API 和 Agent。
网关 `/health/ready` 需要当前 QMT 行情连接与完整供给快照，不得在 Agent 启动前
用它阻塞启动顺序。最终 full readiness 仍检查供给、Engine 消费和 QMT 状态。

Monitor 保持独立生命周期：

```powershell
.\ops\quantx.ps1 up -Environment dev -Component monitor
.\ops\quantx.ps1 status -Environment dev -Component monitor
.\ops\quantx.ps1 logs -Environment dev -Component monitor
.\ops\quantx.ps1 down -Environment dev -Component monitor
```

`ops/quantx.ps1` 只接受 `-Environment dev`，不提供 install、uninstall、rollback
或 agent-mode 命令。不得绕过统一入口单独启动 QMT Agent，以免重复会话争用。
Windows 需要 Node 20；若 nvm 的 PATH 在非交互 Shell 中不可见，可在
`apps/api/.env.development` 设置 `QUANTX_NODE_EXE` 为对应 `node.exe` 的绝对路径。

## 地址与端口

Caddy 是唯一公开入口，监听 `0.0.0.0:8080`：

- Windows 本机：`http://127.0.0.1:8080`
- 局域网客户端、iOS、Web codegen：`http://192.168.5.6:8080`
- GraphQL HTTP：`http://192.168.5.6:8080/graphql`
- GraphQL WebSocket：`ws://192.168.5.6:8080/graphql`
- QMT Agent 登记根地址：`http://192.168.5.6:8080`

内部端口只绑定 `127.0.0.1`：API `18081`、Market Gateway `18082`、Monitor
`18083`、Vite `5250`、VitePress `5251`。QMT Agent 只读健康端点使用
`0.0.0.0:18084`。

本地 Dev 使用 HTTP/WS，不启用 TLS。首次访问时只需允许 Caddy 通过 Windows
专用网络防火墙，不需要安装私有 CA。

## 外部依赖

PostgreSQL、Redis、InfluxDB 和 Prefect Server 由外部环境提供，启动器只检查，
不负责安装或启停。Prefect API 默认是 `http://192.168.5.6:30420/api`，Worker
pool 为 `quantx-pool`。

若这些依赖运行在同机 WSL，Windows 通过 portproxy 暴露 `30081`、`30420`、
`30179` 和 `32432`。WSL NAT 地址变化时，用管理员 PowerShell 幂等安装同步任务：

```powershell
.\ops\windows\sync-wsl-portproxy.ps1 install
.\ops\windows\sync-wsl-portproxy.ps1 status
```

`QuantX-WSL-PortProxy` 仅维护上述四个端口，每五分钟解析一次 WSL `eth0`；不会
重置其他 portproxy，也不会修改 Windows 防火墙规则。

`.env` 只从 `apps/api/.env` 和 `apps/api/.env.development` 读取。主服务地址应为：

```dotenv
PUBLIC_URL=http://192.168.5.6:8080
QUANTX_AGENT_API_URL=http://192.168.5.6:8080
```

具体变量名以 `apps/api/.env.example` 为准；券商凭据和设备密钥不得提交。

## Dev 实盘安全

Dev 实盘使用 `ENV=testing`，并仍需 `ENABLE_REAL_TRADING=true`、
`QMT_REAL_TRADING_ENABLED=true`、唯一账户白名单、Agent READY、新鲜快照和对账
就绪。启动预检失败时保持 `full/live` 请求，但关闭服务端实盘能力门并以
`DEGRADED / BLOCKED` 启动非 QMT 服务，不得伪装成 `data-only` 或 `ready`。

QMT Agent 的 token 只用于建立新连接；后台刷新不会主动拆除健康连接。PostgreSQL
或 Redis 短暂抖动时，API 在原会话内背压并重试，只有真实的认证失效、会话替换、
传输中断或超过行情新鲜度预算才触发重连与重同步。

本机 Agent 健康以启动器记录的本次 QMT 启动边界和服务端 heartbeat `updated_at`
为准。Agent 自报时间与服务端处理时间的差值只用于诊断，不会因队列或数据库短暂
积压产生 5 秒硬阻断；API/Agent 会话 ID 仅保护连接替换、命令发送、行情租约和报告
归属。断线、旧启动心跳、重复 live Agent、快照过期或未完成对账仍保持硬阻断。

## 备份、迁移与检查

备份、隔离恢复验证和数据库前向迁移仍属于 Dev 数据维护：

```powershell
.\ops\quantx.ps1 backup -Environment dev
.\ops\quantx.ps1 restore-verify -Environment dev -BackupPath <目录>
.\ops\quantx.ps1 migrate -Environment dev
.\ops\quantx.ps1 doctor -Environment dev
.\ops\quantx.ps1 verify -Environment dev
```

`restore-verify` 启动时输出验证 ID；阶段状态与脱敏日志持续写入
`.runtime/restore-verifications/<ID>/status.json` 和 `verification.log`。正常执行期间
等待原进程退出，或按需读取这个小状态文件与日志尾部，不反复读取整库或重启验证。
`COPY bytes_processed` 是当前 COPY 操作的计数，不能当作整库总量或可靠百分比。

数据导入完整、但 schema 检查/升级失败时，保留该隔离数据库供修复后重试：

```powershell
.\ops\quantx.ps1 restore-verify -Environment dev -BackupPath <原目录> -RestoreVerificationId <ID>
```

重试要求备份路径、manifest 指纹和数据库服务器一致，并重新校验备份文件校验和。
只跳过已成功的数据导入；重新执行 schema 检查/升级和 QMT journal、Monitor 完整性
检查，不把阶段通过当作整体验收完成。同一个 ID 不能并发执行，也不能重复执行已通过
的验证。部分导入失败会清理本次创建的临时库，不可复用。

schema 验证成功后临时 PostgreSQL 库会清理；后续 journal/Monitor 失败仍留下日志，
但这个已清理的数据库不能按 ID 复用。失败保留的隔离库占用磁盘，需要在成功重试后
自动清理，或在明确放弃该验证时由运维确认清理。不要手工编辑状态文件或对保留库
运行无关写入。重试验证的是当前保留状态，不能替代必须从原始备份重新开始的迁移测试。

先以小规模、覆盖迁移约束的数据运行定向测试，再做最终完整恢复验收。失败先查看
脱敏日志，不用再次全量恢复来获取第一次遗漏的错误输出。

`down` 只停止 `.runtime/state` 中记录且 PID/启动时间匹配的进程，不终止未受
QuantX 管理的进程。

## 验收

```powershell
.\.runtime\tools\caddy\caddy.exe validate `
  --config .\ops\caddy\Caddyfile.dev --adapter caddyfile
python -m pytest tests/infrastructure/test_ops_contract.py
```

完整 Dev 实盘验收还应确认 `status` 显示 `profile=full`、`agentMode=live`、
唯一账户、`liveTrading=ENABLED`，且 QMT Agent、对账和行情流稳定为 `ready`。
