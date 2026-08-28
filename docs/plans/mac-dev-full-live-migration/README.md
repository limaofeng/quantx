# QuantX Mac 开发环境 full/live 迁移总说明

> 文档状态：实施规格草案  
> 版本：1.1
> 基线日期：2026-08-27  
> 适用范围：开发环境迁移；生产部署、K8s、Dev/Prod 并行运行均不在本轮范围内

本迁移把 QuantX 开发环境拆成两个独立运行节点：Mac 运行全部服务端与客户端
开发服务，Windows 只运行唯一能够访问 XTData/XTTrading 的 QMT Agent。迁移完成
标准是完整的 `full/live` 实盘能力，不是 `data-only` 或 `paper`。

本文是四份文档的总入口，负责冻结三个执行方案之间的公共契约、集成顺序和最终
验收标准。三份实施方案可以在不同服务器、不同工作分支上执行：

1. [QMT Agent 远程执行端改造](01-qmt-agent-remote-execution-plan.md)
2. [Mac full/live 运行环境改造](02-macos-full-live-runtime-plan.md)
3. [Windows 启动器清理](03-windows-launcher-cleanup-plan.md)

这些文档描述的是目标态。对应代码合并并完成验收前，不得把其中的目标命令当作
当前可用的运维命令。

## 1. 迁移目标

目标拓扑如下：

```text
Windows QMT 执行节点
├── QMT 客户端 / XTData / XTTrading
├── quantx-qmt-agent（唯一允许导入 xtquant 的进程）
├── 改造前既有的 `xtquant-demo` Conda 环境
├── Windows Credential Manager
├── 本地命令与回报 journal
└── 开发实盘联合备份协调器
               │
               │ 仅出站 HTTP(S) / WS(S)
               ▼
Mac 开发服务节点
├── Caddy（唯一对外入口）
├── API / GraphQL / Agent WebSocket Hub
├── Market Gateway
├── Engine
├── Prefect Worker
├── Web / VitePress
└── Monitor（独立生命周期）
               │
               ▼
外部 PostgreSQL / Redis / InfluxDB / Prefect Server
```

迁移后的完整业务链路必须是：

```text
Mac StrategyBase.step
  -> Engine 生成 TradeIntent
  -> 交易域与风控生成命令
  -> 数据库 outbox
  -> API Agent Hub
  -> Windows QMT Agent
  -> XTTrading 下单或撤单
  -> QMT 委托/成交/持仓回报
  -> Agent 本地 journal
  -> API inbox 持久化
  -> Engine 收敛
  -> Web / GraphQL 展示
```

`command_ack` 仍然只表示投递，不能推进成交状态；实盘成交真源仍然只能来自
QMT Agent 上报的委托与成交回报。

## 2. 范围与非目标

### 2.1 本轮范围

- Windows QMT Agent 从本机子进程改为独立、出站连接的远程执行节点。
- Mac 运行 Caddy、API、Market Gateway、Engine、Worker、Web、Docs 和独立
  Monitor。
- 普通 Dev 启动仍默认解析为 `profile=full`、`agentMode=live`。
- Agent 离线时服务端可以启动，但所有实盘执行能力必须动态 fail-closed。
- Agent 接入并完成完整快照、对账和全部安全门后，系统无需重启即可恢复实盘。
- Windows Dev 启动器最终只保留 QMT Agent、journal 和联合备份职责。
- 迁移期间维持个人单账户模型，不增加多账户、多租户抽象。

### 2.2 明确非目标

- 不设计或实施 K8s。
- 不设计生产部署。
- 不解决 Dev 与 Prod 同时启动、数据库隔离或环境命名空间问题。
- 不把 QMT SDK、券商配置或设备密钥迁移到 Mac。
- 不让 Mac 通过 SSH、WinRM 或其他方式控制 Windows 进程。
- 不让 Windows 启动器控制 Mac 进程。
- 不以 `data-only`、`paper`、模拟 Broker 或 Mock 回报替代最终实盘验收。
- 不引入长期双启动协议或旧协议兼容层。

`data-only` 仍可作为显式诊断模式保留，但其通过不能作为任何一个方案完成的证据。

