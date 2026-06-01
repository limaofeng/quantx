# 策略系统完整指南

## 1. 概述

QuantX 策略系统提供完整的策略开发、注册、运行和管理能力,支持自动化的策略生命周期管理。

### 核心特性

- 🔍 **自动发现注册** - 启动时自动扫描并注册策略类
- 🔄 **智能协调同步** - 自动检测策略变更并协调数据库状态
- 📋 **参数配置系统** - 基于 JSON Schema 的类型安全参数定义
- 🚀 **动态加载机制** - 零代码配置的策略动态加载
- 📊 **版本控制** - 代码哈希和语义化版本管理
- 🎯 **运行实例管理** - 自动管理受影响的运行实例

### 架构总览

```
┌─────────────────┐
│  启动流程        │
└────────┬────────┘
         ↓
┌─────────────────┐
│ StrategyManager │  策略管理器(协调入口)
└────────┬────────┘
         ↓
    ┌────┴────┐
    ↓         ↓
┌──────────┐ ┌────────────────┐
│Registry  │ │Reconciler      │
│(发现策略) │ │(协调同步)       │
└──────────┘ └───────┬────────┘
                     ↓
            ┌────────────────┐
            │  Database      │
            │  - strategies  │
            │  - strategy_runs│
            └────────────────┘
```

---

## 2. 核心概念

### 2.1 策略生命周期

```
注册 → 激活 → 运行 → 暂停 → 更新 → 删除
  ↓      ↓      ↓      ↓      ↓      ↓
ACTIVE  运行中  RUNNING PAUSED UPGRADING DEPRECATED
```

**策略状态** (`StrategyStatus`):
- `ACTIVE` - 策略激活,可创建运行实例
- `UPGRADING` - 策略代码已更新,待确认升级
- `DEPRECATED` - 策略已弃用,不可创建新实例

**注**: `is_active` 是只读计算属性,等价于 `status == ACTIVE`

### 2.2 关键组件

#### StrategyManager (策略管理器)
**文件**: `core/strategy_manager.py`

**职责**:
- 策略生命周期管理的统一入口
- 启动时自动发现并协调策略
- 管理策略运行实例

**核心方法**:
```python
# 启动管理器(自动完成策略协调)
await strategy_manager.start()

# 创建策略运行
run_id = await strategy_manager.create(
    strategy_id=1,
    strategy_class=StrategyClass,
    mode="backtest",
    instruments=["000001"],
    parameters={"rsi_period": 14}
)

# 停止运行
await strategy_manager.stop(run_id)
```

#### StrategyRegistry (策略注册表)
**文件**: `core/strategy_registry.py`

**职责**:
- 自动发现 `core/strategies/` 下的策略类
- 提取策略元数据(名称、版本、参数schema等)
- 计算代码 SHA256 哈希
- 动态加载策略类

**核心方法**:
```python
# 发现所有策略
strategies = strategy_registry.discover_strategies()

# 动态加载策略类
strategy_class = strategy_registry.get_strategy_class(
    class_name="RSIStrategy",
    file_path="core/strategies/rsi_strategy.py"  # 可选
)

# 获取元数据
metadata = strategy_registry.get_strategy("RSIStrategy")
```

#### StrategyReconciler (策略协调器)
**文件**: `core/strategy_reconciler.py`

**职责**:
- 协调代码策略与数据库策略的一致性
- 处理新增、更新、删除场景
- 管理受影响的运行实例状态

**核心方法**:
```python
# 执行协调(内部使用,不对外暴露)
result = await reconciler.reconcile(discovered_strategies)

# 协调结果
print(f"新增: {result.new}")
print(f"更新: {result.updated}")
print(f"删除: {result.deleted}")
print(f"未变更: {result.unchanged}")
print(f"暂停实例: {result.paused_runs}")
print(f"停止实例: {result.stopped_runs}")
```

### 2.3 数据模型

#### Strategy 模型
**表**: `strategies`

```python
class Strategy(BaseModel):
    id: int                          # 主键
    name: str                        # 策略名称
    description: str                 # 策略描述
    file_path: str                   # 文件路径 (如 core/strategies/rsi_strategy.py)
    class_name: str                  # 类名 (如 RSIStrategy)
    parameter_schema: ParameterSchema  # 参数配置Schema(Pydantic对象)
    version: str                     # 版本号 (如 1.0.0)
    code_hash: str                   # 代码SHA256哈希
    status: str                      # 状态: active/upgrading/deprecated
    category: StrategyCategory       # 策略分类
    risk_level: RiskLevel            # 风险等级
    tags: List[str]                  # 策略标签(ARRAY字段)
    is_active: bool                  # 是否激活
    created_at: datetime
    updated_at: datetime
```

