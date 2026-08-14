from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.services import financial_sync_health_service


@pytest.mark.asyncio
async def test_financial_sync_health_marks_old_success_stale(monkeypatch) -> None:
  latest = SimpleNamespace(
    status="success",
    completed_at=datetime(2026, 8, 7, 9, 5),
    warnings=None,
    requested_codes=5000,
    synced_codes=5000,
    empty_codes=0,
    statement_rows=100,
    metric_rows=100,
  )
  repo = SimpleNamespace(
    find_latest=AsyncMock(return_value=latest),
    find_latest_success=AsyncMock(return_value=latest),
  )
  monkeypatch.setattr(
    financial_sync_health_service,
    "FinancialSyncRunRepository",
    lambda _db: repo,
  )

  result = await financial_sync_health_service.financial_sync_health(
    object(),
    now=datetime(2026, 8, 10, 15, 0),
  )

  assert result["status"] == "STALE"
  assert result["is_stale"] is True
  assert result["synced_codes"] == 5000


@pytest.mark.asyncio
async def test_financial_sync_health_surfaces_partial_coverage(monkeypatch) -> None:
  latest = SimpleNamespace(
    status="partial_failure",
    completed_at=datetime(2026, 8, 10, 9, 20),
    warnings="2 只股票未返回受支持的财务四表",
    requested_codes=5000,
    synced_codes=4998,
    empty_codes=2,
    statement_rows=100,
    metric_rows=100,
  )
  repo = SimpleNamespace(
    find_latest=AsyncMock(return_value=latest),
    find_latest_success=AsyncMock(return_value=None),
  )
  monkeypatch.setattr(
    financial_sync_health_service,
    "FinancialSyncRunRepository",
    lambda _db: repo,
  )

  result = await financial_sync_health_service.financial_sync_health(
    object(),
    now=datetime(2026, 8, 10, 15, 0),
  )

  assert result["status"] == "PARTIAL_FAILURE"
  assert result["is_stale"] is False
  assert result["empty_codes"] == 2