## 3. 三个方案的边界

| 方案 | 主要执行节点 | 主要代码所有权 | 输出 |
| --- | --- | --- | --- |
| QMT Agent 远程化 | Windows；协议联调需要 Mac API | `apps/qmt-agent`、Agent Hub/API、Agent 会话安全与相关契约 | 可远程连接、可恢复、具备完整实盘和行情能力的执行端 |
| Mac full/live | Mac | Mac 启动器、跨平台进程管理、Mac Caddy/依赖/路径 | 可运行全部非 QMT 服务的 `full/live` 开发节点 |
| Windows 启动器清理 | Windows | `ops/quantx-agent.ps1`、Windows QMT 服务定义、旧 Dev 启动路径清理、联合备份 | 只拥有 QMT Agent 的权威 Windows 运维入口 |

为降低不同服务器合并时的冲突，文件所有权按以下原则处理：

- 方案一拥有 Agent 协议、Agent 会话和服务端远程 Agent 就绪判定。
- 方案二拥有 Mac 进程编排和平台适配，不自行修改 Agent 会话语义。
- 方案三拥有 Windows PowerShell 生命周期和备份，不自行修改 Agent 线协议。
- `AGENTS.md`、现有部署总文档和本文的最终状态只由集成负责人在三项合并后更新。
- 如果某项实施发现必须修改其他方案拥有的接口，应停止该部分实现，在集成分支先
  更新本文公共契约，不能单方面制造第二套接口。

建议三个执行节点在开始前记录同一个只读基线：

```text
BASE_COMMIT=<相同的完整 Git SHA>
PLAN_BRANCH=<本方案工作分支>
MAC_DEV_PUBLIC_URL=<不含凭据的稳定 HTTP 或 HTTPS 根地址>
TARGET_ACCOUNT_ID=<只记录在安全运行配置，不写入公开文档>
```

三项工作必须从同一个 `BASE_COMMIT` 开始。交接时提供提交 SHA 和验证证据，不以
未提交工作区作为交付物。

## 4. 冻结的公共运行契约

### 4.1 网络契约

Mac 只暴露 Caddy。内部服务继续绑定回环地址：

| 服务 | 目标监听 | 对 Windows 可见 |
| --- | --- | --- |
| Caddy | Mac 的稳定私网 HTTP 或 HTTPS 地址 | 是，唯一入口 |
| API | `127.0.0.1:18081` | 否 |
| Market Gateway | `127.0.0.1:18082` | 否 |
| Monitor | `127.0.0.1:18083` | 否，只经 Caddy `/monitor/*` |
| Vite | `127.0.0.1:5250` | 否，只经 Caddy |
| VitePress | `127.0.0.1:5251` | 否，只经 Caddy `/docs/*` |

Windows Agent 保存一个稳定的 `<MAC_DEV_PUBLIC_URL>`，所有通信均从该地址派生：

- 设备令牌：`/auth/agent/token`
- 控制 WebSocket：`/ws/agent`
- 全市场 WebSocket：`/ws/agent/market`
- 行情分块上传：`/agent/market-data/{request_id}/...`

控制协议维持 `1.1`，全市场子协议维持 `quantx.market.v2`。除非实施证明现有协议
无法表达必要语义，否则本轮不升级协议版本；服务端生成的连接身份保留在服务端
状态和 heartbeat details 中，不要求 Agent 回传可信的会话 ID。

`full/live` 最终验收接受设备登记时明确写入的 `http://` 或 `https://` 根地址。
Agent 必须严格保留登记 scheme 和 authority：HTTP 固定派生 HTTP/WS，HTTPS 固定
派生 HTTPS/WSS；禁止重定向、自动换址、HTTPS 失败后降级到 HTTP，修改地址或 scheme
必须重新受控登记。

HTTP 会让登记交换、设备认证、控制/行情和历史上传流量在网络中明文传输，只能用于
用户明确接受该风险的受控私有局域网；HTTPS 仍是推荐形态，使用时必须由 Windows
信任其证书链。Windows 防火墙不为 Agent 开放入站服务。Mac 防火墙只允许需要访问
Caddy 的可信来源，不公开内部端口。

