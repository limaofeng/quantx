"""Generate self-contained artifacts for a completed T-trade replay."""

from __future__ import annotations

import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from quantx_infrastructure.core.utils import time_utils

REPORT_SCHEMA_VERSION = 2
REPORT_JSON_NAME = "t-trade-report.json"
REPORT_HTML_NAME = "t-trade-report.html"


def _resolve_manifest_path(raw_path: str) -> Path:
  candidates = [Path(raw_path)]
  if not Path(raw_path).is_absolute():
    candidates.append(Path("data") / raw_path)
  for candidate in candidates:
    resolved = candidate.resolve(strict=False)
    if resolved.is_file() and resolved.name == "manifest.json":
      return resolved
  raise FileNotFoundError(f"回测 manifest 不存在: {raw_path}")


def _conclusion(replay: Dict[str, Any]) -> tuple[str, str]:
  summary = dict(replay.get("summary") or {})
  failed = int(summary.get("liquidation_failed_cycles", 0) or 0)
  completed = int(summary.get("completed_cycles", 0) or 0)
  if failed > 0:
    return "INCONCLUSIVE", "存在期末未清算批次，当前结果不能用于判断信号可靠性。"
  diagnostics = dict(replay.get("opportunity_diagnostics") or {})
  if diagnostics.get("available") is not True:
    return (
      "DIAGNOSTICS_UNAVAILABLE",
      "成交与资金结果已生成，但缺少按当前策略运行聚合的 V3 机会诊断，"
      "不能据此判断信号漏斗或触发质量。",
    )
  ready_seconds = sum(
    float(
      dict(dict(item or {}).get("denominator") or {}).get(
        "ready_instrument_seconds", 0.0
      )
      or 0.0
    )
    for item in list(diagnostics.get("partitions") or [])
  )
  if ready_seconds <= 0.0:
    return (
      "INSUFFICIENT_READY_TIME",
      "本次回放没有可计量的 READY 标的时长，不能评价机会生成质量。",
    )
  if completed < 10:
    return "INSUFFICIENT_SAMPLE", "闭环样本少于 10 批，结论仅供观察，需要扩大回放区间。"
  if (
    float(summary.get("t_net_profit", 0.0) or 0.0) > 0
    and float(summary.get("excess_return_pct", 0.0) or 0.0) > 0
    and float(summary.get("win_rate_pct", 0.0) or 0.0) >= 50.0
  ):
    return (
      "PROMISING",
      "样本内税费后收益、相对基准和胜率均为正向，信号值得继续样本外验证。",
    )
  return (
    "NOT_VALIDATED",
    "样本内结果未同时通过收益、超额收益和胜率检查，信号可靠性尚未验证。",
  )


def _fmt(value: Any, digits: int = 2) -> str:
  try:
    return f"{float(value):,.{digits}f}"
  except (TypeError, ValueError):
    return "--"


