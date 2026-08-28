# macOS Dev full/live 运行手册

本文记录“Mac full/live 运行环境改造”的实际交付契约。迁移总说明和方案二仍是范围、
集成顺序与最终验收的权威规格；在方案一远程会话改造及跨主机验收完成前，不得把本
文中的本地通过误写成整项迁移完成。

## 1. 已验证平台与安装选择

首个版本固定为：

- macOS arm64。
- Python `3.13.9`，由 `.python-version` 固定。
- Node `20.20.2`，由 `.nvmrc` 固定；npm 必须不低于 `10`。
- Caddy `2.11.4` macOS arm64，归档和解包后二进制 SHA-256 均在
  `ops/tools.lock.json` 中锁定。

Mac 服务端安装不包含 QMT Agent：

```bash
nvm use
uv sync --locked --group dev
npm install
./ops/quantx bootstrap
```

启动命令也必须在 `node --version` 为 `.nvmrc` 固定的 `v20.20.2` 的 shell 中执行；
检测到其他 Node 版本时，启动器会在创建组件前以退出码 `69` 失败。

Windows QMT 执行环境安装根项目的 `qmt-agent` extra：

```bash
uv sync --locked --group dev --extra qmt-agent
```

Mac 执行完整 monorepo 测试（包括 QMT Agent 的模拟器和边界测试）时也使用该 extra；
这只安装跨平台测试依赖，不安装厂商 XTQuant SDK，也不把 Agent 加入 Mac 服务进程图。

`bootstrap` 只安装并校验仓库锁定的 Mac Caddy，同时验证 Python、Node 和 npm；
它不安装或导入 XTQuant、miniQMT 或 `quantx_qmt_agent`。

## 2. full/live 配置

在进程环境、仓库根 `.env` 或 `apps/api/.env.development` 中配置服务端依赖和公开
入口。加载优先级依次为进程环境、`apps/api/.env.development`、`apps/api/.env`、仓库
根 `.env`：

```dotenv
PUBLIC_URL=http://quantx-dev.internal:8080
QUANTX_CADDY_TRUSTED_IPS=192.168.50.20/32
DATABASE_URL=postgresql+asyncpg://<服务端数据库连接>
REDIS_URL=redis://<外部 Redis>/0
REDIS_HOST=<外部 Redis 主机>
REDIS_PORT=6379
INFLUXDB_HOST=https://<外部 InfluxDB>
PREFECT_API_URL=http://<外部 Prefect Server>/api
PREFECT_WORKER_POOL=quantx-pool
REAL_TRADING_ACCOUNT_ALLOWLIST=<唯一账户>
```

当前 Dev 外部服务统一使用 `192.168.5.6`，端口不变：

| 服务 | 不含凭据的目标端点 |
| --- | --- |
| PostgreSQL | `192.168.5.6:32432` |
| Redis | `192.168.5.6:30179` |
| InfluxDB | `http://192.168.5.6:30081` |
| Prefect Server | `http://192.168.5.6:30420/api` |

`QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST=192.168.5.6` 会把四个 URL 的主机部分原子改写
为该地址；启动器仍分别保留原 URL 的协议、端口、路径和凭据。任一服务未监听或健康
检查失败时，`up` 在创建 Mac 子进程前以退出码 `69` 终止。

`PUBLIC_URL` 必须是不含凭据和路径、显式使用 `8080` 端口的稳定 HTTP(S) 地址。
当前本地 Dev 在受控私有局域网内统一使用 HTTP，避免分发和维护开发 CA；只有需要
模拟 TLS 的专项验收才显式配置 HTTPS。
`QUANTX_CADDY_TRUSTED_IPS` 必须至少包含 Windows Agent 的固定私网地址或受控私网
网段。启动器还会自动加入 Mac 自身 IPv4 地址和回环地址，其他来源由 Caddy 返回
`403`。内部 API、Market Gateway、Vite 和 VitePress 始终只监听 `127.0.0.1`。

启动器不会读取或保存 Windows 设备密钥、券商凭据、QMT 路径或 QMT journal。
`T_TRADE_LIVE_ENABLED` 继续是独立功能开关；未显式配置时保持关闭，不影响账户级
full/live 配置事实。

启动前的 PostgreSQL 只读预检会统计以下共享字段中的 Windows 绝对路径，但不会把
路径值写入终端或状态文件：

- `strategies.file_path`
- `strategy_backtests.result_path`
- `strategy_grid_book_snapshots.source_path`
- `market_data_transfer.storage_reference`

