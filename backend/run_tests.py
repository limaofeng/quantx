#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QuantX API 测试运行器
提供便捷的测试执行和报告功能
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run_miniqmt_tests(args):
  """运行MiniQMT模块测试"""
  print("[INFO] 运行MiniQMT模块测试...")
  print("=" * 60)

  # 切换到tests目录
  tests_dir = Path(__file__).parent / "tests"
  if not tests_dir.exists():
    print("[ERROR] 错误: tests目录不存在")
    return 1

  # 构建pytest命令
  cmd = ["python", "-m", "pytest"]

  # 根据参数选择测试文件
  if hasattr(args, "module") and args.module:
    # 运行特定模块测试
    test_file = f"tests/integration/miniqmt/test_miniqmt_{args.module}.py"
    if not (
      tests_dir / "integration" / "miniqmt" / f"test_miniqmt_{args.module}.py"
    ).exists():
      print(f"[ERROR] 错误: 测试文件 {test_file} 不存在")
      return 1
    cmd.append(test_file)
    print(f"[INFO] 运行 {args.module} 模块测试")
  elif hasattr(args, "performance") and args.performance:
    # 运行性能测试
    cmd.extend(
      [
        "tests/integration/miniqmt/test_miniqmt_data.py::TestMiniQMTDataManager",
        "tests/integration/miniqmt/test_miniqmt_utils.py::TestMiniQMTUtils",
        "tests/integration/miniqmt/test_miniqmt_integration.py::TestMiniQMTIntegration",
      ]
    )
    cmd.append("--durations=0")
    print("[PERF] 运行MiniQMT性能测试")
  else:
    # 运行所有MiniQMT测试
    cmd.extend(
      [
        "tests/integration/miniqmt/test_miniqmt_config.py",
        "tests/integration/miniqmt/test_miniqmt_utils.py",
        "tests/integration/miniqmt/test_miniqmt_data.py",
        "tests/integration/miniqmt/test_miniqmt_trading.py",
        "tests/integration/miniqmt/test_miniqmt_integration.py",
      ]
    )
    print("[INFO] 运行所有MiniQMT测试")

  # 添加通用参数
  if args.verbose:
    cmd.append("-v")

  cmd.extend(["--tb=short", "--color=yes", "--disable-warnings"])

  # 添加覆盖率报告
  if not (hasattr(args, "performance") and args.performance):
    cmd.extend(["--cov=miniqmt", "--cov-report=term-missing"])

  print(f"[CMD] 执行命令: {' '.join(cmd)}")
  print("-" * 60)

  # 运行测试
  try:
    result = subprocess.run(cmd, check=False, cwd=tests_dir.parent)

    print("-" * 60)
    if result.returncode == 0:
      print("[SUCCESS] MiniQMT测试全部通过!")
    else:
      print("[FAILED] 部分MiniQMT测试失败")

    return result.returncode

  except KeyboardInterrupt:
    print("\n[ERROR] 测试被用户中断")
    return 1
  except Exception as e:
    print(f"[ERROR] 运行MiniQMT测试时出错: {e}")
    return 1


