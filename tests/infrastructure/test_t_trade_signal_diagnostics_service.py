from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateExecutionFill,
  CandidateOutcomeDefinition,
  CandidatePriceObservation,
  apply_candidate_execution_fill,
  observe_candidate_outcome,
  start_candidate_outcome,
)
from quantx_infrastructure.services.t_trade_signal_diagnostics_service import (
  TTradeSignalDiagnosticsService,
)


def _row(
  at: datetime,
  *,
  score: float,
  health: str = "READY",
  pullback: str = "PULLBACK_FORMING",
  momentum: str = "BASELINING",
  candidate_id: str | None = None,
  candidate_status: str = "NONE",
  blockers: list[dict[str, str]] | None = None,
  record_kind: str = "MATERIAL",
  coalesced_count: int = 1,
  policy_version: str = "policy-v3",
  feature_schema_version: str = "1",
  profile_version: str | None = "profile-v1",
  episode_id: str | None = "episode-1",
  strategy_run_id: str = "run-1",
  trade_date: str = "2026-08-23",
  continuity_generation: str = "continuity-1",
) -> SimpleNamespace:
  return SimpleNamespace(
    id=f"event-{at.timestamp()}-{policy_version}-{strategy_run_id}",
    strategy_run_id=strategy_run_id,
    instrument_code="600000.SH",
    evaluated_at=at,
    record_kind=record_kind,
    coalesced_count=coalesced_count,
    payload={
      "signal_snapshot": {
        "trade_date": trade_date,
        "continuity_generation": continuity_generation,
        "data_health": health,
        "pullback": {"phase": pullback},
        "momentum": {"phase": momentum},
        "selected_path": "PULLBACK_REBOUND",
        "opportunity_score": score,
        "preview_threshold": 55.0,
        "episode_id": episode_id,
        "candidate_id": candidate_id,
        "candidate_status": candidate_status,
        "top_blockers": blockers or [],
        "policy_version": policy_version,
        "feature_schema_version": feature_schema_version,
        "profile_version": profile_version,
      }
    },
  )


def _intent(
  candidate_id: str,
  *,
  status: str = "FILLED",
  strategy_run_id: str = "run-1",
) -> SimpleNamespace:
  return SimpleNamespace(
    id=f"intent-{strategy_run_id}-{candidate_id}",
    status=status,
    order_id="order-1" if status == "FILLED" else None,
    executed_volume=100 if status == "FILLED" else 0,
    strategy_run_id=strategy_run_id,
    intent_metadata={
      "opportunity_schema_version": 3,
      "candidate_id": candidate_id,
    },
  )


def _outcome(
  candidate_id: str,
  *,
  fee: float | None = 2.0,
) -> SimpleNamespace:
  state = start_candidate_outcome(
    CandidateOutcomeDefinition(
      candidate_id=candidate_id,
      candidate_fingerprint="a" * 64,
      strategy_run_id="run-1",
      instrument_code="600000.SH",
      source_time_ms=1_000_000,
      tick_ordinal=1,
      continuity_generation="continuity-1",
      reference_price=10.0,
      policy_version="policy-v3",
      feature_schema_version="1",
      profile_version="profile-v1",
      horizons_seconds=(60, 120),
      max_observation_gap_ms=120_000,
    )
  )
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill(
      fill_id="entry-1",
      role="ENTRY",
      source_time_ms=1_001_000,
      price=10.0,
      volume=100,
      fee=fee,
      entry_complete=True,
      entry_target_volume=100,
    ),
  )
  for source_time_ms, ordinal, price in (
    (1_061_000, 2, 10.2),
    (1_121_000, 3, 9.9),
  ):
    observe_candidate_outcome(
      state,
      CandidatePriceObservation(
        source_time_ms=source_time_ms,
        tick_ordinal=ordinal,
        continuity_generation="continuity-1",
        price=price,
      ),
    )
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill(
      fill_id="exit-1",
      role="EXIT",
      source_time_ms=1_122_000,
      price=10.1,
      volume=100,
      fee=fee,
    ),
  )
  return SimpleNamespace(
    strategy_run_id="run-1",
    candidate_id=candidate_id,
    state=state.to_dict(),
  )


