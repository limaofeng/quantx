import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _python_sources(root: Path):
  return root.rglob("*.py") if root.exists() else []


def test_runtime_apps_do_not_depend_on_research_package() -> None:
  runtime_apps = ["api", "engine", "worker", "qmt-agent"]
  offenders = []
  for app in runtime_apps:
    app_root = REPO_ROOT / "apps" / app
    for source in _python_sources(app_root):
      tree = ast.parse(source.read_text(encoding="utf-8"))
      imported = []
      for node in ast.walk(tree):
        if isinstance(node, ast.Import):
          imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
          imported.append(node.module or "")
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
          name = getattr(node.func, "id", getattr(node.func, "attr", ""))
          if name in {"__import__", "import_module"}:
            imported.append(str(node.args[0].value))
      # A subprocess module argument is an isolated protocol, not an import
      # into the API/Worker process. Keep enforcing actual import boundaries.
      if any(name == "quantx_research" or name.startswith("quantx_research.") for name in imported):
        offenders.append(source.relative_to(REPO_ROOT).as_posix())

  assert offenders == []


def test_research_app_does_not_import_runtime_or_qmt_sdks() -> None:
  forbidden = (
    "quantx_api",
    "quantx_engine",
    "quantx_worker",
    "quantx_qmt_agent",
    "xtquant",
    "miniqmt",
    "prefect",
  )
  offenders = []
  research_root = REPO_ROOT / "apps" / "research" / "src"
  for source in _python_sources(research_root):
    text = source.read_text(encoding="utf-8")
    matches = [name for name in forbidden if name in text]
    if matches:
      offenders.append((source.relative_to(REPO_ROOT).as_posix(), matches))

  assert offenders == []
