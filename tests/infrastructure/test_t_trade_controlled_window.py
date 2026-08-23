import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
import quantx_infrastructure.services.t_trade_operations_service as operations_module
from quantx_domain.clock import utcnow
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from quantx_infrastructure.services.t_trade_rollout_evidence_service import (
  V3_ROLLOUT_GATE_CODES,
)


def _session_context(db):
  class SessionContext:
    async def __aenter__(self):
      return db

    async def __aexit__(self, exc_type, exc, traceback):
      return False

  return SessionContext


def _rollout(**overrides):
  values = {
    "account_id": "account-1",
    "stage": "SHADOW",
    "enabled": False,
    "kill_switch": False,
    "policy_version": 3,
    "paused_reason": None,
    "reconcile_status": "READY",
    "last_snapshot_id": "snapshot-1",
    "last_snapshot_hash": "a" * 64,
    "last_snapshot_at": utcnow(),
    "controlled_window_active": False,
    "controlled_window_snapshot_id": None,
    "controlled_window_snapshot_hash": None,
    "controlled_window_started_at": None,
    "controlled_window_started_by_user_id": None,
    "controlled_window_external_order_ids": [],
    "controlled_window_external_trade_ids": [],
    "acknowledged_policy_version": None,
    "activated_by_user_id": None,
    "activated_at": None,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def _window_readiness(**overrides):
  values = {
    "automation_ready": True,
    "blocked_reasons": [],
    "policy_version": 3,
    "controlled_window_active": True,
    "snapshot_id": "snapshot-1",
    "snapshot_hash": "a" * 64,
    "checks": [],
  }
  values.update(overrides)
  return values


def _locked_v3_evidence(*, operator_review_confirmed: bool = False) -> dict:
  return {
    "checks": [
      {
        "code": code,
        "passed": (
          operator_review_confirmed if code == "V3_OPERATOR_REVIEW_CONFIRMED" else True
        ),
        "message": "" if code != "V3_OPERATOR_REVIEW_CONFIRMED" else "review pending",
      }
      for code in sorted(V3_ROLLOUT_GATE_CODES)
    ],
    "summary": {
      "fingerprint": "locked-v3-evidence",
      "operator_review": {
        "review_evidence_fingerprint": "locked-review-evidence",
      },
    },
  }


def test_locked_v3_activation_summary_rejects_duplicate_gate_code() -> None:
  evidence = _locked_v3_evidence()
  # The old dict-comprehension behavior would let the succeeding passed value
  # overwrite this failed proof and permit activation.  A duplicate must be a
  # protocol/evidence integrity failure instead.
  evidence["checks"].insert(
    0,
    {
      "code": "V3_REPLAY_STRICT_CAUSAL",
      "passed": False,
      "message": "duplicate stale failure",
    },
  )

  with pytest.raises(
    ValueError,
    match="V3 上线门禁存在重复检查项：V3_REPLAY_STRICT_CAUSAL",
  ):
    TTradeOperationsService._locked_v3_activation_summary(evidence)


@pytest.fixture(autouse=True)
def _stub_locked_v3_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
  evaluator = SimpleNamespace(evaluate=AsyncMock(return_value=_locked_v3_evidence()))
  monkeypatch.setattr(
    TTradeOperationsService,
    "rollout_evidence_evaluator",
    evaluator,
  )


@pytest.mark.asyncio
async def test_external_snapshot_activity_uses_effective_session_expiry() -> None:
  result = MagicMock()
  result.scalars.return_value.all.return_value = []
  db = SimpleNamespace(execute=AsyncMock(return_value=result))
  payload = {
    "orders": [
      {
        "account_id": "account-1",
        "order_id": "expired-1",
        "order_status": 50,
        "effective_order_status": "EXPIRED",
        "effective_status_reason": "MARKET_SESSION_CLOSED",
      }
    ],
    "trades": [],
  }

  activity = await TTradeOperationsService()._external_snapshot_activity(
    db,
    account_id="account-1",
    payload=payload,
  )

  assert activity == {
    "orders": [
      {
        "business_id": "expired-1",
        "status": "EXPIRED",
        "raw_status": "SUBMITTED",
        "status_reason": "MARKET_SESSION_CLOSED",
      }
    ],
    "trades": [],
  }


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "code,message",
  [
    ("ENGINE_READY", "Engine 未就绪"),
    ("LIVE_AGENT_READY", "Agent 未就绪"),
    ("AGENT_MODE_LIVE", "Agent 非 live"),
    ("PROTOCOL_1_1", "协议不是 1.1"),
    ("SNAPSHOT_RECONCILED", "快照未对账"),
    ("SNAPSHOT_FRESH", "快照已过期"),
    ("SNAPSHOT_ACTIVITY_CLASSIFIED", "活动未分类"),
    ("KILL_SWITCH_CLEAR", "kill switch 已触发"),
  ],
)
async def test_begin_controlled_window_requires_each_preparation_gate(
  code: str,
  message: str,
) -> None:
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=_rollout())
  service.readiness = AsyncMock(
    return_value=_window_readiness(
      controlled_window_active=False,
      checks=[{"code": code, "passed": False, "message": message}],
    )
  )

  with pytest.raises(ValueError, match=message):
    await service.begin_controlled_window(
      "account-1",
      user_id="user-1",
      snapshot_id="snapshot-1",
    )


