"""Bind reviewed P5 evidence to the exact economic policies of a CANARY target."""

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

from .t_assistant_backtest_evaluation import verify_backtest_admission_conclusion
from .t_assistant_live_admission import canary_instrument_codes


def verify_live_release_evidence(
  directory, *, expected_report_hash, expected_policy_hash, target
):
  """No approval is written; caller must separately authenticate the reviewer."""
  evidence = verify_backtest_admission_conclusion(
    directory,
    expected_report_hash=expected_report_hash,
    expected_policy_hash=expected_policy_hash,
  )
  report = evidence["report"]["material"]
  if report["strategy_admission"] != "PASS" or report["p6_allowed"] is not True:
    raise ValueError("LIVE_RELEASE_P5_PASS_REQUIRED")
  request = evidence["evaluation"]["material"]["request"]
  config, options = request["config"], request["runtime_options"]
  payload = target.canonical_payload
  if (
    target.scorer_mode != "RULE_ONLY"
    or target.entry_authorization != "MANUAL_CONFIRM"
    or target.rollout_stage != "CANARY"
    or config["scorer_mode"] != "RULE_ONLY"
    or config["entry_authorization"] != "MANUAL_CONFIRM"
    or config["policy_version"] != target.policy_version
    or config["feature_schema_version"] != target.feature_schema_version
    or options["parameters"].get("entry_authorization") != "MANUAL_CONFIRM"
    or payload.get("legacy_settings_snapshot") != options["parameters"]
    or payload.get("symbol_rule_policy") != options["parameters"].get("signal_policy")
  ):
    raise ValueError("LIVE_RELEASE_STRATEGY_CONFIG_CONFLICT")
  codes = canary_instrument_codes(payload)
  if not set(codes) <= set(options["initial_positions"]):
    raise ValueError("LIVE_RELEASE_UNEVALUATED_SYMBOL")

  def require_policy(actual, expected):
    if not isinstance(actual, dict) or any(
      key not in actual
      or stable_manifest_hash({"value": actual[key]})
      != stable_manifest_hash({"value": value})
      for key, value in expected.items()
    ):
      raise ValueError("LIVE_RELEASE_ECONOMIC_POLICY_CONFLICT")

  require_policy(payload.get("portfolio_policy"), options["portfolio_policy"])
  require_policy(payload.get("entry_execution_gate_policy"), options["gate_policy"])
  for code in codes:
    require_policy(
      payload.get("t_trading_envelope_policy"), options["envelope_policies"][code]
    )
  material = {
    "p5_evidence_hash": expected_report_hash,
    "p5_policy_hash": expected_policy_hash,
    "evaluation_hash": evidence["evaluation"]["hash"],
    "config_version_id": target.config_version_id,
    "config_snapshot_hash": target.config_snapshot_hash,
    "allowed_stock_codes": list(codes),
  }
  return {"material": material, "hash": stable_manifest_hash(material)}
