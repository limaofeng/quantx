from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateOutcomeState,
  CandidateOutcomeStatus,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import StrategyRuntimeEvent
from quantx_infrastructure.models.t_trade_candidate_outcome import (
  TTradeCandidateOutcome,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_trade_candidate_outcome_repository import (
  CandidateOutcomeConcurrencyError,
  TTradeCandidateOutcomeRepository,
)
from quantx_infrastructure.services.t_trade_candidate_outcome_service import (
  TTradeCandidateOutcomePersistenceFacade,
  TTradeCandidateOutcomeService,
  _authoritative_entry_complete,
  _authoritative_entry_target_volume,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class _Repository:
  def __init__(self) -> None:
    self.rows: dict[tuple[str, str], SimpleNamespace] = {}
    self.unfinalized_page_sizes: list[int] = []
    self.unfinalized_page_limits: list[int] = []

  async def get(self, *, strategy_run_id: str, candidate_id: str):
    return self.rows.get((strategy_run_id, candidate_id))

  async def list_observing(
    self,
    *,
    strategy_run_id: str,
    instrument_code: str | None = None,
  ):
    return [
      row
      for row in self.rows.values()
      if row.strategy_run_id == strategy_run_id
      and (row.status == "OBSERVING" or row.post_fill_status == "OBSERVING")
      and (instrument_code is None or row.instrument_code == instrument_code)
    ]

  async def create_or_get(self, *, account_id: str, state: CandidateOutcomeState):
    key = (state.definition.strategy_run_id, state.definition.candidate_id)
    row = self.rows.get(key)
    if row is None:
      row = SimpleNamespace(
        account_id=account_id,
        strategy_run_id=key[0],
        candidate_id=key[1],
        instrument_code=state.definition.instrument_code,
        status=state.status.value,
        post_fill_status=state.post_fill.status.value,
        state=deepcopy(state.to_dict()),
        state_version=1,
      )
      self.rows[key] = row
    return row

  async def list_unfinalized(
    self,
    *,
    strategy_run_id: str,
    after_candidate_id: str | None,
    limit: int,
  ):
    rows = sorted(
      [
      row
      for row in self.rows.values()
      if row.strategy_run_id == strategy_run_id
      and (after_candidate_id is None or row.candidate_id > after_candidate_id)
      and (
        row.status == "OBSERVING"
        or row.post_fill_status in {"WAITING_ENTRY", "OBSERVING"}
      )
      ],
      key=lambda row: row.candidate_id,
    )[:limit]
    self.unfinalized_page_limits.append(limit)
    self.unfinalized_page_sizes.append(len(rows))
    return rows

  async def save(self, *, state: CandidateOutcomeState, expected_version: int):
    key = (state.definition.strategy_run_id, state.definition.candidate_id)
    row = self.rows[key]
    assert row.state_version == expected_version
    row.state = deepcopy(state.to_dict())
    row.status = state.status.value
    row.post_fill_status = state.post_fill.status.value
    row.state_version += 1
    return row

  @staticmethod
  def state_from_row(row) -> CandidateOutcomeState:
    return CandidateOutcomeState.from_dict(deepcopy(row.state))


def _event() -> dict:
  return {
    "record_kind": "MATERIAL",
    "event_type": "CANDIDATE_LATCHED",
    "instrument_code": "600000.SH",
    "signal_snapshot": {
      "candidate_id": "candidate-1",
      "candidate_fingerprint": "a" * 64,
      "source_time_ms": 1_000_000,
      "tick_ordinal": 10,
      "continuity_generation": "3",
      "policy_version": "policy-3",
      "feature_schema_version": 1,
      "profile_version": "profile-1",
      "profile_fingerprint": "b" * 64,
      "features": {"price": 10.0},
    },
  }


def test_entry_completion_requires_terminal_status_and_cumulative_volume() -> None:
  intent = SimpleNamespace(
    status="PARTIAL_FILLED", executed_volume=100, target_volume=200
  )
  assert _authoritative_entry_complete(intent, {}) is False
  intent.status = "FILLED"
  assert _authoritative_entry_complete(intent, {}) is False
  intent.executed_volume = 200
  assert _authoritative_entry_complete(intent, {}) is True


def test_amount_target_uses_persisted_sized_volume_as_execution_truth() -> None:
  intent = SimpleNamespace(
    status="FILLED",
    executed_volume=200,
    target_amount=2_000.0,
    target_volume=None,
    intent_metadata={"sized_volume": 200},
  )

  assert _authoritative_entry_target_volume(intent, {}) == 200
  assert _authoritative_entry_complete(intent, {}) is True

  intent.executed_volume = 100
  assert _authoritative_entry_complete(intent, {}) is False


def test_report_requested_volume_precedes_stale_intent_sizing_metadata() -> None:
  intent = SimpleNamespace(
    status="FILLED",
    executed_volume=100,
    target_volume=None,
    intent_metadata={"sized_volume": 200},
  )

  assert (
    _authoritative_entry_target_volume(
      intent,
      {"requested_entry_volume": 100},
    )
    == 100
  )
  assert (
    _authoritative_entry_complete(
      intent,
      {"requested_entry_volume": 100},
    )
    is True
  )


@pytest.mark.asyncio
async def test_seed_is_restart_safe_and_observation_resumes_from_repository() -> None:
  repository = _Repository()
  first = TTradeCandidateOutcomeService(
    repository, horizons_seconds=(1, 2), max_observation_gap_ms=1_500
  )
  await first.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )
  await first.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )
  assert len(repository.rows) == 1

  await first.observe_tick(
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    source_time_ms=1_001_000,
    tick_ordinal=11,
    continuity_generation="3",
    price=10.1,
  )
  restarted = TTradeCandidateOutcomeService(
    repository, horizons_seconds=(1, 2), max_observation_gap_ms=1_500
  )
  states = await restarted.observe_tick(
    strategy_run_id="run-1",
    instrument_code="600000.SH",
    source_time_ms=1_002_000,
    tick_ordinal=12,
    continuity_generation="3",
    price=10.2,
  )
  assert states[0].status is CandidateOutcomeStatus.MATURED
  assert states[0].horizons[0].observed_price == pytest.approx(10.1)
  assert states[0].horizons[1].observed_price == pytest.approx(10.2)


