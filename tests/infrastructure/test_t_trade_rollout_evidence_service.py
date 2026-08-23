from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from quantx_infrastructure.services.t_trade_rollout_evidence_service import (
  MAX_REPLAY_ROWS,
  V3_ROLLOUT_GATE_CODES,
  TTradeV3RolloutEvidenceEvaluator,
)

_UNSET = object()
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _weekdays(start: date, count: int) -> list[date]:
  result: list[date] = []
  current = start
  while len(result) < count:
    if current.weekday() < 5:
      result.append(current)
    current += timedelta(days=1)
  return result


def _snapshot(
  candidate_id: str,
  *,
  trade_date: date,
  episode_id: str | None = None,
  source_time_ms: int | None = None,
  evaluated_at_ms: int | None = None,
) -> dict:
  default_source = datetime.combine(trade_date, time(10, 0), tzinfo=_SHANGHAI)
  source_time_ms = source_time_ms or int(default_source.timestamp() * 1_000)
  evaluated_at_ms = evaluated_at_ms or source_time_ms + 1
  return {
    "candidate_id": candidate_id,
    "candidate_fingerprint": f"fingerprint-{candidate_id}",
    "instrument_code": "600000.SH",
    "episode_id": episode_id or f"episode-{candidate_id}",
    "trade_date": trade_date.isoformat(),
    "source_time_ms": source_time_ms,
    "evaluated_at_ms": evaluated_at_ms,
    "tick_ordinal": source_time_ms,
    "continuity_generation": "generation-1",
    "policy_version": "t_trade_opportunity_v3.0.0",
    "feature_schema_version": "3",
    "profile_version": "profile-v1",
    "score_contributions": [{"name": "PULLBACK_DEPTH", "points": 20.0}],
    "hard_gates": [
      {"code": "DATA_READY", "passed": True},
      {"code": "REFERENCE_PROFILE_CAUSAL", "passed": True},
    ],
    "blockers": [],
    "pending_entry_intent_id": None,
  }


def _evaluation(
  candidate_id: str,
  *,
  run_id: str,
  trade_date: date,
  episode_id: str | None = None,
  source_time_ms: int | None = None,
  evaluated_at_ms: int | None = None,
) -> SimpleNamespace:
  snapshot = _snapshot(
    candidate_id,
    trade_date=trade_date,
    episode_id=episode_id,
    source_time_ms=source_time_ms,
    evaluated_at_ms=evaluated_at_ms,
  )
  evaluated_at = datetime.fromtimestamp(
    snapshot["evaluated_at_ms"] / 1_000,
    _SHANGHAI,
  ).replace(tzinfo=None)
  return SimpleNamespace(
    candidate_id=candidate_id,
    strategy_run_id=run_id,
    instrument_code="600000.SH",
    schema_version="3",
    record_kind="MATERIAL",
    evaluated_at=evaluated_at,
    window_started_at=None,
    window_ended_at=None,
    payload={"signal_snapshot": snapshot},
  )


def _coverage_evaluations(
  *,
  run_id: str,
  trading_dates: list[date],
) -> list[SimpleNamespace]:
  rows: list[SimpleNamespace] = []
  for trading_date in trading_dates:
    for session_start, session_end in (
      (time(9, 30), time(11, 30)),
      (time(13, 0), time(15, 0)),
    ):
      current = datetime.combine(trading_date, session_start)
      ended_at = datetime.combine(trading_date, session_end)
      while current < ended_at:
        window_ended_at = min(current + timedelta(minutes=10), ended_at)
        source_time_ms = int(
          window_ended_at.replace(tzinfo=_SHANGHAI).timestamp() * 1_000
        )
        snapshot = _snapshot(
          f"coverage-{run_id}",
          trade_date=trading_date,
          source_time_ms=source_time_ms,
          evaluated_at_ms=source_time_ms,
        )
        rows.append(
          SimpleNamespace(
            candidate_id=None,
            strategy_run_id=run_id,
            instrument_code="600000.SH",
            schema_version="3",
            record_kind="COALESCED_DIAGNOSTIC",
            evaluated_at=window_ended_at,
            window_started_at=current,
            window_ended_at=window_ended_at,
            payload={"signal_snapshot": snapshot},
          )
        )
        current = window_ended_at
  return rows


