"""Runtime instrument-universe resolution for Engine-owned coordinators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Protocol, Sequence

from quantx_domain.enums import StrategyInstrumentUniverseMode
from quantx_domain.trading.limit_up_board_universe import (
  select_limit_up_board_universe,
)


@dataclass(frozen=True)
class InstrumentUniverseSnapshot:
  """Normalized point-in-time universe consumed by runtime reconciliation."""

  mode: StrategyInstrumentUniverseMode
  instruments: tuple[str, ...]
  metadata: Dict[str, Dict[str, Any]]

  @classmethod
  def create(
    cls,
    mode: StrategyInstrumentUniverseMode,
    instruments: Sequence[str],
    metadata: Mapping[str, Mapping[str, Any]] | None = None,
  ) -> InstrumentUniverseSnapshot:
    normalized = tuple(
      sorted(
        {
          str(instrument or "").strip().upper()
          for instrument in instruments
          if str(instrument or "").strip()
        }
      )
    )
    source_metadata = {
      str(code or "").strip().upper(): dict(value)
      for code, value in dict(metadata or {}).items()
      if str(code or "").strip()
    }
    return cls(
      mode=mode,
      instruments=normalized,
      metadata={code: source_metadata.get(code, {}) for code in normalized},
    )


class InstrumentUniverseRequest:
  """Marker base for typed universe-provider input facts."""


@dataclass(frozen=True)
class StaticUniverseRequest(InstrumentUniverseRequest):
  instruments: Sequence[str]
  metadata: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class AccountHoldingPosition:
  instrument_code: str
  instrument_name: str = ""
  volume: int = 0
  available_volume: int = 0
  frozen_volume: int = 0
  average_price: float = 0.0
  market_value: float = 0.0


@dataclass(frozen=True)
class AccountInstrumentWork:
  instrument_code: str
  active_volume: int = 0
  pending_entry_intent_id: str = ""
  pending_exit_intent_id: str = ""

  @property
  def is_open(self) -> bool:
    return bool(
      self.active_volume > 0
      or self.pending_entry_intent_id
      or self.pending_exit_intent_id
    )


@dataclass(frozen=True)
class AccountHoldingsUniverseRequest(InstrumentUniverseRequest):
  enabled: bool
  positions: Sequence[AccountHoldingPosition]
  instrument_work: Sequence[AccountInstrumentWork] = ()
  ignored_instruments: Sequence[str] = ()
  force_draining: bool = False


@dataclass(frozen=True)
class RadarCandidatesUniverseRequest(InstrumentUniverseRequest):
  items: Sequence[Mapping[str, Any]]
  settings: Mapping[str, Any]
  enabled: bool = True
  preferences: Mapping[str, str] = field(default_factory=dict)
  sticky_instruments: Sequence[str] = ()
  force_preferred_instruments: Sequence[str] = ()
  arm_versions: Mapping[str, int] = field(default_factory=dict)


class InstrumentUniverseProvider(Protocol):
  mode: StrategyInstrumentUniverseMode
  request_type: type[InstrumentUniverseRequest]

  def resolve(
    self, request: InstrumentUniverseRequest
  ) -> InstrumentUniverseSnapshot: ...


class StaticInstrumentUniverseProvider:
  mode = StrategyInstrumentUniverseMode.STATIC
  request_type = StaticUniverseRequest

  def resolve(self, request: InstrumentUniverseRequest) -> InstrumentUniverseSnapshot:
    facts = _require_request(request, self.request_type, self.mode)
    return InstrumentUniverseSnapshot.create(
      self.mode,
      facts.instruments,
      facts.metadata,
    )


class AccountHoldingsInstrumentUniverseProvider:
  mode = StrategyInstrumentUniverseMode.ACCOUNT_HOLDINGS
  request_type = AccountHoldingsUniverseRequest

  def resolve(self, request: InstrumentUniverseRequest) -> InstrumentUniverseSnapshot:
    facts = _require_request(request, self.request_type, self.mode)
    ignored = {
      str(code or "").strip().upper()
      for code in facts.ignored_instruments
      if str(code or "").strip()
    }
    positions = {
      str(position.instrument_code or "").strip().upper(): position
      for position in facts.positions
      if _is_a_share_code(position.instrument_code) and position.volume > 0
    }
    open_work = {
      str(item.instrument_code or "").strip().upper(): item
      for item in facts.instrument_work
      if item.is_open and str(item.instrument_code or "").strip()
    }
    desired = {
      code
      for code in positions
      if facts.enabled and code not in ignored and not facts.force_draining
    }
    desired.update(open_work)

    metadata: Dict[str, Dict[str, Any]] = {}
    for code in sorted(desired):
      position = positions.get(code)
      volume = int(position.volume if position else 0)
      available = int(position.available_volume if position else 0)
      draining = bool(
        facts.force_draining or not facts.enabled or code in ignored or position is None
      )
      eligible = not draining and available >= 100
      reason = "ELIGIBLE"
      if draining:
        reason = "DRAINING_EXISTING_T_BATCH"
      elif available < 100:
        reason = "AVAILABLE_VOLUME_BELOW_100"
      metadata[code] = {
        "eligible": eligible,
        "reason": reason,
        "draining": draining,
        "instrument_name": str((position.instrument_name if position else "") or code),
        "position_shares": volume,
        "position_available_shares": available,
        "position_frozen_shares": max(
          0, int(position.frozen_volume if position else 0)
        ),
        "position_avg_price": float(position.average_price if position else 0.0),
        "position_market_value": float(position.market_value if position else 0.0),
      }
    return InstrumentUniverseSnapshot.create(self.mode, sorted(desired), metadata)


class RadarCandidatesInstrumentUniverseProvider:
  mode = StrategyInstrumentUniverseMode.RADAR_CANDIDATES
  request_type = RadarCandidatesUniverseRequest

  def resolve(self, request: InstrumentUniverseRequest) -> InstrumentUniverseSnapshot:
    facts = _require_request(request, self.request_type, self.mode)
    selection = select_limit_up_board_universe(
      facts.items,
      settings=facts.settings,
      enabled=facts.enabled,
      preferences=facts.preferences,
      sticky_codes=facts.sticky_instruments,
      force_preferred_codes=facts.force_preferred_instruments,
      arm_versions=facts.arm_versions,
    )
    return InstrumentUniverseSnapshot.create(
      self.mode,
      selection.instruments,
      selection.metadata,
    )


class InstrumentUniverseProviderRegistry:
  """Resolve a universe through the provider declared by a strategy mode."""

  def __init__(self, providers: Sequence[InstrumentUniverseProvider] = ()) -> None:
    self._providers: Dict[
      StrategyInstrumentUniverseMode, InstrumentUniverseProvider
    ] = {}
    for provider in providers:
      self.register(provider)

  @classmethod
  def with_defaults(cls) -> InstrumentUniverseProviderRegistry:
    return cls(
      (
        StaticInstrumentUniverseProvider(),
        AccountHoldingsInstrumentUniverseProvider(),
        RadarCandidatesInstrumentUniverseProvider(),
      )
    )

  def register(self, provider: InstrumentUniverseProvider) -> None:
    mode = _normalize_mode(provider.mode)
    if mode in self._providers:
      raise ValueError(f"标的池 Provider 已注册: {mode.value}")
    self._providers[mode] = provider

  def resolve(
    self,
    mode: StrategyInstrumentUniverseMode | str,
    request: InstrumentUniverseRequest,
  ) -> InstrumentUniverseSnapshot:
    normalized_mode = _normalize_mode(mode)
    provider = self._providers.get(normalized_mode)
    if provider is None:
      raise ValueError(f"未注册标的池 Provider: {normalized_mode.value}")
    if not isinstance(request, provider.request_type):
      raise TypeError(
        f"{normalized_mode.value} 标的池需要 "
        f"{provider.request_type.__name__}，实际为 {type(request).__name__}"
      )
    snapshot = provider.resolve(request)
    if snapshot.mode != normalized_mode:
      raise ValueError(
        f"标的池 Provider 返回了错误模式: expected={normalized_mode.value}, "
        f"actual={snapshot.mode.value}"
      )
    return snapshot


def _require_request(
  request: InstrumentUniverseRequest,
  request_type: type[Any],
  mode: StrategyInstrumentUniverseMode,
) -> Any:
  if not isinstance(request, request_type):
    raise TypeError(
      f"{mode.value} 标的池需要 {request_type.__name__}，"
      f"实际为 {type(request).__name__}"
    )
  return request


def _normalize_mode(
  mode: StrategyInstrumentUniverseMode | str,
) -> StrategyInstrumentUniverseMode:
  raw_mode = getattr(mode, "value", mode)
  try:
    return StrategyInstrumentUniverseMode(str(raw_mode))
  except ValueError as exc:
    raise ValueError(f"未知标的池模式: {raw_mode}") from exc


def _is_a_share_code(instrument_code: str) -> bool:
  return bool(
    re.fullmatch(
      r"\d{6}\.(SH|SZ|BJ)",
      str(instrument_code or "").strip().upper(),
    )
  )


instrument_universe_provider_registry = (
  InstrumentUniverseProviderRegistry.with_defaults()
)
