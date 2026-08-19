# QuantX Engine

`apps/engine` 独占策略管理器、自动退出计划、条件清仓、全局做 T、热缓存和
Agent 回报收敛。它使用 PostgreSQL advisory lock 保证同数据库只运行一个
实例，并定期写入组件心跳。

Engine 的 `WholeQuoteHub` 是沪深 tick 的唯一进程内入口。它先订阅 Redis 二进制
批次频道，再加载最新全量快照与水位，按 `stream_id + sequence` 检查重复、
乱序和缺口，并按标的源时间阻止旧 tick 回退；启动期间收到的批次在快照补水
完成后按水位衔接，消除“先读快照、后订阅”的窗口。全市场雷达、退出监控、策略、
暖缓存和单标的展示均在 Hub 内本地过滤；新增标的不再向 Agent 创建或重建
whole-quote 订阅。每 3 秒把最新候选榜和通用盘中量能快照批量写入 Redis
派生读模型。API 只读取该投影，不因页面访问创建行情订阅。首次触板、封板、
炸板和回封等阶段变化追加到 PostgreSQL `limit_up_radar_events`，用于 Engine
重启后恢复当日轨迹；Redis 仍不是事件真源。

Engine 从 `engine_command_outbox` 和 `agent_report_inbox` 恢复消费：
前者承载 API 发起的策略、做 T 和清仓控制命令，后者承载 Agent 上报的原始
订单、成交、持仓与对账结果。进程重启后会恢复超时的 `PROCESSING` 消息，
并继续从数据库推进。

完整账户快照的对账按灰度阶段处理。`SHADOW` 是手工交易共存的准备阶段：QMT
客户端产生且没有 QuantX 关联 ID 的委托/成交会作为外部活动持久化并计数，
不会阻止账户事实收敛；`CANARY / LIVE` 中出现同类活动则暂停自动执行。成功的
新协议 1.1 完整快照会把同设备、同账户范围内较旧的完整快照死信标记为
`SUPERSEDED`，并闭环对应告警，但保留原始失败审计记录。

对账和受控窗口以 Agent 派生的 `effective_order_status` 判断委托是否仍可成交，
同时保留 QMT 原始状态用于审计。未成交的 A 股日内委托在收盘后按
`EXPIRED / MARKET_SESSION_CLOSED` 收敛，不再被计入活动外部委托；
历史委托本身仍保留在外部活动基线中。

账户灰度 mutation 与 Engine 的快照对账会串行锁定同一条
`account_trading_rollouts` 记录。建立受控窗口和启用自动交易时，服务会在锁内
重新读取并校验灰度阶段、kill switch、对账状态、完整快照及外部活动计数，
避免读取 readiness 后到实际提交前发生状态竞争。新增外部活动、手工暂停或
无法解释的对账异常会立即使窗口失效；断线重连后也必须重新满足新鲜快照门禁。

Redis 只用于唤醒消费者，以及向 API 发布行情、策略与交易事件的订阅通知，
不能作为订单、成交、Portfolio 或 bucket 的状态真源。API 收到交易事件
唤醒后仍会从数据库重新读取投影。订单必须先持久化 pending 状态和
`trade_command_outbox`，才能由 API Hub 下发给 Agent。

自动卖出由 Engine 的 `ExitPlanBook` 统一承载。入场策略在 BUY 意图中附带
`ExitPlanTemplate`，只有真实 BUY 成交回报会激活计划。Engine 在策略
`step()` 之前评估退出规则，将命中的计划转换成标准 SELL `TradeIntent`，
继续经过 OrderSizer、后置风控、Broker 和成交回报收敛。做 T 仅负责入场
信号和退出模板，不再维护独立的自动卖出主路径。完整契约见
[A 股自动退出计划与卖出策略契约](../../trading/contracts/A股自动退出计划与卖出策略契约.md)。

手工持仓的部分动态止盈也由 Engine 承载。计划创建时固定保护股数，条件清仓
监控每秒从 `WholeQuoteHub` 中央快照读取价格、累计成交量和五档盘口，执行
`ADAPTIVE_VOLUME_PRICE_TRAILING`。量能陈旧会降级到价格模式，价格陈旧则
暂停；触发后持久化 pending 委托，逐笔成交通过 `agent_report_inbox` 幂等
回填，部分成交只继续管理未成交的保护数量。实盘计划要求显式自动卖出授权。

Engine 使用 PostgreSQL advisory lock 保证同一数据库只有一个实例取得执行
权，并持续写入 `runtime_component_heartbeats`，供 API 就绪检查使用。

持久化 `ExitPlanMonitor` 与策略运行解耦，每秒扫描 `auto_exit_plans` 的活动
计划并消费 `WholeQuoteHub` 全市场批次。API 对计划的创建、修改、启停、取消、立即
评估和批量清仓全部写入 `engine_command_outbox`；Engine 在账户＋股票锁内
校验 `config_version`、保护量冲突和待成交 SELL。策略非回测计划在运行时幂等
同步到同一张表，策略停止不再终止已有退出保护。

行情状态为 `STARTING → SYNCING → READY → STALE/OFFLINE`。只有 `READY`
继续分发关键实时动作；交易时段 10 秒无新批次进入 `STALE`，午休、收盘和
非交易日不误判。关键消费者使用容量 8 的有序队列，溢出后显式进入
`LAGGING` 并停止相关回调；UI 使用容量 1 的 latest-only 队列并记录合并数。
Pub/Sub 缺批时 Hub 从 Redis 最新全量快照收敛，不重放可能过时的中间 tick。
缺口出现到补水完成期间停止增量分发，中央行情和关键消费者都不能恢复为
`READY`。sequence 1 快照在 API 仍为 `SYNCING` 时只更新中央状态，不提前分发；
sequence 2 连续性屏障使 API、Engine 与 Redis freshness lease 的 stream/sequence
完全一致后，Hub 才首次向消费者分发完整中央快照。任一水位或租约不一致时，
策略、条件清仓和自动退出等实时交易动作保持关闭。
