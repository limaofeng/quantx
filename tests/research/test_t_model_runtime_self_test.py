"""Pinned numerical self-tests; no formal model trial or registry registration."""

from dataclasses import asdict
from math import log

import pytest
from quantx_domain.trading.t_assistant_execution import (
  TModelRuntimeBinding,
  stable_manifest_hash,
)
from quantx_infrastructure.services.t_model_cpu_artifact import CLASS_ORDER
from quantx_infrastructure.services.t_model_runtime_self_test import (
  TOLERANCE_POLICY_VERSION,
  load_self_tested_cpu_artifact,
)
from quantx_research.t_model_export import publish_immutable_cpu_artifact

from tests.research.test_t_assistant_model_data import bar
from tests.research.test_t_model_cpu_artifact import payload


def fixture(tmp_path):
  path = tmp_path / "model.json"
  artifact = publish_immutable_cpu_artifact(path, payload() | {"intercepts": [log(2), 0.0, 0.0]})
  feature = bar()
  manifest = dict(format="quantx-t-runtime-self-test.v1", tolerance_policy_version=TOLERANCE_POLICY_VERSION,
    class_order=list(CLASS_ORDER), cases=[dict(case_id="known-logit-fixture", feature_bar=asdict(feature),
      model_as_of_ms=feature.available_at_ms, expected_probabilities=[0.5, 0.25, 0.25],
      expected_ood_status="IN_DISTRIBUTION")])
  material = dict(model_id=artifact.model_id, model_version=artifact.model_version,
    registry_stage="SHADOW", registry_authorization_revision=2, artifact_manifest_sha256=artifact.sha256,
    feature_schema_version=1, label_spec_version=artifact.label_spec_version,
    calibration_version=artifact.calibration_version, portfolio_policy_compatibility_hash=artifact.policy_compatibility_hash,
    runtime_self_test_manifest_hash=stable_manifest_hash(manifest), self_test_tolerance_policy_version=TOLERANCE_POLICY_VERSION)
  return dict(root=tmp_path, entry=dict(relative_path="model.json", sha256=artifact.sha256, bytes=path.stat().st_size),
    binding=TModelRuntimeBinding.create(**material), self_test_manifest=manifest)


def rebind(args, **changes):
  material = args["binding"].to_dict()
  material.pop("binding_hash")
  material.update(runtime_self_test_manifest_hash=stable_manifest_hash(args["self_test_manifest"]))
  material.update(changes)
  args["binding"] = TModelRuntimeBinding.create(**material)


def test_pinned_fixed_sample_runs_real_cpu_inference(tmp_path):
  args = fixture(tmp_path)
  artifact = load_self_tested_cpu_artifact(**args)
  assert artifact.sha256 == args["binding"].artifact_manifest_sha256


@pytest.mark.parametrize("damage", ["expected", "ood", "duplicate", "empty", "budget", "class_order", "tolerance", "feature", "fence", "ood_type"])
def test_self_test_rejects_invalid_or_mismatched_vectors(tmp_path, damage):
  args = fixture(tmp_path)
  manifest = args["self_test_manifest"]
  case = manifest["cases"][0]
  if damage == "expected":
    case["expected_probabilities"] = [0.1, 0.2, 0.7]
  elif damage == "ood":
    case["expected_ood_status"] = "WARN"
  elif damage == "duplicate":
    manifest["cases"].append(dict(case))
  elif damage == "empty":
    manifest["cases"] = []
  elif damage == "budget":
    manifest["cases"] = [dict(case, case_id=str(index)) for index in range(33)]
  elif damage == "class_order":
    manifest["class_order"].reverse()
  elif damage == "tolerance":
    manifest["tolerance_policy_version"] = "arbitrarily-loose"
  elif damage == "fence":
    case["feature_bar"]["source_fence_range"] = []
  elif damage == "ood_type":
    case["expected_ood_status"] = []
  else:
    case["feature_bar"]["feature_values"] = [1.0] * 6
  rebind(args)
  with pytest.raises(ValueError):
    load_self_tested_cpu_artifact(**args)


@pytest.mark.parametrize("field,value", [("model_id", "other"), ("model_version", "other"),
  ("feature_schema_version", 2), ("label_spec_version", "other"), ("calibration_version", "other"),
  ("portfolio_policy_compatibility_hash", "f" * 64), ("artifact_manifest_sha256", "f" * 64)])
def test_loaded_artifact_must_match_full_binding(tmp_path, field, value):
  args = fixture(tmp_path)
  rebind(args, **{field: value})
  with pytest.raises(ValueError, match="T_MODEL_SELF_TEST_"):
    load_self_tested_cpu_artifact(**args)


def test_changed_expectations_without_new_binding_hash_are_rejected(tmp_path):
  args = fixture(tmp_path)
  args["self_test_manifest"]["cases"][0]["expected_probabilities"] = [0.1, 0.2, 0.7]
  with pytest.raises(ValueError, match="MANIFEST_INVALID"):
    load_self_tested_cpu_artifact(**args)


def test_self_test_preserves_safe_path_and_hash_loading(tmp_path):
  args = fixture(tmp_path)
  args["entry"]["relative_path"] = "../model.json"
  with pytest.raises(ValueError, match="PATH_INVALID"):
    load_self_tested_cpu_artifact(**args)


def test_oversized_self_test_is_rejected_before_artifact_loading(tmp_path, monkeypatch):
  import quantx_infrastructure.services.t_model_runtime_self_test as module

  args = fixture(tmp_path)
  args["self_test_manifest"]["cases"][0]["case_id"] = "x" * module.MAX_SELF_TEST_BYTES
  rebind(args)
  def forbidden(*args, **kwargs):
    raise AssertionError("oversized self-test reached artifact IO")
  monkeypatch.setattr(module, "load_registered_cpu_artifact", forbidden)
  with pytest.raises(ValueError, match="MANIFEST_INVALID"):
    load_self_tested_cpu_artifact(**args)
