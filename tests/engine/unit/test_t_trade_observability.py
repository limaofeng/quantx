from __future__ import annotations

from typing import Any

import pytest
from quantx_domain.strategies.base import RuntimeStatePatch, StrategyOutput
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_engine.t_trade_observability import TTradeRuntimeObservability
from sqlalchemy.exc import OperationalError


def _snapshot(
  source_time_ms: int,
  *,
  generation: str = "g1",
  health: str = "READY",
  pullback_phase: str = "OBSERVING",
  momentum_phase: str = "OBSERVING",
  pullback_score: float | None = 40.0,
  momentum_score: float | None = 30.0,
  path: str = "PULLBACK_REBOUND",
  reasons: list[str] | None = None,
  external_blockers: list[str] | None = None,
  episode_id: str | None = None,
  candidate_id: str | None = None,
  candidate_status: str = "NONE",
  candidate_expires_at_ms: int | None = None,
) -> dict[str, Any]:
  return {
    "state_schema_version": 3,
    "source_time_ms": source_time_ms,
    "tick_ordinal": 1,
    "continuity_generation": generation,
    "selected_path": path,
    "data_health": health,
    "data_health_reasons": reasons or [],
    "policy_version": "policy-1",
    "pullback": {"phase": pullback_phase, "score": pullback_score},
    "momentum": {"phase": momentum_phase, "score": momentum_score},
    "preview_threshold": 55.0,
    "candidate_threshold": 72.0,
    "revalidate_threshold": 60.0,
    "rearm_threshold": 45.0,
    "external_blockers": external_blockers or [],
    "episode_id": episode_id,
    "candidate_id": candidate_id,
    "candidate_status": candidate_status,
    "candidate_expires_at_ms": candidate_expires_at_ms,
  }


def _output(
  snapshot: dict[str, Any],
  *,
  instrument_code: str = "600000.SH",
) -> StrategyOutput:
  return StrategyOutput(
    runtime_state_patch=RuntimeStatePatch(
      set={
        "instrument_states": {
          instrument_code: {
            "opportunity": {"latest_evaluation": snapshot},
          }
        }
      }
    )
  )


def _series(metrics: TTradeRuntimeObservability) -> dict[tuple[str, str], float]:
  result: dict[tuple[str, str], float] = {}
  for item in metrics.snapshot()["series"]:
    key = (str(item["metric"]), str(item["detail"]))
    result[key] = result.get(key, 0.0) + float(item["value"])
  return result


def test_observes_input_health_fsm_threshold_and_continuity_metrics() -> None:
  metrics = TTradeRuntimeObservability()

  assert metrics.observe_output(
    run_id="run-1",
    output=_output(
      _snapshot(
        1_000,
        health="WARMING",
        pullback_score=40.0,
      )
    ),
  )
  assert metrics.observe_output(
    run_id="run-1",
    output=_output(
      _snapshot(
        3_000,
        pullback_phase="PULLBACK_FORMING",
        pullback_score=58.0,
        external_blockers=["POSITION_NOT_ELIGIBLE"],
      )
    ),
  )
  assert metrics.observe_output(
    run_id="run-1",
    output=_output(
      _snapshot(
        3_000,
        pullback_phase="PULLBACK_FORMING",
        pullback_score=58.0,
        reasons=["DUPLICATE_SOURCE_IDENTITY"],
      )
    ),
  )
  assert metrics.observe_output(
    run_id="run-1",
    output=_output(
      _snapshot(
        5_000,
        generation="g2",
        health="CONTINUITY_LOST",
        pullback_score=None,
      )
    ),
  )

  observed = _series(metrics)
  assert sum(
    value for (metric, _), value in observed.items() if metric == "inputs_total"
  ) == 4
  assert observed[("duplicate_inputs_total", "DUPLICATE_SOURCE_IDENTITY")] == 1
  assert observed[("continuity_generation_changes_total", "EXPLICIT_GENERATION_CHANGE")] == 1
  assert observed[("health_observed_seconds_total", "WARMING")] == 2
  assert observed[("fsm_transitions_total", "PULLBACK:OBSERVING->PULLBACK_FORMING")] == 1
  assert observed[("threshold_crossings_total", "PULLBACK:PREVIEW:UP")] == 1
  assert observed[("external_gate_failures_total", "POSITION_NOT_ELIGIBLE")] == 1


def test_observes_candidate_lifecycle_and_ttl_without_instrument_labels() -> None:
  metrics = TTradeRuntimeObservability()
  metrics.observe_output(
    run_id="run-1",
    output=_output(_snapshot(10_000, pullback_score=70.0)),
  )
  metrics.observe_output(
    run_id="run-1",
    output=_output(
      _snapshot(
        11_000,
        pullback_score=75.0,
        episode_id="episode-1",
        candidate_id="candidate-1",
        candidate_status="AWAITING_APPROVAL",
        candidate_expires_at_ms=12_000,
      )
    ),
  )
  metrics.observe_output(
    run_id="run-1",
    output=_output(
      _snapshot(
        12_500,
        pullback_score=30.0,
        episode_id="episode-1",
        candidate_id="candidate-1",
        candidate_status="SUPPRESSED",
        candidate_expires_at_ms=12_000,
      )
    ),
  )

  snapshot = metrics.snapshot()
  observed = _series(metrics)
  assert observed[("episodes_total", "STARTED")] == 1
  assert observed[("candidates_total", "LATCHED")] == 1
  assert observed[("candidate_ttl_expirations_total", "EXPIRED")] == 1
  assert snapshot["activeStreamCount"] == 1
  assert "600000.SH" not in str(snapshot)
  assert "run-1" not in str(snapshot)
  metrics.forget_run("run-1")
  assert metrics.snapshot()["activeStreamCount"] == 0
  assert _series(metrics)[("candidates_total", "LATCHED")] == 1


