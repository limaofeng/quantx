"""
市场数据相关的原子任务

包含指数、板块等市场数据的获取和保存任务
"""

import re
from typing import Any, Dict, List, Optional

from prefect import get_run_logger, task
from prefect.cache_policies import INPUTS, CachePolicy

from miniqmt import XTDataManagerRegistry
from prefector.tasks.utils import convert_xtinstrument_to_instrument
from services import SectorService
from services.instrument_service import InstrumentService

from .stock_tasks import PRICE_CACHE_EXPIRATION, SAVE_RETRIES


@task(
    name="获取市场指数",
    description="获取市场指数数据",
    cache_policy=INPUTS,
    cache_expiration=PRICE_CACHE_EXPIRATION,
    retries=2,
    retry_delay_seconds=30,
)
async def fetch_market_indices(sector_name: str) -> Dict[str, Dict[str, Any]]:
    """获取市场指数数据"""
    logger = get_run_logger()
    logger.info("开始获取市场指数数据...")

    data_registry = XTDataManagerRegistry()
    data_manager = data_registry.get_manager()

    stock_list = data_manager.get_stock_list_in_sector(sector_name)
    stock_list = list(set(stock_list))  # 去重
    logger.info(f"板块 {sector_name} 包含 {len(stock_list)} 个成分股")

    try:
        indices = data_manager.get_instrument_detail_list(stock_list, iscomplete=True)

        logger.info(f"成功获取 {len(indices)} 个指数数据")
        return indices

    except Exception as e:
        logger.error(f"获取市场指数失败: {e}")
        raise


@task(
    name="保存市场指数数据",
    description="将市场指数数据保存到数据库",
    retries=SAVE_RETRIES,
    retry_delay_seconds=30,
)
async def save_market_indices(indices: Dict[str, Dict[str, Any]]) -> int:
    """保存市场指数数据到数据库"""
    logger = get_run_logger()
    logger.info("开始保存市场指数数据...")

    instrument_service = InstrumentService()

    try:
        saved_count = 0
        for index_code, index_data in indices.items():
            instrument = convert_xtinstrument_to_instrument(index_data)
            instrument_data = await instrument_service.save(instrument)

            logger.debug(f"保存指数 {instrument_data.name} ({index_code}) 成功")
            saved_count += 1

        logger.info(f"成功保存 {saved_count} 条指数数据")
        return saved_count

    except Exception as e:
        logger.error(f"保存市场指数数据失败: {e}")
        raise


@task(
    name="下载行业板块数据",
    description="下载最新的行业板块数据",
    retries=2,
    retry_delay_seconds=30,
)
async def download_sector_data() -> None:
    """下载行业板块数据"""
    logger = get_run_logger()
    logger.info("开始下载行业板块数据...")

    from miniqmt import XTDataManagerRegistry

    data_registry = XTDataManagerRegistry()
    data_manager = data_registry.get_manager()

    try:
        data_manager.download_sector_data()
        logger.info("成功下载行业板块数据")

    except Exception as e:
        logger.error(f"下载行业板块数据失败: {e}")
        raise


