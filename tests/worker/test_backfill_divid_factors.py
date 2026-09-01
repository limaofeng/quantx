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


def _agent_store(status: str, capabilities: list[str] | None = None):
  class Store:
    async def component_status(self, prefix):
      assert prefix == "qmt-agent:"
      return [
        {
          "status": status,
          "instance_id": "device-1",
          "updated_at": datetime.now(timezone.utc),
          "details": {
            "capabilities": capabilities or ["market-data", "divid-factors", "live"],
          },
        }
      ]

    async def close(self):
      return None

  return Store


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["READY", "RECONCILING"])
async def test_factor_readiness_accepts_fresh_status(
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


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["live", "data-only"])
async def test_factor_readiness_accepts_either_agent_mode(monkeypatch, mode):
  module = _load_module()
  monkeypatch.setattr(
    module,
    "DurableRuntimeStore",
    _agent_store("READY", ["market-data", "divid-factors", mode]),
  )

  assert await module.ensure_factor_agent_ready() == "device-1"


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
  assert module.request_payload(state, failed_job)["request_key"].endswith("attempt-3")
  assert state["jobs"][1]["status"] == "completed"


def test_explicit_retry_can_abandon_one_failed_completed_request_verification():
  module = _load_module()
  state = {
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "verifying",
        "attempt": 2,
        "attempt_limit": 3,
        "request_id": "request-old",
        "last_error": "RuntimeError: digest mismatch",
        "verification_failures": 1,
      }
    ]
  }

  retried = module._retry_failed_jobs(state, max_attempts=3)

  job = state["jobs"][0]
  assert retried == ["job-1"]
  assert job["status"] == "pending"
  assert job["attempt"] == 3
  assert job["attempt_limit"] == 6
  assert job["retry_history"][0]["previous_status"] == "verifying"
  assert "verification_failures" not in job


def test_job_history_is_bounded():
  module = _load_module()
  job = {}

  for index in range(module.MAX_JOB_HISTORY_EVENTS + 10):
    module._append_job_history(job, "events", {"index": index})

  assert len(job["events"]) == module.MAX_JOB_HISTORY_EVENTS
  assert job["events"][0]["index"] == 10


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

    async def assert_held(self):
      return None

    async def release(self):
      return None

  async def load_state(_args, _state_path, **_kwargs):
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


@pytest.mark.asyncio
async def test_run_resumes_verification_without_repeating_qmt_request(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state = {
    "run_key": "campaign",
    "status": "failed",
    "start_date": "20200313",
    "end_date": "20260831",
    "universe": {
      "universe_version": module.UNIVERSE_VERSION,
      "stock_count": 1,
      "etf_count": 0,
      "market_instrument_count": 1,
      "requested_code_count": 1,
    },
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "verifying",
        "attempt": 2,
        "request_id": "request-1",
      }
    ],
  }

  class Lock:
    async def acquire(self):
      return None

    async def assert_held(self):
      return None

    async def release(self):
      return None

  async def load_state(_args, _state_path, **_kwargs):
    return state

  async def verify(*, request_id, expected_payload):
    assert request_id == "request-1"
    assert expected_payload["request_key"].endswith("attempt-2")
    return {
      "request_id": request_id,
      "requested_code_count": 1,
      "source_code_count": 1,
      "source_record_count": 1,
      "persisted_record_count": 1,
      "source_sha256": "a" * 64,
    }

  async def must_not_request_qmt(**_kwargs):
    raise AssertionError("verifying jobs must not repeat QMT requests")

  monkeypatch.setattr(module, "CampaignDatabaseLock", Lock)
  monkeypatch.setattr(module, "_load_or_create_state", load_state)
  monkeypatch.setattr(module, "verify_completed_request", verify)
  monkeypatch.setattr(module.divid_factor_sync_flow, "fn", must_not_request_qmt)
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    state_file=str(tmp_path / "factor-state.json"),
    retry_failed=False,
    max_attempts=3,
    max_jobs=None,
    poll_seconds=0.01,
    timeout_seconds=900,
  )

  result = await module.run(args)

  assert result == 0
  assert state["jobs"][0]["status"] == "completed"
  assert state["jobs"][0]["attempt"] == 2
  assert state["jobs"][0]["request_id"] == "request-1"
  assert state["completed_at"]