def _outcome(candidate_id: str, *, run_id: str, trade_date: date) -> SimpleNamespace:
  return SimpleNamespace(
    candidate_id=candidate_id,
    strategy_run_id=run_id,
    instrument_code="600000.SH",
    candidate_fingerprint=f"fingerprint-{candidate_id}",
    policy_version="t_trade_opportunity_v3.0.0",
    status="MATURED",
    candidate_at=datetime.combine(trade_date, datetime.min.time()),
  )


def _intent(
  intent_id: str,
  *,
  candidate_id: str,
  run_id: str,
) -> SimpleNamespace:
  return SimpleNamespace(
    id=intent_id,
    strategy_run_id=run_id,
    intent_metadata={
      "candidate_id": candidate_id,
      "candidate_fingerprint": f"fingerprint-{candidate_id}",
    },
  )


def _replay_row(
  dates: list[date],
  *,
  run_id: str = "replay-run",
  version: int = 1,
  completed_at: datetime | None = None,
) -> tuple[SimpleNamespace, ...]:
  return (
    SimpleNamespace(
      id=run_id,
      parameters={"t_trade_replay": True},
      updated_at=completed_at,
    ),
    SimpleNamespace(status="COMPLETED"),
    SimpleNamespace(
      id=f"backtest-{run_id}",
      version=version,
      status="COMPLETED",
      end_time=completed_at,
      created_at=completed_at,
      metrics={
        "t_trade_replay": {
          "data_quality": "OK",
          "methodology": {"tick_read_audit": {"verified_windows": 20, "issues": []}},
          "rollout_evidence": {
            "strict_causal": True,
            "trading_dates": [item.isoformat() for item in dates],
            "market_scenario_coverage": {
              "normal_trading_dates": [item.isoformat() for item in dates[:-1]],
              "abnormal_trading_dates": [dates[-1].isoformat()],
            },
          },
          "summary": {"t_net_profit": 1.0, "total_fees": 0.1},
        }
      },
    ),
  )


def _paper_run(
  trading_dates: list[date],
  *,
  run_id: str = "paper-run",
  status: str = "RUNNING",
  stop_time: datetime | None = None,
  error_message: str | None = None,
) -> SimpleNamespace:
  return SimpleNamespace(
    id=run_id,
    mode="PAPER",
    status=status,
    start_time=datetime.combine(trading_dates[0], time(9, 25)),
    stop_time=stop_time,
    error_message=error_message,
  )


def _rollout() -> SimpleNamespace:
  return SimpleNamespace(
    max_active_batches=1,
    max_batch_volume=100,
    max_order_amount=20_000.0,
    max_total_exposure_pct=0.02,
  )


def _global_config() -> SimpleNamespace:
  return SimpleNamespace(
    mode="live",
    auto_exit_acknowledged=True,
    settings={
      "max_concurrent_batches": 1,
      "max_total_t_exposure_pct": 0.02,
      "max_trade_amount": 12_000.0,
    },
  )


def _evaluate(
  *,
  paper_runs: list[SimpleNamespace] | None = None,
  paper_evaluations: list[SimpleNamespace] | None = None,
  paper_outcomes: list[SimpleNamespace] | None = None,
  paper_intents: list[SimpleNamespace] | None = None,
  paper_calendar: list[date] | None = None,
  replay_rows: list[tuple[SimpleNamespace, ...]] | None = None,
  replay_evaluations: list[SimpleNamespace] | None = None,
  replay_outcomes: list[SimpleNamespace] | None = None,
  replay_intents: list[SimpleNamespace] | None = None,
  paper_runs_truncated: bool = False,
  paper_evidence_truncated: bool = False,
  rollout: SimpleNamespace | None | object = _UNSET,
  global_config: SimpleNamespace | None | object = _UNSET,
  live_run: SimpleNamespace | None | object = _UNSET,
  review_events: list[SimpleNamespace] | None = None,
  review_events_truncated: bool = False,
) -> dict:
  replay_dates = _weekdays(date(2026, 7, 6), 20)
  default_paper_runs = [_paper_run(paper_calendar)] if paper_calendar else []
  return TTradeV3RolloutEvidenceEvaluator().evaluate_records(
    account_id="account-1",
    rollout=_rollout() if rollout is _UNSET else rollout,
    global_config=_global_config() if global_config is _UNSET else global_config,
    live_run=(
      SimpleNamespace(mode="LIVE", instruments=["600000.SH"])
      if live_run is _UNSET
      else live_run
    ),
    replay_rows=list(replay_rows or [_replay_row(replay_dates)]),
    replay_evaluations=list(replay_evaluations or []),
    replay_outcomes=list(replay_outcomes or []),
    replay_intents=list(replay_intents or []),
    paper_runs=list(default_paper_runs if paper_runs is None else paper_runs),
    paper_evaluations=list(paper_evaluations or []),
    paper_outcomes=list(paper_outcomes or []),
    paper_intents=list(paper_intents or []),
    paper_calendar=paper_calendar,
    replay_rows_truncated=False,
    replay_evidence_truncated=False,
    paper_runs_truncated=paper_runs_truncated,
    paper_evidence_truncated=paper_evidence_truncated,
    review_events=list(review_events or []),
    review_events_truncated=review_events_truncated,
    query_available=True,
  )