@pytest.mark.asyncio
async def test_fill_uses_authoritative_fee_and_is_idempotent() -> None:
  repository = _Repository()
  service = TTradeCandidateOutcomeService(repository, horizons_seconds=(1,))
  await service.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )

  for _ in range(2):
    await service.record_fill(
      strategy_run_id="run-1",
      candidate_id="candidate-1",
      fill_id="trade-1",
      role="ENTRY",
      source_time_ms=1_000_500,
      price=10.0,
      volume=100,
      fee=None,
    )
  state = _Repository.state_from_row(next(iter(repository.rows.values())))
  assert state.execution.entry_volume == 100
  assert state.execution.entry_fee is None


@pytest.mark.asyncio
async def test_trade_adapter_reads_broker_trade_id_price_volume_and_commission() -> (
  None
):
  repository = _Repository()
  service = TTradeCandidateOutcomeService(repository, horizons_seconds=(1,))
  await service.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )
  await service.record_trade_fact(
    strategy_run_id="run-1",
    trade=SimpleNamespace(
      trade_id="trade-1",
      trade_time=datetime.fromtimestamp(1_001),
      price=10.0,
      volume=100,
      commission=3.5,
      instrument_code="600000.SH",
      metadata={
        "account_id": "account-1",
        "strategy_run_id": "run-1",
        "instrument_code": "600000.SH",
        "candidate_id": "candidate-1",
        "candidate_fingerprint": "a" * 64,
        "policy_version": "policy-3",
        "t_trade_role": "entry",
        "entry_complete": True,
        "requested_entry_volume": 100,
      },
    ),
  )
  state = _Repository.state_from_row(next(iter(repository.rows.values())))
  assert state.execution.entry_volume == 100
  assert state.execution.entry_fee == pytest.approx(3.5)
  assert state.execution.entry_frozen is True
  assert state.post_fill.armed_at_ms == 1_001_000


@pytest.mark.asyncio
async def test_live_placeholder_commission_is_not_authoritative_fee_truth() -> None:
  repository = _Repository()
  service = TTradeCandidateOutcomeService(repository, horizons_seconds=(1,))
  await service.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )

  await service.record_trade_fact(
    strategy_run_id="run-1",
    trade=SimpleNamespace(
      trade_id="trade-live-1",
      trade_time=datetime.fromtimestamp(1_001),
      price=10.0,
      volume=100,
      commission=0.0,
      instrument_code="600000.SH",
      metadata={
        "account_id": "account-1",
        "strategy_run_id": "run-1",
        "instrument_code": "600000.SH",
        "candidate_id": "candidate-1",
        "candidate_fingerprint": "a" * 64,
        "policy_version": "policy-3",
        "t_trade_role": "entry",
        "entry_complete": True,
        "requested_entry_volume": 100,
      },
    ),
    fee_is_authoritative=False,
  )

  state = _Repository.state_from_row(next(iter(repository.rows.values())))
  assert state.execution.entry_fee is None
  assert state.post_fill.net_available is False


