"""A-share multi-instrument positive-T assistant.

The strategy owns causal opportunity and batch state only.  The pure V3
opportunity reducer observes every valid Tick before execution-state blockers
are considered.  Account holdings, legal sizing, T+1 checks, and broker truth
remain owned by the execution/orchestration layer.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from quantx_domain.clock import SHANGHAI
from quantx_domain.enums import (
  StrategyCategory,
  StrategyInstrumentScope,
  StrategyInstrumentUniverseMode,
)
from quantx_domain.schemas import ParameterProperty, ParameterSchema
from quantx_domain.state_schema import StateProperty, StateSchema
from quantx_domain.strategies.base import (
  ManualApprovalRecoveryCandidate,
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
)
from quantx_domain.trading.bucket_ledger import SWING_BUCKET
from quantx_domain.trading.exit_plan import (
  ExitExecutionPolicy,
  ExitPlanCommand,
  ExitPlanCommandType,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleSpec,
  ExitRuleType,
  ExitSizingPolicy,
  ExitT1Policy,
)
from quantx_domain.trading.t_trade import (
  TickSample,
  TradingCostPolicy,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OPPORTUNITY_STATE_SCHEMA_VERSION,
  CandidateControl,
  CandidateStatus,
  DataHealth,
  OpportunityCandidate,
  OpportunityEvaluation,
  OpportunityFeatures,
  OpportunityGateContext,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
  OpportunityState,
  reduce_opportunity,
  transition_candidate,
)

_RUNTIME_STATE_SCHEMA_VERSION = 3
_OPPORTUNITY_EVENT_TYPE = "T_TRADE_OPPORTUNITY_EVALUATION"
_OPPORTUNITY_EVENT_MATERIAL = "MATERIAL"
_OPPORTUNITY_EVENT_DIAGNOSTIC = "COALESCED_DIAGNOSTIC"
_DIAGNOSTIC_COALESCE_MS = 2_000
_PROFILE_CONTEXT_KEY = "t_trade_instrument_profile"
_EMISSION_CONTEXT_KEY = "t_trade_intent_emission"


class TTradeStatus:
  OBSERVING = "OBSERVING"
  AWAITING_APPROVAL = "AWAITING_APPROVAL"
  RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
  ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
  ENTRY_PARTIAL = "ENTRY_PARTIAL"
  MONITORING = "MONITORING"
  PROFIT_ARMED = "PROFIT_ARMED"
  EXIT_TRIGGERED = "EXIT_TRIGGERED"
  EXIT_SUBMITTED = "EXIT_SUBMITTED"
  EXIT_PARTIAL = "EXIT_PARTIAL"
  COOLDOWN = "COOLDOWN"
  DRAINING = "DRAINING"
  ERROR = "ERROR"


class TTradeTimeExitMode:
  UNLIMITED = "UNLIMITED"
  END_OF_DAY = "END_OF_DAY"
  MAX_HOLDING_DAYS = "MAX_HOLDING_DAYS"


class AshareIntradayTAssistantStrategy(StrategyBase):
  """Monitor an account holdings universe in one strategy instance."""

  USES_T_TRADE_OPPORTUNITY_PROFILE = True
  CATEGORY = StrategyCategory.MEAN_REVERSION
  RISK_LEVEL = "medium"
  TAGS = [
    "A股",
    "做T",
    "Tick",
    "回撤反弹",
    "动量加速",
    "人工确认",
    "动态止盈",
    "T+1",
    "动态持仓",
  ]
  INSTRUMENT_SCOPE = StrategyInstrumentScope.MULTI
  INSTRUMENT_UNIVERSE_MODE = StrategyInstrumentUniverseMode.ACCOUNT_HOLDINGS

  @property
  def name(self) -> str:
    return "A股动态持仓做T策略"

  @property
  def version(self) -> str:
    return "3.0.0"

  @property
  def description(self) -> str:
    return (
      "动态监测全部持仓的回撤反弹与早期动量机会，人工确认买入，并按批次净收益自动退出。"
    )

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      additionalProperties=True,
      properties={
        "account_id": ParameterProperty(type="string", default="", group="binding"),
        "target_trade_amount": ParameterProperty(
          type="number",
          minimum=100.0,
          maximum=1_000_000.0,
          default=10_000.0,
          group="sizing",
        ),
        "max_trade_amount": ParameterProperty(
          type="number",
          minimum=100.0,
          maximum=1_000_000.0,
          default=12_000.0,
          group="sizing",
        ),
        "max_concurrent_batches": ParameterProperty(
          type="integer", minimum=1, maximum=20, default=3, group="sizing"
        ),
        "max_total_t_exposure_pct": ParameterProperty(
          type="number", minimum=0.01, maximum=1.0, default=0.1, group="sizing"
        ),
        "signal_policy": ParameterProperty(
          type="object",
          default=OpportunityPolicy().to_dict(),
          group="signal",
        ),
        "max_price_deviation_pct": ParameterProperty(
          type="number", minimum=0.05, maximum=2.0, default=0.3, group="approval"
        ),
        "execution_quote_max_age_seconds": ParameterProperty(
          type="number", minimum=0.1, maximum=30.0, default=3.0, group="approval"
        ),
        "entry_cutoff_time": ParameterProperty(
          type="string", default="14:50", group="risk"
        ),
        "max_exit_slippage_bps": ParameterProperty(
          type="number", minimum=0.0, maximum=200.0, default=30.0, group="risk"
        ),
        "auto_exit_acknowledged": ParameterProperty(
          type="boolean", default=False, group="risk"
        ),
        "target_profit_pct": ParameterProperty(
          type="number", minimum=0.1, maximum=20.0, default=2.0, group="exit"
        ),
        "base_floor_pct": ParameterProperty(
          type="number", minimum=-2.0, maximum=10.0, default=0.5, group="exit"
        ),
        "initial_gap_pct": ParameterProperty(
          type="number", minimum=0.1, maximum=10.0, default=1.5, group="exit"
        ),
        "trailing_gap_slope": ParameterProperty(
          type="number", minimum=0.0, maximum=2.0, default=0.25, group="exit"
        ),
        "max_gap_pct": ParameterProperty(
          type="number", minimum=0.1, maximum=15.0, default=3.0, group="exit"
        ),
        "high_profit_lock_enabled": ParameterProperty(
          type="boolean", default=True, group="exit"
        ),
        "high_profit_arm_pct": ParameterProperty(
          type="number", minimum=0.5, maximum=30.0, default=4.0, group="exit"
        ),
        "high_profit_max_drawdown_pct": ParameterProperty(
          type="number", minimum=0.1, maximum=10.0, default=1.2, group="exit"
        ),
        "rapid_reversal_enabled": ParameterProperty(
          type="boolean", default=True, group="exit"
        ),
        "rapid_reversal_window_seconds": ParameterProperty(
          type="integer", minimum=3, maximum=120, default=15, group="exit"
        ),
        "rapid_reversal_drawdown_pct": ParameterProperty(
          type="number", minimum=0.1, maximum=5.0, default=0.8, group="exit"
        ),
        "rapid_reversal_confirm_ticks": ParameterProperty(
          type="integer", minimum=1, maximum=10, default=2, group="exit"
        ),
        "limit_up_touch_exit_enabled": ParameterProperty(
          type="boolean", default=True, group="exit"
        ),
        "limit_up_touch_tolerance_ticks": ParameterProperty(
          type="integer", minimum=0, maximum=20, default=0, group="exit"
        ),
        "hard_stop_enabled": ParameterProperty(
          type="boolean", default=False, group="risk"
        ),
        "hard_stop_pct": ParameterProperty(
          type="number", minimum=-10.0, maximum=0.0, default=-0.8, group="risk"
        ),
        "time_exit_mode": ParameterProperty(
          type="string", default=TTradeTimeExitMode.UNLIMITED, group="risk"
        ),
        "time_exit_time": ParameterProperty(
          type="string", default="14:50", group="risk"
        ),
        "max_holding_trading_days": ParameterProperty(
          type="integer", minimum=1, maximum=250, default=5, group="risk"
        ),
        "cooldown_seconds": ParameterProperty(
          type="integer", minimum=0, maximum=3600, default=300, group="risk"
        ),
        "commission_rate": ParameterProperty(
          type="number", minimum=0.0, maximum=0.01, default=0.0003, group="cost"
        ),
        "minimum_commission": ParameterProperty(
          type="number", minimum=0.0, maximum=100.0, default=5.0, group="cost"
        ),
        "stamp_tax_rate": ParameterProperty(
          type="number", minimum=0.0, maximum=0.01, default=0.0005, group="cost"
        ),
        "transfer_fee_rate": ParameterProperty(
          type="number", minimum=0.0, maximum=0.01, default=0.00001, group="cost"
        ),
      },
      required=["account_id"],
    )

  @classmethod
  def get_state_schema(cls) -> StateSchema:
    return StateSchema(
      type="object",
      properties={
        "state_schema_version": StateProperty(
          type="integer", default=_RUNTIME_STATE_SCHEMA_VERSION
        ),
        "instrument_states": StateProperty(type="object", default={}),
        "universe_revision": StateProperty(type="integer", default=0),
      },
    )

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": True, "periods": []}

  def apply_state_snapshot(self, state: Optional[Dict[str, Any]]) -> None:
    """Atomically move runtime state to V3 without interpreting legacy signals.

    An incompatible opportunity schema starts from a new WARMING reducer state.
    Only batches backed by report-derived fill projections (and their exit
    correlation) are retained; QMT reports and the external execution chain remain
    authoritative. Legacy unconfirmed entry IDs are exposed once through
    :meth:`invalidated_manual_intent_ids` so the Engine can expire their durable
    TradeIntent records; no compatibility DTO or dual decision path remains.
    """

    snapshot = dict(state or {})
    try:
      schema_version = int(snapshot.get("state_schema_version", 0) or 0)
    except (TypeError, ValueError, OverflowError):
      schema_version = 0
    migrating = schema_version != _RUNTIME_STATE_SCHEMA_VERSION
    raw_states = dict(snapshot.get("instrument_states") or {})
    if not raw_states and snapshot.get("status"):
      code = str((self.context.instruments or [""])[0] or "").strip().upper()
      if code:
        raw_states[code] = snapshot

    invalidated: set[str] = set()
    instrument_states: Dict[str, Dict[str, Any]] = {}
    for raw_code, raw_state in raw_states.items():
      code = str(raw_code or "").strip().upper()
      if not code or not isinstance(raw_state, Mapping):
        continue
      item = dict(raw_state)
      pending_id = str(item.get("pending_entry_intent_id") or "")
      pending_status = str(item.get("entry_order_status") or "").upper()
      entry_filled = max(0, int(item.get("entry_filled_volume", 0) or 0))
      if (
        migrating
        and pending_id
        and (pending_status == "AWAITING_APPROVAL" or entry_filled <= 0)
      ):
        invalidated.add(pending_id)
      instrument_states[code] = self._restore_instrument_state(
        item,
        preserve_opportunity=not migrating,
      )

    self._invalidated_manual_intent_ids_on_restore = sorted(invalidated)
    super().apply_state_snapshot(
      {
        "state_schema_version": _RUNTIME_STATE_SCHEMA_VERSION,
        "instrument_states": instrument_states,
        "universe_revision": int(snapshot.get("universe_revision", 0) or 0),
      }
    )

  def pending_manual_intent_ids(self) -> List[str]:
    pending = []
    for state in self._instrument_states().values():
      intent_id = str(state.get("pending_entry_intent_id", "") or "")
      opportunity = dict(state.get("opportunity") or {})
      if (
        intent_id
        and str(state.get("entry_order_status", "") or "").upper()
        == "AWAITING_APPROVAL"
        and str(opportunity.get("candidate_status") or "").upper()
        == CandidateStatus.AWAITING_APPROVAL.value
        and opportunity.get("candidate_awaiting_approval") is True
      ):
        pending.append(intent_id)
    return pending

  def manual_approval_recovery_candidates(
    self,
  ) -> Optional[List[ManualApprovalRecoveryCandidate]]:
    """Expose durable V3 linkage facts without mutating strategy state."""

    candidates: List[ManualApprovalRecoveryCandidate] = []
    for raw_code, raw_state in self._instrument_states().items():
      code = str(raw_code or "").strip().upper()
      state = dict(raw_state or {})
      opportunity = dict(state.get("opportunity") or {})
      candidate_status = str(
        opportunity.get("candidate_status") or ""
      ).strip().upper()
      if candidate_status not in {
        CandidateStatus.LATCHED.value,
        CandidateStatus.AWAITING_APPROVAL.value,
      }:
        continue
      candidate = dict(opportunity.get("candidate") or {})
      evaluation = dict(opportunity.get("latest_evaluation") or {})
      try:
        candidate_state_version = int(
          evaluation.get("candidate_state_version")
          or opportunity.get("state_version")
          or 0
        )
        source_time_ms = int(
          evaluation.get("source_time_ms")
          or candidate.get("source_time_ms")
          or 0
        )
      except (TypeError, ValueError, OverflowError):
        candidate_state_version = 0
        source_time_ms = 0
      candidates.append(
        ManualApprovalRecoveryCandidate(
          instrument_code=code,
          candidate_id=str(
            candidate.get("candidate_id")
            or evaluation.get("candidate_id")
            or ""
          ).strip(),
          candidate_fingerprint=str(
            candidate.get("fingerprint")
            or evaluation.get("candidate_fingerprint")
            or ""
          ).strip(),
          candidate_state_version=candidate_state_version,
          candidate_status=candidate_status,
          pending_intent_id=str(state.get("pending_entry_intent_id") or "").strip(),
          order_status=str(state.get("entry_order_status") or "").strip().upper(),
          source_time_ms=max(0, source_time_ms),
        )
      )
    return sorted(
      candidates,
      key=lambda item: (
        item.instrument_code,
        item.candidate_id,
        item.pending_intent_id,
      ),
    )

  def invalidated_manual_intent_ids(self) -> List[str]:
    invalidated = set(
      getattr(self, "_invalidated_manual_intent_ids_on_restore", []) or []
    )
    for state in self._instrument_states().values():
      intent_id = str(state.get("pending_entry_intent_id") or "")
      if not intent_id:
        continue
      opportunity = dict(state.get("opportunity") or {})
      candidate = dict(opportunity.get("candidate") or {})
      if (
        str(opportunity.get("candidate_status") or "").upper()
        != CandidateStatus.AWAITING_APPROVAL.value
        or opportunity.get("candidate_awaiting_approval") is not True
        or not candidate
      ):
        invalidated.add(intent_id)
    return sorted(invalidated)

  def invalidate_realtime_market_window(
    self,
    instrument_code: str,
    *,
    reason: str,
  ) -> bool:
    """Durably clear V3 causal state before Engine reopens realtime routing."""

    code = str(instrument_code or "").strip().upper()
    if not code or not self._is_bound_instrument(code):
      return False
    state = self._instrument_state(code)
    previous = dict(state.get("opportunity") or {})
    if not previous:
      return True

    trade_date = str(previous.get("trade_date") or "")
    previous_generation = str(previous.get("continuity_generation") or "unknown")
    reset = OpportunityState.initial(
      instrument_code=code,
      trade_date=trade_date,
    ).to_dict()
    # The marker deliberately differs even when a Hub resync stays in the same
    # transport generation. The first authoritative Tick therefore emits the
    # required CONTINUITY_LOST transition before normal WARMING resumes.
    reset["continuity_generation"] = f"invalidated:{previous_generation}"
    reset.update(
      {
        "feature_schema_version": int(previous.get("feature_schema_version", 0) or 0),
        "policy_version": str(previous.get("policy_version") or ""),
        "config_version": int(previous.get("config_version", 0) or 0),
        "profile_fingerprint": previous.get("profile_fingerprint") or None,
        "state_version": int(previous.get("state_version", 0) or 0) + 1,
      }
    )
    previous_evaluation = dict(previous.get("latest_evaluation") or {})
    if previous_evaluation:
      state_version = int(reset["state_version"])
      previous_evaluation.update(
        {
          "data_health": DataHealth.CONTINUITY_LOST.value,
          "data_health_reasons": [str(reason)],
          "opportunity_score": None,
          "selected_path": None,
          "candidate_status": CandidateStatus.NONE.value,
          "candidate_id": None,
          "candidate_fingerprint": None,
          "candidate_created_at_ms": None,
          "candidate_expires_at_ms": None,
          "pending_entry_intent_id": None,
          "signal_version": state_version,
          "candidate_state_version": state_version,
          "blockers": [str(reason)],
          "top_blockers": [str(reason)],
          "score_contributions": [],
        }
      )
      reset["latest_evaluation"] = previous_evaluation
    state["opportunity"] = reset
    patch = self._patch_instrument_state(code, state)
    self.state.update(patch.set)
    return True

  def validate_manual_approval(
    self,
    intent: TradeIntent,
    market_data: Any,
  ) -> Optional[tuple[str, str]]:
    """Fail closed unless the latest V3 candidate passes causal revalidation."""

    metadata = dict(intent.metadata or {})
    if (
      intent.direction != TradeIntentDirection.BUY
      or intent.execution_mode != TradeIntentExecutionMode.MANUAL_CONFIRM
      or str(metadata.get("t_trade_role") or "").lower() != "entry"
    ):
      return None
    code = str(intent.instrument_code or "").strip().upper()
    state = self._instrument_state(code)
    opportunity = dict(state.get("opportunity") or {})
    candidate = dict(opportunity.get("candidate") or {})
    evaluation = dict(opportunity.get("latest_evaluation") or {})
    if (
      str(state.get("pending_entry_intent_id") or "") != intent.intent_id
      or str(state.get("entry_order_status") or "").upper() != "AWAITING_APPROVAL"
      or str(opportunity.get("candidate_status") or "").upper()
      != CandidateStatus.AWAITING_APPROVAL.value
      or opportunity.get("candidate_awaiting_approval") is not True
    ):
      return (
        "T_TRADE_CANDIDATE_NOT_AWAITING_APPROVAL",
        "该意图已不是当前等待确认的机会候选，请刷新后重试",
      )

    candidate_path = str(candidate.get("path") or "").upper()
    path_key = (
      "pullback"
      if candidate_path == "PULLBACK_REBOUND"
      else "momentum"
      if candidate_path == "MOMENTUM_ACCELERATION"
      else ""
    )
    path_evaluation = evaluation.get(path_key) if path_key else None
    if (
      not path_key
      or str(evaluation.get("selected_path") or "").upper() != candidate_path
      or not isinstance(path_evaluation, Mapping)
    ):
      return (
        "T_TRADE_CANDIDATE_NOT_LATEST",
        "当前评估与候选路径不一致，请刷新后重试",
      )

    try:
      expected = {
        "candidate_id": str(metadata.get("candidate_id") or ""),
        "fingerprint": str(metadata.get("candidate_fingerprint") or ""),
        "state_version": int(metadata.get("candidate_state_version", 0) or 0),
        "config_version": int(metadata.get("config_version", 0) or 0),
        "policy_version": str(metadata.get("policy_version") or ""),
      }
      current_state_version = int(opportunity.get("state_version", 0) or 0)
      current_config_version = int(opportunity.get("config_version", 0) or 0)
    except (TypeError, ValueError, OverflowError):
      return (
        "T_TRADE_CANDIDATE_METADATA_INVALID",
        "待确认意图的机会身份信息无效，请刷新后重试",
      )
    if (
      str(candidate.get("candidate_id") or "") != expected["candidate_id"]
      or str(candidate.get("fingerprint") or "") != expected["fingerprint"]
      or current_state_version != expected["state_version"]
      or current_config_version != expected["config_version"]
      or str(opportunity.get("policy_version") or "") != expected["policy_version"]
    ):
      return (
        "T_TRADE_CANDIDATE_NOT_LATEST",
        "待确认意图与当前最新机会候选不一致，请刷新后重试",
      )

    if str(evaluation.get("data_health") or "") != DataHealth.READY.value:
      return (
        "T_TRADE_DATA_HEALTH_NOT_READY",
        "当前行情数据健康状态不是 READY，候选已保守失效",
      )
    try:
      score = float(path_evaluation.get("score"))
      revalidate = float(opportunity.get("revalidate_score"))
    except (TypeError, ValueError, OverflowError):
      return (
        "T_TRADE_REVALIDATE_SCORE_UNAVAILABLE",
        "当前机会分或重验阈值不可用，候选已保守失效",
      )
    if score < revalidate:
      return (
        "T_TRADE_REVALIDATE_SCORE_BELOW_FLOOR",
        "当前机会分已低于人工确认重验阈值",
      )
    gates = path_evaluation.get("hard_gates")
    if (
      not isinstance(gates, list)
      or not gates
      or any(
        not isinstance(item, Mapping) or item.get("passed") is not True
        for item in gates
      )
    ):
      return (
        "T_TRADE_HARD_GATE_BLOCKED",
        "当前机会硬门禁未全部通过",
      )
    path_blockers = path_evaluation.get("blockers")
    if not isinstance(path_blockers, list):
      return (
        "T_TRADE_REVALIDATION_BLOCKED",
        "当前机会阻断信息不可用，不能确认",
      )
    blockers = self._filtered_candidate_blockers(
      path_evaluation,
      CandidateStatus.AWAITING_APPROVAL,
    )
    external_blockers = [
      str(item) for item in list(evaluation.get("external_blockers") or []) if str(item)
    ]
    blockers.extend(external_blockers)
    if blockers:
      return (
        "T_TRADE_REVALIDATION_BLOCKED",
        "当前机会存在阻断项，不能确认",
      )
    expires_at_ms = int(candidate.get("expires_at_ms", 0) or 0)
    quote_time = getattr(market_data, "timestamp", None)
    if expires_at_ms <= 0 or (
      isinstance(quote_time, datetime)
      and int(quote_time.timestamp() * 1000) >= expires_at_ms
    ):
      return ("T_TRADE_CANDIDATE_EXPIRED", "机会候选已过期")
    return None

  async def on_init(self) -> None:
    # Universe eligibility is an input fact, never durable algorithm state.
    # A fresh runtime therefore remains fail-closed until its first RECONCILE.
    self._emission_context_by_instrument: Dict[str, Dict[str, Any]] = {}

  async def on_stop(self) -> None:
    return None

  def mark_candidate_awaiting_approval(
    self,
    instrument_code: str,
    candidate_id: str,
    intent_id: str,
    *,
    source_time_ms: int,
  ) -> RuntimeStatePatch:
    """Link a durably-created manual intent to its exact latched candidate.

    The Engine calls this only after TradeIntent persistence succeeds.  A retry
    with the same candidate/intent is idempotent; every mismatch fails closed.
    """

    code = str(instrument_code or "").strip().upper()
    normalized_candidate_id = str(candidate_id or "").strip()
    normalized_intent_id = str(intent_id or "").strip()
    if not code or not normalized_candidate_id or not normalized_intent_id:
      raise ValueError("candidate approval linkage requires complete identity")
    state = self._instrument_state(code)
    opportunity = dict(state.get("opportunity") or {})
    try:
      reducer_state = OpportunityState.from_dict(opportunity)
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("candidate opportunity state is unavailable") from exc
    candidate = reducer_state.candidate
    if candidate is None or candidate.candidate_id != normalized_candidate_id:
      raise ValueError("candidate does not match current opportunity state")
    if reducer_state.candidate_status == CandidateStatus.AWAITING_APPROVAL:
      if (
        str(state.get("pending_entry_intent_id") or "") == normalized_intent_id
        and str(state.get("entry_order_status") or "").upper() == "AWAITING_APPROVAL"
      ):
        return self._patch_instrument_state(code, state)
      raise ValueError("candidate is already linked to another intent")
    if reducer_state.candidate_status != CandidateStatus.LATCHED:
      raise ValueError("candidate is not in LATCHED state")

    transitioned = transition_candidate(
      reducer_state,
      CandidateControl(
        awaiting_approval_candidate_id=normalized_candidate_id,
      ),
      source_time_ms=int(source_time_ms),
    )
    if transitioned.candidate_status != CandidateStatus.AWAITING_APPROVAL:
      raise ValueError("candidate expired before approval intent linkage")
    state_version = int(opportunity.get("state_version", 0) or 0) + 1
    evaluation = dict(opportunity.get("latest_evaluation") or {})
    evaluation.update(
      {
        "candidate_status": CandidateStatus.AWAITING_APPROVAL.value,
        "candidate_id": candidate.candidate_id,
        "candidate_fingerprint": candidate.fingerprint,
        "candidate_state_version": state_version,
        "signal_version": state_version,
        "pending_entry_intent_id": normalized_intent_id,
      }
    )
    evaluation["blockers"] = self._filtered_candidate_blockers(
      evaluation,
      CandidateStatus.AWAITING_APPROVAL,
    )
    evaluation["top_blockers"] = list(evaluation["blockers"])
    opportunity.update(transitioned.to_dict())
    opportunity.update(
      {
        "state_version": state_version,
        "latest_evaluation": evaluation,
      }
    )
    projection = self._material_projection(
      opportunity,
      list(evaluation.get("external_blockers") or []),
    )
    signature = self._stable_fingerprint(projection)
    opportunity["event_cursor"] = {
      "material_signature": signature,
      "last_emitted_source_time_ms": int(source_time_ms),
      "diagnostic_window_started_at_ms": int(source_time_ms),
      "coalesced_count": 0,
    }
    state.update(
      {
        "status": TTradeStatus.AWAITING_APPROVAL,
        "pending_entry_intent_id": normalized_intent_id,
        "entry_order_status": "AWAITING_APPROVAL",
        "opportunity": opportunity,
      }
    )
    event_key = self._stable_fingerprint(
      {
        "run_id": self.context.run_id,
        "instrument_code": code,
        "candidate_id": candidate.candidate_id,
        "intent_id": normalized_intent_id,
        "candidate_state_version": state_version,
        "transition": "AWAITING_APPROVAL",
      }
    )
    event = {
      "type": _OPPORTUNITY_EVENT_TYPE,
      "event_key": f"tto:{event_key}",
      "record_kind": _OPPORTUNITY_EVENT_MATERIAL,
      "event_type": "INTENT_LINKED",
      "instrument_code": code,
      "evaluated_at_ms": int(source_time_ms),
      "signal_snapshot": evaluation,
      "intent_link": {
        "candidate_id": candidate.candidate_id,
        "candidate_fingerprint": candidate.fingerprint,
        "intent_id": normalized_intent_id,
      },
      "external_blockers": list(evaluation.get("external_blockers") or []),
      "metrics": {"opportunity_score": evaluation.get("opportunity_score")},
    }
    return self._apply_callback_state(code, state, append_events=[event])

  def import_external_entry(
    self,
    instrument_code: str,
    volume: int,
    price: float,
    source_trade_id: str,
  ) -> RuntimeStatePatch:
    """Register a user-declared external buy fill as an auditable T batch."""

    code = str(instrument_code or "").strip().upper()
    if not code or not self._is_bound_instrument(code):
      raise ValueError("该股票不在当前做 T 监控范围内")
    if volume <= 0 or volume % 100 != 0:
      raise ValueError("外部买入数量必须是大于 0 的 100 股整数倍")
    if price <= 0:
      raise ValueError("外部买入成交均价必须大于 0")
    trade_id = str(source_trade_id or "").strip()
    if not trade_id:
      raise ValueError("成交编号不能为空")
    imported_ids = {
      str(event.get("source_trade_id", "") or "")
      for event in list(self.state.get("runtime_events", []) or [])
      if isinstance(event, dict)
      and event.get("type") == "T_TRADE_EXTERNAL_ENTRY_IMPORTED"
    }
    if trade_id in imported_ids:
      raise ValueError("该笔成交已经加入做 T 助手")

    state = self._instrument_state(code)
    if self._active_volume(state) > 0:
      raise ValueError("该股票已有未完成的 T 批次")
    if self._has_pending_intent(state):
      raise ValueError("该股票仍有待处理委托，不能导入外部成交")

    batch_id = str(uuid.uuid4())
    exit_plan_id = f"t-exit-{batch_id}"
    exit_policy = self._exit_policy_snapshot()
    state.update(
      {
        "status": TTradeStatus.MONITORING,
        "pending_entry_intent_id": "",
        "pending_exit_intent_id": "",
        "entry_order_status": "EXTERNAL_FILLED",
        "requested_entry_amount": 0.0,
        "exit_order_status": "",
        "entry_pending_fill_base": 0,
        "exit_pending_fill_base": 0,
        "entry_filled_volume": int(volume),
        "entry_avg_price": float(price),
        "exit_filled_volume": 0,
        "exit_avg_price": 0.0,
        "peak_net_profit_pct": 0.0,
        "trailing_floor_pct": -999.0,
        "profit_armed": False,
        "last_exit_reason": "",
        "batch_id": batch_id,
        "exit_plan_id": exit_plan_id,
        "batch_started_trade_date": "",
        "last_holding_trade_date": "",
        "holding_trading_days": 0,
        "exit_policy_snapshot": exit_policy,
      }
    )
    states = self._instrument_states()
    states[code] = dict(state)
    return RuntimeStatePatch(
      set={"instrument_states": states},
      append_events=[
        {
          "type": "T_TRADE_EXTERNAL_ENTRY_IMPORTED",
          "instrument_code": code,
          "batch_id": batch_id,
          "volume": int(volume),
          "price": float(price),
          "source_trade_id": trade_id,
        }
      ],
    )

  async def step(self, input: StrategyInput) -> StrategyOutput:
    if input.cadence == StrategyCadence.RECONCILE:
      return self._reconcile_universe(input)
    if input.cadence != StrategyCadence.TICK:
      return StrategyOutput()
    code = str(input.instrument_code or "").strip().upper()
    if not self._is_bound_instrument(code):
      return StrategyOutput(
        decision_tags=["instrument_mismatch", "no_trade"],
        trace_payload={"reason": "INSTRUMENT_NOT_IN_HOLDINGS_UNIVERSE"},
      )

    opportunity_sample = self._opportunity_sample(input)
    if opportunity_sample is None:
      return StrategyOutput(decision_tags=["invalid_tick", "no_trade"])
    state = self._instrument_state(code)
    previous_opportunity = dict(state.get("opportunity") or {})
    policy = self._opportunity_policy()
    profile, profile_fingerprint = self._reference_profile(input)
    reducer_state = self._opportunity_state_for_input(
      previous_opportunity,
      instrument_code=code,
      trade_date=opportunity_sample.trade_date,
      policy=policy,
      config_version=self._config_version(),
      profile_fingerprint=profile_fingerprint,
    )
    previous_candidate_lifecycle = self._candidate_lifecycle(reducer_state)
    if previous_opportunity:
      try:
        persisted_reducer_state = OpportunityState.from_dict(previous_opportunity)
      except (TypeError, ValueError, OverflowError):
        pass
      else:
        previous_candidate_lifecycle = self._candidate_lifecycle(
          persisted_reducer_state
        )
    reduction = reduce_opportunity(
      reducer_state,
      opportunity_sample,
      gate_context=OpportunityGateContext(
        continuous_session=input.market_data_context.session.is_continuous,
        quote_stale=bool(input.market_data_context.quote_stale),
        session_code=input.market_data_context.session.value,
        local_second_of_day=self._source_local_second_of_day(input.timestamp),
      ),
      policy=policy,
      reference_profile=profile,
    )
    if reduction.ignored:
      generation, source_time_ms, tick_ordinal = opportunity_sample.source_identity
      # Duplicate/out-of-order observations are retained as a bounded
      # in-process trace only. They must not create a RuntimeStatePatch: the
      # Engine would otherwise checkpoint a repeated evaluation and enqueue a
      # diagnostic/material event despite the domain state being unchanged.
      return StrategyOutput(
        decision_tags=["opportunity_tick_ignored", "no_trade"],
        trace_payload={
          "reason": reduction.ignored_reason or "SOURCE_IDENTITY_IGNORED",
          "accepted": reduction.accepted,
          "ignored": True,
          "source_identity": {
            "continuity_generation": generation,
            "source_time_ms": source_time_ms,
            "tick_ordinal": tick_ordinal,
          },
        },
      )
    candidate_state_version = int(previous_opportunity.get("state_version", 0) or 0)
    if previous_candidate_lifecycle != self._candidate_lifecycle(reduction.state):
      candidate_state_version += 1

    external_blockers = self._intent_emission_blockers(input, state)
    reduced_state = reduction.state
    candidate_created = reduction.candidate_created
    intent: Optional[TradeIntent] = None
    if candidate_created is not None and external_blockers:
      suppressed = transition_candidate(
        reduced_state,
        CandidateControl(suppress_candidate_id=candidate_created.candidate_id),
        source_time_ms=opportunity_sample.source_time_ms,
      )
      if self._candidate_lifecycle(suppressed) != self._candidate_lifecycle(
        reduced_state
      ):
        candidate_state_version += 1
      reduced_state = suppressed

    predicted_awaiting_version = candidate_state_version + 1
    if candidate_created is not None and not external_blockers:
      intent = self._build_candidate_intent(
        input,
        state,
        candidate_created,
        policy=policy,
        candidate_state_version=predicted_awaiting_version,
        profile_fingerprint=profile_fingerprint,
      )

    evaluation = self._evaluation_snapshot(
      reduction.evaluation,
      reduced_state,
      policy=policy,
      candidate_state_version=candidate_state_version,
      config_version=self._config_version(),
      profile_fingerprint=profile_fingerprint,
      external_blockers=external_blockers,
      pending_entry_intent_id=str(state.get("pending_entry_intent_id") or ""),
    )
    opportunity = self._opportunity_payload(
      reduced_state,
      evaluation=evaluation,
      policy=policy,
      candidate_state_version=candidate_state_version,
      config_version=self._config_version(),
      profile_fingerprint=profile_fingerprint,
      previous=previous_opportunity,
    )
    events = self._opportunity_events(
      input,
      previous_opportunity=previous_opportunity,
      opportunity=opportunity,
      external_blockers=external_blockers,
      candidate_created=candidate_created,
    )
    state.update(
      {
        "opportunity": opportunity,
        "last_price": opportunity_sample.price,
      }
    )
    exit_sample = self._exit_tick_sample(opportunity_sample)
    active_volume = self._active_volume(state)
    if active_volume > 0:
      return self._monitor_open_lot(
        input,
        exit_sample,
        state,
        active_volume,
        opportunity_events=events,
      )
    if intent is not None:
      return StrategyOutput(
        trade_intents=[intent],
        runtime_state_patch=self._patch_instrument_state(
          code,
          state,
          append_events=events,
        ),
        decision_tags=[
          "t_trade_opportunity_candidate",
          "manual_confirmation_required",
        ],
        trace_payload={
          "reason": "T_TRADE_OPPORTUNITY_CANDIDATE_LATCHED",
          "candidate_id": candidate_created.candidate_id,
          "candidate_fingerprint": candidate_created.fingerprint,
        },
      )
    reason = self._entry_observation_reason(evaluation, external_blockers)
    state["status"] = (
      TTradeStatus.COOLDOWN
      if opportunity_sample.source_time_ms < int(state.get("cooldown_until_ms", 0) or 0)
      else TTradeStatus.OBSERVING
    )
    return self._state_output(
      code,
      state,
      tags=["opportunity_observed", "no_trade"],
      reason=reason,
      trace={"signal_snapshot": evaluation},
      append_events=events,
    )

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    role = str(event.metadata.get("t_trade_role", "") or "")
    intent_id = str(event.metadata.get("intent_id", "") or "")
    code = self._event_instrument_code(event, intent_id)
    status = str(event.status or "").upper()
    if role not in {"entry", "exit"} or not code:
      return None

    state = self._instrument_state(code)
    callback_events: List[Dict[str, Any]] = []
    pending_key = f"pending_{role}_intent_id"
    current_pending_intent_id = str(state.get(pending_key, "") or "")
    if intent_id and intent_id != current_pending_intent_id:
      candidate_id = str(event.metadata.get("candidate_id", "") or "")
      opportunity = dict(state.get("opportunity") or {})
      current_candidate = dict(opportunity.get("candidate") or {})
      is_exact_latched_entry_compensation = (
        role == "entry"
        and not current_pending_intent_id
        and status in {"REJECTED", "EXPIRED"}
        and bool(candidate_id)
        and candidate_id == str(current_candidate.get("candidate_id", "") or "")
        and str(opportunity.get("candidate_status", "") or "")
        == CandidateStatus.LATCHED.value
      )
      event_exit_plan_id = str(event.metadata.get("exit_plan_id", "") or "")
      state_exit_plan_id = str(state.get("exit_plan_id", "") or "")
      if is_exact_latched_entry_compensation:
        # The Engine emits this synthetic terminal callback only when the
        # candidate state checkpoint succeeded but its first durable
        # evaluation or TradeIntent write failed.  There is deliberately no
        # pending intent to correlate yet, so the immutable candidate identity
        # is the sole safe compensation key.  Unrelated/late callbacks remain
        # ignored by the normal mismatch branch below.
        pass
      elif (
        role == "exit"
        and not current_pending_intent_id
        and event_exit_plan_id
        and event_exit_plan_id == state_exit_plan_id
      ):
        # Exit intents are emitted by the executor's exit-plan engine, so the
        # first broker report can beat the next strategy projection. The stable
        # plan correlation safely installs that pending intent before applying
        # the report; an unrelated report remains ignored.
        state[pending_key] = intent_id
        state["exit_pending_fill_base"] = int(state.get("exit_filled_volume", 0) or 0)
      else:
        return None

    previous_role_status = str(state.get(f"{role}_order_status", "") or "").upper()
    terminal = status in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}
    current_fill = max(0, int(state.get(f"{role}_filled_volume", 0) or 0))
    reported_fill = max(0, int(event.filled_volume or 0))
    request_volume = max(
      0,
      int(getattr(event.request, "volume", 0) or 0),
    )
    fill_base = max(
      0,
      int(state.get(f"{role}_pending_fill_base", 0) or 0),
    )
    expected_fill = fill_base + reported_fill
    if status == "FILLED" and reported_fill <= 0:
      expected_fill = (
        fill_base + request_volume if request_volume > 0 else current_fill + 1
      )
    if terminal and expected_fill > current_fill:
      state.update(
        {
          f"{role}_order_status": "RECONCILE_REQUIRED",
          f"{role}_terminal_order_status": status,
          f"{role}_expected_fill_volume": expected_fill,
          "status": TTradeStatus.RECONCILE_REQUIRED,
          "reconciliation_reason": (f"AWAITING_{role.upper()}_EXECUTION_REPORT"),
        }
      )
      return self._apply_callback_state(code, state)

    state[f"{role}_order_status"] = status
    if status == "RECONCILE_REQUIRED":
      state["reconciliation_reason"] = (
        str(event.metadata.get("approval_reason", "") or "")
        or "DURABLE_ORDER_OUTCOME_INDETERMINATE"
      )
    elif role == "entry" or previous_role_status == "RECONCILE_REQUIRED":
      state["reconciliation_reason"] = ""
    if terminal:
      state[pending_key] = ""
      state[f"{role}_terminal_order_status"] = ""
      state[f"{role}_expected_fill_volume"] = 0
      state[f"{role}_pending_fill_base"] = 0
      if role == "entry":
        state["requested_entry_amount"] = 0.0

    if role == "entry":
      if status == "PARTIAL_FILLED":
        state["status"] = TTradeStatus.ENTRY_PARTIAL
      elif status in {"PENDING", "SUBMITTED", "ACCEPTED"}:
        state["status"] = TTradeStatus.ENTRY_SUBMITTED
      elif status == "RECONCILE_REQUIRED":
        state["status"] = TTradeStatus.RECONCILE_REQUIRED
      elif status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        suppression_event = self._suppress_current_candidate(
          code,
          state,
          source_time_ms=self._order_event_source_time_ms(event, state),
          reason=str(event.metadata.get("approval_reason") or status),
          intent_id=intent_id,
        )
        if suppression_event is not None:
          callback_events.append(suppression_event)
        if self._active_volume(state) > 0:
          state["status"] = TTradeStatus.MONITORING
        else:
          state.update(
            {
              "status": TTradeStatus.OBSERVING,
              "requested_entry_amount": 0.0,
              "batch_id": "",
              "exit_plan_id": "",
              "exit_policy_snapshot": {},
            }
          )
      elif status == "FILLED":
        state["status"] = TTradeStatus.MONITORING
    else:
      if status == "PARTIAL_FILLED":
        state["status"] = TTradeStatus.EXIT_PARTIAL
      elif status in {"PENDING", "SUBMITTED", "ACCEPTED"}:
        state["status"] = TTradeStatus.EXIT_SUBMITTED
      elif status == "RECONCILE_REQUIRED":
        state["status"] = TTradeStatus.RECONCILE_REQUIRED
      elif status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        state["status"] = TTradeStatus.MONITORING
      elif status == "FILLED" and self._active_volume(state) <= 0:
        state["status"] = TTradeStatus.COOLDOWN

    return self._apply_callback_state(code, state, append_events=callback_events)

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    metadata = dict(event.metadata or {})
    role = str(metadata.get("t_trade_role", "") or "")
    code = str(event.instrument_code or metadata.get("instrument_code", "") or "")
    if (
      role not in {"entry", "exit"} or not code or event.volume <= 0 or event.price <= 0
    ):
      return None

    state = self._instrument_state(code)
    if role == "entry":
      batch_id = str(metadata.get("t_batch_id") or metadata.get("batch_id") or "")
      exit_plan_template = metadata.get("exit_plan_template")
      template_plan_id = (
        str(exit_plan_template.get("plan_id") or "")
        if isinstance(exit_plan_template, dict)
        else ""
      )
      exit_plan_id = str(metadata.get("exit_plan_id") or template_plan_id or "")
      if batch_id and not state.get("batch_id"):
        state["batch_id"] = batch_id
      if exit_plan_id and not state.get("exit_plan_id"):
        state["exit_plan_id"] = exit_plan_id
    volume_key = f"{role}_filled_volume"
    price_key = f"{role}_avg_price"
    previous_volume = int(state.get(volume_key, 0) or 0)
    previous_price = float(state.get(price_key, 0.0) or 0.0)
    total_volume = previous_volume + int(event.volume)
    average_price = (
      previous_price * previous_volume + float(event.price) * int(event.volume)
    ) / total_volume
    state[volume_key] = total_volume
    state[price_key] = average_price

    expected_fill = max(
      0,
      int(state.get(f"{role}_expected_fill_volume", 0) or 0),
    )
    terminal_status = str(state.get(f"{role}_terminal_order_status", "") or "").upper()
    awaiting_execution_report = expected_fill > 0 and total_volume < expected_fill

    if role == "entry":
      if not state.get("exit_policy_snapshot"):
        state["exit_policy_snapshot"] = self._exit_policy_snapshot()
      if not state.get("batch_started_trade_date"):
        trade_time = event.trade_time or self.context.current_time
        if trade_time:
          trade_date = trade_time.date().isoformat()
          state["batch_started_trade_date"] = trade_date
          state["last_holding_trade_date"] = trade_date
          state["holding_trading_days"] = 1
      state["status"] = (
        TTradeStatus.RECONCILE_REQUIRED
        if awaiting_execution_report
        else TTradeStatus.MONITORING
      )
    else:
      if awaiting_execution_report:
        state["status"] = TTradeStatus.RECONCILE_REQUIRED
      elif self._active_volume(state) <= 0:
        policy = dict(state.get("exit_policy_snapshot") or {})
        cooldown_ms = (
          int(
            policy.get("cooldown_seconds", self.get_parameter("cooldown_seconds", 300))
          )
          * 1000
        )
        trade_time = event.trade_time or self.context.current_time
        now_ms = int(trade_time.timestamp() * 1000) if trade_time else 0
        state.update(
          {
            "status": TTradeStatus.COOLDOWN,
            "pending_exit_intent_id": "",
            "cooldown_until_ms": now_ms + cooldown_ms,
            "completed_cycles": int(state.get("completed_cycles", 0) or 0) + 1,
            "batch_id": "",
            "batch_started_trade_date": "",
            "last_holding_trade_date": "",
            "holding_trading_days": 0,
            "exit_policy_snapshot": {},
            "requested_entry_amount": 0.0,
          }
        )
      else:
        state["status"] = TTradeStatus.EXIT_PARTIAL

    if expected_fill > 0:
      if awaiting_execution_report:
        state[f"{role}_order_status"] = "RECONCILE_REQUIRED"
        state["status"] = TTradeStatus.RECONCILE_REQUIRED
        state["reconciliation_reason"] = f"AWAITING_{role.upper()}_EXECUTION_REPORT"
      else:
        state[f"pending_{role}_intent_id"] = ""
        state[f"{role}_order_status"] = terminal_status
        state[f"{role}_terminal_order_status"] = ""
        state[f"{role}_expected_fill_volume"] = 0
        state[f"{role}_pending_fill_base"] = 0
        state["reconciliation_reason"] = ""
        if role == "entry":
          state["requested_entry_amount"] = 0.0
        if role == "exit" and terminal_status in {
          "REJECTED",
          "CANCELLED",
          "EXPIRED",
        }:
          if self._active_volume(state) > 0:
            state["status"] = TTradeStatus.MONITORING

    return self._apply_callback_state(code, state)

  def _reconcile_universe(self, input: StrategyInput) -> StrategyOutput:
    event = dict(input.event or {})
    policy_changed = bool(
      event.get("policy_changed") or event.get("configuration_changed")
    )
    desired = [str(code or "").upper() for code in event.get("instruments", []) if code]
    metadata = {
      str(code or "").upper(): dict(value or {})
      for code, value in dict(event.get("instrument_metadata") or {}).items()
    }
    states = self._instrument_states()
    emission_by_instrument = getattr(
      self,
      "_emission_context_by_instrument",
      {},
    )
    opportunity_events: List[Dict[str, Any]] = []
    rewarm_identity = self._policy_rewarm_identity() if policy_changed else None

    for code in desired:
      state = dict(states.get(code) or self._empty_instrument_state())
      previous_opportunity = dict(state.get("opportunity") or {})
      if (
        policy_changed
        and previous_opportunity.get("last_policy_rewarm_identity") != rewarm_identity
      ):
        state = self._restore_instrument_state(
          state,
          preserve_opportunity=False,
        )
        opportunity, audit_event = self._policy_changed_opportunity(
          input,
          code,
          previous_opportunity=previous_opportunity,
          rewarm_identity=str(rewarm_identity),
        )
        state["opportunity"] = opportunity
        opportunity_events.append(audit_event)
      item = metadata.get(code)
      if item is not None:
        state["draining"] = bool(item.get("draining", False))
        blockers = [
          str(value) for value in list(item.get("blockers") or []) if str(value)
        ]
        eligible = bool(item.get("eligible", False)) and not state["draining"]
        if not eligible and not blockers:
          blockers.append(str(item.get("reason") or "POSITION_NOT_ELIGIBLE"))
        emission_by_instrument[code] = {
          "allowed": eligible,
          "blockers": blockers,
        }
      states[code] = state

    for code in list(states):
      if code in desired:
        continue
      state = dict(states[code])
      emission_by_instrument.pop(code, None)
      if self._active_volume(state) > 0 or self._has_pending_intent(state):
        state.update(
          {
            "draining": True,
            "status": TTradeStatus.DRAINING,
          }
        )
        states[code] = state
      else:
        states.pop(code, None)
    self._emission_context_by_instrument = emission_by_instrument

    return StrategyOutput(
      runtime_state_patch=RuntimeStatePatch(
        set={
          "instrument_states": states,
          "universe_revision": int(self.state.get("universe_revision", 0) or 0) + 1,
        },
        append_events=opportunity_events,
      ),
      decision_tags=[
        "holdings_universe_reconciled",
        *(["opportunity_policy_rewarmed"] if policy_changed else []),
      ],
      trace_payload={
        "reason": (
          "T_TRADE_POLICY_CHANGED_REWARMED"
          if policy_changed
          else "ACCOUNT_HOLDINGS_UNIVERSE_RECONCILED"
        ),
        "added": list(event.get("added") or []),
        "removed": list(event.get("removed") or []),
        "instrument_count": len(desired),
        "rewarmed_instrument_count": len(opportunity_events),
      },
    )

  def _policy_changed_opportunity(
    self,
    input: StrategyInput,
    instrument_code: str,
    *,
    previous_opportunity: Mapping[str, Any],
    rewarm_identity: str,
  ) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Create an auditable WARMING state without inventing a market sample."""

    policy = self._opportunity_policy()
    evaluated_at_ms = max(1, int(input.timestamp.timestamp() * 1000))
    state_version = int(previous_opportunity.get("state_version", 0) or 0) + 1
    profile_fingerprint = previous_opportunity.get("profile_fingerprint") or None
    blocker = "POLICY_CHANGED_REWARMING"
    evaluation: Dict[str, Any] = {
      "instrument_code": instrument_code,
      "trade_date": None,
      "evaluated_at_ms": evaluated_at_ms,
      "source_time_ms": None,
      "tick_ordinal": None,
      "continuity_generation": None,
      "data_health": DataHealth.WARMING.value,
      "data_health_reasons": [blocker],
      "features": OpportunityFeatures().to_dict(),
      "pullback": {
        "path": "PULLBACK_REBOUND",
        "phase": "OBSERVING",
        "score": None,
        "preview": False,
        "candidate_ready": False,
        "components": [],
        "hard_gates": [],
        "blockers": [blocker],
      },
      "momentum": {
        "path": "MOMENTUM_ACCELERATION",
        "phase": "BASELINING",
        "score": None,
        "preview": False,
        "candidate_ready": False,
        "components": [],
        "hard_gates": [],
        "blockers": [blocker],
      },
      "selected_path": "NONE",
      "opportunity_score": None,
      "hard_gates": [],
      "blockers": [blocker],
      "top_blockers": [blocker],
      "candidate_status": CandidateStatus.NONE.value,
      "candidate_id": None,
      "candidate_fingerprint": None,
      "candidate_created_at_ms": None,
      "candidate_expires_at_ms": None,
      "episode_id": None,
      "policy_version": policy.policy_version,
      "feature_schema_version": policy.feature_schema_version,
      "reference_profile_version": None,
      "reference_profile_schema_version": None,
      "preview_threshold": policy.preview_score,
      "candidate_threshold": policy.candidate_score,
      "revalidate_threshold": policy.revalidate_score,
      "rearm_threshold": policy.rearm_score,
      "signal_version": state_version,
      "candidate_state_version": state_version,
      "state_schema_version": OPPORTUNITY_STATE_SCHEMA_VERSION,
      "config_version": self._config_version(),
      "profile_version": None,
      "profile_fingerprint": profile_fingerprint,
      "pending_entry_intent_id": None,
      "data_age_ms": None,
      "external_blockers": [],
      "score_contributions": [],
    }
    opportunity = OpportunityState.initial(instrument_code=instrument_code).to_dict()
    opportunity.update(
      {
        "state_version": state_version,
        "latest_evaluation": evaluation,
        "revalidate_score": policy.revalidate_score,
        "policy_version": policy.policy_version,
        "feature_schema_version": policy.feature_schema_version,
        "config_version": self._config_version(),
        "profile_fingerprint": profile_fingerprint,
        "last_policy_rewarm_identity": rewarm_identity,
        "thresholds": {
          "preview": policy.preview_score,
          "candidate": policy.candidate_score,
          "revalidate": policy.revalidate_score,
          "rearm": policy.rearm_score,
        },
      }
    )
    signature = self._stable_fingerprint(self._material_projection(opportunity, ()))
    opportunity["event_cursor"] = {
      "material_signature": signature,
      # A RECONCILE control event has no authoritative market source identity.
      "last_emitted_source_time_ms": 0,
      "diagnostic_window_started_at_ms": 0,
      "coalesced_count": 0,
    }
    event_key = self._stable_fingerprint(
      {
        "run_id": input.run_id,
        "instrument_code": instrument_code,
        "event_type": "POLICY_CHANGED",
        "policy_version": policy.policy_version,
        "config_version": self._config_version(),
        "rewarm_identity": rewarm_identity,
      }
    )
    previous_candidate = dict(previous_opportunity.get("candidate") or {})
    return opportunity, {
      "type": _OPPORTUNITY_EVENT_TYPE,
      "event_key": f"tto:{event_key}",
      "record_kind": _OPPORTUNITY_EVENT_MATERIAL,
      "event_type": "POLICY_CHANGED",
      "instrument_code": instrument_code,
      "evaluated_at_ms": evaluated_at_ms,
      "signal_snapshot": evaluation,
      "transition": {
        "candidate_id": previous_candidate.get("candidate_id"),
        "from": previous_opportunity.get("candidate_status"),
        "to": CandidateStatus.NONE.value,
        "reason": "POLICY_CHANGED",
      },
      "external_blockers": [],
      "metrics": {"opportunity_score": None},
    }

  def _policy_rewarm_identity(self) -> str:
    policy = self._opportunity_policy()
    return self._stable_fingerprint(
      {
        "config_version": self._config_version(),
        "policy": policy.to_dict(),
      }
    )

  def _opportunity_policy(self) -> OpportunityPolicy:
    raw = self.get_parameter("signal_policy", OpportunityPolicy().to_dict())
    if not isinstance(raw, Mapping):
      raise ValueError("signal_policy must be a mapping")
    return OpportunityPolicy.from_dict(raw)

  def _config_version(self) -> int:
    try:
      return max(0, int(self.get_parameter("global_config_version", 0) or 0))
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("global_config_version must be a non-negative integer") from exc

  @staticmethod
  def _stable_fingerprint(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
      dict(value),
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

  def _reference_profile(
    self,
    input: StrategyInput,
  ) -> tuple[Optional[OpportunityReferenceProfile], Optional[str]]:
    raw = input.market_context.get(_PROFILE_CONTEXT_KEY)
    try:
      if isinstance(raw, OpportunityReferenceProfile):
        profile = raw
        fingerprint = self._stable_fingerprint(profile.to_dict())
      elif isinstance(raw, Mapping):
        profile = OpportunityReferenceProfile.from_dict(raw)
        fingerprint = str(raw.get("profile_fingerprint") or "").strip()
        if not fingerprint:
          fingerprint = self._stable_fingerprint(profile.to_dict())
      else:
        return None, None
    except (TypeError, ValueError, OverflowError):
      return None, None
    return profile, fingerprint

  def _opportunity_state_for_input(
    self,
    opportunity: Mapping[str, Any],
    *,
    instrument_code: str,
    trade_date: str,
    policy: OpportunityPolicy,
    config_version: int,
    profile_fingerprint: Optional[str],
  ) -> OpportunityState:
    if not opportunity:
      return OpportunityState.initial(
        instrument_code=instrument_code,
        trade_date=trade_date,
      )
    compatible = (
      int(opportunity.get("schema_version", 0) or 0) == OPPORTUNITY_STATE_SCHEMA_VERSION
      and int(opportunity.get("feature_schema_version", 0) or 0)
      == policy.feature_schema_version
      and str(opportunity.get("policy_version") or "") == policy.policy_version
      and int(opportunity.get("config_version", -1) or 0) == config_version
      and (opportunity.get("profile_fingerprint") or None) == profile_fingerprint
    )
    if not compatible:
      return OpportunityState.initial(
        instrument_code=instrument_code,
        trade_date=trade_date,
      )
    try:
      restored = OpportunityState.from_dict(opportunity)
    except (TypeError, ValueError, OverflowError):
      return OpportunityState.initial(
        instrument_code=instrument_code,
        trade_date=trade_date,
      )
    if restored.instrument_code and restored.instrument_code != instrument_code:
      raise ValueError("opportunity state instrument mismatch")
    return restored

  @staticmethod
  def _candidate_lifecycle(state: OpportunityState) -> tuple[Any, ...]:
    candidate = state.candidate
    return (
      candidate.candidate_id if candidate is not None else None,
      state.candidate_status.value,
      state.candidate_suppressed,
      state.candidate_awaiting_approval,
    )

  @staticmethod
  def _opportunity_sample(input: StrategyInput) -> Optional[OpportunitySample]:
    tick = input.event
    context = input.market_data_context
    try:
      price = float(getattr(tick, "last_price", 0.0) or 0.0)
      source_time_ms = int(context.source_time_ms)
      tick_ordinal = int(context.tick_ordinal)
    except (TypeError, ValueError, OverflowError):
      return None
    # Price validity belongs to the reducer so an invalid quote publishes an
    # explicit INVALID_PRICE/INSUFFICIENT snapshot instead of leaving an older
    # READY projection visible. Invalid source identity cannot be audited
    # causally and remains rejected here.
    if source_time_ms <= 0 or tick_ordinal < 0:
      return None
    trade_date = context.trade_date.isoformat()
    if trade_date == "0001-01-01":
      return None
    bids = list(getattr(tick, "bid_price", []) or [])
    asks = list(getattr(tick, "ask_price", []) or [])
    bid_volumes = list(getattr(tick, "bid_vol", []) or [])
    ask_volumes = list(getattr(tick, "ask_vol", []) or [])

    def optional_positive(value: Any) -> Optional[float]:
      try:
        normalized = float(value)
      except (TypeError, ValueError, OverflowError):
        return None
      return normalized if normalized > 0 else None

    def optional_non_negative(value: Any) -> Optional[float]:
      try:
        normalized = float(value)
      except (TypeError, ValueError, OverflowError):
        return None
      return normalized if normalized >= 0 else None

    raw_price_tick = input.market_context.get("price_tick", 0.01)
    return OpportunitySample(
      instrument_code=str(input.instrument_code or "").strip().upper(),
      trade_date=trade_date,
      source_time_ms=source_time_ms,
      tick_ordinal=tick_ordinal,
      price=price,
      continuity_generation=str(context.continuity_generation),
      received_at_ms=(
        int(context.received_at_ms) if int(context.received_at_ms or 0) > 0 else None
      ),
      bid_price=optional_positive(bids[0] if bids else None),
      ask_price=optional_positive(asks[0] if asks else None),
      bid_volume=optional_non_negative(bid_volumes[0] if bid_volumes else None),
      ask_volume=optional_non_negative(ask_volumes[0] if ask_volumes else None),
      cumulative_amount=optional_non_negative(getattr(tick, "amount", None)),
      cumulative_volume=optional_non_negative(getattr(tick, "pvolume", None)),
      price_tick=float(raw_price_tick or 0.01),
    )

  @staticmethod
  def _exit_tick_sample(sample: OpportunitySample) -> TickSample:
    return TickSample(
      timestamp_ms=sample.source_time_ms,
      price=sample.price,
      bid_price=float(sample.bid_price or 0.0),
      ask_price=float(sample.ask_price or 0.0),
      cumulative_amount=float(sample.cumulative_amount or 0.0),
      cumulative_volume=float(sample.cumulative_volume or 0.0),
    )

  @staticmethod
  def _source_local_second_of_day(timestamp: datetime) -> int:
    local = (
      timestamp.astimezone(SHANGHAI) if timestamp.tzinfo is not None else timestamp
    )
    return local.hour * 3600 + local.minute * 60 + local.second

  def _intent_emission_blockers(
    self,
    input: StrategyInput,
    state: Mapping[str, Any],
  ) -> List[str]:
    blockers: List[str] = []
    raw_emission = input.market_context.get(_EMISSION_CONTEXT_KEY)
    if not isinstance(raw_emission, Mapping):
      blockers.append("INTENT_EMISSION_CONTEXT_MISSING")
    else:
      blockers.extend(
        str(item) for item in list(raw_emission.get("blockers") or []) if str(item)
      )
      if raw_emission.get("allowed") is not True and not blockers:
        blockers.append("INTENT_EMISSION_NOT_ALLOWED")

    universe = dict(
      getattr(self, "_emission_context_by_instrument", {}).get(
        input.instrument_code,
        {},
      )
      or {}
    )
    if not universe:
      blockers.append("UNIVERSE_ELIGIBILITY_UNAVAILABLE")
    elif universe.get("allowed") is not True:
      blockers.extend(
        str(item) for item in list(universe.get("blockers") or []) if str(item)
      )
      if not universe.get("blockers"):
        blockers.append("POSITION_NOT_ELIGIBLE")
    if state.get("draining"):
      blockers.append("INSTRUMENT_DRAINING")
    if self._has_reconciliation_gate():
      blockers.append("T_TRADE_RECONCILIATION_REQUIRED")
    if self._active_volume(dict(state)) > 0:
      blockers.append("ACTIVE_T_BATCH_EXISTS")
    opportunity = dict(state.get("opportunity") or {})
    self_linked_pending_entry = bool(
      state.get("pending_entry_intent_id")
      and str(state.get("entry_order_status") or "").upper() == "AWAITING_APPROVAL"
      and str(opportunity.get("candidate_status") or "").upper()
      == CandidateStatus.AWAITING_APPROVAL.value
      and opportunity.get("candidate_awaiting_approval") is True
      and opportunity.get("candidate")
    )
    if state.get("pending_exit_intent_id") or (
      state.get("pending_entry_intent_id") and not self_linked_pending_entry
    ):
      blockers.append("INTENT_PENDING")
    if input.market_data_context.source_time_ms < int(
      state.get("cooldown_until_ms", 0) or 0
    ):
      blockers.append("COOLDOWN_ACTIVE")
    if self._should_block_new_entry(input):
      blockers.append("ENTRY_CUTOFF_REACHED")
    return list(dict.fromkeys(blockers))

  @staticmethod
  def _filtered_candidate_blockers(
    evaluation: Mapping[str, Any],
    candidate_status: CandidateStatus,
  ) -> List[str]:
    blockers = [str(item) for item in list(evaluation.get("blockers") or [])]
    if candidate_status in {
      CandidateStatus.LATCHED,
      CandidateStatus.AWAITING_APPROVAL,
    }:
      formation_only = {
        "PULLBACK_PATTERN_NOT_CONFIRMED",
        "MOMENTUM_PATTERN_NOT_CONFIRMED",
        "SCORE_BELOW_CANDIDATE",
      }
      blockers = [item for item in blockers if item not in formation_only]
    return blockers

  def _evaluation_snapshot(
    self,
    evaluation: OpportunityEvaluation,
    state: OpportunityState,
    *,
    policy: OpportunityPolicy,
    candidate_state_version: int,
    config_version: int,
    profile_fingerprint: Optional[str],
    external_blockers: Sequence[str],
    pending_entry_intent_id: str,
  ) -> Dict[str, Any]:
    payload = evaluation.to_dict()
    candidate = state.candidate
    payload.update(
      {
        "candidate_status": state.candidate_status.value,
        "candidate_id": candidate.candidate_id if candidate else None,
        "candidate_fingerprint": candidate.fingerprint if candidate else None,
        "candidate_created_at_ms": candidate.latched_at_ms if candidate else None,
        "candidate_expires_at_ms": candidate.expires_at_ms if candidate else None,
        "episode_id": candidate.episode_id if candidate else payload.get("episode_id"),
        "preview_threshold": policy.preview_score,
        "candidate_threshold": policy.candidate_score,
        "revalidate_threshold": policy.revalidate_score,
        "rearm_threshold": policy.rearm_score,
        "signal_version": candidate_state_version,
        "candidate_state_version": candidate_state_version,
        "state_schema_version": OPPORTUNITY_STATE_SCHEMA_VERSION,
        "feature_schema_version": policy.feature_schema_version,
        "policy_version": policy.policy_version,
        "config_version": config_version,
        "profile_version": (
          candidate.reference_profile_version
          if candidate is not None
          else evaluation.reference_profile_version
        ),
        "profile_fingerprint": profile_fingerprint,
        "pending_entry_intent_id": pending_entry_intent_id or None,
        "data_age_ms": max(0, evaluation.evaluated_at_ms - evaluation.source_time_ms),
        "external_blockers": list(external_blockers),
      }
    )
    blockers = self._filtered_candidate_blockers(payload, state.candidate_status)
    blockers.extend(str(item) for item in external_blockers if str(item))
    payload["blockers"] = list(dict.fromkeys(blockers))
    payload["top_blockers"] = list(payload["blockers"])
    selected = (
      payload.get("pullback")
      if payload.get("selected_path") == "PULLBACK_REBOUND"
      else payload.get("momentum")
      if payload.get("selected_path") == "MOMENTUM_ACCELERATION"
      else None
    )
    payload["score_contributions"] = list(dict(selected or {}).get("components") or [])
    return payload

  @staticmethod
  def _opportunity_payload(
    state: OpportunityState,
    *,
    evaluation: Mapping[str, Any],
    policy: OpportunityPolicy,
    candidate_state_version: int,
    config_version: int,
    profile_fingerprint: Optional[str],
    previous: Mapping[str, Any],
  ) -> Dict[str, Any]:
    payload = state.to_dict()
    payload.update(
      {
        "latest_evaluation": dict(evaluation),
        "preview_score": policy.preview_score,
        "candidate_score": policy.candidate_score,
        "revalidate_score": policy.revalidate_score,
        "rearm_score": policy.rearm_score,
        "thresholds": {
          "preview": policy.preview_score,
          "candidate": policy.candidate_score,
          "revalidate": policy.revalidate_score,
          "rearm": policy.rearm_score,
        },
        "state_version": candidate_state_version,
        "feature_schema_version": policy.feature_schema_version,
        "policy_version": policy.policy_version,
        "config_version": config_version,
        "profile_fingerprint": profile_fingerprint,
        "event_cursor": dict(previous.get("event_cursor") or {}),
        "last_policy_rewarm_identity": previous.get("last_policy_rewarm_identity"),
      }
    )
    return payload

  @staticmethod
  def _threshold_band(score: Any, policy: Mapping[str, Any]) -> str:
    if score is None:
      return "UNAVAILABLE"
    normalized = float(score)
    if normalized < float(policy["rearm"]):
      return "BELOW_REARM"
    if normalized < float(policy["preview"]):
      return "BELOW_PREVIEW"
    if normalized < float(policy["revalidate"]):
      return "PREVIEW"
    if normalized < float(policy["candidate"]):
      return "REVALIDATE"
    return "CANDIDATE"

  def _material_projection(
    self,
    opportunity: Mapping[str, Any],
    external_blockers: Sequence[str],
  ) -> Dict[str, Any]:
    evaluation = dict(opportunity.get("latest_evaluation") or {})
    pullback = dict(evaluation.get("pullback") or {})
    momentum = dict(evaluation.get("momentum") or {})
    thresholds = dict(opportunity.get("thresholds") or {})
    return {
      "data_health": evaluation.get("data_health"),
      "data_health_reasons": list(evaluation.get("data_health_reasons") or []),
      "pullback_phase": pullback.get("phase"),
      "momentum_phase": momentum.get("phase"),
      "pullback_threshold_band": self._threshold_band(
        pullback.get("score"), thresholds
      ),
      "momentum_threshold_band": self._threshold_band(
        momentum.get("score"), thresholds
      ),
      "selected_threshold_band": self._threshold_band(
        evaluation.get("opportunity_score"), thresholds
      ),
      "hard_gates": [
        (str(item.get("code") or ""), item.get("passed") is True)
        for item in list(evaluation.get("hard_gates") or [])
        if isinstance(item, Mapping)
      ],
      "blockers": list(evaluation.get("blockers") or []),
      "external_blockers": list(external_blockers),
      "candidate_id": evaluation.get("candidate_id"),
      "candidate_fingerprint": evaluation.get("candidate_fingerprint"),
      "candidate_status": evaluation.get("candidate_status"),
      "policy_version": opportunity.get("policy_version"),
      "config_version": opportunity.get("config_version"),
      "profile_fingerprint": opportunity.get("profile_fingerprint"),
    }

  def _opportunity_events(
    self,
    input: StrategyInput,
    *,
    previous_opportunity: Mapping[str, Any],
    opportunity: Dict[str, Any],
    external_blockers: Sequence[str],
    candidate_created: Optional[OpportunityCandidate],
  ) -> List[Dict[str, Any]]:
    source_time_ms = int(input.market_data_context.source_time_ms)
    current_projection = self._material_projection(opportunity, external_blockers)
    material_signature = self._stable_fingerprint(current_projection)
    cursor = dict(previous_opportunity.get("event_cursor") or {})
    previous_signature = str(cursor.get("material_signature") or "")
    material = material_signature != previous_signature
    last_emitted_at = int(cursor.get("last_emitted_source_time_ms", 0) or 0)
    window_started_at = int(
      cursor.get("diagnostic_window_started_at_ms", source_time_ms) or source_time_ms
    )
    coalesced_count = int(cursor.get("coalesced_count", 0) or 0) + 1
    if not material and source_time_ms - last_emitted_at < _DIAGNOSTIC_COALESCE_MS:
      opportunity["event_cursor"] = {
        "material_signature": material_signature,
        "last_emitted_source_time_ms": last_emitted_at,
        "diagnostic_window_started_at_ms": window_started_at,
        "coalesced_count": coalesced_count,
      }
      return []

    event_kind = (
      _OPPORTUNITY_EVENT_MATERIAL if material else _OPPORTUNITY_EVENT_DIAGNOSTIC
    )
    event_type = (
      self._material_event_type(
        previous_opportunity,
        opportunity,
        candidate_created=candidate_created,
      )
      if material
      else "HEARTBEAT"
    )
    evaluation = dict(opportunity["latest_evaluation"])
    key_seed = {
      "run_id": input.run_id,
      "instrument_code": input.instrument_code,
      "continuity_generation": evaluation.get("continuity_generation"),
      "source_time_ms": source_time_ms,
      "tick_ordinal": evaluation.get("tick_ordinal"),
      "record_kind": event_kind,
      "event_type": event_type,
      "material_signature": material_signature,
      "candidate_state_version": opportunity.get("state_version"),
    }
    event: Dict[str, Any] = {
      "type": _OPPORTUNITY_EVENT_TYPE,
      "event_key": f"tto:{self._stable_fingerprint(key_seed)}",
      "record_kind": event_kind,
      "event_type": event_type,
      "instrument_code": input.instrument_code,
      "evaluated_at_ms": int(evaluation.get("evaluated_at_ms") or source_time_ms),
      "signal_snapshot": evaluation,
      "external_blockers": list(external_blockers),
      "metrics": {
        "opportunity_score": evaluation.get("opportunity_score"),
        "sample_count": dict(evaluation.get("features") or {}).get("sample_count"),
      },
    }
    if material:
      event["transition"] = {
        "from": previous_signature or None,
        "to": material_signature,
      }
    else:
      event.update(
        {
          "window_started_at_ms": window_started_at,
          "window_ended_at_ms": source_time_ms,
          "coalesced_count": coalesced_count,
        }
      )
    opportunity["event_cursor"] = {
      "material_signature": material_signature,
      "last_emitted_source_time_ms": source_time_ms,
      "diagnostic_window_started_at_ms": source_time_ms,
      "coalesced_count": 0,
    }
    return [event]

  @staticmethod
  def _material_event_type(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    candidate_created: Optional[OpportunityCandidate],
  ) -> str:
    if candidate_created is not None:
      if str(current.get("candidate_status") or "") == CandidateStatus.SUPPRESSED.value:
        return "CANDIDATE_SUPPRESSED"
      return "CANDIDATE_LATCHED"
    before = dict(previous.get("latest_evaluation") or {})
    after = dict(current.get("latest_evaluation") or {})
    if not before:
      return "STATE_INITIALIZED"
    if before.get("candidate_status") != after.get("candidate_status"):
      return f"CANDIDATE_{after.get('candidate_status') or 'CHANGED'}"
    if before.get("data_health") != after.get("data_health"):
      return f"DATA_HEALTH_{after.get('data_health') or 'CHANGED'}"
    if dict(before.get("pullback") or {}).get("phase") != dict(
      after.get("pullback") or {}
    ).get("phase") or dict(before.get("momentum") or {}).get("phase") != dict(
      after.get("momentum") or {}
    ).get("phase"):
      return "FSM_TRANSITION"
    if previous.get("policy_version") != current.get("policy_version") or previous.get(
      "config_version"
    ) != current.get("config_version"):
      return "POLICY_CHANGED"
    return "DIAGNOSTIC_STATE_CHANGED"

  @staticmethod
  def _entry_observation_reason(
    evaluation: Mapping[str, Any],
    external_blockers: Sequence[str],
  ) -> str:
    if external_blockers:
      return str(external_blockers[0])
    blockers = list(evaluation.get("blockers") or [])
    if blockers:
      return str(blockers[0])
    return str(evaluation.get("data_health") or "OPPORTUNITY_OBSERVED")

  def _build_candidate_intent(
    self,
    input: StrategyInput,
    state: Dict[str, Any],
    candidate: OpportunityCandidate,
    *,
    policy: OpportunityPolicy,
    candidate_state_version: int,
    profile_fingerprint: Optional[str],
  ) -> TradeIntent:
    target_amount = float(self.get_parameter("target_trade_amount", 10_000.0))
    if target_amount <= 0:
      raise ValueError("target_trade_amount must be positive")
    run_candidate_identity = f"{input.run_id}:{candidate.fingerprint}"
    batch_id = str(
      uuid.uuid5(uuid.NAMESPACE_URL, f"quantx:t-trade:batch:{run_candidate_identity}")
    )
    intent_id = str(
      uuid.uuid5(uuid.NAMESPACE_URL, f"quantx:t-trade:intent:{run_candidate_identity}")
    )
    exit_plan_id = f"t-exit-{batch_id}"
    exit_policy = self._exit_policy_snapshot()
    exit_plan_template = self.build_exit_plan_template(
      instrument_code=input.instrument_code,
      batch_id=batch_id,
      plan_id=exit_plan_id,
      policy=exit_policy,
    )
    exit_plan_template_payload = exit_plan_template.to_dict()
    exit_plan_template_payload["metadata"] = {
      **dict(exit_plan_template_payload.get("metadata") or {}),
      "account_id": str(self.get_parameter("account_id", "") or ""),
      "strategy_run_id": input.run_id,
      "candidate_id": candidate.candidate_id,
      "candidate_fingerprint": candidate.fingerprint,
      "policy_version": policy.policy_version,
      "feature_schema_version": policy.feature_schema_version,
      "profile_version": candidate.reference_profile_version,
      "profile_fingerprint": profile_fingerprint,
    }
    state.update(
      {
        "status": TTradeStatus.OBSERVING,
        "requested_entry_amount": target_amount,
        "entry_pending_fill_base": 0,
        "exit_pending_fill_base": 0,
        "entry_filled_volume": 0,
        "entry_avg_price": 0.0,
        "exit_filled_volume": 0,
        "exit_avg_price": 0.0,
        "peak_net_profit_pct": 0.0,
        "trailing_floor_pct": -999.0,
        "profit_armed": False,
        "last_exit_reason": "",
        "batch_id": batch_id,
        "exit_plan_id": exit_plan_id,
        "batch_started_trade_date": "",
        "last_holding_trade_date": "",
        "holding_trading_days": 0,
        "exit_policy_snapshot": exit_policy,
      }
    )
    return TradeIntent(
      strategy_id=str(input.strategy_id),
      run_id=input.run_id,
      instrument_code=input.instrument_code,
      direction=TradeIntentDirection.BUY,
      bucket=SWING_BUCKET,
      reason=f"T_TRADE_{candidate.path.value}_ENTRY",
      priority=TradeIntentPriority.NORMAL,
      target_amount=target_amount,
      limit_price_hint=candidate.price,
      execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
      approval_ttl_ms=policy.candidate_ttl_seconds * 1000,
      max_price_deviation_bps=float(self.get_parameter("max_price_deviation_pct", 0.3))
      * 100.0,
      metadata={
        "t_trade_role": "entry",
        "account_id": str(self.get_parameter("account_id", "") or ""),
        "strategy_run_id": input.run_id,
        "instrument_code": input.instrument_code,
        "opportunity_schema_version": OPPORTUNITY_STATE_SCHEMA_VERSION,
        "signal_version": candidate_state_version,
        "candidate_id": candidate.candidate_id,
        "candidate_fingerprint": candidate.fingerprint,
        "candidate_state_version": candidate_state_version,
        "candidate_status": CandidateStatus.AWAITING_APPROVAL.value,
        "config_version": self._config_version(),
        "policy_version": policy.policy_version,
        "feature_schema_version": policy.feature_schema_version,
        "profile_version": candidate.reference_profile_version,
        "profile_fingerprint": profile_fingerprint,
        "source_time_ms": candidate.source_time_ms,
        "tick_ordinal": candidate.tick_ordinal,
        "continuity_generation": input.market_data_context.continuity_generation,
        "opportunity_score": candidate.score,
        "requested_entry_amount": target_amount,
        "target_trade_amount": target_amount,
        "max_trade_amount": float(self.get_parameter("max_trade_amount", 12_000.0)),
        "t_batch_id": batch_id,
        "exit_plan_id": exit_plan_id,
        "exit_plan_template": exit_plan_template_payload,
        "global_monitor_id": str(self.get_parameter("global_monitor_id", "") or ""),
      },
      trace_id=input.trace_id,
      intent_id=intent_id,
      created_at=input.timestamp,
    )

  def _monitor_open_lot(
    self,
    input: StrategyInput,
    sample: TickSample,
    state: Dict[str, Any],
    active_volume: int,
    *,
    opportunity_events: Optional[List[Dict[str, Any]]] = None,
  ) -> StrategyOutput:
    """Project the Engine-owned exit plan; this strategy no longer emits SELL."""

    code = input.instrument_code
    policy, policy_event = self._refresh_exit_policy(input, state)
    plan_id = str(state.get("exit_plan_id", "") or "")
    plan = next(
      (
        dict(item or {})
        for item in list(input.exit_plans or [])
        if str(dict(item or {}).get("template", {}).get("plan_id", "") or "") == plan_id
      ),
      None,
    )
    commands: List[ExitPlanCommand] = []
    if policy_event and plan_id:
      commands.append(
        ExitPlanCommand(
          command=ExitPlanCommandType.UPSERT_POLICY,
          plan_id=plan_id,
          template=self.build_exit_plan_template(
            instrument_code=code,
            batch_id=str(state.get("batch_id", "") or ""),
            plan_id=plan_id,
            policy=policy,
          ),
          reason="T_TRADE_GLOBAL_CONFIG_UPDATED",
        )
      )

    if plan is None:
      state.update({"last_price": sample.price})
      return self._state_output(
        code,
        state,
        tags=["exit_plan_missing", "monitoring"],
        reason="WAITING_FOR_EXIT_PLAN_REGISTRATION",
        trace={"active_volume": active_volume, "exit_plan_id": plan_id},
        append_events=[
          *list(opportunity_events or []),
          *([policy_event] if policy_event else []),
        ],
        exit_plan_commands=commands,
      )

    plan_status = str(plan.get("status", "") or "")
    floor = plan.get("trailing_floor_pct")
    armed = bool(plan.get("profit_armed", False))
    state.update(
      {
        "last_price": sample.price,
        "last_net_profit_pct": float(plan.get("last_net_profit_pct", 0.0) or 0.0),
        "peak_net_profit_pct": float(plan.get("peak_net_profit_pct", 0.0) or 0.0),
        "trailing_floor_pct": floor if floor is not None else -999.0,
        "profit_armed": armed,
        "holding_trading_days": int(plan.get("holding_trading_days", 0) or 0),
        "last_holding_trade_date": str(plan.get("last_holding_trade_date", "") or ""),
        "last_exit_reason": str(plan.get("last_exit_reason", "") or ""),
      }
    )
    if plan_status == ExitPlanStatus.EXIT_PENDING.value:
      pending_intent_id = str(plan.get("pending_intent_id", "") or "")
      if pending_intent_id != str(state.get("pending_exit_intent_id", "") or ""):
        state["exit_pending_fill_base"] = int(state.get("exit_filled_volume", 0) or 0)
      state["status"] = TTradeStatus.EXIT_SUBMITTED
      state["pending_exit_intent_id"] = pending_intent_id
      state["exit_order_status"] = "PENDING"
    elif plan_status == ExitPlanStatus.PARTIALLY_EXITED.value:
      state["status"] = TTradeStatus.EXIT_PARTIAL
    elif state.get("draining"):
      state["status"] = TTradeStatus.DRAINING
    else:
      state["status"] = TTradeStatus.PROFIT_ARMED if armed else TTradeStatus.MONITORING
    return self._state_output(
      code,
      state,
      tags=["profit_armed" if armed else "monitoring"],
      reason="MONITOR_ENGINE_EXIT_PLAN",
      trace={
        "exit_plan_id": plan_id,
        "exit_plan_status": plan_status,
        "net_profit_pct": state["last_net_profit_pct"],
        "peak_net_profit_pct": state["peak_net_profit_pct"],
        "trailing_floor_pct": floor,
        "time_exit_mode": policy.get("time_exit_mode"),
        "holding_trading_days": state["holding_trading_days"],
      },
      append_events=[
        *list(opportunity_events or []),
        *([policy_event] if policy_event else []),
      ],
      exit_plan_commands=commands,
    )

  @staticmethod
  def _empty_instrument_state() -> Dict[str, Any]:
    return {
      "status": TTradeStatus.OBSERVING,
      "requested_entry_amount": 0.0,
      "draining": False,
      "opportunity": {},
      "pending_entry_intent_id": "",
      "pending_exit_intent_id": "",
      "entry_order_status": "",
      "exit_order_status": "",
      "entry_terminal_order_status": "",
      "exit_terminal_order_status": "",
      "entry_expected_fill_volume": 0,
      "exit_expected_fill_volume": 0,
      "entry_pending_fill_base": 0,
      "exit_pending_fill_base": 0,
      "entry_filled_volume": 0,
      "entry_avg_price": 0.0,
      "exit_filled_volume": 0,
      "exit_avg_price": 0.0,
      "last_price": 0.0,
      "last_net_profit_pct": 0.0,
      "peak_net_profit_pct": 0.0,
      "trailing_floor_pct": -999.0,
      "profit_armed": False,
      "last_exit_reason": "",
      "cooldown_until_ms": 0,
      "completed_cycles": 0,
      "batch_id": "",
      "exit_plan_id": "",
      "batch_started_trade_date": "",
      "last_holding_trade_date": "",
      "holding_trading_days": 0,
      "exit_policy_snapshot": {},
      "reconciliation_reason": "",
    }

  @classmethod
  def _restore_instrument_state(
    cls,
    raw: Mapping[str, Any],
    *,
    preserve_opportunity: bool,
  ) -> Dict[str, Any]:
    defaults = cls._empty_instrument_state()
    entry_filled = max(0, int(raw.get("entry_filled_volume", 0) or 0))
    exit_filled = max(0, int(raw.get("exit_filled_volume", 0) or 0))
    has_report_derived_entry_fill_projection = entry_filled > 0
    if preserve_opportunity:
      restored = {
        key: raw[key] for key in defaults if key in raw and key != "opportunity"
      }
      defaults.update(restored)
      opportunity = raw.get("opportunity")
      if isinstance(opportunity, Mapping):
        try:
          OpportunityState.from_dict(opportunity)
        except (TypeError, ValueError, OverflowError):
          defaults["opportunity"] = {}
        else:
          defaults["opportunity"] = dict(opportunity)
      return defaults

    # V1/V2 opportunity and unconfirmed-entry fields are deliberately not
    # interpreted. Only report-derived projections following a real fill survive;
    # they remain subordinate to QMT/ExitPlan truth during Engine reconciliation.
    defaults["completed_cycles"] = max(
      0,
      int(raw.get("completed_cycles", 0) or 0),
    )
    defaults["cooldown_until_ms"] = max(
      0,
      int(raw.get("cooldown_until_ms", 0) or 0),
    )
    if not has_report_derived_entry_fill_projection:
      return defaults

    execution_keys = {
      "status",
      "draining",
      "pending_entry_intent_id",
      "pending_exit_intent_id",
      "entry_order_status",
      "exit_order_status",
      "entry_terminal_order_status",
      "exit_terminal_order_status",
      "entry_expected_fill_volume",
      "exit_expected_fill_volume",
      "entry_pending_fill_base",
      "exit_pending_fill_base",
      "entry_filled_volume",
      "entry_avg_price",
      "exit_filled_volume",
      "exit_avg_price",
      "last_price",
      "last_net_profit_pct",
      "peak_net_profit_pct",
      "trailing_floor_pct",
      "profit_armed",
      "last_exit_reason",
      "cooldown_until_ms",
      "completed_cycles",
      "batch_id",
      "exit_plan_id",
      "batch_started_trade_date",
      "last_holding_trade_date",
      "holding_trading_days",
      "exit_policy_snapshot",
      "reconciliation_reason",
    }
    for key in execution_keys:
      if key in raw and key in defaults:
        defaults[key] = raw[key]
    defaults["entry_filled_volume"] = entry_filled
    defaults["exit_filled_volume"] = min(exit_filled, entry_filled)
    if str(defaults.get("entry_order_status") or "").upper() == "AWAITING_APPROVAL":
      defaults["pending_entry_intent_id"] = ""
      defaults["entry_order_status"] = ""
    if entry_filled > exit_filled:
      defaults["status"] = (
        TTradeStatus.DRAINING if defaults.get("draining") else TTradeStatus.MONITORING
      )
    return defaults

  def _instrument_states(self) -> Dict[str, Dict[str, Any]]:
    return {
      str(code): dict(value or {})
      for code, value in dict(self.state.get("instrument_states", {}) or {}).items()
    }

  def _instrument_state(self, code: str) -> Dict[str, Any]:
    state = self._empty_instrument_state()
    state.update(self._instrument_states().get(code, {}))
    return state

  def _patch_instrument_state(
    self,
    code: str,
    state: Dict[str, Any],
    *,
    append_events: Optional[List[Dict[str, Any]]] = None,
  ) -> RuntimeStatePatch:
    states = self._instrument_states()
    states[code] = dict(state)
    return RuntimeStatePatch(
      set={"instrument_states": states},
      append_events=list(append_events or []),
    )

  def _apply_callback_state(
    self,
    code: str,
    state: Dict[str, Any],
    *,
    append_events: Optional[List[Dict[str, Any]]] = None,
  ) -> RuntimeStatePatch:
    patch = self._patch_instrument_state(
      code,
      state,
      append_events=append_events,
    )
    self.state.update(patch.set)
    return patch

  def _state_output(
    self,
    code: str,
    state: Dict[str, Any],
    *,
    tags: List[str],
    reason: str,
    trace: Optional[Dict[str, Any]] = None,
    append_events: Optional[List[Dict[str, Any]]] = None,
    exit_plan_commands: Optional[List[ExitPlanCommand]] = None,
  ) -> StrategyOutput:
    return StrategyOutput(
      exit_plan_commands=list(exit_plan_commands or []),
      runtime_state_patch=self._patch_instrument_state(
        code,
        state,
        append_events=append_events,
      ),
      decision_tags=tags,
      trace_payload={"reason": reason, **dict(trace or {})},
    )

  @staticmethod
  def _active_volume(state: Dict[str, Any]) -> int:
    return max(
      0,
      int(state.get("entry_filled_volume", 0) or 0)
      - int(state.get("exit_filled_volume", 0) or 0),
    )

  @staticmethod
  def _has_pending_intent(state: Dict[str, Any]) -> bool:
    return bool(
      state.get("pending_entry_intent_id") or state.get("pending_exit_intent_id")
    )

  @staticmethod
  def _order_event_source_time_ms(
    event: OrderStateEvent,
    state: Mapping[str, Any],
  ) -> int:
    if isinstance(event.timestamp, datetime):
      return int(event.timestamp.timestamp() * 1000)
    evaluation = dict(
      dict(state.get("opportunity") or {}).get("latest_evaluation") or {}
    )
    return int(evaluation.get("source_time_ms", 0) or 0)

  def _suppress_current_candidate(
    self,
    code: str,
    state: Dict[str, Any],
    *,
    source_time_ms: int,
    reason: str,
    intent_id: str,
  ) -> Optional[Dict[str, Any]]:
    opportunity = dict(state.get("opportunity") or {})
    try:
      reducer_state = OpportunityState.from_dict(opportunity)
    except (TypeError, ValueError, OverflowError):
      return None
    candidate = reducer_state.candidate
    if candidate is None or reducer_state.candidate_status not in {
      CandidateStatus.LATCHED,
      CandidateStatus.AWAITING_APPROVAL,
      CandidateStatus.REARMING,
    }:
      return None
    normalized_source_time_ms = max(candidate.source_time_ms, int(source_time_ms or 0))
    transitioned = transition_candidate(
      reducer_state,
      CandidateControl(suppress_candidate_id=candidate.candidate_id),
      source_time_ms=normalized_source_time_ms,
    )
    if self._candidate_lifecycle(transitioned) == self._candidate_lifecycle(
      reducer_state
    ):
      return None
    state_version = int(opportunity.get("state_version", 0) or 0) + 1
    blocker = f"CANDIDATE_SUPPRESSED_{str(reason or 'UNSPECIFIED').upper()}"
    evaluation = dict(opportunity.get("latest_evaluation") or {})
    evaluation.update(
      {
        "evaluated_at_ms": normalized_source_time_ms,
        "candidate_status": CandidateStatus.SUPPRESSED.value,
        "candidate_state_version": state_version,
        "signal_version": state_version,
        "pending_entry_intent_id": None,
      }
    )
    evaluation["blockers"] = list(
      dict.fromkeys([*list(evaluation.get("blockers") or []), blocker])
    )
    evaluation["top_blockers"] = list(evaluation["blockers"])
    opportunity.update(transitioned.to_dict())
    opportunity.update(
      {
        "state_version": state_version,
        "latest_evaluation": evaluation,
      }
    )
    projection = self._material_projection(
      opportunity,
      list(evaluation.get("external_blockers") or []),
    )
    signature = self._stable_fingerprint(projection)
    opportunity["event_cursor"] = {
      "material_signature": signature,
      "last_emitted_source_time_ms": normalized_source_time_ms,
      "diagnostic_window_started_at_ms": normalized_source_time_ms,
      "coalesced_count": 0,
    }
    state["opportunity"] = opportunity
    event_key = self._stable_fingerprint(
      {
        "run_id": self.context.run_id,
        "instrument_code": code,
        "candidate_id": candidate.candidate_id,
        "intent_id": intent_id,
        "candidate_state_version": state_version,
        "reason": reason,
      }
    )
    return {
      "type": _OPPORTUNITY_EVENT_TYPE,
      "event_key": f"tto:{event_key}",
      "record_kind": _OPPORTUNITY_EVENT_MATERIAL,
      "event_type": "CANDIDATE_SUPPRESSED",
      "instrument_code": code,
      "evaluated_at_ms": normalized_source_time_ms,
      "signal_snapshot": evaluation,
      "transition": {
        "candidate_id": candidate.candidate_id,
        "from": reducer_state.candidate_status.value,
        "to": CandidateStatus.SUPPRESSED.value,
        "reason": str(reason or "UNSPECIFIED"),
      },
      "intent_link": {"intent_id": intent_id or None},
      "external_blockers": list(evaluation.get("external_blockers") or []),
      "metrics": {"opportunity_score": evaluation.get("opportunity_score")},
    }

  def _has_reconciliation_gate(self) -> bool:
    return any(
      (
        str(state.get("entry_order_status", "") or "").upper() == "RECONCILE_REQUIRED"
        and bool(state.get("pending_entry_intent_id"))
      )
      or (
        str(state.get("exit_order_status", "") or "").upper() == "RECONCILE_REQUIRED"
        and bool(state.get("pending_exit_intent_id"))
      )
      for state in self._instrument_states().values()
    )

  def _event_instrument_code(self, event: OrderStateEvent, intent_id: str) -> str:
    code = str(event.metadata.get("instrument_code", "") or "")
    if not code and event.request is not None:
      code = str(getattr(event.request, "instrument_code", "") or "")
    if code:
      return code
    if intent_id:
      for candidate, state in self._instrument_states().items():
        if intent_id in {
          str(state.get("pending_entry_intent_id", "") or ""),
          str(state.get("pending_exit_intent_id", "") or ""),
        }:
          return candidate
    return ""

  def _is_bound_instrument(self, code: str) -> bool:
    return bool(code) and code in set(self.context.instruments or [])

  @staticmethod
  def _is_continuous_trading_time(timestamp: datetime) -> bool:
    """Exclude opening/closing call auctions and the midday recess."""

    local_timestamp = (
      timestamp.astimezone(SHANGHAI) if timestamp.tzinfo is not None else timestamp
    )
    local_time = local_timestamp.time()
    return time(9, 30) <= local_time <= time(11, 30) or time(
      13, 0
    ) <= local_time < time(14, 57)

  def build_exit_plan_template(
    self,
    *,
    instrument_code: str,
    batch_id: str,
    plan_id: str,
    policy: Optional[Dict[str, Any]] = None,
  ) -> ExitPlanTemplate:
    """Describe T-batch protection without owning its runtime lifecycle."""

    resolved = self._normalize_exit_policy(dict(policy or self._exit_policy_snapshot()))
    sizing = ExitSizingPolicy()
    rules = [
      ExitRuleSpec(
        rule_id=f"{plan_id}:trailing-profit",
        strategy=ExitRuleType.TRAILING_NET_PROFIT,
        priority=700,
        sizing=sizing,
        parameters={
          "target_profit_pct": float(resolved.get("target_profit_pct", 2.0)),
          "base_floor_pct": float(resolved.get("base_floor_pct", 0.5)),
          "initial_gap_pct": float(resolved.get("initial_gap_pct", 1.5)),
          "gap_slope": float(resolved.get("trailing_gap_slope", 0.25)),
          "max_gap_pct": float(resolved.get("max_gap_pct", 3.0)),
          "high_profit_lock_enabled": bool(
            resolved.get("high_profit_lock_enabled", True)
          ),
          "high_profit_arm_pct": float(resolved.get("high_profit_arm_pct", 4.0)),
          "high_profit_max_drawdown_pct": float(
            resolved.get("high_profit_max_drawdown_pct", 1.2)
          ),
          "reason": "TRAILING_FLOOR_REACHED",
        },
      )
    ]
    if bool(resolved.get("rapid_reversal_enabled", True)):
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:rapid-profit-reversal",
          strategy=ExitRuleType.RAPID_PROFIT_REVERSAL,
          priority=850,
          sizing=sizing,
          parameters={
            "arm_profit_pct": float(resolved.get("high_profit_arm_pct", 4.0)),
            "window_seconds": int(
              resolved.get("rapid_reversal_window_seconds", 15) or 15
            ),
            "drawdown_pct": float(resolved.get("rapid_reversal_drawdown_pct", 0.8)),
            "confirm_ticks": int(resolved.get("rapid_reversal_confirm_ticks", 2) or 2),
            "reason": "RAPID_PROFIT_REVERSAL",
          },
        )
      )
    if bool(resolved.get("limit_up_touch_exit_enabled", True)):
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:limit-up-touch",
          strategy=ExitRuleType.LIMIT_UP_TOUCH,
          priority=900,
          sizing=sizing,
          parameters={
            "tolerance_ticks": int(
              resolved.get("limit_up_touch_tolerance_ticks", 0) or 0
            ),
            "reason": "LIMIT_UP_TOUCH",
          },
        )
      )
    if bool(resolved.get("hard_stop_enabled", False)):
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:hard-stop",
          strategy=ExitRuleType.HARD_STOP,
          priority=1000,
          sizing=sizing,
          parameters={
            "stop_loss_pct": float(resolved.get("hard_stop_pct", -0.8)),
            "reason": "HARD_STOP",
          },
        )
      )
    time_exit_mode = str(
      resolved.get("time_exit_mode", TTradeTimeExitMode.UNLIMITED) or ""
    )
    if time_exit_mode == TTradeTimeExitMode.END_OF_DAY:
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:end-of-day",
          strategy=ExitRuleType.TIME_OF_DAY,
          priority=800,
          sizing=sizing,
          parameters={
            "exit_time": str(resolved.get("time_exit_time", "14:50")),
            "reason": "END_OF_DAY_FLATTEN",
          },
        )
      )
    elif time_exit_mode == TTradeTimeExitMode.MAX_HOLDING_DAYS:
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:max-holding-days",
          strategy=ExitRuleType.MAX_HOLDING_DAYS,
          priority=800,
          sizing=sizing,
          parameters={
            "max_holding_trading_days": int(
              resolved.get("max_holding_trading_days", 5) or 5
            ),
            "exit_time": str(resolved.get("time_exit_time", "14:50")),
            "reason": "MAX_HOLDING_DAYS_REACHED",
          },
        )
      )
    return ExitPlanTemplate(
      plan_id=plan_id,
      source_type="T_TRADE_BATCH",
      source_id=batch_id,
      account_id=str(self.get_parameter("account_id", "") or ""),
      instrument_code=instrument_code,
      bucket=SWING_BUCKET,
      rules=rules,
      strategy_id=str(getattr(self.context, "strategy_id", "") or ""),
      run_id=str(self.context.run_id or ""),
      config_version=int(resolved.get("config_version", 0) or 0),
      costs=TradingCostPolicy(
        commission_rate=float(resolved.get("commission_rate", 0.0003)),
        minimum_commission=float(resolved.get("minimum_commission", 5.0)),
        stamp_tax_rate=float(resolved.get("stamp_tax_rate", 0.0005)),
        transfer_fee_rate=float(resolved.get("transfer_fee_rate", 0.00001)),
      ),
      t1_policy=ExitT1Policy.ALLOW_SAME_INSTRUMENT_SUBSTITUTION,
      execution=ExitExecutionPolicy(
        price_reference=ExitPriceReference.BID,
        price_type="MARKET",
        protected_limit=False,
        max_slippage_bps=float(self.get_parameter("max_exit_slippage_bps", 30.0)),
        urgency="PROTECTIVE_EXIT",
        execution_mode="AUTO",
      ),
      metadata={
        "t_trade_role": "exit",
        "instrument_code": instrument_code,
        "t_batch_id": batch_id,
        "global_monitor_id": str(self.get_parameter("global_monitor_id", "") or ""),
        "exit_policy_version": int(resolved.get("config_version", 0) or 0),
      },
      auto_exit_authorized=bool(self.get_parameter("auto_exit_acknowledged", False)),
    )

  def _exit_policy_snapshot(self) -> Dict[str, Any]:
    keys = [
      "target_profit_pct",
      "base_floor_pct",
      "initial_gap_pct",
      "trailing_gap_slope",
      "max_gap_pct",
      "high_profit_lock_enabled",
      "high_profit_arm_pct",
      "high_profit_max_drawdown_pct",
      "rapid_reversal_enabled",
      "rapid_reversal_window_seconds",
      "rapid_reversal_drawdown_pct",
      "rapid_reversal_confirm_ticks",
      "limit_up_touch_exit_enabled",
      "limit_up_touch_tolerance_ticks",
      "hard_stop_enabled",
      "hard_stop_pct",
      "time_exit_mode",
      "time_exit_time",
      "max_holding_trading_days",
      "cooldown_seconds",
      "commission_rate",
      "minimum_commission",
      "stamp_tax_rate",
      "transfer_fee_rate",
    ]
    defaults = {
      "target_profit_pct": 2.0,
      "base_floor_pct": 0.5,
      "initial_gap_pct": 1.5,
      "trailing_gap_slope": 0.25,
      "max_gap_pct": 3.0,
      "high_profit_lock_enabled": True,
      "high_profit_arm_pct": 4.0,
      "high_profit_max_drawdown_pct": 1.2,
      "rapid_reversal_enabled": True,
      "rapid_reversal_window_seconds": 15,
      "rapid_reversal_drawdown_pct": 0.8,
      "rapid_reversal_confirm_ticks": 2,
      "limit_up_touch_exit_enabled": True,
      "limit_up_touch_tolerance_ticks": 0,
      "hard_stop_enabled": False,
      "hard_stop_pct": -0.8,
      "time_exit_mode": TTradeTimeExitMode.UNLIMITED,
      "time_exit_time": "14:50",
      "max_holding_trading_days": 5,
      "cooldown_seconds": 300,
      "commission_rate": 0.0003,
      "minimum_commission": 5.0,
      "stamp_tax_rate": 0.0005,
      "transfer_fee_rate": 0.00001,
    }
    snapshot = {key: self.get_parameter(key, defaults[key]) for key in keys}
    if self.get_parameter("time_exit_mode", None) is None:
      snapshot["time_exit_mode"] = (
        TTradeTimeExitMode.END_OF_DAY
        if bool(self.get_parameter("flatten_end_of_day", False))
        else TTradeTimeExitMode.UNLIMITED
      )
      snapshot["time_exit_time"] = self.get_parameter("end_of_day_exit_time", "14:50")
    if self.get_parameter("hard_stop_enabled", None) is None:
      snapshot["hard_stop_enabled"] = (
        self.get_parameter("hard_stop_pct", None) is not None
      )
    snapshot["config_version"] = int(
      self.get_parameter("global_config_version", 0) or 0
    )
    return snapshot

  def _is_time_exit(
    self, input: StrategyInput, policy: Optional[Dict[str, Any]] = None
  ) -> bool:
    raw = str(
      (policy or {}).get(
        "time_exit_time",
        self.get_parameter(
          "time_exit_time",
          self.get_parameter("end_of_day_exit_time", "14:50"),
        ),
      )
      or "14:50"
    )
    try:
      hour, minute = (int(part) for part in raw.split(":", 1))
      exit_time = time(hour=hour, minute=minute)
    except (TypeError, ValueError):
      exit_time = time(hour=14, minute=50)
    return input.timestamp.time() >= exit_time

  def _should_block_new_entry(self, input: StrategyInput) -> bool:
    try:
      entry_cutoff = time.fromisoformat(
        str(self.get_parameter("entry_cutoff_time", "14:50") or "14:50")
      )
    except ValueError:
      entry_cutoff = time(14, 50)
    if input.timestamp.time() >= entry_cutoff:
      return True
    policy = self._exit_policy_snapshot()
    mode = str(policy.get("time_exit_mode", TTradeTimeExitMode.UNLIMITED) or "")
    if mode == TTradeTimeExitMode.END_OF_DAY:
      return self._is_time_exit(input, policy)
    return (
      mode == TTradeTimeExitMode.MAX_HOLDING_DAYS
      and int(policy.get("max_holding_trading_days", 5) or 5) == 1
      and self._is_time_exit(input, policy)
    )

  def _refresh_exit_policy(
    self, input: StrategyInput, state: Dict[str, Any]
  ) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    previous = self._normalize_exit_policy(
      dict(state.get("exit_policy_snapshot") or {})
    )
    current = self._exit_policy_snapshot()
    if not previous:
      state["exit_policy_snapshot"] = current
      return current, None
    previous_version = int(previous.get("config_version", 0) or 0)
    current_version = int(current.get("config_version", 0) or 0)
    if current_version == previous_version:
      return previous, None
    state["exit_policy_snapshot"] = current
    return current, {
      "type": "T_TRADE_EXIT_POLICY_UPDATED",
      "instrument_code": input.instrument_code,
      "batch_id": str(state.get("batch_id", "") or ""),
      "changed_at": input.timestamp.isoformat(),
      "previous_config_version": previous_version,
      "config_version": current_version,
      "previous_policy": previous,
      "policy": current,
      "previous_time_exit_mode": self._legacy_time_exit_mode(previous),
      "time_exit_mode": current.get("time_exit_mode"),
      "previous_hard_stop_enabled": self._legacy_hard_stop_enabled(previous),
      "hard_stop_enabled": bool(current.get("hard_stop_enabled", False)),
    }

  @classmethod
  def _normalize_exit_policy(cls, policy: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(policy)
    if "time_exit_mode" not in normalized:
      normalized["time_exit_mode"] = cls._legacy_time_exit_mode(normalized)
    if "time_exit_time" not in normalized:
      normalized["time_exit_time"] = str(
        normalized.get("end_of_day_exit_time", "14:50") or "14:50"
      )
    if "max_holding_trading_days" not in normalized:
      normalized["max_holding_trading_days"] = 5
    if "hard_stop_enabled" not in normalized:
      normalized["hard_stop_enabled"] = cls._legacy_hard_stop_enabled(normalized)
    return normalized

  @staticmethod
  def _legacy_time_exit_mode(policy: Dict[str, Any]) -> str:
    if policy.get("time_exit_mode"):
      return str(policy["time_exit_mode"])
    return (
      TTradeTimeExitMode.END_OF_DAY
      if bool(policy.get("flatten_end_of_day", False))
      else TTradeTimeExitMode.UNLIMITED
    )

  @staticmethod
  def _legacy_hard_stop_enabled(policy: Dict[str, Any]) -> bool:
    if "hard_stop_enabled" in policy:
      return bool(policy.get("hard_stop_enabled"))
    return "hard_stop_pct" in policy
