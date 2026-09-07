"""Explicit frozen PAPER seed; never derives inventory or cash from LIVE."""

from copy import deepcopy
from dataclasses import fields
from datetime import UTC, datetime
from typing import Mapping

from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_application.t_trade_v3.portfolio_reference import (
  TPortfolioReference,
  aware_time,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.brokers.base import Position
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_infrastructure.models.paper_execution import PaperExecutionAccountRecord
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)


async def prepare_paper_seed(db, *, execution, config_payload, now: datetime):
  """Initialize from one explicit config seed, or report its absence. Caller commits.

  Existing isolated accounts may have been initialized through the same ledger
  API. They retain their original seed; a configured seed must agree on retries.
  Malformed explicit seeds fail closed instead of becoming partial accounts.
  """
  now = aware_time(now).astimezone(UTC)
  if execution.environment is not ExecutionEnvironment.PAPER:
    raise ValueError("PAPER_EXECUTION_SCOPE_REQUIRED")
  raw = config_payload.get("paper_seed")
  if raw is None:
    account = await db.get(PaperExecutionAccountRecord, execution.execution_id)
    if account is None:
      return ("PAPER_SEED_REQUIRED",)
    if account.account_id != execution.account_id or account.environment != "PAPER":
      raise ValueError("PAPER_ACCOUNT_SCOPE_INVALID")
    snapshot = await PaperExecutionLedger(
      db,
      receipt_sink=PaperReceiptConvergence(),
    ).get_snapshot(execution_id=execution.execution_id)
    if snapshot["as_of"] > now:
      raise ValueError("PAPER_FUTURE_SEED_FORBIDDEN")
    return ()
  if not isinstance(raw, Mapping) or set(raw) != {
    "snapshot_id",
    "as_of",
    "cash",
    "non_trading_asset_value",
    "positions",
    "bucket_checkpoint",
  }:
    raise ValueError("PAPER_FROZEN_SEED_FIELDS_INVALID")
  seed = deepcopy(dict(raw))
  as_of = aware_time(seed["as_of"]).astimezone(UTC)
  if as_of > now:
    raise ValueError("PAPER_FUTURE_SEED_FORBIDDEN")
  if not isinstance(seed["snapshot_id"], str) or not seed["snapshot_id"].strip():
    raise ValueError("PAPER_SEED_IDENTITY_REQUIRED")
  positions = seed["positions"]
  if not isinstance(positions, Mapping):
    raise ValueError("PAPER_FROZEN_SEED_POSITIONS_INVALID")
  position_fields = {item.name for item in fields(Position)}
  parsed = {}
  for code, values in positions.items():
    if (
      not isinstance(code, str)
      or not isinstance(values, Mapping)
      or set(values) != position_fields
      or values["instrument_code"] != code
    ):
      raise ValueError("PAPER_FROZEN_SEED_POSITION_FIELDS_INVALID")
    parsed[code] = Position(**values)
  seed["as_of"] = as_of.isoformat()
  await PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence()).initialize(
    execution_id=execution.execution_id,
    account_id=execution.account_id,
    cash=seed["cash"],
    non_trading_asset_value=seed["non_trading_asset_value"],
    positions=parsed,
    bucket_checkpoint=seed["bucket_checkpoint"],
    seed_as_of=as_of,
    seed_snapshot_id=seed["snapshot_id"],
    seed_snapshot_hash=stable_manifest_hash(seed),
  )
  return ()


def paper_policy_blockers(config_payload, *, now, required_codes):
  """Validate frozen entry policies before advertising execution readiness."""
  reasons = []
  try:
    reference = TPortfolioReference.from_config(
      config_payload,
      as_of=now,
      required_codes=required_codes,
    )
    reference.previous_trading_day(now)
  except ValueError as exc:
    reasons.append(str(exc))
  try:
    gate = dict(config_payload["entry_execution_gate_policy"])
    MarketDataCapabilityManifest(**gate.pop("capabilities"))
    EntryExecutionGatePolicy(**gate)
  except (KeyError, TypeError, ValueError):
    reasons.append("PAPER_REVIEW_FROZEN_GATE_POLICY_REQUIRED")
  return tuple(dict.fromkeys(reasons))
