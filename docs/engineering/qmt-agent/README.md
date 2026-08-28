# QuantX QMT Agent

`apps/qmt-agent` 是唯一允许导入 `xtquant` 的应用。交易控制、行情、历史上传与报告
只建立出站 HTTP(S)/WS(S)；同一 Agent 进程另提供固定只读健康监听，不导入服务端
ORM、Repository 或策略实现。

QMT Agent 是独立的 Windows 远程执行进程，不依赖 API 所在主机的启动器、PID
或进程启动时间。它只从登记的服务根地址派生 token、控制 WebSocket、行情
WebSocket 和历史上传地址。登记地址必须是无凭据、无路径、无查询参数的 `http://`
或 `https://` 根地址。Agent 严格保留登记 scheme 和 authority：HTTP 派生 HTTP/WS，
HTTPS 派生 HTTPS/WSS；任何重定向、系统代理、自动换址或跨 scheme 回退均被禁止，
HTTPS 证书失败不会降级为 HTTP。

HTTP 会让登记交换、设备认证、控制/行情和历史上传流量在网络中明文传输，只应在
用户明确接受风险的受控私有局域网使用；HTTPS 仍是推荐形态。

使用 Caddy 私有 CA 的 HTTPS 地址时，把 Mac Caddy 根证书路径显式配置为
`SSL_CERT_FILE`。Agent 会直接用该证书构造 HTTP 与 WSS 的 TLS context，同时继续
设置 `trust_env=false` 和 `proxy=None`，因此不会为了读取 CA 而启用系统代理，也不会
接受重定向或降级到 HTTP。

Windows QMT 运行时复用改造前既有的 `xtquant-demo` Conda 环境，不创建独立 QMT
Agent venv，也不使用服务端 Python 兜底。统一启动器优先采用显式配置的
`QUANTX_QMT_PYTHON_EXE`；未配置时固定解析 `xtquant-demo`，解析失败即阻断 Agent，
不得静默换用当前 shell 的 Python。

设备密钥保存在 Windows Credential Manager，服务端只保存哈希；QMT 配置、
SQLite journal 和历史上传 spool 也只留在 Windows。运行模式为 `data-only`、
`paper` 或 `live`。`live` 只允许在 `ENV=testing` 或 `ENV=production`，且同时
显式设置 `ENABLE_REAL_TRADING=true`、`QMT_REAL_TRADING_ENABLED=true` 和唯一
账户 `QMT_ACCOUNT_WHITELIST` 时启动。服务端账户白名单、账户增仓授权、完整快照、
对账和策略授权仍会阻断不合规命令；`T_TRADE_LIVE_ENABLED` 只控制做 T 助手，
不参与 QMT Agent 或账户实盘能力判定。

启动顺序固定为：加载凭据与安全配置、校验 live 环境、校验 journal 完整性、获取
短期 token、完成控制 WebSocket 认证，然后才初始化 XTData/XTTrading。每次新控制会话
都必须重放未确认报告并重新上报完整账户快照；Engine 对账完成前，Agent heartbeat
不能自行把状态提升为 `READY`，服务端也不会向该 live 会话发放全市场行情租约。
Windows 单实例锁和长期进程监督由 Windows 启动器方案负责，不能通过手工并行启动
两个 Agent 绕过。

短期 token 只用于新连接握手。运行中的 Agent 在后台更新未来重连凭据，续期失败按
退避重试但不拆除已经认证的控制/行情连接；设备撤销、会话替换和传输心跳仍由服务端
独立校验。普通每分钟完整账户快照只用于收敛外部账户事实，不进入 `RECONCILING`；
只有真实控制重连、XTTrading 连接代际变化或显式恢复请求才重新打开对账门。

## 独立健康监听

