# 持仓做 T 有状态机会引擎 V3 实施规格

> 状态：实施权威规格；Windows 当前交付已明确 iOS scope-waiver
> 版本：V3
> 最后更新：2026-08-23
> 适用范围：账户持仓做 T 助手的入场机会识别、人工确认前候选管理、诊断与客户端展示

> **当前 Windows 交付边界（2026-08-23）**：本轮交付按 Windows 主机范围验收，Web 只按桌面体验验收；移动 Web、手机断点、触控 ergonomics 与 phone-browser compatibility 不额外扩展。iOS V3 本轮暂不开发、不生成 Apollo types、不运行 Xcode/SwiftUI/Dynamic Type/VoiceOver 验证，也不作为当前完成门禁。§16、§18.6 与 Phase 4 保留为后续 iOS 计划；`apps/ios` 只保留并行 watchlist 与用户原有改动。

## 1. 文档地位与适用边界

本规格是持仓做 T 助手 V3 入场“信号”生成逻辑的唯一实施依据。产品文案继续称“信号”；代码、数据库和交易域一律使用 `opportunity`，不得恢复旧 `Signal`、`SignalType`、`generate_signal`、`on_tick` 或 `on_bar` 决策主路径。

本规格覆盖：

1. Tick 因果数据如何进入有状态规则引擎。
2. 数据健康、回撤/动量双 FSM、候选生命周期三层信号域状态。
3. 特征、评分、硬门禁、episode、候选锁存与再武装。
4. `MarketDataContext`、`RuntimeState`、评估事件、标的画像和读投影的真源边界。
5. GraphQL、Web（当前按桌面范围）的查询、展示、配置和实时刷新契约；iOS 对应契约保留为后续计划。
6. 回测、PAPER、LIVE 的一致性、测试、验收和迁移。
7. 机器学习作为 V3 稳定后的后续阶段，不参与本轮实盘决策。

本规格不重写持仓同步、人工确认、订单风控、成交收敛、T+1 库存置换和自动退出。它们继续服从以下通用权威契约：

- [A 股动态天平双仓策略实现落地规格与迁移计划](A股动态天平双仓策略实现落地规格与迁移计划.md)
- [A 股三层协作与执行契约](../trading/contracts/A股三层协作与执行契约.md)
- [A 股交易域数据结构与状态机](../trading/contracts/A股交易域数据结构与状态机.md)
- [A 股自动退出计划与卖出策略契约](../trading/contracts/A股自动退出计划与卖出策略契约.md)

与 [持仓做 T 助手一期实现规格](持仓做T助手一期实现规格.md) 的关系：一期文档保留全局监控、持仓 universe、人工确认、批次执行和退出规则的历史实施背景；其中“固定 AND 条件即生成信号”、客户端信号进度和旧信号展示结构由本规格完整替代。若两者冲突，以本规格及上述通用交易契约为准。

## 2. 核心结论

V3 继续采用规则引擎，但从“当前 Tick 同时满足若干条件”改为“可恢复的因果状态机 + 可解释评分 + 硬门禁 + episode 防重复”。该设计解决的不是简单地放宽阈值，而是让系统明确回答：

- 当前行情数据是否足以判断；
- 回撤和动量形态各自走到哪一步；
- 机会分离候选阈值还差多少；
- 哪个硬门禁阻止了候选或意图；
- 同一段行情是否已经发过一次候选；
- 重启、断流、配置变化后是否仍能安全恢复；
- 用户看到的信号、人工确认意图、交易批次分别由什么事实驱动。

规则引擎仍是首选，是因为它能在现有数据量下提供确定性、可回放、可审计和安全迁移。机器学习只有在 V3 累积足够完整的正负样本与结果标签后才进入影子评估。

## 3. 术语与命名

| 产品/UI 文案 | 代码与数据域 | 含义 |
|---|---|---|
| 信号 | `opportunity` | 一次可解释的入场机会判断，不等于订单或成交 |
| 信号路径 | `OpportunityPath` | `PULLBACK_REBOUND` 回撤反弹或 `MOMENTUM_ACCELERATION` 早期动量 |
| 待确认信号 | `OpportunityCandidate` + 待确认 `TradeIntent` | 候选已通过外部发意图门禁，并产生人工确认意图 |
| 机会分 | `opportunity_score` | 0～100 的规则质量分，不是上涨概率或收益承诺 |
| 机会片段 | `OpportunityEpisode` | 同一标的、交易日、路径和形态起点对应的一段行情 |
| 信号评估 | `OpportunityEvaluation` | 一次可审计的状态、特征、分数、门禁和阻断原因快照 |
| 标的画像 | `TTradeInstrumentProfile` | 账户无关、时点可得的慢变量和基线事实 |
| 当前信号快照 | `TTradeSignalSnapshot` | GraphQL/UI 产品边界上的最新读投影，不是真源 |
| 做 T 批次 | `TTradeBatch` / `TTradeStatus` | 订单与成交后的执行生命周期，独立于信号域状态 |

GraphQL 允许在产品边界使用 `Signal` 命名，便于客户端理解；GraphQL resolver 必须从 `opportunity` 领域对象映射，不能借此恢复旧交易域 `Signal` 类或第三条策略输出路径。

## 4. 不可违反的硬约束

1. 回测、PAPER 和 LIVE 只调用 `StrategyBase.step(StrategyInput)`；不得增加或恢复其他行情决策入口。
2. V3 在 `StrategyOutput` 中使用的领域变更载荷只有 `TradeIntent[]` 和 `RuntimeStatePatch`。评估诊断通过状态补丁的当前评估投影及 Engine 物化器产生，不增加第三种策略输出。
3. 策略是纯交易域逻辑，不访问账户服务、数据库、Redis、InfluxDB、网络、文件、系统时钟或 QMT。
4. `RuntimeState` 只保存算法状态；不得保存真实现金、真实持仓、可卖量、冻结资金或最终合法订单数量。
5. Universe/运行资格由全局监控器和执行层提供，是 `TradeIntent` 发射前的外部门禁，不属于信号域内部三层状态。
6. OrderSizer、订单风控、交易日历、A 股整手/T+1/涨跌停/停牌/资金/可卖量检查仍在策略之后执行；机会引擎不得复制这些计算。
7. 人工确认只批准一个尚有效的 `TradeIntent`，不代表成交；成交唯一真源仍是 QMT Agent 上报并持久化的委托与成交回报。
8. 入场真实成交后使用共享 `ExitPlanBook` 管理退出；V3 不新建自定义 SELL FSM，也不把退出状态塞入机会 FSM。
9. 所有特征严格因果。任何画像、基线和结果标签都必须满足 `as_of <= evaluation_time`，不得读取未来数据。
10. 缺失、陈旧、断流或不确定数据必须保守阻断新候选；`null` 与真实数值 `0` 不得互换。
11. Web 和 iOS 只展示服务端分数、门禁和状态，不得在客户端重算信号进度、机会分或资格。
12. Redis/GraphQL subscription 只负责通知刷新；PostgreSQL、InfluxDB 与 QMT 回报才是对应事实真源。

## 5. 总体数据流

```text
Influx ticks / WholeQuoteHub
        │
        ├─ 顺序、时点、交易时段、连续代际、陈旧性
        ▼
MarketDataContext + 当前 Tick + point-in-time InstrumentProfile
        │
        ├─ 当前 RuntimeState + versioned SignalPolicy
        ▼
StrategyBase.step(StrategyInput)
        │
        ├─ RuntimeStatePatch：窗口、健康、双 FSM、episode、候选、当前评估
        └─ TradeIntent[]：仅候选已锁存且外部发意图门禁通过时产生
        ▼
Engine CAS 持久化 RuntimeState
        ├─ 物化 MATERIAL / COALESCED_DIAGNOSTIC 评估事件
        ├─ 重建 t_trade_global_monitor_projections
        ├─ 发布 tTradeUpdates 刷新通知
        └─ TradeIntent → 人工确认 → OrderSizer/Risk/Broker → QMT 回报
```

执行顺序必须保持：市场数据与上下文风控 → 仓位调节 → `StrategyBase.step()` → `TradeIntent` → OrderSizer → 订单风控 → Broker → 回报收敛 → RuntimeState/BucketLedger/ExitPlan。机会引擎只负责图中 `step()` 内的入场判断。

## 6. `MarketDataContext` 权威输入契约

### 6.1 必需字段

Engine 在构造 `StrategyInput` 时提供结构化 `MarketDataContext`，至少包含：

| 字段 | 类型 | 语义 |
|---|---|---|
| `source_time_ms` | `int64` | 行情源事件时间；所有窗口、停留和 TTL 的因果时间基准 |
| `tick_ordinal` | `int64` | 同一 source time/连接代际内的稳定顺序号 |
| `continuity_generation` | `int64` | WholeQuoteHub 明确判定连续性重建后递增的代际 |
| `quote_stale` | `bool` | 当前报价是否超过服务端陈旧阈值；与连续性丢失分开表达 |
| `session` | enum | 当前 A 股交易时段；午休、集合竞价和闭市不得靠本地时钟推断 |
| `trade_date` | `date` | 交易日历给出的交易日，不从设备本地日期猜测 |

建议同时携带只读的 `input_id`、`trace_id`、接收时间、行情字段完整性和数据源标识，但它们不能改变上述六个字段的语义。

### 6.2 Tick 身份与顺序

权威 source identity 为：

```text
(continuity_generation, source_time_ms, tick_ordinal)
```

- 身份相同的重复 Tick 必须幂等，不得推进 FSM、计时或重复生成评估。
- 同一代际中小于最后已消费身份的乱序 Tick 不进入状态窗口，只记录诊断计数。
- `trade_date` 变化时关闭前一交易日 episode，清空日内窗口并重新进入 `WARMING`。
- 策略不得调用 wall clock；回放同一输入序列必须得到字节级等价的状态与意图身份。

### 6.3 连续性与稀疏 Tick

只有 `continuity_generation` 明确变化，才判定实时行情连续性丢失。普通个股数秒甚至更久没有新 Tick 是市场稀疏性，不得因为“超过 N 秒无 Tick”就清空窗口或伪造 `CONTINUITY_LOST`。

代际变化时必须：

1. 当前输入先产生 `CONTINUITY_LOST` 物化评估；
2. 清空所有依赖连续 Tick 的样本和聚合；
3. 使尚未发意图的候选失效，并阻断旧候选确认；
4. 更新 RuntimeState 中的代际；
5. 下一有效输入进入 `WARMING`，直到所有强制窗口重新满足覆盖要求。

