# 多标的做 T 助手 P0 冻结基线

> 文档状态：`P0_DONE`（P0 冻结、清点、只读审计和基线证据已完成；P1 readiness=false）<br>
> 日期：2026-09-03<br>
> 目标设计：[多标的做 T 助手新架构设计 v2.2](../architecture/多标的做T助手新架构设计.md)<br>
> 实施追踪：[多标的做 T 助手新架构开发实施方案](多标的做T助手新架构开发实施方案.md)<br>
> 当前基线：[系统架构设计（As-Is）](../architecture/系统架构设计.md)

本文是 P0 的冻结记录，不是 P1/P2/P3 的实现报告。除特别标注为“当前 As-Is”的内容外，
所有 owner、协议、数据库约束、阈值和 order policy 都是后续阶段必须遵守的 To-Be 决策。
P0 允许清点、冻结、只读审计和基线测试；本阶段不建表、不改协议、不切换运行链，不能把目标
能力写成当前已具备能力。

## 1. 范围与权威边界

### 1.1 当前事实（As-Is）

- 当前仍是 `StrategyRun + protocol 1.1`：运行以 `StrategyRun` 作为做 T 的身份，Agent 公共协议为 `1.1`。
- 现有 V3 做 T、普通策略、人工命令、ExitPlan、pending/outbox、回报收敛和 GraphQL/Web
  均按当前代码与工程 README 工作；本文不改变它们的运行行为。
- 当前 ExitPlan 的 entry-source 仍可能依赖 `strategy_run_id` 和运行时 ExitPlanBook；这是存量
  事实，不是独立 `EXIT_PLAN` owner 已经上线的证据。
- 当前开发库中的异常和存量义务必须保持可见、可对账、可追溯。未知券商结果不能被本阶段
  的脚本、SQL 或文档“修复”为成功或失败。

### 1.2 P0 允许与禁止

| P0 允许 | P0 禁止 |
|---|---|
| 读取代码、schema、契约和测试，建立分层 identity 清单 | 新建/修改数据库表、迁移、索引或约束 |
| 只读聚合 legacy owner、候选、审批、订单、回报、batch、ExitPlan 的一致性 | 修改协议 1.1/1.2 payload、双协议旁路或启动新 owner 下单 |
| 冻结唯一 owner、reason code、阈值、排序、收盘和灰度决策 | 为旧做 T `StrategyRun` 增加功能、字段、回调、fallback 或兼容入口 |
| 运行脱敏基线测试并保存命令、commit、结果 | 删除、批量猜测 owner、重发未知命令、伪造成交或伪造闭环 |

旧做 T `StrategyRun` 自本文生效起只允许安全修复和义务排空。任何新增 T 功能必须以未来
`TAssistantExecution` 的 P1/P2/P3 迁移任务提出，不能继续扩展旧 owner。

### 1.3 冲突处理

1. 判断当前是否能运行，以当前代码、[系统架构设计](../architecture/系统架构设计.md) 和各工程
   README 为准。
2. 判断最终做 T 契约，以目标架构和上位交易契约为准；目标未实现时必须标注 To-Be。
3. 判断阶段、依赖和退出门，以[实施方案](多标的做T助手新架构开发实施方案.md)为准。
4. 自动 scanner 只产生候选清单；下表的人工确认是迁移边界的权威清单。scanner 漏报、误报或
   生成文件命中不能改变 owner 结论。

## 2. 分层 identity 清单

表中“当前假设”描述 2026-09-03 的代码事实；“迁移落点”描述尚未执行的阶段目标；“验证”
是该边界的最小验证位置，不代表该目标已经通过。

### 2.1 DB / ORM

| 当前 run 假设 | 精确现有代码文件（符号/表） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| `StrategyRun` 是策略实例、做 T 调度和恢复的主要身份 | `packages/infrastructure/src/quantx_infrastructure/models/strategy_run.py`（`StrategyRun`） | P1 只保留允许的普通策略 owner；P3 新增 `TAssistantExecution`，做 T 不再创建新 `StrategyRun` | `tests/engine/unit/test_strategy_executor.py`、`tests/application/test_t_trade_v3_application.py` |
| 运行状态以 `run_id` 关联现金、冻结、总资产和 custom state | `packages/infrastructure/src/quantx_infrastructure/models/strategy_run_state.py`（`StrategyRunState`） | P1 迁移公共 owner/environment 引用；P3 由 T execution checkpoint/账户事实替代做 T callback，不复制账户真源 | `tests/engine/unit/test_strategy_base.py`、`tests/application/test_t_trade_v3_application.py` |
| intent 表保留 nullable `strategy_run_id`，并同时有 owner 字段；读写仍大量按 run 过滤 | `packages/infrastructure/src/quantx_infrastructure/models/trade_intent_record.py`（`strategy_trade_intents`） | P1 owner pair + environment 成为强制公共身份；受控 legacy run 对账后再排空 | `tests/infrastructure/test_runtime_owner_audit_safety.py`、`tests/infrastructure/test_exit_plan_trade_intent_ownership.py` |
| outbox、pending、correlation、runtime event、TTradeBatch 的模型仍有 run-centric 字段或 run 外键 | `packages/infrastructure/src/quantx_infrastructure/models/agent_runtime.py`（`TradeCommandOutbox`、`PendingTradeOrder`、`StrategyOrderCorrelation`、`StrategyRuntimeEvent`、`TTradeBatch`、`AgentReportInbox`） | P1 原子补齐 owner_type/owner_id/environment、稳定 client_order_id 和 event identity；P2/P3 分离 T batch/cycle/allocation | `tests/engine/test_report_processor_reconciliation.py`、`tests/engine/test_t_trade_runtime_events.py` |
| ExitPlan 通过 source_type/source_id 和 nullable `strategy_run_id` 识别来源，entry-source 可能绑定 run | `packages/infrastructure/src/quantx_infrastructure/models/auto_exit_plan.py`（`auto_exit_plans`） | P1 写入 source execution owner ref；P2 独立 `ExitPlanRuntime`，SELL 只使用 `EXIT_PLAN/plan_id` | `tests/domain/test_exit_plan.py`、`tests/infrastructure/test_exit_plan_trade_intent_ownership.py` |
| 全局做 T 配置行可关联 `strategy_run_id`，配置与旧运行耦合 | `packages/infrastructure/src/quantx_infrastructure/models/t_trade_global_config.py`（`t_trade_global_configs`） | P1/P3 使用版本化 `TAssistantConfig`/`TAssistantExecution`；配置 head 仍单账户唯一 | `tests/application/test_t_trade_v3_application.py`、`tests/engine/unit/test_t_trade_v3_approval.py` |

