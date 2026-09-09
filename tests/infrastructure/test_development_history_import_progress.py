"""Range requests submit every partition and expose incomplete progress."""

import json
from datetime import date
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services import development_history_import as importer
from quantx_infrastructure.services.holiday_service import HolidayService


@pytest.mark.parametrize("first_status", ["INCOMPLETE", "WAITING_SOURCE", "BLOCKED"])
async def test_range_submits_later_partitions_and_reports_failures(monkeypatch, first_status):
  calls = []

  async def holidays(*args, **kwargs):
    return [SimpleNamespace(date=date(2026, 1, 1))]

  async def partition(request):
    calls.append(request)
    if len(calls) == 1:
      return {"id": "first-id", "status": first_status, "reason": "SOURCE_COVERAGE_MISSING"}
    return {"local_verification": {"records_verified": 3}}

  monkeypatch.setattr(HolidayService, "get_holidays", holidays)
  monkeypatch.setattr(importer, "import_partition", partition)
  result = await importer.request_remote_history(
    {"operation": "bars", "stock_list": ["000001.SZ", "600036.SH"],
     "periods": ["tick"], "start_time": "20260810", "end_time": "20260810"},
    timeout_seconds=0,
  )
  assert len(calls) == 2
  assert result["expected_partitions"] == 2
  assert result["verified_partitions"] == 1
  assert result["status"] == ("failed" if first_status in {"INCOMPLETE", "BLOCKED"} else "timeout")
  assert result["partitions"][0]["id"] == "first-id"
  assert result["partitions"][0]["reason"] == "SOURCE_COVERAGE_MISSING"
  assert result["partitions"][0]["trading_date"] == "2026-08-10"
  assert result["partitions"][1]["status"] == "LOCAL_VERIFIED"


@pytest.mark.parametrize("status,exit_code", [("timeout", 0), ("failed", 2)])
async def test_cli_preserves_partition_progress(monkeypatch, capsys, status, exit_code):
  expected = {"status": status, "expected_partitions": 2,
              "verified_partitions": 1, "partitions": [{"id": "pending-id"}]}

  async def request(*args, **kwargs):
    return expected

  monkeypatch.setattr(importer, "request_remote_history", request)
  assert await importer.run_range(["000001.SZ"], "tick", date(2026, 8, 10), date(2026, 8, 10)) == exit_code
  assert json.loads(capsys.readouterr().out) == expected