`quote_stale=true` 产生 `STALE`；它不递增代际，也不自动清空已有因果窗口。恢复新鲜报价后，只有窗口覆盖仍有效时才可直接回到 `READY`，否则进入 `WARMING`。

## 7. 信号域三层状态模型

信号域内部只有以下三层。交易批次/订单/退出是独立的第四层执行状态，不能复用或折叠到其中。

### 7.1 第一层：数据健康 `DataHealth`

```text
WARMING | READY | DEGRADED | STALE | CONTINUITY_LOST | INSUFFICIENT
```

| 状态 | 定义 | 是否可新建候选 |
|---|---|---|
| `WARMING` | 启动、交易日切换、代际变化或关键配置变化后，因果窗口尚在重建 | 否 |
| `READY` | 强制行情字段、画像时点、窗口覆盖和样本数全部满足当前策略版本 | 是 |
| `DEGRADED` | 可继续展示部分特征，但可选字段或质量指标下降，无法满足安全决策级别 | 否 |
| `STALE` | 当前报价被服务端判定陈旧 | 否 |
| `CONTINUITY_LOST` | Engine 明确宣告行情连续代际变化的物化过渡状态 | 否 |
| `INSUFFICIENT` | 必需输入、画像或可计算特征从根本上不足，继续等待也未必自动满足 | 否 |

状态优先级为 `CONTINUITY_LOST > STALE > INSUFFICIENT > WARMING > DEGRADED > READY`。服务端必须同时输出 `data_health_reasons[]`，不能只给一个颜色或枚举。

只有 `READY` 允许候选锁存。非 `READY` 时仍可在输入足够的部分上生成诊断分数，但必须把未知分量输出为 `null`，且硬门禁 `DATA_READY` 为失败。

### 7.2 第二层：并行形态 FSM

回撤和动量是两个并行分支，同一个 Tick 必须分别推进；系统不得用一个互斥枚举丢失另一分支的进度。

回撤分支：

```text
PullbackPhase =
  OBSERVING
  | PULLBACK_FORMING
  | LOW_STABILIZING
  | REBOUND_CONFIRMING
  | CANDIDATE_LATCHED
  | SUPPRESSED
```

推荐迁移语义：

1. `OBSERVING`：窗口可用但尚未形成有效回撤。
2. `PULLBACK_FORMING`：出现满足最小幅度方向的高点到低点结构，持续更新形态起点和极值。
3. `LOW_STABILIZING`：低点已形成，价格未再显著破低，累计低位稳定证据。
4. `REBOUND_CONFIRMING`：出现因果反弹，等待分数、持续时间和硬门禁共同确认。
5. `CANDIDATE_LATCHED`：本分支在当前 episode 已锁存候选。
6. `SUPPRESSED`：形态存在但过度延伸、数据失效、已有同 episode 候选或其他明确原因禁止发射。

动量分支：

```text
MomentumPhase =
  OBSERVING
  | BASELINING
  | MOMENTUM_BUILDING
  | ACCELERATING
  | OVEREXTENDED
  | CANDIDATE_LATCHED
  | SUPPRESSED
```

推荐迁移语义：

1. `OBSERVING`：尚未取得足够的短窗与基线数据。
2. `BASELINING`：因果构建同标的、同时段成交额/速度基线。
3. `MOMENTUM_BUILDING`：短窗涨幅、斜率或成交额开始增强但尚未确认。
4. `ACCELERATING`：价格与成交强度同步加速，进入候选确认区。
5. `OVEREXTENDED`：VWAP 溢价、末端距离或冲击成本超限，禁止追高。
6. `CANDIDATE_LATCHED`：本分支在当前 episode 已锁存候选。
7. `SUPPRESSED`：存在形态但被数据、重复、配置或外部条件抑制。

`dominant_phase` 只用于列表压缩展示，按候选状态、距候选阈值、形态进展和阻断严重度确定。它是可重建投影，不得覆盖 `pullback_phase` 或 `momentum_phase`，也不得作为后续交易输入。

### 7.3 第三层：候选生命周期 `CandidateStatus`

```text
NONE | LATCHED | AWAITING_APPROVAL | SUPPRESSED | REARMING
```

| 状态 | 含义 |
|---|---|
| `NONE` | 当前无锁存候选，两个 FSM 可继续形成 episode |
| `LATCHED` | 形态、分数、确认时间和机会硬门禁均满足，候选身份已原子锁存；尚未通过外部发意图门禁 |
| `AWAITING_APPROVAL` | 已为候选创建唯一 `TradeIntent`，等待人工确认、取消或 TTL 到期 |
| `SUPPRESSED` | 候选因重复、数据/策略变化、TTL、外部门禁或审批拒绝而不可继续发射 |
| `REARMING` | 已结束一次候选，等待机会分持续低于再武装阈值后允许新 episode |

候选状态不表示下单、已报、部分成交、已成交或退出。对应 `TradeIntent` 创建后，待确认事实以 `pending_entry_intent_id` 和 TradeIntent 持久化记录为准；确认后的委托与成交以订单/回报状态机为准；做 T 批次继续使用现有 `TTradeStatus`。

候选从 `LATCHED` 到 `AWAITING_APPROVAL` 的唯一动作是成功持久化一个带候选 fingerprint 的人工确认 `TradeIntent`。Universe/运行资格、账户并发批次、总 T 暴露和已有待处理意图由 Engine 提供为外部发意图门禁；失败时记录结构化 blocker，不得把真实账户数值写入 RuntimeState。

## 8. 特征、评分与门禁

### 8.1 因果特征

每个分支只使用当前 source identity 及之前的数据。特征至少分为：

- 行情结构：窗口高低点、回撤幅度、反弹幅度、斜率、持续时间、距高低点距离。
- 成交强度：短窗成交额、变化率、相对历史同时段画像的倍率、覆盖率。
- 定价位置：当日因果 VWAP 及偏离、日内区间位置、过度延伸程度。
- 微观流动性：买一/卖一完整性、价差 tick 数、盘口/成交有效性。
- 质量信息：样本数、有效覆盖秒数、代际、陈旧性和画像版本。

所有百分比在领域层使用小数，金额使用人民币元，价格使用人民币元，时间戳使用带时区的权威 source time。窗口样本必须有上界，不得保存全日无限 Tick。

### 8.2 机会分

两个分支分别计算 `pullback_score` 和 `momentum_score`，范围 0～100。`opportunity_score` 是当前选中路径的分数；当无路径具备最小可计算条件时为 `null`，不得用 0 代替。

分数由版本化 `score_contributions[]` 组成，每项包含：

```text
code, label, points, max_points, value, target, unit
```

回撤分支的贡献族至少包括回撤形成、低点稳定、反弹确认、VWAP 位置、成交确认和流动性质量；动量分支至少包括短窗涨速、持续性、成交额加速、距高点位置、VWAP 甜蜜区和流动性质量。具体权重属于 `policy_version`，上线前用严格因果回放校准；一期阈值只能作为 V3 初始搜索种子，不能把旧 AND 条件原样伪装成评分模型。

机会分只是排序与解释量，不得显示为“成功率”“上涨概率”或“预计收益”。

### 8.3 四条分数阈值

策略配置必须满足：

```text
0 <= rearm_score < preview_threshold < revalidate_score < candidate_threshold <= 100
```

- `preview_threshold`：进入 UI 重点观察区，仅影响展示和诊断采样，不产生候选。
- `candidate_threshold`：与分支确认时长、硬门禁和 episode 约束共同决定候选锁存。
- `revalidate_score`：人工确认时对尚未过期候选执行服务端重验；允许候选锁存后的正常小幅回落，但低于该值必须拒绝确认并抑制候选。
- `rearm_score`：候选结束后必须持续低于该值 `rearm_seconds` 才允许新 episode，形成迟滞，避免阈值附近反复触发。

分数等于阈值时按“达到”处理。阈值、确认秒数、最少确认 source identity 数、再武装秒数和候选 TTL 都进入策略版本与候选审计。

`policy_version=t_trade_opportunity_v3.0.0` 的初始校准种子冻结为：

| 项目 | 初始值 |
|---|---:|
| `preview_threshold` | 55 |
| `candidate_threshold` | 72 |
| `revalidate_score` | 60 |
| `rearm_score` | 45 |
| `confirm_seconds` | 2 秒 |
| `min_confirm_source_identities` | 2 |
| `rearm_seconds` | 15 秒 |

这些值是 walk-forward 的起始 policy，不是胜率或最终上线承诺；历史验证和 PAPER 验收完成后，实际 LIVE 值以冻结的新 `policy_version` 为准。

V3 初始贡献权重如下，硬门禁始终独立并具有绝对否决权：

| 路径 | 正向贡献（满分 100） | 诊断惩罚 |
|---|---|---|
| 回撤 | 回撤深度 25、反弹 20、低点稳定 15、转折斜率 10、VWAP 位置 10、流动性 10、量能 10 | 数据降级最多 -10；正 VWAP 溢价追涨最多 -20 |
| 动量 | 短窗涨幅 20、成交速度 20、斜率 15、高位保持 10、VWAP 强度 15、流动性 10、盘口 10 | 数据降级最多 -10；过度延伸最多 -30 |

最终分数裁剪到 `[0, 100]`。惩罚项必须作为独立 `score_contributions` 输出，不能藏在总分公式里；非 `READY` 即使诊断分数超过 72 也不能锁存 candidate。

### 8.4 硬门禁

硬门禁与分数正交：门禁失败时，只要数据仍可计算就继续输出诊断分数，但绝不创建候选。服务端输出：

```text
code, passed, detail, observed_value, required_value
```

机会引擎内的强制门禁至少包括：

1. `DATA_READY`：`DataHealth == READY`。
2. `TRADING_SESSION`：处于策略允许的连续竞价与收盘保护边界内。
3. `QUOTE_COMPLETE`：成交价、买一、卖一等该路径必需字段完整。
4. `SPREAD_ALLOWED`：价差不超过策略上限。
5. `WINDOW_COVERAGE`：样本数和有效覆盖达到路径最低要求。
6. `PATH_CONFIRMED`：选中 FSM 已进入可确认阶段并满足最短停留。
7. `NOT_OVEREXTENDED`：动量末端、VWAP 溢价或追价约束未超限。
8. `EPISODE_AVAILABLE`：当前 episode 未发过候选且未在再武装。
9. `POLICY_CURRENT`：使用中的 policy/config/feature schema 与 RuntimeState 一致。