#### StrategyRun 模型
**表**: `strategy_runs`

```python
class StrategyRun(BaseModel):
    id: str                    # 运行实例ID(UUID)
    strategy_id: int           # 关联的策略ID
    strategy_version: str      # 运行时使用的策略版本
    mode: str                  # 运行模式: backtest/paper/live
    instruments: List[str]     # 交易标的
    parameters: Dict           # 实际参数值
    status: str                # PENDING/RUNNING/PAUSED/STOPPED/COMPLETED/ERROR
    upgrade_required: bool     # 是否需要升级
    start_time: datetime
    stop_time: datetime
    error_message: str
```

---

## 3. 策略自动注册

### 3.1 发现机制

系统启动时自动扫描 `core/strategies/` 目录:

1. **扫描策略类**
   - 遍历所有 Python 文件
   - 查找继承自 `StrategyBase` 的类
   - 跳过基类和导入的类

2. **提取元数据**
   - 策略名称、版本、描述
   - 文件路径 (`file_path`)
   - 模块路径 (`module_path` - 动态计算)
   - 参数 Schema (调用 `get_parameter_schema()`)
   - 分类、风险等级、标签 (从类属性读取)

3. **计算代码哈希**
   - 读取策略文件内容
   - 计算 SHA256 哈希值
   - 用于变更检测

**注**: `module_path` 是从 `file_path` 动态计算的属性,无冗余存储

### 3.2 协调流程

#### 新策略注册
```
发现新策略 → 创建 Strategy 记录 → 设置 status=active
```

**自动操作**:
- ✅ 插入数据库记录
- ✅ 设置为 active 状态
- ✅ 记录初始版本和代码哈希

#### 策略更新检测

检测条件(满足任一):
- 版本号变化
- 代码哈希变化
- 参数 Schema 变化

**自动操作**:
```
检测到变更 → 更新策略记录 → status=upgrading → 暂停运行实例 → upgrade_required=true
```

#### 策略删除处理

```
代码中不存在 → status=DEPRECATED → 停止所有运行实例
```

**自动操作**:
- ✅ 标记为 deprecated
- ✅ 停止所有 RUNNING/PAUSED 实例
- ✅ 设置实例错误消息

### 3.3 版本控制

**语义化版本号**:
```python
class MyStrategy(StrategyBase):
    @property
    def version(self) -> str:
        return "1.2.3"  # 主版本.次版本.修订号
```

**代码哈希对比**:
- 即使版本号未变,代码变化也会被检测
- 确保数据库中的策略与代码完全一致

**Schema 变化检测**:
- 比较参数类型、范围、默认值
- 检测新增/删除/修改的参数

---

## 4. 策略动态加载

### 4.1 加载机制

**核心方法**: `StrategyRegistry.get_strategy_class()`

**工作流程**:
```python
class_name + file_path
      ↓
查找注册表元数据
      ↓
构建模块路径 (core.strategies.rsi_strategy)
      ↓
importlib.import_module()
      ↓
getattr(module, class_name)
      ↓
验证继承自 StrategyBase
      ↓
返回策略类
```

**使用示例**:
```python
# 方式1: 仅使用类名(从注册表加载)
strategy_class = strategy_registry.get_strategy_class("RSIStrategy")

# 方式2: 指定文件路径(支持未注册的策略)
strategy_class = strategy_registry.get_strategy_class(
    class_name="CustomStrategy",
    file_path="plugins/custom_strategies/my_strategy.py"
)

# 创建实例
context = StrategyContext(...)
strategy = strategy_class(context)
```

### 4.2 数据库字段规范

#### file_path 字段
**格式**: 相对路径,从项目根目录开始

**标准策略**:
```
core/strategies/{module_name}.py
```

**插件策略**:
```
plugins/{plugin_name}/{module_name}.py
```

**示例**:
```python
# ✅ 正确
file_path = "core/strategies/rsi_strategy.py"
file_path = "plugins/custom/my_strategy.py"

# ❌ 错误
file_path = "/absolute/path/strategy.py"  # 不要使用绝对路径
file_path = "rsi_strategy.py"            # 缺少完整路径
```

#### class_name 字段
**格式**: Python 类名,必须与代码中的类名完全一致

```python
# ✅ 正确
class_name = "RSIStrategy"           # 匹配 class RSIStrategy(StrategyBase)
class_name = "MovingAverageCross"    # 匹配 class MovingAverageCross(StrategyBase)

# ❌ 错误
class_name = "rsi_strategy"          # 大小写不匹配
class_name = "RSI Strategy"          # 包含空格
```

