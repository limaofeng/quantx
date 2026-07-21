**文档目标:** 定义 A 股单标的策略的仓位调节层。仓位调节层读取环境快照、前置风控上限 `RiskContextCaps` 和当前策略状态，产出 `PositionAdjustmentProfile`，用于调整 Sigmoid 动态天平的仓位边界、core/swing 拆分、现金缓冲和交易活跃度。仓位调节层不生成订单，不直接修改真实账户状态。

---

## 0. 核心定位

仓位调节层回答一个问题：**在当前环境和风险约束下，动态天平应该有多大的活动空间？**

环境层负责描述外部世界，风控层负责给出硬约束，仓位调节层把这些信息翻译为动态天平可用的参数。

仓位调节层只做：

- 调整 `MinPct / MaxPct`
- 调整 `core_share / swing_share`
- 调整 `cash_buffer_pct`
- 调整 `balance_beta / inventory_gamma`
- 调整网格活跃度和加仓节奏

仓位调节层不做：

- 不输出 BUY / SELL
- 不计算 A 股合法数量
- 不判断 miniQMT 成交状态
- 不直接改 bucket 真实持仓

---

## 1. 输入与输出

### 1.1 输入

输入包括：

- `MarketContextSnapshot`
- `RiskContextCaps`：前置风控输出的最大仓位、现金缓冲、禁买/只降风险/熔断等上限
- 当前策略状态：趋势状态、仓位阶段、动态基准、grid index
- 当前组合状态：总仓位、core/swing/locked_core 归因、现金缓冲
- 实例风险配置

### 1.2 输出

输出 `PositionAdjustmentProfile`。

```json
{
  "profile": "DEFENSIVE",
  "min_position_pct": 0.0,
  "max_position_pct": 0.35,
  "target_cash_buffer_pct": 0.35,
  "core_share_min": 0.70,
  "core_share_max": 0.95,
  "swing_max_pct": 0.0,
  "balance_beta_multiplier": 0.45,
  "inventory_gamma_multiplier": 1.40,
  "grid_step_multiplier": 1.50,
  "allow_core_buy": false,
  "allow_swing_buy": false,
  "allow_swing_sell": true,
  "reason_tags": ["market_panic", "sector_broken"]
}
```


### 1.3 与双阶段风控的关系

仓位调节层只读取前置风控 `RiskContextCaps`，不读取后置订单风控的 `OrderRiskDecision`。

原因：

- `RiskContextCaps` 在策略产生 `TradeIntent` 之前已经存在，适合用来约束动态天平边界。
- `OrderRiskDecision` 需要具体订单数量、价格和可卖量，必须在 `TradeIntent -> OrderSizer` 之后才能计算。
- 仓位调节层不得根据某一笔订单的拒单结果反向创造新的买卖方向。

通用调用顺序见：

[A 股三层协作与执行契约](../../contracts/A股三层协作与执行契约.md)

---

## 2. Profile 类型

| profile | 触发环境 | 行为 |
|---|---|---|
| `AGGRESSIVE_ACCUMULATION` | 大盘稳定，行业强，个股低位承接 | 提高 core 上限，允许慢慢吃饱 |
| `NORMAL_BALANCE` | 环境中性，个股结构正常 | 使用默认动态天平参数 |
| `RANGE_TRADING` | 大盘稳定，行业中性，个股箱体震荡 | 提高 swing 空间 |
| `CAUTIOUS` | 环境偏弱或流动性收缩 | 降低新增买入，扩大网格 |
| `DISTRIBUTION` | 高位转弱或环境风险升高 | 优先卖 swing，分批降 core |
| `DEFENSIVE` | 大盘恐慌、行业破位、熔断风险 | 禁止 swing 买入，保护现金 |

Profile 是仓位参数配置，不是交易命令。

---

## 3. 动态天平边界调节

Sigmoid 动态天平原始输出：

```text
RawTargetPct = sigmoid(BalanceSignal, CurrentPositionPct)
TargetTotalPct = MinPct + RawTargetPct * (MaxPct - MinPct)
```

仓位调节层提供 `MinPct / MaxPct`。

默认边界：

| profile | MinPct | MaxPct |
|---|---:|---:|
| `AGGRESSIVE_ACCUMULATION` | 0.30 | 0.80 |
| `NORMAL_BALANCE` | 0.20 | 0.70 |
| `RANGE_TRADING` | 0.25 | 0.65 |
| `CAUTIOUS` | 0.10 | 0.50 |
| `DISTRIBUTION` | 0.00 | 0.40 |
| `DEFENSIVE` | 0.00 | 0.20 |

`MaxPct` 永远不能超过实例级 `max_position_pct`，也不能突破风控层给出的 cap。

---

## 4. core / swing 拆分调节

仓位调节层决定总目标仓位中 core 与 swing 的目标占比边界。

