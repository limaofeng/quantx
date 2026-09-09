"""Bounded, hash-pinned JSON inference; no pickle, imports or executable payloads."""

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from math import exp, isfinite
from pathlib import Path

from quantx_application.t_trade_v3.model_features import (
  FEATURE_ORDER,
  FEATURE_SCHEMA_VERSION,
  TModelFeatureBar,
)
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

CLASS_ORDER = ("TARGET_FIRST", "STOP_FIRST", "NO_TOUCH")
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


def _numbers(values, count):
  if (
    not isinstance(values, (list, tuple))
    or len(values) != count
    or any(type(v) not in (int, float) or not isfinite(v) for v in values)
  ):
    raise ValueError("T_MODEL_NUMERIC_SHAPE_INVALID")
  return tuple(float(v) for v in values)


def _object(pairs):
  value = {}
  for key, item in pairs:
    if key in value:
      raise ValueError("T_MODEL_DUPLICATE_JSON_KEY")
    value[key] = item
  return value


@dataclass(frozen=True)
class TModelProbability:
  source_feature_bar_id: str
  source_bar_end_ms: int
  model_as_of_ms: int
  artifact_sha256: str
  model_id: str
  model_version: str
  label_spec_version: str
  horizon_ms: int
  probabilities: tuple[float, float, float]
  out_of_distribution_status: str


@dataclass(frozen=True)
class TCpuArtifact:
  sha256: str
  model_id: str
  model_version: str
  model_type: str
  label_spec_version: str
  horizon_ms: int
  capability_manifest_version: str
  training_cutoff_ms: int
  policy_compatibility_hash: str
  calibration_version: str
  temperature: float
  offsets: tuple[float, ...]
  scales: tuple[float, ...]
  feature_bounds: tuple[tuple[float, float], ...]
  intercepts: tuple[float, ...]
  coefficients: tuple[tuple[float, ...], ...]
  trees: tuple[tuple[int, tuple[tuple, ...]], ...]

  def score(self, bar: TModelFeatureBar, *, model_as_of_ms: int):
    if (
      bar.status != "COMPLETE"
      or bar.feature_coverage != 1.0
      or bar.feature_schema_version != FEATURE_SCHEMA_VERSION
      or bar.capability_manifest_version != self.capability_manifest_version
      or self.training_cutoff_ms >= bar.interval_start_ms
      or type(model_as_of_ms) is not int
      or model_as_of_ms < bar.available_at_ms
      or bar.available_at_ms < bar.interval_end_ms
    ):
      raise ValueError("T_MODEL_FEATURE_BINDING_INVALID")
    values = _numbers(bar.feature_values, len(FEATURE_ORDER))
    expected = stable_manifest_hash(
      {
        "schema": FEATURE_SCHEMA_VERSION,
        "feature_order": FEATURE_ORDER,
        "values": values,
      }
    )
    if expected != bar.feature_vector_hash:
      raise ValueError("T_MODEL_FEATURE_HASH_INVALID")
    ood = any(
      not low <= v <= high
      for v, (low, high) in zip(values, self.feature_bounds, strict=True)
    )
    features = tuple(
      (v - o) / s for v, o, s in zip(values, self.offsets, self.scales, strict=True)
    )
    if any(not isfinite(value) for value in features):
      raise ValueError("T_MODEL_NONFINITE_FEATURE_TRANSFORM")
    logits = list(self.intercepts)
    if self.model_type == "LOGISTIC":
      for k in range(3):
        logits[k] += sum(
          w * x for w, x in zip(self.coefficients[k], features, strict=True)
        )
    else:
      for k, nodes in self.trees:
        index = 0
        while len(nodes[index]) != 1:
          feature, threshold, left, right = nodes[index]
          index = left if features[feature] <= threshold else right
        logits[k] += nodes[index][0]
    if any(not isfinite(x) for x in logits):
      raise ValueError("T_MODEL_NONFINITE_OUTPUT")
    maximum = max(logits)
    weights = tuple(exp((x - maximum) / self.temperature) for x in logits)
    total = sum(weights)
    probabilities = tuple(x / total for x in weights)
    return TModelProbability(
      bar.feature_bar_id,
      bar.interval_end_ms,
      model_as_of_ms,
      self.sha256,
      self.model_id,
      self.model_version,
      self.label_spec_version,
      self.horizon_ms,
      probabilities,
      "BLOCK" if ood else "IN_DISTRIBUTION",
    )


