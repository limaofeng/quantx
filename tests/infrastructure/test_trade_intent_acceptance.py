from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import TradeIntent, TradeIntentDirection
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def intent_database(monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(db, tables=[TradeIntentRecord.__table__])
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)

  async def get_db():
    async with sessions() as db:
      yield db

  monkeypatch.setattr("quantx_infrastructure.database.connection.get_async_db", get_db)
  yield sessions
  await engine.dispose()


def intent(key, amount=1000):
  return TradeIntent(
    intent_id=key,
    strategy_id="1",
    run_id="run",
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="core",
    reason="BUY",
    target_amount=amount,
  )


@pytest.mark.asyncio
async def test_output_batch_conflict_does_not_publish_other_intents(intent_database):
  manager = RuntimeStateManager(run_id="run", persist_enabled=True)
  await manager.record_trade_intent(intent("existing"), status="AWAITING_APPROVAL")
  with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
    await manager.record_trade_intents(
      [
        (intent("new"), "AWAITING_APPROVAL"),
        (intent("existing", amount=2000), "AWAITING_APPROVAL"),
      ]
    )
  assert "new" not in manager._state["trade_intents"]
  assert manager._state["trade_intents"]["existing"]["target_amount"] == 1000
  async with intent_database() as db:
    assert await db.scalar(select(func.count()).select_from(TradeIntentRecord)) == 1


@pytest.mark.asyncio
async def test_database_commit_failure_exposes_no_intent(intent_database, monkeypatch):
  async def fail(_repository, _records):
    raise RuntimeError("commit failed")

  monkeypatch.setattr(TradeIntentRepository, "create_intents_idempotent", fail)
  manager = RuntimeStateManager(run_id="run", persist_enabled=True)
  with pytest.raises(RuntimeError, match="commit failed"):
    await manager.record_trade_intents(
      [(intent("first"), "AWAITING_APPROVAL"), (intent("second"), "PENDING")]
    )
  assert manager._state["trade_intents"] == {}


@pytest.mark.asyncio
async def test_status_commit_failure_preserves_accepted_cache(
  intent_database, monkeypatch
):
  manager = RuntimeStateManager(run_id="run", persist_enabled=True)
  await manager.record_trade_intent(intent("entry"), status="AWAITING_APPROVAL")
  monkeypatch.setattr(
    TradeIntentRepository,
    "update_intent",
    AsyncMock(side_effect=RuntimeError("commit failed")),
  )
  with pytest.raises(RuntimeError, match="commit failed"):
    await manager.update_trade_intent_status("entry", "APPROVED")
  assert manager._state["trade_intents"]["entry"]["status"] == "AWAITING_APPROVAL"
