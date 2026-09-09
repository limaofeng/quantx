"""CPU numerical parity on tiny synthetic fixtures, never a research trial."""

import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest
from quantx_application.t_trade_v3.model_features import FEATURE_ORDER
from quantx_infrastructure.services.t_model_cpu_artifact import (
  CLASS_ORDER,
  load_cpu_artifact,
)
from quantx_research.t_model_export import (
  export_model_payload,
  publish_immutable_cpu_artifact,
)

from tests.research.test_t_assistant_model_data import START, bar


def manifest(kind="LOGISTIC"):
  return dict(
    format="quantx-t-cpu-json.v1",
    model_id="fixture",
    model_version="1",
    model_type=kind,
    label_spec_version="fixture-only",
    horizon_ms=3000,
    feature_order=list(FEATURE_ORDER),
    feature_schema_version=1,
    class_order=list(CLASS_ORDER),
    capability_manifest_version="quotes-v1",
    training_cutoff_ms=START - 1,
    policy_compatibility_hash="a" * 64,
    calibration_version="fixture-temperature",
    temperature=1.0,
    offsets=[0.0] * 6,
    scales=[1.0] * 6,
    feature_bounds=[[-10, 10]] * 6,
    intercepts=[0.0] * 3,
  )


def payload():
  return {**manifest(), "coefficients": [[0.0] * 6] * 3}


def test_logistic_safe_export_matches_fitted_multiclass_model(tmp_path):
  from sklearn.linear_model import LogisticRegression

  rng = np.random.default_rng(3)
  x = rng.normal(size=(60, 6))
  labels = np.array(CLASS_ORDER)[np.arange(60) % 3]
  model = LogisticRegression().fit(x, labels)
  exported = export_model_payload(
    model, manifest=manifest(), training_class_order=tuple(model.classes_)
  )
  loaded = publish_immutable_cpu_artifact(tmp_path / "model.json", exported)
  feature = bar()
  expected = model.predict_proba([feature.feature_values])[0]
  expected = [expected[list(model.classes_).index(name)] for name in CLASS_ORDER]
  assert loaded.score(
    feature, model_as_of_ms=feature.available_at_ms
  ).probabilities == pytest.approx(expected, abs=1e-12)


def test_lightgbm_safe_export_matches_native_cpu_inference(tmp_path):
  import lightgbm as lgb

  rng = np.random.default_rng(4)
  x, y = rng.normal(size=(60, 6)), np.arange(60) % 3
  model = lgb.train(
    dict(
      objective="multiclass",
      num_class=3,
      num_threads=1,
      seed=4,
      min_data_in_leaf=2,
      verbosity=-1,
    ),
    lgb.Dataset(x, label=y, feature_name=list(FEATURE_ORDER)),
    num_boost_round=2,
  )
  order = ("NO_TOUCH", "TARGET_FIRST", "STOP_FIRST")
  exported = export_model_payload(
    model, manifest=manifest("LIGHTGBM"), training_class_order=order
  )
  loaded = publish_immutable_cpu_artifact(tmp_path / "model.json", exported)
  feature = bar()
  expected = model.predict(np.array([feature.feature_values]))[0]
  assert loaded.score(
    feature, model_as_of_ms=feature.available_at_ms
  ).probabilities == pytest.approx(
    [expected[order.index(name)] for name in CLASS_ORDER], abs=1e-12
  )


def test_publication_is_immutable_and_hash_pinned(tmp_path):
  destination = tmp_path / "model.json"
  loaded = publish_immutable_cpu_artifact(destination, payload())
  with pytest.raises(FileExistsError):
    publish_immutable_cpu_artifact(destination, payload())
  assert not list(tmp_path.glob(".t-model-*"))
  destination.write_bytes(b"tampered")
  with pytest.raises(ValueError, match="HASH_OR_SIZE"):
    load_cpu_artifact(destination, expected_sha256=loaded.sha256)


