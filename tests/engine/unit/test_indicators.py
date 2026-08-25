"""
技术指标测试
"""

from datetime import datetime

import pytest
from quantx_domain.indicators import EMA, MACD, RSI, SMA, BollingerBands
from quantx_infrastructure.models.kline import KLine


class TestIndicatorBase:
  """指标基类测试"""

  @pytest.fixture
  def mock_bars(self):
    """创建模拟K线数据"""
    bars = []
    prices = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13, 12, 11, 10, 9, 8, 9, 10, 11, 12, 13]

    for i, price in enumerate(prices):
      bar = KLine(
        stock_code="000001.SZ",
        period="1d",
        time=datetime.now(),
        open=price - 0.1,
        high=price + 0.2,
        low=price - 0.2,
        close=price,
        volume=1000,
        amount=price * 1000
      )
      bars.append(bar)

    return bars

  def test_indicator_warmup(self, mock_bars):
    """测试指标预热"""
    sma = SMA(period=5)

    # 前4根K线不应该产生指标值
    for i in range(4):
      result = sma.update(mock_bars[i])
      assert result is None
      assert not sma.is_warmed_up

    # 第5根K线应该产生指标值
    result = sma.update(mock_bars[4])
    assert result is not None
    assert sma.is_warmed_up

  def test_indicator_data_window(self, mock_bars):
    """测试指标数据窗口"""
    sma = SMA(period=5)

    # 更新10根K线
    for bar in mock_bars[:10]:
      sma.update(bar)

    # 数据窗口应该保持最大长度
    assert len(sma.data_window) == 10  # max(5*2, 100) = 100，但只有10根数据

  def test_indicator_values_history(self, mock_bars):
    """测试指标值历史"""
    sma = SMA(period=3)

    for bar in mock_bars[:8]:
      sma.update(bar)

    # 应该有6个指标值（8-3+1=6）
    assert len(sma.values) == 6

    # 测试获取历史值
    recent_values = sma.get_values(3)
    assert len(recent_values) == 3

    all_values = sma.get_values()
    assert len(all_values) == 6

  def test_indicator_reset(self, mock_bars):
    """测试指标重置"""
    sma = SMA(period=3)

    # 更新一些数据
    for bar in mock_bars[:5]:
      sma.update(bar)

    assert sma.is_warmed_up
    assert len(sma.values) > 0

    # 重置
    sma.reset()

    assert not sma.is_warmed_up
    assert len(sma.values) == 0
    assert len(sma.data_window) == 0


class TestSMA:
  """简单移动平均线测试"""

  @pytest.fixture
  def sma(self):
    return SMA(period=3)

  def test_sma_calculation(self, sma):
    """测试SMA计算"""
    # 测试数据：[10, 20, 30]，期望SMA = 20
    data = [10.0, 20.0, 30.0]
    result = sma.calculate(data)
    assert result == 20.0

  def test_sma_insufficient_data(self, sma):
    """测试数据不足时的处理"""
    data = [10.0, 20.0]  # 少于周期长度
    result = sma.calculate(data)
    assert result is None

  def test_sma_name(self, sma):
    """测试指标名称"""
    assert sma.name == "SMA_3"


class TestEMA:
  """指数移动平均线测试"""

  @pytest.fixture
  def ema(self):
    return EMA(period=3)

  def test_ema_alpha_calculation(self, ema):
    """测试EMA平滑系数"""
    # alpha = 2/(n+1) = 2/4 = 0.5
    assert ema.alpha == 0.5

  def test_ema_first_calculation(self, ema):
    """测试EMA首次计算"""
    data = [10.0, 20.0, 30.0]
    result = ema.calculate(data)

    # 首次计算应该使用SMA作为初始值
    expected_sma = sum(data) / len(data)  # 20.0
    assert result == expected_sma
    assert ema.previous_ema == expected_sma

  def test_ema_subsequent_calculation(self, ema):
    """测试EMA后续计算"""
    # 先计算一次以设置初始值
    data1 = [10.0, 20.0, 30.0]
    ema.calculate(data1)

    # 再次计算
    data2 = [10.0, 20.0, 30.0, 40.0]
    result = ema.calculate(data2)

    # EMA = alpha * current_price + (1 - alpha) * previous_ema
    # EMA = 0.5 * 40 + 0.5 * 20 = 30
    expected = 0.5 * 40 + 0.5 * 20
    assert result == expected

  def test_ema_reset(self, ema):
    """测试EMA重置"""
    data = [10.0, 20.0, 30.0]
    ema.calculate(data)

    assert ema.previous_ema is not None

    ema.reset()
    assert ema.previous_ema is None


