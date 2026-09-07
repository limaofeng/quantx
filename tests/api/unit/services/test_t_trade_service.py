import hashlib
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import quantx_infrastructure.services.t_trade_service as t_trade_service_module
from quantx_application.t_trade_v3 import normalize_signal_policy
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy
from quantx_infrastructure.models.enums import OrderStatus, OrderType, StrategyRunMode
from quantx_infrastructure.services.t_trade_service import TTradeService


def signal_policy(**overrides):
  payload = OpportunityPolicy().to_dict()
  payload.update(overrides)
  return payload


def test_t_trade_parameters_accept_safe_defaults():
  TTradeService._validate_parameters({}, StrategyRunMode.PAPER)


def _external_import_harness(
  monkeypatch,
  *,
  plan_error: Exception | None = None,
  locked_order_overrides: dict | None = None,
):
  patch = SimpleNamespace(
    set={
      "instrument_states": {
        "600000.SH": {
          "batch_id": "batch-external",
          "exit_plan_id": "t-exit-batch-external",
          "exit_policy_snapshot": {},
        }
      }
    },
    unset=[],
    append_events=[{"type": "T_TRADE_EXTERNAL_ENTRY_IMPORTED"}],
  )

  class Template:
    @staticmethod
    def to_dict():
      return {"plan_id": "t-exit-batch-external"}

  class Strategy:
    @staticmethod
    def import_external_entry(*_args):
      return patch

    @staticmethod
    def build_exit_plan_template(**_kwargs):
      return Template()

  monkeypatch.setattr(
    t_trade_service_module,
    "AshareIntradayTAssistantStrategy",
    Strategy,
  )
  strategy = Strategy()
  state_record = SimpleNamespace(
    custom_state={"instrument_states": {}, "runtime_events": []},
    version=4,
  )
  order = SimpleNamespace(
    account_id="account-1",
    type=OrderType.BUY,
    status=OrderStatus.SUCCEEDED,
    traded_volume=100,
    traded_price=10.0,
    stock_code="600000.SH",
    time=datetime(2026, 9, 3, 10),
  )
  locked_order = SimpleNamespace(**vars(order))
  for key, value in dict(locked_order_overrides or {}).items():
    setattr(locked_order, key, value)
  added: list[object] = []
  db = SimpleNamespace(
    get=AsyncMock(side_effect=[order, locked_order]),
    scalar=AsyncMock(return_value=state_record),
    add=added.append,
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )

  async def get_db():
    yield db

  monkeypatch.setattr(t_trade_service_module, "get_async_db", get_db)
  imported_repo = SimpleNamespace(
    find_source=AsyncMock(return_value=None),
    save=AsyncMock(),
  )
  monkeypatch.setattr(
    t_trade_service_module,
    "TTradeImportedEntryRepository",
    lambda _db: imported_repo,
  )
  register = AsyncMock(side_effect=plan_error)
  monkeypatch.setattr(
    t_trade_service_module,
    "AutoExitPlanService",
    lambda: SimpleNamespace(register_strategy_entry_fill=register),
  )
  apply_patch = Mock()
  runtime = SimpleNamespace(
    strategy=strategy,
    state_manager=SimpleNamespace(
      checkpoint_strategy_state_changes=AsyncMock(return_value=True)
    ),
    context=SimpleNamespace(
      parameters={"account_id": "account-1"},
      mode=SimpleNamespace(value="paper"),
    ),
  )
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    executor=SimpleNamespace(publish_external_durable_state=apply_patch),
  )
  service = TTradeService(manager)
  service.get_session = AsyncMock(return_value={"status": "MONITORING"})
  return service, db, imported_repo, register, apply_patch, state_record, added


