import asyncio
import json
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
  requested_trading_days: int = DEFAULT_TRADING_DAYS,
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
    window=ReplayWindow(
      snapshot=snapshot,
      trading_dates=days,
      requested_trading_days=requested_trading_days,
    ),
    inspections=inspections,
  )


@pytest.mark.asyncio
async def test_completed_trading_date_resolver_excludes_today_weekend_and_holiday() -> None:
  # 2026-08-24 is an SH trading Monday.  It must be excluded even after the
  # market close; the supplied official calendar also models a non-contiguous
  # holiday/weekend gap before the five completed dates.
  official_calendar = (
    date(2026, 8, 12),
    date(2026, 8, 13),
    date(2026, 8, 14),
    date(2026, 8, 18),
    date(2026, 8, 19),
    date(2026, 8, 20),
    date(2026, 8, 21),
    date(2026, 8, 24),
  )

  async def fetch_calendar(_start: date, _end: date):
    return official_calendar

  weekday_result = await acceptance_module.resolve_completed_trading_dates(
    requested_days=5,
    as_of_date=date(2026, 8, 24),
    calendar_fetcher=fetch_calendar,
  )
  weekend_result = await acceptance_module.resolve_completed_trading_dates(
    requested_days=5,
    as_of_date=date(2026, 8, 23),
    calendar_fetcher=fetch_calendar,
  )

  assert weekday_result == (
    date(2026, 8, 14),
    date(2026, 8, 18),
    date(2026, 8, 19),
    date(2026, 8, 20),
    date(2026, 8, 21),
  )
  assert weekend_result == weekday_result
  assert all(item < date(2026, 8, 24) for item in weekday_result)


@pytest.mark.asyncio
async def test_completed_trading_date_resolver_fails_closed_when_insufficient() -> None:
  async def fetch_calendar(_start: date, _end: date):
    return (date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20))

  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="COMPLETED_TRADING_DAYS_INSUFFICIENT",
  ):
    await acceptance_module.resolve_completed_trading_dates(
      requested_days=5,
      as_of_date=date(2026, 8, 24),
      calendar_fetcher=fetch_calendar,
    )


@pytest.mark.asyncio
async def test_formal_d1_next20_window_uses_only_completed_calendar_dates(
  monkeypatch,
) -> None:
  snapshot = _snapshot(snapshot_date=date(2026, 7, 20))
  official_calendar = tuple(
    day
    for day in (
      date(2026, 7, 21) + timedelta(days=index) for index in range(40)
    )
    if day.weekday() < 5
  )

  class CalendarHelper:
    async def get_trading_calendar(self, *, market, start_date, end_date):
      assert market == "SH"
      return [item for item in official_calendar if start_date <= item <= end_date]

  monkeypatch.setattr(acceptance_module, "TradingDateHelper", CalendarHelper)
  windows = await acceptance_module.build_replay_windows(
    [snapshot],
    requested_trading_days=20,
    as_of_date=date(2026, 8, 24),
  )

  assert len(windows) == 1
  assert len(windows[0].trading_dates) == 20
  assert windows[0].trading_dates == official_calendar[:20]
  assert all(item < date(2026, 8, 24) for item in windows[0].trading_dates)


def test_recent_completed_diagnostic_uses_separate_default_report_path() -> None:
  args = acceptance_module.build_parser().parse_args(
    ["--recent-completed-trading-days", "5"]
  )

  report = acceptance_module._apply_recent_completed_diagnostic_report_path(args)

  assert report == acceptance_module.DEFAULT_RECENT_COMPLETED_DIAGNOSTIC_REPORT_PATH
  assert args.report != acceptance_module.DEFAULT_FORMAL_REPORT_PATH


@pytest.mark.asyncio
async def test_audit_tick_coverage_accepts_only_dual_single_day_empty_evidence(
  monkeypatch,
) -> None:
  snapshot = _snapshot()
  trading_day = date(2026, 6, 2)
  window = ReplayWindow(
    snapshot=snapshot,
    trading_dates=(trading_day,),
    requested_trading_days=1,
  )

  def missing_tick_day(_self, _service, _code, _day):
    return {
      "complete": False,
      "classification": "MISSING",
      "reason_codes": ["NO_TICK_DATA"],
      "statistics": {"record_count": 0},
      "message": "未找到 Tick 数据",
    }

  async def completed_empty(*, instrument_code, trading_dates):
    assert trading_dates == [trading_day]
    if instrument_code == "600000.SH":
      # The shared query already proved both exact-day Tick and 1d zero-row
      # audits. Formal acceptance must consume that authority instead of
      # re-implementing a second exception path.
      return {trading_day}
    # A completed verified 1d day-coverage record with data (including one
    # from a multi-day request) is a contradiction, so the shared query must
    # return no empty proof.
    return set()

  monkeypatch.setattr(
    acceptance_module.StrategyManager,
    "_inspect_t_trade_replay_tick_day",
    missing_tick_day,
  )
  monkeypatch.setattr(
    acceptance_module,
    "load_completed_empty_tick_days",
    completed_empty,
  )

  audit = (await acceptance_module.audit_tick_coverage([window]))[0]
  confirmed_empty = audit.inspections[("600000.SH", trading_day)]
  daily_nonempty = audit.inspections[("000001.SZ", trading_day)]

  assert confirmed_empty.complete is True
  assert confirmed_empty.classification == "CONFIRMED_EMPTY"
  assert confirmed_empty.reason_codes == ()
  assert confirmed_empty.statistics["completed_empty_tick_day"] is True
  assert confirmed_empty.statistics["completed_empty_daily_day"] is True
  assert daily_nonempty.complete is False
  assert daily_nonempty.classification == "MISSING"
  assert daily_nonempty.reason_codes == ("NO_TICK_DATA",)


