"""
StockQuote GraphQL 类型单元测试
"""

from datetime import datetime

from quantx_api.gqlapi.types.market_data_types import StockQuote
from quantx_infrastructure.models.tick import Tick


class TestStockQuoteTypes:
  """测试 StockQuote GraphQL 类型"""

  def test_stock_quote_from_tick_basic(self):
    """测试基础的 Tick 到 StockQuote 转换"""
    # 创建测试用的 Tick 数据
    tick = Tick(
      stock_code="600000.SH",
      period="tick",
      time=datetime.now(),
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

    # 转换为 StockQuote
    quote = StockQuote.from_tick(tick)

    # 验证基础字段
    assert quote.stock_code == "600000.SH"
    assert quote.last_price == 10.50
    assert quote.open == 10.20
    assert quote.high == 10.80
    assert quote.low == 10.00
    assert quote.pre_close == 10.30
    assert quote.volume == 95000.0
    assert quote.amount == 1000000.0

  def test_stock_quote_change_calculation(self):
    """测试涨跌额和涨跌幅计算"""
    # 上涨情况
    tick_up = Tick(
      stock_code="600000.SH",
      period="tick",
      time=datetime.now(),
      last_price=10.50,
      open=10.20,
      high=10.80,
      low=10.00,
      last_close=10.00,  # 前收盘价10.00，当前价10.50
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

    quote_up = StockQuote.from_tick(tick_up)

    # 验证上涨计算
    assert quote_up.change == 0.50  # 10.50 - 10.00
    assert quote_up.change_percent == 5.0  # (0.50 / 10.00) * 100

    # 下跌情况
    tick_down = Tick(
      stock_code="600001.SH",
      period="tick",
      time=datetime.now(),
      last_price=9.50,
      open=10.20,
      high=10.80,
      low=9.00,
      last_close=10.00,  # 前收盘价10.00，当前价9.50
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

    quote_down = StockQuote.from_tick(tick_down)

    # 验证下跌计算
    assert quote_down.change == -0.50  # 9.50 - 10.00
    assert quote_down.change_percent == -5.0  # (-0.50 / 10.00) * 100

  def test_stock_quote_no_pre_close(self):
    """测试没有前收盘价的情况"""
    tick = Tick(
      stock_code="600000.SH",
      period="tick",
      time=datetime.now(),
      last_price=10.50,
      open=10.20,
      high=10.80,
      low=10.00,
      last_close=None,  # 没有前收盘价
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

    quote = StockQuote.from_tick(tick)

    # 没有前收盘价时，涨跌额和涨跌幅应该为None
    assert quote.change is None
    assert quote.change_percent is None
    assert quote.pre_close is None

  def test_stock_quote_zero_pre_close(self):
    """测试前收盘价为0的情况"""
    tick = Tick(
      stock_code="600000.SH",
      period="tick",
      time=datetime.now(),
      last_price=10.50,
      open=10.20,
      high=10.80,
      low=10.00,
      last_close=0.0,  # 前收盘价为0
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

    quote = StockQuote.from_tick(tick)

    # 前收盘价为0时，涨跌额和涨跌幅应该为None（避免除零错误）
    assert quote.change is None
    assert quote.change_percent is None
    assert quote.pre_close == 0.0

  def test_stock_quote_flat_price(self):
    """测试平盘的情况"""
    tick = Tick(
      stock_code="600000.SH",
      period="tick",
      time=datetime.now(),
      last_price=10.00,
      open=10.20,
      high=10.80,
      low=10.00,
      last_close=10.00,  # 前收盘价和当前价相同
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

    quote = StockQuote.from_tick(tick)

    # 平盘情况
    assert quote.change == 0.0
    assert quote.change_percent == 0.0

  def test_stock_quote_turnover_rate_default(self):
    """测试换手率字段默认值"""
    tick = Tick(
      stock_code="600000.SH",
      period="tick",
      time=datetime.now(),
      last_price=10.50,
      open=10.20,
      high=10.80,
      low=10.00,
      last_close=10.00,
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

    quote = StockQuote.from_tick(tick)

    # 换手率暂时默认为None（需要额外计算）
    assert quote.turnover_rate is None