健康运行时代码默认监听 `0.0.0.0:18084`，并校验 `QMT_AGENT_HEALTH_HOST` 和
`QMT_AGENT_HEALTH_PORT`。权威 Windows 入口 `quantx-agent.ps1` 会把托管 Agent
固定到该地址与端口，并维护完全匹配的防火墙规则；正式托管运行不得另行覆盖。
监听器只提供：

- `GET /health/live`：只证明主事件循环能响应，固定返回脱敏的 v1 存活契约；
- `GET /health/ready`：返回本地控制连接、对账、XTData、按模式解释的 XTTrading、
  行情流状态、版本与运行时间；本地 ready 为 HTTP 200，degraded/unavailable 为
  HTTP 503。

响应不包含账户、设备 ID、远端地址、QMT 路径、凭据、日志、异常文本或调用栈。
handler 只读取一次不可变的 `AgentHealthState`，不会调用 broker、XTData、
XTTrading、数据库或网络。`data-only` 和 `paper` 的 XTTrading 状态固定为
`disabled`，不会因此失败；`live` 必须实际连接 XTTrading。emergency stop、账户增仓
授权和 kill switch 仍由既有安全入口裁决，不单独改变本地健康状态。

健康 server、Agent runtime 与进程 watchdog heartbeat 位于同一结构化并发边界。
端口绑定失败或 server 意外退出会结束 Agent 主进程并由 supervisor 重启；正常关闭
会同步停止 Uvicorn，不留下孤立监听任务。`quantx-agent.ps1 up` 只在 `Private`
网络配置文件为 TCP `18084` 幂等维护入站规则，远端地址为 `Any`，不限制单一
Monitor IP；创建或修复规则需要管理员 PowerShell，规则正确时普通重启无需提升权限。

独立 Monitor 会把 `/health/ready` 的真实 HTTP RTT 与 API 对 WebSocket 会话、心跳、
完整账户快照和对账的权威语义取较差结果。这个观测结果永远不回写交易门禁。

新增或增仓命令还要求 `marketStreamStatus=READY`。API 在入队与实际写入控制
WebSocket 前都会检查 Agent 声明、API 已提交 watermark 与 Engine 已应用 watermark
完全一致，并在发送前重新计算账户级增仓安全状态；Windows Agent 在调用 Broker 前
再次检查。撤单与明确的风险降低型卖出保留故障逃生路径。

API 为每个进程和控制连接分别生成 `apiInstanceId` 与 `agentSessionId`，并用服务端
接收时间计算 90 秒 TTL。Agent 断线、撤销、同设备重复连接或 API 实例切换会立即
使旧会话失去命令和行情资格；旧报告仍按原消息 ID 幂等收敛，但不能提升新会话。
账户安全门不再读取本机 `QMT_AGENT_LAUNCH_*` 或 PID 状态。
行情租约绑定当前控制连接的 `apiInstanceId + agentSessionId + deviceId`。行情连接
自身仍必须用有效短期 token 完成握手，但 token 续期不会改变已认证控制会话身份。
Redis 只允许较新的 API 启动代际覆盖租约，旧 API 只能清理自己的租约，不能删除或
回写新代际。服务端不持久化或输出 token 本身。

服务端控制的 Agent heartbeat details 字段为：`apiInstanceId`、
`agentSessionId`、`serverConnectedAt`、`serverReceivedAt`、`agentSentAt`、
`remoteAddressSummary`、`sessionActive` 和 `reasonCode`。Agent 自报同名值不会覆盖
这些字段。API 持久化报告时还会注入只在服务端使用的会话元数据和认证时冻结的
`authorizedAccountIds`；该元数据不改变线协议，也不参与 Agent 原始 payload hash。

