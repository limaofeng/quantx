"""Pure first-board promotion assessment for A-share limit-up candidates.

The evaluator intentionally consumes an as-of feature snapshot.  It never reads
market data, account state, persistence, or model providers, which keeps the
same calculation usable by live scanning, shadow evaluation, and backtests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Optional

FIRST_BOARD_MODEL_VERSION = "first-board-promotion-v2-shadow-1"
FIRST_BOARD_EXIT_POLICY_VERSION = "first-board-exit-v2-shadow-1"
FIRST_BOARD_DISCOVERY_PROGRESS = 0.30


class FirstBoardSegment(StrEnum):
  MAIN = "MAIN"
  GROWTH = "GROWTH"
  UNSUPPORTED = "UNSUPPORTED"


class FirstBoardHighPositionType(StrEnum):
  BASE_BREAKOUT = "BASE_BREAKOUT"
  HIGH_BREAKOUT = "HIGH_BREAKOUT"
  OVERHEATED = "OVERHEATED"
  DATA_UNKNOWN = "DATA_UNKNOWN"


@dataclass(frozen=True)
class FirstBoardPromotionFeatures:
  instrument_code: str
  stage: str
  change_pct: float
  limit_up_price: float
  current_price: float
  price_change_5m_pct: float = 0.0
  amount_pace_ratio: float = 0.0
  volume_pace_ratio: float = 0.0
  last_5m_volume_ratio: float = 0.0
  turnover_rate_pct: Optional[float] = None
  depth_imbalance_5: float = 0.0
  industry_candidate_count: int = 0
  sector_promotion_rate: float = 0.0
  break_count: int = 0
  ever_touched_limit: bool = False
  one_word_limit_up: bool = False
  is_stale: bool = False
  quality_tags: tuple[str, ...] = ()
  history_trading_days: Optional[int] = None
  previous_limit_up_streak: int = 0
  recent_limit_up_count_10d: int = 0
  price_position_252: Optional[float] = None
  prior_20d_return_pct: Optional[float] = None
  ma20_deviation_pct: Optional[float] = None
  realized_volatility_20_pct: Optional[float] = None


@dataclass(frozen=True)
class FirstBoardPromotionFactor:
  code: str
  label: str
  contribution: float
  explanation: str


@dataclass(frozen=True)
class FirstBoardPromotionAssessment:
  model_version: str
  exit_policy_version: str
  segment: FirstBoardSegment
  observed: bool
  eligible: bool
  veto_reasons: tuple[str, ...]
  high_position_type: FirstBoardHighPositionType
  normalized_limit_progress: float
  first_board_close_probability: float
  next_day_limit_touch_probability: float
  next_day_limit_seal_probability: float
  expected_net_return_pct: float
  cvar95_loss_pct: float
  rank_score: float
  factors: tuple[FirstBoardPromotionFactor, ...] = field(default_factory=tuple)

  def to_dict(self) -> dict[str, Any]:
    payload = asdict(self)
    payload["segment"] = self.segment.value
    payload["high_position_type"] = self.high_position_type.value
    return payload


class FirstBoardPromotionEvaluator:
  """Versioned, deterministic shadow model for first-board promotion.

  Values are deliberately labelled as a shadow model.  Production promotion
  requires replacing its frozen coefficients with walk-forward research output;
  the eligibility and risk interfaces remain unchanged.
  """

  CRITICAL_QUALITY_TAGS = frozenset(
    {
      "MISSING_VOLUME_BASELINE",
      "MISSING_AMOUNT_BASELINE",
      "MISSING_FLOAT_VOLUME",
      "MISSING_DEPTH",
      "MISSING_PRICE_HISTORY_5M",
      "STALE_MARKET_DATA",
    }
  )
  PRE_TOUCH_STAGES = frozenset({"MOMENTUM", "SURGING", "NEAR_LIMIT"})

  def evaluate(
    self, features: FirstBoardPromotionFeatures
  ) -> FirstBoardPromotionAssessment:
    segment = first_board_segment(features.instrument_code)
    limit_pct = 20.0 if segment is FirstBoardSegment.GROWTH else 10.0
    progress = _clamp(features.change_pct / max(limit_pct, 1e-6), 0.0, 1.25)
    high_position = self._high_position(features, segment)
    vetoes = list(self._veto_reasons(features, segment, progress, high_position))

    progress_factor = _clamp((progress - 0.30) / 0.70)
    acceleration_factor = _clamp(
      features.price_change_5m_pct / max(limit_pct * 0.20, 0.5)
    )
    amount_factor = _clamp((features.amount_pace_ratio - 1.0) / 2.0)
    volume_factor = _clamp((features.last_5m_volume_ratio - 1.0) / 4.0)
    depth_factor = _clamp(max(0.0, features.depth_imbalance_5) / 0.6)
    industry_breadth = _clamp(features.industry_candidate_count / 5.0)
    sector_promotion = _clamp(features.sector_promotion_rate)
    industry_factor = 0.65 * industry_breadth + 0.35 * sector_promotion
    turnover_target = 12.0 if segment is FirstBoardSegment.GROWTH else 8.0
    turnover = max(0.0, float(features.turnover_rate_pct or 0.0))
    turnover_factor = _clamp(1.0 - abs(turnover - turnover_target) / turnover_target)
    base_bonus = 1.0 if high_position is FirstBoardHighPositionType.BASE_BREAKOUT else 0.5

    close_probability = _clamp(
      0.18
      + 0.25 * progress_factor
      + 0.11 * acceleration_factor
      + 0.12 * amount_factor
      + 0.10 * volume_factor
      + 0.08 * turnover_factor
      + 0.08 * depth_factor
      + 0.05 * industry_factor
      + 0.03 * base_bonus
      - 0.10 * min(features.break_count, 2),
      0.03,
      0.94,
    )
    touch_probability = _clamp(
      0.05
      + 0.55 * close_probability
      + 0.08 * industry_factor
      + 0.05 * base_bonus
      - (0.08 if high_position is FirstBoardHighPositionType.HIGH_BREAKOUT else 0.0),
      0.02,
      0.88,
    )
    seal_probability = _clamp(
      touch_probability
      * (0.55 if segment is FirstBoardSegment.MAIN else 0.45),
      0.01,
      0.72,
    )

    failure_probability = 1.0 - close_probability
    expected_return = (
      touch_probability * limit_pct * 0.55
      + max(0.0, close_probability - touch_probability) * 1.2
      - failure_probability * limit_pct * 0.35
      - 0.25
    )
    volatility_penalty = _clamp(
      float(features.realized_volatility_20_pct or 0.0) / limit_pct,
      0.0,
      1.0,
    )
    cvar95 = limit_pct * (
      0.68
      + 0.12 * volatility_penalty
      + (0.10 if high_position is FirstBoardHighPositionType.HIGH_BREAKOUT else 0.0)
    )
    rank_score = _clamp(
      50.0
      + expected_return * 6.0
      + touch_probability * 20.0
      - cvar95 * 1.8,
      0.0,
      100.0,
    )

    factors = (
      FirstBoardPromotionFactor(
        "LIMIT_PROGRESS",
        "涨停进度",
        round(progress_factor, 4),
        f"已完成当日涨停幅度的 {progress * 100:.1f}%",
      ),
      FirstBoardPromotionFactor(
        "INTRADAY_ACCELERATION",
        "盘中加速",
        round(acceleration_factor, 4),
        f"近5分钟涨幅 {features.price_change_5m_pct:+.2f}%",
      ),
      FirstBoardPromotionFactor(
        "LIQUIDITY_PACE",
        "量价节奏",
        round((amount_factor + volume_factor + turnover_factor) / 3.0, 4),
        f"成交额进度 {features.amount_pace_ratio:.2f}x，近5分钟量比 {features.last_5m_volume_ratio:.2f}x",
      ),
      FirstBoardPromotionFactor(
        "SECTOR_BREADTH",
        "板块梯队",
        round(industry_factor, 4),
        (
          f"同板块候选 {features.industry_candidate_count} 只，"
          f"首板晋级率 {sector_promotion * 100:.1f}%"
        ),
      ),
      FirstBoardPromotionFactor(
        "PRICE_POSITION",
        "事前价格位置",
        round(base_bonus, 4),
        high_position.value,
      ),
    )
    eligible = not vetoes and features.stage.upper() == "NEAR_LIMIT"
    return FirstBoardPromotionAssessment(
      model_version=FIRST_BOARD_MODEL_VERSION,
      exit_policy_version=FIRST_BOARD_EXIT_POLICY_VERSION,
      segment=segment,
      observed=progress >= FIRST_BOARD_DISCOVERY_PROGRESS - 1e-6,
      eligible=eligible,
      veto_reasons=tuple(vetoes),
      high_position_type=high_position,
      normalized_limit_progress=round(progress, 4),
      first_board_close_probability=round(close_probability, 4),
      next_day_limit_touch_probability=round(touch_probability, 4),
      next_day_limit_seal_probability=round(seal_probability, 4),
      expected_net_return_pct=round(expected_return, 4),
      cvar95_loss_pct=round(cvar95, 4),
      rank_score=round(rank_score, 2),
      factors=factors,
    )

  def _veto_reasons(
    self,
    features: FirstBoardPromotionFeatures,
    segment: FirstBoardSegment,
    progress: float,
    high_position: FirstBoardHighPositionType,
  ) -> Iterable[str]:
    if segment is FirstBoardSegment.UNSUPPORTED:
      yield "UNSUPPORTED_BOARD_SEGMENT"
    if features.history_trading_days is None or features.history_trading_days < 120:
      yield "INSUFFICIENT_LISTING_HISTORY"
    if features.previous_limit_up_streak > 0:
      yield "NOT_FIRST_BOARD"
    if features.is_stale:
      yield "STALE_MARKET_DATA"
    if features.one_word_limit_up:
      yield "ONE_WORD_LIMIT_UP"
    if features.ever_touched_limit or features.stage.upper() in {
      "TOUCHING",
      "SEALED",
      "BROKEN",
      "RESEALED",
    }:
      yield "ALREADY_TOUCHED_LIMIT"
    if features.stage.upper() not in self.PRE_TOUCH_STAGES:
      yield "NOT_PRE_TOUCH_STAGE"
    if progress < FIRST_BOARD_DISCOVERY_PROGRESS - 1e-6:
      yield "BELOW_DISCOVERY_PROGRESS"
    if self.CRITICAL_QUALITY_TAGS.intersection(features.quality_tags):
      yield "CRITICAL_DATA_MISSING"
    if high_position is FirstBoardHighPositionType.OVERHEATED:
      yield "OVERHEATED_HIGH_POSITION"
    if high_position is FirstBoardHighPositionType.DATA_UNKNOWN:
      yield "PRICE_POSITION_DATA_MISSING"

  @staticmethod
  def _high_position(
    features: FirstBoardPromotionFeatures,
    segment: FirstBoardSegment,
  ) -> FirstBoardHighPositionType:
    if (
      features.price_position_252 is None
      or features.ma20_deviation_pct is None
      or features.history_trading_days is None
    ):
      return FirstBoardHighPositionType.DATA_UNKNOWN
    position = _clamp(features.price_position_252)
    prior_return = float(features.prior_20d_return_pct or 0.0)
    ma20_deviation = float(features.ma20_deviation_pct or 0.0)
    return_limit = 40.0 if segment is FirstBoardSegment.GROWTH else 25.0
    deviation_limit = 30.0 if segment is FirstBoardSegment.GROWTH else 18.0
    if (
      prior_return >= return_limit
      or ma20_deviation >= deviation_limit
      or features.recent_limit_up_count_10d >= 2
    ):
      return FirstBoardHighPositionType.OVERHEATED
    if position < 0.70:
      return FirstBoardHighPositionType.BASE_BREAKOUT
    base_return_limit = 25.0 if segment is FirstBoardSegment.GROWTH else 15.0
    base_deviation_limit = 18.0 if segment is FirstBoardSegment.GROWTH else 10.0
    if prior_return <= base_return_limit and ma20_deviation <= base_deviation_limit:
      return FirstBoardHighPositionType.BASE_BREAKOUT
    return FirstBoardHighPositionType.HIGH_BREAKOUT


def first_board_segment(instrument_code: str) -> FirstBoardSegment:
  code = str(instrument_code or "").upper()
  digits = code.split(".", 1)[0]
  if code.endswith(".SZ") and digits.startswith(("300", "301")):
    return FirstBoardSegment.GROWTH
  if code.endswith(".SH") and digits.startswith("688"):
    return FirstBoardSegment.GROWTH
  if code.endswith(".SH") and digits.startswith(("600", "601", "603", "605")):
    return FirstBoardSegment.MAIN
  if code.endswith(".SZ") and digits.startswith(("000", "001", "002", "003")):
    return FirstBoardSegment.MAIN
  return FirstBoardSegment.UNSUPPORTED


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
  return max(lower, min(upper, float(value)))
