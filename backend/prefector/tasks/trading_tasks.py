"""
交易数据相关的原子任务

包含委托(Order)和成交(Trade)数据获取、保存等基础任务
"""

import datetime
import math
from typing import Any, Dict, List

from prefect import get_run_logger, task

from miniqmt.manager_registry import XTTradingManagerRegistry
from models import Order, Position, Trade
from models.enums import AccountType
from services import PositionService
from services.daily_asset_snapshot_service import DailyAssetSnapshotService
from services.order_service import OrderService
from services.trade_service import TradeService
from core.utils import time_utils

# 缓存配置
CACHE_EXPIRATION = datetime.timedelta(minutes=5)

# 重试配置
DEFAULT_RETRIES = 3
SAVE_RETRIES = 2

# 注册表实例
trading_registry = XTTradingManagerRegistry()


@task(
  name="检查账户可用资金",
  description="检查交易账户的可用资金是否满足最小要求",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=30,
)
async def check_account_cash(account_id: str, min_cash: float) -> bool:
  """
  检查账户可用资金

  Args:
      account_id: 交易账户ID

  Returns:
      可用资金
  """
  trading_manager = trading_registry.get_manager(account_id)
  account_info = trading_manager.get_account_info()
  return account_info and account_info.get("cash", 0) >= min_cash


@task(
  name="获取当日委托数据",
  description="从交易接口获取当日委托信息",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=30,
)
async def fetch_daily_orders(account_id: str = "300000013250") -> List[Dict[str, Any]]:
  """
  获取指定账户当日的委托信息

  Args:
      account_id: 交易账户ID

  Returns:
      委托数据列表
  """
  logger = get_run_logger()

  # 使用当前日期
  trade_date = time_utils.today().strftime("%Y-%m-%d")

  logger.info(f"开始获取账户 {account_id} 在 {trade_date} 的委托信息...")

  try:
    trading_manager = trading_registry.get_manager(account_id)

    # 获取委托数据
    xt_orders = trading_manager.get_orders(cancelable_only=False)

    orders_data = []
    for xt_order in xt_orders:
      order_dict = {
        "account_id": account_id,
        "account_type": xt_order.account_type,
        "stock_code": xt_order.stock_code,
        "order_id": xt_order.order_id,
        "order_sysid": xt_order.order_sysid,
        "order_time": xt_order.order_time,
        "order_type": xt_order.order_type,
        "order_volume": xt_order.order_volume,
        "price_type": xt_order.price_type,
        "price": xt_order.price,
        "traded_volume": xt_order.traded_volume,
        "traded_price": xt_order.traded_price,
        "order_status": xt_order.order_status,
        "status_msg": xt_order.status_msg,
        "strategy_name": xt_order.strategy_name,
        "order_remark": xt_order.order_remark,
        "direction": xt_order.direction,
        "offset_flag": xt_order.offset_flag,
        "secu_account": xt_order.secu_account,
        "instrument_name": xt_order.instrument_name,
      }
      orders_data.append(order_dict)

    logger.info(f"获取到 {len(orders_data)} 个委托记录")
    return orders_data

  except Exception as e:
    logger.error(f"获取委托数据失败: {e}")
    raise


@task(
  name="获取当日成交数据",
  description="从交易接口获取当日成交信息",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=30,
)
async def fetch_daily_trades(account_id: str = "300000013250") -> List[Dict[str, Any]]:
  """
  获取指定账户当日的成交信息

  Args:
      account_id: 交易账户ID

  Returns:
      成交数据列表
  """
  logger = get_run_logger()

  # 使用当前日期
  trade_date = time_utils.today().strftime("%Y-%m-%d")

  logger.info(f"开始获取账户 {account_id} 在 {trade_date} 的成交信息...")

  try:
    trading_manager = trading_registry.get_manager(account_id)

    # 获取成交数据
    trades = trading_manager.get_trades()

    if trades is None or len(trades) == 0:
      logger.info("当日无成交记录")
      return []

    trades_data = []
    for trade in trades:
      # XtTrade 同步仅保留柜台接口稳定返回字段
      trade_dict = {
        "account_id": trade.account_id,
        "account_type": trade.account_type,
        "stock_code": trade.stock_code,
        "order_type": trade.order_type,
        "traded_id": trade.traded_id,
        "traded_time": trade.traded_time,
        "traded_price": trade.traded_price,
        "traded_volume": trade.traded_volume,
        "traded_amount": trade.traded_amount,
        "order_id": trade.order_id,
        "order_sysid": trade.order_sysid,
        "strategy_name": trade.strategy_name,
        "order_remark": trade.order_remark,
        "direction": trade.direction,
        "offset_flag": trade.offset_flag,
      }
      trades_data.append(trade_dict)

    logger.info(f"获取到 {len(trades_data)} 个成交记录")
    return trades_data

  except Exception as e:
    logger.error(f"获取成交数据失败: {e}")
    raise


