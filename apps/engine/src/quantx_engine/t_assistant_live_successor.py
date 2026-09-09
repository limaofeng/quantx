"""Atomic, explicitly approved RULE_ONLY successor preparation.

The internal Engine command consumes existing approval evidence. The release control plane
must first persist an exact AUTO_RELEASE_APPROVED execution event after P6's
real observation gate and operator approval. Tests use isolated synthetic facts.
No successor is made RUNNING here and no broker/ExitPlan ownership is changed.
"""

import uuid
from dataclasses import fields
from datetime import UTC, datetime

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

from .t_assistant_live_drain import drain_live_entry_work


def _hash(value):
  return (
    isinstance(value, str)
    and len(value) == 64
    and all(c in "0123456789abcdef" for c in value)
  )


async def dispatch_live_auto_successor(db, *, payload, now):
  """Bind a durable command to one immutable release approval and account."""
  required = {
    "account_id",
    "predecessor_id",
    "config_version_id",
    "approval_event_key",
    "approval_hash",
    "expected_head_version",
  }
  if (
    not db.in_transaction()
    or not isinstance(payload, dict)
    or set(payload) != required
    or any(
      not isinstance(payload[key], str) or not payload[key].strip()
      for key in required - {"expected_head_version"}
    )
    or not _hash(payload["approval_hash"])
  ):
    raise ValueError("T_SUCCESSOR_COMMAND_INVALID")
  predecessor = await db.get(TAssistantExecutionRecord, payload["predecessor_id"])
  if predecessor is None or predecessor.account_id != payload["account_id"]:
    raise ValueError("T_SUCCESSOR_COMMAND_ACCOUNT_CONFLICT")
  approval = await db.scalar(
    select(TAssistantExecutionEventRecord).where(
      TAssistantExecutionEventRecord.execution_id == payload["predecessor_id"],
      TAssistantExecutionEventRecord.event_key == payload["approval_event_key"],
    )
  )
  if (
    approval is None
    or stable_manifest_hash(approval.payload) != payload["approval_hash"]
  ):
    raise ValueError("T_SUCCESSOR_COMMAND_APPROVAL_CONFLICT")
  return await prepare_live_auto_successor(
    db,
    predecessor_id=payload["predecessor_id"],
    config_version_id=payload["config_version_id"],
    approval_event_key=payload["approval_event_key"],
    expected_head_version=payload["expected_head_version"],
    now=now,
  )


