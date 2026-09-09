"""Explicit initial CANARY preparation; no readiness activation or broker action."""

import re
import uuid
from dataclasses import fields
from datetime import UTC
from decimal import Decimal, InvalidOperation

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantExecutionEvent,
  stable_manifest_hash,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified


def _hash(value):
  return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _amount(value):
  if isinstance(value, bool):
    raise ValueError("LIVE_CANARY_LIMIT_INVALID")
  try:
    result = Decimal(str(value))
  except (InvalidOperation, ValueError) as exc:
    raise ValueError("LIVE_CANARY_LIMIT_INVALID") from exc
  if not result.is_finite() or result <= 0:
    raise ValueError("LIVE_CANARY_LIMIT_INVALID")
  return result


def canary_instrument_codes(payload):
  universe = payload.get("universe_policy")
  codes = universe.get("allowed_stock_codes") if isinstance(universe, dict) else None
  if (
    not isinstance(codes, list)
    or not codes
    or any(
      not isinstance(code, str) or re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", code) is None
      for code in codes
    )
    or len(codes) != len(set(codes))
    or not isinstance(universe.get("ignored_stock_codes"), list)
    or any(not isinstance(code, str) for code in universe["ignored_stock_codes"])
    or set(codes) & set(universe["ignored_stock_codes"])
  ):
    raise ValueError("LIVE_CANARY_EXPLICIT_UNIVERSE_REQUIRED")
  return tuple(codes)


