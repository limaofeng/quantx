"""
股票数据相关的原子任务

包含数据获取、保存等基础任务
"""

import asyncio
import datetime
import hashlib
import json
import random
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from prefect import get_run_logger, task
from prefect.cache_policies import INPUTS

from miniqmt import XTDataManagerRegistry
from prefector.tasks.utils import convert_xtinstrument_to_instrument
from services import InstrumentService
from core.utils import time_utils

# 缓存配置
CACHE_EXPIRATION = datetime.timedelta(minutes=30)
PRICE_CACHE_EXPIRATION = datetime.timedelta(minutes=1)

# 重试配置
DEFAULT_RETRIES = 3
SAVE_RETRIES = 2
FINANCIAL_BATCH_TIMEOUT_SECONDS = 300
FINANCIAL_TASK_TIMEOUT_SECONDS = 960
FINANCIAL_WORKER_OUTPUT_LIMIT = 4000


@task(
  name="获取股票列表",
  description="从数据源获取A股股票基础信息",
  cache_policy=INPUTS,
  cache_expiration=CACHE_EXPIRATION,
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=60,
)
async def fetch_stock_list() -> List[Dict[str, Any]]:
  """获取A股股票列表"""
  logger = get_run_logger()
  logger.info("开始获取股票列表...")

  try:
    mock_stocks = [
      {"code": "000001", "name": "平安银行", "market": "SZ"},
      {"code": "000002", "name": "万科A", "market": "SZ"},
      {"code": "600000", "name": "浦发银行", "market": "SH"},
      {"code": "600036", "name": "招商银行", "market": "SH"},
      {"code": "600519", "name": "贵州茅台", "market": "SH"},
      {"code": "000858", "name": "五粮液", "market": "SZ"},
    ]

    logger.info(f"成功获取 {len(mock_stocks)} 只股票信息")
    return mock_stocks

  except Exception as e:
    logger.error(f"获取股票列表失败: {e}")
    raise


@task(
  name="获取股票价格",
  description="获取指定股票的实时价格数据",
  cache_policy=INPUTS,
  cache_expiration=PRICE_CACHE_EXPIRATION,
  retries=2,
  retry_delay_seconds=30,
)
async def fetch_stock_prices(stock_codes: List[str]) -> Dict[str, Dict[str, Any]]:
  """获取股票实时价格"""
  logger = get_run_logger()
  logger.info(f"开始获取 {len(stock_codes)} 只股票的实时价格...")

  try:
    prices = {}
    for code in stock_codes:
      base_price = random.uniform(10.0, 100.0)
      change = random.uniform(-0.1, 0.1) * base_price

      prices[code] = {
        "current_price": round(base_price, 2),
        "change": round(change, 2),
        "change_percent": round((change / base_price) * 100, 2),
        "volume": random.randint(1000000, 50000000),
        "turnover": round(base_price * random.randint(1000000, 50000000), 2),
        "timestamp": time_utils.now().isoformat(),
      }

    logger.info(f"成功获取 {len(prices)} 只股票价格数据")
    return prices

  except Exception as e:
    logger.error(f"获取股票价格失败: {e}")
    raise


@task(
  name="保存股票数据",
  description="将股票数据保存到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=30,
)
async def save_stock_data(
  stocks: List[Dict[str, Any]], prices: Dict[str, Dict[str, Any]]
) -> int:
  """保存股票数据到数据库"""
  logger = get_run_logger()
  logger.info("开始保存股票数据到数据库...")

  try:
    saved_count = 0

    for stock in stocks:
      code = stock["code"]
      if code in prices:
        price_data = prices[code]
        combined_data = {**stock, **price_data}
        logger.debug(f"保存股票 {code} 数据: {combined_data}")
        saved_count += 1

    logger.info(f"成功保存 {saved_count} 条股票数据")
    return saved_count

  except Exception as e:
    logger.error(f"保存股票数据失败: {e}")
    raise


@task(
  name="更新价格缓存",
  description="更新实时价格数据到缓存系统",
  retries=1,
  retry_delay_seconds=10,
)
async def update_price_cache(prices: Dict[str, Dict[str, Any]]) -> bool:
  """更新实时价格数据到缓存系统（如Redis）"""
  logger = get_run_logger()
  logger.info("开始更新实时价格缓存...")

  try:
    for code, price_data in prices.items():
      logger.debug(f"更新股票 {code} 缓存: {price_data}")

    logger.info(f"成功更新 {len(prices)} 条价格缓存")
    return True

  except Exception as e:
    logger.error(f"更新价格缓存失败: {e}")
    return False