def _diagnostic_partition_html(raw: Any, index: int) -> str:
  partition = dict(raw or {})
  denominator = dict(partition.get("denominator") or {})
  ready_seconds = float(denominator.get("ready_instrument_seconds", 0.0) or 0.0)
  coordinate = " / ".join(
    [
      str(partition.get("policy_version") or "--"),
      str(partition.get("feature_schema_version") or "--"),
      str(partition.get("profile_version") or "无画像"),
    ]
  )
  funnel_rows = []
  for raw_stage in list(partition.get("funnel") or []):
    stage = dict(raw_stage or {})
    conversion = stage.get("conversion_rate")
    conversion_text = (
      "--" if conversion is None else f"{_fmt(float(conversion) * 100.0)}%"
    )
    funnel_rows.append(
      "<tr>"
      f"<td>{html.escape(str(stage.get('label') or stage.get('code') or ''))}</td>"
      f"<td><code>{html.escape(str(stage.get('code') or ''))}</code></td>"
      f"<td><code>{html.escape(str(stage.get('unit_code') or '--'))}</code></td>"
      f"<td>{int(stage.get('count', 0) or 0)}</td>"
      f"<td>{html.escape(str(stage.get('denominator_code') or '起点'))} · {conversion_text}</td>"
      "</tr>"
    )
  funnel_body = "".join(funnel_rows) or (
    '<tr><td colspan="5">当前分区没有形成漏斗事件</td></tr>'
  )
  blocker_rows = []
  for raw_blocker in list(partition.get("blockers") or []):
    item = dict(raw_blocker or {})
    blocker = dict(item.get("blocker") or {})
    rate = item.get("rate")
    rate_text = "--" if rate is None else f"{_fmt(float(rate) * 100.0)}%"
    denominator_text = (
      f"{item.get('denominator_code') or '--'}={_fmt(item.get('denominator_value'), 0)}"
    )
    blocker_rows.append(
      "<tr>"
      f"<td>{html.escape(str(blocker.get('label') or blocker.get('code') or ''))}</td>"
      f"<td><code>{html.escape(str(blocker.get('code') or ''))}</code></td>"
      f"<td>{int(item.get('count', 0) or 0)}</td>"
      f"<td>{rate_text}</td>"
      f"<td><code>{html.escape(denominator_text)}</code></td>"
      "</tr>"
    )
  blocker_body = "".join(blocker_rows) or (
    '<tr><td colspan="5">当前分区没有记录 blocker</td></tr>'
  )
  transition_rows = []
  for raw_transition in list(partition.get("fsm_transitions") or []):
    transition = dict(raw_transition or {})
    transition_rows.append(
      "<tr>"
      f"<td>{html.escape(str(transition.get('branch') or ''))}</td>"
      f"<td>{html.escape(str(transition.get('from_phase') or ''))}</td>"
      f"<td>{html.escape(str(transition.get('to_phase') or ''))}</td>"
      f"<td>{int(transition.get('count', 0) or 0)}</td>"
      "</tr>"
    )
  transition_body = "".join(transition_rows) or (
    '<tr><td colspan="4">当前分区没有 FSM 转移</td></tr>'
  )
  outcome_rows = []
  for raw_outcome in list(partition.get("candidate_outcomes") or []):
    outcome = dict(raw_outcome or {})
    outcome_rows.append(
      "<tr>"
      f"<td>{html.escape(str(outcome.get('label') or outcome.get('code') or ''))}</td>"
      f"<td><code>{html.escape(str(outcome.get('code') or ''))}</code></td>"
      f"<td>{int(outcome.get('count', 0) or 0)}</td>"
      "</tr>"
    )
  outcome_body = "".join(outcome_rows) or (
    '<tr><td colspan="3">当前分区没有候选结果</td></tr>'
  )
  performance = dict(partition.get("post_candidate_performance") or {})
  if performance.get("available") is True:
    performance_html = (
      '<div class="notice">'
      f"费用后 MFE：{_fmt(performance.get('net_mfe_pct'))}%；"
      f"费用后 MAE：{_fmt(performance.get('net_mae_pct'))}%；"
      f"样本：{int(performance.get('sample_count', 0) or 0)}"
      "</div>"
    )
  else:
    required = "、".join(
      str(item) for item in performance.get("required_data_codes") or []
    )
    performance_html = (
      '<div class="notice warning"><strong>'
      + html.escape(
        str(
          performance.get("reason_code")
          or "POST_FILL_CAUSAL_PATH_AND_COST_LEDGER_UNAVAILABLE"
        )
      )
      + "</strong><br>"
      + html.escape(str(performance.get("reason") or "成交后表现不可用。"))
      + (f"<br><code>{html.escape(required)}</code>" if required else "")
      + "</div>"
    )
  return f"""
  <h3>版本分区 {index + 1} · <code>{html.escape(coordinate)}</code></h3>
  <div class="grid diagnostics-summary">
    <div class="card"><div class="label">样本分母</div><div class="value">{_fmt(ready_seconds / 3600.0, 3)} h</div><div class="muted">{_fmt(ready_seconds, 1)} READY 标的秒</div></div>
    <div class="card"><div class="label">口径</div><div class="value small-value">READY_INSTRUMENT_SECONDS</div><div class="muted">不使用原始 Tick 数</div></div>
  </div>
  <h3>机会漏斗</h3>
  <table><thead><tr><th>阶段</th><th>代码</th><th>单位</th><th>数量</th><th>转换分母</th></tr></thead><tbody>{funnel_body}</tbody></table>
  <h3>主要阻断</h3>
  <table><thead><tr><th>阻断</th><th>代码</th><th>次数</th><th>率</th><th>分母</th></tr></thead><tbody>{blocker_body}</tbody></table>
  <h3>FSM 转移</h3>
  <table><thead><tr><th>分支</th><th>From</th><th>To</th><th>次数</th></tr></thead><tbody>{transition_body}</tbody></table>
  <h3>候选结果</h3>
  <table><thead><tr><th>结果</th><th>代码</th><th>数量</th></tr></thead><tbody>{outcome_body}</tbody></table>
  <h3>候选后效果</h3>
  {performance_html}
  """