### 4.2 模式与能力契约

Mac 的默认启动意图保持不变：

```text
profile=full
agentMode=live
configuredLive=true
```

`configuredLive=true` 只表示服务以完整实盘配置启动，不代表可以下单。有效实盘能力
由动态安全门计算：

| 条件 | 系统状态 | QMT 状态 | 有效实盘能力 |
| --- | --- | --- | --- |
| Mac 已启动，Agent 未连接 | `DEGRADED` | `BLOCKED / REMOTE_AGENT_OFFLINE` | `DISABLED` |
| Agent 已连接，完整快照或对账未完成 | `DEGRADED` | `BLOCKED / REMOTE_AGENT_NOT_RECONCILED` | `DISABLED` |
| 当前会话、账户、快照、对账、备份和授权全部通过 | `READY` | `READY` | `ENABLED` |
| Agent 断线、会话失效或心跳超过 90 秒 | `DEGRADED` | `BLOCKED` | `DISABLED` |
| 显式 `data-only` | 非实盘 | 不要求 live Agent | `DISABLED` |

服务端不得因为远程 Agent 离线而把期望模式改写为 `data-only`，也不得要求重启
API/Engine 才能在 Agent 恢复后开启有效能力。

### 4.3 当前会话可信度契约

跨主机后不能继续依赖服务端读取 Windows QMT PID 或
`QMT_AGENT_LAUNCH_STARTED_AT`。目标设计必须满足：

1. API 每次进程启动生成不可复用的 `api_instance_id`，并在对外 ready 前写入
   当前 API component heartbeat。
2. 每个通过认证的 Agent 控制连接由 API 生成 `agent_session_id` 和
   `server_connected_at`。
3. 服务端写入 Agent heartbeat 时附带当前 `api_instance_id`、
   `agent_session_id` 和服务端接收时间；这些值不能由 Agent 自报覆盖。
4. 有效 Agent 必须同时满足：设备未撤销、账户匹配、模式为 `live`、心跳不超过
   90 秒、heartbeat 的 `api_instance_id` 与当前 API heartbeat 一致，并且当前
   Agent Hub 中存在对应活动连接。
5. API 启动、Agent 断开或设备撤销时，应立即使当前会话失效；90 秒 TTL 只是
   最后的保守兜底。
6. `AgentEnvelope.sent_at` 继续用于检测发送延迟和时钟偏差，但实盘就绪不能只信任
   远端时间；关键新鲜度同时使用服务端接收时间。

优先把这些身份放入现有 heartbeat details，避免为本轮增加数据库字段和迁移。

### 4.4 完整能力清单

以下能力必须保持完整，不能以 Mock 或纸面检查替代：

- XTData 连接、代码表刷新、全推订阅和关键指数覆盖率检查。
- `1m/5m/1d` 等单标的行情请求和大块历史行情分块上传。
- 除权因子请求与上传。
- 市场流 `SNAPSHOT -> DELTA -> readiness-confirm -> READY` 收敛。
- 账户、资产、持仓、委托、成交的完整快照。
- 下单、撤单、命令幂等、命令投递确认。
- 委托、成交和持仓回报先 journal、再 inbox、后 Engine 收敛。
- Agent/API/Market Gateway/Engine 任意单进程重启后的恢复。
- 网络分区后的 journal 重放和无重复订单保证。
- 独立账户增仓授权、快照绑定、对账、备份和策略授权。
- Web、GraphQL、状态页能够观察真实健康、延迟和阻断原因。

### 4.5 配置所有权

Mac 持有服务端配置：

```text
PUBLIC_URL=<MAC_DEV_PUBLIC_URL>
DATABASE_URL=<external PostgreSQL>
REDIS_URL=<external Redis>
INFLUXDB_*=<external InfluxDB>
PREFECT_API_URL=<external Prefect Server>
ENABLE_REAL_TRADING=true
QMT_REAL_TRADING_ENABLED=true
REAL_TRADING_ACCOUNT_ALLOWLIST=<唯一账户>
T_TRADE_LIVE_ENABLED=<按功能显式配置>
```