@pytest.mark.asyncio
async def test_repeated_verification_failure_becomes_explicitly_retryable(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state = {
    "run_key": "campaign",
    "status": "failed",
    "start_date": "20200313",
    "end_date": "20260831",
    "universe": {
      "universe_version": module.UNIVERSE_VERSION,
      "stock_count": 1,
      "etf_count": 0,
      "market_instrument_count": 1,
      "requested_code_count": 1,
    },
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "verifying",
        "attempt": 0,
        "request_id": "request-1",
      }
    ],
  }

  class Lock:
    async def acquire(self):
      return None

    async def assert_held(self):
      return None

    async def release(self):
      return None

  async def load_state(_args, _state_path, **_kwargs):
    return state

  async def fail_verification(**_kwargs):
    raise RuntimeError("persistent digest mismatch")

  def persist_without_disk(_path, current, *, status=None, error=""):
    if status is not None:
      current["status"] = status
    if error:
      current["last_error"] = error
    module._refresh_summary(current)

  monkeypatch.setattr(module, "CampaignDatabaseLock", Lock)
  monkeypatch.setattr(module, "_load_or_create_state", load_state)
  monkeypatch.setattr(module, "verify_completed_request", fail_verification)
  monkeypatch.setattr(module, "_persist_state", persist_without_disk)
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    state_file=str(tmp_path / "factor-state.json"),
    retry_failed=False,
    max_attempts=3,
    max_jobs=None,
    poll_seconds=0.01,
    timeout_seconds=1,
  )

  assert await module.run(args) == 2
  assert state["jobs"][0]["status"] == "verifying"
  assert state["jobs"][0]["attempt"] == 0
  assert await module.run(args) == 2
  assert state["jobs"][0]["status"] == "failed"
  assert state["jobs"][0]["attempt"] == 1
  assert state["jobs"][0]["verification_failures"] == 2


@pytest.mark.asyncio
async def test_repeated_running_exception_reselects_agent_then_stops(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state = {
    "run_key": "campaign",
    "status": "pending",
    "start_date": "20200313",
    "end_date": "20260831",
    "universe": {
      "universe_version": module.UNIVERSE_VERSION,
      "stock_count": 1,
      "etf_count": 0,
      "market_instrument_count": 1,
      "requested_code_count": 1,
    },
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "pending",
        "attempt": 0,
      }
    ],
  }
  readiness_calls = 0

  class Lock:
    async def acquire(self):
      return None

    async def assert_held(self):
      return None

    async def release(self):
      return None

  async def load_state(_args, _state_path, **_kwargs):
    return state

  async def no_foreign(**_kwargs):
    return []

  async def ready():
    nonlocal readiness_calls
    readiness_calls += 1
    return f"device-{readiness_calls}"

  async def fail_flow(**_kwargs):
    raise RuntimeError("database temporarily unavailable")

  def persist_without_disk(_path, current, *, status=None, error=""):
    if status is not None:
      current["status"] = status
    if error:
      current["last_error"] = error
    module._refresh_summary(current)

  monkeypatch.setattr(module, "CampaignDatabaseLock", Lock)
  monkeypatch.setattr(module, "_load_or_create_state", load_state)
  monkeypatch.setattr(module, "foreign_active_requests", no_foreign)
  monkeypatch.setattr(module, "ensure_factor_agent_ready", ready)
  monkeypatch.setattr(module.divid_factor_sync_flow, "fn", fail_flow)
  monkeypatch.setattr(module, "_persist_state", persist_without_disk)
  monkeypatch.setattr(module.asyncio, "sleep", lambda _seconds: _async_none())
  monkeypatch.setattr(module, "RUNNING_RESELECT_AFTER_FAILURES", 1)
  monkeypatch.setattr(module, "RUNNING_STOP_AFTER_FAILURES", 3)
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    state_file=str(tmp_path / "factor-state.json"),
    retry_failed=False,
    max_attempts=3,
    max_jobs=None,
    poll_seconds=0.01,
    timeout_seconds=1,
  )

  assert await module.run(args) == 2
  assert readiness_calls == 3
  assert state["jobs"][0]["status"] == "failed"
  assert state["jobs"][0]["attempt"] == 1
  assert len(state["jobs"][0]["transient_history"]) == 3


