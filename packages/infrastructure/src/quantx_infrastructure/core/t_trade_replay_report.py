"""Generate self-contained artifacts for a completed T-trade replay."""

from __future__ import annotations

import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from quantx_infrastructure.core.utils import time_utils

REPORT_SCHEMA_VERSION = 1
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
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 10px; }}
    .card {{ padding: 14px; border: 1px solid #1e293b; background: #0b1628; }}
    .label {{ color: #64748b; font-size: 11px; }} .value {{ margin-top: 5px; font-size: 20px; font-weight: 800; }}
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