def _complete_paper_evidence() -> tuple[
  list[SimpleNamespace], list[SimpleNamespace], list[date]
]:
  paper_days = _weekdays(date(2026, 8, 3), 5)
  evaluations: list[SimpleNamespace] = []
  outcomes: list[SimpleNamespace] = []
  for day_index, trade_date in enumerate(paper_days):
    for candidate_index in range(4):
      candidate_id = f"paper-{day_index}-{candidate_index}"
      evaluations.append(
        _evaluation(candidate_id, run_id="paper-run", trade_date=trade_date)
      )
      outcomes.append(_outcome(candidate_id, run_id="paper-run", trade_date=trade_date))
  evaluations.extend(
    _coverage_evaluations(run_id="paper-run", trading_dates=paper_days)
  )
  return evaluations, outcomes, paper_days


def _passed(result: dict) -> dict[str, bool]:
  return {item["code"]: bool(item["passed"]) for item in result["checks"]}


class _DatabaseResult:
  """Small SQLAlchemy-result double for the evaluator's two result shapes."""

  def __init__(
    self, *, rows: list[object] | None = None, scalars: list[object] | None = None
  ):
    self._rows = list(rows or [])
    self._scalars = list(scalars or [])

  def all(self) -> list[object]:
    return list(self._rows)

  def scalars(self) -> SimpleNamespace:
    return SimpleNamespace(all=lambda: list(self._scalars))


def test_evaluator_accepts_one_complete_durable_v3_evidence_set() -> None:
  paper_evaluations, paper_outcomes, paper_days = _complete_paper_evidence()
  replay_evaluation = _evaluation(
    "replay-candidate",
    run_id="replay-run",
    trade_date=date(2026, 7, 6),
  )
  replay_outcome = _outcome(
    "replay-candidate",
    run_id="replay-run",
    trade_date=date(2026, 7, 6),
  )

  result = _evaluate(
    paper_evaluations=paper_evaluations,
    paper_outcomes=paper_outcomes,
    paper_calendar=paper_days,
    replay_evaluations=[replay_evaluation],
    replay_outcomes=[replay_outcome],
  )

  passed = _passed(result)
  assert set(passed) == V3_ROLLOUT_GATE_CODES
  assert all(
    value for code, value in passed.items() if code != "V3_OPERATOR_REVIEW_CONFIRMED"
  )
  assert passed["V3_OPERATOR_REVIEW_CONFIRMED"] is False
  assert result["summary"]["paper"]["matured_count"] == 20
  assert result["summary"]["fingerprint"]
  assert result["summary"]["operator_review"]["policy_version"] == 0


def test_evaluator_returns_structured_fail_closed_checks_for_absent_evidence() -> None:
  result = _evaluate(
    paper_calendar=[],
    global_config=None,
    live_run=None,
  )

  passed = _passed(result)
  assert set(passed) == V3_ROLLOUT_GATE_CODES
  assert passed["V3_EVIDENCE_QUERY_AVAILABLE"] is True
  assert passed["V3_PAPER_5_CONSECUTIVE_TRADING_DAYS"] is False
  assert passed["V3_PAPER_20_COMPLETED_CANDIDATE_LIFECYCLES"] is False
  assert passed["V3_PAPER_EPISODE_DUPLICATES_ZERO"] is False
  assert passed["V3_REPLAY_EPISODE_DUPLICATES_ZERO"] is False
  assert passed["V3_CANARY_LIMITS_CONFIGURED"] is False
  assert result["summary"]["paper"]["consecutive_days_message"]


