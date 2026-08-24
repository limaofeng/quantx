"""Engine-owned account-level T-trade monitor tests."""

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_engine.t_trade_global_monitor as monitor_module
from quantx_domain.trading.t_trade_opportunity_engine import (
  OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
  CandidateControl,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
  OpportunityState,
  reduce_opportunity,
  transition_candidate,
)
from quantx_engine.t_trade_global_monitor import (
  CONFIG_APPLIED_CODE,
  CONFIG_APPLY_PENDING_CODE,
  CONFIG_APPLY_PENDING_MARKER,
  TTradeConfigVersionConflict,
  TTradeGlobalMonitorService,
)
from quantx_infrastructure.services.t_trade_service import TTradeService


def signal_policy(**overrides):
  payload = OpportunityPolicy().to_dict()
  payload.update(overrides)
  return payload


def test_snapshot_sequence_is_exposed_without_graphql_int_overflow():
  sequence = 1784082686848005
  data = TTradeGlobalMonitorService._snapshot_data({"sequence": sequence})
  assert data["position_snapshot_sequence"] == str(sequence)


def position(
  stock_code: str,
  *,
  volume: int = 1000,
  available: int = 1000,
  name: str = "测试股票",
):
  return SimpleNamespace(
    stock_code=stock_code,
    instrument_name=name,
    volume=volume,
    can_use_volume=available,
  )


def agent_snapshot(*, sequence: int = 1):
  return {
    "account_id": "account-1",
    "sequence": sequence,
    "source": "QMT_AGENT",
    "reported_at": datetime(2026, 8, 23, 1, 0),
    "received_at": datetime(2026, 8, 23, 1, 0),
    "position_count": 1,
    "is_complete": True,
    "last_error": None,
  }


def config(
  *,
  enabled: bool = True,
  ignored=None,
  version: int = 2,
  run_id: str = "run-global",
):
  return SimpleNamespace(
    id="global-1",
    account_id="account-1",
    enabled=enabled,
    mode="paper",
    auto_exit_acknowledged=False,
    ignored_stock_codes=list(ignored or []),
    settings={},
    config_version=version,
    strategy_run_id=run_id,
    universe_revision=3,
    last_reconciled_at=None,
    last_error=None,
    created_at=None,
    updated_at=None,
  )


def session(
  code: str,
  *,
  active: int = 0,
  pending: str = "",
  run_status: str = "running",
  global_config_version=2,
):
  return {
    "run_id": "run-global",
    "stock_code": code,
    "mode": "paper",
    "run_status": run_status,
    "active_volume": active,
    "pending_entry_intent_id": pending or None,
    "pending_exit_intent_id": None,
    "global_config_version": global_config_version,
    "completed_cycles": 0,
    "last_net_profit_pct": 0.0,
  }


def test_ignored_codes_are_normalized_and_deduplicated():
  service = TTradeGlobalMonitorService()
  assert service._normalize_ignored_codes(
    ["600000", "600000.SH", " 000001 ", "830001"]
  ) == ["000001.SZ", "600000.SH", "830001.BJ"]
  with pytest.raises(ValueError, match="无效股票代码"):
    service._normalize_ignored_codes(["not-a-stock"])


def test_global_settings_expose_one_nested_signal_policy_contract():
  settings = TTradeGlobalMonitorService()._normalized_settings({})

  assert settings["signal_policy"]["policy_version"] == ("t_trade_opportunity_v3.0.0")
  assert settings["signal_policy"]["candidate_score"] == 72.0
  assert set(settings["signal_policy"]) == {
    "policy_version",
    "feature_schema_version",
    *OpportunityPolicy.configurable_field_names(),
  }
  assert "signal_lookback_seconds" not in settings
  assert "momentum_window_seconds" not in settings


