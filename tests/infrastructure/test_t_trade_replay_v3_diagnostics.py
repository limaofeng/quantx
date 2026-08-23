from __future__ import annotations

import json
from datetime import datetime

from quantx_infrastructure.core.t_trade_replay_metrics import (
  attach_t_trade_opportunity_diagnostics,
  attach_t_trade_phase_one_baseline,
)
from quantx_infrastructure.core.t_trade_replay_report import (
  write_t_trade_replay_report,
)


def _replay_metrics() -> dict:
  return {
    "data_quality": "OK",
    "data_quality_message": "历史回放与期末清算完整",
    "summary": {
      "liquidation_failed_cycles": 0,
      "completed_cycles": 1,
      "t_net_profit": 12.0,
      "excess_return_pct": 0.1,
      "win_rate_pct": 100.0,
      "capital_utilization_pct": 50.0,
      "average_holding_hours": 1.0,
      "max_holding_hours": 1.0,
      "capital_turnover_times": 0.2,
      "forced_exit_cycles": 0,
      "total_fees": 10.0,
    },
    "methodology": {
      "forced_liquidation": "strict",
      "capital_utilization": "capital weighted",
      "price_limits": "native first",
    },
    "cycles": [],
  }


def _diagnostics(*, run_id: str = "run-v3") -> dict:
  return {
    "available": True,
    "merged_versions": False,
    "warnings": [],
    "scope": {
      "strategy_run_id": run_id,
      "stock_code": None,
      "start_time": "2026-08-01T09:30:00",
      "end_time": "2026-08-23T15:00:00",
    },
    "partitions": [
      {
        "policy_version": "t_trade_opportunity_v3.0.0",
        "feature_schema_version": "3",
        "profile_version": "profile-v1",
        "denominator": {
          "code": "READY_INSTRUMENT_SECONDS",
          "label": "READY 标的时长（秒）",
          "ready_instrument_seconds": 7200.0,
        },
        "funnel": [
          {
            "code": "ELIGIBLE",
            "label": "合格持仓评估",
            "unit_code": "MATERIAL_EVENTS",
            "denominator_code": None,
            "count": 4,
            "conversion_rate": None,
          },
          {
            "code": "CANDIDATE",
            "label": "候选信号",
            "unit_code": "RUN_SCOPED_CANDIDATES",
            "denominator_code": "ELIGIBLE",
            "count": 1,
            "conversion_rate": 0.25,
          },
        ],
        "blockers": [
          {
            "blocker": {
              "code": "QUOTE_STALE",
              "label": "行情陈旧",
              "detail": "等待新报价",
            },
            "count": 2,
            "rate": 0.5,
            "denominator_code": "MATERIAL_EVENTS",
            "denominator_value": 4.0,
          }
        ],
        "score_distribution": [],
        "fsm_dwell": [],
        "fsm_transitions": [
          {
            "branch": "PULLBACK",
            "from_phase": "OBSERVING",
            "to_phase": "PULLBACK_FORMING",
            "count": 1,
          }
        ],
        "candidate_outcomes": [{"code": "FILLED", "label": "已成交", "count": 1}],
        "post_candidate_performance": {
          "available": False,
          "reason_code": "POST_FILL_CAUSAL_PATH_AND_COST_LEDGER_UNAVAILABLE",
          "reason": "没有权威费用账本与完整因果价格路径。",
          "sample_count": 0,
          "net_mfe_pct": None,
          "net_mae_pct": None,
          "fixed_window_returns": [],
          "required_data_codes": [
            "AUTHORITATIVE_EXECUTION_FEE_LEDGER",
            "COMPLETE_POST_FILL_CAUSAL_MARKET_PATH",
          ],
        },
      }
    ],
    "version_groups": [
      {
        "policy_version": "t_trade_opportunity_v3.0.0",
        "feature_schema_version": "3",
        "profile_version": "profile-v1",
        "count": 8,
      }
    ],
  }


