from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from quantx_research.core.config import StudyConfig
from quantx_research.core.models import (
  ComparisonStatistic,
  EventCurvePoint,
  GroupStatistic,
  RegressionCoefficient,
  RegressionResult,
  StudyResult,
)
from quantx_research.reporting import render_report
from quantx_research.reporting.renderer import (
  _build_findings,
  _significant_interaction,
)


@dataclass(frozen=True)
class QualityInput:
  included: int
  excluded: int
  exclusion_reasons: dict[str, int]


@dataclass(frozen=True)
class ManifestInput:
  status: str
  run_id: str
  completed_at: datetime
  git_commit: str
  config_hash: str


def test_render_report_from_canonical_models_writes_complete_artifacts(
  tmp_path: Path,
) -> None:
  base_result = _study_result()
  result = base_result.model_copy(
    update={"comparison_sensitivity": {"cooldown_5d": base_result.comparison}}
  )
  config = StudyConfig(date_range=(date(2021, 1, 1), date(2025, 12, 31)))
  quality = QualityInput(
    included=360,
    excluded=40,
    exclusion_reasons={"insufficient_history": 40},
  )
  manifest = ManifestInput(
    status="completed",
    run_id="volume-shock-v1-test",
    completed_at=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
    git_commit="abc123",
    config_hash="f" * 64,
  )

  report = render_report(tmp_path / "run", result, quality, manifest, config)

  assert report == tmp_path / "run" / "report.html"
  assert report.is_file()
  html = report.read_text(encoding="utf-8")
  assert "<h1>异常放量与价格位置事件研究</h1>" in html
  assert "有效事件" in html
  assert "沪深300超额" in html
  assert "放量滞涨" in html
  assert "阈值前样本" in html
  assert "差值中位数" in html
  assert "冷却敏感性：cooldown_5d" in html
  assert "配置冷却后事件" in html
  assert "不构成因果" in html
  assert "历史 ST 有效期不可得" in html
  assert html.count("<svg") >= 3
  assert "暂无可视化数据" not in (
    tmp_path / "run" / "figures" / "event_curve.svg"
  ).read_text(encoding="utf-8")
  assert "暂无可视化数据" not in (
    tmp_path / "run" / "figures" / "interaction_heatmap.svg"
  ).read_text(encoding="utf-8")
  assert {path.name for path in (tmp_path / "run" / "figures").glob("*.svg")} == {
    "event_curve.svg",
    "interaction_heatmap.svg",
    "regression_coefficients.svg",
  }


def test_render_report_has_explicit_empty_states(tmp_path: Path) -> None:
  report = render_report(
    tmp_path / "empty",
    {
      "study_id": "volume-shock",
      "version": "v1",
      "event_count": 0,
      "grouped_statistics": [],
      "event_curve": [],
      "regressions": [],
      "robustness": {},
      "warnings": [],
    },
    {},
    {"status": "failed_preflight"},
    {},
  )

  html = report.read_text(encoding="utf-8")
  assert "未提供 event_curve 数据" in html
  assert "未提供 grouped_statistics 数据" in html
  assert "未提供 regressions 数据或回归未能估计" in html
  assert "未提供 data_quality 数据" in html
  for figure in (report.parent / "figures").glob("*.svg"):
    assert "暂无可视化数据" in figure.read_text(encoding="utf-8")


def test_report_escapes_input_and_embeds_all_visuals(tmp_path: Path) -> None:
  result = _study_result().model_copy(
    update={
      "warnings": ['<img src="https://example.invalid/tracker" onerror="alert(1)">']
    }
  )
  report = render_report(
    tmp_path / "safe",
    result,
    QualityInput(360, 40, {}),
    ManifestInput(
      "completed",
      "safe",
      datetime(2026, 7, 29, tzinfo=timezone.utc),
      "abc123",
      "hash",
    ),
    StudyConfig(),
    title="<script>alert('x')</script>",
  )

  html = report.read_text(encoding="utf-8")
  assert "<script" not in html.lower()
  assert "<img" not in html.lower()
  assert "<link" not in html.lower()
  assert 'src="figures/' not in html
  assert "&lt;script&gt;alert" in html
  assert "&lt;img src=&#34;https://example.invalid/tracker&#34;" in html
  assert html.count("<svg") >= 3


