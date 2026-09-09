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
from quantx_infrastructure.training_host_guard import (
  HostAdmissionDenied,
  high_resource_guard,
)


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    prog="quantx-research",
    description="QuantX 离线只读指标、概率模型与事件研究",
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
    "--resume-run-dir",
    type=Path,
    help=(
      "仅用于 indicator-study：从失败运行的已核验冻结样本和逐报告检查点恢复，"
      "不重新读取行情"
    ),
  )
  run_parser.add_argument(
    "--market-data-archive",
    type=Path,
    help="从已验证 QMT 日线 archive 读取行情，不依赖 InfluxDB",
  )

  render_parser = subparsers.add_parser(
    "render", help="从已有结构化产物重新生成 HTML 报告"
  )
  render_parser.add_argument("--run-dir", type=Path, required=True)

  train_selection_parser = subparsers.add_parser(
    "train-next-day-selection",
    help="手工训练并评估次日开盘至收盘上涨概率模型",
  )
  train_selection_parser.add_argument("--config", type=Path, required=True)
  train_selection_parser.add_argument(
    "--run-kind",
    choices=("DEVELOPMENT", "FINAL_EVALUATION"),
    required=True,
    help="DEVELOPMENT 只做验证；FINAL_EVALUATION 只使用已锁定开发运行",
  )
  train_selection_parser.add_argument(
    "--dataset-dir",
    dest="dataset_directory",
    type=Path,
    required=True,
    help="quantx-research certify-next-day-selection-dataset 生成的目录",
  )
  train_selection_parser.add_argument("--output-root", type=Path)
  train_selection_parser.add_argument("--run-id")
  train_selection_parser.add_argument(
    "--parent-run-dir",
    type=Path,
    help="FINAL_EVALUATION 必须提供成功 DEVELOPMENT 运行目录",
  )
  for name, help_text in (
    ("--spec-hash", "数据库锁定的 spec 小写 SHA-256"),
    ("--coordinate-hash", "数据库锁定的 coordinate 小写 SHA-256"),
    ("--environment-requirement-hash", "数据库锁定的环境要求小写 SHA-256"),
  ):
    train_selection_parser.add_argument(name, required=True, help=help_text)
  train_selection_parser.add_argument(
    "--frozen-test-access-count", type=_non_negative_int, default=0
  )

  certify_parser = subparsers.add_parser(
    "certify-next-day-selection-dataset",
    help="从受审计数据构造一次不可变次日选股训练面板",
  )
  certify_parser.add_argument("--config", type=Path, required=True)
  certify_parser.add_argument("--dataset-version", required=True)
  certify_parser.add_argument("--market-data-archive", type=Path)
  certify_parser.add_argument("--output-root", type=Path)

  qualify_parser = subparsers.add_parser(
    "qualify-lightgbm-gpu",
    help="用认证黄金面板执行 LightGBM OpenCL CPU/GPU 资格验证",
  )
  qualify_parser.add_argument("--dataset-dir", type=Path, required=True)
  qualify_parser.add_argument(
    "--output",
    type=Path,
    help="资格证书输出路径；省略时使用默认 .runtime/research-gpu 路径",
  )
  qualify_parser.add_argument("--requirement-hash")
  qualify_parser.add_argument(
    "--build-evidence",
    type=Path,
    required=True,
    help="锁定的官方 Windows GPU wheel，或本机构建的 schema-v1 build evidence JSON",
  )

  probe_parser = subparsers.add_parser(
    "probe-lightgbm-gpu",
    help=argparse.SUPPRESS,
  )
  probe_parser.add_argument(
    "--json",
    action="store_true",
    help=argparse.SUPPRESS,
  )

  job_parser = subparsers.add_parser(
    "run-next-day-selection-job",
    help=argparse.SUPPRESS,
  )
  job_parser.add_argument("--request-file", type=Path, required=True)
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  args = build_parser().parse_args(argv)
  if args.command == "render":
    return _dispatch(args)
  try:
    with high_resource_guard():
      return _dispatch(args)
  except HostAdmissionDenied as exc:
    print(f"主机训练门禁: {exc}", file=sys.stderr)
    return 75


def _dispatch(args: argparse.Namespace) -> int:
  from quantx_research.runner import (
    ResearchPreflightError,
    ResearchResourceError,
    render_existing,
    run_study,
    validate_study,
  )

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
          resume_run_dir=args.resume_run_dir,
        )
      )
      print(f"研究完成: {run_dir}")
      print(f"报告: {run_dir / 'report.html'}")
      return 0
    if args.command == "render":
      report = render_existing(args.run_dir)
      print(f"报告已重新生成: {report}")
      return 0
    if args.command == "train-next-day-selection":
      from quantx_research.next_day_selection_training import (
        train_next_day_selection,
      )

      if args.run_kind == "FINAL_EVALUATION" and args.parent_run_dir is None:
        raise ValueError("FINAL_EVALUATION 必须提供 --parent-run-dir")
      if args.run_kind == "DEVELOPMENT" and args.parent_run_dir is not None:
        raise ValueError("DEVELOPMENT 不接受 --parent-run-dir")

      run_dir = asyncio.run(
        train_next_day_selection(
          args.config,
          run_kind=args.run_kind,
          dataset_directory=args.dataset_directory,
          output_root=args.output_root,
          run_id=args.run_id,
          parent_run_directory=args.parent_run_dir,
          spec_hash=args.spec_hash,
          coordinate_hash=args.coordinate_hash,
          environment_requirement_hash=args.environment_requirement_hash,
          frozen_test_access_count=args.frozen_test_access_count,
        )
      )
      print(f"模型研究完成: {run_dir}")
      print(f"发布证据: {run_dir / 'manifest.json'}")
      return 0
    if args.command == "certify-next-day-selection-dataset":
      from quantx_research.next_day_selection_dataset import (
        certify_next_day_selection_dataset,
      )

      dataset_dir = asyncio.run(
        certify_next_day_selection_dataset(
          args.config,
          dataset_version=args.dataset_version,
          market_data_archive=args.market_data_archive,
          output_root=args.output_root,
        )
      )
      print(f"认证数据集已生成: {dataset_dir}")
      return 0
    if args.command == "qualify-lightgbm-gpu":
      from quantx_research.next_day_selection_gpu import qualify_lightgbm_gpu

      evidence = qualify_lightgbm_gpu(
        args.dataset_dir,
        args.output,
        requirement_hash=args.requirement_hash,
        build_evidence=args.build_evidence,
      )
      print(f"GPU 资格状态: {evidence['status']}")
      from quantx_research.next_day_selection_gpu import _default_qualification_path

      print(f"资格证据: {args.output or _default_qualification_path()}")
      return 0 if evidence["status"] == "GPU_AVAILABLE" else 2
    if args.command == "probe-lightgbm-gpu":
      from quantx_research.next_day_selection_gpu import probe_lightgbm_gpu

      print(
        json.dumps(
          probe_lightgbm_gpu(),
          ensure_ascii=False,
          sort_keys=True,
          separators=(",", ":"),
          allow_nan=False,
        )
      )
      return 0
    if args.command == "run-next-day-selection-job":
      from quantx_research.next_day_selection_job import main as run_job

      return int(run_job(["--request-file", str(args.request_file)]))
  except HostAdmissionDenied:
    raise
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


def _non_negative_int(value: str) -> int:
  parsed = int(value)
  if parsed < 0:
    raise argparse.ArgumentTypeError("必须是不小于 0 的整数")
  return parsed


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
