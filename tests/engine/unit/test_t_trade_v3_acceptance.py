from datetime import date, timedelta

import pytest
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
