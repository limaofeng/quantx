# 交易事件系统

统一的事件发布订阅系统,用于处理订单、成交、持仓、账户变动等交易事件的实时推送。

## 架构设计

### 核心特性 (个人量化软件专用)

- **事件驱动**: 基于异步队列的发布订阅模式
- **简化设计**: 无多账户概念,专注个人量化交易场景
- **灵活过滤**: 支持按事件类型、股票代码、策略名称过滤
- **性能优化**: 背压控制、订阅者隔离、自动清理
- **单例模式**: 全局唯一的事件管理器实例

### 事件类型

```python
class TradingEventType(str, Enum):
  # 订单事件
  ORDER_CREATED = "ORDER_CREATED"
  ORDER_SUBMITTED = "ORDER_SUBMITTED"
  ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
  ORDER_FILLED = "ORDER_FILLED"
  ORDER_CANCELLED = "ORDER_CANCELLED"
  ORDER_REJECTED = "ORDER_REJECTED"
  ORDER_EXPIRED = "ORDER_EXPIRED"

  # 成交事件
  TRADE_EXECUTED = "TRADE_EXECUTED"

  # 持仓事件
  POSITION_UPDATED = "POSITION_UPDATED"
  POSITION_OPENED = "POSITION_OPENED"
  POSITION_CLOSED = "POSITION_CLOSED"

  # 账户事件
  ACCOUNT_BALANCE_CHANGED = "ACCOUNT_BALANCE_CHANGED"
```

## 使用方法

### 1. 发布事件 (在业务代码中)

```python
from core.events import trading_event_manager, TradingEventType
from core.events.types import OrderEvent
import uuid
from datetime import datetime

# 创建订单事件
event = OrderEvent(
  id=str(uuid.uuid4()),
  event_type=TradingEventType.ORDER_FILLED,
  timestamp=datetime.now(),
  account_id="300000013250",
  order=order_object,  # Order 模型实例
  previous_status="REPORTED",
  changes="订单已完全成交"
)

# 发布事件
await trading_event_manager.publish(
  TradingEventType.ORDER_FILLED,
  event
)
```

### 2. 订阅事件 (在策略或监控系统中)

```python
# 订阅所有事件 (个人量化软件)
async for event in trading_event_manager.subscribe():
  print(f"收到事件: {event.event_type}")

# 只订阅订单事件
async for event in trading_event_manager.subscribe(
  event_types=[
    TradingEventType.ORDER_FILLED,
    TradingEventType.ORDER_CANCELLED
  ]
):
  print(f"订单状态: {event.order.order_status}")

# 按股票代码过滤 (自选股监控)
async for event in trading_event_manager.subscribe(
  stock_codes=["600000.SH", "000001.SZ"]
):
  print(f"自选股事件: {event.order.stock_code}")

# 按策略名称过滤 (策略监控)
async for event in trading_event_manager.subscribe(
  strategy_names=["MA_CROSS_STRATEGY"]
):
  print(f"策略订单: {event.order.strategy_name}")
```

## GraphQL 订阅

### 订阅所有交易事件

```graphql
subscription {
  tradingEvents {
    eventType
    timestamp
    accountId
    ... on OrderEvent {
      order {
        id
        stockCode
        stockName
        status
        volume
        price
      }
      changes
    }
    ... on TradeEvent {
      trade {
        id
        price
        quantity
        amount
      }
    }
  }
}
```

### 只订阅订单成交事件

```graphql
subscription {
  tradingEvents(
    eventTypes: [ORDER_FILLED, ORDER_PARTIALLY_FILLED]
  ) {
    ... on OrderEvent {
      order {
        id
        stockCode
        tradedVolume
        tradedPrice
      }
    }
  }
}
```

### 监控自选股事件

```graphql
subscription {
  tradingEvents(
    stockCodes: ["600000.SH", "000001.SZ"]
  ) {
    eventType
    timestamp
    ... on OrderEvent {
      order { stockCode status }
    }
    ... on TradeEvent {
      trade { stockCode price quantity }
    }
  }
}
```

### 监控特定策略事件

```graphql
subscription {
  tradingEvents(
    strategyNames: ["MA_CROSS_STRATEGY"]
  ) {
    eventType
    timestamp
    ... on OrderEvent {
      order { stockCode status strategyName }
    }
    ... on TradeEvent {
      trade { stockCode price quantity }
    }
  }
}
```

## 集成到现有系统

