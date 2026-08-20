from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.resolvers import limit_up_board_replay as resolver_module
from quantx_api.gqlapi.resolvers.limit_up_board_replay import (
  LimitUpBoardReplayResolver,
)
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.schemas import limit_up_board_assistant_schema as schema_module
from quantx_api.gqlapi.schemas.limit_up_board_assistant_schema import (
  LimitUpBoardAssistantQuery,
)
from quantx_api.gqlapi.types.limit_up_board_replay_types import (
  LimitUpBoardReplayStartInput,
)
from quantx_infrastructure.services.engine_command_service import (
  EngineCommandReceipt,
)


def _snapshot(
  *,
  job_id: str = "job-1",
  status: str = "RUNNING",
) -> dict:
  return {
    "job_id": job_id,
    "account_id": "account-1",
    "status": status,
    "progress_pct": 40.0,
    "processed_until": "2026-08-01T10:00:00",
    "revision": "7",
    "scenario_profile": "STANDARD_V1",
    "request": {
      "start_time": "2026-08-01T09:30:00",
      "end_time": "2026-08-05T15:00:00",
      "scenario_profile": "STANDARD_V1",
      "initial_cash": 100_000.0,
      "initial_total_asset": 200_000.0,
    },
    "dataset_fingerprint": "dataset-fingerprint",
    "config_fingerprint": "config-fingerprint",
    "input_manifest": {
      "schema_version": 1,
      "source": "POINT_IN_TIME_UNIVERSE_V1",
      "requested_range": {
        "start_time": "2026-08-01T09:30:00",
        "end_time": "2026-08-05T15:00:00",
        "timezone": "Asia/Shanghai",
      },
      "config_fingerprint": "config-fingerprint",
      "snapshot_refs_fingerprint": "snapshot-fingerprint",
      "versions": {
        "score": ["score-v1"],
        "feature": ["feature-v1"],
        "promotion_model": ["model-v1"],
        "exit_policy": ["exit-v1"],
      },
      "coverage": {
        "frame_count": 20,
        "candidate_observations": 100,
        "promotion_eligible_observations": 8,
        "raw_tick_count": 12_000,
        "candidate_fresh_tick_coverage_pct": 99.5,
        "first_observed_at": "2026-08-01T09:30:00",
        "last_observed_at": "2026-08-05T15:00:00",
      },
      "artifacts": {
        "candidate_universe": {
          "content_sha256": "universe-sha",
          "row_count": 20,
          "format": "jsonl",
          "compression": "gzip",
        },
        "raw_ticks": {
          "content_sha256": "ticks-sha",
          "row_count": 12_000,
          "format": "jsonl",
          "compression": "gzip",
        },
      },
      "data_quality": {
        "status": "OK",
        "executable": True,
        "source": "POINT_IN_TIME_UNIVERSE_V1",
        "coverage": {
          "raw_tick_count": 12_000,
          "candidate_fresh_tick_coverage_pct": 99.5,
        },
        "tick_field_quality": {
          "missing_native_price_limits_count": 2,
          "missing_five_level_book_count": 3,
        },
      },
      "dataset_fingerprint": "dataset-fingerprint",
      "manifest_sha256": "manifest-sha",
    },
    "data_quality": {
      "status": "OK",
      "executable": True,
      "source": "POINT_IN_TIME_UNIVERSE_V1",
      "coverage": {
        "frame_count": 20,
        "candidate_observations": 100,
        "promotion_eligible_observations": 8,
        "covered_trading_dates": ["2026-08-01", "2026-08-05"],
        "missing_trading_dates": [],
        "max_frame_gap_seconds": 10.0,
        "frame_gaps_over_15_seconds": 0,
        "scanner_stopped_frames": 0,
        "raw_tick_count": 12_000,
        "candidate_fresh_tick_coverage_pct": 99.5,
      },
      "tick_field_quality": {
        "tick_count": 12_000,
        "missing_native_price_limits_count": 2,
        "missing_five_level_book_count": 3,
      },
      "tick_load_errors": {"600000.SH": "missing partition"},
      "future_data_violations": 0,
      "candidate_frame_count_mismatches": 0,
      "score_versions": ["score-v1"],
      "feature_versions": ["feature-v1"],
      "model_versions": ["model-v1"],
      "exit_policy_versions": ["exit-v1"],
      "blockers": [],
      "warnings": [],
    },
    "error_message": None,
    "started_at": "2026-08-20T10:00:00",
    "completed_at": None,
    "created_at": "2026-08-20T09:59:00",
    "updated_at": "2026-08-20T10:01:00",
    "scenarios": [
      {
        "scenario_id": "THEORETICAL",
        "backtest_id": "backtest-1",
        "status": status,
        "progress_pct": 40.0,
        "processed_until": "2026-08-01T10:00:00",
        "revision": "4",
        "error_message": None,
        "confirmation_delay_ms": 0,
        "participation_cap_pct": 0.05,
        "book_depth_participation_pct": 0.5,
      }
    ],
  }


