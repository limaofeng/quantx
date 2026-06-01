from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.daily_asset_snapshot import DailyAssetSnapshot
from repositories.daily_asset_snapshot_repository import DailyAssetSnapshotRepository


def _snapshot_values(total_asset=100_000):
  return {
    "scope_type": "ACCOUNT",
    "scope_key": "account:test",
    "account_id": "test",
    "account_type": None,
    "strategy_run_id": None,
    "trade_date": date(2026, 5, 29),
    "snapshot_at": datetime(2026, 5, 29, 15, 5),
    "source": "MINIQMT",
    "total_asset_cny": total_asset,
    "cash_available_cny": total_asset,
    "cash_frozen_cny": 0,
    "market_value_cny": 0,
    "gross_asset_delta_cny": None,
    "net_capital_flow_cny": 0,
    "daily_pnl_cny": None,
    "daily_return_pct": None,
    "previous_snapshot_id": None,
    "data_quality": "NO_PREVIOUS_SNAPSHOT",
    "snapshot_metadata": {"quality_flags": ["NO_PREVIOUS_SNAPSHOT"]},
  }


@pytest.mark.asyncio
async def test_upsert_snapshot_inserts_new_scope_date(monkeypatch):
  db = MagicMock()
  db.commit = AsyncMock()
  db.refresh = AsyncMock()
  repo = DailyAssetSnapshotRepository(db)
  monkeypatch.setattr(repo, "find_by_id", AsyncMock(return_value=None))
  monkeypatch.setattr(repo, "find_by_scope_and_date", AsyncMock(return_value=None))

  record = await repo.upsert_snapshot(_snapshot_values())

  assert record.scope_key == "account:test"
  assert record.trade_date == date(2026, 5, 29)
  db.add.assert_called_once()
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_snapshot_overwrites_existing_scope_date(monkeypatch):
  db = MagicMock()
  db.commit = AsyncMock()
  db.refresh = AsyncMock()
  repo = DailyAssetSnapshotRepository(db)
  existing = DailyAssetSnapshot(id="existing", **_snapshot_values())
  monkeypatch.setattr(repo, "find_by_id", AsyncMock(return_value=None))
  monkeypatch.setattr(
    repo, "find_by_scope_and_date", AsyncMock(return_value=existing)
  )

  record = await repo.upsert_snapshot(_snapshot_values(total_asset=101_000))

  assert record is existing
  assert float(existing.total_asset_cny) == pytest.approx(101_000)
  db.add.assert_not_called()
  db.commit.assert_awaited_once()
