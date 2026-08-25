import json
from datetime import datetime

from quantx_infrastructure.core.exit_plan_replay_report import (
  write_exit_plan_replay_report,
)


def test_exit_plan_replay_report_writes_json_and_html(tmp_path) -> None:
  result_path = tmp_path / "state" / "result.json"
  result_path.parent.mkdir()
  metrics = {
    "summary": {
      "conclusion": "本区间计划较继续持有高 1.25 个百分点。",
      "conclusion_code": "PLAN_OUTPERFORMED_HOLD",
    },
    "events": [
      {
        "timestamp": datetime(2026, 8, 20, 10, 30),
        "rule_type": "TARGET_PRICE",
        "volume": 100,
        "price": 12.34,
      }
    ],
  }

  report = write_exit_plan_replay_report(
    str(result_path), metrics, run_id="run-1", backtest_id="backtest-1"
  )

  assert report["status"] == "READY"
  json_payload = json.loads(
    (result_path.parent / "exit-plan-replay-report.json").read_text("utf-8")
  )
  assert json_payload["run_id"] == "run-1"
  html = (result_path.parent / "exit-plan-replay-report.html").read_text("utf-8")
  assert "TARGET_PRICE" in html
  assert "本区间计划较继续持有高" in html
