"""Persist an authenticated release review after complete P5 artifact validation."""

import asyncio
from dataclasses import fields
from datetime import UTC

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

from .t_assistant_release_evidence import verify_live_release_evidence


async def record_live_release_approval(
  db,
  *,
  account_id,
  actor_id,
  review_reference,
  approval_event_key,
  source_execution_id,
  config_version_id,
  expected_config_hash,
  expected_head_version,
  evidence_directory,
  expected_report_hash,
  expected_policy_hash,
  window_start,
  window_end,
  now,
):
  """Caller authenticates actor/account and owns transaction; no LIVE enablement.

  Review hashes and window must be explicitly confirmed by that actor. This
  internal service is not an API and cannot infer user approval from a P5 PASS.
  """
  now, start, end = (
    aware_time(v).astimezone(UTC) for v in (now, window_start, window_end)
  )
  if (
    not db.in_transaction()
    or any(
      not isinstance(v, str) or not v.strip()
      for v in (
        account_id,
        actor_id,
        review_reference,
        approval_event_key,
        expected_config_hash,
      )
    )
    or type(expected_head_version) is not int
    or expected_head_version < 1
    or start >= end
  ):
    raise ValueError("LIVE_RELEASE_EXPLICIT_REVIEW_REQUIRED")
  record = await db.get(TAssistantConfigVersionRecord, config_version_id)
  if record is None:
    raise ValueError("LIVE_RELEASE_CONFIG_REQUIRED")
  target = TAssistantConfigVersion(
    **{
      field.name: getattr(record, field.name)
      for field in fields(TAssistantConfigVersion)
    }
  )
  if target.config_snapshot_hash != expected_config_hash:
    raise ValueError("LIVE_RELEASE_REVIEWED_CONFIG_CHANGED")
  verified = await asyncio.to_thread(
    verify_live_release_evidence,
    evidence_directory,
    expected_report_hash=expected_report_hash,
    expected_policy_hash=expected_policy_hash,
    target=target,
  )
  payload = {
    **verified["material"],
    "release_binding_hash": verified["hash"],
    "account_id": account_id,
    "actor_id": actor_id,
    "review_reference": review_reference,
    "p5_outcome": "PASSED",
    "window_start": start.isoformat(),
    "window_end": end.isoformat(),
    "max_total_t_amount": str(
      target.canonical_payload["portfolio_policy"]["max_total_t_amount"]
    ),
    "expected_head_version": expected_head_version,
  }
  async with db.begin_nested():
    head = await db.get(
      TTradeGlobalConfig, target.config_id, with_for_update=True, populate_existing=True
    )
    source = await db.get(
      TAssistantExecutionRecord,
      source_execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      head is None
      or source is None
      or source.environment != "PAPER"
      or source.account_id != account_id
      or head.account_id != account_id
      or source.config_id != target.config_id
    ):
      raise ValueError("LIVE_RELEASE_SOURCE_ACCOUNT_CONFLICT")
    existing = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == source_execution_id,
        TAssistantExecutionEventRecord.event_key == approval_event_key,
      )
    )
    if existing is None:
      if (
        now >= end
        or head.state_version != expected_head_version
        or not head.enabled
        or head.strategy_run_id
        or head.desired_environment != "PAPER"
        or head.active_config_version_id != source.config_version_id
        or target.version <= source.frozen_config_version
      ):
        raise ValueError("LIVE_RELEASE_HEAD_OR_WINDOW_CHANGED")
      if await db.scalar(
        select(TAssistantExecutionRecord.execution_id)
        .where(
          TAssistantExecutionRecord.account_id == account_id,
          TAssistantExecutionRecord.environment == "LIVE",
        )
        .limit(1)
      ):
        raise ValueError("LIVE_RELEASE_INITIAL_SOURCE_ALREADY_EXISTS")
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        source_execution_id,
        approval_event_key,
        "LIVE_CANARY_RELEASE_APPROVED",
        now,
        payload,
      )
    )
  return {
    "approval_event_key": approval_event_key,
    "approval_hash": stable_manifest_hash(payload),
  }