@pytest.mark.asyncio
async def test_save_config_marks_commit_pending_then_clears_after_reconcile(
  monkeypatch: pytest.MonkeyPatch,
):
  current = config(version=2)
  saved = []

  class Repository:
    def __init__(self, _db):
      pass

    async def find_by_account_for_update(self, _account_id):
      return current

    async def save(self, value):
      saved.append(value.last_error)
      return value

  async def db_stream():
    yield object()

  monkeypatch.setattr(monitor_module, "get_async_db", db_stream)
  monkeypatch.setattr(monitor_module, "TTradeGlobalConfigRepository", Repository)
  service = TTradeGlobalMonitorService()
  service._block_new_entries_if_needed = AsyncMock()

  reconcile_calls = 0

  async def reconcile_side_effect(_account_id):
    nonlocal reconcile_calls
    reconcile_calls += 1
    if reconcile_calls == 1:
      return {"config_version": 3, "last_error": "rewarm unavailable"}
    current.last_error = None
    return {"config_version": 4, "last_error": None}

  service._reconcile_account_locked = AsyncMock(
    side_effect=reconcile_side_effect
  )

  pending = await service.save_config(
    {"account_id": "account-1", "expected_config_version": 2}
  )
  assert saved[0] == f"{CONFIG_APPLY_PENDING_MARKER}: config_version=3"
  assert pending["apply_status"] == "PENDING"
  assert pending["apply_code"] == CONFIG_APPLY_PENDING_CODE

  recovered = await service.save_config(
    {"account_id": "account-1", "expected_config_version": 3}
  )
  assert saved[1] == f"{CONFIG_APPLY_PENDING_MARKER}: config_version=4"
  assert recovered["apply_status"] == "APPLIED"
  assert recovered["apply_code"] == CONFIG_APPLIED_CODE
  assert current.last_error is None
  assert service._block_new_entries_if_needed.await_count == 2


@pytest.mark.asyncio
async def test_post_commit_reconcile_failure_returns_durable_pending_outcome(
  monkeypatch: pytest.MonkeyPatch,
):
  current = config(version=2)
  saved_errors = []

  class Repository:
    def __init__(self, _db):
      pass

    async def find_by_account_for_update(self, _account_id):
      return current

    async def find_by_id(self, _config_id):
      return current

    async def save(self, value):
      saved_errors.append(value.last_error)
      return value

  async def db_stream():
    yield object()

  monkeypatch.setattr(monitor_module, "get_async_db", db_stream)
  monkeypatch.setattr(monitor_module, "TTradeGlobalConfigRepository", Repository)
  service = TTradeGlobalMonitorService()
  service._block_new_entries_if_needed = AsyncMock()
  service._reconcile_account_locked = AsyncMock(
    side_effect=RuntimeError("rewarm exploded")
  )
  service.get_monitor = AsyncMock(
    return_value={"account_id": "account-1", "config_version": 3, "last_error": None}
  )

  result = await service.save_config(
    {"account_id": "account-1", "expected_config_version": 2}
  )

  assert saved_errors[0] == f"{CONFIG_APPLY_PENDING_MARKER}: config_version=3"
  assert CONFIG_APPLY_PENDING_MARKER in saved_errors[-1]
  assert "rewarm exploded" in saved_errors[-1]
  assert result["apply_status"] == "PENDING"
  assert result["apply_code"] == CONFIG_APPLY_PENDING_CODE
  assert CONFIG_APPLY_PENDING_MARKER in result["last_error"]
  assert service._block_new_entries_if_needed.await_count == 2


@pytest.mark.asyncio
async def test_session_read_failure_preserves_run_pointer_and_blocks_new_entries():
  service = TTradeGlobalMonitorService()
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(return_value=[position("600000.SH")])
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-global"]
  )
  service.session_service.get_run_sessions = AsyncMock(
    side_effect=RuntimeError("runtime state unavailable")
  )
  service.session_service.block_account_strategy_entries = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  assert current.strategy_run_id == "run-global"
  service.session_service.block_account_strategy_entries.assert_awaited_once_with(
    "run-global",
    reason=CONFIG_APPLY_PENDING_MARKER,
  )
  errors = service._save_reconcile_config.await_args.args[1]
  assert "状态读取失败" in errors[0]