@pytest.mark.asyncio
async def test_primary_trade_adapter_rejects_cross_scope_candidate_metadata() -> None:
  repository = _Repository()
  service = TTradeCandidateOutcomeService(repository, horizons_seconds=(1,))
  await service.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )

  with pytest.raises(ValueError, match="作用域"):
    await service.record_trade_fact(
      strategy_run_id="run-1",
      trade=SimpleNamespace(
        trade_id="trade-cross-scope",
        trade_time=datetime.fromtimestamp(1_001),
        instrument_code="600000.SH",
        price=10.0,
        volume=100,
        commission=3.5,
        metadata={
          "account_id": "another-account",
          "strategy_run_id": "run-1",
          "instrument_code": "600000.SH",
          "candidate_id": "candidate-1",
          "candidate_fingerprint": "a" * 64,
          "policy_version": "policy-3",
          "t_trade_role": "entry",
          "entry_complete": True,
          "requested_entry_volume": 100,
        },
      ),
    )

  state = _Repository.state_from_row(next(iter(repository.rows.values())))
  assert state.execution.entry_volume == 0


@pytest.mark.asyncio
async def test_finalize_run_marks_unmatured_window_unavailable() -> None:
  repository = _Repository()
  service = TTradeCandidateOutcomeService(repository, horizons_seconds=(60,))
  await service.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )
  result = await service.finalize_run(
    strategy_run_id="run-1", finalized_at_ms=1_030_000
  )
  state = _Repository.state_from_row(next(iter(repository.rows.values())))
  assert result.finalized_count == 1
  assert result.concurrently_finalized_count == 0
  assert result.page_count == 1
  assert state.status is CandidateOutcomeStatus.UNAVAILABLE
  assert state.horizons[0].return_pct is None

  repeated = await service.finalize_run(
    strategy_run_id="run-1", finalized_at_ms=1_030_000
  )
  assert repeated.examined_count == 0
  assert repeated.page_count == 0


@pytest.mark.asyncio
async def test_finalize_run_pages_large_waiting_entry_population_with_bounded_reads() -> (
  None
):
  repository = _Repository()
  service = TTradeCandidateOutcomeService(
    repository,
    horizons_seconds=(60,),
    finalize_page_size=17,
  )
  for ordinal in range(513):
    event = deepcopy(_event())
    snapshot = event["signal_snapshot"]
    snapshot["candidate_id"] = f"candidate-{ordinal:04d}"
    snapshot["source_time_ms"] = 1_000_000 + ordinal
    snapshot["tick_ordinal"] = 10 + ordinal
    await service.seed_material_event(
      account_id="account-1",
      strategy_run_id="run-1",
      event=event,
    )

  result = await service.finalize_run(
    strategy_run_id="run-1",
    finalized_at_ms=2_000_000,
  )

  assert result.finalized_count == 513
  assert result.concurrently_finalized_count == 0
  assert result.examined_count == 513
  assert result.page_count == 31
  assert repository.unfinalized_page_limits == [17] * 32
  assert max(repository.unfinalized_page_sizes) == 17
  assert repository.unfinalized_page_sizes[-1] == 0
  assert all(
    row.post_fill_status == "UNAVAILABLE" for row in repository.rows.values()
  )


@pytest.mark.asyncio
async def test_finalize_run_accepts_concurrent_terminal_cas_as_idempotent() -> None:
  class ConcurrentRepository(_Repository):
    async def save(self, *, state: CandidateOutcomeState, expected_version: int):
      row = self.rows[(state.definition.strategy_run_id, state.definition.candidate_id)]
      row.state = deepcopy(state.to_dict())
      row.status = state.status.value
      row.post_fill_status = state.post_fill.status.value
      row.state_version = expected_version + 1
      raise CandidateOutcomeConcurrencyError("concurrent terminal write")

  repository = ConcurrentRepository()
  service = TTradeCandidateOutcomeService(repository, horizons_seconds=(60,))
  await service.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )

  result = await service.finalize_run(
    strategy_run_id="run-1", finalized_at_ms=1_030_000
  )

  assert result.finalized_count == 0
  assert result.concurrently_finalized_count == 1
  assert result.examined_count == 1


