# Windows Dev 运行与运维

QuantX 是个人单账户项目，只维护 Windows 工作区中的 `dev` 运行形态。不存在
production、WinSW、Kubernetes、release 安装或 macOS 服务端部署路径。

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

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web -Mode data-only
```

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

## 备份、迁移与检查

备份、隔离恢复验证和数据库前向迁移仍属于 Dev 数据维护：

```powershell
.\ops\quantx.ps1 backup -Environment dev
.\ops\quantx.ps1 restore-verify -Environment dev -BackupPath <目录>
.\ops\quantx.ps1 migrate -Environment dev
.\ops\quantx.ps1 doctor -Environment dev
.\ops\quantx.ps1 verify -Environment dev
```

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
