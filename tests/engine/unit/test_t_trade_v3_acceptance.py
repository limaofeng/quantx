from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import quantx_engine.t_trade_v3_acceptance as acceptance_module
from quantx_domain.strategies.base import StrategyRunMode
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_engine.t_trade_v3_acceptance import (
  DEFAULT_TRADING_DAYS,
  HeldInstrument,
  LatencyAccumulator,
  ReplayWindow,
  SnapshotPortfolio,
  TickDayInspection,
  WindowAudit,
  build_report_document,
  build_synthetic_pressure_fixture,
  render_markdown,
  select_formal_window,
  select_pressure_window,
)


def _snapshot(
  *,
  snapshot_date: date = date(2026, 6, 1),
  replayable: bool = True,
) -> SnapshotPortfolio:
  return SnapshotPortfolio(
    account_id="test-account",
    snapshot_id=f"snapshot-{snapshot_date.isoformat()}",
    snapshot_date=snapshot_date,
    source="TEST",
    data_quality="OK",
    holdings=(
      HeldInstrument(
        instrument_code="600000.SH",
        volume=100,
        available_volume=100,
        replayable=replayable,
        reason="" if replayable else "INSTRUMENT_LIFECYCLE_REFERENCE_INCOMPLETE",
      ),
      HeldInstrument(
        instrument_code="000001.SZ",
        volume=200,
        available_volume=200,
        replayable=True,
      ),
    ),
  )


def _audit(
  *,
  day_count: int = DEFAULT_TRADING_DAYS,
  incomplete_pairs: set[tuple[str, int]] | None = None,
  replayable: bool = True,
) -> WindowAudit:
  snapshot = _snapshot(replayable=replayable)
  days = tuple(snapshot.snapshot_date + timedelta(days=index + 1) for index in range(day_count))
  incomplete_pairs = incomplete_pairs or set()
  inspections = {}
  for trading_date in days:
    index = (trading_date - days[0]).days
    for code in snapshot.instrument_codes:
      complete = (code, index) not in incomplete_pairs
      inspections[(code, trading_date)] = TickDayInspection(
        instrument_code=code,
        trading_date=trading_date,
        complete=complete,
        classification="COMPLETE" if complete else "MISSING",
        reason_codes=() if complete else ("NO_TICK_DATA",),
        statistics={"record_count": 240 if complete else 0},
      )
  return WindowAudit(
    window=ReplayWindow(snapshot=snapshot, trading_dates=days),
    inspections=inspections,
  )


def test_formal_window_requires_all_holdings_all_days_and_abnormal_evidence() -> None:
  audit = _audit()

  assert select_formal_window([audit]) is None
  selected = select_formal_window(
    [audit], abnormal_dates=[audit.window.trading_dates[3]]
  )

  assert selected is audit
  assert audit.blockers(abnormal_dates=[audit.window.trading_dates[3]]) == []


def test_missing_one_stock_day_blocks_formal_replay_and_preserves_exact_evidence() -> None:
  audit = _audit(incomplete_pairs={("000001.SZ", 7)})

  assert select_formal_window(
    [audit], abnormal_dates=[audit.window.trading_dates[2]]
  ) is None
  assert "ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE" in audit.blockers(
    abnormal_dates=[audit.window.trading_dates[2]]
  )
  assert len(audit.missing) == 1
  assert audit.missing[0].instrument_code == "000001.SZ"
  assert audit.missing[0].trading_date == audit.window.trading_dates[7]
  assert audit.missing[0].reason_codes == ("NO_TICK_DATA",)


def test_pressure_baseline_only_uses_contiguous_shared_prefix() -> None:
  audit = _audit(
    day_count=5,
    incomplete_pairs={("600000.SH", 2)},
  )

  selected, prefix = select_pressure_window(
    [audit], snapshot_date=audit.window.snapshot.snapshot_date
  )

  assert selected is audit
  assert prefix == audit.window.trading_dates[:2]
  assert audit.window.trading_dates[3] in audit.full_shared_dates
  assert audit.window.trading_dates[3] not in prefix


