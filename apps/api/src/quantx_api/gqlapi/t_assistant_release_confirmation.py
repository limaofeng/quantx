"""Native device confirmation and atomic outbox handoff for initial T release."""

import re
import secrets
from datetime import UTC, timedelta
from uuid import UUID, uuid4

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)

from .t_trade_control import (
  TTradeControlChallengeService,
  _require_native_control_principal,
)
from .trade_approval import (
  challenge_token_digest,
  signed_payload_fingerprint,
  validate_persistent_trade_challenge,
)

ACTION = "T_ASSISTANT_LIVE_RELEASE"
COMMAND = "T_ASSISTANT_CONFIRM_LIVE_RELEASE"


def normalize_release_request(request):
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
  if not isinstance(request, dict) or set(request) != required:
    raise ValueError("LIVE_RELEASE_REQUEST_INVALID")
  result = dict(request)
  if any(
    not isinstance(result[key], str) or not result[key].strip()
    for key in required - {"expected_head_version", "window_start", "window_end"}
  ):
    raise ValueError("LIVE_RELEASE_REQUEST_INVALID")
  if (
    type(result["expected_head_version"]) is not int
    or result["expected_head_version"] < 1
  ):
    raise ValueError("LIVE_RELEASE_VERSION_INVALID")
  if any(
    re.fullmatch(r"[0-9a-f]{64}", result[key]) is None
    for key in ("expected_config_hash", "expected_report_hash", "expected_policy_hash")
  ):
    raise ValueError("LIVE_RELEASE_HASH_INVALID")
  if str(UUID(result["evaluation_id"])) != result["evaluation_id"]:
    raise ValueError("LIVE_RELEASE_EVALUATION_ID_INVALID")
  start, end = (
    aware_time(result[key]).astimezone(UTC) for key in ("window_start", "window_end")
  )
  if start >= end:
    raise ValueError("LIVE_RELEASE_WINDOW_INVALID")
  result.update(window_start=start.isoformat(), window_end=end.isoformat())
  return result


async def _lock_source(db, request):
  target = await db.get(TAssistantConfigVersionRecord, request["config_version_id"])
  if target is None or target.config_snapshot_hash != request["expected_config_hash"]:
    raise ValueError("LIVE_RELEASE_TARGET_CHANGED")
  head = await db.get(
    TTradeGlobalConfig, target.config_id, with_for_update=True, populate_existing=True
  )
  source = await db.get(
    TAssistantExecutionRecord,
    request["source_execution_id"],
    with_for_update=True,
    populate_existing=True,
  )
  if (
    head is None
    or source is None
    or not head.enabled
    or head.strategy_run_id
    or head.account_id != request["account_id"]
    or source.account_id != request["account_id"]
    or source.config_id != target.config_id
    or source.environment != "PAPER"
    or head.desired_environment != "PAPER"
    or head.state_version != request["expected_head_version"]
    or head.active_config_version_id != source.config_version_id
    or target.version <= source.frozen_config_version
    or target.entry_authorization != "MANUAL_CONFIRM"
    or target.rollout_stage != "CANARY"
    or target.scorer_mode != "RULE_ONLY"
  ):
    raise ValueError("LIVE_RELEASE_SOURCE_CHANGED")


async def issue_release_confirmation(db, *, principal, request, now):
  if not db.in_transaction():
    raise ValueError("LIVE_RELEASE_TRANSACTION_REQUIRED")
  request = normalize_release_request(request)
  now = aware_time(now).astimezone(UTC)
  _require_native_control_principal(principal, request["account_id"])
  current = await TTradeControlChallengeService._lock_current_principal(
    db, principal, request["account_id"]
  )
  await _lock_source(db, request)
  expires = min(now + timedelta(seconds=60), aware_time(request["window_end"]))
  if expires <= now:
    raise ValueError("LIVE_RELEASE_WINDOW_EXPIRED")
  token, identity = secrets.token_urlsafe(48), str(uuid4())
  db.add(
    TradeConfirmationChallenge(
      id=identity,
      action=ACTION,
      user_id=current.user_id,
      device_session_id=current.device_session_id,
      account_id=request["account_id"],
      idempotency_key=identity,
      payload=request,
      payload_fingerprint=signed_payload_fingerprint(request),
      token_digest=challenge_token_digest(token),
      created_at=time_utils.to_shanghai(now),
      expires_at=time_utils.to_shanghai(expires),
    )
  )
  await db.flush()
  return {
    "challenge_id": identity,
    "confirmation_token": token,
    "expires_at": expires,
    "request": request,
  }