### 2.2 contracts

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| Agent 公共协议固定 `PROTOCOL_VERSION = 1.1`，服务端与 Agent 接受旧版本集合 | `packages/contracts/src/quantx_contracts/agent.py`（协议版本、命令/报告 DTO、`TradeCommandPayload`） | P1 原子切换唯一 `1.2`；业务 owner 留在服务端 durable correlation，不进入券商 wire | `tests/contracts/test_agent_protocol.py`、`tests/qmt_agent/test_live_broker_reconnect.py` |
| `TradeCommandPayload` 当前携带 `strategy_name`、`strategy_run_id`、`strategy_order_id`、`intent_id`、`batch_id`、T role 和 metadata 等服务端字段 | `packages/contracts/src/quantx_contracts/agent.py`（`TradeCommandPayload`） | P1 删除 wire 上的服务端 identity/策略字段，固定本文第 4 节的 1.2 白名单 | `tests/contracts/test_agent_protocol.py`、`tests/qmt_agent/test_account_unavailable_runtime.py` |

### 2.3 domain

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| `StrategyInput` 和 `TradeIntent` 以 `run_id` 为必需身份，origin 默认 `STRATEGY_RUN` | `packages/domain/src/quantx_domain/strategies/base.py`（`StrategyInput`、`TradeIntent`、`StrategyRunIntentOrigin`、`StrategyCadence`） | P1 引入强类型 `ExecutionOwnerRef`；P3 加入 `SNAPSHOT` cadence 与 `execution_ref`，不再把 run id 当 T 主身份 | `tests/engine/unit/test_strategy_base.py` |
| V3 规则内核由固定标的策略输入和 run context 驱动，规则本身不应访问账户或外部服务 | `packages/domain/src/quantx_domain/strategies/ashare_intraday_t_assistant.py` | P3 由 `TAssistantDecisionRuntime` 调用同一 `StrategyBase.step(StrategyInput)`；规则/FSM 语义保留 | `tests/engine/unit/strategies/test_ashare_intraday_t_assistant.py`、`tests/engine/unit/trading/test_t_trade_opportunity_engine.py` |
| ExitPlan 值对象表达买入来源、卖出条件和 T+1 约束，当前实现不等于独立 owner runtime | `packages/domain/src/quantx_domain/trading/exit_plan.py` | P2 把 source execution ref 与 SELL owner 分开；数量合法性仍由交易域、OrderSizer、Risk 和 Broker 决定 | `tests/domain/test_exit_plan.py` |

### 2.4 application

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| V3 use case、account facts、ports 通过 run/旧 T 配置组织候选、审批和状态推进 | `packages/application/src/quantx_application/t_trade_v3/contracts.py`、`use_cases.py`、`account_facts.py`、`ports.py` | P1 owner adapter；P2 公共容量/ExitPlan/admission；P3 独立 execution lifecycle、cycle attempt 和 checkpoint | `tests/application/test_t_trade_v3_application.py`、`tests/engine/unit/test_t_trade_v3_approval.py` |
| 命令用例路由仍可把策略 run 作为执行关联 | `packages/application/src/quantx_application/trade_commands.py` | P1 统一服务端 owner/correlation，公共 command 仅传 1.2 wire 字段 | `tests/api/integration/test_agent_trade_command_priority.py`、`tests/infrastructure/test_trade_command_idempotency.py` |

### 2.5 infrastructure

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| 恢复状态、检查点和推进以 run key 为主 | `packages/infrastructure/src/quantx_infrastructure/core/runtime_state_manager.py` | P1 支持 owner/environment；P3 用 execution/cycle checkpoint，拒绝默认 run fallback | `tests/engine/test_t_trade_runtime_events.py` |
| intent repository 有大量按 `strategy_run_id` 的读取、删除、审批和恢复查询 | `packages/infrastructure/src/quantx_infrastructure/repositories/trade_intent_repository.py` | P1 迁移 owner/environment 查询与 owner 级幂等；legacy 查询只用于受控排空 | `tests/infrastructure/test_runtime_owner_audit_safety.py`、`tests/infrastructure/test_exit_plan_trade_intent_ownership.py` |
| 命令服务从 run intent 组装 pending/order/outbox、容量和幂等 | `packages/infrastructure/src/quantx_infrastructure/services/trade_command_service.py` | P1 owner router + 1.2 payload；P2 接入公共 admission、Entry/Exit policy 和 unknown 门 | `tests/infrastructure/test_trade_command_idempotency.py`、`tests/infrastructure/test_trade_command_cancel_retry.py` |
| processor 的持久化、审批和执行路径仍依赖旧 intent/run 关联 | `packages/infrastructure/src/quantx_infrastructure/services/trade_intent_processor.py` | P1 只接受完整 owner/environment；P2 在统一风险/容量/ExitPlan 链路执行 | `tests/infrastructure/test_exit_plan_trade_intent_ownership.py` |
| runtime obligation blocker 按 run 聚合 pending/event/intent/ExitPlan/T batch | `packages/infrastructure/src/quantx_infrastructure/services/runtime_obligations.py`（`runtime_obligation_blocker`） | P1 以 owner/environment 聚合，legacy run 仅可审计排空；P2 形成 account obligation watermark | `tests/infrastructure/test_runtime_owner_audit_safety.py` |
| runtime source 的 ExitPlan owner matrix 要求 run 和内部 ExitPlanBook，手工来源另行处理 | `packages/infrastructure/src/quantx_infrastructure/services/exit_plan_execution_owner.py` | P1 source execution ref；P2 独立 `ExitPlanRuntime`，任何 SELL owner 固定为 `EXIT_PLAN` | `tests/infrastructure/test_exit_plan_trade_intent_ownership.py` |
| 容量服务有账户级事实，但做 T 义务和老仓归因仍从旧链路取值 | `packages/infrastructure/src/quantx_infrastructure/services/account_capacity_service.py` | P2 固化 obligation watermark、`locked_core/core/swing` 优先级、protected floor 和公共 admission | `tests/infrastructure/test_account_capacity_service.py`、`tests/infrastructure/test_execution_environment_capacity.py` |