def _early_exit_outcome(candidate_id: str) -> SimpleNamespace:
  state = start_candidate_outcome(
    CandidateOutcomeDefinition(
      candidate_id=candidate_id,
      candidate_fingerprint="c" * 64,
      strategy_run_id="run-1",
      instrument_code="600000.SH",
      source_time_ms=1_000_000,
      tick_ordinal=1,
      continuity_generation="continuity-1",
      reference_price=10.0,
      policy_version="policy-v3",
      feature_schema_version="1",
      profile_version="profile-v1",
      horizons_seconds=(60, 120),
      max_observation_gap_ms=120_000,
    )
  )
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill(
      fill_id=f"{candidate_id}-entry",
      role="ENTRY",
      source_time_ms=1_001_000,
      price=10.0,
      volume=100,
      fee=2.0,
      entry_complete=True,
      entry_target_volume=100,
    ),
  )
  apply_candidate_execution_fill(
    state,
    CandidateExecutionFill(
      fill_id=f"{candidate_id}-exit",
      role="EXIT",
      source_time_ms=1_010_000,
      price=10.1,
      volume=100,
      fee=2.0,
    ),
  )
  return SimpleNamespace(
    strategy_run_id="run-1",
    candidate_id=candidate_id,
    state=state.to_dict(),
  )


def test_diagnostics_uses_ready_time_and_material_event_funnel() -> None:
  start = datetime(2026, 8, 23, 9, 30)
  evaluations = [
    _row(start, score=50.0),
    _row(
      start + timedelta(seconds=2),
      score=75.0,
      candidate_id="candidate-1",
      candidate_status="AWAITING_APPROVAL",
    ),
    _row(
      start + timedelta(seconds=4),
      score=74.0,
      health="STALE",
      pullback="CANDIDATE_LATCHED",
      candidate_id="candidate-1",
      candidate_status="SUPPRESSED",
      blockers=[
        {
          "code": "QUOTE_STALE",
          "label": "行情陈旧",
          "detail": "等待新报价",
        }
      ],
    ),
  ]

  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=evaluations,
    intents=[_intent("candidate-1")],
  )

  assert result["available"] is True
  assert result["merged_versions"] is False
  assert result["warnings"] == []
  assert len(result["partitions"]) == 1
  partition = result["partitions"][0]
  assert partition["denominator"] == {
    "code": "READY_INSTRUMENT_SECONDS",
    "label": "READY 标的时长（秒）",
    "ready_instrument_seconds": 4.0,
  }
  assert {item["code"]: item["count"] for item in partition["funnel"]} == {
    "ELIGIBLE": 3,
    "DATA_READY": 2,
    "PATTERN": 1,
    "PREVIEW": 1,
    "CANDIDATE": 1,
    "TRADE_INTENT": 1,
    "APPROVED": 1,
    "ORDERED": 1,
    "FILLED": 1,
  }
  assert partition["funnel"][0]["unit_code"] == "MATERIAL_EVENTS"
  assert partition["funnel"][0]["denominator_code"] is None
  assert partition["funnel"][2]["unit_code"] == "RUN_SCOPED_EPISODES"
  assert partition["funnel"][2]["denominator_code"] == "DATA_READY"
  assert partition["blockers"][0] == {
    "blocker": {
      "code": "QUOTE_STALE",
      "label": "行情陈旧",
      "detail": "等待新报价",
    },
    "count": 1,
    "rate": 0.333333,
    "denominator_code": "MATERIAL_EVENTS",
    "denominator_value": 3.0,
  }
  assert sum(item["count"] for item in partition["score_distribution"]) == 3
  assert partition["score_distribution"][0]["policy_version"] == "policy-v3"
  assert partition["fsm_transitions"] == [
    {
      "branch": "PULLBACK",
      "from_phase": "PULLBACK_FORMING",
      "to_phase": "CANDIDATE_LATCHED",
      "count": 1,
    }
  ]
  assert partition["candidate_outcomes"] == [
    {"code": "FILLED", "label": "已成交", "count": 1}
  ]
  assert result["version_groups"][0]["count"] == 3
  assert partition["post_candidate_performance"] == {
    "available": False,
    "reason_code": "POST_FILL_OUTCOME_COHORT_INCOMPLETE",
    "reason": "权威已成交候选与成交后结果未一一对应；为避免幸存者偏差，禁止聚合残存样本。",
    "sample_count": 0,
    "net_mfe_pct": None,
    "net_mae_pct": None,
    "fixed_window_returns": [],
    "required_data_codes": ["COMPLETE_FILLED_CANDIDATE_OUTCOME_COHORT"],
  }