@pytest.mark.asyncio
async def test_canonical_archive_audit_and_identity_never_construct_influx(
  monkeypatch,
) -> None:
  snapshot = _snapshot()
  trading_day = date(2026, 6, 2)
  window = ReplayWindow(
    snapshot=snapshot,
    trading_dates=(trading_day,),
    requested_trading_days=DEFAULT_TRADING_DAYS,
  )

  class Reader:
    cutover = SimpleNamespace(
      formal_scope=SimpleNamespace(
        snapshot_date=snapshot.snapshot_date,
        instrument_codes=snapshot.instrument_codes,
        trading_dates=window.trading_dates,
      )
    )

    @staticmethod
    def inspect_tick_day(*, instrument_code, trading_date):
      del instrument_code, trading_date
      return {
        "complete": True,
        "classification": "COMPLETE",
        "reason_codes": [],
        "statistics": {"record_count": 240},
      }

    @staticmethod
    def iter_tick_pages(**_kwargs):
      return iter(((object(),),))

  monkeypatch.setattr(
    acceptance_module,
    "HistoricalMarketDataService",
    lambda: (_ for _ in ()).throw(AssertionError("Influx must not be constructed")),
  )
  reader = Reader()

  audits = await acceptance_module.audit_canonical_tick_coverage(
    [window], reader=reader
  )
  identity = await acceptance_module.audit_source_identity(
    audits[0],
    window.trading_dates,
    archive_reader=reader,
  )

  assert audits[0].completed_pair_count == audits[0].expected_pair_count
  assert identity.passed is True
  assert identity.to_dict()["source"] == "IMMUTABLE_CANONICAL_TICK_ARCHIVE"


@pytest.mark.asyncio
async def test_audit_tick_coverage_fails_closed_when_empty_proof_lookup_fails(
  monkeypatch,
) -> None:
  snapshot = _snapshot()
  trading_day = date(2026, 6, 2)
  window = ReplayWindow(
    snapshot=snapshot,
    trading_dates=(trading_day,),
    requested_trading_days=1,
  )

  def missing_tick_day(_self, _service, _code, _day):
    return {
      "complete": False,
      "classification": "MISSING",
      "reason_codes": ["NO_TICK_DATA"],
      "statistics": {"record_count": 0},
      "message": "未找到 Tick 数据",
    }

  async def lookup_failure(**_kwargs):
    raise RuntimeError("daily empty-proof lookup unavailable")

  monkeypatch.setattr(
    acceptance_module.StrategyManager,
    "_inspect_t_trade_replay_tick_day",
    missing_tick_day,
  )
  monkeypatch.setattr(
    acceptance_module,
    "load_completed_empty_tick_days",
    lookup_failure,
  )

  audit = (await acceptance_module.audit_tick_coverage([window]))[0]
  for inspection in audit.inspections.values():
    assert inspection.complete is False
    assert inspection.classification == "MISSING"
    assert inspection.reason_codes == ("NO_TICK_DATA",)


def _operational_evidence() -> dict:
  return {
    "schema_version": 1,
    "historical_tick_transfer": {
      "status": "COMPLETED",
      "scope": {"snapshot_date": "2026-06-01"},
      "cumulative_records": {"received": 212_291, "saved": 212_291, "verified": 212_291},
      "strict_coverage": {
        "complete_instrument_days": 46,
        "expected_instrument_days": 160,
      },
      "source_identity": {"failed_instrument_days": 0},
    },
    "formal_causal_replay": {
      "completed_trading_days": 0,
      "requested_trading_days": 20,
      "stage": "SHADOW",
    },
    "restore_verify": {
      "status": "PASSED",
      "isolated_scratch_database": True,
      "production_database_restored": False,
      "forward_migration_only": True,
      "scratch_cleanup_verified": True,
      "qmt_journal_integrity_passed": True,
    },
    "rollout": {
      "paper": {
        "status": "BLOCKED",
        "consecutive_trading_days": 0,
        "required_consecutive_trading_days": 5,
        "completed_candidate_lifecycles": 0,
        "required_candidate_lifecycles": 20,
      },
      "canary": {"status": "NOT_EXECUTED"},
      "live": {"status": "NOT_EXECUTED"},
    },
  }


def test_formal_window_requires_all_holdings_all_days_and_abnormal_evidence() -> None:
  audit = _audit()

  assert select_formal_window([audit]) is None
  selected = select_formal_window(
    [audit], abnormal_dates=[audit.window.trading_dates[3]]
  )

  assert selected is audit
  assert audit.blockers(abnormal_dates=[audit.window.trading_dates[3]]) == []