@task(
    name="获取行业板块列表",
    description="获取行业板块及其层级数据",
    retries=3,
    retry_delay_seconds=60,
)
async def fetch_sector_data() -> List[Dict[str, Any]]:
    """获取格式化的行业板块列表，包含层级和分类信息"""
    logger = get_run_logger()
    logger.info("开始获取行业板块数据...")

    data_registry = XTDataManagerRegistry()
    data_manager = data_registry.get_manager()
    
    sector_list = data_manager.get_sector_list()
    logger.info(f"原生获取到 {len(sector_list)} 个板块")

    # 分类配置表: (正则表达式, 默认市场, 分类映射标识, 是否提取数字作为层级/类型)
    # 规则: 只有 harvest=True 时，正则需提供 3 个捕获组: (前缀数字, 层级数字, 名称)
    # 否则提供 1 个捕获组: (名称)
    PREFIX_CONFIG = [
        (r"^(\d+)?HKSW(\d+)?(.*)$", "HK", "HKSW", True),
        (r"^(\d+)?SW(\d+)?(.*)$", "CN", "SW", True),
        (r"^()DY(\d+)?(.*)$", "CN", "DY", True),
        (r"^(\d+)?THY(\d+)?(.*)$", "CN", "THY", True),
        (r"^(\d+)?HY(\d+)?(.*)$", "CN", "HY", True),
        
        # 1. 市场板块与宽基指数 (Market Boards & Broad Indices)
        # 按市场细分归类，避免全部识别为 CN
        
        # 港股行情指数 (优先级高)
        (r"^((?:恒生|HS|HKSW|港股|香港|联交所).*)", "HK", "MKT", False),
        
        # 美股/全球行情指数
        (r"^((?:标普|纳斯达克|道琼斯|S&P|美股|道指|纳指|FTSE).*)", "US", "MKT", False),
        
        # 其他国际市场与跨市场指数
        (r"^((?:日经|德意志|法兰克福|伦敦|全球|MSCI|外汇).*)", "GLB", "MKT", False),
        
        # A股基本指数/板块 (完全不含 TGN/GN 前缀)
        (r"^((?:上证|深证|沪深|中证|国证|创业板|科创板|北证|沪市|深市|全A|全指|A股|板块|指数|证券|ETF|分级|ESG|SME).*)", "CN", "MKT", False),
        
        # 2. 交易所/资产品类 (Exchanges & Asset Classes)
        (r"^((?:上期所|中金所|大商所|郑商所|能源中心|期货|期权|债券|基金|转债|合约|连续|仓单).*)", "CN", "EXCH", False),
        
        # 3. 纯数字开头的指数 (Numeric Indices - 放到后面以防截获 SW1 等)
        (r"^(\d{3,}.*)", "CN", "IDX", False),

        (r"^TGN(.*)$", "CN", "TGN", False),
        (r"^GN(.*)$", "CN", "GN", False),
        (r"^TFG(.*)$", "CN", "TFG", False),
        (r"^FG(.*)$", "CN", "FG", False),
        (r"^CSRC1(.*)$", "CN", "CSRC1", False),
        (r"^CSRC(.*)$", "CN", "CSRC", False),
    ]
    
    # 预处理数据
    processed_items = []
    total = len(sector_list)
    for i, full_key in enumerate(sector_list):
        if i % 100 == 0:
            logger.info(f"正在预处理板块数据: {i}/{total}...")
        
        classification = "OTH"
        name = full_key
        level = 1
        market = "CN"
        
        # 匹配正则分类
        for pattern_str, default_market, cat_id, harvest in PREFIX_CONFIG:
            match = re.match(pattern_str, full_key)
            if match:
                market = default_market
                
                if harvest:
                    # 提取了数字层级
                    prefix_digits = match.group(1) or ""
                    level_str = match.group(2) or ""
                    name_part = match.group(3)
                    
                    if cat_id == "DY":
                        classification = "DY"
                        level = int(level_str) if level_str else 1
                    else:
                        # 类似 1000SW1, THY3
                        classification = f"{prefix_digits}{cat_id}{level_str}"
                        level = 1
                    name = name_part.strip() if name_part else full_key
                else:
                    # 不提取数字，全部归入名称
                    try:
                        name_part = match.group(1)
                        classification = cat_id
                        level = 1
                        name = name_part.strip() if name_part else full_key
                    except IndexError:
                        name = full_key
                        classification = cat_id
                
                if not name:
                    name = full_key
                break
        
        # 获取成分股
        stock_list = data_manager.get_stock_list_in_sector(full_key)
        
        processed_items.append({
            "full_key": full_key,
            "name": name,
            "code": full_key, # 使用全路径 key 作为唯一 code
            "classification": classification,
            "market": market,
            "level": level,
            "stock_list": stock_list
        })

    # 为具有层级特征的板块（如 DY）计算 parent_code
    # 规则：如果 A.name 是 B.name 的前缀且 level(B) = level(A) + 1
    # 先按 level 排序，从低到高
    processed_items.sort(key=lambda x: (x["classification"], x["level"]))
    
    for i, item in enumerate(processed_items):
        if i % 100 == 0:
            logger.info(f"正在建立板块层级关系: {i}/{len(processed_items)}...")

        if item["classification"] == "DY" and item["level"] > 1:
            # 向上找最近的父级
            potential_parents = [
                p for p in processed_items[:i] 
                if p["classification"] == "DY" and 
                   p["level"] < item["level"] and 
                   item["name"].startswith(p["name"])
            ]
            if potential_parents:
                parent = max(potential_parents, key=lambda x: x["level"])
                item["parent_code"] = parent["code"]

    logger.info(f"完成格式化处理，共计 {len(processed_items)} 个板块")
    return processed_items


@task(
    name="保存板块数据",
    description="将层级化的板块数据保存到数据库",
    retries=SAVE_RETRIES,
    retry_delay_seconds=30,
)
async def save_sector_data(sector_data_list: List[Dict[str, Any]]) -> int:
    """保存板块数据到数据库，支持层级关联"""
    logger = get_run_logger()
    logger.info("开始入库板块数据...")
    sector_service = SectorService()
    
    try:
        # 第一步：建立 code 到 DB ID 的映射（先存一轮以确保基础记录存在）
        # 这里需要注意顺序：按 level 从低到高保存，确保父级先被创建
        saved_count = 0
        code_to_id = {}
        
        # 按 level 排序
        sorted_sectors = sorted(sector_data_list, key=lambda x: x["level"])
        
        for item in sorted_sectors:
            parent_id = None
            if "parent_code" in item:
                parent_id = code_to_id.get(item["parent_code"])
            
            # 使用 save_sector
            sector = await sector_service.save_sector(
                name=item["name"],
                code=item["code"],
                description=f"Level {item['level']} {item['classification']} sector",
                classification=item["classification"],
                market=item["market"],
                level=item["level"],
                parent_id=parent_id,
                stock_list=item["stock_list"]
            )
            
            if sector:
                code_to_id[item["code"]] = sector.id
                saved_count += 1
                if "1000SW" in item["code"] or "TGN" in item["code"]:
                    logger.info(f"[DEBUG] Saving: code={item['code']}, class={item['classification']}")
                if saved_count % 50 == 0:
                    logger.info(f"已保存 {saved_count} 个板块...")

        # 清理数据库中不再存在于当前列表中的板块
        all_codes = [s["code"] for s in sector_data_list]
        deleted_names = await sector_service.delete_sectors_not_in_list(all_codes)
        
        if deleted_names:
            logger.info(f"清理了 {len(deleted_names)} 个过时板块")

        logger.info(f"数据同步任务圆满完成，共入库 {saved_count} 个板块")
        return saved_count

    except Exception as e:
        logger.error(f"保存板块数据失败: {e}", exc_info=True)
        raise
