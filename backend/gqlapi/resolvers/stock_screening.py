import logging
import math
from datetime import date
from typing import Any, Dict, List, Optional

from core.utils import time_utils
from database.relational_connection import get_async_db
from repositories.daily_signal_run_repository import DailySignalRunRepository
from repositories.indicator_snapshot_repository import IndicatorSnapshotRepository
from services.trading_time_service import TradingTimeService

from ..types.stock_screening_types import (
  SignalMeta,
  StockScreenInput,
  StockScreenItem,
  StockScreenPage,
)


logger = logging.getLogger(__name__)


SIGNAL_DEFINITIONS = [
  ("超跌反弹", "超跌反弹", "technical", "回撤、RSI、布林位置联合低位信号", 252),
  ("强势股", "强势股", "technical", "价格接近强势区间且量能放大", 252),
  ("KDJ 金叉", "KDJ 金叉", "technical", "K值向上穿越D值", 9),
  ("放量突破", "放量突破", "technical", "当日量比显著放大", 20),
  ("均线金叉", "均线金叉", "technical", "MA5向上穿越MA10", 10),
  ("布林下轨反弹", "布林下轨反弹", "technical", "价格靠近布林下轨", 20),
  ("布林上轨突破", "布林上轨突破", "technical", "价格靠近布林上轨", 20),
  ("RSI 超卖", "RSI 超卖", "technical", "RSI12低位", 24),
  ("RSI 强势", "RSI 强势", "technical", "RSI12强势区间", 24),
]


def _finite_float(value: Any, default: float = 0.0) -> float:
  if value is None:
    return default
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number if math.isfinite(number) else default


def _finite_optional_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if math.isfinite(number) else None


def _finite_int(value: Any, default: int = 0) -> int:
  number = _finite_optional_float(value)
  return default if number is None else int(number)


