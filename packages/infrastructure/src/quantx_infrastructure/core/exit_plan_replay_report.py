"""Write compact JSON and HTML artifacts for exit-plan replays."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


def _json_default(value: Any) -> str:
  if isinstance(value, datetime):
    return value.isoformat()
  return str(value)


def write_exit_plan_replay_report(
  result_path: str, metrics: Mapping[str, Any], *, run_id: str, backtest_id: str
) -> dict[str, Any]:
  base = Path(result_path).parent
  json_path = base / "exit-plan-replay-report.json"
  html_path = base / "exit-plan-replay-report.html"
  payload = dict(metrics)
  payload.update({"run_id": run_id, "backtest_id": backtest_id})
  json_path.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
    encoding="utf-8",
  )
  summary = dict(payload.get("summary") or {})
  rows = (
    "".join(
      "<tr>"
      f"<td>{html.escape(str(item.get('timestamp') or ''))}</td>"
      f"<td>{html.escape(str(item.get('rule_type') or item.get('rule_id') or ''))}</td>"
      f"<td>{int(item.get('volume') or 0)}</td>"
      f"<td>{float(item.get('price') or 0):.3f}</td>"
      "</tr>"
      for item in list(payload.get("events") or [])
    )
    or '<tr><td colspan="4">区间内没有卖出成交</td></tr>'
  )
  html_path.write_text(
    "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
    "<title>卖出计划历史回放</title>"
    "<style>body{font:14px system-ui;background:#050b16;color:#e2e8f0;padding:28px}"
    "h1{font-size:22px}code{color:#93c5fd}table{border-collapse:collapse;width:100%}"
    "th,td{border:1px solid #243247;padding:8px;text-align:left}th{background:#0f1d30}"
    ".card{background:#0b1728;border:1px solid #243247;padding:16px;margin:16px 0}</style>"
    f"<h1>卖出计划历史回放</h1><p><code>{html.escape(run_id)}</code></p>"
    f"<div class='card'>{html.escape(str(summary.get('conclusion') or ''))}</div>"
    "<table><thead><tr><th>时间</th><th>规则</th><th>数量</th><th>价格</th>"
    f"</tr></thead><tbody>{rows}</tbody></table></html>",
    encoding="utf-8",
  )
  return {
    "status": "READY",
    "schema_version": 1,
    "generated_at": datetime.now().isoformat(),
    "conclusion_code": str(summary.get("conclusion_code") or ""),
    "conclusion": str(summary.get("conclusion") or ""),
    "html_artifact": str(html_path),
    "json_artifact": str(json_path),
  }