### 2.6 Engine

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| 全局做 T monitor 创建、恢复并持有旧 StrategyRun | `apps/engine/src/quantx_engine/t_trade_global_monitor.py` | P3 改为唯一 `TAssistantExecution` producer；P1 前禁止新 T owner 下单 | `tests/application/test_t_trade_v3_application.py`、`tests/engine/test_t_trade_runtime_events.py` |
| strategy manager 扫描、恢复和运行 `StrategyRun` 实例 | `apps/engine/src/quantx_engine/strategy_manager.py` | P1 保留普通策略；P3 做 T 从 manager/run scheduler 脱离 | `tests/engine/unit/test_strategy_executor.py` |
| executor 将 intent 转换为 pending/correlation/outbox，并在 metadata/字段中保留 run | `apps/engine/src/quantx_engine/strategy_executor.py` | P1 一次性迁移 owner/environment/client order identity；P2 接入统一 admission 和 order policy | `tests/engine/unit/test_strategy_executor.py`、`tests/engine/unit/test_t_trade_v3_approval.py` |
| report processor 按 strategy run 找 correlation、写 runtime event 并收敛回报 | `apps/engine/src/quantx_engine/report_processor.py` | P1 只以 client_order_id 找服务端 correlation，再由 owner router 收敛；乱序/迟到不得重复推进 | `tests/engine/test_report_processor_reconciliation.py`、`tests/engine/test_t_trade_runtime_events.py` |

### 2.7 API

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| Agent HTTP/WebSocket 命令与报告入口围绕 1.1 会话和 run/correlation 工作 | `apps/api/src/quantx_api/agent_api.py` | P1 与 contracts/Engine/QMT 原子切换 1.2；API 不猜 owner、不在 wire 回填 owner | `tests/contracts/test_agent_protocol.py`、`tests/qmt_agent/test_live_broker_reconnect.py` |
| approval 查询/确认以 strategy run、intent 和现有审批状态展示 | `apps/api/src/quantx_api/gqlapi/trade_approval.py`、`apps/api/src/quantx_api/gqlapi/types/trade_approval_types.py` | P1 改为 owner/environment；P4 只读展示 allocation/gate/admission 事实 | `tests/engine/unit/test_t_trade_v3_approval.py` |
| T control/resolver/schema/types 读取全局配置、run、batch 和 V3 状态 | `apps/api/src/quantx_api/gqlapi/t_trade_control.py`、`apps/api/src/quantx_api/gqlapi/resolvers/t_trade.py`、`apps/api/src/quantx_api/gqlapi/schemas/t_trade_schema.py`、`apps/api/src/quantx_api/gqlapi/types/t_trade_types.py` | P1 owner projection；P3/P4 映射 execution/cycle/candidate/allocation 只读投影 | `tests/application/test_t_trade_v3_application.py` |
| 普通 strategy schema/control/types 仍以 StrategyRun 为一等对象 | `apps/api/src/quantx_api/gqlapi/strategy_control.py`、`apps/api/src/quantx_api/gqlapi/schemas/strategy_schema.py`、`apps/api/src/quantx_api/gqlapi/types/strategy_types.py` | P1 保留普通 StrategyRun 语义；禁止为了 T 增加 run fallback | `tests/engine/unit/test_strategy_base.py`、`tests/engine/unit/test_strategy_executor.py` |
| liquidation approval/resolver/schema/types 关联现有 ExitPlan、run 和清仓审批 | `apps/api/src/quantx_api/gqlapi/liquidation_approval.py`、`apps/api/src/quantx_api/gqlapi/resolvers/liquidation.py`、`apps/api/src/quantx_api/gqlapi/schemas/liquidation_schema.py`、`apps/api/src/quantx_api/gqlapi/types/liquidation_types.py` | P2 展示独立 ExitPlan owner/source ref；不得把 SELL 重新归因给 T run | `tests/domain/test_exit_plan.py`、`tests/infrastructure/test_exit_plan_trade_intent_ownership.py` |

### 2.8 Worker

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| 标的画像/配置流可能以旧 T config 和 run 作为同步关联 | `apps/worker/src/quantx_worker/prefector/flows/t_trade_instrument_profile_flow.py` | P3 写入版本化 config/profile，不创建运行 owner，不产生交易命令 | `tests/worker/test_snapshot_flows.py` |
| durable agent flow 管理长任务、恢复和 Agent 相关 durable 状态，存在 run-centric 输入 | `apps/worker/src/quantx_worker/prefector/flows/durable_agent_flows.py` | P1 只传明确 owner/environment 和协议版本；未知结果由服务端 reconcile | `tests/worker/test_durable_agent_flows.py` |

### 2.9 QMT Agent

| 当前 run 假设 | 精确现有代码文件（符号） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| Agent runtime 解析协议 1.1、接收带服务端策略/run 字段的命令并管理会话 | `apps/qmt-agent/src/quantx_qmt_agent/runtime.py` | P1 只接受 1.2；只执行合法 wire 字段；业务 owner 不进入本地状态真源 | `tests/qmt_agent/test_account_unavailable_runtime.py`、`tests/qmt_agent/test_live_broker_reconnect.py` |
| broker adapter 以 command payload 映射 XTTrading，下单/撤单事实回报不负责推断 owner | `apps/qmt-agent/src/quantx_qmt_agent/broker.py` | P1 报告只带 client_order_id 与券商事实；unknown 不得自行重发 | `tests/qmt_agent/test_live_broker_reconnect.py` |
| miniQMT local agent/manager 处理本地连接和券商字段，可能复用 remark/策略字段 | `apps/qmt-agent/src/quantx_qmt_agent/miniqmt/local_agent.py`、`apps/qmt-agent/src/quantx_qmt_agent/miniqmt/trading/trading_manager.py` | P1 本地由 client_order_id 稳定生成 order remark；`strategy_name` 为空，服务端 owner 不落入 remark | `tests/qmt_agent/test_account_unavailable_runtime.py`、`tests/qmt_agent/test_live_broker_reconnect.py` |

### 2.10 GraphQL / Web

| 当前 run 假设 | 精确现有代码文件（符号/目录） | P1/P2/P3 迁移落点 | 验证测试 |
|---|---|---|---|
| T 页面和查询以 global config、strategyRunId、approval、batch/replay 关联展示 | `apps/web/src/features/portfolio/hooks/useTTradeGlobal.ts`、`apps/web/src/features/portfolio/pages/TTradeGlobalPage.tsx` | P1 同步 generated owner/environment 类型；P4 展示 opportunity/allocation/gate/admission 只读事实 | `apps/web/src/features/portfolio/pages/t-trade-global/*.test.ts*` |
| 做 T 子页面把 activity、live monitor、decision audit、positions、replay 和操作持久化绑定现有字段 | `apps/web/src/features/portfolio/pages/t-trade-global/`（`activity.ts`、`liveBatchAdapter.ts`、`monitoring.ts`、`operationPersistence.ts`、`serverTruthRecovery.ts`、`TTradeLiveDecisionAudit.tsx` 等） | P1 不显示伪造 owner；P4 以服务端投影显示稳定 identity、reason、TTL 和版本；写操作仍走服务端门禁 | `apps/web/src/features/portfolio/pages/t-trade-global/*.test.ts*` |
| GraphQL schema/codegen 与 Web 查询共享现有 T/strategy/liquidation 字段 | `apps/api/src/quantx_api/gqlapi/schemas/t_trade_schema.py`、`apps/api/src/quantx_api/gqlapi/schemas/strategy_schema.py`、`apps/api/src/quantx_api/gqlapi/schemas/liquidation_schema.py`、`apps/web/src/core/graphql/` | P1/P4 schema 变更时一次性 codegen、check、lint、test、build；P0 不运行 schema 迁移 | 当前 T 页面测试；schema 变更阶段执行仓库规定的 GraphQL 全套命令 |