### 4.3 GraphQL 集成

**位置**: `gqlapi/resolvers/strategies.py:230-236`

```python
@strawberry.mutation
async def create_strategy_run(run_input: StrategyRunInput) -> StrategyRun:
    # 1. 获取策略模板
    strategy = await repo.get_strategy(run_input.strategy_id)

    # 2. 动态加载策略类
    strategy_class = strategy_registry.get_strategy_class(
        strategy.class_name,
        strategy.file_path
    )

    # 3. 创建运行实例
    run_id = await strategy_manager.create(
        strategy_id=run_input.strategy_id,
        strategy_class=strategy_class,  # 动态加载的类
        mode=run_input.mode,
        instruments=run_input.instruments,
        parameters=json.loads(run_input.parameters)
    )

    return StrategyRun(id=run_id, ...)
```

---

## 5. 参数配置系统

### 5.1 Schema 定义

#### ParameterSchema (Pydantic 模型)

**文件**: `models/parameter_schema.py`

```python
from models.parameter_schema import ParameterSchema, ParameterProperty

class MyStrategy(StrategyBase):
    @classmethod
    def get_parameter_schema(cls) -> ParameterSchema:
        return ParameterSchema(
            type="object",
            properties={
                "rsi_period": ParameterProperty(
                    type="integer",
                    minimum=2,
                    maximum=50,
                    default=14,
                    title="RSI计算周期",
                    description="RSI指标的计算周期(天)",
                    group="技术指标"
                ),
                # 更多参数...
            },
            required=["rsi_period"],
            additionalProperties=False
        )
```

#### 数据库存储

- **存储方式**: JSON 格式,使用 `ParameterSchemaType` TypeDecorator
- **自动序列化**: Pydantic ↔ JSON 自动转换
- **类型安全**: 读取时自动构造 `ParameterSchema` 对象

```python
# 写入数据库
strategy.parameter_schema = ParameterSchema(...)  # Pydantic对象
# → 自动序列化为 JSON

# 从数据库读取
schema = strategy.parameter_schema  # ParameterSchema对象
# → 自动从 JSON 反序列化
```

### 5.2 参数类型

#### 基础类型

| 类型 | JSON Schema | 示例值 | 说明 |
|------|------------|--------|------|
| 整数 | `integer` | `14` | 周期、数量等 |
| 小数 | `number` | `0.08` | 比例、阈值等 |
| 字符串 | `string` | `"fast"` | 模式、名称等 |
| 布尔 | `boolean` | `true` | 开关、标志等 |

#### 复杂类型

**数组类型**:
```python
"instruments": ParameterProperty(
    type="array",
    items=ParameterProperty(type="string"),
    default=["000001", "000002"],
    title="交易标的列表"
)
```

**对象类型(嵌套)**:
```python
"risk_config": ParameterProperty(
    type="object",
    properties={
        "stop_loss": ParameterProperty(type="number", default=0.08),
        "take_profit": ParameterProperty(type="number", default=0.15),
    },
    title="风险控制配置"
)
```

**枚举类型**:
```python
"exit_mode": ParameterProperty(
    type="string",
    enum=["rsi_reversal", "fixed_profit", "trailing_stop"],
    default="rsi_reversal",
    title="退出模式",
    enumDescriptions={
        "rsi_reversal": "RSI反转退出",
        "fixed_profit": "固定收益退出",
        "trailing_stop": "追踪止损退出"
    }
)
```

### 5.3 UI 扩展字段

#### 标题和描述
```python
title="RSI计算周期"              # UI显示的标题
description="RSI指标的计算周期(天)"  # 鼠标悬停提示
```

#### 参数分组
```python
group="技术指标"  # 将参数归类到"技术指标"分组
```

**推荐分组**:
- `技术指标` - 技术指标相关参数
- `交易意图` - 买卖意图触发条件
- `资金管理` - 仓位大小、分批建仓
- `风险控制` - 止损、止盈、最大回撤
- `时间参数` - 交易时间、持仓时长
- `高级选项` - 其他高级配置

#### 表单控件
```python
widget="slider"      # 滑块控件
widget="radio"       # 单选框
widget="checkbox"    # 复选框
step=1               # 数值步长
placeholder="请输入"  # 占位符文本
unit="%"             # 参数单位
```

### 5.4 参数验证

#### 类型验证
```python
type="integer"  # 必须是整数
type="number"   # 可以是小数
```