@pytest.mark.parametrize(
  "damage",
  ["pickle", "feature_order", "scale", "nan", "duplicate", "tree_cycle", "unreachable"],
)
def test_unsafe_or_malformed_artifacts_are_rejected(tmp_path, damage):
  data = payload()
  if damage == "pickle":
    data["model_type"] = "PICKLE"
  if damage == "feature_order":
    data["feature_order"] = list(reversed(FEATURE_ORDER))
  if damage == "scale":
    data["scales"][0] = 0
  if damage == "nan":
    data["temperature"] = float("nan")
  if damage in {"tree_cycle", "unreachable"}:
    data = {
      **manifest("LIGHTGBM"),
      "trees": [
        {
          "class_index": 0,
          "nodes": [[0, 0.0, 0, 1], [1.0]]
          if damage == "tree_cycle"
          else [[1.0], [2.0]],
        }
      ],
    }
  raw = json.dumps(data).encode()
  if damage == "duplicate":
    raw = raw[:-1] + b',"model_type":"LOGISTIC"}'
  path = tmp_path / "bad.json"
  path.write_bytes(raw)
  with pytest.raises(ValueError):
    load_cpu_artifact(path, expected_sha256=hashlib.sha256(raw).hexdigest())


def test_future_binding_ood_and_feature_tampering(tmp_path):
  data = payload()
  data["feature_bounds"] = [[0, 0]] * 6
  loaded = publish_immutable_cpu_artifact(tmp_path / "model.json", data)
  feature = bar()
  assert (
    loaded.score(
      feature, model_as_of_ms=feature.available_at_ms
    ).out_of_distribution_status
    == "BLOCK"
  )
  with pytest.raises(ValueError, match="FEATURE_HASH"):
    loaded.score(
      replace(feature, feature_values=(1.0,) * 6),
      model_as_of_ms=feature.available_at_ms,
    )
  with pytest.raises(ValueError, match="BINDING"):
    loaded.score(feature, model_as_of_ms=feature.interval_start_ms)


@pytest.mark.parametrize("damage", ["traversal", "symbolic", "size", "absolute"])
def test_registry_index_confines_reads_to_regular_manifest_files(tmp_path, damage):
  from quantx_infrastructure.services.t_model_cpu_artifact import (
    load_registered_cpu_artifact,
  )

  destination = tmp_path / "model.json"
  loaded = publish_immutable_cpu_artifact(destination, payload())
  entry = dict(
    relative_path="model.json", sha256=loaded.sha256, bytes=destination.stat().st_size
  )
  assert load_registered_cpu_artifact(tmp_path, entry).sha256 == loaded.sha256
  if damage == "traversal":
    entry["relative_path"] = "../model.json"
  if damage == "size":
    entry["bytes"] += 1
  if damage == "absolute":
    entry["relative_path"] = str(destination)
  if damage == "symbolic":
    (tmp_path / "link.json").symlink_to(destination)
    entry["relative_path"] = "link.json"
  with pytest.raises(ValueError):
    load_registered_cpu_artifact(tmp_path, entry)


def test_zero_as_missing_tree_export_is_rejected():
  class Model:
    def dump_model(self):
      return dict(
        num_class=3,
        num_tree_per_iteration=3,
        objective="multiclass num_class:3",
        average_output=False,
        feature_names=list(FEATURE_ORDER),
        tree_info=[
          dict(
            tree_index=0, tree_structure=dict(decision_type="<=", missing_type="Zero")
          )
        ],
      )

  with pytest.raises(ValueError, match="SPLIT_UNSUPPORTED"):
    export_model_payload(
      Model(), manifest=manifest("LIGHTGBM"), training_class_order=CLASS_ORDER
    )


def test_overflowing_feature_transform_is_rejected(tmp_path):
  data = payload()
  data["scales"] = [1e-320] * 6
  loaded = publish_immutable_cpu_artifact(tmp_path / "overflow.json", data)
  feature = bar()
  with pytest.raises(ValueError, match="NONFINITE_FEATURE_TRANSFORM"):
    loaded.score(feature, model_as_of_ms=feature.available_at_ms)