移动端/其他客户端也可能命中共享 contracts，但不在 P0 的改动范围；P1 原子协议切换前必须
把所有仍消费 1.1 的客户端纳入发布清单，不得以“未扫描到”当作安全豁免。

## 3. `ExecutionOwnerRef` 与数据库约束冻结（To-Be）

### 3.1 唯一 owner 类型

后续公共执行链只认以下六种 owner，拼写和顺序固定，不允许新增同义字符串或 metadata-only
owner：

```text
STRATEGY_RUN
T_ASSISTANT_EXECUTION
ENTRY_PLAN
BOARD_ASSISTANT_EXECUTION
EXIT_PLAN
MANUAL_COMMAND
```

```text
ExecutionOwnerRef {
  owner_type: one of the six values above
  owner_id: non-empty stable id
}
```

`owner_type`、`owner_id` 和 `environment` 是公共 intent、pending、correlation、outbox、runtime
event 的必需业务身份。owner/environment 一经引用不可更新；没有 owner、空 owner id、未知 owner
类型、owner 与目标实体冲突或 owner/environment 不匹配，均在 repository 和路由边界拒绝。

### 3.2 legacy run 约束

P1/P2 迁移期间允许存在 nullable `strategy_run_id`，但仅用于已存在且尚未排空的
`STRATEGY_RUN` 存量。其唯一允许关系是：

```text
strategy_run_id IS NULL
OR (
  owner_type = 'STRATEGY_RUN'
  AND owner_id = strategy_run_id
)
```

非 `STRATEGY_RUN` 的新 owner 不得同时写 `strategy_run_id`；不存在默认 owner 或缺失 owner 时
fallback。P7 legacy 义务排空并完成全量对账后删除该列和对应查询分支。

### 3.3 目标表约束、唯一键和索引

以下是必须在 P1/P2/P3 迁移中落地的目标约束；P0 只冻结，不执行 migration：

| 对象 | 冻结约束 |
|---|---|
| 配置 | `t_assistant_configs(account_id)` 唯一；单账户只有一个配置 head。 |
| LIVE execution | `t_assistant_executions(account_id)` partial unique：`environment='LIVE' AND status IN ('WARMING','RUNNING')` 最多一行；进入 `DRAINING` 与阻断新 ENTRY 同一事务完成。 |
| cycle | `(execution_id, cycle_sequence)` 唯一；`(execution_id, decision_key, attempt)` 唯一；`cycle_id` 不复用。 |
| allocation | `(execution_id, cycle_id, allocation_attempt)` 唯一；`(allocation_batch_id, intent_id)` 唯一；allocation/admission 只允许整批可见。 |
| owner | 公共 intent/pending/correlation/outbox/runtime event 的 `owner_type` 受 enum/check 约束，`owner_id` 非空；所有引用带 `environment`， owner/environment 不可变。 |
| intent 幂等 | owner 级幂等业务键唯一，至少包含 `environment + owner_type + owner_id + idempotency_key`；同一 intent 不得由两个 owner 认领。 |
| command | pending、correlation、outbox 的 `client_order_id` 唯一；券商 `broker_order_id` 只来自 Agent 事实，不能由服务端猜测。 |
| BUY fill → ExitPlan | 一个真实 BUY fill/role 只能激活一个 ExitPlan；重复回报命中原 plan，不插入第二条。 |
| event/inbox/projection | event、Agent inbox 和 projection 按稳定 source identity/event key 唯一；重复应用不增加数量、不推进第二次状态。 |
| ExitPlan | `auto_exit_plans` 保存 `source_execution_owner_type`、`source_execution_owner_id`；入口来源不再靠 nullable run 作为真源。 |
| T batch | `t_trade_batches` 保存 source T execution ref（owner type/id + environment），不得依赖策略 run 推断来源。 |

迁移必须先用影子查询证明现有数据满足约束；发现不确定 owner、重复活动 execution、冲突
ExitPlan、unknown result 或未对账账户时，进入 `RECONCILE_REQUIRED`，不得通过删除、猜 owner、
放宽 nullable 或批量 SQL 规避约束。

## 4. Agent protocol 1.2 原子契约冻结（To-Be）

### 4.1 版本原则

- 目标公共协议唯一支持 `1.2`，不维护 1.1/1.2 双协议，也不把 owner 塞入旧 metadata 旁路。
- 业务 owner、intent、batch、policy、risk、trace 和审批事实留在服务端 durable outbox、
  correlation 和业务表；QMT Agent 只收到执行所需的最小 wire payload。
- `command_ack` 只表示命令已投递/接收，不推进订单或成交状态；券商报告先进入 inbox，再由
  服务端根据 `client_order_id` 找 correlation 和 owner 收敛。

### 4.2 `PLACE_ORDER` wire 白名单

`PLACE_ORDER` 的 wire payload 只能含以下字段，字段集合是精确集合，不得追加服务端 identity：

```text
command_kind
client_order_id
account_id
execution_mode
instrument_code
side
price_type
limit_price
volume
expires_at
```

明确禁止 `instance_id`、`strategy_name`、`strategy_run_id`、`strategy_order_id`、`intent_id`、
`batch_id`、`t_trade_role`、`bucket`、`trace`、`risk`、`policy`、`reason`、`substitution`、
`request_metadata` 和 `owner` 字段。`price_type` 只能表达 FIX_PRICE/限价；不允许市场单或
无限追价。Agent 本地 miniQMT 的 order remark 由 `client_order_id` 稳定生成；`strategy_name`
保持空值，不需要 `order_label`。

### 4.3 `CANCEL_ORDER` wire 白名单

`CANCEL_ORDER` 只能含以下字段：

```text
command_kind
client_order_id
account_id
execution_mode
broker_order_id
expires_at
```

撤单目标只能来自服务端已持久化的 authoritative order fact；服务端没有 broker order identity
时不得猜测或改写为新的 client order。

### 4.4 1.2 切换 runbook

