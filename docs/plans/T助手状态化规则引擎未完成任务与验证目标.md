# T 助手状态化规则引擎：未完成任务与验证目标

> 状态：当前开发迭代收口清单；不是生产验收通过证明。
>
> 更新：2026-08-25
>
> 适用范围：持仓做 T 有状态机会引擎 V3，以及其 RuntimeState、回放与运行时持久化链路。

## 1. 本轮已完成边界

本轮已经完成的代码设计与实现边界如下；它们不等同于下文所有长时、外部环境和实盘验收均已通过。

1. **行情真源、内存状态与持久化投影分离**：权威行情缓存/历史行情存储只保存一次行情；QMT Agent 只采集/上报实时行情和回报。BACKTEST 只读服务端已有缓存/历史存储，LIVE Engine 消费已接入实时流/热缓存。`RuntimeState` 在进程内保留策略运行所需的全量状态；T 助手的行情窗口和样本只保留在内存。边界持久化使用紧凑投影，不再把行情窗口当作可恢复数据库状态写入。
2. **普通 Tick 零持久化、材料事实保留**：`USES_T_TRADE_OPPORTUNITY_PROFILE` 的普通 Tick 不生成 `DecisionTrace`、普通 evaluation、`COALESCED_DIAGNOSTIC` 或 observation JSON。候选/FSM 转换、信号创建/失效、连续性/策略/kill switch、外部入场、退出策略与 `TradeIntent` 等材料事实仍按原有事实链路持久化，不能因热状态内存化而丢失。
3. **按运行模式的检查点策略**：PAPER/LIVE 的普通热路径不写数据库，在 11:35、15:05 的边界进行持久化；BACKTEST 以交易日为批次持久化。顶层 compact RuntimeState 只保留一份，`runtime_checkpoints` 只保留一个有界元数据记录；其中 `PREPARED` 以顶层 material outbox + manifest 物化，`SEALED` 以顶层状态 fingerprint 验证。不保留 samples、完整输入 roots 或嵌套 state payload。
4. **可恢复边界协议**：使用 `PREPARED → materialize → FINALIZE`，并通过可识别的完成收据和恢复逻辑处理边界中断。`PREPARED` 必须校验当前顶层状态和 outbox/manifest；`SEALED` 必须校验当前顶层紧凑状态 fingerprint；损坏或不匹配一律进入 continuity/warming 的 fail-closed 路径，不能回滚到嵌套 payload。
5. **热路径减负**：状态同步采用根增量与边界全量捕获；材料 trace 与评估事件使用紧凑索引/物化路径；SQL 写入采用分块批量操作。完整审计是“trace index + 权威 material evidence + source archive”的可验证、可重建证据，不是每个 Tick 内联完整快照。

除本节已明确记录为完成的门槛外，其余回归、外部数据覆盖和实盘验收仍须完成，才能成为可宣称的生产验收结果。

### 1.1 本轮已完成验证

以下结果已在本轮收口前完成，故不再列为未完成项：

1. 第 3.2 节列出的核心聚焦组合（含机会 repository、runtime、replay 测试）退出码为 0。
2. `tests/engine/unit/test_strategy_market_event_backpressure.py` 全文件 31 项通过。
3. `tests/engine/unit/test_strategy_executor.py -k session_checkpoint` 的 6 项通过。
4. 本轮 Python 白名单的 Ruff 检查通过，`git diff --check` 无错误。
5. 会话协调器已经修复在检查状态或重试窗口之前查询交易日数据库的问题，避免该查询进入普通热路径。
6. 主审最终扩大回归已通过 **13 个文件、410 项**（原 11 个目标文件，加 `test_backtest_result_storage.py` 与 `test_strategy_market_event_backpressure.py`）。

上述结果只覆盖本轮代码回归与静态检查，不替代尚未完成的真实历史数据覆盖、完整根边界套件或实盘验收。

### 1.2 已完成：固定 9,600 Tick 语义压力验收

本机忽略的证据文件为
`docs/reports/t-trade-v3-pressure-semantic-final-9600-20260824.{md,json}`，不作为本轮
提交物。该次合成非历史压力运行状态为
`EXECUTED_SYNTHETIC_NON_HISTORICAL`：`timed_out=false`、回放 `COMPLETED`、耗时
`94.812122s`；请求 `9,600` Tick，实际有效处理 `9,472` Tick，另有 `128` Tick 按
`14:57` 政策过滤。Engine 与 durable accounting 均通过。

