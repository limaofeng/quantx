from datetime import datetime, timezone

import httpx
import pytest
from quantx_contracts import (
  ACCOUNT_EXECUTION_SAFETY_CHECK_CODES,
  AccountSafetyCheckObservation,
  AccountSafetyCheckStatus,
  AccountSafetyObservationSnapshot,
)
from quantx_monitor.models import MonitorStatus
from quantx_monitor.probes.account_safety import (
  ACCOUNT_SAFETY_SCHEMA_MISMATCH,
  AccountSafetyProbe,
)


def _snapshot() -> dict:
  return AccountSafetyObservationSnapshot(
    status="ready",
    observed_at=datetime.now(timezone.utc),
    checks=[
      AccountSafetyCheckObservation(
        code=code,
        status=AccountSafetyCheckStatus.PASSED,
        scope="INCREASE_RISK",
      )
      for code in ACCOUNT_EXECUTION_SAFETY_CHECK_CODES
    ],
  ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_probe_accepts_only_the_complete_versioned_snapshot():
  async def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/internal/monitor/account-safety"
    return httpx.Response(200, json=_snapshot())

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    outcome = await AccountSafetyProbe("http://api", 1).run(client)

  assert outcome.source.observed_status is MonitorStatus.HEALTHY
  assert outcome.source.latency_ms is not None
  assert outcome.snapshot is not None
  assert len(outcome.snapshot.checks) == 18


@pytest.mark.asyncio
async def test_probe_turns_a_schema_mismatch_into_unknown_observation_source():
  async def handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"schema_version": 1})

  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    outcome = await AccountSafetyProbe("http://api", 1).run(client)

  assert outcome.source.observed_status is MonitorStatus.UNAVAILABLE
  assert outcome.source.reason_code == ACCOUNT_SAFETY_SCHEMA_MISMATCH
  assert outcome.snapshot is None