def test_evaluator_fails_paper_quality_gate_on_future_data() -> None:
  paper_evaluations, paper_outcomes, paper_days = _complete_paper_evidence()
  snapshot = paper_evaluations[0].payload["signal_snapshot"]
  snapshot["source_time_ms"] = snapshot["evaluated_at_ms"] + 1

  result = _evaluate(
    paper_evaluations=paper_evaluations,
    paper_outcomes=paper_outcomes,
    paper_calendar=paper_days,
  )

  assert _passed(result)["V3_PAPER_FUTURE_DATA_ZERO"] is False


def test_evaluator_does_not_count_one_paper_point_per_day_as_session_coverage() -> None:
  paper_days = _weekdays(date(2026, 8, 3), 5)
  evaluations = [
    _evaluation(f"paper-{index}", run_id="paper-run", trade_date=trading_date)
    for index, trading_date in enumerate(paper_days)
  ]
  outcomes = [
    _outcome(f"paper-{index}", run_id="paper-run", trade_date=trading_date)
    for index, trading_date in enumerate(paper_days)
  ]

  result = _evaluate(
    paper_evaluations=evaluations,
    paper_outcomes=outcomes,
    paper_calendar=paper_days,
  )

  assert _passed(result)["V3_PAPER_5_CONSECUTIVE_TRADING_DAYS"] is False
  coverage = result["summary"]["paper"]["intraday_coverage"]
  assert coverage["covered_session_count"] == 0
  assert coverage["required_session_count"] == 10


def test_evaluator_requires_healthy_paper_run_lifecycle_around_full_coverage() -> None:
  evaluations, outcomes, paper_days = _complete_paper_evidence()
  failed_run = _paper_run(
    paper_days,
    status="ERROR",
    stop_time=datetime.combine(paper_days[-1], time(15, 1)),
    error_message="runtime failed",
  )

  result = _evaluate(
    paper_runs=[failed_run],
    paper_evaluations=evaluations,
    paper_outcomes=outcomes,
    paper_calendar=paper_days,
  )

  assert _passed(result)["V3_PAPER_5_CONSECUTIVE_TRADING_DAYS"] is False
  assert result["summary"]["paper"]["intraday_coverage"]["lifecycle_ready"] is False


def test_evaluator_fails_paper_day_gate_when_run_query_is_truncated() -> None:
  evaluations, outcomes, paper_days = _complete_paper_evidence()

  result = _evaluate(
    paper_evaluations=evaluations,
    paper_outcomes=outcomes,
    paper_calendar=paper_days,
    paper_runs_truncated=True,
  )

  assert _passed(result)["V3_PAPER_5_CONSECUTIVE_TRADING_DAYS"] is False
  assert "\u622a\u65ad" in result["summary"]["paper"]["consecutive_days_message"]


def test_evaluator_fails_trace_gate_for_multiple_intents_from_one_candidate() -> None:
  evaluations, outcomes, paper_days = _complete_paper_evidence()
  duplicate_intents = [
    _intent(
      "intent-1",
      candidate_id="paper-0-0",
      run_id="paper-run",
    ),
    _intent(
      "intent-2",
      candidate_id="paper-0-0",
      run_id="paper-run",
    ),
  ]

  result = _evaluate(
    paper_evaluations=evaluations,
    paper_outcomes=outcomes,
    paper_intents=duplicate_intents,
    paper_calendar=paper_days,
  )

  assert _passed(result)["V3_PAPER_CANDIDATE_TRACE_COMPLETE"] is False
  assert result["summary"]["paper"]["duplicate_intent_count"] == 1
  assert result["summary"]["paper"]["trace_broken_count"] == 1


