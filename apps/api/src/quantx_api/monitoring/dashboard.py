#!/usr/bin/env python3
"""
实时监控仪表板
显示系统指标、请求统计和应用状态
"""

import asyncio
import os
from typing import Dict, Optional

import aiohttp
from quantx_infrastructure.core.utils import time_utils


class MonitoringDashboard:
  def __init__(self, api_url: str = "http://localhost:8000"):
    self.api_url = api_url
    self.metrics_history = []
    self.running = False

  async def start(self, refresh_interval: int = 5):
    """启动监控仪表板"""
    self.running = True
    print("🔍 QuantX 实时监控仪表板")
    print("=" * 60)
    print("按 Ctrl+C 退出")
    print()

    try:
      while self.running:
        await self.update_dashboard()
        await asyncio.sleep(refresh_interval)
    except KeyboardInterrupt:
      print("\n👋 监控仪表板已停止")

  async def update_dashboard(self):
    """更新仪表板显示"""
    # 清屏
    os.system("cls" if os.name == "nt" else "clear")

    current_time = time_utils.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"🔍 QuantX 监控仪表板 - {current_time}")
    print("=" * 60)

    async with aiohttp.ClientSession() as session:
      # 获取健康状态
      health_status = await self.get_health_status(session)
      self.display_health_status(health_status)

      # 获取指标数据
      metrics_data = await self.get_metrics_data(session)
      self.display_metrics(metrics_data)

      # 获取应用状态
      app_status = await self.get_app_status(session)
      self.display_app_status(app_status)

      print("\n" + "=" * 60)
      print("🔄 自动刷新中... (按 Ctrl+C 退出)")

  async def get_health_status(self, session: aiohttp.ClientSession) -> Optional[Dict]:
    """获取健康状态"""
    try:
      async with session.get(f"{self.api_url}/health") as response:
        if response.status == 200:
          return await response.json()
    except Exception as e:
      return {"error": str(e)}
    return None

  async def get_metrics_data(self, session: aiohttp.ClientSession) -> Optional[str]:
    """获取Prometheus指标数据"""
    try:
      async with session.get(f"{self.api_url}/metrics") as response:
        if response.status == 200:
          return await response.text()
    except Exception as e:
      return f"# Error: {e}"
    return None

  async def get_app_status(self, session: aiohttp.ClientSession) -> Dict:
    """获取应用状态（通过GraphQL查询）"""
    try:
      query = {"query": "query { stocks { code } }"}
      async with session.post(f"{self.api_url}/graphql", json=query) as response:
        if response.status == 200:
          data = await response.json()
          return {
            "graphql_status": "healthy",
            "stocks_available": len(data.get("data", {}).get("stocks", [])),
          }
    except Exception as e:
      return {"graphql_status": "error", "error": str(e)}
    return {"graphql_status": "unknown"}

  def display_health_status(self, health: Optional[Dict]):
    """显示健康状态"""
    print("🏥 服务健康状态")
    print("-" * 30)

    if health is None:
      print("❌ 无法连接到服务器")
      return

    if "error" in health:
      print(f"❌ 错误: {health['error']}")
      return

    status = health.get("status", "unknown")
    icon = "✅" if status == "healthy" else "❌"
    print(f"{icon} 状态: {status}")
    print(f"📦 版本: {health.get('version', 'unknown')}")
    print(f"🌍 环境: {health.get('environment', 'unknown')}")
    print(f"🐛 调试模式: {'开启' if health.get('debug') else '关闭'}")
    print(f"📡 实时数据: {'开启' if health.get('realtime_enabled') else '关闭'}")

  def display_metrics(self, metrics_text: Optional[str]):
    """显示关键指标"""
    print("\n📊 系统指标")
    print("-" * 30)

    if metrics_text is None:
      print("❌ 无法获取指标数据")
      return

    # 解析关键指标
    metrics = self.parse_prometheus_metrics(metrics_text)

    # 系统资源
    cpu_usage = metrics.get("system_cpu_usage_percent", "N/A")
    memory_usage = metrics.get("system_memory_usage_percent", "N/A")
    disk_usage = metrics.get("system_disk_usage_percent", "N/A")

    print(f"💻 CPU使用率: {cpu_usage}%")
    print(f"🧠 内存使用率: {memory_usage}%")
    print(f"💾 磁盘使用率: {disk_usage}%")

    # HTTP请求统计
    total_requests = metrics.get("http_requests_total", "0")
    print(f"📈 总请求数: {total_requests}")

    # WebSocket连接
    active_connections = metrics.get("websocket_connections_active", "0")
    print(f"🔌 活跃连接: {active_connections}")

  def display_app_status(self, app_status: Dict):
    """显示应用状态"""
    print("\n🚀 应用状态")
    print("-" * 30)

    graphql_status = app_status.get("graphql_status", "unknown")
    icon = "✅" if graphql_status == "healthy" else "❌"
    print(f"{icon} GraphQL: {graphql_status}")

    if "stocks_available" in app_status:
      print(f"📈 可用股票: {app_status['stocks_available']} 只")

    if "error" in app_status:
      print(f"❌ 错误: {app_status['error']}")

  def parse_prometheus_metrics(self, metrics_text: str) -> Dict[str, str]:
    """解析Prometheus指标"""
    metrics = {}

    for line in metrics_text.split("\n"):
      line = line.strip()
      if line.startswith("#") or not line:
        continue

      # 简单解析指标值
      if " " in line:
        metric_name, value = line.split(" ", 1)
        # 移除标签，只保留指标名
        if "{" in metric_name:
          metric_name = metric_name.split("{")[0]

        try:
          # 尝试格式化数值
          float_value = float(value)
          if float_value.is_integer():
            metrics[metric_name] = str(int(float_value))
          else:
            metrics[metric_name] = f"{float_value:.2f}"
        except (ValueError, TypeError):
          metrics[metric_name] = value

    return metrics


async def main():
  """主函数，用于独立运行仪表板"""
  dashboard = MonitoringDashboard()
  await dashboard.start(refresh_interval=3)


if __name__ == "__main__":
  print("🔍 启动 QuantX 监控仪表板...")
  print("请确保 QuantX API 服务器正在运行在 http://localhost:8000")
  print("按 Enter 继续...")
  input()

  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    print("\n👋 再见！")
