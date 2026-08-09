"""
技术指标日快照模型
每日收盘后由 Prefect Flow 批量计算写入，用于量化选股
"""

from sqlalchemy import ARRAY, Column, Date, Float, Integer, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin


class IndicatorSnapshot(Base, TimestampMixin):
  """技术指标日快照表（每标的每交易日一行）"""

  __tablename__ = "indicator_snapshots"

  # ── 主键 ──────────────────────────────────────────
  code = Column(String(10), primary_key=True, comment="标的代码，如 000001.SZ")
  snapshot_date = Column(Date, primary_key=True, comment="快照日期（交易日）")

  # ── 基本信息（冗余，省 JOIN）─────────────────────
  instrument_type = Column(String(10), comment="stock / etf")
  name = Column(String(100), comment="标的名称")

  # ── 当日行情 ──────────────────────────────────────
  current_price = Column(Float, comment="收盘价")
  open_price = Column(Float, comment="开盘价")
  high_price = Column(Float, comment="最高价")
  low_price_day = Column(Float, comment="当日最低价")
  change_pct = Column(Float, comment="涨跌幅 %")
  volume = Column(Float, comment="成交量（手）")
  amount = Column(Float, comment="成交额")

  # ── 量比 ─────────────────────────────────────────
  volume_ratio = Column(Float, comment="量比 = 当日量 / 近20日均量")
  avg_volume_20 = Column(Float, comment="近20日均量")
  avg_volume_5 = Column(Float, comment="近5日均量")
  volume_ratio_5 = Column(Float, comment="5日量比 = 当日量 / 近5日均量")
  avg_amount_20 = Column(Float, comment="近20日均成交额")
  amount_ratio_20 = Column(Float, comment="成交额倍数 = 当日成交额 / 近20日均成交额")
  turnover_rate_pct = Column(Float, comment="换手率 %，按流通股本估算")
  volume_percentile_60 = Column(Float, comment="近60日成交量分位 0~100")
  amount_percentile_60 = Column(Float, comment="近60日成交额分位 0~100")

  # ── 移动均线 ──────────────────────────────────────
  ma5 = Column(Float, comment="MA5")
  ma10 = Column(Float, comment="MA10")
  ma20 = Column(Float, comment="MA20")
  ma5_prev = Column(Float, comment="前一日MA5，用于金叉检测")
  ma10_prev = Column(Float, comment="前一日MA10，用于金叉检测")

  # ── RSI ───────────────────────────────────────────
  rsi6 = Column(Float, comment="RSI 6")
  rsi12 = Column(Float, comment="RSI 12")
  rsi24 = Column(Float, comment="RSI 24")
  rsi12_prev = Column(Float, comment="前一日RSI12，用于穿越检测")

  # ── KDJ (9,3,3) ───────────────────────────────────
  kdj_k = Column(Float, comment="KDJ K值")
  kdj_d = Column(Float, comment="KDJ D值")
  kdj_j = Column(Float, comment="KDJ J值")
  kdj_k_prev = Column(Float, comment="前一日K值，用于金叉检测")
  kdj_d_prev = Column(Float, comment="前一日D值，用于金叉检测")

  # ── Bollinger Bands (20, 2) ───────────────────────
  boll_upper = Column(Float, comment="布林上轨")
  boll_mid = Column(Float, comment="布林中轨（MA20）")
  boll_lower = Column(Float, comment="布林下轨")
  boll_percent_b = Column(Float, comment="%B：当前价在带内位置 0~1")
  boll_bandwidth = Column(Float, comment="带宽 = (upper-lower)/mid")

  # ── 价格统计（近252日）────────────────────────────
  peak_price = Column(Float, comment="近252日最高价")
  price_drop_pct = Column(Float, comment="距高回撤 %（负值）")
  days_since_peak = Column(Integer, comment="距高点交易日数")
  low_price_252 = Column(Float, comment="近252日最低价")
  price_rise_pct = Column(Float, comment="距低点涨幅 %")
  days_since_low = Column(Integer, comment="距低点交易日数")

  # ── 连续下跌 ──────────────────────────────────────
  consecutive_down_days = Column(Integer, comment="连续下跌天数")
  consecutive_down_pct = Column(Float, comment="连续下跌累计幅度 %（负值）")

  # ── 命中信号 ──────────────────────────────────────
  matched_signals = Column(ARRAY(String), comment="本日命中的量化信号列表")