| profile | core share | swing 行为 |
|---|---:|---|
| `AGGRESSIVE_ACCUMULATION` | 80% - 95% | swing 降低，优先建 core |
| `NORMAL_BALANCE` | 65% - 85% | 正常 swing |
| `RANGE_TRADING` | 50% - 75% | swing 提高 |
| `CAUTIOUS` | 75% - 95% | swing 降低 |
| `DISTRIBUTION` | 80% - 100% | swing 优先清理 |
| `DEFENSIVE` | 90% - 100% | swing 禁止买入 |

解释：

- core 是长期暴露，不参与频繁网格。
- swing 是波动增强仓，环境差时必须先收缩。
- 建仓期优先 core，防止底仓被普通网格过早卖出。
- 高位转弱先清 swing，再分批降 core。

---

## 5. 现金缓冲调节

现金缓冲不是固定值，应随环境变化。

| profile | 现金缓冲 |
|---|---:|
| `AGGRESSIVE_ACCUMULATION` | 20% |
| `NORMAL_BALANCE` | 20% - 25% |
| `RANGE_TRADING` | 25% |
| `CAUTIOUS` | 30% |
| `DISTRIBUTION` | 35% |
| `DEFENSIVE` | 40% 或更高 |

现金缓冲用于：

- 应对 T+1
- 应对连续跌停
- 避免被动满仓
- 给人工干预留空间

仓位调节层只能提高或降低目标现金缓冲，真实资金冻结由执行层管理。

---

## 6. 动态天平参数调节

### 6.1 balance_beta

`balance_beta` 控制动态天平对信号的响应强度。

| 环境 | 调节 |
|---|---|
| 环境稳定、趋势清晰 | 适当提高 |
| 环境震荡、噪声大 | 保持默认 |
| 环境恶化、系统性风险 | 降低 |

风险环境下降低 beta，可以避免策略在急跌中频繁补仓。

### 6.2 inventory_gamma

`inventory_gamma` 控制仓位偏置回归强度。

| 情况 | 调节 |
|---|---|
| 仓位过高且环境变差 | 提高 gamma |
| 仓位过低且低位承接明确 | 降低 gamma |
| 正常环境 | 默认 |

gamma 的作用是防止仓位长期贴近极端。

### 6.3 grid_step_multiplier

高波动环境下扩大网格间距：

| 环境 | grid step |
|---|---|
| 稳定震荡 | 默认 |
| 波动放大 | 1.2 - 1.5 倍 |
| 恐慌环境 | 禁止 swing 买入 |

---

## 7. Profile 选择规则

Profile 选择顺序从强约束到弱约束：

1. 若风控层输出 `KILL_SWITCH`，进入 `DEFENSIVE`
2. 若大盘 `PANIC` 或行业 `BROKEN`，进入 `DEFENSIVE`
3. 若高位反转评分触发，进入 `DISTRIBUTION`
4. 若环境 `RISK_OFF`，进入 `CAUTIOUS`
5. 若环境稳定、行业强、个股低位承接，进入 `AGGRESSIVE_ACCUMULATION`
6. 若箱体震荡且环境稳定，进入 `RANGE_TRADING`
7. 其他情况进入 `NORMAL_BALANCE`

如果多个条件同时触发，保守 profile 优先。

---

## 8. 对 Sigmoid 动态天平的影响

仓位调节层输出会影响：

- `MinPct`
- `MaxPct`
- `NeutralPositionPct`
- `balance_beta`
- `inventory_gamma`
- `CoreShareMin`
- `CoreShareMax`
- `SwingMaxPct`
- `GridStepPct`

不会影响：

- A 股合法数量计算
- T+1 可卖量
- miniQMT 成交状态
- 订单生命周期

---

## 9. 与 GA 进化的关系

可进化参数：

- profile 切换阈值
- 环境评分权重
- beta multiplier
- gamma multiplier
- core share 范围
- grid step multiplier

不可进化参数：

- 单票硬仓位上限
- 最低现金缓冲硬下限
- 是否允许 locked_core 库存置换
- miniQMT 成交真源规则
- A 股交易规则

GA 只能优化软参数，不能突破真实交易和硬风控边界。

---

## 10. 数据缺失与降级

| 缺失输入 | 调节行为 |
|---|---|
| 缺概念数据 | 不影响 profile，概念视为中性 |
| 缺行业数据 | 禁止进入 aggressive，最高 normal |
| 缺大盘数据 | 进入 cautious |
| 环境层 data_quality=INSUFFICIENT | 进入 cautious 或 defensive |
| 风控层 cap 缺失 | 使用实例硬上限 |

缺数据时只能更保守，不能更激进。

---

## 11. 测试计划

- `RISK_ON + 行业 STRONG + 个股低位承接` 进入 `AGGRESSIVE_ACCUMULATION`
- `RISK_OFF` 进入 `CAUTIOUS`
- `PANIC` 进入 `DEFENSIVE`
- 行业 `BROKEN` 时禁止 aggressive
- 高位反转进入 `DISTRIBUTION`
- 防御 profile 禁止 swing 买入
- 风险环境降低 `max_position_pct`
- 强势环境允许提高 core 上限
- 高波动环境扩大网格间距
- 缺行业数据时不进入 aggressive
- 风控 cap 小于 profile MaxPct 时，以 cap 为准