#### 范围验证
```python
minimum=2       # 最小值
maximum=50      # 最大值
```

#### 必填字段
```python
required=["rsi_period", "position_size"]  # 必填参数列表
```

#### 额外属性
```python
additionalProperties=False  # 不允许未定义的参数
```

**验证函数**: `models/parameter_schema.py`
```python
from models.parameter_schema import validate_parameters

is_valid, error_msg = validate_parameters(
    parameters={"rsi_period": 14},
    parameter_schema=schema.model_dump()
)

if not is_valid:
    raise ValueError(error_msg)
```

### 5.5 完整示例

#### RSI 策略参数 Schema
```python
from models.parameter_schema import ParameterSchema, ParameterProperty

@classmethod
def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
        type="object",
        properties={
            # 技术指标
            "rsi_period": ParameterProperty(
                type="integer",
                minimum=2,
                maximum=50,
                default=14,
                title="RSI周期",
                description="RSI计算周期",
                group="技术指标",
                widget="slider",
                step=1
            ),

            # 交易意图
            "oversold_level": ParameterProperty(
                type="number",
                minimum=0,
                maximum=50,
                default=30,
                title="超卖线",
                description="RSI低于此值时认为超卖",
                group="交易意图"
            ),
            "overbought_level": ParameterProperty(
                type="number",
                minimum=50,
                maximum=100,
                default=70,
                title="超买线",
                description="RSI高于此值时认为超买",
                group="交易意图"
            ),

            # 资金管理
            "position_size": ParameterProperty(
                type="integer",
                minimum=100,
                default=1000,
                title="仓位大小",
                description="单次交易股数",
                group="资金管理"
            ),
            "max_positions": ParameterProperty(
                type="integer",
                minimum=1,
                default=3,
                title="最大持仓数",
                description="最大持仓数量(分批建仓)",
                group="资金管理"
            ),

            # 风险控制
            "exit_mode": ParameterProperty(
                type="string",
                enum=["rsi_reversal", "fixed_profit", "trailing_stop"],
                default="rsi_reversal",
                title="退出模式",
                group="风险控制",
                enumDescriptions={
                    "rsi_reversal": "RSI反转退出",
                    "fixed_profit": "固定收益退出",
                    "trailing_stop": "追踪止损退出"
                }
            ),
            "profit_target_pct": ParameterProperty(
                type="number",
                minimum=0,
                default=0.08,
                title="目标收益率",
                description="目标收益率(仅在fixed_profit模式下生效)",
                group="风险控制",
                step=0.01,
                unit="%"
            ),
        },
        required=["rsi_period"],
        additionalProperties=False
    )
```

---

## 6. 开发指南

### 6.1 快速开始

#### 步骤 1: 创建策略文件
```bash
# 在标准目录创建
touch core/strategies/my_strategy.py
```

#### 步骤 2: 实现策略类
```python
# file: core/strategies/examples/my_strategy.py
from typing import List
from core.strategies.base import (
    StrategyBase,
    StrategyCadence,
    StrategyInput,
    StrategyOutput,
    TradeIntent,
    TradeIntentDirection,
)
from models.parameter_schema import ParameterSchema, ParameterProperty

class MyStrategy(StrategyBase):
    """我的策略"""

    # 可选: 分类和风险等级
    CATEGORY = "trend_following"
    RISK_LEVEL = "medium"
    TAGS = ["趋势", "日内"]

    @property
    def name(self) -> str:
        return "我的策略"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "这是一个示例策略"

    @classmethod
    def get_parameter_schema(cls) -> ParameterSchema:
        return ParameterSchema(
            type="object",
            properties={
                "period": ParameterProperty(
                    type="integer",
                    default=20,
                    minimum=5,
                    maximum=100,
                    title="周期",
                    group="技术指标"
                ),
            },
            required=["period"],
            additionalProperties=False
        )

    async def on_init(self) -> None:
        """初始化"""
        self.period = self.get_parameter("period", 20)
        self.log_info(f"策略初始化: period={self.period}")

    async def step(self, input: StrategyInput) -> StrategyOutput:
        """统一策略决策入口"""
        if input.cadence != StrategyCadence.BAR:
            return StrategyOutput()

        bar = input.event
        close_price = getattr(bar, "close_price", getattr(bar, "close", 0.0))
        intent = TradeIntent(
            strategy_id=self.name,
            run_id=self.context.run_id,
            instrument_code=input.instrument_code,
            direction=TradeIntentDirection.BUY,
            bucket="swing",
            reason="example_buy",
            target_position_pct=0.2,
            limit_price_hint=close_price,
            metadata={"period": self.period},
        )
        return StrategyOutput(trade_intents=[intent])

    async def on_stop(self) -> None:
        """停止清理"""
        self.log_info("策略已停止")
```

