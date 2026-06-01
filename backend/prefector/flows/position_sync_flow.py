# -*- coding: utf-8 -*-
"""
持仓同步流程

获取指定账户的当前持仓数据并保存到数据库，同时可选择性地同步这些持仓标的的行情数据
"""

import datetime
from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from core.utils import time_utils
from prefector.tasks import (
    fetch_account_positions,
    generate_position_sync_report,
    save_positions,
)
from prefector.flows.daily_market_data_sync_flow import daily_market_data_sync_flow


@flow(
    name="持仓行情同步",
    description="同步持仓、保存记录并可选同步标的的 Tick 与 1m K线数据",
    retries=1,
    **STANDARD_FLOW_HOOKS
)
async def position_sync_flow(
    account_id: str = "300000013250",
    periods: List[str] = ["tick"],
    days_back: int = 0,
    sync_market_data: bool = True
) -> Dict[str, Any]:
    """
    持仓同步主流程

    1. 获取并保存持仓数据到数据库
    2. 生成同步报告
    3. (可选) 同步这些持仓标的的行情数据
    """
    logger = get_run_logger()
    start_time = time_utils.now()

    try:
        # 1. 获取并保存持仓
        logger.info(f"第一阶段: 同步账户 {account_id} 持仓到数据库")
        positions = await fetch_account_positions(account_id=account_id)

        if not positions:
            logger.info("当前无持仓，流程结束")
            return {
                "status": "success",
                "message": "No positions found",
                "saved_result": None,
                "start_time": start_time,
                "end_time": time_utils.now(),
            }

        # 保存到数据库
        save_result = await save_positions(positions)

        # 生成报告
        report = await generate_position_sync_report(positions, save_result)

        # 2. 如果开启了行情同步，则进入第二阶段
        market_sync_result = None
        if sync_market_data:
            logger.info(f"第二阶段: 开始同步 {len(positions)} 只持仓标的的行情数据")
            codes = list(set([p["stock_code"] for p in positions]))

            # 计算时间范围
            today = time_utils.now().strftime("%Y%m%d")
            start_date = (time_utils.now() - datetime.timedelta(days=days_back)).strftime("%Y%m%d")

            market_sync_result = await daily_market_data_sync_flow(
                stock_list=codes,
                periods=periods,
                start_time=start_date,
                end_time=today
            )
        else:
            logger.info("已跳过行情数据同步")

        return {
            "status": "success",
            "account_id": account_id,
            "position_report": report,
            "market_sync_result": market_sync_result,
            "start_time": start_time,
            "end_time": time_utils.now(),
        }

    except Exception as e:
        logger.error(f"持仓同步失败: {e}")
        raise