def run_prefect_tests(args):
  """运行Prefect模块测试"""
  print("[INFO] 运行Prefect模块测试...")
  print("=" * 60)

  # 切换到tests目录
  tests_dir = Path(__file__).parent / "tests"
  if not tests_dir.exists():
    print("[ERROR] 错误: tests目录不存在")
    return 1

  # 构建pytest命令
  cmd = ["python", "-m", "pytest"]

  # 根据参数选择测试文件
  if hasattr(args, "flow") and args.flow:
    # 运行特定流程测试
    flow_name = args.flow

    # 如果用户没有提供完整的流程文件名，尝试添加 _flow 后缀
    if not flow_name.endswith("_flow"):
      # 先尝试不带后缀的版本
      test_file_no_suffix = f"tests/integration/prefector/test_{flow_name}.py"
      if (tests_dir / "integration" / "prefector" / f"test_{flow_name}.py").exists():
        test_file = test_file_no_suffix
      else:
        # 如果不存在，尝试添加 _flow 后缀
        flow_name = f"{flow_name}_flow"
        test_file = f"tests/integration/prefector/test_{flow_name}.py"
    else:
      test_file = f"tests/integration/prefector/test_{flow_name}.py"

    if not (tests_dir / "integration" / "prefector" / f"test_{flow_name}.py").exists():
      print(f"[ERROR] 错误: 测试文件 {test_file} 不存在")
      return 1
    cmd.append(test_file)
    print(f"[INFO] 运行 {args.flow} 流程测试")
  elif hasattr(args, "unit") and args.unit:
    # 运行单元测试
    cmd.extend(
      [
        "tests/unit/prefector/test_bond_repo_flow.py",
        "tests/unit/prefector/test_daily_trading_sync_flow.py",
        "tests/unit/prefector/test_comprehensive_market_flow.py",
        "tests/unit/prefector/test_batch_stock_flow.py",
        "tests/unit/prefector/test_flow_error_handling.py",
        "tests/unit/prefector/test_flow_scheduling.py",
        "tests/unit/prefector/test_realtime_price_flow.py",
        "tests/unit/prefector/test_sector_data_flow.py",
        "tests/unit/prefector/test_single_stock_flow.py",
      ]
    )
    print("[INFO] 运行Prefect单元测试")
  elif hasattr(args, "integration") and args.integration:
    # 运行集成测试
    cmd.extend(
      [
        "tests/integration/prefector/test_bond_repo_flow.py",
        "tests/integration/prefector/test_flow_integration.py",
        "tests/integration/prefector/test_flow_scheduling.py",
        "tests/integration/prefector/test_flow_error_handling.py",
      ]
    )
    print("[INFO] 运行Prefect集成测试（使用Mock服务，安全测试）")
  elif hasattr(args, "e2e") and args.e2e:
    # 运行E2E测试（危险测试）
    cmd.extend(
      [
        "tests/e2e/prefector/test_bond_repo_real_trading.py",
      ]
    )
    cmd.extend(["-m", "e2e"])  # 只运行标记为e2e的测试
    print("[WARNING] ⚠️  运行E2E测试 - 可能执行真实交易操作！")
    print("[WARNING] 请确保在测试环境中运行，并设置 ENABLE_REAL_TRADING=true")
  else:
    # 运行所有Prefect测试
    cmd.extend(
      [
        "tests/integration/prefector/test_daily_market_data_sync_flow.py",
        "tests/integration/prefector/test_realtime_price_flow.py",
        "tests/integration/prefector/test_comprehensive_market_flow.py",
        "tests/integration/prefector/test_batch_stock_flow.py",
        "tests/integration/prefector/test_single_stock_flow.py",
        "tests/integration/prefector/test_sector_data_flow.py",
        "tests/integration/prefector/test_bond_repo_flow.py",
        "tests/integration/prefector/test_market_indices_flow.py",
        "tests/integration/prefector/test_daily_trading_sync_flow.py",
        "tests/integration/prefector/test_flow_integration.py",
        "tests/integration/prefector/test_flow_scheduling.py",
        "tests/integration/prefector/test_flow_error_handling.py",
      ]
    )
    print("[INFO] 运行所有Prefect测试")

  # 添加通用参数
  if args.verbose:
    cmd.append("-v")

  cmd.extend(["--tb=short", "--color=yes", "--disable-warnings"])

  # 添加覆盖率报告
  if not (hasattr(args, "integration") and args.integration):
    cmd.extend(["--cov=prefector", "--cov-report=term-missing"])

  print(f"[CMD] 执行命令: {' '.join(cmd)}")
  print("-" * 60)

  # 运行测试
  try:
    result = subprocess.run(cmd, check=False, cwd=tests_dir.parent)

    print("-" * 60)
    if result.returncode == 0:
      print("[SUCCESS] Prefect测试全部通过!")
    else:
      print("[FAILED] 部分Prefect测试失败")

    return result.returncode

  except KeyboardInterrupt:
    print("\n[ERROR] 测试被用户中断")
    return 1
  except Exception as e:
    print(f"[ERROR] 运行Prefect测试时出错: {e}")
    return 1


