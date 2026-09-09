# A 股自动退出计划与卖出策略契约

## 1. 目标

自动卖出是 Engine 的公共交易能力。PAPER/LIVE 由独立 `ExitPlanRuntime` 消费
`auto_exit_plans` 唯一真源；`ExitPlanBook` 只保留为 BACKTEST 内存适配器。任何入场功能只负责回答“为什么买、
买什么”，成交后由统一的
`ExitPlan` 回答“何时卖、卖多少、如何遵守 T+1、如何委托”。

公共能力必须满足：

- 做 T、打板、趋势、网格和条件清仓可以组合不同卖出策略。
- 只有真实买入成交才能激活退出计划，买入意图和 `command_ack` 均不能激活。
- 卖出必须生成标准 `TradeIntent`，继续经过 OrderSizer、后置风控、Broker
  和成交回报收敛。
- PAPER/LIVE 退出计划统一持久化在 `auto_exit_plans`，策略参数升级不得丢失
  已成交数量、峰值、追踪止盈底线和待成交卖单。
- 做 T、打板、买入计划、普通策略和人工计划统一恢复到 `ExitPlanRuntime`；source
  execution 可以先终态，仍存续的退出义务不得反向阻塞或复活 source runtime。
- 来源执行身份只用于不可变归因和审计；所有 PAPER/LIVE 卖出执行身份均为
  `EXIT_PLAN/<plan_id>`，不得按 `strategy_run_id` 再建第二条消费路径。

## 2. 四层卖出策略

一个 `ExitPlanTemplate` 由四类可组合策略组成。

### 2.1 触发策略 `ExitRuleSpec`

触发策略只判断当前是否应该退出，不计算最终合法卖量。

内置策略：

| 策略 | 用途 |
| --- | --- |
| `TARGET_PRICE` | 到达绝对目标价 |
| `STOP_PRICE` | 跌破绝对止损价 |
| `GROSS_TAKE_PROFIT` | 达到毛收益率 |
| `NET_TAKE_PROFIT` | 扣除双边费用后达到净收益率 |
| `TRAILING_NET_PROFIT` | 净收益达到门槛后按动态底线回撤退出 |
| `ADAPTIVE_VOLUME_PRICE_TRAILING` | 达标后按量价强弱跟涨或退出固定保护数量 |
| `TRAILING_PRICE_DRAWDOWN` | 从持仓后峰值价格回撤退出 |
| `HARD_STOP` | 按成本收益率强制止损 |
| `TIME_OF_DAY` | 到达日内指定时点退出 |
| `MAX_HOLDING_DAYS` | 达到最大交易日持有期退出 |

同一计划可以配置多条规则。若同一时刻多条规则触发，优先级最高的规则胜出。
规则可以配置 `once=true`，用于分批止盈后不再重复执行同一阶段。

`ExitStrategyRegistry` 使用字符串注册规则，不使用封闭枚举限制业务扩展；
Engine 通过 `StrategyExecutor.register_exit_strategy()` 统一注册，并在运行恢复
时复用同一个注册表。
例如打板功能可以注册 `LIMIT_UP_BREAK`、`AUCTION_WEAKNESS` 或
`OPEN_BOARD_TIMEOUT`，无需修改 `ExitPlanBook` 状态机。

### 2.2 卖出数量策略 `ExitSizingPolicy`

规则触发后，数量策略只基于计划剩余数量计算目标卖量：

- `ALL_REMAINING`：全部剩余数量。
- `PERCENT_REMAINING`：按剩余数量百分比分批退出。
- `FIXED_VOLUME`：固定数量退出。

数量策略负责整手取整和清仓零股表达。真实可卖量、冻结量、资金、涨跌停、
停牌和最终合法数量仍由交易域、OrderSizer、风控与 Broker 决定。

### 2.3 T+1 策略 `ExitT1Policy`

每个退出计划必须显式选择：

- `WAIT_UNTIL_SELLABLE`：当日买入不可卖，等待可卖日；适合打板、趋势持仓。
- `ALLOW_SAME_INSTRUMENT_SUBSTITUTION`：允许使用同标的昨日可卖库存置换；
  仅适合正向做 T。
- `REJECT_IF_UNSELLABLE`：不可卖时拒绝，不自动等待。

T+1 置换不得是系统隐式默认行为。卖出意图会携带
`allow_t1_substitution` 和 `t1_insufficient_action`，由统一风控执行。