@pytest.mark.asyncio
async def test_timeout_resumes_same_request_and_attempt_without_agent_recheck(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state = {
    "run_key": "campaign",
    "status": "pending",
    "start_date": "20200313",
    "end_date": "20260831",
    "universe": {
      "universe_version": module.UNIVERSE_VERSION,
      "stock_count": 1,
      "etf_count": 0,
      "market_instrument_count": 1,
      "requested_code_count": 1,
    },
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "pending",
        "attempt": 0,
      }
    ],
  }
  flow_calls = []
  readiness_calls = 0

  class Lock:
    async def acquire(self):
      return None

    async def assert_held(self):
      return None

    async def release(self):
      return None

  async def load_state(_args, _state_path, **_kwargs):
    return state

  async def no_foreign(**_kwargs):
    return []

  async def ready_once():
    nonlocal readiness_calls
    readiness_calls += 1
    if readiness_calls > 1:
      raise AssertionError("waiting request must resume before Agent readiness")
    return "device-1"

  async def flow(**kwargs):
    flow_calls.append(kwargs)
    if len(flow_calls) == 1:
      return {
        "status": "timeout",
        "request_id": "request-1",
        "durable_status": "DELIVERED",
      }
    return {"status": "completed", "request_id": "request-1"}

  async def verify(*, request_id, expected_payload):
    assert request_id == "request-1"
    return {
      "request_id": request_id,
      "requested_code_count": 1,
      "source_code_count": 1,
      "source_record_count": 1,
      "persisted_record_count": 1,
      "source_sha256": "a" * 64,
    }

  def persist_without_disk(_path, current, *, status=None, error=""):
    if status is not None:
      current["status"] = status
    if error:
      current["last_error"] = error
    elif status in {"running", "completed"}:
      current.pop("last_error", None)
    module._refresh_summary(current)

  monkeypatch.setattr(module, "CampaignDatabaseLock", Lock)
  monkeypatch.setattr(module, "_load_or_create_state", load_state)
  monkeypatch.setattr(module, "foreign_active_requests", no_foreign)
  monkeypatch.setattr(module, "ensure_factor_agent_ready", ready_once)
  monkeypatch.setattr(module.divid_factor_sync_flow, "fn", flow)
  monkeypatch.setattr(module, "verify_completed_request", verify)
  monkeypatch.setattr(module, "_persist_state", persist_without_disk)
  monkeypatch.setattr(module.asyncio, "sleep", lambda _seconds: _async_none())
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    state_file=str(tmp_path / "factor-state.json"),
    retry_failed=False,
    max_attempts=3,
    max_jobs=None,
    poll_seconds=0.01,
    timeout_seconds=1,
  )

  result = await module.run(args)

  assert result == 0
  assert readiness_calls == 1
  assert len(flow_calls) == 2
  assert flow_calls[0]["request_key"] == flow_calls[1]["request_key"]
  assert state["jobs"][0]["attempt"] == 0
  assert state["jobs"][0]["request_id"] == "request-1"
  assert len(state["jobs"][0]["wait_history"]) == 1


async def _async_none():
  return None


@pytest.mark.asyncio
async def test_final_reverification_marks_drifted_scope_for_fresh_attempt(monkeypatch):
  module = _load_module()
  state = {
    "run_key": "campaign",
    "start_date": "20200313",
    "end_date": "20260831",
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "completed",
        "attempt": 0,
        "request_id": "request-1",
      }
    ],
  }

  async def drifted(**_kwargs):
    raise RuntimeError("database digest drift")

  monkeypatch.setattr(module, "verify_completed_request", drifted)

  error = await module._reverify_completed_jobs(state)

  assert "database digest drift" in error
  assert state["jobs"][0]["status"] == "failed"
  assert state["jobs"][0]["attempt"] == 1
  assert state["jobs"][0]["final_verification_history"][0]["request_id"] == (
    "request-1"
  )