def test_diagnostics_aggregates_only_matured_authoritative_post_fill_outcomes() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=75.0,
        candidate_id="candidate-1",
        candidate_status="AWAITING_APPROVAL",
      )
    ],
    intents=[_intent("candidate-1")],
    outcomes=[_outcome("candidate-1")],
  )

  performance = result["partitions"][0]["post_candidate_performance"]
  assert performance["available"] is True
  assert performance["sample_count"] == 1
  assert performance["net_mfe_pct"] is not None
  assert performance["net_mae_pct"] is not None
  assert [item["window_seconds"] for item in performance["fixed_window_returns"]] == [
    60,
    120,
  ]
  assert all(item["sample_count"] == 1 for item in performance["fixed_window_returns"])
  assert performance["required_data_codes"] == []


def test_diagnostics_rejects_matured_outcome_with_unknown_fee() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=75.0,
        candidate_id="candidate-1",
        candidate_status="AWAITING_APPROVAL",
      )
    ],
    intents=[_intent("candidate-1")],
    outcomes=[_outcome("candidate-1", fee=None)],
  )

  performance = result["partitions"][0]["post_candidate_performance"]
  assert performance["available"] is False
  assert performance["reason_code"] == "AUTHORITATIVE_EXECUTION_FEES_INCOMPLETE"
  assert performance["net_mfe_pct"] is None
  assert performance["fixed_window_returns"] == []


def test_diagnostics_rejects_survivor_only_post_fill_aggregation() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=75.0,
        candidate_id="candidate-1",
        candidate_status="AWAITING_APPROVAL",
      ),
      _row(
        at + timedelta(seconds=1),
        score=76.0,
        candidate_id="candidate-2",
        candidate_status="AWAITING_APPROVAL",
        episode_id="episode-2",
      ),
    ],
    intents=[_intent("candidate-1"), _intent("candidate-2")],
    outcomes=[_outcome("candidate-1"), _early_exit_outcome("candidate-2")],
  )

  performance = result["partitions"][0]["post_candidate_performance"]
  assert performance["available"] is False
  assert performance["reason_code"] == "POST_FILL_COHORT_INCOMPLETE"
  assert performance["sample_count"] == 0
  assert performance["fixed_window_returns"] == []
  assert performance["required_data_codes"] == ["COMPLETE_POST_FILL_COHORT"]


def test_diagnostics_rejects_filled_candidate_without_outcome_row() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=75.0,
        candidate_id="candidate-1",
        candidate_status="AWAITING_APPROVAL",
      ),
      _row(
        at + timedelta(seconds=1),
        score=76.0,
        candidate_id="candidate-2",
        candidate_status="AWAITING_APPROVAL",
        episode_id="episode-2",
      ),
    ],
    intents=[_intent("candidate-1"), _intent("candidate-2")],
    outcomes=[_outcome("candidate-1")],
  )

  performance = result["partitions"][0]["post_candidate_performance"]
  assert performance["available"] is False
  assert performance["reason_code"] == "POST_FILL_OUTCOME_COHORT_INCOMPLETE"
  assert performance["sample_count"] == 0
  assert performance["required_data_codes"] == [
    "COMPLETE_FILLED_CANDIDATE_OUTCOME_COHORT"
  ]


def test_diagnostics_rejects_outcome_without_authoritative_entry_fill() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  outcome = _outcome("candidate-1")
  outcome.state["execution"]["entry_volume"] = 0
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=75.0,
        candidate_id="candidate-1",
        candidate_status="AWAITING_APPROVAL",
      )
    ],
    intents=[_intent("candidate-1")],
    outcomes=[outcome],
  )

  performance = result["partitions"][0]["post_candidate_performance"]
  assert performance["available"] is False
  assert performance["reason_code"] == "POST_FILL_OUTCOME_COHORT_INCOMPLETE"
  assert performance["sample_count"] == 0
  assert performance["required_data_codes"] == [
    "COMPLETE_FILLED_CANDIDATE_OUTCOME_COHORT"
  ]