### 2.4 委托执行策略 `ExitExecutionPolicy`

执行策略描述触发后的委托偏好：

- 价格参考：买一、卖一、最新价或限价。
- 订单类型：限价或受支持的其他 Broker 类型。
- 最大退出滑点。
- `AUTO` 或 `MANUAL_CONFIRM` 授权模式。

实盘自动卖出必须具有显式 `auto_exit_authorized=true`。未授权的自动计划会
被 Engine 降级为人工确认，不得由入场策略绕过。
精确授权的预览—确认挑战同时支持 Web 和原生客户端，但必须绑定当前
有效会话、唯一授权资金账户、计划版本、安全快照和一次性确认凭据；
确认时必须在同一会话内重新校验 `trade:approve` 权限与实盘就绪状态。

做 T 的人工 BUY 确认必须同时冻结确定性的退出计划 ID、模板版本和最大保护
数量。真实 BUY 成交只能在该确认上限内派生精确退出授权；部分成交可以扩大到
确认上限，但不得延长授权有效期。模板变化、超量成交、授权过期或外部导入的
入场没有原确认信封时，退出计划必须等待单独人工授权。

独立 `T_ASSISTANT_EXECUTION` 的退出确认信封使用 schema 2，绑定 source execution ref、
LIVE 环境、candidate id/fingerprint、policy/feature schema 和精确退出模板/保护量，不携带
虚构的 `strategy_run_id`。source execution 的不可变身份关联冻结配置；确认后的重新分配
不得替换这些身份。legacy StrategyRun 的 schema 1 原确认仅按其原身份继续验证，服务端
根据真实 owner 选择格式，不对新 owner 回退到旧信封；P7 排空收尾时再清理 legacy 路径。
源执行停止不撤销已成交计划的合法保护；LIVE 退出仍竞争同一账户持仓锁，不能借用 PAPER
账本或复制保护义务。以上授权基础已隔离验证，尚不表示新 owner 的 LIVE 入场已开放。

做 T 保护退出固定使用 `TExitOrderPolicy.v1`：只允许 `FIX_PRICE`，以 BID1 为
参考，最多向下 30bps，并受价格 tick、跌停价和适用的价格笼子下界约束。单笔
委托 30 秒、总退出窗口 90 秒，最多 replace 2 次；撤单未确认或订单结果未知时
只能对账，禁止 replace。不得为做 T 退出保留 MARKET/五档转限的第二种生产语义。
`ExitPlanTemplate.execution` 和 metadata 必须直接由该版本化 policy 投影，策略参数
不得覆盖 30bps、30 秒、90 秒或 2 次 replace；计划配置版本另存为
`exit_plan_config_version/config_version`，不得冒充 `exit_policy_version`。

### 2.5 冻结成本依据 `ExitCostBasisSnapshot`

退出计划判断收益时必须使用创建时冻结的成本依据，不得持续读取当前持仓均价。
多次买入会改变券商持仓均价，但不会改变既有计划的动态保盈线。

人工计划创建时必须二选一：

- `BROKER_BUY_ORDERS`：用户选择 QuantX 已持久化且成交数量大于 0 的买入委托。
  Engine 在同一事务中重新读取委托，按每笔成交额加保守估算的买入费用计算
  每股全成本；所选委托成交数量之和不得小于计划卖出数量。同一笔买入委托
  在任一有效退出计划中按整笔独占，只有原计划进入终态后才能被新计划选用。
- `MANUAL_UNIT_COST`：用户填写每股全成本；输入值必须已包含买入手续费，
  计算净收益时不得再次计入买入费。

快照固定保存模式、单位成本、依据数量、费用处理、所选委托成交快照、费用
策略和冻结时间，并进入实盘授权指纹。成本依据不可编辑；需要更换时必须取消
并重建计划。策略入场成交使用 `ENTRY_FILLS`；迁移前计划保持
`POSITION_AVERAGE_SNAPSHOT` 语义，不追溯重构历史批次。

## 3. 生命周期

```text
入场策略输出 BUY TradeIntent + ExitPlanTemplate
  -> OrderSizer / Risk / Broker
  -> 真实 BUY TradeExecutionEvent
  -> 持久化 auto_exit_plans（source execution 可独立终态）
  -> ACTIVE
  -> ExitPlanRuntime 消费权威行情并评估 ExitRuleSpec
  -> 生成 SELL TradeIntent
  -> OrderSizer / Risk / Broker
  -> 真实 SELL TradeExecutionEvent
  -> PARTIALLY_EXITED / COMPLETED
```