async def consume_release_confirmation(
  db, *, principal, challenge_id, confirmation_token, now
):
  if not db.in_transaction():
    raise ValueError("LIVE_RELEASE_TRANSACTION_REQUIRED")
  now = aware_time(now).astimezone(UTC)
  challenge = await db.get(TradeConfirmationChallenge, challenge_id)
  if challenge is None:
    raise ValueError("LIVE_RELEASE_CHALLENGE_REQUIRED")
  _require_native_control_principal(principal, challenge.account_id)
  current = await TTradeControlChallengeService._lock_current_principal(
    db, principal, challenge.account_id
  )
  challenge = await db.get(
    TradeConfirmationChallenge,
    challenge_id,
    with_for_update=True,
    populate_existing=True,
  )
  if challenge is None:
    raise ValueError("LIVE_RELEASE_CHALLENGE_REQUIRED")
  request = normalize_release_request(challenge.payload)
  validate_persistent_trade_challenge(
    challenge=challenge,
    principal=current,
    action=ACTION,
    confirmation_token=confirmation_token,
    now=time_utils.to_shanghai(now),
    payload=request,
    allow_consumed=True,
  )
  if request["account_id"] != challenge.account_id:
    raise ValueError("LIVE_RELEASE_CONFIRMATION_SCOPE_CONFLICT")
  if challenge.consumed_at is not None:
    identity = (
      (challenge.result_reference or {}).get("engine_command", {}).get("message_id")
    )
    command = await db.get(EngineCommandOutbox, identity) if identity else None
    if (
      command is None
      or command.command_type != COMMAND
      or command.aggregate_id != request["source_execution_id"]
      or command.payload != {"challenge_id": challenge_id}
    ):
      raise ValueError("LIVE_RELEASE_COMMAND_REFERENCE_CONFLICT")
    return identity
  await _lock_source(db, request)
  if not aware_time(request["window_start"]) <= now < aware_time(request["window_end"]):
    raise ValueError("LIVE_RELEASE_OUTSIDE_MAINTENANCE_WINDOW")
  identity = str(uuid4())
  db.add(
    EngineCommandOutbox(
      message_id=identity,
      idempotency_key=f"live-release:{challenge_id}",
      command_type=COMMAND,
      aggregate_id=request["source_execution_id"],
      payload={"challenge_id": challenge_id},
      available_at=now.replace(tzinfo=None),
      processing_status="PENDING",
    )
  )
  challenge.consumed_at = time_utils.to_shanghai(now)
  challenge.result_reference = {"engine_command": {"message_id": identity}}
  await db.flush()
  return identity