Universe、忽略名单、持仓资格、可卖老仓、并发批次、总暴露、已有待确认意图属于外部 `INTENT_EMISSION_ALLOWED` 门禁。它们必须显示在同一诊断视图，但不得冒充数据健康、形态状态或由策略重新计算。OrderSizer/订单风控还会在人工确认后再次验证资金、数量、T+1、涨跌停、停牌和整手规则。

### 8.5 路径选择

两个分支都达到候选条件时按以下确定性顺序只产生一个候选：

1. 机会分更高者优先；
2. 分数相同，先达到 `candidate_threshold` 的 source identity 优先；
3. 仍相同，使用固定路径顺序 `PULLBACK_REBOUND` 后 `MOMENTUM_ACCELERATION`。

被选中路径写入 `selected_path`；另一分支继续保留自身状态用于诊断，但同一标的有待确认意图或活动入场时外部发意图门禁为失败。

## 9. Episode、候选锁存与再武装

### 9.1 Episode 身份

每个分支在形态起点确定时创建稳定 episode：

```text
episode_id = sha256(
  instrument_code
  | trade_date
  | path
  | pattern_start_source_identity
  | policy_version
)
```

`pattern_start_source_identity` 使用第一个确定形态起点的 `(continuity_generation, source_time_ms, tick_ordinal)`，后续极值更新不得随意改写起点。策略版本改变必须产生新 episode 语义，不能让旧策略候选穿越到新策略。

### 9.2 候选 fingerprint

```text
candidate_fingerprint = sha256(
  episode_id
  | confirmation_source_identity
  | feature_schema_version
)
```

同一 `episode_id` 最多创建一次 candidate；同一 fingerprint 最多创建一个 `TradeIntent`。重放、重试、Engine 崩溃恢复和 GraphQL 重复确认都必须依赖唯一约束与幂等键得到相同结果。

### 9.3 候选生成条件

某路径只有同时满足以下条件才从 `NONE` 原子进入 `LATCHED`：

1. `DataHealth == READY`；
2. 对应 FSM 处于确认阶段；
3. 所有机会硬门禁通过；
4. 分数在至少 2 个不同 source identity 上连续满足 `candidate_threshold`，且 source time 覆盖 `confirm_seconds`；
5. 当前 episode 未生成过 candidate；
6. 当前候选状态不是 `AWAITING_APPROVAL` 或 `REARMING`。

Engine 在同一幂等处理链中应用 RuntimeStatePatch、持久化 candidate fingerprint 并尝试创建 `TradeIntent`。不得先发布 UI 信号再补存候选，避免幽灵信号。

### 9.4 失效与再武装

以下事件使尚未成交的候选进入 `SUPPRESSED`，并使对应待确认入场意图失效：

- 候选 TTL 到期；
- 人工拒绝或取消；
- `continuity_generation` 变化；
- 交易日变化；
- policy/config/feature schema 变化且 `requires_rewarm=true`；
- Engine 检测到候选与当前持久化版本不一致；
- 外部发意图门禁在意图创建前失败。

候选结束后进入 `REARMING`。只有选中路径的机会分在同一连续代际内、连续合格评估中保持 `< rearm_score` 达到 `rearm_seconds`，才清除旧 episode 并回到 `NONE`。任一评估重新达到 `rearm_score` 会重置再武装计时。普通稀疏 Tick 不构成断流，也不得单独重置 episode；计时只依据权威 source time 和实际合格评估推进。

## 10. `StrategyBase.step()` 处理算法

V3 的唯一主路径可表达为：

```text
step(input):
  state = decode_and_validate(input.strategy_state)
  ctx = require_market_data_context(input)

  if duplicate_or_out_of_order(ctx.source_identity, state.last_source_identity):
      return StrategyOutput([], no_or_diagnostic_patch)

  if ctx.trade_date changed or ctx.continuity_generation changed:
      invalidate causal windows and unapproved candidate
      transition through CONTINUITY_LOST/WARMING

  samples = append_bounded_causal_sample(state.samples, input.market_data)
  health = evaluate_data_health(ctx, samples, point_in_time_profile)

  pullback = advance_pullback_fsm(previous, causal_features, health)
  momentum = advance_momentum_fsm(previous, causal_features, health)

  scores = calculate_versioned_contributions(pullback, momentum)
  gates = evaluate_opportunity_hard_gates(health, features, policy, episode)
  candidate = advance_candidate_lifecycle(scores, gates, episode, source_identity)

  intents = []
  if candidate newly LATCHED and input external emission gate allows:
      intents = [build_manual_entry_trade_intent(candidate_fingerprint)]
      candidate = AWAITING_APPROVAL

  patch = RuntimeStatePatch(set=current_bounded_state_and_evaluation)
  return StrategyOutput(trade_intents=intents, runtime_state_patch=patch)
```

同一个输入最多产生一个入场 `TradeIntent`。任何“不发意图”的路径也必须更新必要状态并给出结构化 blocker，使“不买、不能买、还没到、已经发过”可区分。

订单和成交回调只能用权威事件更新候选与入场关联的算法状态补丁，不得反向伪造行情特征。真实 BUY 首次成交后由 Engine 激活冻结参数的 `ExitPlan`；退出计划与批次继续走共享执行链路。

## 11. RuntimeState 结构与恢复

### 11.1 真源与建议载荷

可恢复算法状态的唯一真源仍是 `strategy_run_states.custom_state`，通过现有 CAS/version 机制更新。V3 在其中使用独立命名空间，例如：

```text
t_trade_opportunity_v3:
  state_schema_version
  feature_schema_version
  policy_version
  config_version
  continuity_generation
  trade_date
  last_source_identity
  bounded_samples / bounded_aggregates
  data_health / data_health_reasons
  pullback_tracker
  momentum_tracker
  episode_tracker
  candidate_tracker
  rearm_tracker
  current_evaluation_snapshot
```

其中只保存重启后继续因果判断所需的数据。样本窗口必须按最大 lookback、最大样本数和交易日三重裁剪；可以保存聚合就不重复保存无限原始样本。

`instrument_states[*].opportunity` 及候选发意图补丁不得写入：账户现金、总资产、真实持仓、可用/可卖数量、冻结数量、最终订单量、券商订单真相、批次盈亏或 ExitPlan 执行状态。

为了不改写既有执行链，外层 `instrument_state` 可保留 `pending_*_intent_id`、`batch_id`、`exit_plan_id` 等关联键，以及由 QMT 委托/成交回报和权威 ExitPlan 事件派生的有界执行投影。这些字段不是 PortfolioState、OrderState、BucketLedger 或 ExitPlan 真源，不可用于绕过 OrderSizer、资金、T+1、可卖量或订单风控。投影与外部真源冲突时，Engine 必须以外部真源覆盖并进入 reconcile，不得由策略自行猜测修正。

### 11.2 恢复规则

1. BACKTEST 从回放输入确定性构建状态，不从 LIVE/PAPER RuntimeState 读取历史窗口。
2. PAPER/LIVE 重启读取 `custom_state`，校验 schema、版本、trade date 和 continuity generation。
3. 恢复时丢弃晚于当前决策时点、早于最大 lookback 或身份乱序的样本。
4. schema 不兼容、关键字段缺失或配置要求重热时，不做静默兼容；清除 V3 机会状态并进入 `WARMING`。
5. CAS 冲突必须重新读取新版本再重算或幂等终止，不能 last-write-wins 覆盖较新候选。
6. RuntimeState 持久化失败时不得发布候选、创建意图或更新读投影。
7. 重启发现 `LATCHED` 无持久化意图、或 `LATCHED/AWAITING_APPROVAL` 与持久化意图身份不一致时，必须先以稳定恢复事件键写入 MATERIAL 审计，再通过正式 `OrderStateEvent` 抑制候选；已有不一致意图进入 `REJECTED/EXPIRED`，不得自动补发订单。
8. 已进入 `SUBMITTED/PARTIAL_FILLED/FILLED` 的意图继续交给既有 QMT inbox 和订单/成交回报收敛，不得按“孤儿候选”误抑制。恢复重试必须幂等，且只在 MATERIAL 审计成功后强制保存修复后的 RuntimeState。

## 12. 评估、画像、投影与交易事实真源

| 数据 | 权威存储/来源 | 可变性 | 责任 |
|---|---|---|---|
| 原始 Tick | InfluxDB `ticks` | 追加 | 回放与行情事实；不由 PostgreSQL 投影替代 |
| 算法当前状态 | `strategy_run_states.custom_state` | CAS 可变 | 窗口、数据健康、双 FSM、当前 episode/candidate/rearm |
| 机会评估 | `t_trade_opportunity_evaluations` | append-only | 可审计的状态跃迁、候选、门禁、分数和诊断证据 |
| 标的画像 | `t_trade_instrument_profiles` | 不可变版本 | 账户无关、point-in-time 的慢变量与同时段基线 |
| 候选结果 | `t_trade_candidate_outcomes` | 幂等累计后冻结 | 候选、入场/退出成交、费用可信度、MFE/MAE 与固定成熟窗标签 |
| 最新 UI 快照 | `t_trade_global_monitor_projections` | 可重建 | GraphQL 最新读模型；绝不作为决策真源 |
| 待确认/已确认入场 | TradeIntent 持久化记录 | 状态机 | `pending_entry_intent_id`、人工确认和失效真源 |
| 订单/成交 | QMT inbox 与收敛后的订单/成交表 | 追加+状态机 | 实盘执行真源；`command_ack` 只代表投递 |
| 做 T 批次/退出 | TTradeBatch、BucketLedger、ExitPlanBook | 状态机 | 成交后的批次、T+1 置换与自动退出 |

### 12.1 `t_trade_opportunity_evaluations`

该表只持久化两类事件，不允许逐 Tick 写 PostgreSQL：

- `MATERIAL`：数据健康/FSM/候选状态跃迁、episode 创建或关闭、候选建立/抑制/失效、意图关联、关键门禁变化、跨阈值、策略版本变化。
- `COALESCED_DIAGNOSTIC`：没有物化变化时按服务端窗口合并的最新诊断，默认每标的最多每 2 秒一条。

