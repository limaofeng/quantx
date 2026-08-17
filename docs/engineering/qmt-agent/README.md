# QuantX QMT Agent

`apps/qmt-agent` 是唯一允许导入 `xtquant` 的应用。它只建立出站 WebSocket，
不开放局域网监听端口，也不导入服务端 ORM、Repository 或策略实现。

设备密钥保存在 Windows Credential Manager，服务端只保存哈希。运行模式为
`data-only`、`paper` 或 `live`。`live` 只允许在 `ENV=testing` 或
`ENV=production`，且同时显式设置 `ENABLE_REAL_TRADING=true`、
`QMT_REAL_TRADING_ENABLED=true` 和 `QMT_ACCOUNT_WHITELIST` 时启动。
production 还必须设置 `T_TRADE_LIVE_ENABLED=true`；服务端账户白名单、
灰度阶段、快照、对账与策略授权仍会独立阻断不合规命令。普通开发 `up`
默认提升为 `full/live` 并启动 QMT Agent；启动器保持服务端为 `development`，只为
QMT Agent 子进程注入 `ENV=testing`、账户白名单和实盘开关。账户可显式传入，
也可由本机环境中的唯一账户配置自动解析：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web -AccountId <账户>
```

需要纯行情通道时显式传入 `-Mode data-only`。生产环境的模式切换仍要求精确
确认和独立配置的实盘开关。

交易控制、心跳与订单回报走协议 `1.1` 的 `/ws/agent`；沪深实时行情独占
`/ws/agent/market`，子协议固定为 `quantx.market.v1`。Agent 只建立一个
`subscribe_whole_quote(["SH", "SZ"])`，先发送完整快照，再发送递增序号的
二进制增量批次。单标的 `1m/5m/1d` 等 QMT K 线仍由主连接控制
`subscribe_quote`，不得从 tick 合成。

whole-quote 回调只进入容量 8、估算上限 64 MiB 的捕获队列，序列化和网络 ACK
在专用任务完成；
队列溢出、10 秒 ACK 超时、连接故障或 RESYNC 都会关闭行情连接、取消本地
whole-quote 并用新 `stream_id` 和全量快照恢复，不静默丢弃旧事件。交易连接
不会被大行情帧阻塞。批量历史行情仍按请求 ID、批次序号、压缩和 SHA256
通过 HTTP 上传。交易连接断线重连后 Agent 先上报完整账户快照。

性能回归使用固定 5,000 标的、30 个批次运行
`python ops/benchmark-market-stream.py`，记录 orjson 编解码 p50/p95/p99、帧大小、
CPU 时间和峰值内存；实机验收再结合各阶段日志比较 WebSocket ACK、Redis 应用
和 Engine 水位延迟，不设置脱离设备负载的固定 SLA。

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
