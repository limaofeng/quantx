from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_infrastructure.repositories.strategy_grid_book_snapshot_repository import (
  BACKTEST_FINAL_SNAPSHOT,
  CURRENT_SNAPSHOT,
  TEMPLATE_SNAPSHOT,
  StrategyGridBookSnapshotRepository,
)


@pytest.mark.asyncio
async def test_upsert_backtest_final_uses_backtest_scoped_key(monkeypatch):
  db = MagicMock()
  db.commit = AsyncMock()
  db.refresh = AsyncMock()
  repo = StrategyGridBookSnapshotRepository(db)
  monkeypatch.setattr(repo, "find_by_id", AsyncMock(return_value=None))

  record = await repo.upsert_backtest_final(
    strategy_run_id="run-1",
    backtest_id="bt-1",
    backtest_version=8,
    snapshot={
      "instrument_code": "562500.SH",
      "version": 2,
      "parameter_version": "3",
      "levels": [],
    },
    source_path="data/backtests/bt-1.jsonl",
    snapshot_count=12,
    observed_count=120,
  )

  assert record.id == "BACKTEST_FINAL:bt-1"
  assert record.snapshot_type == BACKTEST_FINAL_SNAPSHOT
  assert record.strategy_run_id == "run-1"
  assert record.backtest_id == "bt-1"
  assert record.backtest_version == 8
  assert record.instrument_code == "562500.SH"
  assert record.snapshot_count == 12
  assert record.observed_count == 120
  db.add.assert_called_once()
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_current_overwrites_run_scoped_record(monkeypatch):
  db = MagicMock()
  db.commit = AsyncMock()
  db.refresh = AsyncMock()
  repo = StrategyGridBookSnapshotRepository(db)
  existing = MagicMock()
  monkeypatch.setattr(repo, "find_by_id", AsyncMock(return_value=existing))

  record = await repo.upsert_current(
    strategy_run_id="run-1",
    mode="PAPER",
    snapshot={"instrument_code": "000001.SZ", "levels": []},
  )

  assert record is existing
  assert existing.strategy_run_id == "run-1"
  assert existing.snapshot_type == CURRENT_SNAPSHOT
  assert existing.mode == "PAPER"
  assert existing.instrument_code == "000001.SZ"
  db.add.assert_not_called()
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_template_uses_run_scoped_template_key(monkeypatch):
  db = MagicMock()
  db.commit = AsyncMock()
  db.refresh = AsyncMock()
  repo = StrategyGridBookSnapshotRepository(db)
  monkeypatch.setattr(repo, "find_by_id", AsyncMock(return_value=None))

  record = await repo.upsert_template(
    strategy_run_id="run-1",
    mode="BACKTEST",
    snapshot={
      "instrument_code": "562500.SH",
      "version": 4,
      "parameter_version": "9",
      "levels": [],
    },
  )

  assert record.id == "TEMPLATE:run-1"
  assert record.snapshot_type == TEMPLATE_SNAPSHOT
  assert record.strategy_run_id == "run-1"
  assert record.backtest_id is None
  assert record.backtest_version is None
  assert record.grid_book_version == 4
  assert record.parameter_version == "9"
  db.add.assert_called_once()
  db.commit.assert_awaited_once()
