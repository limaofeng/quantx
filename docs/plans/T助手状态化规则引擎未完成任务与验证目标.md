# T 助手状态化规则引擎：回放与运行时修复记录

> 状态：当前开发迭代的工程记录；不定义发布、CANARY 或 LIVE 验收门禁。
>
> 更新：2026-08-25
>
> 适用范围：持仓做 T 有状态机会引擎 V3，以及其 RuntimeState、回放与运行时持久化链路。

## 1. 本轮已完成边界

本轮已经完成的代码设计与实现边界如下：

1. **行情真源、内存状态与持久化投影分离**：权威行情缓存/历史行情存储只保存一次行情；QMT Agent 只采集/上报实时行情和回报。BACKTEST 只读服务端已有缓存/历史存储，LIVE Engine 消费已接入实时流/热缓存。`RuntimeState` 在进程内保留策略运行所需的全量状态；T 助手的行情窗口和样本只保留在内存。边界持久化使用紧凑投影，不再把行情窗口当作可恢复数据库状态写入。
2. **普通 Tick 零持久化、材料事实保留**：`USES_T_TRADE_OPPORTUNITY_PROFILE` 的普通 Tick 不生成 `DecisionTrace`、普通 evaluation、`COALESCED_DIAGNOSTIC` 或 observation JSON。候选/FSM 转换、信号创建/失效、连续性/策略/kill switch、外部入场、退出策略与 `TradeIntent` 等材料事实仍按原有事实链路持久化，不能因热状态内存化而丢失。
3. **按运行模式的检查点策略**：PAPER/LIVE 的普通热路径不写数据库，在 11:35、15:05 的边界进行持久化；BACKTEST 以交易日为批次持久化。顶层 compact RuntimeState 只保留一份，`runtime_checkpoints` 只保留一个有界元数据记录；其中 `PREPARED` 以顶层 material outbox + manifest 物化，`SEALED` 以顶层状态 fingerprint 验证。不保留 samples、完整输入 roots 或嵌套 state payload。
4. **可恢复边界协议**：使用 `PREPARED → materialize → FINALIZE`，并通过可识别的完成收据和恢复逻辑处理边界中断。`PREPARED` 必须校验当前顶层状态和 outbox/manifest；`SEALED` 必须校验当前顶层紧凑状态 fingerprint；损坏或不匹配一律进入 continuity/warming 的 fail-closed 路径，不能回滚到嵌套 payload。
5. **热路径减负**：状态同步采用根增量与边界全量捕获；材料 trace 与评估事件使用紧凑索引/物化路径；SQL 写入采用分块批量操作。完整审计是“trace index + 权威 material evidence + source archive”的可验证、可重建证据，不是每个 Tick 内联完整快照。

其余回归与外部数据覆盖用于验证实现质量，不改变 CANARY/LIVE 的授权状态。

### 1.1 本轮已完成验证

以下结果已在本轮收口前完成，故不再列为未完成项：

1. 第 3.2 节列出的核心聚焦组合（含机会 repository、runtime、replay 测试）退出码为 0。
2. `tests/engine/unit/test_strategy_market_event_backpressure.py` 全文件 31 项通过。
3. `tests/engine/unit/test_strategy_executor.py -k session_checkpoint` 的 6 项通过。
4. 本轮 Python 白名单的 Ruff 检查通过，`git diff --check` 无错误。
5. 会话协调器已经修复在检查状态或重试窗口之前查询交易日数据库的问题，避免该查询进入普通热路径。
6. 主审最终扩大回归已通过 **13 个文件、410 项**（原 11 个目标文件，加 `test_backtest_result_storage.py` 与 `test_strategy_market_event_backpressure.py`）。

上述结果只覆盖本轮代码回归与静态检查。

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

### 1.3 已确认：20 日是普通回放功能

现有 Web“做 T 助手 → 回放测试 → 20 日 → 启动回放”是普通 BACKTEST 入口。
前端按交易日历解析最近 20 个已完成交易日，经 GraphQL
`startTTradeReplay`、Engine 命令 `T_TRADE_REPLAY_START` 创建真实
`StrategyRunMode.BACKTEST` 运行，并调用与 PAPER/LIVE 相同的
`AshareIntradayTAssistantStrategy.step(StrategyInput)`。它只使用回测 Broker，
不会发送实盘委托。

