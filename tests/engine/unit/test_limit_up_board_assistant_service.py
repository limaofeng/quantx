from types import SimpleNamespace

from quantx_engine.limit_up_board_assistant import LimitUpBoardAssistantService


def _candidate(code: str, score: float, **overrides: object) -> dict[str, object]:
  item: dict[str, object] = {
    "code": code,
    "stage": "NEAR_LIMIT",
    "promotion_eligible": True,
    "promotion_score": score,
    "is_stale": False,
    "blocked_reasons": [],
    "cvar95_loss_pct": 5.0,
  }
  item.update(overrides)
  return item


def test_ignored_candidate_does_not_consume_top_five_slot() -> None:
  service = LimitUpBoardAssistantService(runtime_manager=None)
  config = SimpleNamespace(enabled=True, settings={"max_ranked_candidates": 5})
  items = [_candidate(f"00000{index}.SZ", 100 - index) for index in range(6)]
  preferences = {"000000.SZ": SimpleNamespace(preference="IGNORE")}

  universe = service._resolve_universe_snapshot(
    config,
    {"items": items},
    [],
    runtime=None,
    preferences=preferences,
  )
  desired = list(universe.instruments)

  assert "000000.SZ" not in desired
  assert len(desired) == 5
  assert "000005.SZ" in desired


def test_preference_changes_attention_order_but_cannot_bypass_hard_veto() -> None:
  service = LimitUpBoardAssistantService(runtime_manager=None)
  config = SimpleNamespace(enabled=True, settings={"max_ranked_candidates": 2})
  items = [
    _candidate("000001.SZ", 99),
    _candidate("000002.SZ", 98),
    _candidate("000003.SZ", 1),
    _candidate(
      "000004.SZ",
      100,
      promotion_eligible=False,
      blocked_reasons=["OVERHEATED_ACCELERATION"],
    ),
  ]
  preferences = {
    "000003.SZ": SimpleNamespace(preference="PREFER"),
    "000004.SZ": SimpleNamespace(preference="PREFER"),
  }

  universe = service._resolve_universe_snapshot(
    config,
    {"items": items},
    [],
    runtime=None,
    preferences=preferences,
  )
  metadata, desired = universe.metadata, list(universe.instruments)

  assert desired == ["000001.SZ", "000003.SZ"]
  assert metadata["000003.SZ"]["source"] == "PREFERRED"
  assert "000004.SZ" not in desired


def test_candidate_metadata_carries_deterministic_liquidity_amount_cap() -> None:
  service = LimitUpBoardAssistantService(runtime_manager=None)
  config = SimpleNamespace(
    enabled=True,
    settings={
      "max_ranked_candidates": 5,
      "liquidity_participation_pct": 0.005,
    },
  )

  universe = service._resolve_universe_snapshot(
    config,
    {"items": [_candidate("000001.SZ", 90, amount=2_000_000)]},
    [],
    runtime=None,
    preferences={},
  )
  metadata, desired = universe.metadata, list(universe.instruments)

  assert desired == ["000001.SZ"]
  assert metadata["000001.SZ"]["liquidity_cap_amount"] == 10_000
