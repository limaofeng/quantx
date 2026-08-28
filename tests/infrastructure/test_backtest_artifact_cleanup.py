import json
from pathlib import Path

from quantx_infrastructure.core.backtest_artifact_cleanup import (
  delete_backtest_artifacts,
)


def test_delete_backtest_artifacts_removes_manifest_files_and_performance(
  tmp_path: Path,
  monkeypatch,
) -> None:
  monkeypatch.chdir(tmp_path)
  base_dir = tmp_path / "data" / "backtests" / "run-1" / "v1"
  base_dir.mkdir(parents=True)
  artifact_path = base_dir / "execution_logs.jsonl"
  artifact_path.write_text("{}\n", encoding="utf-8")
  manifest_path = base_dir / "manifest.json"
  manifest_path.write_text(
    json.dumps({"artifacts": {"logs": {"path": artifact_path.name}}}),
    encoding="utf-8",
  )
  performance_path = tmp_path / "data" / "backtests" / "performance" / "backtest-1.json"
  performance_path.parent.mkdir(parents=True)
  performance_path.write_text("{}", encoding="utf-8")

  deleted = delete_backtest_artifacts(
    "backtest-1",
    "backtests/run-1/v1/manifest.json",
  )

  assert set(deleted) == {
    str(manifest_path.resolve()),
    str(artifact_path.resolve()),
    str(performance_path.resolve()),
  }
  assert not manifest_path.exists()
  assert not artifact_path.exists()
  assert not performance_path.exists()


def test_delete_backtest_artifacts_never_deletes_outside_backtest_root(
  tmp_path: Path,
  monkeypatch,
) -> None:
  monkeypatch.chdir(tmp_path)
  outside_path = tmp_path / "do-not-delete.json"
  outside_path.write_text("{}", encoding="utf-8")

  deleted = delete_backtest_artifacts("backtest-2", str(outside_path))

  assert deleted == []
  assert outside_path.exists()
