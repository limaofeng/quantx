"""Load a pinned CPU artifact and verify hash-frozen engineering test vectors.

This is numerical/runtime verification, not OOS qualification or model approval.
"""

from dataclasses import fields
from math import isfinite

from quantx_application.t_trade_v3.model_features import (
  FEATURE_SCHEMA_VERSION,
  TModelFeatureBar,
)
from quantx_domain.trading.t_assistant_execution import (
  TModelRuntimeBinding,
  canonical_json_payload,
  stable_manifest_hash,
)

from quantx_infrastructure.services.t_model_cpu_artifact import (
  CLASS_ORDER,
  load_registered_cpu_artifact,
)

TOLERANCE_POLICY_VERSION = "t-cpu-probability-abs-1e-9.v1"
MAX_SELF_TEST_BYTES = 1_048_576
MAX_SELF_TEST_CASES = 32


def load_self_tested_cpu_artifact(*, root, entry, binding: TModelRuntimeBinding, self_test_manifest):
  # Reconstruct even a supplied value object so persisted/foreign callers cannot
  # skip the binding hash and schema checks.
  binding = TModelRuntimeBinding.from_mapping(binding.to_dict())
  if (
    not isinstance(self_test_manifest, dict)
    or set(self_test_manifest) != {"format", "tolerance_policy_version", "class_order", "cases"}
    or self_test_manifest["format"] != "quantx-t-runtime-self-test.v1"
    or self_test_manifest["tolerance_policy_version"] != TOLERANCE_POLICY_VERSION
    or binding.self_test_tolerance_policy_version != TOLERANCE_POLICY_VERSION
    or self_test_manifest["class_order"] != list(CLASS_ORDER)
    or not isinstance(self_test_manifest["cases"], list)
    or not 1 <= len(self_test_manifest["cases"]) <= MAX_SELF_TEST_CASES
    or len(canonical_json_payload(self_test_manifest).encode("utf-8")) > MAX_SELF_TEST_BYTES
    or stable_manifest_hash(self_test_manifest) != binding.runtime_self_test_manifest_hash
    or not isinstance(entry, dict) or entry.get("sha256") != binding.artifact_manifest_sha256
  ):
    raise ValueError("T_MODEL_SELF_TEST_MANIFEST_INVALID")
  artifact = load_registered_cpu_artifact(root, entry)
  if (
    artifact.model_id != binding.model_id or artifact.model_version != binding.model_version
    or binding.feature_schema_version != FEATURE_SCHEMA_VERSION
    or artifact.label_spec_version != binding.label_spec_version
    or artifact.calibration_version != binding.calibration_version
    or artifact.policy_compatibility_hash != binding.portfolio_policy_compatibility_hash
  ):
    raise ValueError("T_MODEL_SELF_TEST_BINDING_MISMATCH")
  seen = set()
  for case in self_test_manifest["cases"]:
    if not isinstance(case, dict) or set(case) != {
      "case_id", "feature_bar", "model_as_of_ms", "expected_probabilities", "expected_ood_status",
    }:
      raise ValueError("T_MODEL_SELF_TEST_CASE_INVALID")
    identity = case["case_id"]
    expected = case["expected_probabilities"]
    data = case["feature_bar"]
    if (
      not isinstance(identity, str) or not identity or len(identity) > 80 or identity in seen
      or not isinstance(data, dict) or set(data) != {item.name for item in fields(TModelFeatureBar)}
      or not isinstance(expected, list) or len(expected) != 3
      or any(type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1 for value in expected)
      or abs(sum(expected) - 1) > 1e-9
      or not isinstance(case["expected_ood_status"], str)
      or case["expected_ood_status"] not in {"IN_DISTRIBUTION", "WARN", "BLOCK"}
      or not isinstance(data["feature_values"], (list, tuple))
      or not isinstance(data["source_fence_range"], (list, tuple))
      or len(data["source_fence_range"]) != 2
      or any(type(value) is not int or value < 1 for value in data["source_fence_range"])
      or data["source_fence_range"][0] > data["source_fence_range"][1]
    ):
      raise ValueError("T_MODEL_SELF_TEST_CASE_INVALID")
    seen.add(identity)
    feature = TModelFeatureBar(**(data | {
      "feature_values": tuple(data["feature_values"]), "source_fence_range": tuple(data["source_fence_range"]),
    }))
    result = artifact.score(feature, model_as_of_ms=case["model_as_of_ms"])
    if (
      result.out_of_distribution_status != case["expected_ood_status"]
      or any(abs(actual - target) > 1e-9 for actual, target in zip(result.probabilities, expected))
    ):
      raise ValueError("T_MODEL_SELF_TEST_OUTPUT_MISMATCH")
  return artifact
