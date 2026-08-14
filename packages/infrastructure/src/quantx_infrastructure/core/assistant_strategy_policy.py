"""Shared policy for strategies owned by dedicated assistant surfaces."""

from __future__ import annotations

from typing import Any

T_TRADE_STRATEGY_CLASS_NAME = "AshareIntradayTAssistantStrategy"
LIMIT_UP_BOARD_STRATEGY_CLASS_NAME = "AshareLimitUpBoardStrategy"
LIMIT_UP_BOARD_ASSISTANT_STRATEGY_CLASS_NAME = "AshareLimitUpBoardAssistantStrategy"
ASSISTANT_MANAGED_STRATEGY_CLASS_NAMES = frozenset(
  {
    T_TRADE_STRATEGY_CLASS_NAME,
    LIMIT_UP_BOARD_STRATEGY_CLASS_NAME,
    LIMIT_UP_BOARD_ASSISTANT_STRATEGY_CLASS_NAME,
  }
)
ACTIVE_STRATEGY_RUN_STATUSES = frozenset({"pending", "running", "paused"})


def strategy_class_name(value: Any) -> str:
  strategy = getattr(value, "strategy", value)
  return str(getattr(strategy, "class_name", "") or "")


def is_assistant_managed_strategy(value: Any) -> bool:
  return strategy_class_name(value) in ASSISTANT_MANAGED_STRATEGY_CLASS_NAMES


def enum_value(value: Any) -> str:
  return str(getattr(value, "value", value) or "").lower()


def is_active_execution_run(run: Any) -> bool:
  return (
    enum_value(getattr(run, "mode", None)) != "backtest"
    and enum_value(getattr(run, "status", None)) in ACTIVE_STRATEGY_RUN_STATUSES
  )
