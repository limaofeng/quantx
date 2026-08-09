from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from quantx_research.data import (
  QMT_DAILY_BAR_ARCHIVE_FORMAT,
  QmtDailyBarArchiveError,
  QmtDailyBarArchiveResearchDataSource,
)


class FakeMetadataSource:
  def __init__(
    self,
    stock_codes: list[str],
    *,
    open_dates: dict[str, str] | None = None,
    expire_dates: dict[str, str] | None = None,
  ) -> None:
    self.stock_codes = stock_codes
    self.open_dates = open_dates or {}
    self.expire_dates = expire_dates or {}
    self.factor_calls = 0
    self.coverage_calls = 0

  async def list_instruments(
    self,
    *,
    instrument_types=("stock",),
    codes=None,
  ) -> pd.DataFrame:
    if "index" in instrument_types:
      available = ["000300.SH"]
      instrument_type = "index"
    else:
      available = self.stock_codes
      instrument_type = "stock"
    selected = [code for code in available if codes is None or code in codes]
    return pd.DataFrame(
      {
        "stock_code": selected,
        "instrument_type": [instrument_type] * len(selected),
        "name": selected,
        "market": [code[-2:] for code in selected],
        "open_date": [
          pd.Timestamp(self.open_dates.get(code, "1991-01-01"))
          for code in selected
        ],
        "expire_date": [
          pd.Timestamp(self.expire_dates[code])
          if code in self.expire_dates
          else pd.NaT
          for code in selected
        ],
      }
    )

  async def load_daily_bars(self, *args, **kwargs) -> pd.DataFrame:
    del args, kwargs
    raise AssertionError("archive source must not delegate daily bars")

  async def load_dividend_factors(
    self,
    stock_codes,
    *,
    start=None,
    end=None,
  ) -> pd.DataFrame:
    del stock_codes, start, end
    self.factor_calls += 1
    return pd.DataFrame(columns=["stock_code", "time", "dr"])

  async def load_dividend_factor_coverage(
    self,
    stock_codes,
    *,
    start,
    end,
  ) -> pd.DataFrame:
    del stock_codes, start, end
    self.coverage_calls += 1
    return pd.DataFrame()


def _record(
  code: str,
  day: str,
  *,
  close: float = 10.12349,
  volume: float = 100.126,
) -> dict[str, Any]:
  timestamp = int(pd.Timestamp(day, tz="UTC").timestamp() * 1000)
  return {
    "code": code,
    "period": "1d",
    "time": timestamp,
    "open": close - 0.1,
    "high": close + 0.2,
    "low": close - 0.2,
    "close": close,
    "volume": volume,
    "amount": volume * close,
    "suspendFlag": 0,
  }


def _write_archive(
  root: Path,
  request_specs: list[dict[str, Any]],
  *,
  universe_codes: list[str],
  status: str = "completed",
  expected_request_count: int | None = None,
) -> Path:
  entries: list[dict[str, Any]] = []
  for spec in request_specs:
    request_id = spec["request_id"]
    request_dir = root / "requests" / request_id
    request_dir.mkdir(parents=True)
    records = list(spec["records"])
    compressed = gzip.compress(
      json.dumps(records, sort_keys=True).encode("utf-8"),
      mtime=0,
    )
    chunk_path = request_dir / "00000000.json.gz"
    chunk_path.write_bytes(compressed)
    codes = sorted(spec["codes"])
    start_date = spec["start_date"]
    end_date = spec["end_date"]
    payload = {
      "operation": "bars",
      "download": True,
      "stock_list": codes,
      "periods": ["1d"],
      "start_time": start_date,
      "end_time": end_date,
    }
    keys = sorted(
      f"{record['code']}|{int(record['time'])}" for record in records
    )
    entry = {
      "job_id": spec["job_id"],
      "request_id": request_id,
      "kind": spec["kind"],
      "codes": codes,
      "start_date": start_date,
      "end_date": end_date,
      "payload": payload,
      "payload_sha256": _canonical_json_sha256(payload),
      "expected_chunks": 1,
      "received_chunks": 1,
      "record_count": len(records),
      "symbol_count": len({record["code"] for record in records}),
      "source_key_sha256": _text_sha256("\n".join(keys)),
      "archived_at": "2026-07-30T12:00:00+08:00",
      "chunks": [
        {
          "index": 0,
          "path": f"requests/{request_id}/00000000.json.gz",
          "sha256": hashlib.sha256(compressed).hexdigest(),
          "records": len(records),
          "compressed": True,
          "bytes": len(compressed),
        }
      ],
    }
    (request_dir / "manifest.json").write_text(
      json.dumps(entry, ensure_ascii=False, indent=2, sort_keys=True),
      encoding="utf-8",
    )
    entries.append(entry)

  plan = [
    {
      "job_id": entry["job_id"],
      "kind": entry["kind"],
      "codes": entry["codes"],
      "start_date": entry["start_date"],
      "end_date": entry["end_date"],
    }
    for entry in sorted(entries, key=lambda item: item["job_id"])
  ]
  expected = (
    len(entries) if expected_request_count is None else expected_request_count
  )
  ledger = {
    "schema_version": 1,
    "archive_format": QMT_DAILY_BAR_ARCHIVE_FORMAT,
    "status": status,
    "expected_request_count": expected,
    "effective_job_count": expected,
    "job_plan_sha256": _canonical_json_sha256(plan),
    "campaign": {
      "run_key": "campaign-1",
      "start_date": min(entry["start_date"] for entry in entries),
      "end_date": max(entry["end_date"] for entry in entries),
      "universe_sha256": _text_sha256("\n".join(sorted(universe_codes))),
    },
    "summary": {
      "request_count": len(entries),
      "chunk_count": sum(len(entry["chunks"]) for entry in entries),
      "record_count": sum(entry["record_count"] for entry in entries),
    },
    "requests": entries,
  }
  root.mkdir(parents=True, exist_ok=True)
  ledger_path = root / "ledger.json"
  ledger_path.write_text(
    json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
  )
  return ledger_path