@pytest.mark.asyncio
async def test_unknown_duplicate_run_is_not_stopped_or_adopted():
  service = TTradeGlobalMonitorService()
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-global", "run-duplicate"]
  )
  service.session_service.get_run_sessions = AsyncMock(
    side_effect=[
      [session("600000.SH")],
      RuntimeError("session projection unavailable"),
    ]
  )
  service.session_service.stop_account_strategy = AsyncMock()
  service.session_service.block_account_strategy_entries = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock(
    return_value={"added": [], "removed": [], "instruments": []}
  )
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_not_awaited()
  service.session_service.block_account_strategy_entries.assert_any_await(
    "run-duplicate",
    reason=CONFIG_APPLY_PENDING_MARKER,
  )
  assert current.strategy_run_id == "run-global"
  errors = service._save_reconcile_config.await_args.args[1]
  assert any("run-duplicate" in error and "状态未知" in error for error in errors)


@pytest.mark.asyncio
async def test_signal_policy_preview_is_read_only_and_reports_rewarm():
  service = TTradeGlobalMonitorService()
  current = config(version=2)
  service._load_config = AsyncMock(return_value=current)

  result = await service.preview_signal_policy(
    {
      "account_id": "account-1",
      "expected_config_version": 2,
      "signal_policy": signal_policy(candidate_score=74.0),
    }
  )

  assert result["errors"] == []
  assert result["requires_rewarm"] is True
  assert "candidate_score" in result["changed_fields"]
  assert result["normalized_policy"]["candidate_score"] == 74.0


@pytest.mark.asyncio
async def test_signal_policy_preview_rejects_stale_config_version():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config(version=3))

  with pytest.raises(TTradeConfigVersionConflict) as raised:
    await service.preview_signal_policy(
      {
        "account_id": "account-1",
        "expected_config_version": 2,
        "signal_policy": signal_policy(),
      }
    )

  assert raised.value.actual == 3


@pytest.mark.asyncio
async def test_monitor_projects_multiple_holdings_from_one_run():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config(ignored=["000001.SZ"]))
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(
    return_value=[
      position("600000.SH", name="浦发银行"),
      position("000001.SZ", name="平安银行"),
      position("300001.SZ", available=0, name="特锐德"),
    ]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", pending="intent-1")]
  )

  result = await service.get_monitor("account-1")

  assert result["strategy_run_id"] == "run-global"
  assert result["holding_count"] == 3
  assert result["eligible_count"] == 1
  assert result["ignored_count"] == 1
  assert result["monitored_count"] == 1
  assert result["pending_signal_count"] == 1
  assert {item["status"] for item in result["holdings"]} == {
    "IGNORED",
    "INELIGIBLE",
    "MONITORED",
  }
  ineligible = next(
    item for item in result["holdings"] if item["stock_code"] == "300001.SZ"
  )
  assert ineligible["reason"] == "昨日可用库存 0 股，不足一手（100 股）"