def test_coalesced_diagnostic_never_inflates_material_funnel_or_blockers() -> None:
  start = datetime(2026, 8, 23, 9, 30)
  blocker = {"code": "SPREAD_WIDE", "label": "价差过宽", "detail": ""}
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(start, score=20.0, blockers=[blocker]),
      _row(
        start + timedelta(minutes=30),
        score=20.0,
        blockers=[blocker],
        pullback="CANDIDATE_LATCHED",
        record_kind="COALESCED_DIAGNOSTIC",
        coalesced_count=10_000,
      ),
    ],
    intents=[],
  )

  partition = result["partitions"][0]
  assert partition["denominator"]["ready_instrument_seconds"] == 0.0
  assert partition["funnel"][0]["code"] == "ELIGIBLE"
  assert partition["funnel"][0]["count"] == 1
  assert partition["blockers"][0]["count"] == 1
  assert partition["blockers"][0]["denominator_value"] == 1.0
  assert partition["fsm_transitions"] == []


def test_blocker_rates_use_material_events_not_total_blocker_occurrences() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=20.0,
        blockers=[
          {"code": "QUOTE_STALE", "label": "行情陈旧", "detail": ""},
          {"code": "SPREAD_WIDE", "label": "价差过宽", "detail": ""},
        ],
      )
    ],
    intents=[],
  )

  blockers = result["partitions"][0]["blockers"]
  assert {item["rate"] for item in blockers} == {1.0}
  assert {item["denominator_code"] for item in blockers} == {"MATERIAL_EVENTS"}
  assert {item["denominator_value"] for item in blockers} == {1.0}


def test_versions_are_partitioned_by_default_and_only_merge_with_warning() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  evaluations = [
    _row(at, score=50.0, policy_version="policy-v3-a"),
    _row(
      at + timedelta(seconds=1),
      score=75.0,
      policy_version="policy-v3-b",
      profile_version="profile-v2",
    ),
  ]

  separated = TTradeSignalDiagnosticsService().aggregate(
    evaluations=evaluations,
    intents=[],
  )
  assert len(separated["partitions"]) == 2
  assert separated["warnings"] == []
  assert {
    bucket["policy_version"]
    for partition in separated["partitions"]
    for bucket in partition["score_distribution"]
  } == {"policy-v3-a", "policy-v3-b"}

  merged = TTradeSignalDiagnosticsService().aggregate(
    evaluations=evaluations,
    intents=[],
    merge_versions=True,
  )
  assert merged["merged_versions"] is True
  assert merged["warnings"] == ["MIXED_SIGNAL_VERSIONS_EXPLICITLY_MERGED"]
  assert len(merged["partitions"]) == 1
  assert merged["partitions"][0]["policy_version"] == "MIXED"
  assert {
    item["policy_version"] for item in merged["partitions"][0]["score_distribution"]
  } == {"policy-v3-a", "policy-v3-b"}

  single_version_merge = TTradeSignalDiagnosticsService().aggregate(
    evaluations=evaluations[:1],
    intents=[],
    merge_versions=True,
  )
  assert single_version_merge["warnings"] == []
  assert single_version_merge["partitions"][0]["policy_version"] == "policy-v3-a"


def test_lifecycle_ids_are_run_scoped_and_market_lineage_breaks_time_edges() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=75.0,
        pullback="OBSERVING",
        candidate_id="deterministic-candidate",
        strategy_run_id="run-1",
        continuity_generation="generation-1",
      ),
      _row(
        at,
        score=75.0,
        pullback="PULLBACK_FORMING",
        candidate_id="deterministic-candidate",
        strategy_run_id="run-2",
        continuity_generation="generation-1",
      ),
      _row(
        at + timedelta(seconds=1),
        score=75.0,
        pullback="PULLBACK_FORMING",
        candidate_id="deterministic-candidate",
        strategy_run_id="run-1",
        continuity_generation="generation-2",
      ),
      _row(
        at + timedelta(seconds=2),
        score=75.0,
        pullback="CANDIDATE_LATCHED",
        candidate_id="deterministic-candidate",
        strategy_run_id="run-1",
        continuity_generation="generation-2",
      ),
    ],
    intents=[
      _intent("deterministic-candidate", strategy_run_id="run-1"),
      _intent("deterministic-candidate", strategy_run_id="run-2"),
    ],
  )

  partition = result["partitions"][0]
  funnel = {item["code"]: item["count"] for item in partition["funnel"]}
  assert funnel["PATTERN"] == 2
  assert funnel["CANDIDATE"] == 2
  assert funnel["TRADE_INTENT"] == 2
  assert partition["denominator"]["ready_instrument_seconds"] == 1.0
  assert partition["fsm_transitions"] == [
    {
      "branch": "PULLBACK",
      "from_phase": "PULLBACK_FORMING",
      "to_phase": "CANDIDATE_LATCHED",
      "count": 1,
    }
  ]


