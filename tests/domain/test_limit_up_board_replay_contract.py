from quantx_domain.trading.limit_up_board_replay import (
  LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE,
  get_limit_up_board_replay_scenarios,
)


def test_standard_profile_is_fixed_and_ordered_by_execution_stress() -> None:
  scenarios = get_limit_up_board_replay_scenarios()

  assert [item.scenario_id for item in scenarios] == [
    "THEORETICAL",
    "FAST",
    "BASE",
    "STRESS",
  ]
  assert [item.confirmation_delay_ms for item in scenarios] == sorted(
    item.confirmation_delay_ms for item in scenarios
  )
  assert scenarios[0].is_theoretical_upper_bound is True
  assert all(0 < item.participation_cap_pct <= 1 for item in scenarios)
  assert all(0 < item.book_depth_participation_pct <= 1 for item in scenarios)


def test_unknown_scenario_profile_fails_closed() -> None:
  try:
    get_limit_up_board_replay_scenarios("CUSTOM")
  except ValueError as exc:
    assert "CUSTOM" in str(exc)
  else:
    raise AssertionError("unknown replay profile must be rejected")

  assert LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE == "STANDARD_V1"