def _spec(
  *,
  suffix: int,
  code: str = "000001.SZ",
  kind: str = "stocks",
  start_date: str = "20240101",
  end_date: str = "20240103",
  records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
  return {
    "job_id": f"{kind}-{start_date}-{end_date}-{suffix}",
    "request_id": f"00000000-0000-4000-8000-{suffix:012d}",
    "kind": kind,
    "codes": [code],
    "start_date": start_date,
    "end_date": end_date,
    "records": records
    or [
      _record(code, "2024-01-02"),
      _record(code, "2024-01-03", close=10.56789),
    ],
  }


@pytest.mark.asyncio
async def test_archive_source_reads_worker_compatible_bars_and_provenance(
  tmp_path: Path,
) -> None:
  ledger = _write_archive(
    tmp_path / "archive",
    [_spec(suffix=1)],
    universe_codes=["000001.SZ"],
  )
  metadata = FakeMetadataSource(["000001.SZ"])
  source = QmtDailyBarArchiveResearchDataSource(
    ledger,
    metadata_source=metadata,
    required_request_count=1,
  )

  instruments = await source.list_instruments(instrument_types=("stock",))
  bars = await source.load_daily_bars(
    ["000001.sz"],
    date(2024, 1, 1),
    date(2024, 1, 4),
  )
  factors = await source.load_dividend_factors(["000001.SZ"])

  assert instruments["stock_code"].tolist() == ["000001.SZ"]
  assert bars["stock_code"].tolist() == ["000001.SZ", "000001.SZ"]
  assert bars["time"].dt.strftime("%Y-%m-%d").tolist() == [
    "2024-01-02",
    "2024-01-03",
  ]
  assert bars["close"].tolist() == [10.123, 10.568]
  assert bars["volume"].tolist() == [100.13, 100.13]
  assert factors.empty
  assert metadata.factor_calls == 1

  provenance = source.provenance
  assert provenance["metadata_universe_validated"] is True
  assert provenance["selected_request_count"] == 1
  assert provenance["selected_chunk_count"] == 1
  assert provenance["selected_source_record_count"] == 2
  assert provenance["emitted_rows"] == 2
  assert provenance["queries"][0]["boundary_truncated"] is True
  assert len(provenance["ledger_sha256"]) == 64
  assert len(provenance["requests"][0]["manifest_file_sha256"]) == 64


@pytest.mark.asyncio
async def test_archive_source_rejects_internal_date_coverage_gap(
  tmp_path: Path,
) -> None:
  first = _spec(
    suffix=1,
    start_date="20240101",
    end_date="20240102",
    records=[_record("000001.SZ", "2024-01-02")],
  )
  second = _spec(
    suffix=2,
    start_date="20240104",
    end_date="20240105",
    records=[_record("000001.SZ", "2024-01-04")],
  )
  ledger = _write_archive(
    tmp_path / "archive",
    [first, second],
    universe_codes=["000001.SZ"],
  )
  source = QmtDailyBarArchiveResearchDataSource(
    ledger,
    metadata_source=FakeMetadataSource(["000001.SZ"]),
    required_request_count=2,
  )

  with pytest.raises(QmtDailyBarArchiveError, match="内部日期缺口"):
    await source.load_daily_bars(
      ["000001.SZ"],
      date(2024, 1, 1),
      date(2024, 1, 5),
    )


@pytest.mark.asyncio
async def test_archive_source_allows_pre_ipo_query_boundary(
  tmp_path: Path,
) -> None:
  code = "001201.SZ"
  ledger = _write_archive(
    tmp_path / "archive",
    [
      _spec(
        suffix=1,
        code=code,
        start_date="20210101",
        end_date="20211231",
        records=[_record(code, "2021-06-10")],
      )
    ],
    universe_codes=[code],
  )
  source = QmtDailyBarArchiveResearchDataSource(
    ledger,
    metadata_source=FakeMetadataSource(
      [code],
      open_dates={code: "2021-06-10"},
    ),
    required_request_count=1,
  )

  await source.list_instruments(instrument_types=("stock",))
  bars = await source.load_daily_bars(
    [code],
    date(2020, 3, 13),
    date(2021, 12, 31),
  )

  assert bars["stock_code"].tolist() == [code]
  query = source.provenance["queries"][0]
  assert query["available_start"] == "2020-03-13"
  assert query["available_end"] == "2021-12-31"
  assert query["boundary_truncated"] is False
  assert query["lifecycle_adjusted_code_count"] == 1


@pytest.mark.asyncio
async def test_archive_source_rejects_tampered_chunk(
  tmp_path: Path,
) -> None:
  archive = tmp_path / "archive"
  ledger = _write_archive(
    archive,
    [_spec(suffix=1)],
    universe_codes=["000001.SZ"],
  )
  source = QmtDailyBarArchiveResearchDataSource(
    ledger,
    metadata_source=FakeMetadataSource(["000001.SZ"]),
    required_request_count=1,
  )
  chunk = next((archive / "requests").rglob("*.json.gz"))
  chunk.write_bytes(chunk.read_bytes() + b"tampered")

  with pytest.raises(QmtDailyBarArchiveError, match="字节数不匹配"):
    await source.load_daily_bars(
      ["000001.SZ"],
      date(2024, 1, 1),
      date(2024, 1, 3),
    )


@pytest.mark.asyncio
async def test_archive_source_rejects_duplicate_daily_key(
  tmp_path: Path,
) -> None:
  duplicated = _record("000001.SZ", "2024-01-02")
  ledger = _write_archive(
    tmp_path / "archive",
    [
      _spec(
        suffix=1,
        records=[duplicated, dict(duplicated)],
      )
    ],
    universe_codes=["000001.SZ"],
  )
  source = QmtDailyBarArchiveResearchDataSource(
    ledger,
    metadata_source=FakeMetadataSource(["000001.SZ"]),
    required_request_count=1,
  )

  with pytest.raises(QmtDailyBarArchiveError, match="重复日线键"):
    await source.load_daily_bars(
      ["000001.SZ"],
      date(2024, 1, 1),
      date(2024, 1, 3),
    )


@pytest.mark.asyncio
async def test_archive_source_rejects_current_universe_drift(
  tmp_path: Path,
) -> None:
  ledger = _write_archive(
    tmp_path / "archive",
    [_spec(suffix=1)],
    universe_codes=["000001.SZ"],
  )
  source = QmtDailyBarArchiveResearchDataSource(
    ledger,
    metadata_source=FakeMetadataSource(["000001.SZ", "000002.SZ"]),
    required_request_count=1,
  )

  with pytest.raises(QmtDailyBarArchiveError, match="证券总体"):
    await source.list_instruments(instrument_types=("stock",))


def test_archive_source_rejects_partial_nonterminal_ledger(
  tmp_path: Path,
) -> None:
  ledger = _write_archive(
    tmp_path / "archive",
    [_spec(suffix=1)],
    universe_codes=["000001.SZ"],
    status="in_progress",
    expected_request_count=180,
  )

  with pytest.raises(QmtDailyBarArchiveError, match="尚未 completed"):
    QmtDailyBarArchiveResearchDataSource(
      ledger,
      metadata_source=FakeMetadataSource(["000001.SZ"]),
    )


def test_archive_source_default_requires_full_180_request_plan(
  tmp_path: Path,
) -> None:
  ledger = _write_archive(
    tmp_path / "archive",
    [_spec(suffix=1)],
    universe_codes=["000001.SZ"],
  )

  with pytest.raises(QmtDailyBarArchiveError, match="全量任务门禁"):
    QmtDailyBarArchiveResearchDataSource(
      ledger,
      metadata_source=FakeMetadataSource(["000001.SZ"]),
    )


def test_archive_source_rejects_noncanonical_chunk_path(
  tmp_path: Path,
) -> None:
  ledger_path = _write_archive(
    tmp_path / "archive",
    [_spec(suffix=1)],
    universe_codes=["000001.SZ"],
  )
  ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
  ledger["requests"][0]["chunks"][0]["path"] = "../outside.json.gz"
  ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

  with pytest.raises(QmtDailyBarArchiveError, match="path 非 canonical"):
    QmtDailyBarArchiveResearchDataSource(
      ledger_path,
      metadata_source=FakeMetadataSource(["000001.SZ"]),
      required_request_count=1,
    )


def _canonical_json_sha256(value: Any) -> str:
  encoded = json.dumps(
    value,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _text_sha256(value: str) -> str:
  return hashlib.sha256(value.encode("utf-8")).hexdigest()
