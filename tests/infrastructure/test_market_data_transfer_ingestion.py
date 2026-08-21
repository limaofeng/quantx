import asyncio
import gzip
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from quantx_contracts import (
  HISTORICAL_BAR_NO_DATA_REASON,
  HISTORICAL_TICK_ORDINAL_FIELD,
  HistoricalBarSummary,
  historical_bar_key,
)
from quantx_infrastructure.services import market_data_staging as staging
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion

SHANGHAI_DAY_START_MS = 1_699_977_600_000
SHANGHAI_DAY_END_EXCLUSIVE_MS = SHANGHAI_DAY_START_MS + 24 * 60 * 60 * 1000


def _payload(
  *,
  stock_list: list[str] | None = None,
  periods: list[str] | None = None,
) -> dict:
  return {
    "operation": "bars",
    "stock_list": stock_list or ["600000.SH"],
    "periods": periods or ["tick"],
    "start_time": "20231115",
    "end_time": "20231115",
  }


def _tick_row(
  *,
  code: str = "600000.SH",
  time: int = SHANGHAI_DAY_START_MS + 9 * 60 * 60 * 1000 + 30 * 60 * 1000,
  ordinal: int = 0,
  last_price: float = 10.0,
) -> dict:
  return {
    "code": code,
    "period": "tick",
    "time": time,
    HISTORICAL_TICK_ORDINAL_FIELD: ordinal,
    "lastPrice": last_price,
    "open": 9.9,
    "high": 10.1,
    "low": 9.8,
    "lastClose": 9.85,
    "amount": 100_000.0,
    "volume": 10_000.0,
    "pvolume": 10_000.0,
    "tickvol": 100.0,
    "stockStatus": 0,
    "openInt": 0,
    "lastSettlementPrice": 0.0,
    "settlementPrice": 0.0,
    "transactionNum": 10,
    "askPrice": [10.01, 0.0, 0.0, 0.0, 0.0],
    "bidPrice": [10.0, 0.0, 0.0, 0.0, 0.0],
    "askVol": [100.0, 0.0, 0.0, 0.0, 0.0],
    "bidVol": [200.0, 0.0, 0.0, 0.0, 0.0],
    "priceTick": 0.01,
    "upperLimit": 10.84,
    "lowerLimit": 8.87,
  }


def _kline_row(
  *,
  code: str = "600000.SH",
  period: str = "1m",
  time: int = SHANGHAI_DAY_START_MS,
) -> dict:
  return {
    "code": code,
    "period": period,
    "time": time,
    "open": 10.0,
    "high": 10.2,
    "low": 9.9,
    "close": 10.1,
    "preClose": 9.95,
    "volume": 1000.0,
    "amount": 10_000.0,
    "suspendFlag": 0,
    "settlementPrice": 0.0,
    "openInterest": 0,
  }


def _summary(
  rows: list[dict],
  *,
  code: str = "600000.SH",
  period: str = "tick",
) -> dict:
  digest = hashlib.sha256()
  for index, row in enumerate(rows):
    if index:
      digest.update(b"\n")
    digest.update(
      historical_bar_key(
        code=code,
        period=period,
        time_ms=int(row["time"]),
        tick_ordinal=(
          int(row[HISTORICAL_TICK_ORDINAL_FIELD]) if period == "tick" else None
        ),
      ).encode("utf-8")
    )
  return HistoricalBarSummary(
    code=code,
    period=period,
    row_count=len(rows),
    min_time=int(rows[0]["time"]) if rows else None,
    max_time=int(rows[-1]["time"]) if rows else None,
    key_sha256=digest.hexdigest(),
    no_data_reason=(None if rows else HISTORICAL_BAR_NO_DATA_REASON),
  ).model_dump(mode="json")


def _write_chunk(
  directory: Path,
  records: list[dict],
  *,
  index: int = 0,
  compressed: bool = True,
  raw: bytes | None = None,
) -> dict:
  encoded = raw
  if encoded is None:
    encoded = json.dumps(
      records,
      ensure_ascii=False,
      separators=(",", ":"),
      sort_keys=True,
      allow_nan=False,
    ).encode("utf-8")
  body = gzip.compress(encoded, mtime=0) if compressed else encoded
  path = directory / f"{index:08d}.json.gz"
  path.write_bytes(body)
  return {
    "chunk_index": index,
    "checksum_sha256": hashlib.sha256(body).hexdigest(),
    "record_count": len(records),
    "compressed": compressed,
    "storage_reference": str(path),
  }