def test_non_replayable_actual_holding_cannot_be_used_for_pressure_baseline() -> None:
  audit = _audit(day_count=2, replayable=False)

  with pytest.raises(RuntimeError, match="PRESSURE_HELD_INSTRUMENT_NOT_REPLAYABLE"):
    select_pressure_window([audit], snapshot_date=audit.window.snapshot.snapshot_date)


def test_only_sealed_v3_pressure_backtest_enables_durable_runtime_state() -> None:
  def runtime(mode: StrategyRunMode, parameters: dict) -> SimpleNamespace:
    return SimpleNamespace(
      context=SimpleNamespace(mode=mode, parameters=parameters),
    )

  marker = {"_internal_v3_pressure_runtime_state_persistence": True}
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(StrategyRunMode.BACKTEST, {})
  ) is False
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(StrategyRunMode.BACKTEST, marker)
  ) is False
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(
      StrategyRunMode.BACKTEST,
      {
        **marker,
        "t_trade_replay": True,
        "replay_acceptance": "V3_CAUSAL_20D",
      },
    )
  ) is False
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(
      StrategyRunMode.BACKTEST,
      {
        **marker,
        "t_trade_replay": True,
        "replay_acceptance": "V3_PRESSURE_BASELINE",
      },
    )
  ) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(StrategyRunMode.PAPER, {})
  ) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(StrategyRunMode.LIVE, {})
  ) is True


def test_report_keeps_blocked_gate_distinct_from_pressure_baseline() -> None:
  audit = _audit(incomplete_pairs={("600000.SH", 1)})
  report = build_report_document(
    [audit],
    requested_trading_days=DEFAULT_TRADING_DAYS,
    abnormal_dates=[audit.window.trading_dates[0]],
    pressure_baseline={"status": "EXECUTED_SYNTHETIC_NON_HISTORICAL"},
  )
  markdown = render_markdown(report, json_name="acceptance.json")

  assert report["formal_20_trading_day"]["status"] == "BLOCKED"
  assert report["candidate_windows"][0]["missing_instrument_days"][0][
    "instrument_code"
  ] == "600000.SH"
  assert "**BLOCKED**" in markdown
  assert "**EXECUTED_SYNTHETIC_NON_HISTORICAL**" in markdown
  assert "不替代 20 交易日门禁" in markdown
  microbenchmark = report["performance_remediation_microbenchmark"]
  assert microbenchmark["status"] == "MICROBENCHMARK_NON_GATING"
  assert microbenchmark["batch_transaction_microtest"] == {
    "closed_diagnostics": 8,
    "preserved_rows": 8,
    "owned_sessions": 1,
    "commits": 1,
  }
  assert microbenchmark["full_9600_replayed_after_patch"] is False
  assert microbenchmark["slo_status"] == "BLOCKED"
  assert "320 rows / 8 streams" in markdown
  assert "320 commits / 1016.579 ms → 40 commits / 551.746 ms" in markdown
  assert "未重跑 9,600 Tick；SLO 仍为 BLOCKED" in markdown


