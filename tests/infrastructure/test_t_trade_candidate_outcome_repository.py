from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateOutcomeDefinition,
  CandidatePriceObservation,
  observe_candidate_outcome,
  start_candidate_outcome,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
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


def _paper_execution() -> TAssistantExecutionRecord:
  at = datetime(2026, 9, 3, 9, 30, tzinfo=timezone.utc)
  return TAssistantExecutionRecord(
    execution_id="execution-1",
    config_id="config-1",
    config_version_id="config-version-1",
    frozen_config_version=1,
    config_snapshot_hash="a" * 64,
    account_id="account-1",
    environment="PAPER",
    entry_authorization="MANUAL_CONFIRM",
    rollout_stage="CANARY",
    status="WARMING",
    entry_readiness="WARMING",
    entry_readiness_reasons=["T_REWARM_REQUIRED"],
    entry_readiness_as_of=at,
    policy_version="policy-1",
    feature_schema_version=1,
    scorer_mode="RULE_ONLY",
    model_runtime_binding=None,
    universe_revision=0,
    last_assigned_cycle_sequence=0,
    last_committed_cycle_sequence=0,
    checkpoint_revision=0,
    state_version=1,
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
    created = await repository.create_or_get(
      account_id="account-1", state=state, execution_environment="PAPER"
    )
    duplicate = await repository.create_or_get(
      account_id="account-1", state=state, execution_environment="PAPER"
    )
    assert duplicate.id == created.id
    assert duplicate.state_version == 1
    with pytest.raises(ValueError, match="证券账户"):
      await repository.create_or_get(
        account_id="", state=state, execution_environment="PAPER"
      )
    with pytest.raises(ValueError, match="证券账户不一致"):
      await repository.create_or_get(
        account_id="account-2", state=state, execution_environment="PAPER"
      )

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
async def test_paper_t_assistant_outcome_has_nullable_run_witness_and_owner_cas():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(TAssistantExecutionRecord.__table__.create)
    await connection.run_sync(TTradeCandidateOutcome.__table__.create)
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  owner = ExecutionOwnerRef(
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
    "execution-1",
  )
  state = start_candidate_outcome(
    CandidateOutcomeDefinition(
      candidate_id="shadow-candidate-1",
      candidate_fingerprint="b" * 64,
      strategy_run_id=None,
      execution_ref=owner,
      execution_environment=ExecutionEnvironment.PAPER,
      instrument_code="600000.SH",
      source_time_ms=1_000_000,
      tick_ordinal=10,
      continuity_generation="generation-1",
      reference_price=10.0,
      policy_version="policy-1",
      feature_schema_version="1",
      horizons_seconds=(1,),
      max_observation_gap_ms=2_000,
    )
  )
  try:
    async with sessions() as db:
      db.add(_paper_execution())
      await db.commit()
      repository = TTradeCandidateOutcomeRepository(db)
      created = await repository.create_or_get(account_id="account-1", state=state)

      assert created.owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value
      assert created.owner_id == "execution-1"
      assert created.environment == ExecutionEnvironment.PAPER.value
      assert created.strategy_run_id is None
      assert await repository.get(strategy_run_id="execution-1", candidate_id=state.definition.candidate_id) is None

      observe_candidate_outcome(
        state,
        CandidatePriceObservation(1_001_000, 11, "generation-1", 10.2),
      )
      saved = await repository.save(state=state, expected_version=1)
      restored = repository.state_from_row(saved)
      assert restored.definition.execution_ref == owner
      assert restored.definition.strategy_run_id is None
      assert restored.horizons[0].observed_price == 10.2
  finally:
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
  repository.get_for_owner = AsyncMock(
    side_effect=[None, SimpleNamespace(account_id="account-2")]
  )

  with pytest.raises(ValueError, match="证券账户不一致"):
    await repository.create_or_get(
      account_id="account-1", state=_state(), execution_environment="PAPER"
    )

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
      execution_environment="PAPER",
    )
    await repository.create_or_get(
      account_id="account-2",
      state=_state(candidate_id="candidate-b", fingerprint="b" * 64),
      execution_environment="PAPER",
    )
    await repository.create_or_get(
      account_id="account-1",
      state=_state(
        candidate_id="candidate-c",
        fingerprint="c" * 64,
        strategy_run_id="run-2",
      ),
      execution_environment="PAPER",
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
    await repository.create_or_get(
      account_id="account-1", state=_state(), execution_environment="PAPER"
    )
    with pytest.raises(ValueError, match="冻结身份不一致"):
      await repository.create_or_get(
        account_id="account-1",
        state=_state(fingerprint="b" * 64),
        execution_environment="PAPER",
      )
  await engine.dispose()


@pytest.mark.asyncio
async def test_repository_delete_for_run_joins_caller_transaction() -> None:
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
      state=_state(candidate_id="old-run", strategy_run_id="run-delete"),
      execution_environment="PAPER",
    )
    await repository.create_or_get(
      account_id="account-1",
      state=_state(candidate_id="other-run", strategy_run_id="run-keep"),
      execution_environment="PAPER",
    )
    assert await repository.delete_for_run("run-delete", commit=False) == 1
    assert (
      await repository.get(
        strategy_run_id="run-delete",
        candidate_id="old-run",
      )
      is None
    )
    assert (
      await repository.get(
        strategy_run_id="run-keep",
        candidate_id="other-run",
      )
      is not None
    )
    await db.rollback()

  async with sessions() as db:
    assert (
      await TTradeCandidateOutcomeRepository(db).get(
        strategy_run_id="run-delete",
        candidate_id="old-run",
      )
      is not None
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
        execution_environment="PAPER",
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
    await repository.create_or_get(
      account_id="account-1", state=state, execution_environment="PAPER"
    )
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
