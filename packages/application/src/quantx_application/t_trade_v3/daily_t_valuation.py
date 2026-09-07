"""Intraday T-only cost accounting, rebased to explicit prior-session marks."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from quantx_application.t_trade_v3.portfolio_reference import aware_time

_ZERO = Decimal(0)
_EXCHANGE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class TValuationMark:
  instrument_code: str
  price: Decimal
  as_of: datetime
  source_id: str

  def validate(self, cut, *, max_age_seconds=None):
    time = aware_time(self.as_of)
    if (
      not self.source_id
      or not self.instrument_code
      or not isinstance(self.price, Decimal)
      or not self.price.is_finite()
      or self.price <= 0
    ):
      raise ValueError("T_VALUATION_MARK_INVALID")
    if time > cut:
      raise ValueError("T_VALUATION_FUTURE_MARK")
    if max_age_seconds is not None and (cut - time).total_seconds() > max_age_seconds:
      raise ValueError("T_VALUATION_MARK_STALE")


@dataclass(frozen=True)
class TDailyFill:
  fill_id: str
  batch_id: str
  instrument_code: str
  side: str
  volume: int
  price: Decimal
  fee: Decimal
  occurred_at: datetime
  event_revision: int
  event_fill_index: int


@dataclass(frozen=True)
class TOpeningPosition:
  batch_id: str
  instrument_code: str
  volume: int


@dataclass(frozen=True)
class TDailyValuation:
  realized: Decimal
  unrealized: Decimal
  cash_flow: Decimal
  opening_value: Decimal
  closing_value: Decimal
  remaining_quantities: tuple[tuple[str, int], ...]


def value_daily_t_positions(
  *,
  as_of: datetime,
  previous_trading_day: date,
  opening_positions: tuple[TOpeningPosition, ...],
  opening_marks: dict[str, TValuationMark],
  current_marks: dict[str, TValuationMark],
  fills: tuple[TDailyFill, ...],
  mark_max_age_seconds: int,
) -> TDailyValuation:
  """Realized + unrealized equals cash flow + marked change in T inventory.

  Seed holdings are absent by construction: only source-bound T batches enter.
  The adapter proves the previous trading day and supplies persisted fill order.
  """
  as_of = aware_time(as_of)
  day = as_of.astimezone(_EXCHANGE).date()
  if (
    previous_trading_day >= day
    or type(mark_max_age_seconds) is not int
    or mark_max_age_seconds <= 0
  ):
    raise ValueError("T_VALUATION_DAY_OR_FRESHNESS_INVALID")
  positions, costs, codes = {}, {}, {}
  opening_value = cash_flow = realized = _ZERO
  with localcontext() as context:
    context.prec = 50
    for position in opening_positions:
      if (
        not position.batch_id
        or position.batch_id in positions
        or type(position.volume) is not int
        or position.volume <= 0
      ):
        raise ValueError("T_VALUATION_OPENING_POSITION_INVALID")
      mark = opening_marks.get(position.instrument_code)
      if mark is None or mark.instrument_code != position.instrument_code:
        raise ValueError("T_VALUATION_OPENING_MARK_REQUIRED")
      mark.validate(as_of)
      if mark.as_of.astimezone(_EXCHANGE).date() != previous_trading_day:
        raise ValueError("T_VALUATION_OPENING_DAY_MISMATCH")
      positions[position.batch_id] = position.volume
      codes[position.batch_id] = position.instrument_code
      costs[position.batch_id] = position.volume * mark.price
      opening_value += costs[position.batch_id]
    seen, ordering = set(), set()
    ordered = sorted(
      fills, key=lambda fill: (fill.event_revision, fill.event_fill_index)
    )
    previous_time = None
    for fill in ordered:
      occurred = aware_time(fill.occurred_at)
      if (
        not fill.fill_id
        or fill.fill_id in seen
        or not fill.batch_id
        or not fill.instrument_code
        or fill.side not in {"BUY", "SELL"}
        or type(fill.volume) is not int
        or fill.volume <= 0
        or type(fill.event_revision) is not int
        or fill.event_revision < 1
        or type(fill.event_fill_index) is not int
        or fill.event_fill_index < 0
        or (fill.event_revision, fill.event_fill_index) in ordering
        or not isinstance(fill.price, Decimal)
        or not fill.price.is_finite()
        or fill.price <= 0
        or not isinstance(fill.fee, Decimal)
        or not fill.fee.is_finite()
        or fill.fee < 0
      ):
        raise ValueError("T_VALUATION_FILL_INVALID")
      if (
        occurred > as_of
        or occurred.astimezone(_EXCHANGE).date() != day
        or (previous_time is not None and occurred < previous_time)
      ):
        raise ValueError("T_VALUATION_FILL_TIME_INVALID")
      seen.add(fill.fill_id)
      ordering.add((fill.event_revision, fill.event_fill_index))
      previous_time = occurred
      if codes.setdefault(fill.batch_id, fill.instrument_code) != fill.instrument_code:
        raise ValueError("T_VALUATION_BATCH_SYMBOL_CONFLICT")
      quantity = positions.setdefault(fill.batch_id, 0)
      cost = costs.setdefault(fill.batch_id, _ZERO)
      amount = fill.volume * fill.price
      if fill.side == "BUY":
        positions[fill.batch_id] += fill.volume
        costs[fill.batch_id] += amount + fill.fee
        cash_flow -= amount + fill.fee
      else:
        if fill.volume > quantity:
          raise ValueError("T_VALUATION_EXIT_OVERFILL")
        released = cost if fill.volume == quantity else cost * fill.volume / quantity
        positions[fill.batch_id] -= fill.volume
        costs[fill.batch_id] -= released
        realized += amount - fill.fee - released
        cash_flow += amount - fill.fee
    closing_value = _ZERO
    for batch_id, quantity in positions.items():
      if quantity == 0:
        continue
      mark = current_marks.get(codes[batch_id])
      if mark is None or mark.instrument_code != codes[batch_id]:
        raise ValueError("T_VALUATION_CURRENT_MARK_REQUIRED")
      mark.validate(as_of, max_age_seconds=mark_max_age_seconds)
      closing_value += quantity * mark.price
    # Compute the authoritative daily total from cash and inventory conservation;
    # absorb only Decimal division residue into unrealized, never a money tolerance.
    unrealized = cash_flow + closing_value - opening_value - realized
    return TDailyValuation(
      realized,
      unrealized,
      cash_flow,
      opening_value,
      closing_value,
      tuple(sorted(positions.items())),
    )