def test_report_renders_raw_missing_source_identity_storage_audit() -> None:
  audit = _audit(incomplete_pairs={("600000.SH", 1)})
  report = build_report_document(
    [audit],
    requested_trading_days=DEFAULT_TRADING_DAYS,
    abnormal_dates=[audit.window.trading_dates[0]],
    historical_short_window_preflight={
      "passed": False,
      "failure": {
        "failures": [
          {
            "instrument_day": "2026-06-04:600000.SH",
            "message": "historical Tick source identity is missing",
          }
        ]
      },
      "raw_storage_identity_audit": {
        "instrument_day_count": 1,
        "row_count": 12,
        "source_time_ms": {
          "arrow_type": "int64",
          "non_null_count": 0,
          "null_count": 12,
        },
        "tick_ordinal": {
          "arrow_type": "int64",
          "non_null_count": 0,
          "null_count": 12,
        },
        "continuity_generation": {"field_present": False},
        "storage_time": {
          "all_instrument_days_strictly_increasing": True,
          "duplicate_count": 0,
        },
        "conclusion": "MISSING_AUTHORITY_SOURCE_IDENTITY_NO_DERIVATION",
      },
    },
  )

  markdown = render_markdown(report, json_name="acceptance.json")

  assert "原始存储审计：1 个 instrument-day / 12 Tick" in markdown
  assert "source_time_ms=int64（非空 0/12）" in markdown
  assert "continuity_generation 字段存在=False" in markdown
  assert "MISSING_AUTHORITY_SOURCE_IDENTITY_NO_DERIVATION" in markdown


def test_report_only_marks_remediation_full_after_completed_9600_fixture() -> None:
  audit = _audit(incomplete_pairs={("600000.SH", 1)})
  pressure = {
    "status": "EXECUTED_SYNTHETIC_NON_HISTORICAL",
    "fixture": {
      "tick_count": 9_600,
      "fixture_sha256": "fixture-hash",
      "held_instruments": ["600000.SH", "000001.SZ"],
      "market_time_policy": "TEST",
    },
    "replay": {"status": "COMPLETED", "progress_pct": 100.0},
    "run_evidence": {"run_id": "full-run"},
    "elapsed_seconds": 12.5,
    "timeout_seconds": 1_800.0,
    "timed_out": False,
    "cancellation": None,
    "execution_boundary": {"runtime_state_persist_enabled": True},
    "frozen_local_slo": {"status": "FROZEN_FIRST_LOCAL_SYNTHETIC_BASELINE"},
  }

  report = build_report_document(
    [audit],
    requested_trading_days=DEFAULT_TRADING_DAYS,
    abnormal_dates=[audit.window.trading_dates[0]],
    pressure_baseline=pressure,
  )
  markdown = render_markdown(report, json_name="acceptance.json")

  microbenchmark = report["performance_remediation_microbenchmark"]
  assert microbenchmark["full_9600_replayed_after_patch"] is True
  assert microbenchmark["slo_status"] == "FROZEN_FIRST_LOCAL_SYNTHETIC_BASELINE"
  assert "当前生产 Engine 路径已完成固定全持仓全量合成负载" in markdown
  assert "已使用性能补丁后的完整 9,600 Tick 运行" in markdown


def test_completed_9600_without_durable_runtime_state_cannot_freeze_slo() -> None:
  evidence = acceptance_module._performance_remediation_evidence(
    {
      "status": "EXECUTED_SYNTHETIC_NON_HISTORICAL",
      "fixture": {"tick_count": 9_600},
      "replay": {"status": "COMPLETED"},
      "execution_boundary": {"runtime_state_persist_enabled": False},
    }
  )

  assert evidence["full_9600_replayed_after_patch"] is False
  assert evidence["slo_status"] == "BLOCKED"


def test_nonpersistent_diagnostic_is_explicitly_non_gating() -> None:
  normalized = acceptance_module._normalize_nonpersistent_diagnostic_pressure_attempt(
    {
      "status": "EXECUTED_DIAGNOSTIC_NON_GATING",
      "diagnostic_non_gating": True,
      "run_evidence": {
        "parameters": {
          "t_trade_replay": True,
          "replay_acceptance": "V3_PRESSURE_BASELINE",
        }
      },
      "production_path_coverage": {"runtime_state_checkpoint": True},
    }
  )

  assert normalized is not None
  assert normalized["status"] == "EXECUTED_DIAGNOSTIC_NON_GATING_NONPERSISTENT"
  assert normalized["runtime_state_persistence"]["enabled"] is False
  assert normalized["production_path_coverage"]["runtime_state_checkpoint"] == (
    "NOT_PERSISTENT_NON_GATING"
  )