20 个交易日是单次普通回放的资源上限，不是必须完成的发布指标。专用 acceptance
CLI、canonical archive 准备器和 rollout evidence 门禁已删除，不再维护第二套
“正式回放”概念。

前端回放所需 Tick 先读 InfluxDB 已持久化历史行情；缺失时由 Engine 通过唯一登记的
QMT Agent 请求 XTData 历史 Tick，写入 InfluxDB 后重新校验并继续同一生产链路。
收盘同步与盘中实盘写入允许共存：本次上传键必须全部可读，额外既有真实行情保留并
合并；只有上传键缺失、存储键不一致、越界、重复、非有限值或读回失败才拒绝。

## 2. 后续工程验证

| 优先级 | 验证项 | 当前事实 | 验证目标 |
| --- | --- | --- | --- |
| P0 | 前端普通 20 日回放 | 已从前端真实入口执行三次。第一次发现跨回放版本复用零行补数请求；第二次和第三次均通过行情检查并进入真实 Tick 循环，但被 Engine 单次 PostgreSQL 心跳操作超时触发的监督重启中断。现已完成补数幂等隔离、回放实际协作让出和心跳失败短间隔重试，相关 55 项测试通过；当前运行中的 Engine 尚未重新导入最后一项修复。 | 可在方便时用统一运维入口重启并重跑，确认状态 `COMPLETED`、进度与报告完整。该项只验证普通回测功能，不阻塞发布、CANARY 或 LIVE。 |
| P1 | 根边界与外部服务环境复验 | 本轮聚焦回归已完成，但 `python -m pytest tests/` 根边界套件尚未作为收口门槛完整执行。部分依赖外部服务的套件在 PostgreSQL 未配置时可能无法建立运行条件。 | 在 PostgreSQL 等外部服务配置完整的环境中运行根边界套件并复验受影响项，分别记录基础设施不可用与真实测试失败；不得将环境失败静默计为跳过或通过。 |
| P1 | Dev `full/live` 最终重启验收 | 本轮按收口指令不执行完整 Dev 重启，也不启动真实交易验收。 | 未来经授权后，用统一运维入口启动并检查状态：`profile=full`、`agentMode=live`、`liveTrading=ENABLED`、唯一账户、QMT Agent `ready`、协议 `1.1`、行情快照新鲜度小于 90 秒。若运行时预检失败，只能报告 `DEGRADED / BLOCKED` 与 `liveTrading=DISABLED`，不得伪装为 `ready`。 |
| P0（外部授权） | LIVE | 尚未执行。 | 只在用户显式确认时执行，并以调用当下的 QMT `ready`、协议 `1.1`、唯一账户、当前安全快照、对账、有限暴露与实盘窗口为准；历史回放和 PAPER 不参与授权。订单/成交回报仍由 QMT Agent 持久化 inbox 后收敛为唯一真源，`command_ack` 不能被当作成交。 |

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

### 3.3 前端普通 20 日回放运行记录

2026-08-25 已直接操作 Web“做 T 助手”的回放测试功能。前端“20 日”实际解析为：

- 最近 20 个已完成交易日：`2026-07-28` 至 `2026-08-24`；
- 初始持仓快照日期：`2026-07-22`；
- 初始持仓 10 个，其中 9 个进入 Tick 回放；
- 数据来源：优先读取 InfluxDB，缺口经 QMT Agent 从 XTData 获取并写回 InfluxDB，
  全部为真实行情，`synthetic=false`。

第一次前端运行 `c1b801dc-8c5a-4ff7-8ca0-1948aee57086` 被
`300917.SZ / 2026-08-24 / tick` 缺失阻断。调查确认 QMT/XTData 当前可返回该日
3,890 条 Tick；真正问题是所有回放版本共用固定补数幂等 scope，复用了当天 08:21
产生的零行完成请求，导致新版本没有再次请求。现已把补数 scope 隔离到具体
backtest/version：同一版本恢复仍可幂等复用，新版本会重新检查和获取。