@pytest.mark.asyncio
async def test_deleted_monitor_projection_rebuilds_from_truth_without_changing_decision(
  monkeypatch: pytest.MonkeyPatch,
):
  profile = OpportunityReferenceProfile(
    profile_version="profile-v1",
    profile_schema_version=OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
    as_of_trade_date="2026-08-20",
    pullback_threshold_pct=0.8,
    momentum_rise_threshold_pct=0.8,
    momentum_amount_velocity_ratio=2.0,
    pullback_max_spread_ticks=3,
    momentum_max_spread_ticks=10,
  )
  opportunity_state = OpportunityState.initial()
  reduction = None
  for ordinal, (seconds, price, amount, volume) in enumerate(
    (
      (0, 100.0, 1_000_000.0, 10_000.0),
      (5, 99.0, 1_050_000.0, 10_500.0),
      (20, 99.0, 1_100_000.0, 11_000.0),
      (22, 99.30, 1_120_000.0, 11_200.0),
      (24, 99.32, 1_140_000.0, 11_400.0),
    )
  ):
    reduction = reduce_opportunity(
      opportunity_state,
      OpportunitySample(
        instrument_code="600000.SH",
        trade_date="2026-08-21",
        source_time_ms=seconds * 1_000,
        tick_ordinal=ordinal,
        price=price,
        continuity_generation="generation-1",
        bid_price=price - 0.01,
        ask_price=price,
        cumulative_amount=amount,
        cumulative_volume=volume,
      ),
      reference_profile=profile,
    )
    opportunity_state = reduction.state
  assert reduction is not None
  assert reduction.candidate_created is not None

  intent_truth = SimpleNamespace(intent_id="intent-authoritative")
  opportunity_state = transition_candidate(
    opportunity_state,
    CandidateControl(
      awaiting_approval_candidate_id=reduction.candidate_created.candidate_id
    ),
    source_time_ms=reduction.candidate_created.source_time_ms,
  )
  evaluation_truth = {
    **reduction.evaluation.to_dict(),
    "candidate_status": opportunity_state.candidate_status.value,
    "pending_entry_intent_id": intent_truth.intent_id,
  }
  batch_truth = SimpleNamespace(
    batch_id="batch-authoritative",
    entry_filled_volume=300,
    exit_filled_volume=100,
  )
  runtime_state_truth = {
    "600000.SH": {
      "status": "AWAITING_ENTRY",
      "pending_entry_intent_id": intent_truth.intent_id,
      "pending_exit_intent_id": "",
      "entry_filled_volume": 0,
      "exit_filled_volume": 0,
      "opportunity": {
        **opportunity_state.to_dict(),
        "state_version": 5,
        "latest_evaluation": evaluation_truth,
      },
    },
    "000001.SZ": {
      "status": "ACTIVE",
      "batch_id": batch_truth.batch_id,
      "pending_entry_intent_id": "",
      "pending_exit_intent_id": "",
      "entry_filled_volume": batch_truth.entry_filled_volume,
      "exit_filled_volume": batch_truth.exit_filled_volume,
      "opportunity": {},
    },
  }
  runtime_state_before_rebuild = deepcopy(runtime_state_truth)
  run = SimpleNamespace(
    id="run-global",
    mode=SimpleNamespace(value="paper"),
    created_at=datetime(2026, 8, 21, 9, 30),
    updated_at=datetime(2026, 8, 21, 9, 35),
  )
  session_projector = TTradeService()
  authoritative_sessions = [
    session_projector._project_session(
      run=run,
      run_status="running",
      error_message=None,
      params={"account_id": "account-1", "global_config_version": 2},
      stock_code=stock_code,
      state=state,
    )
    for stock_code, state in runtime_state_truth.items()
  ]
  next_sample = OpportunitySample(
    instrument_code="600000.SH",
    trade_date="2026-08-21",
    source_time_ms=30_000,
    tick_ordinal=5,
    price=99.34,
    continuity_generation="generation-1",
    bid_price=99.33,
    ask_price=99.34,
    cumulative_amount=1_160_000.0,
    cumulative_volume=11_600.0,
  )
  decision_without_projection_rebuild = reduce_opportunity(
    OpportunityState.from_dict(runtime_state_truth["600000.SH"]["opportunity"]),
    next_sample,
    reference_profile=profile,
  ).to_dict()

  projection_store = {
    "account-1": {
      "pending_signal_count": 99,
      "active_batch_count": 99,
      "sessions": [{"stock_code": "STALE"}],
    }
  }
  del projection_store["account-1"]
  assert "account-1" not in projection_store
  read_projection = AsyncMock(return_value=None)

  async def save_projection(account_id, payload):
    projection_store[account_id] = deepcopy(payload)
    return {
      **deepcopy(payload),
      "projection_version": "1",
      "projection_generated_at": datetime(2026, 8, 21, 9, 36),
    }

  monkeypatch.setattr(
    monitor_module.t_trade_monitor_projection_service,
    "get",
    read_projection,
  )
  monkeypatch.setattr(
    monitor_module.t_trade_monitor_projection_service,
    "save",
    save_projection,
  )
  readiness = {
    "stage": "SHADOW",
    "engine_status": "READY",
    "agent_status": "READY",
    "reconcile_status": "READY",
    "kill_switch": False,
    "can_approve": False,
    "can_activate_live": False,
    "blocked_reasons": [],
  }
  monkeypatch.setattr(
    monitor_module,
    "TTradeOperationsService",
    lambda: SimpleNamespace(readiness=AsyncMock(return_value=readiness)),
  )
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH"), position("000001.SZ")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=authoritative_sessions
  )

  rebuilt = await service.get_monitor("account-1")

  read_projection.assert_not_awaited()
  assert projection_store["account-1"]["pending_signal_count"] == 1
  assert projection_store["account-1"]["active_batch_count"] == 1
  rebuilt_sessions = {item["stock_code"]: item for item in rebuilt["sessions"]}
  assert rebuilt_sessions["600000.SH"]["signal_snapshot"] == evaluation_truth
  assert rebuilt_sessions["600000.SH"]["pending_entry_intent_id"] == (
    intent_truth.intent_id
  )
  assert rebuilt_sessions["000001.SZ"]["active_volume"] == 200
  assert runtime_state_truth == runtime_state_before_rebuild

  decision_after_projection_rebuild = reduce_opportunity(
    OpportunityState.from_dict(runtime_state_truth["600000.SH"]["opportunity"]),
    next_sample,
    reference_profile=profile,
  ).to_dict()
  assert decision_after_projection_rebuild == decision_without_projection_rebuild


