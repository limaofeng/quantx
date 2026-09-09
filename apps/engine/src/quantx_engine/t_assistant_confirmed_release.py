"""Engine release command consumes a durable device confirmation, never actor input."""

from datetime import UTC
from pathlib import Path
from uuid import UUID

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_domain.clock import SHANGHAI
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)

from .t_assistant_live_admission import dispatch_live_canary_preparation
from .t_assistant_release_approval import record_live_release_approval

RELEASE_ACTION = "T_ASSISTANT_LIVE_RELEASE"
RELEASE_COMMAND = "T_ASSISTANT_CONFIRM_LIVE_RELEASE"


def _utc(value):
  return (value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value).astimezone(
    UTC
  )


async def execute_confirmed_release(
  db, *, challenge_id, command_id, evidence_root, now
):
  now = aware_time(now).astimezone(UTC)
  if not db.in_transaction() or not command_id:
    raise ValueError("LIVE_RELEASE_DURABLE_COMMAND_REQUIRED")
  challenge = await db.get(
    TradeConfirmationChallenge,
    challenge_id,
    with_for_update=True,
    populate_existing=True,
  )
  command = await db.get(EngineCommandOutbox, command_id)
  if (
    challenge is None
    or command is None
    or challenge.action != RELEASE_ACTION
    or challenge.owner_type is not None
    or challenge.owner_id is not None
    or challenge.environment is not None
    or not challenge.user_id
    or not challenge.device_session_id
    or challenge.consumed_at is None
    or not _utc(challenge.created_at)
    <= _utc(challenge.consumed_at)
    < _utc(challenge.expires_at)
    or _utc(challenge.consumed_at) > now
    or (challenge.result_reference or {}).get("engine_command", {}).get("message_id")
    != command_id
    or command.command_type != RELEASE_COMMAND
    or command.payload != {"challenge_id": challenge_id}
  ):
    raise ValueError("LIVE_RELEASE_CONFIRMATION_REQUIRED")
  request = dict(challenge.payload or {})
  required = {
    "account_id",
    "source_execution_id",
    "config_version_id",
    "expected_config_hash",
    "expected_head_version",
    "evaluation_id",
    "expected_report_hash",
    "expected_policy_hash",
    "window_start",
    "window_end",
  }
  if (
    set(request) != required
    or request["account_id"] != challenge.account_id
    or command.aggregate_id != request["source_execution_id"]
  ):
    raise ValueError("LIVE_RELEASE_CONFIRMATION_SCOPE_CONFLICT")
  identity = request.pop("evaluation_id")
  if not isinstance(identity, str) or str(UUID(identity)) != identity:
    raise ValueError("LIVE_RELEASE_EVALUATION_ID_INVALID")
  root = Path(evidence_root).resolve(strict=True)
  directory = (root / identity).resolve(strict=True)
  if directory.parent != root or not directory.is_dir():
    raise ValueError("LIVE_RELEASE_EVALUATION_SCOPE_CONFLICT")
  async with db.begin_nested():
    approval = await record_live_release_approval(
      db,
      **request,
      actor_id=challenge.user_id,
      review_reference=f"device-confirmation:{challenge.id}",
      approval_event_key=f"live-release:{challenge.id}",
      evidence_directory=directory,
      now=now,
    )
    execution_id = await dispatch_live_canary_preparation(
      db,
      payload={
        **approval,
        "account_id": challenge.account_id,
        "source_execution_id": request["source_execution_id"],
        "config_version_id": request["config_version_id"],
        "expected_head_version": request["expected_head_version"],
      },
      now=now,
    )
  return {"success": True, "execution_id": execution_id, **approval}