每条至少保存：`id`、唯一 `event_key`、`strategy_run_id`、`instrument_code`、`evaluated_at`、source identity、事件类型、双 FSM、候选状态、选中路径、数据健康及原因、特征快照、分数和贡献、门禁、blocker、episode/candidate/fingerprint、关联 intent、policy/config/feature/profile 版本。`event_key` 保证重试幂等；历史分页使用 `(evaluated_at, id)` 稳定游标。

评估物化器消费 `step()` 成功应用后的有效 RuntimeState、输入元数据和 TradeIntent 关联，不向 `StrategyOutput` 增加第三种结果。评估写入失败时不得声称候选已对外可见；重试必须由相同 event key 收敛。

### 12.2 `t_trade_instrument_profiles`

画像是账户无关、不可变的 point-in-time 事实，至少包含：

```text
instrument_code, as_of, profile, schema_version, version,
fingerprint, metrics, data_manifest
```

它用于慢变量、分时成交基线、流动性分位和其他无法仅靠数分钟 RuntimeState 稳定估计的特征。画像生成不得读取 `as_of` 之后的数据；策略评估只能选择 `as_of <= evaluated_at` 的最新兼容版本，并把 profile fingerprint 写入评估。画像缺失或 schema 不兼容时进入 `INSUFFICIENT`，不能偷偷退回全市场常数。

### 12.3 `t_trade_global_monitor_projections`

该表只保存当前列表/详情所需的最新快照，可由 RuntimeState、最后评估、TradeIntent 和批次事实重建。任何 resolver、Web 或 iOS 都不得把它写回策略或用作执行判断。Redis 事件和 `tTradeUpdates` 只携带“某账户/标的/版本已变化”的通知，客户端收到后 refetch。

### 12.4 `t_trade_candidate_outcomes`

结果表为诊断与后续机器学习提供独立、可追溯的标签，不参与当前候选生成、下单或风控：

- 以 `strategy_run_id + candidate_id` 唯一，候选 MATERIAL 评估建立 seed，记录账户、标的、路径和 policy/feature/profile 版本；
- LIVE 入场/退出成交只能由已持久化并收敛为 `APPLIED` 的 QMT 成交事实推进，`command_ack`、客户端状态或行情猜测均不得写成交；
- PAPER 模拟成交必须在同一 RuntimeState 检查点冻结到 manager-owned outbox，再幂等投影到候选结果；进程重启后只重放该冻结事实，不查询券商或重新推演。该事实仅用于 PAPER 分析标签，不是实盘成交真源；
- BACKTEST 在状态检查点之后按同一因果输入确定性更新候选结果，不读取 LIVE/PAPER 历史或券商状态；
- 入场目标量使用 OrderSizer/风控后的持久化请求事实，部分成交在达到权威目标前不得提前冻结观察窗；
- MFE、MAE 和固定窗口收益只使用 seed 之后的因果行情，费用后收益只有在费用事实权威且候选 cohort 完整时才可用；
- LIVE 重启时从 durable `APPLIED TRADE` 事件幂等修复漏写的分析投影；PAPER 重启时从 RuntimeState 中的冻结模拟成交 outbox 幂等修复。坏事件按事件隔离、记录有界失败统计并继续处理后续事实；任何跨账户、跨 run、跨标的或 candidate 错链的 intent 必须 fail-closed；
- 诊断以所有权威已成交候选为分母，要求每个候选恰有一个 outcome、存在入场成交且整个 cohort 均已成熟。任一缺失、重复、未成熟或费用不权威时，整组费用后表现返回 unavailable，禁止只聚合幸存子集。

## 13. 策略配置、版本与安全更新

### 13.1 `TTradeSignalPolicy`

配置必须是服务端有类型、可校验、可审计的对象，至少分为：

| 配置组 | 必需内容 |
|---|---|
| 数据质量 | 最大 quote age、各路径最小样本数、最小窗口覆盖、强制字段 |
| 通用门禁 | 允许交易时段、距收盘保护时间、最大价差 tick、候选 TTL |
| 回撤特征 | 回看窗、最小回撤、低点稳定、最小反弹、VWAP 约束、成交确认 |
| 动量特征 | 短窗、最小涨速/持续时间、基线窗、成交额倍率、基线覆盖、距高点和 VWAP 甜蜜区 |
| 分数 | 两路径贡献项权重、归一化边界、惩罚项、`preview_threshold`、`candidate_threshold`、`revalidate_score` |
| Episode | `confirm_seconds`、最少确认 source identity 数、`rearm_score`、`rearm_seconds`、同标的待确认限制 |

一期规格中的 300 秒回撤窗、0.8% 回撤、0.2% 反弹、15 秒稳定、60 秒动量窗、300 秒基线、2 倍成交额、80% 覆盖、VWAP 区间与 3 tick 价差，只作为首次回放搜索和对照的种子。V3 生产阈值和权重必须由同一 feature schema 的严格因果回放、PAPER 影子结果和版本记录确定；不得在代码中保留一套 UI 不可见的魔法默认值。

所有配置必须满足交叉校验，例如：

- `rearm_score < preview_threshold < revalidate_score < candidate_threshold`；
- 短窗不大于对应基线/回看窗；
- 覆盖率在 `[0, 1]`；
- 权重非负且每条路径归一化为 100；
- VWAP 下界不大于上界；
- TTL、确认和再武装时间为正且不跨越收盘保护边界；
- 数据质量门槛不能低于特征所需的最小因果数据量。

### 13.2 版本

| 版本 | 变化时机 | 用途 |
|---|---|---|
| `state_schema_version` | RuntimeState 结构变化 | 恢复兼容性；不兼容时重热，不写兼容分支 |
| `feature_schema_version` | 特征定义、单位、缺失语义变化 | 候选 fingerprint、回放和 ML 数据一致性 |
| `policy_version` | 任何影响判断的阈值、权重、门禁变化 | episode 身份和决策审计 |
| `config_version` | 全局监控配置每次成功保存 | 客户端乐观并发锁 |
| `profile.version/fingerprint` | 新画像物化 | point-in-time 特征来源审计 |

### 13.3 预览、保存与重热

GraphQL 提供纯校验预览：

```text
previewTTradeSignalPolicy(accountId, policy, expectedConfigVersion)
  -> errors, warnings, normalizedPolicy, changedFields, requiresRewarm
```

预览不得写数据库、改变 RuntimeState、跑完整回放或发布订阅。保存时 `SaveTTradeGlobalMonitorInput.expectedConfigVersion` 必填，首次创建传 `0`。版本冲突返回 `CONFIG_VERSION_CONFLICT` 和最新 monitor；不得静默覆盖，客户端保留用户草稿供比较或重新应用。

成功保存后 `config_version` 递增。特征窗口、状态阈值、评分权重、确认/再武装或数据健康规则变化时，Engine 必须原子执行：

1. 新 policy 生效并生成新 `policy_version`；
2. 对相关标的清空不兼容的 V3 窗口、episode 与未发射候选；
3. 使旧版本待确认入场意图失效；
4. 进入 `WARMING` 并物化配置变化评估；
5. 保留已经真实成交的批次、BucketLedger 和冻结参数 ExitPlan，继续安全退出。

## 14. GraphQL 权威读写契约

本节是本轮原子切换的权威读写契约。实现必须在同一批次同步替换 Python schema/resolver、公开 SDL、Web 生成类型与 iOS Apollo operations，不保留旧 signal-history 双协议。

### 14.1 最新信号快照

在现有 `TTradeSession` 增加非客户端计算的 `signalSnapshot: TTradeSignalSnapshot`，替代 `latestEvaluation/currentSignal` 的展示职责。建议结构：

```graphql
type TTradeSignalSnapshot {
  evaluatedAt: DateTime!
  sourceAt: DateTime!
  sourceTimeMs: String!
  tickOrdinal: String!
  continuityGeneration: String!
  dataAgeMs: Int
  windowCoverageSeconds: Int
  sampleCount: Int!

  dataHealth: TTradeSignalDataHealth!
  dataHealthReasons: [TTradeSignalReason!]!
  pullbackPhase: TTradePullbackPhase!
  momentumPhase: TTradeMomentumPhase!
  dominantPhase: TTradeDominantPhase!
  selectedPath: TTradeSignalPath

  pullbackScore: Float
  momentumScore: Float
  opportunityScore: Float
  previewThreshold: Float!
  candidateThreshold: Float!
  revalidateThreshold: Float!
  rearmThreshold: Float!

  hardGates: [TTradeSignalGate!]!
  scoreContributions: [TTradeScoreContribution!]!
  topBlockers: [TTradeSignalBlocker!]!

  episodeId: ID
  candidateId: ID
  candidateFingerprint: String
  candidateStatus: TTradeCandidateStatus!
  candidateCreatedAt: DateTime
  candidateExpiresAt: DateTime
  pendingEntryIntentId: ID

  stateSchemaVersion: String!
  featureSchemaVersion: String!
  policyVersion: String!
  configVersion: Int!
  profileVersion: String
  profileFingerprint: String
}
```

枚举值与第 7 节完全一致，客户端遇到未知枚举必须显示“版本不兼容/未知状态”并保守禁用确认，不得映射成 `READY`、`NONE` 或零分。所有不可计算数值使用 `null`；GraphQL resolver 不用 `0`、空字符串或当前时间兜底。

三个大整数在 GraphQL 边界按十进制字符串传输，避免标准 GraphQL `Int` 的 32 位限制；客户端只用于身份、展示和顺序比较，不做浮点转换。

`TTradeSignalGate`、`TTradeScoreContribution`、`TTradeSignalBlocker` 和 reason 必须有稳定 `code` 供测试/筛选，并有服务端生成的用户可读 `label/detail`。客户端可翻译 label，但不得改变 passed、points 或 observed value。

### 14.2 历史与诊断

历史查询：

```text
tTradeSignalEvaluations(
  accountId,
  stockCode?,
  eventKinds?,
  startTime?,
  endTime?,
  after?,
  first
): TTradeSignalEvaluationPage!
```

- 默认优先返回 `MATERIAL`，用户进入诊断详情时再取合并诊断。
- 游标由 `(evaluated_at, id)` 编码，排序稳定。
- 返回原始服务端分数、门禁、贡献和版本，不从当前配置回算历史。

候选全链路追溯使用唯一三键：

```text
tTradeCandidateTrace(accountId, strategyRunId, candidateId)
  -> sourceIdentity, integrityStatus, missingReasons, links, events
```