#### 步骤 3: 启动系统,自动注册
```bash
python main.py
```

**日志输出**:
```
INFO - 开始扫描策略包: core.strategies
INFO - 发现策略: 我的策略 v1.0.0 (MyStrategy)
INFO - 策略扫描完成,共发现 4 个策略
INFO - 开始协调策略...
INFO - 注册新策略成功: 我的策略 v1.0.0
INFO - 策略协调完成: 新增=1, 更新=0, 删除=0, 未变更=3
```

### 6.2 标准策略开发

#### 必需实现的方法

| 方法 | 类型 | 说明 |
|------|------|------|
| `name` | @property | 策略名称 |
| `version` | @property | 版本号(语义化) |
| `description` | @property | 策略描述 |
| `get_parameter_schema()` | @classmethod | 返回 ParameterSchema |
| `on_init()` | async | 初始化回调 |
| `step(input)` | async | 唯一策略决策入口，返回 StrategyOutput |
| `on_stop()` | async | 停止清理回调 |

#### 可选实现的方法

| 方法 | 说明 |
|------|------|
| `on_order()` | 订单状态更新回调 |
| `on_trade()` | 成交回调 |
| `get_statistics()` | 获取策略统计信息 |

#### 可选类属性

```python
class MyStrategy(StrategyBase):
    # 策略分类
    CATEGORY = "trend_following"  # 可选值见 StrategyCategory 枚举

    # 风险等级
    RISK_LEVEL = "medium"  # low/medium/high/very_high

    # 标签
    TAGS = ["趋势", "日内", "高频"]
```

### 6.3 插件策略开发

#### 创建插件目录
```bash
mkdir -p plugins/my_plugin
touch plugins/my_plugin/__init__.py
touch plugins/my_plugin/custom_strategy.py
```

#### 实现策略(同标准策略)
```python
# file: plugins/my_plugin/custom_strategy.py
from core.strategies.base import StrategyBase

class CustomStrategy(StrategyBase):
    # 实现同标准策略...
    pass
```

#### 手动注册到数据库
```python
from models.strategy import Strategy
from repositories.strategy_repository import StrategyRepository

async def register_plugin_strategy():
    async for db in get_async_db():
        repo = StrategyRepository(db)

        strategy = Strategy(
            name="自定义策略",
            description="插件策略示例",
            file_path="plugins/my_plugin/custom_strategy.py",  # 关键!
            class_name="CustomStrategy",                        # 关键!
            parameter_schema=CustomStrategy.get_parameter_schema().model_dump(),
            version="1.0.0",
            category="custom",
            risk_level="medium",
            tags=["插件", "自定义"],
            status=StrategyStatus.ACTIVE
        )

        await repo.create(strategy)
        break
```

#### 使用(自动动态加载)
```python
# GraphQL 或代码中使用时,系统会自动加载
strategy_class = strategy_registry.get_strategy_class(
    class_name="CustomStrategy",
    file_path="plugins/my_plugin/custom_strategy.py"
)
```

### 6.4 最佳实践

#### 命名规范
```python
# ✅ 推荐
class MovingAverageCrossStrategy  # 驼峰命名,Strategy后缀
class RSIStrategy
class MeanReversionStrategy

# ❌ 避免
class ma_cross_strategy     # 不要用下划线命名
class Strategy1             # 不要用数字命名
class MyStrat               # 不要缩写
```

#### 版本管理
```python
# ✅ 语义化版本
version = "1.0.0"  # 主版本.次版本.修订号
version = "1.2.3"  # 新增功能时增加次版本号
version = "2.0.0"  # 不兼容变更时增加主版本号

# ❌ 避免
version = "v1"
version = "2024-01-01"
```

#### 参数设计
```python
# ✅ 推荐: 提供合理的默认值和范围
"rsi_period": ParameterProperty(
    type="integer",
    minimum=2,     # 合理的最小值
    maximum=50,    # 合理的最大值
    default=14,    # 经典默认值
    title="RSI周期",
    description="RSI指标的计算周期(K线数量)",
    group="技术指标"
)

# ❌ 避免: 缺少约束
"rsi_period": ParameterProperty(
    type="integer",
    default=14  # 缺少范围限制,用户可能输入不合理值
)
```