@pytest.mark.asyncio
async def test_monitor_embeds_full_readiness_for_live_controls(
  monkeypatch: pytest.MonkeyPatch,
):
  readiness = {
    "stage": "SHADOW",
    "engine_status": "READY",
    "agent_status": "READY",
    "reconcile_status": "READY",
    "kill_switch": False,
    "can_approve": False,
    "can_activate_live": False,
    "blocked_reasons": ["尚未基于最新完整快照建立账户实盘窗口"],
    "controlled_window_active": False,
  }
  operations = SimpleNamespace(readiness=AsyncMock(return_value=readiness))
  monkeypatch.setattr(
    monitor_module,
    "TTradeOperationsService",
    lambda: operations,
  )
  save_projection = AsyncMock(side_effect=lambda _account_id, data: data)
  monkeypatch.setattr(
    monitor_module.t_trade_monitor_projection_service,
    "save",
    save_projection,
  )
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(return_value=[])
  service.session_service.get_run_sessions = AsyncMock(return_value=[])

  result = await service.get_monitor("account-1")

  assert result["readiness"] is readiness
  assert result["agent_status"] == "READY"
  assert result["blocked_reasons"] == readiness["blocked_reasons"]


@pytest.mark.asyncio
async def test_four_lot_holding_is_eligible_without_a_percentage_gate():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config(run_id=None))
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(
    return_value=[position("300917.SZ", volume=400, available=400)]
  )

  result = await service.get_monitor("account-1")

  assert result["eligible_count"] == 1
  assert result["holdings"][0]["eligible"] is True
  assert result["holdings"][0]["status"] == "PENDING_START"


def test_disabled_monitor_reports_stopped_before_inventory_eligibility():
  service = TTradeGlobalMonitorService()

  status, reason = service._holding_status(
    config_data={"enabled": False},
    stock_code="600000.SH",
    volume=1000,
    available=1000,
    is_eligible=False,
    is_ignored=False,
    session=None,
  )

  assert status == "STOPPED"
  assert reason == "全局监控未启动"


@pytest.mark.asyncio
async def test_monitor_uses_instrument_master_when_snapshot_name_is_code():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service._load_instrument_names = AsyncMock(return_value={"001248.SZ": "华润新能"})
  service.position_service.get_snapshot_status = AsyncMock(return_value=None)
  service.position_service.get_positions = AsyncMock(
    return_value=[position("001248.SZ", name="001248.SZ")]
  )
  service.session_service.get_run_sessions = AsyncMock(return_value=[])

  result = await service.get_monitor("account-1")

  assert result["holdings"][0]["instrument_name"] == "华润新能"
  service._load_instrument_names.assert_awaited_once_with(["001248.SZ"])