人工导入已成交做 T 买单时，`TTradeBatch`、导入来源账本、策略持久状态补丁和公共
`auto_exit_plans` 必须在同一数据库事务中提交；只有提交成功后才更新驻留策略镜像。
任一持久步骤失败必须整体回滚，不能留下仅存在于内存的批次或缺少退出计划的导入记录。
由该公共计划生成 SELL 时必须携带 `t_trade_role=EXIT` 和原 `t_batch_id`，使最终命令边界
再次执行冻结的 `TExitOrderPolicy.v1` 并把角色/批次写入 pending 与 correlation。

主要状态：

- `PENDING_ENTRY`：只有模板，尚无真实买入成交。
- `ACTIVE`：已有受保护数量，等待卖出规则触发。
- `EXIT_PENDING`：卖出意图、委托或成交回报尚未收敛。
- `PARTIALLY_EXITED`：已部分退出，仍有剩余数量。
- `COMPLETED`：计划数量全部退出。
- `PAUSED / CANCELLED / ERROR`：人工或异常状态。

委托回报和成交回报可能乱序。`FILLED` 委托先到时，计划必须保留 pending
上下文，直到对应成交数量收敛后才完成一次性规则并释放 pending，防止重复
卖出。普通 `CANCELLED / REJECTED / EXPIRED` 即使携带累计成交量零，也不能单独
证明不存在迟到成交。`RECONCILED_ZERO_FILL` 只有在持久化了 QMT 完整快照零成交
证明、从未投递的本地消息箱过期/取消证明、明确未越过 Broker 边界的本地拒绝证明，
或 Agent 明确声明尚未执行的拒绝/过期证明后才能释放；已投递但没有明确未执行证明的
命令只能进入 `RECONCILE_REQUIRED`。后续 accepted ACK、Broker 委托或成交等相反
证据必须撤销本地证明。已经终结并释放的 intent ID 不得再次使用；若之后仍收到该 intent 的迟到
成交，必须先累计真实成交，再将计划置为 `ERROR` 并阻断新的 SELL。
已完成 intent 的更高序号终态委托重放，如果权威累计成交量不大于已经持久化的真实
成交量，只推进回报水位，不得误判为矛盾；工作态回退、累计成交增长或新成交才触发
fail-closed。由证明失效或旧 intent 错配形成的安全 `ERROR` 禁止普通启用/恢复，
只能在显式 Broker 对账确认旧委托完全收敛后解除。

所有 `owner_type=EXIT_PLAN` 的首笔 SELL 在进入 Broker 前必须由 Engine 覆盖为确定性
幂等键 `strategy-exit:{plan_id}:{intent_id}`。仍活动的做 T intent 可以在原 90 秒窗口内，
按 `TExitOrderPolicy.v1` 创建最多两次替单，替单键固定为
`strategy-exit:{plan_id}:{intent_id}:replace:{n}`（`n=1,2`）。每次替单保留原 owner、
intent、batch 与 trace；PendingTradeOrder 保存从零开始的 `t_order_attempt`、前一
`t_order_parent_client_id` 与不可延长的 UTC `t_order_original_created_at`，每个 attempt
拥有自己的 client/correlation/outbox。数据库保证同 intent/attempt 唯一且前单至多一个后继。
旧单权威终态、累计成交与已应用成交明细完全对齐之前不得替单；零成交仍需上述独立证明。
生命周期未完成时，单个 attempt 终态不释放 intent/ExitPlan pending；旧 attempt 的迟到回报
只能推进该单证据与累计真实成交，不得终结新单。整个原 intent 的全部 attempt 收敛后才能
最终释放。ExitPlanRuntime 恢复同一 attempt 必须返回既有持久订单，不得因重启生成新 attempt。
最终入队事务必须再次校验 `auto_exit_plans` 投影与内嵌模板的计划、账户、标的、
来源和运行身份完全一致，并按本节正向 owner 矩阵核验来源与 run；历史托管命令
标记不再参与 PAPER/LIVE 所有权判定。

## 4. 状态所有权

`StrategyBase` 只拥有信号和入场业务状态。PAPER/LIVE 的
`auto_exit_plans` 是退出计划唯一持久化真源，不再向 source runtime 的
`ExitPlanBook` 双写；回测使用隔离的内存 `ExitPlanBook`，不写计划表。
`StrategyRunState.custom_state.auto_exit_plan_book` 不再作为 PAPER/LIVE 的恢复
来源，也不得与计划表长期双写。

