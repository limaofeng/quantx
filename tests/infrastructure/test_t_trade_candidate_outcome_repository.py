from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateOutcomeDefinition,
  CandidatePriceObservation,
  observe_candidate_outcome,
  start_candidate_outcome,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.t_trade_candidate_outcome import (
  TTradeCandidateOutcome,
)
from quantx_infrastructure.repositories.t_trade_candidate_outcome_repository import (
  CandidateOutcomeConcurrencyError,
  TTradeCandidateOutcomeRepository,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _state(
  *,
  fingerprint: str = "a" * 64,
  candidate_id: str = "candidate-1",
  strategy_run_id: str = "run-1",
  instrument_code: str = "600000.SH",
  source_time_ms: int = 1_000_000,
):
  return start_candidate_outcome(
    CandidateOutcomeDefinition(
      candidate_id=candidate_id,
      candidate_fingerprint=fingerprint,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      source_time_ms=source_time_ms,
      tick_ordinal=10,
      continuity_generation="1",
      reference_price=10.0,
      policy_version="policy-1",
      feature_schema_version="1",
      horizons_seconds=(1,),
      max_observation_gap_ms=2_000,
    )
  )


@pytest.mark.asyncio
async def test_repository_create_get_and_optimistic_update_are_restart_safe() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: TTradeCandidateOutcome.__table__.create(sync_connection)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)

  async with sessions() as db:
    repository = TTradeCandidateOutcomeRepository(db)
    state = _state()
    created = await repository.create_or_get(account_id="account-1", state=state)
    duplicate = await repository.create_or_get(account_id="account-1", state=state)
    assert duplicate.id == created.id
    assert duplicate.state_version == 1
    with pytest.raises(ValueError, match="证券账户"):
      await repository.create_or_get(account_id="", state=state)
    with pytest.raises(ValueError, match="证券账户不一致"):
      await repository.create_or_get(account_id="account-2", state=state)

    observe_candidate_outcome(
      state,
      CandidatePriceObservation(1_001_000, 11, "1", 10.2),
    )
    updated = await repository.save(state=state, expected_version=1)
    assert updated.state_version == 2
    assert repository.state_from_row(updated).horizons[0].observed_price == 10.2

    same = await repository.save(state=state, expected_version=1)
    assert same.state_version == 2

  await engine.dispose()


@pytest.mark.asyncio
async def test_repository_rejects_cross_account_integrity_race_winner() -> None:
  class FakeDb:
    def __init__(self) -> None:
      self.rollback_calls = 0

    def add(self, _row) -> None:
      return None

    async def commit(self) -> None:
      raise IntegrityError("insert", {}, RuntimeError("unique violation"))

    async def rollback(self) -> None:
      self.rollback_calls += 1

  db = FakeDb()
  repository = TTradeCandidateOutcomeRepository(db)
  repository.get = AsyncMock(
    side_effect=[None, SimpleNamespace(account_id="account-2")]
  )

  with pytest.raises(ValueError, match="证券账户不一致"):
    await repository.create_or_get(account_id="account-1", state=_state())

  assert db.rollback_calls == 1


@pytest.mark.asyncio
async def test_repository_lists_only_account_run_instrument_and_time_scope() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: TTradeCandidateOutcome.__table__.create(sync_connection)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    repository = TTradeCandidateOutcomeRepository(db)
    await repository.create_or_get(
      account_id="account-1",
      state=_state(candidate_id="candidate-a", fingerprint="a" * 64),
    )
    await repository.create_or_get(
      account_id="account-2",
      state=_state(candidate_id="candidate-b", fingerprint="b" * 64),
    )
    await repository.create_or_get(
      account_id="account-1",
      state=_state(
        candidate_id="candidate-c",
        fingerprint="c" * 64,
        strategy_run_id="run-2",
      ),
    )
    rows = await repository.list_for_scope(
      account_id="account-1",
      strategy_run_id="run-1",
      instrument_code="600000.SH",
      started_at=datetime.fromtimestamp(900, tz=timezone.utc),
      ended_at=datetime.fromtimestamp(1_100, tz=timezone.utc),
    )
    assert [row.candidate_id for row in rows] == ["candidate-a"]
  await engine.dispose()


@pytest.mark.asyncio
async def test_repository_rejects_candidate_identity_collision() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: TTradeCandidateOutcome.__table__.create(sync_connection)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    repository = TTradeCandidateOutcomeRepository(db)
    await repository.create_or_get(account_id="account-1", state=_state())
    with pytest.raises(ValueError, match="冻结身份不一致"):
      await repository.create_or_get(
        account_id="account-1", state=_state(fingerprint="b" * 64)
      )
  await engine.dispose()


@pytest.mark.asyncio
async def test_repository_lists_unfinalized_with_bounded_candidate_keyset() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: TTradeCandidateOutcome.__table__.create(sync_connection)
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    repository = TTradeCandidateOutcomeRepository(db)
    for index, candidate_id in enumerate(
      ("candidate-d", "candidate-b", "candidate-e", "candidate-a", "candidate-c")
    ):
      await repository.create_or_get(
        account_id="account-1",
        state=_state(
          candidate_id=candidate_id,
          fingerprint=f"{index + 1}" * 64,
          source_time_ms=1_000_000 + index,
        ),
      )

    first = await repository.list_unfinalized(
      strategy_run_id="run-1",
      after_candidate_id=None,
      limit=2,
    )
    second = await repository.list_unfinalized(
      strategy_run_id="run-1",
      after_candidate_id=first[-1].candidate_id,
      limit=2,
    )
    third = await repository.list_unfinalized(
      strategy_run_id="run-1",
      after_candidate_id=second[-1].candidate_id,
      limit=2,
    )

    assert [row.candidate_id for row in first] == ["candidate-a", "candidate-b"]
    assert [row.candidate_id for row in second] == ["candidate-c", "candidate-d"]
    assert [row.candidate_id for row in third] == ["candidate-e"]
    with pytest.raises(ValueError, match="分页大小"):
      await repository.list_unfinalized(
        strategy_run_id="run-1",
        after_candidate_id=None,
        limit=257,
      )
  await engine.dispose()


@pytest.mark.asyncio
async def test_repository_rejects_stale_write_with_different_state() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection, tables=[TTradeCandidateOutcome.__table__]
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  async with sessions() as db:
    repository = TTradeCandidateOutcomeRepository(db)
    state = _state()
    await repository.create_or_get(account_id="account-1", state=state)
    observe_candidate_outcome(
      state, CandidatePriceObservation(1_001_000, 11, "1", 10.1)
    )
    await repository.save(state=state, expected_version=1)

    stale = _state()
    observe_candidate_outcome(
      stale, CandidatePriceObservation(1_001_000, 11, "1", 10.3)
    )
    with pytest.raises(CandidateOutcomeConcurrencyError):
      await repository.save(state=stale, expected_version=1)
  await engine.dispose()
