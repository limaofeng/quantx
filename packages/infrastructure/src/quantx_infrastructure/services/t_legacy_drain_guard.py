"""Durable legacy ENTRY fence shared by Engine runtime and public dispatch."""

from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import TTradeRolloutEvent
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig

LEGACY_T_DRAIN_EVENT = "LEGACY_T_DRAIN_STARTED"


def legacy_t_drain_event_id(run_id: str) -> str:
  return f"legacy-t-drain:{run_id}"


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
        TTradeGlobalConfig.strategy_run_id == run_id,
      )
      .with_for_update()
      .execution_options(populate_existing=True)
    )
  marker = await db.get(
    TTradeRolloutEvent, legacy_t_drain_event_id(run_id), populate_existing=True
  )
  if marker is None:
    return False
  details = dict(marker.details or {})
  if (
    marker.event_type != LEGACY_T_DRAIN_EVENT
    or marker.account_id != account_id
    or marker.next_stage != "DRAINING"
    or details.get("run_id") != run_id
  ):
    raise ValueError("LEGACY_T_DRAIN_MARKER_CONFLICT")
  return True
