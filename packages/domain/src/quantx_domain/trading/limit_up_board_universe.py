"""Pure point-in-time selection for the account-level board assistant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class LimitUpBoardUniverseSelection:
  instruments: tuple[str, ...]
  metadata: dict[str, dict[str, Any]]
  qualified_count: int


def select_limit_up_board_universe(
  items: Sequence[Mapping[str, Any]],
  *,
  settings: Mapping[str, Any],
  enabled: bool = True,
  preferences: Mapping[str, str] | None = None,
  sticky_codes: Sequence[str] = (),
  force_preferred_codes: Sequence[str] = (),
  arm_versions: Mapping[str, int] | None = None,
) -> LimitUpBoardUniverseSelection:
  """Select Top-N candidates without reading clocks, accounts, or storage."""

  preferences = {
    str(code or "").upper(): str(value or "").upper()
    for code, value in dict(preferences or {}).items()
    if code
  }
  arm_versions = {
    str(code or "").upper(): max(0, int(value or 0))
    for code, value in dict(arm_versions or {}).items()
    if code
  }
  by_code = {
    str(item.get("code") or "").upper(): dict(item)
    for item in items
    if item.get("code")
  }
  qualified: list[tuple[str, dict[str, Any], str]] = []
  if enabled:
    for code, item in by_code.items():
      preference = preferences.get(code, "")
      if (
        str(item.get("stage") or "").upper() == "NEAR_LIMIT"
        and bool(item.get("promotion_eligible"))
        and not bool(item.get("is_stale"))
        and not list(item.get("blocked_reasons") or [])
        and preference != "IGNORE"
      ):
        qualified.append((code, item, preference))
  qualified.sort(
    key=lambda entry: (
      -int(entry[2] == "PREFER"),
      -_number(entry[1].get("promotion_score")),
      -_number(entry[1].get("radar_score")),
      entry[0],
    )
  )
  max_candidates = max(
    1,
    min(50, int(settings.get("max_ranked_candidates", 5) or 5)),
  )
  desired: set[str] = set()
  source_by_code: dict[str, str] = {}
  for code, _item, preference in qualified[:max_candidates]:
    desired.add(code)
    source_by_code[code] = "PREFERRED" if preference == "PREFER" else "AUTO"

  for raw_code in force_preferred_codes:
    code = str(raw_code or "").upper()
    item = by_code.get(code, {})
    if (
      enabled
      and item
      and bool(item.get("promotion_eligible"))
      and not list(item.get("blocked_reasons") or [])
    ):
      desired.add(code)
      source_by_code[code] = "PREFERRED"

  sticky = {str(code or "").upper() for code in sticky_codes if code}
  desired.update(sticky)
  metadata: dict[str, dict[str, Any]] = {}
  for code in sorted(desired):
    item = by_code.get(code, {})
    blocked = list(item.get("blocked_reasons") or [])
    source = source_by_code.get(code, "DRAINING")
    eligible = bool(
      enabled
      and source in {"AUTO", "PREFERRED"}
      and item
      and not bool(item.get("is_stale"))
      and not blocked
    )
    metadata[code] = {
      "eligible": eligible,
      "reason": "ELIGIBLE" if eligible else "DRAINING_EXISTING_WORK",
      "source": source,
      "draining": not eligible and code in sticky,
      "arm_version": arm_versions.get(code, 0),
      "radar_score": _number(item.get("radar_score")),
      "radar_stage": str(item.get("stage") or ""),
      "radar_updated_at": str(item.get("updated_at") or ""),
      "radar_is_stale": bool(item.get("is_stale", False)),
      "promotion_eligible": bool(item.get("promotion_eligible", False)),
      "promotion_score": _number(item.get("promotion_score")),
      "promotion_snapshot_version": str(
        item.get("promotion_snapshot_version") or ""
      ),
      "promotion_model_version": str(item.get("promotion_model_version") or ""),
      "exit_policy_version": str(item.get("exit_policy_version") or ""),
      "board_segment": str(item.get("board_segment") or ""),
      "cvar95_loss_pct": _number(item.get("cvar95_loss_pct")),
      "expected_net_return_pct": _number(item.get("expected_net_return_pct")),
      "target_position_pct": target_position_pct(settings, item),
      "liquidity_cap_amount": liquidity_cap_amount(settings, item),
      "high_position_type": str(item.get("high_position_type") or ""),
    }
  return LimitUpBoardUniverseSelection(
    instruments=tuple(sorted(desired)),
    metadata=metadata,
    qualified_count=len(qualified),
  )


def target_position_pct(
  settings: Mapping[str, Any],
  item: Mapping[str, Any],
) -> float:
  single_cap = _number(settings.get("max_single_position_pct"), 0.02)
  tail_budget = _number(settings.get("planned_tail_loss_pct"), 0.0015)
  cvar_ratio = _number(item.get("cvar95_loss_pct")) / 100.0
  if cvar_ratio <= 0:
    return 0.0
  return max(0.0, min(single_cap, tail_budget / cvar_ratio))


def liquidity_cap_amount(
  settings: Mapping[str, Any],
  item: Mapping[str, Any],
) -> float:
  participation = _number(settings.get("liquidity_participation_pct"), 0.005)
  traded_amount = _number(item.get("amount"))
  if participation <= 0 or traded_amount <= 0:
    return 0.0
  return max(0.0, traded_amount * participation)


def _number(value: Any, default: float = 0.0) -> float:
  try:
    return float(value)
  except (TypeError, ValueError):
    return default