def test_sealed_diagnostic_is_never_rendered_as_nonpersistent_calibration() -> None:
  sealed = {
    "status": "EXECUTED_DIAGNOSTIC_NON_GATING",
    "diagnostic_non_gating": True,
    "run_evidence": {
      "run_id": "sealed-run",
      "parameters": {
        "_internal_v3_pressure_runtime_state_persistence": True,
        "t_trade_replay": True,
        "replay_acceptance": "V3_PRESSURE_BASELINE",
      },
    },
  }

  normalized = acceptance_module._normalize_nonpersistent_diagnostic_pressure_attempt(
    sealed
  )

  assert normalized == sealed
  assert (
    acceptance_module._is_nonpersistent_diagnostic_pressure_attempt(normalized)
    is False
  )


def test_report_does_not_freeze_slo_for_completed_short_calibration() -> None:
  audit = _audit(incomplete_pairs={("600000.SH", 1)})
  pressure = {
    "status": "EXECUTED_SYNTHETIC_NON_HISTORICAL",
    "fixture": {
      "tick_count": 480,
      "fixture_sha256": "calibration-hash",
      "held_instruments": ["600000.SH", "000001.SZ"],
      "market_time_policy": "TEST",
    },
    "replay": {"status": "COMPLETED", "progress_pct": 100.0},
    "not_a_completed_slo": True,
    "frozen_local_slo": None,
  }

  report = build_report_document(
    [audit],
    requested_trading_days=DEFAULT_TRADING_DAYS,
    abnormal_dates=[audit.window.trading_dates[0]],
    pressure_baseline=pressure,
  )
  markdown = render_markdown(report, json_name="acceptance.json")

  microbenchmark = report["performance_remediation_microbenchmark"]
  assert microbenchmark["full_9600_replayed_after_patch"] is False
  assert microbenchmark["slo_status"] == "BLOCKED"
  assert "只有完成固定 9,600 Tick 全量夹具才可冻结/判定 SLO" in markdown


@pytest.mark.asyncio
async def test_completed_synthetic_replay_projection_is_repaired_after_callback_grace(
  monkeypatch,
) -> None:
  projection_status = "RUNNING"
  updates: list[dict] = []

  class FakeReplayService:
    async def get(self, _run_id):
      return {"status": projection_status}

  async def durable_completed_evidence(_run_id):
    return {
      "status": "completed",
      "mode": "backtest",
      "parameters": {
        "account_id": "synthetic-account",
        "t_trade_replay": True,
        "replay_acceptance": "V3_PRESSURE_BASELINE",
        "replay_end_time": "2026-06-05T15:00:00",
      },
    }

  async def update_projection(**kwargs):
    nonlocal projection_status
    updates.append(kwargs)
    projection_status = "COMPLETED"
    return {"status": projection_status}

  monkeypatch.setattr(
    acceptance_module,
    "_load_run_evidence",
    durable_completed_evidence,
  )
  monkeypatch.setattr(
    acceptance_module.t_trade_replay_projection_service,
    "update",
    update_projection,
  )

  replay, evidence, convergence = (
    await acceptance_module._await_synthetic_replay_terminal(
      FakeReplayService(),
      "synthetic-run",
      callback_grace_seconds=0,
    )
  )

  assert replay["status"] == "COMPLETED"
  assert evidence["status"] == "completed"
  assert convergence["projection_repaired"] is True
  assert updates == [
    {
      "run_id": "synthetic-run",
      "account_id": "synthetic-account",
      "status": "COMPLETED",
      "progress_pct": 100.0,
      "processed_until": acceptance_module.datetime(2026, 6, 5, 15, 0),
      "kind": acceptance_module.TTradeReplayUpdateKind.RESULT_READY,
    }
  ]