class TestRSI:
  """RSI指标测试"""

  @pytest.fixture
  def rsi(self):
    return RSI(period=4)

  def test_rsi_calculation(self, rsi):
    """测试RSI计算"""
    # 测试数据：价格上涨序列
    data = [10.0, 12.0, 14.0, 16.0, 18.0]
    result = rsi.calculate(data)

    # 纯上涨序列RSI应该接近100
    assert result is not None
    assert result > 90

  def test_rsi_calculation_downtrend(self, rsi):
    """测试下跌序列的RSI"""
    data = [18.0, 16.0, 14.0, 12.0, 10.0]
    result = rsi.calculate(data)

    # 纯下跌序列RSI应该接近0
    assert result is not None
    assert result < 10

  def test_rsi_no_change(self, rsi):
    """测试价格无变化的RSI"""
    data = [10.0, 10.0, 10.0, 10.0, 10.0]
    result = rsi.calculate(data)

    # 无变化时RSI应该是50
    assert result == 50.0

  def test_rsi_insufficient_data(self, rsi):
    """测试数据不足"""
    data = [10.0, 12.0]  # 少于period+1
    result = rsi.calculate(data)
    assert result is None


class TestMACD:
  """MACD指标测试"""

  @pytest.fixture
  def macd(self):
    return MACD(fast_period=3, slow_period=6, signal_period=3)

  def test_macd_insufficient_data(self, macd):
    """测试数据不足"""
    data = [10.0, 11.0, 12.0]  # 少于慢线周期
    result = macd.calculate(data)
    assert result is None

  def test_macd_structure(self, macd):
    """测试MACD返回结构"""
    data = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
    result = macd.calculate(data)

    assert result is not None
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result

  def test_macd_signal_line_delay(self, macd):
    """测试信号线延迟"""
    data = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0]
    result = macd.calculate(data)

    # 信号线需要更多数据才能生成
    if result["signal"] is None:
      assert result["histogram"] is None


class TestBollingerBands:
  """布林带测试"""

  @pytest.fixture
  def bb(self):
    return BollingerBands(period=3, multiplier=2.0)

  def test_bollinger_bands_structure(self, bb):
    """测试布林带返回结构"""
    data = [10.0, 12.0, 14.0]
    result = bb.calculate(data)

    assert result is not None
    assert "upper" in result
    assert "middle" in result
    assert "lower" in result
    assert "percent_b" in result
    assert "bandwidth" in result

  def test_bollinger_bands_symmetry(self, bb):
    """测试布林带对称性"""
    # 使用相同价格，标准差为0
    data = [10.0, 10.0, 10.0]
    result = bb.calculate(data)

    assert result["upper"] == result["middle"] == result["lower"]
    assert result["percent_b"] == 0.5  # 在中线上
    assert result["bandwidth"] == 0  # 带宽为0

  def test_bollinger_bands_calculation(self, bb):
    """测试布林带基本计算"""
    data = [8.0, 10.0, 12.0]
    result = bb.calculate(data)

    middle = sum(data) / len(data)  # 10.0
    assert result["middle"] == middle

    # 上轨应该大于中轨，下轨应该小于中轨
    assert result["upper"] > result["middle"]
    assert result["lower"] < result["middle"]

    # 当前价格在中线上，percent_b应该是0.5
    current_price = data[-1]  # 12.0
    expected_percent_b = (current_price - result["lower"]) / (result["upper"] - result["lower"])
    assert abs(result["percent_b"] - expected_percent_b) < 0.001


@pytest.mark.integration
class TestIndicatorIntegration:
  """指标集成测试"""

  def test_multiple_indicators_same_data(self):
    """测试多个指标使用相同数据"""
    sma = SMA(5)
    ema = EMA(5)
    rsi = RSI(5)

    # 创建测试K线数据
    bars = []
    prices = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13]
    for price in prices:
      bar = KLine(
        stock_code="000001.SZ",
        period="1d",
        time=datetime.now(),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1000,
        amount=price * 1000
      )
      bars.append(bar)

    # 更新所有指标
    for bar in bars:
      sma.update(bar)
      ema.update(bar)
      rsi.update(bar)

    # 所有指标都应该产生值
    assert sma.get_current_value() is not None
    assert ema.get_current_value() is not None
    assert rsi.get_current_value() is not None

    # 验证指标都已预热
    assert sma.is_warmed_up
    assert ema.is_warmed_up
    assert rsi.is_warmed_up