def test_short_window_is_non_gating_diagnostic_and_blocks_formal_20_gate() -> None:
  audit = _audit(day_count=5, requested_trading_days=5)
  abnormal_dates = [audit.window.trading_dates[0]]

  assert select_formal_window([audit], abnormal_dates=abnormal_dates) is None
  report = build_report_document(
    [audit],
    requested_trading_days=5,
    abnormal_dates=abnormal_dates,
  )
  formal = report["formal_20_trading_day"]
  diagnostic = report["short_window_coverage_diagnostic"]
  markdown = render_markdown(report, json_name="acceptance.json")

  assert formal == {
    "status": "BLOCKED",
    "selected_snapshot_date": None,
    "execution": None,
    "blocker": "FORMAL_20_TRADING_DAYS_REQUIRED",
  }
  assert diagnostic["status"] == "COVERAGE_DIAGNOSTIC_NON_GATING"
  assert diagnostic["requested_trading_days"] == 5
  assert diagnostic["candidate_count"] == 1
  assert diagnostic["complete_candidate_count"] == 1
  assert "formal_gate_blockers" not in diagnostic["coverage_windows"][0]
  diagnostic_json = json.dumps(diagnostic).upper()
  for prohibited_term in ("FORMAL", "PAPER", "CANARY", "LIVE"):
    assert prohibited_term not in diagnostic_json
  diagnostic_section = markdown.split("## 短窗口覆盖诊断（NON_GATING）", 1)[1].split(
    "## 真实短窗口 source identity 预检", 1
  )[0]
  assert "COVERAGE_DIAGNOSTIC_NON_GATING" in diagnostic_section
  assert "不产生回放、审批或上线结论" in diagnostic_section
  for prohibited_term in ("FORMAL", "PAPER", "CANARY", "LIVE"):
    assert prohibited_term not in diagnostic_section.upper()
  assert "READY_NOT_EXECUTED" not in markdown


def test_twenty_day_report_retains_ready_and_pass_formal_behavior() -> None:
  audit = _audit()
  abnormal_dates = [audit.window.trading_dates[0]]

  ready = build_report_document(
    [audit],
    requested_trading_days=DEFAULT_TRADING_DAYS,
    abnormal_dates=abnormal_dates,
  )
  passed = build_report_document(
    [audit],
    requested_trading_days=DEFAULT_TRADING_DAYS,
    abnormal_dates=abnormal_dates,
    formal_execution={"replay": {"status": "COMPLETED"}},
  )

  assert ready["formal_20_trading_day"]["status"] == "READY_NOT_EXECUTED"
  assert ready["formal_20_trading_day"]["selected_snapshot_date"] == "2026-06-01"
  assert ready["short_window_coverage_diagnostic"] is None
  assert passed["formal_20_trading_day"]["status"] == "PASS"
  assert passed["formal_20_trading_day"]["execution"] == {
    "replay": {"status": "COMPLETED"}
  }


def test_report_rejects_pseudo_formal_execution_without_ready_20_day_window() -> None:
  formal_execution = {"replay": {"status": "COMPLETED"}}
  no_abnormal_evidence = _audit()
  incomplete_twenty_day_window = _audit(
    day_count=19,
    requested_trading_days=DEFAULT_TRADING_DAYS,
  )
  short_window = _audit(day_count=5, requested_trading_days=5)

  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="FORMAL_EXECUTION_REQUIRES_READY_20_TRADING_DAY_WINDOW",
  ):
    build_report_document(
      [no_abnormal_evidence],
      requested_trading_days=DEFAULT_TRADING_DAYS,
      formal_execution=formal_execution,
    )
  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="FORMAL_EXECUTION_REQUIRES_READY_20_TRADING_DAY_WINDOW",
  ):
    build_report_document(
      [incomplete_twenty_day_window],
      requested_trading_days=DEFAULT_TRADING_DAYS,
      abnormal_dates=[incomplete_twenty_day_window.window.trading_dates[0]],
      formal_execution=formal_execution,
    )
  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="FORMAL_EXECUTION_REQUIRES_EXACTLY_20_TRADING_DAYS",
  ):
    build_report_document(
      [short_window],
      requested_trading_days=5,
      abnormal_dates=[short_window.window.trading_dates[0]],
      formal_execution=formal_execution,
    )


def test_reused_short_window_report_cannot_retain_formal_20_claim() -> None:
  report = {
    "scope": {"requested_trading_days": 5},
    "candidate_windows": [],
    "formal_20_trading_day": {
      "status": "PASS",
      "selected_snapshot_date": "2026-06-01",
      "execution": {"replay": {"status": "COMPLETED"}},
      "blocker": None,
    },
  }

  acceptance_module._fail_closed_reused_formal_gate(report)

  assert report["formal_20_trading_day"] == {
    "status": "BLOCKED",
    "selected_snapshot_date": None,
    "execution": None,
    "blocker": "FORMAL_20_TRADING_DAYS_REQUIRED",
  }


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


