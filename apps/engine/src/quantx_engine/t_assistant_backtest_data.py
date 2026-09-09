"""Pin historical input hashes, optionally sharing immutable snapshot objects.

Callers inject LocalHistoricalTickReader and TradingDateHelper
with their explicitly selected data environment. This module never discovers or
starts a service, connects to QMT, or writes back to either source.
"""

from contextlib import aclosing
from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from numbers import Integral, Real
from pathlib import Path

from quantx_domain.clock import SHANGHAI
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.market_session import classify_market_data_session
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick
from quantx_domain.trading.t_trade import normalize_ashare_cumulative_volume
from quantx_domain.trading.t_trade_opportunity_engine import OpportunitySample
from quantx_infrastructure.services.t_assistant_backtest_store import (
  TAssistantBacktestStore,
)

from quantx_engine.t_assistant_backtest_timeline import BacktestTick

FIELDS = (
  "stock_code",
  "source_time_ms",
  "tick_ordinal",
  "last_price",
  "price_tick",
  "bid_price",
  "ask_price",
  "bid_vol",
  "ask_vol",
  "amount",
  "volume",
  "pvolume",
  "stock_status",
)


def _archive_value(value):
  """Arrow/pandas missing numerics are null archive facts, never fake prices."""
  if isinstance(value, (list, tuple)):
    return [_archive_value(v) for v in value]
  if isinstance(value, Real) and not isinstance(value, bool):
    if not isfinite(value):
      return None
    return int(value) if isinstance(value, Integral) else float(value)
  return value


def _source_row(tick, preserve_raw):
  row = {key: getattr(tick, key) for key in FIELDS}
  if preserve_raw:
    row = {key: _archive_value(value) for key, value in row.items()}
    row["last_close"] = _archive_value(getattr(tick, "last_close", None))
  return row


async def _daily_limits(history, code, day):
  daily = await history.read_daily_klines(stock_code=code, trading_date=day)
  if len(daily) > 1 or any(
    bar.time.astimezone(SHANGHAI).date() != day for bar in daily
  ):
    raise ValueError("BACKTEST_DAILY_REFERENCE_INVALID")
  return {
    key: _archive_value(getattr(daily[0], key, None)) if daily else None
    for key in ("up_stop_price", "down_stop_price")
  }


def _publish_shared(path, content):
  if not path.exists():
    try:
      TAssistantBacktestStore._create(path, content)
      return
    except FileExistsError:
      pass  # Another writer published this content-addressed object first.
  if stable_manifest_hash(TAssistantBacktestStore._read(path)) != stable_manifest_hash(
    content
  ):
    raise ValueError("BACKTEST_SHARED_OBJECT_CORRUPT")


def _event(row, *, stream_id, sequence, symbol_sequence, latency_ms):
  if any(
    len(row[name]) != 5 for name in ("bid_price", "ask_price", "bid_vol", "ask_vol")
  ):
    raise ValueError("BACKTEST_FULL_BOOK_REQUIRED")
  source_time = datetime.fromtimestamp(row["source_time_ms"] / 1000, UTC)
  decision_time = source_time + timedelta(milliseconds=latency_ms)
  day = source_time.astimezone(SHANGHAI).date().isoformat()
  volume = normalize_ashare_cumulative_volume(
    pvolume=row["pvolume"], volume=row["volume"]
  ).shares
  if volume is None:
    raise ValueError("BACKTEST_SOURCE_VOLUME_REQUIRED")
  sample = OpportunitySample(
    row["stock_code"],
    day,
    row["source_time_ms"],
    row["tick_ordinal"],
    row["last_price"],
    received_at_ms=int(decision_time.timestamp() * 1000),
    continuity_generation="1",
    bid_price=row["bid_price"][0],
    ask_price=row["ask_price"][0],
    bid_volume=row["bid_vol"][0],
    ask_volume=row["ask_vol"][0],
    cumulative_amount=row["amount"],
    cumulative_volume=volume,
    price_tick=row["price_tick"],
  )
  market = MarketDataSnapshot(
    row["stock_code"],
    source_time,
    row["last_price"],
    volume=volume,
    amount=row["amount"],
    price_tick=row["price_tick"],
    limit_up=row["up_stop_price"],
    limit_down=row["down_stop_price"],
    # Preserve the existing normalized states and documented XTData statuses.
    # Unknown raw enums are valid archive facts, but cannot authorize trading.
    is_trading=row["stock_status"] in {-1, 0, 13},
    suspended=row["stock_status"] in {1, 16, 17, 20},
    bid_price=row["bid_price"],
    ask_price=row["ask_price"],
    bid_vol=row["bid_vol"],
    ask_vol=row["ask_vol"],
    source=stream_id,
  )
  accepted = AcceptedTMarketTick(
    stream_id,
    symbol_sequence,
    sample.received_at_ms,
    sample,
    market_fence_sequence=sequence,
  )
  identity = f"{row['stock_code']}:{row['source_time_ms']}:{row['tick_ordinal']}"
  return BacktestTick(decision_time, sequence, identity, accepted, market)


