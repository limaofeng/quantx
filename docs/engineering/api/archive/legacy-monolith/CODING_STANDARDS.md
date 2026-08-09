# 💻 QuantX 编码规范

本文档定义了 QuantX 项目的 Python 编码标准、命名约定和最佳实践，确保代码质量和团队协作效率。

## 📋 目录

- [编码风格](#编码风格)
- [命名约定](#命名约定)
- [代码结构](#代码结构)
- [类型注解](#类型注解)
- [文档字符串](#文档字符串)
- [异步编程规范](#异步编程规范)
- [错误处理](#错误处理)
- [测试规范](#测试规范)
- [代码检查工具](#代码检查工具)
- [最佳实践](#最佳实践)

## 🎨 编码风格

### 基础配置

项目使用 `ruff` 作为主要的代码检查和格式化工具，配置如下：

```toml
[tool.ruff]
indent-width = 2                        # 使用 2 个空格作为缩进
line-length = 88                        # 默认行长度，与 Black 一致
target-version = "py39"                 # 指定 Python 版本
exclude = ["migrations", "tests", "core.bak"]

[tool.ruff.lint]
select = ["E", "F", "I"]                # 启用 pycodestyle (E)、pyflakes (F) 和 isort (I) 规则
ignore = ["E501"]                       # 忽略行长度超限警告

[tool.ruff.format]
quote-style = "double"                  # 使用双引号
indent-style = "space"                  # 使用空格缩进
```

### 代码格式化

```bash
# 格式化代码
ruff format .

# 检查代码风格
ruff check .

# 自动修复可修复的问题
ruff check . --fix
```

### 缩进和空行

```python
# ✅ 正确：使用 2 个空格缩进
class StrategyManager:
  def __init__(self, config: StrategyConfig):
    self.config = config
    self.strategies = {}

  def register_strategy(self, strategy: BaseStrategy) -> str:
    strategy_id = generate_id()
    self.strategies[strategy_id] = strategy
    return strategy_id

# ❌ 错误：使用 4 个空格缩进
class StrategyManager:
    def __init__(self, config: StrategyConfig):
        self.config = config
```

### 引号使用

```python
# ✅ 正确：使用双引号
message = "策略启动成功"
sql = "SELECT * FROM strategies WHERE status = 'active'"

# ❌ 错误：混用单双引号
message = '策略启动成功'  # 应该使用双引号
```

### 行长度

```python
# ✅ 正确：适当的行长度（88字符以内）
result = strategy_service.execute_strategy(
  strategy_id="MA_CROSS_001",
  instruments=["000001.SZ", "000002.SZ"],
  parameters={"fast_period": 10, "slow_period": 20}
)

# ❌ 错误：行太长
result = strategy_service.execute_strategy(strategy_id="MA_CROSS_001", instruments=["000001.SZ", "000002.SZ"], parameters={"fast_period": 10, "slow_period": 20})
```

## 🏷️ 命名约定

### 通用规则

- **snake_case**: 变量、函数、模块名
- **PascalCase**: 类名、类型别名
- **UPPER_CASE**: 常量
- **_leading_underscore**: 内部使用的私有成员

### 变量和函数命名

```python
# ✅ 正确的变量命名
strategy_id = "MA_CROSS_001"
current_price = 10.50
is_market_open = True
order_count = 0

# ✅ 正确的函数命名
def get_market_data(symbol: str) -> MarketData:
    pass

def calculate_moving_average(prices: List[float], period: int) -> float:
    pass

async def execute_trading_strategy(strategy: BaseStrategy) -> TradeResult:
    pass

# ❌ 错误的命名
strategyId = "MA_CROSS_001"  # 应该使用 snake_case
GetMarketData = lambda x: x  # 函数名应该小写
```

### 类命名

```python
# ✅ 正确的类命名
class StrategyManager:
    pass

class MarketDataService:
    pass

class OrderExecutionError(Exception):
    pass

# ❌ 错误的类命名
class strategy_manager:  # 应该使用 PascalCase
    pass

class marketDataService:  # 应该使用 PascalCase
    pass
```

### 常量命名

```python
# ✅ 正确的常量命名
DEFAULT_TIMEOUT = 30
MAX_RETRY_COUNT = 3
API_BASE_URL = "https://api.quantx.com"
TRADING_HOURS = {
    "open": "09:30",
    "close": "15:00"
}

# ❌ 错误的常量命名
default_timeout = 30  # 应该全大写
MaxRetryCount = 3     # 应该使用下划线分隔
```

### 金融术语命名规范

```python
# ✅ 推荐的金融术语命名
class KLineData:
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: int
    turnover: float

class Position:
    symbol: str
    quantity: int
    average_cost: float
    market_value: float
    unrealized_pnl: float  # profit and loss

class Order:
    order_id: str
    symbol: str
    order_type: OrderType
    order_side: OrderSide
    quantity: int
    price: Optional[float]
```

## 🏗️ 代码结构

### 模块导入顺序

```python
# 1. 标准库导入
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

# 2. 第三方库导入
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException
from sqlalchemy import Column, Integer, String

# 3. 项目内部导入
from core.strategies.base import BaseStrategy
from services.market_data_service import MarketDataService
from models.order import Order, OrderType
```

### 类结构组织

```python
class StrategyManager:
    """策略管理器，负责策略的生命周期管理"""

    # 1. 类常量
    DEFAULT_TIMEOUT = 30
    MAX_STRATEGIES = 100

    # 2. 构造函数
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.strategies: Dict[str, BaseStrategy] = {}
        self._logger = logging.getLogger(__name__)

    # 3. 公共方法
    def register_strategy(self, strategy: BaseStrategy) -> str:
        """注册新策略"""
        pass

    def start_strategy(self, strategy_id: str) -> None:
        """启动策略"""
        pass

    # 4. 私有方法
    def _validate_strategy(self, strategy: BaseStrategy) -> bool:
        """验证策略配置"""
        pass

    # 5. 特殊方法
    def __len__(self) -> int:
        return len(self.strategies)

    def __repr__(self) -> str:
        return f"StrategyManager(strategies={len(self.strategies)})"
```

### 函数组织

```python
# ✅ 正确：函数职责单一，参数清晰
def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    计算 RSI (相对强弱指数)

    Args:
        prices: 价格序列
        period: 计算周期，默认14

    Returns:
        RSI 值 (0-100)

    Raises:
        ValueError: 当价格数据不足时
    """
    if len(prices) < period + 1:
        raise ValueError(f"价格数据不足，需要至少 {period + 1} 个数据点")

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-change)

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi

# ❌ 错误：函数过于复杂，职责不清
def process_market_data_and_execute_orders(data):
    # 函数名太长，职责过多
    pass
```

## 🔤 类型注解

### 基本类型注解

```python
from typing import Dict, List, Optional, Union, Callable, Any
from decimal import Decimal

# ✅ 正确的类型注解
def get_strategy_by_id(strategy_id: str) -> Optional[BaseStrategy]:
    pass

def calculate_portfolio_value(
    positions: List[Position],
    prices: Dict[str, float]
) -> Decimal:
    pass

async def fetch_market_data(
    symbols: List[str],
    start_date: datetime,
    end_date: Optional[datetime] = None
) -> List[KLineData]:
    pass

# 复杂类型定义
StrategyParameters = Dict[str, Union[int, float, str, bool]]
PriceCallback = Callable[[str, float], None]
```

### 自定义类型

```python
from enum import Enum
from typing import TypeVar, Generic

# 枚举类型
class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"

class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"

# 泛型类型
T = TypeVar('T')

class Repository(Generic[T]):
    def save(self, entity: T) -> T:
        pass

    def find_by_id(self, id: str) -> Optional[T]:
        pass
```

### 协议和接口

```python
from typing import Protocol

class TradingBroker(Protocol):
    """交易经纪商接口协议"""

    def place_order(self, order: Order) -> str:
        """下单"""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        ...

    def get_positions(self) -> List[Position]:
        """获取持仓"""
        ...
```

## 📚 文档字符串

### 函数文档字符串

```python
def execute_strategy(
    strategy: BaseStrategy,
    instruments: List[str],
    start_date: datetime,
    end_date: Optional[datetime] = None
) -> StrategyResult:
    """
    执行策略回测或实盘交易

    Args:
        strategy: 策略实例
        instruments: 交易工具列表，如 ["000001.SZ", "000002.SZ"]
        start_date: 开始日期
        end_date: 结束日期，None 表示实时执行

    Returns:
        StrategyResult: 包含执行结果和绩效指标

    Raises:
        StrategyExecutionError: 当策略执行失败时
        InsufficientDataError: 当数据不足时

    Example:
        >>> strategy = MACrossStrategy(fast_period=10, slow_period=20)
        >>> result = execute_strategy(
        ...     strategy=strategy,
        ...     instruments=["000001.SZ"],
        ...     start_date=datetime(2023, 1, 1),
        ...     end_date=datetime(2023, 12, 31)
        ... )
        >>> print(f"总收益率: {result.total_return:.2%}")
    """
    pass
```

### 类文档字符串

```python
class MovingAverageCrossStrategy(BaseStrategy):
    """
    移动平均交叉策略

    当短期移动平均线向上穿越长期移动平均线时买入，
    向下穿越时卖出。这是一个经典的趋势跟踪策略。

    Attributes:
        fast_period: 短期均线周期
        slow_period: 长期均线周期
        position_size: 仓位大小比例 (0-1)

    Example:
        >>> strategy = MovingAverageCrossStrategy(
        ...     fast_period=10,
        ...     slow_period=20,
        ...     position_size=0.8
        ... )
        >>> strategy.initialize()
    """

    def __init__(self, fast_period: int = 10, slow_period: int = 20, position_size: float = 1.0):
        """
        初始化移动平均交叉策略

        Args:
            fast_period: 短期均线周期，必须小于长期周期
            slow_period: 长期均线周期，必须大于短期周期
            position_size: 仓位大小，取值范围 0-1

        Raises:
            ValueError: 当参数配置无效时
        """
        if fast_period >= slow_period:
            raise ValueError("短期周期必须小于长期周期")

        if not 0 < position_size <= 1:
            raise ValueError("仓位大小必须在 0-1 之间")

        super().__init__()
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.position_size = position_size
```

## ⚡ 异步编程规范

### 异步函数定义

```python
# ✅ 正确的异步函数
async def fetch_real_time_data(symbol: str) -> Optional[MarketData]:
    """获取实时市场数据"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"/api/market/{symbol}") as response:
            if response.status == 200:
                data = await response.json()
                return MarketData.from_dict(data)
            return None

# 异步生成器
async def stream_market_data(symbols: List[str]) -> AsyncIterator[MarketData]:
    """流式获取市场数据"""
    while True:
        for symbol in symbols:
            data = await fetch_real_time_data(symbol)
            if data:
                yield data
        await asyncio.sleep(1)
```

### 异步上下文管理器

```python
class DatabaseConnection:
    """数据库连接异步上下文管理器"""

    async def __aenter__(self):
        self.connection = await asyncpg.connect(DATABASE_URL)
        return self.connection

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.connection.close()

# 使用示例
async def save_strategy_result(result: StrategyResult):
    async with DatabaseConnection() as conn:
        await conn.execute(
            "INSERT INTO strategy_results (id, profit, drawdown) VALUES ($1, $2, $3)",
            result.id, result.profit, result.drawdown
        )
```

### 并发控制

```python
import asyncio
from asyncio import Semaphore

class StrategyExecutor:
    def __init__(self, max_concurrent: int = 5):
        self.semaphore = Semaphore(max_concurrent)

    async def execute_strategy_safe(self, strategy: BaseStrategy) -> StrategyResult:
        """限制并发执行的策略运行"""
        async with self.semaphore:
            return await self._execute_strategy(strategy)

    async def execute_multiple_strategies(
        self,
        strategies: List[BaseStrategy]
    ) -> List[StrategyResult]:
        """并发执行多个策略"""
        tasks = [
            self.execute_strategy_safe(strategy)
            for strategy in strategies
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)
```

## ⚠️ 错误处理

### 异常定义

```python
# 自定义异常层次结构
class QuantXError(Exception):
    """QuantX 基础异常"""
    pass

class StrategyError(QuantXError):
    """策略相关异常"""
    pass

class StrategyConfigError(StrategyError):
    """策略配置错误"""
    pass

class StrategyExecutionError(StrategyError):
    """策略执行错误"""
    pass

class TradingError(QuantXError):
    """交易相关异常"""
    pass

class InsufficientFundsError(TradingError):
    """资金不足异常"""
    pass

class OrderRejectedError(TradingError):
    """订单被拒绝异常"""
    pass
```

### 错误处理模式

```python
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ✅ 正确的错误处理
async def place_order_safe(order: Order) -> Optional[str]:
    """安全下单，包含完整的错误处理"""
    try:
        # 参数验证
        if order.quantity <= 0:
            raise ValueError("订单数量必须大于0")

        # 资金检查
        account = await get_account_info()
        if account.available_cash < order.estimated_cost:
            raise InsufficientFundsError(
                f"资金不足：需要 {order.estimated_cost}，可用 {account.available_cash}"
            )

        # 执行下单
        order_id = await trading_service.place_order(order)
        logger.info(f"订单下达成功：{order_id}")
        return order_id

    except InsufficientFundsError:
        logger.warning(f"资金不足，无法下单：{order}")
        raise  # 重新抛出业务异常

    except ValueError as e:
        logger.error(f"订单参数错误：{e}")
        raise StrategyConfigError(f"订单配置错误：{e}")

    except Exception as e:
        logger.error(f"下单失败：{e}", exc_info=True)
        raise StrategyExecutionError(f"下单过程中发生未知错误：{e}")

# ❌ 错误的错误处理
def bad_error_handling():
    try:
        # 一些操作
        pass
    except:  # 不要使用裸露的 except
        pass  # 不要忽略异常
```

### 错误恢复策略

```python
from functools import wraps
import asyncio

def retry_on_failure(max_retries: int = 3, delay: float = 1.0):
    """重试装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except (ConnectionError, TimeoutError) as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"函数 {func.__name__} 第 {attempt + 1} 次尝试失败，"
                            f"{delay} 秒后重试：{e}"
                        )
                        await asyncio.sleep(delay * (2 ** attempt))  # 指数退避
                    else:
                        logger.error(f"函数 {func.__name__} 重试 {max_retries} 次后仍然失败")

            raise last_exception
        return wrapper
    return decorator

# 使用示例
@retry_on_failure(max_retries=3, delay=1.0)
async def fetch_market_data_with_retry(symbol: str) -> MarketData:
    """带重试的市场数据获取"""
    return await external_api.get_market_data(symbol)
```

## 🧪 测试规范

### 测试文件组织

```python
# tests/unit/core/test_strategy_manager.py
import pytest
from unittest.mock import Mock, patch, AsyncMock
from core.strategy_manager import StrategyManager
from core.strategies.base import BaseStrategy

class TestStrategyManager:
    """策略管理器测试类"""

    @pytest.fixture
    def strategy_manager(self):
        """测试固件：策略管理器实例"""
        config = Mock()
        return StrategyManager(config)

    @pytest.fixture
    def mock_strategy(self):
        """测试固件：模拟策略"""
        strategy = Mock(spec=BaseStrategy)
        strategy.name = "test_strategy"
        return strategy

    def test_register_strategy_success(self, strategy_manager, mock_strategy):
        """测试策略注册成功场景"""
        # Given
        expected_id_prefix = "strategy_"

        # When
        strategy_id = strategy_manager.register_strategy(mock_strategy)

        # Then
        assert strategy_id.startswith(expected_id_prefix)
        assert strategy_id in strategy_manager.strategies
        assert strategy_manager.strategies[strategy_id] == mock_strategy

    def test_register_duplicate_strategy_raises_error(self, strategy_manager, mock_strategy):
        """测试注册重复策略抛出异常"""
        # Given
        strategy_manager.register_strategy(mock_strategy)

        # When & Then
        with pytest.raises(StrategyConfigError, match="策略已存在"):
            strategy_manager.register_strategy(mock_strategy)

    @pytest.mark.asyncio
    async def test_start_strategy_async(self, strategy_manager, mock_strategy):
        """测试异步启动策略"""
        # Given
        strategy_id = strategy_manager.register_strategy(mock_strategy)
        mock_strategy.start = AsyncMock()

        # When
        await strategy_manager.start_strategy(strategy_id)

        # Then
        mock_strategy.start.assert_called_once()
```

### Mock 使用规范

```python
# ✅ 正确的 Mock 使用
@patch('services.trading_service.TradingService')
async def test_strategy_execution_with_mock_trading(mock_trading_service):
    """使用 Mock 测试策略执行"""
    # 配置 Mock 返回值
    mock_trading_service.return_value.place_order = AsyncMock(return_value="ORDER_123")
    mock_trading_service.return_value.get_account_info = AsyncMock(
        return_value=AccountInfo(available_cash=10000)
    )

    # 执行测试
    strategy = MACrossStrategy()
    result = await strategy.execute()

    # 验证调用
    mock_trading_service.return_value.place_order.assert_called_once()

# ✅ 参数化测试
@pytest.mark.parametrize("period,expected", [
    (10, 45.2),
    (20, 42.8),
    (30, 41.5),
])
def test_rsi_calculation(period, expected):
    """参数化测试 RSI 计算"""
    prices = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13]
    result = calculate_rsi(prices, period)
    assert abs(result - expected) < 0.1
```

## 🔧 代码检查工具

### Pre-commit 配置

项目使用 pre-commit 钩子确保代码质量：

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.13.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

### 代码检查命令

```bash
# 运行所有检查
ruff check .

# 格式化代码
ruff format .

# 检查类型注解
python -m mypy . --ignore-missing-imports

# 运行测试
python -m pytest tests/ --cov=.
```

### IDE 配置建议

```json
// .vscode/settings.json
{
  "python.defaultInterpreterPath": "./venv/bin/python",
  "python.linting.enabled": true,
  "python.linting.ruffEnabled": true,
  "python.formatting.provider": "ruff",
  "editor.formatOnSave": true,
  "editor.codeActionsOnSave": {
    "source.organizeImports": true
  }
}
```

## ✨ 最佳实践

### 性能优化

```python
# ✅ 使用生成器节省内存
def process_large_dataset(data_source):
    for chunk in data_source.iter_chunks(size=1000):
        yield process_chunk(chunk)

# ✅ 缓存计算结果
from functools import lru_cache

@lru_cache(maxsize=128)
def expensive_calculation(param: str) -> float:
    # 复杂计算
    return result

# ✅ 批量操作
async def save_orders_batch(orders: List[Order]):
    """批量保存订单，提高性能"""
    async with database.transaction():
        await database.execute_many(
            "INSERT INTO orders (...) VALUES (...)",
            [order.to_dict() for order in orders]
        )
```

### 安全编程

```python
# ✅ 避免 SQL 注入
async def get_orders_by_symbol(symbol: str) -> List[Order]:
    """安全的数据库查询"""
    query = "SELECT * FROM orders WHERE symbol = $1"
    result = await database.fetch_all(query, symbol)
    return [Order.from_dict(row) for row in result]

# ✅ 敏感信息处理
def mask_account_info(account_id: str) -> str:
    """脱敏账户信息"""
    if len(account_id) > 4:
        return account_id[:2] + "*" * (len(account_id) - 4) + account_id[-2:]
    return "****"

# ✅ 输入验证
def validate_order_params(order: Order) -> None:
    """验证订单参数"""
    if not order.symbol or not order.symbol.strip():
        raise ValueError("股票代码不能为空")

    if order.quantity <= 0:
        raise ValueError("订单数量必须大于0")

    if order.price is not None and order.price <= 0:
        raise ValueError("订单价格必须大于0")
```

### 日志记录

```python
import logging
from functools import wraps

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

def log_execution_time(func):
    """记录函数执行时间的装饰器"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            execution_time = time.time() - start_time
            logger.info(f"{func.__name__} 执行完成，耗时 {execution_time:.2f}s")
            return result
        except Exception as e:
            execution_time = time.time() - start_time
            logger.error(f"{func.__name__} 执行失败，耗时 {execution_time:.2f}s，错误：{e}")
            raise
    return wrapper

# 使用示例
@log_execution_time
async def execute_trading_strategy(strategy: BaseStrategy):
    """执行交易策略"""
    logger.info(f"开始执行策略：{strategy.name}")
    result = await strategy.run()
    logger.info(f"策略执行完成，收益率：{result.return_rate:.2%}")
    return result
```

### 配置管理

```python
from pydantic import BaseSettings, Field
from typing import Optional

class Settings(BaseSettings):
    """应用配置"""

    # 数据库配置
    database_url: str = Field(..., env="DATABASE_URL")
    redis_url: str = Field("redis://localhost:6379", env="REDIS_URL")

    # 交易配置
    trading_enabled: bool = Field(False, env="TRADING_ENABLED")
    max_position_size: float = Field(0.1, env="MAX_POSITION_SIZE")

    # API 配置
    api_key: Optional[str] = Field(None, env="API_KEY")
    api_secret: Optional[str] = Field(None, env="API_SECRET")

    # 日志配置
    log_level: str = Field("INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        case_sensitive = False

# 全局配置实例
settings = Settings()
```

---

**相关文档**：
- [测试指南](./TESTING_GUIDE.md)
- [API接口文档](./API.md)
- [系统架构](./ARCHITECTURE.md)
- [使用示例](./EXAMPLES.md)

**工具推荐**：
- [Ruff](https://docs.astral.sh/ruff/) - 代码检查和格式化
- [Pre-commit](https://pre-commit.com/) - Git 钩子管理
- [Pytest](https://docs.pytest.org/) - 测试框架
- [MyPy](https://mypy.readthedocs.io/) - 类型检查

*请确保所有团队成员都遵循这些编码规范，以保持代码质量和一致性。*