@pytest.mark.asyncio
async def test_terminal_projection_repair_rejects_non_backtest_run(monkeypatch) -> None:
  class FakeReplayService:
    async def get(self, _run_id):
      return {"status": "RUNNING"}

  async def unsafe_evidence(_run_id):
    return {
      "status": "completed",
      "mode": "live",
      "parameters": {
        "account_id": "synthetic-account",
        "t_trade_replay": True,
        "replay_acceptance": "V3_PRESSURE_BASELINE",
        "replay_end_time": "2026-06-05T15:00:00",
      },
    }

  monkeypatch.setattr(acceptance_module, "_load_run_evidence", unsafe_evidence)

  with pytest.raises(RuntimeError, match="TERMINAL_PROJECTION_NOT_CONVERGED"):
    await acceptance_module._await_synthetic_replay_terminal(
      FakeReplayService(),
      "unsafe-run",
      callback_grace_seconds=0,
    )


def test_latency_accumulator_reports_interpolated_quantiles_and_cap() -> None:
  accumulator = LatencyAccumulator(max_samples=3)
  for value in (3_000_000, 1_000_000, 2_000_000, 4_000_000):
    accumulator.observe(value)

  summary = accumulator.to_dict()

  assert summary["sample_count"] == 3
  assert summary["dropped_samples"] == 1
  assert summary["quantiles_exact"] is False
  assert summary["p50"] == 2.0
  assert summary["p95"] == 2.9
  assert summary["p99"] == 2.98


def test_database_write_counters_report_bounded_latency_and_call_sites() -> None:
  writes = acceptance_module.DatabaseWriteCounters()
  writes.commits = 2
  writes.flushes = 1
  writes.dml_executes = 3
  writes.commit_latency.observe(2_000_000)
  writes.commit_latency.observe(4_000_000)
  writes.flush_latency.observe(1_000_000)
  writes.dml_execute_latency.observe(3_000_000)
  writes.commit_call_sites["strategy_run_state_repository.py:upsert_state"] = 2
  writes.flush_call_sites["strategy_run_state_repository.py:upsert_state"] = 1
  writes.dml_execute_call_sites["strategy_run_state_repository.py:upsert_state"] = 3

  summary = writes.to_dict()

  assert summary["commit_calls"] == 2
  assert summary["latency"]["commit"]["p50"] == 3.0
  assert summary["latency"]["flush"]["p99"] == 1.0
  assert summary["call_sites"]["commit"] == {
    "strategy_run_state_repository.py:upsert_state": 2
  }
  assert summary["call_sites"]["dml_execute"] == {
    "strategy_run_state_repository.py:upsert_state": 3
  }

  runtime_state = acceptance_module.RuntimeStateDatabaseCounters()
  runtime_state.state_upsert_latency.observe(5_000_000)
  runtime_summary = runtime_state.to_dict()
  assert runtime_summary["latency"]["state_upsert"]["p50"] == 5.0


def test_synthetic_fixture_is_deterministic_and_stays_inside_trade_sessions() -> None:
  audit = _audit(day_count=2)

  first = build_synthetic_pressure_fixture(
    audit,
    audit.window.trading_dates,
    ticks_per_instrument_day=4,
  )
  second = build_synthetic_pressure_fixture(
    audit,
    audit.window.trading_dates,
    ticks_per_instrument_day=4,
  )
  ticks = [
    tick for items in first.ticks_by_instrument.values() for tick in items
  ]

  assert first.fixture_sha256 == second.fixture_sha256
  assert first.tick_count == 16
  assert len(
    {
      (tick.continuity_generation, tick.source_time_ms, tick.tick_ordinal)
      for tick in ticks
    }
  ) == len(ticks)
  assert all(tick.time.tzinfo is not None for tick in ticks)
  assert all(
    (date_time.hour, date_time.minute) >= (9, 30)
    and (date_time.hour, date_time.minute) <= (11, 29)
    or (date_time.hour, date_time.minute) >= (13, 0)
    and (date_time.hour, date_time.minute) <= (14, 59)
    for date_time in (tick.time for tick in ticks)
  )