@pytest.mark.asyncio
async def test_external_import_commits_plan_ledger_and_state_before_hot_patch(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service, db, repo, register, apply_patch, state_record, added = (
    _external_import_harness(monkeypatch)
  )

  result = await service.import_external_entry(
    "run-1",
    "account-1",
    "101",
    account_coordination_held=True,
  )

  assert result["code"] == "EXTERNAL_ENTRY_IMPORTED"
  register.assert_awaited_once()
  assert register.await_args.kwargs["db"] is db
  assert register.await_args.kwargs["commit"] is False
  assert register.await_args.kwargs["event_business_key"].startswith(
    "strategy-external-entry:run-1:order:101:"
  )
  repo.save.assert_awaited_once()
  assert repo.save.await_args.kwargs["commit"] is False
  db.commit.assert_awaited_once()
  db.rollback.assert_not_awaited()
  assert state_record.version == 5
  assert added[0].batch_id == "batch-external"
  assert added[0].status == "ENTRY_FILLED"
  apply_patch.assert_called_once()
  assert apply_patch.call_args.kwargs == {
    "durable_state_version": 5,
    "durable_custom_state": state_record.custom_state,
  }
  assert db.get.await_args_list[1].kwargs == {
    "with_for_update": True,
    "populate_existing": True,
  }


@pytest.mark.asyncio
async def test_external_import_plan_failure_rolls_back_without_hot_state_ghost(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service, db, repo, register, apply_patch, _state_record, _added = (
    _external_import_harness(
      monkeypatch,
      plan_error=RuntimeError("public plan persistence failed"),
    )
  )

  with pytest.raises(RuntimeError, match="public plan persistence failed"):
    await service.import_external_entry(
      "run-1",
      "account-1",
      "101",
      account_coordination_held=True,
    )

  register.assert_awaited_once()
  repo.save.assert_not_awaited()
  db.commit.assert_not_awaited()
  db.rollback.assert_awaited_once()
  apply_patch.assert_not_called()


@pytest.mark.asyncio
async def test_external_import_rejects_order_changed_before_write_lock(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service, db, repo, register, apply_patch, _state_record, _added = (
    _external_import_harness(
      monkeypatch,
      locked_order_overrides={"traded_volume": 200},
    )
  )

  with pytest.raises(RuntimeError, match="导入事务前已变化"):
    await service.import_external_entry(
      "run-1",
      "account-1",
      "101",
      account_coordination_held=True,
    )

  db.rollback.assert_awaited_once()
  register.assert_not_awaited()
  repo.save.assert_not_awaited()
  apply_patch.assert_not_called()


@pytest.mark.asyncio
async def test_external_import_requires_account_coordination_lock(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  service, db, _repo, register, apply_patch, _state_record, _added = (
    _external_import_harness(monkeypatch)
  )

  with pytest.raises(RuntimeError, match="必须持有账户协调锁"):
    await service.import_external_entry("run-1", "account-1", "101")

  db.get.assert_not_awaited()
  register.assert_not_awaited()
  apply_patch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("order_id", ["101", "00101", "+101"])
async def test_external_import_retry_uses_ledger_without_recreating_batch(monkeypatch, order_id):
  service, db, repo, register, apply_patch, state_record, added = (
    _external_import_harness(monkeypatch)
  )
  repo.find_source.return_value = SimpleNamespace(strategy_run_id="run-1")
  result = await service.import_external_entry(
    "run-1", "account-1", order_id, account_coordination_held=True
  )
  assert result["success"] is True
  assert state_record.version == 4
  assert added == []
  db.commit.assert_not_awaited()
  register.assert_not_awaited()
  apply_patch.assert_not_called()
  repo.save.assert_not_awaited()
  repo.find_source.assert_awaited_once_with("account-1", "order:101")


@pytest.mark.asyncio
async def test_external_import_publication_failure_keeps_durable_recovery_image(monkeypatch):
  service, db, repo, register, apply_patch, state_record, added = (
    _external_import_harness(monkeypatch)
  )
  apply_patch.side_effect = RuntimeError("publication failed")
  with pytest.raises(RuntimeError, match="publication failed"):
    await service.import_external_entry(
      "run-1", "account-1", "101", account_coordination_held=True
    )
  db.commit.assert_awaited_once()
  db.rollback.assert_not_awaited()
  assert state_record.version == 5
  assert state_record.custom_state["instrument_states"]["600000.SH"]["batch_id"] == added[0].batch_id
  repo.save.assert_awaited_once()
  register.assert_awaited_once()


def test_t_trade_parameters_require_floor_below_target():
  with pytest.raises(ValueError, match="初始保护线必须低于止盈武装线"):
    TTradeService._validate_parameters(
      {"target_profit_pct": 2.0, "base_floor_pct": 2.0},
      StrategyRunMode.PAPER,
    )


def test_t_trade_amount_hard_cap_must_cover_target():
  with pytest.raises(ValueError, match="硬上限不能低于目标"):
    TTradeService._validate_parameters(
      {"target_trade_amount": 10_000, "max_trade_amount": 9_000},
      StrategyRunMode.PAPER,
    )


def test_t_trade_momentum_window_must_cover_minimum_move():
  with pytest.raises(ValueError, match="momentum_min_move_seconds"):
    TTradeService._validate_parameters(
      {
        "signal_policy": signal_policy(
          momentum_window_seconds=30,
          momentum_min_move_seconds=31,
        )
      },
      StrategyRunMode.PAPER,
    )


def test_t_trade_momentum_vwap_band_must_be_ordered():
  with pytest.raises(ValueError, match="momentum VWAP premium band"):
    TTradeService._validate_parameters(
      {
        "signal_policy": signal_policy(
          momentum_min_vwap_premium_pct=3.5,
          momentum_max_vwap_premium_pct=3.5,
        )
      },
      StrategyRunMode.PAPER,
    )


def test_t_trade_high_profit_arm_must_exceed_base_arm():
  with pytest.raises(ValueError, match="高利润保护武装线必须高于"):
    TTradeService._validate_parameters(
      {
        "target_profit_pct": 4.0,
        "high_profit_arm_pct": 4.0,
      },
      StrategyRunMode.PAPER,
    )


def test_t_trade_high_profit_drawdown_must_stay_below_arm():
  with pytest.raises(ValueError, match="高利润最大回吐必须低于"):
    TTradeService._validate_parameters(
      {
        "high_profit_arm_pct": 4.0,
        "high_profit_max_drawdown_pct": 4.0,
      },
      StrategyRunMode.PAPER,
    )


def test_live_t_trade_accepts_unlimited_protection():
  TTradeService._validate_parameters(
    {"time_exit_mode": "UNLIMITED", "hard_stop_enabled": False},
    StrategyRunMode.LIVE,
  )


def test_live_time_exit_requires_safe_afternoon_time():
  with pytest.raises(ValueError, match="14:30 到 14:57"):
    TTradeService._validate_parameters(
      {"time_exit_mode": "END_OF_DAY", "time_exit_time": "10:00"},
      StrategyRunMode.LIVE,
    )


def test_hard_stop_is_validated_only_when_enabled():
  TTradeService._validate_parameters(
    {"hard_stop_enabled": False, "hard_stop_pct": 1.0},
    StrategyRunMode.PAPER,
  )
  with pytest.raises(ValueError, match="大于 -10 且小于 0"):
    TTradeService._validate_parameters(
      {"hard_stop_enabled": True, "hard_stop_pct": 0.0},
      StrategyRunMode.PAPER,
    )


def test_legacy_exit_parameters_are_normalized():
  normalized = TTradeService._normalize_exit_settings(
    {
      "flatten_end_of_day": True,
      "end_of_day_exit_time": "14:48",
      "hard_stop_pct": -1.0,
    }
  )
  assert normalized["time_exit_mode"] == "END_OF_DAY"
  assert normalized["time_exit_time"] == "14:48"
  assert normalized["hard_stop_enabled"] is True


def test_t_trade_mapping_decodes_persisted_json_parameters():
  assert TTradeService._mapping(
    '{"account_id":"300000013250","target_trade_amount":10000}'
  ) == {
    "account_id": "300000013250",
    "target_trade_amount": 10000,
  }


def test_t_trade_mapping_rejects_non_object_json():
  assert TTradeService._mapping('["not", "an", "object"]') == {}


def test_session_projection_maps_only_server_signal_snapshot():
  now = datetime(2026, 8, 13, 10, 5, tzinfo=timezone.utc)
  run = SimpleNamespace(
    id="run-telemetry",
    mode=StrategyRunMode.PAPER,
    created_at=now,
    updated_at=now,
  )
  service = TTradeService()
  params = {"account_id": "account-1"}
  state = {
    "opportunity": {
      "latest_evaluation": {
        "evaluated_at_ms": int(now.timestamp() * 1000),
        "data_health": "READY",
        "opportunity_score": 61.0,
        "features": {"session_vwap": None},
      }
    }
  }

  projected = service._project_session(
    run=run,
    run_status="RUNNING",
    error_message=None,
    params=params,
    stock_code="600000.SH",
    state=state,
  )
  missing = service._project_session(
    run=run,
    run_status="RUNNING",
    error_message=None,
    params=params,
    stock_code="000001.SZ",
    state={},
  )

  assert projected["signal_snapshot"] == state["opportunity"]["latest_evaluation"]
  assert projected["signal_snapshot"]["features"]["session_vwap"] is None
  assert missing["signal_snapshot"] is None


@pytest.mark.asyncio
async def test_run_sessions_uses_complete_live_strategy_state_over_compact_checkpoint():
  now = datetime(2026, 8, 13, 10, 5, tzinfo=timezone.utc)
  run = SimpleNamespace(
    id="run-live-state",
    strategy=SimpleNamespace(class_name="AshareIntradayTAssistantStrategy"),
    instruments=["600000.SH"],
    status=SimpleNamespace(value="running"),
    mode=StrategyRunMode.PAPER,
    parameters={"account_id": "account-1"},
    created_at=now,
    updated_at=now,
    error_message=None,
  )
  complete_snapshot = {
    "instrument_code": "600000.SH",
    "trade_date": "2026-08-13",
    "evaluated_at_ms": int(now.timestamp() * 1000),
    "source_time_ms": int(now.timestamp() * 1000),
    "tick_ordinal": 3,
    "continuity_generation": "generation-1",
    "data_health": "READY",
    "features": {"sample_count": 25},
    "pullback": {"phase": "REBOUND_CONFIRMING"},
    "momentum": {"phase": "BASELINING"},
    "selected_path": "NONE",
    "preview_threshold": 55.0,
    "candidate_threshold": 72.0,
    "revalidate_threshold": 60.0,
    "rearm_threshold": 45.0,
    "signal_version": 7,
    "candidate_state_version": 7,
    "policy_version": "policy-v3",
    "config_version": 3,
    "feature_schema_version": "1",
  }
  live_state = {
    "instrument_states": {
      "600000.SH": {
        "status": "OBSERVING",
        "last_price": 10.5,
        "opportunity": {"latest_evaluation": complete_snapshot},
      }
    }
  }
  runtime = SimpleNamespace(
    strategy=SimpleNamespace(
      state=SimpleNamespace(to_dict=lambda: live_state),
    )
  )
  manager = SimpleNamespace(get_run=lambda _run_id: runtime)
  service = TTradeService(manager)
  service._load_persisted_run = AsyncMock(
    return_value=(
      run,
      {
        "instrument_states": {
          "600000.SH": {
            "status": "OBSERVING",
            "opportunity": {
              "latest_evaluation": {
                "instrument_code": "600000.SH",
                "data_health": "INSUFFICIENT",
              }
            },
          }
        }
      },
    )
  )

  sessions = await service.get_run_sessions("run-live-state")

  assert sessions[0]["signal_snapshot"] == complete_snapshot
  assert sessions[0]["last_price"] == 10.5


def test_signal_policy_normalization_assigns_deterministic_version():
  first = TTradeService._normalize_signal_policy(signal_policy(candidate_score=74.0))
  second = TTradeService._normalize_signal_policy(signal_policy(candidate_score=74.0))

  assert first == second
  assert first["policy_version"].startswith("t_trade_opportunity_v3.")
  assert first["policy_version"] != "t_trade_opportunity_v3.0.0"

  version_payload = {
    key: value for key, value in first.items() if key != "policy_version"
  }
  encoded = json.dumps(
    version_payload,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  ).encode("utf-8")
  assert first["policy_version"] == (
    f"t_trade_opportunity_v3.{hashlib.sha256(encoded).hexdigest()[:12]}"
  )

  spoofed = signal_policy(candidate_score=74.0)
  spoofed["policy_version"] = "client-controlled-version"
  assert TTradeService._normalize_signal_policy(spoofed) == first


def test_signal_policy_normalization_rejects_partial_unknown_and_old_feature_schema():
  with pytest.raises(ValueError, match="signal_policy missing fields"):
    TTradeService._normalize_signal_policy({"candidate_score": 74.0})
  with pytest.raises(ValueError, match="signal_policy has unknown fields"):
    TTradeService._normalize_signal_policy({**signal_policy(), "hidden_magic": 1})
  with pytest.raises(ValueError, match="feature_schema_version is not current"):
    TTradeService._normalize_signal_policy(signal_policy(feature_schema_version=999))


@pytest.mark.parametrize(
  "payload",
  [
    None,
    signal_policy(),
    signal_policy(candidate_score=74.0, policy_version="client-version"),
  ],
)
def test_signal_policy_normalization_matches_application_canonical(payload):
  assert TTradeService._normalize_signal_policy(payload) == normalize_signal_policy(
    payload
  )


@pytest.mark.parametrize(
  "payload",
  [
    "not-a-policy",
    [],
    {"candidate_score": 74.0},
    {**signal_policy(), "hidden_magic": 1},
  ],
)
def test_signal_policy_validation_errors_match_application_canonical(payload):
  with pytest.raises(ValueError) as application_error:
    normalize_signal_policy(payload)
  with pytest.raises(type(application_error.value)) as service_error:
    TTradeService._normalize_signal_policy(payload)
  assert str(service_error.value) == str(application_error.value)


def test_policy_version_hash_covers_every_configuration_category():
  variants = [
    {"max_quote_age_ms": 3_001},
    {"pullback_min_samples": 4},
    {"pullback_required_fields": ["bid_price", "ask_price"]},
    {"allowed_session_codes": ["CONTINUOUS_AM"]},
    {"continuous_am_start_time": "09:31:00"},
    {"pullback_lookback_seconds": 301},
    {"profile_pullback_threshold_min_multiplier": 0.8},
    {"pullback_depth_weight": 24.0, "pullback_rebound_weight": 21.0},
    {"pullback_rebound_score_max_pct": 0.25},
    {"pullback_data_quality_penalty_points": 11.0},
    {"candidate_confirm_seconds": 3},
  ]
  versions = {
    TTradeService._normalize_signal_policy(signal_policy(**overrides))["policy_version"]
    for overrides in variants
  }

  assert len(versions) == len(variants)
  assert "t_trade_opportunity_v3.0.0" not in versions


@pytest.mark.asyncio
async def test_ensure_account_strategy_running_starts_restored_idle_runtime():
  runtime = SimpleNamespace(
    status=SimpleNamespace(value="PENDING"),
    task=None,
  )
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    start_strategy=AsyncMock(return_value=True),
    resume_strategy=AsyncMock(),
  )
  service = TTradeService(manager)

  changed = await service.ensure_account_strategy_running(
    "run-restored",
    account_coordination_held=True,
  )

  assert changed is True
  manager.start_strategy.assert_awaited_once_with(
    "run-restored",
    t_trade_account_coordination_held=True,
  )
  manager.resume_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_account_strategy_running_resumes_live_paused_runtime():
  runtime = SimpleNamespace(
    status=SimpleNamespace(value="PAUSED"),
    task=SimpleNamespace(done=lambda: False),
  )
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    start_strategy=AsyncMock(),
    resume_strategy=AsyncMock(return_value=True),
  )
  service = TTradeService(manager)

  changed = await service.ensure_account_strategy_running("run-paused")

  assert changed is True
  manager.resume_strategy.assert_awaited_once_with("run-paused")
  manager.start_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_block_account_strategy_entries_clears_only_entry_authorization():
  runtime = SimpleNamespace(
    context=SimpleNamespace(parameters={"account_id": "account-1"}),
  )
  invalidate = AsyncMock(return_value=True)
  manager = SimpleNamespace(
    get_run=lambda _run_id: runtime,
    executor=SimpleNamespace(invalidate_t_trade_entry_authority=invalidate),
  )
  service = TTradeService(manager)

  await service.block_account_strategy_entries(
    "run-global",
    reason="CONFIG_APPLY_PENDING",
  )

  invalidate.assert_called_once_with(
    "run-global",
    account_id="account-1",
    reason="CONFIG_APPLY_PENDING",
  )


@pytest.mark.asyncio
async def test_reject_entry_captures_stock_before_terminalizing_intent():
  calls: list[tuple[str, str | None, str | None]] = []

  async def get_session(
    run_id: str,
    *,
    intent_id: str | None = None,
    stock_code: str | None = None,
  ):
    calls.append((run_id, intent_id, stock_code))
    if intent_id:
      return {"stock_code": "600000.SH", "status": "AWAITING_APPROVAL"}
    return {"stock_code": stock_code, "status": "REJECTED"}

  reject = AsyncMock(return_value={"success": True, "code": "REJECTED"})
  service = TTradeService(
    SimpleNamespace(executor=SimpleNamespace(reject_trade_intent=reject))
  )
  service.get_session = get_session

  result = await service.reject_entry("run-1", "intent-1")

  assert result["session"]["stock_code"] == "600000.SH"
  assert calls == [
    ("run-1", "intent-1", None),
    ("run-1", None, "600000.SH"),
  ]
  reject.assert_awaited_once_with("run-1", "intent-1", reason="USER_REJECTED")


@pytest.mark.asyncio
async def test_update_account_strategy_forwards_explicit_configuration_change():
  manager = SimpleNamespace(
    reconcile_run_instruments=AsyncMock(
      return_value={"added": [], "removed": [], "instruments": ["600000.SH"]}
    )
  )
  service = TTradeService(manager)

  await service.update_account_strategy(
    "run-global",
    {},
    ["600000.SH"],
    {},
    configuration_changed=False,
  )

  manager.reconcile_run_instruments.assert_awaited_once()
  assert (
    manager.reconcile_run_instruments.await_args.kwargs["configuration_changed"]
    is False
  )