计划来源由不可变 source execution 绑定确定，执行所有权统一如下：

- 入场来源 `T_TRADE_BATCH / LIMIT_UP_BOARD / FIRST_BOARD_PROMOTION_V2 / ENTRY_PLAN`
  必须保存与模板完全一致的 source owner/environment；旧来源可为 `STRATEGY_RUN`，
  新来源分别允许明确的 `T_ASSISTANT_EXECUTION / BOARD_ASSISTANT_EXECUTION /
  ENTRY_PLAN`，但 `EXIT_PLAN` 不得自指为 source。
- 人工来源 `MANUAL_POSITION / MANUAL_LIQUIDATION` 由 `MANUAL_COMMAND` 见证，
  `strategy_run_id` 必须为空。历史托管命令标记只是迁移输入，不能决定运行时所有权。
- 以上合法计划全部由 `ExitPlanRuntime` 执行；行级 owner 与模板 source 三元组缺失、
  未知、冲突或环境不一致均为 `INVALID_OWNER` 并 fail-closed。

Engine 所有权审计与持续看门狗验证公共 runtime、计划投影和 source 绑定；source
execution 缺失或已终态不是退出失败条件。公共 runtime 不运行、计划绑定冲突或
回报水位不可判定时阻断新风险增加，已有退出计划保持可见并由同一 runtime 恢复。

PAPER/LIVE 计划的运行态由单调 `state_version` 做 CAS，规则配置由
`config_version` 管理。规则命中导致 `pending_intent_id` 从空变为非空时，必须在
同一数据库事务写入同 ID、同计划、同账户的 SELL `TradeIntent`；缺失、
错绑、重复 owner 或版本竞争必须整体回滚。CAS 失败后必须重新加载
权威计划再重放同一行情事实，不能覆盖另一执行者的更新。

策略可以：

- 在 BUY `TradeIntent.metadata.exit_plan_template` 中附带退出模板。
- BACKTEST 可从 `StrategyInput.exit_plans` 读取内存投影；PAPER/LIVE source 不读取
  私有计划簿，展示从公共投影查询。
- 输出 `ExitPlanCommand` 更新规则、暂停、恢复或取消计划。

策略不得：

- 直接把行情触发当成卖出成交。
- 修改计划的真实已成交数量。
- 自行计算真实可卖量或绕过统一风控。
- 因入场功能停止而静默丢弃仍有剩余数量的退出计划。

source execution 只需收敛自身 BUY approval、pending、outbox 和未知结果即可终态；
活动 ExitPlan/TTradeBatch 仍进入账户级 obligation watermark 和同票准入占用，但不阻止
source 停止。ExitPlanRuntime 在 Engine 重启后按计划表和回报事实独立继续。

## 5. 当前功能映射

### 5.1 正向做 T

入场：

- 回撤后企稳反弹信号。
- 手工确认买入。

退出计划：

- `TRAILING_NET_PROFIT`。
- 可选 `HARD_STOP`。
- 可选 `TIME_OF_DAY` 或 `MAX_HOLDING_DAYS`。
- `ALL_REMAINING`。
- `ALLOW_SAME_INSTRUMENT_SUBSTITUTION`。

做 T source 不直接输出自动 SELL。真实 BUY fill 注册公共计划后，由
`ExitPlanRuntime` 评估并生成 `EXIT_PLAN/<plan_id>` SELL；source 停止不影响计划恢复。
BACKTEST 仍可通过内存 `ExitPlanBook` 重放同一规则语义。

### 5.2 条件清仓

现有持仓级条件清仓通过适配器复用：

- 目标收益率映射为 `GROSS_TAKE_PROFIT`。
- 目标价格映射为 `TARGET_PRICE`。
- 全部、百分比、固定数量映射为 `ExitSizingPolicy`。

其触发和数量语义与退出计划一致；旧 API 与订单模型继续作为兼容入口。

