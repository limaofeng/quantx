import logging
import math
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

from quantx_infrastructure.core.assistant_strategy_policy import (
  LIMIT_UP_BOARD_STRATEGY_CLASS_NAME,
  is_active_execution_run,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import get_async_db
from quantx_infrastructure.repositories.daily_signal_run_repository import (
  DailySignalRunRepository,
)
from quantx_infrastructure.repositories.indicator_snapshot_repository import (
  IndicatorSnapshotRepository,
)
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.financial_sync_health_service import (
  financial_sync_health,
)
from quantx_infrastructure.services.limit_up_radar import (
  RADAR_SCORE_VERSION,
  limit_up_radar_store,
)
from quantx_infrastructure.services.trading_time_service import (
  TradingDateHelper,
  TradingTimeService,
)

from ..types.financial_types import FinancialSyncHealthStatus
from ..types.stock_screening_types import (
  IntradayVolumeScreenInput,
  IntradayVolumeScreenItem,
  IntradayVolumeScreenPage,
  LimitUpRadarEvent,
  LimitUpRadarIndustryHeat,
  LimitUpRadarInput,
  LimitUpRadarItem,
  LimitUpRadarPage,
  LimitUpRadarScoreFactor,
  LimitUpRadarSortField,
  LimitUpRadarStage,
  LimitUpRadarSummary,
  RoeQualityStatus,
  SignalMeta,
  StockScreenFinancialHealth,
  StockScreenInput,
  StockScreenItem,
  StockScreenPage,
  StockScreenSnapshotStatus,
)

logger = logging.getLogger(__name__)


SIGNAL_DEFINITIONS = [
  ("超跌反弹", "超跌反弹", "technical", "回撤、RSI、布林位置联合低位信号", 252),
  ("强势股", "强势股", "technical", "价格接近强势区间且量能放大", 252),
  ("KDJ 金叉", "KDJ 金叉", "technical", "K值向上穿越D值", 9),
  ("放量突破", "放量突破", "technical", "当日量比显著放大", 20),
  ("放量上涨", "放量上涨", "technical", "上涨且20日量比显著放大", 20),
  ("放量下跌", "放量下跌", "technical", "下跌且20日量比显著放大", 20),
  ("成交额放大", "成交额放大", "technical", "当日成交额相对20日均额放大", 20),
  ("高换手", "高换手", "technical", "按流通股本估算的换手率较高", 20),
  ("缩量调整", "缩量调整", "technical", "下跌但成交量低于20日均量", 20),
  ("高位放量滞涨", "高位放量滞涨", "technical", "高位区域放量但涨跌幅有限", 20),
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


def _instrument_type_value(value: Any) -> str:
  enum_value = getattr(value, "value", None)
  if enum_value is not None:
    value = enum_value
  text = str(value or "stock")
  if "." in text:
    text = text.rsplit(".", 1)[-1]
  text = text.lower()
  return text if text in {"stock", "etf"} else "stock"


def _parse_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if value in (None, ""):
    return None
  try:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError:
    return None


def _radar_stage(value: Any) -> LimitUpRadarStage:
  try:
    return LimitUpRadarStage(str(value or "MOMENTUM"))
  except ValueError:
    return LimitUpRadarStage.MOMENTUM


class StockScreeningResolver:
  SNAPSHOT_CUTOFF = time(15, 35)

  @staticmethod
  def _today() -> date:
    return time_utils.today()

  @staticmethod
  def _now() -> datetime:
    return time_utils.now()

  @staticmethod
  async def _expected_snapshot_date(today: date) -> date:
    """Return the trading date that should have a completed signal snapshot."""
    trading_time_service = TradingTimeService()
    try:
      now = StockScreeningResolver._now()
      if (
        await trading_time_service.is_trading_day("SH", today)
        and (
          today < now.date()
          or (
            today == now.date()
            and now.time() >= StockScreeningResolver.SNAPSHOT_CUTOFF
          )
        )
      ):
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
  async def stock_screen_snapshot_status(
    lookback_days: int = 30,
  ) -> StockScreenSnapshotStatus:
    """返回当前应有快照、缺失交易日和最近运行状态。"""
    normalized_lookback = min(max(int(lookback_days or 30), 1), 30)
    today = StockScreeningResolver._today()
    expected_date = await StockScreeningResolver._expected_snapshot_date(today)
    window_start = expected_date - timedelta(days=normalized_lookback - 1)
    warnings: List[str] = []

    async for db in get_async_db():
      snapshot_repo = IndicatorSnapshotRepository(db)
      run_repo = DailySignalRunRepository(db)
      latest_snapshot_date = await snapshot_repo.get_latest_snapshot_date()
      latest_run = await run_repo.find_latest()
      successful_expected_run = await run_repo.find_latest_completed(
        expected_date
      )

      if latest_snapshot_date is None:
        missing_dates = [expected_date]
        warnings.append("尚无可用日级信号快照")
        latest_calculated_at = (
          latest_run.completed_at if latest_run is not None else None
        )
      else:
        snapshot_dates = set(
          await snapshot_repo.find_snapshot_dates(
            window_start,
            expected_date,
          )
        )
        completed_dates = set(
          await run_repo.find_completed_dates(
            window_start,
            expected_date,
          )
        )
        available_dates = snapshot_dates & completed_dates
        history_anchor = min(
          available_dates or snapshot_dates or {expected_date}
        )
        calendar_start = max(window_start, min(history_anchor, expected_date))
        helper = TradingDateHelper()
        trading_dates = await helper.get_trading_calendar(
          "SH",
          start_date=calendar_start,
          end_date=expected_date,
        )
        missing_dates = [
          target for target in trading_dates if target not in available_dates
        ]
        latest_snapshot_run = await run_repo.find_latest(latest_snapshot_date)
        latest_calculated_at = (
          latest_snapshot_run.completed_at
          if latest_snapshot_run is not None
          else await snapshot_repo.get_latest_calculated_at(
            latest_snapshot_date
          )
        )

      if latest_run is not None and latest_run.status in {
        "failed",
        "partial_failure",
      }:
        warnings.append(
          "最近快照运行未成功: "
          f"{latest_run.warnings or latest_run.status}"
        )
      if missing_dates:
        warnings.append(
          f"缺少 {len(missing_dates)} 个交易日快照"
        )

      return StockScreenSnapshotStatus(
        latest_snapshot_date=latest_snapshot_date,
        expected_snapshot_date=expected_date,
        missing_snapshot_dates=missing_dates,
        is_complete=(
          not missing_dates
          and latest_snapshot_date == expected_date
          and successful_expected_run is not None
        ),
        latest_run_status=(
          latest_run.status if latest_run is not None else None
        ),
        latest_calculated_at=latest_calculated_at,
        warnings=warnings,
      )

    raise RuntimeError("数据库连接不可用")

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
      sort = (
        {
          "field": input.sort.field.value,
          "direction": input.sort.direction.value,
        }
        if input.sort
        else None
      )
      records, total = await snapshot_repo.screen_snapshots(
        snapshot_date=snapshot_date,
        signal_codes=required_signals,
        field_conditions=field_conditions,
        include_industries=input.include_industries,
        exclude_industries=input.exclude_industries,
        sort=sort,
        min_roe=input.min_roe,
        min_net_profit_growth=input.min_net_profit_growth,
        min_yoy_growth=input.min_yoy_growth,
        limit=limit,
        offset=offset,
        universe=input.universe.value,
        exclude_st=input.exclude_st,
      )
      try:
        financial_health_data = await financial_sync_health(db)
      except Exception:
        logger.exception("读取财务同步健康状态失败")
        financial_health_data = {
          "status": "FAILED",
          "last_success_at": None,
          "requested_codes": 0,
          "synced_codes": 0,
          "warnings": ["财务同步健康状态读取失败"],
        }
      try:
        financial_quality_counts = await snapshot_repo.financial_quality_counts(
          snapshot_date,
          include_industries=input.include_industries,
          exclude_industries=input.exclude_industries,
          universe=input.universe.value,
          exclude_st=input.exclude_st,
        )
      except (AttributeError, NotImplementedError):
        financial_quality_counts = {
          "verified": int(financial_health_data.get("synced_codes") or 0),
          "selectable": 0,
          "stale": 0,
          "suspicious": 0,
          "invalid": 0,
          "unverified": 0,
        }
      financial_health = StockScreenFinancialHealth(
        status=FinancialSyncHealthStatus(financial_health_data["status"]),
        last_success_at=financial_health_data.get("last_success_at"),
        verified_count=int(financial_quality_counts.get("verified") or 0),
        selectable_count=int(financial_quality_counts.get("selectable") or 0),
        excluded_stale_count=int(financial_quality_counts.get("stale") or 0),
        excluded_suspicious_count=int(
          financial_quality_counts.get("suspicious") or 0
        ),
        excluded_invalid_count=int(financial_quality_counts.get("invalid") or 0),
        excluded_unverified_count=int(
          financial_quality_counts.get("unverified") or 0
        ),
      )
      financial_filter_active = any(
        value is not None
        for value in [
          input.min_roe,
          input.min_net_profit_growth,
          input.min_yoy_growth,
        ]
      )
      if financial_filter_active:
        if financial_health_data["status"] != "SUCCESS":
          coverage = (
            f"{financial_health_data.get('synced_codes', 0)}/"
            f"{financial_health_data.get('requested_codes', 0)}"
          )
          detail = "；".join(financial_health_data.get("warnings") or [])
          warnings.append(
            "财务同步状态异常，未验证或质量异常的数据已排除："
            f"状态={financial_health_data['status']}，覆盖={coverage}"
            + (f"；{detail}" if detail else "")
          )
      if financial_filter_active and total == 0:
        warnings.append("未找到满足财务指标条件的标的，未公告或质量异常的财报不会通过财务筛选")
      industry_map = await snapshot_repo.find_industry_names_by_codes([record.code for record in records])
      instrument_type_map = (
        await snapshot_repo.find_instrument_types_by_codes([record.code for record in records])
        if hasattr(snapshot_repo, "find_instrument_types_by_codes")
        else {}
      )
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
        financial_metric = getattr(record, "financial_metric", None)
        financial_audit = getattr(record, "financial_audit", None)
        raw_roe_quality_status = getattr(
          record,
          "roe_quality_status",
          "UNVERIFIED",
        )
        try:
          roe_quality_status = RoeQualityStatus(raw_roe_quality_status)
        except ValueError:
          roe_quality_status = RoeQualityStatus.UNVERIFIED
        items.append(
          StockScreenItem(
            code=record.code,
            name=record.name or record.code,
            industry=industry_map.get(record.code),
            instrument_type=_instrument_type_value(
              instrument_type_map.get(record.code)
              or getattr(record, "instrument_type", None)
            ),
            current_price=current_price,
            open_price=open_price,
            change_pct=_finite_float(record.change_pct),
            volume=_finite_float(record.volume),
            volume_ratio=_finite_float(record.volume_ratio),
            avg_volume_20=_finite_float(record.avg_volume_20),
            avg_volume_5=_finite_float(getattr(record, "avg_volume_5", None)),
            volume_ratio_5=_finite_float(getattr(record, "volume_ratio_5", None)),
            avg_amount_20=_finite_float(getattr(record, "avg_amount_20", None)),
            amount_ratio_20=_finite_float(getattr(record, "amount_ratio_20", None)),
            turnover_rate_pct=_finite_optional_float(
              getattr(record, "turnover_rate_pct", None)
            ),
            volume_percentile_60=_finite_float(
              getattr(record, "volume_percentile_60", None)
            ),
            amount_percentile_60=_finite_float(
              getattr(record, "amount_percentile_60", None)
            ),
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
            roe=(
              _finite_optional_float(getattr(financial_metric, "roe_ttm", None))
              if roe_quality_status is RoeQualityStatus.VALID
              else None
            ),
            roe_quality_status=roe_quality_status,
            net_profit_growth=_finite_optional_float(
              getattr(financial_metric, "net_profit_quarter_growth_pct", None)
            ),
            yoy_growth=_finite_optional_float(
              getattr(financial_metric, "revenue_quarter_growth_pct", None)
            ),
            net_profit_accum_growth=_finite_optional_float(
              getattr(financial_metric, "net_profit_growth_pct", None)
            ),
            revenue_accum_growth=_finite_optional_float(
              getattr(financial_metric, "revenue_growth_pct", None)
            ),
            financial_report_date=getattr(financial_metric, "report_date", None),
            financial_announce_date=getattr(financial_metric, "announce_date", None),
            financial_as_of_date=getattr(financial_metric, "as_of_date", None),
            financial_verified_at=getattr(financial_audit, "verified_at", None),
            financial_quality_flags=list(
              getattr(record, "roe_quality_flags", None) or []
            ),
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

      if sort is None:
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
        is_complete=(
          not has_stale_data
          and metadata_run is not None
          and metadata_run.status == "success"
        ),
        warnings=warnings,
        financial_health=financial_health,
      )

    raise RuntimeError("数据库连接不可用")

  @staticmethod
  async def intraday_volume_screen(
    input: IntradayVolumeScreenInput,
  ) -> IntradayVolumeScreenPage:
    limit = min(max(input.limit or 200, 1), 200)
    offset = min(max(input.offset or 0, 0), 200 * 1000)
    page = await limit_up_radar_store.read_intraday()
    if page is None:
      return IntradayVolumeScreenPage(
        items=[],
        top_gainers=[],
        top_losers=[],
        advancers=0,
        decliners=0,
        flats=0,
        total=0,
        limit=limit,
        offset=offset,
        updated_at=None,
        is_scanner_running=False,
        warnings=["Engine 全市场扫描尚未就绪，请检查 Engine 与 QMT Agent"],
      )

    include_industries = set(input.include_industries or [])
    exclude_industries = set(input.exclude_industries or [])
    universe = input.universe.value

    def matches(item: Dict[str, Any]) -> bool:
      instrument_type = str(item.get("instrument_type") or "stock")
      if universe != "stock_and_etf" and instrument_type != universe:
        return False
      industry = item.get("industry")
      if include_industries and industry not in include_industries:
        return False
      if exclude_industries and industry in exclude_industries:
        return False
      thresholds = (
        (input.min_volume_pace_ratio, "volume_pace_ratio"),
        (input.min_amount_pace_ratio, "amount_pace_ratio"),
        (input.min_last_5m_volume_ratio, "last_5m_volume_ratio"),
        (input.min_intraday_turnover_rate, "intraday_turnover_rate_pct"),
        (input.min_depth_imbalance_5, "depth_imbalance_5"),
      )
      return all(
        threshold is None
        or item.get(key) is not None
        and _finite_float(item.get(key)) >= threshold
        for threshold, key in thresholds
      )

    matched = [item for item in list(page.get("items") or []) if matches(item)]
    advancers = sum(
      1 for item in matched if _finite_float(item.get("change_pct")) > 0.000001
    )
    decliners = sum(
      1 for item in matched if _finite_float(item.get("change_pct")) < -0.000001
    )
    flats = len(matched) - advancers - decliners
    top_gainers = sorted(
      (
        item
        for item in matched
        if _finite_float(item.get("change_pct")) > 0.000001
      ),
      key=lambda item: _finite_float(item.get("change_pct")),
      reverse=True,
    )[:10]
    top_losers = sorted(
      (
        item
        for item in matched
        if _finite_float(item.get("change_pct")) < -0.000001
      ),
      key=lambda item: _finite_float(item.get("change_pct")),
    )[:10]
    matched.sort(
      key=lambda item: (
        _finite_float(item.get("volume_pace_ratio")),
        _finite_float(item.get("amount_pace_ratio")),
        _finite_float(item.get("last_5m_volume_ratio")),
      ),
      reverse=True,
    )
    page_items = matched[offset : offset + limit]
    warnings = list(page.get("warnings") or [])
    if not page_items:
      warnings.append("尚未收到匹配条件的全市场实时 tick")

    def to_graphql_item(item: Dict[str, Any]) -> IntradayVolumeScreenItem:
      return IntradayVolumeScreenItem(
        code=str(item.get("code") or ""),
        name=str(item.get("name") or item.get("code") or ""),
        industry=item.get("industry"),
        instrument_type=str(item.get("instrument_type") or "stock"),
        current_price=_finite_float(item.get("current_price")),
        change_pct=_finite_float(item.get("change_pct")),
        volume=_finite_float(item.get("volume")),
        amount=_finite_float(item.get("amount")),
        volume_ratio=_finite_float(item.get("volume_ratio")),
        amount_ratio=_finite_float(item.get("amount_ratio")),
        volume_pace_ratio=_finite_float(item.get("volume_pace_ratio")),
        amount_pace_ratio=_finite_float(item.get("amount_pace_ratio")),
        last_5m_volume_ratio=_finite_float(item.get("last_5m_volume_ratio")),
        intraday_turnover_rate_pct=_finite_optional_float(
          item.get("intraday_turnover_rate_pct")
        ),
        depth_imbalance_5=_finite_float(item.get("depth_imbalance_5")),
        avg_trade_amount_proxy=_finite_optional_float(
          item.get("avg_trade_amount_proxy")
        ),
        matched_signals=list(item.get("matched_signals") or []),
        updated_at=_parse_datetime(item.get("updated_at"))
        or StockScreeningResolver._now(),
        is_stale=bool(item.get("is_stale")),
      )

    return IntradayVolumeScreenPage(
      items=[to_graphql_item(item) for item in page_items],
      top_gainers=[to_graphql_item(item) for item in top_gainers],
      top_losers=[to_graphql_item(item) for item in top_losers],
      advancers=advancers,
      decliners=decliners,
      flats=flats,
      total=len(matched),
      limit=limit,
      offset=offset,
      updated_at=_parse_datetime(page.get("updated_at")),
      is_scanner_running=bool(page.get("is_scanner_running")),
      warnings=warnings,
    )

  @staticmethod
  async def limit_up_radar(
    input: LimitUpRadarInput,
    *,
    user_id: Optional[str] = None,
  ) -> LimitUpRadarPage:
    limit = min(max(input.limit or 200, 1), 200)
    offset = min(max(input.offset or 0, 0), 200 * 1000)
    projection = await limit_up_radar_store.read_radar()
    if projection is None:
      return LimitUpRadarPage(
        items=[],
        industries=[],
        summary=LimitUpRadarSummary(
          scanned_count=0,
          candidate_count=0,
          near_limit_count=0,
          sealed_count=0,
          broken_count=0,
          stale_count=0,
          excluded_count=0,
        ),
        total=0,
        limit=limit,
        offset=offset,
        score_version=RADAR_SCORE_VERSION,
        updated_at=None,
        is_scanner_running=False,
        warnings=["Engine 全市场打板雷达尚未就绪，请检查 Engine 与 QMT Agent"],
      )

    existing_instances: Dict[str, str] = {}
    if user_id:
      async for db in get_async_db():
        runs = await StrategyRunRepository(db).find_all_strategy_runs(user_id)
        active_runs = sorted(
          (run for run in runs if is_active_execution_run(run)),
          key=lambda run: (
            getattr(run, "updated_at", None)
            or getattr(run, "created_at", None)
            or datetime.min,
            str(getattr(run, "id", "")),
          ),
          reverse=True,
        )
        for run in active_runs:
          strategy = getattr(run, "strategy", None)
          if not strategy or (
            getattr(strategy, "class_name", "")
            != LIMIT_UP_BOARD_STRATEGY_CLASS_NAME
            and "打板" not in str(getattr(strategy, "name", ""))
          ):
            continue
          for instrument_code in list(getattr(run, "instruments", None) or []):
            existing_instances.setdefault(
              str(instrument_code).upper(),
              str(run.id),
            )
        break

    stages = {stage.value for stage in input.stages or []}
    industries = set(input.include_industries or [])
    search = str(input.search or "").strip().lower()
    values = []
    for raw in list(projection.get("items") or []):
      stage = str(raw.get("stage") or "")
      if stages and stage not in stages:
        continue
      if industries and raw.get("industry") not in industries:
        continue
      if input.min_score is not None and _finite_float(
        raw.get("radar_score")
      ) < input.min_score:
        continue
      if search and search not in (
        f"{raw.get('code') or ''} {raw.get('name') or ''}".lower()
      ):
        continue
      values.append(dict(raw))

    sort_field = input.sort_field
    sort_key = {
      LimitUpRadarSortField.SCORE: "radar_score",
      LimitUpRadarSortField.DISTANCE_TO_LIMIT: "distance_to_limit_pct",
      LimitUpRadarSortField.AMOUNT: "amount",
      LimitUpRadarSortField.UPDATED_AT: "updated_at",
    }[sort_field]
    reverse = input.sort_direction.value == "desc"
    if sort_field is LimitUpRadarSortField.UPDATED_AT:
      values.sort(key=lambda item: str(item.get(sort_key) or ""), reverse=reverse)
    else:
      values.sort(
        key=lambda item: _finite_float(item.get(sort_key)),
        reverse=reverse,
      )
    total = len(values)
    values = values[offset : offset + limit]

    def item_type(raw: Dict[str, Any]) -> LimitUpRadarItem:
      code = str(raw.get("code") or "").upper()
      return LimitUpRadarItem(
        code=code,
        name=str(raw.get("name") or code),
        industry=raw.get("industry"),
        current_price=_finite_float(raw.get("current_price")),
        change_pct=_finite_float(raw.get("change_pct")),
        limit_up_price=_finite_float(raw.get("limit_up_price")),
        price_tick=_finite_float(raw.get("price_tick"), 0.01),
        distance_to_limit_pct=_finite_float(raw.get("distance_to_limit_pct")),
        distance_to_limit_ticks=_finite_float(raw.get("distance_to_limit_ticks")),
        price_change_5m_pct=_finite_float(raw.get("price_change_5m_pct")),
        amount=_finite_float(raw.get("amount")),
        amount_pace_ratio=_finite_float(raw.get("amount_pace_ratio")),
        volume_pace_ratio=_finite_float(raw.get("volume_pace_ratio")),
        last_5m_volume_ratio=_finite_float(raw.get("last_5m_volume_ratio")),
        intraday_turnover_rate_pct=_finite_optional_float(
          raw.get("intraday_turnover_rate_pct")
        ),
        depth_imbalance_5=_finite_float(raw.get("depth_imbalance_5")),
        bid1_price=_finite_optional_float(raw.get("bid1_price")),
        ask1_price=_finite_optional_float(raw.get("ask1_price")),
        bid1_volume=_finite_float(raw.get("bid1_volume")),
        ask1_volume=_finite_float(raw.get("ask1_volume")),
        stage=_radar_stage(raw.get("stage")),
        stage_label=str(raw.get("stage_label") or ""),
        radar_score=_finite_float(raw.get("radar_score")),
        score_version=str(raw.get("score_version") or RADAR_SCORE_VERSION),
        score_breakdown=[
          LimitUpRadarScoreFactor(
            code=str(factor.get("code") or ""),
            label=str(factor.get("label") or ""),
            score=_finite_float(factor.get("score")),
            max_score=_finite_float(factor.get("max_score")),
            explanation=str(factor.get("explanation") or ""),
          )
          for factor in list(raw.get("score_breakdown") or [])
        ],
        break_count=_finite_int(raw.get("break_count")),
        first_touch_at=_parse_datetime(raw.get("first_touch_at")),
        first_sealed_at=_parse_datetime(raw.get("first_sealed_at")),
        last_stage_at=_parse_datetime(raw.get("last_stage_at")),
        events=[
          LimitUpRadarEvent(
            event_id=str(event.get("eventId") or ""),
            stage=_radar_stage(event.get("stage")),
            stage_label=str(event.get("stageLabel") or ""),
            occurred_at=_parse_datetime(event.get("occurredAt"))
            or StockScreeningResolver._now(),
            score=_finite_float(event.get("score")),
          )
          for event in list(raw.get("events") or [])
        ],
        one_word_limit_up=bool(raw.get("one_word_limit_up")),
        is_stale=bool(raw.get("is_stale")),
        quality_tags=list(raw.get("quality_tags") or []),
        blocked_reasons=list(raw.get("blocked_reasons") or []),
        can_create_instance=bool(raw.get("can_create_instance")),
        existing_instance_id=existing_instances.get(code),
        updated_at=_parse_datetime(raw.get("updated_at"))
        or StockScreeningResolver._now(),
      )

    summary = dict(projection.get("summary") or {})
    return LimitUpRadarPage(
      items=[item_type(raw) for raw in values],
      industries=[
        LimitUpRadarIndustryHeat(
          industry=str(item.get("industry") or "未分类"),
          candidate_count=_finite_int(item.get("candidate_count")),
          near_limit_count=_finite_int(item.get("near_limit_count")),
          sealed_count=_finite_int(item.get("sealed_count")),
          average_score=_finite_float(item.get("average_score")),
        )
        for item in list(projection.get("industries") or [])
      ],
      summary=LimitUpRadarSummary(
        scanned_count=_finite_int(summary.get("scanned_count")),
        candidate_count=_finite_int(summary.get("candidate_count")),
        near_limit_count=_finite_int(summary.get("near_limit_count")),
        sealed_count=_finite_int(summary.get("sealed_count")),
        broken_count=_finite_int(summary.get("broken_count")),
        stale_count=_finite_int(summary.get("stale_count")),
        excluded_count=_finite_int(summary.get("excluded_count")),
      ),
      total=total,
      limit=limit,
      offset=offset,
      score_version=str(projection.get("score_version") or RADAR_SCORE_VERSION),
      updated_at=_parse_datetime(projection.get("updated_at")),
      is_scanner_running=bool(projection.get("is_scanner_running")),
      warnings=list(projection.get("warnings") or []),
    )

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
