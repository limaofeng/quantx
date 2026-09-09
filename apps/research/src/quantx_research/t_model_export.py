"""Export fitted baselines to bounded CPU JSON, without serializing Python objects."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from quantx_application.t_trade_v3.model_features import FEATURE_ORDER
from quantx_infrastructure.services.t_model_cpu_artifact import (
  CLASS_ORDER,
  load_cpu_artifact,
)


def export_model_payload(
  model, *, manifest: dict, training_class_order: tuple[str, ...]
):
  """Only fitted Logistic / numeric multiclass LightGBM; no training is started."""
  if len(training_class_order) != 3 or set(training_class_order) != set(CLASS_ORDER):
    raise ValueError("T_MODEL_EXPORT_CLASS_ORDER_INVALID")
  result = dict(manifest)
  if result.get("model_type") == "LOGISTIC":
    coefficients = model.coef_.tolist()
    intercepts = model.intercept_.tolist()
    if (
      len(coefficients) != 3
      or len(intercepts) != 3
      or tuple(model.classes_) != training_class_order
    ):
      raise ValueError("T_MODEL_EXPORT_LOGISTIC_SHAPE_INVALID")
    result["coefficients"] = [
      coefficients[training_class_order.index(name)] for name in CLASS_ORDER
    ]
    result["intercepts"] = [
      intercepts[training_class_order.index(name)] for name in CLASS_ORDER
    ]
  elif result.get("model_type") == "LIGHTGBM":
    dump = model.dump_model()
    if (
      dump.get("num_class") != 3
      or dump.get("num_tree_per_iteration") != 3
      or not str(dump.get("objective", "")).startswith("multiclass ")
      or dump.get("average_output") is not False
      or dump.get("feature_names") != list(FEATURE_ORDER)
    ):
      raise ValueError("T_MODEL_EXPORT_LIGHTGBM_UNSUPPORTED")
    trees = []
    for index, tree in enumerate(dump["tree_info"]):
      if index >= 10_000 or tree["tree_index"] != index:
        raise ValueError("T_MODEL_EXPORT_TREE_ORDER_INVALID")
      nodes = []

      def visit(node, depth=0):
        if depth > 64 or len(nodes) >= 100_000:
          raise ValueError("T_MODEL_EXPORT_TREE_SIZE_EXCEEDED")
        position = len(nodes)
        nodes.append(None)
        if "leaf_value" in node:
          nodes[position] = [node["leaf_value"]]
        else:
          if node.get("decision_type") != "<=" or node.get("missing_type") not in {
            "None",
            "NaN",
          }:
            raise ValueError("T_MODEL_EXPORT_CATEGORICAL_SPLIT_UNSUPPORTED")
          left, right = (
            visit(node["left_child"], depth + 1),
            visit(node["right_child"], depth + 1),
          )
          nodes[position] = [node["split_feature"], node["threshold"], left, right]
        return position

      visit(tree["tree_structure"])
      trees.append(
        {
          "class_index": CLASS_ORDER.index(training_class_order[index % 3]),
          "nodes": nodes,
        }
      )
    result["trees"] = trees
    result["intercepts"] = [0.0, 0.0, 0.0]
  else:
    raise ValueError("T_MODEL_EXPORT_TYPE_UNSUPPORTED")
  return result


def publish_immutable_cpu_artifact(path: Path, payload: dict):
  """Validate, fsync and publish once; an existing destination is never replaced."""
  raw = json.dumps(
    payload, sort_keys=True, separators=(",", ":"), allow_nan=False
  ).encode()
  digest = hashlib.sha256(raw).hexdigest()
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = None
  try:
    with tempfile.NamedTemporaryFile(
      dir=path.parent, prefix=".t-model-", suffix=".tmp", delete=False
    ) as handle:
      temporary = Path(handle.name)
      handle.write(raw)
      handle.flush()
      os.fsync(handle.fileno())
    loaded = load_cpu_artifact(temporary, expected_sha256=digest)
    os.link(temporary, path)
    return loaded
  finally:
    if temporary is not None:
      temporary.unlink(missing_ok=True)