def test_projection_metrics_and_invalid_values_are_explicit() -> None:
  metrics = TTradeRuntimeObservability()
  metrics.record_projection(
    lag_seconds=1.25,
    published=False,
    coalesced=True,
    path="MOMENTUM_ACCELERATION",
    health="READY",
    policy_version="policy-2",
  )
  observed = _series(metrics)

  assert observed[("projection_lag_seconds_sum", "TOTAL")] == 1.25
  assert observed[("projection_lag_seconds_count", "TOTAL")] == 1
  assert observed[("subscription_notices_total", "COALESCED")] == 1
  assert observed[("subscription_publish_total", "FAILED")] == 1
  with pytest.raises(ValueError):
    metrics.record_operation("bad", value=-1)


def test_policy_versions_share_one_prometheus_series() -> None:
  metrics = TTradeRuntimeObservability()

  metrics.record_operation(
    "inputs_total",
    path="PULLBACK_REBOUND",
    health="READY",
    policy_version="policy-hash-a",
  )
  metrics.record_operation(
    "inputs_total",
    path="PULLBACK_REBOUND",
    health="READY",
    policy_version="policy-hash-b",
  )

  snapshot = metrics.snapshot()
  assert snapshot["schemaVersion"] == 2
  assert snapshot["seriesCount"] == 1
  assert snapshot["series"][0]["value"] == 2
  assert "policyVersion" not in snapshot["series"][0]
  assert "policy-hash" not in str(snapshot)


def test_series_capacity_drops_new_keys_and_accounts_every_overflow_update() -> None:
  metrics = TTradeRuntimeObservability(series_capacity=2)

  metrics.record_operation("inputs_total", detail="FIRST")
  metrics.record_operation("inputs_total", detail="SECOND")
  metrics.record_operation("inputs_total", detail="THIRD")
  metrics.record_operation("inputs_total", detail="THIRD")
  metrics.record_operation("inputs_total", detail="FIRST")

  snapshot = metrics.snapshot()
  assert snapshot["seriesCapacity"] == 2
  assert snapshot["seriesCount"] == 2
  assert snapshot["seriesOverflowUpdatesTotal"] == 2
  values = {
    item["detail"]: item["value"]
    for item in snapshot["series"]
  }
  assert values == {"FIRST": 2, "SECOND": 1}


def test_accumulator_capacity_cannot_exceed_process_hard_limits() -> None:
  with pytest.raises(ValueError):
    TTradeRuntimeObservability(series_capacity=1_025)
  with pytest.raises(ValueError):
    TTradeRuntimeObservability(stream_capacity=4_097)


def test_stream_capacity_evicts_least_recently_observed_stream_with_accounting() -> None:
  metrics = TTradeRuntimeObservability(stream_capacity=2)

  metrics.observe_output(run_id="run-1", output=_output(_snapshot(1_000)))
  metrics.observe_output(run_id="run-2", output=_output(_snapshot(1_000)))
  metrics.observe_output(run_id="run-1", output=_output(_snapshot(2_000)))
  metrics.observe_output(run_id="run-3", output=_output(_snapshot(1_000)))

  snapshot = metrics.snapshot()
  assert snapshot["activeStreamCount"] == 2
  assert snapshot["streamCapacity"] == 2
  assert snapshot["streamEvictionsTotal"] == 1
  metrics.forget_run("run-2")
  assert metrics.snapshot()["activeStreamCount"] == 2
  metrics.forget_run("run-1")
  assert metrics.snapshot()["activeStreamCount"] == 1


@pytest.mark.asyncio
async def test_transient_evaluation_materialization_is_bounded_and_idempotent() -> None:
  class _RuntimeService:
    calls = 0

    async def materialize_evaluation(self, **_kwargs: Any) -> str:
      self.calls += 1
      if self.calls < 3:
        raise OperationalError("append", {}, ConnectionError("database"))
      return "materialized"

  metrics = TTradeRuntimeObservability()
  service = _RuntimeService()
  executor = StrategyExecutor(
    max_workers=1,
    opportunity_runtime_service=service,
    opportunity_observability=metrics,
  )
  try:
    result = await executor._materialize_t_trade_evaluation_with_retry(
      event={"event_key": "stable-event-key"},
      account_id="account-1",
      strategy_run_id="run-1",
      labels={
        "path": "PULLBACK_REBOUND",
        "health": "READY",
        "policy_version": "policy-1",
      },
      cas_committed=True,
    )
  finally:
    executor.thread_pool.shutdown(wait=False)

  assert result == "materialized"
  assert service.calls == 3
  observed = _series(metrics)
  assert observed[("evaluation_materialization_retries_total", "RETRY_1")] == 1
  assert observed[("evaluation_materialization_retries_total", "RETRY_2")] == 1
  assert observed[("evaluation_materialization_attempts_total", "SUCCESS")] == 1