活动路径计数不为零时 `up` 以退出码 `69` 阻断。`COMPLETED`/`FAILED` 行情请求的
staging 文件已按终态清理契约删除，其旧绝对引用只作为历史审计计数报告，不伪造成
Mac 文件，也不阻断启动。需要保留的历史回测产物应先复制到 Mac 的
`data/backtests/`，逐条核对内容和摘要后把记录改为受控相对路径；未完成的行情上传应
核对请求状态后重新发起。禁止按盘符或文件名批量猜测替换。新行情分块只在数据库中
保存 `<request-id>/<chunk-name>`，API 和 Worker 在同一个
`QUANTX_RUNTIME_DIR/market-data` 根下解析并再次验证边界。

### Dev 传输

当前 Mac 与 Windows QMT 节点位于受控私有局域网，Dev 使用 HTTP/WS，不需要安装
Caddy 根证书，也不设置 `SSL_CERT_FILE`。Caddy 仍是唯一公开入口，并通过
`QUANTX_CADDY_TRUSTED_IPS` 限制来源；API、Market Gateway 和其他服务继续只绑定
回环地址。

如果专项测试显式把 `PUBLIC_URL` 改为 HTTPS，Windows 节点仍必须信任 Mac Caddy
根证书，并把公钥证书保存为
`F:\Workspace\quantx\.runtime\certs\mac-dev-root.crt`。Agent 会严格使用登记的
scheme，不会在 TLS 失败后自动降级到 HTTP。

Mac 服务全部 READY 后，Windows 只通过 Agent 专用入口接入：

```powershell
.\ops\quantx-agent.ps1 doctor -Environment dev
.\ops\quantx-agent.ps1 up -Environment dev
.\ops\quantx-agent.ps1 status -Environment dev
.\ops\quantx-agent.ps1 logs -Environment dev
.\ops\quantx-agent.ps1 down -Environment dev
```

`up` 使用当前 Windows 交互用户的计划任务启动 supervisor，使 Credential Manager
仍可读取设备密钥；任务本身只启动一个 `xtquant-demo` QMT Agent，不拥有 Mac 服务、
数据库、Redis、Prefect 或 Caddy 的生命周期。当前登记根地址固定为
`http://192.168.5.16:8080`，所有 token、控制、市场和上传地址都由它派生。

## 3. 权威命令

```bash
./ops/quantx up --environment dev --profile web
./ops/quantx status
./ops/quantx logs
./ops/quantx logs --component engine --tail 200
./ops/quantx down

./ops/quantx up --environment dev --component monitor
./ops/quantx status --environment dev --component monitor
./ops/quantx logs --environment dev --component monitor
./ops/quantx down --environment dev --component monitor
```

普通 `web` 启动原子提升为 `full/live`。唯一显式非实盘入口是：

```bash
./ops/quantx up --environment dev --profile web --mode data-only
```

full/live 启动不执行本机 QMT 登记或运行时预检，不创建 QMT 进程，也不通过 SSH、
WinRM 或其他方式联系 Windows。Agent 离线不会使静态配置降为 `data-only`；非 QMT
服务正常启动，`status` 显示 `DEGRADED`、`REMOTE_AGENT_OFFLINE` 和
`liveTrading=DISABLED`。Agent 恢复并通过账户、快照、对账、市场流、备份和授权门后，
同一 API/Engine 进程的状态动态变为 `ENABLED`。

## 4. 组件图与生命周期

固定启动顺序为：

```text
外部依赖检查
  -> sleep-guard
  -> Market Gateway
  -> API
  -> Engine
  -> AI Runtime（功能开启时）
  -> Prefect Worker（full profile）
  -> Web
  -> Docs
  -> Caddy
  -> Caddy 公共端点验证
```

`sleep-guard` 使用 macOS `caffeinate -dimsu`，只在本次 Dev 运行期间阻止系统睡眠。
每个组件由独立的 Unix 进程组和单实例 supervisor 管理。Supervisor 使用
`1/2/5/10/30` 秒崩溃退避、10 MiB × 5 份的 stdout/stderr 轮转日志和 Unix 文件锁。
Mac 启动器为 Engine 注入本轮唯一实例 ID；readiness 必须同时匹配该 ID 和 `ready`
状态，因此共享数据库中另一台主机的旧 Engine 心跳不能让本轮启动误通过。
启动前还会只读检查 PostgreSQL `pg_locks`；若旧运行仍持有
`quantx-engine-singleton-v1`，`up` 在创建任何 Mac 子进程前以退出码 `69` 阻断。