`candidate_id` 不承担全库全局唯一语义；resolver 与 repository 必须在同一查询中同时限定账户、策略运行和候选，随后再限定标的。时间线只串联该作用域内的 evaluation、TradeIntent、委托、成交、批次和 ExitPlan；缺失的预期节点与异常断链分别显示，不得用另一运行中的同名候选补齐。

聚合查询：

```text
tTradeSignalDiagnostics(
  accountId,
  stockCode?,
  startTime,
  endTime
): TTradeSignalDiagnostics!
```

至少返回：READY 标的分钟/小时、形态进入次数、preview 跨越、candidate、TradeIntent、确认、下单、成交漏斗，主 blocker 排名，分数分布，双 FSM 停留时间，候选过期/拒绝/重复抑制、完整 cohort 的成交后表现，按 policy/feature/profile 版本分组。漏斗分母不得用原始 Tick 数伪造高样本量；不同版本默认分区，不得把不可比较版本直接相加。

### 14.3 配置 mutation

- `previewTTradeSignalPolicy`：纯校验与规范化。
- `saveTTradeGlobalMonitor(input)`：要求 `expectedConfigVersion`，成功返回新 monitor/version。
- `CONFIG_VERSION_CONFLICT`：返回最新服务端配置，绝不自动合并交易参数。
- 人工确认继续复用 TradeIntent 审批契约；确认时服务端重验 candidate TTL、fingerprint、policy/config version、最新分数不低于 `revalidate_score` 和最新外部门禁。

### 14.4 实时刷新

复用 `tTradeUpdates` 作为通知订阅。服务端按账户/标的最多约 2 秒合并高频诊断通知，以下事件立即通知：

- 数据健康、任一 FSM、候选状态变化；
- 跨越 preview/candidate/rearm 阈值；
- candidate/TradeIntent/批次关联变化；
- policy/config 版本变化；
- 关键 blocker 变化。

通知只携带变更身份和 projection version；Web/iOS 收到后 refetch `TTradeSession.signalSnapshot`。断线重连先 refetch，再恢复订阅，不能用漏掉的推送推断当前状态。

### 14.5 权限与生成代码

所有 query/mutation/subscription 必须走当前账户授权与 Caddy 同源 `/graphql`。schema、resolver 和前端 operation 在同一实施批次原子切换；不得保留新旧字段双写或 `as any` 兼容。Web 必须运行仓库规定的 GraphQL codegen/check/lint/test/build；iOS 必须重新生成 Apollo types 并通过 Swift 测试。

## 15. Web 前端设计

### 15.1 设计原则

沿用 QuantX 现有深色交易工作台、字号层级、卡片、表格和 A 股涨跌色语义，不另起视觉体系。页面目标是让用户先看结论，再看原因，最后才进入参数和原始评估。

前端严禁继续在 `monitoring.ts` 一类工具中用当前 Tick 拼接条件或计算 `conditionProgress`。服务端 `signalSnapshot` 是分数、状态、门禁与 blocker 的唯一展示来源。

### 15.2 信息架构

现有 `/t-trade` 与 `TTradeGlobalPage` 保留，页内模式调整为：

```text
总览 | 信号 | 诊断 | 做T仓位 | 订单事件 | 参数
```

- **总览**：全局运行状态、数据健康分布、待确认信号、活动批次、关键风险。
- **信号**：所有持仓的当前最新机会快照与单标的 inspector。
- **诊断**：漏斗、blocker、分数分布、FSM 停留和历史评估。
- **做 T 仓位**：已有批次/库存置换/退出计划，继续使用执行事实，不混入候选状态。
- **订单事件**：TradeIntent、委托、成交与拒绝审计。
- **参数**：有类型的 signal policy 编辑、预览、版本冲突处理和重热提示。

### 15.3 信号看板

桌面表格最少列：

1. 股票代码/名称与当前持仓标识；
2. 最新价与服务端 source time；
3. 数据健康及首要原因；
4. dominant phase 与选中路径；
5. 机会分 / candidate threshold；
6. 第一 blocker；
7. 候选/待确认状态；
8. 关联批次状态。

排序默认：`AWAITING_APPROVAL` → `LATCHED` → 越过 preview → 距 candidate 最近 → 其他 READY → 非 READY。排序键由服务端字段组成，不能本地推演形态。

状态必须同时使用文字、图标和颜色，例如“数据陈旧 · 12 秒”“回撤 · 反弹确认”“机会分 72/78”；不得只用红绿圆点。机会分组件标注“规则机会分，不是概率”。

### 15.4 单标的 inspector

建议按下列顺序展示：

1. **结论条**：是否可候选、选中路径、机会分/阈值、第一 blocker、source time。
2. **价格上下文**：价格、VWAP、窗口高低点、价差和相关阈值。
3. **分数趋势**：机会分随 source time 的趋势，并画 preview/candidate/revalidate/rearm 四条线；`revalidate` 只在候选待确认时强调，缺失段断线显示，不补零。
4. **双 FSM**：回撤与动量两条并行轨道，显示当前阶段、进入时间和最近跃迁。
5. **硬门禁**：passed/failed、观测值、要求值和说明。
6. **分数贡献**：points/max points、原始值与目标；合计与服务端机会分一致。
7. **数据健康**：代际、source age、覆盖秒数、样本数、profile 版本和 reasons。
8. **候选与执行**：episode、candidate fingerprint、TTL、pending TradeIntent、人工确认、批次/ExitPlan 链接。
9. **审计历史**：MATERIAL 事件优先，可展开 COALESCED_DIAGNOSTIC。

前端只做数值格式化、筛选和视图状态，不做分数求和来替代服务端总分；若贡献合计与总分因四舍五入不同，显示服务端总分。

### 15.5 诊断页

诊断页至少包含：

- 漏斗：eligible → data ready → pattern → preview → candidate → TradeIntent → confirmed → filled；
- blocker 排名：按 READY 标的小时、episode 或 MATERIAL 事件为分母；
- 机会分分布：按路径与 policy version 分组；
- 双 FSM 停留时间和转移矩阵；
- 候选结果：过期、拒绝、确认、成交、重复抑制与失效原因；
- 单标的时间线：评估、TradeIntent、订单、成交、批次和 ExitPlan 串联。

诊断必须明确时间范围、样本分母和版本。不同 feature/policy 版本默认不合并；用户主动合并时给出“规则版本不同”提示。

### 15.6 参数编辑

参数页使用分组表单，不允许用户直接编辑无 schema 的 JSON。交互流程：

1. 载入配置与 `configVersion`；
2. 本地只做输入类型/范围即时提示；
3. 点击“验证配置”调用 `previewTTradeSignalPolicy`；
4. 展示 errors、warnings、normalized values、changed fields 和 `requiresRewarm`；
5. errors 清零后才允许保存；
6. 保存携带 `expectedConfigVersion`；
7. 冲突时保留草稿，并并排显示服务端新值；
8. 需要重热时明确提示旧待确认信号会失效，但已成交批次与退出计划不受影响。

首期由 Web 提供完整编辑；iOS 只读策略摘要，避免在小屏上误改高风险参数。

### 15.7 响应式与无障碍

- 宽屏保持列表 + inspector 双栏；中屏切换抽屉；窄屏改为纵向信号卡片，核心字段不得依赖横向滚动。当前 Windows 验收只覆盖桌面宽度（已检查 1920/1366）；移动布局是后续范围，不作为本轮门禁。
- 图表必须有同数据的可读摘要/表格、轴标签和数值单位；键盘可访问 tooltip 与筛选。
- 所有状态变化有 `aria-live` 但对 2 秒刷新去抖，避免读屏轰炸。
- 交互焦点、对比度、触控目标、错误关联符合现有设计系统与 WCAG 2.1 AA。
- 尊重 `prefers-reduced-motion`，FSM 跃迁和分数更新不使用强制动画。
- 数值正负不只依赖红绿；“READY/STALE”等文本始终可见。

## 16. iOS SwiftUI 设计（后续计划；当前 Windows scope-waiver）

本节保留目标 iOS 设计与未来验收契约。本轮不实现、不生成、不编译、不运行 iOS 相关代码；§19 当前 Windows 完成定义不引用本节门禁。

### 16.1 页面结构

保留现有原生 SwiftUI `TTradeAssistantView` 与 QuantX 设计系统，不嵌 WebView。做 T 助手内分段建议为：

```text
监控 | 批次 | 信号 | 门禁 | 控制
```

- **监控**：运行状态、数据健康、待确认信号和活动批次摘要。
- **批次**：现有 TTradeBatch/退出状态。
- **信号**：持仓信号卡片与单标的详情。
- **门禁**：失败门禁、blocker 与数据健康诊断。
- **控制**：现有启停/授权，首期只读 policy/version 摘要。

### 16.2 信号卡与详情

持仓卡至少显示：dominant phase、机会分/阈值、数据健康、第一 blocker、source time 和候选 TTL。进入详情后依次展示：结论、Swift Charts 价格/VWAP 与分数、两条垂直 FSM、硬门禁、分数贡献、状态跃迁、TradeIntent/批次/退出审计。

Swift Charts 缺失值必须断开，不能以零点连线；图表提供 VoiceOver summary。动态字体放大后卡片纵向展开，不截断 blocker 或关键风险文案。红绿语义同时配图标与文字。

### 16.3 数据与实时

- `TTradeControlRepository`/对应新 repository 查询 `signalSnapshot`、历史和门禁。
- 增加 `tTradeUpdates` subscription；通知后合并 refetch，不把推送 payload 当真源。
- 进入前台、网络恢复、订阅重连时先全量 refetch。
- 刷新失败时保留最后一个可信快照并明确显示“数据可能已过期”，禁止基于旧 snapshot 确认。
- 未知枚举、feature schema 不兼容或缺失关键版本时进入只读失败态。
- 不在本地持久化完整特征快照、候选 fingerprint 历史或任何券商敏感信息。

### 16.4 配置边界

首期 iOS 不提供完整策略参数编辑，只显示当前 policy/config/feature version、核心阈值和最近生效时间。若后续支持编辑，必须复用服务端 preview、版本锁、重热说明和本地生物认证，不能在 App 内实现另一套校验。

## 17. 可观测性与效果指标

### 17.1 运行指标

至少提供以下指标。Prometheus 在线序列只使用固定枚举的 path、health、detail 与 Engine instance 等低基数标签；`policy_version` 保留在 evaluation/outcome 和离线诊断分区中，不得作为运行时序列标签造成基数无界增长：

