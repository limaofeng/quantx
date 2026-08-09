"""Render structured research results into a self-contained Chinese report."""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .charts import (
  build_event_curve_svg,
  build_interaction_heatmap_svg,
  build_regression_coefficients_svg,
)

_DEFAULT_TITLE = "异常放量与价格位置事件研究"
_RETURN_KIND_LABELS = {
  "close_response": "事件日收盘响应",
  "next_open": "次日开盘入场",
}
_BENCHMARK_LABELS = {
  "absolute": "绝对收益",
  "csi300": "沪深300超额",
  "market_equal_weight": "全市场等权超额",
}
_DIMENSION_LABELS = {
  "rvol_bin": "RVOL20",
  "relative_volume_bin": "RVOL20",
  "volume_bin": "相对成交量",
  "price_position_bin": "事前价格位置",
  "price_position": "事前价格位置",
  "event_direction": "事件方向",
  "direction": "事件方向",
  "event_type": "事件类型",
  "comparison": "对照估计",
}
_VALUE_LABELS = {
  "study_id": "研究标识",
  "version": "版本",
  "event_count": "事件数",
  "analysis_sample_count": "阈值前合格样本数",
  "status": "状态",
  "started_at": "开始时间",
  "completed_at": "完成时间",
  "generated_at": "生成时间",
  "git_commit": "Git 提交",
  "git_dirty": "工作区有未提交修改",
  "config_hash": "配置哈希",
  "data_fingerprint": "数据指纹",
  "date_range": "研究区间",
  "start_date": "开始日期",
  "end_date": "结束日期",
  "universe": "股票池",
  "benchmark": "基准",
  "excluded": "排除数",
  "included": "纳入数",
  "missing": "缺失数",
  "warnings": "警告",
}


def render_report(
  run_dir: str | Path,
  metrics: Mapping[str, Any] | object,
  data_quality: Mapping[str, Any] | object,
  manifest: Mapping[str, Any] | object,
  config: Mapping[str, Any] | object,
  *,
  title: str | None = None,
) -> Path:
  """Write figures and a self-contained ``report.html`` into ``run_dir``.

  Inputs may be JSON-native mappings, dataclasses, or Pydantic models.  Pydantic
  inputs are converted with ``model_dump(mode="json", by_alias=True)``.  Missing
  optional result sections are rendered as explicit empty states instead of
  making the report fail.

  Returns:
    The path to the generated ``report.html``.
  """

  output_dir = Path(run_dir)
  figures_dir = output_dir / "figures"
  figures_dir.mkdir(parents=True, exist_ok=True)

  metrics_data = _as_mapping(metrics)
  quality_data = _as_mapping(data_quality)
  manifest_data = _as_mapping(manifest)
  # manifest.artifacts contains report.html's checksum. Embedding that checksum
  # back into report.html would create a self-referential, unstable artifact.
  manifest_data.pop("artifacts", None)
  config_data = _as_mapping(config)

  grouped_rows = _mapping_rows(
    metrics_data, "grouped_statistics", "group_statistics", "groups"
  )
  event_curve_rows = _mapping_rows(metrics_data, "event_curve", "event_curves")
  comparison_rows = _mapping_rows(
    metrics_data,
    "comparison",
    "comparison_statistics",
  )
  comparison_sensitivity_data = metrics_data.get(
    "comparison_sensitivity",
    {},
  )
  regression_rows = _mapping_rows(
    metrics_data, "regressions", "regression", "regression_results"
  )
  robustness_data = metrics_data.get("robustness", {})
  statistics_config = config_data.get("statistics")
  statistics_mapping = (
    statistics_config if isinstance(statistics_config, Mapping) else {}
  )
  minimum_cell_samples = _integer(statistics_mapping.get("minimum_cell_samples")) or 30
  minimum_inference_dates = (
    _integer(statistics_mapping.get("minimum_inference_dates")) or 30
  )
  configured_fdr_alpha = _number(statistics_mapping.get("fdr_alpha"))
  fdr_alpha = (
    configured_fdr_alpha
    if configured_fdr_alpha is not None and 0 < configured_fdr_alpha < 1
    else 0.05
  )
  outcomes_config = config_data.get("outcomes")
  outcomes_mapping = outcomes_config if isinstance(outcomes_config, Mapping) else {}
  configured_horizons = _configured_horizons(outcomes_mapping.get("horizons"))
  confidence_level = _number(statistics_mapping.get("confidence_level")) or 0.95
  confidence_label = f"{confidence_level * 100:g}% CI"

  event_curve_svg = build_event_curve_svg(
    event_curve_rows,
    confidence_label=confidence_label,
  )
  heatmap_svg = build_interaction_heatmap_svg(grouped_rows)
  regression_svg = build_regression_coefficients_svg(
    regression_rows,
    confidence_label=confidence_label,
  )
  _write_text_atomic(figures_dir / "event_curve.svg", event_curve_svg, encoding="utf-8")
  _write_text_atomic(
    figures_dir / "interaction_heatmap.svg",
    heatmap_svg,
    encoding="utf-8",
  )
  _write_text_atomic(
    figures_dir / "regression_coefficients.svg",
    regression_svg,
    encoding="utf-8",
  )

  report_title = title or str(
    _first_nonempty(
      config_data.get("title"),
      metrics_data.get("title"),
      _DEFAULT_TITLE,
    )
  )
  study_id = str(metrics_data.get("study_id") or "volume-shock")
  version = str(metrics_data.get("version") or "v1")
  warnings = _collect_warnings(metrics_data, quality_data, regression_rows)

  context = {
    "title": report_title,
    "study_id": study_id,
    "version": version,
    "generated_at": _first_nonempty(
      manifest_data.get("completed_at"),
      manifest_data.get("generated_at"),
      manifest_data.get("started_at"),
      "未记录",
    ),
    "run_id": _first_nonempty(
      manifest_data.get("run_id"),
      manifest_data.get("id"),
      manifest_data.get("config_hash"),
      "未记录",
    ),
    "status": _status_view(manifest_data.get("status")),
    "summary_cards": _build_summary_cards(
      metrics_data, quality_data, manifest_data, config_data, grouped_rows
    ),
    "findings": _build_findings(
      metrics_data,
      grouped_rows,
      comparison_rows,
      regression_rows,
      minimum_cell_samples=minimum_cell_samples,
      minimum_inference_dates=minimum_inference_dates,
      fdr_alpha=fdr_alpha,
      configured_horizons=configured_horizons,
    ),
    "warnings": warnings,
    "methodology": _build_methodology(config_data),
    "event_curve_svg": event_curve_svg,
    "heatmap_svg": heatmap_svg,
    "regression_svg": regression_svg,
    "event_curve_table": _event_curve_table(
      event_curve_rows,
      confidence_label=confidence_label,
    ),
    "grouped_table": _grouped_statistics_table(
      grouped_rows,
      minimum_cell_samples=minimum_cell_samples,
      confidence_label=confidence_label,
    ),
    "comparison_table": _comparison_table(
      comparison_rows,
      confidence_label=confidence_label,
    ),
    "comparison_sensitivity_tables": _comparison_sensitivity_tables(
      comparison_sensitivity_data,
      confidence_label=confidence_label,
    ),
    "regression_table": _regression_table(
      regression_rows,
      confidence_label=confidence_label,
    ),
    "robustness_tables": _robustness_tables(
      robustness_data,
      minimum_cell_samples=minimum_cell_samples,
      confidence_label=confidence_label,
    ),
    "confidence_label": confidence_label,
    "quality_table": _key_value_table(
      "数据质量与样本排除", quality_data, "未提供 data_quality 数据。"
    ),
    "manifest_table": _key_value_table(
      "运行清单", manifest_data, "未提供 manifest 数据。"
    ),
    "config_table": _key_value_table(
      "解析后的研究配置", config_data, "未提供 config 数据。"
    ),
    "artifacts": (
      "figures/event_curve.svg",
      "figures/interaction_heatmap.svg",
      "figures/regression_coefficients.svg",
      "report.html",
    ),
  }

  html = _render_template(context)
  report_path = output_dir / "report.html"
  _write_text_atomic(report_path, html, encoding="utf-8")
  return report_path