def run_tests(args):
  """运行测试"""
  # 确保在正确的目录
  script_dir = Path(__file__).parent
  os.chdir(script_dir)

  # 构建pytest命令
  cmd = ["python", "-m", "pytest"]

  # 添加参数
  if args.verbose:
    cmd.append("-v")

  if args.coverage:
    cmd.extend(["--cov=.", "--cov-report=html", "--cov-report=term"])

  if args.markers:
    cmd.extend(["-m", args.markers])

  if args.pattern:
    cmd.extend(["-k", args.pattern])

  if args.failfast:
    cmd.append("-x")

  if args.parallel:
    cmd.extend(["-n", str(args.parallel)])

  # 添加测试路径
  if hasattr(args, "file") and args.file:
    cmd.append(args.file)
  else:
    cmd.append("tests/")

  # 添加额外参数
  if args.extra_args:
    cmd.extend(args.extra_args.split())

  print(f"[CMD] 运行命令: {' '.join(cmd)}")
  print("=" * 60)

  # 运行测试
  try:
    result = subprocess.run(cmd, check=False)
    return result.returncode
  except KeyboardInterrupt:
    print("\n[ERROR] 测试被用户中断")
    return 1
  except Exception as e:
    print(f"[ERROR] 运行测试时出错: {e}")
    return 1


