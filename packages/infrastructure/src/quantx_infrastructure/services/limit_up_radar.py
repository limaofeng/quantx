"""Engine-owned full-market limit-up radar and Redis read projection."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from quantx_domain.trading.first_board_promotion import (
  FIRST_BOARD_MODEL_VERSION,
  FirstBoardPromotionEvaluator,
  FirstBoardPromotionFeatures,
  FirstBoardSegment,
  first_board_segment,
)
from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.first_board_promotion import LimitUpChainSnapshot
from quantx_infrastructure.models.indicator_snapshot import IndicatorSnapshot
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.limit_up_radar_event import LimitUpRadarEvent
from quantx_infrastructure.repositories.first_board_promotion_repository import (
  FirstBoardPromotionRepository,
)
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
FIRST_BOARD_FEATURE_VERSION = "first-board-features-v2-1"
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


def _resolved_listing_history_days(
  instrument: Optional[Instrument], snapshot_date: date
) -> Optional[int]:
  """Resolve listing tenure without treating an unpopulated QMT counter as an IPO."""

  if instrument is None:
    return None
  direct = getattr(instrument, "day_count_from_ipo", None)
  if direct is not None:
    try:
      parsed = int(direct)
      if parsed > 0:
        return parsed
    except (TypeError, ValueError):
      pass
  open_date = getattr(instrument, "open_date", None)
  if not isinstance(open_date, date) or open_date > snapshot_date:
    return None
  # Some QMT deployments leave DayCountFromIPO empty.  The exchange open date
  # is still authoritative; 230 sessions/year is deliberately conservative
  # around public holidays and keeps recent IPOs behind the 120-session gate.
  calendar_days = (snapshot_date - open_date).days
  return max(0, calendar_days * 230 // 365)


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


def _stable_version(value: Dict[str, Any]) -> str:
  return hashlib.sha256(
    json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
  ).hexdigest()[:32]


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
    self.promotion_evaluator = FirstBoardPromotionEvaluator()
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
    baseline_rows = list(baselines)
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

    for baseline in baseline_rows:
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
      segment = first_board_segment(code)
      default_limit_pct = 20.0 if segment is FirstBoardSegment.GROWTH else 10.0
      daily_limit_pct = (
        (state.up_stop_price / state.pre_close - 1.0) * 100.0
        if state.pre_close > 0
        else default_limit_pct
      )
      normalized_limit_progress = _number(metrics.get("change_pct")) / max(
        daily_limit_pct, 1e-6
      )
      is_candidate = (
        normalized_limit_progress >= 0.30 - 1e-6
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
          "history_trading_days": baseline.get("history_trading_days"),
          "previous_limit_up_streak": int(
            baseline.get("previous_limit_up_streak", 0) or 0
          ),
          "recent_limit_up_count_10d": int(
            baseline.get("recent_limit_up_count_10d", 0) or 0
          ),
          "price_position_252": baseline.get("price_position_252"),
          "prior_20d_return_pct": baseline.get("prior_20d_return_pct"),
          "ma20_deviation_pct": baseline.get("ma20_deviation_pct"),
          "realized_volatility_20_pct": baseline.get(
            "realized_volatility_20_pct"
          ),
          "_lifecycle": lifecycle,
        }
      )

    industry_counts: Dict[str, int] = {}
    for item in preliminary:
      industry = str(item.get("industry") or "未分类")
      industry_counts[industry] = industry_counts.get(industry, 0) + 1

    prior_first_boards: Dict[str, int] = {}
    for baseline in baseline_rows:
      if int(baseline.get("previous_limit_up_streak", 0) or 0) == 1:
        industry = str(baseline.get("industry") or "未分类")
        prior_first_boards[industry] = prior_first_boards.get(industry, 0) + 1
    promoted_by_industry: Dict[str, int] = {}
    for item in preliminary:
      if int(item.get("previous_limit_up_streak", 0) or 0) != 1:
        continue
      if str(item.get("stage") or "") not in {
        "TOUCHING",
        "SEALED",
        "BROKEN",
        "RESEALED",
      }:
        continue
      industry = str(item.get("industry") or "未分类")
      promoted_by_industry[industry] = promoted_by_industry.get(industry, 0) + 1
    industry_promotion_rates = {
      industry: promoted_by_industry.get(industry, 0) / max(1, count)
      for industry, count in prior_first_boards.items()
    }

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
      assessment = self.promotion_evaluator.evaluate(
        FirstBoardPromotionFeatures(
          instrument_code=item["code"],
          stage=item["stage"],
          change_pct=_number(item.get("change_pct")),
          limit_up_price=_number(item.get("limit_up_price")),
          current_price=_number(item.get("current_price")),
          price_change_5m_pct=_number(item.get("price_change_5m_pct")),
          amount_pace_ratio=_number(item.get("amount_pace_ratio")),
          volume_pace_ratio=_number(item.get("volume_pace_ratio")),
          last_5m_volume_ratio=_number(item.get("last_5m_volume_ratio")),
          turnover_rate_pct=item.get("intraday_turnover_rate_pct"),
          depth_imbalance_5=_number(item.get("depth_imbalance_5")),
          industry_candidate_count=industry_counts.get(industry, 0),
          sector_promotion_rate=industry_promotion_rates.get(industry, 0.0),
          break_count=lifecycle.break_count,
          ever_touched_limit=lifecycle.ever_touched,
          one_word_limit_up=bool(item.get("one_word_limit_up")),
          is_stale=bool(item.get("is_stale")),
          quality_tags=tuple(item.get("quality_tags") or []),
          history_trading_days=item.get("history_trading_days"),
          previous_limit_up_streak=int(
            item.get("previous_limit_up_streak", 0) or 0
          ),
          recent_limit_up_count_10d=int(
            item.get("recent_limit_up_count_10d", 0) or 0
          ),
          price_position_252=item.get("price_position_252"),
          prior_20d_return_pct=item.get("prior_20d_return_pct"),
          ma20_deviation_pct=item.get("ma20_deviation_pct"),
          realized_volatility_20_pct=item.get("realized_volatility_20_pct"),
        )
      )
      assessment_payload = assessment.to_dict()
      assessment_payload["factors"] = [
        {
          "code": factor.code,
          "label": factor.label,
          "contribution": factor.contribution,
          "explanation": factor.explanation,
        }
        for factor in assessment.factors
      ]
      blocked_reasons = list(assessment.veto_reasons)
      can_create = assessment.eligible
      snapshot_version = _stable_version(
        {
          "tradeDate": trade_date.isoformat(),
          "code": item["code"],
          "stage": item["stage"],
          "eligible": assessment.eligible,
          "veto": blocked_reasons,
          "high": assessment.high_position_type.value,
          "rank": round(assessment.rank_score, 0),
          "touch": round(assessment.next_day_limit_touch_probability, 2),
          "breakCount": lifecycle.break_count,
        }
      )
      items.append(
        {
          **item,
          "radar_score": score,
          "score_version": RADAR_SCORE_VERSION,
          "score_breakdown": breakdown,
          "promotion_model_version": assessment.model_version,
          "exit_policy_version": assessment.exit_policy_version,
          "promotion_snapshot_version": snapshot_version,
          "board_segment": assessment.segment.value,
          "promotion_observed": assessment.observed,
          "promotion_eligible": assessment.eligible,
          "promotion_score": assessment.rank_score,
          "promotion_factors": assessment_payload["factors"],
          "high_position_type": assessment.high_position_type.value,
          "normalized_limit_progress": assessment.normalized_limit_progress,
          "first_board_close_probability": assessment.first_board_close_probability,
          "next_day_limit_touch_probability": assessment.next_day_limit_touch_probability,
          "next_day_limit_seal_probability": assessment.next_day_limit_seal_probability,
          "expected_net_return_pct": assessment.expected_net_return_pct,
          "cvar95_loss_pct": assessment.cvar95_loss_pct,
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
        _number(item.get("promotion_score")),
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
    eligible_count = sum(1 for item in items if item.get("promotion_eligible"))
    first_board_count = sum(
      1
      for item in items
      if int(item.get("previous_limit_up_streak", 0) or 0) == 0
      and item.get("stage") in {"TOUCHING", "SEALED", "BROKEN", "RESEALED"}
    )
    max_board_count = max(
      (
        int(item.get("previous_limit_up_streak", 0) or 0) + 1
        for item in items
        if item.get("stage") in {"TOUCHING", "SEALED", "BROKEN", "RESEALED"}
      ),
      default=0,
    )
    chain_payload = {
      "trade_date": trade_date.isoformat(),
      "as_of": updated_at,
      "max_board_count": max_board_count,
      "first_board_count": first_board_count,
      "sealed_count": stage_counts["SEALED"] + stage_counts["RESEALED"],
      "broken_count": stage_counts["BROKEN"],
      "break_rate": round(
        stage_counts["BROKEN"]
        / max(
          1,
          stage_counts["SEALED"]
          + stage_counts["RESEALED"]
          + stage_counts["BROKEN"],
        ),
        4,
      ),
      "promotion_rate": round(
        sum(promoted_by_industry.values())
        / max(1, sum(prior_first_boards.values())),
        4,
      ),
      "board_ladders": [
        {
          "board_count": board_count,
          "codes": sorted(
            str(item["code"])
            for item in items
            if int(item.get("previous_limit_up_streak", 0) or 0) + 1
            == board_count
            and item.get("stage")
            in {"TOUCHING", "SEALED", "BROKEN", "RESEALED"}
          ),
        }
        for board_count in range(1, max_board_count + 1)
      ],
      "sector_ladders": [
        {
          "industry": industry["industry"],
          "candidate_count": industry["candidate_count"],
          "sealed_count": industry["sealed_count"],
          "average_score": industry["average_score"],
          "promotion_rate": round(
            industry_promotion_rates.get(industry["industry"], 0.0), 4
          ),
        }
        for industry in industries[:20]
      ],
    }
    chain_payload["snapshot_version"] = _stable_version(
      {
        "trade_date": chain_payload["trade_date"],
        "max_board_count": chain_payload["max_board_count"],
        "first_board_count": chain_payload["first_board_count"],
        "sealed_count": chain_payload["sealed_count"],
        "broken_count": chain_payload["broken_count"],
        "break_rate": chain_payload["break_rate"],
        "promotion_rate": chain_payload["promotion_rate"],
        "board_ladders": chain_payload["board_ladders"],
        "sector_ladders": [
          {
            **item,
            "average_score": round(_number(item.get("average_score"))),
          }
          for item in chain_payload["sector_ladders"]
        ],
      }
    )
    return {
      "score_version": RADAR_SCORE_VERSION,
      "promotion_model_version": FIRST_BOARD_MODEL_VERSION,
      "updated_at": updated_at,
      "is_scanner_running": scanner_running,
      "warnings": warnings,
      "summary": {
        "scanned_count": scanned_count,
        "candidate_count": len(items),
        "discovered_count": len(items),
        "eligible_count": eligible_count,
        "near_limit_count": stage_counts["NEAR_LIMIT"] + stage_counts["TOUCHING"],
        "sealed_count": stage_counts["SEALED"] + stage_counts["RESEALED"],
        "broken_count": stage_counts["BROKEN"],
        "stale_count": stale_count,
        "excluded_count": excluded_count,
      },
      "chain": chain_payload,
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
    self._persisted_snapshot_versions: set[str] = set()
    self._persisted_chain_versions: set[str] = set()

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
      instruments = {
        instrument.id: instrument
        for instrument in (
          await db.execute(select(Instrument).where(Instrument.id.in_(codes)))
        ).scalars().all()
      }
      recent_dates = await repo.find_snapshot_dates(
        snapshot_date - timedelta(days=60), snapshot_date
      )
      recent_dates = recent_dates[-21:]
      history_rows = (
        await db.execute(
          select(
            IndicatorSnapshot.code,
            IndicatorSnapshot.snapshot_date,
            IndicatorSnapshot.current_price,
            IndicatorSnapshot.change_pct,
          ).where(IndicatorSnapshot.snapshot_date.in_(recent_dates))
        )
      ).all()
      history_by_code: Dict[str, Dict[date, tuple[float, float]]] = {}
      for code, as_of_date, close, change_pct in history_rows:
        history_by_code.setdefault(str(code), {})[as_of_date] = (
          _number(close),
          _number(change_pct),
        )

      def promotion_history(record: Any) -> Dict[str, Any]:
        history = history_by_code.get(record.code, {})
        ordered = [history[value] for value in recent_dates if value in history]
        recent_changes = [value[1] for value in ordered[-10:]]
        segment = first_board_segment(record.code)
        limit_threshold = 19.0 if segment is FirstBoardSegment.GROWTH else 9.5
        streak = 0
        for _, change_pct in reversed(ordered):
          if change_pct < limit_threshold:
            break
          streak += 1
        anchor_close = ordered[0][0] if len(ordered) >= 20 else 0.0
        current_close = _number(record.current_price)
        prior_return = (
          (current_close / anchor_close - 1.0) * 100.0
          if current_close > 0 and anchor_close > 0
          else None
        )
        peak = _number(record.peak_price)
        low = _number(record.low_price_252)
        position = (
          _clamp((current_close - low) / (peak - low))
          if peak > low > 0 and current_close > 0
          else None
        )
        ma20 = _number(record.ma20)
        ma20_deviation = (
          (current_close / ma20 - 1.0) * 100.0
          if current_close > 0 and ma20 > 0
          else None
        )
        bandwidth = _number(record.boll_bandwidth)
        return {
          "history_trading_days": _resolved_listing_history_days(
            instruments.get(record.code), snapshot_date
          ),
          "previous_limit_up_streak": streak,
          "recent_limit_up_count_10d": sum(
            1 for value in recent_changes if value >= limit_threshold
          ),
          "price_position_252": position,
          "prior_20d_return_pct": prior_return,
          "ma20_deviation_pct": ma20_deviation,
          # Bollinger bandwidth is a stable T-1 volatility proxy until the
          # walk-forward feature pipeline publishes annualized realized vol.
          "realized_volatility_20_pct": bandwidth * 25.0 if bandwidth > 0 else None,
        }

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
          **promotion_history(record),
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
    await self._persist_market_facts(radar)
    pending = self.builder.pop_pending_events()
    if pending:
      async with AsyncSessionLocal() as db:
        await LimitUpRadarEventRepository(db).append_many(
          LimitUpRadarEvent(**event) for event in pending
        )

  async def _persist_market_facts(self, radar: Dict[str, Any]) -> None:
    """Persist bounded, immutable V2 facts without blocking the live projection."""
    trade_date = time_utils.to_shanghai(time_utils.now()).date()
    chain = dict(radar.get("chain") or {})
    chain_version = str(chain.get("snapshot_version") or "")
    candidates = [
      item
      for index, item in enumerate(list(radar.get("items") or []))
      if index < 50
      or bool(item.get("promotion_eligible"))
      or bool(item.get("first_touch_at"))
    ]
    unseen = [
      item
      for item in candidates
      if str(item.get("promotion_snapshot_version") or "")
      not in self._persisted_snapshot_versions
    ]
    if not unseen and (
      not chain_version or chain_version in self._persisted_chain_versions
    ):
      return
    try:
      async with AsyncSessionLocal() as db:
        repo = FirstBoardPromotionRepository(db)
        for item in unseen:
          snapshot_version = str(item["promotion_snapshot_version"])
          as_of = _projection_datetime(item.get("updated_at")) or time_utils.now()
          assessment_payload = {
            "model_version": str(item.get("promotion_model_version") or ""),
            "exit_policy_version": str(item.get("exit_policy_version") or ""),
            "segment": str(item.get("board_segment") or "UNSUPPORTED"),
            "eligible": bool(item.get("promotion_eligible")),
            "observed": bool(item.get("promotion_observed")),
            "rank_score": _number(item.get("promotion_score")),
            "first_board_close_probability": _number(
              item.get("first_board_close_probability")
            ),
            "next_day_limit_touch_probability": _number(
              item.get("next_day_limit_touch_probability")
            ),
            "next_day_limit_seal_probability": _number(
              item.get("next_day_limit_seal_probability")
            ),
            "expected_net_return_pct": _number(
              item.get("expected_net_return_pct")
            ),
            "cvar95_loss_pct": _number(item.get("cvar95_loss_pct")),
            "high_position_type": str(item.get("high_position_type") or ""),
            "veto_reasons": list(item.get("blocked_reasons") or []),
            "factors": list(item.get("promotion_factors") or []),
            "input_snapshot_version": snapshot_version,
          }
          await repo.save_lifecycle_and_assessment(
            trade_date=trade_date,
            instrument_code=str(item["code"]),
            as_of=as_of,
            snapshot_version=snapshot_version,
            feature_version=FIRST_BOARD_FEATURE_VERSION,
            stage=str(item.get("stage") or ""),
            ever_touched_limit=bool(item.get("first_touch_at")),
            break_count=int(item.get("break_count", 0) or 0),
            lifecycle_payload=dict(item),
            assessment_payload=assessment_payload,
          )
          self._persisted_snapshot_versions.add(snapshot_version)
        if chain_version and chain_version not in self._persisted_chain_versions:
          await repo.save_chain(
            LimitUpChainSnapshot(
              trade_date=trade_date,
              as_of=_projection_datetime(chain.get("as_of")) or time_utils.now(),
              snapshot_version=chain_version,
              score_version=str(radar.get("score_version") or RADAR_SCORE_VERSION),
              max_board_count=int(chain.get("max_board_count", 0) or 0),
              first_board_count=int(chain.get("first_board_count", 0) or 0),
              sealed_count=int(chain.get("sealed_count", 0) or 0),
              broken_count=int(chain.get("broken_count", 0) or 0),
              break_rate=_number(chain.get("break_rate")),
              payload=chain,
            )
          )
          self._persisted_chain_versions.add(chain_version)
    except Exception as exc:
      logger.warning("首板晋级市场事实持久化失败: %s", exc.__class__.__name__)


limit_up_radar_store = LimitUpRadarProjectionStore()
limit_up_radar_monitor = LimitUpRadarMonitor(store=limit_up_radar_store)
