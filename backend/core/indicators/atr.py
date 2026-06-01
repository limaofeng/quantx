"""
ATR (Average True Range) 指标实现
"""

from typing import Dict, List, Optional, Union

from core.indicators.base import IndicatorBase
from core.indicators.ma import SMA
from models.kline import KLine


class ATR(IndicatorBase):
  """
  ATR (Average True Range) 平均真实波幅指标
  衡量市场波动率的指标
  """

  def __init__(self, period: int = 14):
    """
    初始化 ATR 指标

    Args:
        period: 计算周期，默认14
    """
    super().__init__(period=period)
    self.previous_close: float = 0.0
    self.tr_history: List[float] = []
    # ATR 通常使用 SMA 或 RMA (Running Moving Average) 做平滑
    # 这里使用标准的 SMA 实现，也支持 Wilder's Smoothing (RMA) 变体配置
    # 暂时使用 SMA 以保持简单和标准
    self.sma = SMA(period=period)

  @property
  def name(self) -> str:
    return f"ATR_{self.period}"

  def calculate(self, data: List[float]) -> Union[float, Dict[str, float], None]:
    """
    计算 ATR 值
    注意：ATR 计算需要 High/Low Price，不仅仅是 Close Price。
    基类的 calculate 接口只传了 list(self.data_window) 即 close price，
    对于 ATR 来说不够用。ATR 的核心逻辑主要在 update 方法中处理。
    """
    # 如果通过 data_window 传入的数据只包含 close，无法计算准确 ATR
    # 因此这里主要依赖 update 中的流式计算
    return self.sma.get_current_value()

  def update(self, bar: KLine) -> Optional[Union[float, Dict[str, float]]]:
    """
    更新 ATR 指标

    Args:
        bar: K线数据

    Returns:
        当前的 ATR 值
    """
    # 1. 计算 True Range (TR)
    # TR = max(high-low, abs(high-pre_close), abs(low-pre_close))
    
    current_high = bar.high
    current_low = bar.low
    
    if len(self.tr_history) == 0:
      # 第一根 K 线，TR = High - Low
      tr = current_high - current_low
    else:
      # 后续 K 线
      hl = current_high - current_low
      hc = abs(current_high - self.previous_close)
      lc = abs(current_low - self.previous_close)
      tr = max(hl, hc, lc)

    # 2. 更新状态
    self.previous_close = bar.close
    self.tr_history.append(tr)
    
    # 保持历史长度
    if len(self.tr_history) > self.period * 2:
      self.tr_history.pop(0)

    # 3. 计算 ATR (对 TR 进行移动平均)
    # 我们利用 SMA 组件来计算 TR 的均值
    # 这里我们构造一个虚拟的 bar 传给 SMA，主要是为了复用 SMA 的 update 逻辑
    # SMA.update 只需要 bar.close，所以我们把 TR 当作 close 传进去
    tr_bar = KLine(
      code=bar.stock_code,
      time=bar.time,
      open=tr, high=tr, low=tr, close=tr, # 将 TR 作为价格传入
      volume=0, amount=0
    )
    
    atr_value = self.sma.update(tr_bar)
    
    # 同步预热状态
    self.is_warmed_up = self.sma.is_warmed_up
    
    if atr_value:
        # 记录指标值
        return super().update(bar) # 调用基类 update 只是为了记录值到 self.values
    
    return None

  def calculate_tr(self, high: float, low: float, prev_close: float) -> float:
      """计算单根 K 线的 True Range"""
      return max(high - low, abs(high - prev_close), abs(low - prev_close))

  #以此覆盖基类的 update，实际上 IndicatorBase 的 update 逻辑比较通用
  # 但 ATR 比较特殊，它的输入是 TR 序列而不是 Price 序列
  # 为了利用基类的 values 存储和 manage 机制，我们稍微 hack 一下
  def update(self, bar: KLine):
      # 1. 计算 TR
      if not self.previous_close and len(self.tr_history) == 0:
          # 第一根 K 线没有前收盘，TR = High - Low
          tr = bar.high - bar.low
          self.previous_close = bar.close
      else:
          tr = max(
              bar.high - bar.low,
              abs(bar.high - self.previous_close),
              abs(bar.low - self.previous_close)
          )
          self.previous_close = bar.close
      
      self.tr_history.append(tr)
      
      # 2. 计算 ATR (TR 的 SMA)
      # 这里我们需要手动维护一个 TR 窗口或者复用 SMA 类
      # 复用 SMA 类最简单
      
      # 创建一个只有 close = tr 的虚拟 bar
      tr_bar = KLine(
          code=bar.stock_code,
          time=bar.time,
          open=tr, high=tr, low=tr, close=tr,
          volume=0, amount=0
      )
      
      sma_val = self.sma.update(tr_bar)
      
      self.is_warmed_up = self.sma.is_warmed_up
      
      if sma_val is not None:
         # 将计算结果 (ATR) 存入基类的 values 列表
         # data_window 存的是原始 close price，这里保持不变
         super().update(bar) 
         # 修正最后一个 value 的值，因为 super().update 计算的是 close 的指标，而我们需要的是 ATR
         self.values[-1].value = sma_val.value
         self.values[-1].metadata["tr"] = tr
         return self.values[-1]
      
      # 即使没有 ATR，也要维护 data_window
      super().update(bar)
      if len(self.values) > 0 and self.values[-1].timestamp == bar.time:
           # 如果 super().update 产生了值（例如因为 data_window 满了），
           # 但 sma 还没 warm up，我们需要移除这个无效值
           self.values.pop()
           
      return None

  def reset(self):
    super().reset()
    self.previous_close = 0.0
    self.tr_history.clear()
    self.sma.reset()