交易控制、心跳与订单回报走协议 `1.1` 的 `/ws/agent`；沪深实时行情独占
`/ws/agent/market`，子协议固定为 `quantx.market.v2`。该端点由独立的 Market
Data Service 进程承载，控制面 API 重启不会中断行情提交。Agent 只建立一个
原生 `subscribe_whole_quote(a股代码列表 + 沪深指数代码列表)`。显式代码表来自
“沪深A股”和“沪深指数”的去重并集，约 5,800 个代码仍是一次 whole-quote
调用的一个参数；ETF、债券等其他 SH/SZ 合约不会进入 SDK 解码与下游链路。
回调入口继续按同一 active universe 做防御性过滤。单标的 `1m/5m/1d` 等 QMT
K 线仍由主连接控制 `subscribe_quote`，不得从 tick 合成。

主控制连接的 API receiver 不执行命令或行情数据库轮询；收到的帧先进入按字节和
条数限制的队列，再按连接顺序持久化。API 使用唯一 writer 发送 ACK、交易命令和
行情控制，并为 `report_ack` 保留高优先级容量。只有 `agent_report_inbox` 提交成功
才会确认报告。PostgreSQL 暂时不可用时，API 保留当前报告原地重试、暂停命令轮询，
并让有界接收队列对 socket 形成自然背压，不主动关闭控制连接；未确认报告继续保留在
本地 SQLite journal。只有协议/鉴权失败、真实传输失活或发送失败才重连并用原消息 ID
重放。

原生 whole-quote 采集与行情 WebSocket sink 生命周期分离：API 断线、ACK 超时、
RESYNC 或下游 Redis 故障只会令 sink 进入 `SYNCING/STALE`，采集器继续维护每个
标的的最新状态，不取消并重建 XTData 订阅。新 stream 从一致性 watermark 生成
sequence 1 `SNAPSHOT`；其 ACK 后仍保持 latest-state convergence，只把快照水位后
的收敛更新生成为 sequence 2 `DELTA` pre-cut 连续性屏障（没有变化时可为空），
API 提交后仍保持 `SYNCING`。
Agent 收到 sequence 2 ACK 后原子启用有序捕获，并强制把 ACK 窗口内的收敛更新
作为 sequence 3 `DELTA` readiness-confirm 发送（没有变化时也必须发送空批次）。
sequence 3 通过普通有界发送管线并被 API 原子提交后服务端进入 `READY`；API 返回
该批次 ACK 后 Agent 才进入 `READY`。之后的真实有序回调从 sequence 4 开始。每日
代码表刷新若发现代码集合
变化，会提升 source generation 并使 stream 失效；唯一 supervisor 严格先退订
旧 source，再激活 pending universe、建立一次新订阅并从全量快照恢复。取消失败
时 fail-stop，禁止重叠两路，也不回退到 `["SH", "SZ"]`。相同代码集合只更新
metadata，不重订。
初始 `SNAPSHOT` 只允许来自同一条 whole-quote 回调状态，协议同时携带完整
`universe_codes` 和当前已物化 tick；覆盖率至少 99% 且上证、深证、创业板关键
指数齐全才开始同步。覆盖不足时失败重连，禁止调用 `get_full_tick` 回补，因为
点查询与全推回调混合会放大 XTData GIL 阻塞并破坏一致水位。后续 DELTA 可补齐
快照时尚未物化、但已在 universe 中的代码。独立 Python 子进程每 5 秒检查 Agent
心跳；即使原生 SDK 持有 GIL 令进程内超时无法运行，连续 90 秒无心跳也会强制
终止父进程。不可恢复的 XTData
超时或原生取消失败使用专用退出码 fail-stop，确保残留 SDK 线程不能留下“PID
在线、心跳停止”的僵尸 Agent，并交由带 1/2/5/10/30 秒退避、Windows Job Object
子进程回收和状态文件的统一监督器重启。

