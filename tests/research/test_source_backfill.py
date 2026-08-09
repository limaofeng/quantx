from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from quantx_research import source_backfill
from quantx_research.data.qmt_archive_source import (
  QmtDailyBarArchiveResearchDataSource,
)

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
CODE = "000001.SZ"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _time_ms(year: int, month: int, day: int) -> int:
  return int(
    datetime(year, month, day, tzinfo=SHANGHAI).timestamp() * 1000
  )


def _record(day: int = 2) -> dict[str, object]:
  return {
    "code": CODE,
    "period": "1d",
    "index": f"202001{day:02d}",
    "time": _time_ms(2020, 1, day),
    "open": 10.0,
    "high": 11.0,
    "low": 9.5,
    "close": 10.5,
    "volume": 1000,
    "amount": 10500.0,
    "suspendFlag": 0,
  }


def _job(*, status: str = "pending") -> dict[str, object]:
  return {
    "id": "stocks-20200101-20200131-test",
    "kind": "stocks",
    "codes": [CODE],
    "start_date": "20200101",
    "end_date": "20200131",
    "status": status,
  }


def _transfer(
  root: Path,
  *,
  records_by_chunk: list[list[dict[str, object]]] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
  chunks = records_by_chunk or [[_record(2)], [_record(3)]]
  source = root / "market-data" / REQUEST_ID
  source.mkdir(parents=True)
  manifest: list[dict[str, object]] = []
  for index, records in enumerate(chunks):
    payload = gzip.compress(
      json.dumps(records, separators=(",", ":")).encode("utf-8")
    )
    path = source / f"{index:08d}.json.gz"
    path.write_bytes(payload)
    manifest.append(
      {
        "chunk_index": index,
        "checksum_sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": len(records),
        "compressed": True,
        "storage_reference": str(path),
      }
    )
  request = {
    "request_id": REQUEST_ID,
    "request_payload": source_backfill._request_payload(_job()),
    "status": "UPLOADED",
    "expected_chunks": len(manifest),
    "received_chunks": len(manifest),
    "processing_error": None,
  }
  return request, manifest


def _campaign() -> dict[str, object]:
  plan = [
    {
      "job_id": _job()["id"],
      "kind": _job()["kind"],
      "codes": _job()["codes"],
      "start_date": _job()["start_date"],
      "end_date": _job()["end_date"],
    }
  ]
  return {
    "source_state_path": "state.json",
    "source_state_sha256_at_load": "a" * 64,
    "run_key": "campaign-run",
    "start_date": "20200101",
    "end_date": "20200131",
    "universe_sha256": hashlib.sha256(CODE.encode()).hexdigest(),
    "job_plan_sha256": source_backfill._canonical_json_sha256(plan),
    "jobs": [_job()],
  }


def test_atomic_ledger_write_retries_transient_windows_reader_lock(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  target = tmp_path / "ledger.json"
  target.write_text('{"old":true}', encoding="utf-8")
  real_replace = source_backfill.os.replace
  attempts = 0

  def flaky_replace(source: Path, destination: Path) -> None:
    nonlocal attempts
    attempts += 1
    if attempts < 3:
      raise PermissionError(5, "destination is temporarily open")
    real_replace(source, destination)

  monkeypatch.setattr(source_backfill.os, "replace", flaky_replace)
  monkeypatch.setattr(source_backfill.time, "sleep", lambda _seconds: None)

  source_backfill._atomic_write_json(target, {"new": True})

  assert attempts == 3
  assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
  assert list(tmp_path.glob("*.tmp")) == []


def test_archive_publish_retries_transient_windows_file_lock(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  archive_root = tmp_path / "archive"
  archive_root.mkdir()
  request, manifest = _transfer(tmp_path)
  source_root = (tmp_path / "market-data").resolve()
  real_replace = source_backfill.os.replace
  attempts = 0

  def flaky_replace(source: Path, destination: Path) -> None:
    nonlocal attempts
    attempts += 1
    if attempts < 3:
      raise PermissionError(5, "fresh archive file is temporarily open")
    real_replace(source, destination)

  monkeypatch.setattr(source_backfill.os, "replace", flaky_replace)
  monkeypatch.setattr(source_backfill.time, "sleep", lambda _seconds: None)

  entry = source_backfill.archive_request(
    archive_root=archive_root,
    source_root=source_root,
    job=_job(),
    request=request,
    manifest=manifest,
  )

  assert attempts == 3
  assert entry["request_id"] == REQUEST_ID
  assert (archive_root / "requests" / REQUEST_ID / "manifest.json").is_file()
  assert list((archive_root / ".staging").iterdir()) == []


@pytest.mark.asyncio
async def test_archive_request_is_loader_compatible_and_resumable(
  tmp_path: Path,
) -> None:
  archive_root = tmp_path / "archive"
  archive_root.mkdir()
  request, manifest = _transfer(tmp_path)
  source_root = (tmp_path / "market-data").resolve()

  entry = source_backfill.archive_request(
    archive_root=archive_root,
    source_root=source_root,
    job=_job(),
    request=request,
    manifest=manifest,
  )
  ledger = source_backfill._ledger_template(_campaign())
  source_backfill.publish_ledger_entry(
    archive_root=archive_root,
    ledger=ledger,
    entry=entry,
  )

  assert ledger["status"] == "completed"
  assert ledger["summary"] == {
    "request_count": 1,
    "chunk_count": 2,
    "record_count": 2,
  }
  assert entry["source_key_sha256"] == hashlib.sha256(
    (
      f"{CODE}|{_time_ms(2020, 1, 2)}\n"
      f"{CODE}|{_time_ms(2020, 1, 3)}"
    ).encode()
  ).hexdigest()
  manifest_file = archive_root / "requests" / REQUEST_ID / "manifest.json"
  assert json.loads(manifest_file.read_text(encoding="utf-8")) == entry
  assert json.loads(
    (archive_root / "ledger.json").read_text(encoding="utf-8")
  )["requests"] == [entry]

  data_source = QmtDailyBarArchiveResearchDataSource(
    archive_root,
    metadata_source=object(),
    required_request_count=1,
  )
  loaded = await data_source.load_daily_bars(
    [CODE],
    datetime(2020, 1, 1, tzinfo=SHANGHAI),
    datetime(2020, 1, 31, tzinfo=SHANGHAI),
  )
  assert len(loaded) == 2

  resumed = source_backfill.archive_request(
    archive_root=archive_root,
    source_root=source_root,
    job=_job(),
    request=request,
    manifest=manifest,
  )
  assert resumed == entry


def test_archive_request_rejects_duplicate_daily_key(tmp_path: Path) -> None:
  archive_root = tmp_path / "archive"
  archive_root.mkdir()
  duplicate = _record(2)
  request, manifest = _transfer(
    tmp_path,
    records_by_chunk=[[duplicate], [dict(duplicate)]],
  )

  with pytest.raises(
    source_backfill.SourceBackfillError,
    match="重复日线键",
  ):
    source_backfill.archive_request(
      archive_root=archive_root,
      source_root=(tmp_path / "market-data").resolve(),
      job=_job(),
      request=request,
      manifest=manifest,
    )

  assert not (archive_root / "requests" / REQUEST_ID).exists()


def test_archive_request_rejects_payload_and_checksum_mismatch(
  tmp_path: Path,
) -> None:
  archive_root = tmp_path / "archive"
  archive_root.mkdir()
  request, manifest = _transfer(tmp_path)
  request["request_payload"] = {
    **request["request_payload"],
    "end_time": "20200201",
  }
  with pytest.raises(
    source_backfill.SourceBackfillError,
    match="payload",
  ):
    source_backfill.archive_request(
      archive_root=archive_root,
      source_root=(tmp_path / "market-data").resolve(),
      job=_job(),
      request=request,
      manifest=manifest,
    )

  request["request_payload"] = source_backfill._request_payload(_job())
  manifest[0]["checksum_sha256"] = "0" * 64
  with pytest.raises(
    source_backfill.SourceBackfillError,
    match="SHA256",
  ):
    source_backfill.archive_request(
      archive_root=archive_root,
      source_root=(tmp_path / "market-data").resolve(),
      job=_job(),
      request=request,
      manifest=manifest,
    )


def test_archive_request_rejects_storage_reference_outside_canonical_root(
  tmp_path: Path,
) -> None:
  archive_root = tmp_path / "archive"
  archive_root.mkdir()
  request, manifest = _transfer(tmp_path)
  canonical = Path(str(manifest[0]["storage_reference"]))
  escaped = tmp_path / "outside" / REQUEST_ID / canonical.name
  escaped.parent.mkdir(parents=True)
  escaped.write_bytes(canonical.read_bytes())
  manifest[0]["storage_reference"] = str(escaped)

  with pytest.raises(
    source_backfill.SourceBackfillError,
    match="逃逸 canonical market-data root",
  ):
    source_backfill.archive_request(
      archive_root=archive_root,
      source_root=(tmp_path / "market-data").resolve(),
      job=_job(),
      request=request,
      manifest=manifest,
    )


def test_archive_request_uses_short_staging_names_on_windows(
  tmp_path: Path,
) -> None:
  padding = max(8, 170 - len(str(tmp_path)) - 1)
  archive_root = tmp_path / ("a" * padding)
  archive_root.mkdir()
  request, manifest = _transfer(tmp_path)

  entry = source_backfill.archive_request(
    archive_root=archive_root,
    source_root=(tmp_path / "market-data").resolve(),
    job=_job(),
    request=request,
    manifest=manifest,
  )

  assert entry["request_id"] == REQUEST_ID
  assert (
    archive_root / "requests" / REQUEST_ID / "manifest.json"
  ).is_file()
  assert list((archive_root / ".staging").iterdir()) == []


def test_load_campaign_excludes_superseded_and_freezes_plan(
  tmp_path: Path,
) -> None:
  second_code = "000002.SZ"
  jobs = [
    {
      **_job(),
      "id": "superseded-parent",
      "codes": [CODE, second_code],
      "status": "superseded",
    },
    {
      **_job(),
      "id": "stocks-child-a",
      "kind": "stocks-retry",
      "codes": [CODE],
    },
    {
      **_job(),
      "id": "stocks-child-b",
      "kind": "stocks-retry",
      "codes": [second_code],
    },
  ]
  universe_codes = [CODE, second_code]
  state = {
    "run_key": "run-key",
    "start_date": "20200101",
    "end_date": "20200131",
    "universe": {
      "code_sha256": hashlib.sha256(
        "\n".join(universe_codes).encode()
      ).hexdigest()
    },
    "jobs": jobs,
  }
  state_path = tmp_path / "state.json"
  original = json.dumps(state, ensure_ascii=False, indent=2)
  state_path.write_text(original, encoding="utf-8")

  campaign = source_backfill.load_campaign(state_path)

  assert [job["id"] for job in campaign["jobs"]] == [
    "stocks-child-a",
    "stocks-child-b",
  ]
  assert state_path.read_text(encoding="utf-8") == original
  canonical_plan = sorted(
    [
      {
        "job_id": "stocks-child-a",
        "kind": "stocks-retry",
        "codes": [CODE],
        "start_date": "20200101",
        "end_date": "20200131",
      },
      {
        "job_id": "stocks-child-b",
        "kind": "stocks-retry",
        "codes": [second_code],
        "start_date": "20200101",
        "end_date": "20200131",
      },
    ],
    key=lambda item: item["job_id"],
  )
  assert campaign["job_plan_sha256"] == hashlib.sha256(
    json.dumps(
      canonical_plan,
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode()
  ).hexdigest()


def test_load_campaign_rejects_overlapping_effective_jobs(
  tmp_path: Path,
) -> None:
  state = {
    "run_key": "run-key",
    "start_date": "20200101",
    "end_date": "20200131",
    "universe": {"code_sha256": "a" * 64},
    "jobs": [
      _job(),
      {
        **_job(),
        "id": "overlap",
        "start_date": "20200115",
      },
    ],
  }
  path = tmp_path / "state.json"
  path.write_text(json.dumps(state), encoding="utf-8")

  with pytest.raises(
    source_backfill.SourceBackfillError,
    match="重叠",
  ):
    source_backfill.load_campaign(path)


@pytest.mark.asyncio
async def test_reconcile_uploaded_archive_finishes_failed_without_influx(
  tmp_path: Path,
) -> None:
  archive_root = tmp_path / "archive"
  manifest_dir = archive_root / "requests" / REQUEST_ID
  manifest_dir.mkdir(parents=True)
  (manifest_dir / "manifest.json").write_text("{}", encoding="utf-8")
  payload = source_backfill._request_payload(_job())

  class Store:
    status = "UPLOADED"
    finish_call: tuple[str, str, str] | None = None

    async def market_data_request(self, request_id):
      assert request_id == REQUEST_ID
      return {
        "request_payload": payload,
        "status": self.status,
      }

    async def finish_market_data_request(
      self,
      request_id,
      *,
      status,
      error="",
    ):
      self.finish_call = (request_id, status, error)
      self.status = status

    async def market_data_request_status(self, request_id):
      assert request_id == REQUEST_ID
      return self.status

  store = Store()
  await source_backfill._reconcile_archived_terminal(
    store,
    {"request_id": REQUEST_ID, "payload": payload},
    archive_root=archive_root,
  )

  assert store.finish_call is not None
  assert store.finish_call[:2] == (REQUEST_ID, "FAILED")
  assert "SOURCE_ONLY_ARCHIVED_NO_INFLUX" in store.finish_call[2]
  assert "no Influx ingestion was attempted" in store.finish_call[2]


@pytest.mark.asyncio
async def test_process_job_creates_data_only_request_then_archives(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  archive_root = tmp_path / "archive"
  archive_root.mkdir()
  request, manifest = _transfer(tmp_path)
  campaign = _campaign()
  ledger = source_backfill._ledger_template(campaign)

  class Store:
    status = "UPLOADED"
    created: tuple[dict[str, object], str] | None = None
    finish_call: tuple[str, str, str] | None = None

    async def create_market_data_request(self, payload, *, device_id):
      self.created = (payload, device_id)
      return REQUEST_ID

    async def market_data_request(self, request_id):
      return {**request, "status": self.status}

    async def market_data_transfers(self, request_id):
      return manifest

    async def finish_market_data_request(
      self,
      request_id,
      *,
      status,
      error="",
    ):
      self.finish_call = (request_id, status, error)
      self.status = status

    async def market_data_request_status(self, request_id):
      return self.status

  async def find_request(store, payload):
    return None

  async def no_queue(*args, **kwargs):
    return None

  async def data_only(store, *, max_age_seconds):
    return "data-only-device"

  monkeypatch.setattr(
    source_backfill,
    "_find_request_by_idempotency",
    find_request,
  )
  monkeypatch.setattr(
    source_backfill,
    "_wait_for_unrelated_queue",
    no_queue,
  )
  monkeypatch.setattr(source_backfill, "_data_only_agent", data_only)

  store = Store()
  outcome = await source_backfill._process_job(
    store,
    archive_root=archive_root,
    source_root=(tmp_path / "market-data").resolve(),
    ledger=ledger,
    job=_job(),
    poll_seconds=0.01,
    request_timeout_seconds=1,
    queue_timeout_seconds=1,
    agent_max_age_seconds=90,
    existing_only=False,
    dry_run=False,
  )

  assert outcome == "archived"
  assert store.created is not None
  assert store.created[1] == "data-only-device"
  assert store.finish_call is not None
  assert store.finish_call[1] == "FAILED"
  assert ledger["status"] == "completed"


@pytest.mark.asyncio
async def test_process_job_dry_run_scans_without_writes(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  archive_root = tmp_path / "does-not-exist"
  request, manifest = _transfer(tmp_path)
  ledger = source_backfill._ledger_template(_campaign())

  class Store:
    async def market_data_request(self, request_id):
      assert request_id == REQUEST_ID
      return request

    async def market_data_transfers(self, request_id):
      assert request_id == REQUEST_ID
      return manifest

  async def find_request(store, payload):
    return REQUEST_ID

  monkeypatch.setattr(
    source_backfill,
    "_find_request_by_idempotency",
    find_request,
  )

  outcome = await source_backfill._process_job(
    Store(),
    archive_root=archive_root,
    source_root=(tmp_path / "market-data").resolve(),
    ledger=ledger,
    job=_job(),
    poll_seconds=0.01,
    request_timeout_seconds=1,
    queue_timeout_seconds=1,
    agent_max_age_seconds=90,
    existing_only=True,
    dry_run=True,
  )

  assert outcome == "validated"
  assert not archive_root.exists()
  assert ledger["requests"] == []


def test_parse_args_requires_positive_limits() -> None:
  with pytest.raises(SystemExit):
    source_backfill.parse_args(
      [
        "--state-file",
        "state.json",
        "--archive-root",
        "archive",
        "--max-jobs",
        "0",
      ]
    )
  args = source_backfill.parse_args(
    [
      "--state-file",
      "state.json",
      "--archive-root",
      "archive",
      "--existing-only",
      "--dry-run",
    ]
  )
  assert args.existing_only is True
  assert args.dry_run is True