def test_evaluator_selects_single_replay_with_most_passing_gates() -> None:
  replay_dates = _weekdays(date(2026, 7, 6), 20)
  good_evaluation = _evaluation(
    "good-candidate",
    run_id="a-good-run",
    trade_date=replay_dates[0],
  )
  bad_evaluation = _evaluation(
    "bad-candidate",
    run_id="z-bad-run",
    trade_date=replay_dates[0],
  )
  bad_evaluation.payload["signal_snapshot"]["source_time_ms"] = (
    bad_evaluation.payload["signal_snapshot"]["evaluated_at_ms"] + 1
  )

  result = _evaluate(
    replay_rows=[
      _replay_row(
        replay_dates,
        run_id="a-good-run",
        completed_at=datetime(2026, 8, 1, 12, 0),
      ),
      _replay_row(
        replay_dates,
        run_id="z-bad-run",
        completed_at=datetime(2026, 8, 2, 12, 0),
      ),
    ],
    replay_evaluations=[good_evaluation, bad_evaluation],
    replay_outcomes=[
      _outcome(
        "good-candidate",
        run_id="a-good-run",
        trade_date=replay_dates[0],
      ),
      _outcome(
        "bad-candidate",
        run_id="z-bad-run",
        trade_date=replay_dates[0],
      ),
    ],
  )

  replay = result["summary"]["replay"]
  assert replay["run_id"] == "a-good-run"
  assert replay["selection_gate_pass_count"] == 6
  assert replay["future_data_violation_count"] == 0


def test_evaluator_breaks_equal_replay_quality_tie_by_completion_time() -> None:
  replay_dates = _weekdays(date(2026, 7, 6), 20)
  evaluations = [
    _evaluation("old-candidate", run_id="z-old-run", trade_date=replay_dates[0]),
    _evaluation("new-candidate", run_id="a-new-run", trade_date=replay_dates[0]),
  ]
  outcomes = [
    _outcome("old-candidate", run_id="z-old-run", trade_date=replay_dates[0]),
    _outcome("new-candidate", run_id="a-new-run", trade_date=replay_dates[0]),
  ]

  result = _evaluate(
    replay_rows=[
      _replay_row(
        replay_dates,
        run_id="z-old-run",
        version=99,
        completed_at=datetime(2026, 8, 1, 12, 0),
      ),
      _replay_row(
        replay_dates,
        run_id="a-new-run",
        version=1,
        completed_at=datetime(2026, 8, 2, 12, 0),
      ),
    ],
    replay_evaluations=evaluations,
    replay_outcomes=outcomes,
  )

  replay = result["summary"]["replay"]
  assert replay["run_id"] == "a-new-run"
  assert replay["backtest_version"] == 1


def test_evaluator_requires_limited_single_instrument_canary_configuration() -> None:
  paper_evaluations, paper_outcomes, paper_days = _complete_paper_evidence()
  result = _evaluate(
    paper_evaluations=paper_evaluations,
    paper_outcomes=paper_outcomes,
    paper_calendar=paper_days,
    live_run=SimpleNamespace(mode="LIVE", instruments=["600000.SH", "000001.SZ"]),
  )

  assert _passed(result)["V3_CANARY_LIMITS_CONFIGURED"] is False
  assert result["summary"]["canary"]["instrument_count"] == 2


def test_evaluator_requires_matching_durable_operator_review_event() -> None:
  paper_evaluations, paper_outcomes, paper_days = _complete_paper_evidence()
  replay_evaluation = _evaluation(
    "replay-candidate",
    run_id="replay-run",
    trade_date=date(2026, 7, 6),
  )
  replay_outcome = _outcome(
    "replay-candidate",
    run_id="replay-run",
    trade_date=date(2026, 7, 6),
  )
  rollout = _rollout()
  rollout.policy_version = 7
  rollout.last_snapshot_id = "snapshot-1"
  base = _evaluate(
    paper_evaluations=paper_evaluations,
    paper_outcomes=paper_outcomes,
    paper_calendar=paper_days,
    replay_evaluations=[replay_evaluation],
    replay_outcomes=[replay_outcome],
    rollout=rollout,
  )
  review = base["summary"]["operator_review"]
  event = SimpleNamespace(
    event_id="review-event-1",
    event_type="CANARY_ACTIVATED",
    actor_user_id="user-1",
    snapshot_id="snapshot-1",
    details={
      "operationId": "challenge-1",
      "operatorReview": {
        "acknowledged": True,
        "confirmation": "AUTHENTICATED_IDEMPOTENT_ACTIVATION",
        "evidenceFingerprint": review["review_evidence_fingerprint"],
        "policyVersion": 7,
        "snapshotId": "snapshot-1",
        "operationId": "challenge-1",
      },
    },
  )

  result = _evaluate(
    paper_evaluations=paper_evaluations,
    paper_outcomes=paper_outcomes,
    paper_calendar=paper_days,
    replay_evaluations=[replay_evaluation],
    replay_outcomes=[replay_outcome],
    rollout=rollout,
    review_events=[event],
  )

  assert _passed(result)["V3_OPERATOR_REVIEW_CONFIRMED"] is True
  assert result["summary"]["operator_review"]["event_id"] == "review-event-1"