class ManifestStore:
  def __init__(self, *, payload: dict, manifest: list[dict]) -> None:
    self.request = {
      "expected_chunks": len(manifest),
      "received_chunks": len(manifest),
      "request_payload": payload,
    }
    self.manifest = manifest

  async def market_data_request(self, request_id: str):
    assert request_id == "request-1"
    return self.request

  async def market_data_transfers(self, request_id: str):
    assert request_id == "request-1"
    return self.manifest


class AtomicRequestStore:
  def __init__(self) -> None:
    self.status = "UPLOADED"
    self.error = ""
    self.ingestion_result = None
    self.claim_count = 0
    self.release_count = 0
    self.claim_token: str | None = None
    self.transitions = ["UPLOADED"]
    self._lock = asyncio.Lock()

  async def claim_market_data_request(self, request_id: str) -> str | None:
    assert request_id == "request-1"
    async with self._lock:
      if self.status != "UPLOADED":
        return None
      self.status = "PROCESSING"
      self.claim_count += 1
      self.claim_token = f"claim-{self.claim_count}"
      self.transitions.append(self.status)
      return self.claim_token

  async def renew_market_data_request_claim(
    self,
    request_id: str,
    *,
    claim_token: str,
  ) -> bool:
    assert request_id == "request-1"
    return self.status == "PROCESSING" and claim_token == self.claim_token

  async def finish_market_data_request(
    self,
    request_id: str,
    *,
    status: str,
    error: str = "",
    ingestion_result: dict | None = None,
    claim_token: str | None = None,
  ) -> None:
    assert request_id == "request-1"
    if self.status != "PROCESSING" or claim_token != self.claim_token:
      raise RuntimeError("market-data processing claim was lost")
    self.status = status
    self.error = error
    self.ingestion_result = ingestion_result
    self.claim_token = None
    self.transitions.append(status)

  async def release_market_data_request_claim(
    self,
    request_id: str,
    *,
    claim_token: str,
    error: str,
  ) -> bool:
    assert request_id == "request-1"
    if self.status != "PROCESSING" or claim_token != self.claim_token:
      return False
    self.status = "UPLOADED"
    self.error = error
    self.claim_token = None
    self.release_count += 1
    self.transitions.append("UPLOADED")
    return True

  async def market_data_transfers(self, request_id: str):
    assert request_id == "request-1"
    return []


def test_tick_preprocessing_preserves_source_key_and_optional_limit_fields() -> None:
  row = _tick_row()
  frame = pd.DataFrame(
    [{key: value for key, value in row.items() if key not in {"code", "period"}}]
  )

  normalized = ingestion.preprocess_market_data("tick", {"600000.SH": frame})

  result = normalized.iloc[0]
  assert result["price_tick"] == pytest.approx(0.01)
  assert result["up_stop_price"] == pytest.approx(10.84)
  assert result["down_stop_price"] == pytest.approx(8.87)
  assert result["source_time_ms"] == row["time"]
  assert result[HISTORICAL_TICK_ORDINAL_FIELD] == 0
  assert result["settlement_price"] == 0.0
  reconstructed = normalized["time"].dt.tz_convert("UTC").astype("int64") // 1_000_000
  assert reconstructed.tolist() == [row["time"]]


def test_request_scope_uses_inclusive_shanghai_calendar_boundaries() -> None:
  rows = [
    _kline_row(time=SHANGHAI_DAY_START_MS),
    _kline_row(time=SHANGHAI_DAY_END_EXCLUSIVE_MS - 1),
  ]
  records = [*rows, _summary(rows, period="1m")]

  ingestion.validate_bar_records_against_request(
    records,
    _payload(periods=["1m"]),
  )

  for outside_time in (
    SHANGHAI_DAY_START_MS - 1,
    SHANGHAI_DAY_END_EXCLUSIVE_MS,
  ):
    outside = _kline_row(time=outside_time)
    with pytest.raises(ingestion.MarketDataValidationError, match="outside request window"):
      ingestion.validate_bar_records_against_request(
        [outside, _summary([outside], period="1m")],
        _payload(periods=["1m"]),
      )


@pytest.mark.parametrize(
  ("conflicting_field", "match"),
  [
    ("settelementPrice", "conflicting settlement price"),
    ("openInt", "conflicting open interest"),
  ],
)
def test_kline_alias_collisions_are_rejected_before_dataframe_normalization(
  conflicting_field: str,
  match: str,
) -> None:
  row = _kline_row()
  row[conflicting_field] = 0

  with pytest.raises(ingestion.MarketDataValidationError, match=match):
    ingestion.validate_bar_records_against_request(
      [row, _summary([row], period="1m")],
      _payload(periods=["1m"]),
    )