### 在订单服务中发布事件

```python
# services/order_service.py

from core.events import trading_event_manager, TradingEventType
from core.events.types import OrderEvent
import uuid
from datetime import datetime

class OrderService:
  async def update_order_status(self, order_id, new_status):
    # 更新订单状态
    order = await self.repository.update_status(order_id, new_status)

    # 发布订单事件
    event_type = self._status_to_event_type(new_status)
    if event_type:
      event = OrderEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,
        timestamp=datetime.now(),
        account_id=order.account_id,
        order=order,
        changes=f"订单状态变更为: {new_status}"
      )
      await trading_event_manager.publish(event_type, event)

    return order

  def _status_to_event_type(self, status):
    mapping = {
      OrderStatus.REPORTED: TradingEventType.ORDER_SUBMITTED,
      OrderStatus.SUCCEEDED: TradingEventType.ORDER_FILLED,
      OrderStatus.CANCELED: TradingEventType.ORDER_CANCELLED,
      OrderStatus.PART_SUCC: TradingEventType.ORDER_PARTIALLY_FILLED,
    }
    return mapping.get(status)
```

### 在 Broker 中集成事件

```python
# core/brokers/live.py

from core.events import trading_event_manager, TradingEventType
from core.events.types import OrderEvent
import uuid
from datetime import datetime

class LiveBroker(BaseBroker):
  async def place_order(self, request):
    # 下单逻辑
    order_response = await self._submit_order(request)

    # 发布订单创建事件
    event = OrderEvent(
      id=str(uuid.uuid4()),
      event_type=TradingEventType.ORDER_CREATED,
      timestamp=datetime.now(),
      account_id=self.account_id,
      order=order_response.to_model(),
      changes="新订单创建"
    )
    await trading_event_manager.publish(
      TradingEventType.ORDER_CREATED,
      event
    )

    return order_response
```

## 性能考虑

### 背压控制

- 队列最大容量: 1000 条消息
- 队列满时自动丢弃最旧的消息
- 避免内存泄漏

### 订阅者清理

```python
# 手动清理过期订阅者 (30分钟无活动)
cleaned = await trading_event_manager.cleanup_stale_subscribers(
  max_idle_minutes=30
)
print(f"清理了 {cleaned} 个过期订阅者")
```

### 统计信息

```python
stats = trading_event_manager.get_stats()
print(f"已发布事件: {stats['events_published']}")
print(f"丢弃事件: {stats['events_dropped']}")
print(f"活跃订阅者: {stats['active_subscribers']}")
```

## 测试

运行示例测试脚本:

```bash
PYTHONPATH=/path/to/backend python examples/test_trading_events.py
```

## 注意事项

1. **单例模式**: `trading_event_manager` 是全局单例,在整个应用中共享
2. **异步编程**: 所有事件发布和订阅都是异步的,需要在 async 函数中使用
3. **事件顺序**: 同一订阅者接收的事件保证顺序,但不同订阅者之间不保证
4. **资源清理**: 订阅者退出时会自动清理资源,但长时间运行的订阅应该处理 `CancelledError`
5. **错误处理**: 订阅中的异常会导致订阅终止,建议添加 try-except 处理

## 迁移指南

### 从旧的 trading_orders 订阅迁移

**旧代码** (已删除):
```graphql
subscription {
  trading_orders(stockList: ["600000.SH"]) {
    id
    stockCode
    status
  }
}
```

**新代码**:
```graphql
subscription {
  tradingEvents(
    eventTypes: [ORDER_CREATED, ORDER_SUBMITTED, ORDER_FILLED, ORDER_CANCELLED],
    stockCodes: ["600000.SH"]
  ) {
    ... on OrderEvent {
      order {
        id
        stockCode
        status
      }
    }
  }
}
```

### 主要改进

1. **真正的事件驱动**: 不再是轮询,而是真实的状态变化推送
2. **更细粒度的事件**: 可以区分 ORDER_CREATED, ORDER_FILLED 等不同事件
3. **统一的事件接口**: 订单、成交、持仓、账户事件使用相同的订阅接口
4. **更好的性能**: 事件驱动 + 背压控制 + 智能过滤

## 未来扩展

- [ ] 支持事件持久化 (Redis Streams / Kafka)
- [ ] 添加事件重放功能
- [ ] 支持事件优先级
- [ ] 添加更多事件类型 (风控告警、策略状态等)
- [ ] 集成到 Prefect 工作流