class StockScreeningResolver:
  @staticmethod
  def _today() -> date:
    return time_utils.today()

  @staticmethod
  async def _expected_snapshot_date(today: date) -> date:
    """Return the trading date that should have a completed signal snapshot."""
    trading_time_service = TradingTimeService()
    try:
      if await trading_time_service.is_trading_day("SH", today):
        return today
      return await trading_time_service.get_previous_trading_day("SH", today)
    except Exception as exc:
      logger.warning("交易日历判断失败，使用自然日判断选股快照新鲜度: %s", exc)
      return today

  @staticmethod
  def _stale_snapshot_warning(expected_snapshot_date: date, today: date) -> str:
    if expected_snapshot_date == today:
      return "今日快照未完成，结果来自最近可用信号快照"
    return f"{expected_snapshot_date.isoformat()} 交易日快照未完成，结果来自最近可用信号快照"

  @staticmethod
  def _score(matched: List[str], weights: Dict[str, float], volume_ratio: float, price_drop_pct: float) -> float:
    if weights:
      return round(
        sum(_finite_float(weights.get(signal, 0.0)) for signal in matched),
        4,
      )
    volume_ratio = _finite_float(volume_ratio)
    price_drop_pct = _finite_float(price_drop_pct)
    signal_score = len(matched) * 10.0
    volume_score = min(max(volume_ratio, 0.0), 3.0) * 2.0
    drawdown_score = min(abs(price_drop_pct) / 10.0, 5.0) if price_drop_pct < 0 else 0.0
    return round(signal_score + volume_score + drawdown_score, 4)

  @staticmethod
  async def stock_screen(input: StockScreenInput) -> StockScreenPage:
    limit = min(max(input.limit or 200, 1), 200)
    offset = min(max(input.offset or 0, 0), 200 * 1000)
    warnings: List[str] = []

    async for db in get_async_db():
      snapshot_repo = IndicatorSnapshotRepository(db)
      run_repo = DailySignalRunRepository(db)

      today = StockScreeningResolver._today()
      expected_snapshot_date = await StockScreeningResolver._expected_snapshot_date(today)
      run = await run_repo.find_latest_completed(
        expected_snapshot_date if input.require_fresh else None
      )
      snapshot_date = run.snapshot_date if run else await snapshot_repo.get_latest_snapshot_date()
      if run is not None:
        latest_run = run
      elif snapshot_date is not None:
        latest_run = await run_repo.find_latest(snapshot_date)
      else:
        latest_run = await run_repo.find_latest()

      if input.require_fresh and (
        run is None or snapshot_date != expected_snapshot_date
      ):
        warnings.append(
          f"{expected_snapshot_date.isoformat()} 交易日信号快照尚未完成，"
          "requireFresh=true 时不返回上次快照结果"
        )
        return StockScreenPage(
          items=[],
          total=0,
          limit=limit,
          offset=offset,
          snapshot_date=snapshot_date,
          score_version="score-v1",
          signal_version="daily-signal-v2",
          calculated_at=None,
          has_stale_data=True,
          is_complete=False,
          warnings=warnings,
        )

      if snapshot_date is None:
        if latest_run and latest_run.status in {"failed", "partial_failure"}:
          warnings.append(
            f"最近日级信号快照运行未成功: {latest_run.warnings or latest_run.status}"
          )
        if not warnings:
          warnings.append("尚无可用日级信号快照")
        return StockScreenPage(
          items=[],
          total=0,
          limit=limit,
          offset=offset,
          snapshot_date=None,
          score_version="score-v1",
          signal_version="daily-signal-v2",
          calculated_at=None,
          has_stale_data=True,
          is_complete=False,
          warnings=warnings,
        )

      metadata_run = latest_run if latest_run and latest_run.snapshot_date == snapshot_date else run
      calculated_at = metadata_run.completed_at if metadata_run else await snapshot_repo.get_latest_calculated_at(snapshot_date)
      signal_version = metadata_run.signal_version if metadata_run else f"indicator-snapshot:{snapshot_date.isoformat()}"
      score_version = metadata_run.score_version if metadata_run else "score-v1"
      has_stale_data = snapshot_date != expected_snapshot_date
      if has_stale_data:
        warnings.append(
          StockScreeningResolver._stale_snapshot_warning(
            expected_snapshot_date,
            today,
          )
        )
      if metadata_run is None:
        warnings.append("未找到信号运行元信息，已回退到快照更新时间")
      elif metadata_run.status == "partial_failure":
        warnings.append(f"日级信号快照部分完成: {metadata_run.warnings or '部分标的未成功'}")
      elif metadata_run.status == "failed":
        warnings.append(f"最近日级信号快照运行失败: {metadata_run.warnings or '未保存任何快照'}")
      if input.min_roe or input.min_net_profit_growth or input.min_yoy_growth:
        warnings.append("财务筛选字段尚未进入日级信号快照，本次仅应用技术面与行业条件")

      required_signals = [
        item.signal_code for item in input.signal_conditions or [] if item.required
      ]
      field_conditions = [
        {
          "field": item.field,
          "operator": item.operator,
          "value": item.value,
          "value_to": item.value_to,
        }
        for item in input.field_conditions or []
      ]
      records, total = await snapshot_repo.screen_snapshots(
        snapshot_date=snapshot_date,
        signal_codes=required_signals,
        field_conditions=field_conditions,
        include_industries=input.include_industries,
        exclude_industries=input.exclude_industries,
        limit=limit,
        offset=offset,
      )
      industry_map = await snapshot_repo.find_industry_names_by_codes([record.code for record in records])
      weights = {item.signal_code: item.weight for item in input.score_rules or []}

      items: List[StockScreenItem] = []
      for record in records:
        matched = list(record.matched_signals or [])
        missing = [signal for signal in required_signals if signal not in matched]
        score = StockScreeningResolver._score(
          matched,
          weights,
          record.volume_ratio,
          record.price_drop_pct,
        )
        current_price = _finite_float(record.current_price)
        open_price = _finite_float(record.open_price)
        items.append(
          StockScreenItem(
            code=record.code,
            name=record.name or record.code,
            industry=industry_map.get(record.code),
            current_price=current_price,
            open_price=open_price,
            change_pct=_finite_float(record.change_pct),
            volume=_finite_float(record.volume),
            volume_ratio=_finite_float(record.volume_ratio),
            avg_volume_20=_finite_float(record.avg_volume_20),
            is_bullish=current_price > open_price,
            peak_price=_finite_float(record.peak_price),
            days_since_peak=_finite_int(record.days_since_peak),
            price_drop_pct=_finite_float(record.price_drop_pct),
            low_price=_finite_float(record.low_price_252),
            days_since_low=_finite_int(record.days_since_low),
            price_rise_pct=_finite_float(record.price_rise_pct),
            consecutive_down_days=_finite_int(record.consecutive_down_days),
            consecutive_down_pct=_finite_float(record.consecutive_down_pct),
            k=_finite_float(record.kdj_k),
            d=_finite_float(record.kdj_d),
            j=_finite_float(record.kdj_j),
            rsi6=_finite_float(record.rsi6),
            rsi12=_finite_float(record.rsi12),
            rsi24=_finite_float(record.rsi24),
            upper_band=_finite_float(record.boll_upper),
            middle_band=_finite_float(record.boll_mid),
            lower_band=_finite_float(record.boll_lower),
            ma5=_finite_float(record.ma5),
            ma10=_finite_float(record.ma10),
            ma20=_finite_float(record.ma20),
            ma5_prev=_finite_optional_float(record.ma5_prev),
            ma10_prev=_finite_optional_float(record.ma10_prev),
            matched_strategies=matched,
            score=score,
            score_version=score_version,
            signal_version=signal_version,
            calculated_at=calculated_at,
            has_stale_data=has_stale_data,
            signal_missing=bool(missing),
            missing_signals=missing,
          )
        )

      items.sort(key=lambda item: (item.score, item.change_pct), reverse=True)
      return StockScreenPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        snapshot_date=snapshot_date,
        score_version=score_version,
        signal_version=signal_version,
        calculated_at=calculated_at,
        has_stale_data=has_stale_data,
        is_complete=metadata_run is not None and metadata_run.status == "success",
        warnings=warnings,
      )

    raise RuntimeError("数据库连接不可用")

  @staticmethod
  async def stock_signal_snapshot_meta() -> List[SignalMeta]:
    async for db in get_async_db():
      snapshot_repo = IndicatorSnapshotRepository(db)
      run_repo = DailySignalRunRepository(db)
      run = await run_repo.find_latest_completed()
      snapshot_date = run.snapshot_date if run else await snapshot_repo.get_latest_snapshot_date()
      if run is not None:
        latest_run = run
      elif snapshot_date is not None:
        latest_run = await run_repo.find_latest(snapshot_date)
      else:
        latest_run = await run_repo.find_latest()
      calculated_at = latest_run.completed_at if latest_run else (
        await snapshot_repo.get_latest_calculated_at(snapshot_date)
        if snapshot_date is not None
        else None
      )
      signal_version = latest_run.signal_version if latest_run else "daily-signal-v2"
      return [
        SignalMeta(
          signal_code=code,
          display_name=name,
          category=category,
          description=description,
          max_window=max_window,
          signal_version=signal_version,
          calculated_at=calculated_at,
          available_snapshot_date=snapshot_date,
          enabled=True,
        )
        for code, name, category, description, max_window in SIGNAL_DEFINITIONS
      ]
    return []
