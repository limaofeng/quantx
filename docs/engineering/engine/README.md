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

Engine-owned 动态策略的标的范围由 `InstrumentUniverseProviderRegistry` 统一解析，
并提供 `STATIC` 固定标的规范化实现。策略类以 `INSTRUMENT_UNIVERSE_MODE` 声明
`ACCOUNT_HOLDINGS` 或 `RADAR_CANDIDATES` 后，协调器只提供账户持仓与未完成工作、
雷达候选与偏好等时点事实，对应 Provider 生成规范化
`InstrumentUniverseSnapshot`。动态快照的增删仍只通过运行时
`reconcile_run_instruments` 串行进入
`StrategyBase.step(RECONCILE)`；Provider 不订阅行情，策略也不读取账户或选池数据源。

Engine 从 `engine_command_outbox` 和 `agent_report_inbox` 恢复消费：
前者承载 API 发起的策略、做 T 和清仓控制命令，后者承载 Agent 上报的原始
订单、成交、持仓与对账结果。进程重启后会恢复超时的 `PROCESSING` 消息，
并继续从数据库推进。

订单/成交回报由 `business_key` 唯一的 `strategy_runtime_events`
串行进入策略。TradeIntent 和做 T 批次投影与该事件的首次落库在
同一事务内完成；Engine 回调后再把 event marker、资金、持仓和策略状态作为
一个 RuntimeState 快照提交，并在同一收敛过程中更新 PAPER/LIVE
`auto_exit_plans` 真源，最后才把事件设为 `APPLIED`。回测的 `ExitPlanBook`
仍随隔离运行状态保存。回调异常会回滚当次内存效果；快照提交失败、结果不确定，
或启动时存在未应用事件时，runtime 安装同业务键屏障，丢弃新的
tick/kline 决策并拒绝人工确认，直到同事件幂等收敛。

同一运行的 durable event 严格按 `(created_at, event_id)` 串行推进。暂停、停止或
尚未启动的运行没有可用消费者时，事件保持 `PENDING` 且不消耗失败次数；消费者
会跳过该运行继续处理其他运行，避免全局队头阻塞。暂停和停止在存在待审批意图、
活动委托、冻结预留或 durable barrier 时必须拒绝；正常停止顺序固定为停止生产者、
有界等待当前串行事件、停止策略并写最终快照，最后才断开 Broker。

委托终态和成交回报是两条独立消息。`FILLED`，或带累计成交量的
`CANCELLED / REJECTED / EXPIRED`，若领先于已持久化 TRADE，TradeIntent、T 批次、
策略与退出计划必须保持 `RECONCILE_REQUIRED` 和原 pending 关联；只有真实 TRADE
累计追平该委托报告量后，才能进入最终 `FILLED / CANCELLED`、`OPEN / CLOSED` 等
状态。委托报告不得单独生成成交或释放入场/退出门控。

RuntimeState 版本更新使用数据库原子 CAS。每次 Engine 快照带 manager-owned attempt
token；提交结果不确定时以数据库 token 和版本为准采纳已提交结果，外部写入赢得
CAS 时只合并其归属字段并保留 Engine dirty state，随后基于新版本继续保存。

PAPER/LIVE 做 T 策略把有界、因果 tick 观察窗保留在内存热路径；普通 tick、
滚动窗口、评分、指标和诊断不得逐 tick 写数据库。策略 RuntimeState 只在上午
`11:35`（覆盖 `11:30` 边界）和下午 `15:05`（覆盖 `15:00` 边界）尝试 `SESSION`
checkpoint：每 `5` 秒重试一次，最多 `60` 次（约 `5` 分钟）；超时仍保持 `BLOCKED` /
fail-closed，绝不能把该边界伪造为完成。必须已证明 `WholeQuoteHub` 的全局
`stream_id + committed sequence` 就绪围栏，且 event/control/market 队列已排空、没有
待处理 invalidation 和 `LAGGING` 消费者，才可 seal 完整检查点。检查点记录交易日、
会话/边界、全局水位与连续性、状态指纹和完整性；逐标的源水位仅作审计，不能代替全局
围栏。恢复只能从最近完整检查点加权威 tick 重放，缺口立即 fail-closed。

PAPER/LIVE 的 `TERMINAL` 已处理前缀不等同于上午/下午 session boundary。只有队列
quiesced、连续性完整，并已证明 processed watermark/source prefix 时，才可执行
`PREPARED → receipt → FINALIZE`；缺任一证明仍 fail-closed。正常完成、停止和错误/
取消都必须强制 flush 当前热诊断并 seal 已处理前缀，随后仍按这条证明链收口。审批、
TradeIntent、命令 outbox、订单/成交 inbox 与回报、冻结/订单状态及用户可操作候选等
外部交易事实仍在事件发生时原子持久化，不等待 `SESSION` checkpoint。

