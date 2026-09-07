"""Typed, read-only facts from an explicitly scoped PAPER execution."""

from datetime import datetime
from decimal import Decimal
from typing import Generic, TypeVar

import strawberry

from .common_types import PageInfo

T = TypeVar("T")


@strawberry.type
class TAssistantPaperPage(Generic[T]):
  nodes: list[T]
  page_info: PageInfo


@strawberry.type
class TAssistantPaperExecution:
  execution_id: str
  environment: str
  status: str
  entry_readiness: str
  entry_readiness_reasons: list[str]
  entry_readiness_as_of: datetime
  scorer_mode: str
  config_version_id: str
  frozen_config_version: int
  config_snapshot_hash: str
  policy_version: str
  feature_schema_version: int
  created_at: datetime
  seed_present: bool
  seed_as_of: datetime | None
  seed_snapshot_id: str | None
  snapshot_as_of: datetime | None


@strawberry.type
class TAssistantPaperOpportunity:
  evidence_id: str
  candidate_id: str | None
  instrument_code: str
  event_type: str
  evaluated_at: datetime
  created_at: datetime
  frozen_evidence_present: bool
  source_at: datetime | None
  accepted_at: datetime | None
  score: float | None
  reason_codes: list[str] | None
  candidate_fingerprint: str | None


@strawberry.type
class TAssistantPaperAllocation:
  allocation_batch_id: str
  cycle_id: str
  allocation_attempt: int
  status: str
  created_at: datetime
  committed_at: datetime | None
  expires_at: datetime
  terminal_reason: str | None
  decision_id: str | None
  instrument_code: str | None
  candidate_id: str | None
  rank: int | None
  action: str | None
  reason_codes: list[str] | None
  requested_amount_ceiling: Decimal | None
  allocated_amount_cap: Decimal | None
  next_eligible_at: datetime | None


@strawberry.type
class TAssistantPaperReason:
  event_id: str
  event_type: str
  occurred_at: datetime
  source_type: str | None
  source_id: str | None
  reason_code: str | None
  reason_codes: list[str] | None


@strawberry.type
class TAssistantPaperOrder:
  order_id: str
  intent_id: str
  instrument_code: str
  owner_type: str
  owner_id: str
  side: str
  status: str
  volume: int
  filled_volume: int
  limit_price: Decimal
  submitted_at: datetime
  expires_at: datetime
  source_at: datetime | None
  accepted_at: datetime | None


@strawberry.type
class TAssistantPaperExitPlan:
  plan_id: str
  instrument_code: str
  status: str
  protected_volume: int
  exited_volume: int
  remaining_volume: int
  capacity_status: str
  capacity_error: str | None
  last_error: str | None
  last_evaluated_at: datetime | None
