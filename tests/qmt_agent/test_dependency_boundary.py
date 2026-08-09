import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "apps" / "qmt-agent" / "src" / "quantx_qmt_agent"
FORBIDDEN = {
  "auth",
  "core",
  "database",
  "gqlapi",
  "models",
  "quantx_application",
  "quantx_api",
  "quantx_domain",
  "quantx_engine",
  "quantx_infrastructure",
  "quantx_worker",
  "repositories",
  "services",
}


def test_qmt_agent_does_not_import_server_or_strategy_packages() -> None:
  violations = {}
  for path in SOURCE.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots = set()
    for node in ast.walk(tree):
      if isinstance(node, ast.Import):
        roots.update(alias.name.split(".", 1)[0] for alias in node.names)
      elif isinstance(node, ast.ImportFrom) and node.module:
        roots.add(node.module.split(".", 1)[0])
    forbidden = sorted(roots & FORBIDDEN)
    if forbidden:
      violations[str(path.relative_to(ROOT))] = forbidden
  assert violations == {}
