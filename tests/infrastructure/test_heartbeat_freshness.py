from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
import quantx_infrastructure.services.t_trade_operations_service as operations_module
import quantx_infrastructure.services.trade_command_service as trade_command_module
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService

FRESHNESS_CHECKS: tuple[Callable[[object], bool], ...] = (
  TTradeOperationsService._fresh,
  TradeCommandService._heartbeat_fresh,
)


@pytest.fixture
def fixed_utcnow(monkeypatch: pytest.MonkeyPatch) -> datetime:
  value = datetime(2026, 7, 28, 10, 30)
  monkeypatch.setattr(operations_module, "utcnow", lambda: value)
  monkeypatch.setattr(trade_command_module, "utcnow", lambda: value)
  return value


@pytest.mark.parametrize("freshness_check", FRESHNESS_CHECKS)
def test_heartbeat_freshness_accepts_naive_utc(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(seconds=30),
  )

  assert freshness_check(heartbeat)


@pytest.mark.parametrize("freshness_check", FRESHNESS_CHECKS)
def test_heartbeat_freshness_normalizes_aware_datetime(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=datetime(
      2026,
      7,
      28,
      18,
      29,
      30,
      tzinfo=timezone(timedelta(hours=8)),
    ),
  )

  assert freshness_check(heartbeat)


@pytest.mark.parametrize("freshness_check", FRESHNESS_CHECKS)
def test_heartbeat_freshness_rejects_stale_timestamp(
  freshness_check: Callable[[object], bool],
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
    status="READY",
    updated_at=fixed_utcnow - timedelta(seconds=91),
  )

  assert not freshness_check(heartbeat)


def test_operations_freshness_requires_ready_status(
  fixed_utcnow: datetime,
) -> None:
  heartbeat = SimpleNamespace(
    status="DEGRADED",
    updated_at=fixed_utcnow,
  )

  assert not TTradeOperationsService._fresh(heartbeat)


@pytest.mark.asyncio
async def test_readiness_uses_one_database_round_trip(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  snapshot_result = MagicMock()
  snapshot_result.all.return_value = [
    (None, None, None, None, 0, None, 0, 0)
  ]
  db = SimpleNamespace(
    execute=AsyncMock(return_value=snapshot_result)
  )

  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    SessionContext,
  )

  result = await TTradeOperationsService().readiness("TEST-ACCOUNT")

  assert db.execute.await_count == 1
  assert len(result["checks"]) == 14
  assert result["ready"] is False