# ===================== 新增：单股票处理相关 tasks =====================


@task(
  name="获取沪深国债逆回购代码列表",
  description="获取所有国债逆回购代码列表",
)
async def fetch_all_trr_codes() -> List[str]:
  """获取所有国债逆回购代码列表"""
  logger = get_run_logger()
  logger.info("开始获取所有国债逆回购代码列表...")

  try:
    data_registry = XTDataManagerRegistry()
    data_manager = data_registry.get_manager()

    trr_codes = data_manager.get_stock_list_in_sector("国债逆回购")

    logger.info(f"成功获取 {len(trr_codes)} 只国债逆回购代码")
    return trr_codes

  except Exception as e:
    logger.error(f"获取国债逆回购代码列表失败: {e}")
    raise


@task(
  name="获取指定板块代码列表",
  description="获取指定板块（如沪深A股、ETF、指数）的代码列表",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=60,
)
async def fetch_instrument_codes(sectors: List[str] = None) -> List[str]:
  """
  获取指定板块的标的代码列表

  Args:
      sectors: 板块名称列表，如 ["沪深A股", "沪深ETF"]

  Returns:
      代码列表
  """
  logger = get_run_logger()
  if not sectors:
      sectors = ["沪深A股"]
      
  logger.info(f"开始获取板块代码: {sectors}")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  try:
    all_codes = []
    for sector in sectors:
        codes = data_manager.get_stock_list_in_sector(sector)
        logger.info(f"板块 [{sector}] 获取到 {len(codes)} 只标的")
        all_codes.extend(codes)
    
    # 去重
    all_codes = list(set(all_codes))
    return all_codes
  except Exception as e:
    logger.error(f"获取板块代码列表失败: {e}")
    raise


@task(
  name="获取单只股票信息",
  description="获取单只股票的基础信息",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=30,
)
async def fetch_stock_info(stock_code: str) -> Dict[str, Any]:
  """获取单只股票基础信息"""
  logger = get_run_logger()
  logger.debug(f"获取股票 {stock_code} 基础信息")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  try:
    # iscomplete=True 确保获取到完整的扩展信息
    instrument_detail = data_manager.get_instrument_detail(stock_code, iscomplete=True)
    # logger.debug(f"获取到股票信息: {instrument_detail}")
    return instrument_detail

  except Exception as e:
    logger.error(f"获取股票 {stock_code} 信息失败: {e}")
    raise


@task(
    name="获取财务数据",
    description="获取股票财务数据",
    retries=2
)
async def fetch_stock_financial_data(stock_code: str) -> Dict[str, Any]:
  """获取单只股票的财务数据"""
  logger = get_run_logger()
  logger.debug(f"获取股票 {stock_code} 财务数据")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  try:
    financial_data_map = data_manager.get_financial_data_list([stock_code])
    financial_data = financial_data_map.get(stock_code, {})
    # logger.debug(f"获取到财务数据: {financial_data}")
    return financial_data

  except Exception as e:
    logger.error(f"获取股票 {stock_code} 财务数据失败: {e}")
    msg = str(e)
    # 如果是空数据，不抛出异常，只记录
    if "No financial data" in msg:
        return {}
    raise


@task(
  name="获取单只股票价格",
  description="获取单只股票的价格数据",
  cache_policy=INPUTS,
  cache_expiration=PRICE_CACHE_EXPIRATION,
  retries=2,
  retry_delay_seconds=20,
)
async def fetch_single_stock_price(stock_code: str) -> Dict[str, Any]:
  """获取单只股票价格数据"""
  logger = get_run_logger()
  logger.debug(f"获取股票 {stock_code} 价格数据")

  try:
    # 模拟获取实时价格
    base_price = random.uniform(10.0, 100.0)
    change = random.uniform(-0.1, 0.1) * base_price

    price_data = {
      "stock_code": stock_code,
      "trade_date": time_utils.today().isoformat(),
      "open_price": round(base_price * 0.99, 2),
      "close_price": round(base_price, 2),
      "high_price": round(base_price * 1.05, 2),
      "low_price": round(base_price * 0.95, 2),
      "change": round(change, 2),
      "change_percent": round((change / base_price) * 100, 2),
      "volume": random.randint(1000000, 50000000),
      "turnover": round(base_price * random.randint(1000000, 50000000), 2),
      "timestamp": time_utils.now().isoformat(),
    }

    logger.debug(f"获取到价格数据: {price_data}")
    return price_data

  except Exception as e:
    logger.error(f"获取股票 {stock_code} 价格失败: {e}")
    raise


