from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services import account_execution_safety_service as safety


@pytest.mark.asyncio
@pytest.mark.parametrize("error_class", [ValueError, asyncio.CancelledError])
@pytest.mark.parametrize("elapsed", [0.25, 1.5])
async def test_slow_read_reports_phases_without_sensitive_error_text(
  monkeypatch, caplog, error_class, elapsed
):
  clock = [0.0]
  private_text = "private-account-secret-snapshot-payload"

  @asynccontextmanager
  async def session():
    yield SimpleNamespace()

  async def snapshot(*_args):
    clock[0] += elapsed
    raise error_class(private_text)

  monkeypatch.setattr(safety, "perf_counter", lambda: clock[0])
  monkeypatch.setattr(safety, "AsyncSessionLocal", session)
  monkeypatch.setattr(
    safety.AccountExecutionSafetyService, "_readiness_snapshot", snapshot
  )
  with caplog.at_level(logging.WARNING, logger=safety.__name__):
    with pytest.raises(error_class):
      await safety.AccountExecutionSafetyService().checks(private_text)
  assert private_text not in caplog.text
  if elapsed < 1:
    assert caplog.records == []
  else:
    assert len(caplog.records) == 1
    assert "total_ms=1500.00 base_query_ms=1500.00" in caplog.text
    assert "market_ms=0.00 details_ms=0.00" in caplog.text
    assert f"error_type={error_class.__name__}" in caplog.text
