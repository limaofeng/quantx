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
async def test_closed_diagnostics_for_one_global_tick_are_committed_as_one_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sparse global tick may close every held instrument's prior window.

    The service must preserve every diagnostic row but avoid turning that one
    causal boundary into one PostgreSQL transaction per instrument.
    """

    class CountingAsyncSession(AsyncSession):
        commit_calls = 0

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
    codes = (
        "600000.SH",
        "600001.SH",
        "600002.SH",
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
        "300001.SZ",
        "300002.SZ",
    )
    try:
        # All streams begin in one source-time window. Nothing is durable yet:
        # an open diagnostic is intentionally held for coalescing.
        for ordinal, code in enumerate(codes):
            await service.materialize_evaluation(
                event=_diagnostic_event(
                    code,
                    evaluated_at_ms=10_000,
                    source_time_ms=10_000,
                    ordinal=ordinal,
                ),
                account_id="account-1",
                strategy_run_id="run-1",
            )

        assert session_yields == 0
        assert CountingAsyncSession.commit_calls == 0

        # The next global source time closes all eight prior windows. They are
        # appended atomically in one owned session/commit, not eight sessions.
        await service.materialize_evaluation(
            event=_diagnostic_event(
                codes[0],
                evaluated_at_ms=12_100,
                source_time_ms=12_100,
                ordinal=len(codes),
            ),
            account_id="account-1",
            strategy_run_id="run-1",
        )

        assert session_yields == 1
        assert CountingAsyncSession.commit_calls == 1
        async with reader_sessions() as db:
            assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 8
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_owned_diagnostic_batch_rolls_back_and_reinserts_every_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed batch cannot leave a partial durable/retry split-brain."""

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
    original_append = TTradeOpportunityRuntimeService._append_evaluation
    append_calls = 0

    async def fail_second_append(
        repository: object,
        event: dict,
        *,
        commit: bool = True,
    ) -> object:
        nonlocal append_calls
        append_calls += 1
        if append_calls == 2:
            raise RuntimeError("database unavailable")
        return await original_append(repository, event, commit=commit)

    monkeypatch.setattr(
        TTradeOpportunityRuntimeService,
        "_append_evaluation",
        staticmethod(fail_second_append),
    )
    service = TTradeOpportunityRuntimeService()
    first_codes = ("600000.SH", "000001.SZ")
    try:
        for ordinal, code in enumerate(first_codes):
            await service.materialize_evaluation(
                event=_diagnostic_event(
                    code,
                    evaluated_at_ms=10_000,
                    source_time_ms=10_000,
                    ordinal=ordinal,
                ),
                account_id="account-1",
                strategy_run_id="run-1",
            )

        with pytest.raises(RuntimeError, match="database unavailable"):
            await service.materialize_evaluation(
                event=_diagnostic_event(
                    first_codes[0],
                    evaluated_at_ms=12_100,
                    source_time_ms=12_100,
                    ordinal=2,
                ),
                account_id="account-1",
                strategy_run_id="run-1",
            )

        assert {
            window.instrument_code for window in service._diagnostic_windows.values()
        } == set(first_codes)
        async with sessions() as db:
            assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 0
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