所有 BACKTEST 对普通热状态、逐 tick trace、诊断和不伴随 `TradeIntent` 的纯
`MATERIAL` 评估采用 `DAY_BATCH`：每个虚拟交易日作为一个日级 UOW 持久化，普通
tick 循环不执行数据库 I/O；错误或取消必须 seal 已处理前缀及当日部分终态证据。
真正可执行的候选、`TradeIntent` 与模拟成交生命周期仍是即时幂等交易事实，不能为
压测而延后。性能证据必须将这些不可消除的业务写入与普通热路径写入分开统计；恢复
同样依赖最近完整日检查点加权威 tick，任何连续性缺口均 fail-closed。

完整账户快照的对账按灰度阶段处理。`SHADOW` 是手工交易共存的准备阶段：QMT
客户端产生且没有 QuantX 关联 ID 的委托/成交会作为外部活动持久化并计数，
不会阻止账户事实收敛；`CANARY / LIVE` 中出现同类活动则暂停自动执行。成功的
新协议 1.1 完整快照会把同设备、同账户范围内较旧的完整快照死信标记为
`SUPERSEDED`，并闭环对应告警，但保留原始失败审计记录。
周期性完整快照只更新账户事实并参与同样的旧快照合并，不得重新打开已完成的账户
对账门；只有新控制会话、XTTrading 连接代际变化或明确的异常恢复快照可以触发
`RECONCILING → READY` promotion。

对账和账户实盘窗口以 Agent 派生的 `effective_order_status` 判断委托是否仍可成交，
同时保留 QMT 原始状态用于审计。未成交的 A 股日内委托在收盘后按
`EXPIRED / MARKET_SESSION_CLOSED` 收敛，不再被计入活动外部委托；
历史委托本身仍保留在外部活动基线中。

账户执行控制 mutation 与 Engine 的快照对账会串行锁定同一条
`account_execution_controls` 记录。建立账户实盘窗口和启用增仓授权时，服务会在
锁内重新读取并校验账户授权状态、kill switch、对账状态、完整快照及外部活动计数，
避免读取 readiness 后到实际提交前发生状态竞争。新增外部活动、手工暂停或
无法解释的对账异常会立即使账户窗口失效；断线重连后也必须重新满足新鲜快照门禁。
`account_trading_rollouts` 仅保存做 T 助手的灰度阶段、额度和策略确认；助手暂停或
功能开关关闭不会改写账户级增仓授权。

Redis 只用于唤醒消费者，以及向 API 发布行情、策略与交易事件的订阅通知，
不能作为订单、成交、Portfolio 或 bucket 的状态真源。API 收到交易事件
唤醒后仍会从数据库重新读取投影。订单必须先持久化 pending 状态和
`trade_command_outbox`，才能由 API Hub 下发给 Agent。

自动卖出由 Engine 的 `ExitPlanBook` 统一承载。它是运行时内部组件，不是一个
独立的“卖出策略”服务。入场策略在 BUY 意图中附带
`ExitPlanTemplate`，只有真实 BUY 成交回报会激活计划。Engine 在策略
`step()` 之前评估退出规则，将命中的计划转换成标准 SELL `TradeIntent`，
继续经过 OrderSizer、后置风控、Broker 和成交回报收敛。做 T 仅负责入场
信号和退出模板；同一个做 T `StrategyRun` 内的 `ExitPlanBook` 负责退出评估，
绝不为做 T 新建退出策略。PAPER/LIVE 计划以
`auto_exit_plans` 为唯一持久化真源，运行内 `ExitPlanBook` 只是热缓存；回测
保持内存计划。完整契约见
[A 股自动退出计划与卖出策略契约](../../trading/contracts/A股自动退出计划与卖出策略契约.md)。

手工持仓的部分动态止盈也由 Engine 承载。此类 `MANUAL_POSITION` 计划不创建
StrategyRun，由全局 `ExitPlanMonitor` 从 `WholeQuoteHub` 中央快照读取价格、累计
成交量和五档盘口，执行
`ADAPTIVE_VOLUME_PRICE_TRAILING`。量能陈旧会降级到价格模式，价格陈旧则
暂停；触发后持久化 pending 委托，逐笔成交通过 `agent_report_inbox` 幂等
回填，部分成交只继续管理未成交的保护数量。实盘计划要求显式自动卖出授权。

Engine 使用 PostgreSQL advisory lock 保证同一数据库只有一个实例取得执行
权，并持续写入 `runtime_component_heartbeats`，供 API 就绪检查使用。