- 输入、重复、乱序、显式 continuity generation 变化计数；
- `READY` 标的分钟/小时与非 READY 原因时长；
- FSM 转移计数与停留时长；
- preview/candidate/rearm 阈值跨越；
- episode、candidate、重复抑制、TTL 失效和外部门禁失败；
- RuntimeState CAS 冲突、评估物化重试、projection 落后量；
- subscription 合并率和客户端 refetch 错误。

Engine 内存累计器必须同时限制活动 stream 数和 metric series 数，使用 LRU/overflow 计数暴露丢弃情况；API 只有在整个 heartbeat snapshot 的 schema、容量、枚举和值全部合法时才导出，不能静默截取前 N 条形成不完整指标。

### 17.2 产品效果指标

效果评价不能只看“信号数量”。至少按 READY 标的小时报告：

- episode 与 candidate 率；
- 候选到人工确认、意图到成交的转化率；
- 被拒绝/过期/门禁阻断的结构化原因；
- 成交后净手续费的 MFE、MAE、固定窗口收益与批次最终净收益；
- 路径、股票流动性分组、时段、policy/profile 版本的稳定性；
- 固定规则一期基线与 V3 的差异。

未成交候选和未触发 episode 同样是训练/诊断样本；不得只保留成功交易造成幸存者偏差。

## 18. 测试设计

### 18.1 领域单元测试

必须覆盖：

1. 同一输入序列在 BACKTEST/PAPER/LIVE 上的 evaluator 结果一致。
2. 重复 Tick 幂等、乱序 Tick 不推进、相同 source time 依赖 ordinal 稳定排序。
3. 普通稀疏 Tick 不触发 continuity lost；代际变化必清连续窗口并重热。
4. trade date 切换、午休、集合竞价、收盘保护的状态行为。
5. 六种 `DataHealth` 的优先级、reason 和 fail-closed 行为。
6. 回撤与动量 FSM 可同时推进，dominant phase 不改变分支状态。
7. 每个状态转移的边界值、回退、过度延伸和抑制。
8. score contribution 合计、阈值等号、`null` 缺失语义和门禁/分数正交。
9. 两路径同时满足时的确定性选择。
10. episode 起点稳定、每 episode 最多一 candidate、fingerprint 可重放。
11. `NONE/LATCHED/AWAITING_APPROVAL/SUPPRESSED/REARMING` 全转移及 TTL。
12. 再武装低于阈值持续、被中断重置、稀疏评估和 source time 行为。
13. RuntimeState 序列化/恢复/裁剪/schema 不兼容重热。
14. 策略不输出账户数量计算，不产生 SELL FSM 或额外信号对象。
15. 属性测试：任意输入前缀的输出不因追加未来 Tick 而改变；同 episode candidate 数量永不大于 1。

### 18.2 Engine 与持久化测试

- `RuntimeStatePatch` CAS 成功后才物化评估、projection 与通知。
- CAS 冲突、进程崩溃和重试不重复 candidate、评估或 TradeIntent。
- `event_key` 唯一，MATERIAL 不丢，诊断按窗口合并且不逐 Tick 落库。
- 重启恢复覆盖 `LATCHED` 无 intent、`LATCHED/AWAITING_APPROVAL` 与持久化 intent 不一致，以及正常 `SUBMITTED/FILLED` 继续由 inbox 收敛；任何恢复路径都不自动下单。
- 候选结果修复覆盖 poison event 后仍能处理合法成交、跨 scope intent fail-closed、重复修复幂等，以及缺失/重复/未成熟 outcome 使整个成交 cohort unavailable。
- projection 可从 RuntimeState + evaluation + TradeIntent + batch 重建，删除投影后不影响决策。
- profile 查询严格执行 `as_of <= evaluated_at`，不存在未来画像回退。
- 配置保存乐观锁、冲突返回、`requiresRewarm`、旧待确认意图失效。
- 重热保留已成交批次、BucketLedger 和 ExitPlan。
- 外部 Universe/运行资格变化只阻断 intent 发射，不污染三层信号状态。
- Redis 丢通知、重复通知和订阅重连均可通过 refetch 收敛。
- QMT `command_ack` 不推进成交；委托/成交回报幂等收敛。

### 18.3 回放与回测测试

- 原始 Tick 回放严格按全市场 source identity 排序，多标的不可逐股票各自跑完整天后拼接。
- 固定输入、参数、画像和版本得到相同 episode/candidate/TradeIntent identity。
- 覆盖跌停、涨停、停牌、无盘口、零成交、极稀疏、午休、断流重连和行情修订样本。
- 对照一期 AND 规则时，主比较只使用同一 Tick、同一 lineage 上双方均为 READY 的共同暴露分母，报告共同 READY 标的小时、candidate rate 差异、主 blocker、MFE/MAE 和费用后结果；双方各自 READY 的原始计数只作描述，禁止用不同分母直接声称 V3 提高或降低触发率。
- 任何调参只在训练窗口进行，随后用时间外窗口验证；测试窗口不得反向改阈值。

### 18.4 GraphQL/API 测试

- 最新 snapshot 的 null、枚举、版本与候选/intent 关联映射。
- 历史稳定游标、时间范围、event kind 和股票筛选。
- 诊断聚合分母与版本分组。
- 候选追溯严格使用 `accountId + strategyRunId + candidateId`，跨账户/跨运行同名候选不可见。
- preview 纯读无副作用、save version conflict、未授权账户拒绝。
- subscription 只通知、2 秒合并、断线重连后的 refetch。
- schema 与 Web/iOS 生成类型原子切换，无旧字段兼容分支。

### 18.5 Web 测试

- `signalSnapshot` 到列表、排序、inspector、诊断的纯映射。
- 删除 `conditionProgress` 后不存在客户端机会分/门禁计算。
- `null` 显示“不可计算”，0 显示真实零值。
- 双 FSM、门禁、贡献、TTL、版本冲突和重热警告。
- subscription 重复/丢失/重连、最后可信快照与 loading/error 状态。
- 1920/1366 px 桌面响应式；键盘、读屏、对比度、reduced motion。移动 Web 断点与 phone-browser compatibility 不属于当前 Windows 验收。
- 图表与等价表格使用同一服务端数据。

### 18.6 iOS 测试（后续门禁；不属于当前 Windows 完成定义）

本节测试清单继续作为后续 macOS/iOS 交付门禁；本轮只验证 iOS V3 scope-waiver 与残留引用清理，不将 Swift/Xcode 验收结果计入 Windows 交付。

- GraphQL enum/model 映射和未知枚举失败态。
- 信号卡、详情、双 FSM、门禁和贡献的 snapshot tests/单元测试。
- Apollo subscription 通知合并、前后台切换、网络恢复与 refetch。
- 旧 snapshot 明确过期并禁用确认。
- Dynamic Type、VoiceOver、深浅外观和小屏布局。
- iOS 首期不出现可写的完整 policy 表单。

## 19. 验收标准

### 19.1 契约验收

- [x] 行情决策路径只有 `StrategyBase.step(StrategyInput)`。
- [x] V3 使用的策略输出只有 `TradeIntent[] + RuntimeStatePatch`，仓库无新增 `Signal/generate_signal/on_tick/on_bar` 主路径。
- [x] V3 opportunity RuntimeState 与候选补丁不含现金、持仓、可卖量、冻结量或最终订单量；外层回报派生投影不参与合法数量判断。
- [x] 三层信号状态与 `TTradeStatus`、订单、ExitPlan 完全独立。
- [x] Universe/运行资格只作为外部 intent 发射门禁。
- [x] 当前 Windows Web 桌面客户端对分数、门禁、资格零重算；iOS 同等验收属于后续 scope，不阻塞本轮。

### 19.2 正确性验收

- [x] 同一 episode 在崩溃、重放、重试和重复 Tick 下最多一个 candidate、一个 fingerprint 对应最多一个 TradeIntent。
- [x] 普通稀疏 Tick 不被误判断流；显式 generation 变化 100% 触发窗口失效与重热。
- [x] 非 `READY` 不产生新 candidate；未知值使用 `null`。
- [x] 两个 FSM 并行推进，历史可还原每次跃迁、分数贡献、门禁和 blocker。
- [x] 配置变化需要重热时，旧待确认意图全部失效，已成交批次/ExitPlan 无一丢失。
- [x] 回放无未来画像、未来 Tick 或跨测试窗口调参。
- [x] QMT 回报仍是成交唯一真源。

### 19.3 性能与可靠性验收

- [x] PostgreSQL 不逐 Tick 写评估；COALESCED_DIAGNOSTIC 达到合并窗口约束，MATERIAL 全量保留。
- [x] 最新列表使用读投影，不对每行执行独立历史查询；不存在 N+1。
- [x] RuntimeState 和 evaluation 写入失败时不发布幽灵候选。
- [x] Redis/订阅中断、静默丢通知、重复通知或订阅重连后，Web 桌面端将通知仅视作失效提示，并通过重连、前台/网络/可见性恢复及 30 秒审计的 `network-only` refetch 回拉数据库真源；重复版本和错误会合并，避免 refetch 风暴。iOS 对应验证留在后续 §18.6。
- [ ] **BLOCKED（未达 SLO）**：交易时段全持仓压力试验下，Engine 延迟、CAS 冲突率和数据库写入量尚未满足或证明满足现有服务 SLO。固定 9,600 输入的全持仓负载在约 4.15% 完成度时因旧执行路径过慢而取消，未产生可验收的延迟、CAS 与写入量基线；必须完成性能补丁后重跑，才可冻结机器基线或判定 PASS。

### 19.4 前端验收

- [ ] 用户在一个屏幕内可回答“现在有没有信号、还差多少、被什么阻止、数据是否可信”。
- [ ] 信号、待确认意图、订单、成交、批次在文案和视觉上不混淆。
- [x] Web 1920/1366 px 桌面范围无核心信息横向溢出，键盘焦点、读屏语义与 reduced motion 可完成查看和配置预览；移动 Web 不额外扩展。
- [ ] iOS Dynamic Type/VoiceOver 可读，网络失效时明确旧数据且不能误确认（后续 iOS 门禁，本轮 Windows 不计入）。
- [x] 参数冲突不覆盖草稿，重热影响在保存前清楚呈现。

### 19.5 上线验收

