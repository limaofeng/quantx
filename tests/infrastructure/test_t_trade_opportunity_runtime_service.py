from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_infrastructure.database.connection as database_connection
import quantx_infrastructure.repositories.trade_intent_repository as intent_repository
import quantx_infrastructure.services.t_trade_opportunity_runtime_service as opportunity_runtime_service_module
from quantx_domain.strategies.base import (
    TradeIntent,
    TradeIntentDirection,
)
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
    TTradeOpportunityEvaluation,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
    TTradeOpportunityEvaluationRepository,
)
from quantx_infrastructure.services.t_trade_opportunity_runtime_service import (
    TTradeOpportunityRuntimeService,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _snapshot() -> dict:
    return {
        "evaluated_at_ms": 1_724_300_000_000,
        "source_time_ms": 1_724_300_000_000,
        "tick_ordinal": 3,
        "continuity_generation": "1",
        "features": {"sample_count": 25},
        "pullback": {"phase": "REBOUND_CONFIRMING"},
        "momentum": {"phase": "BASELINING"},
        "preview_threshold": 55.0,
        "candidate_threshold": 72.0,
        "revalidate_threshold": 60.0,
        "rearm_threshold": 45.0,
        "signal_version": 7,
        "candidate_state_version": 7,
        "state_schema_version": 3,
        "feature_schema_version": 1,
        "policy_version": "t_trade_opportunity_v3.0.0",
        "config_version": 3,
        "data_health": "READY",
        "opportunity_score": 74.0,
    }


@pytest.mark.asyncio
async def test_materialize_material_evaluation_uses_stable_snapshot_payload():
    repository = SimpleNamespace(append_material=AsyncMock())
    service = TTradeOpportunityRuntimeService()
    event = {
        "type": "T_TRADE_OPPORTUNITY_EVALUATION",
        "event_key": "run-1:600000.SH:1:1724300000000:3:MATERIAL",
        "record_kind": "MATERIAL",
        "event_type": "CANDIDATE_LATCHED",
        "instrument_code": "600000.sh",
        "evaluated_at_ms": 1_724_300_000_000,
        "signal_snapshot": {
            **_snapshot(),
            "blockers": ["SCORE_BELOW_CANDIDATE"],
        },
        "external_blockers": ["T_TRADE_RECONCILIATION_REQUIRED"],
    }

    await service.materialize_evaluation(
        event=event,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )

    kwargs = repository.append_material.await_args.kwargs
    assert kwargs["event_key"] == event["event_key"]
    assert kwargs["instrument_code"] == "600000.SH"
    assert kwargs["event_type"] == "CANDIDATE_LATCHED"
    assert kwargs["payload"] == {
        "signal_snapshot": {
            **_snapshot(),
            "blockers": ["SCORE_BELOW_CANDIDATE"],
            "top_blockers": [
                {
                    "code": "T_TRADE_RECONCILIATION_REQUIRED",
                    "label": "做 T 状态需要对账",
                    "detail": "外部发意图门禁未通过",
                },
                {
                    "code": "SCORE_BELOW_CANDIDATE",
                    "label": "机会分未到候选阈值",
                    "detail": "",
                },
            ],
        },
        "external_blockers": ["T_TRADE_RECONCILIATION_REQUIRED"],
    }


@pytest.mark.asyncio
async def test_materialize_diagnostic_coalesces_latest_event_in_source_time_window():
    repository = SimpleNamespace(append_coalesced_diagnostic=AsyncMock())
    service = TTradeOpportunityRuntimeService()
    first = {
        "type": "T_TRADE_OPPORTUNITY_EVALUATION",
        "event_key": "run-1:600000.SH:1:1724300002100:4:DIAGNOSTIC",
        "record_kind": "COALESCED_DIAGNOSTIC",
        "event_type": "HEARTBEAT",
        "instrument_code": "600000.SH",
        "evaluated_at_ms": 1_724_300_002_100,
        "signal_snapshot": _snapshot(),
    }
    latest = {
        **first,
        "event_key": "run-1:600000.SH:1:1724300002900:5:DIAGNOSTIC",
        "evaluated_at_ms": 1_724_300_002_900,
        "metrics": {"latest": True},
    }
    next_window = {
        **first,
        "event_key": "run-1:600000.SH:1:1724300004100:6:DIAGNOSTIC",
        "evaluated_at_ms": 1_724_300_004_100,
    }

    await service.materialize_evaluation(
        event=first,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )
    await service.materialize_evaluation(
        event=latest,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )
    repository.append_coalesced_diagnostic.assert_not_awaited()
    await service.materialize_evaluation(
        event=next_window,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )

    kwargs = repository.append_coalesced_diagnostic.await_args.kwargs
    assert kwargs["event_key"] == (
        "run-1:600000.SH:DIAGNOSTIC:1724300002000"
    )
    assert kwargs["coalesced_count"] == 2
    assert kwargs["window_started_at"] < kwargs["window_ended_at"]
    assert kwargs["window_ended_at"] == kwargs["evaluated_at"]
    assert kwargs["metrics"] == {"latest": True}


def _diagnostic_event(
    instrument_code: str,
    *,
    evaluated_at_ms: int,
    source_time_ms: int,
    ordinal: int,
) -> dict:
    return {
        "type": "T_TRADE_OPPORTUNITY_EVALUATION",
        "event_key": (
            f"run-1:{instrument_code}:{evaluated_at_ms}:{ordinal}:DIAGNOSTIC"
        ),
        "record_kind": "COALESCED_DIAGNOSTIC",
        "event_type": "HEARTBEAT",
        "instrument_code": instrument_code,
        "evaluated_at_ms": evaluated_at_ms,
        "signal_snapshot": {
            **_snapshot(),
            "source_time_ms": source_time_ms,
        },
    }


def _material_event(
    instrument_code: str,
    *,
    evaluated_at_ms: int,
    source_time_ms: int,
    ordinal: int,
) -> dict:
    return {
        "type": "T_TRADE_OPPORTUNITY_EVALUATION",
        "event_key": f"run-1:{instrument_code}:{evaluated_at_ms}:{ordinal}:MATERIAL",
        "record_kind": "MATERIAL",
        "event_type": "CANDIDATE_LATCHED",
        "instrument_code": instrument_code,
        "evaluated_at_ms": evaluated_at_ms,
        "signal_snapshot": {
            **_snapshot(),
            "source_time_ms": source_time_ms,
        },
    }


@pytest.mark.asyncio
async def test_diagnostic_windows_are_bounded_and_evict_sparse_streams_by_source_time():
    repository = SimpleNamespace(append_coalesced_diagnostic=AsyncMock())
    service = TTradeOpportunityRuntimeService(
        max_diagnostic_windows=3,
        diagnostic_idle_ms=1_000,
    )

    for ordinal, instrument_code in enumerate(("600000.SH", "000001.SZ", "300001.SZ")):
        await service.materialize_evaluation(
            event=_diagnostic_event(
                instrument_code,
                evaluated_at_ms=10_000 + ordinal * 2_000,
                source_time_ms=10_000,
                ordinal=ordinal,
            ),
            account_id="account-1",
            strategy_run_id="run-1",
            repository=repository,
        )

    assert len(service._diagnostic_windows) == 3

    await service.materialize_evaluation(
        event=_diagnostic_event(
            "600001.SH",
            evaluated_at_ms=20_000,
            source_time_ms=11_001,
            ordinal=4,
        ),
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )

    assert len(service._diagnostic_windows) == 1
    assert set(repository.append_coalesced_diagnostic.await_args_list[i].kwargs["instrument_code"] for i in range(3)) == {
        "000001.SZ",
        "300001.SZ",
        "600000.SH",
    }


@pytest.mark.asyncio
async def test_diagnostic_eviction_failure_reinserts_without_exceeding_bound_and_retries():
    repository = SimpleNamespace(
        append_coalesced_diagnostic=AsyncMock(
            side_effect=[RuntimeError("database unavailable"), object()]
        )
    )
    service = TTradeOpportunityRuntimeService(
        max_diagnostic_windows=1,
        diagnostic_idle_ms=1_000,
    )
    first = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_000,
        source_time_ms=10_000,
        ordinal=1,
    )
    second = _diagnostic_event(
        "000001.SZ",
        evaluated_at_ms=12_000,
        source_time_ms=10_001,
        ordinal=2,
    )

    await service.materialize_evaluation(
        event=first,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.materialize_evaluation(
            event=second,
            account_id="account-1",
            strategy_run_id="run-1",
            repository=repository,
        )

    assert len(service._diagnostic_windows) == 1
    assert next(iter(service._diagnostic_windows.values())).instrument_code == (
        "600000.SH"
    )

    await service.materialize_evaluation(
        event=second,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )

    assert len(service._diagnostic_windows) == 1
    assert next(iter(service._diagnostic_windows.values())).instrument_code == (
        "000001.SZ"
    )
    assert repository.append_coalesced_diagnostic.await_count == 2


@pytest.mark.asyncio
async def test_material_is_retried_when_source_time_eviction_persistence_fails():
    repository = SimpleNamespace(
        append_coalesced_diagnostic=AsyncMock(
            side_effect=[RuntimeError("database unavailable"), object()]
        ),
        append_material=AsyncMock(return_value=object()),
    )
    service = TTradeOpportunityRuntimeService(
        max_diagnostic_windows=2,
        diagnostic_idle_ms=1_000,
    )
    diagnostic = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_000,
        source_time_ms=10_000,
        ordinal=1,
    )
    material = {
        **diagnostic,
        "event_key": "run-1:600001.SH:12_000:2:MATERIAL",
        "record_kind": "MATERIAL",
        "event_type": "CANDIDATE_LATCHED",
        "instrument_code": "600001.SH",
        "evaluated_at_ms": 12_000,
        "signal_snapshot": {
            **_snapshot(),
            "source_time_ms": 12_000,
        },
    }

    await service.materialize_evaluation(
        event=diagnostic,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.materialize_evaluation(
            event=material,
            account_id="account-1",
            strategy_run_id="run-1",
            repository=repository,
        )
    repository.append_material.assert_not_awaited()

    await service.materialize_evaluation(
        event=material,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )
    repository.append_material.assert_awaited_once()


@pytest.mark.asyncio
async def test_flush_diagnostics_persists_final_open_window_once():
    repository = SimpleNamespace(append_coalesced_diagnostic=AsyncMock())
    service = TTradeOpportunityRuntimeService()
    event = {
        "type": "T_TRADE_OPPORTUNITY_EVALUATION",
        "event_key": "run-1:600000.SH:1:1724300004100:6:DIAGNOSTIC",
        "record_kind": "COALESCED_DIAGNOSTIC",
        "event_type": "HEARTBEAT",
        "instrument_code": "600000.SH",
        "evaluated_at_ms": 1_724_300_004_100,
        "signal_snapshot": _snapshot(),
    }

    await service.materialize_evaluation(
        event=event,
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )
    await service.flush_diagnostics(
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )
    await service.flush_diagnostics(
        account_id="account-1",
        strategy_run_id="run-1",
        repository=repository,
    )

    repository.append_coalesced_diagnostic.assert_awaited_once()


@pytest.mark.asyncio
async def test_checkpoint_batch_closes_its_own_segments_without_crossing_material(
    monkeypatch: pytest.MonkeyPatch,
):
    """Same-window diagnostics on either side of MATERIAL stay distinct."""

    service = TTradeOpportunityRuntimeService()
    persisted_batches: list[list[dict]] = []

    async def persist(records):
        persisted_batches.append([dict(record) for record in records])
        return [SimpleNamespace(event_key=record["event_key"]) for record in records]

    monkeypatch.setattr(service, "_persist_checkpoint_records", persist)
    before = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_100,
        source_time_ms=10_100,
        ordinal=1,
    )
    material = _material_event(
        "600000.SH",
        evaluated_at_ms=10_500,
        source_time_ms=10_500,
        ordinal=2,
    )
    after = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_900,
        source_time_ms=10_900,
        ordinal=3,
    )

    receipt = await service.materialize_checkpoint_batch(
        events=[before, material, after],
        account_id="account-1",
        strategy_run_id="run-1",
    )

    assert receipt.persisted_event_keys == (
        before["event_key"],
        material["event_key"],
        after["event_key"],
    )
    assert len(persisted_batches) == 1
    records = persisted_batches[0]
    assert [record["record_kind"] for record in records] == [
        "COALESCED_DIAGNOSTIC",
        "MATERIAL",
        "COALESCED_DIAGNOSTIC",
    ]
    assert [record["coalesced_count"] for record in records] == [1, 1, 1]
    assert records[0]["event_key"] != records[2]["event_key"]
    assert all(":SEGMENT:" in records[index]["event_key"] for index in (0, 2))
    assert service._diagnostic_windows == {}