持久化 `ExitPlanMonitor` 每秒只扫描 `auto_exit_plans` 中没有运行绑定的
`MANUAL_POSITION / MANUAL_LIQUIDATION`，并消费 `WholeQuoteHub` 全市场批次；历史
托管运行命令标记由启动迁移清除，不改变 `strategy_run_id` 为空即归 Monitor 的规则。
`T_TRADE_BATCH / LIMIT_UP_BOARD / FIRST_BOARD_PROMOTION_V2 / ENTRY_PLAN`
等入场来源计划由原 `StrategyRun` 在自身串行行情队列、策略 `step()` 之前评估。
用户新建的人工托管 `MANUAL_POSITION` 与清仓计划都由 Monitor 执行。Engine 启动
迁移会先停止旧专用退出运行、保留计划状态和订单血缘，再恢复策略运行；持续看门狗
负责发现孤儿或错配所有者，并立即
fail-stop；Monitor 不接管执行。API 对人工计划的
创建、修改、启停、取消、立即评估和批量清仓全部写入
`engine_command_outbox`；Engine 在账户＋股票锁内校验 `config_version`、保护量
冲突和待成交 SELL。承载活跃入场来源计划的原运行只能 `DRAINING`，不得普通
停止；原运行异常时只能恢复同一个运行，不得另建退出策略。

退出计划运行态使用独立单调 `state_version` 做数据库 CAS；配置变更继续使用
`config_version`，两者不得混用。一次规则命中时，计划的 `pending_intent_id` 与同
ID、同 `plan_id/run_id/account_id` 的 SELL `TradeIntent` 必须在一个事务中提交；
任何版本竞争、绑定不一致或意图写入失败都整体回滚。重启恢复时，已有
`PendingTradeOrder` 的意图只等待回报，不再次路由；命令结果落盘前崩溃后的重放
返回既有订单。普通 `CANCELLED / REJECTED / EXPIRED` 即使累计成交量为零，也不能
单独释放 pending；只有带 QMT 完整快照证明、从未投递的本地消息箱过期/取消证明、
明确未越过 Broker 边界的本地拒绝证明，或 Agent 明确声明尚未执行的拒绝/过期证明的
`RECONCILED_ZERO_FILL` 才能释放。已投递但缺少这种证明的过期命令继续进入
`RECONCILE_REQUIRED`。后续 accepted ACK、Broker 委托或成交等相反证据必须立即撤销
本地零成交证明；迟到成交必须计入成交量并把计划置为 `ERROR`，禁止第二次卖出。
已释放 intent 的同终态委托快照重放，只有在累计成交量不超过已经持久化的真实
成交量时才视为幂等重放；更高序号的工作态、累计成交增长或新成交才是矛盾证据。
由零成交证明失效或旧 intent 迟到成交形成的安全 `ERROR` 不得通过普通启用、
`RESUME` 或规则更新清除。LIVE 计划发生这种反证时，计划失效与账户执行隔离必须在
同一事务完成：账户转为 `PAUSED / RECONCILE_REQUIRED`、清空 controlled window，
`paused_reason.kind=BROKER_EXECUTION_AFTER_RELEASE`。隔离事务扫描该账户所有未终结的
LIVE PLACE SELL，不只处理触发反证计划的替代卖单；历史上已有持久化终态的
卖单不得被重新打开。每个受影响的退出计划都撤销旧自动授权、停用并保留
sticky `ERROR`，之后只能取消后按最新持仓重建。若某条 SELL 的 outbox 可以证明
从未投递，则把 outbox/pending 本地取消，并由它所属计划的 `ExitPlanBook` 以
`RECONCILED_ZERO_FILL / LOCAL_OUTBOX_CANCEL` 释放当前 pending，同时保留原 sticky
`ERROR`；若已经投递或绑定不完整，则原 PLACE_ORDER 立即转为
`RECONCILE_REQUIRED` 并禁止重投，精确 pending 持久化为 `CANCEL_REQUESTED`。有
Broker order id 时只排队一个幂等 `CANCEL_ORDER`；没有 id 时等待权威委托回报，首个
补齐身份的 ORDER 回报沿同一业务身份排队撤单，重复回报不得重复撤单。每个撤单对象
拥有稳定业务身份和单调尝试号：安全过期且确定未投递的尝试可以原地续期；结果不确定的
旧尝试必须封存，再以同一业务身份创建至多一个后继尝试。旧证据单与当前替代单是两个
独立撤单对象，不能因其中一个已经终态而漏撤另一个。投递、ACK 与物理 WebSocket 发送
路径统一按“候选只读发现 → 账户锁 → outbox 锁并重验”取锁，不能与失效事务形成
outbox→account 反向锁；迟到 `command_processing` 或 accepted ACK 只记录事实，不能
复活或改写已隔离 PLACE_ORDER。全链统一锁序为
`AccountExecutionControl → TradeCommandOutbox → PendingTradeOrder → StrategyOrderCorrelation`
` → TradeIntentRecord → AutoExitPlanRecord`。

