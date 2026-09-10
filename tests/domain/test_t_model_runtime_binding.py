"""Complete binding identity; synthetic hashes are not self-test/release evidence."""

from dataclasses import FrozenInstanceError, replace

import pytest
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TModelRuntimeBinding,
)


def binding(mode="ACTIVE", **changes):
  return TModelRuntimeBinding.create(**(dict(model_id="t-model", model_version="v1",
    registry_stage=mode, registry_authorization_revision=1, artifact_manifest_sha256="a" * 64,
    feature_schema_version=1, label_spec_version="label-v1", calibration_version="calibration-v1",
    portfolio_policy_compatibility_hash="b" * 64, runtime_self_test_manifest_hash="c" * 64,
    self_test_tolerance_policy_version="tolerance-v1") | changes))


def config(value, mode="ACTIVE", schema=1):
  return TAssistantConfigVersion.create(config_version_id="version-1", config_id="config-1",
    version=1, config_schema_version="v1", canonical_payload={"policy": "v1"},
    entry_authorization="MANUAL_CONFIRM", rollout_stage="CANARY", policy_version="policy-v1",
    feature_schema_version=schema, scorer_mode=mode, model_runtime_binding=value)


def test_binding_is_immutable_and_round_trips_with_all_fields():
  value = binding()
  assert TModelRuntimeBinding.from_mapping(value.to_dict()) == value
  with pytest.raises(FrozenInstanceError):
    value.model_version = "v2"
  changed = value.to_dict()
  changed["model_version"] = "v2"
  with pytest.raises(ValueError, match="HASH_MISMATCH"):
    TModelRuntimeBinding.from_mapping(changed)
  assert binding(model_version="v2").binding_hash != value.binding_hash


@pytest.mark.parametrize("field,value", [("registry_stage", "SUSPENDED"), ("registry_stage", []),
  ("registry_authorization_revision", True), ("registry_authorization_revision", 0),
  ("feature_schema_version", False), ("artifact_manifest_sha256", "A" * 64),
  ("runtime_self_test_manifest_hash", "bad"), ("self_test_tolerance_policy_version", ""),
  ("model_id", "model ")])
def test_invalid_binding_fields_are_rejected(field, value):
  with pytest.raises(ValueError, match="T_MODEL_BINDING_INVALID"):
    binding(**{field: value})


@pytest.mark.parametrize("change", ["missing", "extra", "hash_only"])
def test_binding_shape_has_no_partial_or_unknown_field_fallback(change):
  value = binding().to_dict()
  if change == "missing":
    value.pop("runtime_self_test_manifest_hash")
  elif change == "extra":
    value["training_run"] = "not-online-identity"
  else:
    value = {"binding_hash": value["binding_hash"]}
  with pytest.raises(ValueError, match="FIELDS_INVALID"):
    config(value)


@pytest.mark.parametrize("mode", ["SHADOW", "ACTIVE"])
def test_config_binds_exact_mode_schema_and_copies_input(mode):
  value = binding(mode).to_dict()
  version = config(value, mode)
  assert version.model_runtime_binding == value
  value["model_version"] = "tampered"
  assert version.model_runtime_binding["model_version"] == "v1"
  with pytest.raises(ValueError, match="EXECUTION_MISMATCH"):
    config(binding(mode).to_dict(), mode, schema=2)
  with pytest.raises(ValueError, match="EXECUTION_MISMATCH"):
    config(binding(mode).to_dict(), "ACTIVE" if mode == "SHADOW" else "SHADOW")


def test_execution_rechecks_binding_on_construction():
  from tests.domain.test_t_assistant_execution import _execution

  original = _execution()
  with pytest.raises(ValueError, match="requires a model"):
    replace(original, scorer_mode="ACTIVE")
  with pytest.raises(ValueError, match="FIELDS_INVALID"):
    replace(original, scorer_mode="ACTIVE", model_runtime_binding={"binding_hash": "a" * 64})
  model = binding().to_dict()
  execution = replace(original, scorer_mode="ACTIVE", model_runtime_binding=model)
  model["binding_hash"] = "d" * 64
  assert execution.model_runtime_binding["binding_hash"] != model["binding_hash"]
