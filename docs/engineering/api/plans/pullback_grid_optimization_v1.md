# Pullback Grid v20 回测问题优化计划（v1.0）

## 0. 目标

冻结一个可直接评审、可复用的修复设计，先修复 `Pullback Grid` 在 v20 快照中的关键回测行为异常，再落地代码：

1. 网格可出现“仅卖不买”。
2. 同一卖位一天内重复卖出。
3. 买入 lot 与卖位绑定不稳定，导致账本归因和防重放失效。

## 1. 问题现象

- 现象 A：某些 v20 片段出现仅 `SELL`（缺少 `BUY`），而行情与网格结构仍有可触发买位。
- 现象 B：同一个 SELL 网格位在单日内被反复触发，出现“一天五个卖出”等异常频次。
- 现象 C：buy 阶段创建的 `GridInventoryLot` 未强绑定目标卖位，导致匹配时出现“非目标卖位接单”。
- 现象 D：重复触发与阻断原因未形成统一审计字段，回放时难以还原“为何未触发”。

## 2. 根因

- 触发链路分裂：早期实现里 BUY 判定对 BAR/TICK 的覆盖不完整，导致某些 BAR 时段没有可执行入口。
- 卖位状态回退过快：SELL 完全成交后直接恢复到可再次触发状态，缺少“日内防抖”状态位。
- 绑定规则过宽：未强制 `lot.target_sell_level_id/index`，`_lot_can_feed_sell` 对空绑定做了兼容，导致目标错配。
- 幂等约束缺失：缺少“同 bar 同网格同方向只触发一次”的统一 key，导致重复意图被放行。
- 决策观测层不足：`block` 轨迹没有统一 `block_reason` 与触发源/网格位点上下文。

## 3. 设计方案

### 3.1 触发链路改造（BAR/TICK 双入口）

- `BAR` 与 `TICK` 均复用 `_collect_buy_intents`。
- 所有买入候选判断都走统一入口，入参包含：
  - `source`: `BAR` 或 `TICK`
  - `bar_key`: `YYYYMMDDHHmm`（用于同 bar 去重）
  - `allow_swing_buy`: 来自编排/仓位 profile 的最终方向许可
- `BAR` 主要用于趋势与网格基准更新，`TICK` 用于快速复核与精细触发，但二者输出同一种 BUY 生成逻辑。

### 3.2 SELL 防重入与“同卖位日内一次触发”

- 在 `GridLevel` 引入以下字段并持久化：
  - `is_day_locked`
  - `sell_last_filled_date`
  - `sell_lock_reason`
- `SELL` 满仓成交后触发“日内锁定”：
  - 标记 `is_day_locked = True`
  - `sell_last_filled_date = today`
  - `sell_lock_reason = "locked_after_full_fill"`
  - 状态机不直接回到可重复触发态。
- 锁定策略默认优先级高于库存可卖性：
  - 若处于日内锁定态，直接阻断并记录 block reason，不再发新 SELL intent。
- 解锁策略：
  - 当有“新买重建”发生（新增买入 lot 成功）且该新 lot 绑定到对应卖位后，调用 `_unlock_sell_level_for_rebuild(sell_level_id)`。
  - 解锁后才允许卖位再次进入可触发路径。

### 3.3 LOT 与卖位绑定修复

- `_create_inventory_lot_from_buy` 必须绑定目标卖位：
  - `target_sell_level_id`
  - `target_sell_level_index`
- `_resolve_target_sell_level_for_buy_grid` 规则：
  1. 优先 `level_index + 1`（同买位上对应上方卖位）
  2. 兜底到当前 instrument 的下一个可用 SELL 网格（按距离触发价从近到远）
- `_lot_can_feed_sell` 改为强校验：
  - 无目标绑定（ID 与 index 同时为空）直接返回 `False`
  - 目标 ID/索引不匹配直接返回 `False`
  - 仅允许来自 `bucket=swing` 且仍有剩余份额的 lot 参与 sell 匹配

### 3.4 幂等键与单 bar 一次触发约束

- 每次 BUY/SELL 意图记录：
  - `intent_key = "{source}:{bar_key}:{grid_id}:{grid.level_index}:{side}:{last_intent_id}"`
  - 对比 `grid.last_intent_bar_key / last_intent_source / last_intent_side`
- 同一 `grid + source + side + bar_key` 二次重复：
  - 不发新 intent
  - 写入 `block_reason = duplicate_intent_same_bar`

### 3.5 决策阻断与原因码统一化

`trace_payload` / `block_events` 统一记录以下最小字段集合，便于回放与告警聚合：

- `reason`: 触发/阻断主因（人类可读）
- `block_reason`: 枚举类型之一（见 3.6）
- `grid_id`: 触发网格 ID
- `grid_side`: `BUY/SELL`
- `grid_level_index`
- `source`: `BAR` / `TICK`
- `bar_key`
- `trigger_price`
- `current_price`
- `lot_id`（若存在）
- `intent_key`
- `timestamp`
- `sell_level_lock_state`（包含 `is_day_locked` 与 `sell_last_filled_date`）

