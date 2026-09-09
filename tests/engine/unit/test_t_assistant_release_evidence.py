"""Synthetic P5 evidence must bind the release target; no real approval."""

import json
from copy import deepcopy
from dataclasses import asdict, replace

import pytest
from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
from quantx_engine.t_assistant_backtest_evaluation import evaluate_backtest_comparison
from quantx_engine.t_assistant_backtest_runtime import json_value
from quantx_engine.t_assistant_release_evidence import verify_live_release_evidence

from tests.engine.unit.test_t_assistant_backtest_qualification import policy
from tests.engine.unit.test_t_assistant_backtest_runtime import CODES, runtime, ticks


@pytest.fixture
async def release(tmp_path):
  request = runtime(request_only=True)
  request.runtime_options["parameters"]["entry_authorization"] = "MANUAL_CONFIRM"
  config_values = asdict(request.config)
  config_values.pop("config_snapshot_hash")
  config_values["canonical_payload"] = request.runtime_options["parameters"]
  request = replace(request, config=TAssistantConfigVersion.create(**config_values))
  approved = replace(
    policy(minute_coverage=0.001), scenario_thresholds={"base": (-1.0, 1.0, -1.0, 1.0)}
  )
  directory, report = await evaluate_backtest_comparison(
    request=request,
    events=ticks(),
    scenarios={"base": 0.0},
    code_manifest={"synthetic": "v1"},
    root=tmp_path,
    policy=approved,
  )
  values = asdict(request.config)
  values.pop("config_snapshot_hash")
  options = json_value(request.runtime_options)
  values.update(
    config_version_id="live-target",
    version=2,
    canonical_payload={
      "legacy_settings_snapshot": options["parameters"],
      "symbol_rule_policy": options["parameters"]["signal_policy"],
      "universe_policy": {"allowed_stock_codes": [CODES[0]], "ignored_stock_codes": []},
      "portfolio_policy": options["portfolio_policy"],
      "entry_execution_gate_policy": options["gate_policy"],
      "t_trading_envelope_policy": options["envelope_policies"][CODES[0]],
    },
  )
  saved = json.loads((directory / "report.json").read_text())
  return directory, saved["hash"], report["evidence"]["admission_policy_hash"], values


@pytest.mark.parametrize(
  "damage", [None, "parameters", "portfolio", "envelope", "gate", "symbol", "scorer"]
)
async def test_release_target_must_match_evaluated_policies(release, damage):
  directory, report_hash, policy_hash, original = release
  values = deepcopy(original)
  payload = values["canonical_payload"]
  if damage == "parameters":
    payload["legacy_settings_snapshot"]["target_trade_amount"] += 1
  elif damage == "portfolio":
    payload["portfolio_policy"]["max_total_t_amount"] = "99999"
  elif damage == "envelope":
    payload["t_trading_envelope_policy"]["max_entry_volume"] += 100
  elif damage == "gate":
    payload["entry_execution_gate_policy"]["version"] = "changed"
  elif damage == "symbol":
    payload["universe_policy"]["allowed_stock_codes"] = ["601398.SH"]
  elif damage == "scorer":
    values["entry_authorization"] = "AUTO"
  target = TAssistantConfigVersion.create(**values)
  if damage:
    with pytest.raises(ValueError, match="LIVE_RELEASE_"):
      verify_live_release_evidence(
        directory,
        expected_report_hash=report_hash,
        expected_policy_hash=policy_hash,
        target=target,
      )
  else:
    verified = verify_live_release_evidence(
      directory,
      expected_report_hash=report_hash,
      expected_policy_hash=policy_hash,
      target=target,
    )
    assert verified["material"]["config_snapshot_hash"] == target.config_snapshot_hash
    assert verified["material"]["allowed_stock_codes"] == [CODES[0]]
