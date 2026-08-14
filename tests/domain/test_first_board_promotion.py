from quantx_domain.trading.first_board_promotion import (
  FirstBoardHighPositionType,
  FirstBoardPromotionEvaluator,
  FirstBoardPromotionFeatures,
  FirstBoardSegment,
  first_board_segment,
)


def _features(**overrides):
  values = {
    "instrument_code": "600000.SH",
    "stage": "NEAR_LIMIT",
    "change_pct": 9.3,
    "limit_up_price": 11.0,
    "current_price": 10.93,
    "price_change_5m_pct": 1.2,
    "amount_pace_ratio": 2.0,
    "volume_pace_ratio": 2.1,
    "last_5m_volume_ratio": 2.8,
    "turnover_rate_pct": 8.0,
    "depth_imbalance_5": 0.4,
    "industry_candidate_count": 4,
    "history_trading_days": 400,
    "price_position_252": 0.76,
    "prior_20d_return_pct": 8.0,
    "ma20_deviation_pct": 7.0,
    "realized_volatility_20_pct": 3.0,
  }
  values.update(overrides)
  return FirstBoardPromotionFeatures(**values)


def test_board_segments_are_split_and_bse_is_unsupported():
  assert first_board_segment("600000.SH") is FirstBoardSegment.MAIN
  assert first_board_segment("000001.SZ") is FirstBoardSegment.MAIN
  assert first_board_segment("300001.SZ") is FirstBoardSegment.GROWTH
  assert first_board_segment("688001.SH") is FirstBoardSegment.GROWTH
  assert first_board_segment("830001.BJ") is FirstBoardSegment.UNSUPPORTED


def test_eligible_candidate_is_pre_touch_first_board_only():
  result = FirstBoardPromotionEvaluator().evaluate(_features())

  assert result.eligible is True
  assert result.segment is FirstBoardSegment.MAIN
  assert result.high_position_type is FirstBoardHighPositionType.BASE_BREAKOUT
  assert result.first_board_close_probability > 0
  assert result.next_day_limit_touch_probability > 0
  assert result.cvar95_loss_pct > 0


def test_touching_or_existing_chain_is_never_eligible():
  result = FirstBoardPromotionEvaluator().evaluate(
    _features(stage="TOUCHING", ever_touched_limit=True, previous_limit_up_streak=1)
  )

  assert result.eligible is False
  assert "ALREADY_TOUCHED_LIMIT" in result.veto_reasons
  assert "NOT_FIRST_BOARD" in result.veto_reasons


def test_high_position_breakout_is_distinguished_from_overheat():
  breakout = FirstBoardPromotionEvaluator().evaluate(_features())
  overheated = FirstBoardPromotionEvaluator().evaluate(
    _features(prior_20d_return_pct=32.0, ma20_deviation_pct=22.0)
  )

  assert breakout.high_position_type is FirstBoardHighPositionType.BASE_BREAKOUT
  assert overheated.high_position_type is FirstBoardHighPositionType.OVERHEATED
  assert "OVERHEATED_HIGH_POSITION" in overheated.veto_reasons


def test_missing_point_in_time_history_fails_closed():
  result = FirstBoardPromotionEvaluator().evaluate(
    _features(history_trading_days=None, price_position_252=None)
  )

  assert result.eligible is False
  assert result.high_position_type is FirstBoardHighPositionType.DATA_UNKNOWN
  assert "INSUFFICIENT_LISTING_HISTORY" in result.veto_reasons
  assert "PRICE_POSITION_DATA_MISSING" in result.veto_reasons


def test_growth_board_uses_twenty_percent_normalization():
  result = FirstBoardPromotionEvaluator().evaluate(
    _features(
      instrument_code="300001.SZ",
      change_pct=18.6,
      limit_up_price=12.0,
      current_price=11.86,
      prior_20d_return_pct=20.0,
      ma20_deviation_pct=15.0,
    )
  )

  assert result.segment is FirstBoardSegment.GROWTH
  assert result.normalized_limit_progress == 0.93
  assert result.cvar95_loss_pct > 10
