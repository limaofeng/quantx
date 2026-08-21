# QuantX QMT Agent

`apps/qmt-agent` 是唯一允许导入 `xtquant` 的应用。它只建立出站 WebSocket，
不开放局域网监听端口，也不导入服务端 ORM、Repository 或策略实现。

设备密钥保存在 Windows Credential Manager，服务端只保存哈希。运行模式为
`data-only`、`paper` 或 `live`。`live` 只允许在 `ENV=testing` 或
`ENV=production`，且同时显式设置 `ENABLE_REAL_TRADING=true`、
`QMT_REAL_TRADING_ENABLED=true` 和 `QMT_ACCOUNT_WHITELIST` 时启动。
production 还必须设置 `T_TRADE_LIVE_ENABLED=true`；服务端账户白名单、
灰度阶段、快照、对账与策略授权仍会独立阻断不合规命令。普通开发 `up`
默认提升为 `full/live`；登记与运行时预检通过时才启动 QMT Agent。启动器保持
服务端为 `development`，只为 QMT Agent 子进程注入 `ENV=testing`、账户白名单
和实盘开关。预检失败时仍保持期望模式为 `live`，但会在 API/Engine 启动前关闭
全部实盘能力门、清空实盘账户允许列表并把 QMT 标记为 `BLOCKED`，让非 QMT
服务和基于已持久化历史行情的回测独立运行；这不是 `data-only`，也不代表 Agent
已经 `READY`。预检通过时，API/Engine 只消费不早于本次
`QMT_AGENT_LAUNCH_STARTED_AT` 的 Agent 心跳，启动验收还要求受管 QMT 进程的
PID 与进程启动时间持续匹配；上一轮残留的 90 秒新鲜心跳不能恢复实盘能力。
账户可显式传入，
也可由本机环境中的唯一账户配置自动解析：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web -AccountId <账户>
```

需要纯行情通道时显式传入 `-Mode data-only`。生产环境的模式切换仍要求精确
确认和独立配置的实盘开关。

交易控制、心跳与订单回报走协议 `1.1` 的 `/ws/agent`；沪深实时行情独占
`/ws/agent/market`，子协议固定为 `quantx.market.v2`。该端点由独立的 Market
Gateway 进程承载，控制面 API 重启不会中断行情提交。Agent 只建立一个
原生 `subscribe_whole_quote(a股代码列表 + 沪深指数代码列表)`。显式代码表来自
“沪深A股”和“沪深指数”的去重并集，约 5,800 个代码仍是一次 whole-quote
调用的一个参数；ETF、债券等其他 SH/SZ 合约不会进入 SDK 解码与下游链路。
回调入口继续按同一 active universe 做防御性过滤。单标的 `1m/5m/1d` 等 QMT
K 线仍由主连接控制 `subscribe_quote`，不得从 tick 合成。

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
Store 与 Engine Hub 共用 contracts 中的唯一解析器。个人单账户部署要求三者使用
同一台已校时主机的 UTC 时钟：来源时间或 `captured_at` 超前 API ingress 5 秒即
拒绝；Store 在实际 commit 时再按 10 秒 freshness 窗口检查 `captured_at`，因此
排队积压不能刷新一个过期的 `READY` lease。Engine 在交易时段同样按 10 秒
`captured_at` age fail-closed；非交易时段允许保留昨日快照，但未来超过 5 秒仍
无条件拒绝。

历史 `tick` 上传保留 XTData 的原始毫秒时间戳 `time`，并为同一
`code + time` 下的每条快照生成从 0 开始且连续的 `tick_ordinal`，
取值范围为 0–999。Agent 不删除同毫秒快照，也不修改原始毫秒时间。
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
  --api-url http://127.0.0.1:8080 `
  --code <一次性登记码>
python -m quantx_qmt_agent.main status
```

本地 SQLite journal 持久化命令幂等记录和待确认回报。相同消息 ID 与不同
载荷会被拒绝；已完成命令在重连后只重放原确认与未确认回报，不重复调用
broker。过期、账户不在白名单和协议版本不兼容的命令都会在本地拒绝。
命令的 `execution_mode` 必须与 Agent 模式完全一致；paper 命令不会进入
live Agent，live 命令也不会降级为模拟成交。

`live` Agent 只代表它能够读取真实账户并在门禁许可后执行命令，不代表账户
已经授予 QuantX 自动下单权。产品准备阶段保持账户在 `SHADOW`：用户可继续在
QMT 客户端手工交易，Agent 每分钟完整上报这些外部委托、成交与持仓，服务端
完成分类和事实收敛。切入 `CANARY / LIVE` 前必须选择没有手工/外部活动的受控
窗口；受控窗口内再次出现外部活动会自动暂停 QuantX 执行。

Agent 在完整快照中同时保留 QMT 的原始 `order_status` 和派生的
`effective_order_status`。A 股当日委托在收盘后仍被 QMT 报为已报/待成交且
成交量为零时，派生状态为 `EXPIRED`，原因记录为
`MARKET_SESSION_CLOSED`。原始状态及 `can_cancel` 查询结果不覆盖、不删除；
后者在收盘后可能仍是陈旧的 `true`，不能让当日委托跨日继续被视为活动。
盘中仍可撤或可能成交的委托继续按活动委托处理并阻断受控窗口。