def _persisted_factor_row(*, interest: Decimal = Decimal("28.0242")):
  return (
    "600519.SH",
    datetime(2020, 6, 24),
    "20200624",
    interest,
    Decimal("0.0000"),
    Decimal("0.0000"),
    Decimal("0.0000"),
    Decimal("0.0000"),
    Decimal("0.0000"),
    Decimal("1.011677"),
  )


def _completed_factor_request(module, row):
  payload = {
    "operation": "divid_factors",
    "stock_list": ["600519.SH"],
    "start_time": "20200313",
    "end_time": "20260831",
  }
  digest = module.divid_factor_rows_sha256([row])
  request = {
    "status": "COMPLETED",
    "request_payload": payload,
    "expected_chunks": 1,
    "received_chunks": 1,
    "ingestion_result": {
      "operation": "divid_factors",
      "records_received": 1,
      "records_saved": 1,
      "replacement_audit": {
        "audit_schema_version": 2,
        "stock_count": 1,
        "stock_codes_sha256": module.divid_factor_codes_sha256(payload["stock_list"]),
        "prior_count": 0,
        "deleted_count": 0,
        "inserted_count": 1,
        "verified_count": 1,
        "start_ex_date": payload["start_time"],
        "end_ex_date": payload["end_time"],
        "source_sha256": digest,
        "persisted_sha256": digest,
        "code_audits": {
          "600519.SH": {
            "record_count": 1,
            "source_sha256": digest,
            "persisted_sha256": digest,
          }
        },
      },
    },
  }
  manifest = [
    {
      "chunk_index": 0,
      "record_count": 1,
      "checksum_sha256": "a" * 64,
    }
  ]
  return payload, request, manifest


@pytest.mark.asyncio
async def test_completed_request_uses_durable_audit_after_staging_cleanup(monkeypatch):
  module = _load_module()
  row = _persisted_factor_row()
  payload, request, manifest = _completed_factor_request(module, row)

  class Store:
    async def market_data_request(self, _request_id):
      return request

    async def market_data_transfers(self, _request_id):
      return manifest

    async def close(self):
      return None

  class Result:
    def __init__(self, rows=None):
      self.rows = rows or []

    def all(self):
      return self.rows

  class Session:
    def __init__(self):
      self.statements = []

    async def execute(self, statement):
      self.statements.append(statement)
      return Result([] if len(self.statements) == 1 else [row])

  session = Session()

  class SessionContext:
    async def __aenter__(self):
      return session

    async def __aexit__(self, *_args):
      return None

  monkeypatch.setattr(module, "DurableRuntimeStore", Store)
  monkeypatch.setattr(module, "AsyncSessionLocal", SessionContext)

  audit = await module.verify_completed_request(
    request_id="request-1",
    expected_payload=payload,
  )

  assert audit["source_record_count"] == 1
  assert audit["persisted_record_count"] == 1
  assert audit["source_sha256"] == audit["persisted_sha256"]
  assert audit["verified_code_audits"] == 1
  assert audit["code_audits"] == {
    "600519.SH": {
      "record_count": 1,
      "source_sha256": module.divid_factor_rows_sha256([row]),
      "persisted_sha256": module.divid_factor_rows_sha256([row]),
    }
  }
  assert "pg_advisory_xact_lock_shared" in str(session.statements[0])


@pytest.mark.asyncio
async def test_completed_request_rejects_database_content_drift(monkeypatch):
  module = _load_module()
  source_row = _persisted_factor_row()
  payload, request, manifest = _completed_factor_request(module, source_row)
  drifted_row = _persisted_factor_row(interest=Decimal("99.0000"))

  class Store:
    async def market_data_request(self, _request_id):
      return request

    async def market_data_transfers(self, _request_id):
      return manifest

    async def close(self):
      return None

  class Result:
    def __init__(self, rows=None):
      self.rows = rows or []

    def all(self):
      return self.rows

  class Session:
    def __init__(self):
      self.calls = 0

    async def execute(self, _statement):
      self.calls += 1
      return Result([] if self.calls == 1 else [drifted_row])

  class SessionContext:
    async def __aenter__(self):
      return Session()

    async def __aexit__(self, *_args):
      return None

  monkeypatch.setattr(module, "DurableRuntimeStore", Store)
  monkeypatch.setattr(module, "AsyncSessionLocal", SessionContext)

  with pytest.raises(RuntimeError, match="持久化摘要验收失败"):
    await module.verify_completed_request(
      request_id="request-1",
      expected_payload=payload,
    )