@pytest.mark.asyncio
async def test_begin_controlled_window_rejects_stale_page_snapshot() -> None:
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=_rollout())
  service.readiness = AsyncMock(
    return_value=_window_readiness(
      controlled_window_active=False,
      snapshot_id="snapshot-new",
    )
  )

  with pytest.raises(ValueError, match="完整快照已经更新"):
    await service.begin_controlled_window(
      "account-1",
      user_id="user-1",
      snapshot_id="snapshot-old",
    )


@pytest.mark.asyncio
async def test_begin_controlled_window_rejects_working_external_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout()
  db = SimpleNamespace(
    get=AsyncMock(return_value=rollout),
    add=MagicMock(),
    commit=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(
    return_value=_window_readiness(
      controlled_window_active=False,
      checks=[
        {"code": "ENGINE_READY", "passed": True, "message": ""},
        {"code": "LIVE_AGENT_READY", "passed": True, "message": ""},
        {"code": "AGENT_MODE_LIVE", "passed": True, "message": ""},
        {"code": "PROTOCOL_1_1", "passed": True, "message": ""},
        {"code": "SNAPSHOT_RECONCILED", "passed": True, "message": ""},
        {"code": "SNAPSHOT_FRESH", "passed": True, "message": ""},
        {
          "code": "SNAPSHOT_ACTIVITY_CLASSIFIED",
          "passed": True,
          "message": "",
        },
        {"code": "KILL_SWITCH_CLEAR", "passed": True, "message": ""},
      ],
    )
  )
  service._latest_full_snapshot = AsyncMock(return_value={"is_complete": True})
  service._external_snapshot_activity = AsyncMock(
    return_value={
      "orders": [{"business_id": "working-1", "status": "SUBMITTED"}],
      "trades": [],
    }
  )

  with pytest.raises(ValueError, match="MiniQMT 撤单"):
    await service.begin_controlled_window(
      "account-1",
      user_id="user-1",
      snapshot_id="snapshot-1",
    )

  db.commit.assert_not_awaited()
  assert rollout.controlled_window_active is False


@pytest.mark.asyncio
async def test_begin_controlled_window_records_terminal_history_as_baseline(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(stage="PAUSED", paused_reason="old pause")
  db = SimpleNamespace(
    get=AsyncMock(return_value=rollout),
    add=MagicMock(),
    commit=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(
    return_value=_window_readiness(
      controlled_window_active=False,
      checks=[
        {"code": code, "passed": True, "message": ""}
        for code in {
          "ENGINE_READY",
          "LIVE_AGENT_READY",
          "AGENT_MODE_LIVE",
          "PROTOCOL_1_1",
          "SNAPSHOT_RECONCILED",
          "SNAPSHOT_FRESH",
          "SNAPSHOT_ACTIVITY_CLASSIFIED",
          "KILL_SWITCH_CLEAR",
        }
      ],
    )
  )
  service._latest_full_snapshot = AsyncMock(return_value={"is_complete": True})
  service._external_snapshot_activity = AsyncMock(
    return_value={
      "orders": [{"business_id": "done-1", "status": "CANCELLED"}],
      "trades": [{"business_id": "trade-1", "status": "FILLED"}],
    }
  )

  await service.begin_controlled_window(
    "account-1",
    user_id="user-1",
    snapshot_id="snapshot-1",
  )

  assert rollout.controlled_window_active is True
  assert rollout.controlled_window_external_order_ids == ["done-1"]
  assert rollout.controlled_window_external_trade_ids == ["trade-1"]
  assert rollout.stage == "SHADOW"
  assert rollout.paused_reason is None
  event = db.add.call_args.args[0]
  assert event.details["policyVersion"] == 3
  db.commit.assert_awaited_once()
  db.add.assert_called_once()


@pytest.mark.asyncio
async def test_direct_live_requires_development_and_exact_confirmation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
  )

  async def get(model, *_args, **_kwargs):
    if model is operations_module.AccountTradingRolloutEvent:
      return None
    return rollout

  db = SimpleNamespace(
    get=AsyncMock(side_effect=get),
    add=MagicMock(),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value=_window_readiness())
  monkeypatch.setattr(operations_module.settings, "environment", "development")

  with pytest.raises(ValueError, match="精确确认"):
    await service.activate_rollout(
      "account-1",
      user_id="user-1",
      acknowledged_policy_version=3,
      target_stage="LIVE",
      confirmation="LIVE:wrong-account",
    )

  await service.activate_rollout(
    "account-1",
    user_id="user-1",
    acknowledged_policy_version=3,
    target_stage="LIVE",
    confirmation="LIVE:account-1",
    operation_id="direct-live-activate-1",
  )

  assert rollout.stage == "LIVE"
  assert rollout.enabled is True
  assert rollout.activated_by_user_id == "user-1"
  assert db.add.call_args.args[0].snapshot_id == "snapshot-1"
  db.commit.assert_awaited_once()
  db.add.assert_called_once()

  monkeypatch.setattr(operations_module.settings, "environment", "production")
  with pytest.raises(ValueError, match="生产环境禁止"):
    await service.activate_rollout(
      "account-1",
      user_id="user-1",
      acknowledged_policy_version=3,
      target_stage="LIVE",
      confirmation="LIVE:account-1",
    )


@pytest.mark.asyncio
async def test_direct_live_requires_all_readiness_and_controlled_window(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(controlled_window_active=False)
  db = SimpleNamespace(
    get=AsyncMock(return_value=rollout),
    add=MagicMock(),
    commit=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  monkeypatch.setattr(operations_module.settings, "environment", "development")
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(
    return_value=_window_readiness(
      automation_ready=False,
      blocked_reasons=["最近成功备份缺失或已超过 24 小时"],
    )
  )

  with pytest.raises(ValueError, match="最近成功备份"):
    await service.activate_rollout(
      "account-1",
      user_id="user-1",
      acknowledged_policy_version=3,
      target_stage="LIVE",
      confirmation="LIVE:account-1",
    )

  service.readiness = AsyncMock(
    return_value=_window_readiness(controlled_window_active=False)
  )
  with pytest.raises(ValueError, match="账户实盘窗口"):
    await service.activate_rollout(
      "account-1",
      user_id="user-1",
      acknowledged_policy_version=3,
      target_stage="LIVE",
      confirmation="LIVE:account-1",
    )

  db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_activation_rejects_snapshot_changed_after_readiness_before_apply(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
  )

  async def locked_rollout(model, *args, **kwargs):
    if model is operations_module.AccountTradingRolloutEvent:
      return None
    # Simulate a complete snapshot arriving after confirm consumed the
    # challenge but before the apply transaction acquired its lock.
    rollout.last_snapshot_id = "snapshot-2"
    rollout.last_snapshot_hash = "b" * 64
    return rollout

  db = SimpleNamespace(
    get=AsyncMock(side_effect=locked_rollout),
    add=MagicMock(),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  monkeypatch.setattr(operations_module.settings, "environment", "development")
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value=_window_readiness())

  with pytest.raises(ValueError, match="完整快照已经更新"):
    await service.activate_rollout(
      "account-1",
      user_id="user-1",
      acknowledged_policy_version=3,
      target_stage="LIVE",
      confirmation="LIVE:account-1",
      expected_snapshot_id="snapshot-1",
      operation_id="challenge-1",
    )

  assert rollout.stage == "SHADOW"
  assert rollout.enabled is False
  db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_begin_rejects_policy_changed_after_readiness_before_apply(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout()

  async def locked_rollout(model, *args, **kwargs):
    if model is operations_module.AccountTradingRolloutEvent:
      return None
    rollout.policy_version = 4
    return rollout

  db = SimpleNamespace(
    get=AsyncMock(side_effect=locked_rollout),
    add=MagicMock(),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(
    return_value=_window_readiness(controlled_window_active=False)
  )
  service._latest_full_snapshot = AsyncMock(return_value={"is_complete": True})
  service._external_snapshot_activity = AsyncMock(
    return_value={"orders": [], "trades": []}
  )

  with pytest.raises(ValueError, match="自动退出策略版本已过期"):
    await service.begin_controlled_window(
      "account-1",
      user_id="user-1",
      snapshot_id="snapshot-1",
      expected_policy_version=3,
      operation_id="challenge-1",
    )

  assert rollout.controlled_window_active is False
  db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_live_rechecks_locked_rollout_after_readiness(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    controlled_window_active=False,
    controlled_window_snapshot_id=None,
  )

  async def get(model, *_args, **_kwargs):
    if model is operations_module.AccountTradingRolloutEvent:
      return None
    return rollout

  db = SimpleNamespace(
    get=AsyncMock(side_effect=get),
    add=MagicMock(),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  monkeypatch.setattr(operations_module.settings, "environment", "development")
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value=_window_readiness())

  with pytest.raises(ValueError, match="账户实盘窗口"):
    await service.activate_rollout(
      "account-1",
      user_id="user-1",
      acknowledged_policy_version=3,
      target_stage="LIVE",
      confirmation="LIVE:account-1",
      operation_id="window-required-1",
    )

  assert any(
    call.args == (operations_module.AccountTradingRollout, "account-1")
    and call.kwargs == {"with_for_update": True}
    for call in db.get.await_args_list
  )
  db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_live_only_promotes_shadow_and_canary_preserves_live(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    stage="CANARY",
    enabled=True,
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
  )

  async def get(model, *_args, **_kwargs):
    if model is operations_module.AccountTradingRolloutEvent:
      return None
    return rollout

  db = SimpleNamespace(
    get=AsyncMock(side_effect=get),
    add=MagicMock(),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  monkeypatch.setattr(operations_module.settings, "environment", "development")
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value=_window_readiness())

  with pytest.raises(ValueError, match="只允许从 SHADOW"):
    await service.activate_rollout(
      "account-1",
      user_id="user-1",
      acknowledged_policy_version=3,
      target_stage="LIVE",
      confirmation="LIVE:account-1",
      operation_id="shadow-only-live-1",
    )

  rollout.stage = "LIVE"
  await service.activate_rollout(
    "account-1",
    user_id="user-1",
    acknowledged_policy_version=3,
    target_stage="CANARY",
    operation_id="canary-preserves-live-1",
  )

  assert rollout.stage == "LIVE"
  assert rollout.enabled is True


@pytest.mark.asyncio
async def test_manual_pause_invalidates_controlled_window_and_audits(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    stage="LIVE",
    enabled=True,
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
    controlled_window_external_order_ids=["old-order"],
    controlled_window_external_trade_ids=["old-trade"],
  )
  db = SimpleNamespace(
    get=AsyncMock(return_value=rollout),
    add=MagicMock(),
    commit=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value={"status": "PREPARING"})

  result = await service.pause(
    "account-1",
    "manual pause",
    user_id="user-1",
  )

  assert result == {"status": "PREPARING"}
  assert rollout.stage == "PAUSED"
  assert rollout.enabled is False
  assert rollout.controlled_window_active is False
  assert rollout.controlled_window_snapshot_id is None
  assert rollout.controlled_window_external_order_ids == []
  assert rollout.controlled_window_external_trade_ids == []
  event = db.add.call_args.args[0]
  assert event.event_type == "ENTRIES_PAUSED"
  assert event.actor_user_id == "user-1"
  assert event.previous_stage == "LIVE"
  assert event.next_stage == "PAUSED"
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_invalidates_controlled_window_and_audits(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    stage="LIVE",
    enabled=True,
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
    controlled_window_external_order_ids=["old-order"],
    controlled_window_external_trade_ids=["old-trade"],
  )
  empty_result = MagicMock()
  empty_result.scalars.return_value.all.return_value = []
  db = SimpleNamespace(
    get=AsyncMock(return_value=rollout),
    execute=AsyncMock(return_value=empty_result),
    add=MagicMock(),
    commit=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  alert_service = SimpleNamespace(raise_alert=AsyncMock())
  monkeypatch.setattr(
    operations_module,
    "OperationalAlertService",
    lambda _db: alert_service,
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value={"status": "HARD_KILL"})

  result = await service.kill(
    "account-1",
    "manual kill",
    user_id="user-1",
  )

  assert result == {"status": "HARD_KILL"}
  assert rollout.stage == "KILL_SWITCHED"
  assert rollout.enabled is False
  assert rollout.kill_switch is True
  assert rollout.controlled_window_active is False
  assert rollout.controlled_window_snapshot_id is None
  assert rollout.controlled_window_external_order_ids == []
  assert rollout.controlled_window_external_trade_ids == []
  event = db.add.call_args_list[0].args[0]
  assert event.event_type == "KILL_SWITCHED"
  assert event.actor_user_id == "user-1"
  assert event.previous_stage == "LIVE"
  assert event.next_stage == "KILL_SWITCHED"
  alert_service.raise_alert.assert_awaited_once()
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_scans_and_cancels_manual_order_committed_before_it(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    stage="LIVE",
    enabled=True,
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
  )
  pending = SimpleNamespace(
    client_order_id="manual-client-1",
    broker_order_id=None,
    status="QUEUED",
    status_reason=None,
  )
  source = SimpleNamespace(
    client_order_id="manual-client-1",
    device_id="device-1",
    delivery_status="QUEUED",
    last_error=None,
    payload={"command_kind": "PLACE_ORDER"},
  )

  def result(rows):
    value = MagicMock()
    value.scalars.return_value.all.return_value = rows
    return value

  db = SimpleNamespace(
    get=AsyncMock(return_value=rollout),
    execute=AsyncMock(side_effect=[result([]), result([pending]), result([source])]),
    add=MagicMock(),
    commit=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  alert_service = SimpleNamespace(raise_alert=AsyncMock())
  monkeypatch.setattr(
    operations_module,
    "OperationalAlertService",
    lambda _db: alert_service,
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value={"status": "HARD_KILL"})

  await service.kill("account-1", "manual kill", user_id="user-1")

  assert source.delivery_status == "CANCELLED_KILL"
  assert source.last_error == "hard_kill_before_broker_confirmation"
  assert pending.status == "KILL_SWITCHED"
  assert pending.status_reason == "hard kill before broker order id"
  assert db.get.await_args.kwargs == {"with_for_update": True}
  db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_operation_id_replay_does_not_repeat_any_side_effect(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    stage="LIVE",
    enabled=True,
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
  )
  pending = SimpleNamespace(
    client_order_id="manual-client-idempotent",
    broker_order_id="broker-order-idempotent",
    execution_mode="live",
    status="SUBMITTED",
    status_reason=None,
  )
  source = SimpleNamespace(
    client_order_id="manual-client-idempotent",
    device_id="device-1",
    delivery_status="DELIVERED",
    last_error=None,
    payload={"command_kind": "PLACE_ORDER"},
  )

  def result(rows):
    value = MagicMock()
    value.scalars.return_value.all.return_value = rows
    return value

  added: list = []
  marker_lookups = 0

  async def get(model, key, **kwargs):
    nonlocal marker_lookups
    if model is operations_module.AccountTradingRollout:
      assert key == "account-1"
      assert kwargs == {"with_for_update": True}
      return rollout
    assert model is operations_module.AccountTradingRolloutEvent
    assert key == "kill-operation-1"
    marker_lookups += 1
    if marker_lookups == 1:
      return None
    return next(
      item
      for item in added
      if isinstance(item, operations_module.AccountTradingRolloutEvent)
    )

  db = SimpleNamespace(
    get=AsyncMock(side_effect=get),
    execute=AsyncMock(side_effect=[result([]), result([pending]), result([source])]),
    add=MagicMock(side_effect=added.append),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  alert_service = SimpleNamespace(raise_alert=AsyncMock())
  monkeypatch.setattr(
    operations_module,
    "OperationalAlertService",
    lambda _db: alert_service,
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value={"status": "HARD_KILL"})

  first = await service.kill(
    "account-1",
    "idempotent emergency",
    user_id="user-1",
    operation_id="kill-operation-1",
  )
  first_added_count = len(added)
  first_execute_count = db.execute.await_count
  first_alert_count = alert_service.raise_alert.await_count
  second = await service.kill(
    "account-1",
    "idempotent emergency",
    user_id="user-1",
    operation_id="kill-operation-1",
  )

  assert first == second == {"status": "HARD_KILL"}
  assert marker_lookups == 2
  assert len(added) == first_added_count == 3
  assert db.execute.await_count == first_execute_count == 3
  assert alert_service.raise_alert.await_count == first_alert_count == 1
  assert db.commit.await_count == 1
  assert db.rollback.await_count == 1
  events = [
    item
    for item in added
    if isinstance(item, operations_module.AccountTradingRolloutEvent)
  ]
  cancel_commands = [
    item
    for item in added
    if isinstance(item, operations_module.TradeCommandOutbox)
    and item.payload.get("command_kind") == "CANCEL_ORDER"
  ]
  emergency_commands = [
    item
    for item in added
    if isinstance(item, operations_module.TradeCommandOutbox)
    and item.payload.get("command_kind") == "EMERGENCY_STOP"
  ]
  assert len(events) == 1
  assert events[0].event_id == "kill-operation-1"
  assert len(cancel_commands) == 1
  assert len(emergency_commands) == 1
  assert (
    cancel_commands[0].idempotency_key
    == hashlib.sha256(
      b"hard-kill-cancel:account-1:device-1:broker-order-idempotent:kill-operation-1"
    ).hexdigest()
  )


@pytest.mark.asyncio
async def test_begin_controlled_window_operation_id_replay_is_a_noop(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout()
  added: list = []
  marker_lookups = 0

  async def get(model, key, **kwargs):
    nonlocal marker_lookups
    if model is operations_module.AccountTradingRollout:
      return rollout
    marker_lookups += 1
    if marker_lookups <= 2:
      return None
    return next(
      item
      for item in added
      if isinstance(item, operations_module.AccountTradingRolloutEvent)
    )

  db = SimpleNamespace(
    get=AsyncMock(side_effect=get),
    add=MagicMock(side_effect=added.append),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(
    return_value=_window_readiness(controlled_window_active=False)
  )
  service._latest_full_snapshot = AsyncMock(return_value={"is_complete": True})
  service._external_snapshot_activity = AsyncMock(
    return_value={"orders": [], "trades": []}
  )

  await service.begin_controlled_window(
    "account-1",
    user_id="user-1",
    snapshot_id="snapshot-1",
    operation_id="begin-operation-1",
  )
  await service.begin_controlled_window(
    "account-1",
    user_id="user-1",
    snapshot_id="snapshot-1",
    operation_id="begin-operation-1",
  )

  assert marker_lookups == 3
  assert len(added) == 1
  assert added[0].event_id == "begin-operation-1"
  assert added[0].details["operationId"] == "begin-operation-1"
  service._latest_full_snapshot.assert_awaited_once()
  service._external_snapshot_activity.assert_awaited_once()
  db.commit.assert_awaited_once()
  assert db.rollback.await_count == 2


@pytest.mark.asyncio
async def test_activate_rollout_operation_id_replay_is_a_noop(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  rollout = _rollout(
    controlled_window_active=True,
    controlled_window_snapshot_id="snapshot-1",
  )
  added: list = []
  marker_lookups = 0

  async def get(model, key, **kwargs):
    nonlocal marker_lookups
    if model is operations_module.AccountTradingRollout:
      return rollout
    marker_lookups += 1
    if marker_lookups <= 2:
      return None
    return next(
      item
      for item in added
      if isinstance(item, operations_module.AccountTradingRolloutEvent)
    )

  db = SimpleNamespace(
    get=AsyncMock(side_effect=get),
    add=MagicMock(side_effect=added.append),
    commit=AsyncMock(),
    rollback=AsyncMock(),
  )
  monkeypatch.setattr(
    operations_module,
    "AsyncSessionLocal",
    _session_context(db),
  )
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(return_value=_window_readiness())

  await service.activate_rollout(
    "account-1",
    user_id="user-1",
    acknowledged_policy_version=3,
    target_stage="CANARY",
    operation_id="activate-operation-1",
  )
  await service.activate_rollout(
    "account-1",
    user_id="user-1",
    acknowledged_policy_version=3,
    target_stage="CANARY",
    operation_id="activate-operation-1",
  )

  assert marker_lookups == 3
  assert len(added) == 1
  assert added[0].event_id == "activate-operation-1"
  assert added[0].details == {
    "operationId": "activate-operation-1",
    "policyVersion": 3,
    "confirmation": "",
    "v3EvidenceFingerprint": "locked-v3-evidence",
    "targetStage": "CANARY",
    "operatorReview": {
      "acknowledged": True,
      "confirmation": "AUTHENTICATED_IDEMPOTENT_ACTIVATION",
      "operationId": "activate-operation-1",
      "policyVersion": 3,
      "snapshotId": "snapshot-1",
      "evidenceFingerprint": "locked-review-evidence",
      "reviewPreviouslyConfirmed": False,
      "userConfirmation": "",
    },
  }
  evaluator = TTradeOperationsService.rollout_evidence_evaluator
  assert evaluator.evaluate.await_args_list == [
    call(
      db,
      account_id="account-1",
      rollout=rollout,
      bypass_cache=True,
    )
  ]
  db.commit.assert_awaited_once()
  assert db.rollback.await_count == 2


@pytest.mark.asyncio
async def test_begin_controlled_window_concurrent_same_operation_has_one_transition(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """The second caller must see the marker after waiting for the row lock."""

  rollout = _rollout()

  class SharedState:
    def __init__(self) -> None:
      self.lock = asyncio.Lock()
      self.event = None
      self.event_adds = 0

  state = SharedState()

  class Session:
    def __init__(self) -> None:
      self.locked = False
      self.pending_event = None

    async def __aenter__(self):
      return self

    async def __aexit__(self, exc_type, exc, traceback):
      if self.locked:
        state.lock.release()
        self.locked = False
      return False

    async def get(self, model, key, **kwargs):
      if model is operations_module.AccountTradingRollout:
        assert kwargs == {"with_for_update": True}
        await state.lock.acquire()
        self.locked = True
        return rollout
      assert model is operations_module.AccountTradingRolloutEvent
      assert key == "begin-race-1"
      return state.event

    def add(self, value):
      if isinstance(value, operations_module.AccountTradingRolloutEvent):
        self.pending_event = value
        state.event_adds += 1

    async def commit(self):
      if self.pending_event is not None:
        state.event = self.pending_event
      if self.locked:
        state.lock.release()
        self.locked = False

    async def rollback(self):
      if self.locked:
        state.lock.release()
        self.locked = False

  monkeypatch.setattr(operations_module, "AsyncSessionLocal", Session)
  service = TTradeOperationsService()
  service.ensure_rollout = AsyncMock(return_value=rollout)
  service.readiness = AsyncMock(
    return_value=_window_readiness(controlled_window_active=False)
  )
  service._latest_full_snapshot = AsyncMock(return_value={"is_complete": True})
  service._external_snapshot_activity = AsyncMock(
    return_value={"orders": [], "trades": []}
  )

  results = await asyncio.gather(
    service.begin_controlled_window(
      "account-1",
      user_id="user-1",
      snapshot_id="snapshot-1",
      operation_id="begin-race-1",
    ),
    service.begin_controlled_window(
      "account-1",
      user_id="user-1",
      snapshot_id="snapshot-1",
      operation_id="begin-race-1",
    ),
  )

  assert results[0] == results[1]
  assert state.event_adds == 1
  assert state.event is not None
  assert state.event.event_id == "begin-race-1"
  service._latest_full_snapshot.assert_awaited_once()
  service._external_snapshot_activity.assert_awaited_once()
