"""Causal D-1 instrument profiles for the stateful T-trade opportunity engine.

Profiles are deliberately account independent and immutable.  The builder only
uses source observations at or before ``as_of`` and selects the latest complete
trading days from that prefix.  It does not inspect opportunity outcomes or
orders, which keeps the reference inputs usable by live execution and replay.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Optional, Sequence

from quantx_contracts import HISTORICAL_TICK_ORDINALS_PER_MILLISECOND

from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeInstrumentProfileRepository,
)

T_TRADE_PROFILE_SCHEMA_VERSION = "1"
T_TRADE_PROFILE_VERSION = "t_trade_instrument_profile_v1"
T_TRADE_PROFILE_TARGET_COMPLETE_DAYS = 20
T_TRADE_PROFILE_MIN_COMPLETE_DAYS = 10

_MINUTES_PER_SESSION = 120
_MIN_COMPLETE_MINUTES_PER_SESSION = 108
T_TRADE_PROFILE_PAGE_SIZE = 10_000
T_TRADE_PROFILE_MAX_PAGES = 1_024
T_TRADE_PROFILE_MAX_SOURCE_TICKS = 2_000_000
T_TRADE_PROFILE_MAX_MINUTE_ENTRIES = 60 * 240


@dataclass(frozen=True)
class TTradeInstrumentProfileBuild:
  instrument_code: str
  as_of: datetime
  profile: dict[str, Any]
  schema_version: str
  version: str
  fingerprint: str
  metrics: dict[str, Any]
  data_manifest: dict[str, Any]

  def repository_arguments(self) -> dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "as_of": self.as_of,
      "profile": self.profile,
      "schema_version": self.schema_version,
      "version": self.version,
      "fingerprint": self.fingerprint,
      "metrics": self.metrics,
      "data_manifest": self.data_manifest,
    }


@dataclass(frozen=True)
class _MinuteObservation:
  at: datetime
  price: float
  cumulative_amount: float
  spread_ticks: Optional[float]


def _validate_tick_storage_time(
  tick: Any,
  source_key: tuple[int, int],
) -> None:
  raw_time = getattr(tick, "time", None)
  if hasattr(raw_time, "to_pydatetime"):
    raw_time = raw_time.to_pydatetime()
  if not isinstance(raw_time, datetime):
    raise ValueError(
      "做 T 标的画像历史 Tick 存储时间缺失或不是 datetime，未保存画像"
    )
  try:
    actual_time = time_utils.to_utc(raw_time)
    expected_time = time_utils.to_utc(tick_storage_time(*source_key))
  except (TypeError, ValueError, OverflowError, OSError) as exc:
    raise ValueError(
      "做 T 标的画像历史 Tick 存储时间无法规范化，未保存画像"
    ) from exc
  if actual_time != expected_time:
    raise ValueError(
      "做 T 标的画像历史 Tick 存储时间与源身份不一致，未保存画像"
    )


class _CausalMinuteAccumulator:
  """Bounded reducer from raw Tick pages to one observation per minute."""

  def __init__(
    self,
    *,
    instrument_code: str,
    cutoff: datetime,
    max_minute_entries: int,
    require_source_identity: bool,
    earliest_date: Optional[date] = None,
  ) -> None:
    self.instrument_code = instrument_code
    self.cutoff = cutoff
    self.max_minute_entries = max_minute_entries
    self.require_source_identity = require_source_identity
    self.earliest_date = earliest_date
    self._latest_by_minute: dict[
      tuple[date, int, int], _MinuteObservation
    ] = {}
    self._latest_keys: dict[tuple[date, int, int], tuple[Any, ...]] = {}
    self.accepted_tick_count = 0

  @property
  def minute_rows(self) -> dict[date, list[_MinuteObservation]]:
    by_day: dict[date, list[_MinuteObservation]] = defaultdict(list)
    for row in self._latest_by_minute.values():
      by_day[row.at.date()].append(row)
    for rows in by_day.values():
      rows.sort(key=lambda item: item.at)
    return dict(by_day)

  def add_ticks(self, ticks: Iterable[Any]) -> None:
    for tick in ticks:
      self._accept_tick(tick)

  def add_page(
    self,
    page: Sequence[Any],
    *,
    previous_key: Optional[tuple[int, int]],
  ) -> Optional[tuple[int, int]]:
    page_key = previous_key
    for tick in page:
      key = _strict_tick_source_identity(tick)
      if page_key is not None and key <= page_key:
        raise ValueError(
          "做 T 标的画像历史 Tick 页重复、乱序或未推进，未保存画像"
        )
      page_key = key
      self._accept_tick(tick)
    return page_key

  def _accept_tick(self, tick: Any) -> None:
    if self.require_source_identity:
      source_key = _strict_tick_source_identity(tick)
      _validate_tick_storage_time(tick, source_key)
    else:
      source_key = None
    if _instrument_code(getattr(tick, "stock_code", self.instrument_code)) != (
      self.instrument_code
    ):
      return
    at = time_utils.to_shanghai(getattr(tick, "time"))
    if source_key is not None:
      at = _source_time_at(source_key[0])
    if (
      at > self.cutoff
      or (self.earliest_date is not None and at.date() < self.earliest_date)
      or not _continuous_session(at.time())
    ):
      return
    price = _finite_positive(getattr(tick, "last_price", None))
    amount = _finite_non_negative(getattr(tick, "amount", None))
    if price is None or amount is None:
      return

    self.accepted_tick_count += 1
    minute_key = (at.date(), at.hour, at.minute)
    if source_key is None:
      ordinal = int(getattr(tick, "tick_ordinal", 0) or 0)
      order_key: tuple[Any, ...] = (at, ordinal)
    else:
      order_key = source_key
    current_key = self._latest_keys.get(minute_key)
    if current_key is not None and order_key <= current_key:
      return
    if current_key is None and len(self._latest_by_minute) >= self.max_minute_entries:
      raise ValueError(
        "做 T 标的画像分钟聚合超过安全内存上限，未保存画像"
      )
    self._latest_keys[minute_key] = order_key
    self._latest_by_minute[minute_key] = _MinuteObservation(
      at=at,
      price=price,
      cumulative_amount=amount,
      spread_ticks=_spread_ticks(tick),
    )


class TTradeInstrumentProfileService:
  """Build and persist a deterministic prior-only reference profile."""

  def build_profile(
    self,
    *,
    instrument_code: str,
    ticks: Sequence[Any],
    as_of: datetime,
    target_complete_days: int = T_TRADE_PROFILE_TARGET_COMPLETE_DAYS,
    min_complete_days: int = T_TRADE_PROFILE_MIN_COMPLETE_DAYS,
    max_minute_entries: int = T_TRADE_PROFILE_MAX_MINUTE_ENTRIES,
  ) -> TTradeInstrumentProfileBuild:
    code = _instrument_code(instrument_code)
    cutoff = time_utils.to_shanghai(as_of)
    _validate_minute_entry_limit(max_minute_entries)
    accumulator = _CausalMinuteAccumulator(
      instrument_code=code,
      cutoff=cutoff,
      max_minute_entries=max_minute_entries,
      require_source_identity=False,
    )
    ordered = sorted(ticks, key=_tick_order_key)
    accumulator.add_ticks(ordered)
    return self._build_from_minutes(
      instrument_code=code,
      cutoff=cutoff,
      minute_rows=accumulator.minute_rows,
      accepted_tick_count=accumulator.accepted_tick_count,
      target_complete_days=target_complete_days,
      min_complete_days=min_complete_days,
    )

  def build_profile_from_pages(
    self,
    *,
    instrument_code: str,
    pages: Iterable[Sequence[Any]],
    as_of: datetime,
    lookback_calendar_days: int = 60,
    target_complete_days: int = T_TRADE_PROFILE_TARGET_COMPLETE_DAYS,
    min_complete_days: int = T_TRADE_PROFILE_MIN_COMPLETE_DAYS,
    page_size: int = T_TRADE_PROFILE_PAGE_SIZE,
    max_pages: int = T_TRADE_PROFILE_MAX_PAGES,
    max_source_ticks: int = T_TRADE_PROFILE_MAX_SOURCE_TICKS,
  ) -> TTradeInstrumentProfileBuild:
    """Build a profile from bounded pages without retaining raw Tick rows.

    The page producer is responsible for querying until an exhausted page is
    observed.  This method still validates every page's size and strict source
    identity so a repeated, unordered, or silently truncated source fails
    closed before any profile can be saved.
    """

    code = _instrument_code(instrument_code)
    cutoff = time_utils.to_shanghai(as_of)
    max_minutes = _minute_entry_limit(lookback_calendar_days)
    _validate_page_limits(page_size, max_pages, max_source_ticks)
    accumulator = _CausalMinuteAccumulator(
      instrument_code=code,
      cutoff=cutoff,
      max_minute_entries=max_minutes,
      require_source_identity=True,
      earliest_date=cutoff.date() - timedelta(days=lookback_calendar_days - 1),
    )
    page_count = 0
    source_tick_count = 0
    previous_key: Optional[tuple[int, int]] = None
    for page in pages:
      page_count += 1
      if page_count > max_pages:
        raise ValueError(
          "做 T 标的画像历史 Tick 页数超过安全上限，未保存画像"
        )
      if len(page) > page_size:
        raise ValueError(
          "做 T 标的画像历史 Tick 单页超过安全上限，未保存画像"
        )
      source_tick_count += len(page)
      if source_tick_count > max_source_ticks:
        raise ValueError(
          "做 T 标的画像历史 Tick 总量超过安全上限，未保存画像"
        )
      previous_key = accumulator.add_page(page, previous_key=previous_key)
    return self._build_from_minutes(
      instrument_code=code,
      cutoff=cutoff,
      minute_rows=accumulator.minute_rows,
      accepted_tick_count=accumulator.accepted_tick_count,
      target_complete_days=target_complete_days,
      min_complete_days=min_complete_days,
    )

  async def build_and_save_profile_from_pages(
    self,
    *,
    instrument_code: str,
    pages: AsyncIterable[Sequence[Any]],
    as_of: datetime,
    repository: TTradeInstrumentProfileRepository,
    lookback_calendar_days: int = 60,
    target_complete_days: int = T_TRADE_PROFILE_TARGET_COMPLETE_DAYS,
    min_complete_days: int = T_TRADE_PROFILE_MIN_COMPLETE_DAYS,
    page_size: int = T_TRADE_PROFILE_PAGE_SIZE,
    max_pages: int = T_TRADE_PROFILE_MAX_PAGES,
    max_source_ticks: int = T_TRADE_PROFILE_MAX_SOURCE_TICKS,
  ) -> Any:
    """Consume an async Tick page stream and save only after full validation."""

    code = _instrument_code(instrument_code)
    cutoff = time_utils.to_shanghai(as_of)
    max_minutes = _minute_entry_limit(lookback_calendar_days)
    _validate_page_limits(page_size, max_pages, max_source_ticks)
    accumulator = _CausalMinuteAccumulator(
      instrument_code=code,
      cutoff=cutoff,
      max_minute_entries=max_minutes,
      require_source_identity=True,
      earliest_date=cutoff.date() - timedelta(days=lookback_calendar_days - 1),
    )
    page_count = 0
    source_tick_count = 0
    previous_key: Optional[tuple[int, int]] = None
    async for page in pages:
      page_count += 1
      if page_count > max_pages:
        raise ValueError(
          "做 T 标的画像历史 Tick 页数超过安全上限，未保存画像"
        )
      if len(page) > page_size:
        raise ValueError(
          "做 T 标的画像历史 Tick 单页超过安全上限，未保存画像"
        )
      source_tick_count += len(page)
      if source_tick_count > max_source_ticks:
        raise ValueError(
          "做 T 标的画像历史 Tick 总量超过安全上限，未保存画像"
        )
      previous_key = accumulator.add_page(page, previous_key=previous_key)

    build = self._build_from_minutes(
      instrument_code=code,
      cutoff=cutoff,
      minute_rows=accumulator.minute_rows,
      accepted_tick_count=accumulator.accepted_tick_count,
      target_complete_days=target_complete_days,
      min_complete_days=min_complete_days,
    )
    return await repository.save_profile(**build.repository_arguments())

  def _build_from_minutes(
    self,
    *,
    instrument_code: str,
    cutoff: datetime,
    minute_rows: dict[date, list[_MinuteObservation]],
    accepted_tick_count: int,
    target_complete_days: int,
    min_complete_days: int,
  ) -> TTradeInstrumentProfileBuild:
    code = _instrument_code(instrument_code)
    target_days, minimum_days = _day_limits(
      target_complete_days,
      min_complete_days,
    )
    complete = [
      (trade_date, rows)
      for trade_date, rows in sorted(minute_rows.items())
      if _is_complete_day(rows)
    ]
    selected = complete[-target_days:]
    if len(selected) < minimum_days:
      raise ValueError(
        "做 T 标的画像完整交易日不足: "
        f"需要至少 {minimum_days} 日，实际 {len(selected)} 日"
      )

    pullbacks: list[float] = []
    momentum_rises: list[float] = []
    amount_velocity_ratios: list[float] = []
    spread_ticks: list[float] = []
    slot_amount_velocities: dict[str, list[float]] = defaultdict(list)
    day_metrics: list[dict[str, Any]] = []
    selected_times: list[datetime] = []
    total_minutes = 0
    for trade_date, rows in selected:
      derived = _derive_day(rows)
      pullbacks.extend(derived["pullbacks"])
      momentum_rises.extend(derived["momentum_rises"])
      amount_velocity_ratios.extend(derived["amount_velocity_ratios"])
      spread_ticks.extend(derived["spread_ticks"])
      for slot, values in derived["slot_amount_velocities"].items():
        slot_amount_velocities[slot].extend(values)
      selected_times.extend(row.at for row in rows)
      total_minutes += len(rows)
      day_metrics.append(
        {
          "trade_date": trade_date.isoformat(),
          "minute_count": len(rows),
          "amount_end": _round(rows[-1].cumulative_amount, 2),
          "pullback_p75_pct": _nullable_round(
            _percentile(derived["pullbacks"], 0.75), 4
          ),
          "momentum_rise_p75_pct": _nullable_round(
            _percentile(derived["momentum_rises"], 0.75), 4
          ),
          "spread_p90_ticks": _nullable_round(
            _percentile(derived["spread_ticks"], 0.90), 4
          ),
        }
      )

    if not pullbacks or not momentum_rises or not amount_velocity_ratios:
      raise ValueError("做 T 标的画像有效价格或成交额样本不足")
    if len(spread_ticks) < total_minutes // 2:
      raise ValueError("做 T 标的画像盘口价差覆盖不足")

    pullback_threshold = _round(
      _clamp(_percentile(pullbacks, 0.75) or 0.8, 0.6, 1.6),
      4,
    )
    momentum_threshold = _round(
      _clamp(_percentile(momentum_rises, 0.75) or 0.8, 0.6, 1.6),
      4,
    )
    amount_velocity_threshold = _round(
      _clamp(_percentile(amount_velocity_ratios, 0.75) or 2.0, 1.25, 5.0),
      4,
    )
    spread_p90 = _percentile(spread_ticks, 0.90) or 1.0
    pullback_spread = max(1, min(3, math.ceil(spread_p90)))
    momentum_spread = max(1, min(10, math.ceil(spread_p90 * 1.5)))

    selected_dates = [item[0].isoformat() for item in selected]
    profile = {
      "pullback_threshold_pct": pullback_threshold,
      "momentum_rise_threshold_pct": momentum_threshold,
      "momentum_amount_velocity_ratio": amount_velocity_threshold,
      "pullback_max_spread_ticks": pullback_spread,
      "momentum_max_spread_ticks": momentum_spread,
      "complete_trade_days": len(selected),
      "intraday_amount_velocity_baseline": {
        slot: _round(_percentile(values, 0.50) or 0.0, 4)
        for slot, values in sorted(slot_amount_velocities.items())
      },
      "spread_ticks_percentiles": {
        "p50": _round(_percentile(spread_ticks, 0.50) or 0.0, 4),
        "p90": _round(spread_p90, 4),
        "p95": _round(_percentile(spread_ticks, 0.95) or spread_p90, 4),
      },
    }
    source_min_at = min(selected_times)
    source_max_at = max(selected_times)
    metrics = {
      "complete_day_count": len(selected),
      "target_complete_days": target_days,
      "minimum_complete_days": minimum_days,
      "minute_count": total_minutes,
      "minute_coverage_ratio": _round(
        total_minutes / (len(selected) * _MINUTES_PER_SESSION * 2),
        6,
      ),
      "quantile_method": "linear",
      "day_metrics": day_metrics,
    }
    data_manifest = {
      "source": "influx:ticks",
      "source_min_at": source_min_at.isoformat(timespec="milliseconds"),
      "source_max_at": source_max_at.isoformat(timespec="milliseconds"),
      "causal_cutoff": cutoff.isoformat(timespec="milliseconds"),
      "input_tick_count": accepted_tick_count,
      "accepted_source_count": accepted_tick_count,
      "selected_trade_dates": selected_dates,
      "complete_trade_day_count": len(selected),
    }
    fingerprint = _fingerprint(
      {
        "instrument_code": code,
        "as_of": cutoff.isoformat(timespec="milliseconds"),
        "schema_version": T_TRADE_PROFILE_SCHEMA_VERSION,
        "builder_version": T_TRADE_PROFILE_VERSION,
        "profile": profile,
        "metrics": metrics,
        "data_manifest": data_manifest,
      }
    )
    materialization_version = f"{T_TRADE_PROFILE_VERSION}.{fingerprint[:12]}"
    return TTradeInstrumentProfileBuild(
      instrument_code=code,
      as_of=cutoff,
      profile=profile,
      schema_version=T_TRADE_PROFILE_SCHEMA_VERSION,
      version=materialization_version,
      fingerprint=fingerprint,
      metrics=metrics,
      data_manifest=data_manifest,
    )

  async def build_and_save_profile(
    self,
    *,
    instrument_code: str,
    ticks: Sequence[Any],
    as_of: datetime,
    repository: TTradeInstrumentProfileRepository,
    target_complete_days: int = T_TRADE_PROFILE_TARGET_COMPLETE_DAYS,
    min_complete_days: int = T_TRADE_PROFILE_MIN_COMPLETE_DAYS,
  ) -> Any:
    build = self.build_profile(
      instrument_code=instrument_code,
      ticks=ticks,
      as_of=as_of,
      target_complete_days=target_complete_days,
      min_complete_days=min_complete_days,
    )
    return await repository.save_profile(**build.repository_arguments())


def _causal_minutes(
  instrument_code: str,
  ticks: Sequence[Any],
  cutoff: datetime,
) -> tuple[dict[date, list[_MinuteObservation]], int]:
  """Compatibility reducer used by older callers and focused tests."""

  accumulator = _CausalMinuteAccumulator(
    instrument_code=_instrument_code(instrument_code),
    cutoff=time_utils.to_shanghai(cutoff),
    max_minute_entries=T_TRADE_PROFILE_MAX_MINUTE_ENTRIES,
    require_source_identity=False,
  )
  accumulator.add_ticks(sorted(ticks, key=_tick_order_key))
  return accumulator.minute_rows, accumulator.accepted_tick_count


def _tick_order_key(tick: Any) -> tuple[datetime, int]:
  return (
    time_utils.to_shanghai(getattr(tick, "time")),
    int(getattr(tick, "tick_ordinal", 0) or 0),
  )


def _strict_tick_source_identity(tick: Any) -> tuple[int, int]:
  source_value = getattr(tick, "source_time_ms", None)
  ordinal_value = getattr(tick, "tick_ordinal", None)
  if isinstance(source_value, bool) or isinstance(ordinal_value, bool):
    raise ValueError("做 T 标的画像历史 Tick 源身份包含布尔值，未保存画像")
  try:
    source_time_ms = int(source_value)
    tick_ordinal = int(ordinal_value)
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError(
      "做 T 标的画像历史 Tick 源身份不是整数，未保存画像"
    ) from exc
  if (
    source_time_ms <= 0
    or tick_ordinal < 0
    or tick_ordinal >= HISTORICAL_TICK_ORDINALS_PER_MILLISECOND
  ):
    raise ValueError(
      "做 T 标的画像历史 Tick 缺少可证明的源身份，未保存画像"
    )
  if source_value != source_time_ms or ordinal_value != tick_ordinal:
    raise ValueError(
      "做 T 标的画像历史 Tick 源身份无法无损表示，未保存画像"
    )
  return source_time_ms, tick_ordinal


def _source_time_at(source_time_ms: int) -> datetime:
  try:
    return tick_storage_time(source_time_ms, 0)
  except (OverflowError, OSError, ValueError) as exc:
    raise ValueError(
      "做 T 标的画像历史 Tick 源时间无法转换，未保存画像"
    ) from exc


def _minute_entry_limit(lookback_calendar_days: int) -> int:
  days = int(lookback_calendar_days)
  if days <= 0:
    raise ValueError("画像日历回看天数必须为正数")
  return days * 240


def _validate_minute_entry_limit(max_minute_entries: int) -> None:
  if int(max_minute_entries) <= 0:
    raise ValueError("画像分钟聚合上限必须为正数")


def _validate_page_limits(
  page_size: int,
  max_pages: int,
  max_source_ticks: int,
) -> None:
  if int(page_size) <= 0 or int(page_size) > T_TRADE_PROFILE_PAGE_SIZE:
    raise ValueError("画像历史 Tick 单页上限必须在 1 到 10000 之间")
  if int(max_pages) <= 0:
    raise ValueError("画像历史 Tick 总页上限必须为正数")
  if int(max_source_ticks) <= 0:
    raise ValueError("画像历史 Tick 总量上限必须为正数")


def _is_complete_day(rows: Sequence[_MinuteObservation]) -> bool:
  morning = [row for row in rows if row.at.time() < time(11, 30)]
  afternoon = [row for row in rows if row.at.time() >= time(13, 0)]
  if (
    len(morning) < _MIN_COMPLETE_MINUTES_PER_SESSION
    or len(afternoon) < _MIN_COMPLETE_MINUTES_PER_SESSION
  ):
    return False
  if (
    morning[0].at.time() > time(9, 35)
    or morning[-1].at.time() < time(11, 24)
    or afternoon[0].at.time() > time(13, 5)
    or afternoon[-1].at.time() < time(14, 54)
  ):
    return False
  amounts = [row.cumulative_amount for row in rows]
  if amounts[-1] <= 0:
    return False
  return all(after >= before for before, after in zip(amounts, amounts[1:]))


def _derive_day(rows: Sequence[_MinuteObservation]) -> dict[str, Any]:
  pullbacks: list[float] = []
  momentum_rises: list[float] = []
  amount_velocity_ratios: list[float] = []
  spreads = [row.spread_ticks for row in rows if row.spread_ticks is not None]
  slot_values: dict[str, list[float]] = defaultdict(list)

  amounts = [row.cumulative_amount for row in rows]
  amount_deltas = [0.0]
  amount_deltas.extend(max(0.0, current - previous) for previous, current in zip(amounts, amounts[1:]))
  for index, row in enumerate(rows):
    pullback_start = max(0, index - 5)
    pullback_high = max(item.price for item in rows[pullback_start : index + 1])
    pullbacks.append(max(0.0, (pullback_high - row.price) / pullback_high * 100.0))

    momentum_start = max(0, index - 1)
    momentum_low = min(item.price for item in rows[momentum_start : index + 1])
    momentum_rises.append(max(0.0, (row.price - momentum_low) / momentum_low * 100.0))

    baseline = amount_deltas[max(0, index - 5) : index]
    positive_baseline = [value for value in baseline if value > 0]
    current_amount = amount_deltas[index]
    if positive_baseline and current_amount > 0:
      baseline_mean = sum(positive_baseline) / len(positive_baseline)
      ratio = current_amount / baseline_mean
      if math.isfinite(ratio):
        amount_velocity_ratios.append(ratio)
        slot_values[row.at.strftime("%H:%M")].append(ratio)

  return {
    "pullbacks": pullbacks,
    "momentum_rises": momentum_rises,
    "amount_velocity_ratios": amount_velocity_ratios,
    "spread_ticks": spreads,
    "slot_amount_velocities": dict(slot_values),
  }


def _spread_ticks(tick: Any) -> Optional[float]:
  ask_values = getattr(tick, "ask_price", None) or []
  bid_values = getattr(tick, "bid_price", None) or []
  ask = _finite_positive(ask_values[0] if ask_values else None)
  bid = _finite_positive(bid_values[0] if bid_values else None)
  price_tick = _finite_positive(getattr(tick, "price_tick", None))
  if ask is None or bid is None or price_tick is None or ask < bid:
    return None
  return max(0.0, (ask - bid) / price_tick)


def _continuous_session(value: time) -> bool:
  return time(9, 30) <= value < time(11, 30) or time(13, 0) <= value < time(15, 0)


def _day_limits(target: int, minimum: int) -> tuple[int, int]:
  target_days = int(target)
  minimum_days = int(minimum)
  if minimum_days <= 0 or target_days < minimum_days:
    raise ValueError("画像交易日参数必须满足 0 < minimum <= target")
  return target_days, minimum_days


def _instrument_code(value: Any) -> str:
  normalized = str(value or "").strip().upper()
  if not normalized:
    raise ValueError("证券代码不能为空")
  return normalized


def _finite_positive(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if math.isfinite(normalized) and normalized > 0 else None


def _finite_non_negative(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if math.isfinite(normalized) and normalized >= 0 else None


def _percentile(values: Iterable[float], quantile: float) -> Optional[float]:
  ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
  if not ordered:
    return None
  if len(ordered) == 1:
    return ordered[0]
  position = (len(ordered) - 1) * quantile
  lower = math.floor(position)
  upper = math.ceil(position)
  if lower == upper:
    return ordered[lower]
  weight = position - lower
  return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _round(value: float, digits: int) -> float:
  return round(float(value), digits)


def _nullable_round(value: Optional[float], digits: int) -> Optional[float]:
  return None if value is None else _round(value, digits)


def _clamp(value: float, lower: float, upper: float) -> float:
  return max(lower, min(upper, float(value)))


def _fingerprint(value: dict[str, Any]) -> str:
  encoded = json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


__all__ = [
  "T_TRADE_PROFILE_MIN_COMPLETE_DAYS",
  "T_TRADE_PROFILE_MAX_MINUTE_ENTRIES",
  "T_TRADE_PROFILE_MAX_PAGES",
  "T_TRADE_PROFILE_MAX_SOURCE_TICKS",
  "T_TRADE_PROFILE_PAGE_SIZE",
  "T_TRADE_PROFILE_SCHEMA_VERSION",
  "T_TRADE_PROFILE_TARGET_COMPLETE_DAYS",
  "T_TRADE_PROFILE_VERSION",
  "TTradeInstrumentProfileBuild",
  "TTradeInstrumentProfileService",
]
