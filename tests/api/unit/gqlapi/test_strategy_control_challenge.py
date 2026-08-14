from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.resolvers.strategies import StrategyResolver
from quantx_api.gqlapi.schemas.strategy_schema import StrategyMutation
from quantx_api.gqlapi.strategy_control import (
  StrategyControlChallengeService,
  StrategyControlPreviewData,
  _readiness_binding,
  _validate_action_state,
  _validate_readiness,
  normalize_strategy_control_request,
)
from quantx_api.gqlapi.trade_approval import TradeApprovalChallengeError
from quantx_api.gqlapi.types.strategy_types import (
  StrategyControlAction,
  StrategyControlPreviewInput,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.enums import StrategyRunMode, StrategyRunStatus


def _principal() -> Principal:
  return Principal(
    user_id="user-1",
    username="operator",
    display_name="Operator",
    device_session_id="session-1",
    access_token_expires_at=utcnow() + timedelta(minutes=5),
    permissions=frozenset(
      {"strategy:read", "strategy:control", "trade:approve"}
    ),
    authorized_account_ids=("ACCOUNT-1",),
    active_account_id="ACCOUNT-1",
  )


def _info() -> SimpleNamespace:
  return SimpleNamespace(context={"principal": _principal()})


def _run(*, mode=StrategyRunMode.LIVE, status=StrategyRunStatus.PAUSED):
  return SimpleNamespace(
    id="run-1",
    strategy_id=7,
    name="Dynamic Balance",
    mode=mode,
    status=status,
    instruments=["600000.SH"],
    parameters={
      "account_id": "ACCOUNT-1",
      "_mobile_config_version": "3",
      "cash_buffer_pct": 0.1,
    },
  )


def _readiness(*, server_enabled: bool = True):
  checks = [
    {
      "code": "SERVER_REAL_TRADING_ENABLED",
      "passed": server_enabled,
      "message": "server disabled" if not server_enabled else "",
    },
    {
      "code": "T_TRADE_LIVE_ENABLED",
      "passed": False,
      "message": "unrelated t-trade gate",
    },
    {"code": "ENGINE_READY", "passed": True, "message": ""},
    {"code": "LIVE_AGENT_READY", "passed": True, "message": ""},
  ]
  return {
    "status": "READY",
    "stage": "CANARY",
    "engine_status": "READY",
    "agent_status": "READY",
    "agent_device_id": "agent-1",
    "ready_live_agent_count": 1,
    "agent_mode": "live",
    "protocol_version": "1.1",
    "reconcile_status": "READY",
    "kill_switch": False,
    "policy_version": 2,
    "snapshot_id": "snapshot-1",
    "snapshot_hash": "hash-1",
    "snapshot_at": time_utils.now(),
    "controlled_window_active": True,
    "controlled_window_snapshot_id": "snapshot-1",
    "new_external_order_count": 0,
    "new_external_trade_count": 0,
    "working_external_order_count": 0,
    "checks": checks,
  }


def test_strategy_control_input_is_strongly_typed_and_versioned():
  request = normalize_strategy_control_request(
    account_id=" ACCOUNT-1 ",
    instance_id="run-1",
    action=StrategyControlAction.RESUME_LIVE,
    expected_config_version="03",
    idempotency_key="ios-control-1",
  )
  assert request.account_id == "ACCOUNT-1"
  assert request.expected_config_version == "3"

  with pytest.raises(TradeApprovalChallengeError) as caught:
    normalize_strategy_control_request(
      account_id="ACCOUNT-1",
      instance_id="run-1",
      action="DELETE",
      expected_config_version="3",
      idempotency_key="ios-control-2",
    )
  assert caught.value.code == "INVALID_STRATEGY_CONTROL_ACTION"


def test_live_resume_and_paper_clone_have_fail_closed_state_machines():
  resume = normalize_strategy_control_request(
    account_id="ACCOUNT-1",
    instance_id="run-1",
    action=StrategyControlAction.RESUME_LIVE,
    expected_config_version="3",
    idempotency_key="resume-1",
  )
  _validate_action_state(_run(), resume)

  with pytest.raises(TradeApprovalChallengeError) as caught:
    _validate_action_state(
      _run(mode=StrategyRunMode.PAPER, status=StrategyRunStatus.PAUSED),
      resume,
    )
  assert caught.value.code == "STRATEGY_STATE_CONFLICT"

  promote = normalize_strategy_control_request(
    account_id="ACCOUNT-1",
    instance_id="run-1",
    action=StrategyControlAction.CLONE_TO_LIVE,
    expected_config_version="3",
    idempotency_key="promote-1",
  )
  _validate_action_state(
    _run(mode=StrategyRunMode.PAPER, status=StrategyRunStatus.STOPPED),
    promote,
  )


def test_strategy_readiness_ignores_only_t_trade_product_gate():
  readiness = _readiness()
  _validate_readiness(readiness)
  assert "T_TRADE_LIVE_ENABLED" not in {
    item["code"] for item in _readiness_binding(readiness)["checks"]
  }

  with pytest.raises(TradeApprovalChallengeError) as caught:
    _validate_readiness(_readiness(server_enabled=False))
  assert caught.value.code == "STRATEGY_LIVE_NOT_READY"


@pytest.mark.asyncio
async def test_native_direct_live_resume_is_blocked_before_engine_command():
  with (
    patch(
      "quantx_api.gqlapi.schemas.strategy_schema._authorize_native_strategy_run",
      new=AsyncMock(),
    ),
    patch.object(
      StrategyControlChallengeService,
      "instance_requires_confirmation",
      new=AsyncMock(return_value=True),
    ),
    patch.object(
      StrategyResolver,
      "resume_strategy_instance",
      new=AsyncMock(),
    ) as resume,
  ):
    result = await StrategyMutation().resume_strategy_instance(_info(), "run-1")

  assert result.success is False
  assert "previewStrategyControl" in result.message
  resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_strategy_control_returns_only_server_readiness_projection():
  request_input = StrategyControlPreviewInput(
    account_id="ACCOUNT-1",
    instance_id="run-1",
    action=StrategyControlAction.RESUME_LIVE,
    expected_config_version="3",
    idempotency_key="ios-preview-1",
  )
  request = normalize_strategy_control_request(
    account_id="ACCOUNT-1",
    instance_id="run-1",
    action=StrategyControlAction.RESUME_LIVE,
    expected_config_version="3",
    idempotency_key="ios-preview-1",
  )
  issued = StrategyControlPreviewData(
    challenge_id="challenge-1",
    confirmation_token="secret-token",
    request=request,
    target_instance_id="run-1",
    current_mode="live",
    current_status="paused",
    config_version="3",
    readiness=_readiness(),
    challenge_expires_at=time_utils.now_aware(),
  )
  with patch.object(
    StrategyControlChallengeService,
    "issue",
    new=AsyncMock(return_value=issued),
  ):
    result = await StrategyMutation().preview_strategy_control(
      _info(), request_input
    )

  assert result.success is True
  assert result.preview is not None
  assert result.preview.target_instance_id == "run-1"
  assert result.preview.snapshot_id == "snapshot-1"
  assert result.preview.confirmation_token == "secret-token"
