"""Domain enums without Strawberry or ORM decorators."""

from enum import Enum


class StrategyRunMode(str, Enum):
  BACKTEST = "backtest"
  PAPER = "paper"
  LIVE = "live"


class StrategyInstrumentScope(str, Enum):
  SINGLE = "single"
  MULTI = "multi"


class StrategyInstrumentUniverseMode(str, Enum):
  STATIC = "static"
  ACCOUNT_HOLDINGS = "account_holdings"
  RADAR_CANDIDATES = "radar_candidates"


class StrategyCategory(str, Enum):
  TREND_FOLLOWING = "trend_following"
  MEAN_REVERSION = "mean_reversion"
  ARBITRAGE = "arbitrage"
  MARKET_MAKING = "market_making"
  OTHER = "other"


class SellReason(str, Enum):
  STOP_LOSS = "stop_loss"
  STRUCTURE_BREAK = "structure_break"
  TIME_STOP = "time_stop"
  TAKE_PROFIT = "take_profit"
  REBALANCE = "rebalance"
  RISK_CONTROL = "risk_control"


class RiskControlLevel(str, Enum):
  NORMAL = "normal"
  REDUCE = "reduce"
  STOP_OPEN = "stop_open"
  STOP_ALL = "stop_all"
  LIQUIDATE = "liquidate"
