"""Deterministic cross-domain ordering for account risk increases."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerType

RISK_INCREASE_ADMISSION_POLICY_VERSION = "AccountRiskIncreaseAdmissionPolicy.v1"
RISK_INCREASE_OWNER_PRIORITY: Mapping[ExecutionOwnerType, int] = MappingProxyType(
  {
    ExecutionOwnerType.MANUAL_COMMAND: 0,
    ExecutionOwnerType.ENTRY_PLAN: 1,
    ExecutionOwnerType.BOARD_ASSISTANT_EXECUTION: 2,
    ExecutionOwnerType.T_ASSISTANT_EXECUTION: 3,
    ExecutionOwnerType.STRATEGY_RUN: 4,
  }
)


@dataclass(frozen=True)
class RiskIncreaseAdmissionCandidate:
  intent_id: str
  owner_type: ExecutionOwnerType
  owner_id: str
  created_at: datetime
  material_fingerprint: str
  allocation_batch_id: str | None = None
  allocation_rank: int | None = None
  allocation_created_at: datetime | None = None

  def __post_init__(self) -> None:
    if not str(self.intent_id or "").strip():
      raise ValueError("RISK_ADMISSION_INTENT_ID_REQUIRED")
    if not str(self.owner_id or "").strip():
      raise ValueError("RISK_ADMISSION_OWNER_ID_REQUIRED")
    if self.owner_type not in RISK_INCREASE_OWNER_PRIORITY:
      raise ValueError("RISK_ADMISSION_OWNER_NOT_RISK_INCREASE_PRODUCER")
    if len(str(self.material_fingerprint or "")) != 64:
      raise ValueError("RISK_ADMISSION_MATERIAL_FINGERPRINT_REQUIRED")
    if self.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION:
      if (
        not self.allocation_batch_id
        or type(self.allocation_rank) is not int
        or self.allocation_rank < 1
        or not isinstance(self.allocation_created_at, datetime)
      ):
        raise ValueError("RISK_ADMISSION_T_ALLOCATION_REQUIRED")
    elif any(
      value is not None
      for value in (
        self.allocation_batch_id,
        self.allocation_rank,
        self.allocation_created_at,
      )
    ):
      raise ValueError("RISK_ADMISSION_ALLOCATION_OWNER_CONFLICT")

  def manifest_value(self) -> dict[str, str | int | None]:
    return {
      "intent_id": self.intent_id,
      "owner_type": self.owner_type.value,
      "owner_id": self.owner_id,
      "created_at": self.created_at.isoformat(timespec="microseconds"),
      "material_fingerprint": self.material_fingerprint.lower(),
      "allocation_batch_id": self.allocation_batch_id,
      "allocation_rank": self.allocation_rank,
      "allocation_created_at": self.allocation_created_at.isoformat(
        timespec="microseconds"
      )
      if self.allocation_created_at is not None
      else None,
    }


@dataclass(frozen=True)
class RankedRiskIncreaseAdmission:
  admission_rank: int
  candidate: RiskIncreaseAdmissionCandidate


def rank_risk_increase_candidates(
  candidates: Iterable[RiskIncreaseAdmissionCandidate],
) -> tuple[RankedRiskIncreaseAdmission, ...]:
  values = list(candidates)
  if len({item.intent_id for item in values}) != len(values):
    raise ValueError("RISK_ADMISSION_DUPLICATE_INTENT")
  ordered = sorted(
    values,
    key=lambda item: (
      RISK_INCREASE_OWNER_PRIORITY[item.owner_type],
      item.allocation_created_at
      if item.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION
      else item.created_at,
      item.owner_type.value,
      item.owner_id,
      item.allocation_batch_id or "",
      item.allocation_rank or 0,
      item.intent_id,
    ),
  )
  return tuple(
    RankedRiskIncreaseAdmission(rank, item)
    for rank, item in enumerate(ordered, start=1)
  )


def risk_increase_intent_manifest_hash(
  ranked: Iterable[RankedRiskIncreaseAdmission],
) -> str:
  payload = [
    {
      "rank": item.admission_rank,
      **item.candidate.manifest_value(),
    }
    for item in ranked
  ]
  return hashlib.sha256(
    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
  ).hexdigest()


def risk_increase_input_fingerprint(
  *,
  account_id: str,
  environment: ExecutionEnvironment,
  paper_execution_id: str | None,
  account_snapshot_id: str,
  account_snapshot_hash: str,
  obligation_watermark: str,
  intent_manifest_hash: str,
  policy_version: str = RISK_INCREASE_ADMISSION_POLICY_VERSION,
) -> str:
  if not isinstance(environment, ExecutionEnvironment) or environment not in {
    ExecutionEnvironment.LIVE,
    ExecutionEnvironment.PAPER,
  }:
    raise ValueError("RISK_ADMISSION_ENVIRONMENT_INVALID")
  if (
    environment is ExecutionEnvironment.LIVE
    and paper_execution_id is not None
    or environment is ExecutionEnvironment.PAPER
    and (
      not isinstance(paper_execution_id, str)
      or not paper_execution_id.strip()
      or paper_execution_id != paper_execution_id.strip()
    )
  ):
    raise ValueError("RISK_ADMISSION_PAPER_SCOPE_INVALID")
  values = {
    "account_id": str(account_id),
    "account_snapshot_id": str(account_snapshot_id),
    "account_snapshot_hash": str(account_snapshot_hash).lower(),
    "obligation_watermark": str(obligation_watermark).lower(),
    "intent_manifest_hash": str(intent_manifest_hash).lower(),
    "policy_version": str(policy_version),
  }
  if any(not value for value in values.values()):
    raise ValueError("RISK_ADMISSION_INPUT_INCOMPLETE")
  values.update(environment=environment.value, paper_execution_id=paper_execution_id)
  return hashlib.sha256(
    json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
  ).hexdigest()


__all__ = [
  "RISK_INCREASE_ADMISSION_POLICY_VERSION",
  "RISK_INCREASE_OWNER_PRIORITY",
  "RankedRiskIncreaseAdmission",
  "RiskIncreaseAdmissionCandidate",
  "rank_risk_increase_candidates",
  "risk_increase_input_fingerprint",
  "risk_increase_intent_manifest_hash",
]