#### 错误处理
```python
async def on_init(self) -> None:
    try:
        self.period = self.get_parameter("period", 20)

        # ✅ 参数验证
        if self.period < 1:
            raise ValueError("周期必须大于0")

        # ✅ 状态初始化
        self.ma = SMA(self.period)
        self.log_info(f"初始化成功: period={self.period}")

    except Exception as e:
        self.log_error(f"初始化失败: {e}")
        raise  # 重新抛出,让系统知道初始化失败
```

#### 日志记录
```python
# ✅ 推荐: 分级日志
self.log_info("策略启动")         # 正常信息
self.log_warning("参数超出建议范围")  # 警告
self.log_error("数据加载失败")      # 错误

# ✅ 包含上下文信息
self.log_info(f"生成意图: {intent.direction.value} {intent.instrument_code} @ {intent.limit_price_hint}")

# ❌ 避免: 无意义日志
self.log_info("here")
self.log_info("test")
```

---

## 7. 运维管理

### 7.1 策略更新流程

#### 自动检测
系统启动时自动检测:
```
版本号变化 OR 代码哈希变化 OR Schema变化
  ↓
更新策略记录
  ↓
status = "upgrading"
  ↓
暂停所有 RUNNING 实例
  ↓
upgrade_required = true
```

#### 管理员确认
```sql
-- 1. 查看待升级策略
SELECT id, name, version, status FROM strategies WHERE status = 'upgrading';

-- 2. 查看受影响的实例
SELECT sr.id, sr.status, sr.upgrade_required
FROM strategy_runs sr
WHERE sr.strategy_id = {strategy_id}
  AND sr.upgrade_required = true;

-- 3. 确认升级(改为active)
UPDATE strategies SET status = 'active' WHERE id = {strategy_id};
```

#### 用户重启
用户需要:
1. 查看升级提示
2. 停止旧版本实例
3. 检查参数 Schema 变化,调整参数
4. 启动新版本实例

### 7.2 策略删除流程

#### 自动标记
```
代码中不存在
  ↓
status = DEPRECATED
  ↓
停止所有实例
  ↓
error_message = "策略已被删除,自动停止运行"
```

#### 手动清理(可选)
```sql
-- 软删除(推荐): 保留历史记录
UPDATE strategies SET status = 'deprecated'
WHERE id = {strategy_id};

-- 硬删除(不推荐): 删除记录
DELETE FROM strategies WHERE id = {strategy_id};
```

### 7.3 监控和日志

#### 协调日志
```
INFO - 开始协调策略...
INFO - 数据库中已有 3 个策略
INFO - 代码中发现 4 个策略
INFO - 新策略: {'NewStrategy'}
INFO - 已删除策略: set()
INFO - 已存在策略: {'RSIStrategy', 'MAStrategy', 'MRStrategy'}
INFO - 正在注册策略: 新策略
INFO - 注册新策略成功: 新策略 v1.0.0
INFO - 策略协调完成: 新增=1, 更新=0, 删除=0, 未变更=3
```

#### 运行状态监控
```python
# 获取运行统计
stats = strategy_manager.get_run(run_id).get_statistics()

print(f"意图数量: {stats['trade_intents_count']}")
print(f"订单数量: {stats['orders_count']}")
print(f"持仓情况: {stats['positions']}")
print(f"运行状态: {stats['is_running']}")
```

#### 错误追踪
```sql
-- 查看失败的运行
SELECT id, strategy_id, status, error_message
FROM strategy_runs
WHERE status = 'ERROR'
ORDER BY created_at DESC
LIMIT 10;
```

---

## 8. API 参考

### 8.1 StrategyRegistry API

#### discover_strategies()
```python
strategies: List[StrategyMetadata] = strategy_registry.discover_strategies(
    package_name="core.strategies"  # 可选,默认此路径
)

# 返回值
for s in strategies:
    print(f"{s.name} v{s.version}")
    print(f"  class_name: {s.class_name}")
    print(f"  file_path: {s.file_path}")
    print(f"  module_path: {s.module_path}")  # 动态计算属性
    print(f"  code_hash: {s.code_hash}")
```

#### get_strategy_class()
```python
strategy_class: Type[StrategyBase] = strategy_registry.get_strategy_class(
    class_name="RSIStrategy",           # 必需
    file_path="core/strategies/..."     # 可选
)

# 创建实例
strategy = strategy_class(context)
```

#### get_strategy() / get_all_strategies()
```python
# 获取单个元数据
metadata = strategy_registry.get_strategy("RSIStrategy")

# 获取所有元数据
all_metadata = strategy_registry.get_all_strategies()
```

### 8.2 StrategyReconciler API

