import json
from types import SimpleNamespace

from core.strategy_performance import StrategyPerformanceService


def test_build_performance_calculates_core_risk_and_trade_stats():
  performance = StrategyPerformanceService.build_performance(
    run_id="run-1",
    backtest_id="bt-1",
    mode="backtest",
    samples=[
      {
        "sequence": 1,
        "timestamp": "2026-04-01T09:30:00",
        "event_type": "tick",
        "equity": 100.0,
        "return_pct": 0.0,
        "drawdown_pct": 0.0,
      },
      {
        "sequence": 2,
        "timestamp": "2026-04-02T09:30:00",
        "event_type": "trade",
        "equity": 110.0,
        "return_pct": 10.0,
        "drawdown_pct": 0.0,
        "metadata": {"trade_pnl_delta": 10.0},
      },
      {
        "sequence": 3,
        "timestamp": "2026-04-03T09:30:00",
        "event_type": "trade",
        "equity": 105.0,
        "return_pct": 5.0,
        "drawdown_pct": 4.5454545,
        "metadata": {"trade_pnl_delta": -5.0},
      },
      {
        "sequence": 4,
        "timestamp": "2026-04-03T10:00:00",
        "event_type": "order",
        "equity": 105.0,
        "return_pct": 5.0,
        "drawdown_pct": 4.5454545,
        "metadata": {"status": "PENDING"},
      },
    ],
    metrics={
      "initial_capital": 100.0,
      "trade_intents_generated": 3,
      "orders_placed": 2,
      "trades_executed": 2,
    },
    benchmark_code=None,
    source="test",
    limit=None,
  )

  assert performance["summary"]["total_return_pct"] == 5.0
  assert round(performance["summary"]["max_drawdown_pct"], 2) == 4.55
  assert performance["trade_stats"]["total_trades"] == 2
  assert performance["trade_stats"]["winning_trades"] == 1
  assert performance["trade_stats"]["losing_trades"] == 1
  assert performance["trade_stats"]["profit_factor"] == 2.0
  assert performance["execution_quality"]["fill_rate_pct"] == 100.0


def test_pending_or_rejected_orders_do_not_count_as_trades():
  performance = StrategyPerformanceService.build_performance(
    run_id="run-1",
    backtest_id=None,
    mode="paper",
    samples=[
      {
        "sequence": 1,
        "timestamp": "2026-04-01T09:30:00",
        "event_type": "order",
        "equity": 100.0,
        "return_pct": 0.0,
        "drawdown_pct": 0.0,
        "metadata": {"status": "REJECTED"},
      }
    ],
    metrics={
      "initial_capital": 100.0,
      "trade_intents_generated": 1,
      "orders_placed": 0,
      "trades_executed": 0,
      "rejected_orders": 1,
    },
    benchmark_code=None,
    source="test",
    limit=None,
  )

  assert performance["trade_stats"]["total_trades"] == 0
  assert performance["execution_quality"]["rejected_orders"] == 1
  assert performance["execution_quality"]["fill_rate_pct"] == 0.0


def test_missing_benchmark_returns_warning_instead_of_error():
  performance = StrategyPerformanceService.build_performance(
    run_id="run-1",
    backtest_id="bt-1",
    mode="backtest",
    samples=[
      {
        "sequence": 1,
        "timestamp": "2026-04-01T09:30:00",
        "event_type": "tick",
        "equity": 100.0,
        "return_pct": 0.0,
        "drawdown_pct": 0.0,
      }
    ],
    metrics={"initial_capital": 100.0},
    benchmark_code="000300.SH",
    source="test",
    limit=None,
  )

  assert performance["benchmark_code"] is None
  assert "基准数据暂不可用" in performance["data_quality"]["warning"]


def test_snapshot_pagination_without_cursor_samples_full_curve():
  performance = {
    "equity_curve": [
      {"sequence": idx, "value": float(idx)}
      for idx in range(1, 101)
    ],
    "drawdown_curve": [
      {"sequence": idx, "value": float(idx)}
      for idx in range(1, 101)
    ],
    "data_quality": {"sample_count": 100},
  }

  paged = StrategyPerformanceService.paginate_performance(
    performance,
    cursor=None,
    limit=10,
  )

  assert len(paged["equity_curve"]) == 10
  assert paged["equity_curve"][0]["sequence"] == 1
  assert paged["equity_curve"][-1]["sequence"] == 100
  assert paged["drawdown_curve"][-1]["sequence"] == 100
  assert paged["data_quality"]["returned_sample_count"] == 10
  assert paged["data_quality"]["truncated"] is True
  assert paged["page_info"]["has_more"] is False


