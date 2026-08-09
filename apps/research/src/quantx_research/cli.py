"""Command-line interface for QuantX offline research."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from quantx_research.runner import (
  ResearchPreflightError,
  ResearchResourceError,
  render_existing,
  run_study,
  validate_study,
)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="quantx-research",
    description="QuantX 离线只读因子与事件研究",
  )
  subparsers = parser.add_subparsers(dest="command", required=True)

  validate_parser = subparsers.add_parser(
    "validate", help="检查配置和已有数据是否足以执行研究"
  )
  validate_parser.add_argument("--config", type=Path, required=True)
  validate_parser.add_argument(
    "--market-data-archive",
    type=Path,
    help="从已验证 QMT 日线 archive 读取行情，不依赖 InfluxDB",
  )

  run_parser = subparsers.add_parser("run", help="执行研究并生成结构化结果与 HTML 报告")
  run_parser.add_argument("--config", type=Path, required=True)
  run_parser.add_argument("--output-root", type=Path)
  run_parser.add_argument(
    "--market-data-archive",
    type=Path,
    help="从已验证 QMT 日线 archive 读取行情，不依赖 InfluxDB",
  )

  render_parser = subparsers.add_parser(
    "render", help="从已有结构化产物重新生成 HTML 报告"
  )
  render_parser.add_argument("--run-dir", type=Path, required=True)
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  try:
    if args.command == "validate":
      result = asyncio.run(
        validate_study(
          args.config,
          market_data_archive=args.market_data_archive,
        )
      )
      print(
        json.dumps(
          _summarize_validation_for_console(result),
          ensure_ascii=False,
          indent=2,
          default=str,
        )
      )
      return 0 if result["valid"] else 2
    if args.command == "run":
      run_dir = asyncio.run(
        run_study(
          args.config,
          market_data_archive=args.market_data_archive,
          output_root=args.output_root,
        )
      )
      print(f"研究完成: {run_dir}")
      print(f"报告: {run_dir / 'report.html'}")
      return 0
    if args.command == "render":
      report = render_existing(args.run_dir)
      print(f"报告已重新生成: {report}")
      return 0
  except ResearchResourceError as exc:
    if exc.run_dir is not None:
      print(f"研究因资源保护停止，诊断产物: {exc.run_dir}", file=sys.stderr)
    print(str(exc), file=sys.stderr)
    return 1
  except ResearchPreflightError as exc:
    if exc.run_dir is not None:
      print(f"研究前置检查失败，诊断产物: {exc.run_dir}", file=sys.stderr)
    print(str(exc), file=sys.stderr)
    return 2
  except (OSError, ValueError, ValidationError) as exc:
    print(f"配置或文件错误: {exc}", file=sys.stderr)
    return 2
  except Exception as exc:
    print(f"研究运行失败: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1
  return 1


def _summarize_validation_for_console(
  result: dict[str, object],
  *,
  preview_size: int = 10,
) -> dict[str, object]:
  """Keep CLI validation readable without discarding structured evidence."""
  summarized = copy.deepcopy(result)
  data_quality = summarized.get("data_quality")
  if not isinstance(data_quality, dict):
    return summarized
  factor_coverage = data_quality.get("dividend_factor_coverage")
  if not isinstance(factor_coverage, dict):
    return summarized
  for key in ("requested_codes", "covered_codes", "uncovered_codes"):
    codes = factor_coverage.pop(key, None)
    if not isinstance(codes, (list, tuple)):
      continue
    factor_coverage[f"{key}_count"] = len(codes)
    factor_coverage[f"{key}_preview"] = list(codes[:preview_size])
  return summarized


if __name__ == "__main__":
  raise SystemExit(main())