该 fixture 没有 profile 或候选，因此其正确的普通路径语义为 `MATERIAL=0`、
`DecisionTrace=0`、evaluation=`0`、ordinary diagnostic durable rows=`0`；这不是
漏记，而是普通行情观察只保留 source identity、watermark/count 与版本化策略后可重放的
契约。材料事实仍须在出现候选/FSM/连续性/政策/配置/profile 或显式 callback 时按
`event_key` 精确对账。

- Tick p50/p95/p99 为 `4.9641/7.31051/9.000282 ms`，吞吐为 `99.902837 Tick/s`，strategy p95 为 `3.22552 ms`。
- state upsert p50/p95/max 为 `508.8617/1318.5096/1520.5289 ms`，snapshot max 为 `2722.2428 ms`；commit 仍有外部数据库同步长尾，max 为 `3232.9278 ms`，不再归因于大状态投影。
- 实际数据库 `custom_state` 为 `29,320 B`，主要由 `instrument_states=23,854 B`、bucket=`4,389 B`、checkpoint=`893 B` 构成；无 `samples`、`state_payload`、`runtime_events` 或 pending outbox。

若同一 formal runner 的整体退出码为 `1`，其原因是 `formal20day` 仍为 `BLOCKED`；不得将其误写为上述固定 9,600 Tick 压力失败。

### 1.3 已实现：正式 20 日真实行情准备链路

正式 runner 已增加独立的 `--prepare-canonical-tick-archive` 模式。它固定读取一个
D-1 账户快照及其后恰好 20 个已完成的上交所交易日，通过已登记 QMT Agent 只读
获取 XTData 历史 Tick；每个标的按最多 7 个日历日拆分请求，对完整范围独立采集
两遍，并逐个 instrument-day 比对记录数、内容 SHA-256 和首尾 source identity。
只有 180/180 范围完全一致后，才会发布不可变 canonical archive token，并对归档
重新执行会话边缘、连续性、最小记录数和 source identity 门禁。准备模式不访问交易
Broker，不运行 PAPER/LIVE，也不允许 Influx、QMT 或其他数据源在正式执行时补数。

行情只按真实交易日与实际数据事实验收。正式门禁不再要求用户声明或人为划分任何
行情场景；窗口内所有真实行情统一接受同一套因果性、完整性和身份校验。

同时已修正 durable ingestion 的目的地路由：canonical archive 请求在 CLI 或 Worker
恢复路径中都只能进入归档准备器，不能落入普通 Influx 持久化。准备前还会检测会
永久阻塞 Agent 串行队列的既有 ingestion 失败，并立即 fail-closed。

## 2. 未完成项与验收目标

| 优先级 | 未完成项 | 当前事实 | 必须达成的验证目标 |
| --- | --- | --- | --- |
| P0 | 20 个已完成交易日严格因果历史回放 | 正式范围已固定为账户 `300000013250` 的 `2026-07-21` D-1 快照、`2026-07-22` 至 `2026-08-18` 的 20 个上交所交易日、9 个持仓、180 个 `instrument-day`。现有数据库会话覆盖为 171/180，另有 67 个 legacy instrument-day 缺少完整 source identity。真实 QMT 重采链路已实现并实际启动，但尚未获得 canonical token，正式回放未执行。 | 先解除第 3.3 节记录的 durable ingestion 阻塞；随后完成双重 QMT 采集、180/180 内容与身份一致、归档质量门通过，并以生成的唯一 token 执行正式严格因果 BACKTEST。缺失或不一致必须保守阻断。 |
| P0 | 最近 5 个已完成交易日回放（替代 PAPER） | 用户已同意以历史数据回放替代本轮 PAPER 连续运行；该替代验收尚未完成。 | 排除当前交易日后，完成最近 5 个已完成交易日的因果回放，达到 5/5 数据与结果对账；若验收工具仍保留该门槛，还须至少覆盖 20 个候选生命周期。该结果是 **PAPER 的替代验收**，不得表述为实际 PAPER 连续运行。 |
| P1 | 根边界与外部服务环境复验 | 本轮聚焦回归已完成，但 `python -m pytest tests/` 根边界套件尚未作为收口门槛完整执行。部分依赖外部服务的套件在 PostgreSQL 未配置时可能无法建立运行条件。 | 在 PostgreSQL 等外部服务配置完整的环境中运行根边界套件并复验受影响项，分别记录基础设施不可用与真实测试失败；不得将环境失败静默计为跳过或通过。 |
| P1 | Dev `full/live` 最终重启验收 | 本轮按收口指令不执行完整 Dev 重启，也不启动真实交易验收。 | 未来经授权后，用统一运维入口启动并检查状态：`profile=full`、`agentMode=live`、`liveTrading=ENABLED`、唯一账户、QMT Agent `ready`、协议 `1.1`、行情快照新鲜度小于 90 秒。若运行时预检失败，只能报告 `DEGRADED / BLOCKED` 与 `liveTrading=DISABLED`，不得伪装为 `ready`。 |
| P0（外部授权） | LIVE 灰度 | 尚未执行。 | 必须由用户重新明确授权真实交易后才可开始。灰度前须满足 QMT `ready`、协议 `1.1`、唯一允许账户、账户与风控白名单、订单/成交回报由 QMT Agent 持久化 inbox 后收敛为唯一真源；`command_ack` 不能被当作成交。 |