def test_all_backtests_enable_generic_day_batch_runtime_state() -> None:
  def runtime(mode: StrategyRunMode, parameters: dict) -> SimpleNamespace:
    return SimpleNamespace(
      context=SimpleNamespace(mode=mode, parameters=parameters),
    )

  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(StrategyRunMode.BACKTEST, {})
  ) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(
      StrategyRunMode.BACKTEST,
      {
        "t_trade_replay": True,
        "replay_acceptance": "V3_CAUSAL_20D",
      },
    )
  ) is True
  assert StrategyExecutor._runtime_state_persistence_enabled(
    runtime(
      StrategyRunMode.BACKTEST,
      {
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
    "elapsed_seconds": 12.5,
    "timeout_seconds": 1_800.0,
    "timed_out": False,
    "cancellation": None,
    "isolated_backtest": True,
    "no_live_or_paper_broker": True,
    "execution_boundary": {
      "strategy_run_mode": "BACKTEST",
      "runtime_state_persist_enabled": True,
      "runtime_state_checkpoint_policy": "DAY_BATCH",
      "qmt_invocation": False,
      "paper_or_live_command": False,
    },
    "terminal_convergence": {"status": "TERMINAL"},
    "run_evidence": {
      "run_id": "full-run",
      "mode": "backtest",
      "status": "completed",
      "parameters": {
        "t_trade_replay": True,
        "replay_acceptance": "V3_PRESSURE_BASELINE",
        "runtime_state_checkpoint_policy": "DAY_BATCH",
      },
    },
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


def test_report_renders_operational_evidence_without_upgrading_rollout_gate() -> None:
  audit = _audit(incomplete_pairs={("600000.SH", 1)})
  evidence = _operational_evidence()
  evidence["restore_verify"].update(
    {
      "source_schema_revision": "20260822_0028_qmt_agent_handover",
      "target_schema_revision": "20260823_0031_watchlist_groups",
    }
  )

  report = build_report_document(
    [audit],
    requested_trading_days=DEFAULT_TRADING_DAYS,
    abnormal_dates=[audit.window.trading_dates[0]],
    operational_evidence=evidence,
  )
  markdown = render_markdown(report, json_name="acceptance.json")

  assert "received/saved/verified=212291/212291/212291" in markdown
  assert "严格覆盖：46/160 instrument-day" in markdown
  assert "source identity 严格 keyset 失败=0" in markdown
  assert "正式因果回放：0/20；stage=SHADOW" in markdown
  assert "production database restored=False" in markdown
  assert "PAPER BLOCKED（连续交易日 0/5，完成候选生命周期 0/20）" in markdown
  assert "CANARY=NOT_EXECUTED；LIVE=NOT_EXECUTED" in markdown
  assert report["formal_20_trading_day"]["status"] == "BLOCKED"


@pytest.mark.parametrize(
  ("field", "invalid_value"),
  [
    ("status", "BLOCKED"),
    ("isolated_scratch_database", False),
    ("production_database_restored", True),
    ("forward_migration_only", False),
    ("scratch_cleanup_verified", False),
    ("qmt_journal_integrity_passed", False),
  ],
)
def test_operational_evidence_rejects_unverified_restore_boundary(
  tmp_path,
  field: str,
  invalid_value: object,
) -> None:
  evidence = _operational_evidence()
  evidence["restore_verify"][field] = invalid_value
  path = tmp_path / "operational-evidence.json"
  path.write_text(json.dumps(evidence), encoding="utf-8")

  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="RESTORE_VERIFY_BOUNDARY_INVALID",
  ):
    acceptance_module._load_operational_evidence(path)


def test_completed_pressure_reuse_replaces_stale_candidate_coverage() -> None:
  stale = _audit(
    day_count=2,
    incomplete_pairs={("600000.SH", 1)},
  )
  refreshed = _audit(day_count=2)
  report = {
    "candidate_windows": [stale.to_dict()],
    "formal_20_trading_day": {
      "status": "BLOCKED",
      "selected_snapshot_date": None,
      "execution": None,
      "blocker": "NO_ALL_HOLDINGS_20_TRADING_DAY_WINDOW",
    },
  }

  acceptance_module._replace_reused_candidate_window(report, refreshed)

  current = report["candidate_windows"][0]
  assert current["coverage"]["complete_instrument_days"] == 4
  assert current["coverage"]["expected_instrument_days"] == 4
  assert current["missing_instrument_days"] == []

  evidence = _operational_evidence()
  evidence["historical_tick_transfer"]["strict_coverage"] = {
    "complete_instrument_days": 4,
    "expected_instrument_days": 4,
  }
  acceptance_module._assert_operational_evidence_matches_refreshed_window(
    evidence,
    refreshed,
    {"passed": True, "failure": None},
  )

  evidence["historical_tick_transfer"]["strict_coverage"][
    "complete_instrument_days"
  ] = 3
  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="OPERATIONAL_EVIDENCE_REFRESH_MISMATCH",
  ):
    acceptance_module._assert_operational_evidence_matches_refreshed_window(
      evidence,
      refreshed,
      {"passed": True, "failure": None},
    )


def _completed_pressure_report_payload() -> dict:
  return {
    "pressure_baseline": {
      "status": "EXECUTED_SYNTHETIC_NON_HISTORICAL",
      "fixture": {
        "tick_count": 9_600,
        "ticks_per_instrument_day": 600,
        "trading_dates": ["2026-06-04", "2026-06-05"],
        "held_instruments": [
          "000001.SZ",
          "000002.SZ",
          "000003.SZ",
          "000004.SZ",
          "000005.SZ",
          "000006.SZ",
          "000007.SZ",
          "000008.SZ",
        ],
      },
      "replay": {
        "status": "COMPLETED",
        "account_id": "must-not-appear",
        "progress_pct": 100.0,
      },
      "execution_boundary": {
        "strategy_run_mode": "BACKTEST",
        "runtime_state_persist_enabled": True,
        "runtime_state_checkpoint_policy": "DAY_BATCH",
        "qmt_invocation": False,
        "paper_or_live_command": False,
      },
      "terminal_convergence": {"status": "TERMINAL"},
      "isolated_backtest": True,
      "no_live_or_paper_broker": True,
      "throughput": {"engine_ticks_processed": 9_472},
      "latency": {"engine_tick": {"sample_count": 9_472}},
      "cas": {"checkpoint_attempts": 9_473},
      "database_write_activity": {
        "runtime_state": {
          "state_upsert_attempts": 9_503,
          "snapshot_save_failures": 0,
        }
      },
      "run_evidence": {
        "run_id": "full-run",
        "mode": "backtest",
        "status": "completed",
        "parameters_sha256": "fixture-proof",
        "parameters": {
          "account_id": "must-not-appear",
          "t_trade_replay": True,
          "replay_acceptance": "V3_PRESSURE_BASELINE",
          "runtime_state_checkpoint_policy": "DAY_BATCH",
        },
        "evaluations": {
          "material_rows": 1_072,
          "diagnostic_logical_events": 8_400,
        },
      },
    }
  }


def test_completed_pressure_import_requires_sealed_backtest_and_redacts_account(
  tmp_path,
) -> None:
  raw_pressure_report = _completed_pressure_report_payload()
  path = tmp_path / "completed-pressure.json"
  path.write_text(json.dumps(raw_pressure_report), encoding="utf-8")

  imported = acceptance_module._load_completed_pressure_baseline(path)

  assert imported["replay"] == {"status": "COMPLETED", "progress_pct": 100.0}
  assert imported["run_evidence"]["parameters"] == {
    "t_trade_replay": True,
    "replay_acceptance": "V3_PRESSURE_BASELINE",
    "runtime_state_checkpoint_policy": "DAY_BATCH",
  }
  assert imported["tick_accounting"] == {
    "requested_fixture_ticks": 9_600,
    "engine_ticks_processed": 9_472,
    "policy_filtered_ticks": 128,
    "policy_filtered_per_instrument_day": 8,
    "instrument_day_count": 16,
    "expected_policy_filtered_ticks": 128,
    "continuous_pm_policy": "13:00 <= local_time < 14:57",
    "policy_filtered_time_range": "14:57:10..14:59:59",
    "accounting_passed": True,
  }
  assert "must-not-appear" not in json.dumps(imported)

  raw_pressure_report["pressure_baseline"]["execution_boundary"][
    "qmt_invocation"
  ] = True
  path.write_text(json.dumps(raw_pressure_report), encoding="utf-8")
  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="COMPLETED_PRESSURE_EVIDENCE_INVALID",
  ):
    acceptance_module._load_completed_pressure_baseline(path)