QMT 回调只做快速捕获；READY 捕获入口以 64 MiB 保守估算字节预算为主约束，
结构上限由每批至少 1 KiB 的计费下限推导为 65,536 个回调，因此不会在字节预算
尚充足时因固定 8 回调阈值误触发重同步。编码后发送队列最多 8 批、64 MiB，且
最多 2 个批次处于未 ACK 状态。
序列化和网络收发由专用任务处理。
状态同步阶段允许按标的合并为最新值，`READY` 阶段同标的更新必须有序且不得静默
覆盖。任何容量/字节上限、ACK 超时或序号异常都会显式使 stream 失效并从全量
快照收敛，但不得拖垮交易连接、心跳或成交回报。批量历史行情仍按请求 ID、批次
序号、压缩和 SHA256 通过 HTTP 上传；交易连接重连后先上报完整账户快照。
每条 whole-quote tick 在线路编码前必须带有可比较的合法来源时间 `time` 或
`timetag`；缺失、非有限或非法值会精确使当前行情 stream 失效并重新同步，不得
回退到本机墙钟时间，也不得把单个 stream 的数据错误升级为整个 Agent 进程故障。
`time` 只接受 epoch 秒或毫秒，`timetag` 按上海时区解析并保留亚秒；Agent、API
Store 与 Engine Hub 共用 contracts 中的唯一解析器。跨主机部署要求 Windows
Agent 与 API 主机都使用可靠的 UTC 时间同步：来源时间或 `captured_at` 超前 API
ingress 5 秒即拒绝；Store 在实际 commit 时再按 10 秒 freshness 窗口检查
`captured_at`，因此排队积压不能刷新一个过期的 `READY` lease。Engine 在交易
时段同样按 10 秒 `captured_at` age fail-closed；非交易时段允许保留昨日快照，
但未来超过 5 秒仍无条件拒绝。

历史 `tick` 上传保留 XTData 的原始毫秒时间戳 `time`，并为同一
`code + time` 下的每条快照生成从 0 开始且连续的 `tick_ordinal`，
取值范围为 0–999。Agent 不删除同毫秒快照，也不修改原始毫秒时间。
在生成序号、摘要和上传分块之前，Agent 必须按
`quantx_contracts.historical_bar_transfer_fields(period)` 投影 XTData 行；
只转发版本化的公共历史 bar/tick 字段，保留已支持的可选字段，并丢弃供应商
新增字段（例如 `pe`）。服务端对同一清单继续严格校验，未知字段、缺失必填字段
或试图上传仅存储字段 `source_time_ms` 都 fail-closed；`source_time_ms` 仅由
Worker 从原始 `time` 写入持久层。
`tick` 的唯一键为
`(code, period, time, tick_ordinal)`；非 `tick` 周期不携带该序号，
仍要求 `(code, period, time)` 唯一。`tick_ordinal` 是根据稳定快照字段生成的
确定性代理顺序，用于重拉、分片和存储的一致性；它不声称代表交易所未提供的
同毫秒内部先后顺序。
每个历史行情请求按 `period`、规范化代码、时间和同毫秒序号生成确定性记录流，
并在每个请求 `code × period` 数据之后强制追加一条 `bar_summary`。XTData 未返回
某个请求代码时也必须发送行数为 0、原因 `XT_DATA_NO_ROWS` 的摘要，不能静默略过；
非空摘要携带行数、时间范围以及规范键 SHA-256。上传瞬时网络错误、408/429 和
5xx 不调用服务端 `/fail`，而是保留同一份 immutable gzip spool，断开控制连接后
重投；只有确定性的请求、编码或非瞬时 4xx 契约错误才进入 `FAILED`。

协议编解码回归使用 5,822 标的、30 个批次运行：

```powershell
uv run python ops/market-stream-load-test.py codec
```

非交易时段的完整数据面压力测试使用独立回环网关和随机 Redis keyspace，复用
生产 WebSocket 解码、双帧背压、分块提交与 ACK 管线，但不连接 XTData、交易
Agent 或订单接口：

```powershell
uv run python ops/market-stream-load-test.py run `
  --profile standard --duration 30m --allow-shared-redis
