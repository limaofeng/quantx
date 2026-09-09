"""Consume a device-confirmed maintenance window before fencing a legacy owner."""

import hmac
from datetime import UTC

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_domain.clock import SHANGHAI
from quantx_infrastructure.models.agent_runtime import (
  EngineCommandOutbox,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  trade_confirmation_payload_fingerprint,
)
from quantx_infrastructure.services.t_legacy_drain_guard import legacy_t_drain_event_id

from .t_assistant_legacy_drain import begin_legacy_t_drain

ACTION = "T_ASSISTANT_LEGACY_DRAIN"
COMMAND = "T_ASSISTANT_CONFIRM_LEGACY_DRAIN"


def _challenge_utc(value):
  return (value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value).astimezone(
    UTC
  )


async def execute_confirmed_legacy_drain(
  db, *, challenge_id, command_id, account_id, now
):
  now = aware_time(now).astimezone(UTC)
  if not db.in_transaction() or not command_id:
    raise ValueError("LEGACY_T_DRAIN_DURABLE_COMMAND_REQUIRED")
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
    or challenge.action != ACTION
    or any((challenge.owner_type, challenge.owner_id, challenge.environment))
    or not challenge.user_id
    or not challenge.device_session_id
    or challenge.consumed_at is None
    or not _challenge_utc(challenge.created_at)
    <= _challenge_utc(challenge.consumed_at)
    < _challenge_utc(challenge.expires_at)
    or _challenge_utc(challenge.consumed_at) > now
    or (challenge.result_reference or {}).get("engine_command", {}).get("message_id")
    != command_id
    or command.command_type != COMMAND
    or command.payload != {"challenge_id": challenge_id}
  ):
    raise ValueError("LEGACY_T_DRAIN_CONFIRMATION_REQUIRED")
  request = dict(challenge.payload or {})
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
  if (
    set(request) != required
    or request["account_id"] != challenge.account_id
    or request["account_id"] != account_id
    or command.aggregate_id != request["run_id"]
    or not hmac.compare_digest(
      str(challenge.payload_fingerprint or ""),
      trade_confirmation_payload_fingerprint(request),
    )
  ):
    raise ValueError("LEGACY_T_DRAIN_CONFIRMATION_SCOPE_CONFLICT")
  start, end = (
    aware_time(request[key]).astimezone(UTC) for key in ("window_start", "window_end")
  )
  if start >= end or not start <= _challenge_utc(challenge.consumed_at) < end:
    raise ValueError("LEGACY_T_DRAIN_WINDOW_INVALID")
  head = await db.get(
    TTradeGlobalConfig,
    request["config_id"],
    with_for_update=True,
    populate_existing=True,
  )
  if head is None or head.account_id != account_id:
    raise ValueError("LEGACY_T_DRAIN_HEAD_CONFLICT")
  marker = await db.get(TTradeRolloutEvent, legacy_t_drain_event_id(request["run_id"]))
  if marker is None and not start <= now < end:
    raise ValueError("LEGACY_T_DRAIN_OUTSIDE_MAINTENANCE_WINDOW")
  result = await begin_legacy_t_drain(
    db,
    **{
      key: value
      for key, value in request.items()
      if key not in {"account_id", "window_start", "window_end"}
    },
    actor_id=challenge.user_id,
    now=now,
  )
  return {"success": True, "account_id": account_id, **result}
