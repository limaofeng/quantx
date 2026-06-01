"""
ATR 指标单元测试
"""

import pytest
from datetime import datetime
from core.indicators.atr import ATR
from models.kline import KLine

class TestATR:
  """ATR 指标测试"""

  @pytest.fixture
  def atr(self):
    return ATR(period=3)

  @pytest.fixture
  def mock_bars(self):
    """创建模拟K线数据"""
    bars = []
    # 构造一组数据
    # Bar 0: H=10, L=8, C=9. TR=2
    # Bar 1: H=11, L=9, C=10. PreC=9. TR=max(2, 2, 0)=2
    # Bar 2: H=13, L=10, C=12. PreC=10. TR=max(3, 3, 0)=3
    # Bar 3: H=12, L=11, C=11. PreC=12. TR=max(1, 0, 1)=1
    
    data = [
        (10, 8, 9),
        (11, 9, 10),
        (13, 10, 12),
        (12, 11, 11),
        (14, 12, 13)
    ]
    
    for h, l, c in data:
      bar = KLine(
        stock_code="000001",
        period="1d",
        time=datetime.now(),
        open=l, high=h, low=l, close=c,
        volume=1000, amount=10000
      )
      bars.append(bar)

    return bars

  def test_tr_calculation(self, atr, mock_bars):
    """测试 TR 计算逻辑"""
    # 第一根 K 线
    val = atr.update(mock_bars[0])
    assert atr.tr_history[-1] == 2.0
    
    # 第二根 K 线
    val = atr.update(mock_bars[1])
    assert atr.tr_history[-1] == 2.0
    
    # 第三根 K 线
    val = atr.update(mock_bars[2])
    assert atr.tr_history[-1] == 3.0
    
    # 第四根 K 线
    val = atr.update(mock_bars[3])
    assert atr.tr_history[-1] == 1.0

  def test_atr_warmup(self, atr, mock_bars):
    """测试 ATR 预热"""
    # Period = 3
    # Update 1: TR=2. SMA buffer=[2]. Not warmed up.
    atr.update(mock_bars[0])
    assert not atr.is_warmed_up
    assert atr.get_current_value() is None
    
    # Update 2: TR=2. SMA buffer=[2, 2]. Not warmed up.
    atr.update(mock_bars[1])
    assert not atr.is_warmed_up
    
    # Update 3: TR=3. SMA buffer=[2, 2, 3]. Warmed up.
    # SMA = (2+2+3)/3 = 2.333
    val = atr.update(mock_bars[2])
    assert atr.is_warmed_up
    assert val is not None
    assert abs(val.value - 2.333) < 0.001
    
    # Update 4: TR=1. SMA buffer=[2, 3, 1].
    # SMA = (2+3+1)/3 = 2.0
    val = atr.update(mock_bars[3])
    assert abs(val.value - 2.0) < 0.001

  def test_large_gap(self, atr):
    """测试跳空缺口对 TR 的影响"""
    # Day 1: Close = 10
    bar1 = KLine(stock_code="000001", period="1d", time=datetime.now(), open=10, high=11, low=9, close=10, volume=100, amount=1000)
    atr.update(bar1)
    
    # Day 2: Up Gap. Direct Open at 15, High 16, Low 15, Close 16
    # H=16, L=15, PreC=10.
    # H-L = 1
    # |H-PreC| = 6
    # |L-PreC| = 5
    # TR should be 6
    bar2 = KLine(stock_code="000001", period="1d", time=datetime.now(), open=15, high=16, low=15, close=16, volume=100, amount=1000)
    atr.update(bar2)
    
    assert atr.tr_history[-1] == 6.0
