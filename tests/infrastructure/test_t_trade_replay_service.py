from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_infrastructure.services.t_trade_replay_service import (
  _INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY,
  TTradeReplayService,
  _v3_pressure_runtime_state_persistence_capability,
)


def test_v3_pressure_durable_state_switch_requires_opaque_capability() -> None:
  parameters = {
    _INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY: True,
  }

  TTradeReplayService._apply_v3_pressure_runtime_state_persistence(
    parameters,
    {"replay_acceptance": "V3_PRESSURE_BASELINE"},
    object(),
  )

  assert _INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY not in parameters

  TTradeReplayService._apply_v3_pressure_runtime_state_persistence(
    parameters,
    {"replay_acceptance": "V3_PRESSURE_BASELINE"},
    _v3_pressure_runtime_state_persistence_capability(),
  )

  assert parameters[_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY] is True
  with pytest.raises(ValueError, match="仅允许 V3_PRESSURE_BASELINE"):
    TTradeReplayService._apply_v3_pressure_runtime_state_persistence(
      parameters,
      {"replay_acceptance": "V3_CAUSAL_20D"},
      _v3_pressure_runtime_state_persistence_capability(),
    )


@pytest.mark.asyncio
async def test_deferred_replay_stays_pending_until_runtime_actually_starts() -> None:
  request_id = "00000000-0000-0000-0000-000000000126"
  manager = AsyncMock()
  manager.run_strategy.return_value = request_id
  manager.defer_start_strategy.return_value = True
  service = TTradeReplayService(manager)
  service.t_trade_service.build_parameters = lambda _payload: {}
  service.t_trade_service._validate_parameters = lambda *_args: None
  service.t_trade_service._get_strategy_template_id = AsyncMock(return_value=1)
  service._has_active_replay = AsyncMock(return_value=False)
  service._load_run_and_backtest = AsyncMock(return_value=(None, None))
  service._load_instrument_references = AsyncMock(
    return_value={
      "600887.SH": SimpleNamespace(
        id="600887.SH",
        name="伊利股份",
        open_date=date(1996, 3, 12),
        expire_date=date(2038, 1, 19),
      )
    }
  )
  service.get = AsyncMock(
    return_value={
      "run_id": request_id,
      "status": "PENDING",
      "progress_pct": 0.0,
    }
  )
  payload = {
    "account_id": "account-1",
    "start_time": datetime(2026, 8, 19, 9, 30),
    "end_time": datetime(2026, 8, 19, 15, 0),
    "initial_portfolio_as_of": datetime(2026, 8, 18, 15, 0),
    "initial_cash": 90_000.0,
    "initial_total_asset": 100_000.0,
    "initial_positions": [
      {
        "stock_code": "600887.SH",
        "instrument_name": "伊利股份",
        "volume": 400,
        "available_volume": 400,
        "avg_price": 25.0,
        "last_price": 25.0,
        "market_value": 10_000.0,
      },
      {
        "stock_code": "787825.SH",
        "instrument_name": "测试申购代码",
        "volume": 100,
        "available_volume": 100,
        "avg_price": 1.0,
        "last_price": 1.0,
        "market_value": 100.0,
      }
    ],
  }

  with (
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "TradingDateHelper.get_trading_calendar",
      new_callable=AsyncMock,
      return_value=[date(2026, 8, 19)],
    ),
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "t_trade_replay_projection_service.create",
      new_callable=AsyncMock,
      return_value={"status": "PENDING"},
    ) as create_projection,
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "t_trade_replay_projection_service.update",
      new_callable=AsyncMock,
    ) as update_projection,
  ):
    replay = await service.start(
      payload,
      defer_start=True,
      request_id=request_id,
    )

  assert replay["status"] == "PENDING"
  create_projection.assert_awaited_once_with(
    run_id=request_id,
    account_id="account-1",
  )
  update_projection.assert_not_awaited()
  manager.defer_start_strategy.assert_awaited_once_with(request_id)
  run_kwargs = manager.run_strategy.await_args.kwargs
  assert run_kwargs["run_id"] == request_id
  assert run_kwargs["auto_start"] is False
  assert run_kwargs["parameters"]["initial_instrument_metadata"]["600887.SH"] == {
    "instrument_name": "伊利股份",
    "instrument_status_as_of": None,
    "listing_date": "1996-03-12",
    "expiry_date": "2038-01-19",
    "price_limit_reference_source": "INSTRUMENT_MASTER",
    "eligible": True,
    "reason": "",
    "position_shares": 400,
    "position_available_shares": 400,
    "position_frozen_shares": 0,
    "position_avg_price": 25.0,
    "position_market_value": 10_000.0,
  }
  assert run_kwargs["parameters"]["replay_price_limit_policy"] == {
    "schema_version": 1,
    "source_priority": [
      "HISTORICAL_TICK_NATIVE_LIMITS",
      "PREVIOUS_CLOSE_EXCHANGE_RULES",
    ],
    "instrument_reference": "INSTRUMENT_MASTER_AT_REPLAY_CREATION",
    "ambiguous_action": "STRICT_RISK_REJECT",
  }
  assert run_kwargs["parameters"]["replay_skipped_instruments"] == [
    {
      "stock_code": "787825.SH",
      "instrument_name": "测试申购代码",
      "reason": "证券主数据不完整，无法确认历史涨跌停规则",
    }
  ]
  assert run_kwargs["instruments"] == ["600887.SH"]
  assert run_kwargs["parameters"]["initial_portfolio_as_of"] == (
    "2026-08-18T15:00:00"
  )
  assert "MANUAL_HISTORICAL_PORTFOLIO" in run_kwargs["parameters"][
    "initial_asset_reconciliation"
  ]["quality_flags"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("portfolio_as_of", "message"),
  [
    (None, "手工历史组合必须提供可审计的组合时点"),
    (
      datetime(2026, 8, 19, 9, 30),
      "手工历史组合时点必须早于回放开始时间，禁止使用未来账户数据",
    ),
    (
      datetime(2026, 8, 20, 15, 0),
      "手工历史组合时点必须早于回放开始时间，禁止使用未来账户数据",
    ),
  ],
)
async def test_manual_portfolio_requires_auditable_pre_replay_timestamp(
  portfolio_as_of: datetime | None,
  message: str,
) -> None:
  manager = AsyncMock()
  service = TTradeReplayService(manager)
  service._has_active_replay = AsyncMock(return_value=False)
  payload = {
    "account_id": "account-1",
    "start_time": datetime(2026, 8, 19, 9, 30),
    "end_time": datetime(2026, 8, 19, 15, 0),
    "initial_portfolio_as_of": portfolio_as_of,
    "initial_cash": 90_000.0,
    "initial_total_asset": 100_000.0,
    "initial_positions": [
      {
        "stock_code": "600887.SH",
        "volume": 400,
        "available_volume": 400,
        "last_price": 25.0,
        "market_value": 10_000.0,
      }
    ],
  }

  with patch(
    "quantx_infrastructure.services.t_trade_replay_service."
    "TradingDateHelper.get_trading_calendar",
    new_callable=AsyncMock,
    return_value=[date(2026, 8, 19)],
  ):
    with pytest.raises(ValueError, match=message):
      await service.start(payload)

  manager.run_strategy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "terminal_status",
  ["COMPLETED", "ERROR", "FAILED", "CANCELLED", "STOPPED"],
)
async def test_cancel_replay_rejects_terminal_projection_without_side_effects(
  terminal_status: str,
) -> None:
  manager = AsyncMock()
  service = TTradeReplayService(manager)
  service._load_run_and_backtest = AsyncMock(
    return_value=(
      SimpleNamespace(parameters={"t_trade_replay": True}),
      SimpleNamespace(id="backtest-1", status=terminal_status),
    )
  )

  with (
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "t_trade_replay_projection_service.get",
      new_callable=AsyncMock,
      return_value={"run_id": "replay-1", "status": terminal_status},
    ) as get_projection,
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "t_trade_replay_projection_service.update",
      new_callable=AsyncMock,
    ) as update_projection,
  ):
    with pytest.raises(ValueError, match=f"终态 {terminal_status}"):
      await service.cancel("replay-1")

  get_projection.assert_awaited_once_with("replay-1")
  update_projection.assert_not_awaited()
  manager.cancel_deferred_start.assert_not_awaited()
  manager.stop_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_running_replay_forces_past_simulated_exit_plan_guard() -> None:
  runtime = SimpleNamespace(
    context=SimpleNamespace(current_time=datetime(2026, 8, 19, 14, 30)),
    exit_plan_book=SimpleNamespace(active_plans=lambda: [object()]),
    get_metrics=lambda: {"error_count": 0},
  )
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    cancel_deferred_start=AsyncMock(return_value=True),
    stop_strategy=AsyncMock(
      side_effect=lambda _run_id, *, force=False: bool(force)
    ),
  )
  service = TTradeReplayService(manager)
  service._load_run_and_backtest = AsyncMock(
    return_value=(
      SimpleNamespace(
        parameters={
          "t_trade_replay": True,
          "account_id": "account-1",
        },
        metrics={},
      ),
      None,
    )
  )
  service.get = AsyncMock(
    return_value={"run_id": "replay-1", "status": "CANCELLED"}
  )

  with (
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "build_t_trade_replay_metrics",
      return_value={"cycles": {"total": 0}},
    ),
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "t_trade_replay_projection_service.get",
      new_callable=AsyncMock,
      return_value={
        "run_id": "replay-1",
        "status": "RUNNING",
        "progress_pct": 42.0,
      },
    ),
    patch(
      "quantx_infrastructure.services.t_trade_replay_service."
      "t_trade_replay_projection_service.update",
      new_callable=AsyncMock,
      return_value={"run_id": "replay-1", "status": "CANCELLED"},
    ) as update_projection,
  ):
    replay = await service.cancel("replay-1")

  assert replay["status"] == "CANCELLED"
  assert runtime.exit_plan_book.active_plans()
  manager.stop_strategy.assert_awaited_once_with("replay-1", force=True)
  update_projection.assert_awaited_once()
  assert update_projection.await_args.kwargs["status"] == "CANCELLED"


def test_formal_v3_replay_metadata_requires_an_exact_window_and_abnormal_day() -> None:
  dates = [date(2026, 7, 1) + timedelta(days=index) for index in range(20)]

  normalized = TTradeReplayService._normalize_rollout_evidence_request(
    {
      "replay_acceptance": "V3_CAUSAL_20D",
      "replay_abnormal_dates": [dates[-1].isoformat(), dates[-1].isoformat()],
    },
    trading_dates=dates,
  )

  assert normalized == {
    "replay_acceptance": "V3_CAUSAL_20D",
    "replay_abnormal_dates": [dates[-1].isoformat()],
  }
  with pytest.raises(ValueError, match="恰好覆盖 20 个交易日"):
    TTradeReplayService._normalize_rollout_evidence_request(
      {"replay_acceptance": "V3_CAUSAL_20D", "replay_abnormal_dates": []},
      trading_dates=dates[:-1],
    )
  with pytest.raises(ValueError, match="必须声明至少一个异常行情日"):
    TTradeReplayService._normalize_rollout_evidence_request(
      {"replay_acceptance": "V3_CAUSAL_20D", "replay_abnormal_dates": []},
      trading_dates=dates,
    )
