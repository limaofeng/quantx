"""Market-calendar-aligned forward outcomes.

The functions in this module deliberately do not use ``groupby().shift()`` on
the sparse per-stock frame.  A missing bar must remain missing; it must not
turn the following stock bar into a fake T+N market session.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


def add_forward_outcomes(
  panel: pd.DataFrame,
  horizons: Iterable[int],
  *,
  benchmark: pd.DataFrame | None = None,
  calendar: pd.DatetimeIndex | None = None,
  include_close_response: bool = True,
  include_next_open_return: bool = True,
  include_benchmark_excess: bool = True,
  include_cross_section_excess: bool = True,
) -> pd.DataFrame:
  """Add forward returns and path excursions to a normalized market panel.

  ``panel`` must have one row per ``stock_code`` and ``event_date`` and contain
  ``open/high/low/close``. ``event_date`` is a normalized pandas timestamp.
  Returns are decimals (``0.01`` means one percent).
  """
  required = {"stock_code", "event_date", "open", "high", "low", "close"}
  missing = sorted(required.difference(panel.columns))
  if missing:
    raise ValueError(f"market panel is missing columns: {', '.join(missing)}")

  normalized_horizons = tuple(sorted(set(int(item) for item in horizons)))
  if not normalized_horizons or normalized_horizons[0] < 1:
    raise ValueError("horizons must contain positive trading-day counts")

  result = panel.copy()
  calendar = (
    market_calendar(result, benchmark)
    if calendar is None
    else pd.DatetimeIndex(pd.to_datetime(calendar).dropna().unique()).sort_values()
  )
  date_to_ordinal = pd.Series(
    np.arange(len(calendar), dtype=np.int64),
    index=calendar,
  )
  result["_session_ordinal"] = result["event_date"].map(date_to_ordinal).astype("Int64")

  computed: list[pd.DataFrame] = []
  for _, stock_frame in result.groupby("stock_code", sort=False, observed=True):
    computed.append(
      _stock_forward_outcomes(
        stock_frame,
        calendar_size=len(calendar),
        horizons=normalized_horizons,
        include_close_response=include_close_response,
        include_next_open_return=include_next_open_return,
      )
    )
  result = pd.concat(computed, axis=0).sort_index() if computed else result

  if include_cross_section_excess:
    _add_cross_section_excess(
      result,
      normalized_horizons,
      include_close_response=include_close_response,
      include_next_open_return=include_next_open_return,
    )

  if include_benchmark_excess and benchmark is not None and not benchmark.empty:
    _add_benchmark_excess(
      result,
      benchmark,
      calendar,
      normalized_horizons,
      include_close_response=include_close_response,
      include_next_open_return=include_next_open_return,
    )

  return result


def required_outcome_columns(
  horizons: Iterable[int],
  *,
  include_close_response: bool,
  include_next_open_return: bool,
) -> tuple[str, ...]:
  """Return primary absolute-outcome columns required for complete events."""
  columns: list[str] = []
  for horizon in sorted(set(int(item) for item in horizons)):
    if include_close_response:
      columns.append(f"close_return_h{horizon}")
    if include_next_open_return:
      columns.append(f"next_open_return_h{horizon}")
  return tuple(columns)


def market_calendar(
  panel: pd.DataFrame, benchmark: pd.DataFrame | None
) -> pd.DatetimeIndex:
  dates = pd.DatetimeIndex(pd.to_datetime(panel["event_date"]).dropna().unique())
  if benchmark is not None and not benchmark.empty:
    benchmark_date_column = (
      "event_date"
      if "event_date" in benchmark.columns
      else "time"
      if "time" in benchmark.columns
      else "date"
      if "date" in benchmark.columns
      else None
    )
    if benchmark_date_column:
      benchmark_dates = _normalize_dates(benchmark[benchmark_date_column])
      dates = dates.union(pd.DatetimeIndex(benchmark_dates.dropna().unique()))
  return dates.sort_values()


def _stock_forward_outcomes(
  stock_frame: pd.DataFrame,
  *,
  calendar_size: int,
  horizons: tuple[int, ...],
  include_close_response: bool,
  include_next_open_return: bool,
) -> pd.DataFrame:
  frame = stock_frame.copy()
  ordinals = frame["_session_ordinal"].astype(int).to_numpy()
  dense = (
    frame.set_index("_session_ordinal")[["open", "high", "low", "close"]]
    .reindex(range(calendar_size))
    .astype(float)
  )
  base_close = dense["close"].where(dense["close"] > 0)
  entry_open = dense["open"].shift(-1).where(lambda values: values > 0)

  for horizon in horizons:
    target_close = dense["close"].shift(-horizon).where(lambda values: values > 0)
    future_highs = pd.concat(
      [dense["high"].shift(-step) for step in range(1, horizon + 1)],
      axis=1,
    )
    future_lows = pd.concat(
      [dense["low"].shift(-step) for step in range(1, horizon + 1)],
      axis=1,
    )
    complete_path = (future_highs.count(axis=1) == horizon) & (
      future_lows.count(axis=1) == horizon
    )
    max_high = future_highs.max(axis=1).where(complete_path)
    min_low = future_lows.min(axis=1).where(complete_path)

    if include_close_response:
      frame[f"close_return_h{horizon}"] = (
        target_close.div(base_close).sub(1.0).iloc[ordinals].to_numpy()
      )
      frame[f"mfe_close_h{horizon}"] = (
        max_high.div(base_close).sub(1.0).iloc[ordinals].to_numpy()
      )
      frame[f"mae_close_h{horizon}"] = (
        min_low.div(base_close).sub(1.0).iloc[ordinals].to_numpy()
      )
      # Short aliases are retained for event-level parquet convenience.
      frame[f"mfe_h{horizon}"] = frame[f"mfe_close_h{horizon}"]
      frame[f"mae_h{horizon}"] = frame[f"mae_close_h{horizon}"]

    if include_next_open_return:
      frame[f"next_open_return_h{horizon}"] = (
        target_close.div(entry_open).sub(1.0).iloc[ordinals].to_numpy()
      )
      frame[f"mfe_next_open_h{horizon}"] = (
        max_high.div(entry_open).sub(1.0).iloc[ordinals].to_numpy()
      )
      frame[f"mae_next_open_h{horizon}"] = (
        min_low.div(entry_open).sub(1.0).iloc[ordinals].to_numpy()
      )
  return frame


def _add_cross_section_excess(
  panel: pd.DataFrame,
  horizons: tuple[int, ...],
  *,
  include_close_response: bool,
  include_next_open_return: bool,
) -> None:
  for horizon in horizons:
    for return_kind, enabled in (
      ("close", include_close_response),
      ("next_open", include_next_open_return),
    ):
      if not enabled:
        continue
      source = f"{return_kind}_return_h{horizon}"
      market_mean = panel.groupby("event_date", observed=True)[source].transform("mean")
      panel[f"market_excess_{return_kind}_h{horizon}"] = panel[source] - market_mean


def _add_benchmark_excess(
  panel: pd.DataFrame,
  benchmark: pd.DataFrame,
  calendar: pd.DatetimeIndex,
  horizons: tuple[int, ...],
  *,
  include_close_response: bool,
  include_next_open_return: bool,
) -> None:
  benchmark_frame = _normalized_benchmark(benchmark)
  calendar_lookup = pd.Series(np.arange(len(calendar)), index=calendar)
  benchmark_frame["_session_ordinal"] = benchmark_frame["event_date"].map(
    calendar_lookup
  )
  benchmark_frame = benchmark_frame.dropna(subset=["_session_ordinal"])
  benchmark_frame["_session_ordinal"] = benchmark_frame["_session_ordinal"].astype(int)
  benchmark_frame = benchmark_frame.drop_duplicates("_session_ordinal", keep="last")
  dense = (
    benchmark_frame.set_index("_session_ordinal")[["open", "close"]]
    .reindex(range(len(calendar)))
    .astype(float)
  )
  base_close = dense["close"].where(dense["close"] > 0)
  entry_open = dense["open"].shift(-1).where(lambda values: values > 0)
  event_ordinals = panel["_session_ordinal"].astype(int).to_numpy()

  for horizon in horizons:
    target_close = dense["close"].shift(-horizon).where(lambda values: values > 0)
    if include_close_response:
      benchmark_return = target_close.div(base_close).sub(1.0)
      aligned = benchmark_return.iloc[event_ordinals].to_numpy()
      column = f"csi300_excess_close_h{horizon}"
      panel[column] = panel[f"close_return_h{horizon}"] - aligned
      panel[f"benchmark_excess_close_h{horizon}"] = panel[column]
    if include_next_open_return:
      benchmark_return = target_close.div(entry_open).sub(1.0)
      aligned = benchmark_return.iloc[event_ordinals].to_numpy()
      column = f"csi300_excess_next_open_h{horizon}"
      panel[column] = panel[f"next_open_return_h{horizon}"] - aligned
      panel[f"benchmark_excess_next_open_h{horizon}"] = panel[column]


def _normalized_benchmark(benchmark: pd.DataFrame) -> pd.DataFrame:
  frame = benchmark.copy()
  if "event_date" not in frame:
    if "time" in frame:
      frame["event_date"] = _normalize_dates(frame["time"])
    elif "date" in frame:
      frame["event_date"] = _normalize_dates(frame["date"])
    else:
      raise ValueError("benchmark is missing event_date/time/date")
  else:
    frame["event_date"] = _normalize_dates(frame["event_date"])
  missing = sorted({"event_date", "open", "close"}.difference(frame.columns))
  if missing:
    raise ValueError(f"benchmark is missing columns: {', '.join(missing)}")
  for column in ("open", "close"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
  return frame.sort_values("event_date")


def _normalize_dates(values: pd.Series) -> pd.Series:
  parsed = pd.to_datetime(values, errors="coerce", utc=True)
  return parsed.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize()
