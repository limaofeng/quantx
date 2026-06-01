"""
Prefect 任务运行器

提供便捷的命令行接口来运行和管理 Prefect 任务
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.append(str(Path(__file__).parent.parent))

from prefector.flows import market_sync_flow, realtime_price_sync_flow


async def run_realtime_sync(stock_codes=None):
  """运行实时价格同步"""
  print("开始运行实时价格同步...")
  result = await realtime_price_sync_flow(stock_codes)
  print(f"同步完成: {result}")
  return result


async def run_market_sync():
  """运行市场数据同步"""
  print("开始运行市场数据同步...")
  result = await market_sync_flow()
  print(f"同步完成: {result}")
  return result


def main():
  """主函数"""
  parser = argparse.ArgumentParser(description="QuantX Prefect 任务运行器")

  subparsers = parser.add_subparsers(dest="command", help="可用命令")

  # 每日同步命令
  subparsers.add_parser("daily", help="运行每日数据同步")

  # 实时同步命令
  realtime_parser = subparsers.add_parser("realtime", help="运行实时价格同步")
  realtime_parser.add_argument(
    "--stocks", nargs="*", help="指定股票代码，如: 000001 600519"
  )

  # 市场同步命令
  subparsers.add_parser("market", help="运行市场数据同步")

  # 测试命令
  subparsers.add_parser("test", help="运行所有测试")

  # 服务命令
  serve_parser = subparsers.add_parser("serve", help="启动 Prefect 服务")
  serve_parser.add_argument("--port", type=int, default=4200, help="服务端口")

  args = parser.parse_args()

  if args.command == "daily":
    print("每日同步功能尚未实现")

  elif args.command == "realtime":
    stock_codes = args.stocks if args.stocks else None
    asyncio.run(run_realtime_sync(stock_codes))

  elif args.command == "market":
    asyncio.run(run_market_sync())

  elif args.command == "test":
    print("运行所有测试流程...")
    asyncio.run(run_test_all())

  elif args.command == "serve":
    print(f"启动 Prefect 服务器在端口 {args.port}...")
    start_prefect_server(args.port)

  else:
    parser.print_help()


async def run_test_all():
  """运行所有测试"""
  print("=" * 60)
  print("QuantX Prefect 任务测试")
  print("=" * 60)

  try:
    # 测试每日同步
    print("\n1. 测试每日数据同步...")
    print("每日数据同步测试完成（功能尚未实现）")

    # 测试实时同步
    print("\n2. 测试实时价格同步...")
    realtime_result = await run_realtime_sync(["000001", "600519"])

    # 测试市场同步
    print("\n3. 测试市场数据同步...")
    market_result = await run_market_sync()

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)

    # 汇总结果
    print("\n测试结果汇总:")
    print("- 每日同步: 测试完成（功能尚未实现）")
    print(f"- 实时同步: {realtime_result.get('status', 'unknown')}")
    print(f"- 市场同步: {market_result.get('status', 'unknown')}")

  except Exception as e:
    print(f"测试失败: {e}")
    sys.exit(1)


def start_prefect_server(port=4200):
  """启动 Prefect 服务器"""
  import os
  import subprocess

  try:
    print(f"正在启动 Prefect 服务器 (端口: {port})...")
    print(f"访问 UI: http://localhost:{port}")
    print("按 Ctrl+C 停止服务器")

    # 设置环境变量禁用遥测
    env = os.environ.copy()
    env["PREFECT_SERVER_ANALYTICS_ENABLED"] = "false"

    subprocess.run(
      [
        sys.executable,
        "-m",
        "prefect",
        "server",
        "start",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--analytics-off",
      ],
      env=env,
    )

  except KeyboardInterrupt:
    print("\n正在停止服务器...")
  except Exception as e:
    print(f"启动服务器失败: {e}")
    sys.exit(1)


if __name__ == "__main__":
  main()