def test_report_and_svg_output_is_deterministic(tmp_path: Path) -> None:
  inputs = (
    _study_result(),
    QualityInput(360, 40, {"missing_benchmark": 40}),
    ManifestInput(
      "completed",
      "deterministic",
      datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
      "abc123",
      "same-config",
    ),
    StudyConfig(),
  )

  first = render_report(tmp_path / "first", *inputs)
  second = render_report(tmp_path / "second", *inputs)

  assert first.read_bytes() == second.read_bytes()
  first_figures = {
    path.name: path.read_bytes() for path in (first.parent / "figures").glob("*.svg")
  }
  second_figures = {
    path.name: path.read_bytes() for path in (second.parent / "figures").glob("*.svg")
  }
  assert first_figures == second_figures


def test_report_summarizes_dynamic_universe_and_surfaces_quality_warnings(
  tmp_path: Path,
) -> None:
  report = render_report(
    tmp_path / "dynamic",
    _study_result(),
    {
      "requested_end": "2026-07-29T00:00:00",
      "warnings": ["13 个标的历史样本不足"],
    },
    {"status": "success"},
    {
      "universe": {
        "lookback_years": 2,
        "end_date": "latest",
        "benchmark_code": "000300.SH",
        "stock_codes": ["000333.SZ", "600036.SH"],
      }
    },
  )

  html = report.read_text(encoding="utf-8")
  assert "2024-07-29 至 2026-07-29" in html
  assert "2 只固定样本 · 基准 000300.SH" in html
  assert "13 个标的历史样本不足" in html


def test_report_findings_select_preregistered_comparison_deterministically() -> None:
  rows = [
    _comparison_row(
      benchmark="absolute",
      return_kind="next_open",
      horizon=1,
      spread_mean=0.09,
    ),
    _comparison_row(
      benchmark="csi300",
      return_kind="next_open",
      horizon=5,
      spread_mean=0.03,
    ),
    _comparison_row(
      benchmark="csi300",
      return_kind="close_response",
      horizon=5,
      spread_mean=0.01,
    ),
    _comparison_row(
      benchmark="csi300",
      return_kind="close_response",
      horizon=20,
      spread_mean=-0.02,
      ci_low=-0.03,
      ci_high=-0.01,
    ),
  ]

  first = _build_findings(
    {},
    [],
    rows,
    [],
    minimum_cell_samples=30,
    minimum_inference_dates=30,
    fdr_alpha=0.05,
    configured_horizons=(20, 5, 1),
  )
  second = _build_findings(
    {},
    [],
    list(reversed(rows)),
    [],
    minimum_cell_samples=30,
    minimum_inference_dates=30,
    fdr_alpha=0.05,
    configured_horizons=(20, 5, 1),
  )

  assert first == second
  assert first[0].startswith("沪深300超额、事件日收盘响应、T+20 口径下")
  assert "收益差为 -2.00%" in first[0]
  assert "FDR q=0.04" in first[0]


def test_report_findings_require_every_preregistered_confirmation_gate() -> None:
  invalid_rows = [
    _comparison_row(q_value=0.051),
    _comparison_row(ci_low=-0.01, ci_high=0.01),
    _comparison_row(unique_dates=29),
    _comparison_row(shock_sample_size=29),
    _comparison_row(normal_sample_size=29),
    _comparison_row(significant=False),
  ]

  findings = _build_findings(
    {},
    [],
    invalid_rows,
    [],
    minimum_cell_samples=30,
    minimum_inference_dates=30,
    fdr_alpha=0.05,
    configured_horizons=(1, 3, 5, 10, 20),
  )

  assert findings[0] == (
    "预注册的价格位置主对照中，没有 high-minus-low 结果同时满足 "
    "FDR q≤0.05、置信区间不跨 0、至少 30 个有效交易日，且异常量与正常量样本"
    "各不少于 30；因此不形成确认结论。"
  )
  assert "收益差为" not in findings[0]


