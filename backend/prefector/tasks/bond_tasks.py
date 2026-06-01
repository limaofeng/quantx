"""
国债逆回购相关的原子任务

包含国债逆回购收益率的获取、分析和保存任务
"""

import asyncio
import datetime
from typing import Any, Dict

from prefect import get_run_logger, task
from prefect.cache_policies import INPUTS

from database.relational_base import WhereBuilder
from miniqmt.manager_registry import XTDataManagerRegistry
from models import Instrument
from models.enums import InstrumentType, OrderStatus, OrderType, PriceType
from services.holiday_service import HolidayService
from services.instrument_service import InstrumentService
from services.trading_service import TradingService
from services.trading_time_service import TradingTimeService

from .stock_tasks import PRICE_CACHE_EXPIRATION
from core.utils import time_utils


@task(
  name="获取国债逆回购收益率",
  description="获取各期限国债逆回购收益率数据",
  cache_policy=INPUTS,
  cache_expiration=PRICE_CACHE_EXPIRATION,
  retries=2,
  retry_delay_seconds=30,
)
async def fetch_bond_repo_rates() -> Dict[str, Dict[str, Any]]:
  """获取国债逆回购收益率数据"""
  logger = get_run_logger()
  logger.info("开始获取国债逆回购收益率数据...")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  instrument_service = InstrumentService()
  holiday_service = HolidayService()

  # 固定的国债逆回购品种列表
  where = WhereBuilder().eq(Instrument.type, InstrumentType.TRR)
  reverse_repos = await instrument_service.find_all(where=where)

  try:
    # 可以购买的逆回购的天数
    current_date = time_utils.now()
    date = current_date
    days = 1

    # while True:
    #   date += datetime.timedelta(days=1)
    #   weekday = date.weekday()

    #   is_trading_day = weekday < 5
    #   is_holiday = await holiday_service.is_holiday("SH", date.date())

    #   if is_trading_day and not is_holiday:
    #     break
    #   days += 1

    # 确定可以购买的逆回购天数
    reverse_repos = [
      repo
      for repo in reverse_repos
      if repo.interest_accrual_days and repo.interest_accrual_days <= days
    ]

    if not reverse_repos:
      logger.warning("没有找到合适的逆回购期限")
      return {}

    # 合并两个市场的数据
    all_repos = {}
    full_tick = data_manager.get_full_tick(
      stock_codes=[repo.id for repo in reverse_repos]
    )
    for repo in reverse_repos:
      code = repo.id
      market = repo.market
      name = repo.name
      duration = repo.interest_accrual_days
      stock_tick_data = full_tick.get(code, {})
      last_price = stock_tick_data.get("lastPrice", 0)

      current_rate = round(last_price, 4) if last_price > 0 else 0

      # 试算收益（基于 10000 元本金）
      principal = 10000
      expected_profit = round(principal * (current_rate / 100) * (duration / 365), 2)

      # 计算手续费（假设费率 0.001%）
      fee_rate = 0.00001  # 0.001%
      transaction_fee = round(principal * fee_rate, 2)
      net_profit = round(expected_profit - transaction_fee, 2)

      all_repos[code] = {
        "name": name,
        "market": market,
        "duration": duration,
        "current_rate": current_rate,  # 年化收益率
        "volume": stock_tick_data.get("volume", 0),
        "amount": stock_tick_data.get("amount", 0),
        "last_price": last_price,
        "pre_close": stock_tick_data.get("lastClose", 0),
        "timestamp": stock_tick_data.get("timetag", 0),
        "example": {  # 试算收益示例
          "principal": principal,  # 本金
          "expected_profit": expected_profit,  # 预期收益
          "transaction_fee": transaction_fee,  # 交易手续费(参考)
          "net_profit": net_profit,  # 到期收益
        },
      }

    logger.info(f"成功获取 {len(all_repos)} 个国债逆回购品种数据")
    for code, repo in sorted(
      all_repos.items(), key=lambda x: (x[1]["market"], x[1]["duration"])
    ):
      logger.info(
        f"国债逆回购 {'沪市' if repo['market'] == 'SH' else '深市'}{repo['name']}({code})\t{repo['duration']}天期\t最新价: {repo['last_price']}"
      )
    return all_repos

  except Exception as e:
    logger.error(f"获取国债逆回购收益率失败: {e}")
    raise


