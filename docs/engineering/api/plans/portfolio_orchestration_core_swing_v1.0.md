# 通用仓位/资金编排层（核心+Swing）v1.0

## 0. 目标

建立后端统一的“仓位/资金编排入口”，通过 `StrategyInput` 输入一份标准化 `execution_profile`：

- 方向许可：`core / swing / locked_core`
- 资金与额度：单笔金额、单笔数量、日内买卖量
- 决策可观测性：统一阻断原因、锁定状态、冷却令牌

保持策略纯函数：策略只输出 `TradeIntent[]` 与 `RuntimeStatePatch`，不接触 broker 真实状态。

## 1. 设计原则

### 1.1 资金与仓位应“合并输出，分域计算”

建议采用“合并输出、分域计算”：

- **分域计算**：`PositionAllocationCoordinator`（仓位）与 `CapitalConstraintLayer`（资金）在内部分工。
- **合并输出**：统一为 `PortfolioExecutionProfile` 回传给策略。

这样可保证未来策略只消费一份 `execution_profile`，并保留了能力边界清晰和可测性。

### 1.2 不能替代 OrderRisk

编排层给的是“策略可尝试的上限”，不是“最终成交的真相”：

- 真正的可买可卖、涨跌停、T+1、100 股等仍由 `OrderSizer / OrderRiskLayer` 决定。
- 回测与实盘路径仍保留订单事件驱动的状态闭环。

## 2. 组件与数据模型

### 2.1 输入

- `StrategyInput` 字段：
  - `risk_caps`
  - `position_profile`
  - `portfolio_state`
  - `bucket_ledger`
  - `runtime_state`（来源：`strategy.state.to_dict()`）
  - `parameters`
  - `market_context`

### 2.2 核心组件

- `PortfolioOrchestrationLayer`
  - 入口方法：`build_profile(...) -> PortfolioExecutionProfile`
  - 责任：聚合并输出统一能力画像
- `PortfolioExecutionProfile`
  - 字段（v1.0）：
    - `allow_core_buy`, `allow_core_sell`
    - `allow_swing_buy`, `allow_swing_sell`
    - `allow_locked_core_sell`
    - `max_order_cash`, `max_order_qty`
    - `max_daily_spend_cash`, `max_daily_sell_qty`
    - `daily_buy_used`, `daily_sell_used`
    - `cooldown_tokens`, `day_state`, `constraints_version`, `rejected_reasons`, `source_layer`

### 2.3 当前实现映射

- 字段来源顺序：
  - `risk_caps` > `position_profile` > `parameters` > 运行时默认值
- `rejected_reasons` 默认至少包含：
  - `risk_kill_switch`
  - `position_profile_disallow_buy`
  - `zero_max_order_cash`
- `constraints_version` 当前固定为 `v1.0`

## 3. 链路接入（当前代码路径）

### 3.1 StrategyExecutor

- `StrategyExecutor._build_strategy_input()`：
  1. 构建 `market_context`
  2. 构建 `risk_caps`
  3. 构建 `position_profile`
  4. 调用 `_build_execution_profile(...)` 生成 `PortfolioExecutionProfile`
  5. 写入 `StrategyInput.execution_profile`
- `_build_execution_profile(...)`：
  - 调用 `PortfolioOrchestrationLayer().build_profile(...)`
- `_record_strategy_output_trace()`：
  - 结构化记录 `execution_profile` 到 `DecisionTrace`

### 3.2 现有策略对齐

- `AshareSupermarket` 与 `PullbackGrid` 当前逐步过渡：
  - 继续支持 `position_profile` 读取；
  - 新修复项要求优先采用 `execution_profile` 做方向与预算预检；
  - 不允许策略自己读取真实账户可买可卖做最终判断。

## 4. 与 `Pullback Grid v20` 的 v1.0 约束协同

### 4.1 方向与额度入场门

- BUY：
  - 以 `execution_profile.allow_swing_buy` 为首要方向许可；
  - 额度不足时写 `block_reason = max_budget_exhausted` 或 `no_budget`。
- SELL：
  - 以 `execution_profile.allow_swing_sell` 为基础放行；
  - `allow_swing_sell` false 时输出 `disabled_by_profile`。
- 锁仓位：
  - `execution_profile.allow_locked_core_sell` 控制是否允许定向用途流程下的 `locked_core` 销售。

### 4.2 风险与异常降级

- 编排层只做“保守降级”：
  - 无法构建 profile 时不抛异常，返回默认保守 profile。
- 策略按 `block_events` 显式写入原因，而不是静默返回空意图。

## 5. 决策追踪与审计字段

### 5.1 必须写入 `block_events` 的字段

- `source`: `BAR` / `TICK`
- `bar_key`
- `grid_id`
- `grid_level_index`
- `block_reason`
- `budget_state`: 余额、当日额度剩余、历史用量
- `side`（BUY/SELL）
- `intent_key`

### 5.2 统一归档到 `DecisionTrace`

- `execution_profile`: 每次决策都写入完整 profile 快照。
- `output_summary.trace_payload`: 记录本次阻断类型与可恢复动作。
- 便于 v20 回测的按 `grid_id`/`reason` 聚合复盘。

## 6. 分步实施（v1.0）

1. 文档冻结（本阶段）
2. 核对 `packages/domain/src/quantx_domain/trading/orchestration.py`：
   - `PortfolioExecutionProfile` 字段完整性
   - 默认值与兜底策略
3. 核对编排入口：
   - `StrategyExecutor._build_execution_profile`
4. 核对编排归档：
   - `StrategyExecutor._record_strategy_output_trace`
5. Pullback Grid 接入：
   - BUY/SELL 触发读取执行画像
   - 预算与方向不足写入统一 `block_reason`
6. 回归对照 v20：
   - 无 BUY 场景与重复 SELL 场景修复

## 7. 验收标准（冻结后验收门槛）

- `execution_profile` 在策略输入链路中可观测、可追溯，且字段不为空（或有明确保守默认）。
- `Pullback Grid` 使用该画像后的结果满足：
  - v20 不出现“仅有卖出无买入”异常。
  - 同一卖位日内不得重复 SELL，除非新买重建解锁。
- `TradeIntent` 序列可按 block_reason 回放，能解释拒绝/阻断原因。
- 所有真实资金与持仓变化仍然只在 broker 回报链路内处理。