#### reconcile()
```python
result: ReconciliationResult = await reconciler.reconcile(
    discovered_strategies=strategies
)

# 访问结果
print(f"新增: {result.new}")
print(f"更新: {result.updated}")
print(f"删除: {result.deleted}")
print(f"未变更: {result.unchanged}")
print(f"暂停实例: {result.paused_runs}")
print(f"停止实例: {result.stopped_runs}")

# 转为字典
stats_dict = result.to_dict()
```

### 8.3 GraphQL API

#### 查询策略列表
```graphql
query {
  strategies {
    id
    name
    version
    description
    category
    riskLevel
    tags
    parameterSchema {
      type
      properties
      required
    }
    status
    isActive
  }
}
```

#### 创建策略运行
```graphql
mutation {
  createStrategyRun(input: {
    strategyId: 1
    mode: BACKTEST
    instruments: ["000001"]
    parameters: "{\"rsi_period\": 14}"
    startTime: "2024-01-01T00:00:00Z"
    endTime: "2024-12-31T23:59:59Z"
  }) {
    id
    strategyId
    strategyName
    mode
    status
  }
}
```

#### 查询运行状态
```graphql
query {
  strategyRun(id: "run-uuid") {
    id
    status
    startTime
    stopTime
    errorMessage
    metrics
  }
}
```

---

## 9. 故障排查

### 9.1 常见问题

#### 问题 1: 策略未被发现
**症状**:
```
INFO - 策略扫描完成,共发现 0 个策略
```

**原因**:
- 文件不在 `core/strategies/` 目录
- 类未继承 `StrategyBase`
- 文件名以 `_` 开头

**解决**:
```bash
# 检查文件位置
ls core/strategies/my_strategy.py

# 检查类继承
grep "class.*StrategyBase" core/strategies/my_strategy.py
```

#### 问题 2: 模块导入失败
**症状**:
```
ValueError: 无法加载策略 MyStrategy: 模块导入失败
ModuleNotFoundError: No module named 'xxx'
```

**原因**:
- `file_path` 错误
- 文件不存在
- 依赖缺失

**解决**:
```python
# 验证文件路径
import os
assert os.path.exists("core/strategies/my_strategy.py")

# 验证模块路径
import importlib
module = importlib.import_module("core.strategies.my_strategy")
```

#### 问题 3: 策略类不存在
**症状**:
```
ValueError: 策略类 MyStrategy 不存在
```

**原因**:
- `class_name` 与实际类名不一致
- 类名拼写错误

**解决**:
```python
# 检查类名
grep "^class " core/strategies/my_strategy.py
# 输出: class MyStrategy(StrategyBase):

# 确保 class_name 与输出一致
```

#### 问题 4: 参数验证失败
**症状**:
```
ValueError: 参数验证失败: 参数 rsi_period 不能小于 2
```

**原因**:
- 用户输入的参数超出范围
- 缺少必填参数

**解决**:
```python
# 检查 Schema 定义
schema = MyStrategy.get_parameter_schema()
print(schema.properties["rsi_period"].minimum)  # 2
print(schema.required)  # ["rsi_period"]

# 确保用户输入符合要求
parameters = {"rsi_period": 14}  # >= 2
```

#### 问题 5: Schema 比较错误 (已修复)
**症状**:
```
INFO - 策略已更新: RSI策略 1.0.0 -> 1.0.0  # 版本未变但被判定为更新
```

**原因**:
- 旧版本代码: `str(dict) != ParameterSchema` 比较永远不相等
- 已在重构中修复

**解决**:
- 已修复,无需操作
- 现在正确比较: `dict == ParameterSchema.model_dump()`

### 9.2 调试技巧

#### 查看注册日志
```python
import logging
logging.getLogger("core.strategy_registry").setLevel(logging.DEBUG)
logging.getLogger("core.strategy_reconciler").setLevel(logging.DEBUG)
```

#### 手动测试策略发现
```python
from core.strategy_registry import strategy_registry

strategies = strategy_registry.discover_strategies()
for s in strategies:
    print(f"✅ {s.name} v{s.version}")
    print(f"   file: {s.file_path}")
    print(f"   class: {s.class_name}")
    print(f"   hash: {s.code_hash[:8]}...")
```

#### 验证 Schema 格式
```python
from models.parameter_schema import validate_parameters

schema = MyStrategy.get_parameter_schema()
schema_dict = schema.model_dump()

is_valid, error = validate_parameters(
    parameters={"rsi_period": 14},
    parameter_schema=schema_dict
)

if not is_valid:
    print(f"❌ 验证失败: {error}")
else:
    print("✅ 验证通过")
```