@pytest.fixture(autouse=True)
def _stub_scenario_results(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.result_service,
    "load_many",
    AsyncMock(return_value={}),
  )


def _input(idempotency_key: str = "request-1") -> LimitUpBoardReplayStartInput:
  return LimitUpBoardReplayStartInput(
    account_id="account-1",
    idempotency_key=idempotency_key,
    start_time=datetime(2026, 8, 1, 9, 30),
    end_time=datetime(2026, 8, 5, 15, 0),
    initial_cash=100_000.0,
    initial_total_asset=200_000.0,
  )


def test_limit_up_board_replay_contract_is_exposed_and_has_policies() -> None:
  sdl = schema.as_str()

  assert "limitUpBoardReplay(jobId: String!)" in sdl
  assert "limitUpBoardReplayHistory(accountId: String!" in sdl
  assert "limitUpBoardReplayPreparation(accountId: String!" in sdl
  assert "limitUpBoardReplayTrades(" in sdl
  assert "limitUpBoardReplayCurve(" in sdl
  assert "startLimitUpBoardReplay(input: LimitUpBoardReplayStartInput!)" in sdl
  assert "cancelLimitUpBoardReplay(jobId: String!)" in sdl
  assert "jobId: String!" in sdl
  assert "rawTickCount: Int!" in sdl
  assert "fiveLevelMissing: Int!" in sdl
  assert "nativeLimitMissing: Int!" in sdl
  assert "freshCoverage: Float!" in sdl
  assert "contentSha256: String!" in sdl
  assert "runId: String!" not in sdl.split("type LimitUpBoardReplayUpdateNotice", 1)[1].split("}", 1)[0]

  assert operation_policy("Query", "limitUpBoardReplay").required_permissions == (
    "strategy:read",
  )
  assert operation_policy(
    "Query", "limitUpBoardReplayTrades"
  ).required_permissions == ("strategy:read",)
  assert operation_policy(
    "Query", "limitUpBoardReplayCurve"
  ).required_permissions == ("strategy:read",)
  assert operation_policy(
    "Mutation", "startLimitUpBoardReplay"
  ).required_permissions == ("strategy:write",)
  assert operation_policy(
    "Subscription", "limitUpBoardReplayUpdates"
  ).required_permissions == ("strategy:read",)


@pytest.mark.asyncio
async def test_preparation_uses_shared_standard_scenarios(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.assistant_projection_service,
    "get",
    AsyncMock(
      return_value={"config_version": 3, "projection_version": "9"}
    ),
  )
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "list_by_account",
    AsyncMock(return_value=[]),
  )

  preparation = await LimitUpBoardReplayResolver.prepare(
    "account-1",
    datetime(2026, 8, 1, 9, 30),
    datetime(2026, 8, 5, 15, 0),
    "STANDARD_V1",
  )

  assert preparation.ready is True
  assert preparation.assistant_config_version == 3
  assert preparation.assistant_projection_version == "9"
  assert [item.scenario_id for item in preparation.scenarios] == [
    "THEORETICAL",
    "FAST",
    "BASE",
    "STRESS",
  ]
  assert preparation.scenarios[0].theoretical_upper_bound is True
  assert preparation.scenarios[2].confirmation_delay_ms == 3_000


@pytest.mark.asyncio
async def test_preparation_blocks_when_an_active_job_exists(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.assistant_projection_service,
    "get",
    AsyncMock(return_value={"config_version": 1}),
  )
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "list_by_account",
    AsyncMock(
      return_value=[
        {"job_id": "active-job", "account_id": "account-1", "status": "RUNNING"}
      ]
    ),
  )

  preparation = await LimitUpBoardReplayResolver.prepare(
    "account-1",
    datetime(2026, 8, 1, 9, 30),
    datetime(2026, 8, 5, 15, 0),
    "STANDARD_V1",
  )

  assert preparation.ready is False
  assert preparation.has_active_job is True
  assert preparation.active_job_id == "active-job"
  assert "ACTIVE_REPLAY_EXISTS" in preparation.blockers


