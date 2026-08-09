import ast
import importlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "apps" / "worker" / "src" / "quantx_worker"


def test_worker_source_never_imports_qmt_libraries() -> None:
  violations = {}
  for path in WORKER.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = set()
    for node in ast.walk(tree):
      if isinstance(node, ast.Import):
        imported.update(alias.name.split(".", 1)[0] for alias in node.names)
      elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module.split(".", 1)[0])
    forbidden = imported & {"miniqmt", "xtquant"}
    if forbidden:
      violations[str(path.relative_to(ROOT))] = sorted(forbidden)
  assert violations == {}


def test_worker_source_has_no_retired_qmt_compatibility_layer() -> None:
  forbidden_tokens = {
    "legacy_qmt_disabled",
    "XTDataManagerRegistry",
    "XTTradingManagerRegistry",
  }
  violations = {}
  for path in WORKER.rglob("*.py"):
    content = path.read_text(encoding="utf-8")
    matches = sorted(token for token in forbidden_tokens if token in content)
    if matches:
      violations[str(path.relative_to(ROOT))] = matches
  assert violations == {}


def test_prefect_deployment_entrypoints_exist_and_import() -> None:
  prefect_file = ROOT / "apps" / "worker" / "prefect.yaml"
  config = yaml.safe_load(prefect_file.read_text(encoding="utf-8"))
  deployments = config["deployments"]
  assert deployments
  assert len({item["name"] for item in deployments}) == len(deployments)

  for deployment in deployments:
    path_text, function_name = deployment["entrypoint"].split(":", 1)
    source_path = ROOT / path_text
    assert source_path.is_file(), deployment["entrypoint"]

    relative_module = source_path.relative_to(ROOT / "apps" / "worker" / "src")
    module_name = ".".join(relative_module.with_suffix("").parts)
    module = importlib.import_module(module_name)
    assert hasattr(module, function_name), deployment["entrypoint"]
