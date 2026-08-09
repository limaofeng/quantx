# QuantX 当前用法示例

## 统一启动

```powershell
.\ops\quantx.ps1 up -Environment dev -Profile web
Invoke-RestMethod http://127.0.0.1:8080/health/components
.\ops\quantx.ps1 down
```

需要 Prefect 与 data-only QMT Agent 时使用 `-Profile full`。数据库、
InfluxDB 和 Redis 由外部管理。

## 策略域导入

```python
from quantx_domain.strategies import (
  StrategyBase,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  TradeIntent,
  TradeIntentDirection,
)
```

策略的唯一决策入口是：

```python
output: StrategyOutput = await strategy.step(
  StrategyInput(
    run_id="run-1",
    strategy_id="strategy-1",
    timestamp=decision_time,
    cadence=StrategyCadence.BAR,
    instrument_code="000001.SZ",
    market_data=bar,
    parameters=parameters,
  )
)
```

策略不得自行读取数据库、账户或 QMT，也不得计算真实可卖量。

## Agent 协议

```python
from quantx_contracts.agent import MessageEnvelope, MessageType
```

所有控制消息都使用版本化信封。API 先持久化 outbox，Agent 上报先持久化
inbox；重启后从数据库恢复，不依赖 Redis 消息留存。

## GraphQL 前端

```typescript
import { gql } from "@/generated/gql";

const HealthQuery = gql(`
  query HealthExample {
    agentDevices {
      id
      status
    }
  }
`);
```

修改 operation 后通过公共 Caddy 端点重新生成类型。
