import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / "apps" / "api" / "src" / "quantx_api" / "main.py"
SERVER_SOURCE_ROOTS = [
  ROOT / "apps" / "api",
  ROOT / "apps" / "engine" / "src",
  ROOT / "apps" / "worker" / "src",
]


def test_api_lifespan_does_not_start_engine_prefect_or_qmt() -> None:
  tree = ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))
  imported = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      imported.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      imported.add(node.module)
  forbidden = [
    name
    for name in imported
    if (
      name.startswith("prefect")
      or name.startswith("miniqmt")
      or name.startswith("xtquant")
      or name.startswith("quantx_engine")
      or name.startswith("quantx_worker")
      or name.startswith("quantx_qmt_agent")
    )
  ]
  assert forbidden == []


def test_server_source_never_imports_qmt_libraries() -> None:
  violations = {}
  for source_root in SERVER_SOURCE_ROOTS:
    for path in source_root.rglob("*.py"):
      if "tests" in path.parts:
        continue
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