数据补齐后，第二次前端运行 `de3e5c59-3f02-4c57-a959-b96cb17d663f` 已通过行情
门禁并开始回放，在处理到 `2026-07-28 09:46:57`、累计 2,999 Tick 时 Engine
heartbeat 的 10 秒数据库操作超时，监督器停止并重建 Engine。该版本随后恢复时因
中断检查点与顶层状态指纹不一致，以 `COMPLETE_CHECKPOINT_STATE_MISMATCH` 结束。
日志证明取消原因为 Engine 监督重启，不是策略、行情或用户主动停止。

第一次心跳修复在多标的和单标的正式回放循环中每处理 128 个事件执行一次
`asyncio.sleep(0)` 协作让出。Engine 重启加载后，第三次前端运行
`04f11b63-22b0-4b24-8278-a3b80c7efe4e` 再次通过行情门禁并进入真实 Tick 循环，
但首日边界前 PostgreSQL 心跳 `db.get(RuntimeComponentHeartbeat, "engine")` 仍超过
10 秒；单次观测写入超时再次被当成致命任务失败，监督器取消了回放。该运行证明
事件循环让出不足以解决外部数据库长尾，也不能标记为通过。

最终修复把回放让出改为实际 1ms 调度间隔，并明确由独立 advisory-lease watchdog
负责 Engine 存活与唯一实例判定；心跳只是观测投影，单次超时或瞬时失败只记录告警
并在 1 秒后重试，不再取消策略运行。取消任务仍正常传播，数据库租约失败仍保持
fail-closed。补数幂等隔离、回放调度、心跳重试及既有全局时间线顺序共 55 项相关
测试通过。

当前状态仍是 `PENDING_FRONTEND_REPLAY_AFTER_ENGINE_RELOAD`：代码修复已完成，但
13:36 启动的 Engine 解释器尚未重新导入最终心跳修复。必须整体重启后再次从相同前端入口
运行 20 日并取得 `COMPLETED` 与完整报告，才能确认该普通回测问题已经闭环；这不影响
CANARY/LIVE 授权，也不再维护“5 日替代 PAPER”验收项。

### 3.4 未来的 Dev `full/live` 验收

仅在用户重新授权运行时执行：

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
.\ops\quantx.ps1 status
```

如需重启，应先按仓库运维约束执行 `down`，不得绕过统一入口单独启动 QMT Agent。

## 4. 明确排除项

1. **iOS**：iOS 受 Windows 环境和用户明确要求排除；不运行 Xcode、SwiftUI、Apollo iOS 或移动端验证。Web 做 T 回放是普通 BACKTEST 功能。本轮 GraphQL 操作说明变化按 Web 契约流程执行 codegen，不涉及 iOS operation/type 变化。
2. **真实交易**：本轮不执行 LIVE 灰度、下单、成交验证或任何真实交易动作；该项必须以未来的明确授权为前提。
3. **尚未完成的长时/外部验证**：固定 9,600 Tick 压力已完成；前端普通 20 日回放已实际运行三次并暴露、修复补数幂等和 Engine 心跳两个生产链路问题，但尚未在 Engine 重新加载最终修复后取得 `COMPLETED`。它是普通回测问题的后续验证，不是发布或自动执行门禁。
4. **数据库迁移**：0028 → 0031 已在此前处理完成，不列为本轮未完成项，也不因本文件重复迁移。
5. **无关工作区改动**：本轮提交只应包含状态化规则引擎和本文件对应的已审核改动；不得吸收 iOS、Web、QMT、迁移基线、临时报告或其他用户工作。

## 5. 收口结论

普通 20 日回放已经使用现有 Web 做 T 回放的真实生产链路，并已实际执行三次。
第一次定位并修复跨版本复用零行补数请求，后两次确认真实 Tick 可进入策略循环，
同时定位并修复单次心跳数据库长尾错误触发 Engine 重启的问题。当前只完成了实现与相关测试，尚待
Engine 重新加载后从前端重跑并取得 `COMPLETED` 与完整报告。因此，**该普通回测问题
仍待最终运行确认**，但它不阻塞 CANARY/LIVE。固定 9,600 Tick 合成压力和“回放已启动”
也不代表普通 20 日回放已经完成。
