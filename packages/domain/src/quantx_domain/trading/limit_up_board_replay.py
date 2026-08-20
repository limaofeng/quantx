"""Shared contracts for account-level limit-up-board historical replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

LIMIT_UP_BOARD_REPLAY_SCHEMA_VERSION = 1
LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE = "STANDARD_V1"
LIMIT_UP_BOARD_REPLAY_TERMINAL_STATUSES = frozenset(
  {"COMPLETED", "CANCELLED", "ERROR"}
)


@dataclass(frozen=True)
class LimitUpBoardReplayScenarioSpec:
  """One immutable, named execution assumption in the standard profile."""

  scenario_id: str
  label: str
  confirmation_delay_ms: int
  participation_cap_pct: float
  book_depth_participation_pct: float
  is_theoretical_upper_bound: bool = False

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


# Product-owned scenarios are versioned and fixed so that the replay remains a
# validation tool rather than an unconstrained parameter optimizer.
STANDARD_V1_SCENARIOS: tuple[LimitUpBoardReplayScenarioSpec, ...] = (
  LimitUpBoardReplayScenarioSpec(
    scenario_id="THEORETICAL",
    label="理论上界",
    confirmation_delay_ms=0,
    participation_cap_pct=0.05,
    book_depth_participation_pct=0.50,
    is_theoretical_upper_bound=True,
  ),
  LimitUpBoardReplayScenarioSpec(
    scenario_id="FAST",
    label="快速确认",
    confirmation_delay_ms=500,
    participation_cap_pct=0.03,
    book_depth_participation_pct=0.25,
  ),
  LimitUpBoardReplayScenarioSpec(
    scenario_id="BASE",
    label="基准情景",
    confirmation_delay_ms=3_000,
    participation_cap_pct=0.02,
    book_depth_participation_pct=0.15,
  ),
  LimitUpBoardReplayScenarioSpec(
    scenario_id="STRESS",
    label="压力情景",
    confirmation_delay_ms=10_000,
    participation_cap_pct=0.01,
    book_depth_participation_pct=0.05,
  ),
)


def get_limit_up_board_replay_scenarios(
  profile: str = LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE,
) -> tuple[LimitUpBoardReplayScenarioSpec, ...]:
  normalized = str(profile or "").strip().upper()
  if normalized != LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE:
    raise ValueError(f"不支持的打板回放情景集: {normalized or profile}")
  return STANDARD_V1_SCENARIOS


__all__ = [
  "LIMIT_UP_BOARD_REPLAY_SCHEMA_VERSION",
  "LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE",
  "LIMIT_UP_BOARD_REPLAY_TERMINAL_STATUSES",
  "LimitUpBoardReplayScenarioSpec",
  "STANDARD_V1_SCENARIOS",
  "get_limit_up_board_replay_scenarios",
]