@pytest.mark.asyncio
async def test_reconcile_creates_one_run_for_dynamic_holdings_universe():
  service = TTradeGlobalMonitorService()
  service.session_service.list_active_account_run_ids = AsyncMock(return_value=[])
  current = config(ignored=["000001.SZ"], run_id=None)
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[
      position("600000.SH"),
      position("000001.SZ"),
      position("300001.SZ", available=99),
      position("830001.BJ"),
    ]
  )
  service.session_service.start_account_strategy = AsyncMock(return_value="run-new")
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.start_account_strategy.assert_awaited_once()
  payload, instruments, metadata = (
    service.session_service.start_account_strategy.await_args.args
  )
  assert payload["global_config_version"] == 2
  assert instruments == ["300001.SZ", "600000.SH", "830001.BJ"]
  assert metadata["300001.SZ"]["eligible"] is False
  assert metadata["600000.SH"]["eligible"] is True
  assert current.strategy_run_id == "run-new"


@pytest.mark.asyncio
async def test_reconcile_updates_existing_run_once_for_all_codes():
  service = TTradeGlobalMonitorService()
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-global"]
  )
  service._load_config = AsyncMock(return_value=config())
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH"), position("000001.SZ")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", pending="intent-current")]
  )
  service.session_service.reject_entry = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock(
    return_value={"added": [], "removed": [], "instruments": []}
  )
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.update_account_strategy.assert_awaited_once()
  assert set(service.session_service.update_account_strategy.await_args.args[2]) == {
    "000001.SZ",
    "600000.SH",
  }
  assert (
    service.session_service.update_account_strategy.await_args.kwargs[
      "configuration_changed"
    ]
    is False
  )
  service.session_service.reject_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_marks_a_real_config_version_change_for_one_time_rewarm():
  service = TTradeGlobalMonitorService()
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-global"]
  )
  service._load_config = AsyncMock(return_value=config(version=3))
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", pending="intent-old", global_config_version=2)]
  )
  service.session_service.reject_entry = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock(
    return_value={"added": [], "removed": [], "instruments": []}
  )
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.reject_entry.assert_awaited_once_with(
    "run-global",
    "intent-old",
    reason="GLOBAL_CONFIG_CHANGED",
  )
  assert (
    service.session_service.update_account_strategy.await_args.kwargs[
      "configuration_changed"
    ]
    is True
  )


def test_unknown_run_config_version_fails_closed_as_configuration_change():
  service = TTradeGlobalMonitorService()
  current = config(version=2)

  assert service._configuration_changed(current, []) is True
  assert service._configuration_changed(config(version=None), [session("600000.SH")]) is True
  assert service._configuration_changed(current, [session("600000.SH")]) is False
  assert (
    service._configuration_changed(
      current,
      [session("600000.SH", global_config_version=None)],
    )
    is True
  )
  assert (
    service._configuration_changed(
      current,
      [session("600000.SH", global_config_version="not-a-version")],
    )
    is True
  )


@pytest.mark.asyncio
async def test_reconcile_restores_paused_run_before_updating_universe():
  service = TTradeGlobalMonitorService()
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-global"]
  )
  service._load_config = AsyncMock(return_value=config())
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", run_status="paused")]
  )
  service.session_service.ensure_account_strategy_running = AsyncMock(return_value=True)
  service.session_service.update_account_strategy = AsyncMock(
    return_value={"added": [], "removed": [], "instruments": []}
  )
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.ensure_account_strategy_running.assert_awaited_once_with(
    "run-global",
    account_coordination_held=True,
  )
  service.session_service.update_account_strategy.assert_awaited_once()
  assert service._save_reconcile_config.await_args.args[1] == []


@pytest.mark.asyncio
async def test_disabled_monitor_keeps_only_active_code_in_draining_state():
  service = TTradeGlobalMonitorService()
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-global"]
  )
  service._load_config = AsyncMock(return_value=config(enabled=False))
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH"), position("000001.SZ")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", active=100), session("000001.SZ")]
  )
  service.session_service.update_account_strategy = AsyncMock(return_value={})
  service.session_service.stop_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_not_awaited()
  args = service.session_service.update_account_strategy.await_args.args
  assert args[2] == ["600000.SH"]
  assert args[3]["600000.SH"]["draining"] is True