#### 检查代码哈希
```python
import hashlib
from pathlib import Path

file_path = Path("core/strategies/rsi_strategy.py")
content = file_path.read_bytes()
code_hash = hashlib.sha256(content).hexdigest()

print(f"代码哈希: {code_hash}")
```

---

## 10. 示例代码

### 10.1 完整策略示例

参考项目中的示例策略:
- `core/strategies/ma_cross.py` - 均线交叉策略
- `core/strategies/rsi_strategy.py` - RSI 超买超卖策略
- `core/strategies/mean_reversion.py` - 均值回归策略
- `core/strategies/tick_bar_strategy.py` - Tick-Bar 双回调策略

### 10.2 参数 Schema 示例

#### 简单参数
```python
"period": ParameterProperty(
    type="integer",
    minimum=5,
    maximum=200,
    default=20,
    title="周期",
    description="计算周期",
    group="技术指标"
)
```

#### 复杂嵌套参数
```python
"risk_management": ParameterProperty(
    type="object",
    properties={
        "stop_loss": ParameterProperty(
            type="number",
            minimum=0.01,
            maximum=0.5,
            default=0.08,
            title="止损比例"
        ),
        "take_profit": ParameterProperty(
            type="number",
            minimum=0.01,
            maximum=1.0,
            default=0.15,
            title="止盈比例"
        ),
        "max_drawdown": ParameterProperty(
            type="number",
            minimum=0.05,
            maximum=0.5,
            default=0.2,
            title="最大回撤"
        )
    },
    title="风险管理配置",
    group="风险控制"
)
```

#### 数组参数
```python
"periods": ParameterProperty(
    type="array",
    items=ParameterProperty(type="string"),
    default=["1m", "5m", "15m"],
    title="K线周期列表",
    description="支持多周期分析",
    group="数据源"
)
```

---

## 11. 附录

### 11.1 数据库 Schema

#### strategies 表
```sql
CREATE TABLE strategies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    file_path VARCHAR(255) NOT NULL,
    class_name VARCHAR(100) NOT NULL,
    parameter_schema JSON,
    version VARCHAR(20),
    code_hash VARCHAR(64),
    status VARCHAR(20) DEFAULT 'active',
    category strategy_category,
    risk_level risk_level,
    tags VARCHAR[] DEFAULT '{}',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_strategies_status ON strategies(status);
CREATE INDEX idx_strategies_class_name ON strategies(class_name);
```

#### strategy_runs 表
```sql
CREATE TABLE strategy_runs (
    id VARCHAR(36) PRIMARY KEY,
    strategy_id INTEGER REFERENCES strategies(id),
    strategy_version VARCHAR(20),
    mode VARCHAR(20) NOT NULL,
    instruments JSON NOT NULL,
    parameters JSON NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',
    upgrade_required BOOLEAN DEFAULT false,
    start_time TIMESTAMP,
    stop_time TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_strategy_runs_strategy_id ON strategy_runs(strategy_id);
CREATE INDEX idx_strategy_runs_status ON strategy_runs(status);
CREATE INDEX idx_strategy_runs_upgrade ON strategy_runs(strategy_id, upgrade_required);
```

### 11.2 枚举类型

#### StrategyCategory
```python
class StrategyCategory(str, Enum):
    TREND_FOLLOWING = "trend_following"      # 趋势跟随
    MEAN_REVERSION = "mean_reversion"        # 均值回归
    MOMENTUM = "momentum"                    # 动量策略
    VOLATILITY = "volatility"                # 波动率策略
    ARBITRAGE = "arbitrage"                  # 套利策略
    MARKET_MAKING = "market_making"          # 做市策略
```

#### RiskLevel
```python
class RiskLevel(str, Enum):
    LOW = "low"              # 低风险
    MEDIUM = "medium"        # 中风险
    HIGH = "high"            # 高风险
    VERY_HIGH = "very_high"  # 极高风险
```

### 11.3 相关文档

- [系统架构](./ARCHITECTURE.md) - 整体架构设计
- [GraphQL API](./API.md) - API 接口文档
- [测试指南](./TESTING_GUIDE.md) - 测试规范
- [编码规范](./CODING_STANDARDS.md) - 代码风格

---

## 变更历史

- **2025-10-05**: 整合策略文档,反映最新重构(StrategyReconciler, ParameterSchema)
- **2025-09-26**: 创建策略自动注册文档
- **2025-09-20**: 创建参数配置规范文档
- **2025-09-15**: 创建动态加载文档

---

**维护团队**: QuantX Development Team
**最后更新**: 2025-10-05