停止顺序固定为 Caddy、Web/Docs、Engine、Worker、AI Runtime、API、Market Gateway、
sleep-guard。先关闭唯一公开入口，避免停止期间继续接收新命令；启动器只有在 PID、
创建时间、进程组和命令摘要全部匹配时，才向
该进程组发送 `SIGTERM`；超出有界优雅窗口后才发送 `SIGKILL`。`down` 不读取、不
联系、不停止 Windows Agent。普通 `up/down` 也不拥有 Monitor 生命周期。

启动独立 Monitor 前，在 Mac Dev 环境文件中设置稳定 Windows QMT 主机的健康根
地址，例如：

```bash
MONITOR_QMT_AGENT_HEALTH_URL=http://windows-qmt-host:18084
```

该值不从 WebSocket remote address 推测，不能包含凭据、路径、查询或 fragment。
Monitor 不跟随重定向，也不会把完整内部 URL 写入公共 API、日志或异常文本。

## 5. 状态、日志与退出码

主运行状态位于：

```text
.runtime/state/macos-dev-runtime.json
```

Monitor 使用独立状态：

```text
.runtime/monitor/dev-runtime.json
```

状态 schema 版本为 `1`，顶层记录物理仓库根、runtime ID、profile、agent mode、
配置账户、配置实盘能力、公开 URL 和组件列表。每个组件记录 supervisor PID、进程组、
进程创建时间、命令摘要、工作目录和 readiness 探针；不记录环境变量或凭据。
原子写入文件权限为 `0600`。状态损坏时命令显式报错，不猜测、覆盖或模糊搜索进程。
`status` 为每个组件分别输出 `Process` 与 `Readiness`，存活但探针失败的组件不会让
整体状态成为 `READY`。部分启动失败会记录失败组件、脱敏原因和检查日志后的重试提示；
即使本轮组件已全部安全清理，后续 `status` 也不会隐藏最后一次失败。

Mac 日志位于 `.runtime/logs/macos/`，Monitor 日志位于
`.runtime/monitor/logs/`；`logs` 不读取 Windows WinSW 日志。

主要退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 命令完成；Agent 离线导致的预期 `DEGRADED` 仍可正常启动 |
| `65` | 状态文件损坏、schema 不支持或物理仓库根不匹配 |
| `69` | 工具链或外部依赖不可用 |
| `73` | 已有受管运行或端口被其他进程占用 |
| `74` | 一个或多个已验证进程组未能停止 |

API 的只读 `/health/runtime/live-trading` 端点复用现有账户安全服务，不创建第二套
交易门语义。CLI 用它区分静态 `configuredLive` 与动态 `liveTrading`，并同时展示
协议、快照、市场流、对账、备份和 Engine 租约摘要。

非交易时段的全市场压测从 Mac 直接执行，不依赖 `wsl.exe`：

```bash
uv run python ops/market-stream-load-test.py run \
  --profile standard --duration 30m --allow-shared-redis
```

它会先验证公开 API 就绪且不在交易时段，使用隔离 Redis keyspace，并把报告写入
`.runtime/reports/market-stream-load-test/`。如果旧配置仍把
`QUANTX_DEV_EXTERNAL_DEPENDENCY_HOST` 写为 `wsl`，Mac 会显式阻断，必须改成真实外部
服务地址。

full/live 预检要求 macOS 网络时间服务运行；本轮运行期间由受管的 `caffeinate`
进程阻止系统睡眠。实盘联调应优先使用稳定有线网络，并确认 FileVault、登录会话和
密钥链策略不会让后台开发进程失去服务端配置读取权限。网络或登录环境发生切换时按
网络分区处理，先确认动态实盘能力已关闭，再进行恢复与重新对账。

## 6. 跨主机验收边界

当前迁移已经验证 Windows Agent 经 Mac HTTP/WS 完成控制连接、完整账户快照、
三阶段市场同步和 Engine watermark 追平；Agent 断开会立即关闭动态实盘能力，
Market Gateway 子进程重启后会从 `OFFLINE/SYNCING` 恢复，并且只有所有安全门同时
通过时才短暂显示 `liveTrading=ENABLED`。非交易时段没有新 tick 后，行情 freshness
租约按设计过期并再次 fail-closed，不应被误判为迁移失败。

下列事项仍需要独立授权或更长观察窗口，不能由本轮运维联调替代：

- 一个完整交易时段的 SHADOW 观察和全市场非交易压测。
- 用户明确授权后的 CANARY 委托、撤单或成交全生命周期。

运行证据写入 `.runtime/reports/mac-dev-migration/macos-runtime/`，不得包含私钥、设备
密钥、券商凭据或数据库密码。
