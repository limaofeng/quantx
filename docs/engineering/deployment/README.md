# QuantX 部署与运维

部署标准只有两种形态：

- **开发环境**：在 macOS 或 Windows 工作区使用 `ops/quantx` / `ops/quantx.ps1`
  统一启动；
- **正式环境**：服务端只使用 Linux 容器和 Kubernetes，不在 Windows 上安装
  Caddy、API、行情服务、Monitor、Engine、Worker 或 AI Runtime 系统服务。

QMT Agent 因 XTQuant 限制仍运行在 Windows，但它是集群外的交易执行边缘节点，
交易控制、行情、历史上传和报告链路只向 Kubernetes 公共入口建立出站连接；
同一进程另提供固定只读健康监听，不属于服务端生产宿主环境。

开发环境唯一入口：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 up -Environment dev -Profile full -AccountId <账户>
.\ops\quantx.ps1 up -Environment dev -Profile web -Mode data-only
.\ops\quantx.ps1 status
.\ops\quantx.ps1 logs
.\ops\quantx.ps1 down
.\ops\quantx.ps1 up -Environment dev -Component monitor
.\ops\quantx.ps1 status -Environment dev -Component monitor
.\ops\quantx.ps1 logs -Environment dev -Component monitor
.\ops\quantx.ps1 down -Environment dev -Component monitor
```

普通开发 `up`（包括未显式指定模式的 `-Profile web`）会提升为 `full/live`，
启动 Caddy、API、Market Data Service、Engine、Vite、VitePress 和 Prefect
Worker，并在登记与运行时预检通过后启动 QMT Agent。
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
各功能自己的实盘能力门，并注入同一账户白名单。`T_TRADE_LIVE_ENABLED` 只启用
做 T 助手，不参与账户或 QMT Agent 的实盘能力判定。启动器同时在第一个服务
进程创建前固定 `QMT_AGENT_LAUNCH_STARTED_AT`；命令路由、做 T 就绪、历史补数
设备选择和组件健康只接受不早于该边界的 QMT 心跳，CLI 还要求本次记录的 QMT
进程 PID/启动时间仍匹配，避免上一次启动遗留的 90 秒新鲜心跳冒充本次就绪。这不会
绕过账户白名单、live Agent、快照、对账或 `SHADOW / CANARY / LIVE` 灰度门禁。
显式使用 `-Mode data-only` 时，实盘能力门同样关闭；它不是 QMT 预检失败时的
隐式兜底模式。
`down` 仅停止状态文件记录且启动时间匹配的进程。

`quantx-monitor` 是独立观测进程。普通 `up/down` 不拥有它的生命周期，开发环境
通过 `-Component monitor` 单独启停；生产环境使用独立的单副本 StatefulSet 和
PVC。它每 30 秒检查固定目标，把原始样本写入独立 SQLite 90 天，并保留 1 年
小时汇总。状态页位于 `/settings/status`，只读数据通过 Caddy 的 `/monitor/*`
公开。启动 Monitor 前必须设置
`MONITOR_QMT_AGENT_HEALTH_URL=http://<稳定 Windows 主机>:18084`。Monitor 直连该
只读端点采集真实 RTT，并与 API 会话/对账语义组合；它只记录事实，不参与
`/health/ready`、账户执行能力或交易门禁。
完整目标、状态机和 API 见 [Monitor 工程指南](../monitor/README.md)。

启动器会在读取 `.runtime` 状态或创建子进程前，将仓库根目录的 Junction / 符号
链接解析为真实物理路径。即使从工作区目录联接调用 `ops/quantx.ps1`，Vite、API、
Engine、Worker、QMT Agent 与 Caddy 也会统一使用同一个真实根路径，避免 Windows
下因联接路径与依赖真实路径不一致而跳过 TSX 转译并出现白屏。

开发环境公开端口只有 Caddy 的 `8080`，它监听所有本机 IPv4 接口；本机使用
`http://127.0.0.1:8080`，局域网设备使用
`http://<开发机局域网 IP>:8080`。API 使用 `18081`、Market Data Service 使用
`18082`、Monitor 使用 `18083`、Vite 使用 `5250`、
VitePress 使用 `5251`，这些后端端口仍只绑定 `127.0.0.1`。Windows QMT Agent
的只读健康监听单独使用 `0.0.0.0:18084`，供 Monitor 跨主机直连。Prefect API
通过 `PREFECT_API_URL` 连接外部服务，默认
`http://192.168.5.6:30420/api`，Worker 使用 `quantx-pool`。在线客户端文档
位于统一入口 `/docs/`。正式环境由 Gateway 容器直接提供 Web 与 Docs 静态文件，
不运行 Node 进程；Ingress 或外部负载均衡器负责 TLS。PostgreSQL、InfluxDB、Redis
和 Prefect Server 均为外部依赖，不由开发启动器或 Kubernetes 工作负载安装。

QMT Agent 的交易、行情和报告链路仍只出站；`18084` 只接受固定健康 GET。生产
`quantx-agent.ps1 up` 会校验并幂等维护名为
`QuantX-QMT-Agent-Health-18084` 的 Windows 防火墙规则，仅对 `Private` profile
开放 TCP `18084`，远端地址为 `Any`，不绑定单一 Monitor IP。创建或修复规则需要
管理员 PowerShell；规则正确时普通重启不需要提升权限。若主机当前网络不是
Private，应先修正网络分类，不能扩大到 Public/Domain 规则。

Market Data Service 的 `/health/live` 只表示进程与事件循环存活；
`/health/ready` 会实际执行 Redis `PING`。统一启动器和 API 组件状态均以后者
作为服务就绪依据，因此 Redis 断开时不会把行情服务误报为 `ready`。
API 的 `/health/service-ready` 是 Kubernetes Service 路由门禁，只检查 API 与
PostgreSQL；强业务门禁 `/health/ready` 继续检查 Engine、Worker、QMT Agent、
行情服务和行情新鲜度。二者不得互换，否则 QMT 断线时会把用于恢复连接的 API
Pod 一并摘除。

## Kubernetes 正式环境

生产基线位于 [`ops/k8s/base`](../../../ops/k8s/base)，容器入口位于
[`ops/containers`](../../../ops/containers)。同一版本发布两个不可变镜像：

- `quantx-server`：API、Market Data Service、Engine、Worker、AI Runtime 和
  Monitor 共用代码镜像，但分别作为独立工作负载启动；
- `quantx-gateway`：只包含 Caddy、Web `dist` 和 Docs `dist`。

API、行情服务、Engine、Worker 和 AI Runtime 使用单副本 `Deployment`；Monitor
使用单副本 `StatefulSet` 与独立 PVC；Gateway 使用两个副本。个人单账户系统不做
无依据的水平扩容，Engine 仍由 PostgreSQL 租约保证唯一所有者。配置通过
`quantx-runtime-config` 注入，密钥必须通过集群 Secret 管理方案注入
`quantx-runtime-secrets`，不得把填充值提交到仓库。

Kubernetes 只管理 QuantX 工作负载。PostgreSQL、Redis、InfluxDB 和 Prefect
Server 的高可用、备份与恢复由各自的平台服务负责。发布、迁移、回滚和验收步骤见
[生产运行手册](./PRODUCTION_RUNBOOK.md)。`quantx up` 永远不是生产部署入口。

## PostgreSQL 连接预算与慢查询诊断

QuantX 在同一操作系统进程内只创建一个 SQLAlchemy 连接池，业务模块统一复用；
不同进程无法共享 Python 数据库连接，因此按进程角色设置容量，而不是在 API 内
再拆 Agent 专用池。默认预算为 API `8 + 4`、Market Data Service `1 + 1`、
Engine `6 + 2`、Worker `4 + 2`、AI Runtime `2 + 1`，合计最多 31 条池连接。
Engine 单实例数据库租约另用 1 条从共享池脱离的专用物理连接，因此进程总预算
最多 32 条；它不是第二个连接池，关闭 Engine 时直接关闭。这样报告投影、命令消费、
心跳、监控器和策略状态持久化可共用完整的 8 条 Engine 工作池容量。统一启动器通过
`DATABASE_PROCESS_ROLE` 注入角色；`DATABASE_POOL_SIZE` 与
`DATABASE_MAX_OVERFLOW` 仅用于经过容量评审后的临时覆盖。所有池使用 3 秒获取
超时、30 分钟回收、连接预检和 LIFO 复用；API 查询另有 15 秒 statement
timeout，避免慢查询长期占住连接。Engine 心跳的 `details.databasePool` 同时
暴露当前池容量、借出和溢出连接数，便于区分数据库慢查询与进程内连接占用。
Engine 恢复报告积压时，仅合并同一 Agent 已由更新且结构校验通过的完整快照；
夹在快照之间的委托、成交和持仓增量仍严格按接收顺序收敛。

`/metrics` 中的 `quantx_database_pool_connections` 按 `role/state` 展示池大小、
借出、空闲、overflow 与最大预算；GraphQL 查询准入的活动数、等待时间和拒绝数
分别由 `quantx_graphql_query_admission_active`、
`quantx_graphql_query_admission_wait_seconds` 与
`quantx_graphql_query_admission_rejections_total` 展示。排查连接耗尽时先按
`application_name` 观察占用，再查 `pg_stat_statements`，不要先放大超时：

```sql
SELECT application_name, state, count(*)
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY application_name, state
ORDER BY application_name, state;

SELECT queryid, calls, mean_exec_time, max_exec_time, rows,
       left(query, 160) AS query_sample
FROM pg_stat_statements
ORDER BY max_exec_time DESC
LIMIT 20;
```

若第二条查询提示视图不存在，应由 PostgreSQL 管理侧评估并启用
`pg_stat_statements`；QuantX 启动器不会修改外部数据库实例配置。

Prefect Worker 的本机 CLI 状态位于 `.runtime/prefect`，CLI 和 Worker 固定
使用 UTF-8。不要把该运行时目录提交到仓库。

生产部署不使用工作区源码或本机 Python 环境。完整镜像发布、迁移、备份、回滚、
紧急停止与影子/CANARY 步骤见
[PRODUCTION_RUNBOOK.md](./PRODUCTION_RUNBOOK.md)。`bootstrap` 仅供开发环境；
正式版本以镜像 digest 和 Git tag 唯一标识。
升级到 `20260815_0016` 会 fail-closed 撤销无法区分的旧 native/Web 会话，
不删除会话或审计记录；发布前必须按运行手册预告用户重新登录。

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
fail-closed 降级状态。正式环境在 Windows 执行节点使用
`ops/quantx-agent.ps1 -Environment production` 管理 Agent；集群内不运行 QMT
容器，也不挂载券商目录或设备凭证。
`paper` 和 `live` 还要求设置
`QMT_ACCOUNT_WHITELIST`；`live` 额外要求 QMT Agent 子进程使用 `ENV=testing` 或
`ENV=production`、`ENABLE_REAL_TRADING=true` 和
`QMT_REAL_TRADING_ENABLED=true`。production 还要求
服务端 `REAL_TRADING_ACCOUNT_ALLOWLIST`、独立账户增仓授权、Agent READY、快照
新鲜度、对账状态和策略授权全部通过。只有启用做 T 助手时才额外要求
`T_TRADE_LIVE_ENABLED=true` 及助手自己的灰度证据。

账户级实盘安全状态独立展示健康与能力：账户事实链路正常时为 `HEALTHY`，执行
权限按 `OBSERVE_ONLY / REDUCE_ONLY / TRADING / KILLED` 展示；具体助手未启用
不得把账户健康误报为故障。做 T 自己继续使用 `SHADOW / CANARY / LIVE` 与
`PREPARING / READY / BLOCKED` 表达助手灰度和自动确认能力。QMT 手工活动在观察
阶段不会单独制造账户级 `BLOCKED`。

账户实盘窗口建立后，以当时完整快照中的历史外部委托/成交作为审计基线；只有
基线之后新增的外部活动才使窗口失效并暂停自动执行。开发环境允许账户从
做 T 助手允许从 `SHADOW` 直接进入 `LIVE`，但必须同时满足独立账户增仓授权、
新鲜账户实盘窗口、24 小时内成功备份、
全部 readiness 门禁、`trade:approve` 权限，并精确确认
`LIVE:<账户>`。生产环境仍禁止 `SHADOW` 直升 `LIVE`，继续使用既有 Canary
流程。建立窗口、启用、暂停和失败尝试均写入追加式审计事件。

## 端口与进程安全

`up` 遇到端口冲突只报告 PID 与可用的命令行信息，不终止未记录在
`.runtime/state` 中的进程。`down` 会校验 PID 与进程启动时间，只按依赖
倒序停止本次 QuantX 运行记录中的进程。旧的或由其他方式启动的 QuantX
实例必须由其原启动方式停止。

## 非交易时段行情压测

全市场链路压力测试不得连接或替换已登记的 QMT Agent。统一工具会启动独立动态
端口网关，使用随机 Redis keyspace，并在确认 `tradingSession=false` 后执行：

```powershell
uv run python ops/market-stream-load-test.py run `
  --profile standard --duration 30m --allow-shared-redis
```

`--allow-shared-redis` 只允许共享 Redis 进程资源，不允许共享生产行情键；工具
禁止全库扫描和 `FLUSHDB`，中断或失败也必须停止测试 supervisor 并精准清理本次
keyspace。报告位于 `.runtime/reports/market-stream-load-test/`。

## 验收命令

```powershell
.\.runtime\tools\caddy\caddy.exe validate `
  --config .\ops\caddy\Caddyfile.dev --adapter caddyfile
python -m pytest tests/infrastructure/test_ops_contract.py
```

正式清单验收：

```bash
kubectl kustomize ops/k8s/base >/tmp/quantx-k8s.yaml
docker build -f ops/containers/server.Dockerfile .
docker build -f ops/containers/gateway.Dockerfile .
```

GraphQL 契约验收必须在 `web` profile 已经就绪后，通过
`http://127.0.0.1:8080/graphql` 执行；不要绕过 Caddy 指向 API 内部端口。
