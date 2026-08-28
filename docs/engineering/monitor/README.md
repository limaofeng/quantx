# QuantX Monitor 工程指南

`apps/monitor` 是与主 QuantX 运行时分离的观测服务。它负责固定服务目标的周期
检测、延迟采样、状态去抖、事故历史和状态页只读 API；不导入 API、Engine、Worker
或交易域代码，也不向任何交易门禁写状态。

## 生命周期与入口

开发环境从仓库根目录单独管理：

```powershell
.\ops\quantx.ps1 up -Environment dev -Component monitor
.\ops\quantx.ps1 status -Environment dev -Component monitor
.\ops\quantx.ps1 logs -Environment dev -Component monitor
.\ops\quantx.ps1 down -Environment dev -Component monitor
```

普通 QuantX `up/down` 不启停 Monitor。开发进程状态写入
`.runtime/monitor/dev-process.json`，历史库默认位于
`.runtime/monitor/quantx-monitor.sqlite3`。生产发布安装 `QuantXMonitor` WinSW
自动服务，使用相同的持久化运行目录。内部端口为 `127.0.0.1:18083`；唯一公共
入口仍是 Caddy `8080` 下的 `/monitor/*`。

## 检测模型

默认每 30 秒运行一次检测，最多并发 8 个。目标分两组：

- 外部依赖：PostgreSQL、Redis、InfluxDB、Prefect Server；
- QuantX 组件：Web 入口、文档、API 公共入口、API 进程、Market Gateway、
  Engine、Worker、QMT Agent、行情链路和可选 AI Runtime。

前一类使用独立协议或 HTTP probe；后一类的进程级入口直接探测，Engine、Worker、
行情和 AI Runtime 从一次脱敏的 `/health/components` 快照派生，避免同一轮重复触发
主服务的完整健康计算。QMT Agent 是组合目标：Monitor 直接请求 Windows
`/health/ready` 取得本地状态和真实 HTTP RTT，再与同轮 API 会话、心跳、完整快照和
对账语义取较差结果；每轮只持久化一条 QMT Agent 样本。纯派生目标不伪造独立延迟。

固定目标来源使用 `probeKind=direct | derived | composite`。QMT Agent 为
`composite`；Engine、Worker、行情服务和 AI Runtime 为 `derived`；其余为
`direct`。Windows 连接或协议失败时 QMT Agent 立即为 unavailable 且延迟为空；合法
HTTP 200/503 都生成 RTT 样本。原因优先级固定为健康端点传输/协议错误、API 服务端
语义原因、Windows 本地 readiness 原因。

状态词汇固定为 `healthy / degraded / unavailable / unknown / disabled`。连续两次
`unavailable` 才打开事故；事故打开后连续两次 `healthy` 才关闭。第一次失败和
第一次恢复均保持 `degraded`，避免单个瞬时样本制造事故抖动。可选且未配置的目标
使用 `disabled`，不拉低组状态。

## 存储与保留

SQLite 使用 WAL、事务写入和版本化 schema：

- 原始样本保留 90 天；
- 已完成小时的汇总保留 365 天；
- `24h/7d/30d/90d` 从原始样本分桶，`1y` 从小时汇总分桶；
- 每次检测保存观测状态、去抖后的有效状态、耗时、HTTP 状态码和稳定原因码；
- 活动事故与关闭时间持久化，Monitor 重启后继续沿用连续计数和事故状态。

统一 `backup` 在历史库存在时通过 SQLite online backup API 写入
`monitor/quantx-monitor.sqlite3`，`restore-verify` 会校验清单、哈希和
`PRAGMA integrity_check`。历史库尚未产生时，备份明确告警但不阻塞数据库和 QMT
journal 的既有备份。

## 只读 API

公开 API 不启用 Swagger/OpenAPI 页面，并统一返回 `Cache-Control: no-store`：

| 路径 | 用途 |
| --- | --- |
| `/monitor/health/live` | Monitor 进程存活 |
| `/monitor/health/ready` | 调度器运行、SQLite 可写且最近持久化无错误 |
| `/monitor/api/v1/summary?window=24h` | 当前状态和 `24h/7d/30d` 汇总 |
| `/monitor/api/v1/targets/{id}/history?range=24h` | `24h/7d/30d/90d/1y` 分桶历史 |
| `/monitor/api/v1/incidents?range=30d&targetId={id}` | 固定目标的事故记录 |

响应只包含固定目标 ID/名称、状态、采样时间、延迟分位数、覆盖率和稳定原因码。
连接串、Redis/InfluxDB 凭据、内部探测 URL、异常文本和调用栈都不会出现在公共
响应中。前端状态页为 `/settings/status`；首页系统摘要也只消费 Monitor summary，
不再轮询 `/health/components`。

## 配置

常用环境变量：

- `MONITOR_HOST`、`MONITOR_PORT`、`MONITOR_DATABASE_PATH`；
- `MONITOR_CHECK_INTERVAL_SECONDS`、`MONITOR_MAX_CONCURRENCY`；
- `MONITOR_RAW_RETENTION_DAYS`、`MONITOR_ROLLUP_RETENTION_DAYS`；
- `MONITOR_PUBLIC_BASE_URL`、`MONITOR_API_URL`、
  `MONITOR_MARKET_GATEWAY_URL`；
- `MONITOR_QMT_AGENT_HEALTH_URL=http://<windows-host>:18084`，必须是无凭据、
  无路径、无查询和 fragment 的绝对 HTTP(S) 根地址，不接受重定向。

PostgreSQL、Redis、InfluxDB 与 Prefect 连接配置复用现有环境变量，但归
`quantx-monitor` 进程自行读取。任何新增目标都必须同时补充固定定义、脱敏 API
契约、状态聚合规则和测试，不接受用户输入的任意 URL 探测。
