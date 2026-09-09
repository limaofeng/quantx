"""Create a portable, credential-free release request without approving a release."""

import asyncio
from dataclasses import fields
from pathlib import Path
from uuid import UUID

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from sqlalchemy import select

from .t_assistant_release_evidence import verify_live_release_evidence


async def build_release_request(
  db,
  *,
  account_id,
  source_execution_id,
  config_version_id,
  expected_config_hash,
  expected_report_hash,
  expected_policy_hash,
  evidence_directory,
  window_start,
  window_end,
  now,
):
  """Read current source/head and verify reviewed evidence; caller owns read transaction."""
  start, end, now = map(aware_time, (window_start, window_end, now))
  directory = Path(evidence_directory)
  if str(UUID(directory.name)) != directory.name or start >= end or now >= end:
    raise ValueError("LIVE_RELEASE_REQUEST_WINDOW_OR_EVALUATION_INVALID")
  record = await db.get(TAssistantConfigVersionRecord, config_version_id)
  if record is None or record.config_snapshot_hash != expected_config_hash:
    raise ValueError("LIVE_RELEASE_REVIEWED_CONFIG_CHANGED")
  target = TAssistantConfigVersion(
    **{
      field.name: getattr(record, field.name)
      for field in fields(TAssistantConfigVersion)
    }
  )
  head = await db.get(TTradeGlobalConfig, target.config_id)
  source = await db.get(TAssistantExecutionRecord, source_execution_id)
  if (
    head is None
    or source is None
    or not head.enabled
    or head.strategy_run_id
    or head.account_id != account_id
    or source.account_id != account_id
    or head.desired_environment != "PAPER"
    or source.environment != "PAPER"
    or source.config_id != target.config_id
    or head.active_config_version_id != source.config_version_id
    or target.version <= source.frozen_config_version
  ):
    raise ValueError("LIVE_RELEASE_SOURCE_OR_HEAD_CONFLICT")
  if await db.scalar(
    select(TAssistantExecutionRecord.execution_id)
    .where(
      TAssistantExecutionRecord.account_id == account_id,
      TAssistantExecutionRecord.environment == "LIVE",
    )
    .limit(1)
  ):
    raise ValueError("LIVE_RELEASE_INITIAL_SOURCE_ALREADY_EXISTS")
  await asyncio.to_thread(
    verify_live_release_evidence,
    directory,
    expected_report_hash=expected_report_hash,
    expected_policy_hash=expected_policy_hash,
    target=target,
  )
  return {
    "schema": "quantx.t-assistant-release-request.v1",
    "request": {
      "accountId": account_id,
      "sourceExecutionId": source_execution_id,
      "configVersionId": config_version_id,
      "expectedConfigHash": expected_config_hash,
      "expectedHeadVersion": head.state_version,
      "evaluationId": directory.name,
      "expectedReportHash": expected_report_hash,
      "expectedPolicyHash": expected_policy_hash,
      "windowStart": start.isoformat(),
      "windowEnd": end.isoformat(),
    },
  }
