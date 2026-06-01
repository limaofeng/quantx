"""
持仓数据相关的原子任务

包含持仓数据获取、保存等基础任务
"""

import datetime
from typing import Any, Dict, List

from prefect import get_run_logger, task
from prefect.cache_policies import INPUTS

from miniqmt import XTDataManagerRegistry, XTTradingManagerRegistry
from models import Position
from services import PositionService
from core.utils import time_utils

# 缓存配置
CACHE_EXPIRATION = datetime.timedelta(minutes=5)

# 重试配置
DEFAULT_RETRIES = 3
SAVE_RETRIES = 2

# 注册表实例
trading_registry = XTTradingManagerRegistry()
data_registry = XTDataManagerRegistry()


@task(
  name="获取账户持仓",
  description="从交易接口获取账户持仓信息",
  cache_policy=INPUTS,
  cache_expiration=CACHE_EXPIRATION,
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=30,
)
async def fetch_account_positions(
  account_id: str = "300000013250",
) -> List[Dict[str, Any]]:
  """获取指定账户的持仓信息"""
  logger = get_run_logger()
  logger.info(f"开始获取账户 {account_id} 的持仓信息...")

  try:
    trading_manager = trading_registry.get_manager(account_id)

    # 获取持仓数据
    stock_positions = trading_manager.get_positions()
    logger.info(f"获取到 {len(stock_positions)} 个持仓记录")

    if not stock_positions:
      logger.info("当前无持仓")
      return []

    positions = []
    for pos in stock_positions:
      position_data = {
        "account_id": pos.account_id,
        "stock_code": pos.stock_code,
        "stock_name": pos.instrument_name,
        "volume": pos.volume,
        "can_use_volume": pos.can_use_volume,
        "open_price": pos.open_price,
        "market_value": pos.market_value,
        "frozen_volume": pos.frozen_volume,
        "on_road_volume": pos.on_road_volume,
        "yesterday_volume": pos.yesterday_volume,
        "avg_price": pos.avg_price,
        "direction": pos.direction,
        "last_price": pos.last_price,
        "profit_rate": pos.profit_rate,
        "secu_account": pos.secu_account,
      }
      positions.append(position_data)

    return positions

  except Exception as e:
    logger.error(f"获取持仓数据失败: {str(e)}")
    raise


@task(
  name="保存持仓数据",
  description="将持仓数据保存到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=60,
)
async def save_positions(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
  """保存持仓数据到数据库"""
  logger = get_run_logger()

  if not positions:
    logger.info("无持仓数据需要保存")
    return {"saved_count": 0, "message": "无持仓数据"}

  logger.info(f"开始保存 {len(positions)} 个持仓记录到数据库...")

  try:
    position_service = PositionService()
    saved_count = 0
    failed_count = 0

    for position_data in positions:
      try:
        position = Position(
          account_id=position_data["account_id"],
          stock_code=position_data["stock_code"],
          volume=position_data["volume"],
          can_use_volume=position_data["can_use_volume"],
          open_price=position_data["open_price"],
          market_value=position_data["market_value"],
          frozen_volume=position_data["frozen_volume"],
          on_road_volume=position_data["on_road_volume"],
          yesterday_volume=position_data["yesterday_volume"],
          avg_price=position_data["avg_price"],
          direction=position_data["direction"],
        )
        await position_service.save_position(position)
        saved_count += 1

      except Exception as e:
        logger.error(f"保存持仓 {position_data['stock_code']} 失败: {str(e)}")
        failed_count += 1

    result = {
      "saved_count": saved_count,
      "failed_count": failed_count,
      "total_count": len(positions),
    }

    logger.info(f"持仓数据保存完成: {result}")
    return result

  except Exception as e:
    logger.error(f"保存持仓数据失败: {str(e)}")
    raise


@task(name="生成持仓同步报告", description="生成持仓同步的详细报告", retries=1)
async def generate_position_sync_report(
  positions: List[Dict[str, Any]], save_result: Dict[str, Any]
) -> Dict[str, Any]:
  """生成持仓同步报告"""
  logger = get_run_logger()

  try:
    # 计算统计信息
    total_market_value = sum(pos["market_value"] for pos in positions)
    # 由于不处理利润数据，这里设置为0
    total_profit_loss = 0
    profitable_positions = []
    loss_positions = []

    report = {
      "sync_time": time_utils.now().isoformat(),
      "summary": {
        "total_positions": len(positions),
        "total_market_value": round(total_market_value, 2),
        "total_profit_loss": round(total_profit_loss, 2),
        "profitable_count": len(profitable_positions),
        "loss_count": len(loss_positions),
      },
      "database_save": save_result,
      "positions": positions,
    }

    logger.info("持仓同步报告生成完成")
    logger.info(f"总持仓数: {len(positions)}")
    logger.info(f"总市值: {total_market_value:,.2f}")
    logger.info(f"总盈亏: {total_profit_loss:,.2f}")

    return report

  except Exception as e:
    logger.error(f"生成持仓同步报告失败: {str(e)}")
    raise
