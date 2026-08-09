# A 股超市策略

当前实现：

- 策略：
  `packages/domain/src/quantx_domain/strategies/ashare_supermarket.py`
- 参数 schema：
  `packages/domain/src/quantx_domain/strategies/config/ashare_supermarket_schema.py`
- 单元测试：
  `tests/domain/strategies/test_ashare_supermarket.py`

导入方式：

```python
from quantx_domain.strategies import (
  AshareSupermarketStrategy,
  StrategyInput,
)
from quantx_domain.strategies.config import AshareSupermarketConfig
```

策略遵循统一 `step(StrategyInput)` 契约，只输出 `TradeIntent` 和算法状态
补丁。标的选择、真实仓位、最终订单数量和 QMT 调用均不属于策略职责。

旧单体版说明已移到
[../archive/legacy-monolith/strategies/ashare_supermarket.md](../archive/legacy-monolith/strategies/ashare_supermarket.md)。