@pytest.mark.asyncio
async def test_start_processing_receipt_returns_dedicated_deterministic_job(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.assistant_projection_service,
    "get",
    AsyncMock(return_value={"config_version": 1, "projection_version": "2"}),
  )
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "list_by_account",
    AsyncMock(return_value=[]),
  )
  get_projection = AsyncMock(return_value=None)
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "get",
    get_projection,
  )
  request = AsyncMock(
    return_value=EngineCommandReceipt(
      message_id="command-message-1",
      command_type="LIMIT_UP_BOARD_REPLAY_START",
      aggregate_id=None,
      status="PROCESSING",
    )
  )
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)

  first = await LimitUpBoardReplayResolver.start(_input(" stable-key "))
  second = await LimitUpBoardReplayResolver.start(_input("stable-key"))

  assert first.success is True
  assert first.code == "REPLAY_ACCEPTED"
  assert first.replay is not None
  assert second.replay is not None
  assert first.replay.job_id == second.replay.job_id
  assert first.replay.job_id != "command-message-1"
  assert first.replay.status == "PENDING"
  assert len(first.replay.scenarios) == 4
  call = request.await_args_list[0]
  job_id = call.args[1]["input"]["job_id"]
  assert call.kwargs["aggregate_id"] == job_id == first.replay.job_id
  assert call.kwargs["idempotency_key"] == (
    "limit-up-board-replay:account-1:stable-key"
  )


@pytest.mark.asyncio
async def test_start_rejects_blank_idempotency_without_command(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = AsyncMock()
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)

  result = await LimitUpBoardReplayResolver.start(_input("   "))

  assert result.success is False
  assert result.code == "INVALID_IDEMPOTENCY_KEY"
  request.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_idempotency_matches_utc_input_to_stored_shanghai_time(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  existing = _snapshot(status="COMPLETED")
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "get",
    AsyncMock(return_value=existing),
  )
  request = AsyncMock()
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)
  value = LimitUpBoardReplayStartInput(
    account_id="account-1",
    idempotency_key="same-request",
    start_time=datetime(2026, 8, 1, 1, 30, tzinfo=timezone.utc),
    end_time=datetime(2026, 8, 5, 7, 0, tzinfo=timezone.utc),
    initial_cash=100_000.0,
    initial_total_asset=200_000.0,
  )

  result = await LimitUpBoardReplayResolver.start(value)

  assert result.success is True
  assert result.code == "REPLAY_ALREADY_EXISTS"
  request.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_maps_structured_quality_and_scenario_metadata(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "list_by_account",
    AsyncMock(return_value=[_snapshot()]),
  )

  history = await LimitUpBoardReplayResolver.history("account-1", 20)

  assert len(history) == 1
  replay = history[0]
  assert replay.job_id == "job-1"
  assert replay.request.start_time == datetime(2026, 8, 1, 9, 30)
  assert replay.data_quality.executable is True
  assert replay.data_quality.raw_tick_count == 12_000
  assert replay.data_quality.five_level_missing == 3
  assert replay.data_quality.native_limit_missing == 2
  assert replay.data_quality.fresh_coverage == 99.5
  assert replay.data_quality.coverage.candidate_observations == 100
  assert replay.data_quality.tick_load_errors[0].instrument_code == "600000.SH"
  assert replay.input_manifest.versions.promotion_model == ["model-v1"]
  assert replay.input_manifest.artifacts.raw_ticks.content_sha256 == "ticks-sha"
  assert replay.input_manifest.data_quality.executable is True
  assert replay.input_manifest.manifest_sha256 == "manifest-sha"
  assert replay.scenarios[0].label == "理论上界"
  assert replay.scenarios[0].theoretical_upper_bound is True