@pytest.mark.parametrize(
  ("field_path", "invalid_value"),
  [
    (("pressure_baseline", "isolated_backtest"), False),
    (("pressure_baseline", "no_live_or_paper_broker"), False),
    (("pressure_baseline", "run_evidence", "mode"), "live"),
    (("pressure_baseline", "execution_boundary", "strategy_run_mode"), "LIVE"),
    (
      (
        "pressure_baseline",
        "execution_boundary",
        "runtime_state_persist_enabled",
      ),
      False,
    ),
    (
      (
        "pressure_baseline",
        "execution_boundary",
        "runtime_state_checkpoint_policy",
      ),
      "SESSION_BOUNDARY",
    ),
    (
      (
        "pressure_baseline",
        "run_evidence",
        "parameters",
        "runtime_state_checkpoint_policy",
      ),
      "SESSION_BOUNDARY",
    ),
    (("pressure_baseline", "execution_boundary", "paper_or_live_command"), True),
    (
      (
        "pressure_baseline",
        "run_evidence",
        "evaluations",
        "diagnostic_logical_events",
      ),
      8_399,
    ),
    (
      (
        "pressure_baseline",
        "run_evidence",
        "evaluations",
        "material_rows",
      ),
      True,
    ),
    (
      (
        "pressure_baseline",
        "run_evidence",
        "evaluations",
        "diagnostic_logical_events",
      ),
      [],
    ),
  ],
)
def test_completed_pressure_import_fails_closed_for_boundary_or_count_mismatch(
  tmp_path,
  field_path: tuple[str, ...],
  invalid_value: object,
) -> None:
  raw_pressure_report = _completed_pressure_report_payload()
  target: dict = raw_pressure_report
  for field in field_path[:-1]:
    target = target[field]
  target[field_path[-1]] = invalid_value
  path = tmp_path / "completed-pressure-invalid.json"
  path.write_text(json.dumps(raw_pressure_report), encoding="utf-8")

  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="COMPLETED_PRESSURE_EVIDENCE_INVALID",
  ):
    acceptance_module._load_completed_pressure_baseline(path)