只要 `CANCEL_REQUESTED` 尚未由券商终态收敛，完整快照必须持续报告
`CANCEL_REQUEST_PENDING` 并保持账户 `PAUSED / RECONCILE_REQUIRED`。即使旧委托已经
终态，一张干净快照也不能自动清除释放后反证形成的 sticky 隔离。用户必须从账户安全页
选择服务端列出的精确隔离订单，通过两阶段
`REPAIR_QUARANTINED_ORDER` 挑战绑定
`client_order_id / quarantine_reason / snapshot_id / state_version`。服务端在统一锁序内
重验最新完整快照、原隔离事件、计划/意图/订单绑定和权威终态或已收敛成交，修复后只会
终结精确 pending 并写审计事件；计划仍保持 sticky `ERROR`，账户仍保持
`PAUSED / RECONCILE_REQUIRED`，且把该修复快照设为新的新鲜度边界。只有之后一张严格
更新且无冲突的完整快照，才可把账户降为 `DISABLED / READY`；它不会恢复交易权限，
用户仍须取消旧计划并按最新持仓重建。若本地已记录撤单终态、但更晚的权威快照仍显示
原委托处于工作态，则以 `TERMINAL_ORDER_STILL_WORKING` 继续阻断，不把相互矛盾的两份
事实解释为已完成撤单。快照判断只使用 LIVE 订单，并同时校验隔离来源序号、快照
`state_version` 和最终锁内刷新；同一 Broker 事实缺少 execution id 时使用稳定内容指纹，
不得用随机身份破坏幂等。

做 T 的保护 SELL 使用 `MARKET` 执行偏好：LiveBroker 转为
`MARKET_CONVERT_5_LIMIT`，QMT Agent 对沪/北市场与深市分别映射为
`MARKET_SH_CONVERT_5_CANCEL` 和 `MARKET_SZ_CONVERT_5_CANCEL`，即五档即时成交、
剩余撤销。SELL intent 固定由退出计划拥有：`owner_type=EXIT_PLAN`、
`owner_id=plan_id`，并保留 `strategy_run_id`、`t_batch_id` 与
`t_trade_role=exit` 供原运行收敛批次。

未预授权退出的预览—确认挑战绑定精确的计划、意图、账户、设备与版本；挑战消费、
Engine command outbox 创建和幂等业务键在同一事务中提交。`command_ack` 只表示
投递，计划成交状态仍只由 QMT Agent 的委托与成交回报推进。所有
`owner_type=EXIT_PLAN` 的 SELL 在通用执行器进入 Broker 前都强制使用
`strategy-exit:{plan_id}:{intent_id}`，Monitor 崩溃重放也只能命中同一条
持久命令。LIVE SELL 的最终入队事务还会重新校验计划投影与内嵌模板的
`plan/account/instrument/source/run` 完全一致，并按来源、运行和托管命令标记的
正向矩阵确认唯一执行 owner；任一错配或未知来源都不能生成 QMT outbox。
Agent 真正领取普通 PLACE_ORDER 前还会重新锁定账户执行控制和该 outbox：账户未完成
对账、命令已被隔离或命令载荷在候选发现后发生变化时均不投递；撤单和紧急停止仍走
高优先级通道，不被账户隔离阻塞。
人工计划的创建、更新和启停由客户端为每次用户操作生成业务幂等键；传输重试复用
原键，新的用户操作使用新键。服务端按账户、计划和操作做命名空间哈希，因此旧
“启用”响应在后续“暂停”完成后重放，只返回旧命令结果，不能再次启用计划。

行情状态为 `STARTING → SYNCING → READY → STALE/OFFLINE`。只有 `READY`
继续分发关键实时动作；交易时段 10 秒无新批次进入 `STALE`，午休、收盘和
非交易日不误判。关键消费者使用容量 8 的有序队列，溢出后显式进入
`LAGGING` 并停止相关回调；UI 使用容量 1 的 latest-only 队列并记录合并数。
Pub/Sub 缺批时 Hub 从 Redis 最新全量快照收敛，不重放可能过时的中间 tick。
缺口出现到补水完成期间停止增量分发，中央行情和关键消费者都不能恢复为
`READY`。sequence 1 快照在 API 仍为 `SYNCING` 时只更新中央状态，不提前分发；
sequence 2 连续性屏障提交后仍保持 `SYNCING`。Agent 收到其 ACK 后强制发送
sequence 3 readiness-confirm（可为空），只有该批次被同一 Redis CAS 提交后，
API、Engine 与 freshness lease 的 stream/sequence 才完全一致，Hub 才首次向
消费者分发完整中央快照；之后真实有序回调从 sequence 4 开始。任一水位或租约
不一致时，策略、条件清仓和自动退出等实时交易动作保持关闭。
