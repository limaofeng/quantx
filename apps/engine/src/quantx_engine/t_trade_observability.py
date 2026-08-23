"""Low-cardinality operational metrics for the stateful T-trade evaluator.

The Engine and API are separate processes, so registering ``prometheus_client``
counters in the Engine would make them invisible from the public ``/metrics``
endpoint.  This module instead keeps a bounded process-local accumulator.  The
Engine heartbeat publishes :meth:`snapshot`, and the API translates that
heartbeat payload into Prometheus gauges.

These metrics are observations only.  They never feed the evaluator, strategy
state, order sizing, or approval decisions.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Optional

_MAX_OBSERVED_SOURCE_GAP_SECONDS = 300.0
_MAX_RUNTIME_SERIES = 1_024
_MAX_ACTIVE_STREAMS = 4_096
_KNOWN_HEALTH = {
  "WARMING",
  "READY",
  "DEGRADED",
  "STALE",
  "CONTINUITY_LOST",
  "INSUFFICIENT",
}
_KNOWN_PATH = {"NONE", "PULLBACK_REBOUND", "MOMENTUM_ACCELERATION"}


def _label(value: Any, *, fallback: str = "UNKNOWN", maximum: int = 80) -> str:
  normalized = str(value or "").strip().upper()
  if not normalized:
    return fallback
  if len(normalized) > maximum or any(
    not (char.isalnum() or char in {"_", "-", ".", ":", ">"})
    for char in normalized
  ):
    return "OTHER"
  return normalized


def _optional_float(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if isfinite(normalized) else None


@dataclass(frozen=True)
class _ObservedEvaluation:
  source_time_ms: int
  tick_ordinal: int
  continuity_generation: str
  path: str
  health: str
  pullback_phase: str
  momentum_phase: str
  pullback_score: Optional[float]
  momentum_score: Optional[float]
  episode_id: Optional[str]
  candidate_id: Optional[str]
  candidate_status: str
  candidate_expires_at_ms: Optional[int]


class TTradeRuntimeObservability:
  """Bounded accumulator keyed only by run/instrument and low-cardinality labels."""

  schema_version = 2

  def __init__(
    self,
    *,
    series_capacity: int = _MAX_RUNTIME_SERIES,
    stream_capacity: int = _MAX_ACTIVE_STREAMS,
  ) -> None:
    if not 0 < series_capacity <= _MAX_RUNTIME_SERIES:
      raise ValueError(
        f"T-trade metric series capacity must be in 1..{_MAX_RUNTIME_SERIES}"
      )
    if not 0 < stream_capacity <= _MAX_ACTIVE_STREAMS:
      raise ValueError(
        f"T-trade observed stream capacity must be in 1..{_MAX_ACTIVE_STREAMS}"
      )
    self._series_capacity = int(series_capacity)
    self._stream_capacity = int(stream_capacity)
    self._series: Counter[tuple[str, str, str, str]] = Counter()
    self._streams: OrderedDict[
      tuple[str, str], _ObservedEvaluation
    ] = OrderedDict()
    self._series_overflow_updates_total = 0
    self._stream_evictions_total = 0

  def reset(self) -> None:
    """Reset process-local state; intended for deterministic unit tests."""

    self._series.clear()
    self._streams.clear()
    self._series_overflow_updates_total = 0
    self._stream_evictions_total = 0

  def forget_run(self, run_id: str) -> None:
    normalized = str(run_id or "").strip()
    if not normalized:
      return
    for key in [key for key in self._streams if key[0] == normalized]:
      self._streams.pop(key, None)

  def observe_output(self, *, run_id: str, output: Any) -> bool:
    """Observe one evaluator output without changing or retaining its payload."""

    patch = getattr(output, "runtime_state_patch", None)
    patch_set = getattr(patch, "set", None)
    if not isinstance(patch_set, Mapping):
      return False
    states = patch_set.get("instrument_states")
    if not isinstance(states, Mapping):
      return False

    observed_any = False
    for instrument_code, raw_state in states.items():
      if not isinstance(raw_state, Mapping):
        continue
      opportunity = raw_state.get("opportunity")
      if not isinstance(opportunity, Mapping):
        continue
      snapshot = opportunity.get("latest_evaluation")
      if not isinstance(snapshot, Mapping):
        continue
      if int(snapshot.get("state_schema_version", 0) or 0) < 3:
        continue
      observed = self._from_snapshot(snapshot)
      if observed is None:
        continue
      self._observe(
        run_id=str(run_id or "").strip(),
        instrument_code=str(instrument_code or "").strip().upper(),
        current=observed,
        snapshot=snapshot,
      )
      observed_any = True
    return observed_any

  def record_operation(
    self,
    metric: str,
    *,
    path: Any = "NONE",
    health: Any = "UNKNOWN",
    policy_version: Any = "UNKNOWN",
    detail: Any = "TOTAL",
    value: float = 1.0,
  ) -> None:
    """Accumulate one bounded operational series.

    ``policy_version`` is deliberately accepted but never retained or exported:
    exact policy identities belong to durable evaluation diagnostics, while a
    Prometheus label would create one time series per configuration hash.
    """

    normalized_value = _optional_float(value)
    if normalized_value is None or normalized_value < 0:
      raise ValueError("T-trade metric value must be a finite non-negative number")
    self._inc(
      metric,
      path=self._path_label(path),
      health=self._health_label(health),
      detail=_label(detail, fallback="TOTAL"),
      value=normalized_value,
    )

  def record_projection(
    self,
    *,
    lag_seconds: float,
    published: bool,
    coalesced: bool,
    path: Any = "NONE",
    health: Any = "UNKNOWN",
    policy_version: Any = "UNKNOWN",
  ) -> None:
    lag = max(0.0, float(lag_seconds))
    labels = {
      "path": path,
      "health": health,
      "policy_version": policy_version,
    }
    self.record_operation(
      "projection_lag_seconds_sum", detail="TOTAL", value=lag, **labels
    )
    self.record_operation(
      "projection_lag_seconds_count", detail="TOTAL", value=1, **labels
    )
    self.record_operation(
      "subscription_notices_total",
      detail="COALESCED" if coalesced else "IMMEDIATE",
      **labels,
    )
    self.record_operation(
      "subscription_publish_total",
      detail="SUCCESS" if published else "FAILED",
      **labels,
    )

  def snapshot(self) -> dict[str, Any]:
    series = [
      {
        "metric": metric,
        "path": path,
        "health": health,
        "detail": detail,
        "value": value,
      }
      for (metric, path, health, detail), value in sorted(self._series.items())
    ]
    return {
      "schemaVersion": self.schema_version,
      "activeStreamCount": len(self._streams),
      "streamCapacity": self._stream_capacity,
      "streamEvictionsTotal": self._stream_evictions_total,
      "seriesCount": len(series),
      "seriesCapacity": self._series_capacity,
      "seriesOverflowUpdatesTotal": self._series_overflow_updates_total,
      "maxObservedSourceGapSeconds": _MAX_OBSERVED_SOURCE_GAP_SECONDS,
      "series": series,
    }

  def _observe(
    self,
    *,
    run_id: str,
    instrument_code: str,
    current: _ObservedEvaluation,
    snapshot: Mapping[str, Any],
  ) -> None:
    if not run_id or not instrument_code:
      return
    labels = {
      "path": current.path,
      "health": current.health,
    }
    self.record_operation("inputs_total", **labels)
    reasons = {
      _label(reason)
      for reason in list(snapshot.get("data_health_reasons") or [])
      if reason
    }
    if "DUPLICATE_SOURCE_IDENTITY" in reasons:
      self.record_operation(
        "duplicate_inputs_total", detail="DUPLICATE_SOURCE_IDENTITY", **labels
      )
    if "OUT_OF_ORDER_SOURCE_IDENTITY" in reasons:
      self.record_operation(
        "out_of_order_inputs_total", detail="OUT_OF_ORDER_SOURCE_IDENTITY", **labels
      )

    key = (run_id, instrument_code)
    previous = self._streams.get(key)
    if previous is not None:
      self._observe_interval(previous, current)
      self._observe_transitions(previous, current, snapshot)
    self._remember_stream(key, current)

    for blocker in {
      _label(value)
      for value in list(snapshot.get("external_blockers") or [])
      if value
    }:
      self.record_operation(
        "external_gate_failures_total", detail=blocker, **labels
      )

  def _observe_interval(
    self,
    previous: _ObservedEvaluation,
    current: _ObservedEvaluation,
  ) -> None:
    labels = {
      "path": previous.path,
      "health": previous.health,
    }
    if previous.continuity_generation != current.continuity_generation:
      self.record_operation(
        "continuity_generation_changes_total",
        detail="EXPLICIT_GENERATION_CHANGE",
        **labels,
      )
      return
    delta_ms = current.source_time_ms - previous.source_time_ms
    if delta_ms <= 0:
      return
    raw_seconds = delta_ms / 1000.0
    seconds = min(raw_seconds, _MAX_OBSERVED_SOURCE_GAP_SECONDS)
    self.record_operation(
      "health_observed_seconds_total",
      detail=previous.health,
      value=seconds,
      **labels,
    )
    if previous.health == "READY":
      self.record_operation(
        "ready_observed_seconds_total", value=seconds, **labels
      )
    if raw_seconds > seconds:
      self.record_operation(
        "duration_gap_truncations_total",
        detail="SOURCE_GAP_OVER_300_SECONDS",
        **labels,
      )
    self.record_operation(
      "fsm_dwell_seconds_total",
      detail=f"PULLBACK:{previous.pullback_phase}",
      value=seconds,
      **labels,
    )
    self.record_operation(
      "fsm_dwell_seconds_total",
      detail=f"MOMENTUM:{previous.momentum_phase}",
      value=seconds,
      **labels,
    )

  def _observe_transitions(
    self,
    previous: _ObservedEvaluation,
    current: _ObservedEvaluation,
    snapshot: Mapping[str, Any],
  ) -> None:
    labels = {
      "path": current.path,
      "health": current.health,
    }
    for branch, old_phase, new_phase in (
      ("PULLBACK", previous.pullback_phase, current.pullback_phase),
      ("MOMENTUM", previous.momentum_phase, current.momentum_phase),
    ):
      if old_phase != new_phase:
        self.record_operation(
          "fsm_transitions_total",
          detail=f"{branch}:{old_phase}->{new_phase}",
          **labels,
        )

    thresholds = {
      "REARM": _optional_float(snapshot.get("rearm_threshold")),
      "PREVIEW": _optional_float(snapshot.get("preview_threshold")),
      "REVALIDATE": _optional_float(snapshot.get("revalidate_threshold")),
      "CANDIDATE": _optional_float(snapshot.get("candidate_threshold")),
    }
    for branch, old_score, new_score in (
      ("PULLBACK", previous.pullback_score, current.pullback_score),
      ("MOMENTUM", previous.momentum_score, current.momentum_score),
    ):
      if old_score is None or new_score is None:
        continue
      for threshold_name, threshold in thresholds.items():
        if threshold is None:
          continue
        direction = (
          "UP"
          if old_score < threshold <= new_score
          else "DOWN"
          if old_score >= threshold > new_score
          else None
        )
        if direction is not None:
          self.record_operation(
            "threshold_crossings_total",
            detail=f"{branch}:{threshold_name}:{direction}",
            **labels,
          )

    if current.episode_id and current.episode_id != previous.episode_id:
      self.record_operation("episodes_total", detail="STARTED", **labels)
    if current.candidate_id and current.candidate_id != previous.candidate_id:
      self.record_operation("candidates_total", detail="LATCHED", **labels)
    if current.candidate_status != previous.candidate_status:
      self.record_operation(
        "candidate_status_transitions_total",
        detail=f"{previous.candidate_status}->{current.candidate_status}",
        **labels,
      )
      if current.candidate_status == "SUPPRESSED":
        self.record_operation(
          "candidate_suppressions_total", detail="SUPPRESSED", **labels
        )
      if previous.candidate_status == "REARMING" and current.candidate_status == "NONE":
        self.record_operation("rearm_completions_total", detail="COMPLETED", **labels)
    if (
      previous.candidate_status == "AWAITING_APPROVAL"
      and current.candidate_status in {"SUPPRESSED", "REARMING"}
      and previous.candidate_expires_at_ms is not None
      and current.source_time_ms >= previous.candidate_expires_at_ms
    ):
      self.record_operation("candidate_ttl_expirations_total", detail="EXPIRED", **labels)

  @staticmethod
  def _from_snapshot(snapshot: Mapping[str, Any]) -> Optional[_ObservedEvaluation]:
    source_time_ms = int(snapshot.get("source_time_ms", 0) or 0)
    if source_time_ms <= 0:
      return None
    path = _label(snapshot.get("selected_path"), fallback="NONE")
    if path not in _KNOWN_PATH:
      path = "NONE"
    health = _label(snapshot.get("data_health"))
    if health not in _KNOWN_HEALTH:
      health = "UNKNOWN"
    pullback = snapshot.get("pullback")
    momentum = snapshot.get("momentum")
    pullback = pullback if isinstance(pullback, Mapping) else {}
    momentum = momentum if isinstance(momentum, Mapping) else {}
    expires_at = snapshot.get("candidate_expires_at_ms")
    try:
      candidate_expires_at_ms = int(expires_at) if expires_at is not None else None
    except (TypeError, ValueError, OverflowError):
      candidate_expires_at_ms = None
    return _ObservedEvaluation(
      source_time_ms=source_time_ms,
      tick_ordinal=int(snapshot.get("tick_ordinal", 0) or 0),
      continuity_generation=_label(snapshot.get("continuity_generation")),
      path=path,
      health=health,
      pullback_phase=_label(pullback.get("phase")),
      momentum_phase=_label(momentum.get("phase")),
      pullback_score=_optional_float(pullback.get("score")),
      momentum_score=_optional_float(momentum.get("score")),
      episode_id=str(snapshot.get("episode_id") or "") or None,
      candidate_id=str(snapshot.get("candidate_id") or "") or None,
      candidate_status=_label(snapshot.get("candidate_status"), fallback="NONE"),
      candidate_expires_at_ms=candidate_expires_at_ms,
    )

  def _inc(
    self,
    metric: Any,
    *,
    path: str,
    health: str,
    detail: str,
    value: float,
  ) -> None:
    normalized_metric = _label(metric, fallback="UNKNOWN").lower()
    key = (normalized_metric, path, health, detail)
    if key not in self._series and len(self._series) >= self._series_capacity:
      self._series_overflow_updates_total += 1
      return
    self._series[key] += value

  def _remember_stream(
    self,
    key: tuple[str, str],
    current: _ObservedEvaluation,
  ) -> None:
    if key in self._streams:
      self._streams.move_to_end(key)
    elif len(self._streams) >= self._stream_capacity:
      self._streams.popitem(last=False)
      self._stream_evictions_total += 1
    self._streams[key] = current

  @staticmethod
  def _path_label(value: Any) -> str:
    normalized = _label(value, fallback="NONE")
    return normalized if normalized in _KNOWN_PATH else "NONE"

  @staticmethod
  def _health_label(value: Any) -> str:
    normalized = _label(value)
    return normalized if normalized in _KNOWN_HEALTH else "UNKNOWN"


t_trade_runtime_observability = TTradeRuntimeObservability()