def _render_template(context: Mapping[str, Any]) -> str:
  try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
  except ModuleNotFoundError as exc:  # pragma: no cover - packaging failure
    raise RuntimeError(
      "生成研究报告需要 Jinja2；请安装 quantx-research 声明的依赖。"
    ) from exc

  templates_dir = Path(__file__).with_name("templates")
  environment = Environment(
    loader=FileSystemLoader(str(templates_dir)),
    autoescape=select_autoescape(enabled_extensions=("html", "j2", "xml")),
    trim_blocks=True,
    lstrip_blocks=True,
  )
  template = environment.get_template("report.html.j2")
  return template.render(**context)


def _as_mapping(value: Mapping[str, Any] | object) -> dict[str, Any]:
  plain = _to_plain(value)
  if isinstance(plain, Mapping):
    return {str(key): item for key, item in plain.items()}
  return {"value": plain}


def _to_plain(value: Any) -> Any:
  if value is None or isinstance(value, (str, int, bool)):
    return value
  if isinstance(value, float):
    return value if math.isfinite(value) else None
  if isinstance(value, (date, datetime)):
    return value.isoformat()
  if isinstance(value, Path):
    return str(value)
  if isinstance(value, Enum):
    return _to_plain(value.value)
  model_dump = getattr(value, "model_dump", None)
  if callable(model_dump):
    try:
      return _to_plain(model_dump(mode="json", by_alias=True))
    except TypeError:
      return _to_plain(model_dump())
  if dataclasses.is_dataclass(value) and not isinstance(value, type):
    return _to_plain(dataclasses.asdict(value))
  if isinstance(value, Mapping):
    return {str(key): _to_plain(item) for key, item in value.items()}
  if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
    return [_to_plain(item) for item in value]
  scalar_item = getattr(value, "item", None)
  if callable(scalar_item):
    try:
      return _to_plain(scalar_item())
    except (TypeError, ValueError):
      pass
  return str(value)


