import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLICATION = (
  ROOT / "packages" / "application" / "src" / "quantx_application"
)
FORBIDDEN = {
  "database",
  "fastapi",
  "miniqmt",
  "prefect",
  "quantx_infrastructure",
  "repositories",
  "sqlalchemy",
  "strawberry",
  "xtquant",
}


def test_application_does_not_depend_on_adapters() -> None:
  violations = {}
  for path in APPLICATION.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = set()
    for node in ast.walk(tree):
      if isinstance(node, ast.Import):
        imported.update(alias.name.split(".", 1)[0] for alias in node.names)
      elif isinstance(node, ast.ImportFrom) and node.module:
        imported.add(node.module.split(".", 1)[0])
    if imported & FORBIDDEN:
      violations[str(path.relative_to(ROOT))] = sorted(imported & FORBIDDEN)
  assert violations == {}
