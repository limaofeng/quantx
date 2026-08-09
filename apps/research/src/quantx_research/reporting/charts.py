"""Dependency-free SVG charts used by the offline research report.

The report deliberately emits plain SVG instead of JavaScript charts.  The
result is deterministic, printable, and can be embedded directly in the HTML
artifact while still being written as a standalone figure.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

_WIDTH = 960
_PALETTE = (
  "#b42318",
  "#2563eb",
  "#0f766e",
  "#7c3aed",
  "#c2410c",
  "#0369a1",
  "#a21caf",
  "#4d7c0f",
)

_RETURN_KIND_LABELS = {
  "close_response": "事件收盘响应",
  "next_open": "次日开盘入场",
}
_BENCHMARK_LABELS = {
  "absolute": "绝对收益",
  "csi300": "沪深300超额",
  "market_equal_weight": "全市场等权超额",
}
_DIRECTION_LABELS = {
  "down": "放量下跌",
  "flat": "放量滞涨",
  "stall": "放量滞涨",
  "up": "放量上涨",
  "volume_down": "放量下跌",
  "volume_stall": "放量滞涨",
  "volume_up": "放量上涨",
}


def build_event_curve_svg(
  rows: Sequence[Mapping[str, Any]],
  *,
  confidence_label: str = "95% CI",
) -> str:
  """Render mean forward returns by horizon as a deterministic SVG."""

  points: dict[str, list[tuple[float, float, float | None, float | None]]] = (
    defaultdict(list)
  )
  for row in rows:
    horizon = _number(_first(row, "horizon", "holding_days", "days"))
    value = _number(_first(row, "mean", "mean_return", "value"))
    if horizon is None or value is None:
      continue
    label = str(
      row.get("series")
      or _series_label(
        str(row.get("return_kind") or ""),
        str(row.get("benchmark") or ""),
      )
    )
    points[label].append(
      (
        horizon,
        value,
        _number(_first(row, "ci_low", "ci_lower")),
        _number(_first(row, "ci_high", "ci_upper")),
      )
    )

  if not points:
    return build_empty_state_svg(
      "后续走势事件曲线",
      "没有可绘制的 event_curve 数据；报告仍保留该位置以显式标记缺失。",
    )

  ordered = {
    label: sorted(series, key=lambda point: point[0])
    for label, series in sorted(points.items())[:8]
  }
  all_points = [point for series in ordered.values() for point in series]
  x_values = [point[0] for point in all_points]
  y_values = [point[1] for point in all_points]
  y_values.extend(
    bound for point in all_points for bound in point[2:] if bound is not None
  )

  max_abs = max((abs(value) for value in y_values), default=0.01)
  max_abs = max(max_abs * 1.15, 0.005)
  x_min, x_max = min(x_values), max(x_values)
  if math.isclose(x_min, x_max):
    x_min -= 1
    x_max += 1

  left, right, top, bottom = 82, 34, 58, 92
  height = 430
  plot_w = _WIDTH - left - right
  plot_h = height - top - bottom

  def sx(value: float) -> float:
    return left + (value - x_min) / (x_max - x_min) * plot_w

  def sy(value: float) -> float:
    return top + (max_abs - value) / (2 * max_abs) * plot_h

  parts = [_svg_open(_WIDTH, height, "后续走势事件曲线")]
  parts.append('<text x="24" y="30" class="chart-title">后续走势事件曲线</text>')
  parts.append(
    '<text x="24" y="49" class="chart-subtitle">'
    "均值收益；阴影范围未插值，圆点悬停可查看置信区间"
    "</text>"
  )

  for index in range(5):
    value = max_abs - index * (2 * max_abs / 4)
    y = sy(value)
    parts.append(
      f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" class="grid"/>'
    )
    parts.append(
      f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" '
      f'class="axis-label">{escape(_percent(value))}</text>'
    )

  for value in sorted(set(x_values)):
    x = sx(value)
    parts.append(
      f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
      f'y2="{top + plot_h}" class="grid grid-vertical"/>'
    )
    parts.append(
      f'<text x="{x:.1f}" y="{top + plot_h + 24}" text-anchor="middle" '
      f'class="axis-label">T+{_compact_number(value)}</text>'
    )

  zero_y = sy(0)
  parts.append(
    f'<line x1="{left}" y1="{zero_y:.1f}" x2="{left + plot_w}" '
    f'y2="{zero_y:.1f}" class="zero-line"/>'
  )

  for series_index, (label, series) in enumerate(ordered.items()):
    color = _PALETTE[series_index % len(_PALETTE)]
    path = " ".join(
      f"{'M' if point_index == 0 else 'L'} {sx(point[0]):.1f} {sy(point[1]):.1f}"
      for point_index, point in enumerate(series)
    )
    parts.append(
      f'<path d="{path}" fill="none" stroke="{color}" '
      'stroke-width="2.5" stroke-linejoin="round"/>'
    )
    for horizon, value, ci_low, ci_high in series:
      title = f"{label} · T+{_compact_number(horizon)}：{_percent(value)}"
      if ci_low is not None and ci_high is not None:
        title += f"（{confidence_label} {_percent(ci_low)} ～ {_percent(ci_high)}）"
      parts.append(
        f'<circle cx="{sx(horizon):.1f}" cy="{sy(value):.1f}" r="4.2" '
        f'fill="{color}" stroke="#fff" stroke-width="1.5">'
        f"<title>{escape(title)}</title></circle>"
      )

  legend_y = height - 35
  cursor_x = left
  for series_index, label in enumerate(ordered):
    color = _PALETTE[series_index % len(_PALETTE)]
    parts.append(
      f'<line x1="{cursor_x}" y1="{legend_y}" x2="{cursor_x + 22}" '
      f'y2="{legend_y}" stroke="{color}" stroke-width="3"/>'
    )
    parts.append(
      f'<text x="{cursor_x + 29}" y="{legend_y + 4}" '
      f'class="legend-label">{escape(label)}</text>'
    )
    cursor_x += min(250, 52 + _visual_length(label) * 13)
    if cursor_x > _WIDTH - 190:
      break

  parts.append("</svg>")
  return "".join(parts)


def build_interaction_heatmap_svg(
  rows: Sequence[Mapping[str, Any]],
) -> str:
  """Render the RVOL × prior-position × event-direction interaction matrix."""

  selected, selection_note = _select_heatmap_rows(rows)
  cells: list[tuple[str, str, float, int | None]] = []
  available_dimensions: set[str] = set()

  normalized: list[tuple[dict[str, str], float, int | None]] = []
  for row in selected:
    value = _number(_first(row, "mean", "mean_return", "value"))
    if value is None:
      continue
    dimensions_value = row.get("dimensions")
    dimensions = (
      {str(key): str(value) for key, value in dimensions_value.items()}
      if isinstance(dimensions_value, Mapping)
      else {}
    )
    for key in (
      "rvol_bin",
      "relative_volume_bin",
      "volume_bin",
      "price_position_bin",
      "price_position",
      "event_direction",
      "direction",
    ):
      if key in row and key not in dimensions:
        dimensions[key] = str(row[key])
    available_dimensions.update(dimensions)
    normalized.append(
      (
        dimensions,
        value,
        _integer(_first(row, "sample_size", "n", "count")),
      )
    )

  rvol_key = _find_key(
    available_dimensions,
    "rvol_bin",
    "relative_volume_bin",
    "volume_bin",
  )
  position_key = _find_key(available_dimensions, "price_position_bin", "price_position")
  direction_key = _find_key(available_dimensions, "event_direction", "direction")

  if rvol_key and position_key and direction_key:
    for dimensions, value, sample_size in normalized:
      if not all(key in dimensions for key in (rvol_key, position_key, direction_key)):
        continue
      row_label = (
        f"{_category_label(dimensions[position_key])} / "
        f"{_category_label(dimensions[direction_key])}"
      )
      cells.append(
        (
          row_label,
          _category_label(dimensions[rvol_key]),
          value,
          sample_size,
        )
      )
    x_title = "RVOL20 分组"
    y_title = "事前价格位置 / 事件方向"
  elif position_key and direction_key:
    for dimensions, value, sample_size in normalized:
      if position_key not in dimensions or direction_key not in dimensions:
        continue
      cells.append(
        (
          _category_label(dimensions[position_key]),
          _category_label(dimensions[direction_key]),
          value,
          sample_size,
        )
      )
    x_title = "事件方向"
    y_title = "事前价格位置"
  elif rvol_key and position_key:
    for dimensions, value, sample_size in normalized:
      if rvol_key not in dimensions or position_key not in dimensions:
        continue
      cells.append(
        (
          _category_label(dimensions[position_key]),
          _category_label(dimensions[rvol_key]),
          value,
          sample_size,
        )
      )
    x_title = "RVOL20 分组"
    y_title = "事前价格位置"
  else:
    return build_empty_state_svg(
      "异常放量 × 价格位置交互热力图",
      "grouped_statistics 中缺少价格位置、事件方向或 RVOL 分组维度。",
    )

  if not cells:
    return build_empty_state_svg(
      "异常放量 × 价格位置交互热力图",
      "没有同时包含有效分组维度和均值收益的数据。",
    )

  row_labels = sorted({cell[0] for cell in cells}, key=_category_sort_key)
  column_labels = sorted({cell[1] for cell in cells}, key=_category_sort_key)
  values: dict[tuple[str, str], list[tuple[float, int | None]]] = defaultdict(list)
  for row_label, column_label, value, sample_size in cells:
    values[(row_label, column_label)].append((value, sample_size))

  left = 205
  right = 54
  top = 112
  bottom = 90
  cell_w = max(86, min(145, (_WIDTH - left - right) / len(column_labels)))
  width = int(left + right + cell_w * len(column_labels))
  cell_h = 48
  height = int(top + bottom + cell_h * len(row_labels))
  max_abs = max(abs(value) for _, _, value, _ in cells)
  max_abs = max(max_abs, 0.001)

  parts = [_svg_open(width, height, "异常放量与价格位置交互热力图")]
  parts.append(
    '<text x="24" y="30" class="chart-title">异常放量 × 价格位置交互热力图</text>'
  )
  parts.append(
    f'<text x="24" y="50" class="chart-subtitle">'
    f"{escape(selection_note)}；单元格为均值收益</text>"
  )
  parts.append(
    f'<text x="{left + cell_w * len(column_labels) / 2:.1f}" y="78" '
    f'text-anchor="middle" class="axis-title">{escape(x_title)}</text>'
  )
  parts.append(
    f'<text x="18" y="{top + cell_h * len(row_labels) / 2:.1f}" '
    'text-anchor="middle" class="axis-title" '
    f'transform="rotate(-90 18 {top + cell_h * len(row_labels) / 2:.1f})">'
    f"{escape(y_title)}</text>"
  )

  for column_index, label in enumerate(column_labels):
    x = left + column_index * cell_w + cell_w / 2
    parts.append(
      f'<text x="{x:.1f}" y="{top - 15}" text-anchor="middle" '
      f'class="axis-label">{escape(label)}</text>'
    )

  for row_index, row_label in enumerate(row_labels):
    y = top + row_index * cell_h
    parts.append(
      f'<text x="{left - 12}" y="{y + cell_h / 2 + 4:.1f}" '
      f'text-anchor="end" class="axis-label">{escape(row_label)}</text>'
    )
    for column_index, column_label in enumerate(column_labels):
      x = left + column_index * cell_w
      samples = values.get((row_label, column_label), [])
      if samples:
        cell_value = sum(item[0] for item in samples) / len(samples)
        sample_values = [item[1] for item in samples if item[1] is not None]
        sample_size = sum(sample_values) if sample_values else None
        fill = _heat_color(cell_value / max_abs)
        text_color = "#ffffff" if abs(cell_value / max_abs) >= 0.58 else "#17202a"
        title = f"{row_label} × {column_label}：{_percent(cell_value)}"
        if sample_size is not None:
          title += f"，n={sample_size:,}"
        parts.append(
          f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" '
          f'height="{cell_h:.1f}" fill="{fill}" stroke="#ffffff" '
          'stroke-width="2">'
          f"<title>{escape(title)}</title></rect>"
        )
        parts.append(
          f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h / 2 + 5:.1f}" '
          f'text-anchor="middle" fill="{text_color}" '
          f'class="cell-label">{escape(_percent(cell_value))}</text>'
        )
      else:
        parts.append(
          f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w:.1f}" '
          f'height="{cell_h:.1f}" fill="#f3f4f6" stroke="#ffffff" '
          'stroke-width="2"/>'
        )
        parts.append(
          f'<text x="{x + cell_w / 2:.1f}" y="{y + cell_h / 2 + 5:.1f}" '
          'text-anchor="middle" class="cell-label missing">—</text>'
        )

  legend_y = height - 42
  legend_x = left
  legend_w = min(360, width - left - right)
  steps = 36
  for index in range(steps):
    ratio = -1 + 2 * index / (steps - 1)
    parts.append(
      f'<rect x="{legend_x + legend_w * index / steps:.1f}" '
      f'y="{legend_y:.1f}" width="{legend_w / steps + 0.6:.1f}" '
      f'height="11" fill="{_heat_color(ratio)}"/>'
    )
  parts.append(
    f'<text x="{legend_x}" y="{legend_y + 29}" class="axis-label">'
    f"{escape(_percent(-max_abs))}</text>"
  )
  parts.append(
    f'<text x="{legend_x + legend_w / 2}" y="{legend_y + 29}" '
    'text-anchor="middle" class="axis-label">0%</text>'
  )
  parts.append(
    f'<text x="{legend_x + legend_w}" y="{legend_y + 29}" '
    f'text-anchor="end" class="axis-label">{escape(_percent(max_abs))}</text>'
  )
  parts.append("</svg>")
  return "".join(parts)


def build_regression_coefficients_svg(
  regressions: Sequence[Mapping[str, Any]],
  *,
  confidence_label: str = "95% CI",
) -> str:
  """Render coefficients and confidence intervals for one preferred model."""

  candidates = [
    row
    for row in regressions
    if isinstance(row.get("coefficients"), Sequence)
    and not isinstance(row.get("coefficients"), (str, bytes))
    and any(
      isinstance(coefficient, Mapping)
      and _number(coefficient.get("estimate")) is not None
      for coefficient in row.get("coefficients", [])
    )
  ]
  if not candidates:
    return build_empty_state_svg(
      "回归系数与置信区间",
      "没有可绘制的 regressions 数据。",
    )

  def priority(row: Mapping[str, Any]) -> tuple[int, int, str]:
    horizon = _integer(row.get("horizon")) or 0
    return (
      0 if row.get("return_kind") == "close_response" else 1,
      0 if horizon == 20 else -horizon,
      str(row.get("dependent_variable") or ""),
    )

  model = sorted(candidates, key=priority)[0]
  coefficients: list[tuple[str, float, float, float]] = []
  raw_coefficients = model.get("coefficients")
  assert isinstance(raw_coefficients, Sequence)
  for coefficient in raw_coefficients:
    if not isinstance(coefficient, Mapping):
      continue
    estimate = _number(coefficient.get("estimate"))
    if estimate is None:
      continue
    ci_low = _number(coefficient.get("ci_low"))
    ci_high = _number(coefficient.get("ci_high"))
    coefficients.append(
      (
        str(coefficient.get("term") or "未命名变量"),
        estimate,
        ci_low if ci_low is not None else estimate,
        ci_high if ci_high is not None else estimate,
      )
    )
  if not coefficients:
    return build_empty_state_svg(
      "回归系数与置信区间",
      "回归模型中没有有效的系数估计。",
    )

  coefficients = coefficients[:16]
  max_abs = max(
    abs(value)
    for _, estimate, ci_low, ci_high in coefficients
    for value in (estimate, ci_low, ci_high)
  )
  max_abs = max(max_abs * 1.15, 0.001)
  left, right, top, bottom = 230, 48, 76, 48
  row_h = 39
  height = int(top + bottom + row_h * len(coefficients))
  plot_w = _WIDTH - left - right

  def sx(value: float) -> float:
    return left + (value + max_abs) / (2 * max_abs) * plot_w

  parts = [_svg_open(_WIDTH, height, "回归系数与置信区间")]
  parts.append('<text x="24" y="30" class="chart-title">回归系数与置信区间</text>')
  subtitle = (
    f"{_RETURN_KIND_LABELS.get(str(model.get('return_kind')), model.get('return_kind') or '')}"
    f" · T+{model.get('horizon', '—')} · "
    f"{model.get('dependent_variable', '因变量未标记')}"
  )
  parts.append(
    f'<text x="24" y="50" class="chart-subtitle">{escape(str(subtitle))}</text>'
  )

  for index in range(5):
    value = -max_abs + index * (2 * max_abs / 4)
    x = sx(value)
    parts.append(
      f'<line x1="{x:.1f}" y1="{top - 10}" x2="{x:.1f}" '
      f'y2="{height - bottom}" class="grid"/>'
    )
    parts.append(
      f'<text x="{x:.1f}" y="{height - 18}" text-anchor="middle" '
      f'class="axis-label">{escape(_compact_number(value))}</text>'
    )
  parts.append(
    f'<line x1="{sx(0):.1f}" y1="{top - 12}" x2="{sx(0):.1f}" '
    f'y2="{height - bottom}" class="zero-line"/>'
  )

  for index, (term, estimate, ci_low, ci_high) in enumerate(coefficients):
    y = top + index * row_h + row_h / 2
    parts.append(
      f'<text x="{left - 14}" y="{y + 4:.1f}" text-anchor="end" '
      f'class="axis-label">{escape(term)}</text>'
    )
    parts.append(
      f'<line x1="{sx(ci_low):.1f}" y1="{y:.1f}" '
      f'x2="{sx(ci_high):.1f}" y2="{y:.1f}" '
      'stroke="#475569" stroke-width="2"/>'
    )
    parts.append(
      f'<line x1="{sx(ci_low):.1f}" y1="{y - 5:.1f}" '
      f'x2="{sx(ci_low):.1f}" y2="{y + 5:.1f}" '
      'stroke="#475569" stroke-width="2"/>'
    )
    parts.append(
      f'<line x1="{sx(ci_high):.1f}" y1="{y - 5:.1f}" '
      f'x2="{sx(ci_high):.1f}" y2="{y + 5:.1f}" '
      'stroke="#475569" stroke-width="2"/>'
    )
    color = "#b42318" if estimate >= 0 else "#0f766e"
    parts.append(
      f'<circle cx="{sx(estimate):.1f}" cy="{y:.1f}" r="5" '
      f'fill="{color}"><title>{escape(term)}：'
      f"{escape(_compact_number(estimate))}，{escape(confidence_label)} "
      f"{escape(_compact_number(ci_low))} ～ "
      f"{escape(_compact_number(ci_high))}</title></circle>"
    )

  parts.append("</svg>")
  return "".join(parts)


def build_empty_state_svg(title: str, message: str) -> str:
  """Return a valid SVG that makes unavailable chart data explicit."""

  width, height = _WIDTH, 300
  return "".join(
    [
      _svg_open(width, height, title),
      f'<text x="24" y="32" class="chart-title">{escape(title)}</text>',
      '<rect x="24" y="64" width="912" height="188" rx="14" '
      'fill="#f8fafc" stroke="#cbd5e1" stroke-dasharray="6 5"/>',
      '<text x="480" y="142" text-anchor="middle" '
      'class="empty-title">暂无可视化数据</text>',
      f'<text x="480" y="174" text-anchor="middle" '
      f'class="empty-message">{escape(message)}</text>',
      "</svg>",
    ]
  )


def _select_heatmap_rows(
  rows: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], str]:
  valid = [
    row
    for row in rows
    if _number(_first(row, "mean", "mean_return", "value")) is not None
  ]
  if not valid:
    return [], "无有效分组"

  return_kinds = {str(row.get("return_kind")) for row in valid}
  selected_return = (
    "close_response" if "close_response" in return_kinds else sorted(return_kinds)[0]
  )
  valid = [row for row in valid if str(row.get("return_kind")) == selected_return]

  horizons = [
    value for row in valid if (value := _integer(row.get("horizon"))) is not None
  ]
  selected_horizon = 20 if 20 in horizons else max(horizons, default=0)
  if horizons:
    valid = [row for row in valid if _integer(row.get("horizon")) == selected_horizon]

  benchmarks = {str(row.get("benchmark")) for row in valid}
  preferred_benchmarks = [
    benchmark
    for benchmark in ("csi300", "market_equal_weight", "absolute")
    if benchmark in benchmarks
  ] or sorted(benchmarks)
  selected_benchmark = max(
    preferred_benchmarks,
    key=lambda benchmark: sum(
      _integer(row.get("sample_size")) or 0
      for row in valid
      if str(row.get("benchmark")) == benchmark
    ),
  )
  valid = [row for row in valid if str(row.get("benchmark")) == selected_benchmark]

  note = (
    f"{_RETURN_KIND_LABELS.get(selected_return, selected_return)} · "
    f"{_BENCHMARK_LABELS.get(selected_benchmark, selected_benchmark)}"
  )
  if selected_horizon:
    note += f" · T+{selected_horizon}"
  return valid, note


def _svg_open(width: int, height: int, title: str) -> str:
  return (
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
    f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
    f'aria-label="{escape(title)}">'
    "<style>"
    "text{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "'Microsoft YaHei',sans-serif}"
    ".chart-title{font-size:18px;font-weight:700;fill:#17202a}"
    ".chart-subtitle{font-size:12px;fill:#64748b}"
    ".axis-label{font-size:11px;fill:#475569}"
    ".axis-title{font-size:12px;font-weight:600;fill:#334155}"
    ".grid{stroke:#e2e8f0;stroke-width:1}"
    ".grid-vertical{stroke-dasharray:3 4}"
    ".zero-line{stroke:#64748b;stroke-width:1.2}"
    ".legend-label{font-size:11px;fill:#334155}"
    ".cell-label{font-size:12px;font-weight:700}"
    ".cell-label.missing{fill:#94a3b8}"
    ".empty-title{font-size:18px;font-weight:700;fill:#475569}"
    ".empty-message{font-size:13px;fill:#64748b}"
    "</style>"
  )


def _series_label(return_kind: str, benchmark: str) -> str:
  return (
    f"{_RETURN_KIND_LABELS.get(return_kind, return_kind or '收益')} · "
    f"{_BENCHMARK_LABELS.get(benchmark, benchmark or '未标记基准')}"
  )


def _first(row: Mapping[str, Any], *keys: str) -> Any:
  for key in keys:
    if key in row:
      return row[key]
  return None


def _number(value: Any) -> float | None:
  if isinstance(value, bool) or value is None:
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if math.isfinite(number) else None


def _integer(value: Any) -> int | None:
  number = _number(value)
  return int(number) if number is not None else None


def _find_key(available: set[str], *candidates: str) -> str | None:
  return next((candidate for candidate in candidates if candidate in available), None)


def _category_label(value: str) -> str:
  normalized = value.strip()
  return _DIRECTION_LABELS.get(normalized, normalized)


def _category_sort_key(value: str) -> tuple[int, float, str]:
  normalized = value.lower()
  direction_order = {
    "低位": 0,
    "中位": 1,
    "高位": 2,
    "放量下跌": 3,
    "放量滞涨": 4,
    "放量上涨": 5,
  }
  for label, order in direction_order.items():
    if label in value:
      return order, 0, value
  match = re.search(r"-?\d+(?:\.\d+)?", normalized)
  return 10, float(match.group()) if match else math.inf, value


def _heat_color(ratio: float) -> str:
  ratio = max(-1.0, min(1.0, ratio))
  neutral = (247, 247, 244)
  target = (180, 35, 24) if ratio >= 0 else (15, 118, 110)
  weight = abs(ratio) ** 0.7
  rgb = tuple(
    round(neutral[index] + (target[index] - neutral[index]) * weight)
    for index in range(3)
  )
  return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _percent(value: float) -> str:
  return f"{value * 100:.2f}%"


def _compact_number(value: float) -> str:
  if math.isclose(value, round(value), abs_tol=1e-10):
    return str(int(round(value)))
  return f"{value:.4g}"


def _visual_length(value: str) -> int:
  return sum(2 if ord(character) > 127 else 1 for character in value)