CANARY 与 LIVE 都是实际执行阶段。进入任一阶段前，当前 Windows 范围内的以下门禁均须完成；服务端必须对任一缺项 `fail-closed`，不得仅依赖客户端文案或人工记忆。iOS 自动化与无障碍验收仍属于后续 Phase 4，不作为当前 Windows 门禁。

1. 全量领域/Engine/API/Web（当前仅桌面范围）自动化测试；iOS 自动化测试属于后续 Phase 4，不阻塞本轮 Windows 交付；
2. 至少 20 个交易日严格因果历史回放，覆盖正常与异常行情日；
3. PAPER 连续运行至少 5 个交易日且完成不少于 20 个候选生命周期；
4. episode 重复率为 0，幽灵候选为 0，未来数据违规为 0；
5. 每个候选都能从当前 Web UI 追溯到 source identity、profile/policy/feature 版本、贡献、门禁、TradeIntent 和后续结果；iOS UI 追溯属于后续门禁；
6. 用户确认策略阈值、频率、主 blocker 与费用后结果满足预期后，才进行单账户、有限标的、有限暴露的 LIVE 灰度。

> **当前上线判定（2026-08-23）：BLOCKED。** 服务端 V3 rollout evidence 硬门禁已接入 CANARY/LIVE 激活链路。当前正式严格因果历史回放为 `0/20` 个交易日，PAPER 为 `0/5` 个连续交易日且已完成候选生命周期为 `0/20`，`operator_review=false`。对 17 个 D-1 窗口进行的审计覆盖 706 个唯一 `instrument-day`，没有任何一个窗口能为全部持仓提供完整 `20/20` 个交易日的可用因果数据。因此 CANARY 与 LIVE 均不得激活；诊断样本、固定负载或任何合成压力样本均不得记作历史回放。

收益不作为单次上线的硬保证；验收关注因果正确、可解释、可恢复、安全和统计口径可信。

## 20. 迁移实施计划

V3 采用一次权威契约、分阶段实现、最终原子切换。不得长期双写旧信号协议或保留客户端旧进度兜底。

### Phase 0：规格与基线冻结

- 冻结本规格、通用交易契约、旧规则对照数据集和当前线上指标。
- 记录一期每条 AND 条件的触发率、交集率、主阻断和费用后结果。
- 定义 feature/policy/state schema 的首个版本，不在代码中散落未版本化常量。

### Phase 1：纯领域机会引擎

- 增加结构化 `MarketDataContext`。
- 实现有上界的因果特征、DataHealth、双 FSM、评分、硬门禁、episode/candidate/rearm。
- 将 `ashare_intraday_t_assistant` 的入场判断迁入唯一 `step()`；保持 TradeIntent/ExitPlan 执行契约。
- 完成领域单元、属性和确定性回放测试。

### Phase 2：持久化与 Engine

- 增加 opportunity evaluation/profile 表与 repository。
- 扩展 `custom_state` V3 schema、CAS 恢复、评估物化和 projection 重建。
- WholeQuoteHub 提供 continuity generation/source identity；外部 universe 发射门禁接入。
- 完成崩溃恢复、幂等、压力与数据质量测试。

### Phase 3：GraphQL 与 Web（Windows 当前交付按桌面范围）

- 原子替换最新 signal snapshot、历史、诊断与配置 preview/save 契约。
- Web 增加信号/诊断视图，删除客户端 `conditionProgress` 和旧字段推断。
- 运行 codegen、check、lint、test、build 与桌面范围无障碍/响应式验收；移动 Web 不额外扩展。

### Phase 4：iOS（后续计划；当前 Windows 不执行）

- 在 macOS 上更新 Apollo operations/generated types、repository、models、store 和 SwiftUI 视图；本轮不新增或修复 iOS V3 源码。
- 接入通知/refetch、未知枚举和旧数据保护。
- 完成单元、UI、Dynamic Type 与 VoiceOver 验收。

### Phase 5：回放校准与 PAPER

- 以一期阈值为搜索种子，按时间切分校准权重与阈值。
- 在固定验证窗口对比一期规则，不因验证结果回调训练参数。
- PAPER 影子观察并完成上线验收；任何变更生成新 policy version。

> 当前状态（2026-08-23）：**BLOCKED**。正式历史回放尚为 `0/20`，不能以 17 个 D-1 窗口审计、`INSUFFICIENT_SAMPLE` 闭环、诊断样本或压力样本替代；PAPER 尚为 `0/5` 个连续交易日、`0/20` 个完成候选生命周期。

### Phase 6：原子切换与 LIVE 灰度

- 停止旧入场机会生成，V3 成为唯一权威。
- 旧 RuntimeState 不做字段兼容：schema bump 后进入 `WARMING`。
- 所有旧版本未确认信号/入场意图失效；已成交 TTradeBatch、BucketLedger 和 ExitPlan 原样保留至安全退出。
- 当前 Windows 交付完成 Web/API 同批切换并删除旧 `latestEvaluation/currentSignal/conditionProgress` 查询与兜底；iOS 同批切换留待 Phase 4，不作为本轮门禁。
- 单账户小范围灰度，满足监控窗口后再扩大到全部合格持仓。

> 当前状态（2026-08-23）：**BLOCKED**。CANARY/LIVE 已由服务端 rollout evidence 硬门禁保护；在正式回放、PAPER、候选追溯质量、操作人审查和有限暴露配置均满足前，任何激活请求均应 `fail-closed`。

### 回滚边界

上线前完成数据库预升级备份与 restore-verify，保留 schema revision 兼容的上一稳定应用制品；不运行新旧规则双写。不得自动或默认执行破坏性 Alembic downgrade。若 V3 必须回滚：

1. 停止产生新入场意图；
2. 保留并继续执行所有真实成交后的批次与 ExitPlan；
3. 使 V3 尚未确认意图失效；
4. 仅在 schema revision 兼容时回滚应用并重新从 `WARMING` 开始；schema 不兼容时使用已验证备份 restore 或前向修复，不执行默认 downgrade；
5. 不把 V3 RuntimeState 强行解释为一期状态。

## 21. 文件级实施清单

以下是本轮实现和验收的文件边界；实际模块名以仓库中已落地的唯一实现为准。

### Domain / Application

- `packages/domain/src/quantx_domain/strategies/base.py`：结构化 `MarketDataContext` 接入唯一 `StrategyInput`。
- `packages/domain/src/quantx_domain/strategies/ashare_intraday_t_assistant.py`：V3 唯一 `step()` 编排与状态补丁。
- `packages/domain/src/quantx_domain/trading/t_trade.py`：opportunity 枚举、DTO、policy 与候选身份；不得新增旧 `Signal`。
- `packages/domain/src/quantx_domain/trading/t_trade_opportunity_engine.py`：独立纯模块承载 feature/DataHealth/FSM/scoring/episode/candidate/rearm。
- `packages/application/` 对应端口/用例：画像读取、评估物化、外部发意图门禁和版本更新编排。

### Infrastructure / Engine

- `packages/infrastructure/src/quantx_infrastructure/models/`：evaluation/profile 模型与 migration。
- `packages/infrastructure/src/quantx_infrastructure/repositories/`：append-only evaluation、point-in-time profile repository。
- `packages/infrastructure/src/quantx_infrastructure/models/t_trade_global_monitor_projection.py` 与 `services/t_trade_monitor_projection_service.py`：可重建最新 snapshot。
- `packages/infrastructure/src/quantx_infrastructure/services/t_trade_opportunity_runtime_service.py`：CAS 后评估物化、幂等关联与诊断窗口。
- `apps/engine/src/quantx_engine/strategy_executor.py`：MarketDataContext、恢复、审批重验、持久化顺序与通知。
- `apps/engine/src/quantx_engine/t_trade_global_monitor.py`：universe/运行资格外部门禁与配置重热。
- `apps/engine/src/quantx_engine/t_trade_coordination.py`：账户级配置切换与人工审批共享互斥，消除保存/确认竞态。

### API / Web

- `apps/api/src/quantx_api/gqlapi/types/t_trade_types.py`：snapshot/history/diagnostics/policy types。
- `apps/api/src/quantx_api/gqlapi/schemas/t_trade_schema.py`、`resolvers/t_trade.py`：query/mutation/subscription resolver。
- `apps/web/src/features/portfolio/hooks/useTTradeGlobal.ts`：GraphQL documents、通知/refetch 和 mutation。
- `apps/web/src/features/portfolio/pages/TTradeGlobalPage.tsx`：六模式信息架构。
- `apps/web/src/features/portfolio/pages/t-trade-global/TTradeLiveMonitor.tsx`：服务端 snapshot 看板/inspector。
- `apps/web/src/features/portfolio/pages/t-trade-global/monitoring.ts`：删除客户端业务计算，只保留展示映射。
- 同目录新增/拆分 diagnostics、policy editor、FSM、gate、score components，并配套现有测试目录。

### iOS（后续计划；本轮 Windows 不纳入文件边界）

- `apps/ios/QuantX/GraphQL/Operations/TTradeControls.graphql`：snapshot/history/diagnostics/subscription operations。
- `apps/ios/QuantX/Core/GraphQL/TTradeControlRepository.swift`：通知/refetch 和读模型。
- `apps/ios/QuantX/Features/Assistants/TTradeControlModels.swift`、`TTradeControlStore.swift`：未知枚举、旧数据与版本状态。
- `apps/ios/QuantX/Features/Assistants/TTradeAssistantView.swift`：监控/批次/信号/门禁/控制及详情。
- `apps/ios/QuantXTests/TTradeControlRepositoryTests.swift`、`TTradeControlStoreTests.swift` 与 UI tests：契约、实时、无障碍。

### 本轮实施与验收状态（2026-08-23）