def main():
  """主函数"""
  parser = argparse.ArgumentParser(
    description="QuantX API 测试运行器",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
示例用法:
  python run_tests.py                          # 运行所有测试
  python run_tests.py -v                       # 详细模式
  python run_tests.py --coverage               # 带覆盖率报告
  python run_tests.py -m unit                  # 只运行单元测试
  python run_tests.py -k "test_health"         # 运行匹配的测试
  python run_tests.py tests/test_main.py       # 运行特定文件
  python run_tests.py --parallel 4             # 并行运行（需要pytest-xdist）
  python run_tests.py miniqmt                  # 运行所有MiniQMT测试
  python run_tests.py miniqmt --module config  # 运行MiniQMT配置测试
  python run_tests.py miniqmt --performance    # 运行MiniQMT性能测试
  python run_tests.py prefect                  # 运行所有Prefect测试
  python run_tests.py prefect --flow daily_stock_flow  # 运行每日股票同步测试
  python run_tests.py prefect --flow realtime_price_flow  # 运行实时价格同步测试
  python run_tests.py prefect --flow batch_stock  # 支持有或无 _flow 后缀
  python run_tests.py prefect --unit          # 运行Prefect单元测试
  python run_tests.py prefect --integration   # 运行Prefect集成测试（安全，使用Mock）
  python run_tests.py prefect --e2e           # ⚠️  运行E2E测试（危险，真实交易）
        """,
  )

  parser.add_argument("-v", "--verbose", action="store_true", help="详细输出模式")

  parser.add_argument("--coverage", action="store_true", help="生成测试覆盖率报告")

  parser.add_argument(
    "-m", "--markers", help="按标记运行测试 (如: unit, integration, slow)"
  )

  parser.add_argument("-k", "--pattern", help="按模式匹配测试名称")

  parser.add_argument(
    "-x", "--failfast", action="store_true", help="遇到第一个失败就停止"
  )

  parser.add_argument("-n", "--parallel", type=int, help="并行运行测试的进程数")

  parser.add_argument("--file", help="要运行的特定测试文件")

  parser.add_argument("--extra-args", help="传递给pytest的额外参数")

  # 预定义的测试套件
  subparsers = parser.add_subparsers(dest="suite", help="预定义的测试套件")

  unit_parser = subparsers.add_parser("unit", help="运行单元测试")
  unit_parser.add_argument("-v", "--verbose", action="store_true", help="详细输出模式")
  unit_parser.add_argument("--extra-args", help="传递给pytest的额外参数")

  integration_parser = subparsers.add_parser("integration", help="运行集成测试")
  integration_parser.add_argument(
    "-v", "--verbose", action="store_true", help="详细输出模式"
  )

  api_parser = subparsers.add_parser("api", help="运行API测试")
  api_parser.add_argument("-v", "--verbose", action="store_true", help="详细输出模式")

  middleware_parser = subparsers.add_parser("middleware", help="运行中间件测试")
  middleware_parser.add_argument(
    "-v", "--verbose", action="store_true", help="详细输出模式"
  )

  graphql_parser = subparsers.add_parser("graphql", help="运行GraphQL测试")
  graphql_parser.add_argument(
    "-v", "--verbose", action="store_true", help="详细输出模式"
  )

  quick_parser = subparsers.add_parser("quick", help="快速测试（排除慢速测试）")
  quick_parser.add_argument("-v", "--verbose", action="store_true", help="详细输出模式")

  miniqmt_parser = subparsers.add_parser("miniqmt", help="运行MiniQMT模块测试")
  miniqmt_parser.add_argument(
    "-v", "--verbose", action="store_true", help="详细输出模式"
  )
  miniqmt_parser.add_argument(
    "--module",
    choices=["config", "utils", "data", "trading", "integration"],
    help="运行特定MiniQMT模块的测试",
  )
  miniqmt_parser.add_argument(
    "--performance", action="store_true", help="运行MiniQMT性能测试"
  )

  prefect_parser = subparsers.add_parser("prefect", help="运行Prefect模块测试")
  prefect_parser.add_argument(
    "-v", "--verbose", action="store_true", help="详细输出模式"
  )
  prefect_parser.add_argument(
    "--flow",
    help="运行特定Prefect流程的测试 (直接指定流程文件名，如: daily_stock_flow)",
  )
  prefect_parser.add_argument("--unit", action="store_true", help="运行Prefect单元测试")
  prefect_parser.add_argument(
    "--integration",
    action="store_true",
    help="运行Prefect集成测试（使用Mock服务，安全）",
  )
  prefect_parser.add_argument(
    "--e2e",
    action="store_true",
    help="⚠️  运行E2E测试（危险：可能执行真实交易！仅在测试环境运行）",
  )

  args = parser.parse_args()

  # 处理预定义套件
  if args.suite:
    if args.suite == "unit":
      args.markers = "unit"
    elif args.suite == "integration":
      args.markers = "integration"
    elif args.suite == "api":
      args.markers = "api"
    elif args.suite == "middleware":
      args.markers = "middleware"
    elif args.suite == "graphql":
      args.markers = "graphql"
    elif args.suite == "quick":
      args.markers = "not slow"
    elif args.suite == "miniqmt":
      return run_miniqmt_tests(args)
    elif args.suite == "prefect":
      return run_prefect_tests(args)

  # 检查依赖
  try:
    import importlib.util

    if importlib.util.find_spec("pytest") is None:
      raise ImportError
  except ImportError:
    print("[ERROR] 错误: 未安装 pytest")
    print("请运行: pip install pytest")
    return 1

  if args.coverage:
    try:
      import importlib.util

      if importlib.util.find_spec("pytest_cov") is None:
        raise ImportError
    except ImportError:
      print("[ERROR] 错误: 未安装 pytest-cov")
      print("请运行: pip install pytest-cov")
      return 1

  if args.parallel:
    try:
      import importlib.util

      if importlib.util.find_spec("xdist") is None:
        raise ImportError
    except ImportError:
      print("[ERROR] 错误: 未安装 pytest-xdist")
      print("请运行: pip install pytest-xdist")
      return 1

  # 运行测试
  return run_tests(args)


if __name__ == "__main__":
  sys.exit(main())