@pytest.mark.asyncio
async def test_finalize_run_surfaces_cas_conflict_when_row_remains_open() -> None:
  class ConflictingRepository(_Repository):
    async def save(self, *, state: CandidateOutcomeState, expected_version: int):
      raise CandidateOutcomeConcurrencyError("open row changed concurrently")

  repository = ConflictingRepository()
  service = TTradeCandidateOutcomeService(repository, horizons_seconds=(60,))
  await service.seed_material_event(
    account_id="account-1", strategy_run_id="run-1", event=_event()
  )

  with pytest.raises(CandidateOutcomeConcurrencyError, match="open row"):
    await service.finalize_run(
      strategy_run_id="run-1", finalized_at_ms=1_030_000
    )

  row = next(iter(repository.rows.values()))
  assert row.status == "OBSERVING"
  assert row.post_fill_status == "WAITING_ENTRY"


async def _repair_sessions():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          TTradeCandidateOutcome.__table__,
          TTradeOpportunityEvaluation.__table__,
          TradeIntentRecord.__table__,
          StrategyRuntimeEvent.__table__,
        ],
      )
    )
  return engine, async_sessionmaker(engine, expire_on_commit=False)


def _repair_evaluation(
  candidate_id: str = "candidate-1",
  *,
  account_id: str = "account-1",
  instrument_code: str = "600000.SH",
) -> TTradeOpportunityEvaluation:
  snapshot = deepcopy(_event()["signal_snapshot"])
  snapshot["candidate_id"] = candidate_id
  return TTradeOpportunityEvaluation(
    id=f"evaluation-{candidate_id}",
    event_key=f"run-1:{instrument_code}:{candidate_id}",
    account_id=account_id,
    strategy_run_id="run-1",
    instrument_code=instrument_code,
    candidate_id=candidate_id,
    evaluated_at=datetime(2026, 8, 23, 1, 30, tzinfo=timezone.utc),
    record_kind="MATERIAL",
    event_type="CANDIDATE_LATCHED",
    coalesced_count=1,
    policy_version="policy-3",
    schema_version="3",
    content_fingerprint="c" * 64,
    payload={"signal_snapshot": snapshot},
    metrics={},
  )


def _repair_intent(
  intent_id: str = "intent-1",
  *,
  account_id: str = "account-1",
  candidate_id: str = "candidate-1",
  instrument_code: str = "600000.SH",
) -> TradeIntentRecord:
  return TradeIntentRecord(
    id=intent_id,
    strategy_run_id="run-1",
    owner_type="STRATEGY_RUN",
    owner_id="run-1",
    account_id=account_id,
    instrument_code=instrument_code,
    direction="BUY",
    bucket="swing",
    reason="T_TRADE_ENTRY",
    priority="NORMAL",
    confidence=1.0,
    target_volume=100,
    status="FILLED",
    executed_price=10.0,
    executed_volume=100,
    executed_time=datetime(2026, 8, 23, 1, 30, tzinfo=timezone.utc),
    intent_metadata={
      "account_id": account_id,
      "instrument_code": instrument_code,
      "candidate_id": candidate_id,
      "candidate_fingerprint": "a" * 64,
      "policy_version": "policy-3",
      "t_trade_role": "entry",
      "requested_entry_volume": 100,
    },
  )


def _repair_runtime_event(
  event_id: str,
  *,
  candidate_id: str = "candidate-1",
  intent_id: str = "intent-1",
  account_id: str = "account-1",
  instrument_code: str = "600000.SH",
  traded_time: str = "2026-08-23T01:31:00Z",
  created_second: int = 0,
) -> StrategyRuntimeEvent:
  created_at = datetime(2026, 8, 23, 1, 31, created_second, tzinfo=timezone.utc)
  return StrategyRuntimeEvent(
    event_id=event_id,
    business_key=f"trade:{event_id}",
    strategy_run_id="run-1",
    client_order_id=f"client-{event_id}",
    broker_order_id="1001",
    event_type="TRADE",
    payload={
      "metadata": {
        "strategy_run_id": "run-1",
        "account_id": account_id,
        "candidate_id": candidate_id,
        "candidate_fingerprint": "a" * 64,
        "policy_version": "policy-3",
        "intent_id": intent_id,
        "instrument_code": instrument_code,
        "requested_entry_volume": 100,
        "t_trade_role": "entry",
      },
      "report": {
        "execution_id": f"execution-{event_id}",
        "stock_code": instrument_code,
        "traded_time": traded_time,
        "traded_price": 10.0,
        "traded_volume": 100,
      },
    },
    application_status="APPLIED",
    application_attempts=1,
    created_at=created_at,
    applied_at=created_at,
  )


