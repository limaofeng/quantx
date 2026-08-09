import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "packages" / "domain" / "src" / "quantx_domain"
FORBIDDEN = {
  "aiofiles",
  "asyncpg",
  "database",
  "fastapi",
  "httpx",
  "miniqmt",
  "prefect",
  "quantx_infrastructure",
  "redis",
  "repositories",
  "sqlalchemy",
  "strawberry",
  "xtquant",
}


def _imports(path: Path) -> set[str]:
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  roots: set[str] = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      roots.update(alias.name.split(".", 1)[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      roots.add(node.module.split(".", 1)[0])
  return roots


def test_domain_has_no_io_or_framework_dependencies() -> None:
  violations = {
    str(path.relative_to(ROOT)): sorted(_imports(path) & FORBIDDEN)
    for path in DOMAIN.rglob("*.py")
    if _imports(path) & FORBIDDEN
  }
  assert violations == {}