def _diagnostics_html(replay: Dict[str, Any]) -> str:
  diagnostics = dict(replay.get("opportunity_diagnostics") or {})
  if diagnostics.get("available") is not True:
    reason = html.escape(
      str(diagnostics.get("reason") or "V3 机会诊断未随回放结果生成。")
    )
    reason_code = html.escape(
      str(diagnostics.get("reason_code") or "DIAGNOSTICS_UNAVAILABLE")
    )
    return (
      "<h2>V3 机会诊断</h2>"
      f'<div class="notice warning"><strong>{reason_code}</strong><br>{reason}</div>'
      '<p class="muted">READY 时长、漏斗、阻断、候选结果和版本分组均显示为不可用；'
      "报告不会以 0 或原始 Tick 数代替缺失样本。</p>"
    )
  scope = dict(diagnostics.get("scope") or {})
  run_id = html.escape(str(scope.get("strategy_run_id") or "--"))
  scope_start = html.escape(str(scope.get("start_time") or "--"))
  scope_end = html.escape(str(scope.get("end_time") or "--"))
  version_rows = []
  for raw_version in list(diagnostics.get("version_groups") or []):
    version = dict(raw_version or {})
    version_rows.append(
      "<tr>"
      f"<td><code>{html.escape(str(version.get('policy_version') or 'UNKNOWN'))}</code></td>"
      f"<td><code>{html.escape(str(version.get('feature_schema_version') or 'UNKNOWN'))}</code></td>"
      f"<td><code>{html.escape(str(version.get('profile_version') or '--'))}</code></td>"
      f"<td>{int(version.get('count', 0) or 0)}</td>"
      "</tr>"
    )
  version_body = "".join(version_rows) or (
    '<tr><td colspan="4">当前范围没有版本化评估</td></tr>'
  )
  warning_codes = [str(item) for item in diagnostics.get("warnings") or []]
  warning_html = (
    '<div class="notice warning"><strong>版本合并警告</strong><br>'
    + html.escape("、".join(warning_codes))
    + "</div>"
    if warning_codes
    else ""
  )
  partition_html = (
    "".join(
      _diagnostic_partition_html(item, index)
      for index, item in enumerate(diagnostics.get("partitions") or [])
    )
    or '<div class="notice">当前范围没有可诊断的版本分区。</div>'
  )
  return f"""
  <h2>V3 机会诊断</h2>
  <div class="muted">策略运行 <code>{run_id}</code> · 范围 {scope_start} ～ {scope_end}</div>
  {warning_html}
  <h3>规则与特征版本</h3>
  <table><thead><tr><th>Policy</th><th>Feature schema</th><th>Profile</th><th>评估数</th></tr></thead><tbody>{version_body}</tbody></table>
  {partition_html}
  """


def _phase_one_baseline_html(replay: Dict[str, Any]) -> str:
  baseline = dict(replay.get("phase_one_baseline") or {})
  comparison = dict(replay.get("v3_vs_phase_one") or {})
  common_ready = dict(comparison.get("common_ready") or {})
  if baseline.get("available") is not True:
    return (
      '<h2>一期固定规则对照</h2><div class="notice warning"><strong>'
      + html.escape(
        str(baseline.get("reason_code") or "PHASE_ONE_BASELINE_NOT_COLLECTED")
      )
      + "</strong><br>"
      + html.escape(str(baseline.get("reason") or "一期规则对照不可用。"))
      + "</div>"
    )
  ready_seconds = float(
    dict(baseline.get("denominator") or {}).get("value", 0.0) or 0.0
  )
  edges = dict(baseline.get("candidate_edges") or {})
  reference = dict(baseline.get("candidate_reference_performance") or {})
  window_rows = []
  for raw_window in list(reference.get("fixed_windows") or []):
    window = dict(raw_window or {})
    window_rows.append(
      "<tr>"
      f"<td>{int(window.get('horizon_seconds', 0) or 0)} s</td>"
      f"<td>{int(window.get('sample_count', 0) or 0)}</td>"
      f"<td>{_fmt(window.get('average_return_pct'))}%</td>"
      f"<td>{_fmt(window.get('average_mfe_pct'))}%</td>"
      f"<td>{_fmt(window.get('average_mae_pct'))}%</td>"
      "</tr>"
    )
  window_body = "".join(window_rows) or (
    '<tr><td colspan="5">没有成熟的一期规则候选固定窗</td></tr>'
  )
  v3 = dict(comparison.get("v3") or {})
  phase_one = dict(comparison.get("phase_one") or {})
  warning = str(comparison.get("warning") or "")
  warning_html = (
    f'<div class="notice warning">{html.escape(warning)}</div>' if warning else ""
  )
  return f"""
  <h2>一期固定规则对照</h2>
  <div class="grid diagnostics-summary">
    <div class="card"><div class="label">一期数据可评估时长</div><div class="value">{_fmt(ready_seconds / 3600.0, 3)} h</div></div>
    <div class="card"><div class="label">一期候选边沿</div><div class="value">{sum(int(value or 0) for value in edges.values())}</div></div>
    <div class="card"><div class="label">V3 / 一期原始候选</div><div class="value">{int(v3.get("candidate_count", 0) or 0)} / {int(phase_one.get("candidate_count", 0) or 0)}</div></div>
    <div class="card"><div class="label">共同 READY 暴露</div><div class="value">{_fmt(float(common_ready.get("ready_instrument_seconds", 0.0) or 0.0) / 3600.0, 3)} h</div></div>
    <div class="card"><div class="label">共同口径候选率（V3 / 一期）</div><div class="value">{_fmt(common_ready.get("v3_candidate_rate_per_ready_instrument_hour"), 3)} / {_fmt(common_ready.get("phase_one_candidate_rate_per_ready_instrument_hour"), 3)}</div></div>
  </div>
  <p class="muted"><code>{html.escape(str(baseline.get("baseline_version") or ""))}</code> · 原始候选数分母不同，仅作各自明细；候选频率只在两侧同 Tick 同时 READY 的共同暴露上比较。影子基线不参与订单授权。</p>
  <table><thead><tr><th>窗口</th><th>样本</th><th>平均收益</th><th>平均 MFE</th><th>平均 MAE</th></tr></thead><tbody>{window_body}</tbody></table>
  {warning_html}
  """


