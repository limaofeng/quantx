from datetime import timedelta

import pytest
from quantx_domain.clock import utcnow
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.services import (
  t_trade_operations_service as operations_module,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _closed_batch(
  batch_id: str,
  *,
  entry_price: float,
  exit_price: float,
  hours: float,
  closed_days_ago: int = 0,
  metrics_origin: str = "RULE_ESTIMATE",
) -> TTradeBatch:
  closed_at = utcnow() - timedelta(days=closed_days_ago, minutes=5)
  return TTradeBatch(
    batch_id=batch_id,
    account_id="account-1",
    instrument_code="600000.SH",
    strategy_run_id=f"run-{batch_id}",
    source_execution_owner_type="STRATEGY_RUN",
    source_execution_owner_id=f"run-{batch_id}",
    source_execution_environment="LIVE",
    status="CLOSED",
    environment="LIVE",
    target_volume=100,
    entry_filled_volume=100,
    entry_avg_price=entry_price,
    exit_filled_volume=100,
    exit_avg_price=exit_price,
    entry_filled_at=closed_at - timedelta(hours=hours),
    closed_at=closed_at,
    terminal_at=closed_at,
    metrics_origin=metrics_origin,
    commission_rate=0.0003,
    minimum_commission=5.0,
    stamp_tax_rate=0.0005,
    transfer_fee_rate=0.00001,
  )


@pytest.fixture
async def batch_history_database(tmp_path, monkeypatch):
  engine = create_async_engine(
    f"sqlite+aiosqlite:///{tmp_path / 't-batch-history.db'}"
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(sync, tables=[TTradeBatch.__table__])
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(operations_module, "AsyncSessionLocal", sessions)
  async with sessions() as db:
    now = utcnow()
    db.add_all(
      [
        _closed_batch(
          "closed-a",
          entry_price=10.0,
          exit_price=11.0,
          hours=1.0,
        ),
        _closed_batch(
          "closed-b",
          entry_price=20.0,
          exit_price=21.0,
          hours=8.0,
          metrics_origin="LEGACY_BACKFILL",
        ),
        _closed_batch(
          "closed-stale",
          entry_price=10.0,
          exit_price=11.0,
          hours=2.0,
          closed_days_ago=40,
        ),
        TTradeBatch(
          batch_id="entry-rejected",
          account_id="account-1",
          instrument_code="600001.SH",
          strategy_run_id="run-rejected",
          source_execution_owner_type="STRATEGY_RUN",
          source_execution_owner_id="run-rejected",
          source_execution_environment="LIVE",
          status="ENTRY_REJECTED",
          environment="LIVE",
          target_volume=100,
          terminal_at=now - timedelta(days=1),
        ),
        TTradeBatch(
          batch_id="legacy-no-terminal-time",
          account_id="account-1",
          instrument_code="600005.SH",
          strategy_run_id="run-legacy-missing-time",
          source_execution_owner_type="STRATEGY_RUN",
          source_execution_owner_id="run-legacy-missing-time",
          source_execution_environment="LIVE",
          status="CLOSED",
          environment="LIVE",
          target_volume=100,
          entry_filled_volume=100,
          entry_avg_price=10.0,
          exit_filled_volume=100,
          exit_avg_price=11.0,
          entry_filled_at=now - timedelta(days=1, hours=1),
          closed_at=None,
          terminal_at=None,
        ),
        TTradeBatch(
          batch_id="dirty-over-exit",
          account_id="account-1",
          instrument_code="600006.SH",
          strategy_run_id="run-over-exit",
          source_execution_owner_type="STRATEGY_RUN",
          source_execution_owner_id="run-over-exit",
          source_execution_environment="LIVE",
          status="CLOSED",
          environment="LIVE",
          target_volume=100,
          entry_filled_volume=100,
          entry_avg_price=10.0,
          exit_filled_volume=101,
          exit_avg_price=11.0,
          entry_filled_at=now - timedelta(hours=2),
          closed_at=now - timedelta(hours=1),
          terminal_at=now - timedelta(hours=1),
        ),
        TTradeBatch(
          batch_id="dirty-closed-with-position",
          account_id="account-1",
          instrument_code="600002.SH",
          strategy_run_id="run-dirty",
          source_execution_owner_type="STRATEGY_RUN",
          source_execution_owner_id="run-dirty",
          source_execution_environment="LIVE",
          status="CLOSED",
          environment="LIVE",
          target_volume=100,
          entry_filled_volume=100,
          entry_avg_price=10.0,
          exit_filled_volume=0,
          entry_filled_at=now - timedelta(hours=2),
        ),
        TTradeBatch(
          batch_id="reconcile-zero",
          account_id="account-1",
          instrument_code="600003.SH",
          strategy_run_id="run-reconcile",
          source_execution_owner_type="STRATEGY_RUN",
          source_execution_owner_id="run-reconcile",
          source_execution_environment="LIVE",
          status="RECONCILE_REQUIRED",
          environment="LIVE",
          target_volume=100,
        ),
        TTradeBatch(
          batch_id="awaiting",
          account_id="account-1",
          instrument_code="600004.SH",
          strategy_run_id="run-awaiting",
          source_execution_owner_type="STRATEGY_RUN",
          source_execution_owner_id="run-awaiting",
          source_execution_environment="PAPER",
          status="AWAITING_ENTRY_APPROVAL",
          environment="PAPER",
          target_volume=100,
        ),
      ]
    )
    await db.commit()
  yield sessions
  await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_history_is_zero_remainder_and_defaults_to_thirty_days(
  batch_history_database,
) -> None:
  del batch_history_database
  rows, has_next, summary = await TTradeOperationsService().list_batches_page(
    "account-1",
    batch_filter={"scope": "TERMINAL"},
    first=50,
  )

  assert has_next is False
  assert {row["batch_id"] for row in rows} == {
    "closed-a",
    "closed-b",
    "entry-rejected",
  }
  assert summary["total_count"] == 3
  assert summary["completed_count"] == 2
  assert summary["metrics_covered_count"] == 2
  assert summary["metrics_coverage_pct"] == pytest.approx(200 / 3)
  assert summary["metrics_coverage_state"] == "PARTIAL"
  assert summary["total_fees_cny"] is not None
  assert summary["net_profit_cny"] is not None
  by_id = {row["batch_id"]: row for row in rows}
  assert by_id["closed-b"]["metrics_origin"] == "LEGACY_BACKFILL"
  assert by_id["closed-b"]["metrics"]["origin"] == "LEGACY_BACKFILL"
  expected_weighted_hours = (1.0 * 1005.01 + 8.0 * 2005.02) / (1005.01 + 2005.02)
  assert summary["average_holding_hours"] == pytest.approx(
    expected_weighted_hours
  )


@pytest.mark.asyncio
async def test_current_scope_is_remainder_first_and_keeps_action_required(
  batch_history_database,
) -> None:
  del batch_history_database
  rows, _has_next, _summary = await TTradeOperationsService().list_batches_page(
    "account-1",
    batch_filter={"scope": "CURRENT"},
    first=50,
  )

  by_id = {row["batch_id"]: row for row in rows}
  assert set(by_id) == {
    "dirty-closed-with-position",
    "dirty-over-exit",
    "reconcile-zero",
    "awaiting",
  }
  assert by_id["dirty-closed-with-position"]["active_volume"] == 100
  assert by_id["dirty-closed-with-position"]["price_quality"] == "MISSING"
  assert (
    by_id["dirty-closed-with-position"]["metrics"]["quality"]
    == "INCOMPLETE"
  )
  assert by_id["dirty-over-exit"]["metrics"]["quality"] == "INCOMPLETE"


@pytest.mark.asyncio
async def test_terminal_cursor_uses_activity_time_and_batch_id(
  batch_history_database,
) -> None:
  del batch_history_database
  service = TTradeOperationsService()
  first_rows, has_next, _summary = await service.list_batches_page(
    "account-1",
    batch_filter={"scope": "TERMINAL"},
    first=1,
  )
  assert has_next is True

  second_rows, _has_next, _summary = await service.list_batches_page(
    "account-1",
    batch_filter={"scope": "TERMINAL"},
    cursor_activity_at=first_rows[0]["activity_at"],
    cursor_id=first_rows[0]["batch_id"],
    first=1,
  )

  assert second_rows
  assert second_rows[0]["batch_id"] != first_rows[0]["batch_id"]


@pytest.mark.asyncio
async def test_batch_page_rejects_unbounded_scope(batch_history_database) -> None:
  del batch_history_database

  with pytest.raises(ValueError, match="CURRENT 或 TERMINAL"):
    await TTradeOperationsService().list_batches_page(
      "account-1",
      batch_filter={},
      first=50,
    )


@pytest.mark.asyncio
async def test_zero_metric_coverage_returns_null_financial_totals(
  batch_history_database,
) -> None:
  del batch_history_database

  rows, _has_next, summary = await TTradeOperationsService().list_batches_page(
    "account-1",
    batch_filter={"scope": "TERMINAL", "result_groups": ["REJECTED"]},
    first=50,
  )

  assert [row["batch_id"] for row in rows] == ["entry-rejected"]
  assert summary["metrics_coverage_pct"] == 0.0
  assert summary["metrics_coverage_state"] == "NONE"
  assert summary["total_fees_cny"] is None
  assert summary["net_profit_cny"] is None