def _mapping_rows(source: Mapping[str, Any], *keys: str) -> list[dict[str, Any]]:
  for key in keys:
    if key in source:
      return _coerce_rows(source[key])
  return []


def _coerce_rows(value: Any) -> list[dict[str, Any]]:
  if isinstance(value, Mapping):
    for container_key in ("rows", "records", "data", "items", "values"):
      nested = value.get(container_key)
      if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
        return _coerce_rows(nested)
    rows: list[dict[str, Any]] = []
    for group_name, nested in value.items():
      for row in _coerce_rows(nested):
        row.setdefault("series", str(group_name))
        rows.append(row)
    return rows
  if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
    return [
      {str(key): item for key, item in row.items()}
      for row in value
      if isinstance(row, Mapping)
    ]
  return []


def _build_summary_cards(
  metrics: Mapping[str, Any],
  quality: Mapping[str, Any],
  manifest: Mapping[str, Any],
  config: Mapping[str, Any],
  grouped_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
  event_count = _first_nonempty(
    metrics.get("event_count"),
    quality.get("event_count"),
    quality.get("included"),
    "—",
  )
  analysis_sample_count = _first_nonempty(
    metrics.get("analysis_sample_count"),
    manifest.get("analysis_sample_count"),
    "—",
  )
  date_range = _date_range(config, quality)
  status = _status_view(manifest.get("status"))
  return [
    {
      "label": "有效事件",
      "value": _format_integer(event_count),
      "detail": "冷却与数据质量过滤后",
    },
    {
      "label": "阈值前样本",
      "value": _format_integer(analysis_sample_count),
      "detail": "主对照与主回归使用的完整合格股票日",
    },
    {
      "label": "研究区间",
      "value": date_range,
      "detail": _universe_summary(config),
    },
    {
      "label": "统计分组",
      "value": f"{len(grouped_rows):,}",
      "detail": "收益口径、周期与交互条件",
    },
    {
      "label": "运行状态",
      "value": status["label"],
      "detail": str(
        _first_nonempty(
          manifest.get("completed_at"),
          manifest.get("generated_at"),
          "时间未记录",
        )
      ),
    },
  ]


def _build_findings(
  metrics: Mapping[str, Any],
  grouped_rows: Sequence[Mapping[str, Any]],
  comparison_rows: Sequence[Mapping[str, Any]],
  regressions: Sequence[Mapping[str, Any]],
  *,
  minimum_cell_samples: int,
  minimum_inference_dates: int,
  fdr_alpha: float,
  configured_horizons: tuple[int, ...],
) -> list[str]:
  summary = metrics.get("summary")
  if isinstance(summary, Mapping):
    for key in ("findings", "conclusions", "highlights"):
      value = summary.get(key)
      if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        findings = [str(item) for item in value if str(item).strip()]
        if findings:
          return findings
  elif isinstance(summary, Sequence) and not isinstance(summary, (str, bytes)):
    findings = [str(item) for item in summary if str(item).strip()]
    if findings:
      return findings

  confirmed_comparison = _select_confirmed_comparison(
    comparison_rows,
    minimum_cell_samples=minimum_cell_samples,
    minimum_inference_dates=minimum_inference_dates,
    fdr_alpha=fdr_alpha,
    configured_horizons=configured_horizons,
  )
  findings = [
    _comparison_finding(
      confirmed_comparison,
      minimum_cell_samples=minimum_cell_samples,
      minimum_inference_dates=minimum_inference_dates,
      fdr_alpha=fdr_alpha,
    )
  ]
  eligible = [
    row
    for row in grouped_rows
    if (_number(row.get("mean")) is not None)
    and (_integer(row.get("sample_size")) or 0) >= minimum_cell_samples
  ]
  if eligible:
    preferred = [
      row
      for row in eligible
      if row.get("return_kind") == "close_response"
      and row.get("benchmark") == "csi300"
      and _integer(row.get("horizon")) == 20
    ]
    comparison = preferred or eligible
    best = max(comparison, key=lambda row: _number(row.get("mean")) or 0)
    worst = min(comparison, key=lambda row: _number(row.get("mean")) or 0)
    findings.extend(
      [
        (
          f"{_format_dimensions(best.get('dimensions'))}的平均收益最高，"
          f"为 {_format_percent(best.get('mean'))}（n="
          f"{_format_integer(best.get('sample_size'))}）。"
        ),
        (
          f"{_format_dimensions(worst.get('dimensions'))}的平均收益最低，"
          f"为 {_format_percent(worst.get('mean'))}（n="
          f"{_format_integer(worst.get('sample_size'))}）。"
        ),
      ]
    )
  else:
    findings.append(
      "当前结构化结果不足以自动生成方向性分组摘要，请结合数据质量和分组明细解读。"
    )
  interaction = _significant_interaction(
    regressions,
    fdr_alpha=fdr_alpha,
    configured_horizons=configured_horizons,
  )
  if interaction:
    findings.append(interaction)
  return findings


def _configured_horizons(value: Any) -> tuple[int, ...]:
  if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
    return ()
  return tuple(
    dict.fromkeys(
      integer
      for item in value
      if (integer := _integer(item)) is not None and integer > 0
    )
  )


def _select_confirmed_comparison(
  rows: Sequence[Mapping[str, Any]],
  *,
  minimum_cell_samples: int,
  minimum_inference_dates: int,
  fdr_alpha: float,
  configured_horizons: tuple[int, ...],
) -> Mapping[str, Any] | None:
  confirmed = [
    row
    for row in rows
    if _is_confirmed_comparison(
      row,
      minimum_cell_samples=minimum_cell_samples,
      minimum_inference_dates=minimum_inference_dates,
      fdr_alpha=fdr_alpha,
    )
  ]
  if not confirmed:
    return None
  horizon_order = {horizon: index for index, horizon in enumerate(configured_horizons)}
  benchmark_order = {
    "csi300": 0,
    "market_equal_weight": 1,
    "absolute": 2,
  }
  return min(
    confirmed,
    key=lambda row: (
      benchmark_order.get(str(row.get("benchmark") or ""), 99),
      0 if row.get("return_kind") == "close_response" else 1,
      horizon_order.get(_integer(row.get("horizon")) or -1, 99),
      _integer(row.get("horizon")) or 2**31 - 1,
      json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
    ),
  )


def _is_confirmed_comparison(
  row: Mapping[str, Any],
  *,
  minimum_cell_samples: int,
  minimum_inference_dates: int,
  fdr_alpha: float,
) -> bool:
  dimensions = row.get("dimensions")
  if not isinstance(dimensions, Mapping):
    return False
  if (
    dimensions.get("comparison") != "high_minus_low"
    and dimensions.get("price_position_bin") != "high_minus_low"
  ):
    return False
  q_value = _number(row.get("q_value"))
  ci_low = _number(row.get("ci_low"))
  ci_high = _number(row.get("ci_high"))
  unique_dates = _integer(row.get("unique_dates"))
  shock_samples = _integer(row.get("shock_sample_size"))
  normal_samples = _integer(row.get("normal_sample_size"))
  return bool(
    row.get("significant") is True
    and q_value is not None
    and q_value <= fdr_alpha
    and ci_low is not None
    and ci_high is not None
    and ci_low <= ci_high
    and (ci_low > 0 or ci_high < 0)
    and unique_dates is not None
    and unique_dates >= minimum_inference_dates
    and shock_samples is not None
    and shock_samples >= minimum_cell_samples
    and normal_samples is not None
    and normal_samples >= minimum_cell_samples
  )


def _comparison_finding(
  row: Mapping[str, Any] | None,
  *,
  minimum_cell_samples: int,
  minimum_inference_dates: int,
  fdr_alpha: float,
) -> str:
  if row is None:
    return (
      "预注册的价格位置主对照中，没有 high-minus-low 结果同时满足 "
      f"FDR q≤{_format_probability(fdr_alpha)}、置信区间不跨 0、"
      f"至少 {minimum_inference_dates} 个有效交易日，且异常量与正常量样本"
      f"各不少于 {minimum_cell_samples}；因此不形成确认结论。"
    )
  return (
    f"{_benchmark(row.get('benchmark'))}、{_return_kind(row.get('return_kind'))}、"
    f"{_horizon(row.get('horizon'))} 口径下，高位相对低位的"
    "“异常量−正常量”收益差为 "
    f"{_format_percent(row.get('spread_mean'))}"
    f"（FDR q={_format_probability(row.get('q_value'))}，"
    f"置信区间={_format_interval(row.get('ci_low'), row.get('ci_high'), percent=True)}，"
    f"有效交易日={_format_integer(row.get('unique_dates'))}，"
    f"异常量/正常量样本={_format_integer(row.get('shock_sample_size'))}/"
    f"{_format_integer(row.get('normal_sample_size'))}）；"
    "这是历史条件关联，不代表因果效应。"
  )


def _significant_interaction(
  regressions: Sequence[Mapping[str, Any]],
  *,
  fdr_alpha: float,
  configured_horizons: tuple[int, ...],
) -> str | None:
  candidates: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
  for regression in regressions:
    coefficients = regression.get("coefficients")
    if not isinstance(coefficients, Sequence) or isinstance(coefficients, (str, bytes)):
      continue
    for coefficient in coefficients:
      if not isinstance(coefficient, Mapping):
        continue
      term = str(coefficient.get("term") or "")
      normalized = term.lower()
      is_interaction = (
        ":" in term
        or "*" in term
        or ("rvol" in normalized and "position" in normalized)
        or ("shock" in normalized and "position" in normalized)
      )
      q_value = _number(coefficient.get("q_value"))
      ci_low = _number(coefficient.get("ci_low"))
      ci_high = _number(coefficient.get("ci_high"))
      if (
        is_interaction
        and q_value is not None
        and q_value <= fdr_alpha
        and coefficient.get("significant") is True
        and ci_low is not None
        and ci_high is not None
        and ci_low <= ci_high
        and (ci_low > 0 or ci_high < 0)
      ):
        candidates.append((regression, coefficient))
  if not candidates:
    return None
  horizon_order = {horizon: index for index, horizon in enumerate(configured_horizons)}
  regression, coefficient = min(
    candidates,
    key=lambda item: (
      _regression_benchmark_order(item[0].get("dependent_variable")),
      0 if item[0].get("return_kind") == "close_response" else 1,
      horizon_order.get(_integer(item[0].get("horizon")) or -1, 99),
      _integer(item[0].get("horizon")) or 2**31 - 1,
      str(item[1].get("term") or ""),
      json.dumps(item[0], ensure_ascii=False, sort_keys=True, default=str),
    ),
  )
  estimate = _number(coefficient.get("estimate"))
  q_value = _number(coefficient.get("q_value"))
  direction = "正向" if (estimate or 0) >= 0 else "负向"
  return (
    f"回归中的交互项“{coefficient.get('term')}”呈{direction}关联"
    f"（{_return_kind(regression.get('return_kind'))}、"
    f"{_horizon(regression.get('horizon'))}，FDR q="
    f"{_format_probability(q_value)}）；该结果仍不构成因果证据。"
  )


def _regression_benchmark_order(value: Any) -> int:
  dependent = str(value or "")
  if dependent.startswith("csi300_excess_"):
    return 0
  if dependent.startswith("market_excess_"):
    return 1
  return 2


def _collect_warnings(
  metrics: Mapping[str, Any],
  quality: Mapping[str, Any],
  regressions: Sequence[Mapping[str, Any]],
) -> list[str]:
  warnings: list[str] = []
  for raw in (quality.get("warnings"), metrics.get("warnings")):
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
      warnings.extend(str(item) for item in raw if str(item).strip())
  for regression in regressions:
    regression_warnings = regression.get("warnings")
    if isinstance(regression_warnings, Sequence) and not isinstance(
      regression_warnings, (str, bytes)
    ):
      warnings.extend(str(item) for item in regression_warnings if str(item).strip())
  warnings.append("本报告描述历史样本中的相关关系，不代表因果关系，也不构成投资建议。")
  return list(dict.fromkeys(warnings))


def _build_methodology(config: Mapping[str, Any]) -> list[dict[str, str]]:
  event = config.get("event")
  event_config = event if isinstance(event, Mapping) else {}
  conditioning = config.get("conditioning")
  condition_config = conditioning if isinstance(conditioning, Mapping) else {}
  statistics = config.get("statistics")
  statistics_config = statistics if isinstance(statistics, Mapping) else {}
  rvol_window = _first_nonempty(
    event_config.get("relative_volume_window"),
    event_config.get("rvol_window"),
    20,
  )
  threshold = _first_nonempty(
    event_config.get("relative_volume_threshold"),
    event_config.get("abnormal_threshold"),
    event_config.get("rvol_threshold"),
    1.5,
  )
  normal_min = _first_nonempty(
    event_config.get("normal_relative_volume_min"),
    0.8,
  )
  normal_max = _first_nonempty(
    event_config.get("normal_relative_volume_max"),
    1.2,
  )
  position_window = _first_nonempty(condition_config.get("price_position_window"), 252)
  cooldown = _first_nonempty(event_config.get("cooldown_days"), 10)
  minimum_dates = _first_nonempty(
    statistics_config.get("minimum_inference_dates"),
    30,
  )
  block_length = _first_nonempty(
    statistics_config.get("moving_block_length"),
    "horizon",
  )
  return [
    {
      "title": "异常放量",
      "formula": (
        f"RVOL{rvol_window}ₜ = Volumeₜ / Mean(Volumeₜ₋{rvol_window} … Volumeₜ₋₁)"
      ),
      "detail": f"RVOL ≥ {threshold} 记为异常放量，基准窗口不包含事件日。",
    },
    {
      "title": "预注册正常量对照",
      "formula": f"{normal_min} ≤ RVOL{rvol_window} < {normal_max}",
      "detail": "正常量范围在运行前固定；主结果先做同日截面等权，再比较异常量与正常量。",
    },
    {
      "title": "事前价格位置",
      "formula": (f"Position{position_window}ₜ₋₁ = (Closeₜ₋₁ − Low) / (High − Low)"),
      "detail": "价格位置严格截止 T−1，避免事件日价格变化进入条件变量。",
    },
    {
      "title": "事件与收益",
      "formula": "T 日识别事件；分别计算 T 日收盘响应与 T+1 开盘可交易收益",
      "detail": f"同一股票事件默认冷却 {cooldown} 个交易日；缺失 bar 不前向填充。",
    },
    {
      "title": "推断边界",
      "formula": "交易日期 moving-block bootstrap；block length ≥ horizon",
      "detail": (
        f"配置 block={block_length}（实际不短于收益周期）；独立日期少于"
        f" {minimum_dates} 时不报告置信区间、p 值或 FDR q 值。"
      ),
    },
    {
      "title": "主回归",
      "formula": "Y ~ Shock + centered(Position T−1) + Shock×Position + pre-event controls",
      "detail": (
        "使用完整阈值前合格样本；连续变量中心化。因变量按沪深300、"
        "全市场等权、绝对收益的固定顺序确定，不按结果切换。"
      ),
    },
  ]


def _event_curve_table(
  rows: Sequence[Mapping[str, Any]],
  *,
  confidence_label: str,
) -> dict[str, Any]:
  sorted_rows = sorted(
    rows,
    key=lambda row: (
      str(row.get("return_kind") or ""),
      str(row.get("benchmark") or ""),
      _integer(row.get("horizon")) or 0,
    ),
  )
  body = []
  for row in sorted_rows[:200]:
    body.append(
      [
        _return_kind(row.get("return_kind")),
        _benchmark(row.get("benchmark")),
        _horizon(row.get("horizon")),
        _format_integer(row.get("sample_size")),
        _format_integer(row.get("unique_dates")),
        _format_percent(row.get("mean")),
        _format_percent(row.get("median")),
        _format_percent(row.get("positive_rate")),
        _format_interval(row.get("ci_low"), row.get("ci_high"), percent=True),
      ]
    )
  return {
    "title": "事件曲线明细",
    "headers": (
      "收益口径",
      "比较基准",
      "周期",
      "样本数",
      "独立日期",
      "均值",
      "中位数",
      "上涨概率",
      confidence_label,
    ),
    "rows": body,
    "empty_message": "未提供 event_curve 数据。",
    "note": _limit_note(len(sorted_rows), 200),
  }


def _grouped_statistics_table(
  rows: Sequence[Mapping[str, Any]],
  *,
  title: str = "条件分组统计",
  limit: int = 300,
  minimum_cell_samples: int,
  confidence_label: str,
) -> dict[str, Any]:
  sorted_rows = sorted(
    rows,
    key=lambda row: (
      _integer(row.get("horizon")) or 0,
      _format_dimensions(row.get("dimensions")),
      str(row.get("return_kind") or ""),
      str(row.get("benchmark") or ""),
    ),
  )
  body = []
  for row in sorted_rows[:limit]:
    body.append(
      [
        _format_dimensions(row.get("dimensions")),
        _return_kind(row.get("return_kind")),
        _benchmark(row.get("benchmark")),
        _horizon(row.get("horizon")),
        _format_integer(row.get("sample_size")),
        _format_integer(row.get("unique_dates")),
        _format_percent(row.get("mean")),
        _format_percent(row.get("median")),
        _format_percent(row.get("positive_rate")),
        _format_interval(row.get("ci_low"), row.get("ci_high"), percent=True),
        _format_probability(row.get("q_value")),
        _significance(row, minimum_cell_samples=minimum_cell_samples),
      ]
    )
  return {
    "title": title,
    "headers": (
      "分组",
      "收益口径",
      "比较基准",
      "周期",
      "样本数",
      "独立日期",
      "均值",
      "中位数",
      "上涨概率",
      confidence_label,
      "FDR q值",
      "显著性",
    ),
    "rows": body,
    "empty_message": "未提供 grouped_statistics 数据。",
    "note": _limit_note(len(sorted_rows), limit),
  }


def _comparison_table(
  rows: Sequence[Mapping[str, Any]],
  *,
  confidence_label: str,
) -> dict[str, Any]:
  sorted_rows = sorted(
    rows,
    key=lambda row: (
      _integer(row.get("horizon")) or 0,
      _format_dimensions(row.get("dimensions")),
      str(row.get("return_kind") or ""),
      str(row.get("benchmark") or ""),
    ),
  )
  body = [
    [
      _format_dimensions(row.get("dimensions")),
      _return_kind(row.get("return_kind")),
      _benchmark(row.get("benchmark")),
      _horizon(row.get("horizon")),
      _format_integer(row.get("shock_sample_size")),
      _format_integer(row.get("normal_sample_size")),
      _format_integer(row.get("unique_dates")),
      _format_percent(row.get("shock_mean")),
      _format_percent(row.get("shock_median")),
      _format_percent(row.get("normal_mean")),
      _format_percent(row.get("normal_median")),
      _format_percent(row.get("spread_mean")),
      _format_percent(row.get("spread_median")),
      _format_interval(row.get("ci_low"), row.get("ci_high"), percent=True),
      _format_probability(row.get("q_value")),
      "显著"
      if row.get("significant") is True
      else "不显著"
      if row.get("significant") is False
      else "不推断",
    ]
    for row in sorted_rows[:300]
  ]
  return {
    "title": "异常量与预注册正常量对照",
    "headers": (
      "比较",
      "收益口径",
      "比较基准",
      "周期",
      "异常量样本",
      "正常量样本",
      "独立日期",
      "异常量均值",
      "异常量中位数",
      "正常量均值",
      "正常量中位数",
      "差值",
      "差值中位数",
      confidence_label,
      "FDR q值",
      "显著性",
    ),
    "rows": body,
    "empty_message": "未提供 comparison 数据。",
    "note": (
      _limit_note(len(sorted_rows), 300)
      or "先按交易日与事前价格位置做截面等权，再计算异常量减正常量；"
      "区块 Bootstrap 日期不足时不报告推断。"
    ),
  }


def _comparison_sensitivity_tables(
  value: Any,
  *,
  confidence_label: str,
) -> list[dict[str, Any]]:
  if not isinstance(value, Mapping):
    return []
  tables: list[dict[str, Any]] = []
  for name, rows_value in value.items():
    table = _comparison_table(
      _coerce_rows(rows_value),
      confidence_label=confidence_label,
    )
    table["title"] = f"冷却敏感性：{name}"
    tables.append(table)
  return tables


def _regression_table(
  regressions: Sequence[Mapping[str, Any]],
  *,
  confidence_label: str,
) -> dict[str, Any]:
  body: list[list[str]] = []
  for regression in regressions:
    coefficients = regression.get("coefficients")
    if not isinstance(coefficients, Sequence) or isinstance(coefficients, (str, bytes)):
      continue
    for coefficient in coefficients:
      if not isinstance(coefficient, Mapping):
        continue
      body.append(
        [
          _return_kind(regression.get("return_kind")),
          _horizon(regression.get("horizon")),
          str(regression.get("dependent_variable") or "—"),
          str(coefficient.get("term") or "—"),
          _format_number(coefficient.get("estimate")),
          _format_number(coefficient.get("std_error")),
          _format_number(coefficient.get("t_stat")),
          _format_probability(coefficient.get("p_value")),
          _format_probability(coefficient.get("q_value")),
          "显著"
          if coefficient.get("significant") is True
          else "不显著"
          if coefficient.get("significant") is False
          else "不推断",
          _format_interval(coefficient.get("ci_low"), coefficient.get("ci_high")),
          _format_integer(regression.get("nobs")),
          _format_number(regression.get("r_squared")),
        ]
      )
  return {
    "title": "面板回归结果",
    "headers": (
      "收益口径",
      "周期",
      "因变量",
      "变量",
      "系数",
      "标准误",
      "t 值",
      "p 值",
      "交互 FDR q值",
      "交互显著性",
      confidence_label,
      "样本数",
      "R²",
    ),
    "rows": body[:300],
    "empty_message": "未提供 regressions 数据或回归未能估计。",
    "note": (
      _limit_note(len(body), 300)
      or "标准误口径由运行结果记录；v1 目标为股票与事件日期双向聚类。"
    ),
  }


def _robustness_tables(
  value: Any,
  *,
  minimum_cell_samples: int,
  confidence_label: str,
) -> list[dict[str, Any]]:
  if not isinstance(value, Mapping):
    return []
  tables = []
  for name, rows_value in value.items():
    rows = _coerce_rows(rows_value)
    table = _grouped_statistics_table(
      rows,
      title=f"稳健性：{name}",
      limit=150,
      minimum_cell_samples=minimum_cell_samples,
      confidence_label=confidence_label,
    )
    tables.append(table)
  return tables


def _key_value_table(
  title: str,
  value: Mapping[str, Any],
  empty_message: str,
) -> dict[str, Any]:
  flattened = _flatten_mapping(value)
  return {
    "title": title,
    "headers": ("项目", "值"),
    "rows": [[_humanize_key(key), _format_generic(item)] for key, item in flattened],
    "empty_message": empty_message,
    "note": "",
  }


def _flatten_mapping(
  value: Mapping[str, Any], prefix: str = ""
) -> list[tuple[str, Any]]:
  rows: list[tuple[str, Any]] = []
  for key in sorted(value):
    item = value[key]
    path = f"{prefix}.{key}" if prefix else str(key)
    if isinstance(item, Mapping):
      rows.extend(_flatten_mapping(item, path))
    else:
      rows.append((path, item))
  return rows


def _format_dimensions(value: Any) -> str:
  if not isinstance(value, Mapping) or not value:
    return "全样本"
  return "；".join(
    f"{_DIMENSION_LABELS.get(str(key), str(key))}={_category_label(item)}"
    for key, item in sorted(value.items())
  )


def _category_label(value: Any) -> str:
  labels = {
    "down": "放量下跌",
    "flat": "放量滞涨",
    "stall": "放量滞涨",
    "up": "放量上涨",
    "low": "低位",
    "mid": "中位",
    "middle": "中位",
    "high": "高位",
  }
  text = str(value)
  return labels.get(text, text)


def _date_range(
  config: Mapping[str, Any],
  quality: Mapping[str, Any] | None = None,
) -> str:
  value = config.get("date_range")
  if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
    values = [str(item) for item in value]
    if len(values) >= 2:
      return f"{values[0]} 至 {values[1]}"

  universe = config.get("universe")
  if isinstance(universe, Mapping):
    end_value = universe.get("end_date")
    if end_value == "latest" and quality is not None:
      end_value = quality.get("requested_end")
    end_date = _parse_date(end_value)
    years = _integer(universe.get("lookback_years"))
    if end_date is not None and years is not None:
      try:
        start_date = end_date.replace(year=end_date.year - years)
      except ValueError:
        start_date = end_date.replace(
          year=end_date.year - years,
          month=2,
          day=28,
        )
      return f"{start_date.isoformat()} 至 {end_date.isoformat()}"

  start = _first_nonempty(config.get("start_date"), config.get("date_from"))
  end = _first_nonempty(config.get("end_date"), config.get("date_to"))
  if start or end:
    return f"{start or '—'} 至 {end or '—'}"
  if quality is not None:
    source_start = _parse_date(quality.get("requested_start"))
    source_end = _parse_date(quality.get("requested_end"))
    if source_start is not None or source_end is not None:
      return (
        f"{source_start.isoformat() if source_start else '—'} 至 "
        f"{source_end.isoformat() if source_end else '—'}"
      )
  return "未记录"


def _universe_summary(config: Mapping[str, Any]) -> str:
  universe = config.get("universe")
  if not isinstance(universe, Mapping):
    return "沪深 A 股"
  codes = universe.get("stock_codes")
  if isinstance(codes, Sequence) and not isinstance(codes, (str, bytes)):
    scope = f"{len(codes)} 只固定样本"
  else:
    scope = "全 A 股"
  benchmark = universe.get("benchmark_code")
  return f"{scope} · 基准 {benchmark}" if benchmark else scope


def _parse_date(value: Any) -> date | None:
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  text = str(value or "").strip()
  if len(text) < 10:
    return None
  try:
    return date.fromisoformat(text[:10])
  except ValueError:
    return None


def _status_view(value: Any) -> dict[str, str]:
  normalized = str(value or "unknown").lower()
  if normalized in {"completed", "complete", "success", "succeeded"}:
    return {"label": "已完成", "class": "success"}
  if normalized in {"failed", "failed_preflight", "error"}:
    return {"label": "失败", "class": "danger"}
  if normalized in {"running", "in_progress"}:
    return {"label": "运行中", "class": "warning"}
  return {"label": str(value or "未记录"), "class": "neutral"}


def _return_kind(value: Any) -> str:
  text = str(value or "")
  return _RETURN_KIND_LABELS.get(text, text or "—")


def _benchmark(value: Any) -> str:
  text = str(value or "")
  return _BENCHMARK_LABELS.get(text, text or "—")


def _horizon(value: Any) -> str:
  integer = _integer(value)
  return f"T+{integer}" if integer is not None else "—"


def _significance(
  row: Mapping[str, Any],
  *,
  minimum_cell_samples: int,
) -> str:
  sample_size = _integer(row.get("sample_size"))
  if sample_size is not None and sample_size < minimum_cell_samples:
    return "样本不足"
  value = row.get("significant")
  if value is True:
    return "显著"
  if value is False:
    return "不显著"
  return "未评估"


def _format_percent(value: Any) -> str:
  number = _number(value)
  return f"{number * 100:.2f}%" if number is not None else "—"


def _format_probability(value: Any) -> str:
  number = _number(value)
  return f"{number:.4g}" if number is not None else "—"


def _format_number(value: Any) -> str:
  number = _number(value)
  return f"{number:.6g}" if number is not None else "—"


def _format_integer(value: Any) -> str:
  integer = _integer(value)
  if integer is not None:
    return f"{integer:,}"
  if value not in (None, ""):
    return str(value)
  return "—"


def _format_interval(low: Any, high: Any, *, percent: bool = False) -> str:
  low_number, high_number = _number(low), _number(high)
  if low_number is None or high_number is None:
    return "—"
  if percent:
    return f"[{low_number * 100:.2f}%, {high_number * 100:.2f}%]"
  return f"[{low_number:.6g}, {high_number:.6g}]"


def _format_generic(value: Any) -> str:
  if value is None:
    return "—"
  if isinstance(value, bool):
    return "是" if value else "否"
  if isinstance(value, float):
    return f"{value:.8g}"
  if isinstance(value, (list, tuple)):
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))
  return str(value)


def _humanize_key(key: str) -> str:
  parts = key.split(".")
  translated = [_VALUE_LABELS.get(part, part.replace("_", " ")) for part in parts]
  return " / ".join(translated)


def _limit_note(total: int, limit: int) -> str:
  if total > limit:
    return f"共 {total:,} 行；HTML 仅展示前 {limit:,} 行，完整结果以 CSV/JSON 为准。"
  return ""


def _first_nonempty(*values: Any) -> Any:
  return next((value for value in values if value not in (None, "")), None)


def _number(value: Any) -> float | None:
  if value is None or isinstance(value, bool):
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
  number = _number(value)
  return int(number) if number is not None else None


def _write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
  temporary = path.with_suffix(f"{path.suffix}.tmp")
  temporary.write_text(content, encoding=encoding, newline="\n")
  temporary.replace(path)