def test_render_report_uses_configured_confirmation_thresholds(tmp_path: Path) -> None:
  report = render_report(
    tmp_path / "configured-thresholds",
    {
      "study_id": "volume-shock",
      "version": "v1",
      "comparison": [_comparison_row()],
    },
    {},
    {"status": "success"},
    {
      "statistics": {
        "minimum_cell_samples": 50,
        "minimum_inference_dates": 60,
        "fdr_alpha": 0.01,
      },
      "outcomes": {"horizons": [5]},
    },
  )

  html = report.read_text(encoding="utf-8")
  assert "FDR q≤0.01" in html
  assert "至少 60 个有效交易日" in html
  assert "各不少于 50；因此不形成确认结论" in html


def test_report_regression_finding_requires_q_and_nonzero_interval() -> None:
  crossing_zero = _regression_row(
    dependent_variable="csi300_excess_close_h5",
    return_kind="close_response",
    horizon=5,
    ci_low=-0.01,
    ci_high=0.01,
  )
  absolute = _regression_row(
    dependent_variable="close_return_h20",
    return_kind="close_response",
    horizon=20,
    estimate=0.03,
  )
  csi300 = _regression_row(
    dependent_variable="csi300_excess_close_h20",
    return_kind="close_response",
    horizon=20,
    estimate=-0.02,
    ci_low=-0.03,
    ci_high=-0.01,
  )

  assert (
    _significant_interaction(
      [crossing_zero],
      fdr_alpha=0.05,
      configured_horizons=(5, 20),
    )
    is None
  )
  finding = _significant_interaction(
    [absolute, csi300],
    fdr_alpha=0.05,
    configured_horizons=(20,),
  )
  assert finding is not None
  assert "呈负向关联" in finding
  assert "事件日收盘响应、T+20" in finding


def test_visuals_choose_the_best_covered_available_results(tmp_path: Path) -> None:
  grouped = [
    {
      "dimensions": {
        "rvol_bin": "[1.5,2)",
        "price_position_bin": "low",
        "event_direction": "up",
      },
      "return_kind": "close_response",
      "horizon": 20,
      "benchmark": benchmark,
      "sample_size": sample_size,
      "mean": mean,
    }
    for benchmark, sample_size, mean in (
      ("csi300", 2, -0.01),
      ("market_equal_weight", 40, 0.01),
    )
  ]
  regressions = [
    {
      "return_kind": "close_response",
      "horizon": 20,
      "dependent_variable": "csi300_excess_close_h20",
      "coefficients": [],
    },
    {
      "return_kind": "close_response",
      "horizon": 10,
      "dependent_variable": "market_excess_close_h10",
      "coefficients": [
        {
          "term": "volume_position_interaction",
          "estimate": 0.01,
          "ci_low": 0.002,
          "ci_high": 0.018,
        }
      ],
    },
  ]

  render_report(
    tmp_path / "coverage",
    {
      "grouped_statistics": grouped,
      "event_curve": [],
      "regressions": regressions,
    },
    {},
    {"status": "success"},
    {},
  )

  heatmap = (tmp_path / "coverage" / "figures" / "interaction_heatmap.svg").read_text(
    encoding="utf-8"
  )
  regression = (
    tmp_path / "coverage" / "figures" / "regression_coefficients.svg"
  ).read_text(encoding="utf-8")
  assert "全市场等权超额" in heatmap
  assert "T+10" in regression
  assert "暂无可视化数据" not in regression


def _comparison_row(
  *,
  benchmark: str = "csi300",
  return_kind: str = "close_response",
  horizon: int = 5,
  spread_mean: float = 0.02,
  ci_low: float = 0.01,
  ci_high: float = 0.03,
  q_value: float = 0.04,
  unique_dates: int = 40,
  shock_sample_size: int = 30,
  normal_sample_size: int = 30,
  significant: bool = True,
) -> dict[str, object]:
  return {
    "dimensions": {
      "comparison": "high_minus_low",
      "price_position_bin": "high_minus_low",
    },
    "benchmark": benchmark,
    "return_kind": return_kind,
    "horizon": horizon,
    "spread_mean": spread_mean,
    "ci_low": ci_low,
    "ci_high": ci_high,
    "q_value": q_value,
    "unique_dates": unique_dates,
    "shock_sample_size": shock_sample_size,
    "normal_sample_size": normal_sample_size,
    "significant": significant,
  }