def test_evaluator_fails_closed_when_review_event_query_is_truncated() -> None:
  paper_evaluations, paper_outcomes, paper_days = _complete_paper_evidence()
  result = _evaluate(
    paper_evaluations=paper_evaluations,
    paper_outcomes=paper_outcomes,
    paper_calendar=paper_days,
    rollout=SimpleNamespace(
      **vars(_rollout()), policy_version=7, last_snapshot_id="snapshot-1"
    ),
    review_events_truncated=True,
  )

  assert _passed(result)["V3_OPERATOR_REVIEW_CONFIRMED"] is False
  assert "\u622a\u65ad" in result["summary"]["operator_review"]["message"]


@pytest.mark.asyncio
async def test_evaluator_converts_database_errors_to_blocked_checks(caplog) -> None:
  class BrokenDatabase:
    async def execute(self, *_args, **_kwargs):
      raise RuntimeError("database unavailable")

  result = await TTradeV3RolloutEvidenceEvaluator().evaluate(
    BrokenDatabase(),
    account_id="account-1",
    rollout=None,
  )

  passed = _passed(result)
  assert set(passed) == V3_ROLLOUT_GATE_CODES
  assert not any(passed.values())
  assert result["summary"]["query_error"] == "RuntimeError"
  record = next(
    item
    for item in caplog.records
    if item.message == "t_trade_v3_rollout_evidence_query_failed"
  )
  assert record.event == "t_trade_v3_rollout_evidence_query_failed"
  assert record.account_id == "account-1"
  assert record.error_type == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.parametrize("run_count", (1, MAX_REPLAY_ROWS))
async def test_evaluator_uses_bounded_queries_not_one_query_per_run(
  run_count: int,
) -> None:
  """A maximal run set must use the same fixed statement plan as one run.

  The readiness heartbeat snapshot is intentionally one set-oriented query;
  V3 promotion evidence needs distinct bounded relations (replay, PAPER,
  outcomes, intents and review events).  This guards the complementary
  contract: no statement is issued per replay/PAPER run.
  """

  replay_rows = [
    (
      SimpleNamespace(
        id=f"replay-{index}",
        parameters={"t_trade_replay": True},
      ),
      SimpleNamespace(status="COMPLETED"),
      SimpleNamespace(
        id=f"backtest-{index}",
        version=1,
        status="COMPLETED",
        metrics={},
      ),
    )
    for index in range(run_count)
  ]
  paper_runs = [
    SimpleNamespace(
      id=f"paper-{index}",
      parameters={"account_id": "account-1"},
    )
    for index in range(run_count)
  ]
  db = SimpleNamespace(
    # replay + its three evidence relations, PAPER + its three evidence
    # relations, and bounded rollout-review events: nine executes regardless
    # of the number of run ids carried by the IN predicates.
    execute=AsyncMock(
      side_effect=[
        _DatabaseResult(rows=replay_rows),
        _DatabaseResult(),
        _DatabaseResult(),
        _DatabaseResult(),
        _DatabaseResult(scalars=paper_runs),
        _DatabaseResult(),
        _DatabaseResult(),
        _DatabaseResult(),
        _DatabaseResult(),
      ]
    ),
    # The global configuration is fetched once.  Returning None also proves
    # that no per-live-run lookup is made when no configured live run exists.
    get=AsyncMock(return_value=None),
  )

  result = await TTradeV3RolloutEvidenceEvaluator().evaluate(
    db,
    account_id="account-1",
    rollout=None,
  )

  assert _passed(result)["V3_EVIDENCE_QUERY_AVAILABLE"] is True
  assert db.execute.await_count <= 9
  assert db.get.await_count <= 1
  assert db.execute.await_count + db.get.await_count <= 10


