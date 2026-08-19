"""Point-in-time replay of the authoritative first-board trading policy."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Mapping

import pandas as pd
from quantx_domain.trading.exit_plan import (
  ExitEvaluationContext,
  ExitPlanBook,
  estimate_net_profit_pct,
)
from quantx_domain.trading.first_board_policy import (
  FirstBoardEntryPolicy,
  FirstBoardExitPolicy,
  FirstBoardMarketSnapshot,
  build_first_board_exit_plan,
  evaluate_first_board_market_signal,
)
from quantx_domain.trading.first_board_promotion import (
  FirstBoardPromotionEvaluator,
  FirstBoardPromotionFeatures,
)


@dataclass(frozen=True)
class FirstBoardReplayConfig:
  entry_volume: int = 100
  entry_order_ttl_ms: int = 15_000
  book_depth_participation_pct: float = 0.25
  entry_policy: FirstBoardEntryPolicy = FirstBoardEntryPolicy()
  exit_policy: FirstBoardExitPolicy = FirstBoardExitPolicy()


@dataclass(frozen=True)
class FirstBoardReplayQuality:
  candidate_snapshot_count: int
  market_signal_count: int
  completed_trade_count: int
  excluded_count: int
  coverage_ratio: float
  exclusion_reasons: Mapping[str, int]

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


@dataclass(frozen=True)
class FirstBoardReplayResult:
  trades: pd.DataFrame
  decisions: pd.DataFrame
  quality: FirstBoardReplayQuality


class FirstBoardPolicyReplay:
  """Replay shared entry/exit rules without importing Engine or account state."""

  SNAPSHOT_REQUIRED = {
    "instrument_code",
    "signal_at",
    "feature_as_of",
    "stage",
    "change_pct",
    "limit_up_price",
    "current_price",
  }
  TICK_REQUIRED = {
    "instrument_code",
    "timestamp",
    "last_price",
    "limit_up_price",
    "limit_down_price",
    "price_tick",
    "bid1_price",
    "bid1_volume",
    "ask1_price",
    "ask1_volume",
    "bid_prices",
    "bid_volumes",
    "ask_prices",
    "ask_volumes",
  }

  def __init__(
    self,
    config: FirstBoardReplayConfig | None = None,
    *,
    evaluator: FirstBoardPromotionEvaluator | None = None,
  ) -> None:
    self.config = config or FirstBoardReplayConfig()
    self.evaluator = evaluator or FirstBoardPromotionEvaluator()

  def run(self, snapshots: pd.DataFrame, ticks: pd.DataFrame) -> FirstBoardReplayResult:
    sample, market_ticks = self._validate(snapshots, ticks)
    exclusions: Counter[str] = Counter()
    decisions: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    signalled: set[tuple[str, str]] = set()
    production_signalled: set[tuple[str, str]] = set()

    grouped_ticks = {
      str(code): frame.reset_index(drop=True)
      for code, frame in market_ticks.groupby("instrument_code", sort=False)
    }
    for row in sample.to_dict(orient="records"):
      code = str(row["instrument_code"])
      signal_at = pd.Timestamp(row["signal_at"]).to_pydatetime()
      promotion = self.evaluator.evaluate(_promotion_features(row))
      market = _market_snapshot(row, signal_at)
      signal = evaluate_first_board_market_signal(
        market,
        self.config.entry_policy,
        promotion_eligible=promotion.eligible,
        promotion_reason=(
          promotion.veto_reasons[0]
          if promotion.veto_reasons
          else "candidate_not_eligible"
        ),
      )
      baseline_signal = evaluate_first_board_market_signal(
        market,
        self.config.entry_policy,
        promotion_eligible=True,
      )
      decision_row = {
        **row,
        "model_version": promotion.model_version,
        "exit_policy_version": promotion.exit_policy_version,
        "eligible": signal.eligible,
        "all_near_limit_eligible": baseline_signal.eligible,
        "v1_eligible": bool(
          baseline_signal.eligible
          and float(row.get("radar_score", 100.0) or 0.0) >= 60.0
        ),
        "signal_reason": signal.reason,
        "promotion_score": promotion.rank_score,
        "cvar95_loss_pct": promotion.cvar95_loss_pct,
        "signal_price": signal.signal_price,
        "distance_to_limit_ticks": signal.distance_to_limit_ticks,
      }
      decisions.append(decision_row)
      if not baseline_signal.eligible:
        exclusions[baseline_signal.reason] += 1
        continue
      signal_key = (code, signal_at.date().isoformat())
      if signal.eligible:
        production_signalled.add(signal_key)
      if signal_key in signalled:
        exclusions["DUPLICATE_DAILY_SIGNAL"] += 1
        continue
      signalled.add(signal_key)
      code_ticks = grouped_ticks.get(code)
      if code_ticks is None or code_ticks.empty:
        exclusions["MISSING_TICK_STREAM"] += 1
        continue
      outcome = self._replay_trade(
        decision_row,
        code_ticks,
        signal_at=signal_at,
        cvar95_loss_pct=promotion.cvar95_loss_pct,
      )
      if outcome.get("excluded_reason"):
        exclusions[str(outcome["excluded_reason"])] += 1
      else:
        trades.append(outcome)

    signal_count = len(production_signalled)
    completed = sum(bool(item.get("eligible")) for item in trades)
    quality = FirstBoardReplayQuality(
      candidate_snapshot_count=len(sample),
      market_signal_count=signal_count,
      completed_trade_count=completed,
      excluded_count=sum(exclusions.values()),
      coverage_ratio=(completed / signal_count if signal_count else 0.0),
      exclusion_reasons=dict(sorted(exclusions.items())),
    )
    return FirstBoardReplayResult(
      trades=pd.DataFrame(trades),
      decisions=pd.DataFrame(decisions),
      quality=quality,
    )

  def _replay_trade(
    self,
    signal: Mapping[str, Any],
    ticks: pd.DataFrame,
    *,
    signal_at: Any,
    cvar95_loss_pct: float,
  ) -> dict[str, Any]:
    code = str(signal["instrument_code"])
    ttl = signal_at + timedelta(milliseconds=self.config.entry_order_ttl_ms)
    entry_candidates = ticks[
      (ticks["timestamp"] >= signal_at) & (ticks["timestamp"] <= ttl)
    ]
    if entry_candidates.empty:
      return {"excluded_reason": "MISSING_ENTRY_TICK_COVERAGE"}
    if not all(
      _complete_five_level_book(row)
      for row in entry_candidates.to_dict(orient="records")
    ):
      return {"excluded_reason": "MISSING_FIVE_LEVEL_BOOK"}
    entry_tick = next(
      (
        row
        for row in entry_candidates.to_dict(orient="records")
        if _entry_fill_capacity(row, self.config.book_depth_participation_pct)
        >= self.config.entry_volume
        and 0 < float(row["ask1_price"]) <= float(signal["limit_up_price"])
      ),
      None,
    )
    if entry_tick is None:
      return {"excluded_reason": "ENTRY_NOT_FILLED"}
    entry_time = pd.Timestamp(entry_tick["timestamp"]).to_pydatetime()
    entry_price = float(entry_tick["ask1_price"])
    plan_id = f"research:first-board:{code}:{entry_time.isoformat()}"
    template = build_first_board_exit_plan(
      plan_id=plan_id,
      account_id="research",
      instrument_code=code,
      strategy_id="ashare_limit_up_board_assistant",
      run_id="offline-research",
      entry_trade_date=entry_time.date().isoformat(),
      signal_price=float(signal["signal_price"]),
      entry_limit_up=float(signal["limit_up_price"]),
      promotion_model_version=str(signal["model_version"]),
      exit_policy_version=str(signal["exit_policy_version"]),
      cvar95_loss_pct=float(cvar95_loss_pct),
      policy=self.config.exit_policy,
      auto_exit_authorized=True,
    )
    book = ExitPlanBook()
    plan = book.register_entry_fill(
      template,
      volume=self.config.entry_volume,
      price=entry_price,
      trade_time=entry_time,
    )
    exit_reason = ""
    exit_rule = ""
    exit_time = None
    for tick in ticks[ticks["timestamp"] > entry_time].to_dict(orient="records"):
      timestamp = pd.Timestamp(tick["timestamp"]).to_pydatetime()
      context = ExitEvaluationContext(
        timestamp=timestamp,
        current_price=float(tick["last_price"]),
        bid_price=float(tick["bid1_price"]),
        ask_price=float(tick["ask1_price"]),
        limit_up=float(tick["limit_up_price"]),
        limit_down=float(tick["limit_down_price"]),
        price_tick=float(tick["price_tick"]),
        cumulative_volume=float(tick.get("volume", 0.0) or 0.0),
        cumulative_amount=float(tick.get("amount", 0.0) or 0.0),
        source="research-tick",
      )
      evaluated = book.evaluate(code, context)
      if not evaluated:
        continue
      decision = evaluated[0]
      capacity = _exit_fill_capacity(tick, self.config.book_depth_participation_pct)
      if capacity <= 0:
        continue
      fill_volume = min(decision.volume, capacity)
      fill_price = float(tick["bid1_price"])
      if fill_price <= 0 or (
        float(tick["limit_down_price"]) > 0
        and fill_price <= float(tick["limit_down_price"]) + 1e-8
        and float(tick["bid1_volume"]) <= 0
      ):
        continue
      book.apply_exit_fill(
        plan_id=plan.plan_id,
        volume=fill_volume,
        price=fill_price,
        rule_id=decision.rule_id,
      )
      exit_reason = decision.reason
      exit_rule = decision.rule_type
      exit_time = timestamp
      if plan.remaining_volume <= 0:
        break
    if plan.remaining_volume > 0 or exit_time is None:
      return {"excluded_reason": "INCOMPLETE_EXIT_TICK_COVERAGE"}
    return {
      **dict(signal),
      "entry_order_at": signal_at,
      "entry_filled_at": entry_time,
      "entry_price": entry_price,
      "entry_volume": self.config.entry_volume,
      "exit_rule": exit_rule,
      "exit_reason": exit_reason,
      "exit_at": exit_time,
      "exit_price": plan.exit_avg_price,
      "exit_volume": plan.exited_volume,
      "holding_trading_days": plan.holding_trading_days,
      "net_return_pct": estimate_net_profit_pct(
        entry_price=entry_price,
        exit_price=plan.exit_avg_price,
        volume=self.config.entry_volume,
        costs=template.costs,
      ),
      **_outcome_labels(ticks, signal_at, entry_time),
      "outcome_at": exit_time,
      "historical_rules_complete": True,
    }

  def _validate(
    self, snapshots: pd.DataFrame, ticks: pd.DataFrame
  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    missing_snapshots = sorted(self.SNAPSHOT_REQUIRED - set(snapshots.columns))
    missing_ticks = sorted(self.TICK_REQUIRED - set(ticks.columns))
    if missing_snapshots:
      raise ValueError(f"first-board snapshots missing columns: {missing_snapshots}")
    if missing_ticks:
      raise ValueError(f"first-board ticks missing columns: {missing_ticks}")
    sample = snapshots.copy()
    market_ticks = ticks.copy()
    sample["instrument_code"] = sample["instrument_code"].astype(str).str.upper()
    market_ticks["instrument_code"] = (
      market_ticks["instrument_code"].astype(str).str.upper()
    )
    sample["signal_at"] = pd.to_datetime(sample["signal_at"], errors="raise")
    sample["feature_as_of"] = pd.to_datetime(sample["feature_as_of"], errors="raise")
    market_ticks["timestamp"] = pd.to_datetime(
      market_ticks["timestamp"], errors="raise"
    )
    if bool((sample["feature_as_of"] > sample["signal_at"]).any()):
      raise ValueError("future data detected: feature_as_of is after signal_at")
    return (
      sample.sort_values(["signal_at", "instrument_code"], kind="stable"),
      market_ticks.sort_values(
        ["instrument_code", "timestamp"], kind="stable"
      ).reset_index(drop=True),
    )


def _promotion_features(row: Mapping[str, Any]) -> FirstBoardPromotionFeatures:
  return FirstBoardPromotionFeatures(
    instrument_code=str(row["instrument_code"]),
    stage=str(row["stage"]),
    change_pct=_float(row.get("change_pct")),
    limit_up_price=_float(row.get("limit_up_price")),
    current_price=_float(row.get("current_price")),
    price_change_5m_pct=_float(row.get("price_change_5m_pct")),
    amount_pace_ratio=_float(row.get("amount_pace_ratio")),
    volume_pace_ratio=_float(row.get("volume_pace_ratio")),
    last_5m_volume_ratio=_float(row.get("last_5m_volume_ratio")),
    turnover_rate_pct=_optional_float(row.get("turnover_rate_pct")),
    depth_imbalance_5=_float(row.get("depth_imbalance_5")),
    industry_candidate_count=int(row.get("industry_candidate_count", 0) or 0),
    sector_promotion_rate=_float(row.get("sector_promotion_rate")),
    break_count=int(row.get("break_count", 0) or 0),
    ever_touched_limit=bool(row.get("ever_touched_limit", False)),
    one_word_limit_up=bool(row.get("one_word_limit_up", False)),
    is_stale=bool(row.get("is_stale", False)),
    quality_tags=tuple(row.get("quality_tags", ()) or ()),
    history_trading_days=_optional_int(row.get("history_trading_days")),
    previous_limit_up_streak=int(row.get("previous_limit_up_streak", 0) or 0),
    recent_limit_up_count_10d=int(
      row.get("recent_limit_up_count_10d", row.get("recent_limit_up_count", 0)) or 0
    ),
    price_position_252=_optional_float(
      row.get("price_position_252", row.get("price_position_252d"))
    ),
    prior_20d_return_pct=_optional_float(
      row.get("prior_20d_return_pct", row.get("return_20d_pct"))
    ),
    ma20_deviation_pct=_optional_float(row.get("ma20_deviation_pct")),
    realized_volatility_20_pct=_optional_float(
      row.get("realized_volatility_20_pct", row.get("volatility_20d_pct"))
    ),
  )


def _market_snapshot(
  row: Mapping[str, Any], timestamp: Any
) -> FirstBoardMarketSnapshot:
  return FirstBoardMarketSnapshot(
    instrument_code=str(row["instrument_code"]),
    timestamp=timestamp,
    price=_float(row.get("current_price")),
    limit_up=_float(row.get("limit_up_price")),
    price_tick=max(_float(row.get("price_tick"), 0.01), 1e-8),
    open=_float(row.get("open")),
    high=_float(row.get("high")),
    low=_float(row.get("low")),
    amount=_float(row.get("amount")),
    bid1_volume=int(row.get("bid1_volume", 0) or 0),
    suspended=bool(row.get("suspended", False)),
    is_st=bool(row.get("is_st", False)),
    delist_risk=bool(row.get("delist_risk", False)),
    data_quality=str(row.get("data_quality", "OK") or "OK"),
  )


def _entry_fill_capacity(row: Mapping[str, Any], participation: float) -> int:
  return int(max(0.0, float(row.get("ask1_volume", 0.0) or 0.0) * participation))


def _exit_fill_capacity(row: Mapping[str, Any], participation: float) -> int:
  return int(max(0.0, float(row.get("bid1_volume", 0.0) or 0.0) * participation))


def _complete_five_level_book(row: Mapping[str, Any]) -> bool:
  for key in ("bid_prices", "bid_volumes", "ask_prices", "ask_volumes"):
    value = row.get(key)
    if value is None or isinstance(value, (str, bytes)):
      return False
    try:
      if len(value) < 5:
        return False
    except TypeError:
      return False
  return True


def _outcome_labels(
  ticks: pd.DataFrame, signal_at: Any, entry_time: Any
) -> dict[str, Any]:
  after_signal = ticks[ticks["timestamp"] >= signal_at]
  signal_date = signal_at.date()
  same_day = after_signal[after_signal["timestamp"].dt.date == signal_date]
  later_dates = sorted(
    date_value
    for date_value in after_signal["timestamp"].dt.date.unique()
    if date_value > signal_date
  )
  next_day = (
    after_signal[after_signal["timestamp"].dt.date == later_dates[0]]
    if later_dates
    else after_signal.iloc[0:0]
  )

  def _at_limit(frame: pd.DataFrame) -> pd.Series:
    return (frame["limit_up_price"] > 0) & (
      frame["last_price"] >= frame["limit_up_price"] - frame["price_tick"] / 2
    )

  same_limit = _at_limit(same_day) if not same_day.empty else pd.Series(dtype=bool)
  next_limit = _at_limit(next_day) if not next_day.empty else pd.Series(dtype=bool)
  first_board_close = bool(same_limit.iloc[-1]) if len(same_limit) else False
  next_touch = bool(next_limit.any()) if len(next_limit) else False
  next_seal = bool(next_limit.iloc[-1]) if len(next_limit) else False
  return {
    "trade_date": signal_date,
    "segment": "GROWTH"
    if str(after_signal.iloc[0]["instrument_code"])
    .split(".", 1)[0]
    .startswith(("300", "301", "688"))
    else "MAIN",
    "first_board_close": int(first_board_close),
    "next_day_limit_touch": int(next_touch),
    "next_day_limit_seal": int(next_seal),
    "entry_at": entry_time,
  }


def _float(value: Any, default: float = 0.0) -> float:
  try:
    if value is None or pd.isna(value):
      return default
    return float(value)
  except (TypeError, ValueError):
    return default


def _optional_float(value: Any) -> float | None:
  if value is None or pd.isna(value):
    return None
  return float(value)


def _optional_int(value: Any) -> int | None:
  if value is None or pd.isna(value):
    return None
  return int(value)


__all__ = [
  "FirstBoardPolicyReplay",
  "FirstBoardReplayConfig",
  "FirstBoardReplayQuality",
  "FirstBoardReplayResult",
]
