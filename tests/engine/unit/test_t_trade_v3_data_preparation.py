from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import quantx_engine.t_trade_v3_data_preparation as preparation


def _trading_dates() -> tuple[date, ...]:
  start = date(2026, 7, 22)
  result: list[date] = []
  current = start
  while len(result) < 20:
    if current.weekday() < 5:
      result.append(current)
    current += timedelta(days=1)
  return tuple(result)


def test_request_windows_never_exceed_seven_calendar_days() -> None:
  dates = _trading_dates()

  windows = preparation._request_windows(dates)

  assert windows[0][0] == dates[0]
  assert windows[-1][1] == dates[-1]
  assert all((end - start).days + 1 <= 7 for start, end in windows)
  assert [day for day in dates if any(start <= day <= end for start, end in windows)] == list(
    dates
  )


@pytest.mark.asyncio
async def test_preparation_fails_before_request_when_qmt_agent_is_unavailable(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  requested = False

  async def unavailable() -> str:
    raise preparation.CanonicalTickPreparationError(
      "QMT_MARKET_DATA_AGENT_UNAVAILABLE"
    )

  async def unexpected_request(**_kwargs):
    nonlocal requested
    requested = True
    raise AssertionError("offline preparation must not create a request")

  monkeypatch.setattr(preparation, "_require_market_data_agent", unavailable)
  monkeypatch.setattr(preparation, "request_canonical_tick_sync", unexpected_request)

  with pytest.raises(
    preparation.CanonicalTickPreparationError,
    match="QMT_MARKET_DATA_AGENT_UNAVAILABLE",
  ):
    await preparation.prepare_canonical_tick_archive(
      snapshot_date=date(2026, 7, 21),
      instrument_codes=["600000.SH"],
      trading_dates=_trading_dates(),
      archive_root=tmp_path / "archive",
      timeout_seconds=30,
    )

  assert requested is False


@pytest.mark.asyncio
async def test_market_data_agent_preflight_reports_blocked_durable_ingestion(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class FakeStore:
    closed = False

    async def available_market_data_device(self):
      return "device-1"

    async def blocked_market_data_ingestion(self, device_id):
      assert device_id == "device-1"
      return {"request_id": "blocked-request", "status": "UPLOADED"}

    async def close(self):
      self.closed = True

  store = FakeStore()
  monkeypatch.setattr(preparation, "DurableRuntimeStore", lambda: store)

  with pytest.raises(
    preparation.CanonicalTickPreparationError,
    match="QMT_MARKET_DATA_INGESTION_BLOCKED.*blocked-request",
  ):
    await preparation._require_market_data_agent()

  assert store.closed is True


@pytest.mark.asyncio
async def test_preparation_acquires_twice_and_publishes_exact_verified_scope(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  dates = _trading_dates()
  requests: list[dict] = []
  inspections: list[tuple[str, date]] = []
  preparation_root = tmp_path / "preparation"
  monkeypatch.setenv(
    "QUANTX_CANONICAL_TICK_PREPARATION_DIR",
    str(preparation_root),
  )

  async def available() -> str:
    return "device-1"

  async def request(**kwargs):
    requests.append(dict(kwargs))
    window_start = date.fromisoformat(
      f"{kwargs['start_time'][:4]}-{kwargs['start_time'][4:6]}-{kwargs['start_time'][6:]}"
    )
    window_end = date.fromisoformat(
      f"{kwargs['end_time'][:4]}-{kwargs['end_time'][4:6]}-{kwargs['end_time'][6:]}"
    )
    files = []
    for trading_date in dates:
      if window_start <= trading_date <= window_end:
        stamp = int(
          (
            date.toordinal(trading_date)
            - date.toordinal(date(1970, 1, 1))
          )
          * 86_400_000
        )
        files.append(
          {
            "instrument_code": kwargs["stock_code"],
            "trading_date": trading_date.isoformat(),
            "record_count": 240,
            "content_sha256": f"{trading_date.day:064x}",
            "path": str(tmp_path / f"{trading_date}.ndjson"),
            "first_source_identity": [0, stamp + 1, 0],
            "last_source_identity": [0, stamp + 2, 0],
          }
        )
    return {
      "status": "success",
      "request_id": f"request-{len(requests)}",
      "records_received": 240 * len(files),
      "records_verified": 240 * len(files),
      "canonical_tick_files": files,
    }

  class FakeReader:
    source_manifest_sha256 = "c" * 64

    def validate_formal_scope(self, **kwargs) -> None:
      assert kwargs["snapshot_date"] == date(2026, 7, 21)
      assert kwargs["instrument_codes"] == ["600000.SH"]
      assert tuple(kwargs["trading_dates"]) == dates

    def inspect_tick_day(self, *, instrument_code, trading_date):
      inspections.append((instrument_code, trading_date))
      return {
        "instrument_code": instrument_code,
        "date": trading_date.isoformat(),
        "complete": True,
        "reason_codes": [],
      }

  class FakeArchive:
    def __init__(self, root, *, create=True) -> None:
      assert Path(root) == tmp_path / "archive"
      self.create = create

    def publish(self, *, source_manifest, records):
      assert Path(source_manifest).is_file()
      assert len(records) == 20
      return SimpleNamespace(
        token="canonical-tick-v1-test",
        manifest_fingerprint="d" * 64,
      )

    def open(self, token):
      assert self.create is False
      assert token == "canonical-tick-v1-test"
      return FakeReader()

  monkeypatch.setattr(preparation, "_require_market_data_agent", available)
  monkeypatch.setattr(preparation, "request_canonical_tick_sync", request)
  monkeypatch.setattr(preparation, "CanonicalTickArchive", FakeArchive)

  result = await preparation.prepare_canonical_tick_archive(
    snapshot_date=date(2026, 7, 21),
    instrument_codes=["600000.SH"],
    trading_dates=dates,
    archive_root=tmp_path / "archive",
    timeout_seconds=30,
  )

  windows = preparation._request_windows(dates)
  assert len(requests) == len(windows) * 2
  assert {item["verification_pass"] for item in requests} == {1, 2}
  assert all(item["timeout_seconds"] == 30 for item in requests)
  assert inspections == [("600000.SH", trading_date) for trading_date in dates]
  assert result["status"] == "READY"
  assert result["synthetic"] is False
  assert result["verified_instrument_days"] == 20
  assert result["double_acquisition_match"] is True
  assert result["cutover_token"] == "canonical-tick-v1-test"


@pytest.mark.asyncio
async def test_preparation_rejects_second_acquisition_mismatch(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  dates = _trading_dates()
  monkeypatch.setenv(
    "QUANTX_CANONICAL_TICK_PREPARATION_DIR",
    str(tmp_path / "preparation"),
  )

  async def available() -> str:
    return "device-1"

  async def request(**kwargs):
    start = datetime_date(kwargs["start_time"])
    end = datetime_date(kwargs["end_time"])
    files = []
    for trading_date in dates:
      if start <= trading_date <= end:
        digest_digit = 2 if kwargs["verification_pass"] == 2 and trading_date == dates[0] else 1
        files.append(
          {
            "instrument_code": "600000.SH",
            "trading_date": trading_date.isoformat(),
            "record_count": 240,
            "content_sha256": f"{digest_digit:064x}",
            "path": str(tmp_path / f"{trading_date}.ndjson"),
            "first_source_identity": [0, 1, 0],
            "last_source_identity": [0, 2, 0],
          }
        )
    return {
      "status": "success",
      "request_id": "request",
      "canonical_tick_files": files,
    }

  monkeypatch.setattr(preparation, "_require_market_data_agent", available)
  monkeypatch.setattr(preparation, "request_canonical_tick_sync", request)

  with pytest.raises(
    preparation.CanonicalTickPreparationError,
    match="repeated acquisition is not deterministic",
  ):
    await preparation.prepare_canonical_tick_archive(
      snapshot_date=date(2026, 7, 21),
      instrument_codes=["600000.SH"],
      trading_dates=dates,
      archive_root=tmp_path / "archive",
      timeout_seconds=30,
    )


def datetime_date(value: str) -> date:
  return date(int(value[:4]), int(value[4:6]), int(value[6:]))