def _cache_rollout(**overrides: object) -> SimpleNamespace:
  values: dict[str, object] = {
    "policy_version": 3,
    "last_snapshot_id": "snapshot-1",
    "last_snapshot_hash": "a" * 64,
    "stage": "SHADOW",
    "enabled": False,
    "max_active_batches": 1,
    "max_batch_volume": 100,
    "max_order_amount": 20_000.0,
    "max_total_exposure_pct": 0.02,
    "acknowledged_policy_version": 0,
    "updated_at": None,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def _cache_result(*, passed: bool, query_error: str = "") -> dict:
  return {
    "checks": [
      {
        "code": "V3_EVIDENCE_QUERY_AVAILABLE",
        "passed": passed,
        "message": "" if passed else "blocked",
      }
    ],
    "summary": {
      "fingerprint": "business-evidence-fingerprint",
      **({"query_error": query_error} if query_error else {}),
    },
  }


class _CacheProbeEvaluator(TTradeV3RolloutEvidenceEvaluator):
  def __init__(self, *results: dict) -> None:
    super().__init__()
    self.results = list(results)
    self.calls = 0

  async def _evaluate_uncached(self, _db, **_kwargs) -> dict:
    self.calls += 1
    return self.results.pop(0)


@pytest.mark.asyncio
async def test_evaluator_caches_only_negative_evidence_and_isolates_result() -> None:
  evaluator = _CacheProbeEvaluator(_cache_result(passed=False))
  rollout = _cache_rollout()

  first = await evaluator.evaluate(object(), account_id="account-1", rollout=rollout)
  first["summary"]["fingerprint"] = "caller-mutation-must-not-leak"
  second = await evaluator.evaluate(object(), account_id="account-1", rollout=rollout)

  assert evaluator.calls == 1
  assert first["_cache"]["state"] == "miss"
  assert second["_cache"]["state"] == "hit"
  assert second["_cache"]["reason"] == "blocked"
  assert second["summary"]["fingerprint"] == "business-evidence-fingerprint"
  assert "_cache" not in second["summary"]


@pytest.mark.asyncio
async def test_evaluator_negative_cache_key_changes_with_rollout_revision() -> None:
  evaluator = _CacheProbeEvaluator(
    _cache_result(passed=False),
    _cache_result(passed=False),
    _cache_result(passed=False),
  )
  rollout = _cache_rollout()

  first = await evaluator.evaluate(object(), account_id="account-1", rollout=rollout)
  rollout.policy_version = 4
  policy_changed = await evaluator.evaluate(
    object(), account_id="account-1", rollout=rollout
  )
  rollout.last_snapshot_hash = "b" * 64
  snapshot_changed = await evaluator.evaluate(
    object(), account_id="account-1", rollout=rollout
  )

  assert evaluator.calls == 3
  assert (
    len(
      {
        first["_cache"]["key"],
        policy_changed["_cache"]["key"],
        snapshot_changed["_cache"]["key"],
      }
    )
    == 3
  )


@pytest.mark.asyncio
async def test_evaluator_never_caches_all_green_evidence() -> None:
  evaluator = _CacheProbeEvaluator(
    _cache_result(passed=True),
    _cache_result(passed=True),
  )
  rollout = _cache_rollout()

  first = await evaluator.evaluate(object(), account_id="account-1", rollout=rollout)
  second = await evaluator.evaluate(object(), account_id="account-1", rollout=rollout)

  assert evaluator.calls == 2
  assert first["_cache"]["reason"] == "all_passed"
  assert second["_cache"]["state"] == "miss"


@pytest.mark.asyncio
async def test_evaluator_bypass_cache_always_reads_fresh_without_replacing_entry() -> (
  None
):
  evaluator = _CacheProbeEvaluator(
    _cache_result(passed=False),
    _cache_result(passed=True),
  )
  rollout = _cache_rollout()

  first = await evaluator.evaluate(object(), account_id="account-1", rollout=rollout)
  locked = await evaluator.evaluate(
    object(),
    account_id="account-1",
    rollout=rollout,
    bypass_cache=True,
  )
  after_locked = await evaluator.evaluate(
    object(), account_id="account-1", rollout=rollout
  )

  assert evaluator.calls == 2
  assert first["_cache"]["state"] == "miss"
  assert locked["_cache"] == {
    "state": "bypass",
    "reason": "activation_locked_fresh_read",
    "key": locked["_cache"]["key"],
    "ttl_remaining_seconds": 0.0,
  }
  # A fresh green result never overwrites the prior negative cache.  The
  # remaining 30-second delay is conservative and cannot allow a transition.
  assert after_locked["_cache"]["state"] == "hit"


@pytest.mark.asyncio
async def test_evaluator_query_error_is_fail_closed_and_short_negative_cached() -> None:
  db = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("offline")))
  evaluator = TTradeV3RolloutEvidenceEvaluator()
  rollout = _cache_rollout()

  first = await evaluator.evaluate(db, account_id="account-1", rollout=rollout)
  second = await evaluator.evaluate(db, account_id="account-1", rollout=rollout)

  assert db.execute.await_count == 1
  assert not any(_passed(first).values())
  assert second["_cache"]["state"] == "hit"
  assert second["_cache"]["reason"] == "query_error"
  assert 0.0 < second["_cache"]["ttl_remaining_seconds"] <= 5.0


