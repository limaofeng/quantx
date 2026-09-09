"""Native maintenance confirmation over an already frozen legacy inventory."""

import re
import secrets
from datetime import UTC, timedelta
from uuid import uuid4

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.agent_runtime import (
  EngineCommandOutbox,
  TTradeRolloutEvent,
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

ACTION = "T_ASSISTANT_LEGACY_DRAIN"
COMMAND = "T_ASSISTANT_CONFIRM_LEGACY_DRAIN"


def normalize_drain_request(request):
  required = {
    "account_id",
    "config_id",
    "run_id",
    "expected_head_version",
    "inventory_operation_id",
    "expected_inventory_hash",
    "window_start",
    "window_end",
  }
  if not isinstance(request, dict) or set(request) != required:
    raise ValueError("LEGACY_T_DRAIN_REQUEST_INVALID")
  result = dict(request)
  if (
    any(
      not isinstance(result[key], str) or not result[key].strip()
      for key in required - {"expected_head_version", "window_start", "window_end"}
    )
    or type(result["expected_head_version"]) is not int
    or result["expected_head_version"] < 1
    or re.fullmatch(r"[0-9a-f]{64}", result["expected_inventory_hash"]) is None
  ):
    raise ValueError("LEGACY_T_DRAIN_REQUEST_INVALID")
  start, end = (
    aware_time(result[key]).astimezone(UTC) for key in ("window_start", "window_end")
  )
  if start >= end:
    raise ValueError("LEGACY_T_DRAIN_WINDOW_INVALID")
  result.update(window_start=start.isoformat(), window_end=end.isoformat())
  return result


async def _lock_inventory(db, request, actor_id):
  head = await db.get(
    TTradeGlobalConfig,
    request["config_id"],
    with_for_update=True,
    populate_existing=True,
  )
  inventory = await db.get(TTradeRolloutEvent, request["inventory_operation_id"])
  if (
    head is None
    or head.account_id != request["account_id"]
    or head.mode != "live"
    or head.strategy_run_id != request["run_id"]
    or head.state_version != request["expected_head_version"]
    or inventory is None
    or inventory.event_type != "LEGACY_T_OBLIGATION_INVENTORY_FROZEN"
    or inventory.account_id != request["account_id"]
    or inventory.actor_user_id != actor_id
    or inventory.details.get("manifest_hash") != request["expected_inventory_hash"]
  ):
    raise ValueError("LEGACY_T_DRAIN_INVENTORY_CHANGED")
  manifest = dict(inventory.details.get("manifest") or {})
  if (
    any(
      manifest.get(key) != request[key] for key in ("account_id", "config_id", "run_id")
    )
    or manifest.get("head_version") != request["expected_head_version"]
  ):
    raise ValueError("LEGACY_T_DRAIN_INVENTORY_SCOPE_CONFLICT")


async def issue_drain_confirmation(db, *, principal, request, now):
  if not db.in_transaction():
    raise ValueError("LEGACY_T_DRAIN_TRANSACTION_REQUIRED")
  request = normalize_drain_request(request)
  now = aware_time(now).astimezone(UTC)
  _require_native_control_principal(principal, request["account_id"])
  current = await TTradeControlChallengeService._lock_current_principal(
    db, principal, request["account_id"]
  )
  await _lock_inventory(db, request, current.user_id)
  expires = min(now + timedelta(seconds=60), aware_time(request["window_end"]))
  if expires <= now:
    raise ValueError("LEGACY_T_DRAIN_WINDOW_EXPIRED")
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


async def consume_drain_confirmation(
  db, *, principal, challenge_id, confirmation_token, now
):
  if not db.in_transaction():
    raise ValueError("LEGACY_T_DRAIN_TRANSACTION_REQUIRED")
  now = aware_time(now).astimezone(UTC)
  challenge = await db.get(TradeConfirmationChallenge, challenge_id)
  if challenge is None:
    raise ValueError("LEGACY_T_DRAIN_CHALLENGE_REQUIRED")
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
  request = normalize_drain_request(challenge.payload)
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
    raise ValueError("LEGACY_T_DRAIN_CONFIRMATION_SCOPE_CONFLICT")
  if challenge.consumed_at is not None:
    identity = (
      (challenge.result_reference or {}).get("engine_command", {}).get("message_id")
    )
    command = await db.get(EngineCommandOutbox, identity) if identity else None
    if (
      command is None
      or command.command_type != COMMAND
      or command.aggregate_id != request["run_id"]
      or command.payload != {"challenge_id": challenge_id}
    ):
      raise ValueError("LEGACY_T_DRAIN_COMMAND_REFERENCE_CONFLICT")
    return identity
  await _lock_inventory(db, request, current.user_id)
  if not aware_time(request["window_start"]) <= now < aware_time(request["window_end"]):
    raise ValueError("LEGACY_T_DRAIN_OUTSIDE_MAINTENANCE_WINDOW")
  identity = str(uuid4())
  db.add(
    EngineCommandOutbox(
      message_id=identity,
      idempotency_key=f"legacy-drain:{challenge_id}",
      command_type=COMMAND,
      aggregate_id=request["run_id"],
      payload={"challenge_id": challenge_id},
      available_at=now.replace(tzinfo=None),
      processing_status="PENDING",
    )
  )
  challenge.consumed_at = time_utils.to_shanghai(now)
  challenge.result_reference = {"engine_command": {"message_id": identity}}
  await db.flush()
  return identity


async def enqueue_legacy_inventory(db, *, principal, request_id, request, now):
  """Prepare review material only; user confirmation is a separate operation."""
  from uuid import UUID

  required = {"account_id", "config_id", "run_id", "expected_head_version"}
  if (
    not db.in_transaction()
    or not isinstance(request, dict)
    or set(request) != required
    or type(request["expected_head_version"]) is not int
    or request["expected_head_version"] < 1
    or any(
      not isinstance(request[key], str) or not request[key].strip()
      for key in required - {"expected_head_version"}
    )
    or not isinstance(request_id, str)
    or str(UUID(request_id)) != request_id
  ):
    raise ValueError("LEGACY_T_INVENTORY_REQUEST_INVALID")
  now = aware_time(now).astimezone(UTC)
  _require_native_control_principal(principal, request["account_id"])
  current = await TTradeControlChallengeService._lock_current_principal(
    db, principal, request["account_id"]
  )
  payload = {**request, "actor_id": current.user_id}
  head = await db.get(
    TTradeGlobalConfig,
    request["config_id"],
    with_for_update=True,
    populate_existing=True,
  )
  if head is None or head.account_id != request["account_id"]:
    raise ValueError("LEGACY_T_INVENTORY_HEAD_CONFLICT")
  command_type = "T_ASSISTANT_PREPARE_LEGACY_INVENTORY"
  existing = await db.get(EngineCommandOutbox, request_id)
  if existing is not None:
    if (
      existing.command_type != command_type
      or existing.aggregate_id != request["run_id"]
      or existing.payload != payload
    ):
      raise ValueError("LEGACY_T_INVENTORY_REQUEST_ID_CONFLICT")
    return request_id
  if (
    head.mode != "live"
    or head.strategy_run_id != request["run_id"]
    or head.state_version != request["expected_head_version"]
  ):
    raise ValueError("LEGACY_T_INVENTORY_HEAD_CONFLICT")
  db.add(
    EngineCommandOutbox(
      message_id=request_id,
      idempotency_key=f"legacy-inventory:{request_id}",
      command_type=command_type,
      aggregate_id=request["run_id"],
      payload=payload,
      available_at=now.replace(tzinfo=None),
      processing_status="PENDING",
    )
  )
  await db.flush()
  return request_id


async def read_legacy_maintenance_operation(db, *, principal, account_id, command_id):
  """Only durable, scope-checked evidence is returned as a completed operation."""
  from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
  from quantx_infrastructure.services.t_legacy_drain_guard import (
    legacy_t_drain_event_id,
  )

  _require_native_control_principal(principal, account_id)
  current = await TTradeControlChallengeService._lock_current_principal(
    db, principal, account_id
  )
  command = await db.get(EngineCommandOutbox, command_id)
  if command is None:
    return {"command_id": command_id, "status": "NOT_FOUND", "evidence": None}
  payload = dict(command.payload or {})
  if command.command_type == "T_ASSISTANT_PREPARE_LEGACY_INVENTORY":
    if (
      payload.get("actor_id") != current.user_id
      or payload.get("account_id") != account_id
    ):
      raise ValueError("LEGACY_T_MAINTENANCE_SCOPE_CONFLICT")
    event = await db.get(TTradeRolloutEvent, f"legacy-inventory:{command_id}")
    evidence = None
    if event is not None:
      manifest = dict(event.details.get("manifest") or {})
      if (
        event.event_type != "LEGACY_T_OBLIGATION_INVENTORY_FROZEN"
        or event.account_id != account_id
        or event.actor_user_id != current.user_id
        or manifest.get("run_id") != command.aggregate_id
        or any(
          manifest.get(key) != payload.get(key)
          for key in ("account_id", "config_id", "run_id")
        )
        or manifest.get("head_version") != payload.get("expected_head_version")
        or event.details.get("manifest_hash") != stable_manifest_hash(manifest)
      ):
        raise ValueError("LEGACY_T_MAINTENANCE_EVIDENCE_CONFLICT")
      evidence = {"inventory_operation_id": event.event_id, **event.details}
  elif command.command_type == COMMAND:
    challenge = await db.get(TradeConfirmationChallenge, payload.get("challenge_id"))
    if (
      challenge is None
      or challenge.action != ACTION
      or challenge.account_id != account_id
      or challenge.user_id != current.user_id
      or challenge.device_session_id != current.device_session_id
      or challenge.consumed_at is None
      or (challenge.result_reference or {}).get("engine_command", {}).get("message_id")
      != command_id
      or command.aggregate_id != challenge.payload.get("run_id")
      or set(payload) != {"challenge_id"}
      or not secrets.compare_digest(
        str(challenge.payload_fingerprint or ""),
        signed_payload_fingerprint(challenge.payload),
      )
    ):
      raise ValueError("LEGACY_T_MAINTENANCE_SCOPE_CONFLICT")
    event = await db.get(
      TTradeRolloutEvent, legacy_t_drain_event_id(command.aggregate_id)
    )
    evidence = None
    if event is not None:
      request = challenge.payload
      expected = {
        "config_id": request["config_id"],
        "run_id": request["run_id"],
        "expected_head_version": request["expected_head_version"],
        "inventory_operation_id": request["inventory_operation_id"],
        "inventory_hash": request["expected_inventory_hash"],
      }
      if (
        event.event_type != "LEGACY_T_DRAIN_STARTED"
        or event.next_stage != "DRAINING"
        or event.account_id != account_id
        or event.actor_user_id != current.user_id
        or event.details.get("run_id") != command.aggregate_id
        or event.details.get("request") != expected
      ):
        raise ValueError("LEGACY_T_MAINTENANCE_EVIDENCE_CONFLICT")
      evidence = dict(event.details)
  else:
    raise ValueError("LEGACY_T_MAINTENANCE_COMMAND_INVALID")
  status = command.processing_status
  if status == "SUCCEEDED" and evidence is None:
    raise ValueError("LEGACY_T_MAINTENANCE_EVIDENCE_REQUIRED")
  return {
    "command_id": command_id,
    "status": status,
    "evidence": evidence if status == "SUCCEEDED" else None,
  }