def test_backtest_snapshot_compresses_redundant_samples_but_keeps_key_points():
  samples = [
    {
      "sequence": 1,
      "timestamp": "2026-04-01T09:30:00",
      "event_type": "tick",
      "equity": 100.0,
      "return_pct": 0.0,
      "drawdown_pct": 0.0,
    },
    {
      "sequence": 2,
      "timestamp": "2026-04-01T09:30:05",
      "event_type": "tick",
      "equity": 100.0,
      "return_pct": 0.0,
      "drawdown_pct": 0.0,
    },
    {
      "sequence": 3,
      "timestamp": "2026-04-01T09:30:10",
      "event_type": "tick",
      "equity": 105.0,
      "return_pct": 5.0,
      "drawdown_pct": 0.0,
    },
    {
      "sequence": 4,
      "timestamp": "2026-04-01T09:30:20",
      "event_type": "tick",
      "equity": 95.0,
      "return_pct": -5.0,
      "drawdown_pct": 9.5238095,
    },
    {
      "sequence": 5,
      "timestamp": "2026-04-01T09:30:50",
      "event_type": "tick",
      "equity": 100.0,
      "return_pct": 0.0,
      "drawdown_pct": 4.7619048,
    },
    {
      "sequence": 6,
      "timestamp": "2026-04-01T09:30:55",
      "event_type": "order",
      "equity": 100.0,
      "return_pct": 0.0,
      "drawdown_pct": 4.7619048,
      "metadata": {"status": "PENDING"},
    },
    {
      "sequence": 7,
      "timestamp": "2026-04-01T09:31:10",
      "event_type": "tick",
      "equity": 100.0,
      "return_pct": 0.0,
      "drawdown_pct": 4.7619048,
    },
    {
      "sequence": 8,
      "timestamp": "2026-04-01T09:31:15",
      "event_type": "trade",
      "equity": 100.0,
      "return_pct": 0.0,
      "drawdown_pct": 4.7619048,
      "metadata": {"trade_pnl_delta": 0.0},
    },
    {
      "sequence": 9,
      "timestamp": "2026-04-02T09:31:50",
      "event_type": "tick",
      "equity": 110.0,
      "return_pct": 10.0,
      "drawdown_pct": 0.0,
    },
  ]

  performance = StrategyPerformanceService.build_performance(
    run_id="run-1",
    backtest_id="bt-1",
    mode="backtest",
    samples=samples,
    metrics={"initial_capital": 100.0},
    benchmark_code=None,
    source="backtest_snapshot",
    limit=None,
  )

  sequences = [point["sequence"] for point in performance["equity_curve"]]
  assert sequences == [1, 3, 4, 6, 8, 9]
  assert performance["summary"]["max_drawdown_pct"] == 9.5238095
  assert performance["data_quality"]["raw_sample_count"] == 9
  assert performance["data_quality"]["compressed_sample_count"] == 6
  assert performance["data_quality"]["sample_count"] == 6
  assert (
    performance["data_quality"]["compression_policy"]
    == "minute_close_execution_extrema_v1"
  )