@pytest.mark.asyncio
async def test_evaluator_coalesces_same_key_concurrent_negative_loads() -> None:
  class DelayedEvaluator(_CacheProbeEvaluator):
    def __init__(self) -> None:
      super().__init__(_cache_result(passed=False))
      self.entered = asyncio.Event()
      self.release = asyncio.Event()

    async def _evaluate_uncached(self, _db, **_kwargs) -> dict:
      self.calls += 1
      self.entered.set()
      await self.release.wait()
      return self.results.pop(0)

  evaluator = DelayedEvaluator()
  rollout = _cache_rollout()
  first_task = asyncio.create_task(
    evaluator.evaluate(object(), account_id="account-1", rollout=rollout)
  )
  await evaluator.entered.wait()
  second_task = asyncio.create_task(
    evaluator.evaluate(object(), account_id="account-1", rollout=rollout)
  )
  await asyncio.sleep(0)
  evaluator.release.set()
  first, second = await asyncio.gather(first_task, second_task)

  assert evaluator.calls == 1
  assert {first["_cache"]["state"], second["_cache"]["state"]} == {
    "miss",
    "coalesced",
  }


@pytest.mark.asyncio
async def test_evaluator_coalesces_concurrent_query_error_fail_closed() -> None:
  class DelayedBrokenDatabase:
    def __init__(self) -> None:
      self.calls = 0
      self.entered = asyncio.Event()
      self.release = asyncio.Event()

    async def execute(self, *_args, **_kwargs):
      self.calls += 1
      self.entered.set()
      await self.release.wait()
      raise RuntimeError("database unavailable")

  db = DelayedBrokenDatabase()
  evaluator = TTradeV3RolloutEvidenceEvaluator()
  rollout = _cache_rollout()
  first_task = asyncio.create_task(
    evaluator.evaluate(db, account_id="account-1", rollout=rollout)
  )
  await db.entered.wait()
  second_task = asyncio.create_task(
    evaluator.evaluate(db, account_id="account-1", rollout=rollout)
  )
  await asyncio.sleep(0)
  db.release.set()
  first, second = await asyncio.gather(first_task, second_task)

  assert db.calls == 1
  assert not any(_passed(first).values())
  assert not any(_passed(second).values())
  assert {first["_cache"]["state"], second["_cache"]["state"]} == {
    "miss",
    "coalesced",
  }


def test_evaluator_cache_state_is_safe_across_event_loops() -> None:
  evaluator = _CacheProbeEvaluator(
    _cache_result(passed=False),
    _cache_result(passed=False),
  )
  rollout = _cache_rollout()

  async def read_once() -> dict:
    return await evaluator.evaluate(object(), account_id="account-1", rollout=rollout)

  first = asyncio.run(read_once())
  second = asyncio.run(read_once())

  assert evaluator.calls == 2
  assert first["_cache"]["state"] == "miss"
  assert second["_cache"]["state"] == "miss"