@pytest.mark.asyncio
async def test_applied_trade_reconciliation_repairs_missing_seed_and_fill_once() -> (
  None
):
  engine, sessions = await _repair_sessions()
  async with sessions() as db:
    db.add_all(
      [
        _repair_evaluation(),
        _repair_intent(),
        _repair_runtime_event("runtime-event-1"),
      ]
    )
    await db.commit()

  facade = TTradeCandidateOutcomePersistenceFacade(sessions)
  first = await facade.reconcile_applied_trade_events(
    strategy_run_id="run-1",
  )
  second = await facade.reconcile_applied_trade_events(
    strategy_run_id="run-1",
  )
  assert first.repaired_count == 1
  assert first.quarantined_count == 0
  assert first.complete is True
  assert second.examined_count == 0
  assert second.repaired_count == 0

  async with sessions() as db:
    row = await TTradeCandidateOutcomeRepository(db).get(
      strategy_run_id="run-1",
      candidate_id="candidate-1",
    )
    assert row is not None
    state = TTradeCandidateOutcomeRepository.state_from_row(row)
  assert state.execution.applied_fill_ids == ["execution-runtime-event-1"]
  assert state.execution.entry_volume == 100
  assert state.execution.entry_fee is None
  assert state.execution.entry_frozen is True
  await engine.dispose()


@pytest.mark.asyncio
async def test_applied_trade_reconciliation_uses_one_run_cursor_for_all_instruments() -> (
  None
):
  engine, sessions = await _repair_sessions()
  async with sessions() as db:
    db.add_all(
      [
        _repair_evaluation(),
        _repair_intent(),
        _repair_runtime_event("runtime-event-1"),
        _repair_evaluation(
          "candidate-2",
          instrument_code="000001.SZ",
        ),
        _repair_intent(
          "intent-2",
          candidate_id="candidate-2",
          instrument_code="000001.SZ",
        ),
        _repair_runtime_event(
          "runtime-event-2",
          candidate_id="candidate-2",
          intent_id="intent-2",
          instrument_code="000001.SZ",
          created_second=1,
        ),
      ]
    )
    await db.commit()

  facade = TTradeCandidateOutcomePersistenceFacade(sessions, repair_page_size=1)
  first = await facade.reconcile_applied_trade_events(strategy_run_id="run-1")
  second = await facade.reconcile_applied_trade_events(strategy_run_id="run-1")
  exhausted = await facade.reconcile_applied_trade_events(strategy_run_id="run-1")

  assert first.has_more is True
  assert first.repaired_count == 1
  assert second.complete is True
  assert second.repaired_count == 1
  assert {
    state.definition.instrument_code for state in (*first.states, *second.states)
  } == {
    "600000.SH",
    "000001.SZ",
  }
  assert exhausted.examined_count == 0
  await engine.dispose()


@pytest.mark.asyncio
async def test_live_primary_path_rejects_cross_scope_intent_before_freezing_entry() -> (
  None
):
  engine, sessions = await _repair_sessions()
  async with sessions() as db:
    db.add(_repair_intent(account_id="another-account"))
    await db.commit()
  facade = TTradeCandidateOutcomePersistenceFacade(sessions)
  await facade.seed_material_event(
    account_id="account-1",
    strategy_run_id="run-1",
    event=_event(),
  )

  with pytest.raises(ValueError, match="INTENT_SCOPE_MISMATCH"):
    await facade.record_trade_fact(
      strategy_run_id="run-1",
      trade=SimpleNamespace(
        trade_id="trade-live-cross-scope",
        trade_time=datetime(2026, 8, 23, 1, 31, tzinfo=timezone.utc),
        instrument_code="600000.SH",
        price=10.0,
        volume=100,
        commission=0.0,
        metadata={
          "account_id": "account-1",
          "strategy_run_id": "run-1",
          "instrument_code": "600000.SH",
          "candidate_id": "candidate-1",
          "candidate_fingerprint": "a" * 64,
          "policy_version": "policy-3",
          "intent_id": "intent-1",
          "t_trade_role": "entry",
          "requested_entry_volume": 100,
        },
      ),
      entry_complete=None,
      authoritative_fee=None,
      fee_is_authoritative=False,
      entry_target_volume=None,
    )

  async with sessions() as db:
    row = await TTradeCandidateOutcomeRepository(db).get(
      strategy_run_id="run-1", candidate_id="candidate-1"
    )
    assert row is not None
    state = TTradeCandidateOutcomeRepository.state_from_row(row)
  assert state.execution.entry_volume == 0
  await engine.dispose()


