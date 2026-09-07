"""Immutable planning evidence; never a balance ledger or order reservation.

Adapters must read one complete account snapshot and local obligations at a
single transaction cut. These values deliberately do not enter StrategyInput.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash


def _hash(value: Any) -> str:
  def encode(item: Any) -> Any:
    if is_dataclass(item):
      return {field.name: encode(getattr(item, field.name)) for field in fields(item)}
    if isinstance(item, Decimal):
      value = format(item, "f")
      if "." in value:
        value = value.rstrip("0").rstrip(".")
      return "0" if item == 0 else value
    if isinstance(item, datetime):
      return item.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(item, Enum):
      return item.value
    if isinstance(item, (tuple, list)):
      return [encode(part) for part in item]
    if isinstance(item, dict):
      return {key: encode(part) for key, part in item.items()}
    return item

  return stable_manifest_hash({"value": encode(value)})


def _amount(*values: Decimal) -> None:
  if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 for v in values):
    raise ValueError("T_PORTFOLIO_INVALID_AMOUNT")


def _volume(*values: int) -> None:
  if any(type(v) is not int or v < 0 for v in values):
    raise ValueError("T_PORTFOLIO_INVALID_VOLUME")


def _identity(*values: str) -> None:
  if any(not isinstance(v, str) or not v.strip() for v in values):
    raise ValueError("T_PORTFOLIO_IDENTITY_REQUIRED")


@dataclass(frozen=True)
class PortfolioEvidenceCut:
  execution_ref: ExecutionOwnerRef
  environment: ExecutionEnvironment
  as_of: datetime
  account_snapshot_id: str
  account_snapshot_hash: str
  account_snapshot_as_of: datetime
  local_obligation_watermark: str
  obligations_as_of: datetime
  complete: bool

  def __post_init__(self) -> None:
    if self.execution_ref.owner_type != ExecutionOwnerType.T_ASSISTANT_EXECUTION:
      raise ValueError("T_PORTFOLIO_OWNER_MISMATCH")
    if not isinstance(self.environment, ExecutionEnvironment):
      raise ValueError("T_PORTFOLIO_ENVIRONMENT_REQUIRED")
    _identity(
      self.account_snapshot_id,
      self.account_snapshot_hash,
      self.local_obligation_watermark,
    )
    # Match the P3 aware application clock; persistence conversion is adapter-owned.
    times = (self.as_of, self.account_snapshot_as_of, self.obligations_as_of)
    if any(
      not isinstance(v, datetime) or v.tzinfo is None or v.utcoffset() is None
      for v in times
    ):
      raise ValueError("T_PORTFOLIO_AWARE_TIME_REQUIRED")
    if max(self.account_snapshot_as_of, self.obligations_as_of) > self.as_of:
      raise ValueError("T_PORTFOLIO_FUTURE_EVIDENCE")
    if self.complete is not True:
      raise ValueError("T_PORTFOLIO_INCOMPLETE_SNAPSHOT")


@dataclass(frozen=True)
class TEnvelopePosition:
  instrument_code: str
  primary_industry: str
  locked_core: int
  core: int
  swing: int
  old_sellable_volume: int
  uncovered_protected_volume: int
  current_t_exposure: Decimal
  uncovered_entry_amount: Decimal
  active_or_pending_entry: bool

  def __post_init__(self) -> None:
    _identity(self.instrument_code, self.primary_industry)
    _volume(
      self.locked_core,
      self.core,
      self.swing,
      self.old_sellable_volume,
      self.uncovered_protected_volume,
    )
    _amount(self.current_t_exposure, self.uncovered_entry_amount)
    if self.old_sellable_volume > self.locked_core + self.core + self.swing:
      raise ValueError("T_PORTFOLIO_OLD_POSITION_INCONSISTENT")
    if type(self.active_or_pending_entry) is not bool:
      raise ValueError("T_PORTFOLIO_ENTRY_STATE_REQUIRED")


@dataclass(frozen=True)
class TTradingEnvelopePolicy:
  version: str
  protected_core_volume: int
  max_symbol_t_amount: Decimal
  max_entry_volume: int

  def __post_init__(self) -> None:
    _identity(self.version)
    _volume(self.protected_core_volume, self.max_entry_volume)
    _amount(self.max_symbol_t_amount)


@dataclass(frozen=True)
class TTradingEnvelope:
  cut: PortfolioEvidenceCut
  config_version: str
  policy: TTradingEnvelopePolicy
  observed_position_projection: TEnvelopePosition
  protected_old_position_floor: int = field(init=False)
  max_incremental_t_amount: Decimal = field(init=False)
  planning_entry_volume_ceiling: int = field(init=False)
  planning_replaceable_old_volume_ceiling: int = field(init=False)
  positive_t_eligible: bool = field(init=False)
  reason_codes: tuple[str, ...] = field(init=False)

  def __post_init__(self) -> None:
    _identity(self.config_version)
    position, policy = self.observed_position_projection, self.policy
    floor = position.locked_core + policy.protected_core_volume
    replaceable = max(
      0, position.old_sellable_volume - floor - position.uncovered_protected_volume
    )
    replaceable = min(
      replaceable, position.swing + max(0, position.core - policy.protected_core_volume)
    )
    amount = max(
      Decimal(0),
      policy.max_symbol_t_amount
      - position.current_t_exposure
      - position.uncovered_entry_amount,
    )
    volume = min(replaceable, policy.max_entry_volume)
    reasons: list[str] = []
    if position.active_or_pending_entry:
      reasons.append("T_SAME_SYMBOL_ENTRY_OBLIGATION")
    if volume == 0:
      reasons.append("T_NO_REPLACEABLE_OLD_POSITION")
    if amount == 0:
      reasons.append("T_SYMBOL_AMOUNT_EXHAUSTED")
    for key, value in (
      ("protected_old_position_floor", floor),
      ("max_incremental_t_amount", amount),
      ("planning_entry_volume_ceiling", volume),
      ("planning_replaceable_old_volume_ceiling", replaceable),
      ("positive_t_eligible", not reasons),
      ("reason_codes", tuple(reasons)),
    ):
      object.__setattr__(self, key, value)

  @property
  def input_fingerprint(self) -> str:
    return _hash(
      (self.cut, self.config_version, self.policy, self.observed_position_projection)
    )

  @property
  def envelope_id(self) -> str:
    return "tenv:" + self.input_fingerprint


def build_t_trading_envelope(
  *,
  cut: PortfolioEvidenceCut,
  config_version: str,
  policy: TTradingEnvelopePolicy,
  position: TEnvelopePosition,
) -> TTradingEnvelope:
  return TTradingEnvelope(cut, config_version, policy, position)


@dataclass(frozen=True)
class TPortfolioPolicy:
  version: str
  max_total_t_amount: Decimal
  max_total_asset_fraction: Decimal
  cash_buffer: Decimal
  max_industry_t_amount: Decimal
  max_concurrent_batches: int
  max_daily_loss: Decimal

  def __post_init__(self) -> None:
    _identity(self.version)
    _amount(
      self.max_total_t_amount,
      self.max_total_asset_fraction,
      self.cash_buffer,
      self.max_industry_t_amount,
      self.max_daily_loss,
    )
    _volume(self.max_concurrent_batches)
    if self.max_total_asset_fraction > 1:
      raise ValueError("T_PORTFOLIO_INVALID_ASSET_FRACTION")


@dataclass(frozen=True)
class IndustryTExposure:
  primary_industry: str
  current_t_exposure: Decimal
  uncovered_buy_amount: Decimal

  def __post_init__(self) -> None:
    _identity(self.primary_industry)
    _amount(self.current_t_exposure, self.uncovered_buy_amount)


@dataclass(frozen=True)
class PortfolioTDecisionSnapshot:
  cut: PortfolioEvidenceCut
  cycle_id: str
  config_version: str
  strategy_binding: str
  scorer_binding: str
  policy: TPortfolioPolicy
  envelopes: tuple[TTradingEnvelope, ...]
  industry_exposures: tuple[IndustryTExposure, ...]
  available_cash: Decimal
  total_assets: Decimal
  uncovered_buy_amount: Decimal
  current_t_exposure: Decimal
  realized_t_pnl: Decimal
  unrealized_t_pnl: Decimal
  active_batch_count: int
  entry_enabled: bool
  kill_switch: bool
  reconcile_required: bool

  def __post_init__(self) -> None:
    _identity(
      self.cycle_id, self.config_version, self.strategy_binding, self.scorer_binding
    )
    _amount(
      self.available_cash,
      self.total_assets,
      self.uncovered_buy_amount,
      self.current_t_exposure,
    )
    _volume(self.active_batch_count)
    if any(
      not isinstance(v, Decimal) or not v.is_finite()
      for v in (self.realized_t_pnl, self.unrealized_t_pnl)
    ):
      raise ValueError("T_PORTFOLIO_INVALID_PNL")
    if any(
      type(v) is not bool
      for v in (self.entry_enabled, self.kill_switch, self.reconcile_required)
    ):
      raise ValueError("T_PORTFOLIO_CONTROL_STATE_REQUIRED")
    envelopes = tuple(
      sorted(
        self.envelopes, key=lambda v: v.observed_position_projection.instrument_code
      )
    )
    codes = [v.observed_position_projection.instrument_code for v in envelopes]
    if len(set(codes)) != len(codes):
      raise ValueError("T_PORTFOLIO_DUPLICATE_SYMBOL")
    if any(
      v.cut != self.cut or v.config_version != self.config_version for v in envelopes
    ):
      raise ValueError("T_PORTFOLIO_MIXED_EVIDENCE_CUT")
    industry = tuple(sorted(self.industry_exposures, key=lambda v: v.primary_industry))
    if len({v.primary_industry for v in industry}) != len(industry):
      raise ValueError("T_PORTFOLIO_DUPLICATE_INDUSTRY")
    if (
      sum((v.current_t_exposure for v in industry), Decimal(0))
      != self.current_t_exposure
      or sum((v.uncovered_buy_amount for v in industry), Decimal(0))
      != self.uncovered_buy_amount
    ):
      raise ValueError("T_PORTFOLIO_INDUSTRY_TOTAL_MISMATCH")
    by_industry = {v.primary_industry: v for v in industry}
    for envelope in envelopes:
      position = envelope.observed_position_projection
      aggregate = by_industry.get(position.primary_industry)
      if aggregate is None:
        raise ValueError("T_PORTFOLIO_INDUSTRY_MISSING")
    for aggregate in industry:
      positions = [
        v.observed_position_projection
        for v in envelopes
        if v.observed_position_projection.primary_industry == aggregate.primary_industry
      ]
      if (
        sum((v.current_t_exposure for v in positions), Decimal(0))
        > aggregate.current_t_exposure
        or sum((v.uncovered_entry_amount for v in positions), Decimal(0))
        > aggregate.uncovered_buy_amount
      ):
        raise ValueError("T_PORTFOLIO_ENVELOPE_EXCEEDS_INDUSTRY")
    object.__setattr__(self, "industry_exposures", industry)
    object.__setattr__(self, "envelopes", envelopes)

  @property
  def portfolio_input_fingerprint(self) -> str:
    return _hash(asdict(self))

  @property
  def entry_blockers(self) -> tuple[str, ...]:
    reasons: list[str] = []
    if not self.entry_enabled:
      reasons.append("T_ACCOUNT_ENTRY_DISABLED")
    if self.kill_switch:
      reasons.append("T_ACCOUNT_KILL_SWITCH")
    if self.reconcile_required:
      reasons.append("T_ACCOUNT_RECONCILE_REQUIRED")
    if self.realized_t_pnl + self.unrealized_t_pnl <= -self.policy.max_daily_loss:
      reasons.append("T_DAILY_LOSS_LIMIT")
    return tuple(reasons)

  @property
  def planning_amount_cap(self) -> Decimal:
    if self.entry_blockers:
      return Decimal(0)
    return max(
      Decimal(0),
      min(
        self.policy.max_total_t_amount
        - self.current_t_exposure
        - self.uncovered_buy_amount,
        self.total_assets * self.policy.max_total_asset_fraction
        - self.current_t_exposure
        - self.uncovered_buy_amount,
        self.available_cash - self.policy.cash_buffer - self.uncovered_buy_amount,
      ),
    )

  @property
  def environment(self) -> ExecutionEnvironment:
    return self.cut.environment