@pytest.mark.asyncio
async def test_completed_request_rejects_per_code_digest_drift(monkeypatch):
  module = _load_module()
  row = _persisted_factor_row()
  payload, request, manifest = _completed_factor_request(module, row)
  request["ingestion_result"]["replacement_audit"]["code_audits"]["600519.SH"][
    "source_sha256"
  ] = "b" * 64
  request["ingestion_result"]["replacement_audit"]["code_audits"]["600519.SH"][
    "persisted_sha256"
  ] = "b" * 64

  class Store:
    async def market_data_request(self, _request_id):
      return request

    async def market_data_transfers(self, _request_id):
      return manifest

    async def close(self):
      return None

  class Result:
    def __init__(self, rows=None):
      self.rows = rows or []

    def all(self):
      return self.rows

  class Session:
    def __init__(self):
      self.calls = 0

    async def execute(self, _statement):
      self.calls += 1
      return Result([] if self.calls == 1 else [row])

  class SessionContext:
    async def __aenter__(self):
      return Session()

    async def __aexit__(self, *_args):
      return None

  monkeypatch.setattr(module, "DurableRuntimeStore", Store)
  monkeypatch.setattr(module, "AsyncSessionLocal", SessionContext)

  with pytest.raises(RuntimeError, match="逐代码摘要验收失败"):
    await module.verify_completed_request(
      request_id="request-1",
      expected_payload=payload,
    )


@pytest.mark.asyncio
async def test_campaign_lock_release_does_not_mask_closed_connection_error():
  module = _load_module()

  class LostConnection:
    closed = False

    def __init__(self):
      self.close_called = False

    async def scalar(self, *_args, **_kwargs):
      raise module.SQLAlchemyError("connection is closed")

    async def close(self):
      self.close_called = True

  connection = LostConnection()
  lock = module.CampaignDatabaseLock()
  lock.connection = connection

  await lock.release()

  assert connection.close_called is True
  assert lock.connection is None
  assert lock.backend_pid is None


@pytest.mark.asyncio
async def test_campaign_lock_detects_reconnected_backend_pid(monkeypatch):
  module = _load_module()

  class ReconnectedConnection:
    closed = False

    def __init__(self):
      self.backend_pid = 4101
      self.owns_lock = True
      self.close_called = False

    async def scalar(self, statement, parameters=None):
      sql = str(statement)
      if "pg_try_advisory_lock" in sql:
        return self.owns_lock
      if "FROM pg_locks" in sql:
        assert parameters["lock_key"] == module.CAMPAIGN_LOCK_KEY
        return self.owns_lock and parameters["backend_pid"] == self.backend_pid
      if "pg_advisory_unlock" in sql:
        return parameters["backend_pid"] == self.backend_pid
      if "pg_backend_pid" in sql:
        return self.backend_pid
      raise AssertionError(sql)

    async def close(self):
      self.close_called = True

  connection = ReconnectedConnection()

  async def connect():
    return connection

  monkeypatch.setattr(module, "relational_engine", SimpleNamespace(connect=connect))
  lock = module.CampaignDatabaseLock()

  await lock.acquire()
  assert lock.backend_pid == 4101
  await lock.assert_held()

  # SQLAlchemy may transparently reconnect an invalidated AsyncConnection.
  # A liveness SELECT would succeed, but the session-level lock disappeared.
  connection.backend_pid = 4102
  connection.owns_lock = False
  with pytest.raises(module.CampaignLockLost, match="advisory lock 已丢失"):
    await lock.assert_held()

  await lock.release()
  assert connection.close_called is True