@task(
  name="获取最新持仓数据",
  description="获取账户最新持仓信息",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=30,
)
async def fetch_latest_positions(
  account_id: str = "300000013250",
) -> List[Dict[str, Any]]:
  """
  获取指定账户的最新持仓信息

  Args:
      account_id: 交易账户ID

  Returns:
      持仓数据列表
  """
  logger = get_run_logger()
  logger.info(f"开始获取账户 {account_id} 的最新持仓信息...")

  try:
    trading_manager = trading_registry.get_manager(account_id)

    # 获取持仓数据
    stock_positions = trading_manager.get_positions()

    if not stock_positions:
      logger.info("当前无持仓")
      return []

    positions_data = []
    closed_positions_count = 0

    for position in stock_positions:
      # 检查是否为清仓状态（volume = 0）
      # 当清仓时，QMT API 返回的价格字段可能为 -inf
      # 这种情况下应该跳过，后续由 update_positions_data 处理删除逻辑
      if position.volume == 0:
        closed_positions_count += 1
        logger.info(
          f"检测到清仓持仓: account_id={position.account_id}, "
          f"stock_code={position.stock_code}, 将从数据库中删除"
        )
        # 仍然需要添加到列表中，以便后续删除
        position_dict = {
          "account_id": position.account_id,
          "account_type": position.account_type,
          "stock_code": position.stock_code,
          "instrument_name": position.instrument_name,
          "volume": position.volume,
          "can_use_volume": position.can_use_volume,
          "open_price": None,  # 清仓时价格为无效值
          "market_value": 0.0,
          "frozen_volume": position.frozen_volume,
          "on_road_volume": position.on_road_volume,
          "yesterday_volume": position.yesterday_volume,
          "avg_price": None,  # 清仓时价格为无效值
          "direction": position.direction,
          "last_price": position.last_price,
          "profit_rate": position.profit_rate,
          "secu_account": position.secu_account,
        }
        positions_data.append(position_dict)
        continue

      # 对于正常持仓，验证价格字段
      open_price = position.open_price
      avg_price = position.avg_price
      market_value = position.market_value

      # 检测并记录异常的开仓价
      if open_price is not None and not math.isfinite(open_price):
        logger.warning(
          f"持仓数据异常 - 开仓价为无限值: account_id={position.account_id}, "
          f"stock_code={position.stock_code}, open_price={open_price}"
        )
        open_price = None

      # 检测并记录异常的成本价
      if avg_price is not None and not math.isfinite(avg_price):
        logger.warning(
          f"持仓数据异常 - 成本价为无限值: account_id={position.account_id}, "
          f"stock_code={position.stock_code}, avg_price={avg_price}"
        )
        avg_price = None

      # 检测并记录异常的市值
      if market_value is not None and not math.isfinite(market_value):
        logger.warning(
          f"持仓数据异常 - 市值为无限值: account_id={position.account_id}, "
          f"stock_code={position.stock_code}, market_value={market_value}"
        )
        market_value = None

      position_dict = {
        "account_id": position.account_id,
        "account_type": position.account_type,
        "stock_code": position.stock_code,
        "instrument_name": position.instrument_name,
        "volume": position.volume,
        "can_use_volume": position.can_use_volume,
        "open_price": open_price,
        "market_value": market_value,
        "frozen_volume": position.frozen_volume,
        "on_road_volume": position.on_road_volume,
        "yesterday_volume": position.yesterday_volume,
        "avg_price": avg_price,
        "direction": position.direction,
        "last_price": position.last_price,
        "profit_rate": position.profit_rate,
        "secu_account": position.secu_account,
      }
      positions_data.append(position_dict)

    # 记录统计信息
    active_positions = len(positions_data) - closed_positions_count
    logger.info(
      f"获取到 {len(positions_data)} 个持仓记录 "
      f"(其中 {active_positions} 个活跃持仓, {closed_positions_count} 个清仓持仓)"
    )
    return positions_data

  except Exception as e:
    logger.error(f"获取持仓数据失败: {e}")
    raise


def _convert_order_status(status_code: int) -> str:
  """
  转换订单状态码为字符串

  Args:
      status_code: 状态码

  Returns:
      状态字符串
  """
  status_map = {
    0: "PENDING",  # 待报
    1: "SUBMITTED",  # 已报
    2: "PARTIAL",  # 部分成交
    3: "FILLED",  # 全部成交
    4: "CANCELLED",  # 已撤单
    5: "REJECTED",  # 已拒绝
    6: "EXPIRED",  # 已过期
  }
  return status_map.get(status_code, "UNKNOWN")


@task(
  name="保存委托数据",
  description="批量保存委托数据到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=60,
)
async def save_orders_data(orders_data: List[Dict[str, Any]]) -> Dict[str, Any]:
  """
  批量保存委托数据到数据库

  Args:
      orders_data: 委托数据列表

  Returns:
      保存结果
  """
  logger = get_run_logger()
  logger.info(f"开始保存 {len(orders_data)} 个委托记录...")

  order_service = OrderService()

  try:
    orders = [Order.from_dict(data) for data in orders_data]
    save_result = await order_service.save_orders(orders)
    failed_count = len(orders_data) - save_result.saved_count

    logger.info(f"成功保存 {save_result.saved_count} 个委托记录")

    return {
      "total_count": len(orders_data),
      "saved_count": save_result.saved_count,
      "failed_count": failed_count,
      "success_rate": save_result.saved_count / len(orders_data) if orders_data else 0,
    }

  except Exception as e:
    logger.error(f"保存委托数据失败: {e}")
    raise


