"""
pytest 配置文件 - 测试固件和共享配置
"""
import asyncio
import importlib.util
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient


def pytest_addoption(parser):
    parser.addoption(
        "--quantx-run-e2e",
        action="store_true",
        default=False,
        help="run QuantX tests marked e2e against explicitly approved services",
    )


def pytest_collection_modifyitems(config, items):
    enabled = config.getoption("--quantx-run-e2e") or os.getenv(
        "QUANTX_RUN_E2E", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    if enabled:
        return
    skip = pytest.mark.skip(
        reason=(
            "QuantX e2e tests are opt-in; pass --quantx-run-e2e only after "
            "approving their external-state effects"
        )
    )
    for item in items:
        if item.get_closest_marker("e2e") is not None:
            item.add_marker(skip)


def _module_missing(module_name: str) -> bool:
    return module_name not in sys.modules and importlib.util.find_spec(module_name) is None


if _module_missing("strawberry"):
    strawberry_stub = types.ModuleType("strawberry")
    strawberry_stub.__path__ = []

    def _identity_decorator(obj=None, *args, **kwargs):
        if obj is not None and callable(obj) and not kwargs and not args:
            return obj
        return lambda target: target

    strawberry_stub.enum = _identity_decorator
    strawberry_stub.type = _identity_decorator
    strawberry_stub.input = _identity_decorator
    strawberry_stub.field = _identity_decorator
    strawberry_stub.mutation = _identity_decorator
    strawberry_stub.subscription = _identity_decorator
    strawberry_stub.enum_value = lambda value, description=None: value
    strawberry_stub.ID = str

    class _Private:
        def __class_getitem__(cls, item):
            return item

    strawberry_stub.Private = _Private
    strawberry_stub.types = types.SimpleNamespace(Info=object)
    strawberry_stub.lazy = lambda path: path
    sys.modules["strawberry"] = strawberry_stub

if _module_missing("strawberry.dataloader"):
    dataloader_stub = types.ModuleType("strawberry.dataloader")

    class DataLoader:
        def __init__(self, *args, **kwargs):
            pass

    dataloader_stub.DataLoader = DataLoader
    sys.modules["strawberry.dataloader"] = dataloader_stub

if _module_missing("strawberry.fastapi"):
    fastapi_stub = types.ModuleType("strawberry.fastapi")

    class GraphQLRouter:
        def __init__(self, *args, **kwargs):
            pass

    fastapi_stub.GraphQLRouter = GraphQLRouter
    sys.modules["strawberry.fastapi"] = fastapi_stub

if _module_missing("strawberry.scalars"):
    scalars_stub = types.ModuleType("strawberry.scalars")
    scalars_stub.JSON = dict
    sys.modules["strawberry.scalars"] = scalars_stub
    sys.modules["strawberry"].scalars = scalars_stub

_xtquant_stubbed = False
if _module_missing("xtquant"):
    xtquant_stub = types.ModuleType("xtquant")
    xtquant_stub.__path__ = []

    class DummyConstant:
        ACCOUNT_STATUS_OK = 0
        ACCOUNT_STATUS_FAIL = 3

        def __getattr__(self, name):
            return 0

    xtquant_stub.xtconstant = DummyConstant()
    xtquant_stub.xtdata = types.SimpleNamespace()
    sys.modules["xtquant"] = xtquant_stub
    _xtquant_stubbed = True

if _xtquant_stubbed:
    xttype_stub = types.ModuleType("xtquant.xttype")

    class XtPosition:
        pass

    class XtOrder:
        pass

    class XtTrade:
        pass

    class XtAsset:
        pass

    class StockAccount:
        def __init__(self, *args, **kwargs):
            pass

    xttype_stub.XtPosition = XtPosition
    xttype_stub.XtOrder = XtOrder
    xttype_stub.XtTrade = XtTrade
    xttype_stub.XtAsset = XtAsset
    xttype_stub.StockAccount = StockAccount
    sys.modules["xtquant.xttype"] = xttype_stub

    xttrader_stub = types.ModuleType("xtquant.xttrader")

    class XtQuantTrader:
        def __init__(self, *args, **kwargs):
            pass

    class XtQuantTraderCallback:
        pass

    xttrader_stub.XtQuantTrader = XtQuantTrader
    xttrader_stub.XtQuantTraderCallback = XtQuantTraderCallback
    sys.modules["xtquant.xttrader"] = xttrader_stub

if _module_missing("pypinyin"):
    pypinyin_stub = types.ModuleType("pypinyin")
    pypinyin_stub.lazy_pinyin = lambda value, *args, **kwargs: list(str(value))
    sys.modules["pypinyin"] = pypinyin_stub


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def app():
    """创建测试应用实例"""
    from quantx_api.main import app
    return app

@pytest.fixture
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """创建异步测试客户端"""
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

@pytest.fixture
def sync_client(app):
    """创建同步测试客户端"""
    return TestClient(app)


@pytest.fixture
def authorized_graphql_context():
    """Authenticated default context for resolver-focused GraphQL tests."""
    from quantx_api.auth.principal import Principal

    return {
        "principal": Principal(
            user_id="test-user",
            username="test-user",
            display_name="Test User",
            device_session_id="test-session",
            access_token_expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).replace(tzinfo=None),
            permissions=frozenset(
                {
                    "market:read",
                    "orders:read",
                    "portfolio:read",
                    "strategy:read",
                    "system-status:read",
                    "trade:approve",
                    "mutation:write",
                    "assistant:read",
                    "assistant:write",
                }
            ),
            authorized_account_ids=(
                "test-account",
                "account-1",
                "300000013250",
            ),
        ),
        "request_id": "test-request",
    }

