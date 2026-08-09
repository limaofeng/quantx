# 策略执行器架构文档

## 概述

StrategyExecutor 是 QuantX 系统的核心执行引擎,负责策略运行实例的并发执行和资源管理。本文档说明执行器的架构设计和使用方式。

## 架构设计

### 职责划分

```
┌─────────────────────────────────────────────────┐
│            StrategyManager (策略管理器)          │
│  - 服务生命周期管理                              │
│  - 策略发现和协调                                │
│  - 数据库持久化                                  │
│  - API 层统一入口                                │
└─────────────────┬───────────────────────────────┘
                  │ 委托执行
                  ↓
┌─────────────────────────────────────────────────┐
│           StrategyExecutor (策略执行器)          │
│  - 并发执行控制                                  │
│  - 资源管理 (Broker, DataAdapter)               │
│  - 实时状态监控                                  │
│  - 异常处理和清理                                │
└─────────────────────────────────────────────────┘
```

### 核心组件

#### StrategyExecutor
**文件**: `core/strategy_executor.py`

**职责**:
- ✅ 管理策略运行实例的并发执行
- ✅ 线程池/协程池资源管理
- ✅ 实时状态监控和心跳管理
- ✅ 异常处理和资源清理
- ✅ Broker 和 DataAdapter 的生命周期管理

**不负责**:
- ❌ 数据库持久化
- ❌ 参数验证
- ❌ 策略发现和协调
- ❌ API 层交互

#### StrategyManager
**文件**: `core/strategy_manager.py`

**职责**:
- ✅ 作为单例服务的统一入口
- ✅ 策略发现和协调
- ✅ 服务生命周期管理
- ✅ API 层统一接口
- ✅ 持久化和恢复功能
- ✅ 委托执行逻辑给 StrategyExecutor

**不负责**:
- ❌ 具体的策略执行逻辑
- ❌ 并发控制
- ❌ 资源分配

## API 参考

### StrategyExecutor 方法

#### 创建和控制

```python
from core.strategy_executor import StrategyExecutor, StrategyContext

executor = StrategyExecutor(max_workers=10)

# 创建运行实例
runtime = executor.create(
    run_id="uuid-123",
    strategy_class=MyStrategy,
    context=StrategyContext(...)
)

# 执行控制
await executor.start(run_id)      # 启动
await executor.stop(run_id)       # 停止
await executor.pause(run_id)      # 暂停
await executor.resume(run_id)     # 恢复
await executor.delete(run_id)     # 删除
```

#### 查询状态

```python
# 获取单个运行
runtime = executor.get(run_id)

# 获取所有运行
all_runs = executor.get_all()

# 获取运行中的实例
running = executor.get_running()

# 停止所有运行
await executor.stop_all_runs()

# 关闭执行器
await executor.shutdown()
```

### StrategyManager 方法

```python
from core import strategy_manager

# 启动管理器(自动发现策略和恢复运行)
await strategy_manager.start()

# 运行策略(创建+启动)
run_id = await strategy_manager.run_strategy(
    strategy_id=1,
    strategy_class=MyStrategy,
    mode="backtest",
    instruments=["000001"],
    parameters={"period": 14}
)

# 控制策略
await strategy_manager.start_strategy(run_id)
await strategy_manager.stop_strategy(run_id)
await strategy_manager.pause_strategy(run_id)
await strategy_manager.resume_strategy(run_id)

# 查询
runtime = strategy_manager.get_run(run_id)
all_runs = strategy_manager.get_all_runs()
```

## 委托关系

```
GraphQL API
    ↓
StrategyManager (单例)
    ├── run_strategy()     → 验证参数 → executor.create() → 持久化
    ├── start_strategy()   → executor.start() → 更新数据库
    ├── stop_strategy()    → executor.stop() → 保存指标 → 更新数据库
    ├── pause_strategy()   → executor.pause() → 更新数据库
    ├── resume_strategy()  → executor.resume() → 更新数据库
    ├── get_run()          → executor.get()
    └── get_all_runs()     → executor.get_all()
```

## 设计优势

### 1. 职责清晰
- **Executor**: 纯执行引擎,不关心持久化
- **Manager**: 服务协调,处理 API 和持久化

### 2. 可测试性提升
- Executor 可以独立测试执行逻辑
- Manager 可以 Mock executor 进行测试
- 减少测试中的数据库依赖

### 3. 可扩展性
- 支持创建多个 Executor 实例
- 支持资源隔离和负载均衡
- 易于添加新的执行策略

