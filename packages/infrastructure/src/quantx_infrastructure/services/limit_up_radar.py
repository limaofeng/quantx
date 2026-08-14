"""Engine-owned full-market limit-up radar and Redis read projection."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.limit_up_radar_event import LimitUpRadarEvent
from quantx_infrastructure.repositories.indicator_snapshot_repository import (
  IndicatorSnapshotRepository,
)
from quantx_infrastructure.repositories.limit_up_radar_event_repository import (
  LimitUpRadarEventRepository,
)
from quantx_infrastructure.services.intraday_volume_scanner import (
  IntradayVolumeScanner,
  IntradayVolumeState,
  intraday_volume_scanner,
)

logger = logging.getLogger(__name__)

RADAR_SCORE_VERSION = "limit-up-radar-v1"
RADAR_CACHE_KEY = "limit-up-radar:latest:v1"
INTRADAY_CACHE_KEY = "whole-market-intraday:latest:v1"
RADAR_CACHE_TTL_SECONDS = 36 * 60 * 60
RADAR_STAGE_LABELS = {
  "MOMENTUM": "异动",
  "SURGING": "冲板",
  "NEAR_LIMIT": "临板",
  "TOUCHING": "触板",
  "SEALED": "封板",
  "BROKEN": "炸板",
  "RESEALED": "回封",
}


def _projection_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  text = str(value or "").strip()
  if not text:
    return None
  try:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))
  except ValueError:
    return None


def _has_intraday_market_data(value: Optional[Dict[str, Any]]) -> bool:
  if not value or not list(value.get("items") or []):
    return False
  return _projection_datetime(value.get("updated_at")) is not None


def _projection_epoch(value: Any) -> Optional[float]:
  parsed = _projection_datetime(value)
  if parsed is None:
    return None
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
  return parsed.timestamp()


def select_latest_intraday_projection(
  current: Optional[Dict[str, Any]],
  candidate: Dict[str, Any],
) -> Dict[str, Any]:
  """Keep the last real whole-market snapshot across restarts and outages."""
  if not _has_intraday_market_data(current):
    return candidate
  if _has_intraday_market_data(candidate):
    current_at = _projection_epoch(current.get("updated_at"))
    candidate_at = _projection_epoch(candidate.get("updated_at"))
    if current_at is None or (
      candidate_at is not None and candidate_at >= current_at
    ):
      return candidate

  warnings = list(
    dict.fromkeys(
      [
        *list(current.get("warnings") or []),
        *list(candidate.get("warnings") or []),
        "当前未收到更新的全市场行情，已保留最近一份实盘快照",
      ]
    )
  )
  return {
    **current,
    "is_scanner_running": bool(candidate.get("is_scanner_running")),
    "retained_snapshot": True,
    "warnings": warnings,
  }


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
  return max(minimum, min(maximum, value))


def _number(value: Any, default: float = 0.0) -> float:
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number if math.isfinite(number) else default


def _iso(value: Optional[datetime]) -> Optional[str]:
  return value.isoformat() if value else None


@dataclass
class RadarLifecycle:
  stage: str = ""
  ever_touched: bool = False
  ever_sealed: bool = False
  break_count: int = 0
  first_touch_at: Optional[datetime] = None
  first_sealed_at: Optional[datetime] = None
  last_stage_at: Optional[datetime] = None
  events: List[Dict[str, Any]] = field(default_factory=list)


class LimitUpRadarProjectionStore:
  async def write(
    self,
    radar: Dict[str, Any],
    intraday: Dict[str, Any],
  ) -> bool:
    try:
      redis = await redis_pubsub.get_redis()
      current_intraday = await self._read(INTRADAY_CACHE_KEY)
      retained_intraday = select_latest_intraday_projection(
        current_intraday,
        intraday,
      )
      async with redis.pipeline(transaction=False) as pipeline:
        await pipeline.set(
          RADAR_CACHE_KEY,
          json.dumps(radar, ensure_ascii=False, default=str),
          ex=RADAR_CACHE_TTL_SECONDS,
        )
        await pipeline.set(
          INTRADAY_CACHE_KEY,
          json.dumps(retained_intraday, ensure_ascii=False, default=str),
          ex=RADAR_CACHE_TTL_SECONDS,
        )
        await pipeline.execute()
      return True
    except Exception as exc:
      logger.warning("打板雷达读模型写入失败: %s", exc.__class__.__name__)
      return False

  async def read_radar(self) -> Optional[Dict[str, Any]]:
    return await self._read(RADAR_CACHE_KEY)

  async def read_intraday(self) -> Optional[Dict[str, Any]]:
    return await self._read(INTRADAY_CACHE_KEY)

  async def _read(self, key: str) -> Optional[Dict[str, Any]]:
    try:
      redis = await redis_pubsub.get_redis()
      raw = await redis.get(key)
      if not raw:
        return None
      value = json.loads(raw)
      return value if isinstance(value, dict) else None
    except Exception as exc:
      logger.warning("打板雷达读模型读取失败: %s", exc.__class__.__name__)
      return None


class LimitUpRadarBuilder:
  """Pure deterministic radar scoring over normalized full-market ticks."""

  def __init__(self) -> None:
    self._lifecycles: Dict[str, RadarLifecycle] = {}
    self._pending_events: List[Dict[str, Any]] = []
    self._trade_date: Optional[date] = None

  def restore(self, events: Iterable[LimitUpRadarEvent]) -> None:
    for event in events:
      lifecycle = self._lifecycles.setdefault(
        event.instrument_code, RadarLifecycle()
      )
      timestamp = event.occurred_at
      stage = str(event.stage or "")
      lifecycle.stage = stage
      lifecycle.last_stage_at = timestamp
      if stage in {"TOUCHING", "SEALED", "BROKEN", "RESEALED"}:
        lifecycle.ever_touched = True
        lifecycle.first_touch_at = lifecycle.first_touch_at or timestamp
      if stage in {"SEALED", "BROKEN", "RESEALED"}:
        lifecycle.ever_sealed = True
        lifecycle.first_sealed_at = lifecycle.first_sealed_at or timestamp
      if stage == "BROKEN":
        lifecycle.break_count += 1
      lifecycle.events.append(
        {
          "eventId": event.event_id,
          "stage": stage,
          "stageLabel": RADAR_STAGE_LABELS.get(stage, stage),
          "occurredAt": _iso(timestamp),
          "score": round(_number(event.score), 2),
        }
      )
      lifecycle.events = lifecycle.events[-20:]

  def pop_pending_events(self) -> List[Dict[str, Any]]:
    pending = self._pending_events
    self._pending_events = []
    return pending

  def build(
    self,
    *,
    baselines: Iterable[Dict[str, Any]],
    states: Dict[str, IntradayVolumeState],
    intraday_items: Iterable[Dict[str, Any]],
    scanner_running: bool,
    now: Optional[datetime] = None,
  ) -> Dict[str, Any]:
    now = now or time_utils.now()
    trade_date = time_utils.to_shanghai(now).date()
    if self._trade_date is not None and self._trade_date != trade_date:
      self._lifecycles.clear()
      self._pending_events.clear()
    self._trade_date = trade_date

    metrics_by_code = {
      str(item.get("code") or ""): dict(item) for item in intraday_items
    }
    preliminary: List[Dict[str, Any]] = []
    scanned_count = 0
    stale_count = 0
    excluded_count = 0

    for baseline in baselines:
      code = str(baseline.get("code") or "")
      state = states.get(code)
      metrics = metrics_by_code.get(code)
      if state is None or metrics is None or state.updated_at is None:
        continue
      if str(baseline.get("instrument_type") or "stock").lower() != "stock":
        continue
      scanned_count += 1
      name = str(baseline.get("name") or code)
      is_st = name.upper().startswith(("ST", "*ST"))
      delist_risk = "退" in name
      suspended = int(state.stock_status or 0) == 1
      if (
        is_st
        or delist_risk
        or suspended
        or state.current_price <= 0
        or state.up_stop_price <= 0
        or state.price_tick <= 0
      ):
        excluded_count += 1
        continue

      distance_pct = max(
        0.0,
        (state.up_stop_price - state.current_price) / state.up_stop_price * 100,
      )
      price_change_5m = self._price_change_5m(state)
      lifecycle = self._lifecycles.setdefault(code, RadarLifecycle())
      stage = self._stage(state, lifecycle, distance_pct, price_change_5m)
      touched_from_high = (
        state.high_price > 0
        and state.high_price >= state.up_stop_price - state.price_tick / 2
      )
      is_candidate = (
        (
          _number(metrics.get("change_pct")) >= 3
          and (
            _number(metrics.get("amount_pace_ratio")) >= 1.5
            or _number(metrics.get("last_5m_volume_ratio")) >= 2
            or price_change_5m >= 1
          )
        )
        or distance_pct <= 5
        or lifecycle.ever_touched
        or touched_from_high
      )
      if not is_candidate:
        continue

      is_stale = bool(metrics.get("is_stale"))
      if is_stale:
        stale_count += 1
      one_word = self._is_one_word(state)
      preliminary.append(
        {
          **metrics,
          "code": code,
          "name": name,
          "industry": baseline.get("industry"),
          "limit_up_price": round(state.up_stop_price, 4),
          "price_tick": round(state.price_tick, 6),
          "distance_to_limit_pct": round(distance_pct, 4),
          "distance_to_limit_ticks": round(
            max(0.0, state.up_stop_price - state.current_price)
            / max(state.price_tick, 0.0001),
            2,
          ),
          "price_change_5m_pct": round(price_change_5m, 4),
          "bid1_price": round(state.bid_price[0], 4) if state.bid_price else None,
          "ask1_price": round(state.ask_price[0], 4) if state.ask_price else None,
          "bid1_volume": round(state.bid_vol[0], 2) if state.bid_vol else 0.0,
          "ask1_volume": round(state.ask_vol[0], 2) if state.ask_vol else 0.0,
          "stage": stage,
          "stage_label": RADAR_STAGE_LABELS[stage],
          "one_word_limit_up": one_word,
          "is_stale": is_stale,
          "quality_tags": self._quality_tags(baseline, state, metrics),
          "_lifecycle": lifecycle,
        }
      )

    industry_counts: Dict[str, int] = {}
    for item in preliminary:
      industry = str(item.get("industry") or "未分类")
      industry_counts[industry] = industry_counts.get(industry, 0) + 1

    items: List[Dict[str, Any]] = []
    for item in preliminary:
      lifecycle = item.pop("_lifecycle")
      industry = str(item.get("industry") or "未分类")
      breakdown = self._score_breakdown(
        item,
        industry_count=industry_counts.get(industry, 0),
        break_count=lifecycle.break_count,
      )
      score = round(
        _clamp(sum(factor["score"] for factor in breakdown), 0, 100), 2
      )
      self._transition(
        code=item["code"],
        lifecycle=lifecycle,
        stage=item["stage"],
        occurred_at=states[item["code"]].updated_at or now,
        score=score,
        snapshot={
          "price": item["current_price"],
          "limitUpPrice": item["limit_up_price"],
          "distanceToLimitPct": item["distance_to_limit_pct"],
        },
      )
      blocked_reasons: List[str] = []
      if item["is_stale"]:
        blocked_reasons.append("STALE_MARKET_DATA")
      if item["one_word_limit_up"]:
        blocked_reasons.append("ONE_WORD_LIMIT_UP")
      if item["stage"] in {"TOUCHING", "SEALED", "RESEALED"}:
        blocked_reasons.append("LIMIT_UP_ALREADY_REACHED")
      can_create = not blocked_reasons
      items.append(
        {
          **item,
          "radar_score": score,
          "score_version": RADAR_SCORE_VERSION,
          "score_breakdown": breakdown,
          "break_count": lifecycle.break_count,
          "first_touch_at": _iso(lifecycle.first_touch_at),
          "first_sealed_at": _iso(lifecycle.first_sealed_at),
          "last_stage_at": _iso(lifecycle.last_stage_at),
          "events": list(reversed(lifecycle.events[-20:])),
          "blocked_reasons": blocked_reasons,
          "can_create_instance": can_create,
          "existing_instance_id": None,
        }
      )

    items.sort(
      key=lambda item: (
        _number(item.get("radar_score")),
        -_number(item.get("distance_to_limit_pct")),
        _number(item.get("amount")),
      ),
      reverse=True,
    )
    stage_counts = {
      stage: sum(1 for item in items if item["stage"] == stage)
      for stage in RADAR_STAGE_LABELS
    }
    industries = [
      {
        "industry": industry,
        "candidate_count": count,
        "near_limit_count": sum(
          1
          for item in items
          if str(item.get("industry") or "未分类") == industry
          and item["stage"] in {"NEAR_LIMIT", "TOUCHING"}
        ),
        "sealed_count": sum(
          1
          for item in items
          if str(item.get("industry") or "未分类") == industry
          and item["stage"] in {"SEALED", "RESEALED"}
        ),
        "average_score": round(
          sum(
            _number(item.get("radar_score"))
            for item in items
            if str(item.get("industry") or "未分类") == industry
          )
          / max(1, count),
          2,
        ),
      }
      for industry, count in industry_counts.items()
    ]
    industries.sort(
      key=lambda value: (value["average_score"], value["candidate_count"]),
      reverse=True,
    )
    updated_at = max(
      (
        str(item.get("updated_at") or "")
        for item in items
        if item.get("updated_at")
      ),
      default=None,
    )
    warnings: List[str] = []
    if not scanner_running:
      warnings.append("全市场雷达订阅未运行，请检查 Engine 与 QMT Agent")
    if scanned_count == 0:
      warnings.append("尚未收到沪深全市场实时行情")
    if stale_count:
      warnings.append(f"{stale_count} 个候选行情已过期，已禁止进入执行")
    return {
      "score_version": RADAR_SCORE_VERSION,
      "updated_at": updated_at,
      "is_scanner_running": scanner_running,
      "warnings": warnings,
      "summary": {
        "scanned_count": scanned_count,
        "candidate_count": len(items),
        "near_limit_count": stage_counts["NEAR_LIMIT"] + stage_counts["TOUCHING"],
        "sealed_count": stage_counts["SEALED"] + stage_counts["RESEALED"],
        "broken_count": stage_counts["BROKEN"],
        "stale_count": stale_count,
        "excluded_count": excluded_count,
      },
      "industries": industries,
      "items": items,
    }

  def _stage(
    self,
    state: IntradayVolumeState,
    lifecycle: RadarLifecycle,
    distance_pct: float,
    price_change_5m: float,
  ) -> str:
    at_limit = (
      state.up_stop_price > 0
      and state.current_price >= state.up_stop_price - state.price_tick / 2
    )
    ask1 = state.ask_price[0] if state.ask_price else 0.0
    bid1_volume = state.bid_vol[0] if state.bid_vol else 0.0
    sealed = at_limit and ask1 <= 0 and bid1_volume > 0
    if sealed:
      return "RESEALED" if lifecycle.break_count > 0 else "SEALED"
    if lifecycle.ever_sealed and state.current_price < state.up_stop_price - state.price_tick / 2:
      return "BROKEN"
    if at_limit:
      return "TOUCHING"
    if distance_pct <= 1:
      return "NEAR_LIMIT"
    if distance_pct <= 5:
      return "SURGING"
    return "SURGING" if price_change_5m >= 1 else "MOMENTUM"

  def _transition(
    self,
    *,
    code: str,
    lifecycle: RadarLifecycle,
    stage: str,
    occurred_at: datetime,
    score: float,
    snapshot: Dict[str, Any],
  ) -> None:
    if lifecycle.stage == stage:
      return
    previous = lifecycle.stage
    if stage in {"TOUCHING", "SEALED", "BROKEN", "RESEALED"}:
      lifecycle.ever_touched = True
      lifecycle.first_touch_at = lifecycle.first_touch_at or occurred_at
    if stage in {"SEALED", "RESEALED"}:
      lifecycle.ever_sealed = True
      lifecycle.first_sealed_at = lifecycle.first_sealed_at or occurred_at
    if stage == "BROKEN" and previous in {"SEALED", "RESEALED"}:
      lifecycle.break_count += 1
    lifecycle.stage = stage
    lifecycle.last_stage_at = occurred_at
    event_id = str(uuid.uuid4())
    event = {
      "eventId": event_id,
      "stage": stage,
      "stageLabel": RADAR_STAGE_LABELS[stage],
      "occurredAt": _iso(occurred_at),
      "score": score,
    }
    lifecycle.events.append(event)
    lifecycle.events = lifecycle.events[-20:]
    self._pending_events.append(
      {
        "event_id": event_id,
        "trade_date": time_utils.to_shanghai(occurred_at).date(),
        "instrument_code": code,
        "stage": stage,
        "occurred_at": occurred_at,
        "score": score,
        "score_version": RADAR_SCORE_VERSION,
        "snapshot": snapshot,
      }
    )

  def _score_breakdown(
    self,
    item: Dict[str, Any],
    *,
    industry_count: int,
    break_count: int,
  ) -> List[Dict[str, Any]]:
    has_industry = bool(item.get("industry"))
    factors = [
      (
        "PROXIMITY",
        "涨停距离",
        30 * _clamp(1 - _number(item.get("distance_to_limit_pct")) / 5),
        30,
        f"距涨停 {_number(item.get('distance_to_limit_pct')):.2f}%",
      ),
      (
        "PRICE_ACCELERATION_5M",
        "5分钟加速",
        15 * _clamp(_number(item.get("price_change_5m_pct")) / 3),
        15,
        f"近5分钟 {_number(item.get('price_change_5m_pct')):+.2f}%",
      ),
      (
        "AMOUNT_PACE",
        "成交额进度",
        15 * _clamp((_number(item.get("amount_pace_ratio")) - 1) / 2),
        15,
        f"成交额进度 {_number(item.get('amount_pace_ratio')):.2f}x",
      ),
      (
        "VOLUME_5M",
        "近5分钟量能",
        15 * _clamp((_number(item.get("last_5m_volume_ratio")) - 1) / 4),
        15,
        f"近5分钟量比 {_number(item.get('last_5m_volume_ratio')):.2f}x",
      ),
      (
        "TURNOVER",
        "换手率",
        10 * _clamp(_number(item.get("intraday_turnover_rate_pct")) / 10),
        10,
        f"盘中换手 {_number(item.get('intraday_turnover_rate_pct')):.2f}%",
      ),
      (
        "DEPTH",
        "盘口强度",
        10 * _clamp(max(0.0, _number(item.get("depth_imbalance_5"))) / 0.6),
        10,
        f"五档失衡 {_number(item.get('depth_imbalance_5')):+.2f}",
      ),
      (
        "INDUSTRY_HEAT",
        "行业热度",
        5 * _clamp(industry_count / 5) if has_industry else 0.0,
        5,
        f"行业候选 {industry_count} 只" if has_industry else "行业数据缺失",
      ),
    ]
    result = [
      {
        "code": code,
        "label": label,
        "score": round(score, 2),
        "max_score": max_score,
        "explanation": explanation,
      }
      for code, label, score, max_score, explanation in factors
    ]
    if break_count:
      result.append(
        {
          "code": "BREAK_PENALTY",
          "label": "炸板惩罚",
          "score": float(-min(24, break_count * 8)),
          "max_score": 0.0,
          "explanation": f"当日炸板 {break_count} 次",
        }
      )
    return result

  def _price_change_5m(self, state: IntradayVolumeState) -> float:
    if state.current_price <= 0 or state.updated_at is None:
      return 0.0
    values = sorted(
      (
        minute,
        price,
      )
      for minute, price in state.minute_close.items()
      if 0 <= (state.updated_at - minute).total_seconds() < 6 * 60 and price > 0
    )
    if not values:
      return 0.0
    reference = values[0][1]
    return (
      (state.current_price - reference) / reference * 100 if reference > 0 else 0.0
    )

  def _is_one_word(self, state: IntradayVolumeState) -> bool:
    values = (state.open_price, state.high_price, state.low_price)
    return state.up_stop_price > 0 and all(
      value > 0 and abs(value - state.up_stop_price) <= state.price_tick / 2
      for value in values
    )

  def _quality_tags(
    self,
    baseline: Dict[str, Any],
    state: IntradayVolumeState,
    metrics: Dict[str, Any],
  ) -> List[str]:
    tags: List[str] = []
    if _number(baseline.get("avg_volume_20")) <= 0:
      tags.append("MISSING_VOLUME_BASELINE")
    if _number(baseline.get("avg_amount_20")) <= 0:
      tags.append("MISSING_AMOUNT_BASELINE")
    if _number(baseline.get("float_volume")) <= 0:
      tags.append("MISSING_FLOAT_VOLUME")
    if not baseline.get("industry"):
      tags.append("MISSING_INDUSTRY")
    if not state.bid_vol or not state.ask_vol:
      tags.append("MISSING_DEPTH")
    if len(state.minute_close) < 2:
      tags.append("MISSING_PRICE_HISTORY_5M")
    if metrics.get("is_stale"):
      tags.append("STALE_MARKET_DATA")
    return tags


class LimitUpRadarMonitor:
  def __init__(
    self,
    *,
    scanner: IntradayVolumeScanner = intraday_volume_scanner,
    store: Optional[LimitUpRadarProjectionStore] = None,
    publish_interval_seconds: float = 1.0,
  ) -> None:
    self.scanner = scanner
    self.store = store or LimitUpRadarProjectionStore()
    self.publish_interval_seconds = max(0.25, publish_interval_seconds)
    self.builder = LimitUpRadarBuilder()
    self._baselines: List[Dict[str, Any]] = []
    self._snapshot_date: Optional[date] = None
    self._loaded_at: Optional[datetime] = None
    self._task: Optional[asyncio.Task] = None

  async def start(self) -> None:
    if self._task is not None and not self._task.done():
      return
    await self._reload_baselines(force=True)
    await self._restore_events()
    await self.scanner.start()
    self._task = asyncio.create_task(self._run(), name="limit-up-radar-monitor")

  async def stop(self) -> None:
    task = self._task
    self._task = None
    if task is not None:
      task.cancel()
      await asyncio.gather(task, return_exceptions=True)
    self.scanner.stop()

  async def _run(self) -> None:
    while True:
      try:
        await self._reload_baselines()
        await self._publish_once()
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("全市场打板雷达刷新失败")
      await asyncio.sleep(self.publish_interval_seconds)

  async def _reload_baselines(self, *, force: bool = False) -> None:
    now = time_utils.now()
    if (
      not force
      and self._loaded_at is not None
      and (now - self._loaded_at).total_seconds() < 600
    ):
      return
    async with AsyncSessionLocal() as db:
      repo = IndicatorSnapshotRepository(db)
      snapshot_date = await repo.get_latest_snapshot_date()
      if snapshot_date is None:
        self._baselines = []
        self._snapshot_date = None
        self._loaded_at = now
        return
      records, _ = await repo.screen_snapshots(
        snapshot_date=snapshot_date,
        limit=20000,
        offset=0,
        universe="stock_and_etf",
        exclude_st=True,
      )
      codes = [record.code for record in records]
      industries = await repo.find_industry_names_by_codes(codes)
      float_volumes = await repo.find_float_volume_by_codes(codes)
      instrument_types = await repo.find_instrument_types_by_codes(codes)
      self._baselines = [
        {
          "code": record.code,
          "name": record.name or record.code,
          "industry": industries.get(record.code),
          "instrument_type": str(
            getattr(instrument_types.get(record.code), "value", None)
            or instrument_types.get(record.code)
            or getattr(record, "instrument_type", None)
            or "stock"
          ).lower(),
          "avg_volume_20": _number(record.avg_volume_20),
          "avg_amount_20": _number(getattr(record, "avg_amount_20", None)),
          "float_volume": float_volumes.get(record.code),
        }
        for record in records
      ]
      self._snapshot_date = snapshot_date
      self._loaded_at = now

  async def _restore_events(self) -> None:
    trade_date = time_utils.to_shanghai(time_utils.now()).date()
    async with AsyncSessionLocal() as db:
      events = await LimitUpRadarEventRepository(db).list_for_date(trade_date)
    self.builder.restore(events)

  async def _publish_once(self) -> None:
    intraday = self.scanner.screen(
      self._baselines,
      stale_after_seconds=15,
      limit=20000,
      offset=0,
    )
    radar = self.builder.build(
      baselines=self._baselines,
      states=self.scanner.snapshot_states(),
      intraday_items=intraday["items"],
      scanner_running=self.scanner.is_running,
    )
    intraday_projection = {
      **intraday,
      "snapshot_date": self._snapshot_date.isoformat()
      if self._snapshot_date
      else None,
    }
    await self.store.write(radar, intraday_projection)
    pending = self.builder.pop_pending_events()
    if pending:
      async with AsyncSessionLocal() as db:
        await LimitUpRadarEventRepository(db).append_many(
          LimitUpRadarEvent(**event) for event in pending
        )


limit_up_radar_store = LimitUpRadarProjectionStore()
limit_up_radar_monitor = LimitUpRadarMonitor(store=limit_up_radar_store)