@pytest.mark.asyncio
async def test_checkpoint_batch_rejects_unsupported_kind_without_touching_windows():
    service = TTradeOpportunityRuntimeService()
    event = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_100,
        source_time_ms=10_100,
        ordinal=1,
    )
    event["record_kind"] = "ACTIONABLE"

    with pytest.raises(ValueError, match="COALESCED_DIAGNOSTIC or MATERIAL"):
        await service.materialize_checkpoint_batch(
            events=[event],
            account_id="account-1",
            strategy_run_id="run-1",
        )

    assert service._diagnostic_windows == {}


@pytest.mark.asyncio
async def test_checkpoint_batch_mixes_diagnostics_and_material_in_one_commit_and_replays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed checkpoint is one ordered heterogeneous, idempotent UoW."""

    class CountingAsyncSession(AsyncSession):
        commit_calls = 0
        execute_calls = 0

        async def execute(self, *args, **kwargs):
            type(self).execute_calls += 1
            return await super().execute(*args, **kwargs)

        async def commit(self) -> None:
            type(self).commit_calls += 1
            await super().commit()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(TTradeOpportunityEvaluation.__table__.create)
    sessions = async_sessionmaker(
        engine,
        class_=CountingAsyncSession,
        expire_on_commit=False,
    )
    reader_sessions = async_sessionmaker(engine, expire_on_commit=False)
    session_yields = 0

    async def fake_get_async_db():
        nonlocal session_yields
        session_yields += 1
        async with sessions() as db:
            yield db

    monkeypatch.setattr(
        opportunity_runtime_service_module,
        "get_async_db",
        fake_get_async_db,
    )
    service = TTradeOpportunityRuntimeService()
    before = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_100,
        source_time_ms=10_100,
        ordinal=1,
    )
    material = _material_event(
        "600000.SH",
        evaluated_at_ms=10_500,
        source_time_ms=10_500,
        ordinal=2,
    )
    after = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_900,
        source_time_ms=10_900,
        ordinal=3,
    )
    events = [before, material, after]
    try:
        receipt = await service.materialize_checkpoint_batch(
            events=events,
            account_id="account-1",
            strategy_run_id="run-1",
        )

        assert session_yields == 1
        assert CountingAsyncSession.commit_calls == 1
        assert CountingAsyncSession.execute_calls == 1
        assert receipt.persisted_event_keys == tuple(event["event_key"] for event in events)
        assert [row.record_kind for row in receipt.records] == [
            "COALESCED_DIAGNOSTIC",
            "MATERIAL",
            "COALESCED_DIAGNOSTIC",
        ]
        first_keys = tuple(row.event_key for row in receipt.records)
        async with reader_sessions() as db:
            assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 3

        replay_receipt = await service.materialize_checkpoint_batch(
            events=events,
            account_id="account-1",
            strategy_run_id="run-1",
        )

        assert replay_receipt.persisted_event_keys == receipt.persisted_event_keys
        assert tuple(row.event_key for row in replay_receipt.records) == first_keys
        async with reader_sessions() as db:
            assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_batch_failure_has_no_partial_commit_and_retries_preaggregated_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed boundary retains source summaries for an exact retry."""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(TTradeOpportunityEvaluation.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def fake_get_async_db():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(
        opportunity_runtime_service_module,
        "get_async_db",
        fake_get_async_db,
    )
    original_append_many = TTradeOpportunityEvaluationRepository.append_many
    append_calls = 0

    async def fail_once_then_append(
        repository: TTradeOpportunityEvaluationRepository,
        records: object,
    ) -> object:
        nonlocal append_calls
        append_calls += 1
        if append_calls == 1:
            raise RuntimeError("database unavailable")
        return await original_append_many(repository, records)

    monkeypatch.setattr(TTradeOpportunityEvaluationRepository, "append_many", fail_once_then_append)
    service = TTradeOpportunityRuntimeService()
    first = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_100,
        source_time_ms=10_100,
        ordinal=1,
    )
    first["coalesced_count"] = 7
    second = _diagnostic_event(
        "600000.SH",
        evaluated_at_ms=10_900,
        source_time_ms=10_900,
        ordinal=2,
    )
    second["checkpoint_coalesced_count"] = 11
    events = [first, second]
    try:
        with pytest.raises(RuntimeError, match="database unavailable"):
            await service.materialize_checkpoint_batch(
                events=events,
                account_id="account-1",
                strategy_run_id="run-1",
            )

        assert service._diagnostic_windows == {}
        async with sessions() as db:
            assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 0

        receipt = await service.materialize_checkpoint_batch(
            events=events,
            account_id="account-1",
            strategy_run_id="run-1",
        )

        assert receipt.persisted_event_keys == (first["event_key"], second["event_key"])
        assert receipt.records[0].coalesced_count == 18
        async with sessions() as db:
            assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 1

        replay_receipt = await service.materialize_checkpoint_batch(
            events=events,
            account_id="account-1",
            strategy_run_id="run-1",
        )

        assert replay_receipt.records[0].coalesced_count == 18
        async with sessions() as db:
            assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_profile_loader_uses_strict_previous_day_cutoff_and_maps_provenance():
    row = SimpleNamespace(
        as_of=datetime(2026, 8, 22, 15, 5),
        profile={
            "pullback_threshold_pct": 0.8,
            "momentum_rise_threshold_pct": 0.9,
            "momentum_amount_velocity_ratio": 2.1,
            "pullback_max_spread_ticks": 3,
            "momentum_max_spread_ticks": 10,
        },
        schema_version="1",
        version="profile-20260822",
        fingerprint="a" * 64,
    )
    repository = SimpleNamespace(latest_at_or_before=AsyncMock(return_value=row))

    profile = await TTradeOpportunityRuntimeService().load_reference_profile(
        instrument_code="600000.SH",
        evaluated_at=datetime(2026, 8, 23, 10, 0),
        repository=repository,
    )

    cutoff = repository.latest_at_or_before.await_args.kwargs["as_of"]
    assert cutoff == datetime(2026, 8, 22, 23, 59, 59, 999999)
    assert profile["as_of_trade_date"] == "2026-08-22"
    assert profile["profile_schema_version"] == 1
    assert profile["profile_fingerprint"] == "a" * 64


