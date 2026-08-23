"""Application service for restart-safe candidate outcome maturation.

The service is an analytics side channel.  It never returns data to strategy
evaluation and only accepts source-ordered Tick or authoritative fill facts.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from quantx_domain.trading.t_trade_candidate_outcome import (
  DEFAULT_CANDIDATE_OUTCOME_HORIZONS_SECONDS,
  CandidateExecutionFill,
  CandidateOutcomeDefinition,
  CandidateOutcomeState,
  CandidatePriceObservation,
  apply_candidate_execution_fill,
  finalize_candidate_outcome,
  observe_candidate_outcome,
  start_candidate_outcome,
  validate_fixed_horizons,
)
from sqlalchemy import and_, or_, select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import StrategyRuntimeEvent
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  T_TRADE_EVALUATION_KIND_MATERIAL,
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_trade_candidate_outcome_repository import (
  CandidateOutcomeConcurrencyError,
  TTradeCandidateOutcomeRepository,
)

_REPAIR_PAGE_SIZE = 256
_FINALIZE_PAGE_SIZE = 128
_MAX_REPAIR_RUN_CURSORS = 4_096
_MAX_REPAIR_ISSUE_SAMPLES = 16
_MAX_REPAIR_STATE_SAMPLES = 256


@dataclass(frozen=True)
class CandidateOutcomeRepairIssue:
  """Bounded, non-sensitive evidence for one quarantined runtime event."""

  event_id: str
  code: str


@dataclass(frozen=True)
class CandidateOutcomeReconciliationResult:
  """One bounded page of idempotent candidate-outcome repair work."""

  states: tuple[CandidateOutcomeState, ...]
  examined_count: int
  repaired_count: int
  idempotent_count: int
  skipped_count: int
  quarantined_count: int
  deferred_count: int
  issue_counts: tuple[tuple[str, int], ...]
  issues: tuple[CandidateOutcomeRepairIssue, ...]
  has_more: bool

  @property
  def complete(self) -> bool:
    return not self.has_more


@dataclass(frozen=True)
class CandidateOutcomeFinalizationResult:
  """Bounded-memory summary of one complete run-finalization sweep."""

  finalized_count: int
  concurrently_finalized_count: int
  page_count: int

  @property
  def examined_count(self) -> int:
    return self.finalized_count + self.concurrently_finalized_count


class _CandidateOutcomeRepairRejected(ValueError):
  """A single malformed or cross-scope fact that must not be retried."""

  def __init__(self, code: str) -> None:
    super().__init__(code)
    self.code = code


class TTradeCandidateOutcomeService:
  """Persist causal facts through a caller-owned repository/session."""

  def __init__(
    self,
    repository: TTradeCandidateOutcomeRepository,
    *,
    horizons_seconds: Sequence[int] = DEFAULT_CANDIDATE_OUTCOME_HORIZONS_SECONDS,
    max_observation_gap_ms: int = 60_000,
    finalize_page_size: int = _FINALIZE_PAGE_SIZE,
  ) -> None:
    self.repository = repository
    self.horizons_seconds = validate_fixed_horizons(horizons_seconds)
    if max_observation_gap_ms <= 0:
      raise ValueError("候选结果最大连续缺口必须大于零")
    self.max_observation_gap_ms = int(max_observation_gap_ms)
    normalized_finalize_page_size = int(finalize_page_size)
    if not 1 <= normalized_finalize_page_size <= _FINALIZE_PAGE_SIZE:
      raise ValueError(
        f"候选结果终态分页大小必须在 1..{_FINALIZE_PAGE_SIZE} 之间"
      )
    self.finalize_page_size = normalized_finalize_page_size

  async def seed_material_event(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    event: Mapping[str, Any],
  ) -> CandidateOutcomeState | None:
    """Freeze a candidate definition once; repeated materialization is safe."""

    if str(event.get("record_kind") or "").upper() != "MATERIAL":
      return None
    snapshot = dict(event.get("signal_snapshot") or {})
    candidate_id = _optional_text(snapshot.get("candidate_id"))
    candidate_fingerprint = _optional_text(snapshot.get("candidate_fingerprint"))
    if candidate_id is None or candidate_fingerprint is None:
      return None
    features = dict(snapshot.get("features") or {})
    reference_price = _positive_float(features.get("price"), "候选参考价")
    definition = CandidateOutcomeDefinition(
      candidate_id=candidate_id,
      candidate_fingerprint=candidate_fingerprint,
      strategy_run_id=_required_text(strategy_run_id, "策略运行标识"),
      instrument_code=_required_text(
        event.get("instrument_code") or snapshot.get("instrument_code"),
        "证券代码",
      ),
      source_time_ms=_non_negative_int(snapshot.get("source_time_ms"), "源时间"),
      tick_ordinal=_non_negative_int(snapshot.get("tick_ordinal"), "Tick 序号"),
      continuity_generation=_required_text(
        snapshot.get("continuity_generation"), "连续代际"
      ),
      reference_price=reference_price,
      policy_version=_required_text(snapshot.get("policy_version"), "策略版本"),
      feature_schema_version=_required_text(
        snapshot.get("feature_schema_version"), "特征版本"
      ),
      profile_version=_optional_text(
        snapshot.get("profile_version") or snapshot.get("reference_profile_version")
      ),
      profile_fingerprint=_optional_text(snapshot.get("profile_fingerprint")),
      horizons_seconds=self.horizons_seconds,
      max_observation_gap_ms=self.max_observation_gap_ms,
    )
    state = start_candidate_outcome(definition)
    row = await self.repository.create_or_get(account_id=account_id, state=state)
    return self.repository.state_from_row(row)

  async def observe_tick(
    self,
    *,
    strategy_run_id: str,
    instrument_code: str,
    source_time_ms: int,
    tick_ordinal: int,
    continuity_generation: str,
    price: float,
    trading_halted: bool = False,
  ) -> list[CandidateOutcomeState]:
    """Advance all open candidates for one run/instrument from one real Tick."""

    rows = await self.repository.list_observing(
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
    )
    advanced: list[CandidateOutcomeState] = []
    observation = CandidatePriceObservation(
      source_time_ms=int(source_time_ms),
      tick_ordinal=int(tick_ordinal),
      continuity_generation=str(continuity_generation),
      price=float(price),
      trading_halted=bool(trading_halted),
    )
    for row in rows:
      state = self.repository.state_from_row(row)
      before = state.to_dict()
      observe_candidate_outcome(state, observation)
      if state.to_dict() != before:
        saved = await self.repository.save(
          state=state,
          expected_version=int(row.state_version),
        )
        state = self.repository.state_from_row(saved)
      advanced.append(state)
    return advanced

  async def record_fill(
    self,
    *,
    strategy_run_id: str,
    candidate_id: str,
    fill_id: str,
    role: str,
    source_time_ms: int,
    price: float,
    volume: int,
    fee: float | None,
    entry_complete: bool = False,
    entry_target_volume: int | None = None,
  ) -> CandidateOutcomeState | None:
    row = await self.repository.get(
      strategy_run_id=strategy_run_id,
      candidate_id=candidate_id,
    )
    if row is None:
      return None
    state = self.repository.state_from_row(row)
    before = state.to_dict()
    apply_candidate_execution_fill(
      state,
      CandidateExecutionFill(
        fill_id=fill_id,
        role=str(role).upper(),
        source_time_ms=int(source_time_ms),
        price=float(price),
        volume=int(volume),
        fee=float(fee) if fee is not None else None,
        entry_complete=bool(entry_complete),
        entry_target_volume=(
          int(entry_target_volume) if entry_target_volume is not None else None
        ),
      ),
    )
    if state.to_dict() == before:
      return state
    saved = await self.repository.save(
      state=state,
      expected_version=int(row.state_version),
    )
    return self.repository.state_from_row(saved)

  async def record_trade_fact(
    self,
    *,
    strategy_run_id: str,
    trade: Any,
    entry_complete: bool | None = None,
    authoritative_fee: float | None = None,
    fee_is_authoritative: bool | None = None,
    entry_target_volume: int | None = None,
  ) -> CandidateOutcomeState | None:
    """Adapt a broker ``TradeRecord`` without estimating absent fee truth."""

    normalized_run_id = _required_text(strategy_run_id, "策略运行标识")
    metadata = dict(getattr(trade, "metadata", {}) or {})
    candidate_id = _optional_text(metadata.get("candidate_id"))
    role = _optional_text(metadata.get("t_trade_role"))
    if candidate_id is None or role is None:
      return None
    row = await self.repository.get(
      strategy_run_id=normalized_run_id,
      candidate_id=candidate_id,
    )
    if row is None:
      return None
    state = self.repository.state_from_row(row)
    definition = state.definition
    normalized_role = str(role).upper()
    metadata_instrument = _optional_text(metadata.get("instrument_code"))
    trade_instrument = _optional_text(getattr(trade, "instrument_code", None))
    if (
      normalized_role not in {"ENTRY", "EXIT"}
      or _optional_text(metadata.get("strategy_run_id")) != normalized_run_id
      or _optional_text(metadata.get("account_id"))
      != _optional_text(getattr(row, "account_id", None))
      or metadata_instrument is None
      or metadata_instrument.upper() != definition.instrument_code.upper()
      or (trade_instrument is not None and trade_instrument.upper() != metadata_instrument.upper())
      or _optional_text(metadata.get("candidate_fingerprint"))
      != definition.candidate_fingerprint
      or _optional_text(metadata.get("policy_version")) != definition.policy_version
    ):
      raise ValueError("候选成交作用域与冻结定义不一致")
    trade_time = getattr(trade, "trade_time", None)
    if not isinstance(trade_time, datetime):
      raise ValueError("候选成交缺少权威成交时间")
    raw_fee = (
      None
      if fee_is_authoritative is False
      else authoritative_fee
      if authoritative_fee is not None
      else getattr(trade, "commission", None)
    )
    fee = float(raw_fee) if raw_fee is not None else None
    return await self.record_fill(
      strategy_run_id=normalized_run_id,
      candidate_id=candidate_id,
      fill_id=_required_text(getattr(trade, "trade_id", None), "成交标识"),
      role=normalized_role,
      source_time_ms=int(time_utils.to_utc(trade_time).timestamp() * 1000),
      price=_positive_float(getattr(trade, "price", None), "成交价格"),
      volume=_positive_int(getattr(trade, "volume", None), "成交数量"),
      fee=fee,
      entry_complete=(
        bool(entry_complete)
        if entry_complete is not None
        else bool(metadata.get("entry_complete", False))
      ),
      entry_target_volume=(
        int(entry_target_volume)
        if entry_target_volume is not None
        else _optional_positive_int(metadata.get("requested_entry_volume"))
      ),
    )

  async def finalize_run(
    self,
    *,
    strategy_run_id: str,
    finalized_at_ms: int,
  ) -> CandidateOutcomeFinalizationResult:
    """Fail closed in fixed keyset pages without retaining finalized states.

    Each successful CAS is committed by the repository before the cursor is
    advanced.  If another worker finalized the same row first, that terminal
    row is treated as an idempotent convergence; a conflicting row that is
    still open is surfaced for retry instead of being silently skipped.
    """

    normalized_run_id = _required_text(strategy_run_id, "策略运行标识")
    normalized_finalized_at_ms = _non_negative_int(finalized_at_ms, "终态时间")
    after_candidate_id: str | None = None
    finalized_count = 0
    concurrently_finalized_count = 0
    page_count = 0
    while True:
      rows = await self.repository.list_unfinalized(
        strategy_run_id=normalized_run_id,
        after_candidate_id=after_candidate_id,
        limit=self.finalize_page_size,
      )
      if not rows:
        break
      page_count += 1
      for row in rows:
        candidate_id = _required_text(row.candidate_id, "候选标识")
        state = self.repository.state_from_row(row)
        finalize_candidate_outcome(
          state,
          finalized_at_ms=normalized_finalized_at_ms,
        )
        try:
          await self.repository.save(
            state=state,
            expected_version=int(row.state_version),
          )
        except CandidateOutcomeConcurrencyError:
          current = await self.repository.get(
            strategy_run_id=normalized_run_id,
            candidate_id=candidate_id,
          )
          if current is None or _outcome_row_needs_finalization(current):
            raise
          concurrently_finalized_count += 1
        else:
          finalized_count += 1
        after_candidate_id = candidate_id
    return CandidateOutcomeFinalizationResult(
      finalized_count=finalized_count,
      concurrently_finalized_count=concurrently_finalized_count,
      page_count=page_count,
    )


class TTradeCandidateOutcomePersistenceFacade:
  """Open one caller-owned short transaction per analytics fact."""

  def __init__(
    self,
    session_factory=AsyncSessionLocal,
    *,
    repair_page_size: int = _REPAIR_PAGE_SIZE,
    finalize_page_size: int = _FINALIZE_PAGE_SIZE,
  ) -> None:
    self.session_factory = session_factory
    normalized_page_size = int(repair_page_size)
    if not 1 <= normalized_page_size <= _REPAIR_PAGE_SIZE:
      raise ValueError(f"成交修复分页大小必须在 1..{_REPAIR_PAGE_SIZE} 之间")
    self.repair_page_size = normalized_page_size
    normalized_finalize_page_size = int(finalize_page_size)
    if not 1 <= normalized_finalize_page_size <= _FINALIZE_PAGE_SIZE:
      raise ValueError(
        f"候选结果终态分页大小必须在 1..{_FINALIZE_PAGE_SIZE} 之间"
      )
    self.finalize_page_size = normalized_finalize_page_size
    self._repair_cursors: OrderedDict[str, tuple[datetime, str]] = OrderedDict()

  async def seed_material_event(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    event: Mapping[str, Any],
  ) -> CandidateOutcomeState | None:
    async with self.session_factory() as db:
      return await TTradeCandidateOutcomeService(
        TTradeCandidateOutcomeRepository(db)
      ).seed_material_event(
        account_id=account_id,
        strategy_run_id=strategy_run_id,
        event=event,
      )

  async def observe_tick(self, **facts: Any) -> list[CandidateOutcomeState]:
    async with self.session_factory() as db:
      return await TTradeCandidateOutcomeService(
        TTradeCandidateOutcomeRepository(db)
      ).observe_tick(**facts)

  async def reconcile_applied_trade_events(
    self,
    *,
    strategy_run_id: str,
  ) -> CandidateOutcomeReconciliationResult:
    """Repair analytics from durable APPLIED QMT facts without blocking truth.

    The runtime-event inbox remains the execution truth.  This independent,
    idempotent projection advances one bounded page at a time.  A malformed or
    cross-scope event is quarantined in the bounded result and advances the
    cursor, so it cannot starve later authoritative fills.  Database-level
    failures still raise and leave the current event available for retry.
    """

    normalized_run_id = _required_text(strategy_run_id, "策略运行标识")
    async with self.session_factory() as db:
      conditions = [
        StrategyRuntimeEvent.strategy_run_id == normalized_run_id,
      ]
      cursor = self._repair_cursors.get(normalized_run_id)
      if cursor is not None:
        cursor_created_at, cursor_event_id = cursor
        conditions.append(
          or_(
            StrategyRuntimeEvent.created_at > cursor_created_at,
            and_(
              StrategyRuntimeEvent.created_at == cursor_created_at,
              StrategyRuntimeEvent.event_id > cursor_event_id,
            ),
          )
        )
      events = list(
        (
          await db.execute(
            select(StrategyRuntimeEvent)
            .where(*conditions)
            .order_by(
              StrategyRuntimeEvent.created_at,
              StrategyRuntimeEvent.event_id,
            )
            .limit(self.repair_page_size + 1)
          )
        )
        .scalars()
        .all()
      )
      has_more = len(events) > self.repair_page_size
      page = events[: self.repair_page_size]
      repository = TTradeCandidateOutcomeRepository(db)
      service = TTradeCandidateOutcomeService(repository)
      state_samples: OrderedDict[str, CandidateOutcomeState] = OrderedDict()
      issue_counts: Counter[str] = Counter()
      issue_samples: list[CandidateOutcomeRepairIssue] = []
      repaired_count = 0
      idempotent_count = 0
      skipped_count = 0
      quarantined_count = 0
      deferred_count = 0
      examined_count = 0
      for event in page:
        examined_count += 1
        # Scan the complete per-run inbox, not only already-APPLIED TRADE rows.
        # A PENDING/PROCESSING predecessor is a hard cursor barrier.  Therefore
        # an older row that becomes APPLIED later can never be skipped after a
        # cursor has advanced over a newer row.
        if str(event.application_status or "").upper() != "APPLIED":
          deferred_count = 1
          has_more = True
          break
        if str(event.event_type or "").upper() != "TRADE":
          skipped_count += 1
          self._remember_repair_cursor(
            normalized_run_id,
            created_at=event.created_at,
            event_id=str(event.event_id),
          )
          continue
        try:
          outcome = await _repair_applied_trade_event(
            db,
            event=event,
            service=service,
            repository=repository,
            strategy_run_id=normalized_run_id,
          )
          if outcome is None:
            skipped_count += 1
          else:
            state, changed = outcome
            if changed:
              repaired_count += 1
            else:
              idempotent_count += 1
            candidate_id = state.definition.candidate_id
            state_samples[candidate_id] = state
            state_samples.move_to_end(candidate_id)
            while len(state_samples) > _MAX_REPAIR_STATE_SAMPLES:
              state_samples.popitem(last=False)
        except _CandidateOutcomeRepairRejected as exc:
          quarantined_count += 1
          issue_counts[exc.code] += 1
          if len(issue_samples) < _MAX_REPAIR_ISSUE_SAMPLES:
            issue_samples.append(
              CandidateOutcomeRepairIssue(
                event_id=str(event.event_id),
                code=exc.code,
              )
            )
        self._remember_repair_cursor(
          normalized_run_id,
          created_at=event.created_at,
          event_id=str(event.event_id),
        )
      return CandidateOutcomeReconciliationResult(
        states=tuple(state_samples.values()),
        examined_count=examined_count,
        repaired_count=repaired_count,
        idempotent_count=idempotent_count,
        skipped_count=skipped_count,
        quarantined_count=quarantined_count,
        deferred_count=deferred_count,
        issue_counts=tuple(sorted(issue_counts.items())),
        issues=tuple(issue_samples),
        has_more=has_more,
      )

  def _remember_repair_cursor(
    self,
    strategy_run_id: str,
    *,
    created_at: datetime,
    event_id: str,
  ) -> None:
    self._repair_cursors[strategy_run_id] = (created_at, event_id)
    self._repair_cursors.move_to_end(strategy_run_id)
    while len(self._repair_cursors) > _MAX_REPAIR_RUN_CURSORS:
      self._repair_cursors.popitem(last=False)

  async def record_trade_fact(
    self,
    *,
    strategy_run_id: str,
    trade: Any,
    entry_complete: bool | None,
    authoritative_fee: float | None,
    fee_is_authoritative: bool,
    entry_target_volume: int | None = None,
  ) -> CandidateOutcomeState | None:
    async with self.session_factory() as db:
      metadata = dict(getattr(trade, "metadata", {}) or {})
      resolved_entry_complete = entry_complete
      resolved_entry_target_volume = entry_target_volume
      if (
        resolved_entry_complete is None
        and str(metadata.get("t_trade_role") or "").lower() == "entry"
      ):
        intent_id = _optional_text(metadata.get("intent_id"))
        intent = await db.get(TradeIntentRecord, intent_id) if intent_id else None
        candidate_id = _optional_text(metadata.get("candidate_id"))
        repository = TTradeCandidateOutcomeRepository(db)
        outcome_row = (
          await repository.get(
            strategy_run_id=strategy_run_id,
            candidate_id=candidate_id,
          )
          if candidate_id is not None
          else None
        )
        if outcome_row is None:
          return None
        definition = repository.state_from_row(outcome_row).definition
        _validate_repair_intent(
          intent,
          strategy_run_id=strategy_run_id,
          account_id=str(outcome_row.account_id or ""),
          instrument_code=definition.instrument_code,
          candidate_id=definition.candidate_id,
          candidate_fingerprint=definition.candidate_fingerprint,
          role="ENTRY",
          policy_version=definition.policy_version,
        )
        resolved_entry_complete = _authoritative_entry_complete(intent, metadata)
        resolved_entry_target_volume = _authoritative_entry_target_volume(
          intent, metadata
        )
      return await TTradeCandidateOutcomeService(
        TTradeCandidateOutcomeRepository(db)
      ).record_trade_fact(
        strategy_run_id=strategy_run_id,
        trade=trade,
        entry_complete=resolved_entry_complete,
        authoritative_fee=authoritative_fee,
        fee_is_authoritative=fee_is_authoritative,
        entry_target_volume=resolved_entry_target_volume,
      )

  async def finalize_run(
    self,
    *,
    strategy_run_id: str,
    finalized_at_ms: int,
  ) -> CandidateOutcomeFinalizationResult:
    normalized_run_id = _required_text(strategy_run_id, "策略运行标识")
    async with self.session_factory() as db:
      result = await TTradeCandidateOutcomeService(
        TTradeCandidateOutcomeRepository(db),
        finalize_page_size=self.finalize_page_size,
      ).finalize_run(
        strategy_run_id=normalized_run_id,
        finalized_at_ms=finalized_at_ms,
      )
    self._repair_cursors.pop(normalized_run_id, None)
    return result


def _outcome_row_needs_finalization(row: Any) -> bool:
  return str(getattr(row, "status", "") or "").upper() == "OBSERVING" or str(
    getattr(row, "post_fill_status", "") or ""
  ).upper() in {"WAITING_ENTRY", "OBSERVING"}


def _required_text(value: Any, label: str) -> str:
  normalized = str(value or "").strip()
  if not normalized:
    raise ValueError(f"{label}不能为空")
  return normalized


async def _repair_applied_trade_event(
  db: Any,
  *,
  event: StrategyRuntimeEvent,
  service: TTradeCandidateOutcomeService,
  repository: TTradeCandidateOutcomeRepository,
  strategy_run_id: str,
) -> tuple[CandidateOutcomeState, bool] | None:
  payload = _mapping(event.payload)
  metadata = _mapping(payload.get("metadata"))
  report = _mapping(payload.get("report"))
  candidate_id = _optional_text(metadata.get("candidate_id"))
  role = _optional_text(metadata.get("t_trade_role"))
  if candidate_id is None and role is None:
    return None
  _repair_require(
    candidate_id is not None and role is not None,
    "CANDIDATE_LINK_INCOMPLETE",
  )
  normalized_role = str(role).upper()
  _repair_require(
    normalized_role in {"ENTRY", "EXIT"},
    "TRADE_ROLE_INVALID",
  )
  event_instrument = _optional_text(
    report.get("stock_code")
    or report.get("instrument_code")
    or metadata.get("instrument_code")
  )
  _repair_require(event_instrument is not None, "EVENT_INSTRUMENT_MISSING")
  instrument_code = str(event_instrument).strip().upper()
  _repair_require(
    str(event.strategy_run_id or "").strip() == strategy_run_id
    and _optional_text(metadata.get("strategy_run_id")) == strategy_run_id
    and _optional_text(metadata.get("instrument_code")) is not None
    and str(metadata.get("instrument_code") or "").strip().upper() == instrument_code,
    "EVENT_SCOPE_MISMATCH",
  )

  evaluation = (
    await db.execute(
      select(TTradeOpportunityEvaluation)
      .where(
        TTradeOpportunityEvaluation.strategy_run_id == strategy_run_id,
        TTradeOpportunityEvaluation.instrument_code == instrument_code,
        TTradeOpportunityEvaluation.record_kind == T_TRADE_EVALUATION_KIND_MATERIAL,
        TTradeOpportunityEvaluation.candidate_id == candidate_id,
      )
      .order_by(
        TTradeOpportunityEvaluation.evaluated_at,
        TTradeOpportunityEvaluation.id,
      )
      .limit(1)
    )
  ).scalar_one_or_none()
  _repair_require(evaluation is not None, "MATERIAL_EVALUATION_MISSING")
  evaluation_payload = _mapping(evaluation.payload)
  snapshot = _mapping(evaluation_payload.get("signal_snapshot"))
  evaluation_account = _optional_text(evaluation.account_id)
  candidate_fingerprint = _optional_text(snapshot.get("candidate_fingerprint"))
  _repair_require(
    evaluation_account is not None
    and _optional_text(snapshot.get("candidate_id")) == candidate_id
    and candidate_fingerprint is not None,
    "MATERIAL_EVALUATION_INVALID",
  )
  _repair_require(
    _optional_text(metadata.get("account_id")) == evaluation_account
    and _optional_text(metadata.get("candidate_fingerprint")) == candidate_fingerprint
    and _optional_text(metadata.get("policy_version"))
    == _optional_text(snapshot.get("policy_version")),
    "EVENT_EVALUATION_MISMATCH",
  )

  intent_id = _optional_text(metadata.get("intent_id"))
  _repair_require(intent_id is not None, "INTENT_LINK_MISSING")
  intent = await db.get(TradeIntentRecord, intent_id)
  _validate_repair_intent(
    intent,
    strategy_run_id=strategy_run_id,
    account_id=str(evaluation_account),
    instrument_code=instrument_code,
    candidate_id=str(candidate_id),
    candidate_fingerprint=str(candidate_fingerprint),
    role=normalized_role,
    policy_version=_optional_text(snapshot.get("policy_version")),
  )

  try:
    fill_id = _required_text(
      report.get("execution_id") or report.get("traded_id") or event.business_key,
      "成交标识",
    )
    source_time_ms = _strict_report_time_ms(
      report.get("traded_time") or report.get("trade_time")
    )
    price = _positive_float(
      report.get("traded_price") or report.get("price"),
      "成交价格",
    )
    volume = _positive_int(
      report.get("traded_volume") or report.get("volume"),
      "成交数量",
    )
  except (TypeError, ValueError, OverflowError) as exc:
    raise _CandidateOutcomeRepairRejected("TRADE_FACT_INVALID") from exc

  try:
    seeded = await service.seed_material_event(
      account_id=str(evaluation_account),
      strategy_run_id=strategy_run_id,
      event={
        "record_kind": T_TRADE_EVALUATION_KIND_MATERIAL,
        "instrument_code": instrument_code,
        "signal_snapshot": snapshot,
      },
    )
  except (TypeError, ValueError, OverflowError) as exc:
    raise _CandidateOutcomeRepairRejected("MATERIAL_EVALUATION_INVALID") from exc
  _repair_require(seeded is not None, "MATERIAL_EVALUATION_INVALID")
  outcome_row = await repository.get(
    strategy_run_id=strategy_run_id,
    candidate_id=str(candidate_id),
  )
  _repair_require(
    outcome_row is not None
    and str(outcome_row.account_id or "").strip() == evaluation_account
    and str(outcome_row.instrument_code or "").strip().upper() == instrument_code,
    "OUTCOME_SCOPE_MISMATCH",
  )
  before = repository.state_from_row(outcome_row)
  was_applied = fill_id in before.execution.applied_fill_ids
  try:
    state = await service.record_fill(
      strategy_run_id=strategy_run_id,
      candidate_id=str(candidate_id),
      fill_id=fill_id,
      role=normalized_role,
      source_time_ms=source_time_ms,
      price=price,
      volume=volume,
      fee=None,
      entry_complete=_authoritative_entry_complete(intent, metadata),
      entry_target_volume=_authoritative_entry_target_volume(intent, metadata),
    )
  except (TypeError, ValueError, OverflowError) as exc:
    raise _CandidateOutcomeRepairRejected("OUTCOME_FACT_REJECTED") from exc
  if state is None:
    raise RuntimeError("已应用成交事件无法重建候选结果")
  is_applied = fill_id in state.execution.applied_fill_ids
  _repair_require(is_applied, "OUTCOME_FACT_REJECTED")
  return state, bool(is_applied and not was_applied)


def _validate_repair_intent(
  intent: TradeIntentRecord | None,
  *,
  strategy_run_id: str,
  account_id: str,
  instrument_code: str,
  candidate_id: str,
  candidate_fingerprint: str,
  role: str,
  policy_version: str | None,
) -> None:
  _repair_require(intent is not None, "INTENT_NOT_FOUND")
  assert intent is not None
  intent_metadata = _mapping(intent.intent_metadata)
  expected_direction = "BUY" if role == "ENTRY" else "SELL"
  _repair_require(
    str(intent.strategy_run_id or "").strip() == strategy_run_id
    and str(intent.account_id or "").strip() == account_id
    and str(intent.instrument_code or "").strip().upper() == instrument_code
    and str(intent.direction or "").strip().upper() == expected_direction
    and _optional_text(intent_metadata.get("account_id")) == account_id
    and _optional_text(intent_metadata.get("instrument_code")) is not None
    and str(intent_metadata.get("instrument_code") or "").strip().upper()
    == instrument_code
    and _optional_text(intent_metadata.get("candidate_id")) == candidate_id
    and _optional_text(intent_metadata.get("candidate_fingerprint"))
    == candidate_fingerprint
    and str(intent_metadata.get("t_trade_role") or "").strip().upper() == role
    and _optional_text(intent_metadata.get("policy_version")) == policy_version,
    "INTENT_SCOPE_MISMATCH",
  )


def _repair_require(condition: bool, code: str) -> None:
  if not condition:
    raise _CandidateOutcomeRepairRejected(code)


def _mapping(value: Any) -> dict[str, Any]:
  return dict(value) if isinstance(value, Mapping) else {}


def _strict_report_time_ms(value: Any) -> int:
  parsed: datetime | None = None
  if isinstance(value, datetime):
    parsed = value
  elif isinstance(value, (int, float)):
    try:
      parsed = datetime.fromtimestamp(float(value), tz=time_utils.now().tzinfo)
    except (ValueError, OSError, OverflowError):
      parsed = None
  elif isinstance(value, str) and value.strip():
    try:
      parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
      parsed = None
  if parsed is None:
    raise ValueError("候选成交缺少可解析的权威成交时间")
  return int(time_utils.to_utc(parsed).timestamp() * 1000)


def _authoritative_entry_complete(intent: Any, metadata: Mapping[str, Any]) -> bool:
  requested_volume = _authoritative_entry_target_volume(intent, metadata) or 0
  return bool(
    intent is not None
    and str(intent.status or "").upper() == "FILLED"
    and requested_volume > 0
    and int(intent.executed_volume or 0) >= requested_volume
  )


def _authoritative_entry_target_volume(
  intent: Any,
  metadata: Mapping[str, Any],
) -> int | None:
  intent_metadata = dict(getattr(intent, "intent_metadata", {}) or {})
  return _optional_positive_int(
    metadata.get("requested_entry_volume")
    or metadata.get("sized_volume")
    or intent_metadata.get("requested_entry_volume")
    or intent_metadata.get("sized_volume")
    or getattr(intent, "target_volume", None)
  )


def _optional_text(value: Any) -> str | None:
  normalized = str(value or "").strip()
  return normalized or None


def _non_negative_int(value: Any, label: str) -> int:
  try:
    normalized = int(value)
  except (TypeError, ValueError) as exc:
    raise ValueError(f"{label}必须是整数") from exc
  if normalized < 0:
    raise ValueError(f"{label}不得为负数")
  return normalized


def _positive_float(value: Any, label: str) -> float:
  try:
    normalized = float(value)
  except (TypeError, ValueError) as exc:
    raise ValueError(f"{label}必须是数值") from exc
  if normalized <= 0:
    raise ValueError(f"{label}必须大于零")
  return normalized


def _positive_int(value: Any, label: str) -> int:
  normalized = _non_negative_int(value, label)
  if normalized <= 0:
    raise ValueError(f"{label}必须大于零")
  return normalized


def _optional_positive_int(value: Any) -> int | None:
  if value is None:
    return None
  try:
    normalized = int(value)
  except (TypeError, ValueError):
    return None
  return normalized if normalized > 0 else None
