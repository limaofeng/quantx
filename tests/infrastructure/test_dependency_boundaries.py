import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INFRASTRUCTURE = (
  ROOT / "packages" / "infrastructure" / "src" / "quantx_infrastructure"
)
RUNTIME_APPS = {
  "engine": ROOT / "apps" / "engine" / "src" / "quantx_engine",
  "worker": ROOT / "apps" / "worker" / "src" / "quantx_worker",
}
API = ROOT / "apps" / "api" / "src" / "quantx_api"


def _import_roots(path: Path) -> set[str]:
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  roots: set[str] = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      roots.update(alias.name.split(".", 1)[0] for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      roots.add(node.module.split(".", 1)[0])
  return roots


def _imported_modules(path: Path) -> set[str]:
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  modules: set[str] = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      modules.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      modules.add(node.module)
  return modules


def test_infrastructure_never_depends_on_api_or_runtime_apps() -> None:
  forbidden = {
    "quantx_api",
    "quantx_engine",
    "quantx_qmt_agent",
    "quantx_worker",
  }
  violations = {
    str(path.relative_to(ROOT)): sorted(_import_roots(path) & forbidden)
    for path in INFRASTRUCTURE.rglob("*.py")
    if _import_roots(path) & forbidden
  }
  assert violations == {}


def test_engine_and_worker_do_not_import_api_package() -> None:
  violations = {}
  for app_name, source_root in RUNTIME_APPS.items():
    for path in source_root.rglob("*.py"):
      if "quantx_api" in _import_roots(path):
        violations[f"{app_name}:{path.relative_to(ROOT)}"] = ["quantx_api"]
  assert violations == {}


def test_api_does_not_import_engine_owned_runtime_singletons() -> None:
  forbidden = {
    "quantx_infrastructure.core.data.market_data_service",
    "quantx_engine.warm_cache",
    "quantx_engine.realtime_manager",
    "quantx_engine.strategy_manager",
    "quantx_engine.t_trade_global_monitor",
  }
  violations = {
    str(path.relative_to(ROOT)): sorted(_imported_modules(path) & forbidden)
    for path in API.rglob("*.py")
    if _imported_modules(path) & forbidden
  }
  assert violations == {}