@pytest.fixture
def sample_stock_data():
    """示例股票数据"""
    return {
        "code": "000001",
        "name": "平安银行",
        "current_price": 12.50,
        "change": 0.15,
        "change_percent": 1.22,
        "volume": 1500000
    }

@pytest.fixture
def sample_order_data():
    """示例订单数据"""
    return {
        "stock_code": "000001",
        "order_type": "BUY",
        "quantity": 100,
        "price": 12.50
    }

@pytest.fixture
def sample_strategy_data():
    """示例策略数据"""
    return {
        "name": "测试策略",
        "description": "这是一个测试策略",
        "parameters": {
            "symbol": "000001",
            "period": "1d",
            "threshold": 0.02
        }
    }

@pytest.fixture
def sample_xtquant_config():
    """示例 XTQuant 配置"""
    return {
        "xtquant": {
            "data_server": {
                "host": "127.0.0.1",
                "port": 58610,
                "username": "test_user",
                "password": "test_pass"
            },
            "trading_server": {
                "host": "127.0.0.1",
                "port": 58611,
                "username": "test_user",
                "password": "test_pass"
            },
            "account": {
                "account_id": "test_account",
                "account_type": "stock"
            }
        }
    }

@pytest.fixture
def sample_price_data():
    """示例价格数据"""
    import numpy as np
    import pandas as pd

    np.random.seed(42)
    dates = pd.date_range('2023-01-01', periods=100, freq='D')

    data = pd.DataFrame({
        'open': 100 + np.random.randn(100) * 2,
        'high': 102 + np.random.randn(100) * 2,
        'low': 98 + np.random.randn(100) * 2,
        'close': 100 + np.random.randn(100) * 2,
        'volume': np.random.randint(1000, 10000, 100)
    }, index=dates)

    # 确保价格关系正确
    data['high'] = np.maximum(data[['open', 'close']].max(axis=1), data['high'])
    data['low'] = np.minimum(data[['open', 'close']].min(axis=1), data['low'])

    return data

@pytest.fixture
def mock_xtdata():
    """模拟 xtdata 对象"""
    from unittest.mock import MagicMock

    import numpy as np
    import pandas as pd

    mock = MagicMock()

    # 模拟连接
    mock.connect.return_value = True
    mock.disconnect.return_value = None

    # 模拟市场数据
    mock_data = pd.DataFrame({
        'time': pd.date_range('2023-01-01', periods=10, freq='D'),
        'open': np.random.uniform(10, 20, 10),
        'high': np.random.uniform(20, 30, 10),
        'low': np.random.uniform(5, 15, 10),
        'close': np.random.uniform(10, 25, 10),
        'volume': np.random.randint(1000, 10000, 10)
    })
    mock.get_market_data.return_value = mock_data

    # 模拟实时数据
    mock.get_full_tick.return_value = {
        '000001.SZ': {
            'lastPrice': 10.50,
            'changeRate': 0.02,
            'volume': 1500000,
            'time': '2023-01-01 14:30:00'
        }
    }

    return mock

@pytest.fixture
def mock_xttrader():
    """模拟 xttrader 对象"""
    from unittest.mock import MagicMock

    import pandas as pd

    mock = MagicMock()

    # 模拟连接
    mock.connect.return_value = 0  # 0表示成功
    mock.disconnect.return_value = None

    # 模拟下单
    mock.order_stock.return_value = "ORDER_123456"
    mock.cancel_order_stock.return_value = 0

    # 模拟账户信息
    mock.query_stock_asset.return_value = {
        'account_id': 'test_account',
        'total_asset': 1000000.0,
        'available_cash': 500000.0,
        'market_value': 500000.0,
        'frozen_cash': 0.0
    }

    # 模拟持仓信息
    mock.query_stock_positions.return_value = pd.DataFrame({
        'stock_code': ['000001.SZ', '600036.SH'],
        'stock_name': ['平安银行', '招商银行'],
        'volume': [1000, 500],
        'can_use_volume': [1000, 500],
        'open_price': [12.50, 45.80],
        'last_price': [13.20, 47.50],
        'unrealized_pnl': [700.0, 850.0]
    })

    # 模拟订单信息
    mock.query_stock_orders.return_value = pd.DataFrame({
        'order_id': ['ORDER_123456', 'ORDER_123457'],
        'stock_code': ['000001.SZ', '600036.SH'],
        'order_type': ['buy', 'sell'],
        'order_volume': [100, 200],
        'price': [10.50, 47.00],
        'traded_volume': [100, 0],
        'order_status': ['filled', 'pending'],
        'order_time': ['2023-01-01 09:30:00', '2023-01-01 10:00:00']
    })

    return mock

@pytest.fixture
def sample_trading_account():
    """示例交易账户信息"""
    return {
        'account_id': 'test_account_001',
        'total_asset': 1000000.0,
        'available_cash': 300000.0,
        'market_value': 700000.0,
        'frozen_cash': 0.0,
        'profit_loss': 50000.0,
        'profit_loss_ratio': 0.05
    }