@task(name="验证股票数据", description="验证股票数据的完整性和有效性", retries=1)
async def validate_stock_data(
  stock_info: Dict[str, Any], price_data: Dict[str, Any]
) -> Dict[str, Any]:
  """验证股票数据完整性"""
  logger = get_run_logger()

  errors = []
  warnings = []

  try:
    # 验证股票信息
    if not stock_info:
      errors.append("股票基础信息为空")
    else:
      required_fields = ["code", "name", "market"]
      for field in required_fields:
        if not stock_info.get(field):
          errors.append(f"缺少必要字段: {field}")

    # 验证价格数据
    if not price_data:
      errors.append("价格数据为空")
    else:
      required_price_fields = ["close_price", "volume", "trade_date"]
      for field in required_price_fields:
        if price_data.get(field) is None:
          errors.append(f"缺少价格字段: {field}")

      # 价格合理性检查
      close_price = price_data.get("close_price", 0)
      if close_price <= 0:
        errors.append("收盘价必须大于0")
      elif close_price > 1000:
        warnings.append("收盘价异常高（>1000）")

      # 成交量检查
      volume = price_data.get("volume", 0)
      if volume < 0:
        errors.append("成交量不能为负数")
      elif volume == 0:
        warnings.append("成交量为0，可能停牌")

    is_valid = len(errors) == 0

    result = {
      "is_valid": is_valid,
      "errors": errors,
      "warnings": warnings,
      "validation_time": time_utils.now().isoformat(),
    }

    if warnings:
      logger.warning(f"数据验证警告: {warnings}")

    return result

  except Exception as e:
    logger.error(f"数据验证异常: {e}")
    return {
      "is_valid": False,
      "errors": [f"验证过程异常: {e}"],
      "warnings": [],
      "validation_time": time_utils.now().isoformat(),
    }


@task(
  name="保存单只股票数据",
  description="保存单只股票的完整数据",
  retries=SAVE_RETRIES,
  retry_delay_seconds=30,
)
async def save_single_stock_data(
  stock_code: str, stock_info: Dict[str, Any]
) -> Dict[str, Any]:
  """保存单只股票数据"""
  logger = get_run_logger()
  logger.debug(f"保存股票 {stock_code} 数据")

  instrument_service = InstrumentService()

  try:
    instrument = convert_xtinstrument_to_instrument(stock_info)

    instrument_data = await instrument_service.save(instrument)

    # 这里应该是实际的数据库保存逻辑
    logger.debug(f"保存数据: {instrument_data.id} - {instrument_data.name}")

    result = {
      "stock_code": stock_code,
      "records_count": 1,  # 实际可能包含多条记录（历史数据等）
      "save_time": time_utils.now().isoformat(),
    }

    logger.debug(f"保存成功: {result}")
    return result

  except Exception as e:
    logger.error(f"保存股票 {stock_code} 数据失败: {e}")
    raise


@task(
  name="批量获取股票信息",
  description="批量获取股票的基础信息",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=30,
)
async def fetch_batch_instrument_infos(stock_codes: List[str]) -> List[Dict[str, Any]]:
  """批量获取股票基础信息"""
  logger = get_run_logger()
  logger.info(f"开始批量获取 {len(stock_codes)} 只标的基础信息")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  try:
    # iscomplete=True 确保获取到完整的扩展信息
    details_map = data_manager.get_instrument_detail_list(stock_codes, iscomplete=True)
    
    # 转换为列表，确保包含 code
    result_list = []
    for code, detail in details_map.items():
        if detail:
            # 确保 code 字段存在
            detail["code"] = code
            result_list.append(detail)
            
    logger.info(f"成功获取 {len(result_list)} 条详细信息")
    return result_list

  except Exception as e:
    logger.error(f"批量获取股票信息失败: {e}")
    raise


@task(
  name="批量保存股票数据",
  description="批量保存股票数据到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=30,
)
async def save_batch_stock_data(
  stock_infos: List[Dict[str, Any]]
) -> int:
  """批量保存股票数据"""
  logger = get_run_logger()
  logger.info(f"开始批量保存 {len(stock_infos)} 条股票数据")

  instrument_service = InstrumentService()

  try:
    instruments = []
    for info in stock_infos:
        try:
            inst = convert_xtinstrument_to_instrument(info)
            instruments.append(inst)
        except Exception as e:
            # 个别转换失败不应阻塞整体
            logger.warning(f"转换标的数据失败: {info.get('InstrumentID')}, {e}")
            
    if not instruments:
        return 0
        
    saved_count = await instrument_service.save_batch(instruments)
    logger.info(f"成功保存 {saved_count} 条记录")
    return saved_count

  except Exception as e:
    logger.error(f"批量保存股票数据失败: {e}")
    raise