@pytest.mark.asyncio
async def test_failed_agent_snapshot_does_not_reconcile_or_clear_universe():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service.position_service.read_agent_snapshot = AsyncMock(
    side_effect=RuntimeError("disconnected")
  )
  service.position_service.get_positions = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock()
  service.session_service.block_account_strategy_entries = AsyncMock()
  service._record_reconcile_result = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.position_service.get_positions.assert_not_awaited()
  service.session_service.update_account_strategy.assert_not_awaited()
  service.session_service.block_account_strategy_entries.assert_awaited_once_with(
    "run-global",
    reason=CONFIG_APPLY_PENDING_MARKER,
  )
  service._record_reconcile_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_snapshot_failure_between_reads_blocks_without_reconcile():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service.position_service.read_agent_snapshot = AsyncMock(
    side_effect=[agent_snapshot(), RuntimeError("agent report failed")]
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.update_account_strategy = AsyncMock()
  service.session_service.start_account_strategy = AsyncMock()
  service.session_service.block_account_strategy_entries = AsyncMock()
  service._record_reconcile_result = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.position_service.get_positions.assert_awaited_once()
  service.session_service.update_account_strategy.assert_not_awaited()
  service.session_service.start_account_strategy.assert_not_awaited()
  service.session_service.block_account_strategy_entries.assert_awaited_once_with(
    "run-global",
    reason=CONFIG_APPLY_PENDING_MARKER,
  )
  errors = service._record_reconcile_result.await_args.args[1]
  assert "agent report failed" in errors[0]


@pytest.mark.asyncio
async def test_snapshot_generation_change_between_reads_blocks_without_reconcile():
  service = TTradeGlobalMonitorService()
  service._load_config = AsyncMock(return_value=config())
  service.position_service.read_agent_snapshot = AsyncMock(
    side_effect=[agent_snapshot(sequence=1), agent_snapshot(sequence=2)]
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.update_account_strategy = AsyncMock()
  service.session_service.start_account_strategy = AsyncMock()
  service.session_service.block_account_strategy_entries = AsyncMock()
  service._record_reconcile_result = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.update_account_strategy.assert_not_awaited()
  service.session_service.start_account_strategy.assert_not_awaited()
  service.session_service.block_account_strategy_entries.assert_awaited_once_with(
    "run-global",
    reason=CONFIG_APPLY_PENDING_MARKER,
  )
  errors = service._record_reconcile_result.await_args.args[1]
  assert "读取持仓期间发生变化" in errors[0]


@pytest.mark.asyncio
async def test_terminal_run_without_active_batch_is_replaced():
  service = TTradeGlobalMonitorService()
  service.session_service.list_active_account_run_ids = AsyncMock(return_value=[])
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", run_status="error")]
  )
  service.session_service.stop_account_strategy = AsyncMock(
    return_value={"success": True, "message": "stopped"}
  )
  service.session_service.start_account_strategy = AsyncMock(return_value="run-new")
  service.session_service.update_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_awaited_once_with("run-global")
  service.session_service.start_account_strategy.assert_awaited_once()
  service.session_service.update_account_strategy.assert_not_awaited()
  assert current.strategy_run_id == "run-new"


@pytest.mark.asyncio
async def test_terminal_run_with_active_batch_blocks_replacement():
  service = TTradeGlobalMonitorService()
  service.session_service.list_active_account_run_ids = AsyncMock(return_value=[])
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH", active=100, run_status="error")]
  )
  service.session_service.stop_account_strategy = AsyncMock()
  service.session_service.start_account_strategy = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_not_awaited()
  service.session_service.start_account_strategy.assert_not_awaited()
  service.session_service.update_account_strategy.assert_not_awaited()
  errors = service._save_reconcile_config.await_args.args[1]
  assert "策略运行状态异常" in errors[0]
  assert current.strategy_run_id == "run-global"


