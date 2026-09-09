"""Durable legacy ENTRY fence shared by Engine runtime and public dispatch."""

from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import TTradeRolloutEvent
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig

LEGACY_T_DRAIN_EVENT = "LEGACY_T_DRAIN_STARTED"


def legacy_t_drain_event_id(run_id: str) -> str:
  return f"legacy-t-drain:{run_id}"


async def _account_drain_markers(db, account_id):
  return list(
    await db.scalars(
      select(TTradeRolloutEvent)
      .where(
        TTradeRolloutEvent.account_id == account_id,
        TTradeRolloutEvent.event_type == LEGACY_T_DRAIN_EVENT,
      )
      .execution_options(populate_existing=True)
    )
  )


def _checked_marker_config(marker):
  details = marker.details if isinstance(marker.details, dict) else {}
  request = details.get("request")
  run_id = details.get("run_id")
  if (
    not isinstance(run_id, str)
    or not run_id.strip()
    or marker.event_id != legacy_t_drain_event_id(run_id)
    or marker.next_stage != "DRAINING"
    or not isinstance(request, dict)
    or request.get("run_id") != run_id
    or not isinstance(request.get("config_id"), str)
    or not request["config_id"].strip()
  ):
    raise ValueError("LEGACY_T_DRAIN_MARKER_CONFLICT")
  return request["config_id"]


async def legacy_t_config_is_draining(db, *, account_id: str, config_id: str) -> bool:
  """Persisted cutover prevents legacy reconstruction even after head unbinding."""
  if not db.in_transaction() or not account_id or not config_id:
    raise ValueError("LEGACY_T_DRAIN_SCOPE_REQUIRED")
  configs = {
    _checked_marker_config(row) for row in await _account_drain_markers(db, account_id)
  }
  return config_id in configs


async def legacy_t_entry_is_draining(
  db, *, account_id: str, run_id: str, lock_head=False
) -> bool:
  if not db.in_transaction() or not account_id or not run_id:
    raise ValueError("LEGACY_T_DRAIN_SCOPE_REQUIRED")
  if lock_head:
    # The cutover writer must acquire this same head before freezing debt and
    # recording its marker. A new order is therefore before or after that cut.
    await db.scalar(
      select(TTradeGlobalConfig)
      .where(
        TTradeGlobalConfig.account_id == account_id,
      )
      .with_for_update()
      .execution_options(populate_existing=True)
    )
  marker = await db.get(
    TTradeRolloutEvent, legacy_t_drain_event_id(run_id), populate_existing=True
  )
  if marker is None:
    # An already-started or racing replacement run must not bypass the cutover
    # simply because its new identity has no per-run marker. Other strategies
    # in this same account retain their own entry authority.
    markers = await _account_drain_markers(db, account_id)
    if not markers:
      return False
    from quantx_infrastructure.core.assistant_strategy_policy import (
      T_TRADE_STRATEGY_CLASS_NAME,
    )
    from quantx_infrastructure.models.strategy import Strategy
    from quantx_infrastructure.models.strategy_run import StrategyRun

    run = await db.get(StrategyRun, run_id)
    strategy = await db.get(Strategy, run.strategy_id) if run else None
    if strategy is None or strategy.class_name != T_TRADE_STRATEGY_CLASS_NAME:
      return False
    for row in markers:
      _checked_marker_config(row)
    return True
  details = dict(marker.details or {})
  if (
    marker.event_type != LEGACY_T_DRAIN_EVENT
    or marker.account_id != account_id
    or marker.next_stage != "DRAINING"
    or details.get("run_id") != run_id
  ):
    raise ValueError("LEGACY_T_DRAIN_MARKER_CONFLICT")
  return True
