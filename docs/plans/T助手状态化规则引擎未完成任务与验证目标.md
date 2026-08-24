# T 助手状态化规则引擎：未完成任务与验证目标

> 状态：当前开发迭代收口清单；不是生产验收通过证明。
>
> 更新：2026-08-24
>
> 适用范围：持仓做 T 有状态机会引擎 V3，以及其 RuntimeState、回放与运行时持久化链路。

## 1. 本轮已完成边界

本轮已经完成的代码设计与实现边界如下；它们不等同于下文所有长时、外部环境和实盘验收均已通过。

1. **内存状态与持久化投影分离**：`RuntimeState` 在进程内保留策略运行所需的全量状态；T 助手的行情窗口和样本只保留在内存。边界持久化使用紧凑投影，不再把行情窗口当作可恢复数据库状态写入。
2. **按运行模式的检查点策略**：PAPER/LIVE 的普通热路径不写数据库，在 11:35、15:05 的边界进行持久化；BACKTEST 以交易日为批次持久化。即时可审计的事实仍按原有事实链路持久化，不能因热状态内存化而丢失。
3. **可恢复边界协议**：使用 `PREPARED → materialize → FINALIZE`，并通过可识别的完成收据和恢复逻辑处理边界中断，避免将未完整物化的检查点误判为完成。
4. **热路径减负**：状态同步采用根增量与边界全量捕获；机会读投影、trace 与评估事件使用相应的紧凑/物化路径；SQL 写入采用分块批量操作。

这些实现仍须经过本文件列出的回归与压力门槛，才能成为可宣称的生产验收结果。

### 1.1 本轮已完成验证

以下结果已在本轮收口前完成，故不再列为未完成项：

1. 第 3.2 节列出的核心聚焦组合（含机会 repository、runtime、replay 测试）退出码为 0。
2. `tests/engine/unit/test_strategy_market_event_backpressure.py` 全文件 31 项通过。
3. `tests/engine/unit/test_strategy_executor.py -k session_checkpoint` 的 6 项通过。
4. 本轮 Python 白名单的 Ruff 检查通过，`git diff --check` 无错误。
5. 会话协调器已经修复在检查状态或重试窗口之前查询交易日数据库的问题，避免该查询进入普通热路径。

上述结果只覆盖本轮代码回归与静态检查，不替代固定规模压力、真实历史数据覆盖、完整根边界套件或实盘验收。

## 2. 未完成项与验收目标