@pytest.mark.asyncio
async def test_run_cli_rejects_short_formal_execute_before_database_or_audit(
  monkeypatch,
) -> None:
  calls: list[str] = []

  async def unexpected_snapshot_load(*_args, **_kwargs):
    calls.append("snapshot_load")
    raise AssertionError("short formal execute must stop before database access")

  async def unexpected_audit(*_args, **_kwargs):
    calls.append("audit")
    raise AssertionError("short formal execute must stop before audit")

  monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
  monkeypatch.setattr(
    acceptance_module,
    "load_snapshot_portfolios",
    unexpected_snapshot_load,
  )
  monkeypatch.setattr(acceptance_module, "audit_tick_coverage", unexpected_audit)
  args = acceptance_module.build_parser().parse_args(
    ["--execute", "--trading-days", "5"]
  )

  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="FORMAL_EXECUTION_REQUIRES_EXACTLY_20_TRADING_DAYS",
  ):
    await acceptance_module.run_cli(args)

  assert calls == []


@pytest.mark.asyncio
async def test_run_cli_completed_pressure_rejects_mismatched_operational_evidence(
  monkeypatch,
  tmp_path,
) -> None:
  audit = _audit()
  pressure_path = tmp_path / "completed-pressure.json"
  pressure_path.write_text(
    json.dumps(_completed_pressure_report_payload()), encoding="utf-8"
  )
  operational_path = tmp_path / "operational-evidence.json"
  # This addendum is structurally valid but its 46/160 transfer scope does
  # not match this isolated two-holding audit's 40 instrument-days.
  operational_path.write_text(
    json.dumps(_operational_evidence()), encoding="utf-8"
  )

  async def fake_load_snapshots(*, account_id=None):
    del account_id
    return [audit.window.snapshot]

  async def fake_build_windows(snapshots, *, requested_trading_days):
    assert snapshots == [audit.window.snapshot]
    assert requested_trading_days == DEFAULT_TRADING_DAYS
    return [audit.window]

  async def fake_audit_coverage(windows, *, max_concurrency):
    assert windows == [audit.window]
    assert max_concurrency == 4
    return [audit]

  async def fake_source_identity(_audit, _dates, **_kwargs):
    return SimpleNamespace(to_dict=lambda: {"passed": True, "failure": None})

  monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
  monkeypatch.setattr(
    acceptance_module, "load_snapshot_portfolios", fake_load_snapshots
  )
  monkeypatch.setattr(acceptance_module, "build_replay_windows", fake_build_windows)
  monkeypatch.setattr(acceptance_module, "audit_tick_coverage", fake_audit_coverage)
  monkeypatch.setattr(acceptance_module, "audit_source_identity", fake_source_identity)
  args = acceptance_module.build_parser().parse_args(
    [
      "--completed-pressure-report",
      str(pressure_path),
      "--operational-evidence",
      str(operational_path),
      "--pressure-snapshot-date",
      "2026-06-01",
      "--report",
      str(tmp_path / "acceptance.md"),
    ]
  )

  with pytest.raises(
    acceptance_module.AcceptanceBlockedError,
    match="OPERATIONAL_EVIDENCE_REFRESH_MISMATCH",
  ):
    await acceptance_module.run_cli(args)


