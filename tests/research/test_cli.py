from __future__ import annotations

from pathlib import Path

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
  assert args.market_data_archive == Path(
    ".runtime/research-source/full-a-share"
  )
