"""
Quote DataLoader 单元测试
验证批量加载功能正常工作，并确认解决了 N+1 查询问题
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from quantx_api.gqlapi.dataloaders.quote_loader import load_quotes
from quantx_api.gqlapi.types.market_data_types import StockQuote
from quantx_infrastructure.models.tick import Tick


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_quotes_batch_loading():
  """测试批量加载多个股票行情"""
  # 创建测试用的 Tick 数据
  mock_tick_600000 = Tick(
    stock_code="600000.SH",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=10.50,
    open=10.20,
    high=10.80,
    low=10.00,
    last_close=10.30,
    amount=1000000.0,
    volume=95000.0,
    pvolume=95000.0,
    tickvol=500.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=150,
    ask_price=[10.51, 10.52, 10.53, 10.54, 10.55],
    bid_price=[10.50, 10.49, 10.48, 10.47, 10.46],
    ask_vol=[1000, 2000, 1500, 3000, 2500],
    bid_vol=[1200, 1800, 2200, 1600, 2000]
  )

  mock_tick_000001 = Tick(
    stock_code="000001.SZ",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=12.80,
    open=12.50,
    high=13.00,
    low=12.30,
    last_close=12.60,
    amount=2000000.0,
    volume=155000.0,
    pvolume=155000.0,
    tickvol=800.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=250,
    ask_price=[12.81, 12.82, 12.83, 12.84, 12.85],
    bid_price=[12.80, 12.79, 12.78, 12.77, 12.76],
    ask_vol=[1500, 2500, 1800, 3500, 2800],
    bid_vol=[1400, 2100, 2500, 1900, 2300]
  )

  mock_data = {
    "600000.SH": mock_tick_600000,
    "000001.SZ": mock_tick_000001,
  }

  # Mock market_data_service
  with patch("quantx_api.gqlapi.dataloaders.quote_loader.market_data_service") as mock_service:
    mock_service.get_latest_prices = AsyncMock(return_value=mock_data)

    # 执行批量加载
    codes = ["600000.SH", "000001.SZ"]
    results = await load_quotes(codes)

    # 验证只调用一次（批量加载的关键）
    mock_service.get_latest_prices.assert_called_once_with(codes)

    # 验证返回结果
    assert len(results) == 2
    assert isinstance(results[0], StockQuote)
    assert isinstance(results[1], StockQuote)

    # 验证第一个股票的数据
    assert results[0].stock_code == "600000.SH"
    assert results[0].last_price == 10.50
    assert results[0].pre_close == 10.30

    # 验证第二个股票的数据
    assert results[1].stock_code == "000001.SZ"
    assert results[1].last_price == 12.80
    assert results[1].pre_close == 12.60


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_quotes_order_consistency():
  """测试返回结果顺序与输入一致"""
  # 创建三个股票的 mock 数据
  mock_tick_1 = Tick(
    stock_code="600000.SH",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=10.50,
    open=10.20,
    high=10.80,
    low=10.00,
    last_close=10.30,
    amount=1000000.0,
    volume=95000.0,
    pvolume=95000.0,
    tickvol=500.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=150,
    ask_price=[],
    bid_price=[],
    ask_vol=[],
    bid_vol=[]
  )

  mock_tick_2 = Tick(
    stock_code="000001.SZ",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=12.80,
    open=12.50,
    high=13.00,
    low=12.30,
    last_close=12.60,
    amount=2000000.0,
    volume=155000.0,
    pvolume=155000.0,
    tickvol=800.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=250,
    ask_price=[],
    bid_price=[],
    ask_vol=[],
    bid_vol=[]
  )

  mock_tick_3 = Tick(
    stock_code="601318.SH",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=55.20,
    open=54.80,
    high=55.50,
    low=54.50,
    last_close=54.90,
    amount=5000000.0,
    volume=90000.0,
    pvolume=90000.0,
    tickvol=1200.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=400,
    ask_price=[],
    bid_price=[],
    ask_vol=[],
    bid_vol=[]
  )

  # Mock 数据按字典顺序返回（模拟实际情况可能不按请求顺序）
  mock_data = {
    "600000.SH": mock_tick_1,
    "000001.SZ": mock_tick_2,
    "601318.SH": mock_tick_3,
  }

  with patch("quantx_api.gqlapi.dataloaders.quote_loader.market_data_service") as mock_service:
    mock_service.get_latest_prices = AsyncMock(return_value=mock_data)

    # 故意以不同顺序请求
    codes = ["000001.SZ", "601318.SH", "600000.SH"]
    results = await load_quotes(codes)

    # 验证返回顺序与请求顺序一致
    assert len(results) == 3
    assert results[0].stock_code == "000001.SZ"
    assert results[1].stock_code == "601318.SH"
    assert results[2].stock_code == "600000.SH"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_quotes_missing_data():
  """测试处理缺失数据（返回 None）"""
  # 只创建部分股票的数据
  mock_tick_600000 = Tick(
    stock_code="600000.SH",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=10.50,
    open=10.20,
    high=10.80,
    low=10.00,
    last_close=10.30,
    amount=1000000.0,
    volume=95000.0,
    pvolume=95000.0,
    tickvol=500.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=150,
    ask_price=[],
    bid_price=[],
    ask_vol=[],
    bid_vol=[]
  )

  # 只返回一个股票的数据
  mock_data = {
    "600000.SH": mock_tick_600000,
    # "000001.SZ" 缺失
    # "601318.SH" 缺失
  }

  with patch("quantx_api.gqlapi.dataloaders.quote_loader.market_data_service") as mock_service:
    mock_service.get_latest_prices = AsyncMock(return_value=mock_data)

    # 请求三个股票，但只有一个有数据
    codes = ["600000.SH", "000001.SZ", "601318.SH"]
    results = await load_quotes(codes)

    # 验证返回结果数量正确
    assert len(results) == 3

    # 验证第一个有数据
    assert results[0] is not None
    assert isinstance(results[0], StockQuote)
    assert results[0].stock_code == "600000.SH"

    # 验证后两个缺失数据时返回 None
    assert results[1] is None
    assert results[2] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_quotes_empty_input():
  """测试空输入列表"""
  with patch("quantx_api.gqlapi.dataloaders.quote_loader.market_data_service") as mock_service:
    mock_service.get_latest_prices = AsyncMock(return_value={})

    # 空列表
    codes = []
    results = await load_quotes(codes)

    # 验证返回空列表
    assert len(results) == 0
    mock_service.get_latest_prices.assert_called_once_with([])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_quotes_single_code():
  """测试单个股票代码"""
  mock_tick = Tick(
    stock_code="600000.SH",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=10.50,
    open=10.20,
    high=10.80,
    low=10.00,
    last_close=10.30,
    amount=1000000.0,
    volume=95000.0,
    pvolume=95000.0,
    tickvol=500.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=150,
    ask_price=[],
    bid_price=[],
    ask_vol=[],
    bid_vol=[]
  )

  mock_data = {"600000.SH": mock_tick}

  with patch("quantx_api.gqlapi.dataloaders.quote_loader.market_data_service") as mock_service:
    mock_service.get_latest_prices = AsyncMock(return_value=mock_data)

    # 单个股票
    codes = ["600000.SH"]
    results = await load_quotes(codes)

    # 验证结果
    assert len(results) == 1
    assert results[0] is not None
    assert results[0].stock_code == "600000.SH"
    mock_service.get_latest_prices.assert_called_once_with(["600000.SH"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_quotes_change_calculation():
  """测试涨跌额和涨跌幅计算正确性"""
  # 上涨的股票
  mock_tick_up = Tick(
    stock_code="600000.SH",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=11.00,
    open=10.20,
    high=11.20,
    low=10.00,
    last_close=10.00,  # 前收盘 10.00, 当前 11.00
    amount=1000000.0,
    volume=95000.0,
    pvolume=95000.0,
    tickvol=500.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=150,
    ask_price=[],
    bid_price=[],
    ask_vol=[],
    bid_vol=[]
  )

  # 下跌的股票
  mock_tick_down = Tick(
    stock_code="000001.SZ",
    period="tick",
    time=datetime(2025, 10, 1, 10, 30, 0),
    last_price=9.00,
    open=10.20,
    high=10.50,
    low=8.90,
    last_close=10.00,  # 前收盘 10.00, 当前 9.00
    amount=800000.0,
    volume=85000.0,
    pvolume=85000.0,
    tickvol=400.0,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=120,
    ask_price=[],
    bid_price=[],
    ask_vol=[],
    bid_vol=[]
  )

  mock_data = {
    "600000.SH": mock_tick_up,
    "000001.SZ": mock_tick_down,
  }

  with patch("quantx_api.gqlapi.dataloaders.quote_loader.market_data_service") as mock_service:
    mock_service.get_latest_prices = AsyncMock(return_value=mock_data)

    codes = ["600000.SH", "000001.SZ"]
    results = await load_quotes(codes)

    # 验证上涨股票的计算
    assert results[0].change == 1.00  # 11.00 - 10.00
    assert results[0].change_percent == 10.0  # (1.00 / 10.00) * 100

    # 验证下跌股票的计算
    assert results[1].change == -1.00  # 9.00 - 10.00
    assert results[1].change_percent == -10.0  # (-1.00 / 10.00) * 100