## 3. 完成记录与后续验证

### 3.1 固定 9,600 Tick 压力验收（已完成）

固定规模验收已经按第 1.2 节完成。记录必须以请求/有效 Tick、过滤原因、Engine
instrumentation、durable watermark/count、source identity/range 为 Tick 账本，并只对
实际材料 `event_key` 做 trace/evaluation 精确对账；普通 T Tick 的零行 trace/evaluation
不得被误判为漏记，也不得用 480 Tick 等小基准替代该已完成的固定规模结果。

### 3.2 已完成的聚焦回归与未来根边界测试

本轮已完成下列链路覆盖的聚焦测试，命令退出码为 0：

```powershell
uv run pytest `
  tests/application/test_t_trade_v3_application.py `
  tests/engine/unit/test_strategy_base.py `
  tests/engine/unit/test_strategy_executor.py `
  tests/engine/unit/test_t_trade_opportunity_runtime.py `
  tests/engine/unit/test_t_trade_v3_acceptance.py `
  tests/engine/unit/strategies/test_ashare_intraday_t_assistant.py `
  tests/infrastructure/test_runtime_state_manager_snapshot.py `
  tests/infrastructure/test_runtime_state_manager_v3_recovery.py `
  tests/infrastructure/test_t_trade_opportunity_intelligence_repository.py `
  tests/infrastructure/test_t_trade_opportunity_runtime_service.py `
  tests/infrastructure/test_t_trade_replay_service.py `
  -q
```

另已通过：

```powershell
uv run pytest tests/engine/unit/test_strategy_market_event_backpressure.py -q
uv run pytest tests/engine/unit/test_strategy_executor.py -k session_checkpoint -q
```

其中前者为 31 项，后者为 6 项。本轮 Python 白名单 Ruff 检查及 `git diff --check` 亦已通过。

尚未完成的是：在外部服务依赖可用时执行根边界测试：

```powershell
python -m pytest tests/
```

若该命令受 PostgreSQL 等外部服务影响，必须将“环境不可用”和“测试失败”分别记录；恢复环境后复跑，不能省略。

### 3.3 历史数据窗口与 5 日替代 PAPER 验收

本轮已把 20 日范围固定为：

- D-1 快照：`2026-07-21`；
- 交易日：`2026-07-22` 至 `2026-08-18`，恰好 20 个已完成上交所交易日；
- 持仓：9 个可回放标的，共 180 个 instrument-day；
- 数据来源：真实 XTData，经唯一登记 QMT Agent durable transfer 获取，`synthetic=false`。

实际执行准备命令为：

```powershell
python -m quantx_engine.t_trade_v3_acceptance `
  --account-id 300000013250 `
  --trading-days 20 `
  --prepare-canonical-tick-archive `
  --snapshot-date 2026-07-21 `
  --canonical-tick-archive-root F:\Workspace\quantx\.runtime\canonical-tick-archives\t-trade-v3-20260721
