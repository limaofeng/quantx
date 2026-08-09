from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_module():
  script_path = (
    Path(__file__).parents[2]
    / "apps"
    / "worker"
    / "scripts"
    / "backfill_divid_factors.py"
  )
  spec = importlib.util.spec_from_file_location(
    "backfill_divid_factors",
    script_path,
  )
  assert spec and spec.loader
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _agent_store(status: str):
  class Store:
    async def component_status(self, prefix):
      assert prefix == "qmt-agent:"
      return [
        {
          "status": status,
          "instance_id": "device-1",
          "updated_at": datetime.now(timezone.utc),
          "details": {
            "capabilities": [
              "market-data",
              "divid-factors",
              "data-only",
            ],
          },
        }
      ]

    async def close(self):
      return None

  return Store


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["READY", "RECONCILING"])
async def test_factor_readiness_accepts_fresh_data_only_status(
  monkeypatch,
  status,
):
  module = _load_module()
  monkeypatch.setattr(module, "DurableRuntimeStore", _agent_store(status))

  assert await module.ensure_factor_agent_ready() == "device-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "status",
  ["XTDATA_UNAVAILABLE", "EMERGENCY_STOP"],
)
async def test_factor_readiness_rejects_unavailable_or_stopped_agent(
  monkeypatch,
  status,
):
  module = _load_module()
  monkeypatch.setattr(module, "DurableRuntimeStore", _agent_store(status))

  with pytest.raises(RuntimeError, match="没有新鲜"):
    await module.ensure_factor_agent_ready()


def test_build_jobs_is_sorted_deduplicated_and_bounded():
  module = _load_module()

  jobs = module.build_jobs(
    ["600000.SH", "000001.SZ", "600000.SH", "000002.SZ"],
    batch_size=2,
  )

  assert [job["codes"] for job in jobs] == [
    ["000001.SZ", "000002.SZ"],
    ["600000.SH"],
  ]
  assert jobs[0]["status"] == "pending"
  assert jobs[0]["attempt"] == 0


def test_request_payload_changes_only_when_attempt_changes():
  module = _load_module()
  state = {
    "run_key": "campaign",
    "start_date": "20200313",
    "end_date": "20260729",
  }
  job = {
    "id": "job-1",
    "codes": ["600519.SH"],
    "attempt": 0,
  }

  first = module.request_payload(state, job)
  second = module.request_payload(state, job)
  job["attempt"] = 1
  retry = module.request_payload(state, job)

  assert first == second
  assert first["request_key"].endswith("attempt-0")
  assert retry["request_key"].endswith("attempt-1")
  assert module._request_idempotency_key(first) != (
    module._request_idempotency_key(retry)
  )


def test_retry_failed_jobs_is_explicit_auditable_and_uses_fresh_attempt():
  module = _load_module()
  state = {
    "run_key": "campaign",
    "start_date": "20200313",
    "end_date": "20260730",
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "failed",
        "attempt": 3,
        "attempt_limit": 3,
        "last_error": "RuntimeError: transfer failed",
      },
      {
        "id": "job-2",
        "codes": ["000001.SZ"],
        "status": "completed",
        "attempt": 0,
      },
    ],
  }

  retried = module._retry_failed_jobs(state, max_attempts=2)

  failed_job = state["jobs"][0]
  assert retried == ["job-1"]
  assert failed_job["status"] == "pending"
  assert failed_job["attempt"] == 3
  assert failed_job["attempt_limit"] == 5
  assert failed_job["last_error"] == "RuntimeError: transfer failed"
  assert failed_job["retry_history"] == [
    {
      "requested_at": failed_job["retry_requested_at"],
      "previous_status": "failed",
      "next_attempt": 3,
      "previous_attempt_limit": 3,
      "attempt_limit": 5,
      "last_error": "RuntimeError: transfer failed",
    }
  ]
  assert module.request_payload(state, failed_job)["request_key"].endswith(
    "attempt-3"
  )
  assert state["jobs"][1]["status"] == "completed"


