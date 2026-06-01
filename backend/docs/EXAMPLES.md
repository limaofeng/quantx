# 💡 QuantX 使用示例

本文档提供 QuantX 量化交易系统的常见使用场景和代码示例，帮助开发者快速上手核心功能。

## 📋 目录

- [环境准备](#环境准备)
- [策略开发示例](#策略开发示例)
- [API 使用示例](#api-使用示例)
- [数据获取示例](#数据获取示例)
- [交易操作示例](#交易操作示例)
- [工作流编排示例](#工作流编排示例)
- [技术指标计算示例](#技术指标计算示例)
- [实时数据订阅示例](#实时数据订阅示例)
- [完整应用示例](#完整应用示例)

## 🚀 环境准备

### 1. 安装依赖

```bash
# 克隆项目
git clone https://github.com/yourusername/quantx.git
cd quantx/backend

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件配置数据库连接等信息
```

### 2. 基础配置

```python
# config/settings.py
import os
from pydantic import BaseSettings

class Settings(BaseSettings):
    # 数据库配置
    database_url: str = "postgresql://user:password@localhost/quantx"
    influxdb_url: str = "http://localhost:8086"
    redis_url: str = "redis://localhost:6379"

    # 交易配置
    trading_enabled: bool = False  # 开发环境默认禁用真实交易

    class Config:
        env_file = ".env"

settings = Settings()
```

### 3. 启动服务

```bash
# 开发环境启动
python main.py

# 或者使用 uvicorn
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 📈 策略开发示例

### 1. 简单移动平均策略

```python
# file: strategies/ma_cross_strategy.py
from typing import List

from core.strategies.base import (
    StrategyBase,
    StrategyCadence,
    StrategyInput,
    StrategyOutput,
    TradeIntent,
    TradeIntentDirection,
)
from core.indicators import MA

class MACrossStrategy(StrategyBase):
    """移动平均交叉策略"""

    def __init__(self, context, fast_period: int = 10, slow_period: int = 20):
        super().__init__(context)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.fast_ma = MA(period=fast_period)
        self.slow_ma = MA(period=slow_period)
        self.prices: List[float] = []

    def on_init(self) -> None:
        """策略初始化"""
        self.log_info("移动平均交叉策略初始化完成")
        self.log_info(f"快速均线周期: {self.fast_period}, 慢速均线周期: {self.slow_period}")

    async def step(self, input: StrategyInput) -> StrategyOutput:
        """处理统一策略输入并输出交易意图"""
        if input.cadence != StrategyCadence.BAR:
            return StrategyOutput()

        bar = input.event
        close_price = getattr(bar, "close_price", getattr(bar, "close", 0.0))
        self.prices.append(close_price)

        # 保持价格序列长度
        if len(self.prices) > self.slow_period * 2:
            self.prices = self.prices[-self.slow_period * 2:]

        # 需要足够的数据才能计算指标
        if len(self.prices) < self.slow_period:
            return StrategyOutput()

        # 计算移动平均
        fast_ma_value = self.fast_ma.calculate(self.prices[-self.fast_period:])
        slow_ma_value = self.slow_ma.calculate(self.prices[-self.slow_period:])
        intents: List[TradeIntent] = []

        # 获取前一个值判断交叉
        if len(self.prices) >= self.slow_period + 1:
            prev_fast = self.fast_ma.calculate(self.prices[-self.fast_period-1:-1])
            prev_slow = self.slow_ma.calculate(self.prices[-self.slow_period-1:-1])

            # 金叉：快线上穿慢线
            if prev_fast <= prev_slow and fast_ma_value > slow_ma_value:
                intents.append(TradeIntent(
                    strategy_id=self.name,
                    run_id=self.context.run_id,
                    instrument_code=input.instrument_code,
                    direction=TradeIntentDirection.BUY,
                    bucket="swing",
                    reason="ma_cross_buy",
                    target_position_pct=0.2,
                    limit_price_hint=close_price,
                    metadata={
                        "fast_ma": fast_ma_value,
                        "slow_ma": slow_ma_value,
                    },
                ))

            # 死叉：快线下穿慢线
            elif prev_fast >= prev_slow and fast_ma_value < slow_ma_value:
                intents.append(TradeIntent(
                    strategy_id=self.name,
                    run_id=self.context.run_id,
                    instrument_code=input.instrument_code,
                    direction=TradeIntentDirection.SELL,
                    bucket="swing",
                    reason="ma_cross_sell",
                    target_position_pct=0.0,
                    limit_price_hint=close_price,
                    metadata={
                        "fast_ma": fast_ma_value,
                        "slow_ma": slow_ma_value,
                    },
                ))

        return StrategyOutput(trade_intents=intents)

# 使用示例
async def run_ma_cross_strategy():
    """运行移动平均交叉策略"""
    from core.strategy_manager import StrategyManager
    from core.config import StrategyConfig

    # 创建策略配置
    config = StrategyConfig(
        strategy_class="MACrossStrategy",
        parameters={"fast_period": 10, "slow_period": 20},
        instruments=["000001.SZ", "000002.SZ"],
        mode="backtest"
    )

    # 创建策略管理器
    manager = StrategyManager()

    # 注册并启动策略
    strategy = MACrossStrategy(fast_period=10, slow_period=20)
    strategy_id = manager.register_strategy(strategy)

    await manager.start_strategy(strategy_id, config)

    print(f"策略已启动，ID: {strategy_id}")

# 运行
if __name__ == "__main__":
    import asyncio
    asyncio.run(run_ma_cross_strategy())
```

### 2. RSI 均值回归策略

```python
# file: strategies/rsi_mean_reversion.py
from typing import List

from core.strategies.base import (
    StrategyBase,
    StrategyCadence,
    StrategyInput,
    StrategyOutput,
    TradeIntent,
    TradeIntentDirection,
)
from core.indicators import RSI

class RSIMeanReversionStrategy(StrategyBase):
    """RSI 均值回归策略"""

    def __init__(self, context, rsi_period: int = 14, oversold: float = 30, overbought: float = 70):
        super().__init__(context)
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.rsi = RSI(period=rsi_period)
        self.prices: List[float] = []

    async def step(self, input: StrategyInput) -> StrategyOutput:
        """处理统一策略输入并输出交易意图"""
        if input.cadence != StrategyCadence.BAR:
            return StrategyOutput()

        bar = input.event
        close_price = getattr(bar, "close_price", getattr(bar, "close", 0.0))
        self.prices.append(close_price)

        if len(self.prices) < self.rsi_period + 1:
            return StrategyOutput()

        rsi_value = self.rsi.calculate(self.prices)
        intents: List[TradeIntent] = []

        # 超卖信号
        if rsi_value < self.oversold:
            intents.append(TradeIntent(
                strategy_id=self.name,
                run_id=self.context.run_id,
                instrument_code=input.instrument_code,
                direction=TradeIntentDirection.BUY,
                bucket="swing",
                reason="rsi_oversold_buy",
                target_position_pct=0.2,
                limit_price_hint=close_price,
                metadata={"rsi": rsi_value},
            ))

        # 超买信号
        elif rsi_value > self.overbought:
            intents.append(TradeIntent(
                strategy_id=self.name,
                run_id=self.context.run_id,
                instrument_code=input.instrument_code,
                direction=TradeIntentDirection.SELL,
                bucket="swing",
                reason="rsi_overbought_sell",
                target_position_pct=0.0,
                limit_price_hint=close_price,
                metadata={"rsi": rsi_value},
            ))

        return StrategyOutput(trade_intents=intents)
```

## 🌐 API 使用示例

### 1. GraphQL 查询示例

```python
# file: examples/graphql_examples.py
import asyncio
import aiohttp
from gql import gql, Client
from gql.transport.aiohttp import AIOHTTPTransport

class QuantXClient:
    """QuantX GraphQL 客户端"""

    def __init__(self, url: str = "http://localhost:8000/graphql"):
        self.transport = AIOHTTPTransport(url=url)
        self.client = Client(transport=self.transport, fetch_schema_from_transport=True)

    async def get_strategies(self):
        """获取策略列表"""
        query = gql("""
            query GetStrategies {
                strategies {
                    id
                    name
                    description
                    filePath
                    className
                    defaultParameters
                    createTime
                }
            }
        """)

        result = await self.client.execute_async(query)
        return result["strategies"]

    async def start_strategy(self, strategy_id: int, instruments: List[str]):
        """启动策略"""
        mutation = gql("""
            mutation StartStrategy($input: StartStrategyInput!) {
                startStrategy(input: $input) {
                    success
                    message
                    runId
                }
            }
        """)

        variables = {
            "input": {
                "strategyId": strategy_id,
                "mode": "PAPER",
                "instruments": instruments,
                "parameters": '{"fast_period": 10, "slow_period": 20}'
            }
        }

        result = await self.client.execute_async(mutation, variable_values=variables)
        return result["startStrategy"]

    async def get_market_data(self, symbols: List[str]):
        """获取市场数据"""
        query = gql("""
            query GetMarketData($symbols: [String!]!) {
                marketData(symbols: $symbols) {
                    symbol
                    price
                    change
                    changePercent
                    volume
                    timestamp
                }
            }
        """)

        variables = {"symbols": symbols}
        result = await self.client.execute_async(query, variable_values=variables)
        return result["marketData"]

# 使用示例
async def main():
    client = QuantXClient()

    # 获取策略列表
    strategies = await client.get_strategies()
    print("可用策略:")
    for strategy in strategies:
        print(f"- {strategy['name']}: {strategy['description']}")

    # 启动策略
    if strategies:
        result = await client.start_strategy(
            strategy_id=strategies[0]["id"],
            instruments=["000001.SZ", "000002.SZ"]
        )
        print(f"启动结果: {result}")

    # 获取市场数据
    market_data = await client.get_market_data(["000001.SZ", "000002.SZ"])
    for data in market_data:
        print(f"{data['symbol']}: {data['price']} ({data['changePercent']:.2f}%)")

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. WebSocket 订阅示例

```python
# file: examples/websocket_subscription.py
import asyncio
import websockets
import json
from typing import Dict, Any

class RealtimeSubscriber:
    """实时数据订阅客户端"""

    def __init__(self, url: str = "ws://localhost:8000/graphql"):
        self.url = url
        self.websocket = None

    async def connect(self):
        """连接WebSocket"""
        self.websocket = await websockets.connect(self.url)

        # 发送连接初始化消息
        init_message = {
            "type": "connection_init",
            "payload": {}
        }
        await self.websocket.send(json.dumps(init_message))

        # 等待连接确认
        response = await self.websocket.recv()
        print(f"连接响应: {response}")

    async def subscribe_market_data(self, symbols: List[str]):
        """订阅市场数据"""
        subscription = {
            "id": "market_data_sub",
            "type": "start",
            "payload": {
                "query": """
                    subscription MarketDataStream($symbols: [String!]!) {
                        marketDataStream(symbols: $symbols) {
                            symbol
                            price
                            change
                            changePercent
                            volume
                            timestamp
                        }
                    }
                """,
                "variables": {"symbols": symbols}
            }
        }

        await self.websocket.send(json.dumps(subscription))

        # 监听数据
        async for message in self.websocket:
            data = json.loads(message)
            if data.get("type") == "data":
                market_data = data["payload"]["data"]["marketDataStream"]
                self.handle_market_data(market_data)

    def handle_market_data(self, data: Dict[str, Any]):
        """处理市场数据"""
        print(f"收到数据: {data['symbol']} - {data['price']} ({data['changePercent']:.2f}%)")

# 使用示例
async def main():
    subscriber = RealtimeSubscriber()
    await subscriber.connect()
    await subscriber.subscribe_market_data(["000001.SZ", "000002.SZ"])

if __name__ == "__main__":
    asyncio.run(main())
```

## 📊 数据获取示例

### 1. 历史数据获取

```python
# file: examples/data_examples.py
from services.market_data_service import MarketDataService
from datetime import datetime, timedelta

async def get_historical_data_example():
    """获取历史数据示例"""
    service = MarketDataService()

    # 获取过去30天的日K线数据
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)

    klines = await service.get_kline_data(
        symbol="000001.SZ",
        interval="1d",
        start_time=start_date,
        end_time=end_date
    )

    print(f"获取到 {len(klines)} 条K线数据")
    for kline in klines[-5:]:  # 显示最后5条
        print(f"{kline.timestamp}: O={kline.open_price}, H={kline.high_price}, "
              f"L={kline.low_price}, C={kline.close_price}, V={kline.volume}")

    return klines

# 批量获取多只股票数据
async def get_multiple_stocks_data():
    """批量获取多只股票数据"""
    service = MarketDataService()
    symbols = ["000001.SZ", "000002.SZ", "600000.SH", "600036.SH"]

    tasks = []
    for symbol in symbols:
        task = service.get_latest_market_data(symbol)
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    for symbol, data in zip(symbols, results):
        if data:
            print(f"{symbol}: {data.price} ({data.change_percent:.2f}%)")
        else:
            print(f"{symbol}: 数据获取失败")
```

### 2. 实时数据获取

```python
# file: examples/realtime_data.py
from services.market_data_service import MarketDataService
import asyncio

async def stream_realtime_data():
    """流式获取实时数据"""
    service = MarketDataService()
    symbols = ["000001.SZ", "000002.SZ"]

    async for market_data in service.stream_market_data(symbols):
        print(f"实时数据: {market_data.symbol} - {market_data.price}")

        # 可以在这里添加策略逻辑
        if market_data.change_percent > 5:
            print(f"⚠️ {market_data.symbol} 涨幅超过5%!")

# 使用定时器定期获取数据
async def periodic_data_fetch():
    """定期获取数据"""
    service = MarketDataService()

    while True:
        try:
            data = await service.get_latest_market_data("000001.SZ")
            if data:
                print(f"定期数据: {data.symbol} - {data.price}")
        except Exception as e:
            print(f"数据获取失败: {e}")

        await asyncio.sleep(10)  # 每10秒获取一次
```

## 💼 交易操作示例

### 1. 基础交易操作

```python
# file: examples/trading_examples.py
from services.trading_service import TradingService
from models.order import Order, OrderType, OrderSide
from models.position import Position

async def basic_trading_example():
    """基础交易操作示例"""
    trading_service = TradingService()

    # 查看账户信息
    account = await trading_service.get_account_info()
    print(f"账户余额: {account.available_cash}")
    print(f"总资产: {account.total_value}")

    # 查看当前持仓
    positions = await trading_service.get_positions()
    print("当前持仓:")
    for position in positions:
        print(f"- {position.symbol}: {position.quantity}股, "
              f"成本{position.average_cost}, 市值{position.market_value}")

    # 创建限价买单
    buy_order = Order(
        symbol="000001.SZ",
        order_type=OrderType.LIMIT,
        side=OrderSide.BUY,
        quantity=1000,
        price=10.50
    )

    # 下单
    order_result = await trading_service.place_order(buy_order)
    if order_result.success:
        print(f"买单下达成功，订单号: {order_result.order_id}")
    else:
        print(f"买单失败: {order_result.message}")

    # 创建市价卖单
    sell_order = Order(
        symbol="000001.SZ",
        order_type=OrderType.MARKET,
        side=OrderSide.SELL,
        quantity=500
    )

    # 下单
    sell_result = await trading_service.place_order(sell_order)
    print(f"卖单结果: {sell_result.message}")

# 批量下单示例
async def batch_orders_example():
    """批量下单示例"""
    trading_service = TradingService()

    orders = [
        Order(symbol="000001.SZ", order_type=OrderType.LIMIT,
              side=OrderSide.BUY, quantity=1000, price=10.20),
        Order(symbol="000002.SZ", order_type=OrderType.LIMIT,
              side=OrderSide.BUY, quantity=1000, price=25.50),
        Order(symbol="600000.SH", order_type=OrderType.LIMIT,
              side=OrderSide.BUY, quantity=500, price=12.80),
    ]

    # 并发下单
    tasks = [trading_service.place_order(order) for order in orders]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for order, result in zip(orders, results):
        if isinstance(result, Exception):
            print(f"{order.symbol} 下单失败: {result}")
        else:
            print(f"{order.symbol} 下单成功: {result.order_id}")
```

### 2. 风控和监控

```python
# file: examples/risk_management.py
from services.trading_service import TradingService
from services.position_service import PositionService

class RiskManager:
    """风险管理器"""

    def __init__(self, max_position_ratio: float = 0.1):
        self.trading_service = TradingService()
        self.position_service = PositionService()
        self.max_position_ratio = max_position_ratio

    async def check_position_risk(self, symbol: str, quantity: int) -> bool:
        """检查持仓风险"""
        account = await self.trading_service.get_account_info()
        current_position = await self.position_service.get_position(symbol)

        # 计算新的持仓比例
        current_qty = current_position.quantity if current_position else 0
        new_qty = current_qty + quantity

        # 估算新持仓市值
        market_data = await self.market_data_service.get_latest_market_data(symbol)
        new_market_value = new_qty * market_data.price

        # 检查持仓比例
        position_ratio = new_market_value / account.total_value

        if position_ratio > self.max_position_ratio:
            print(f"⚠️ 风险警告: {symbol} 持仓比例将达到 {position_ratio:.2%}, "
                  f"超过限制 {self.max_position_ratio:.2%}")
            return False

        return True

    async def monitor_positions(self):
        """监控持仓"""
        positions = await self.trading_service.get_positions()

        for position in positions:
            # 检查止损
            if position.unrealized_pnl_ratio < -0.1:  # 亏损超过10%
                print(f"🔴 止损警告: {position.symbol} 亏损 {position.unrealized_pnl_ratio:.2%}")

            # 检查止盈
            elif position.unrealized_pnl_ratio > 0.2:  # 盈利超过20%
                print(f"🟢 止盈提醒: {position.symbol} 盈利 {position.unrealized_pnl_ratio:.2%}")

# 使用示例
async def risk_management_example():
    risk_manager = RiskManager()

    # 检查风险后下单
    symbol = "000001.SZ"
    quantity = 1000

    if await risk_manager.check_position_risk(symbol, quantity):
        order = Order(
            symbol=symbol,
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            quantity=quantity,
            price=10.50
        )
        result = await risk_manager.trading_service.place_order(order)
        print(f"风控通过，下单结果: {result}")
    else:
        print("风控不通过，停止下单")

    # 监控持仓
    await risk_manager.monitor_positions()
```

## 🔄 工作流编排示例

### 1. 自定义 Prefect 流程

```python
# file: examples/custom_flow.py
from prefect import flow, task
from datetime import datetime
from services.market_data_service import MarketDataService
from services.trading_service import TradingService

@task
async def fetch_market_data_task(symbols: List[str]):
    """获取市场数据任务"""
    service = MarketDataService()
    data = []

    for symbol in symbols:
        market_data = await service.get_latest_market_data(symbol)
        if market_data:
            data.append(market_data)

    return data

@task
async def analyze_intents_task(market_data: List[dict]):
    """分析交易意图任务"""
    intents = []

    for data in market_data:
        # 简单的意图逻辑：涨幅超过3%
        if data.change_percent > 3:
            intents.append({
                "symbol": data.symbol,
                "action": "sell",
                "reason": f"涨幅{data.change_percent:.2f}%，考虑止盈"
            })
        # 跌幅超过3%
        elif data.change_percent < -3:
            intents.append({
                "symbol": data.symbol,
                "action": "buy",
                "reason": f"跌幅{data.change_percent:.2f}%，考虑抄底"
            })

    return intents

@task
async def execute_trades_task(intents: List[dict]):
    """执行交易任务"""
    trading_service = TradingService()
    results = []

    for intent in intents:
        try:
            # 这里只是示例，实际需要更复杂的逻辑
            order = Order(
                symbol=intent["symbol"],
                order_type=OrderType.MARKET,
                side=OrderSide.BUY if intent["action"] == "buy" else OrderSide.SELL,
                quantity=100  # 固定数量，实际应该根据仓位管理
            )

            result = await trading_service.place_order(order)
            results.append({
                "symbol": intent["symbol"],
                "success": result.success,
                "message": result.message
            })
        except Exception as e:
            results.append({
                "symbol": intent["symbol"],
                "success": False,
                "message": str(e)
            })

    return results

@flow(name="daily-trading-flow")
async def daily_trading_flow(symbols: List[str] = None):
    """每日交易流程"""
    if symbols is None:
        symbols = ["000001.SZ", "000002.SZ", "600000.SH"]

    print(f"开始每日交易流程，监控股票: {symbols}")

    # 获取市场数据
    market_data = await fetch_market_data_task(symbols)
    print(f"获取到 {len(market_data)} 只股票的数据")

    # 分析交易意图
    intents = await analyze_intents_task(market_data)
    print(f"生成 {len(intents)} 个交易意图")

    # 执行交易
    if intents:
        results = await execute_trades_task(intents)

        # 汇总结果
        success_count = sum(1 for r in results if r["success"])
        print(f"交易执行完成: {success_count}/{len(results)} 成功")

        for result in results:
            status = "✅" if result["success"] else "❌"
            print(f"{status} {result['symbol']}: {result['message']}")
    else:
        print("无交易意图，流程结束")

# 运行流程
if __name__ == "__main__":
    import asyncio
    asyncio.run(daily_trading_flow())
```

### 2. 定时任务配置

```python
# file: examples/scheduled_tasks.py
from prefect import serve
from prefect.deployments import Deployment
from datetime import timedelta

# 创建部署
deployment = Deployment.build_from_flow(
    flow=daily_trading_flow,
    name="daily-trading-deployment",
    schedule=timedelta(hours=1),  # 每小时运行一次
    parameters={"symbols": ["000001.SZ", "000002.SZ", "600000.SH"]},
    work_pool_name="default"
)

# 部署到 Prefect 服务器
async def deploy_flow():
    await deployment.apply()
    print("流程部署成功")

# 运行服务
if __name__ == "__main__":
    import asyncio
    asyncio.run(deploy_flow())
```

## 📊 技术指标计算示例

### 1. 技术指标使用

```python
# file: examples/indicators_examples.py
from core.indicators import MA, RSI, MACD, BollingerBands
import pandas as pd

def calculate_indicators_example():
    """技术指标计算示例"""
    # 模拟价格数据
    prices = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13, 14, 13, 12, 11, 10]

    # 移动平均
    ma5 = MA(period=5)
    ma10 = MA(period=10)

    ma5_value = ma5.calculate(prices)
    ma10_value = ma10.calculate(prices)

    print(f"5日均线: {ma5_value:.2f}")
    print(f"10日均线: {ma10_value:.2f}")

    # RSI
    rsi = RSI(period=14)
    rsi_value = rsi.calculate(prices)
    print(f"RSI: {rsi_value:.2f}")

    # MACD
    macd = MACD()
    macd_line, signal_line, histogram = macd.calculate(prices)
    print(f"MACD: {macd_line:.4f}, signal line: {signal_line:.4f}, Hist: {histogram:.4f}")

    # 布林带
    bb = BollingerBands(period=10, std_dev=2)
    upper, middle, lower = bb.calculate(prices)
    print(f"布林带 - 上轨: {upper:.2f}, 中轨: {middle:.2f}, 下轨: {lower:.2f}")

# 批量计算示例
async def batch_indicator_calculation():
    """批量计算技术指标"""
    from services.market_data_service import MarketDataService

    service = MarketDataService()
    symbols = ["000001.SZ", "000002.SZ"]

    for symbol in symbols:
        # 获取历史数据
        klines = await service.get_kline_data(
            symbol=symbol,
            interval="1d",
            limit=50
        )

        if not klines:
            continue

        prices = [k.close_price for k in klines]

        # 计算指标
        ma20 = MA(period=20).calculate(prices)
        rsi14 = RSI(period=14).calculate(prices)

        # 当前价格
        current_price = prices[-1]

        print(f"\n{symbol} 技术指标:")
        print(f"当前价格: {current_price:.2f}")
        print(f"20日均线: {ma20:.2f}")
        print(f"RSI: {rsi14:.2f}")

        # 简单的技术分析
        if current_price > ma20 and rsi14 < 70:
            print("📈 技术面偏多")
        elif current_price < ma20 and rsi14 > 30:
            print("📉 技术面偏空")
        else:
            print("➡️ 技术面中性")
```

## 🔗 实时数据订阅示例

### 1. 多种订阅方式

```python
# file: examples/subscription_examples.py
import asyncio
from typing import AsyncIterator
from services.market_data_service import MarketDataService

class DataSubscriptionManager:
    """数据订阅管理器"""

    def __init__(self):
        self.market_service = MarketDataService()
        self.subscriptions = {}

    async def subscribe_market_data(self, symbols: List[str], callback):
        """订阅市场数据"""
        async def data_handler():
            async for data in self.market_service.stream_market_data(symbols):
                await callback(data)

        task = asyncio.create_task(data_handler())
        self.subscriptions[f"market_{','.join(symbols)}"] = task
        return task

    async def subscribe_kline_data(self, symbol: str, interval: str, callback):
        """订阅K线数据"""
        async def kline_handler():
            async for kline in self.market_service.stream_kline_data(symbol, interval):
                await callback(kline)

        task = asyncio.create_task(kline_handler())
        self.subscriptions[f"kline_{symbol}_{interval}"] = task
        return task

    def unsubscribe(self, subscription_id: str):
        """取消订阅"""
        if subscription_id in self.subscriptions:
            self.subscriptions[subscription_id].cancel()
            del self.subscriptions[subscription_id]

    def unsubscribe_all(self):
        """取消所有订阅"""
        for task in self.subscriptions.values():
            task.cancel()
        self.subscriptions.clear()

# 使用示例
async def subscription_example():
    """订阅示例"""
    manager = DataSubscriptionManager()

    # 市场数据回调
    async def market_data_callback(data):
        print(f"市场数据: {data.symbol} - {data.price} ({data.change_percent:.2f}%)")

        # 可以在这里添加策略逻辑
        if abs(data.change_percent) > 5:
            print(f"⚠️ {data.symbol} 异动：{data.change_percent:.2f}%")

    # K线数据回调
    async def kline_data_callback(kline):
        print(f"K线数据: {kline.symbol} - "
              f"O:{kline.open_price} H:{kline.high_price} "
              f"L:{kline.low_price} C:{kline.close_price}")

    try:
        # 订阅市场数据
        await manager.subscribe_market_data(
            ["000001.SZ", "000002.SZ"],
            market_data_callback
        )

        # 订阅1分钟K线
        await manager.subscribe_kline_data(
            "000001.SZ",
            "1m",
            kline_data_callback
        )

        # 运行30秒
        await asyncio.sleep(30)

    finally:
        # 清理订阅
        manager.unsubscribe_all()
        print("所有订阅已取消")

if __name__ == "__main__":
    asyncio.run(subscription_example())
```

## 🎯 完整应用示例

### 1. 量化交易机器人

```python
# file: examples/trading_bot.py
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List
from dataclasses import dataclass

@dataclass
class BotConfig:
    """机器人配置"""
    symbols: List[str]
    max_position_ratio: float = 0.1
    stop_loss_ratio: float = -0.05
    take_profit_ratio: float = 0.1
    check_interval: int = 60  # 秒

class QuantTradingBot:
    """量化交易机器人"""

    def __init__(self, config: BotConfig):
        self.config = config
        self.market_service = MarketDataService()
        self.trading_service = TradingService()
        self.position_service = PositionService()
        self.is_running = False
        self.positions: Dict[str, Position] = {}

    async def start(self):
        """启动机器人"""
        print("🤖 量化交易机器人启动")
        self.is_running = True

        while self.is_running:
            try:
                await self.run_cycle()
                await asyncio.sleep(self.config.check_interval)
            except Exception as e:
                print(f"❌ 运行错误: {e}")
                await asyncio.sleep(10)

    async def stop(self):
        """停止机器人"""
        print("🛑 量化交易机器人停止")
        self.is_running = False

    async def run_cycle(self):
        """运行一个周期"""
        print(f"🔄 执行检查周期 - {datetime.now()}")

        # 更新持仓信息
        await self.update_positions()

        # 检查止盈止损
        await self.check_risk_management()

        # 寻找交易机会
        await self.find_trading_opportunities()

    async def update_positions(self):
        """更新持仓信息"""
        positions = await self.trading_service.get_positions()
        self.positions = {pos.symbol: pos for pos in positions}

    async def check_risk_management(self):
        """检查风险管理"""
        for symbol, position in self.positions.items():
            # 止损检查
            if position.unrealized_pnl_ratio <= self.config.stop_loss_ratio:
                print(f"🔴 {symbol} 触发止损: {position.unrealized_pnl_ratio:.2%}")
                await self.close_position(symbol, "止损")

            # 止盈检查
            elif position.unrealized_pnl_ratio >= self.config.take_profit_ratio:
                print(f"🟢 {symbol} 触发止盈: {position.unrealized_pnl_ratio:.2%}")
                await self.close_position(symbol, "止盈")

    async def find_trading_opportunities(self):
        """寻找交易机会"""
        for symbol in self.config.symbols:
            # 获取最新数据
            market_data = await self.market_service.get_latest_market_data(symbol)
            if not market_data:
                continue

            # 简单的交易逻辑
            if await self.should_buy(symbol, market_data):
                await self.open_position(symbol, market_data.price)

    async def should_buy(self, symbol: str, market_data) -> bool:
        """判断是否应该买入"""
        # 检查是否已有持仓
        if symbol in self.positions:
            return False

        # 检查资金是否充足
        account = await self.trading_service.get_account_info()
        position_value = 1000 * market_data.price  # 假设买1000股

        if position_value > account.available_cash * self.config.max_position_ratio:
            return False

        # 简单的技术指标判断
        # 这里可以添加更复杂的策略逻辑
        if market_data.change_percent < -2 and market_data.volume > 1000000:
            return True

        return False

    async def open_position(self, symbol: str, price: float):
        """开仓"""
        try:
            order = Order(
                symbol=symbol,
                order_type=OrderType.LIMIT,
                side=OrderSide.BUY,
                quantity=1000,
                price=price * 0.99  # 稍低于市价
            )

            result = await self.trading_service.place_order(order)
            if result.success:
                print(f"📈 {symbol} 开仓成功: {order.quantity}股 @{order.price}")
            else:
                print(f"❌ {symbol} 开仓失败: {result.message}")

        except Exception as e:
            print(f"❌ {symbol} 开仓异常: {e}")

    async def close_position(self, symbol: str, reason: str):
        """平仓"""
        try:
            position = self.positions.get(symbol)
            if not position:
                return

            order = Order(
                symbol=symbol,
                order_type=OrderType.MARKET,
                side=OrderSide.SELL,
                quantity=position.quantity
            )

            result = await self.trading_service.place_order(order)
            if result.success:
                print(f"📉 {symbol} 平仓成功: {reason}")
                # 从持仓字典中移除
                if symbol in self.positions:
                    del self.positions[symbol]
            else:
                print(f"❌ {symbol} 平仓失败: {result.message}")

        except Exception as e:
            print(f"❌ {symbol} 平仓异常: {e}")

# 使用示例
async def main():
    """主函数"""
    config = BotConfig(
        symbols=["000001.SZ", "000002.SZ", "600000.SH"],
        max_position_ratio=0.1,
        stop_loss_ratio=-0.05,
        take_profit_ratio=0.1,
        check_interval=60
    )

    bot = QuantTradingBot(config)

    try:
        # 启动机器人（这里只运行5分钟作为示例）
        task = asyncio.create_task(bot.start())
        await asyncio.sleep(300)  # 运行5分钟
        await bot.stop()

    except KeyboardInterrupt:
        print("用户中断")
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

## 🛠️ 开发调试示例

```python
# file: examples/debug_examples.py
import logging
from datetime import datetime

from core.strategy_manager import StrategyManager
from core.strategies.base import StrategyCadence, StrategyContext, StrategyInput
from models.enums import StrategyRunMode

# 配置日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def debug_strategy():
    """调试策略示例"""
    # 创建策略管理器
    manager = StrategyManager()

    # 启用调试模式
    context = StrategyContext(
        run_id="debug-ma-cross",
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={"fast_period": 5, "slow_period": 10},
    )
    strategy = MACrossStrategy(context, fast_period=5, slow_period=10)
    strategy.debug_mode = True

    # 注册策略
    strategy_id = manager.register_strategy(strategy)
    print(f"策略注册成功: {strategy_id}")

    # 模拟数据测试
    test_data = [
        KLine(symbol="000001.SZ", close_price=10.0, timestamp=datetime.now()),
        KLine(symbol="000001.SZ", close_price=10.5, timestamp=datetime.now()),
        KLine(symbol="000001.SZ", close_price=11.0, timestamp=datetime.now()),
        # ... 更多测试数据
    ]

    for data in test_data:
        output = await strategy.step(StrategyInput(
            run_id=context.run_id,
            strategy_id=strategy.name,
            timestamp=data.timestamp,
            cadence=StrategyCadence.BAR,
            instrument_code=data.symbol,
            event=data,
            parameters=context.parameters,
        ))
        print(f"输出交易意图数量: {len(output.trade_intents)}")

    print("调试完成")

if __name__ == "__main__":
    import asyncio
    asyncio.run(debug_strategy())
```

---

**相关文档**：
- [系统架构](./ARCHITECTURE.md)
- [功能模块](./MODULES.md)
- [API接口文档](./API.md)
- [测试指南](./TESTING_GUIDE.md)
- [编码规范](./CODING_STANDARDS.md)

**常用工具**：
- [GraphQL Playground](http://localhost:8000/graphql) - API 调试工具
- [Prefect UI](http://localhost:4200) - 工作流管理界面
- [Prometheus](http://localhost:9090) - 监控指标查看

*这些示例涵盖了 QuantX 系统的主要使用场景。在实际使用中，请根据具体需求调整参数和逻辑。*