1. 全账户停止新命令和新风险增加（包括普通策略、T、打板、买入计划和人工 BUY）。
2. 继续消费并持久化 Agent inbox，收敛已经存在的订单/成交事实。
3. 只读确认未投递 `QUEUED` protocol 1.1 outbox 为 `0`，且 protocol 1.1 result unknown 为 `0`。
4. 完成 owner、pending、correlation、ExitPlan、batch、账户快照和券商全量快照审计。
5. 停止组件并备份 durable 数据，审计证据脱敏。
6. 在同一维护窗口原子部署 migration、contracts、API、Engine、QMT Agent、GraphQL/Web 和文档。
7. 组件恢复后只接受 protocol 1.2；先做全量账户/订单/成交/ExitPlan 快照对账，再恢复写入。
8. 任何已投递的 1.2 命令都不得回滚成 1.1、重编码或重发；若结果不确定，只阻断新的风险增加，
   保留 capacity/义务并使用券商全量快照 reconcile。

## 5. Reason code 冻结

reason code 是机器可判定的状态原因和固定动作；自由文本只能作为附加诊断，不能推进状态、
释放资金、改变数量或产生替代订单。

| 类别 | code | 固定语义 | 固定动作 |
|---|---|---|---|
| owner | `OWNER_TYPE_MISSING` | owner 类型缺失 | 拒绝持久化/路由，fail-closed；补齐前不产生新 intent。 |
| owner | `OWNER_ID_MISSING` | owner id 为空或不稳定 | 拒绝并审计，不生成临时 id、不回退 StrategyRun。 |
| owner | `OWNER_TARGET_MISSING` | owner 指向的 durable 目标不存在 | 拒绝并进入 reconcile；不得猜测目标。 |
| owner | `OWNER_CONFLICT` | 同一事实出现两个 owner 或 owner 与目标不一致 | 拒绝状态推进，冻结风险增加，人工/全量链路对账。 |
| owner | `OWNER_ENVIRONMENT_MISMATCH` | owner 与 PAPER/LIVE/BACKTEST 不匹配 | 拒绝跨环境写入；保留原环境事实，不复制到另一环境。 |
| owner | `LEGACY_STRATEGY_RUN_ID_FORBIDDEN` | 新 owner 携带 legacy run id | 拒绝写入；不能用 run id 充当 owner。 |
| owner | `LEGACY_OWNER_RECONCILE_REQUIRED` | legacy 记录 owner 不可证明 | 账号 fail-closed；逐条 broker full snapshot + durable chain 对账。 |
| protocol | `PROTOCOL_VERSION_UNSUPPORTED` | Agent/服务端版本不在当前唯一协议 | 阻止命令；不降级、不转换 payload。 |
| protocol | `PROTOCOL_11_OUTBOX_NOT_DRAINED` | 1.1 未投递 outbox 尚未排空 | 停止切换和所有新命令，继续收敛旧队列。 |
| protocol | `PROTOCOL_11_RESULT_UNKNOWN` | 1.1 命令结果未知 | 保留容量和义务，只做 broker reconcile，禁止重发。 |
| protocol | `COMMAND_FIELD_FORBIDDEN` | wire 出现白名单外字段 | schema 边界拒绝并记录字段名；不进入 outbox。 |
| market | `T_MARKET_STREAM_NOT_READY` | 行情 stream/fence 未 ready | 停止 T 新候选/ENTRY；完成 capture 和 rewarm。 |
| market | `T_MARKET_GENERATION_CHANGED` | stream generation 改变 | 失效受影响窗口，停止新候选；清空并重新 warm。 |
| market | `T_MARKET_SEQUENCE_GAP` | accepted tick sequence 不连续 | 停止受影响标的的新决策，不用 latest quote 补状态；reconcile/rewarm。 |
| market | `T_MARKET_RING_OVERFLOW` | delta ring 覆盖未消费 cursor | 立即使窗口失效、停止新候选/触发；清空 ring 后完整 rewarm。 |
| market | `T_REDUCER_LAG_EXCEEDED` | reducer 超过 tick/时间 lag 硬阈值 | 停止新 ENTRY，保留退出事实；resync 后重新 warm。 |
| market | `T_QUOTE_STALE` | 单标的 quote 超过 freshness | 阻断该标的 Gate/新候选，不用旧价触发退出；等新鲜 tick。 |
| market | `T_SNAPSHOT_STALE` | 市场 snapshot 超过 freshness | 阻断受影响风险增加，保持可见并重新获取 snapshot。 |
| market | `T_REWARM_REQUIRED` | generation/gap/overflow 后尚未完成重热 | 维持 fail-closed；完成规定 rewarm 前不恢复 T 决策。 |
| cycle/allocation | `T_CYCLE_LEASE_CONFLICT` | cycle processing lease 被其他 fence 持有 | 不重复提案、不创建并行 attempt；等待原持有者或按指纹规则接管。 |
| cycle/allocation | `T_CYCLE_INPUT_STALE` | cycle 输入已过期或指纹改变 | 终结为 `ABORTED_STALE`，不补 latest quote、不重用原 attempt。 |
| cycle/allocation | `T_ALLOCATION_LEASE_CONFLICT` | allocation/admission lease 冲突 | 不路由半批；保留 durable batch，按 fence 恢复。 |
| cycle/allocation | `T_ALLOCATION_INPUT_STALE` | allocation 输入/账户水位已改变 | 整批 `SUPERSEDED`/`EXPIRED`，重新走分配，不部分提交。 |
| cycle/allocation | `T_ALLOCATION_EXPIRED` | allocation/admission TTL 已到期 | 终结批次，释放未使用 claim；不复用旧决策。 |
| gate/capacity | `T_ENTRY_CUTOFF_REACHED` | 已到 14:50 entry cutoff | 不创建/replace ENTRY；退出和对账按既有事实继续。 |
| gate/capacity | `T_MIN_EXIT_WINDOW_UNAVAILABLE` | 剩余时间不足 7 分钟退出窗口 | 拒绝新 ENTRY；不以概率或旧价假设可闭环。 |
| gate/capacity | `T_CANDIDATE_EXPIRED` | candidate TTL 已到期 | 标记过期，不进入审批/分配；新行情重新计算。 |
| gate/capacity | `T_APPROVAL_EXPIRED` | approval TTL 已到期 | 审批失效，禁止直接下单；重新校验并重新审批。 |
| gate/capacity | `T_GATE_PRICE_DEVIATION` | 最新价偏离 policy 允许边界 | `DELAY/REJECT` 并重新评分；不静默改价或改量。 |
| gate/capacity | `T_GATE_SPREAD_EXCEEDED` | spread 超过 Gate 边界 | `DELAY/REJECT`；TTL 内重新走完整 Gate，否则过期。 |
| gate/capacity | `ACCOUNT_RECONCILE_REQUIRED` | 账户事实/订单事实无法证明守恒 | 阻断所有新 BUY，保留已知退出义务，完成全量对账。 |
| gate/capacity | `ACCOUNT_SNAPSHOT_STALE` | 账户 snapshot 超过 90 秒或不完整 | 阻断风险增加；不猜现金、可卖量或冻结量。 |
| gate/capacity | `ACCOUNT_CAPACITY_EXCEEDED` | 账户 T capacity 不足 | 拒绝或按已冻结规则 CAP，记录未买/少买原因；不借用其他环境额度。 |
| close/order | `T_CLOSE_SAFETY_BUFFER` | 已进入 14:57 close safety buffer | 取消/终结未成交 ENTRY，停止新风险增加；允许安全退出和对账。 |
| close/order | `T_OVERNIGHT_LIMIT_REACHED` | 隔夜 batch/最坏成本超过上限 | 阻断新 ENTRY，告警并 reconcile；不能伪造闭环。 |
| close/order | `T_OVERNIGHT_CARRY` | 收盘仍有真实未闭合 ENTRY/EXIT 义务 | 继续占 capacity，次日恢复同一 `EXIT_PLAN/plan_id`；不复制计划。 |
| close/order | `T_ENTRY_ORDER_EXPIRED` | ENTRY 单达到 30/60 秒 policy 终点 | 终结或撤单对账；cutoff 后不得 replace。 |
| close/order | `T_EXIT_ORDER_EXPIRED` | EXIT 单达到 30/90 秒 policy 终点 | 按 authoritative 终态收敛；unknown 时只 reconcile，不重复下单。 |
| close/order | `ORDER_CANCEL_UNCONFIRMED` | 撤单未获得权威终态 | 标为 unknown，保留 capacity/position；禁止 replace 或重发。 |
| close/order | `ORDER_RESULT_UNKNOWN` | 委托结果无法由 Agent/券商事实证明 | 只做 broker full snapshot reconcile；不释放义务、不重发。 |
| close/order | `ORDER_REPLACE_LIMIT_REACHED` | replace 次数达到 policy 上限 | 终结新 replace；保留未决事实并按订单终态/对账处理。 |

