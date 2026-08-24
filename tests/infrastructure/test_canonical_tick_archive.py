from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pytest
import quantx_engine.strategy_executor as executor_module
import quantx_infrastructure.services.canonical_tick_archive as archive_module
from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.canonical_tick_archive import (
  MAX_LINE_BYTES,
  CanonicalTickArchive,
  CanonicalTickArchiveError,
  CanonicalTickArchiveHistoricalAdapter,
  FormalReplayScope,
  InstrumentDay,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
CODE = "000001.SZ"


def _source_ms(trading_date: date, *, seconds: int = 0) -> int:
  return int(
    datetime.combine(trading_date, time(9, 30), tzinfo=SHANGHAI).timestamp()
    * 1000
  ) + seconds * 1000


def _record(
  *,
  trading_date: date,
  source_time_ms: int | None = None,
  ordinal: int = 0,
  price: float = 10.0,
) -> dict[str, object]:
  source_time_ms = source_time_ms or _source_ms(trading_date)
  stored = time_utils.to_utc(tick_storage_time(source_time_ms, ordinal))
  return {
    "stock_code": CODE,
    "period": "tick",
    "time": stored.isoformat().replace("+00:00", "Z"),
    "last_price": price,
    "open": price,
    "high": price,
    "low": price,
    "last_close": price,
    "amount": price * 100,
    "volume": 100.0,
    "pvolume": 100.0,
    "tickvol": 100.0,
    "stock_status": 0,
    "open_int": 0,
    "last_settlement_price": 0.0,
    "settlement_price": 0.0,
    "transaction_num": ordinal + 1,
    "price_tick": 0.01,
    "up_stop_price": price * 1.1,
    "down_stop_price": price * 0.9,
    "ask_price": [price + 0.01] * 5,
    "bid_price": [price - 0.01] * 5,
    "ask_vol": [100.0] * 5,
    "bid_vol": [100.0] * 5,
    "source_time_ms": source_time_ms,
    "tick_ordinal": ordinal,
    "continuity_generation": 1,
    "market_stream_id": "verified-provider-stream-7",
    "market_stream_sequence": ordinal + 1,
    "market_stream_reset": False,
  }


def _write_ndjson(path: Path, records: list[dict[str, object]]) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(
    "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
    encoding="utf-8",
  )


def _sha256(path: Path) -> str:
  return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_manifest(
  tmp_path: Path,
  records: dict[InstrumentDay, Path],
  *,
  mutate: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
  dates = tuple(sorted({key.trade_date for key in records}))
  scope = FormalReplayScope.build(
    snapshot_date=dates[0] - timedelta(days=1),
    instrument_codes=[CODE],
    trading_dates=dates,
  )
  manifest: dict[str, Any] = {
    "schema_version": 1,
    "source": {
      "provider_id": "example-verified-provider",
      "source_kind": "EXTERNAL_RAW_TICK_NDJSON",
      "acquired_at": "2026-08-01T16:00:00+08:00",
      "as_of": "2026-08-01T15:05:00+08:00",
      "verification_status": "VERIFIED",
      "identity_provenance": {
        "source_time_ms": "provider event epoch milliseconds",
        "tick_ordinal": "provider same-millisecond stable ordinal",
      },
    },
    "formal_scope": scope.to_dict(),
    "instrument_days": [
      {
        "instrument_code": key.instrument_code,
        "trade_date": key.trade_date.isoformat(),
        "raw_input_sha256": _sha256(path),
        "identity_provenance": {
          "source_time_ms": "provider event epoch milliseconds",
          "tick_ordinal": "provider same-millisecond stable ordinal",
        },
      }
      for key, path in sorted(records.items())
    ],
  }
  if mutate is not None:
    mutate(manifest)
  path = tmp_path / "source-manifest.json"
  path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
  return path


def _formal_inputs(
  tmp_path: Path,
  *,
  first_day_records: int = 1,
  trading_dates: tuple[date, ...] | None = None,
) -> dict[InstrumentDay, Path]:
  trading_dates = trading_dates or tuple(
    date(2026, 7, 1) + timedelta(days=offset) for offset in range(20)
  )
  if len(trading_dates) != 20:
    raise AssertionError("test archive scope must contain exactly 20 dates")
  records: dict[InstrumentDay, Path] = {}
  for offset, trading_date in enumerate(trading_dates):
    key = InstrumentDay.parse(CODE, trading_date)
    path = tmp_path / f"{CODE}-{trading_date.isoformat()}.ndjson"
    day_records = [
      _record(
        trading_date=trading_date,
        source_time_ms=_source_ms(trading_date, seconds=index),
        ordinal=0,
        price=10.0 + index / 10,
      )
      for index in range(first_day_records if offset == 0 else 1)
    ]
    if offset == 0 and len(day_records) > 1:
      day_records.reverse()
    _write_ndjson(path, day_records)
    records[key] = path
  return records


def _publish(
  tmp_path: Path,
  *,
  first_day_records: int = 1,
  trading_dates: tuple[date, ...] | None = None,
):
  records = _formal_inputs(
    tmp_path,
    first_day_records=first_day_records,
    trading_dates=trading_dates,
  )
  manifest = _source_manifest(tmp_path, records)
  archive = CanonicalTickArchive(tmp_path / "archive")
  return archive, archive.publish(source_manifest=manifest, records=records), records


def test_publish_streams_sorts_and_reads_bounded_pages_with_shanghai_window(
  tmp_path: Path,
) -> None:
  archive, cutover, records = _publish(tmp_path, first_day_records=3)
  assert archive.publish(
    source_manifest=tmp_path / "source-manifest.json", records=records
  ) == cutover
  reader = archive.open(cutover.token)
  first_date = min(key.trade_date for key in records)
  start = datetime.combine(first_date, time(9, 25), tzinfo=SHANGHAI)
  end = datetime.combine(first_date, time(15, 5), tzinfo=SHANGHAI)
  pages = list(
    reader.iter_tick_pages(
      instrument_code=CODE,
      start_time=start,
      end_time=end,
      limit=2,
    )
  )
  assert [len(page) for page in pages] == [2, 1]
  assert [tick.last_price for page in pages for tick in page] == [10.0, 10.1, 10.2]
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_READ_LIMIT_EXCEEDED"):
    reader.read_ticks(
      instrument_code=CODE,
      start_time=start,
      end_time=end,
      limit=2,
    )
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_READ_TIMEZONE_REQUIRED"):
    list(
      reader.iter_tick_pages(
        instrument_code=CODE,
        start_time=start.replace(tzinfo=None),
        end_time=end,
      )
    )


def test_publish_releases_each_raw_and_normalized_stage_before_next_input(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  records = _formal_inputs(tmp_path)
  manifest = _source_manifest(tmp_path, records)
  observed_prior_stage_files: list[int] = []
  original_spool = archive_module._spool_verified_raw_input

  def observe_spool(
    source: Path,
    *,
    expected_sha256: str,
    stage_root: Path,
  ) -> Path:
    # At the boundary before every source is copied, a prior instrument-day
    # must have released both its private raw copy and normalized stage file.
    active = [
      *stage_root.glob("raw-*.ndjson"),
      *stage_root.glob("dataset-*.ndjson"),
    ]
    observed_prior_stage_files.append(len(active))
    return original_spool(
      source,
      expected_sha256=expected_sha256,
      stage_root=stage_root,
    )

  monkeypatch.setattr(archive_module, "_spool_verified_raw_input", observe_spool)
  CanonicalTickArchive(tmp_path / "archive").publish(
    source_manifest=manifest,
    records=records,
  )

  assert observed_prior_stage_files == [0] * len(records)


def test_unterminated_overlong_raw_and_object_lines_fail_closed(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  raw_records = _formal_inputs(tmp_path / "raw")
  raw_key, raw_path = next(iter(raw_records.items()))
  raw_path.write_bytes(
    json.dumps(_record(trading_date=raw_key.trade_date)).encode("utf-8")
    + b" " * (MAX_LINE_BYTES + 1)
  )
  raw_manifest = _source_manifest(tmp_path / "raw", raw_records)
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_NDJSON_LINE_TOO_LARGE"):
    CanonicalTickArchive(tmp_path / "raw-archive").publish(
      source_manifest=raw_manifest,
      records=raw_records,
    )

  archive, cutover, records = _publish(tmp_path / "object")
  reader = archive.open(cutover.token)
  dataset = next(iter(reader._datasets.values()))  # type: ignore[attr-defined]
  object_path = (
    tmp_path
    / "object"
    / "archive"
    / "objects"
    / f"{dataset['content_sha256']}.ndjson"
  )
  object_path.write_bytes(b"{" + b" " * (MAX_LINE_BYTES + 1))
  # The production reader validates the object hash first.  Bypass just that
  # check here to prove its independently bounded line reader handles a
  # corrupt, unterminated object without materialising it in full.
  monkeypatch.setattr(
    archive_module,
    "_sha256_file",
    lambda _path: str(dataset["content_sha256"]),
  )
  first_date = min(key.trade_date for key in records)
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_OBJECT_LINE_TOO_LARGE"):
    list(
      reader.iter_tick_pages(
        instrument_code=CODE,
        start_time=datetime.combine(first_date, time(9, 25), tzinfo=SHANGHAI),
        end_time=datetime.combine(first_date, time(15, 5), tzinfo=SHANGHAI),
      )
    )


def test_publish_fails_closed_when_external_raw_hash_or_provenance_is_invalid(
  tmp_path: Path,
) -> None:
  records = _formal_inputs(tmp_path)
  manifest = _source_manifest(tmp_path, records)
  first = next(iter(records.values()))
  first.write_text(first.read_text(encoding="utf-8") + "\n", encoding="utf-8")
  archive = CanonicalTickArchive(tmp_path / "archive")
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_SOURCE_INPUT_HASH_MISMATCH"):
    archive.publish(source_manifest=manifest, records=records)
  assert not list((tmp_path / "archive" / "objects").glob("*.ndjson"))

  second = tmp_path / "second"
  records = _formal_inputs(second)
  manifest = _source_manifest(
    second,
    records,
    mutate=lambda value: value["source"].__setitem__("verification_status", "PENDING"),
  )
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_SOURCE_NOT_VERIFIED"):
    CanonicalTickArchive(tmp_path / "second-archive").publish(
      source_manifest=manifest,
      records=records,
    )


def test_reader_rejects_tamper_and_wrong_formal_scope(tmp_path: Path) -> None:
  archive, cutover, records = _publish(tmp_path)
  reader = archive.open(cutover.token)
  first_date = min(key.trade_date for key in records)
  scope = reader.cutover.formal_scope
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_FORMAL_SCOPE_MISMATCH"):
    reader.validate_formal_scope(
      snapshot_date=scope.snapshot_date,
      instrument_codes=["000002.SZ"],
      trading_dates=scope.trading_dates,
    )
  object_path = next((tmp_path / "archive" / "objects").glob("*.ndjson"))
  object_path.write_text("{}\n", encoding="utf-8")
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_OBJECT_CONTENT_HASH_INVALID"):
    list(
      reader.iter_tick_pages(
        instrument_code=CODE,
        start_time=datetime.combine(first_date, time(9, 25), tzinfo=SHANGHAI),
        end_time=datetime.combine(first_date, time(15, 5), tzinfo=SHANGHAI),
      )
    )


def test_adapter_uses_strict_sequential_cursor_without_market_service(
  tmp_path: Path,
) -> None:
  archive, cutover, records = _publish(tmp_path, first_day_records=5)
  adapter = CanonicalTickArchiveHistoricalAdapter(archive.open(cutover.token))
  first_date = min(key.trade_date for key in records)
  start = datetime.combine(first_date, time(9, 25))
  end = datetime.combine(first_date, time(15, 5))

  async def read_pages() -> None:
    assert adapter.market_data_service is None
    first = await adapter.get_ticks(CODE, start, end, limit=2, offset=0)
    second = await adapter.get_ticks(CODE, start, end, limit=2, offset=2)
    third = await adapter.get_ticks(CODE, start, end, limit=2, offset=4)
    assert [len(first), len(second), len(third)] == [2, 2, 1]
    restarted = await adapter.get_ticks(CODE, start, end, limit=2, offset=0)
    assert len(restarted) == 2
    with pytest.raises(
      CanonicalTickArchiveError,
      match="ARCHIVE_ADAPTER_PAGINATION_OFFSET_UNEXPECTED",
    ):
      await adapter.get_ticks(CODE, start, end, limit=2, offset=1)
    with pytest.raises(
      CanonicalTickArchiveError,
      match="ARCHIVE_ADAPTER_PAGINATION_RESET_OR_CONCURRENT",
    ):
      await adapter.get_ticks(CODE, start, end, limit=2, offset=0)

  asyncio.run(read_pages())


def test_publish_rejects_empty_market_stream_id_and_symlink_input(
  tmp_path: Path,
) -> None:
  records = _formal_inputs(tmp_path)
  first_key, first_path = next(iter(records.items()))
  raw = json.loads(first_path.read_text(encoding="utf-8").splitlines()[0])
  raw["market_stream_id"] = ""
  _write_ndjson(first_path, [raw])
  manifest = _source_manifest(tmp_path, records)
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_FIELD_INVALID_MARKET_STREAM_ID"):
    CanonicalTickArchive(tmp_path / "archive").publish(
      source_manifest=manifest,
      records=records,
    )

  target = tmp_path / "target.ndjson"
  _write_ndjson(target, [_record(trading_date=first_key.trade_date)])
  linked = tmp_path / "linked.ndjson"
  try:
    linked.symlink_to(target)
  except OSError:
    pytest.skip("symlinks unavailable on this Windows test host")
  records[first_key] = linked
  manifest = _source_manifest(tmp_path, records)
  with pytest.raises(CanonicalTickArchiveError, match="ARCHIVE_SYMLINK_PATH_REJECTED"):
    CanonicalTickArchive(tmp_path / "symlink-archive").publish(
      source_manifest=manifest,
      records=records,
    )


def test_archive_adapter_skips_weekend_holiday_and_drives_executor_offset_pages(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  # The scope intentionally omits 2026-08-01/02 (weekend) and 2026-08-03
  # (declared holiday). A multi-day query must select only formal trading days.
  dates = (
    date(2026, 7, 31),
    date(2026, 8, 4),
    date(2026, 8, 5),
    date(2026, 8, 6),
    date(2026, 8, 7),
    date(2026, 8, 10),
    date(2026, 8, 11),
    date(2026, 8, 12),
    date(2026, 8, 13),
    date(2026, 8, 14),
    date(2026, 8, 17),
    date(2026, 8, 18),
    date(2026, 8, 19),
    date(2026, 8, 20),
    date(2026, 8, 21),
    date(2026, 8, 24),
    date(2026, 8, 25),
    date(2026, 8, 26),
    date(2026, 8, 27),
    date(2026, 8, 28),
  )
  archive, cutover, _records = _publish(
    tmp_path,
    first_day_records=3,
    trading_dates=dates,
  )
  adapter = CanonicalTickArchiveHistoricalAdapter(archive.open(cutover.token))
  assert adapter.market_data_service is None
  executor = object.__new__(executor_module.StrategyExecutor)
  executor._record_t_trade_replay_tick_read_success = lambda *_args, **_kwargs: None
  executor._runtime_log = lambda *_args, **_kwargs: None
  monkeypatch.setattr(executor_module, "_T_TRADE_REPLAY_TICK_PAGE_SIZE", 2)
  monkeypatch.setattr(executor_module, "_T_TRADE_REPLAY_MAX_TICK_PAGES_PER_WINDOW", 10)
  runtime = SimpleNamespace(context=SimpleNamespace(parameters={"t_trade_replay": True}))

  async def load() -> list[object]:
    return await executor._load_t_trade_replay_ticks_paginated(
      runtime,
      adapter,
      instrument_code=CODE,
      start_time=datetime.combine(dates[0], time(9, 25)),
      end_time=datetime.combine(dates[1], time(15, 5)),
    )

  ticks = asyncio.run(load())
  # Three sorted records on Friday plus one Tuesday record; no lookup for the
  # intervening weekend/holiday occurs and the real executor consumes offset
  # pages through the archive adapter only.
  assert len(ticks) == 4
  assert [tick.time.date() for tick in ticks] == [dates[0], dates[0], dates[0], dates[1]]