@pytest.mark.asyncio
async def test_profile_loader_returns_none_without_prior_profile():
    repository = SimpleNamespace(latest_at_or_before=AsyncMock(return_value=None))

    result = await TTradeOpportunityRuntimeService().load_reference_profile(
        instrument_code="600000.SH",
        evaluated_at=datetime(2026, 8, 23, 10, 0),
        repository=repository,
    )

    assert result is None


@pytest.mark.asyncio
async def test_profile_loader_rejects_incompatible_profile_shape():
    row = SimpleNamespace(
        as_of=datetime(2026, 8, 22, 15, 5),
        profile={"pullback_threshold_pct": 0.8},
        schema_version="1",
        version="profile-incomplete",
        fingerprint="b" * 64,
    )
    repository = SimpleNamespace(latest_at_or_before=AsyncMock(return_value=row))

    result = await TTradeOpportunityRuntimeService().load_reference_profile(
        instrument_code="600000.SH",
        evaluated_at=datetime(2026, 8, 23, 10, 0),
        repository=repository,
    )

    assert result is None


@pytest.mark.asyncio
async def test_strict_intent_recorder_raises_before_mutating_runtime_truth(
    monkeypatch: pytest.MonkeyPatch,
):
    class _Repository:
        def __init__(self, _db) -> None:
            return None

        async def create_intent_idempotent(self, _payload: dict):
            raise RuntimeError("database unavailable")

    async def _db_sessions():
        yield object()

    monkeypatch.setattr(database_connection, "get_async_db", _db_sessions)
    monkeypatch.setattr(intent_repository, "TradeIntentRepository", _Repository)
    manager = RuntimeStateManager(
        run_id="run-strict-intent",
        persist_enabled=True,
        log_dir="logs/strategy",
    )
    intent = TradeIntent(
        strategy_id="1",
        run_id=manager.run_id,
        instrument_code="600000.SH",
        direction=TradeIntentDirection.BUY,
        bucket="swing",
        reason="V3_OPPORTUNITY",
        target_amount=10_000.0,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await manager.record_trade_intent_strict(intent, status="AWAITING_APPROVAL")

    assert intent.intent_id not in manager._state["trade_intents"]