async def prepare_live_canary_execution(
  db,
  *,
  source_execution_id,
  config_version_id,
  approval_event_key,
  expected_head_version,
  now,
):
  """Consumes a durable release approval written by the release control plane.

  That control plane must verify P5 and record LIVE_CANARY_RELEASE_APPROVED on
  the original PAPER source. This internal service is not a public command and
  does not fabricate approval records, stop legacy orders, or enable live gates.
  """
  now = aware_time(now).astimezone(UTC)
  if (
    not db.in_transaction()
    or type(expected_head_version) is not int
    or expected_head_version < 1
  ):
    raise ValueError("LIVE_CANARY_TRANSACTION_AND_VERSION_REQUIRED")
  async with db.begin_nested():
    source = await db.get(TAssistantExecutionRecord, source_execution_id)
    if source is None or source.environment != "PAPER":
      raise ValueError("LIVE_CANARY_PAPER_SOURCE_REQUIRED")
    head = await db.get(
      TTradeGlobalConfig, source.config_id, with_for_update=True, populate_existing=True
    )
    if head is None or head.account_id != source.account_id:
      raise ValueError("LIVE_CANARY_ACCOUNT_SCOPE_CONFLICT")
    record = await db.get(TAssistantConfigVersionRecord, config_version_id)
    if record is None:
      raise ValueError("LIVE_CANARY_CONFIG_REQUIRED")
    version = TAssistantConfigVersion(
      **{
        item.name: getattr(record, item.name)
        for item in fields(TAssistantConfigVersion)
      }
    )
    if (
      version.config_id != head.id
      or version.entry_authorization.value != "MANUAL_CONFIRM"
      or version.rollout_stage.value != "CANARY"
      or version.scorer_mode.value != "RULE_ONLY"
    ):
      raise ValueError("LIVE_CANARY_CONFIG_SCOPE_INVALID")
    policy = version.canonical_payload
    settings, universe = (
      policy.get("legacy_settings_snapshot"),
      policy.get("universe_policy"),
    )
    codes = list(canary_instrument_codes(policy))
    if (
      not isinstance(settings, dict)
      or settings.get("entry_authorization") != "MANUAL_CONFIRM"
    ):
      raise ValueError("LIVE_CANARY_MANUAL_PARAMETERS_REQUIRED")
    cap = _amount(policy.get("portfolio_policy", {}).get("max_total_t_amount"))
    approval = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == source_execution_id,
        TAssistantExecutionEventRecord.event_key == approval_event_key,
      )
    )
    evidence = dict(approval.payload or {}) if approval else {}
    if (
      approval is None
      or approval.event_type != "LIVE_CANARY_RELEASE_APPROVED"
      or (
        approval.occurred_at.replace(tzinfo=UTC)
        if approval.occurred_at.tzinfo is None
        else approval.occurred_at
      )
      > now
      or evidence.get("account_id") != head.account_id
      or evidence.get("config_version_id") != version.config_version_id
      or evidence.get("config_snapshot_hash") != version.config_snapshot_hash
      or evidence.get("p5_outcome") != "PASSED"
      or not _hash(evidence.get("p5_evidence_hash"))
      or not isinstance(evidence.get("actor_id"), str)
      or not evidence["actor_id"].strip()
      or evidence.get("allowed_stock_codes") != codes
      or cap > _amount(evidence.get("max_total_t_amount"))
    ):
      raise ValueError("LIVE_CANARY_EXACT_RELEASE_APPROVAL_REQUIRED")
    start, end = (
      aware_time(evidence.get("window_start")),
      aware_time(evidence.get("window_end")),
    )
    if start >= end:
      raise ValueError("LIVE_CANARY_MAINTENANCE_WINDOW_INVALID")
    identity = str(
      uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"quantx:live-canary:{source_execution_id}:{config_version_id}:{approval_event_key}",
      )
    )
    existing = await db.get(TAssistantExecutionRecord, identity)
    event_key = f"live-canary-prepared:{identity}"
    material = {
      "source_execution_id": source_execution_id,
      "approval_event_key": approval_event_key,
      "approval_hash": stable_manifest_hash(evidence),
      "config_snapshot_hash": version.config_snapshot_hash,
      "allowed_stock_codes": codes,
      "max_total_t_amount": str(cap),
    }
    if existing is not None:
      created = await db.scalar(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.execution_id == identity,
          TAssistantExecutionEventRecord.event_key == event_key,
        )
      )
      if (
        existing.environment != "LIVE"
        or existing.entry_authorization != "MANUAL_CONFIRM"
        or existing.rollout_stage != "CANARY"
        or existing.scorer_mode != "RULE_ONLY"
        or existing.account_id != head.account_id
        or existing.config_version_id != config_version_id
        or existing.config_snapshot_hash != version.config_snapshot_hash
        or created is None
        or created.event_type != "LIVE_CANARY_EXECUTION_PREPARED"
        or created.payload != material
      ):
        raise ValueError("LIVE_CANARY_IDEMPOTENCY_CONFLICT")
      return identity
    if not start <= now < end:
      raise ValueError("LIVE_CANARY_OUTSIDE_MAINTENANCE_WINDOW")
    if (
      head.state_version != expected_head_version
      or not head.enabled
      or head.strategy_run_id
      or head.active_config_version_id != source.config_version_id
      or head.desired_environment != "PAPER"
      or version.version <= source.frozen_config_version
    ):
      raise ValueError("LIVE_CANARY_HEAD_CHANGED_OR_LEGACY_ACTIVE")
    if await db.scalar(
      select(TAssistantExecutionRecord.execution_id)
      .where(
        TAssistantExecutionRecord.account_id == head.account_id,
        TAssistantExecutionRecord.environment == "LIVE",
      )
      .limit(1)
    ):
      raise ValueError("LIVE_CANARY_INITIAL_SOURCE_ALREADY_EXISTS")
    head.active_config_version_id, head.config_version = (
      version.config_version_id,
      version.version,
    )
    head.desired_environment, head.mode = "LIVE", "live"
    head.state_version += 1
    head.settings, head.ignored_stock_codes = (
      dict(settings),
      list(universe["ignored_stock_codes"]),
    )
    head.updated_at = now.replace(tzinfo=None)
    flag_modified(head, "updated_at")
    db.add(
      TAssistantExecutionRecord(
        execution_id=identity,
        config_id=head.id,
        config_version_id=version.config_version_id,
        frozen_config_version=version.version,
        config_snapshot_hash=version.config_snapshot_hash,
        account_id=head.account_id,
        environment="LIVE",
        entry_authorization="MANUAL_CONFIRM",
        rollout_stage="CANARY",
        scorer_mode="RULE_ONLY",
        status="WARMING",
        entry_readiness="WARMING",
        entry_readiness_reasons=["T_LIVE_READINESS_REQUIRED", "T_REWARM_REQUIRED"],
        entry_readiness_as_of=now,
        policy_version=version.policy_version,
        feature_schema_version=version.feature_schema_version,
        state_version=1,
        created_at=now,
        updated_at=now,
      )
    )
    await db.flush()
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        identity, event_key, "LIVE_CANARY_EXECUTION_PREPARED", now, material
      )
    )
    return identity
