# 策略集成测试指南

本目录的策略集成测试以新版策略域模型为准：

```text
StrategyInput -> StrategyBase.step() -> StrategyOutput / TradeIntent
```

旧的逐事件回调接口、旧信号类型和执行器直接消费信号的写法已经废弃，不应再出现在新增测试中。

## 测试重点

为新策略编写集成测试时，至少覆盖以下场景：

1. 核心 `TradeIntent` 生成逻辑。
2. `TradeIntent` 必填字段：`instrument_code`、`direction`、`bucket`、`reason`、目标仓位或目标金额。
3. `RuntimeStatePatch` 不包含真实现金、真实持仓、可卖量或冻结字段。
4. 订单状态和成交状态只通过 `OrderStateEvent` / `TradeExecutionEvent` 更新策略算法状态。
5. 拒单、撤单、部分成交、全部成交不会在策略输出阶段提前标记完成。

## 测试模板

```python
from datetime import datetime

import pytest

from core.strategies.base import StrategyCadence, StrategyInput
from models.enums import StrategyRunMode


@pytest.mark.integration
class TestExampleStrategyIntegration:
    @pytest.mark.asyncio
    async def test_core_intent_generation(self, strategy_manager, strategy_parameters):
        run_id = await strategy_manager.run_strategy(
            strategy_id=1,
            strategy_class=ExampleStrategy,
            mode=StrategyRunMode.BACKTEST,
            instruments=["000001"],
            parameters=strategy_parameters,
            auto_start=True,
        )

        runtime = strategy_manager.executor.get(run_id)
        strategy = runtime.strategy

        output = await strategy.step(StrategyInput(
            run_id=run_id,
            strategy_id=strategy.name,
            timestamp=datetime.now(),
            cadence=StrategyCadence.BAR,
            instrument_code="000001",
            event=build_test_kline("000001"),
            parameters=strategy_parameters,
        ))

        assert output.trade_intents
        for intent in output.trade_intents:
            assert intent.bucket
            assert intent.reason
            assert intent.instrument_code == "000001"
```

## 调试提示

直接调试策略时也应构造 `StrategyInput` 并调用 `step()`。执行器链路测试应验证：

```text
TradeIntent -> OrderSizer -> OrderRiskDecision -> Broker -> OrderStateEvent / TradeExecutionEvent
```

不要在测试中直接修改策略的真实现金、真实持仓或可卖量；这些状态属于运行时状态管理和 bucket ledger。