---

## 12. 开发落地补齐

仓位调节层的公共契约以[A 股三层协作与执行契约](../../contracts/A股三层协作与执行契约.md)为准。本文的 profile 表适用于当前双仓策略；未来策略可以复用 profile，也可以声明自己的 profile，但必须输出同构的 `PositionAdjustmentProfile`。

### 12.1 Profile 防抖

为避免一天内频繁切换 profile，建议加入防抖规则：

- 保守方向切换立即生效：`NORMAL -> CAUTIOUS/DEFENSIVE` 可立即切换。
- 激进方向切换必须确认：`CAUTIOUS -> NORMAL -> AGGRESSIVE` 需要连续确认窗口。
- `DEFENSIVE` 解除至少需要一个完整交易日确认。
- 盘中 profile 只允许向更保守方向变化，不允许盘中从防御直接转激进。

### 12.2 与前置风控的合成规则

最终 profile 参数必须满足：

```text
profile.max_position_pct <= RiskContextCaps.max_position_pct
profile.target_cash_buffer_pct >= RiskContextCaps.min_cash_buffer_pct
if RiskContextCaps.allow_buy=false: 所有 allow_bucket_buy=false
if RiskContextCaps.only_reduce_position=true: 只允许降低目标仓位
if RiskContextCaps.kill_switch_active=true: profile=DEFENSIVE
```

### 12.3 通用 bucket 适配

当前双仓策略使用：

```text
core
swing
locked_core
```

未来策略可以声明其他 bucket。仓位调节层应输出通用结构：

```json
{
  "bucket_caps": {
    "core": {"min_pct": 0.10, "max_pct": 0.45},
    "swing": {"min_pct": 0.00, "max_pct": 0.05}
  },
  "allow_bucket_buy": {"core": true, "swing": false},
  "allow_bucket_sell": {"core": true, "swing": true}
}
```

如果策略只有 `main` bucket，也应通过 `bucket_caps.main` 表达。

### 12.4 当前双仓策略最低输出

`ashare_dynamic_balance_dual_bucket` 至少需要以下字段：

```text
profile
min_position_pct
max_position_pct
target_cash_buffer_pct
core_share_min
core_share_max
swing_max_pct
balance_beta_multiplier
inventory_gamma_multiplier
grid_step_multiplier
allow_core_buy
allow_swing_buy
allow_swing_sell
reason_tags
```

这些字段可映射到通用 `bucket_caps` 和 `engine_multipliers`，便于后续策略复用。

---

## 13. 与通用 A 股交易域契约的关系

本文定义当前双仓策略使用的仓位调节 profile。工程落地时，仓位调节层应消费：

- `MarketContextSnapshot`
- `RiskContextCaps`
- `PortfolioState`
- `BucketLedgerSnapshot`
- 策略 `RuntimeState`

仓位调节层只输出 `PositionAdjustmentProfile`，不得生成订单、不得修正真实可卖量、不得修改 bucket 账本。profile 字段是策略能力相关的软参数，未来新增策略可以只消费自己支持的字段。

通用协作顺序和公共结构以以下文档为准：

- [A 股三层协作与执行契约](../../contracts/A股三层协作与执行契约.md)
- [A 股交易域数据结构与状态机](../../contracts/A股交易域数据结构与状态机.md)

---

## 14. 工程落地状态

当前后端已实现独立仓位调节层：

```text
backend/core/trading/position_adjustment.py
```

已落地内容：

- `PositionProfileName`
- `PositionAdjustmentProfile`
- `PositionAdjustmentLayer.build_profile()`
- 六类 profile 的默认边界、现金缓冲、core/swing 拆分、动态天平乘数和网格乘数
- 保守优先 profile 选择规则
- 与 `RiskContextCaps` 的合成规则
- `bucket_caps`
- `allow_bucket_buy`
- `allow_bucket_sell`
- `engine_multipliers`

执行器已在构造 `StrategyInput` 时调用仓位调节层：

```text
StrategyExecutor._build_strategy_input()
  -> _build_market_context()
  -> _build_risk_caps()
  -> PositionAdjustmentLayer.build_profile()
  -> StrategyInput.position_profile
```

现有策略已开始消费：

- `PullbackGridStrategy` 使用 `allow_bucket_buy.swing` / `allow_bucket_sell.swing` 控制网格买卖。
- `AshareSupermarketStrategy` 使用 `allow_bucket_buy.swing` 控制新开仓，并使用 `bucket_caps.swing.max_pct` 限制 swing 买入目标仓位。

尚未完全落地内容：

- 环境层 `MarketContextSnapshot` 仍主要由运行参数或事件字段提供，还不是独立服务。
- `BucketLedgerSnapshot` 仍未形成独立账本模块。
- Profile 防抖尚未实现持久化确认窗口。