class BacktestDataset:
  def __init__(self, directory, instruments=None, *, history=None):
    self.history = history
    self.directory = Path(directory)
    self.manifest = TAssistantBacktestStore._read(self.directory / "dataset.json")
    if self.manifest["hash"] != stable_manifest_hash(self.manifest["material"]):
      raise ValueError("BACKTEST_DATA_MANIFEST_CORRUPT")
    if self.manifest["material"]["schema_version"] != "backtest-tick-dataset.v2":
      raise ValueError("BACKTEST_DATA_SCHEMA_INVALID")
    if self.manifest["material"]["storage"] not in {"REFERENCE", "SNAPSHOT"}:
      raise ValueError("BACKTEST_DATA_STORAGE_INVALID")
    self.instruments = tuple(
      sorted(
        self.manifest["material"]["instruments"] if instruments is None else instruments
      )
    )
    if not self.instruments or not set(self.instruments) <= set(
      self.manifest["material"]["instruments"]
    ):
      raise ValueError("BACKTEST_DATA_UNIVERSE_INVALID")

  @property
  def input_manifest(self):
    material = self.manifest["material"]
    if material["status"] == "REFERENCE_REQUIRED":
      raise ValueError("BACKTEST_DATA_REFERENCE_REQUIRED")
    if material["status"] != "FROZEN":
      raise ValueError("BACKTEST_DATA_ACQUISITION_INCOMPLETE")
    parts = [p for p in material["parts"] if p["code"] in self.instruments]
    return {
      "dataset_hash": self.manifest["hash"],
      "instruments": list(self.instruments),
      "count": sum(p["count"] for p in parts),
      "first_ms": min(p["first_ms"] for p in parts),
      "latency_ms": material["latency_ms"],
    }

  def subset(self, instruments):
    return BacktestDataset(self.directory, instruments, history=self.history)

  async def events(self):
    material = self.manifest["material"]
    if material["status"] == "REFERENCE_REQUIRED":
      raise ValueError("BACKTEST_DATA_REFERENCE_REQUIRED")
    if material["status"] != "FROZEN":
      raise ValueError("BACKTEST_DATA_ACQUISITION_INCOMPLETE")
    counts = {code: 0 for code in self.instruments}
    stream_id = f"backtest-data:{stable_manifest_hash(self.input_manifest)}"
    sequence = 0
    for day in material["trading_days"]:
      rows = []
      for part in material["parts"]:
        if part["day"] != day or part["code"] not in self.instruments:
          continue
        if material["storage"] == "REFERENCE":
          if self.history is None:
            raise ValueError("BACKTEST_HISTORY_READER_REQUIRED")
          rows_read = []
          limits = await _daily_limits(
            self.history, part["code"], date.fromisoformat(day)
          )
          async with aclosing(
            self.history.iter_tick_pages(
              stock_code=part["code"],
              start_time=datetime.combine(date.fromisoformat(day), time.min, SHANGHAI),
              end_time=datetime.combine(date.fromisoformat(day), time.max, SHANGHAI),
            )
          ) as pages:
            async for page in pages:
              rows_read.extend(
                {**_source_row(tick, material["preserve_raw"]), **limits} for tick in page
              )
          content = {"rows": rows_read}
        else:
          path = self.directory.parent / "objects" / (part["hash"] + ".json")
          if path.resolve().parent != (self.directory.parent / "objects").resolve():
            raise ValueError("BACKTEST_DATA_PART_PATH_INVALID")
          content = TAssistantBacktestStore._read(path)
        if (
          stable_manifest_hash(content) != part["hash"]
          or len(content["rows"]) != part["count"]
        ):
          raise ValueError(
            "BACKTEST_SOURCE_CHANGED"
            if material["storage"] == "REFERENCE"
            else "BACKTEST_DATA_PART_CORRUPT"
          )
        rows.extend(content["rows"])
      rows.sort(key=lambda r: (r["source_time_ms"], r["tick_ordinal"], r["stock_code"]))
      for row in rows:
        source_at = datetime.fromtimestamp(row["source_time_ms"] / 1000, SHANGHAI)
        if not classify_market_data_session(source_at).is_continuous:
          continue
        sequence += 1
        counts[row["stock_code"]] += 1
        yield _event(
          row,
          stream_id=stream_id,
          sequence=sequence,
          symbol_sequence=counts[row["stock_code"]],
          latency_ms=material["latency_ms"],
        )


