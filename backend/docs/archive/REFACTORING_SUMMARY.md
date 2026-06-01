# 策略执行架构重构总结

## 重构目标

将策略执行功能重构为清晰的分层架构，遵循单一职责原则和依赖倒置原则。

## 新架构设计

### 组件职责

#### StrategyExecutor（策略执行器）
**文件**: `core/strategy_executor.py`

**职责**：
- ✅ 管理策略运行实例的并发执行
- ✅ 线程池/协程池资源管理
- ✅ 实时状态监控和心跳管理
- ✅ 异常处理和资源清理
- ✅ Broker 和 DataAdapter 的生命周期管理

**不负责**：
- ❌ 数据库持久化
- ❌ 参数验证
- ❌ 策略发现和协调
- ❌ API 层交互

**核心方法**：
```python
# 创建运行实例（纯内存操作）
def create(run_id: str, strategy_class: Type[StrategyBase], context: StrategyContext) -> StrategyRuntime

# 执行控制
async def start(run_id: str) -> bool
async def stop(run_id: str) -> bool
async def pause(run_id: str) -> bool
async def resume(run_id: str) -> bool

# 查询
def get(run_id: str) -> Optional[StrategyRuntime]
def get_all() -> List[StrategyRuntime]
```

#### StrategyManager（策略管理器）
**文件**: `core/strategy_manager.py`

**职责**：
- ✅ 作为单例服务的统一入口
- ✅ 策略发现和协调（通过 StrategyRegistry & Reconciler）
- ✅ 服务生命周期管理（start/stop）
- ✅ API 层统一接口
- ✅ 持久化和恢复功能
- ✅ 委托执行逻辑给 StrategyExecutor

**不负责**：
- ❌ 具体的策略执行逻辑
- ❌ 并发控制
- ❌ 资源分配

**核心方法**：
```python
# 服务管理
async def start()  # 启动服务、发现策略、恢复运行
async def stop()   # 停止所有运行

# 策略运行（委托给 Executor）
async def run_strategy(...) -> str  # 创建并启动
async def start_strategy(run_id: str) -> bool
async def stop_strategy(run_id: str) -> bool
async def pause_strategy(run_id: str) -> bool
async def resume_strategy(run_id: str) -> bool

# 查询（从 Executor 获取）
def get(run_id: str) -> Optional[StrategyRuntime]
def get_all() -> List[StrategyRuntime]
```

## 主要改动

### 1. StrategyExecutor 简化
- ✅ 移除 `_persist_runtime_to_db`、`_update_runtime_status`、`_update_runtime_metrics` 方法
- ✅ 移除 `_validate_parameters` 方法
- ✅ 简化 `create` 方法签名，接受 `run_id` 和 `context`
- ✅ 移除所有数据库相关导入（`get_async_db`、`StrategyRunRepository`）

### 2. StrategyManager 重构
- ✅ 引入 `StrategyExecutor` 作为组件：`self.executor = StrategyExecutor(max_workers=10)`
- ✅ 移除重复的 `StrategyRuntime` 定义，使用 `executor.StrategyRuntime`
- ✅ 所有执行方法委托给 `self.executor`
- ✅ 保留持久化逻辑（`_save_runtime_to_db`、`_update_runtime_status`、`_update_runtime_metrics`）
- ✅ 修复 `_save_runtime_to_db` 以适配新的 `StrategyRuntime` 结构

### 3. core/__init__.py 更新
- ✅ 添加 `StrategyManager` 和 `strategy_manager` 导出
- ✅ 更新文档字符串说明新架构

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
    ├── get()          → executor.get()
    └── get_all()     → executor.get_all()
```

## 优势

### 1. 职责清晰
- **Executor**: 纯执行引擎，不关心持久化
- **Manager**: 服务协调，处理 API 和持久化

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
- **O**pen/Closed: 对扩展开放，对修改封闭
- **L**iskov Substitution: 可以替换 Executor 实现
- **I**nterface Segregation: 接口精简明确
- **D**ependency Inversion: 高层不依赖低层实现

## 向后兼容性

### API 兼容
✅ 所有 GraphQL API 保持不变，因为 `strategy_manager` 单例的接口没有改变

### 导入兼容
✅ 现有代码可以继续使用：
```python
from core import StrategyExecutor, StrategyManager, strategy_manager
from core.strategy_executor import ExecutionStatus, StrategyRuntime
```

### 数据库兼容
✅ 数据库schema和持久化逻辑保持不变

## 潜在问题和注意事项

### 1. StrategyManager 中的执行方法
⚠️ `_setup_broker_and_data`、`_run_runtime`、`_run_backtest` 等方法仍在 StrategyManager 中，但已不再被调用（因为委托给了 Executor）。

**建议**：
- 保留这些方法以保持向后兼容
- 在 Executor 中有对应实现
- 未来版本可以逐步删除

### 2. 状态枚举
⚠️ 存在两个状态枚举：
- `ExecutionStatus` (executor) - 执行状态
- `StrategyRunStatus` (models) - 数据库模型状态

**当前处理**：
- StrategyManager 在持久化时将状态转为字符串
- `get_runs_by_status` 接受 `ExecutionStatus`

### 3. StrategyRuntime 结构
⚠️ Executor 中的 `StrategyRuntime` 与原 StrategyManager 中的结构不同：
- **Executor 版本**: 使用 `context: StrategyContext` 存储配置
- **原 Manager 版本**: 直接存储 `mode`、`instruments`、`parameters` 等字段

**已解决**：
- `_save_runtime_to_db` 方法已更新，从 `runtime.context` 读取数据

## 测试建议

### 单元测试
```bash
# 测试 Executor（不依赖数据库）
python -m pytest tests/unit/test_strategy_executor.py -v

# 测试 Manager（Mock executor）
python -m pytest tests/unit/test_strategy_manager.py -v
```

### 集成测试
```bash
# 完整流程测试
python -m pytest tests/integration/strategies/ -v
```

## 后续优化

### 短期
1. 更新测试以匹配新签名
2. 添加状态枚举映射方法
3. 验证所有 GraphQL API 正常工作

### 中期
1. 逐步移除 StrategyManager 中未使用的执行方法
2. 统一状态枚举
3. 改进错误处理和日志记录

### 长期
1. 支持多 Executor 实例和负载均衡
2. 添加执行器资源监控
3. 实现策略运行的热迁移

## 文档更新

需要更新的文档：
- ✅ `docs/REFACTORING_SUMMARY.md` - 本文档
- ⏳ `docs/ARCHITECTURE.md` - 更新架构图
- ⏳ `docs/STRATEGY.md` - 更新组件说明
- ⏳ `docs/MODULES.md` - 更新模块文档

## 结论

本次重构成功地将策略执行功能分离为两个职责明确的组件：
- **StrategyExecutor**: 专注于并发执行和资源管理
- **StrategyManager**: 专注于服务协调和持久化

重构提升了代码的可维护性、可测试性和可扩展性，为未来的功能扩展奠定了良好的基础。