Windows 持有 Agent 配置：

```text
api_url=<MAC_DEV_PUBLIC_URL，保存在设备登记配置>
ENV=testing
ENABLE_REAL_TRADING=true
QMT_REAL_TRADING_ENABLED=true
QMT_ACCOUNT_WHITELIST=<唯一账户>
QUANTX_AGENT_STATE_DIR=<Windows 本地安全目录>
```

设备密钥、券商账号、密码和 QMT 安装路径只能留在 Windows 安全配置中，不能写入
Mac、数据库、Git、日志或迁移证据。Mac 不需要 QMT 安装路径。Windows QMT Agent
不需要导入服务端数据库模块。

### 4.6 备份契约

本轮继续由 Windows 执行节点协调 Dev `full/live` 联合备份，因为它本地拥有 QMT
journal，同时可以使用受控的 PostgreSQL 备份连接：

1. 生成 PostgreSQL 备份并完成归档校验。
2. 通过 QMT Agent 自带命令生成一致的 journal 备份。
3. 为两部分计算校验和并写入同一个 manifest。
4. 只有两部分均成功后，才调用服务端备份登记逻辑更新 `last_backup_at`。
5. 任一部分失败都不得刷新 24 小时实盘备份门。

备份协调属于 Windows 启动器方案，不改变 QMT Agent 的依赖边界。Agent 代码仍然
不能直接访问 PostgreSQL。

## 5. 实施与集成顺序

三个方案可以并行开发，但合并和切换必须按以下顺序：

### 阶段 A：冻结基线

1. 三个执行节点同步到相同 `BASE_COMMIT`。
2. 记录当前数据库版本、QMT Agent 协议和账户安全状态。
3. 完成 PostgreSQL 与 QMT journal 的可恢复备份。
4. 确认当前无待处理命令、未知订单或未收敛回报。

### 阶段 B：合并方案一

先合并 QMT Agent 远程会话与服务端动态安全门。此时旧 Windows Dev 启动路径暂时
仍可作为整体回滚入口，但不能作为新架构验收结果。

### 阶段 C：合并方案二与方案三的抽取提交

合并 Mac 编排器和跨平台适配。在 Windows Agent 未连接时，先以默认
`full/live` 启动 Mac，验收其明确进入 `DEGRADED / BLOCKED`，而不是
`data-only` 或假 `READY`。

方案三必须拆成两个可独立审核的提交。本阶段只合并第一个提交：新增
`ops/quantx-agent.ps1`，抽取 QMT 生命周期和联合备份，但暂不删除或禁用旧
Windows Dev 全栈入口。这样阶段 D 可以使用目标 Windows 命令完成真实联调，同时
旧入口仍是受控的整体回滚点。

### 阶段 D：集成完整链路

1. Windows Agent 登记并连接 Mac Caddy。
2. 等待控制连接、市场连接、完整账户快照和市场 readiness-confirm。
3. 完成账户事实对账和 Engine inbox 收敛。
4. 执行并登记新的联合备份。
5. 在 `SHADOW` 中观察真实行情、指令预览和外部活动基线。
6. 经用户明确授权后进行受控 CANARY 实盘委托、撤单和成交验收。
7. 确认最终 `LIVE` 状态以及断网、重连、单进程重启恢复能力。

任何真实交易都必须满足项目现有的测试环境、账户白名单、实盘开关和人工授权硬门；
文档本身不构成交易授权。

### 阶段 E：合并并执行方案三的清理提交

只有阶段 D 全部通过后，才关闭并删除 Windows Dev 全栈启动路径，使
`ops/quantx-agent.ps1` 成为 Windows 开发节点的唯一入口。生产相关命令本轮冻结，
留待独立生产部署方案处理。

### 阶段 F：更新权威文档

集成负责人原子更新：

- 根目录 `AGENTS.md` 的统一运行方式。
- `docs/engineering/deployment/README.md`。
- `docs/engineering/qmt-agent/README.md`。
- `docs/architecture/系统架构设计.md`。
- 必要的 Engine、Monitor 和测试说明。