@task(
    name="批量分片同步标的信息",
    description="同步一批标的的基础信息（获取+保存）",
    retries=3,
    retry_delay_seconds=60,
)
async def sync_instruments_batch_task(stock_codes: List[str]) -> Dict[str, Any]:
    """批量同步标的信息任务"""
    logger = get_run_logger()
    
    result = {
        "total": len(stock_codes),
        "success": 0,
        "failed": 0,
        "saved_count": 0,
    }
    
    if not stock_codes:
        return result
        
    try:
        # 1. 获取信息 (直接调用 task 函数的 __wrapped__ 或者直接写逻辑)
        # 为了避免嵌套 Task，我们直接在这里写逻辑(或调用内部函数)
        data_registry = XTDataManagerRegistry()
        data_manager = data_registry.get_manager()
        details_map = data_manager.get_instrument_detail_list(stock_codes, iscomplete=True)
        
        instrument_infos = []
        for code, detail in details_map.items():
            if detail:
                detail["code"] = code
                instrument_infos.append(detail)
                
        if not instrument_infos:
            result["failed"] = len(stock_codes)
            return result

        # 2. 保存信息
        instrument_service = InstrumentService()
        instruments = []
        for info in instrument_infos:
            try:
                inst = convert_xtinstrument_to_instrument(info)
                instruments.append(inst)
            except Exception as e:
                logger.warning(f"转换标的数据失败: {info.get('InstrumentID')}, {e}")

        if instruments:
            saved_count = await instrument_service.save_batch(instruments)
            result["saved_count"] = saved_count
            result["success"] = saved_count
            
        result["failed"] = result["total"] - result["success"]
        return result

    except Exception as e:
        logger.error(f"分片同步任务失败: {e}")
        raise

@task(
    name="批量获取财务数据",
    description="批量获取股票财务数据",
    retries=2
)
async def fetch_batch_financial_data(stock_codes: List[str]) -> Dict[str, Any]:
  """批量获取股票财务数据"""
  logger = get_run_logger()
  logger.info(f"开始批量获取 {len(stock_codes)} 只标的财务数据")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  try:
    financial_data_map = data_manager.get_financial_data_list(stock_codes)
    logger.info(f"成功获取 {len(financial_data_map)} 只标的财务数据")
    return financial_data_map

  except Exception as e:
    logger.error(f"批量获取财务数据失败: {e}")
    return {}


@task(
    name="批量保存财务数据",
    description="批量保存股票财务数据",
    retries=SAVE_RETRIES
)
async def save_batch_financial_data(financial_data_map: Dict[str, Any]) -> int:
    """批量保存财务数据到数据库
    
    Args:
        financial_data_map: {stock_code: {table_name: DataFrame}} 格式的数据
        
    Returns:
        保存成功的记录总数
    """
    from services.financial_service import FinancialService
    
    logger = get_run_logger()
    
    if not financial_data_map:
        return 0
        
    logger.info(f"开始批量保存 {len(financial_data_map)} 只标的财务数据")
    
    try:
        service = FinancialService()
        total_saved = await service.save_batch_financial_data(financial_data_map)
        logger.info(f"成功保存 {total_saved} 条财务数据记录")
        return total_saved
    except Exception as e:
        logger.error(f"批量保存财务数据失败: {e}")
        raise