#### 3.6 统一 `block_reason` 枚举（v1.0）

- `disabled_by_profile`
- `duplicate_intent_same_bar`
- `sell_level_day_locked`
- `target_not_bound`
- `sell_inventory_mismatch`
- `insufficient_matching_lot`
- `profile_or_caps_disallow`
- `invalid_grid_or_price`
- `max_budget_exhausted`
- `tick_invalid`

## 4. 状态机（v1.0）

以下是 BUY/SELL 的建议状态机，落库到 `grid.status` 与锁定标志：

- BUY
  - `PLANNED -> MONITORING -> PENDING -> PARTIAL_FILLED`
  - `PARTIAL_FILLED -> PENDING -> FILLED`
  - `FILLED -> LOCKED_BY_REBUILD`（等待对应 SELL 卖位重置）
  - 任何状态下重复触发统一进 `BLOCKED_DUPLICATE_INTENT`，不更新 pending

- SELL
  - `PLANNED -> PENDING -> PARTIAL_FILLED -> FILLED`
  - `FILLED -> LOCKED_BY_DAY`（触发后必须等待新买重建解锁）
  - `LOCKED_BY_DAY -> PLANNED`（由 `_unlock_sell_level_for_rebuild`）
  - 任一阶段遇到预算/方向/账户约束失败进入 `BLOCKED_*`

## 5. 决策追踪（Decision Trace）

### 5.1 记录规则

- 每个 `StrategyOutput.trace_payload` 必须返回：
  - `reason`（主决策）
  - `block_events`（列表）
  - `grid_book_snapshot`（轻量摘要，如 `bar_key/intent_count/lock_count`）
- 每条 block event 同时写入 `DecisionTrace` 级 `execution_profile` 与 `runtime_state_patch`，便于后续回放。

### 5.2 示例（SELL 被阻断）

```json
{
  "bar_key": "202605170930",
  "source": "TICK",
  "grid_id": "sell-3",
  "grid_level_index": 3,
  "grid_side": "SELL",
  "block_reason": "sell_level_day_locked",
  "lot_id": "lot-uuid",
  "intent_key": "TICK:202605170930:sell-3:3:SELL",
  "sell_level_lock_state": {
    "is_day_locked": true,
    "sell_last_filled_date": "2026-05-17"
  }
}
```

### 5.3 LOT 与 sell 绑定证据

- `GridInventoryLot` 的落地字段必须包含：
  - `target_sell_level_id`
  - `target_sell_level_index`
  - `source_level_id`
  - `source_level_index`
- 当 `lot` 与卖位不匹配时，不得进入 `release_events` 与卖位放单。

## 6. 回归用例（v20）

### UC1：仅有 SELL 无 BUY

- 输入：v20 复现实验快照（BAR+TICK）
- 断言：
  - 在同一回撤阶段，`BUY` 与 `SELL` 的意图序列不应长期单向偏移。
  - BAR 与 TICK 均可产生 BUY（当触发条件满足时）。

### UC2：同卖位日内重复触发

- 输入：单日重复触发序列
- 断言：
  - 同一 `sell` 位第一次 `FILLED` 后当天不得再产生新的 SELL intent。
  - 解锁前再次触发时输出 block reason `sell_level_day_locked`，并不生成下单 intent。

### UC3：买入重建后才允许卖位再触发

- 输入：SELL 满仓后进行补仓买入
- 断言：
  - 出现新买入 lot 后，目标卖位解锁。
- 解除后的首次 SELL 触发必须关联新 lot 的绑定目标。

### UC4：lot 与卖位一致性

- 输入：包含非目标和目标 lot 的混合库存
- 断言：
  - `_lot_can_feed_sell` 无目标绑定返回 false。
  - `target` 不一致返回 false，不能出现跨卖位归属成交。

### UC5：阻断审计可回放

- 输入：预算不足、重复触发、库存不足场景
- 断言：
  - `trace_payload.block_events` 有完整 `block_reason + source + bar_key + grid_id + lot_id`。

## 7. v20 回放验收标准（实现前置）

### 7.1 输出对比清单

- `TradeIntent` 序列（时间顺序）
- 每条 intent 的 `direction/side/trigger_price/grid_id`
- `GRID SELL` 每日触发计数（按 `grid_id` 聚合）
- `decision_trace` 中 `block_reason` 分布
- `inventory` 匹配成功率与 `target_sell_level_*` 绑定覆盖率

### 7.2 失败判定阈值

- 发现 `SELL` 占比 > 90% 的连续单向段且无 BUY，视为阻断。
- 单卖位日内重复触发次数 > 1 直接 fail（除非中间出现新买入重建和解锁）。
- 任意 `SELL` 发生时对应的 `lot` 若无目标绑定，立即 fail。

## 8. 交付约束（不可回退）

- 不允许策略直接读取 broker 真实持仓或资金。
- 不允许把 `SELL` 的历史成交状态直接重置为 `PLANNED`。
- 不允许无目标 lot 参与任何卖位匹配。
- 不允许无 block reason 的静默降级。