## 6. 总体验收门

### 6.1 自动验证

至少执行：

```text
python -m pytest tests/
npm run check
npm run lint
npm run test:run
npm run build
```

如果 GraphQL schema、查询或生成类型发生变化，还必须从 Mac Caddy 公共 GraphQL
端点执行项目规定的 `npm run codegen` 完整链路。另需执行 Mac Caddy 校验、Mac
启动器契约测试、Windows Agent 启动器契约测试、Agent/API 集成测试和全市场非交易
压测。

### 6.2 跨主机故障验证

必须留下以下场景的证据：

1. Mac `full/live` 启动但 Agent 离线：实盘关闭、非 QMT 服务可用。
2. Agent 首次接入：快照和对账前实盘关闭，完成后动态开启。
3. API 重启：上一 API 实例的 90 秒新鲜心跳不能恢复实盘。
4. Market Gateway 重启：控制面不伪装市场 READY，流按协议重建。
5. Engine 重启：数据库租约维持单实例，inbox/outbox 不重复收敛。
6. Agent 重启：旧进程被本地启动器识别，完整快照重新建立。
7. 网络中断：新订单入口关闭；恢复后 journal 重放且没有重复委托。
8. 时钟偏差超过允许范围：系统 fail-closed，并显示可诊断原因。
9. 备份任一部分失败：`last_backup_at` 不更新，实盘备份门继续失败。
10. Mac `down` 不关闭 Windows；Windows `down` 不关闭 Mac。

### 6.3 最终状态验收

完成时 `status` 必须明确展示：

```text
Runtime profile=full
agentMode=live
唯一账户=<目标账户>
liveTrading=ENABLED
QMT Agent=READY
protocol=1.1
account snapshot age < 90s
market stream=READY
reconciliation=READY
backup age < 24h
Engine singleton lease=HELD
```

还必须完成至少一次经明确授权的真实委托生命周期证据：命令创建、投递、QMT 委托
回报、撤单或成交回报、inbox 持久化、Engine 收敛和客户端可见。只验证下单接口
返回或 `command_ack` 不算通过。

## 7. 交付证据

每个执行方案应交付：

- 基线 SHA、最终提交 SHA 和批准的文件列表。
- 执行过的测试命令、退出码和时间。
- 不含密钥、账户敏感信息的 `status` 摘要。
- 已知限制和未执行的真实环境步骤。
- 回滚点和回滚前置条件。

运行日志、trace、截图和压测报告放入 `.runtime/reports/mac-dev-migration/` 或项目
规定的 `.codex_screenshots/`，默认不提交。提交到 `docs/reports/` 的证据必须先做
脱敏审查。

## 8. 回滚原则

- 清理方案执行前，可以停用 Mac 和远程 Agent，整体恢复到同一版本的旧 Windows
  Dev 全栈。
- 清理方案执行后，不通过兼容分支回退；应回滚整个已知良好提交并恢复其匹配的
  配置和启动器。
- 本轮优先使用现有 heartbeat details，避免数据库 schema 迁移，从而保留简单的
  代码级整体回滚。
- 回滚前先关闭有效实盘能力、停止新命令生产并等待或人工处置未决命令。
- 只有确认数据库备份和 QMT journal 属于同一 manifest 后，才允许执行数据恢复。
- 禁止同时启动旧 Windows 全栈和新 Mac Engine；PostgreSQL 租约是最后防线，不是
  并行运行授权。

## 9. 完成定义

迁移只有在以下条件全部成立时才完成：

- 三份实施方案各自完成并提交。
- Mac 是 Dev 非 QMT 服务的唯一运行节点。
- Windows 是 Dev QMT Agent 的唯一运行节点。
- 普通 Dev 启动默认为完整 `full/live`。
- Agent 恢复后实盘能力可以动态恢复，不需要重启整套服务。
- 完整行情、历史数据、策略、下单、撤单、回报、对账、备份、监控和恢复链路通过。
- 旧 Windows Dev 全栈启动路径已经清理。
- 权威文档已经原子切换到新命令，不存在两套默认运行协议。