## 6. 行情、快照、cycle/allocation 硬阈值冻结

以下阈值是 P0 冻结版本 `v1`，运行时不可用隐式默认值覆盖；任何变更必须形成新版本、重新
验证并经过对应阶段门。

| 名称 | 冻结值 | 触发动作 | 依据/安全理由 |
|---|---:|---|---|
| per-symbol delta ring | 每标的 `4096` 个 accepted ticks，最长覆盖 `600s` | 任一未消费 cursor 被覆盖即 `T_MARKET_RING_OVERFLOW`，标的窗口失效、清空并 rewarm | 保证 reducer 只能处理连续 accepted tick，绝不由 latest quote 补 FSM |
| reducer lag | `512 ticks` 或 `2s`，先到者 | `T_REDUCER_LAG_EXCEEDED`，阻断新 T ENTRY，resync/rewarm 后恢复 | 限制逐 tick 因果链的积压和决策时延 |
| market capture freshness | `10s` | 超时标记 stream/snapshot 非 ready，停止新候选/风险增加 | 市场上下文必须是新鲜且带 generation/fence 的事实 |
| future timestamp skew | `5s` | 超过允许未来偏差的 tick 不进入 accepted stream，记录 market reason | 防止时钟错误或未来数据污染回测/在线因果 |
| symbol quote/snapshot freshness | `3s` | 该标的 Gate 和新候选停止；不使用旧价触发退出 | 单票报价陈旧不能被组合平均值掩盖 |
| account snapshot freshness | `90s` | `ACCOUNT_SNAPSHOT_STALE`，阻断新 BUY，不猜 cash/available quantity | 资金、冻结和 T+1 事实必须可证明 |
| candidate/approval TTL | `30s` | 过期 candidate/approval 终态化，不能直接分配或下单 | 审批期间必须重新校验最新行情、账户和模型绑定 |
| cycle processing lease | `10s`；每 `3s` 续租 | lease 冲突不重复提案；输入仍完全相同才可用新 fence 续接，否则 `ABORTED_STALE` | attempt 与 decision_key 分离，避免崩溃重复提案 |
| ENTRY cycle input TTL | `15s` | 超时 `T_CYCLE_INPUT_STALE`，不补 latest quote | 决策只能使用点时且仍有效的输入 |
| allocation/admission lease | `10s`；每 `3s` 续租 | 冲突不路由半批；输入一致才续接原 batch | 账户 claim 必须由 durable fence 持有 |
| allocation/admission TTL | `30s` | 整批 `SUPERSEDED`/`EXPIRED`，重新走排序/容量/Gate | 避免旧账户水位下的部分提交 |

到期接管的唯一条件是 `fingerprint` 完全相同且 owner/environment、config/policy、账户水位和
输入版本仍有效；否则分别终结为 `ABORTED_STALE`、`SUPERSEDED` 或 `EXPIRED`。绝不用当前
latest quote 填补丢失 tick 或延长旧 TTL。

## 7. 跨域风险增加优先级冻结

跨域准入只决定“谁先获得风险增加锁和容量检查”，不决定成交先后、不改变券商队列，也不
缓存或预留一份脱离账户真源的资金：

```text
隔离 / 对账 / 撤单 / 紧急停止
  > ExitPlan SELL
  > BUY admission sequencer
  > T 内部协调
  > 配置 / Universe / checkpoint
```

BUY sequencer 的业务优先级固定为：

```text
MANUAL_COMMAND
  > ENTRY_PLAN
  > BOARD_ASSISTANT_EXECUTION
  > T_ASSISTANT_EXECUTION
  > STRATEGY_RUN
```

同一业务优先级内按 `intent.created_at`、`owner_type`、`owner_id`、`intent_id` 稳定升序。
安全隔离、对账、撤单和紧急停止优先于全部风险增加请求；顺序本身不是成交优先级，也不能
绕过 T+1、涨跌停、可卖量、现金、capacity 或 Gate。

## 8. ENTRY/EXIT order policy 冻结

版本固定为 `TEntryOrderPolicy v1` 与 `TExitOrderPolicy v1`，两者都只使用 FIX_PRICE/限价，
不允许市场单或无限追价。

| policy | 参考价与边界 | 单委托存活 | replace | 总时限 | 额外动作 |
|---|---|---:|---:|---:|---|
| `TEntryOrderPolicy v1` | BUY 以 ASK1 为参考，最多向上 `+30bps`，再裁到涨停价/价格笼子 | `30s` | 最多 `1` 次 | `60s` | `14:50` 后不创建、不 replace ENTRY。 |
| `TExitOrderPolicy v1` | SELL 以 BID1 为参考，最多向下 `-30bps`，再裁到跌停价/价格笼子 | `30s` | 最多 `2` 次 | `90s` | replace 前必须有旧单权威终态，只下剩余量。 |

