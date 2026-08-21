from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from unittest.mock import AsyncMock

import pytest
from quantx_engine import command_processor
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.services import engine_command_service as service_module
from quantx_infrastructure.services.engine_command_service import EngineCommandService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class ReplayMode(Enum):
  BACKTEST = "BACKTEST"


@dataclass
class ReplayPositionInput:
  stock_code: str
  volume: int


@dataclass
class ReplayStartInput:
  start_time: datetime
  mode: ReplayMode
  positions: list[ReplayPositionInput]


@pytest.fixture
async def command_database(monkeypatch: pytest.MonkeyPatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[EngineCommandOutbox.__table__],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(service_module, "AsyncSessionLocal", session_factory)
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", session_factory)
  yield session_factory
  await engine.dispose()


@pytest.mark.asyncio
async def test_engine_command_idempotency_is_database_enforced(
  command_database,
) -> None:
  service = EngineCommandService()
  first = await service.enqueue(
    "STRATEGY_STOP",
    {"run_id": "run-1"},
    aggregate_id="run-1",
    idempotency_key="stop:run-1:request-1",
  )
  second = await service.enqueue(
    "STRATEGY_STOP",
    {"run_id": "run-1"},
    aggregate_id="run-1",
    idempotency_key="stop:run-1:request-1",
  )

  assert second.message_id == first.message_id
  async with command_database() as db:
    assert (
      await db.scalar(select(func.count()).select_from(EngineCommandOutbox))
      == 1
    )


@pytest.mark.asyncio
async def test_engine_command_serializes_nested_dataclass_payloads(
  command_database,
) -> None:
  service = EngineCommandService()
  replay_input = ReplayStartInput(
    start_time=datetime(2026, 7, 20, 9, 30),
    mode=ReplayMode.BACKTEST,
    positions=[ReplayPositionInput(stock_code="600887.SH", volume=800)],
  )

  receipt = await service.enqueue(
    "T_TRADE_REPLAY_START",
    {"input": replay_input},
    aggregate_id="account-1",
  )

  async with command_database() as db:
    row = await db.get(EngineCommandOutbox, receipt.message_id)
    assert row.payload == {
      "input": {
        "start_time": "2026-07-20T09:30:00",
        "mode": "BACKTEST",
        "positions": [{"stock_code": "600887.SH", "volume": 800}],
      }
    }


@pytest.mark.asyncio
async def test_engine_recovers_and_completes_claimed_command(
  command_database,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service = EngineCommandService()
  receipt = await service.enqueue(
    "STRATEGY_STOP",
    {"run_id": "run-1"},
    aggregate_id="run-1",
    idempotency_key="stop:run-1:request-2",
  )
  async with command_database() as db:
    row = await db.get(EngineCommandOutbox, receipt.message_id)
    row.processing_status = "PROCESSING"
    await db.commit()

  await command_processor._recover_processing_commands()
  claimed = await command_processor._claim_next()
  assert claimed == (
    receipt.message_id,
    "STRATEGY_STOP",
    {"run_id": "run-1"},
  )

  async def stop_strategy(run_id: str) -> bool:
    assert run_id == "run-1"
    return True

  monkeypatch.setattr(
    command_processor.strategy_manager,
    "stop_strategy",
    stop_strategy,
  )
  result = await command_processor._dispatch(claimed[1], claimed[2])
  await command_processor._complete(claimed[0], result=result)

  completed = await service.get(receipt.message_id)
  assert completed is not None
  assert completed.status == "SUCCEEDED"
  assert completed.result == {"success": True}


@pytest.mark.asyncio
async def test_t_trade_replay_command_defers_slow_runtime_start(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  start = AsyncMock(
    return_value={
      "run_id": "replay-1",
      "status": "PENDING",
      "progress_pct": 0.0,
    }
  )
  monkeypatch.setattr(command_processor.TTradeReplayService, "start", start)

  result = await command_processor._dispatch(
    "T_TRADE_REPLAY_START",
    {"input": {"account_id": "account-1"}},
    command_id="00000000-0000-0000-0000-000000000125",
  )

  assert result == {
    "run_id": "replay-1",
    "status": "PENDING",
    "progress_pct": 0.0,
  }
  start.assert_awaited_once_with(
    {"account_id": "account-1"},
    defer_start=True,
    request_id="00000000-0000-0000-0000-000000000125",
  )