| 优先级 | 未完成项 | 当前事实 | 必须达成的验证目标 |
| --- | --- | --- | --- |
| P0 | 固定 9,600 Tick 全量合成压力验收 | 最近一次旧实测在双投影补丁前：120 秒超时，已处理 4,889 Tick，p50 约 14.56 ms，`state upsert max` 约 26.77 s，`snapshot max` 约 33.99 s。 | 在双投影补丁后重跑固定 9,600 Tick 场景；命令自然完成、`timed_out=false`、`accounting_passed=true`、回放为 `COMPLETED`，并对事件、trace、evaluation 做精确对账。小样本或截断跑不能替代此门槛。 |
| P0 | 20 个已完成交易日严格因果历史回放 | 先前盘点仅有 46/160 个完整 `instrument-day`，尚无一个覆盖全部持仓的连续 20 日窗口。 | 从 D-1 持仓快照出发，排除当前交易日，选择前 20 个上交所已完成交易日；全持仓均需有完整、连续且可标识的数据覆盖。回放不得读取未来数据，缺失必须保守阻断并使验收失败。 |
| P0 | 最近 5 个已完成交易日回放（替代 PAPER） | 用户已同意以历史数据回放替代本轮 PAPER 连续运行；该替代验收尚未完成。 | 排除当前交易日后，完成最近 5 个已完成交易日的因果回放，达到 5/5 数据与结果对账；若验收工具仍保留该门槛，还须至少覆盖 20 个候选生命周期。该结果是 **PAPER 的替代验收**，不得表述为实际 PAPER 连续运行。 |
| P1 | 根边界与外部服务环境复验 | 本轮聚焦回归已完成，但 `python -m pytest tests/` 根边界套件尚未作为收口门槛完整执行。部分依赖外部服务的套件在 PostgreSQL 未配置时可能无法建立运行条件。 | 在 PostgreSQL 等外部服务配置完整的环境中运行根边界套件并复验受影响项，分别记录基础设施不可用与真实测试失败；不得将环境失败静默计为跳过或通过。 |
| P1 | Dev `full/live` 最终重启验收 | 本轮按收口指令不执行完整 Dev 重启，也不启动真实交易验收。 | 未来经授权后，用统一运维入口启动并检查状态：`profile=full`、`agentMode=live`、`liveTrading=ENABLED`、唯一账户、QMT Agent `ready`、协议 `1.1`、行情快照新鲜度小于 90 秒。若运行时预检失败，只能报告 `DEGRADED / BLOCKED` 与 `liveTrading=DISABLED`，不得伪装为 `ready`。 |
| P0（外部授权） | LIVE 灰度 | 尚未执行。 | 必须由用户重新明确授权真实交易后才可开始。灰度前须满足 QMT `ready`、协议 `1.1`、唯一允许账户、账户与风控白名单、订单/成交回报由 QMT Agent 持久化 inbox 后收敛为唯一真源；`command_ack` 不能被当作成交。 |

## 3. 推荐执行命令与记录方式

以下命令仅作为后续验证入口。本轮收口不运行长时压力、Dev `full/live` 或真实交易命令。

### 3.1 固定 9,600 Tick 压力验收

在已有可用审计基线和数据库环境中执行固定规模压力回放，并将报告另存为新的日期命名文件：

```powershell
uv run python -m quantx_engine.t_trade_v3_acceptance `
  --reuse-audit-report docs/reports/t-trade-v3-pressure-postpatch-20260824.md `
  --pressure-snapshot-date 2026-06-03 `
  --synthetic-pressure `
  --synthetic-ticks-per-instrument-day 600 `
  --pressure-timeout-seconds 120 `
  --report docs/reports/t-trade-v3-pressure-9600-<YYYYMMDD>.md
```

验收记录必须同时保存机器可读结果，并记录请求 Tick 数、实际有效 Tick 数、过滤原因、吞吐/延迟、状态写入与快照耗时，以及事件、trace、evaluation 对账结果。不得用 480 Tick 等小基准替代本项。

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

1. 先由数据加载器按交易日历确定 D-1 快照、排除当前交易日，并列出向前的上交所已完成交易日。
2. 对每个持仓、每个交易日验证 Tick 连续性、会话完整性、source identity 与时点可用性；任何缺口均不得用当前日或未来数据补齐。
3. 有完整 20 日窗口后运行严格因果回放；有完整最近 5 日窗口后运行替代 PAPER 回放，并按第 2 节写入候选生命周期与全量对账结果。

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
3. **长时运行**：本轮不继续调优或重跑 9,600 Tick 压力、20 日历史回放、5 日替代 PAPER 回放，避免把当前开发收口再次变成长时间任务。
4. **数据库迁移**：0028 → 0031 已在此前处理完成，不列为本轮未完成项，也不因本文件重复迁移。
5. **无关工作区改动**：本轮提交只应包含状态化规则引擎和本文件对应的已审核改动；不得吸收 iOS、Web、QMT、迁移基线、临时报告或其他用户工作。

## 5. 收口结论

在第 2 节的 P0 门槛、外部数据覆盖和用户授权的 LIVE 门槛完成前，**不能宣称原始生产验收已经完成**。本文件的作用是结束当前开发迭代并保留可重复执行的后续验收清单；本次提交仅提交已经实现且已审核的代码与文档，不替代上述未完成验证。