replace 的必要条件是旧单已经由 Agent/券商事实进入权威终态、剩余量由 durable order fact
计算且结果非 unknown。`ORDER_RESULT_UNKNOWN` 或 `ORDER_CANCEL_UNCONFIRMED` 时禁止 replace；
不能通过换 client order id、换 owner 或重编码规避次数和总时限。

## 9. 收盘和隔夜冻结

| 参数 | 冻结值 | 行为 |
|---|---:|---|
| `entry_cutoff` | `14:50`（交易所本地时间） | 之后不创建或 replace ENTRY。 |
| `minimum_exit_window` | `7 分钟` | 任何新 ENTRY 必须能在该窗口内按 policy 留出退出机会，否则 `T_MIN_EXIT_WINDOW_UNAVAILABLE`。 |
| `close_safety_buffer` | `180 秒`，从 `14:57` 起 | 停止新风险增加，未成交 ENTRY 进入取消/终结和对账；安全 EXIT 继续按权威事实处理。 |
| overnight carry | 默认不允许目标隔夜；真实例外最多 `1 个 batch` | 以最坏 ENTRY 成本计算，硬上限 `12,000 元`。超限后账号阻断新 ENTRY、告警并对账，不能伪造闭环。 |

隔夜 carry 继续占用 capacity 和未覆盖义务；次日恢复原 `EXIT_PLAN/plan_id`，不新建替代
ExitPlan、不修改历史成交、不把未成交假设为已退出。收盘策略只改变风险增加，不得阻断为安全
退出和事实对账所必需的动作。

## 10. CANARY + MANUAL_CONFIRM → AUTO 退出门

`CANARY` 和 `MANUAL_CONFIRM` 是两个正交维度。P0 只冻结从受限的
`CANARY + MANUAL_CONFIRM` execution 创建一个新的 AUTO successor 的退出门；不得原地把旧
execution 改成 AUTO，也不因满足数量门槛而跳过 owner、账户或 QMT 门禁。既有 ExitPlan 仍由
独立退出链处理。

必须同时满足：

- 最多 `3` 个标的、最多 `1` 个 active batch；每批最多 `100` 股；每单金额不超过 `20,000` 元；
  总 T 暴露不超过账户 `2%`。
- 至少 `20` 个完整闭环、至少 `5` 个交易日、覆盖至少 `3` 个标的。
- `0` 重复单、超现金、超昨日老仓、T+1 违规、owner 串写或 environment 串写。
- `0` 个 unknown/reconcile 未解决事实，`0` 个不应存在的活动 ExitPlan 遗留。
- `100%` 可追溯：`intent → order → trade → ExitPlan → batch`；报告 p95 收敛不超过 `30s`。
- Engine/API/QMT 断连、乱序回报、lease 过期、撤单未确认、收盘和恢复故障演练完成，人工审批
  和操作记录完整。

任何一项失败，授权保持 `MANUAL_CONFIRM`，阻断 AUTO successor；不能用“没有观察到问题”代替
闭环证据。

## 11. 只读审计与当前脱敏结果

### 11.1 命令和输出边界

P0 审计命令：

```powershell
python ops\t-assistant-p0-audit.py --format markdown
```

P1 切换 gate 命令：

```powershell
python ops\t-assistant-p0-audit.py --format markdown --require-ready
```

`--require-ready` 只作为 P1 切换 gate：工具检查 owner/legacy owner、审批、pending、outbox、
correlation、protocol、unknown result、未应用 event、batch 和 ExitPlan 等事实；任一 owner 冲突、
legacy 未证明或义务未排空都会返回 not ready。账户 snapshot 是否完整属于 P1 切换 runbook 的
人工/后续门，不是本 P0 工具的表检查项。脚本必须只执行聚合 SELECT/只读 ORM 查询，输出仅有计数、
状态分组、reason 和 readiness；不得打印账户 id、订单 id、client_order_id、券商账号、设备路径
或任何密钥，也不得修改任何记录。

### 11.2 2026-09-03 开发库事实

以下是脱敏后的聚合结果，不含任何 ID，不能被解释为已经完成迁移：

| 聚合项 | 数量/结果 | 安全解释 |
|---|---:|---|
| enabled legacy config | `1` | 存量配置仍在运行基线内。 |
| active legacy T run | `1` | 已绑定正常，但仍是旧 StrategyRun owner。 |
| active candidate | `0` | 当前没有活动候选可进入新路径。 |
| T `AWAITING_APPROVAL` | `2` | 两条均在 terminal run，但缺 candidate identity；需逐条证明，不得猜 owner。 |
| nonterminal intent | `2` | 仍有两条非终态 intent；必须按 durable chain 和券商事实逐条收敛。 |
| nonterminal pending | `0` | 当前没有非终态 pending。 |
| queued outbox | `0` | 当前没有待投递 outbox。 |
| unknown result | `0` | 当前没有记录为 unknown 的结果。 |
| unapplied runtime event | `0` | 当前没有未应用 runtime event。 |
| open/unbalanced batch | `0` | 当前没有开放或数量不平衡 batch。 |
| outstanding T ExitPlan obligation | `1` | 其中 `1` 条 source run orphan，`enabled=false`、状态 `ERROR` 且非终态，仍有 remaining obligation；属于活动义务 blocker。 |
| legacy T intent owner reference invalid | `295` | `owner_type` 不在冻结枚举、`owner_id` 为空，或 `STRATEGY_RUN` 的 `owner_id` 与 `strategy_run_id` 不一致；分布为 `292 EXPIRED`、`2 AWAITING_APPROVAL`、`1 FILLED`。这些记录的 run 存在，但 owner reference 未通过规则。 |
| terminal invalid T ExitPlan | `3` | 均为 `CANCELLED`；仍需留痕，不能批量删除。 |

结论：P1 readiness 为 `false`。账号必须保持 fail-closed；处置固定为逐条使用 broker full
snapshot、Agent inbox、durable order/correlation、fill、batch 与 ExitPlan chain 证明后，才能
终态化或迁移。禁止批量 SQL 猜 owner、删除记录或重发命令。审计输出和本文都不修改上述事实。

审计工具单测已通过：`9 passed`。该单测验证只读查询、脱敏聚合、异常 fail-closed、
`--require-ready` gate 和无写入边界；它不改变开发库事实。

## 12. P0 基线测试证据

基准 commit：`cd12b7e7975acb1936f1c0b022b766689df02cf1f`