def test_completed_9600_without_day_batch_policy_cannot_freeze_slo() -> None:
  evidence = acceptance_module._performance_remediation_evidence(
    {
      "status": "EXECUTED_SYNTHETIC_NON_HISTORICAL",
      "fixture": {"tick_count": 9_600},
      "replay": {"status": "COMPLETED"},
      "terminal_convergence": {"status": "TERMINAL"},
      "isolated_backtest": True,
      "no_live_or_paper_broker": True,
      "execution_boundary": {
        "strategy_run_mode": "BACKTEST",
        "runtime_state_persist_enabled": True,
        "runtime_state_checkpoint_policy": "SESSION_BOUNDARY",
        "qmt_invocation": False,
        "paper_or_live_command": False,
      },
      "run_evidence": {
        "mode": "BACKTEST",
        "status": "COMPLETED",
        "parameters": {
          "replay_acceptance": "V3_PRESSURE_BASELINE",
          "runtime_state_checkpoint_policy": "DAY_BATCH",
        },
      },
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


def test_day_batch_diagnostic_is_never_rendered_as_nonpersistent_calibration() -> None:
  sealed = {
    "status": "EXECUTED_DIAGNOSTIC_NON_GATING",
    "diagnostic_non_gating": True,
    "run_evidence": {
      "run_id": "sealed-run",
      "parameters": {
        "runtime_state_checkpoint_policy": "DAY_BATCH",
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
        "runtime_state_checkpoint_policy": "DAY_BATCH",
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


@pytest.mark.asyncio
async def test_isolated_historical_acceptance_blocks_current_sync_backfill_path(
  monkeypatch,
) -> None:
  audit = _audit(day_count=1)
  sync_blocked = False

  async def original_optional_supplement(*_args, **_kwargs):
    raise AssertionError("old optional supplement path must not be used")

  async def original_sync(*_args, **_kwargs):
    raise AssertionError("current blocking sync path must be intercepted")

  manager = SimpleNamespace(
    _queue_missing_backtest_data_supplement=original_optional_supplement,
    _sync_missing_backtest_data=original_sync,
    get_run=lambda _run_id: runtime,
  )
  runtime = SimpleNamespace(
    task=None,
    state_manager=SimpleNamespace(snapshot_cas_conflicts=0),
  )

  class FakeReplayService:
    def __init__(self, supplied_manager) -> None:
      assert supplied_manager is manager

    async def start(self, _payload, *, request_id):
      assert request_id

      async def race_with_missing_data() -> None:
        nonlocal sync_blocked
        with pytest.raises(
          RuntimeError,
          match="V3_ACCEPTANCE_FORBIDS_MARKET_DATA_SUPPLEMENT",
        ):
          await manager._sync_missing_backtest_data()
        sync_blocked = True

      runtime.task = asyncio.create_task(race_with_missing_data())
      return {"run_id": "isolated-backtest"}

    async def get(self, _run_id):
      return {"status": "ERROR"}

  class Counter:
    def to_dict(self):
      return {"sample_count": 0}

  class Instrumentation:
    def __init__(self) -> None:
      self.engine_tick = Counter()
      self.strategy_evaluation = Counter()
      self.state_checkpoint = Counter()
      self.db_writes = SimpleNamespace(to_dict=lambda: {})

    async def __aenter__(self):
      return self

    async def __aexit__(self, *_args):
      return None

  async def passed_identity(_audit, _trading_dates, **_kwargs):
    return SimpleNamespace(passed=True, to_dict=lambda: {"passed": True})

  async def run_evidence(_run_id):
    return {"mode": "BACKTEST", "status": "ERROR"}

  monkeypatch.setattr(acceptance_module, "StrategyManager", lambda: manager)
  monkeypatch.setattr(acceptance_module, "TTradeReplayService", FakeReplayService)
  monkeypatch.setattr(acceptance_module, "BenchmarkInstrumentation", Instrumentation)
  monkeypatch.setattr(acceptance_module, "audit_source_identity", passed_identity)
  monkeypatch.setattr(acceptance_module, "_load_run_evidence", run_evidence)
  original_sync_reference = manager._sync_missing_backtest_data
  original_optional_reference = manager._queue_missing_backtest_data_supplement

  result = await acceptance_module.execute_isolated_backtest(
    audit,
    audit.window.trading_dates,
    formal_gate=True,
  )

  assert sync_blocked is True
  assert result["market_data_supplement_attempts"] == 1
  assert manager._sync_missing_backtest_data is original_sync_reference
  assert (
    manager._queue_missing_backtest_data_supplement
    is original_optional_reference
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


@pytest.mark.asyncio
async def test_run_evidence_separates_durable_actionable_facts_from_material(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class _Result:
    def __init__(self, *, rows=(), one=None) -> None:
      self._rows = list(rows)
      self._one = one

    def all(self):
      return list(self._rows)

    def one(self):
      return self._one

  class _Database:
    def __init__(self) -> None:
      self.execute_calls = 0

    async def get(self, _model, _run_id):
      return SimpleNamespace(
        mode="BACKTEST",
        status="COMPLETED",
        error_message=None,
        parameters={"t_trade_replay": True},
        instruments=[],
      )

    async def execute(self, _statement):
      self.execute_calls += 1
      if self.execute_calls == 1:
        return _Result(rows=[("MATERIAL", 11, 11)])
      if self.execute_calls == 2:
        return _Result(one=(7, 5))
      if self.execute_calls == 3:
        return _Result(one=(9, 3))
      return _Result(rows=[])

  database = _Database()

  async def fake_get_async_db():
    yield database

  monkeypatch.setattr(acceptance_module, "get_async_db", fake_get_async_db)

  evidence = await acceptance_module._load_run_evidence(
    "run-1",
    include_durable_actionable_fact_observation=True,
  )

  observation = evidence["durable_actionable_fact_observation"]
  assert database.execute_calls == 4
  assert observation["measurement_scope"] == (
    "RUN_SCOPED_DURABLE_ROW_COUNTS_AT_OBSERVATION"
  )
  assert observation["trade_intent_rows"] == 7
  assert observation["immediate_actionable_trade_intent_rows"] == 5
  assert observation["candidate_lifecycle_rows"] == 9
  assert observation["immediate_actionable_candidate_rows"] is None
  assert "pure MATERIAL" in observation[
    "immediate_actionable_candidate_rows_unavailable_reason"
  ]
  assert observation["candidate_lifecycle_rows_with_post_fill_status"] == 3
  assert observation["simulated_fill_event_rows"] is None
  assert "not a fill-event count" in observation[
    "candidate_lifecycle_rows_with_post_fill_status_semantics"
  ]
  assert "MATERIAL without a TradeIntent" in observation[
    "ordinary_material_evaluations"
  ]
  assert "not per-commit attribution" in observation["commit_attribution"]

  safe = acceptance_module._safe_pressure_run_evidence(evidence)
  assert safe["durable_actionable_fact_observation"] == observation


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


@pytest.mark.asyncio
async def test_synthetic_pressure_data_check_accepts_manager_archive_keyword(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Exercise the real Manager start seam used by synthetic pressure.

  ``StrategyManager.start_strategy`` now always supplies
  ``canonical_archive_adapter=`` to its data preflight.  The synthetic
  pressure override must accept that keyword, verify it is ``None``, and
  continue to the synthetic executor loader rather than falling back to any
  historical/archive adapter.
  """

  manager_class = acceptance_module.StrategyManager
  manager = object.__new__(manager_class)
  executor_calls: list[str] = []
  runtime = SimpleNamespace(
    context=SimpleNamespace(
      mode=StrategyRunMode.BACKTEST,
      parameters={"t_trade_replay": True},
      backtest_id=None,
    ),
    status=None,
    error_message=None,
  )

  async def original_data_check(*_args, **_kwargs) -> None:
    return None

  async def original_supplement(*_args, **_kwargs) -> dict[str, object]:
    return {}

  async def synthetic_executor_start(run_id: str, **_kwargs) -> bool:
    executor_calls.append(run_id)
    return False

  manager.executor = SimpleNamespace(
    get=lambda run_id: runtime if run_id == "pressure-seam" else None,
    start=synthetic_executor_start,
  )
  manager._ensure_backtest_data_available = original_data_check
  manager._queue_missing_backtest_data_supplement = original_supplement

  class _StopAfterPressureStart(Exception):
    pass

  class PressureSeamReplayService:
    def __init__(self, supplied_manager) -> None:
      assert supplied_manager is manager

    async def start(self, *_args, **_kwargs) -> dict[str, str]:
      assert await manager.start_strategy("pressure-seam") is False
      raise _StopAfterPressureStart()

  monkeypatch.setattr(acceptance_module, "StrategyManager", lambda: manager)
  monkeypatch.setattr(
    acceptance_module,
    "TTradeReplayService",
    PressureSeamReplayService,
  )

  with pytest.raises(_StopAfterPressureStart):
    await acceptance_module.execute_synthetic_pressure_baseline(
      _audit(day_count=2),
      _audit(day_count=2).window.trading_dates,
      ticks_per_instrument_day=2,
      timeout_seconds=1.0,
    )

  assert executor_calls == ["pressure-seam"]
  assert manager._ensure_backtest_data_available is original_data_check
  assert manager._queue_missing_backtest_data_supplement is original_supplement


@pytest.mark.asyncio
@pytest.mark.parametrize(
  (
    "checkpoint_policy",
    "expected_enabled",
    "expected_evidence",
    "expected_boundary",
  ),
  [
    (
      "DAY_BATCH",
      True,
      "DAY_BATCH BACKTEST runtime-state checkpoint policy present",
      "sealed durable DAY_BATCH BACKTEST runtime-state checkpoint",
    ),
    (
      None,
      False,
      "DAY_BATCH BACKTEST runtime-state checkpoint policy absent",
      "without a DAY_BATCH BACKTEST runtime-state checkpoint",
    ),
  ],
)
async def test_cancelled_pressure_classifies_day_batch_persistence(
  monkeypatch: pytest.MonkeyPatch,
  checkpoint_policy: str | None,
  expected_enabled: bool,
  expected_evidence: str,
  expected_boundary: str,
) -> None:
  """Cancelled evidence is durable only when its policy is ``DAY_BATCH``."""

  started_at = acceptance_module.datetime(2026, 8, 20, 9, 30)
  ended_at = acceptance_module.datetime(2026, 8, 20, 9, 31)
  parameters = {"replay_acceptance": "V3_PRESSURE_BASELINE"}
  if checkpoint_policy is not None:
    parameters["runtime_state_checkpoint_policy"] = checkpoint_policy

  class _Result:
    def __init__(self, *, scalar=None, rows=()) -> None:
      self._scalar = scalar
      self._rows = list(rows)

    def scalar_one_or_none(self):
      return self._scalar

    def all(self):
      return list(self._rows)

  class _Database:
    def __init__(self) -> None:
      self.execute_calls = 0

    async def get(self, _model, _run_id):
      return SimpleNamespace(
        parameters=parameters,
        status="STOPPED",
        created_at=started_at,
        updated_at=ended_at,
      )

    async def execute(self, _statement):
      self.execute_calls += 1
      if self.execute_calls == 1:
        return _Result(
          scalar=SimpleNamespace(start_time=started_at, end_time=ended_at)
        )
      return _Result(rows=[("COALESCED_DIAGNOSTIC", 1, 32)])

  database = _Database()

  async def fake_get_async_db():
    yield database

  class _ReplayService:
    async def get(self, _run_id):
      return {
        "status": "CANCELLED",
        "processed_until": ended_at,
        "progress_pct": 25.0,
      }

  monkeypatch.setattr(acceptance_module, "get_async_db", fake_get_async_db)
  monkeypatch.setattr(acceptance_module, "TTradeReplayService", _ReplayService)
  fixture = SimpleNamespace(to_dict=lambda: {"tick_count": 9_600})

  evidence = await acceptance_module.load_cancelled_full_pressure_attempt(
    "cancelled-sealed-run",
    cancellation_reason="operator authorized",
    fixture=fixture,
  )

  persistence = evidence["runtime_state_persistence"]
  assert persistence["enabled"] is expected_enabled
  assert expected_evidence in persistence["evidence"]
  assert expected_boundary in evidence["primary_observed_boundary"]