@task(
  name="保存成交数据",
  description="批量保存成交数据到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=60,
)
async def save_trades_data(trades_data: List[Dict[str, Any]]) -> Dict[str, Any]:
  """
  批量保存成交数据到数据库

  Args:
      trades_data: 成交数据列表

  Returns:
      保存结果
  """
  logger = get_run_logger()
  logger.info(f"开始保存 {len(trades_data)} 个成交记录...")

  try:
    trade_service = TradeService()
    trades = [Trade.from_dict(data) for data in trades_data]

    save_result = await trade_service.save_trades(trades)
    failed_count = len(trades_data) - save_result.saved_count

    logger.info(f"成功保存 {save_result.saved_count} 个成交记录")

    return {
      "total_count": len(trades_data),
      "saved_count": save_result.saved_count,
      "failed_count": failed_count,
      "success_rate": save_result.saved_count / len(trades_data) if trades_data else 0,
    }

  except Exception as e:
    logger.error(f"保存成交数据失败: {e}")
    raise


@task(
  name="更新持仓数据",
  description="更新账户持仓数据到数据库",
  # retries=SAVE_RETRIES,
  # retry_delay_seconds=60
)
async def update_positions_data(positions_data: List[Dict[str, Any]]) -> Dict[str, Any]:
  """
  更新账户持仓数据到数据库

  Args:
      positions_data: 持仓数据列表

  Returns:
      更新结果
  """
  logger = get_run_logger()
  logger.info(f"开始更新 {len(positions_data)} 个持仓记录...")

  try:
    position_service = PositionService()

    positions = [Position.from_dict(data) for data in positions_data]
    save_result = await position_service.save_positions(positions)

    logger.info(f"成功更新 {save_result.saved_count} 个持仓记录")

    return {
      "total_count": len(positions_data),
      "updated_count": save_result.updated_count,
      "failed_count": len(positions_data) - save_result.saved_count,
      "success_rate": save_result.saved_count / len(positions_data)
      if positions_data
      else 0,
    }

  except Exception as e:
    logger.error(f"更新持仓数据失败: {e}")
    raise


@task(
  name="生成收盘资产快照",
  description="收盘后记录账户与策略日终总资产快照，用于每日盈亏计算",
  retries=SAVE_RETRIES,
  retry_delay_seconds=60,
)
async def create_daily_asset_snapshots(
  account_id: str = "300000013250",
  trade_date: str = None,
  positions_data: List[Dict[str, Any]] = None,
  net_capital_flow_cny: float = 0.0,
) -> Dict[str, Any]:
  logger = get_run_logger()
  trade_date_value = (
    datetime.date.fromisoformat(trade_date)
    if isinstance(trade_date, str) and trade_date
    else time_utils.today()
  )

  logger.info(f"开始生成账户 {account_id} 在 {trade_date_value} 的收盘资产快照")

  try:
    trading_manager = trading_registry.get_manager(account_id)
    if not trading_manager:
      raise RuntimeError(f"无法获取账户 {account_id} 的交易管理器")

    account_info = trading_manager.get_account_info()
    if not account_info:
      raise RuntimeError(f"无法获取账户 {account_id} 的资产信息")

    service = DailyAssetSnapshotService()
    account_snapshot = await service.record_account_snapshot(
      account_id=account_id,
      account_info=account_info,
      trade_date=trade_date_value,
      snapshot_at=time_utils.now(),
      account_type=AccountType.STOCK,
      net_capital_flow_cny=net_capital_flow_cny,
      positions=positions_data or [],
    )
    strategy_snapshots = await service.record_strategy_snapshots_for_account(
      account_id=account_id,
      trade_date=trade_date_value,
      snapshot_at=time_utils.now(),
    )

    result = {
      "account_snapshot_id": account_snapshot.id,
      "account_daily_pnl": float(account_snapshot.daily_pnl_cny)
      if account_snapshot.daily_pnl_cny is not None
      else None,
      "account_daily_return_pct": float(account_snapshot.daily_return_pct)
      if account_snapshot.daily_return_pct is not None
      else None,
      "strategy_snapshot_count": len(strategy_snapshots),
      "snapshot_ids": [account_snapshot.id]
      + [snapshot.id for snapshot in strategy_snapshots],
    }
    logger.info(
      "收盘资产快照完成: "
      f"account_snapshot={account_snapshot.id}, "
      f"strategy_snapshots={len(strategy_snapshots)}"
    )
    return result

  except Exception as e:
    logger.error(f"生成收盘资产快照失败: {e}")
    raise
