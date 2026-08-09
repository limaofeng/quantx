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
      if "quantx_research" in source.read_text(encoding="utf-8"):
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