@pytest.mark.asyncio
async def test_reconcile_adopts_the_only_active_run_when_pointer_is_lost():
  service = TTradeGlobalMonitorService()
  current = config(run_id=None)
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-existing"]
  )
  service.session_service.get_run_sessions = AsyncMock(
    return_value=[session("600000.SH")]
  )
  service.session_service.update_account_strategy = AsyncMock(
    return_value={"added": [], "removed": [], "instruments": []}
  )
  service.session_service.start_account_strategy = AsyncMock()
  service.session_service.stop_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  assert current.strategy_run_id == "run-existing"
  service.session_service.update_account_strategy.assert_awaited_once()
  service.session_service.start_account_strategy.assert_not_awaited()
  service.session_service.stop_account_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_stops_idle_duplicate_run():
  service = TTradeGlobalMonitorService()
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-duplicate", "run-global"]
  )
  service.session_service.get_run_sessions = AsyncMock(
    side_effect=lambda run_id: [session("600000.SH")]
  )
  service.session_service.stop_account_strategy = AsyncMock(
    return_value={"success": True, "message": "stopped"}
  )
  service.session_service.update_account_strategy = AsyncMock(
    return_value={"added": [], "removed": [], "instruments": []}
  )
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_awaited_once_with(
    "run-duplicate"
  )
  service.session_service.update_account_strategy.assert_awaited_once()
  assert service._save_reconcile_config.await_args.args[1] == []


@pytest.mark.asyncio
async def test_reconcile_blocks_when_duplicate_run_has_open_work():
  service = TTradeGlobalMonitorService()
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-duplicate", "run-global"]
  )
  service.session_service.get_run_sessions = AsyncMock(
    side_effect=lambda run_id: [
      session("600000.SH", active=100 if run_id == "run-duplicate" else 0)
    ]
  )
  service.session_service.stop_account_strategy = AsyncMock()
  service.session_service.block_account_strategy_entries = AsyncMock()
  service.session_service.update_account_strategy = AsyncMock()
  service.session_service.start_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_not_awaited()
  service.session_service.block_account_strategy_entries.assert_any_await(
    "run-duplicate",
    reason=CONFIG_APPLY_PENDING_MARKER,
  )
  assert service.session_service.block_account_strategy_entries.await_count == 2
  service.session_service.update_account_strategy.assert_not_awaited()
  service.session_service.start_account_strategy.assert_not_awaited()
  errors = service._save_reconcile_config.await_args.args[1]
  assert "多个做 T 活跃实例" in errors[0]


@pytest.mark.asyncio
async def test_reconcile_records_duplicate_entry_authority_invalidation_failure():
  service = TTradeGlobalMonitorService()
  current = config()
  service._load_config = AsyncMock(return_value=current)
  service.position_service.read_agent_snapshot = AsyncMock(
    return_value=agent_snapshot()
  )
  service.position_service.get_positions = AsyncMock(
    return_value=[position("600000.SH")]
  )
  service.session_service.list_active_account_run_ids = AsyncMock(
    return_value=["run-duplicate", "run-global"]
  )
  service.session_service.get_run_sessions = AsyncMock(
    side_effect=lambda run_id: [
      session("600000.SH", active=100 if run_id == "run-duplicate" else 0)
    ]
  )
  service.session_service.stop_account_strategy = AsyncMock()
  service.session_service.block_account_strategy_entries = AsyncMock(
    side_effect=RuntimeError("authority store unavailable")
  )
  service.session_service.update_account_strategy = AsyncMock()
  service.session_service.start_account_strategy = AsyncMock()
  service._save_reconcile_config = AsyncMock()
  service.get_monitor = AsyncMock(return_value={"account_id": "account-1"})

  await service.reconcile_account("account-1")

  service.session_service.stop_account_strategy.assert_not_awaited()
  service.session_service.block_account_strategy_entries.assert_any_await(
    "run-duplicate",
    reason=CONFIG_APPLY_PENDING_MARKER,
  )
  errors = service._save_reconcile_config.await_args.args[1]
  assert any("关闭重复实例 run-duplicate 新入场失败" in error for error in errors)