def test_attach_diagnostics_requires_exact_run_scope_and_ready_time() -> None:
  attached = attach_t_trade_opportunity_diagnostics(
    _replay_metrics(),
    _diagnostics(),
    expected_strategy_run_id="run-v3",
  )

  diagnostics = attached["opportunity_diagnostics"]
  assert diagnostics["available"] is True
  assert diagnostics["scope"]["strategy_run_id"] == "run-v3"
  assert diagnostics["partitions"][0]["denominator"] == {
    "code": "READY_INSTRUMENT_SECONDS",
    "label": "READY 标的时长（秒）",
    "ready_instrument_seconds": 7200.0,
  }
  assert (
    diagnostics["partitions"][0]["post_candidate_performance"]["net_mfe_pct"] is None
  )
  assert attached["summary"]["t_net_profit"] == 12.0

  mismatch = attach_t_trade_opportunity_diagnostics(
    _replay_metrics(),
    _diagnostics(run_id="another-run"),
    expected_strategy_run_id="run-v3",
  )["opportunity_diagnostics"]
  assert mismatch["available"] is False
  assert mismatch["reason_code"] == "STRATEGY_RUN_SCOPE_MISMATCH"
  assert mismatch["partitions"] == []


def test_attach_diagnostics_rejects_tick_count_denominator() -> None:
  diagnostics = _diagnostics()
  diagnostics["partitions"][0]["denominator"] = {
    "code": "RAW_TICK_COUNT",
    "label": "Tick 数",
    "ready_instrument_seconds": 999.0,
  }

  result = attach_t_trade_opportunity_diagnostics(
    _replay_metrics(),
    diagnostics,
    expected_strategy_run_id="run-v3",
  )["opportunity_diagnostics"]

  assert result["available"] is False
  assert result["reason_code"] == "UNSUPPORTED_DENOMINATOR"
  assert result["partitions"] == []


def test_attach_phase_one_baseline_compares_units_without_fake_fee_result() -> None:
  metrics = attach_t_trade_opportunity_diagnostics(
    _replay_metrics(),
    _diagnostics(),
    expected_strategy_run_id="run-v3",
  )
  attached = attach_t_trade_phase_one_baseline(
    metrics,
    {
      "schema_version": 1,
      "available": True,
      "baseline_version": "phase-one-and-v1",
      "denominator": {
        "code": "BASELINE_DATA_READY_INSTRUMENT_SECONDS",
        "value": 3600.0,
      },
      "candidate_edges": {"PULLBACK_REBOUND": 2, "MOMENTUM_ACCELERATION": 1},
      "candidate_reference_performance": {"candidate_count": 3},
      "common_ready_comparison": {
        "available": True,
        "denominator": {
          "code": "COMMON_READY_INSTRUMENT_SECONDS",
          "value": 1800.0,
        },
        "v3_candidate_edges": {"PULLBACK_REBOUND": 1},
        "phase_one_candidate_edges": {"PULLBACK_REBOUND": 2},
      },
      "fee_adjusted_performance": {
        "available": False,
        "reason_code": "SHADOW_BASELINE_NOT_EXECUTED",
      },
    },
  )

  comparison = attached["v3_vs_phase_one"]
  assert comparison["available"] is True
  assert comparison["v3"]["ready_instrument_seconds"] == 7200.0
  assert comparison["v3"]["candidate_count"] == 1
  assert comparison["v3"]["candidate_rate_per_ready_instrument_hour"] == 0.5
  assert comparison["phase_one"]["data_ready_instrument_seconds"] == 3600.0
  assert comparison["phase_one"]["candidate_count"] == 3
  assert comparison["phase_one"]["candidate_rate_per_ready_instrument_hour"] == 3.0
  assert "candidate_count_delta" not in comparison
  assert comparison["common_ready"] == {
    "available": True,
    "ready_instrument_seconds": 1800.0,
    "v3_candidate_count": 1,
    "phase_one_candidate_count": 2,
    "v3_candidate_rate_per_ready_instrument_hour": 2.0,
    "phase_one_candidate_rate_per_ready_instrument_hour": 4.0,
    "candidate_rate_delta_per_ready_instrument_hour": -2.0,
    "warning": None,
  }
  assert comparison["fee_adjusted_comparison_available"] is False
  assert comparison["warning"] is not None