| 范围 | 当前实施状态 | 验证证据 / 剩余门禁 |
|---|---|---|
| Domain / Strategy | V3 规则引擎、因果特征/DataHealth、双 FSM、episode/candidate/rearm、状态补丁递归账户事实防线与唯一 `step()` 接入已落地。 | Python 修复后全量为 `3119 passed, 16 skipped, 0 failed`。 |
| Infrastructure / Engine | evaluation/profile/projection、持久化、CAS、权威行情 lineage、审批重验、账户级配置互斥、崩溃恢复、幂等关联与结果未知（result unknown）收敛已落地。D-1 画像已改为“Influx 主 `time` keyset + 严格 source identity/storage-time 映射校验”的有界流式、每分钟压缩、`fail-closed`。 | D-1 画像专项已通过（主代理独立 4 文件 `47 passed`）；画像缓存、诊断窗口、终态意图缓存、结果修复游标与结果归档查询继续保持全局或分页硬界限。 |
| GraphQL / API | V3 snapshot/history/diagnostics/preview/save、candidate trace、结果口径和低基数 telemetry 已原子替换；发布 SDL 不再含旧 signal-history；控制链路的 `beginTTradeControlledWindow(accountId, policyVersion, snapshotId, idempotencyKey)` 与 `activateTTradeLive(accountId, policyVersion, snapshotId, idempotencyKey, targetStage, confirmation)` 均以 `policyVersion + snapshotId` 绑定确认上下文。CANARY/LIVE 激活前的 V3 rollout evidence 服务端硬门禁已落地并 `fail-closed`。 | Caddy `/health/live=200`；公开 `/graphql` 已确认新 V3 queries/types 及两项控制 mutation 的完整签名；Web operation 已同步传递两种版本身份，避免重启后客户端与 authoritative schema 漂移。当前 evidence 为正式回放 `0/20`、PAPER `0/5` 与 `0/20` 生命周期、`operator_review=false`，故 CANARY/LIVE 不可激活。 |
| Web（当前仅桌面范围） | 六模式信息架构、信号 inspector、诊断、policy editor、冲突草稿、candidate trace、客户端 scope/版本信任边界与订阅 refetch 已落地。订阅通知只作失效提示；静默丢通知、重复通知、重连、前台/网络/可见性恢复以及 30 秒审计均触发受合并保护的 authoritative `network-only` 回拉。 | 已在 1920/1366 桌面检查总览/信号/诊断/参数纯预览/回放报告：无横向溢出、console 0 errors，键盘焦点、读屏语义与 reduced motion 通过；参数服务端纯校验通过，未执行任何写/交易动作。移动 Web 不额外扩展。 |
| iOS（当前 Windows scope-waiver） | 本轮不开发、不生成 Apollo types、不运行 Xcode/SwiftUI 验证；已按 hunk 清理 V3 手写源码、GraphQL operations、V3 测试与缺失 generated symbol 引用；并行 watchlist 与用户原有 iOS 改动保留。 | Windows 无 Xcode；iOS codegen/xcodebuild、Dynamic Type、VoiceOver 与可编译原子性全部留待 Phase 4，且不属于当前 Windows 完成门禁。 |
| 控制 / 迁移 / 文档质量 | TTrade 控制链路与文档构建已完成专项验证；开发库已按授权完成 `0028 → 0031`。 | 控制专项 `282 passed`；迁移专项 `6 passed`；迁移 `0029/0030/0031` 已增加对开发库预创建完整空表的严格 schema 验证与采用，局部或不匹配 `fail-closed`；迁移前自动备份记录于 `F:\Workspace\quantx\.runtime\backups\20260823T104409Z`，迁移后 schema head 为 `20260823_0031`。随后按 `full/live` 重启验收：9 个受管组件均 RUNNING，`liveTrading=ENABLED`，QMT Agent `ready`、协议 `1.1`，快照新鲜（<90s）。混合工作树 Alembic 唯一 head 为 `20260823_0031`；V3 可提交迁移链独立止于 `20260823_0030`，明确不纳入并行 watchlist `0031`；docs build passed；Ruff 对 146 个变更 Python 文件通过；`diff-check` 无 whitespace errors。 |
| 回放 / 上线 | V3 漏斗、blocker/FSM/版本分组、成熟 cohort 结果口径、同源同 Tick READY 基线与因果诊断报告已落地；CANARY/LIVE rollout evidence 硬门禁已接入服务端。 | **BLOCKED**：现有 4 个闭环均为 `INSUFFICIENT_SAMPLE`，不算 V3 20 日验收；17 个 D-1 窗口、706 个唯一 `instrument-day` 审计未找到任一全持仓 `20/20` 数据窗口，正式严格因果回放为 `0/20`。PAPER 为 `0/5` 个连续交易日、`0/20` 生命周期；`operator_review=false`。固定 9,600 输入全持仓压力试验约运行至 4.15% 即因旧路径过慢取消，SLO 不得判 PASS。CANARY/LIVE 不得激活。 |

#### 未完成 / 阻塞门禁（不得声称完成）

- 发布快照已完成：官方 `npm run docs:contracts` 已刷新发布 SDL，`tests/api/unit/test_client_contracts.py` 9 passed。
- 运行端点已刷新并确认：Caddy `/health/live=200`，公开 `/graphql` 已确认新 V3 queries/types 及 `begin`/`activate` 完整签名（两者均要求 `policyVersion + snapshotId`）；不再保留“运行中仍是旧签名”的表述。
- 公开端点 codegen 后的 `npm run check`、`npm run lint`、`npm run test:run`（100 files/512 tests）、`npm run build` 全通过；Web mutation 变量与当前运行 schema 的 `policyVersion + snapshotId` 已同步。
- Web 桌面端已验证：Redis/GraphQL subscription 仅作失效提示；静默丢通知下也会以 30 秒 `network-only` 审计回拉服务端真源，重连与重复通知受到合并保护。1920/1366 无横向溢出，键盘焦点、读屏语义与 reduced motion 已通过；参数服务端纯校验通过，未执行任何写/交易动作。移动 Web 不额外扩展；iOS Apollo codegen/xcodebuild（Windows 无 Xcode，必须 macOS）及 Dynamic Type/VoiceOver 验证属于后续 Phase 4，未纳入当前门禁。
- 正式 20 交易日严格因果回放为 **BLOCKED（`0/20`）**：17 个 D-1 窗口、706 个唯一 `instrument-day` 审计没有任何全持仓 `20/20` 可用窗口。诊断样本、固定负载或合成压力样本不得称作或替代历史回放。
- PAPER 为 **BLOCKED（`0/5` 连续交易日、`0/20` 完成候选生命周期）**；其结果、重复/幽灵/未来数据质量和候选追溯不得提前声称验收。
- 压力 SLO 为 **BLOCKED**：固定 9,600 输入全持仓负载在约 4.15% 时因旧执行路径过慢取消；性能补丁和完整重跑前，不得冻结基线或声明延迟、CAS 冲突率、数据库写入量达标。
- CANARY/LIVE 为 **BLOCKED**：服务端 rollout evidence 硬门禁已落地，当前正式回放/PAPER 不足且 `operator_review=false`；不得激活 CANARY 或 LIVE。

机器学习仍保留为下一阶段，不计入本轮 V3 已完成范围。

## 22. 机器学习后续计划

### 22.1 前置条件

机器学习不取代本轮规则引擎。只有同时满足以下条件才启动模型开发：

- V3 evaluation/profile schema 稳定并有版本；
- 正样本、未确认候选、未触发 episode 和 blocker 负样本都可追溯；
- 结果标签按固定成熟时间生成，包含费用后收益、MFE、MAE、是否成交等；
- 数据能按时间、标的和 policy/feature/profile version 做严格切分；
- 已完成至少一个足够覆盖行情状态的 PAPER/LIVE 观察周期。

### 22.2 第一阶段：离线 challenger

- 直接复用 V3 因果 feature schema，禁止模型另建无法审计的未来特征。
- 使用 walk-forward/滚动时间验证，不做随机打散交叉验证。
- 与 V3 规则基线比较排序质量、校准、稳定性、换手和费用后结果。
- 模型产物必须包含 `model_version`、`feature_schema_version`、训练窗口、data manifest、as-of、fingerprint 和可复现指标。

### 22.3 第二阶段：在线影子

在线影子输出只保存：模型分数或校准概率、`would_trigger`、规则/模型一致性、model/version/as-of。UI 明确标注“仅观察，不参与候选、确认、排序或交易”；当前 V3 前端不预留一个空的“AI 信号”入口。

影子模型不得：

- 创建/抑制 candidate；
- 绕过 DataHealth、机会硬门禁或外部发意图门禁；
- 改变 TradeIntent、人工确认、OrderSizer、风控或 ExitPlan；
- 用未成熟标签回填在线决策。

### 22.4 可能的受控升级

只有 walk-forward 与影子验收持续优于规则基线，且漂移、校准和回滚机制成熟后，模型才可作为 candidate 之间的排序或规则分数校准器。即使升级：

- `StrategyBase.step()` 仍是唯一入口；
- 输出仍只有 `TradeIntent[] + RuntimeStatePatch`；
- 所有硬门禁、episode 防重复、人工确认和交易风控保持；
- 规则引擎保留为可审计的安全基线和一键回滚路径。

## 23. 明确不做

- 不为了提高触发率简单删除数据质量、流动性或追高门禁。
- 不把机会分解释为概率或承诺收益。
- 不逐 Tick 向 PostgreSQL 写完整评估。
- 不让 Web/iOS 依据行情自行判断 READY、路径或门禁。
- 不在 RuntimeState 保存账户交易事实或无限 Tick。
- 不为旧未部署协议增加双写、兼容 DTO 或降级字段。
- 不把 candidate、TradeIntent、订单、成交和 TTradeBatch 合并成一个“信号状态”。
- 不在 V3 稳定前让机器学习参与任何交易决策。

## 24. 完成定义（按交付平台分层）

### 24.1 当前 Windows 交付

本轮 Windows 交付在以下条件满足时才算完成：领域契约、RuntimeState、评估/画像/投影、GraphQL/API、Web 桌面范围、回放与自动化测试按同一版本原子落地；旧客户端计算和旧信号主路径已删除；任意一个候选都能从 source identity 追溯到因果特征、三层状态、双 FSM、分数贡献、硬门禁、episode、policy/profile/feature 版本、TradeIntent 和后续 QMT 执行事实。移动 Web 不额外扩展，iOS V3 不在本轮完成定义中；§16、§18.6 与 Phase 4 是后续计划，不是当前 Windows 门禁。`apps/ios` 必须不再引用 V3 专属缺失 Apollo symbols，同时保留并行 watchlist 与用户原有改动。

### 24.2 后续 iOS 交付

iOS 完整交付仍需在 macOS 完成 §16 的 SwiftUI 设计、Apollo codegen/生成物、编译原子性、§18.6 的 Swift/UI/Dynamic Type/VoiceOver 测试，并与服务端/Web 契约原子切换；这些条件在后续 Phase 4 完成前不计入 Windows 交付，也不得反向扩大本轮范围。