@pytest.mark.asyncio
async def test_run_never_persists_after_campaign_lock_loss(monkeypatch, tmp_path):
  module = _load_module()
  state = {"status": "pending", "jobs": []}

  class Lock:
    def __init__(self):
      self.assertions = 0

    async def acquire(self):
      return None

    async def assert_held(self):
      self.assertions += 1
      if self.assertions >= 2:
        raise module.CampaignLockLost("simulated reconnect")

    async def release(self):
      return None

  async def load_state(_args, _state_path, **_kwargs):
    return state

  def must_not_persist(*_args, **_kwargs):
    raise AssertionError("lost campaign owner must not write its state ledger")

  monkeypatch.setattr(module, "CampaignDatabaseLock", Lock)
  monkeypatch.setattr(module, "_load_or_create_state", load_state)
  monkeypatch.setattr(module, "_persist_state", must_not_persist)
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    state_file=str(tmp_path / "factor-state.json"),
  )

  with pytest.raises(module.CampaignLockLost, match="simulated reconnect"):
    await module.run(args)

  assert state == {"status": "pending", "jobs": []}


@pytest.mark.asyncio
async def test_run_stops_state_writes_when_lock_is_lost_during_request(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  state = {
    "run_key": "campaign",
    "status": "pending",
    "start_date": "20200313",
    "end_date": "20260831",
    "universe": {
      "universe_version": module.UNIVERSE_VERSION,
      "stock_count": 1,
      "etf_count": 0,
      "market_instrument_count": 1,
      "requested_code_count": 1,
    },
    "jobs": [
      {
        "id": "job-1",
        "codes": ["600519.SH"],
        "status": "pending",
        "attempt": 0,
      }
    ],
  }
  active_lock = None
  persisted = []

  class Lock:
    def __init__(self):
      nonlocal active_lock
      active_lock = self
      self.lost = False

    async def acquire(self):
      return None

    async def assert_held(self):
      if self.lost:
        raise module.CampaignLockLost("lost while request was running")

    async def release(self):
      return None

  async def load_state(_args, _state_path, **_kwargs):
    return state

  async def no_foreign(**_kwargs):
    return []

  async def ready():
    return "device-1"

  async def complete_after_lock_loss(**_kwargs):
    active_lock.lost = True
    return {"status": "completed", "request_id": "request-1"}

  def record_persist(_path, current, *, status=None, error=""):
    persisted.append((status, current["jobs"][0]["status"]))
    if status is not None:
      current["status"] = status
    if error:
      current["last_error"] = error
    module._refresh_summary(current)

  monkeypatch.setattr(module, "CampaignDatabaseLock", Lock)
  monkeypatch.setattr(module, "_load_or_create_state", load_state)
  monkeypatch.setattr(module, "foreign_active_requests", no_foreign)
  monkeypatch.setattr(module, "ensure_factor_agent_ready", ready)
  monkeypatch.setattr(
    module.divid_factor_sync_flow,
    "fn",
    complete_after_lock_loss,
  )
  monkeypatch.setattr(module, "_persist_state", record_persist)
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    state_file=str(tmp_path / "factor-state.json"),
    retry_failed=False,
    max_attempts=3,
    max_jobs=None,
    poll_seconds=0.01,
    timeout_seconds=1,
  )

  with pytest.raises(module.CampaignLockLost, match="lost while request"):
    await module.run(args)

  assert persisted == [("running", "pending"), (None, "running")]
  assert state["jobs"][0].get("request_id") is None


def test_default_state_name_identifies_stock_etf_universe():
  module = _load_module()

  state_path = module.default_state_path(
    start=date(2020, 3, 13),
    end=date(2026, 8, 31),
  )

  assert state_path.name == (
    "full-stock-etf-divid-factors-audit-v2-20200313-20260831.json"
  )


@pytest.mark.asyncio
async def test_state_schema_rejects_stock_only_v2_ledger(tmp_path):
  module = _load_module()
  state_path = tmp_path / "stock-only-v2.json"
  state_path.write_text(
    """
{
  "schema_version": 2,
  "operation_version": "qmt-get-divid-factors-v1",
  "start_date": "20200313",
  "end_date": "20260831",
  "batch_size": 200,
  "code_limit": null
}
""".strip(),
    encoding="utf-8",
  )
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    batch_size=200,
    code_limit=None,
  )

  with pytest.raises(RuntimeError, match="回填参数与状态账本不一致"):
    await module._load_or_create_state(args, state_path)