def test_prelink_pending_intent_is_not_counted_as_human_approval() -> None:
  at = datetime(2026, 8, 23, 9, 30)
  result = TTradeSignalDiagnosticsService().aggregate(
    evaluations=[
      _row(
        at,
        score=75.0,
        candidate_id="candidate-1",
        candidate_status="LATCHED",
      )
    ],
    intents=[_intent("candidate-1", status="PENDING")],
  )

  funnel = {item["code"]: item["count"] for item in result["partitions"][0]["funnel"]}
  assert funnel["TRADE_INTENT"] == 1
  assert funnel["APPROVED"] == 0


@pytest.mark.asyncio
async def test_replay_diagnostics_scope_binds_exact_strategy_run() -> None:
  captured: dict[str, str | None] = {}

  class ScopedService(TTradeSignalDiagnosticsService):
    async def _load_evaluations(self, repository, **kwargs):
      captured["evaluation_run_id"] = kwargs.get("strategy_run_id")
      return []

    async def _load_intents(self, db, **kwargs):
      captured["intent_run_id"] = kwargs.get("strategy_run_id")
      return []

    async def _load_outcomes(self, db, **kwargs):
      captured["outcome_run_id"] = kwargs.get("strategy_run_id")
      return []

  start = datetime(2026, 8, 23, 9, 30)
  result = await ScopedService().signal_diagnostics(
    "account-1",
    stock_code=None,
    start_time=start,
    end_time=start + timedelta(hours=1),
    db=SimpleNamespace(),
    strategy_run_id="run-replay-1",
    merge_versions=True,
  )

  assert captured == {
    "evaluation_run_id": "run-replay-1",
    "intent_run_id": "run-replay-1",
    "outcome_run_id": "run-replay-1",
  }
  assert result["partitions"] == []
  assert result["merged_versions"] is True
  assert result["scope"] == {
    "strategy_run_id": "run-replay-1",
    "stock_code": None,
    "start_time": "2026-08-23T09:30:00",
    "end_time": "2026-08-23T10:30:00",
  }


@pytest.mark.asyncio
async def test_diagnostics_range_over_safety_limit_returns_explicit_unavailable_without_db_reads() -> None:
  class NoReadService(TTradeSignalDiagnosticsService):
    async def _load_evaluations(self, *args, **kwargs):  # pragma: no cover - guard
      raise AssertionError("range rejection must not query evaluations")

    async def _load_intents(self, *args, **kwargs):  # pragma: no cover - guard
      raise AssertionError("range rejection must not query intents")

    async def _load_outcomes(self, *args, **kwargs):  # pragma: no cover - guard
      raise AssertionError("range rejection must not query outcomes")

  start = datetime(2026, 8, 1, 9, 30)
  result = await NoReadService().signal_diagnostics(
    "account-1",
    stock_code="600000.SH",
    start_time=start,
    end_time=start + timedelta(days=31, seconds=1),
    db=SimpleNamespace(),
    merge_versions=True,
  )

  assert result == {
    "available": False,
    "reason_code": "DIAGNOSTICS_RANGE_TOO_LARGE",
    "reason": "单次做 T 诊断最多查询 31 天；请缩小时间范围后重试。",
    "merged_versions": True,
    "warnings": [],
    "partitions": [],
    "version_groups": [],
  }


@pytest.mark.asyncio
async def test_diagnostics_row_capacity_overflow_returns_explicit_unavailable() -> None:
  class OverflowService(TTradeSignalDiagnosticsService):
    async def _load_evaluations(self, *args, **kwargs):
      return [object()] * 50_001

    async def _load_intents(self, *args, **kwargs):  # pragma: no cover - guard
      raise AssertionError("overflow must stop before loading intents")

    async def _load_outcomes(self, *args, **kwargs):  # pragma: no cover - guard
      raise AssertionError("overflow must stop before loading outcomes")

  start = datetime(2026, 8, 23, 9, 30)
  result = await OverflowService().signal_diagnostics(
    "account-1",
    stock_code=None,
    start_time=start,
    end_time=start + timedelta(hours=1),
    db=SimpleNamespace(),
  )

  assert result["available"] is False
  assert result["reason_code"] == "DIAGNOSTICS_ROW_LIMIT_EXCEEDED"
  assert result["partitions"] == []
  assert result["version_groups"] == []