@pytest.mark.parametrize("status", ["failed", "pending", "running", "unknown"])
def test_campaign_completion_rejects_every_non_completed_status(status):
  module = _load_module()
  state = {
    "jobs": [
      {"id": "completed", "status": "completed"},
      {"id": "incomplete", "status": status},
    ]
  }

  error = module._campaign_incomplete_error(state)

  assert "拒绝标记 completed" in error
  assert f"{status}=1" in error


@pytest.mark.asyncio
async def test_run_fails_without_silently_resetting_failed_jobs(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state = {
    "run_key": "campaign",
    "status": "paused",
    "start_date": "20200313",
    "end_date": "20260730",
    "universe": {"stock_count": 1},
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "failed",
        "attempt": 3,
        "last_error": "RuntimeError: transfer failed",
      }
    ],
  }

  class Lock:
    async def acquire(self):
      return None

    async def release(self):
      return None

  async def load_state(_args, _state_path):
    return state

  monkeypatch.setattr(module, "CampaignDatabaseLock", Lock)
  monkeypatch.setattr(module, "_load_or_create_state", load_state)
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 7, 30),
    state_file=str(tmp_path / "factor-state.json"),
    retry_failed=False,
    max_attempts=3,
  )

  result = await module.run(args)

  assert result == 2
  assert state["status"] == "failed"
  assert state["jobs"][0]["status"] == "failed"
  assert state["jobs"][0]["attempt"] == 3
  assert state["summary"]["failed_jobs"] == 1
  assert "必须显式使用 --retry-failed" in state["last_error"]


def test_expected_database_rows_match_postgresql_precision():
  module = _load_module()

  rows = module._expected_database_rows(
    [
      {
        "code": "600519.SH",
        "ex_date": "20200624",
        "time": 1_592_928_000_000,
        "interest": 28.02423,
        "stockBonus": 0,
        "stockGift": 0,
        "allotNum": 0,
        "allotPrice": 0,
        "gugai": 0,
        "dr": 1.01167749,
      }
    ]
  )

  assert rows[0][1] == datetime(2020, 6, 24)
  assert rows[0][3] == Decimal("28.0242")
  assert rows[0][-1] == Decimal("1.011677")


def test_default_state_name_is_window_specific():
  module = _load_module()

  assert module._compact(date(2020, 3, 13)) == "20200313"


@pytest.mark.asyncio
async def test_factor_campaign_universe_always_includes_benchmark(monkeypatch):
  module = _load_module()
  assert module.SCHEMA_VERSION == 2

  class Result:
    def __init__(self, *, rows=None, one=None):
      self.rows = rows
      self.one = one

    def all(self):
      return list(self.rows or [])

    def one_or_none(self):
      return self.one

  class Session:
    def __init__(self):
      self.calls = 0

    async def execute(self, _statement):
      self.calls += 1
      if self.calls == 1:
        return Result(
          rows=[
            ("000001.SZ", date(1991, 4, 3), None),
            ("600000.SH", date(1999, 11, 10), None),
            ("830001.BJ", date(2020, 1, 1), None),
          ]
        )
      return Result(
        one=SimpleNamespace(
          id=module.BENCHMARK_CODE,
          type=module.InstrumentType.INDEX,
        )
      )

  session = Session()

  class SessionContext:
    async def __aenter__(self):
      return session

    async def __aexit__(self, *_args):
      return None

  monkeypatch.setattr(module, "AsyncSessionLocal", SessionContext)

  codes, metadata = await module.load_universe(
    start=date(2020, 1, 1),
    end=date(2024, 12, 31),
    code_limit=1,
  )

  assert codes == ["000001.SZ", "000300.SH"]
  assert metadata["stock_count"] == 1
  assert metadata["requested_code_count"] == 2
  assert metadata["benchmark_code"] == "000300.SH"
