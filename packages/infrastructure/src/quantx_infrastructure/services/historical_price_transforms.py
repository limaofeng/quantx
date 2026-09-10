"""Shared OHLC aggregation and price adjustment, independent of storage repositories."""

import re
from datetime import datetime
from typing import List, Optional, Union

import pandas as pd

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick


class HistoricalPriceTransforms:
  def _normalize_time(self, value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
      return None
    if isinstance(value, pd.Timestamp):
      value = value.to_pydatetime()
    if not isinstance(value, datetime):
      return None
    if value.tzinfo is None:
      return value

    return time_utils.to_shanghai(value)

  def _resample_klines(
    self,
    klines: List[KLine],
    stock_code: str,
    period: str,
    freq: str,
  ) -> List[KLine]:
    if not klines:
      return []

    records = []
    for kline in klines:
      records.append(
        {
          "time": kline.time,
          "open": kline.open,
          "high": kline.high,
          "low": kline.low,
          "close": kline.close,
          "volume": kline.volume,
          "amount": getattr(kline, "amount", 0.0),
          "pre_close": getattr(kline, "pre_close", 0.0),
        }
      )

    frame = pd.DataFrame(records)
    if frame.empty:
      return []

    frame["time"] = pd.to_datetime(frame["time"])
    frame = frame.sort_values("time").set_index("time")

    agg_dict = {
      "open": "first",
      "high": "max",
      "low": "min",
      "close": "last",
      "volume": "sum",
      "amount": "sum",
    }

    resampled = frame.resample(freq).agg(agg_dict).dropna()
    if resampled.empty:
      return []

    resampled["pre_close"] = resampled["close"].shift(1)
    first_pre_close = frame["pre_close"].iloc[0] if not frame.empty else 0.0
    resampled.iloc[0, resampled.columns.get_loc("pre_close")] = first_pre_close

    resampled = resampled.reset_index()
    results: List[KLine] = []
    for _, row in resampled.iterrows():
      results.append(
        KLine(
          stock_code=stock_code,
          period=period,
          time=row["time"],
          open=float(row["open"]),
          high=float(row["high"]),
          low=float(row["low"]),
          close=float(row["close"]),
          pre_close=float(row["pre_close"]) if pd.notna(row["pre_close"]) else 0.0,
          volume=float(row["volume"]),
          amount=float(row["amount"]) if pd.notna(row["amount"]) else 0.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        )
      )

    return results

  async def _apply_dividend_adjustment_async(
    self, klines: List[KLine], stock_code: str, dividend_type: str
  ) -> List[KLine]:
    if not klines:
      return []

    if dividend_type not in ["front", "back", "front_ratio", "back_ratio"]:
      return klines

    dividend_type = "front" if dividend_type == "front_ratio" else dividend_type
    dividend_type = "back" if dividend_type == "back_ratio" else dividend_type

    times = [
      self._normalize_time(kline.time) for kline in klines if kline.time is not None
    ]
    if not times:
      return klines

    start_time = min(times)
    end_time = max(times)

    factors = await self.divid_factor_service_async.get_divid_factors(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=None,
    )

    if not factors:
      self.logger.debug(
        "未找到复权因子，返回原始K线: %s, %s~%s",
        stock_code,
        start_time,
        end_time,
      )
      return klines

    factor_rows = [
      {"time": factor.time, "dr": factor.dr}
      for factor in factors
      if factor.time is not None and factor.dr
    ]
    if not factor_rows:
      self.logger.debug(
        "复权因子无有效数据，返回原始K线: %s, %s~%s",
        stock_code,
        start_time,
        end_time,
      )
      return klines

    factor_df = pd.DataFrame(factor_rows).sort_values("time")
    factor_df = factor_df[pd.to_datetime(factor_df["time"]) <= pd.Timestamp(end_time)]
    factor_df["dr"] = pd.to_numeric(factor_df["dr"], errors="coerce").fillna(1.0)
    factor_df = factor_df[factor_df["dr"] > 0]
    if factor_df.empty:
      return klines

    factor_df["cum_factor"] = factor_df["dr"].cumprod()
    total_factor = float(factor_df["cum_factor"].iloc[-1])

    kline_df = pd.DataFrame({"time": [self._normalize_time(k.time) for k in klines]})
    kline_df = kline_df.sort_values("time")

    aligned = pd.merge_asof(
      kline_df,
      factor_df[["time", "cum_factor"]],
      on="time",
      direction="backward",
    )

    aligned["cum_factor"] = aligned["cum_factor"].fillna(1.0)
    if dividend_type == "front":
      # QMT dr = pre-action raw close / ex-right reference price.
      # Front adjustment restates history on the latest price basis: only
      # corporate actions strictly after a bar may scale that bar.
      aligned["adjust_factor"] = aligned["cum_factor"] / total_factor
    else:
      # Back adjustment keeps the earliest price basis and applies only
      # factors already effective at the bar timestamp.
      aligned["adjust_factor"] = aligned["cum_factor"]

    adjust_factors = dict(zip(aligned["time"], aligned["adjust_factor"]))

    adjusted = []
    for kline in klines:
      factor = adjust_factors.get(self._normalize_time(kline.time), 1.0)
      adjusted.append(
        KLine(
          stock_code=kline.stock_code,
          period=kline.period,
          time=kline.time,
          open=kline.open * factor,
          high=kline.high * factor,
          low=kline.low * factor,
          close=kline.close * factor,
          pre_close=kline.pre_close * factor,
          volume=kline.volume,
          amount=kline.amount,
          settelement_price=kline.settelement_price,
          open_interest=kline.open_interest,
          suspend_flag=kline.suspend_flag,
        )
      )

    return adjusted

  async def _build_adjust_factors_async(
    self, times: List[datetime], stock_code: str, dividend_type: str
  ) -> Optional[pd.Series]:
    if not times:
      return None

    normalized_times = [self._normalize_time(t) for t in times]
    time_df = pd.DataFrame({"time": list(normalized_times)}).reset_index()
    valid_mask = time_df["time"].notna()
    if not valid_mask.any():
      return None

    valid_times = time_df.loc[valid_mask, "time"].tolist()
    start_time = min(valid_times)
    end_time = max(valid_times)

    factors = await self.divid_factor_service_async.get_divid_factors(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=None,
    )

    if not factors:
      self.logger.debug("未找到复权因子，跳过Tick复权: %s", stock_code)
      return None

    factor_rows = [
      {"time": factor.time, "dr": factor.dr}
      for factor in factors
      if factor.time is not None and factor.dr
    ]
    if not factor_rows:
      self.logger.debug("复权因子无有效数据，跳过Tick复权: %s", stock_code)
      return None

    factor_df = pd.DataFrame(factor_rows).sort_values("time")
    factor_df = factor_df[pd.to_datetime(factor_df["time"]) <= pd.Timestamp(end_time)]
    factor_df["dr"] = pd.to_numeric(factor_df["dr"], errors="coerce").fillna(1.0)
    factor_df = factor_df[factor_df["dr"] > 0]
    if factor_df.empty:
      return None

    factor_df["cum_factor"] = factor_df["dr"].cumprod()
    total_factor = float(factor_df["cum_factor"].iloc[-1])

    valid_df = time_df.loc[valid_mask].sort_values("time")

    aligned = pd.merge_asof(
      valid_df,
      factor_df[["time", "cum_factor"]],
      on="time",
      direction="backward",
    )

    aligned["cum_factor"] = aligned["cum_factor"].fillna(1.0)
    if dividend_type == "front":
      aligned["adjust_factor"] = aligned["cum_factor"] / total_factor
    else:
      aligned["adjust_factor"] = aligned["cum_factor"]

    result = pd.Series([None] * len(time_df), index=time_df["index"])
    result.loc[aligned["index"]] = aligned["adjust_factor"].values
    return result.sort_index()

  async def _apply_tick_dividend_adjustment_async(
    self,
    ticks: Union[List[Tick], pd.DataFrame],
    stock_code: str,
    dividend_type: str,
  ) -> Union[List[Tick], pd.DataFrame]:
    if not ticks:
      return ticks

    if dividend_type not in ["front", "back", "front_ratio", "back_ratio"]:
      return ticks

    dividend_type = "front" if dividend_type == "front_ratio" else dividend_type
    dividend_type = "back" if dividend_type == "back_ratio" else dividend_type

    price_fields = [
      "last_price",
      "open",
      "high",
      "low",
      "last_close",
      "last_settlement_price",
      "settlement_price",
    ]

    if isinstance(ticks, pd.DataFrame):
      if ticks.empty or "time" not in ticks.columns:
        return ticks

      factors = await self._build_adjust_factors_async(
        list(ticks["time"]), stock_code, dividend_type
      )
      if factors is None:
        self.logger.debug("未找到复权因子，返回原始Tick: %s", stock_code)
        return ticks

      adjusted = ticks.copy()
      adjusted["_adjust_factor"] = pd.Series(factors).fillna(1.0).values

      for col in price_fields:
        if col in adjusted.columns:
          adjusted[col] = adjusted[col] * adjusted["_adjust_factor"]

      for col in adjusted.columns:
        if re.match(r"^(ask|bid)\\d+$", col):
          adjusted[col] = adjusted[col] * adjusted["_adjust_factor"]

      if "ask_price" in adjusted.columns:
        adjusted["ask_price"] = adjusted.apply(
          lambda r: [p * r["_adjust_factor"] for p in (r["ask_price"] or [])], axis=1
        )
      if "bid_price" in adjusted.columns:
        adjusted["bid_price"] = adjusted.apply(
          lambda r: [p * r["_adjust_factor"] for p in (r["bid_price"] or [])], axis=1
        )

      adjusted.drop(columns=["_adjust_factor"], inplace=True)
      return adjusted

    times = [t.time for t in ticks]
    factors = await self._build_adjust_factors_async(times, stock_code, dividend_type)
    if factors is None:
      self.logger.debug("未找到复权因子，返回原始Tick: %s", stock_code)
      return ticks
    factors = pd.Series(factors).fillna(1.0).values

    for tick, factor in zip(ticks, factors):
      for field in price_fields:
        value = getattr(tick, field, None)
        if value is not None:
          setattr(tick, field, value * factor)

      for list_field in ["ask_price", "bid_price"]:
        values = getattr(tick, list_field, None)
        if values:
          setattr(tick, list_field, [v * factor for v in values])

    return ticks