async def acquire_backtest_dataset(
  *,
  history,
  calendar,
  source_version: str,
  instruments: tuple[str, ...],
  start: date,
  end: date,
  root: Path,
  latency_ms: int,
  stop_on_error: bool = False,
  on_partition=None,
  preserve_raw: bool = False,
  freeze: bool = False,
):
  """Persist incomplete acquisition evidence without changing the sample range.

  Source pagination must exhaust its keyset cursor; the existing history service
  supplies that guarantee. Validation examines raw books; it never forward-fills
  missing prices, replaces Tick data with bars, or downloads through trading APIs.
  """
  if (
    not source_version
    or not instruments
    or len(set(instruments)) != len(instruments)
    or end < start
    or type(latency_ms) is not int
    or latency_ms < 0
  ):
    raise ValueError("BACKTEST_DATA_REQUEST_INVALID")
  root = Path(root)
  root.mkdir(parents=True, exist_ok=True)
  days = await calendar.get_trading_calendar(
    market="SH", start_date=start, end_date=end
  )
  if not days or days != sorted(set(days)) or min(days) < start or max(days) > end:
    raise ValueError("BACKTEST_CALENDAR_INVALID")
  parts, failures, references = [], [], []
  expected_minutes = {
    minute for minute in range(1440)
    if classify_market_data_session(
      datetime.combine(days[0], time(minute // 60, minute % 60, 30), SHANGHAI)
    ).is_continuous
  }
  for day in days:
    for code in sorted(instruments):
      rows, previous = [], None
      observed_minutes = set()
      stock_status_counts = {}
      missing_references = {}
      limits = {"up_stop_price": None, "down_stop_price": None}
      reason = None
      try:
        limits = await _daily_limits(history, code, day)
        async with aclosing(
          history.iter_tick_pages(
            stock_code=code,
            start_time=datetime.combine(day, time.min, SHANGHAI),
            end_time=datetime.combine(day, time.max, SHANGHAI),
          )
        ) as pages:
          async for page in pages:
            for tick in page:
              row = _source_row(tick, preserve_raw)
              row.update(limits)
              status_key = str(row["stock_status"])
              stock_status_counts[status_key] = stock_status_counts.get(status_key, 0) + 1
              identity = (row["source_time_ms"], row["tick_ordinal"])
              if preserve_raw:
                if (
                  row["stock_code"] != code
                  or type(identity[0]) is not int
                  or identity[0] <= 0
                  or type(identity[1]) is not int
                  or identity[1] < 0
                  or (previous is not None and identity <= previous)
                ):
                  raise ValueError("BACKTEST_SOURCE_IDENTITY_INVALID")
                source_at = datetime.fromtimestamp(identity[0] / 1000, SHANGHAI)
                if source_at.date() != day:
                  raise ValueError("BACKTEST_SOURCE_OUTSIDE_REQUEST")
                if not classify_market_data_session(source_at).is_continuous:
                  rows.append(row)
                  previous = identity
                  continue
                missing = [
                  key
                  for key in ("price_tick",)
                  if row[key] is None or row[key] <= 0
                ]
                for key in missing:
                  missing_references[key] = missing_references.get(key, 0) + 1
                if "price_tick" in missing:
                  rows.append(row)
                  previous = identity
                  continue
              if (
                row["stock_code"] != code
                or type(identity[0]) is not int
                or identity[0] <= 0
                or type(identity[1]) is not int
                or identity[1] < 0
                or (previous is not None and identity <= previous)
                or any(
                  not isfinite(row[k]) or row[k] <= 0
                  for k in ("price_tick",)
                )
                or any(
                  row[k] is not None and (not isfinite(row[k]) or row[k] <= 0)
                  for k in ("up_stop_price", "down_stop_price")
                )
                or type(row["stock_status"]) is not int
              ):
                raise ValueError("BACKTEST_SOURCE_IDENTITY_OR_LIMIT_INVALID")
              item = _event(
                row,
                stream_id="acquisition-validation",
                sequence=len(rows) + 1,
                symbol_sequence=len(rows) + 1,
                latency_ms=latency_ms,
              )
              if item.market.timestamp.astimezone(SHANGHAI).date() != day:
                raise ValueError("BACKTEST_SOURCE_OUTSIDE_REQUEST")
              at = item.market.timestamp.astimezone(SHANGHAI)
              minute = at.hour * 60 + at.minute
              if minute in expected_minutes:
                observed_minutes.add(minute)
              rows.append(row)
              previous = identity
      except Exception as exc:
        # Do not publish source exception strings (may contain connection details).
        detail = str(exc)
        reason = (
          detail
          if detail.startswith("BACKTEST_")
          and all(c.isupper() or c == "_" for c in detail)
          else type(exc).__name__
        )
      if not rows or reason:
        failures.append(
          {
            "day": day.isoformat(),
            "code": code,
            "reason": reason or "EMPTY_SOURCE",
            "rows_seen": len(rows),
          }
        )
      if "price_tick" in missing_references:
        references.append(
          {"day": day.isoformat(), "code": code, "missing_fields": missing_references}
        )
      content = {"rows": rows}
      content_hash = stable_manifest_hash(content)
      if freeze:
        objects = root / "objects"
        objects.mkdir(exist_ok=True)
        _publish_shared(objects / (content_hash + ".json"), content)
      times = [r["source_time_ms"] for r in rows]
      parts.append(
        {
          "day": day.isoformat(),
          "code": code,
          "count": len(rows),
          "hash": stable_manifest_hash(content),
          "first_ms": min(times) if times else None,
          "last_ms": max(times) if times else None,
          "source_exhausted": reason is None,
          "missing_reference_fields": missing_references,
          "daily_price_limits": limits,
          "stock_status_counts": stock_status_counts,
          "continuous_minute_coverage": {
            "expected_minutes": len(expected_minutes),
            "observed_minutes": len(observed_minutes),
            "ratio": len(observed_minutes) / len(expected_minutes),
          },
        }
      )
      if on_partition is not None:
        on_partition(
          {**parts[-1], "reason": reason or ("EMPTY_SOURCE" if not rows else None)}
        )
      if reason and stop_on_error:
        break
    if reason and stop_on_error:
      break
  material = {
    "schema_version": "backtest-tick-dataset.v2",
    "price_limit_policy": "CHECK_WHEN_AVAILABLE.v1",
    "price_limit_source": "DAILY_KLINE",
    "coverage_metric": "continuous-minute-coverage.v1",
    "storage": "SNAPSHOT" if freeze else "REFERENCE",
    "source_version": source_version,
    "instruments": sorted(instruments),
    "start": start.isoformat(),
    "end": end.isoformat(),
    "trading_days": [d.isoformat() for d in days],
    "latency_ms": latency_ms,
    "ordering": ["source_time_ms", "tick_ordinal", "instrument_code"],
    "parts": parts,
    "failures": failures,
    "status": "INCOMPLETE"
    if failures
    else "REFERENCE_REQUIRED"
    if references
    else "FROZEN",
    "reference_requirements": references,
    "preserve_raw": preserve_raw,
    "replay_session_policy": "CONTINUOUS_ONLY.v1",
    "strategy_sample_approval": "NOT_CONFIRMED",
    "unattempted_partitions": len(days) * len(instruments) - len(parts),
  }
  directory = root / stable_manifest_hash(material)
  directory.mkdir(exist_ok=True)
  _publish_shared(
    directory / "dataset.json",
    {"material": material, "hash": stable_manifest_hash(material)},
  )
  return BacktestDataset(directory, history=history)
