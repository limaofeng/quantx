from __future__ import annotations

from pathlib import Path

import pytest
from quantx_research.cli import (
  _summarize_validation_for_console,
  build_parser,
)


def test_validation_console_summary_does_not_mutate_full_factor_evidence() -> None:
  result = {
    "valid": False,
    "data_quality": {
      "dividend_factor_coverage": {
        "requested_codes": ["000001.SZ", "000002.SZ", "000300.SH"],
        "covered_codes": [],
        "uncovered_codes": ["000001.SZ", "000002.SZ", "000300.SH"],
        "is_complete": False,
      }
    },
  }

  summarized = _summarize_validation_for_console(result, preview_size=2)
  coverage = summarized["data_quality"]["dividend_factor_coverage"]

  assert coverage["requested_codes_count"] == 3
  assert coverage["requested_codes_preview"] == ["000001.SZ", "000002.SZ"]
  assert coverage["covered_codes_count"] == 0
  assert coverage["covered_codes_preview"] == []
  assert coverage["uncovered_codes_count"] == 3
  assert coverage["uncovered_codes_preview"] == ["000001.SZ", "000002.SZ"]
  assert "requested_codes" not in coverage
  assert result["data_quality"]["dividend_factor_coverage"]["requested_codes"] == [
    "000001.SZ",
    "000002.SZ",
    "000300.SH",
  ]


def test_run_cli_accepts_explicit_qmt_market_data_archive() -> None:
  args = build_parser().parse_args(
    [
      "run",
      "--config",
      "study.yaml",
      "--market-data-archive",
      ".runtime/research-source/full-a-share",
    ]
  )

  assert args.config == Path("study.yaml")
  assert args.market_data_archive == Path(".runtime/research-source/full-a-share")


def test_run_cli_accepts_indicator_resume_directory() -> None:
  args = build_parser().parse_args(
    [
      "run",
      "--config",
      "indicator-study.yaml",
      "--resume-run-dir",
      ".runtime/research-runs/indicator-study-v1/failed-run",
    ]
  )

  assert args.config == Path("indicator-study.yaml")
  assert args.resume_run_dir == Path(
    ".runtime/research-runs/indicator-study-v1/failed-run"
  )


@pytest.mark.parametrize(
  "argv",
  [
    ["run", "--config", "study.yaml"],
    ["validate", "--config", "study.yaml"],
    [
      "certify-next-day-selection-dataset",
      "--config",
      "study.yaml",
      "--dataset-version",
      "v1",
    ],
    [
      "qualify-lightgbm-gpu",
      "--dataset-dir",
      "dataset",
      "--build-evidence",
      "build.json",
    ],
    ["probe-lightgbm-gpu", "--json"],
    ["run-next-day-selection-job", "--request-file", "request.json"],
    [
      "train-next-day-selection",
      "--config",
      "study.yaml",
      "--run-kind",
      "DEVELOPMENT",
      "--dataset-dir",
      "dataset",
      "--spec-hash",
      "s",
      "--coordinate-hash",
      "c",
      "--environment-requirement-hash",
      "e",
    ],
  ],
)
def test_high_resource_cli_cannot_dispatch_without_host_admission(monkeypatch, argv):
  from quantx_research import cli

  def denied():
    raise cli.HostAdmissionDenied("HOST_POLICY_MISSING_OR_INVALID")

  monkeypatch.setattr(cli, "high_resource_guard", denied)
  monkeypatch.setattr(
    cli, "_dispatch", lambda args: pytest.fail("computation started before admission")
  )
  assert cli.main(argv) == 75


def test_direct_job_and_preparation_entrypoints_cannot_bypass_guard(monkeypatch):
  from quantx_infrastructure import training_host_guard
  from quantx_research import next_day_selection_job as job
  from quantx_research import preparation_job as preparation

  def denied():
    raise training_host_guard.HostAdmissionDenied("HOST_POLICY_MISSING_OR_INVALID")

  monkeypatch.setattr(job, "high_resource_guard", denied)
  monkeypatch.setattr(training_host_guard, "high_resource_guard", denied)
  monkeypatch.setattr(job, "_execute_job", lambda path: pytest.fail("job bypass"))
  monkeypatch.setattr(
    preparation, "_execute_main", lambda: pytest.fail("preparation bypass")
  )
  assert job.main(["--request-file", "missing.json"]) == 75
  with pytest.raises(SystemExit) as stopped:
    preparation.main()
  assert stopped.value.code == 75