def load_cpu_artifact(path: Path, *, expected_sha256: str) -> TCpuArtifact:
  if (
    not isinstance(expected_sha256, str)
    or len(expected_sha256) != 64
    or any(c not in "0123456789abcdef" for c in expected_sha256)
  ):
    raise ValueError("T_MODEL_ARTIFACT_HASH_REQUIRED")
  if path.absolute() != path.resolve(strict=True):
    raise ValueError("T_MODEL_SYMBOLIC_PATH_FORBIDDEN")
  descriptor = os.open(
    path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
  )
  with os.fdopen(descriptor, "rb") as handle:
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
      raise ValueError("T_MODEL_REGULAR_FILE_REQUIRED")
    raw = handle.read(MAX_ARTIFACT_BYTES + 1)
  if (
    len(raw) > MAX_ARTIFACT_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256
  ):
    raise ValueError("T_MODEL_ARTIFACT_HASH_OR_SIZE_INVALID")
  try:
    data = json.loads(
      raw,
      object_pairs_hook=_object,
      parse_constant=lambda _: (_ for _ in ()).throw(
        ValueError("T_MODEL_NONFINITE_JSON")
      ),
    )
  except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
    raise ValueError("T_MODEL_JSON_INVALID") from exc
  common = {
    "format",
    "model_id",
    "model_version",
    "model_type",
    "label_spec_version",
    "horizon_ms",
    "feature_order",
    "feature_schema_version",
    "class_order",
    "capability_manifest_version",
    "training_cutoff_ms",
    "policy_compatibility_hash",
    "calibration_version",
    "temperature",
    "offsets",
    "scales",
    "feature_bounds",
    "intercepts",
  }
  if not isinstance(data, dict) or data.get("model_type") not in {
    "LOGISTIC",
    "LIGHTGBM",
  }:
    raise ValueError("T_MODEL_TYPE_NOT_ALLOWED")
  extra = "coefficients" if data["model_type"] == "LOGISTIC" else "trees"
  if (
    set(data) != common | {extra}
    or data["format"] != "quantx-t-cpu-json.v1"
    or data["feature_order"] != list(FEATURE_ORDER)
    or data["class_order"] != list(CLASS_ORDER)
    or type(data["feature_schema_version"]) is not int
    or data["feature_schema_version"] != FEATURE_SCHEMA_VERSION
  ):
    raise ValueError("T_MODEL_MANIFEST_INVALID")
  for key in (
    "model_id",
    "model_version",
    "label_spec_version",
    "capability_manifest_version",
    "policy_compatibility_hash",
    "calibration_version",
  ):
    if not isinstance(data[key], str) or not data[key].strip():
      raise ValueError("T_MODEL_MANIFEST_IDENTITY_INVALID")
  if any(
    type(data[k]) is not int or data[k] <= 0
    for k in ("horizon_ms", "training_cutoff_ms")
  ):
    raise ValueError("T_MODEL_MANIFEST_TIME_INVALID")
  temperature = _numbers([data["temperature"]], 1)[0]
  if temperature <= 0:
    raise ValueError("T_MODEL_CALIBRATION_INVALID")
  n = len(FEATURE_ORDER)
  offsets, scales = _numbers(data["offsets"], n), _numbers(data["scales"], n)
  if (
    any(s <= 0 for s in scales)
    or not isinstance(data["feature_bounds"], list)
    or len(data["feature_bounds"]) != n
  ):
    raise ValueError("T_MODEL_SCALING_INVALID")
  bounds = tuple(_numbers(pair, 2) for pair in data["feature_bounds"])
  if any(low > high for low, high in bounds):
    raise ValueError("T_MODEL_OOD_BOUNDS_INVALID")
  intercepts = _numbers(data["intercepts"], 3)
  coefficients, trees = (), []
  if extra == "coefficients":
    if not isinstance(data[extra], list) or len(data[extra]) != 3:
      raise ValueError("T_MODEL_COEFFICIENTS_INVALID")
    coefficients = tuple(_numbers(row, n) for row in data[extra])
  else:
    if not isinstance(data[extra], list) or not 1 <= len(data[extra]) <= 10_000:
      raise ValueError("T_MODEL_TREE_COUNT_INVALID")
    total_nodes = 0
    for tree in data[extra]:
      if (
        not isinstance(tree, dict)
        or set(tree) != {"class_index", "nodes"}
        or type(tree["class_index"]) is not int
        or not 0 <= tree["class_index"] < 3
      ):
        raise ValueError("T_MODEL_TREE_INVALID")
      nodes = tree["nodes"]
      if not isinstance(nodes, list) or not nodes:
        raise ValueError("T_MODEL_TREE_INVALID")
      total_nodes += len(nodes)
      if total_nodes > 100_000:
        raise ValueError("T_MODEL_TREE_SIZE_EXCEEDED")
      parsed, parents, depths = [], [0] * len(nodes), [0] * len(nodes)
      for index, node in enumerate(nodes):
        if isinstance(node, list) and len(node) == 1:
          parsed.append(_numbers(node, 1))
          continue
        if not isinstance(node, list) or len(node) != 4:
          raise ValueError("T_MODEL_TREE_NODE_INVALID")
        feature, threshold, left, right = node
        _numbers([threshold], 1)
        if (
          type(feature) is not int
          or not 0 <= feature < n
          or any(
            type(c) is not int or not index < c < len(nodes) for c in (left, right)
          )
        ):
          raise ValueError("T_MODEL_TREE_NODE_INVALID")
        for child in (left, right):
          parents[child] += 1
          depths[child] = depths[index] + 1
          if depths[child] > 64:
            raise ValueError("T_MODEL_TREE_DEPTH_EXCEEDED")
        parsed.append((feature, float(threshold), left, right))
      if parents[0] or any(count != 1 for count in parents[1:]):
        raise ValueError("T_MODEL_TREE_TOPOLOGY_INVALID")
      trees.append((tree["class_index"], tuple(parsed)))
  return TCpuArtifact(
    expected_sha256,
    data["model_id"],
    data["model_version"],
    data["model_type"],
    data["label_spec_version"],
    data["horizon_ms"],
    data["capability_manifest_version"],
    data["training_cutoff_ms"],
    data["policy_compatibility_hash"],
    data["calibration_version"],
    temperature,
    offsets,
    scales,
    bounds,
    intercepts,
    coefficients,
    tuple(trees),
  )


def load_registered_cpu_artifact(root: Path, entry: dict) -> TCpuArtifact:
  """Resolve only an explicit manifest entry under the configured artifact root."""
  if not isinstance(entry, dict) or set(entry) != {"relative_path", "sha256", "bytes"}:
    raise ValueError("T_MODEL_ARTIFACT_INDEX_INVALID")
  relative = entry["relative_path"]
  if (
    not isinstance(relative, str)
    or not relative
    or "\\" in relative
    or Path(relative).is_absolute()
    or ".." in Path(relative).parts
    or type(entry["bytes"]) is not int
    or not 0 < entry["bytes"] <= MAX_ARTIFACT_BYTES
  ):
    raise ValueError("T_MODEL_ARTIFACT_PATH_INVALID")
  root = root.resolve(strict=True)
  path = root / relative
  if path.absolute() != path.resolve(strict=True) or not path.resolve().is_relative_to(
    root
  ):
    raise ValueError("T_MODEL_SYMBOLIC_PATH_FORBIDDEN")
  if path.stat().st_size != entry["bytes"]:
    raise ValueError("T_MODEL_ARTIFACT_SIZE_MISMATCH")
  return load_cpu_artifact(path, expected_sha256=entry["sha256"])