@pytest.mark.asyncio
async def test_poison_event_is_quarantined_and_later_valid_event_is_repaired() -> None:
  engine, sessions = await _repair_sessions()
  async with sessions() as db:
    db.add_all(
      [
        _repair_evaluation(),
        _repair_intent(),
        _repair_runtime_event(
          "runtime-event-poison",
          candidate_id="candidate-poison",
          created_second=0,
        ),
        _repair_runtime_event("runtime-event-valid", created_second=1),
      ]
    )
    await db.commit()

  facade = TTradeCandidateOutcomePersistenceFacade(sessions, repair_page_size=1)
  poison_page = await facade.reconcile_applied_trade_events(
    strategy_run_id="run-1"
  )
  valid_page = await facade.reconcile_applied_trade_events(
    strategy_run_id="run-1"
  )
  exhausted = await facade.reconcile_applied_trade_events(
    strategy_run_id="run-1"
  )

  assert poison_page.has_more is True
  assert poison_page.quarantined_count == 1
  assert poison_page.issue_counts == (("MATERIAL_EVALUATION_MISSING", 1),)
  assert valid_page.complete is True
  assert valid_page.repaired_count == 1
  assert exhausted.examined_count == 0
  async with sessions() as db:
    valid = await TTradeCandidateOutcomeRepository(db).get(
      strategy_run_id="run-1", candidate_id="candidate-1"
    )
    poison = await TTradeCandidateOutcomeRepository(db).get(
      strategy_run_id="run-1", candidate_id="candidate-poison"
    )
  assert valid is not None
  assert poison is None
  await engine.dispose()


@pytest.mark.asyncio
async def test_cursor_waits_for_older_pending_event_before_later_applied_fact() -> None:
  engine, sessions = await _repair_sessions()
  pending = _repair_runtime_event("runtime-event-pending", created_second=0)
  pending.event_type = "ORDER"
  pending.application_status = "PENDING"
  pending.applied_at = None
  valid = _repair_runtime_event(
    "runtime-event-valid",
    candidate_id="candidate-2",
    intent_id="intent-2",
    instrument_code="000001.SZ",
    created_second=1,
  )
  async with sessions() as db:
    db.add_all(
      [
        _repair_evaluation(
          "candidate-2",
          instrument_code="000001.SZ",
        ),
        _repair_intent(
          "intent-2",
          candidate_id="candidate-2",
          instrument_code="000001.SZ",
        ),
        pending,
        valid,
      ]
    )
    await db.commit()

  facade = TTradeCandidateOutcomePersistenceFacade(sessions)
  blocked = await facade.reconcile_applied_trade_events(
    strategy_run_id="run-1"
  )
  assert blocked.deferred_count == 1
  assert blocked.has_more is True
  assert blocked.repaired_count == 0

  async with sessions() as db:
    stored = await db.get(StrategyRuntimeEvent, "runtime-event-pending")
    assert stored is not None
    stored.application_status = "APPLIED"
    stored.applied_at = datetime(2026, 8, 23, 1, 32, tzinfo=timezone.utc)
    await db.commit()

  resumed = await facade.reconcile_applied_trade_events(
    strategy_run_id="run-1"
  )
  assert resumed.complete is True
  assert resumed.repaired_count == 1
  assert resumed.skipped_count == 1
  await engine.dispose()


@pytest.mark.asyncio
async def test_cross_scope_intent_is_quarantined_without_analytics_pollution() -> None:
  engine, sessions = await _repair_sessions()
  async with sessions() as db:
    db.add_all(
      [
        _repair_evaluation(),
        _repair_intent(account_id="account-other"),
        _repair_runtime_event("runtime-event-cross-scope"),
      ]
    )
    await db.commit()

  result = await TTradeCandidateOutcomePersistenceFacade(
    sessions
  ).reconcile_applied_trade_events(strategy_run_id="run-1")
  assert result.quarantined_count == 1
  assert result.issue_counts == (("INTENT_SCOPE_MISMATCH", 1),)
  async with sessions() as db:
    row = await TTradeCandidateOutcomeRepository(db).get(
      strategy_run_id="run-1", candidate_id="candidate-1"
    )
  assert row is None
  await engine.dispose()