def test_summary_cartesian_product_and_legal_empty_series_are_closed() -> None:
  records = [
    _summary([], code=code, period=period)
    for period in ("tick", "1d")
    for code in ("000001.SZ", "600000.SH")
  ]
  payload = _payload(
    stock_list=["600000.SH", "000001.SZ"],
    periods=["tick", "1d"],
  )

  ingestion.validate_bar_records_against_request(records, payload)

  with pytest.raises(ingestion.MarketDataValidationError, match="missing required summaries"):
    ingestion.validate_bar_records_against_request(records[:-1], payload)


def test_empty_summary_requires_explicit_no_data_reason() -> None:
  summary = _summary([])
  summary["no_data_reason"] = None

  with pytest.raises(ingestion.MarketDataValidationError, match="invalid historical bar summary"):
    ingestion.validate_bar_records_against_request([summary], _payload())


def test_request_scope_rejects_unbounded_or_invalid_instrument_sets() -> None:
  with pytest.raises(ingestion.MarketDataValidationError, match="at most 300"):
    ingestion.validate_bar_records_against_request(
      [],
      _payload(stock_list=[f"{index:06d}.SZ" for index in range(301)]),
    )
  with pytest.raises(ingestion.MarketDataValidationError, match="invalid instruments"):
    ingestion.validate_bar_records_against_request(
      [],
      _payload(stock_list=["NOT-A-STOCK"]),
    )