def test_indexed_snapshot_reads_curves_from_jsonl(tmp_path):
  snapshot_dir = tmp_path / "perf"
  snapshot_dir.mkdir()
  manifest_path = snapshot_dir / "manifest.json"
  equity_path = snapshot_dir / "equity_curve.jsonl"
  drawdown_path = snapshot_dir / "drawdown_curve.jsonl"
  manifest = {
    "run_id": "run-1",
    "backtest_id": "bt-1",
    "mode": "backtest",
    "benchmark_code": None,
    "source": "backtest_snapshot",
    "generated_at": "2026-04-01T09:30:00",
    "summary_only": False,
    "summary": {"total_return_pct": 99.0},
    "risk": {},
    "trade_stats": {},
    "execution_quality": {},
    "monthly_returns": [],
    "data_quality": {
      "status": "OK",
      "sample_count": 100,
      "raw_sample_count": 120,
      "compressed_sample_count": 100,
      "compression_policy": "minute_close_execution_extrema_v1",
    },
    "storage_format": "strategy_performance_snapshot_v2",
    "artifacts": {
      "equity_curve": {"path": "equity_curve.jsonl", "count": 100},
      "drawdown_curve": {"path": "drawdown_curve.jsonl", "count": 100},
    },
  }
  with open(manifest_path, "w", encoding="utf-8") as fp:
    json.dump(manifest, fp)
  with open(equity_path, "w", encoding="utf-8") as fp:
    for idx in range(1, 101):
      fp.write(json.dumps({"sequence": idx, "value": float(idx)}))
      fp.write("\n")
  with open(drawdown_path, "w", encoding="utf-8") as fp:
    for idx in range(1, 101):
      fp.write(json.dumps({"sequence": idx, "value": -float(idx)}))
      fp.write("\n")

  sampled = StrategyPerformanceService._performance_from_indexed_snapshot(
    str(manifest_path),
    manifest,
    benchmark_code=None,
    cursor=None,
    limit=10,
  )
  cursor_page = StrategyPerformanceService._performance_from_indexed_snapshot(
    str(manifest_path),
    manifest,
    benchmark_code=None,
    cursor=95,
    limit=3,
  )

  assert len(sampled["equity_curve"]) == 10
  assert sampled["equity_curve"][0]["sequence"] == 1
  assert sampled["equity_curve"][-1]["sequence"] == 100
  assert sampled["drawdown_curve"][-1]["sequence"] == 100
  assert sampled["page_info"]["has_more"] is False
  assert sampled["data_quality"]["returned_sample_count"] == 10
  assert sampled["data_quality"]["truncated"] is True

  assert [point["sequence"] for point in cursor_page["equity_curve"]] == [
    96,
    97,
    98,
  ]
  assert cursor_page["page_info"]["has_more"] is True
  assert cursor_page["page_info"]["next_cursor"] == "98"


def test_indexed_snapshot_writer_keeps_manifest_small(tmp_path):
  manifest_path = tmp_path / "manifest.json"
  StrategyPerformanceService._write_indexed_snapshot(
    str(manifest_path),
    {
      "run_id": "run-1",
      "backtest_id": "bt-1",
      "mode": "backtest",
      "source": "backtest_snapshot",
      "summary": {"total_return_pct": 1.0},
      "data_quality": {"sample_count": 2},
      "equity_curve": [
        {"sequence": 1, "value": 0.0},
        {"sequence": 2, "value": 1.0},
      ],
      "drawdown_curve": [
        {"sequence": 1, "value": 0.0},
        {"sequence": 2, "value": 0.5},
      ],
    },
  )

  with open(manifest_path, "r", encoding="utf-8") as fp:
    manifest = json.load(fp)
  with open(tmp_path / "equity_curve.jsonl", "r", encoding="utf-8") as fp:
    equity_rows = [json.loads(line) for line in fp if line.strip()]

  assert manifest["storage_format"] == "strategy_performance_snapshot_v2"
  assert "equity_curve" not in manifest
  assert manifest["artifacts"]["equity_curve"]["count"] == 2
  assert equity_rows[-1]["sequence"] == 2


def test_legacy_snapshot_is_indexed_on_first_backtest_read(tmp_path, monkeypatch):
  monkeypatch.chdir(tmp_path)
  legacy_path = tmp_path / "legacy-performance.json"
  legacy = {
    "run_id": "run-1",
    "backtest_id": "bt-legacy",
    "mode": "backtest",
    "source": "backtest_snapshot",
    "summary": {"total_return_pct": 10.0},
    "data_quality": {"sample_count": 20},
    "equity_curve": [
      {"sequence": idx, "value": float(idx)}
      for idx in range(1, 21)
    ],
    "drawdown_curve": [
      {"sequence": idx, "value": -float(idx)}
      for idx in range(1, 21)
    ],
  }
  with open(legacy_path, "w", encoding="utf-8") as fp:
    json.dump(legacy, fp)

  backtest = SimpleNamespace(
    id="bt-legacy",
    strategy_run_id="run-1",
    metrics={"performance_snapshot_path": str(legacy_path)},
  )
  page = StrategyPerformanceService._performance_from_backtest(
    backtest,
    benchmark_code=None,
    cursor=None,
    limit=5,
  )
  indexed_path = tmp_path / "data/backtests/performance/bt-legacy/manifest.json"
  second_page = StrategyPerformanceService._performance_from_backtest(
    backtest,
    benchmark_code=None,
    cursor=18,
    limit=1,
  )

  assert indexed_path.exists()
  assert len(page["equity_curve"]) == 5
  assert page["equity_curve"][-1]["sequence"] == 20
  assert [point["sequence"] for point in second_page["equity_curve"]] == [19]
  assert second_page["page_info"]["has_more"] is True
