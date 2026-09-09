"""Acquire T publication/execution fences before the public account lock."""

from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType

from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig


async def lock_live_entry_sources(db, *, account_id, order_requests):
  owners = set()
  for request in order_requests:
    ref = request.get("execution_ref")
    if (
      isinstance(ref, ExecutionOwnerRef)
      and ref.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION
    ):
      if (
        request.get("account_id") != account_id
        or request.get("t_trade_role") != "ENTRY"
      ):
        raise ValueError("LIVE_ENTRY_SOURCE_LOCK_SCOPE_INVALID")
      owners.add(ref.owner_id)
  if not owners:
    return
  if not db.in_transaction():
    raise ValueError("LIVE_ENTRY_SOURCE_LOCK_TRANSACTION_REQUIRED")
  probes = {}
  for owner in sorted(owners):
    source = await db.get(TAssistantExecutionRecord, owner)
    if (
      source is None or source.environment != "LIVE" or source.account_id != account_id
    ):
      raise ValueError("LIVE_ENTRY_SOURCE_LOCK_SCOPE_INVALID")
    probes[owner] = source.config_id
  heads = {}
  for config_id in sorted(set(probes.values())):
    head = await db.get(
      TTradeGlobalConfig, config_id, with_for_update=True, populate_existing=True
    )
    if head is None or head.account_id != account_id:
      raise ValueError("LIVE_ENTRY_SOURCE_LOCK_SCOPE_INVALID")
    heads[config_id] = head
  for owner in sorted(owners):
    source = await db.get(
      TAssistantExecutionRecord, owner, with_for_update=True, populate_existing=True
    )
    head = heads[probes[owner]]
    if (
      source is None
      or source.config_id != probes[owner]
      or source.account_id != account_id
      or source.environment != "LIVE"
      or source.status != "RUNNING"
      or source.entry_readiness != "READY"
      or not head.enabled
      or head.strategy_run_id
      or head.desired_environment != "LIVE"
      or head.active_config_version_id != source.config_version_id
    ):
      raise ValueError("LIVE_ENTRY_SOURCE_NOT_READY")