@pytest.mark.asyncio
async def test_pass_one_rejects_late_invalid_summary_before_any_influx_write(
  tmp_path: Path,
) -> None:
  row = _tick_row()
  invalid_summary = _summary([row])
  invalid_summary["key_sha256"] = "0" * 64
  manifest = [
    _write_chunk(tmp_path, [row], index=0),
    _write_chunk(tmp_path, [invalid_summary], index=1),
  ]
  store = ManifestStore(payload=_payload(), manifest=manifest)
  save = AsyncMock()

  with pytest.raises(ingestion.MarketDataValidationError, match="does not match"):
    await ingestion.ingest_uploaded_bar_request(
      store,
      "request-1",
      save_period=save,
    )

  save.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_pass_ingestion_does_not_use_whole_request_materializer(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  row = _tick_row()
  manifest = [
    _write_chunk(tmp_path, [row], index=0),
    _write_chunk(tmp_path, [_summary([row])], index=1),
  ]
  store = ManifestStore(payload=_payload(), manifest=manifest)
  monkeypatch.setattr(
    ingestion,
    "_read_transfer_records",
    lambda _manifest: (_ for _ in ()).throw(AssertionError("whole materializer used")),
  )
  calls: list[int] = []

  async def save_period(*, period, market_data):
    assert period == "tick"
    count = sum(len(frame) for frame in market_data.values())
    calls.append(count)
    return {"status": "success", "saved_count": count}

  result = await ingestion.ingest_uploaded_bar_request(
    store,
    "request-1",
    save_period=save_period,
  )

  assert calls == [1]
  assert result["records_received"] == 1
  assert result["records_saved"] == 1


@pytest.mark.asyncio
async def test_uncompressed_empty_transfer_is_valid_and_performs_no_write(
  tmp_path: Path,
) -> None:
  manifest = [_write_chunk(tmp_path, [_summary([])], compressed=False)]
  store = ManifestStore(payload=_payload(), manifest=manifest)
  save = AsyncMock()

  result = await ingestion.ingest_uploaded_bar_request(
    store,
    "request-1",
    save_period=save,
  )

  save.assert_not_awaited()
  assert result["records_received"] == 0
  assert result["records_saved"] == 0
  assert result["empty_codes"] == ["600000.SH"]


@pytest.mark.asyncio
async def test_pass_two_never_exceeds_2000_rows_per_write() -> None:
  rows = [
    _tick_row(time=SHANGHAI_DAY_START_MS + index, last_price=10 + index / 100_000)
    for index in range(2001)
  ]
  records = [*rows, _summary(rows)]
  batch_sizes: list[int] = []

  async def save_period(*, period, market_data):
    assert period == "tick"
    count = sum(len(frame) for frame in market_data.values())
    batch_sizes.append(count)
    return {"status": "success", "saved_count": count}

  result = await ingestion.persist_bar_records(
    records,
    payload=_payload(),
    save_period=save_period,
  )

  assert batch_sizes == [2000, 1]
  assert max(batch_sizes) <= ingestion.MARKET_DATA_WRITE_BATCH_RECORDS
  assert result["records_saved"] == 2001


@pytest.mark.asyncio
async def test_pass_two_honors_byte_limit_before_appending_next_record(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rows = [
    _tick_row(time=SHANGHAI_DAY_START_MS + index, last_price=10 + index)
    for index in range(2)
  ]
  threshold = max(ingestion._encoded_record_size(row) for row in rows)
  monkeypatch.setattr(ingestion, "MARKET_DATA_WRITE_BATCH_BYTES", threshold)
  batch_sizes: list[int] = []

  async def save_period(*, period, market_data):
    assert period == "tick"
    count = sum(len(frame) for frame in market_data.values())
    batch_sizes.append(count)
    return {"status": "success", "saved_count": count}

  await ingestion.persist_bar_records(
    [*rows, _summary(rows)],
    payload=_payload(),
    save_period=save_period,
  )

  assert batch_sizes == [1, 1]


@pytest.mark.parametrize("compressed", [True, False], ids=["gzip", "raw"])
def test_chunk_uncompressed_limit_applies_to_gzip_and_raw(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  compressed: bool,
) -> None:
  raw = b"[" + b" " * 64 + b"]"
  item = _write_chunk(tmp_path, [], compressed=compressed, raw=raw)
  monkeypatch.setattr(ingestion, "MAX_TRANSFER_CHUNK_UNCOMPRESSED_BYTES", 32)

  with pytest.raises(ingestion.MarketDataValidationError, match="uncompressed limit"):
    ingestion._read_transfer_chunk(item, ingestion._TransferBudget())


def test_chunk_compressed_limit_is_enforced_while_reading(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  item = _write_chunk(tmp_path, [_summary([])])
  size = Path(item["storage_reference"]).stat().st_size
  monkeypatch.setattr(ingestion, "MAX_TRANSFER_CHUNK_COMPRESSED_BYTES", size - 1)

  with pytest.raises(ingestion.MarketDataValidationError, match="compressed limit"):
    ingestion._read_transfer_chunk(item, ingestion._TransferBudget())


def test_record_byte_limit_is_enforced_after_json_decode(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  item = _write_chunk(tmp_path, [{"value": "x" * 64}])
  monkeypatch.setattr(ingestion, "MAX_TRANSFER_RECORD_UNCOMPRESSED_BYTES", 32)

  with pytest.raises(ingestion.MarketDataValidationError, match="record exceeds byte"):
    ingestion._read_transfer_chunk(item, ingestion._TransferBudget())


def test_request_record_total_is_enforced_across_chunks(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  items = [
    _write_chunk(tmp_path, [{"value": index}], index=index)
    for index in range(2)
  ]
  monkeypatch.setattr(ingestion, "MAX_TRANSFER_REQUEST_RECORDS", 1)

  with pytest.raises(ingestion.MarketDataValidationError, match="record count limit"):
    list(ingestion._iter_transfer_chunks(items))


def test_request_uncompressed_total_is_enforced_across_chunks(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  items = [_write_chunk(tmp_path, [], index=index) for index in range(2)]
  monkeypatch.setattr(ingestion, "MAX_TRANSFER_REQUEST_UNCOMPRESSED_BYTES", len(b"[]"))

  with pytest.raises(ingestion.MarketDataValidationError, match="uncompressed byte limit"):
    list(ingestion._iter_transfer_chunks(items))


def test_request_compressed_total_is_enforced_across_chunks(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  items = [_write_chunk(tmp_path, [], index=index) for index in range(2)]
  first_size = Path(items[0]["storage_reference"]).stat().st_size
  monkeypatch.setattr(ingestion, "MAX_TRANSFER_REQUEST_COMPRESSED_BYTES", first_size)

  with pytest.raises(ingestion.MarketDataValidationError, match="compressed byte limit"):
    list(ingestion._iter_transfer_chunks(items))


def test_invalid_gzip_and_bad_checksum_are_permanent_validation_errors(
  tmp_path: Path,
) -> None:
  invalid_gzip = _write_chunk(tmp_path, [], raw=b"not-gzip", compressed=False)
  invalid_gzip["compressed"] = True
  with pytest.raises(ingestion.MarketDataValidationError, match="invalid gzip"):
    ingestion._read_transfer_chunk(invalid_gzip, ingestion._TransferBudget())

  checksum = _write_chunk(tmp_path, [], index=1)
  checksum["checksum_sha256"] = "0" * 64
  with pytest.raises(ingestion.MarketDataValidationError, match="checksum mismatch"):
    ingestion._read_transfer_chunk(checksum, ingestion._TransferBudget())


@pytest.mark.parametrize(
  ("field", "value", "match"),
  [
    ("record_count", True, "record count"),
    ("compressed", "true", "compressed flag"),
    ("checksum_sha256", "not-a-digest", "SHA256"),
    ("storage_reference", "", "storage reference"),
  ],
)
def test_malformed_manifest_item_is_a_permanent_validation_error(
  tmp_path: Path,
  field: str,
  value,
  match: str,
) -> None:
  item = _write_chunk(tmp_path, [])
  item[field] = value

  with pytest.raises(ingestion.MarketDataValidationError, match=match):
    ingestion._read_transfer_chunk(item, ingestion._TransferBudget())


@pytest.mark.asyncio
async def test_claim_success_persists_ingestion_audit() -> None:
  store = AtomicRequestStore()
  audit = {
    "operation": "bars",
    "records_received": 3,
    "records_saved": 3,
    "code_summaries": [],
  }

  async def ingest(_store, request_id):
    assert _store is store
    assert request_id == "request-1"
    return audit

  result = await ingestion.claim_ingest_and_finish_market_data_request(
    store,
    "request-1",
    ingest_request=ingest,
  )

  assert result == {"status": "completed", "request_id": "request-1", **audit}
  assert store.transitions == ["UPLOADED", "PROCESSING", "COMPLETED"]
  assert store.ingestion_result == audit


@pytest.mark.asyncio
async def test_validation_failure_is_terminal_but_influx_failure_is_retryable() -> None:
  invalid_store = AtomicRequestStore()

  async def invalid(_store, _request_id):
    raise ingestion.MarketDataValidationError("bad immutable payload")

  invalid_result = await ingestion.claim_ingest_and_finish_market_data_request(
    invalid_store,
    "request-1",
    ingest_request=invalid,
  )
  assert invalid_result["status"] == "failed"
  assert invalid_store.transitions == ["UPLOADED", "PROCESSING", "FAILED"]
  assert invalid_store.release_count == 0

  retry_store = AtomicRequestStore()

  async def unavailable(_store, _request_id):
    raise RuntimeError("Influx unavailable")

  retry_result = await ingestion.claim_ingest_and_finish_market_data_request(
    retry_store,
    "request-1",
    ingest_request=unavailable,
  )
  assert retry_result == {
    "status": "retryable",
    "request_id": "request-1",
    "reason": "RuntimeError: Influx unavailable",
  }
  assert retry_store.transitions == ["UPLOADED", "PROCESSING", "UPLOADED"]
  assert retry_store.release_count == 1


@pytest.mark.asyncio
async def test_cancellation_releases_claim_without_marking_transfer_failed() -> None:
  store = AtomicRequestStore()
  entered = asyncio.Event()

  async def ingest(_store, _request_id):
    entered.set()
    await asyncio.Event().wait()

  task = asyncio.create_task(
    ingestion.claim_ingest_and_finish_market_data_request(
      store,
      "request-1",
      ingest_request=ingest,
    )
  )
  await entered.wait()
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task

  assert store.status == "UPLOADED"
  assert store.release_count == 1
  assert "cancelled" in store.error


@pytest.mark.asyncio
async def test_lost_claim_stops_ingestion_and_is_classified_retryable(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class LostLeaseStore(AtomicRequestStore):
    async def renew_market_data_request_claim(
      self,
      request_id: str,
      *,
      claim_token: str,
    ) -> bool:
      assert request_id == "request-1"
      assert claim_token == "claim-1"
      self.claim_token = "claim-2"
      return False

  store = LostLeaseStore()
  ingestion_cancelled = asyncio.Event()
  monkeypatch.setattr(ingestion, "MARKET_DATA_CLAIM_RENEW_SECONDS", 0)

  async def ingest(_store, _request_id):
    try:
      await asyncio.Event().wait()
    finally:
      ingestion_cancelled.set()

  result = await ingestion.claim_ingest_and_finish_market_data_request(
    store,
    "request-1",
    ingest_request=ingest,
  )

  assert result is not None
  assert result["status"] == "retryable"
  assert "claim was lost" in result["reason"]
  assert ingestion_cancelled.is_set()
  assert store.status == "PROCESSING"
  assert store.claim_token == "claim-2"


@pytest.mark.asyncio
async def test_stale_claim_owner_cannot_renew_release_or_finish() -> None:
  store = AtomicRequestStore()
  old_token = await store.claim_market_data_request("request-1")
  assert old_token == "claim-1"
  store.claim_token = "claim-2"

  assert (
    await store.renew_market_data_request_claim(
      "request-1",
      claim_token=old_token,
    )
    is False
  )
  assert (
    await store.release_market_data_request_claim(
      "request-1",
      claim_token=old_token,
      error="stale owner",
    )
    is False
  )
  with pytest.raises(RuntimeError, match="claim was lost"):
    await store.finish_market_data_request(
      "request-1",
      status="COMPLETED",
      ingestion_result={"records_received": 0, "records_saved": 0},
      claim_token=old_token,
    )

  assert store.status == "PROCESSING"
  assert store.claim_token == "claim-2"


@pytest.mark.asyncio
async def test_concurrent_consumers_only_ingest_one_claim() -> None:
  store = AtomicRequestStore()
  release_ingestion = asyncio.Event()
  entered_ingestion = asyncio.Event()
  ingestion_count = 0

  async def ingest(_store, _request_id):
    nonlocal ingestion_count
    ingestion_count += 1
    entered_ingestion.set()
    await release_ingestion.wait()
    return {
      "operation": "bars",
      "records_received": 1,
      "records_saved": 1,
    }

  first = asyncio.create_task(
    ingestion.claim_ingest_and_finish_market_data_request(
      store,
      "request-1",
      ingest_request=ingest,
    )
  )
  await entered_ingestion.wait()
  second = asyncio.create_task(
    ingestion.claim_ingest_and_finish_market_data_request(
      store,
      "request-1",
      ingest_request=ingest,
    )
  )
  await asyncio.sleep(0)
  release_ingestion.set()
  results = await asyncio.gather(first, second)

  assert ingestion_count == 1
  assert store.claim_count == 1
  assert sum(result is None for result in results) == 1
  assert sum(
    isinstance(result, dict) and result.get("status") == "completed"
    for result in results
  ) == 1


def test_completed_staging_cleanup_is_bound_to_authoritative_root(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  request_id = "11111111-1111-4111-8111-111111111111"
  runtime_root = tmp_path / "runtime"
  staging_root = runtime_root / "market-data"
  expected_directory = staging_root / request_id
  outside_directory = tmp_path / "outside" / request_id
  expected_directory.mkdir(parents=True)
  outside_directory.mkdir(parents=True)
  outside_file = outside_directory / "00000000.json.gz"
  outside_file.write_bytes(b"must remain")
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(runtime_root))

  ingestion._cleanup_completed_staging(
    request_id,
    [{"storage_reference": str(outside_file)}],
  )

  assert outside_file.read_bytes() == b"must remain"
  assert expected_directory.is_dir()


def test_completed_staging_cleanup_rejects_request_directory_reparse_point(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  request_id = "22222222-2222-4222-8222-222222222222"
  runtime_root = tmp_path / "runtime"
  request_directory = runtime_root / "market-data" / request_id
  request_directory.mkdir(parents=True)
  retained = request_directory / "00000000.json.gz"
  retained.write_bytes(b"must remain")
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(runtime_root))
  original_is_reparse_point = staging.is_reparse_point
  monkeypatch.setattr(
    staging,
    "is_reparse_point",
    lambda path: path == request_directory or original_is_reparse_point(path),
  )

  ingestion._cleanup_completed_staging(
    request_id,
    [{"storage_reference": str(retained)}],
  )

  assert retained.read_bytes() == b"must remain"


def test_completed_staging_cleanup_removes_only_verified_request_files(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  request_id = "33333333-3333-4333-8333-333333333333"
  runtime_root = tmp_path / "runtime"
  request_directory = runtime_root / "market-data" / request_id
  request_directory.mkdir(parents=True)
  staged = request_directory / "00000000.json.gz"
  staged.write_bytes(b"complete")
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(runtime_root))

  ingestion._cleanup_completed_staging(
    request_id,
    [{"storage_reference": str(staged)}],
  )

  assert not staged.exists()
  assert not request_directory.exists()


def test_market_data_staging_root_prefers_explicit_runtime_directory(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  runtime_root = tmp_path / "runtime"
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(runtime_root))
  monkeypatch.setenv("QUANTX_ROOT", str(tmp_path / "ignored-root"))

  assert staging.market_data_staging_root() == (
    runtime_root.resolve() / "market-data"
  )