持仓级动态部分止盈使用 `ADAPTIVE_VOLUME_PRICE_TRAILING`：创建计划时把待保护
数量固化为小于当前可卖量的 100 股整数倍，之后不因总持仓或可卖量变化扩大。
目标收益率或目标价只负责激活，激活后不会立即卖出。Engine 每秒消费 QMT
whole-quote，综合峰值回撤、15/60 秒价格变化、累计成交量速度和五档盘口
失衡：量价强势时继续跟涨，转弱评分连续两次达到阈值时退出；峰值回撤、
放量急跌或动态保盈线失守时立即退出。实时量能陈旧时降级为价格追踪，价格
也陈旧时暂停判断，禁止用旧行情触发卖出。行情新鲜度门禁只在交易时段内
生效；午休、收盘后、周末与休市日统一进入 `MARKET_CLOSED` 等待态，不得把
最后一笔合法收盘行情记为陈旧故障，也不得在等待态生成或确认卖出意图。
下一交易时段开始后，必须先收到新鲜行情并恢复权威流 `READY` 才能继续评估。

手工持仓动态计划和策略入场计划都持久化在 `auto_exit_plans`，统一由
`ExitPlanRuntime` 执行。各入口共享同一个规则、计划
状态机和成交回报语义。已有未完成卖出委托时
拒绝创建或修改；触发后使用买一价减保护滑点的限价委托。只有 QMT Agent 的
真实委托/成交回报才能推进
`EXIT_PENDING -> PARTIALLY_EXITED / COMPLETED`，`command_ack` 不得推进成交。

### 5.3 打板

`AshareLimitUpBoardStrategy` 在临近涨停、尚未封死时生成一次 `swing` BUY
意图；已经封板、缺少涨停价、一字板、风控禁买、已有持仓或已有活跃退出
计划时保守观望。真实买入成交后创建独立模板：

```text
LIMIT_UP_BREAK(priority=1000, sizing=ALL_REMAINING)
TRAILING_PRICE_DRAWDOWN(priority=700, sizing=PERCENT_REMAINING)
MAX_HOLDING_DAYS(priority=600, sizing=ALL_REMAINING)
T1=WAIT_UNTIL_SELLABLE
execution=BID_PROTECTED_LIMIT
```

打板模块只新增自身触发策略和模板配置，不复制订单状态机、T+1、自动授权、
持久化或成交收敛代码。

## 6. 审计要求

每次自动退出必须能从意图与状态中还原：

- `exit_plan_id`、`exit_rule_id`、规则类型和触发原因。
- 来源功能、来源批次、策略运行和配置版本。
- 触发时价格、净收益、峰值、追踪底线和持有交易日。
- 请求数量、已成交数量、剩余数量。
- T+1 策略、风险动作、执行价格参考和授权模式。

被延迟、拒绝、取消、部分成交和重试都必须保留原计划，不得伪造为已退出。

## 7. 卖出管理与人工计划

`/liquidation` 的产品名称统一为“卖出管理”，固定承载“退出计划、持仓清仓、
卖出历史”，不另建任务中心。模拟盘和实盘的非回测计划统一持久化到
`auto_exit_plans`；回测仍使用内存 `ExitPlanBook`。所有合法来源计划都由公共
`ExitPlanRuntime` 执行，source runtime 可在自身 BUY 义务收敛后先停止；不存在人工计划、
入场来源或托管运行之间的第二消费路径。

人工计划创建、更新与启停接口要求调用方提供不超过 128 字符的业务幂等键。同一
网络重试必须复用原键；一次新的人工操作必须生成新键。服务端用账户、计划、操作
类型和调用方键生成持久命令身份，并校验同键 payload 完全相同，禁止旧启用命令在
后来暂停之后通过重放造成 ABA 式重新启用。

人工清仓使用来源 `MANUAL_LIQUIDATION` 和规则 `MANUAL_TRIGGER`。批量操作为
每只股票建立独立计划，以 `group_id` 关联。确认时必须明确选择：

- `AVAILABLE_NOW`：只保护确认时的可卖数量，不等待 T+1。
- `UNTIL_SNAPSHOT_CLEARED`：保护确认时的总持仓，跨日继续处理不可卖部分。
- `UNALLOCATED_ONLY`：保留冲突计划，只认领未分配数量。
- `REPLACE_CANCELLABLE`：取消无待成交委托的冲突计划后重新认领。

暂停和错误计划仍占用保护数量，完成或取消才释放。存在 `EXIT_PENDING` 或待
成交 SELL 时，禁止重复清仓或替换。持续清仓只保护确认时快照，之后新增持仓
不自动加入，计划完成投影必须显示“新增持仓未纳入本次清仓”。