### 4. 符合 SOLID 原则
- **S**ingle Responsibility: 每个类只有一个职责
- **O**pen/Closed: 对扩展开放,对修改封闭
- **L**iskov Substitution: 可以替换 Executor 实现
- **I**nterface Segregation: 接口精简明确
- **D**ependency Inversion: 高层不依赖低层实现

## 状态管理

### ExecutionStatus (执行状态)

```python
class ExecutionStatus(Enum):
    PENDING = "PENDING"      # 待启动
    STARTING = "STARTING"    # 启动中
    RUNNING = "RUNNING"      # 运行中
    STOPPING = "STOPPING"    # 停止中
    STOPPED = "STOPPED"      # 已停止
    ERROR = "ERROR"          # 错误
    PAUSED = "PAUSED"        # 已暂停
```

### StrategyRuntime (运行时对象)

```python
@dataclass
class StrategyRuntime:
    run_id: str
    strategy_class: Type[StrategyBase]
    context: StrategyContext
    strategy: Optional[StrategyBase]
    broker: Optional[BrokerBase]
    data_adapter: Optional[DataAdapter]
    status: ExecutionStatus
    metrics: Optional[ExecutionMetrics]
    error_message: Optional[str]
    task: Optional[asyncio.Task]
    pid: int
    host: str
```

## 使用示例

### 独立使用 Executor

```python
from core.strategy_executor import StrategyExecutor, StrategyContext, StrategyRunMode
from core.strategies.ma_cross import MovingAverageCrossStrategy

# 创建执行器
executor = StrategyExecutor(max_workers=5)

# 创建上下文
context = StrategyContext(
    run_id="test-001",
    mode=StrategyRunMode.BACKTEST,
    start_time=datetime(2024, 1, 1),
    instruments=["000001"],
    parameters={"fast_period": 5, "slow_period": 20},
    initial_capital=100000.0
)

# 创建并启动策略
runtime = executor.create("test-001", MovingAverageCrossStrategy, context)
success = await executor.start("test-001")

# 等待执行完成
while executor.get("test-001").status == ExecutionStatus.RUNNING:
    await asyncio.sleep(1)

# 获取结果
runtime = executor.get("test-001")
print(f"最终指标: {runtime.metrics}")

# 清理
await executor.shutdown()
```

### 通过 Manager 使用

```python
from core import strategy_manager
from core.strategies.rsi_strategy import RSIStrategy

# 启动管理器
await strategy_manager.start()

# 运行策略(自动持久化)
run_id = await strategy_manager.run_strategy(
    strategy_id=1,
    strategy_class=RSIStrategy,
    mode="backtest",
    instruments=["000001", "000002"],
    parameters={
        "rsi_period": 14,
        "oversold_level": 30,
        "overbought_level": 70
    }
)

# 查询状态
runtime = strategy_manager.get_run(run_id)
print(f"状态: {runtime.status}")
print(f"指标: {runtime.metrics}")
```

## 最佳实践

### 1. 资源管理

```python
# ✅ 推荐: 使用 shutdown() 确保资源清理
executor = StrategyExecutor(max_workers=10)
try:
    # 使用 executor
    pass
finally:
    await executor.shutdown()
```

### 2. 错误处理

```python
# ✅ 推荐: 检查返回值
success = await executor.start(run_id)
if not success:
    runtime = executor.get(run_id)
    print(f"启动失败: {runtime.error_message}")
```

### 3. 状态监控

```python
# ✅ 推荐: 定期检查状态
runtime = executor.get(run_id)
if runtime.status == ExecutionStatus.ERROR:
    print(f"错误: {runtime.error_message}")
elif runtime.status == ExecutionStatus.RUNNING:
    print(f"运行中, 心跳: {runtime.metrics.last_heartbeat}")
```

## 性能考虑

### 并发控制

```python
# max_workers 控制并发数量
executor = StrategyExecutor(max_workers=10)  # 最多10个并发运行
```

### 资源隔离

```python
# 为不同场景创建独立的 Executor
backtest_executor = StrategyExecutor(max_workers=20)  # 回测高并发
live_executor = StrategyExecutor(max_workers=2)       # 实盘低并发
```

## 相关文档

- [策略系统指南](./STRATEGY.md) - 策略开发和注册
- [系统架构](./ARCHITECTURE.md) - 整体架构设计
- [测试指南](./TESTING_GUIDE.md) - 测试规范

---

**维护团队**: QuantX Development Team
**最后更新**: 2025-10-11
