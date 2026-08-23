"""Causal maturation of stateful T-trade candidate outcomes.

This module is deliberately independent from strategy evaluation.  It consumes
only facts that became available after a candidate was frozen and therefore
cannot feed future prices back into the opportunity engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

CANDIDATE_OUTCOME_SCHEMA_VERSION = "1"
DEFAULT_CANDIDATE_OUTCOME_HORIZONS_SECONDS = (60, 300, 900)


class CandidateOutcomeStatus(str, Enum):
  OBSERVING = "OBSERVING"
  MATURED = "MATURED"
  UNAVAILABLE = "UNAVAILABLE"


class CandidateOutcomeUnavailableReason(str, Enum):
  CONTINUITY_CHANGED = "CONTINUITY_CHANGED"
  OBSERVATION_GAP = "OBSERVATION_GAP"
  OUT_OF_ORDER = "OUT_OF_ORDER"
  TRADING_HALTED = "TRADING_HALTED"
  WINDOW_INCOMPLETE = "WINDOW_INCOMPLETE"
  ENTRY_INCOMPLETE = "ENTRY_INCOMPLETE"
  NO_ENTRY_FILL = "NO_ENTRY_FILL"


class PostFillOutcomeStatus(str, Enum):
  WAITING_ENTRY = "WAITING_ENTRY"
  OBSERVING = "OBSERVING"
  MATURED = "MATURED"
  UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class CandidateOutcomeDefinition:
  candidate_id: str
  candidate_fingerprint: str
  strategy_run_id: str
  instrument_code: str
  source_time_ms: int
  tick_ordinal: int
  continuity_generation: str
  reference_price: float
  policy_version: str
  feature_schema_version: str
  profile_version: str | None = None
  profile_fingerprint: str | None = None
  horizons_seconds: tuple[int, ...] = DEFAULT_CANDIDATE_OUTCOME_HORIZONS_SECONDS
  max_observation_gap_ms: int = 60_000

  def __post_init__(self) -> None:
    required = {
      "candidate_id": self.candidate_id,
      "candidate_fingerprint": self.candidate_fingerprint,
      "strategy_run_id": self.strategy_run_id,
      "instrument_code": self.instrument_code,
      "continuity_generation": self.continuity_generation,
      "policy_version": self.policy_version,
      "feature_schema_version": self.feature_schema_version,
    }
    if any(not str(value).strip() for value in required.values()):
      raise ValueError("候选结果定义缺少必填身份或版本字段")
    if self.source_time_ms < 0 or self.tick_ordinal < 0:
      raise ValueError("候选结果源时间与序号不得为负数")
    if self.reference_price <= 0:
      raise ValueError("候选结果参考价必须大于零")
    horizons = tuple(int(value) for value in self.horizons_seconds)
    if not horizons or any(value <= 0 for value in horizons):
      raise ValueError("候选结果观察窗口必须为正整数秒")
    if tuple(sorted(set(horizons))) != horizons:
      raise ValueError("候选结果观察窗口必须严格递增且不得重复")
    if self.max_observation_gap_ms <= 0:
      raise ValueError("候选结果最大连续缺口必须大于零")


@dataclass(frozen=True)
class CandidatePriceObservation:
  source_time_ms: int
  tick_ordinal: int
  continuity_generation: str
  price: float
  trading_halted: bool = False


@dataclass(frozen=True)
class CandidateExecutionFill:
  fill_id: str
  role: str
  source_time_ms: int
  price: float
  volume: int
  fee: float | None
  entry_complete: bool = False
  entry_target_volume: int | None = None

  def __post_init__(self) -> None:
    if not self.fill_id.strip():
      raise ValueError("成交事实必须携带稳定成交标识")
    if self.role not in {"ENTRY", "EXIT"}:
      raise ValueError("候选成交角色只能是 ENTRY 或 EXIT")
    if self.price <= 0 or self.volume <= 0:
      raise ValueError("候选成交价格与数量必须大于零")
    if self.fee is not None and self.fee < 0:
      raise ValueError("候选成交费用不得为负数")
    if self.entry_target_volume is not None and self.entry_target_volume <= 0:
      raise ValueError("候选入场目标成交数量必须大于零")


@dataclass
class CandidateHorizonOutcome:
  horizon_seconds: int
  deadline_ms: int
  observed_at_ms: int | None = None
  observed_price: float | None = None
  return_pct: float | None = None
  mfe_pct: float | None = None
  mae_pct: float | None = None
  net_return_pct: float | None = None
  net_mfe_pct: float | None = None
  net_mae_pct: float | None = None

  @property
  def available(self) -> bool:
    return self.observed_at_ms is not None


@dataclass
class CandidateExecutionOutcome:
  applied_fill_ids: list[str] = field(default_factory=list)
  applied_fill_fingerprints: dict[str, str] = field(default_factory=dict)
  entry_volume: int = 0
  entry_amount: float = 0.0
  entry_fee: float | None = 0.0
  exit_volume: int = 0
  exit_amount: float = 0.0
  exit_fee: float | None = 0.0
  realized_net_pnl: float | None = None
  entry_frozen: bool = False
  entry_target_volume: int | None = None
  last_entry_fill_source_time_ms: int | None = None

  @property
  def entry_price(self) -> float | None:
    if self.entry_volume <= 0:
      return None
    return self.entry_amount / self.entry_volume

  @property
  def closed(self) -> bool:
    return self.entry_volume > 0 and self.exit_volume >= self.entry_volume

  @property
  def fee_truth_available(self) -> bool:
    return self.closed and self.entry_fee is not None and self.exit_fee is not None


@dataclass
class CandidatePostFillOutcome:
  status: PostFillOutcomeStatus = PostFillOutcomeStatus.WAITING_ENTRY
  armed_at_ms: int | None = None
  reference_price: float | None = None
  reference_amount: float | None = None
  reference_volume: int | None = None
  horizons: list[CandidateHorizonOutcome] = field(default_factory=list)
  high_price: float | None = None
  low_price: float | None = None
  running_net_mfe_pct: float | None = None
  running_net_mae_pct: float | None = None
  last_source_time_ms: int | None = None
  last_tick_ordinal: int | None = None
  sample_count: int = 0
  unavailable_reason: CandidateOutcomeUnavailableReason | None = None
  finalized_at_ms: int | None = None

  @property
  def available(self) -> bool:
    return self.status is PostFillOutcomeStatus.MATURED

  @property
  def net_available(self) -> bool:
    return self.available and all(
      horizon.net_return_pct is not None
      and horizon.net_mfe_pct is not None
      and horizon.net_mae_pct is not None
      for horizon in self.horizons
    )


@dataclass
class CandidateOutcomeState:
  definition: CandidateOutcomeDefinition
  status: CandidateOutcomeStatus
  horizons: list[CandidateHorizonOutcome]
  high_price: float | None = None
  low_price: float | None = None
  last_source_time_ms: int | None = None
  last_tick_ordinal: int | None = None
  sample_count: int = 0
  unavailable_reason: CandidateOutcomeUnavailableReason | None = None
  finalized_at_ms: int | None = None
  execution: CandidateExecutionOutcome = field(
    default_factory=CandidateExecutionOutcome
  )
  post_fill: CandidatePostFillOutcome = field(default_factory=CandidatePostFillOutcome)

  @property
  def available(self) -> bool:
    return self.status is CandidateOutcomeStatus.MATURED

  def to_dict(self) -> dict[str, Any]:
    payload = asdict(self)
    payload["status"] = self.status.value
    payload["unavailable_reason"] = (
      self.unavailable_reason.value if self.unavailable_reason else None
    )
    payload["post_fill"]["status"] = self.post_fill.status.value
    payload["post_fill"]["unavailable_reason"] = (
      self.post_fill.unavailable_reason.value
      if self.post_fill.unavailable_reason
      else None
    )
    payload["definition"]["horizons_seconds"] = list(self.definition.horizons_seconds)
    payload["schema_version"] = CANDIDATE_OUTCOME_SCHEMA_VERSION
    return payload

  @classmethod
  def from_dict(cls, payload: Mapping[str, Any]) -> CandidateOutcomeState:
    definition_payload = dict(payload["definition"])
    definition_payload["horizons_seconds"] = tuple(
      int(value) for value in definition_payload["horizons_seconds"]
    )
    return cls(
      definition=CandidateOutcomeDefinition(**definition_payload),
      status=CandidateOutcomeStatus(str(payload["status"])),
      horizons=[CandidateHorizonOutcome(**dict(item)) for item in payload["horizons"]],
      high_price=_optional_float(payload.get("high_price")),
      low_price=_optional_float(payload.get("low_price")),
      last_source_time_ms=_optional_int(payload.get("last_source_time_ms")),
      last_tick_ordinal=_optional_int(payload.get("last_tick_ordinal")),
      sample_count=int(payload.get("sample_count") or 0),
      unavailable_reason=(
        CandidateOutcomeUnavailableReason(str(payload["unavailable_reason"]))
        if payload.get("unavailable_reason")
        else None
      ),
      finalized_at_ms=_optional_int(payload.get("finalized_at_ms")),
      execution=CandidateExecutionOutcome(**dict(payload.get("execution") or {})),
      post_fill=_post_fill_from_dict(payload.get("post_fill")),
    )


def start_candidate_outcome(
  definition: CandidateOutcomeDefinition,
) -> CandidateOutcomeState:
  return CandidateOutcomeState(
    definition=definition,
    status=CandidateOutcomeStatus.OBSERVING,
    high_price=definition.reference_price,
    low_price=definition.reference_price,
    horizons=[
      CandidateHorizonOutcome(
        horizon_seconds=horizon,
        deadline_ms=definition.source_time_ms + horizon * 1000,
      )
      for horizon in definition.horizons_seconds
    ],
  )


def observe_candidate_outcome(
  state: CandidateOutcomeState,
  observation: CandidatePriceObservation,
) -> CandidateOutcomeState:
  """Apply one strictly post-candidate market fact in source order."""

  definition = state.definition
  identity = (observation.source_time_ms, observation.tick_ordinal)
  candidate_identity = (definition.source_time_ms, definition.tick_ordinal)
  if identity <= candidate_identity:
    return state
  _observe_post_fill(state, observation)
  if state.status is not CandidateOutcomeStatus.OBSERVING:
    return state
  if observation.continuity_generation != definition.continuity_generation:
    return _make_unavailable(
      state,
      CandidateOutcomeUnavailableReason.CONTINUITY_CHANGED,
      observation.source_time_ms,
    )
  if observation.trading_halted:
    return _make_unavailable(
      state,
      CandidateOutcomeUnavailableReason.TRADING_HALTED,
      observation.source_time_ms,
    )
  if observation.price <= 0:
    return _make_unavailable(
      state,
      CandidateOutcomeUnavailableReason.OBSERVATION_GAP,
      observation.source_time_ms,
    )
  if state.last_source_time_ms is not None:
    last_identity = (state.last_source_time_ms, int(state.last_tick_ordinal or 0))
    if identity == last_identity:
      return state
    if identity < last_identity:
      return _make_unavailable(
        state,
        CandidateOutcomeUnavailableReason.OUT_OF_ORDER,
        observation.source_time_ms,
      )
    gap_from_ms = state.last_source_time_ms
  else:
    gap_from_ms = definition.source_time_ms
  if observation.source_time_ms - gap_from_ms > definition.max_observation_gap_ms:
    return _make_unavailable(
      state,
      CandidateOutcomeUnavailableReason.OBSERVATION_GAP,
      observation.source_time_ms,
    )

  state.last_source_time_ms = observation.source_time_ms
  state.last_tick_ordinal = observation.tick_ordinal
  state.sample_count += 1
  state.high_price = max(state.high_price or observation.price, observation.price)
  state.low_price = min(state.low_price or observation.price, observation.price)
  for horizon in state.horizons:
    if horizon.available or observation.source_time_ms < horizon.deadline_ms:
      continue
    horizon.observed_at_ms = observation.source_time_ms
    horizon.observed_price = observation.price
    horizon.return_pct = _return_pct(observation.price, definition.reference_price)
    horizon.mfe_pct = _return_pct(state.high_price, definition.reference_price)
    horizon.mae_pct = _return_pct(state.low_price, definition.reference_price)
  if all(horizon.available for horizon in state.horizons):
    state.status = CandidateOutcomeStatus.MATURED
    state.finalized_at_ms = observation.source_time_ms
  return state


def apply_candidate_execution_fill(
  state: CandidateOutcomeState,
  fill: CandidateExecutionFill,
) -> CandidateOutcomeState:
  """Attach authoritative fills; unknown fees remain unknown, never zero-filled."""

  execution = state.execution
  fill_fingerprint = _fill_fingerprint(fill)
  if fill.fill_id in execution.applied_fill_ids:
    if execution.applied_fill_fingerprints.get(fill.fill_id) != fill_fingerprint:
      raise ValueError("同一成交标识对应的成交事实不一致")
    if fill.role == "ENTRY":
      _merge_entry_target(execution, fill.entry_target_volume)
      _arm_completed_entry_if_ready(state, fill.entry_complete)
    return state
  if fill.source_time_ms <= state.definition.source_time_ms:
    raise ValueError("候选成交必须严格发生在候选源时间之后")
  if fill.role == "EXIT" and execution.entry_volume <= 0:
    raise ValueError("候选退出成交不得早于候选入场成交")
  if (
    fill.role == "EXIT" and execution.exit_volume + fill.volume > execution.entry_volume
  ):
    raise ValueError("候选退出成交累计数量不得超过入场成交数量")
  if fill.role == "ENTRY":
    if execution.entry_frozen:
      raise ValueError("入场完成冻结后不得追加候选入场成交")
    _merge_entry_target(execution, fill.entry_target_volume)
  execution.applied_fill_ids.append(fill.fill_id)
  execution.applied_fill_fingerprints[fill.fill_id] = fill_fingerprint
  if fill.role == "ENTRY":
    execution.entry_volume += fill.volume
    execution.entry_amount += fill.price * fill.volume
    execution.entry_fee = _accumulate_fee(execution.entry_fee, fill.fee)
    execution.last_entry_fill_source_time_ms = max(
      execution.last_entry_fill_source_time_ms or fill.source_time_ms,
      fill.source_time_ms,
    )
    _arm_completed_entry_if_ready(state, fill.entry_complete)
  else:
    execution.exit_volume += fill.volume
    execution.exit_amount += fill.price * fill.volume
    execution.exit_fee = _accumulate_fee(execution.exit_fee, fill.fee)
  if fill.fee is None:
    _invalidate_post_fill_net(state.post_fill)
  if execution.closed:
    closed_volume = execution.entry_volume
    gross_exit = execution.exit_amount
    if execution.exit_volume > closed_volume:
      gross_exit *= closed_volume / execution.exit_volume
    if execution.entry_fee is not None and execution.exit_fee is not None:
      execution.realized_net_pnl = (
        gross_exit - execution.entry_amount - execution.entry_fee - execution.exit_fee
      )
      _refresh_post_fill_net_from_closed_execution(state)
    else:
      execution.realized_net_pnl = None
    if execution.closed and state.post_fill.status is PostFillOutcomeStatus.OBSERVING:
      _make_post_fill_unavailable(
        state.post_fill,
        CandidateOutcomeUnavailableReason.WINDOW_INCOMPLETE,
        fill.source_time_ms,
      )
  return state


def finalize_candidate_outcome(
  state: CandidateOutcomeState,
  *,
  finalized_at_ms: int,
) -> CandidateOutcomeState:
  if state.status is CandidateOutcomeStatus.OBSERVING:
    _make_unavailable(
      state,
      CandidateOutcomeUnavailableReason.WINDOW_INCOMPLETE,
      finalized_at_ms,
    )
  if state.post_fill.status is PostFillOutcomeStatus.WAITING_ENTRY:
    reason = (
      CandidateOutcomeUnavailableReason.ENTRY_INCOMPLETE
      if state.execution.entry_volume > 0
      else CandidateOutcomeUnavailableReason.NO_ENTRY_FILL
    )
    _make_post_fill_unavailable(state.post_fill, reason, finalized_at_ms)
  elif state.post_fill.status is PostFillOutcomeStatus.OBSERVING:
    _make_post_fill_unavailable(
      state.post_fill,
      CandidateOutcomeUnavailableReason.WINDOW_INCOMPLETE,
      finalized_at_ms,
    )
  return state


def _make_unavailable(
  state: CandidateOutcomeState,
  reason: CandidateOutcomeUnavailableReason,
  finalized_at_ms: int,
) -> CandidateOutcomeState:
  state.status = CandidateOutcomeStatus.UNAVAILABLE
  state.unavailable_reason = reason
  state.finalized_at_ms = finalized_at_ms
  for horizon in state.horizons:
    if not horizon.available:
      horizon.observed_at_ms = None
      horizon.observed_price = None
      horizon.return_pct = None
      horizon.mfe_pct = None
      horizon.mae_pct = None
  return state


def _arm_post_fill(state: CandidateOutcomeState, armed_at_ms: int) -> None:
  execution = state.execution
  post_fill = state.post_fill
  if execution.entry_volume <= 0 or execution.entry_price is None:
    raise ValueError("成交后结果冻结缺少有效入场成交")
  execution.entry_frozen = True
  post_fill.status = PostFillOutcomeStatus.OBSERVING
  post_fill.armed_at_ms = armed_at_ms
  post_fill.reference_price = execution.entry_price
  post_fill.reference_amount = execution.entry_amount
  post_fill.reference_volume = execution.entry_volume
  post_fill.high_price = execution.entry_price
  post_fill.low_price = execution.entry_price
  post_fill.last_source_time_ms = armed_at_ms
  post_fill.horizons = [
    CandidateHorizonOutcome(
      horizon_seconds=horizon,
      deadline_ms=armed_at_ms + horizon * 1000,
    )
    for horizon in state.definition.horizons_seconds
  ]


def _observe_post_fill(
  state: CandidateOutcomeState,
  observation: CandidatePriceObservation,
) -> None:
  post_fill = state.post_fill
  if post_fill.status is not PostFillOutcomeStatus.OBSERVING:
    return
  armed_at_ms = int(post_fill.armed_at_ms or 0)
  if observation.source_time_ms <= armed_at_ms:
    return
  if observation.continuity_generation != state.definition.continuity_generation:
    _make_post_fill_unavailable(
      post_fill,
      CandidateOutcomeUnavailableReason.CONTINUITY_CHANGED,
      observation.source_time_ms,
    )
    return
  if observation.trading_halted:
    _make_post_fill_unavailable(
      post_fill,
      CandidateOutcomeUnavailableReason.TRADING_HALTED,
      observation.source_time_ms,
    )
    return
  if observation.price <= 0:
    _make_post_fill_unavailable(
      post_fill,
      CandidateOutcomeUnavailableReason.OBSERVATION_GAP,
      observation.source_time_ms,
    )
    return
  identity = (observation.source_time_ms, observation.tick_ordinal)
  if post_fill.last_source_time_ms is not None:
    last_identity = (
      post_fill.last_source_time_ms,
      int(post_fill.last_tick_ordinal or -1),
    )
    if identity == last_identity:
      return
    if identity < last_identity:
      _make_post_fill_unavailable(
        post_fill,
        CandidateOutcomeUnavailableReason.OUT_OF_ORDER,
        observation.source_time_ms,
      )
      return
    gap_from_ms = post_fill.last_source_time_ms
  else:
    gap_from_ms = armed_at_ms
  if observation.source_time_ms - gap_from_ms > state.definition.max_observation_gap_ms:
    _make_post_fill_unavailable(
      post_fill,
      CandidateOutcomeUnavailableReason.OBSERVATION_GAP,
      observation.source_time_ms,
    )
    return
  post_fill.last_source_time_ms = observation.source_time_ms
  post_fill.last_tick_ordinal = observation.tick_ordinal
  post_fill.sample_count += 1
  post_fill.high_price = max(
    post_fill.high_price or observation.price, observation.price
  )
  post_fill.low_price = min(post_fill.low_price or observation.price, observation.price)
  net_return_pct = _post_fill_net_return_pct(state.execution, observation.price)
  if net_return_pct is not None:
    post_fill.running_net_mfe_pct = max(
      post_fill.running_net_mfe_pct
      if post_fill.running_net_mfe_pct is not None
      else net_return_pct,
      net_return_pct,
    )
    post_fill.running_net_mae_pct = min(
      post_fill.running_net_mae_pct
      if post_fill.running_net_mae_pct is not None
      else net_return_pct,
      net_return_pct,
    )
  for horizon in post_fill.horizons:
    if horizon.available or observation.source_time_ms < horizon.deadline_ms:
      continue
    reference_price = float(post_fill.reference_price or 0.0)
    horizon.observed_at_ms = observation.source_time_ms
    horizon.observed_price = observation.price
    horizon.return_pct = _return_pct(observation.price, reference_price)
    horizon.mfe_pct = _return_pct(post_fill.high_price, reference_price)
    horizon.mae_pct = _return_pct(post_fill.low_price, reference_price)
    horizon.net_return_pct = net_return_pct
    horizon.net_mfe_pct = post_fill.running_net_mfe_pct
    horizon.net_mae_pct = post_fill.running_net_mae_pct
  if post_fill.horizons and all(horizon.available for horizon in post_fill.horizons):
    post_fill.status = PostFillOutcomeStatus.MATURED
    post_fill.finalized_at_ms = observation.source_time_ms


def _post_fill_net_return_pct(
  execution: CandidateExecutionOutcome,
  mark_price: float,
) -> float | None:
  if not execution.fee_truth_available or execution.entry_amount <= 0:
    return None
  open_volume = execution.entry_volume - execution.exit_volume
  net_pnl = (
    execution.exit_amount
    + mark_price * open_volume
    - execution.entry_amount
    - float(execution.entry_fee or 0.0)
    - float(execution.exit_fee or 0.0)
  )
  return net_pnl / execution.entry_amount * 100.0


def _refresh_post_fill_net_from_closed_execution(
  state: CandidateOutcomeState,
) -> None:
  execution = state.execution
  if not execution.fee_truth_available or execution.entry_amount <= 0:
    return
  fee_pct = (
    (float(execution.entry_fee or 0.0) + float(execution.exit_fee or 0.0))
    / execution.entry_amount
    * 100.0
  )
  net_extrema: list[tuple[float, float]] = []
  for horizon in state.post_fill.horizons:
    if horizon.return_pct is None or horizon.mfe_pct is None or horizon.mae_pct is None:
      continue
    horizon.net_return_pct = horizon.return_pct - fee_pct
    horizon.net_mfe_pct = horizon.mfe_pct - fee_pct
    horizon.net_mae_pct = horizon.mae_pct - fee_pct
    net_extrema.append((horizon.net_mfe_pct, horizon.net_mae_pct))
  if net_extrema:
    state.post_fill.running_net_mfe_pct = max(item[0] for item in net_extrema)
    state.post_fill.running_net_mae_pct = min(item[1] for item in net_extrema)


def _invalidate_post_fill_net(post_fill: CandidatePostFillOutcome) -> None:
  post_fill.running_net_mfe_pct = None
  post_fill.running_net_mae_pct = None
  for horizon in post_fill.horizons:
    horizon.net_return_pct = None
    horizon.net_mfe_pct = None
    horizon.net_mae_pct = None


def _make_post_fill_unavailable(
  post_fill: CandidatePostFillOutcome,
  reason: CandidateOutcomeUnavailableReason,
  finalized_at_ms: int,
) -> None:
  post_fill.status = PostFillOutcomeStatus.UNAVAILABLE
  post_fill.unavailable_reason = reason
  post_fill.finalized_at_ms = finalized_at_ms
  for horizon in post_fill.horizons:
    if not horizon.available:
      horizon.observed_at_ms = None
      horizon.observed_price = None
      horizon.return_pct = None
      horizon.mfe_pct = None
      horizon.mae_pct = None
      horizon.net_return_pct = None
      horizon.net_mfe_pct = None
      horizon.net_mae_pct = None


def _return_pct(price: float | None, reference: float) -> float | None:
  if price is None:
    return None
  return (price / reference - 1.0) * 100.0


def _accumulate_fee(current: float | None, incoming: float | None) -> float | None:
  if current is None or incoming is None:
    return None
  return current + incoming


def _merge_entry_target(
  execution: CandidateExecutionOutcome,
  target_volume: int | None,
) -> None:
  if target_volume is None:
    return
  if (
    execution.entry_target_volume is not None
    and execution.entry_target_volume != target_volume
  ):
    raise ValueError("同一候选的入场目标成交数量不一致")
  execution.entry_target_volume = target_volume


def _arm_completed_entry_if_ready(
  state: CandidateOutcomeState,
  entry_complete: bool,
) -> None:
  execution = state.execution
  if (
    entry_complete
    and not execution.entry_frozen
    and execution.entry_target_volume is not None
    and execution.entry_volume >= execution.entry_target_volume
    and execution.last_entry_fill_source_time_ms is not None
  ):
    _arm_post_fill(state, execution.last_entry_fill_source_time_ms)


def _fill_fingerprint(fill: CandidateExecutionFill) -> str:
  return "|".join(
    (
      fill.role,
      str(fill.source_time_ms),
      format(fill.price, ".17g"),
      str(fill.volume),
      "UNKNOWN" if fill.fee is None else format(fill.fee, ".17g"),
    )
  )


def _optional_int(value: Any) -> int | None:
  return int(value) if value is not None else None


def _optional_float(value: Any) -> float | None:
  return float(value) if value is not None else None


def _post_fill_from_dict(value: Any) -> CandidatePostFillOutcome:
  payload = dict(value or {})
  payload["status"] = PostFillOutcomeStatus(
    str(payload.get("status") or PostFillOutcomeStatus.WAITING_ENTRY.value)
  )
  payload["unavailable_reason"] = (
    CandidateOutcomeUnavailableReason(str(payload["unavailable_reason"]))
    if payload.get("unavailable_reason")
    else None
  )
  payload["horizons"] = [
    CandidateHorizonOutcome(**dict(item)) for item in payload.get("horizons") or []
  ]
  return CandidatePostFillOutcome(**payload)


def validate_fixed_horizons(values: Sequence[int]) -> tuple[int, ...]:
  """Public validator for adapters accepting configured fixed horizons."""

  normalized = tuple(int(value) for value in values)
  CandidateOutcomeDefinition(
    candidate_id="validation",
    candidate_fingerprint="validation",
    strategy_run_id="validation",
    instrument_code="validation",
    source_time_ms=0,
    tick_ordinal=0,
    continuity_generation="validation",
    reference_price=1.0,
    policy_version="validation",
    feature_schema_version="validation",
    horizons_seconds=normalized,
  )
  return normalized