@pytest.mark.asyncio
async def test_new_state_identity_and_audit_use_canonical_universe(
  monkeypatch,
  tmp_path,
):
  module = _load_module()
  codes = ["000001.SZ", "000300.SH", "510300.SH"]
  universe = {
    "universe_version": module.UNIVERSE_VERSION,
    "instrument_types": ["STOCK", "ETF"],
    "stock_count": 1,
    "etf_count": 1,
    "market_instrument_count": 2,
    "requested_code_count": 3,
    "benchmark_code": module.BENCHMARK_CODE,
    "benchmark_count": 1,
    "stock_code_sha256": module._code_hash(["000001.SZ"]),
    "etf_code_sha256": module._code_hash(["510300.SH"]),
    "code_sha256": module._code_hash(codes),
  }

  async def load_universe(**_kwargs):
    return codes, universe

  monkeypatch.setattr(module, "load_universe", load_universe)
  args = SimpleNamespace(
    start_date=date(2020, 3, 13),
    end_date=date(2026, 8, 31),
    batch_size=200,
    code_limit=None,
  )

  state = await module._load_or_create_state(
    args,
    tmp_path / "stock-etf-v4.json",
  )

  assert state["schema_version"] == 4
  assert state["replacement_audit_schema_version"] == 2
  assert state["universe_version"] == module.UNIVERSE_VERSION
  assert state["universe_sha256"] == universe["code_sha256"]
  assert state["universe"] == universe
  module._validate_state_universe_audit(state)


def test_state_universe_audit_rejects_codes_repeated_across_jobs():
  module = _load_module()
  codes = ["000001.SZ", "000300.SH", "510300.SH"]
  state = {
    "universe_sha256": module._code_hash(codes),
    "universe": {
      "universe_version": module.UNIVERSE_VERSION,
      "instrument_types": ["STOCK", "ETF"],
      "stock_count": 1,
      "etf_count": 1,
      "market_instrument_count": 2,
      "requested_code_count": 3,
      "benchmark_code": module.BENCHMARK_CODE,
      "benchmark_count": 1,
      "stock_code_sha256": module._code_hash(["000001.SZ"]),
      "etf_code_sha256": module._code_hash(["510300.SH"]),
      "code_sha256": module._code_hash(codes),
    },
    "jobs": [
      {"codes": ["000001.SZ", "000300.SH"]},
      {"codes": ["000300.SH", "510300.SH"]},
    ],
  }

  with pytest.raises(RuntimeError, match="代码摘要审计不一致"):
    module._validate_state_universe_audit(state)


@pytest.mark.asyncio
async def test_factor_campaign_universe_is_stock_etf_plus_benchmark(monkeypatch):
  module = _load_module()
  assert module.SCHEMA_VERSION == 4

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
            (
              "000001.SZ",
              module.InstrumentType.STOCK,
              date(1991, 4, 3),
              None,
            ),
            (
              "600000.SH",
              module.InstrumentType.STOCK,
              date(1999, 11, 10),
              None,
            ),
            (
              "159915.SZ",
              module.InstrumentType.ETF,
              date(2011, 9, 5),
              None,
            ),
            (
              "510300.SH",
              module.InstrumentType.ETF,
              date(2012, 5, 28),
              None,
            ),
            (
              "830001.BJ",
              module.InstrumentType.STOCK,
              date(2020, 1, 1),
              None,
            ),
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

  assert codes == ["000001.SZ", "000300.SH", "159915.SZ"]
  assert metadata["universe_version"] == module.UNIVERSE_VERSION
  assert metadata["instrument_types"] == ["STOCK", "ETF"]
  assert metadata["stock_count"] == 1
  assert metadata["etf_count"] == 1
  assert metadata["market_instrument_count"] == 2
  assert metadata["requested_code_count"] == 3
  assert metadata["benchmark_code"] == "000300.SH"
  assert metadata["benchmark_count"] == 1
  assert metadata["code_sha256"] == module._code_hash(codes)
