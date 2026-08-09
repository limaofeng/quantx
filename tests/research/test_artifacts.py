from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from quantx_research.artifacts import (
  artifact_index,
  create_run_directory,
  directory_fingerprint,
  fingerprint,
  json_value,
  write_json,
)


def test_fingerprint_is_order_independent_and_sanitizes_non_finite() -> None:
  first = {"b": np.float64(float("nan")), "a": [np.int64(2)]}
  second = {"a": [2], "b": None}

  assert json_value(first) == {"a": [2], "b": None}
  assert fingerprint(first) == fingerprint(second)


def test_run_directory_and_artifact_index_are_reproducible(tmp_path: Path) -> None:
  run_dir = create_run_directory(
    tmp_path,
    "volume-shock",
    "v1",
    "a" * 64,
    now=datetime(2026, 7, 29, 8, 30, tzinfo=timezone.utc),
  )
  write_json(run_dir / "metrics.json", {"events": 3})
  write_json(run_dir / "manifest.json", {"status": "success"})

  assert run_dir.name == "20260729-163000-aaaaaaaa"
  assert artifact_index(run_dir) == [
    {
      "path": "metrics.json",
      "bytes": (run_dir / "metrics.json").stat().st_size,
      "sha256": fingerprint_for_file(run_dir / "metrics.json"),
    }
  ]


def test_directory_fingerprint_tracks_source_contents_not_generated_files(
  tmp_path: Path,
) -> None:
  source = tmp_path / "source"
  source.mkdir()
  (source / "study.py").write_text("VALUE = 1\n", encoding="utf-8")
  first = directory_fingerprint(source)
  generated = source / "__pycache__"
  generated.mkdir()
  (generated / "study.pyc").write_bytes(b"ignored")

  assert directory_fingerprint(source) == first
  (source / "study.py").write_text("VALUE = 2\n", encoding="utf-8")
  assert directory_fingerprint(source) != first


def fingerprint_for_file(path: Path) -> str:
  from quantx_research.artifacts import file_sha256

  return file_sha256(path)