@task(
    name="批量分片同步财务数据",
    description="同步一批标的的财务数据（获取+保存）",
    retries=1,
    retry_delay_seconds=30,
    timeout_seconds=FINANCIAL_TASK_TIMEOUT_SECONDS,
)
async def sync_financial_batch_task(
    stock_codes: List[str],
    batch_index: Optional[int] = None,
    batch_total: Optional[int] = None,
    timeout_seconds: int = FINANCIAL_BATCH_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """批量分片同步财务数据任务"""
    logger = get_run_logger()
    
    result = _financial_batch_result_base(
        stock_codes=stock_codes,
        batch_index=batch_index,
        batch_total=batch_total,
    )
    
    if not stock_codes:
        return result
        
    batch_label = result["batch_label"]
    logger.info(
        f"开始财务分片同步: batch={batch_label}, 股票数={len(stock_codes)}, "
        f"范围={stock_codes[0]}~{stock_codes[-1]}, 超时={timeout_seconds}s"
    )

    try:
        worker_result = await _run_financial_batch_worker(
            stock_codes=stock_codes,
            timeout_seconds=timeout_seconds,
        )
        worker_result = {
            **_financial_batch_result_base(
                stock_codes=stock_codes,
                batch_index=batch_index,
                batch_total=batch_total,
            ),
            **worker_result,
            "batch_index": batch_index,
            "batch_total": batch_total,
            "batch_label": batch_label,
            "stock_range_start": stock_codes[0],
            "stock_range_end": stock_codes[-1],
        }
        logger.info(
            f"财务分片同步完成: batch={batch_label}, "
            f"range={stock_codes[0]}~{stock_codes[-1]}, "
            f"success={worker_result.get('success', 0)}, "
            f"failed={worker_result.get('failed', 0)}, "
            f"saved={worker_result.get('saved_count', 0)}, "
            f"status={worker_result.get('status')}"
        )
        return worker_result
    except Exception as e:
        logger.error(f"财务分片同步任务失败: batch={batch_label}, error={e}")
        raise


def _financial_batch_result_base(
    stock_codes: List[str],
    batch_index: Optional[int],
    batch_total: Optional[int],
) -> Dict[str, Any]:
    batch_label = (
        f"{batch_index}/{batch_total}"
        if batch_index is not None and batch_total is not None
        else "unknown"
    )
    return {
        "total": len(stock_codes),
        "success": 0,
        "failed": 0,
        "saved_count": 0,
        "status": "success",
        "batch_index": batch_index,
        "batch_total": batch_total,
        "batch_label": batch_label,
        "stock_range_start": stock_codes[0] if stock_codes else None,
        "stock_range_end": stock_codes[-1] if stock_codes else None,
        "stock_codes": stock_codes,
    }


def _financial_batch_request_id(stock_codes: List[str]) -> str:
    payload = json.dumps(
        stock_codes,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _run_financial_batch_worker(
    stock_codes: List[str],
    timeout_seconds: int,
) -> Dict[str, Any]:
    backend_root = Path(__file__).resolve().parents[2]
    request_id = _financial_batch_request_id(stock_codes)

    with tempfile.TemporaryDirectory(prefix="quantx_financial_sync_") as tmp_dir:
        input_path = Path(tmp_dir) / "input.json"
        output_path = Path(tmp_dir) / "output.json"
        input_path.write_text(
            json.dumps(
                {
                    "stock_codes": stock_codes,
                    "request_id": request_id,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        process = None
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "prefector.workers.financial_batch_worker",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            cwd=str(backend_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            stdout, stderr = await _kill_financial_batch_process(process)
            raise TimeoutError(
                f"财务批次 worker 超时 {timeout_seconds}s，"
                f"股票范围 {stock_codes[0]}~{stock_codes[-1]}，"
                f"stdout={_decode_worker_output(stdout)}, "
                f"stderr={_decode_worker_output(stderr)}"
            ) from exc
        except asyncio.CancelledError:
            await _kill_financial_batch_process(process)
            raise

        if output_path.exists():
            result = json.loads(output_path.read_text(encoding="utf-8"))
        else:
            result = {}

        if process.returncode != 0:
            raise RuntimeError(
                f"财务批次 worker 退出码 {process.returncode}，"
                f"result={result}, stdout={_decode_worker_output(stdout)}, "
                f"stderr={_decode_worker_output(stderr)}"
            )

        if not result:
            raise RuntimeError(
                f"财务批次 worker 未返回结果，stdout={_decode_worker_output(stdout)}, "
                f"stderr={_decode_worker_output(stderr)}"
            )

        if result.get("request_id") != request_id:
            raise RuntimeError(
                f"财务批次 worker 返回 request_id 不匹配，"
                f"expected={request_id}, actual={result.get('request_id')}"
            )

        if result.get("stock_codes") != stock_codes:
            raise RuntimeError(
                f"财务批次 worker 返回股票列表不匹配，"
                f"expected={stock_codes[0]}~{stock_codes[-1]}, "
                f"actual={result.get('stock_codes')}"
            )

        return result


async def _kill_financial_batch_process(process) -> Tuple[bytes, bytes]:
    if process is None:
        return b"", b""

    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass

    try:
        return await asyncio.wait_for(process.communicate(), timeout=10)
    except asyncio.TimeoutError:
        return b"", b"worker killed but output collection timed out"


def _decode_worker_output(output: bytes) -> str:
    text = output.decode("utf-8", errors="replace").strip()
    if len(text) <= FINANCIAL_WORKER_OUTPUT_LIMIT:
        return text
    return text[-FINANCIAL_WORKER_OUTPUT_LIMIT:]