“计划卖出数量”是 QuantX 内部的持仓认领，不是券商冻结。若外部卖出导致最新
持仓少于同一股票全部退出计划的剩余认领数量，Engine 将计划标记为
`RECONCILE_REQUIRED`，撤销精确自动实盘授权，并阻止新的 SELL 意图；既有委托
和成交事实保持不变。系统不得自动缩减任一计划数量，用户必须在处理冲突后按
最新持仓显式重新对账。

无论 source execution 是否仍运行，退出 SELL 都先写入 `trade_intents`，使用
`owner_type=EXIT_PLAN`、`owner_id=plan_id` 且 `strategy_run_id=NULL`，再执行
OrderSizer、T+1/可卖量、涨跌停、后置风控和数据库消息箱投递。
实盘未预授权 SELL 进入
`AWAITING_APPROVAL`，必须通过设备绑定的预览—确认挑战后重新经过实时风控；
拒绝则释放 pending 意图并恢复计划监控。挑战必须精确绑定
`plan_id/intent_id/account_id/device_session_id/config_version/state_version`；挑战
消费与 Engine command outbox 创建必须在同一事务完成。若订单已经进入
`PendingTradeOrder` 而命令结果尚未落盘就崩溃，重放只能返回既有
`client_order_id`，不得再次通知策略或再次下单。

已由权威证明释放的旧 intent 若随后出现 accepted ACK、工作态委托或真实成交，必须
在同一数据库事务完成两层熔断：精确计划保持 sticky `ERROR` 并撤销自动授权，账户
执行控制写入 `BROKER_EXECUTION_AFTER_RELEASE`，转为
`PAUSED / RECONCILE_REQUIRED` 并清空 controlled window。该边界同时处理计划当前的
替代 SELL 与账户内其他所有未终结 LIVE PLACE SELL，不依赖它们在哪个
Engine 热缓存或 owner 中；历史上已有持久化终态的 SELL 不得被重新打开：

- outbox 为 `QUEUED`，且 `attempts=0`、从未写入 `delivered_at/acknowledged_at`、没有
  Broker id 或来源序号时，才允许以 `LOCAL_OUTBOX_CANCEL` 证明本地零成交；outbox 与
  pending 本地取消，公共计划 CAS 释放当前 pending，但保留原 sticky `ERROR`。
- PLACE_ORDER 已投递、有 Broker 事实或任一绑定不完整时，不得伪造零成交；原命令转为
  `RECONCILE_REQUIRED` 且永不重投，精确 pending 记为 `CANCEL_REQUESTED`。有 Broker
  id 时生成唯一幂等撤单；没有 id 时等待后续权威委托回报取得身份，首个补齐身份的
  ORDER 回报生成同一撤单，重复回报不得重复生成。
- 跨计划本地零成交释放必须逐条证明 durable owner、source、plan、intent 和 correlation
  绑定；成功后该计划停用、清除旧自动授权并进入 sticky `ERROR`。绑定不完整时不得
  猜测释放，必须保留为显式修复对象。
- 普通命令领取、ACK 收敛和物理 WebSocket 发送都先锁账户、再按 `message_id` 锁 outbox
  并重验载荷；与上述失效事务共享相同锁序。迟到 `command_processing` 或 accepted ACK
  不得改写已隔离 PLACE_ORDER。完整锁序固定为
  `AccountExecutionControl → TradeCommandOutbox → PendingTradeOrder → StrategyOrderCorrelation`
  ` → TradeIntentRecord → AutoExitPlanRecord`。
- 每个 Broker 撤单对象使用稳定业务身份和单调尝试号；安全过期且确定未投递的尝试可以
  原地续期，结果不确定的尝试必须封存后创建至多一个后继尝试。旧证据单和替代单分别
  建立撤单义务，不得相互覆盖。Broker 事实缺少 execution id 时必须使用稳定内容指纹，
  不能为重放生成随机身份。