基线命令覆盖以下 13 个文件：

```text
tests/application/test_t_trade_v3_application.py
tests/engine/unit/trading/test_t_trade_opportunity_engine.py
tests/engine/unit/test_t_trade_v3_approval.py
tests/engine/unit/test_strategy_base.py
tests/engine/unit/test_strategy_executor.py
tests/domain/test_exit_plan.py
tests/engine/unit/strategies/test_ashare_intraday_t_assistant.py
tests/infrastructure/test_exit_plan_trade_intent_ownership.py
tests/infrastructure/test_runtime_owner_audit_safety.py
tests/engine/test_report_processor_reconciliation.py
tests/engine/test_t_trade_runtime_events.py
tests/qmt_agent/test_account_unavailable_runtime.py
tests/qmt_agent/test_live_broker_reconnect.py
```

结果：`405 passed, 8 warnings, 16.20s`。警告仅为现有 deprecation/cache permission；没有测试
失败。证据映射如下：

| 能力 | 覆盖测试 |
|---|---|
| V3 规则、candidate/FSM、审批和普通策略纯决策 | `test_t_trade_v3_application.py`、`test_t_trade_opportunity_engine.py`、`test_t_trade_v3_approval.py`、`test_strategy_base.py`、`test_ashare_intraday_t_assistant.py` |
| StrategyRun 执行、intent/pending/outbox owner 关联 | `test_strategy_executor.py`、`test_runtime_owner_audit_safety.py` |
| ExitPlan、T+1 和 SELL owner | `test_exit_plan.py`、`test_exit_plan_trade_intent_ownership.py` |
| 乱序/迟到报告、runtime event 幂等收敛 | `test_report_processor_reconciliation.py`、`test_t_trade_runtime_events.py` |
| QMT 账户不可用、broker 断连恢复与 unknown 安全 | `test_account_unavailable_runtime.py`、`test_live_broker_reconnect.py` |

这是当前实现的安全回归基线；它证明既有路径可重复通过，不证明 protocol 1.2、独立 T owner、
delta reducer、allocation batch 或 AUTO 已实现。

## 13. P0 验收矩阵

| Task | owner | 精确 code location | 验证 | 决策 |
|---|---|---|---|---|
| `TTA-P0-01` | Engine owner：`t_trade_global_monitor.py`、`strategy_manager.py`、`strategy_executor.py` | `apps/engine/src/quantx_engine/t_trade_global_monitor.py`、`strategy_manager.py`、`strategy_executor.py`、`packages/infrastructure/src/quantx_infrastructure/models/strategy_run.py` | V3 application、strategy executor、runtime event 基线 | 旧 T StrategyRun 新功能冻结，只做安全修复和义务排空。 |
| `TTA-P0-02` | 主代理/架构维护者 | 本文第 2 节的 DB、contracts、domain、application、infrastructure、Engine、API、Worker、QMT、GraphQL/Web 人工表；scanner 仅候选 | 分层人工复核 + 13 文件基线 | 清单以人工表为准，未确认命中不得作为迁移完成。 |
| `TTA-P0-03` | Infrastructure owner：repository、obligation、ExitPlan owner audit | `trade_intent_repository.py`、`runtime_obligations.py`、`exit_plan_execution_owner.py`、`agent_runtime.py` | `test_runtime_owner_audit_safety.py`、`test_exit_plan_trade_intent_ownership.py`、只读 audit | 记录 2026-09-03 聚合事实；不确定记录 fail-closed，逐条 reconcile。 |
| `TTA-P0-04` | Contracts/API/QMT owner | `packages/contracts/src/quantx_contracts/agent.py`、`apps/api/src/quantx_api/agent_api.py`、`apps/qmt-agent/src/quantx_qmt_agent/runtime.py`/`broker.py`/`miniqmt/local_agent.py` | agent protocol、QMT disconnect/reconnect 基线 | 冻结唯一 1.2 白名单和原子切换 runbook；P0 不切换。 |
| `TTA-P0-05` | Engine/market owner | `apps/engine/src/quantx_engine/t_trade_global_monitor.py`、`t_trade_runtime.py`、`t_trade_coordination.py`；To-Be reducer/cycle/allocation 落点见本文第 6 节 | V3 opportunity、runtime event、snapshot/断连基线 | ring/lag/freshness/lease/TTL 数值固定；失效只 fail-closed 和 rewarm。 |
| `TTA-P0-06` | Application/risk/order owner | `packages/infrastructure/src/quantx_infrastructure/services/account_capacity_service.py`、`trade_command_service.py`、`runtime_obligations.py`；API/GraphQL liquidation 与 T trade 入口 | approval、ExitPlan ownership、QMT reconnect 基线 | 优先级、限价 policy、14:50、7 分钟、180 秒、隔夜和 CANARY 门固定。 |
| `TTA-P0-07` | QA/主代理 | 上述 13 个测试文件及本文第 12 节证据 | 基准 commit 上 `405 passed` | V3/普通策略/ExitPlan/T+1/乱序/QMT 断连基线通过；目标能力仍待后续阶段实现。 |

## 14. P1 启动前置与禁止事项

P1 只有在以下条件全部可执行时才能开始：

1. 本文和[实施方案](多标的做T助手新架构开发实施方案.md)的 identity、owner、协议、reason、
   threshold、order/close/canary 决策保持一致。
2. 只读审计可重复运行，输出脱敏，`--require-ready` 能以失败状态阻断未准备账号；当前已知
   的 legacy invalid owner、orphan ExitPlan 和缺候选审批仍按逐条 chain 对账。
3. 所有 1.1 outbox/result 的排空与 unknown reconcile 手册已由值班人执行，切换维护窗口、
   备份和全量 broker snapshot 路径已确认。
4. P1 变更清单覆盖本文第 2 节所有公共表和代码边界，并明确删除旧 fallback 的单一原子提交；
   不允许“先写新 owner、旧路径继续写”的双 producer。
5. 迁移前影子查询证明 owner pair、environment、client_order_id、cycle/allocation、BUY
   fill→ExitPlan 和 event identity 不存在未处置冲突。

P0 明确不启动：建表/迁移、协议改码、客户端 codegen、独立 T execution、逐 Tick reducer、
allocation/admission 实现、真实订单、AUTO 授权和模型训练/上线。后续阶段必须引用本文件的
冻结值，不得为实现便利增加默认 run、双协议、metadata owner、latest quote 补洞、无限追价或
不受控兼容层。

## 15. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1.0 | 2026-09-03 | 固化 P0 As-Is 边界、分层 identity、owner/DB 目标约束、protocol 1.2、reason code、硬阈值、跨域准入、订单/收盘/灰度门、只读审计结果和基线测试证据。 |
