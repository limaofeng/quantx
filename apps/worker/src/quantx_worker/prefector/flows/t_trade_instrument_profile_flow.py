"""Post-close materialization of causal T-trade instrument profiles."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from prefect import flow, get_run_logger
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.enums import StrategyRunStatus
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeInstrumentProfileRepository,
)
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
  HistoricalTickPaginationError,
)
from quantx_infrastructure.services.t_trade_instrument_profile_service import (
  T_TRADE_PROFILE_MAX_PAGES,
  T_TRADE_PROFILE_MAX_SOURCE_TICKS,
  T_TRADE_PROFILE_MIN_COMPLETE_DAYS,
  T_TRADE_PROFILE_PAGE_SIZE,
  T_TRADE_PROFILE_TARGET_COMPLETE_DAYS,
  TTradeInstrumentProfileService,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper
from sqlalchemy import select

PROFILE_CUTOFF = time(15, 0)
PROFILE_LOOKBACK_CALENDAR_DAYS = 60


def _parse_date(value: str) -> date:
  normalized = str(value or "").strip().replace("-", "")
  if len(normalized) != 8:
    raise ValueError("画像日期格式必须为 YYYY-MM-DD 或 YYYYMMDD")
  return datetime.strptime(normalized, "%Y%m%d").date()


async def resolve_profile_as_of(
  as_of_date: str = "",
  *,
  reference: Optional[datetime] = None,
  trading_dates: Optional[TradingDateHelper] = None,
) -> datetime:
  """Resolve the latest fully closed Shanghai trading day."""

  helper = trading_dates or TradingDateHelper()
  current = time_utils.to_shanghai(reference or time_utils.now())
  if str(as_of_date or "").strip():
    target = _parse_date(as_of_date)
    if not await helper.is_trading_date("SH", target):
      raise ValueError(f"{target.isoformat()} 不是交易日")
    if target > current.date() or (
      target == current.date() and current.time() < PROFILE_CUTOFF
    ):
      raise ValueError("画像日期尚未完整收盘")
    return datetime.combine(target, PROFILE_CUTOFF)

  target = current.date()
  if not (
    await helper.is_trading_date("SH", target)
    and current.time() >= PROFILE_CUTOFF
  ):
    target = await helper.trading_time_service.get_previous_trading_day(
      "SH",
      target,
    )
  return datetime.combine(target, PROFILE_CUTOFF)


async def resolve_profile_instruments(
  stock_list: Optional[list[str]] = None,
) -> list[str]:
  """Resolve active T-trade run instruments without introducing an account key."""

  explicit = sorted(
    {
      str(code or "").strip().upper()
      for code in (stock_list or [])
      if str(code or "").strip()
    }
  )
  if explicit:
    return explicit

  async with AsyncSessionLocal() as db:
    result = await db.execute(
      select(StrategyRun.instruments)
      .join(
        TTradeGlobalConfig,
        TTradeGlobalConfig.strategy_run_id == StrategyRun.id,
      )
      .where(
        TTradeGlobalConfig.enabled.is_(True),
        TTradeGlobalConfig.strategy_run_id.is_not(None),
        StrategyRun.status == StrategyRunStatus.RUNNING,
      )
    )
    rows = result.scalars().all()
  return sorted(
    {
      str(code or "").strip().upper()
      for instruments in rows
      for code in (instruments or [])
      if str(code or "").strip()
    }
  )


@flow(
  name="做T标的画像",
  description="收盘后从完整历史 Tick 物化 D-1 有状态机会引擎画像",
  retries=0,
)
async def t_trade_instrument_profile_flow(
  stock_list: Optional[list[str]] = None,
  as_of_date: str = "",
  lookback_calendar_days: int = PROFILE_LOOKBACK_CALENDAR_DAYS,
  target_complete_days: int = T_TRADE_PROFILE_TARGET_COMPLETE_DAYS,
  min_complete_days: int = T_TRADE_PROFILE_MIN_COMPLETE_DAYS,
) -> dict[str, Any]:
  if lookback_calendar_days < target_complete_days:
    raise ValueError("画像日历回看天数不能少于目标完整交易日数")
  if min_complete_days <= 0 or target_complete_days < min_complete_days:
    raise ValueError("画像交易日参数必须满足 0 < minimum <= target")

  logger = get_run_logger()
  as_of = await resolve_profile_as_of(as_of_date)
  instruments = await resolve_profile_instruments(stock_list)
  if not instruments:
    return {
      "status": "skipped",
      "as_of": as_of.isoformat(),
      "total": 0,
      "saved": 0,
      "insufficient": 0,
      "failed": 0,
      "errors": [],
    }

  market_data = HistoricalMarketDataService()
  profile_service = TTradeInstrumentProfileService()
  start_at = datetime.combine(
    as_of.date() - timedelta(days=max(lookback_calendar_days - 1, 0)),
    time(9, 30),
  )
  saved = 0
  insufficient = 0
  failed = 0
  errors: list[str] = []
  for code in instruments:
    try:
      pages = market_data.iter_tick_pages(
        stock_code=code,
        start_time=start_at,
        end_time=as_of,
        page_size=T_TRADE_PROFILE_PAGE_SIZE,
        max_pages=T_TRADE_PROFILE_MAX_PAGES,
        max_source_ticks=T_TRADE_PROFILE_MAX_SOURCE_TICKS,
      )
      async with AsyncSessionLocal() as db:
        await profile_service.build_and_save_profile_from_pages(
          instrument_code=code,
          pages=pages,
          as_of=as_of,
          repository=TTradeInstrumentProfileRepository(db),
          lookback_calendar_days=lookback_calendar_days,
          target_complete_days=target_complete_days,
          min_complete_days=min_complete_days,
          page_size=T_TRADE_PROFILE_PAGE_SIZE,
          max_pages=T_TRADE_PROFILE_MAX_PAGES,
          max_source_ticks=T_TRADE_PROFILE_MAX_SOURCE_TICKS,
        )
      saved += 1
    except HistoricalTickPaginationError as exc:
      failed += 1
      errors.append(f"{code}: {exc}")
      logger.error("做 T 标的画像历史 Tick 完整性失败: code=%s error=%s", code, exc)
    except ValueError as exc:
      insufficient += 1
      errors.append(f"{code}: {exc}")
      logger.warning("做 T 标的画像数据不足: code=%s error=%s", code, exc)
    except Exception as exc:
      failed += 1
      errors.append(f"{code}: {exc}")
      logger.exception("做 T 标的画像生成失败: code=%s", code)

  status = "success"
  if failed:
    status = "partial_failure" if saved else "failed"
  elif not saved:
    status = "insufficient"
  return {
    "status": status,
    "as_of": as_of.isoformat(),
    "total": len(instruments),
    "saved": saved,
    "insufficient": insufficient,
    "failed": failed,
    "errors": errors[:20],
  }


__all__ = [
  "resolve_profile_as_of",
  "resolve_profile_instruments",
  "t_trade_instrument_profile_flow",
]