- 只要 `CANCEL_REQUESTED` 尚未由券商终态收敛，完整快照必须报告
  `CANCEL_REQUEST_PENDING` 并维持 `PAUSED / RECONCILE_REQUIRED`。旧委托全部收敛后，
  干净完整快照也不能自动清除释放后反证形成的 sticky 隔离。用户必须从服务端返回的
  隔离订单候选中选择精确 `client_order_id`，通过两阶段
  `REPAIR_QUARANTINED_ORDER` 挑战同时绑定 `quarantine_reason / snapshot_id / state_version`。
  服务端按统一锁序重验最新完整快照、原隔离事件、计划/意图/订单身份和权威终态或已
  收敛成交，修复精确 pending 并记录审计事件；计划继续保持 sticky `ERROR`，账户继续
  保持 `PAUSED / RECONCILE_REQUIRED`，修复所用快照成为新的新鲜度边界。只有之后一张
  严格更新、仅含 LIVE 事实且无冲突的完整快照，才可把账户降为 `DISABLED / READY`。
  它不会重新启用账户、复活旧 PLACE_ORDER 或清除计划错误；之后仍须显式取消并按最新
  持仓重建计划。若本地已记录撤单终态、但更晚的权威完整快照仍显示该委托处于工作态，
  则以 `TERMINAL_ORDER_STILL_WORKING` 继续阻断。快照收敛同时校验隔离来源序号、快照
  `state_version` 和最终锁内刷新，不能让旧快照覆盖并发隔离或修复。

## 8. 卖出计划历史回放

`/liquidation` 同时承载“卖出管理”和“回放测试”两个一级工作区。一级 Tab 与
“卖出计划 / 持仓清仓 / 卖出记录”二级 Tab 固定在同一工具栏；进入回放工作区后
隐藏日常管理二级 Tab，不增加第二行导航。URL 使用 `workspace=REPLAY`、`planId`
和 `runId` 保存当前工作区与回放身份。

单次回放只允许一个资金账户和一个证券标的，计划来源必须二选一：

- 已保存计划：冻结 `auto_exit_plans` 当前 `config_version` 的完整模板，启动时发现
  版本变化必须拒绝。
- 未保存草稿：启动时把编辑器规则序列化成一次性 `ExitPlanTemplate` 快照；该快照
  不写回日常卖出计划。

历史持仓起点优先选择同账户、同证券且已有真实成交的 BUY 委托。选择多笔成交时，
数量与全成本按成交加权，计划从最后一笔所选成交完成后激活；若回放从激活当日开始，
所选数量按 T+1 暂不可卖处理。缺少可用历史成交时，用户可以明确填写历史激活时间、
持仓数量和每股全成本作为手工快照。真实历史 SELL 只作为图表和事件参考，不改变回放
计划的账户状态与收益。

独立回放运行 `AshareManagedExitPlanStrategy.step(StrategyInput)`：策略内部的
`ExitPlanBook` 产生标准 SELL `TradeIntent`，继续经过公共 OrderSizer、A 股
T+1/涨跌停/停牌约束、后置风控和 `BacktestBroker`。该策略只用于隔离回放和回测，
不参与 PAPER/LIVE 日常执行。回放不需要实盘自动卖出授权，也不能提交 QMT 委托。

数据与结果口径：

- 区间最多 20 个已完成交易日，默认 20 日，快捷范围为 5/10/20 日。
- 所有计划至少要求完整 Tick；`ADAPTIVE_VOLUME_PRICE_TRAILING` 还要求买卖五档价格与
  数量。策略通过 `get_backtest_data_requirements(parameters)` 声明严格逐日交易时段
  覆盖和是否需要盘口深度，Engine 必须在创建 Broker、进入回放循环之前完成落库数据
  预检；任一连续竞价 Tick 的五档买卖价量缺失、非有限或为负数时阻断。策略 Step 内
  保留缺失深度检查作为运行时第二道防线，不允许用分钟线或推断盘口近似。
- 成本默认使用系统佣金、最低佣金、印花税、过户费和滑点，允许单次回放显式覆盖；
  所有覆盖值必须是有限非负数。
- 同轴比较“计划卖出 / 继续持有 / 起点立即卖出”三条路径。区间结束时不得强制平仓，
  计划剩余持仓按末价计入期末净值。
- 若区间内形成计划卖出，提供卖出后第 1/3/5/10 个交易日的价格变化；区间数据不足时
  显式标记不可用。
- 结论只陈述该历史区间的收益差、是否触发和数据事实，不外推为未来投资建议。

生命周期真源是 `exit_plan_replay_projections`。Redis 只发送包含 `run_id`、`revision`
和更新类型的轻量通知；查询、历史记录、事件分页和最终 JSON/HTML 报告均从持久化运行、
回测结果和投影重建。回放结束、错误、取消或 Engine 重启恢复都必须把 StrategyRun、
StrategyBacktest 与投影收敛到一致终态。
