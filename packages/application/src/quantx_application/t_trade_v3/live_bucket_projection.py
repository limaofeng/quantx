"""Account-wide bucket attribution replay; broker balances remain authoritative.

Only explicit seed attribution and source-bound fills enter. Unknown differences
never go into core automatically. Available bucket volumes are conservative lower
bounds when the broker reports freezes whose bucket attribution is unavailable.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from quantx_domain.brokers.base import OrderType
from quantx_domain.trading.bucket_ledger import KNOWN_BUCKETS, BucketLedger

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_application.t_trade_v3.portfolio_snapshot import _hash

_EXCHANGE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class LiveAttributedFill:
  fill_id: str
  client_order_id: str
  instrument_code: str
  side: str
  volume: int
  price: Decimal
  occurred_at: datetime
  bucket: str
  substitution_plan: dict | None = None


@dataclass(frozen=True)
class LiveBucketProjection:
  instruments: dict
  source_hash: str


def _qty(value):
  if type(value) is not int or value < 0:
    raise ValueError("LIVE_BUCKET_QUANTITY_INVALID")
  return value


def validate_seed(instruments: dict):
  if not isinstance(instruments, dict) or not instruments:
    raise ValueError("LIVE_BUCKET_EXPLICIT_SEED_REQUIRED")
  result = {}
  for code, buckets in instruments.items():
    if (
      not isinstance(code, str)
      or not code.strip()
      or not isinstance(buckets, dict)
      or set(buckets) != set(KNOWN_BUCKETS)
    ):
      raise ValueError("LIVE_BUCKET_SEED_SHAPE_INVALID")
    result[code] = {}
    for name, values in buckets.items():
      if not isinstance(values, dict) or set(values) != {
        "total_volume",
        "today_buy_volume",
      }:
        raise ValueError("LIVE_BUCKET_SEED_SHAPE_INVALID")
      total, today = _qty(values["total_volume"]), _qty(values["today_buy_volume"])
      if today > total:
        raise ValueError("LIVE_BUCKET_SEED_CONSERVATION_INVALID")
      result[code][name] = dict(
        total_volume=total,
        today_buy_volume=today,
        available_volume=total - today,
        frozen_volume=0,
      )
  return result


def replay_live_bucket_projection(
  *,
  seed: dict,
  seed_as_of: datetime,
  as_of: datetime,
  fills: tuple[LiveAttributedFill, ...],
  broker_positions: dict,
) -> LiveBucketProjection:
  seed_as_of, as_of = (
    aware_time(seed_as_of).astimezone(UTC),
    aware_time(as_of).astimezone(UTC),
  )
  if seed_as_of > as_of:
    raise ValueError("LIVE_BUCKET_FUTURE_SEED")
  instruments = validate_seed(seed)
  ledger = BucketLedger.from_dict(
    {
      "run_id": "",
      "instruments": instruments,
      "last_settlement_date": seed_as_of.astimezone(_EXCHANGE).date().isoformat(),
    }
  )
  previous_day = seed_as_of.astimezone(_EXCHANGE).date()
  seen, order_filled, order_material, same_clock = set(), {}, {}, {}
  for fill in sorted(
    fills, key=lambda item: (aware_time(item.occurred_at), item.fill_id)
  ):
    at = aware_time(fill.occurred_at).astimezone(UTC)
    if (
      not fill.fill_id
      or fill.fill_id in seen
      or not fill.client_order_id
      or not fill.instrument_code
      or fill.bucket not in KNOWN_BUCKETS
      or fill.side not in {"BUY", "SELL"}
      or _qty(fill.volume) <= 0
      or not isinstance(fill.price, Decimal)
      or not fill.price.is_finite()
      or fill.price <= 0
      or not seed_as_of < at <= as_of
    ):
      raise ValueError("LIVE_BUCKET_FILL_INVALID")
    # Source timestamps cannot establish the relative order of a buy and its
    # replacement sell at the same instant. Do not resolve this with UUID order.
    clock_key = (fill.instrument_code, at)
    if clock_key in same_clock and same_clock[clock_key] != fill.side:
      raise ValueError("LIVE_BUCKET_AMBIGUOUS_FILL_ORDER")
    same_clock[clock_key] = fill.side
    material = (fill.instrument_code, fill.side, fill.bucket, fill.substitution_plan)
    if (
      fill.client_order_id in order_material
      and order_material[fill.client_order_id] != material
    ):
      raise ValueError("LIVE_BUCKET_ORDER_MATERIAL_CONFLICT")
    order_material[fill.client_order_id] = deepcopy(material)
    seen.add(fill.fill_id)
    day = at.astimezone(_EXCHANGE).date()
    if day != previous_day:
      ledger.settle_trading_day(day)
      previous_day = day
    metadata = {"bucket": fill.bucket}
    if fill.side == "SELL":
      plan = deepcopy(fill.substitution_plan)
      if plan is not None:
        if (
          not isinstance(plan, dict)
          or plan.get("enabled") is not True
          or plan.get("requested_bucket") != fill.bucket
          or plan.get("reattribute_buy_to_bucket") not in {"core", "swing"}
          or not isinstance(plan.get("sell_from_buckets"), list)
        ):
          raise ValueError("LIVE_BUCKET_SUBSTITUTION_INVALID")
        names = [
          leg.get("bucket")
          for leg in plan["sell_from_buckets"]
          if isinstance(leg, dict)
        ]
        if len(names) != len(set(names)):
          raise ValueError("LIVE_BUCKET_SUBSTITUTION_DUPLICATE_LEG")
        requested = (
          ledger.snapshot()
          .instruments.get(fill.instrument_code, {})
          .get(fill.bucket, {})
        )
        if (
          plan["reattribute_buy_to_bucket"] != fill.bucket
          and requested.get("today_buy_volume", 0) < fill.volume
        ):
          raise ValueError("LIVE_BUCKET_SUBSTITUTION_BUY_EVIDENCE_REQUIRED")
        legs, skip, remaining = (
          [],
          order_filled.get(fill.client_order_id, 0),
          fill.volume,
        )
        for leg in plan["sell_from_buckets"]:
          if not isinstance(leg, dict) or leg.get("bucket") not in {"core", "swing"}:
            raise ValueError("LIVE_BUCKET_SUBSTITUTION_INVALID")
          volume = _qty(leg.get("volume"))
          skipped = min(skip, volume)
          skip -= skipped
          take = min(remaining, volume - skipped)
          if take:
            legs.append({"bucket": leg["bucket"], "volume": take})
            remaining -= take
        if remaining or skip:
          raise ValueError("LIVE_BUCKET_SUBSTITUTION_OVERFILL")
        plan.update(volume=fill.volume, sell_from_buckets=legs)
        plan.pop("applied_volume", None)
        plan.pop("rolled_back_volume", None)
      if not ledger.reserve_order(
        order_id=fill.client_order_id,
        instrument_code=fill.instrument_code,
        order_type=OrderType.SELL,
        volume=fill.volume,
        price=float(fill.price),
        bucket=fill.bucket,
        metadata=metadata,
        substitution_plan=plan,
      ):
        raise ValueError("LIVE_BUCKET_SELL_EXCEEDS_ATTRIBUTION")
    ledger.apply_trade(
      dict(
        order_id=fill.client_order_id,
        instrument_code=fill.instrument_code,
        volume=fill.volume,
        price=float(fill.price),
        trade_type=OrderType.BUY if fill.side == "BUY" else OrderType.SELL,
      ),
      metadata,
    )
    order_filled[fill.client_order_id] = (
      order_filled.get(fill.client_order_id, 0) + fill.volume
    )
  if as_of.astimezone(_EXCHANGE).date() != previous_day:
    ledger.settle_trading_day(as_of.astimezone(_EXCHANGE).date())
  states = ledger.snapshot().instruments
  result = {}
  for code in sorted(set(states) | set(broker_positions)):
    buckets = states.get(
      code,
      {
        name: dict(total_volume=0, available_volume=0, today_buy_volume=0)
        for name in KNOWN_BUCKETS
      },
    )
    actual = broker_positions.get(code, {"volume": 0, "can_use_volume": 0})
    total, free = _qty(actual.get("volume")), _qty(actual.get("can_use_volume"))
    if total != sum(row["total_volume"] for row in buckets.values()) or free > total:
      raise ValueError("LIVE_BUCKET_BROKER_TOTAL_CONFLICT")
    settled = sum(row["available_volume"] for row in buckets.values())
    if free > settled:
      raise ValueError("LIVE_BUCKET_BROKER_SETTLEMENT_CONFLICT")
    unavailable = settled - free
    result[code] = {
      name: dict(
        total_volume=row["total_volume"],
        today_buy_volume=row["today_buy_volume"],
        available_volume=max(0, row["available_volume"] - unavailable),
      )
      for name, row in buckets.items()
    }
  return LiveBucketProjection(
    result,
    _hash(
      dict(
        seed=seed,
        seed_as_of=seed_as_of,
        as_of=as_of,
        fills=fills,
        broker_positions=broker_positions,
        projection=result,
      )
    ),
  )