```

工具从 `/health/components` fail-closed 确认当前不是交易时段，测试网关由
`supervise_process.py` 管理，并只清理本次 `quantx-loadtest:<run-id>:*` 数据。
JSON 报告保存在 `.runtime/reports/market-stream-load-test/`，不提交仓库。

首次运行必须由 Web 创建一次性登记码，再执行：

```powershell
python -m quantx_qmt_agent.main enroll `
  --api-url <MAC_DEV_PUBLIC_URL> `
  --code <一次性登记码>
python -m quantx_qmt_agent.main status
```

`status` 只显示规范化地址和脱敏后的设备 ID，例如
`device_id=1234…abcd`。API `/health/components` 只输出聚合后的远程地址摘要、协议、
模式、快照年龄、脱敏账户摘要和阻断原因，不返回每台设备的原始 heartbeat details。
稳定阻断原因只有：

- `REMOTE_AGENT_OFFLINE`
- `REMOTE_AGENT_SESSION_STALE`
- `REMOTE_AGENT_NOT_RECONCILED`
- `REMOTE_AGENT_ACCOUNT_MISMATCH`

完成登记后，以独立 Windows 进程启动 live Agent：

```powershell
$env:ENV = "testing"
$env:ENABLE_REAL_TRADING = "true"
$env:QMT_REAL_TRADING_ENABLED = "true"
$env:QMT_ACCOUNT_WHITELIST = "<唯一账户>"
python -m quantx_qmt_agent.main run --mode live
```

这些开关只允许 Agent 建立真实 QMT 能力，不等于授权任何订单。未完成跨主机完整
快照、对账、行情 readiness-confirm 和账户级授权时，服务端仍保持 fail-closed。

更换远程 Windows Agent 时使用同一页面发起“安全交接”：旧 Agent 在新 Agent 建立
WebSocket、上传完整账户快照并由 Engine 确认 `READY` 前继续保持有效；完成后
服务端原子撤销旧凭据。取消交接只撤销候选 Agent 和未使用登记码，不影响当前
Agent。页面可查看 XTData、XTTrading、行情序列/队列/ACK/重同步和 journal
摘要，但不会接收 QMT 路径、端口或原始异常详情。

本地 SQLite journal 持久化命令幂等记录和待确认回报。相同消息 ID 与不同
载荷会被拒绝；已完成命令在重连后只重放原确认与未确认回报，不重复调用
broker。过期、账户不在白名单和协议版本不兼容的命令都会在本地拒绝。
命令的 `execution_mode` 必须与 Agent 模式完全一致；paper 命令不会进入
live Agent，live 命令也不会降级为模拟成交。

`live` Agent 只代表它能够读取真实账户并在门禁许可后执行命令，不代表账户
已经授予 QuantX 自动下单权。产品准备阶段保持账户在 `SHADOW`：用户可继续在
QMT 客户端手工交易，Agent 每分钟完整上报这些外部委托、成交与持仓，服务端
完成分类和事实收敛。切入 `CANARY / LIVE` 前必须基于没有手工/外部活动的完整
快照建立账户实盘窗口；窗口内再次出现外部活动会自动暂停 QuantX 执行。

上述每分钟完整快照是观察刷新，不是恢复握手；快照提交和 Engine 收敛期间不得把已
就绪的 Agent 周期性降为 `RECONCILING`。

Agent 在完整快照中同时保留 QMT 的原始 `order_status` 和派生的
`effective_order_status`。A 股当日委托在收盘后仍被 QMT 报为已报/待成交且
成交量为零时，派生状态为 `EXPIRED`，原因记录为
`MARKET_SESSION_CLOSED`。原始状态及 `can_cancel` 查询结果不覆盖、不删除；
后者在收盘后可能仍是陈旧的 `true`，不能让当日委托跨日继续被视为活动。
盘中仍可撤或可能成交的委托继续按活动委托处理并阻断账户实盘窗口。