```

2026-08-25 的首次尝试曾被既有请求
`3c945173-939f-4fdc-89ed-36b91d6f345a` 阻塞。根因是旧读回规则错误地要求当前
Agent 上传摘要与请求窗口内的全部 Influx 存量完全相等；这会把盘中实盘写入或
此前已入库的真实行情误判成当前收盘同步的污染。

现已切换为合并式验收：收盘同步只需证明本次上传的每个键均已写入，允许同一
窗口保留额外既有行情；上传键缺失、Tick 存储键不一致、越界、重复、非有限数值
和读回查询失败仍然拒绝。验证使用最多 2,000 键的有界批次重新读取不可变上传
清单，不把整次 Tick 请求加载进内存。运行中的恢复流程已用新规则将上述请求及
其后两条排队请求收敛为 `COMPLETED`；上述请求验收了 600 个标的/周期组，识别
5 条既有行情，本次上传、保存和验证记录数均为 0。当前设备没有 durable ingestion
阻塞。

解除摄取阻塞后已重新执行上述 canonical 准备命令。两遍 QMT 获取各完成 36 个
durable 请求、读取 764,033 条真实 Tick，且两遍对已有文件的内容校验一致；但
质量门禁仍拒绝发布 archive，因为固定 180 个 instrument-day 中有 9 个没有 Tick：

- `002027.SZ`：`2026-07-22`；
- `605499.SH`、`688552.SH`、`688577.SH`、`689009.SH`：`2026-07-22`、
  `2026-07-23`。

上述 9 个 instrument-day 在 Influx Tick 中同样为 0 行，但对应 `1d` 日线均有
非零成交量、成交额且 `suspend_flag=0`，因此它们是当前可用真实 Tick 数据的覆盖
缺口，不是无交易日，也不能作为空日合并。两遍一致只证明 QMT 返回稳定，不能把
缺失数据变成合格数据。

正式验收状态现在是 `BLOCKED_REAL_TICK_COVERAGE`：没有发布 canonical cutover
token，没有执行正式 20 日 BACKTEST，也没有生成 rollout PASS。继续完成该固定
窗口必须补充上述 9 个 instrument-day 的权威真实 Tick；替换因果窗口或外部数据源
会改变正式验收范围，不能静默执行。5 日替代 PAPER 仍按独立门禁执行，不得由
20 日准备结果或合成压力代替。

### 3.4 未来的 Dev `full/live` 验收

仅在用户重新授权运行时执行：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 status
```

如需重启，应先按仓库运维约束执行 `down`，不得绕过统一入口单独启动 QMT Agent。

## 4. 明确排除项

1. **Web 与 iOS**：本轮未开发 Web 或 iOS 功能。iOS 受 Windows 环境和用户明确要求排除；不运行 Xcode、SwiftUI、Apollo iOS 或移动端验证。当前未发生 API/GraphQL 契约变更，因此无须为本轮执行前端 codegen；这不表示前端功能已实现。
2. **真实交易**：本轮不执行 LIVE 灰度、下单、成交验证或任何真实交易动作；该项必须以未来的明确授权为前提。
3. **尚未完成的长时/外部验证**：固定 9,600 Tick 压力已完成，不再作为本轮待办；既有 durable ingestion 阻塞已经解除，20 日真实行情准备也已实际重跑，但因 9 个有成交日缺少真实 Tick 而未发布 archive。正式回放和 5 日替代 PAPER 均未完成，不能因双取数稳定、压力通过、摄取恢复或准备代码完成而省略。
4. **数据库迁移**：0028 → 0031 已在此前处理完成，不列为本轮未完成项，也不因本文件重复迁移。
5. **无关工作区改动**：本轮提交只应包含状态化规则引擎和本文件对应的已审核改动；不得吸收 iOS、Web、QMT、迁移基线、临时报告或其他用户工作。

## 5. 收口结论

正式 20 日真实行情的准备与验证链路已经实现，固定窗口也已确定，既有 durable
ingestion 阻塞已经解除，canonical 准备也已真实执行；当前唯一直接的数据准备
阻塞是 9 个有成交 instrument-day 缺少真实 Tick。由于没有 canonical token，正式
20 日回放和 5 日替代 PAPER 均没有通过。在第 2 节剩余 P0 门槛、外部数据覆盖和
用户授权的 LIVE 门槛完成前，**仍不能宣称原始生产验收已经完成**。固定 9,600
Tick 合成压力结果不能替代这里的任何真实行情验收结果。