def _report_html(payload: Dict[str, Any]) -> str:
  replay = dict(payload.get("replay") or {})
  summary = dict(replay.get("summary") or {})
  methodology = dict(replay.get("methodology") or {})
  cycles = list(replay.get("cycles") or [])
  rows = []
  for cycle in cycles:
    item = dict(cycle or {})
    rows.append(
      "<tr>"
      f"<td>{html.escape(str(item.get('stock_code', '')))}</td>"
      f"<td>{html.escape(str(item.get('status', '')))}</td>"
      f"<td>{_fmt(item.get('entry_avg_price'), 3)} / {_fmt(item.get('exit_avg_price'), 3)}</td>"
      f"<td>{_fmt(item.get('holding_hours'))} h</td>"
      f"<td>{_fmt(item.get('capital_utilization_pct'))}%</td>"
      f"<td>{_fmt(item.get('net_profit'))}</td>"
      f"<td>{html.escape(str(item.get('exit_reason', '') or '--'))}</td>"
      "</tr>"
    )
  cycle_rows = (
    "".join(rows) or '<tr><td colspan="7">回放区间内没有形成做 T 成交批次</td></tr>'
  )
  conclusion = html.escape(str(payload.get("conclusion", "")))
  diagnostics_html = _diagnostics_html(replay)
  baseline_html = _phase_one_baseline_html(replay)
  return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>QuantX 做 T 历史回放报告</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, "Microsoft YaHei", sans-serif; }}
    body {{ margin: 0; background: #07111f; color: #dbeafe; }}
    main {{ max-width: 1180px; margin: auto; padding: 32px 22px 64px; }}
    h1 {{ margin: 0 0 6px; font-size: 26px; }}
    h2 {{ margin: 28px 0 12px; font-size: 16px; color: #67e8f9; }}
    .muted {{ color: #64748b; font-size: 12px; }}
    .conclusion {{ margin: 22px 0; padding: 16px; border: 1px solid #164e63; background: #0c1f32; }}
    .notice {{ margin: 10px 0; padding: 12px; border: 1px solid #164e63; background: #0c1f32; }}
    .warning {{ border-color: #854d0e; background: #2b1c0a; color: #fde68a; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 10px; }}
    .card {{ padding: 14px; border: 1px solid #1e293b; background: #0b1628; }}
    .label {{ color: #64748b; font-size: 11px; }} .value {{ margin-top: 5px; font-size: 20px; font-weight: 800; }}
    .small-value {{ font-size: 13px; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th,td {{ padding: 9px; border-bottom: 1px solid #1e293b; text-align: left; }} th {{ color: #94a3b8; }}
    code {{ color: #a5f3fc; }}
  </style>
</head>
<body><main>
  <h1>QuantX 做 T 历史回放报告</h1>
  <div class="muted">运行 {html.escape(str(payload.get("run_id", "")))} · 生成于 {html.escape(str(payload.get("generated_at", "")))}</div>
  <div class="conclusion"><strong>{html.escape(str(payload.get("conclusion_code", "")))}</strong><br>{conclusion}</div>
  <div class="grid">
    <div class="card"><div class="label">做 T 税费后增量</div><div class="value">¥{_fmt(summary.get("t_net_profit"))}</div></div>
    <div class="card"><div class="label">相对不做 T 超额</div><div class="value">{_fmt(summary.get("excess_return_pct"))}%</div></div>
    <div class="card"><div class="label">完成批次 / 胜率</div><div class="value">{int(summary.get("completed_cycles", 0) or 0)} / {_fmt(summary.get("win_rate_pct"), 1)}%</div></div>
    <div class="card"><div class="label">等待折损后资金利用率</div><div class="value">{_fmt(summary.get("capital_utilization_pct"))}%</div></div>
    <div class="card"><div class="label">平均 / 最长持有</div><div class="value">{_fmt(summary.get("average_holding_hours"), 1)} / {_fmt(summary.get("max_holding_hours"), 1)} h</div></div>
    <div class="card"><div class="label">资金周转次数</div><div class="value">{_fmt(summary.get("capital_turnover_times"))}×</div></div>
    <div class="card"><div class="label">期末强制清算 / 失败</div><div class="value">{int(summary.get("forced_exit_cycles", 0) or 0)} / {int(summary.get("liquidation_failed_cycles", 0) or 0)}</div></div>
    <div class="card"><div class="label">总税费</div><div class="value">¥{_fmt(summary.get("total_fees"))}</div></div>
  </div>
  <h2>统计口径</h2>
  <p>{html.escape(str(methodology.get("forced_liquidation", "")))}</p>
  <p>{html.escape(str(methodology.get("capital_utilization", "")))}</p>
  <p>{html.escape(str(methodology.get("price_limits", "")))}</p>
  {diagnostics_html}
  {baseline_html}
  <h2>批次明细</h2>
  <table><thead><tr><th>标的</th><th>状态</th><th>买 / 卖均价</th><th>持有</th><th>资金利用率</th><th>净收益</th><th>退出原因</th></tr></thead>
  <tbody>{cycle_rows}</tbody></table>
  <p class="muted">历史回放不构成收益承诺；结论仍需不同时间区间和样本外数据验证。</p>
</main></body></html>"""


def _atomic_write(path: Path, content: str) -> None:
  temporary = path.with_name(f".{path.name}.tmp")
  temporary.write_text(content, encoding="utf-8")
  os.replace(temporary, path)


def write_t_trade_replay_report(
  result_path: str,
  replay_metrics: Dict[str, Any],
  *,
  run_id: str,
  backtest_id: Optional[str],
  start_time: Optional[datetime],
  end_time: Optional[datetime],
) -> Dict[str, Any]:
  """Write JSON + standalone HTML beside the versioned backtest manifest."""

  manifest_path = _resolve_manifest_path(result_path)
  generated_at = time_utils.now().isoformat()
  conclusion_code, conclusion = _conclusion(replay_metrics)
  payload = {
    "schema_version": REPORT_SCHEMA_VERSION,
    "generated_at": generated_at,
    "run_id": run_id,
    "backtest_id": backtest_id,
    "start_time": start_time.isoformat() if start_time else None,
    "end_time": end_time.isoformat() if end_time else None,
    "conclusion_code": conclusion_code,
    "conclusion": conclusion,
    "replay": replay_metrics,
  }
  report_dir = manifest_path.parent
  _atomic_write(
    report_dir / REPORT_JSON_NAME,
    json.dumps(payload, ensure_ascii=False, default=str, indent=2),
  )
  _atomic_write(report_dir / REPORT_HTML_NAME, _report_html(payload))

  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  artifacts = dict(manifest.get("artifacts") or {})
  artifacts.update(
    {
      "t_trade_report_json": REPORT_JSON_NAME,
      "t_trade_report_html": REPORT_HTML_NAME,
    }
  )
  manifest["artifacts"] = artifacts
  manifest["t_trade_report"] = {
    "schema_version": REPORT_SCHEMA_VERSION,
    "generated_at": generated_at,
    "conclusion_code": conclusion_code,
  }
  _atomic_write(
    manifest_path,
    json.dumps(manifest, ensure_ascii=False, default=str, indent=2),
  )
  return {
    "status": "GENERATED",
    "schema_version": REPORT_SCHEMA_VERSION,
    "generated_at": generated_at,
    "conclusion_code": conclusion_code,
    "conclusion": conclusion,
    "html_artifact": REPORT_HTML_NAME,
    "json_artifact": REPORT_JSON_NAME,
  }