@pytest.mark.asyncio
async def test_replay_exposes_scenario_results_and_pages_detail(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  result = {
    "schema_version": 1,
    "scenario_id": "THEORETICAL",
    "no_queue_credit": True,
    "summary": {
      "initial_equity": 100_000.0,
      "final_equity": 102_500.0,
      "total_return_pct": 2.5,
      "max_drawdown_pct": 1.2,
      "cvar95_loss_pct": 0.8,
      "fill_rate_pct": 35.0,
      "open_position_count": 1,
    },
    "funnel": {
      "candidate_frames": 20,
      "candidate_observations": 100,
      "qualified_observations": 8,
      "entry_intents": 5,
      "trades": 2,
    },
    "constraint_statistics": {"daily_exposure_blocked": 2},
    "rejection_reasons": {"NO_ASK_LIQUIDITY": 3},
    "open_positions": [
      {
        "instrument_code": "000001.SZ",
        "volume": 100,
        "available_volume": 0,
        "average_price": 10.0,
        "last_price": 10.2,
        "market_value": 1_020.0,
        "status": "T1_LOCKED",
      }
    ],
    "trades": [
      {
        "trade_id": "trade-1",
        "order_id": "order-1",
        "instrument_code": "000001.SZ",
        "side": "BUY",
        "price": 10.0,
        "volume": 100,
        "amount": 1_000.0,
        "fees": 5.0,
        "trade_time": "2026-08-01T10:00:00",
      },
      {
        "trade_id": "trade-2",
        "order_id": "order-2",
        "instrument_code": "000001.SZ",
        "side": "SELL",
        "price": 10.2,
        "volume": 100,
        "amount": 1_020.0,
        "fees": 5.0,
        "trade_time": "2026-08-04T10:00:00",
      },
    ],
    "curve": [
      {
        "timestamp": "2026-08-01T09:30:00",
        "equity": 100_000.0,
        "return_pct": 0.0,
      },
      {
        "timestamp": "2026-08-05T15:00:00",
        "equity": 102_500.0,
        "return_pct": 2.5,
      },
    ],
  }
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "list_by_account",
    AsyncMock(return_value=[_snapshot(status="COMPLETED")]),
  )
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "get",
    AsyncMock(return_value=_snapshot(status="COMPLETED")),
  )
  load_many = AsyncMock(return_value={"backtest-1": result})
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.result_service,
    "load_many",
    load_many,
  )

  replay = (await LimitUpBoardReplayResolver.history("account-1", 20))[0]
  trades = await LimitUpBoardReplayResolver.trades("job-1", "theoretical", 1, 1)
  curve = await LimitUpBoardReplayResolver.curve("job-1", "THEORETICAL", 0, 1)

  scenario = replay.scenarios[0]
  assert scenario.result_available is True
  assert scenario.summary is not None
  assert scenario.summary.total_return_pct == 2.5
  assert scenario.funnel is not None
  assert scenario.funnel.qualified_observations == 8
  assert scenario.constraint_statistics[0].key == "daily_exposure_blocked"
  assert scenario.rejection_reasons[0].reason == "NO_ASK_LIQUIDITY"
  assert scenario.open_positions[0].status == "T1_LOCKED"
  assert trades.total == 2
  assert trades.has_more is False
  assert trades.items[0].trade_id == "trade-2"
  assert curve.total == 2
  assert curve.has_more is True
  assert curve.items[0].equity == 100_000.0


@pytest.mark.asyncio
async def test_job_query_authorizes_projection_owner(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  authorize = Mock()
  monkeypatch.setattr(
    LimitUpBoardReplayResolver,
    "replay_account_id",
    AsyncMock(return_value="owner-account"),
  )
  monkeypatch.setattr(
    LimitUpBoardReplayResolver,
    "get",
    AsyncMock(return_value=None),
  )

  def authorized(_info, account_id: str) -> str:
    authorize(account_id)
    return account_id

  monkeypatch.setattr(schema_module, "authorized_account_id", authorized)

  await LimitUpBoardAssistantQuery().limit_up_board_replay(object(), "job-1")

  authorize.assert_called_once_with("owner-account")


@pytest.mark.asyncio
async def test_cancel_uses_job_id_and_returns_cancelled_projection(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  current = _snapshot(status="RUNNING")
  cancelled = _snapshot(status="CANCELLED")
  monkeypatch.setattr(
    LimitUpBoardReplayResolver.projection_service,
    "get",
    AsyncMock(return_value=current),
  )
  request = AsyncMock(
    return_value=EngineCommandReceipt(
      message_id="cancel-message",
      command_type="LIMIT_UP_BOARD_REPLAY_CANCEL",
      aggregate_id="job-1",
      status="SUCCEEDED",
      result=cancelled,
    )
  )
  monkeypatch.setattr(resolver_module.engine_command_service, "request", request)

  result = await LimitUpBoardReplayResolver.cancel("job-1")

  assert result.success is True
  assert result.code == "REPLAY_CANCELLED"
  assert result.replay is not None
  assert result.replay.job_id == "job-1"
  request.assert_awaited_once_with(
    "LIMIT_UP_BOARD_REPLAY_CANCEL",
    {"job_id": "job-1"},
    aggregate_id="job-1",
    idempotency_key="limit-up-board-replay-cancel:job-1",
  )