def test_attach_phase_one_baseline_marks_missing_collection_unavailable() -> None:
  attached = attach_t_trade_phase_one_baseline(_replay_metrics(), None)
  assert attached["phase_one_baseline"]["available"] is False
  assert (
    attached["phase_one_baseline"]["reason_code"] == "PHASE_ONE_BASELINE_NOT_COLLECTED"
  )
  assert attached["v3_vs_phase_one"]["available"] is False
  assert "candidate_count_delta" not in attached["v3_vs_phase_one"]
  assert attached["v3_vs_phase_one"]["common_ready"]["available"] is False


def test_report_discloses_v3_funnel_versions_and_missing_excursions(tmp_path) -> None:
  manifest = tmp_path / "manifest.json"
  manifest.write_text('{"schema_version": 3, "artifacts": {}}', encoding="utf-8")
  metrics = attach_t_trade_opportunity_diagnostics(
    _replay_metrics(),
    _diagnostics(),
    expected_strategy_run_id="run-v3",
  )
  metrics = attach_t_trade_phase_one_baseline(
    metrics,
    {
      "schema_version": 1,
      "available": True,
      "baseline_version": "phase-one-and-v1",
      "denominator": {
        "code": "BASELINE_DATA_READY_INSTRUMENT_SECONDS",
        "value": 3600.0,
      },
      "candidate_edges": {"PULLBACK_REBOUND": 2},
      "candidate_reference_performance": {
        "candidate_count": 2,
        "fixed_windows": [
          {
            "horizon_seconds": 60,
            "sample_count": 2,
            "average_return_pct": 0.2,
            "average_mfe_pct": 0.4,
            "average_mae_pct": -0.1,
          }
        ],
      },
      "fee_adjusted_performance": {"available": False},
    },
  )

  result = write_t_trade_replay_report(
    str(manifest),
    metrics,
    run_id="run-v3",
    backtest_id="backtest-v3",
    start_time=datetime(2026, 8, 1, 9, 30),
    end_time=datetime(2026, 8, 23, 15, 0),
  )

  assert result["schema_version"] == 2
  assert result["conclusion_code"] == "INSUFFICIENT_SAMPLE"
  report_json = json.loads((tmp_path / "t-trade-report.json").read_text("utf-8"))
  diagnostics = report_json["replay"]["opportunity_diagnostics"]
  assert diagnostics["scope"]["strategy_run_id"] == "run-v3"
  assert (
    diagnostics["partitions"][0]["post_candidate_performance"]["net_mfe_pct"] is None
  )
  report_html = (tmp_path / "t-trade-report.html").read_text("utf-8")
  assert "READY_INSTRUMENT_SECONDS" in report_html
  assert "不使用原始 Tick 数" in report_html
  assert "QUOTE_STALE" in report_html
  assert "t_trade_opportunity_v3.0.0" in report_html
  assert "POST_FILL_CAUSAL_PATH_AND_COST_LEDGER_UNAVAILABLE" in report_html
  assert "一期固定规则对照" in report_html
  assert "phase-one-and-v1" in report_html
  assert "任一侧缺少权威费用后结果" in report_html


def test_report_marks_unattached_diagnostics_unavailable_without_fake_zero(
  tmp_path,
) -> None:
  manifest = tmp_path / "manifest.json"
  manifest.write_text('{"schema_version": 3, "artifacts": {}}', encoding="utf-8")
  metrics = attach_t_trade_opportunity_diagnostics(
    _replay_metrics(),
    None,
    expected_strategy_run_id="run-v3",
  )

  result = write_t_trade_replay_report(
    str(manifest),
    metrics,
    run_id="run-v3",
    backtest_id="backtest-v3",
    start_time=None,
    end_time=None,
  )

  assert result["conclusion_code"] == "DIAGNOSTICS_UNAVAILABLE"
  diagnostics = json.loads((tmp_path / "t-trade-report.json").read_text("utf-8"))[
    "replay"
  ]["opportunity_diagnostics"]
  assert diagnostics["partitions"] == []
  report_html = (tmp_path / "t-trade-report.html").read_text("utf-8")
  assert "报告不会以 0 或原始 Tick 数代替缺失样本" in report_html