def _regression_row(
  *,
  dependent_variable: str,
  return_kind: str,
  horizon: int,
  estimate: float = 0.02,
  ci_low: float = 0.01,
  ci_high: float = 0.03,
  q_value: float = 0.04,
  significant: bool = True,
) -> dict[str, object]:
  return {
    "dependent_variable": dependent_variable,
    "return_kind": return_kind,
    "horizon": horizon,
    "coefficients": [
      {
        "term": "shock_position_interaction",
        "estimate": estimate,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "q_value": q_value,
        "significant": significant,
      }
    ],
  }


def _study_result() -> StudyResult:
  grouped_statistics = []
  for rvol_index, rvol_bin in enumerate(("1.5–2.0", "2.0–3.0", "≥3.0")):
    for direction_index, direction in enumerate(("down", "flat", "up")):
      mean = (rvol_index - 1) * 0.002 + (direction_index - 1) * 0.004
      grouped_statistics.append(
        GroupStatistic(
          dimensions={
            "rvol_bin": rvol_bin,
            "price_position_bin": "低位 0–30%",
            "event_direction": direction,
          },
          return_kind="close_response",
          horizon=20,
          benchmark="csi300",
          sample_size=40,
          mean=mean,
          median=mean * 0.8,
          positive_rate=0.53,
          p05=-0.08,
          p25=-0.02,
          p75=0.03,
          p95=0.09,
          mae_mean=-0.04,
          mfe_mean=0.05,
          ci_low=mean - 0.003,
          ci_high=mean + 0.003,
          p_value=0.02,
          q_value=0.04,
          significant=True,
        )
      )

  event_curve = [
    EventCurvePoint(
      return_kind=return_kind,
      horizon=horizon,
      benchmark=benchmark,
      sample_size=360,
      mean=horizon * 0.0004,
      median=horizon * 0.0003,
      positive_rate=0.53,
      ci_low=horizon * 0.0004 - 0.002,
      ci_high=horizon * 0.0004 + 0.002,
    )
    for return_kind in ("close_response", "next_open")
    for benchmark in ("absolute", "csi300")
    for horizon in (1, 3, 5, 10, 20)
  ]
  regressions = [
    RegressionResult(
      return_kind="close_response",
      horizon=20,
      dependent_variable="csi300_excess",
      nobs=360,
      r_squared=0.12,
      coefficients=[
        RegressionCoefficient(
          term="shock_indicator",
          estimate=0.004,
          std_error=0.001,
          t_stat=4.0,
          p_value=0.0001,
          ci_low=0.002,
          ci_high=0.006,
        ),
        RegressionCoefficient(
          term="shock_position_interaction",
          estimate=-0.003,
          std_error=0.0012,
          t_stat=-2.5,
          p_value=0.012,
          ci_low=-0.0054,
          ci_high=-0.0006,
          q_value=0.024,
          significant=True,
        ),
      ],
    )
  ]
  return StudyResult(
    study_id="volume-shock",
    version="v1",
    event_count=360,
    analysis_sample_count=12_000,
    grouped_statistics=grouped_statistics,
    event_curve=event_curve,
    comparison=[
      ComparisonStatistic(
        dimensions={
          "comparison": "high_minus_low",
          "price_position_bin": "high_minus_low",
        },
        return_kind="close_response",
        horizon=20,
        benchmark="csi300",
        shock_sample_size=360,
        normal_sample_size=2_000,
        unique_dates=80,
        shock_mean=-0.002,
        shock_median=-0.001,
        normal_mean=0.003,
        normal_median=0.002,
        spread_mean=-0.005,
        spread_median=-0.004,
        ci_low=-0.008,
        ci_high=-0.002,
        p_value=0.004,
        q_value=0.012,
        significant=True,
      )
    ],
    regressions=regressions,
    robustness={"amount_ratio20": grouped_statistics[:3]},
    warnings=["历史 ST 有效期不可得。"],
  )