async def prepare_live_auto_successor(
  db,
  *,
  predecessor_id: str,
  config_version_id: str,
  approval_event_key: str,
  expected_head_version: int,
  now: datetime,
):
  if (
    not db.in_transaction()
    or not isinstance(now, datetime)
    or now.tzinfo is None
    or now.utcoffset() is None
  ):
    raise ValueError("T_SUCCESSOR_EXPLICIT_TRANSACTION_AND_TIME_REQUIRED")
  if type(expected_head_version) is not int or expected_head_version < 1:
    raise ValueError("T_SUCCESSOR_HEAD_VERSION_REQUIRED")
  now = now.astimezone(UTC)
  async with db.begin_nested():
    probe = await db.get(TAssistantExecutionRecord, predecessor_id)
    if probe is None:
      raise ValueError("T_SUCCESSOR_PREDECESSOR_REQUIRED")
    head = await db.get(
      TTradeGlobalConfig, probe.config_id, with_for_update=True, populate_existing=True
    )
    predecessor = await db.get(
      TAssistantExecutionRecord,
      predecessor_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      head is None
      or head.account_id != predecessor.account_id
      or predecessor.environment != "LIVE"
      or predecessor.entry_authorization != "MANUAL_CONFIRM"
      or predecessor.scorer_mode != "RULE_ONLY"
      or head.strategy_run_id
    ):
      raise ValueError("T_SUCCESSOR_SOURCE_SCOPE_INVALID")
    record = await db.get(TAssistantConfigVersionRecord, config_version_id)
    if record is None:
      raise ValueError("T_SUCCESSOR_CONFIG_REQUIRED")
    version = TAssistantConfigVersion(
      **{
        field.name: getattr(record, field.name)
        for field in fields(TAssistantConfigVersion)
      }
    )
    if (
      version.config_id != head.id
      or version.version <= predecessor.frozen_config_version
      or version.entry_authorization.value != "AUTO"
      or version.scorer_mode.value != "RULE_ONLY"
      or version.policy_version != predecessor.policy_version
      or version.feature_schema_version != predecessor.feature_schema_version
    ):
      raise ValueError("T_SUCCESSOR_CONFIG_SCOPE_INVALID")
    approval = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == predecessor_id,
        TAssistantExecutionEventRecord.event_key == approval_event_key,
      )
    )
    evidence = dict(approval.payload or {}) if approval is not None else {}
    if (
      approval is None
      or approval.event_type != "AUTO_RELEASE_APPROVED"
      or (
        approval.occurred_at.replace(tzinfo=UTC)
        if approval.occurred_at.tzinfo is None
        else approval.occurred_at.astimezone(UTC)
      )
      > now
      or evidence.get("account_id") != predecessor.account_id
      or evidence.get("config_version_id") != config_version_id
      or evidence.get("config_snapshot_hash") != version.config_snapshot_hash
      or evidence.get("p6_outcome") != "PASSED"
      or not _hash(evidence.get("p6_evidence_hash"))
      or not isinstance(evidence.get("actor_id"), str)
      or not evidence["actor_id"].strip()
      or not isinstance(evidence.get("auto_observation_policy_version"), str)
      or not evidence["auto_observation_policy_version"].strip()
    ):
      raise ValueError("T_SUCCESSOR_EXACT_RELEASE_APPROVAL_REQUIRED")
    successor_id = str(
      uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"quantx:t-successor:{predecessor_id}:{config_version_id}:{approval_event_key}",
      )
    )
    existing = await db.get(TAssistantExecutionRecord, successor_id)
    if existing is not None:
      prepared = await db.scalar(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.execution_id == successor_id,
          TAssistantExecutionEventRecord.event_key == f"successor-created:{successor_id}",
        )
      )
      prepared_payload = dict(prepared.payload or {}) if prepared is not None else {}
      if (
        existing.config_snapshot_hash != version.config_snapshot_hash
        or existing.config_id != version.config_id
        or existing.frozen_config_version != version.version
        or existing.account_id != predecessor.account_id
        or existing.environment != "LIVE"
        or existing.entry_authorization != "AUTO"
        or existing.config_version_id != config_version_id
        or existing.policy_version != version.policy_version
        or existing.feature_schema_version != version.feature_schema_version
        or existing.scorer_mode != "RULE_ONLY"
        or existing.rollout_stage != version.rollout_stage.value
        or prepared is None
        or prepared.event_type != "LIVE_AUTO_SUCCESSOR_PREPARED"
        or prepared_payload.get("predecessor_id") != predecessor_id
        or prepared_payload.get("approval_event_key") != approval_event_key
        or prepared_payload.get("approval_hash") != stable_manifest_hash(evidence)
        or prepared_payload.get("config_snapshot_hash") != version.config_snapshot_hash
        or prepared_payload.get("expected_head_version") != expected_head_version
      ):
        raise ValueError("T_SUCCESSOR_IDEMPOTENCY_CONFLICT")
      return successor_id
    if (
      head.state_version != expected_head_version
      or head.active_config_version_id != predecessor.config_version_id
      or head.desired_environment != "LIVE"
      or not head.enabled
      or predecessor.status not in {"RUNNING", "DRAINING", "RECONCILE_REQUIRED"}
    ):
      raise ValueError("T_SUCCESSOR_HEAD_CHANGED")
    settings = version.canonical_payload.get("legacy_settings_snapshot")
    universe = version.canonical_payload.get("universe_policy")
    if (
      not isinstance(settings, dict)
      or settings.get("entry_authorization") != "AUTO"
      or not isinstance(universe, dict)
      or not isinstance(universe.get("ignored_stock_codes"), list)
    ):
      raise ValueError("T_SUCCESSOR_COMPLETE_CONFIG_REQUIRED")
    drained = await drain_live_entry_work(
      db,
      execution_id=predecessor_id,
      now=now,
      reason=f"APPROVED_AUTO_SUCCESSOR:{approval_event_key}",
    )
    head.config_version = version.version
    head.active_config_version_id = version.config_version_id
    head.state_version += 1
    head.settings = dict(settings)
    head.ignored_stock_codes = list(universe["ignored_stock_codes"])
    head.updated_at = now.replace(tzinfo=None)
    flag_modified(head, "updated_at")
    successor = TAssistantExecutionRecord(
      execution_id=successor_id,
      config_id=head.id,
      config_version_id=version.config_version_id,
      frozen_config_version=version.version,
      config_snapshot_hash=version.config_snapshot_hash,
      account_id=predecessor.account_id,
      environment="LIVE",
      entry_authorization="AUTO",
      rollout_stage=version.rollout_stage.value,
      status="WARMING",
      entry_readiness="WARMING",
      entry_readiness_reasons=[
        "T_REWARM_REQUIRED",
        "T_PREDECESSOR_RECONCILIATION_REQUIRED",
      ],
      entry_readiness_as_of=now,
      policy_version=version.policy_version,
      feature_schema_version=version.feature_schema_version,
      scorer_mode="RULE_ONLY",
      state_version=1,
      created_at=now,
      updated_at=now,
    )
    db.add(successor)
    await db.flush()
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        successor_id,
        f"successor-created:{successor_id}",
        "LIVE_AUTO_SUCCESSOR_PREPARED",
        now,
        {
          "predecessor_id": predecessor_id,
          "approval_event_key": approval_event_key,
          "approval_hash": stable_manifest_hash(evidence),
          "expected_head_version": expected_head_version,
          "config_snapshot_hash": version.config_snapshot_hash,
          "retained_intent_ids": list(drained.retained_intent_ids),
          "retained_client_order_ids": list(drained.retained_client_order_ids),
        },
      )
    )
    return successor_id