@task(
  name="分析国债逆回购机会",
  description="分析国债逆回购投资机会，识别有利可图的品种",
  retries=1,
  retry_delay_seconds=30,
)
async def analyze_bond_repo_opportunities(
  repo_data: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
  """分析国债逆回购投资机会"""
  logger = get_run_logger()
  logger.info("开始分析国债逆回购投资机会...")

  try:
    analysis = {
      "analysis_time": time_utils.now().isoformat(),
      "profitable_opportunities": [],  # 修改：有利可图的机会
      "best_opportunity": None,  # 新增：最佳机会
      "market_summary": {
        "shanghai": {
          "count": 0,
          "avg_rate": 0,
          "max_rate": 0,
          "min_rate": float("inf"),
        },
        "shenzhen": {
          "count": 0,
          "avg_rate": 0,
          "max_rate": 0,
          "min_rate": float("inf"),
        },
      },
      "duration_analysis": {},
      "recommendations": [],
    }

    shanghai_rates = []
    shenzhen_rates = []
    duration_rates = {}
    all_opportunities = []  # 收集所有机会

    for code, repo in repo_data.items():
      rate = repo["current_rate"]
      market = repo["market"]
      duration = repo["duration"]
      example = repo.get("example", {})
      net_profit = example.get("net_profit", 0)

      # 按市场统计
      if market == "SH":  # 修正市场标识
        shanghai_rates.append(rate)
        analysis["market_summary"]["shanghai"]["count"] += 1
        analysis["market_summary"]["shanghai"]["max_rate"] = max(
          analysis["market_summary"]["shanghai"]["max_rate"], rate
        )
        analysis["market_summary"]["shanghai"]["min_rate"] = min(
          analysis["market_summary"]["shanghai"]["min_rate"], rate
        )
      elif market == "SZ":  # 修正市场标识
        shenzhen_rates.append(rate)
        analysis["market_summary"]["shenzhen"]["count"] += 1
        analysis["market_summary"]["shenzhen"]["max_rate"] = max(
          analysis["market_summary"]["shenzhen"]["max_rate"], rate
        )
        analysis["market_summary"]["shenzhen"]["min_rate"] = min(
          analysis["market_summary"]["shenzhen"]["min_rate"], rate
        )

      # 按期限统计
      if duration not in duration_rates:
        duration_rates[duration] = []
      duration_rates[duration].append(rate)

      # 识别有利可图的机会（净收益 > 0，避免负利率）
      if rate > 0 and net_profit > 0:
        opportunity = {
          "code": code,
          "name": repo["name"],
          "market": market,
          "duration": duration,
          "rate": rate,
          "volume": repo["volume"],
          "net_profit": net_profit,
          "last_price": repo["last_price"],
          "expected_profit": example.get("expected_profit", 0),
          "transaction_fee": example.get("transaction_fee", 0),
        }
        analysis["profitable_opportunities"].append(opportunity)
        all_opportunities.append(opportunity)

    # 选择最佳机会（按净收益排序）
    if all_opportunities:
      best_opportunity = max(all_opportunities, key=lambda x: x["net_profit"])
      analysis["best_opportunity"] = best_opportunity
      logger.info(
        f"最佳机会: {best_opportunity['name']}({best_opportunity['code']})，"
        f"预计收益率 {best_opportunity['rate']}%，净收益 {best_opportunity['net_profit']}元"
      )

    # 计算平均收益率
    if shanghai_rates:
      analysis["market_summary"]["shanghai"]["avg_rate"] = round(
        sum(shanghai_rates) / len(shanghai_rates), 3
      )
    if shenzhen_rates:
      analysis["market_summary"]["shenzhen"]["avg_rate"] = round(
        sum(shenzhen_rates) / len(shenzhen_rates), 3
      )

    # 期限分析
    for duration, rates in duration_rates.items():
      analysis["duration_analysis"][f"{duration}天"] = {
        "avg_rate": round(sum(rates) / len(rates), 3),
        "max_rate": max(rates),
        "min_rate": min(rates),
        "count": len(rates),
      }

    # 生成投资建议
    if analysis["best_opportunity"]:
      best = analysis["best_opportunity"]
      analysis["recommendations"].append(
        f"推荐购买 {best['name']}({best['code']})，"
        f"收益率 {best['rate']}%，期限 {best['duration']}天，净收益 {best['net_profit']}元"
      )
    elif analysis["profitable_opportunities"]:
      # 如果有其他有利可图的机会
      sorted_ops = sorted(
        analysis["profitable_opportunities"],
        key=lambda x: x["net_profit"],
        reverse=True,
      )
      top_op = sorted_ops[0]
      analysis["recommendations"].append(
        f"推荐关注 {top_op['name']}({top_op['code']})，"
        f"收益率 {top_op['rate']}%，净收益 {top_op['net_profit']}元"
      )
    else:
      analysis["recommendations"].append("当前无有利可图的投资机会，建议等待更好时机")

    # 收盘时段特别提醒
    current_hour = time_utils.now().hour
    if 14 <= current_hour <= 15:  # 收盘前一小时
      analysis["recommendations"].append(
        "临近收盘，国债逆回购收益率通常会上升，建议关注短期品种"
      )

    logger.info(
      f"分析完成，发现 {len(analysis['profitable_opportunities'])} 个有利可图的机会"
    )
    return analysis

  except Exception as e:
    logger.error(f"分析国债逆回购机会失败: {e}")
    raise


@task(
  name="检查交易日状态",
  description="检查交易日期是否为交易日",
  cache_policy=INPUTS,
  cache_expiration=datetime.timedelta(hours=1),
  retries=1,
)
async def check_trading_day(trade_date: str = None) -> bool:
  """检查交易是否为交易日"""
  logger = get_run_logger()

  trading_time_service = TradingTimeService()

  try:
    if trade_date is None:
      check_date = time_utils.today()
    else:
      trading_date = datetime.datetime.strptime(trade_date, "%Y-%m-%d")
      check_date = trading_date.date()

    result = await trading_time_service.is_trading_day("SH", check_date)

    logger.info(f"交易日期: {check_date}, 是否为交易日: {result}")

    return result

  except Exception as e:
    logger.error(f"检查交易日状态失败: {e}")
    raise


@task(
  name="执行国债逆回购购买",
  description="根据分析结果执行国债逆回购购买操作",
  retries=2,
  retry_delay_seconds=30,
)
async def execute_bond_repo_purchase(analysis: Dict[str, Any]) -> Dict[str, Any]:
  """执行国债逆回购购买操作"""
  logger = get_run_logger()
  logger.info("开始执行国债逆回购购买操作...")

  trading_service = TradingService()

  try:
    purchase_results = []
    best_opportunity = analysis.get("best_opportunity")

    if not best_opportunity:
      logger.info("无有利可图的机会，跳过购买操作")
      return {"purchases": [], "message": "无有利可图的机会"}

    # 使用最佳机会进行购买
    account_info = await trading_service.get_account_info(
      realtime=True
    )  # 确保账户信息最新

    cash = account_info.cash
    purchase_amount = int(cash / 1000) * 1000

    purchase_volume = purchase_amount // 100  # 假设每手100元

    current_rate = best_opportunity["rate"]
    # 简单保守限价策略：使用98%基准收益率，避免波动影响
    limit_rate = round(current_rate * 0.98, 3)

    logger.info(
      f"执行购买: {best_opportunity['name']}({best_opportunity['code']})，"
      f"目标收益率 {current_rate}%，限价收益率 {limit_rate}%，金额 {purchase_amount}元，"
      f"净收益 {best_opportunity['net_profit']}元"
    )

    print(
      f"下单参数: 代码={best_opportunity['code']}, 类型={OrderType.SELL}, 数量={purchase_volume}, 价格类型={PriceType.FIX_PRICE}, 价格={limit_rate}"
    )

    # 下限价单
    order_result = await trading_service.place_order(
      stock_code=best_opportunity["code"],
      order_type=OrderType.SELL,
      order_volume=purchase_volume,
      price_type=PriceType.FIX_PRICE,
      price=limit_rate,
      order_remark=f"国债逆回购:{'上交所' if best_opportunity['market'] == 'SH' else '深交所'}{best_opportunity['duration']}天",
    )

    if not order_result["success"]:
      logger.warning(f"下单失败: {order_result['error']}")
      raise Exception(
        order_result.get("error", order_result.get("message", "下单失败"))
      )

    order_id = order_result["order_id"]
    logger.info(f"限价单已提交，订单号: {order_id}")

    # 等待3秒钟后检查订单状态
    await asyncio.sleep(3)

    # 等待成交检查（等待6秒）
    order_status = await trading_service.check_order_status(order_id, 6)

    if order_status.get("success", False) is False:
      raise Exception(order_status.get("error", "检查订单状态失败"))

    if order_status["status"] == OrderStatus.SUCCEEDED:
      # 成交成功
      logger.info(f"订单成交成功！实际成交收益率: {order_status['filled_rate']}%")

      purchase_results.append(
        {
          "code": best_opportunity["code"],
          "name": best_opportunity["name"],
          "order_type": "限价单",
          "amount": purchase_amount,
          "target_rate": current_rate,
          "limit_rate": limit_rate,
          "filled_rate": order_status["filled_rate"],
          "order_id": order_id,
          "status": "success",
          "purchase_time": time_utils.now().isoformat(),
        }
      )

      return {"purchases": purchase_results, "message": "购买成功"}

    elif order_status["status"] == OrderStatus.PART_SUCC:
      # 部分成交，记录并继续（可根据需求调整）
      logger.info(f"订单部分成交，已成交: {order_status['filled_amount']}元")
      purchase_results.append(
        {
          "code": best_opportunity["code"],
          "name": best_opportunity["name"],
          "order_type": "限价单",
          "amount": purchase_amount,
          "target_rate": current_rate,
          "limit_rate": limit_rate,
          "filled_rate": order_status.get("filled_rate", 0),
          "order_id": order_id,
          "status": "partial",
          "purchase_time": time_utils.now().isoformat(),
        }
      )
      return {"purchases": purchase_results, "message": "部分成交"}

    else:
      # 未成交，取消订单并抛出异常触发重试
      logger.info("订单未成交，取消订单")
      await trading_service.cancel_order(order_id)
      raise Exception("订单未成交，触发flow重试")

  except Exception as e:
    logger.error(f"执行国债逆回购购买失败: {e}")
    raise