async def read_release_status(db, *, principal, challenge_id, now):
  """Read only this device's operation; success requires durable release evidence."""
  from hmac import compare_digest

  from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantExecutionEventRecord,
  )
  from sqlalchemy import select

  challenge = await db.get(TradeConfirmationChallenge, challenge_id)
  if challenge is None:
    raise ValueError("LIVE_RELEASE_CHALLENGE_REQUIRED")
  _require_native_control_principal(principal, challenge.account_id)
  current = await TTradeControlChallengeService._lock_current_principal(
    db, principal, challenge.account_id
  )
  if (
    challenge.action != ACTION
    or challenge.user_id != current.user_id
    or challenge.device_session_id != current.device_session_id
  ):
    raise ValueError("LIVE_RELEASE_CONFIRMATION_SCOPE_CONFLICT")
  request = normalize_release_request(challenge.payload)
  if request["account_id"] != challenge.account_id or not compare_digest(
    challenge.payload_fingerprint, signed_payload_fingerprint(request)
  ):
    raise ValueError("LIVE_RELEASE_CONFIRMATION_SCOPE_CONFLICT")
  status = {
    "challenge_id": challenge_id,
    "status": "AWAITING_CONFIRMATION",
    "engine_command_id": None,
    "execution_id": None,
    "execution_status": None,
    "reason_code": None,
  }
  if challenge.consumed_at is None:
    if time_utils.to_shanghai(now) >= time_utils.to_shanghai(challenge.expires_at):
      status["status"] = "EXPIRED"
    return status
  identity = (
    (challenge.result_reference or {}).get("engine_command", {}).get("message_id")
  )
  command = await db.get(EngineCommandOutbox, identity) if identity else None
  if (
    command is None
    or command.command_type != COMMAND
    or command.aggregate_id != request["source_execution_id"]
    or command.payload != {"challenge_id": challenge_id}
  ):
    return {
      **status,
      "status": "UNKNOWN",
      "reason_code": "LIVE_RELEASE_COMMAND_REFERENCE_CONFLICT",
    }
  status["engine_command_id"] = identity
  if command.processing_status in {"PENDING", "PROCESSING"}:
    return {**status, "status": command.processing_status}
  if command.processing_status == "FAILED":
    error = command.processing_error or ""
    return {
      **status,
      "status": "FAILED",
      "reason_code": error
      if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", error)
      else "LIVE_RELEASE_EXECUTION_FAILED",
    }
  result = command.result if isinstance(command.result, dict) else {}
  execution = (
    await db.get(TAssistantExecutionRecord, result.get("execution_id"))
    if isinstance(result.get("execution_id"), str) and result["execution_id"]
    else None
  )
  approval_key = f"live-release:{challenge_id}"
  approval = await db.scalar(
    select(TAssistantExecutionEventRecord).where(
      TAssistantExecutionEventRecord.execution_id == request["source_execution_id"],
      TAssistantExecutionEventRecord.event_key == approval_key,
    )
  )
  if (
    command.processing_status != "SUCCEEDED"
    or result.get("success") is not True
    or execution is None
    or approval is None
    or execution.environment != "LIVE"
    or execution.account_id != challenge.account_id
    or execution.config_version_id != request["config_version_id"]
    or execution.config_snapshot_hash != request["expected_config_hash"]
    or execution.entry_authorization != "MANUAL_CONFIRM"
    or execution.rollout_stage != "CANARY"
    or execution.scorer_mode != "RULE_ONLY"
    or result.get("approval_event_key") != approval_key
    or approval.event_type != "LIVE_CANARY_RELEASE_APPROVED"
    or approval.payload.get("actor_id") != current.user_id
    or approval.payload.get("p5_evidence_hash") != request["expected_report_hash"]
    or stable_manifest_hash(approval.payload) != result.get("approval_hash")
  ):
    return {
      **status,
      "status": "UNKNOWN",
      "reason_code": "LIVE_RELEASE_RESULT_EVIDENCE_CONFLICT",
    }
  prepared = await db.scalar(
    select(TAssistantExecutionEventRecord).where(
      TAssistantExecutionEventRecord.execution_id == execution.execution_id,
      TAssistantExecutionEventRecord.event_key
      == f"live-canary-prepared:{execution.execution_id}",
    )
  )
  if (
    prepared is None
    or prepared.event_type != "LIVE_CANARY_EXECUTION_PREPARED"
    or prepared.payload.get("source_execution_id") != request["source_execution_id"]
    or prepared.payload.get("approval_event_key") != approval_key
    or prepared.payload.get("approval_hash") != result.get("approval_hash")
  ):
    return {
      **status,
      "status": "UNKNOWN",
      "reason_code": "LIVE_RELEASE_RESULT_EVIDENCE_CONFLICT",
    }
  return {
    **status,
    "status": "SUCCEEDED",
    "execution_id": execution.execution_id,
    "execution_status": execution.status,
  }
