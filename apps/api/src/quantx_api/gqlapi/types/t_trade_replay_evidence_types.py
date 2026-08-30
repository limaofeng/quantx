"""Explicit-version read contracts for replay signals and decision audit."""

from enum import Enum
from typing import Optional

import strawberry

from .common_types import PageInfo
from .strategy_types import ExecutionTraceView, StrategyDecision
from .t_trade_types import TTradeSignalEvaluation


@strawberry.enum
class TTradeReplayEvidenceAvailability(Enum):
  AVAILABLE = "AVAILABLE"
  UNAVAILABLE = "UNAVAILABLE"


@strawberry.enum
class TTradeReplayEvidenceSource(Enum):
  RUN_PROJECTION = "RUN_PROJECTION"
  VERSION_ARCHIVE = "VERSION_ARCHIVE"


@strawberry.type
class TTradeReplayEvidenceInfo:
  run_id: str
  backtest_id: str
  backtest_version: int
  availability: TTradeReplayEvidenceAvailability
  source: TTradeReplayEvidenceSource
  sealed: bool
  reason_code: Optional[str] = None
  content_fingerprint: Optional[str] = None


@strawberry.input
class TTradeReplaySignalFilterInput:
  stock_code: Optional[str] = None
  event_types: Optional[list[str]] = None
  selected_path: Optional[str] = None
  candidate_status: Optional[str] = None
  candidate_id: Optional[str] = None
  event_key: Optional[str] = None
  search: Optional[str] = None
  include_context: bool = False
  include_diagnostics: bool = False


@strawberry.input
class TTradeReplayAuditFilterInput:
  stock_code: Optional[str] = None
  with_intent: Optional[bool] = None
  execution_status: Optional[str] = None
  event_key: Optional[str] = None
  search: Optional[str] = None


@strawberry.type
class TTradeReplaySignalSummary:
  event_count: int = 0
  candidate_count: int = 0
  linked_intent_count: int = 0
  suppressed_count: int = 0


@strawberry.type
class TTradeReplaySignalPage:
  evidence: TTradeReplayEvidenceInfo
  items: list[TTradeSignalEvaluation]
  summary: TTradeReplaySignalSummary
  page_info: PageInfo


@strawberry.type
class TTradeReplayAuditItem:
  decision: StrategyDecision
  evaluation_event_keys: list[str]
  executions: list[ExecutionTraceView]


@strawberry.type
class TTradeReplayAuditSummary:
  decision_count: int = 0
  with_intent_count: int = 0
  no_intent_count: int = 0
  risk_blocked_count: int = 0


@strawberry.type
class TTradeReplayAuditPage:
  evidence: TTradeReplayEvidenceInfo
  items: list[TTradeReplayAuditItem]
  summary: TTradeReplayAuditSummary
  page_info: PageInfo
