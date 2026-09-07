"""Real SQLite PAPER facts through the authorized Strawberry query boundary."""

from datetime import UTC, datetime, timedelta

import pytest
import strawberry
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers import t_assistant_paper as projection
from quantx_api.gqlapi.schemas.t_assistant_paper_schema import TAssistantPaperQuery
from quantx_engine.t_assistant_paper_entry_runtime import TAssistantPaperEntryRuntime
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from sqlalchemy import event, update

from tests.engine.unit.test_t_assistant_paper_entry_runtime import seeded
from tests.infrastructure.test_t_candidate_evidence import (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  sessions,
)

_FIXTURES = allocation_sessions, base_sessions, frozen_config, ledger_sessions, sessions
SCHEMA = strawberry.Schema(query=TAssistantPaperQuery)


def context():
  return {
    "principal": Principal(
      user_id="user-1",
      username="operator",
      display_name="Operator",
      device_session_id="session-1",
      access_token_expires_at=datetime.now(UTC).replace(tzinfo=None)
      + timedelta(minutes=5),
      permissions=frozenset({"portfolio:read"}),
      authorized_account_ids=("account-1",),
      is_native_session=True,
    )
  }


async def query(text, variables=None):
  return await SCHEMA.execute(text, variable_values=variables, context_value=context())


async def test_actual_frozen_candidates_pagination_scope_and_missing_seed(
  sessions, frozen_config, monkeypatch
):
  source, _ = await seeded(sessions, initialize=False)
  monkeypatch.setattr(projection, "AsyncSessionLocal", sessions)
  execution = await projection.TAssistantPaperResolver.execution(
    "account-1", source.execution_id
  )
  assert execution.environment == "PAPER" and execution.scorer_mode == "RULE_ONLY"
  assert execution.seed_present is False and execution.seed_as_of is None
  assert execution.entry_readiness_as_of.utcoffset() == timedelta(0)
  opportunities = await projection.TAssistantPaperResolver.page(
    "opportunities", "account-1", source.execution_id, 100
  )
  frozen = [node for node in opportunities.nodes if node.frozen_evidence_present]
  assert len(frozen) >= 2
  assert all(
    node.score is not None and node.source_at <= node.accepted_at for node in frozen
  )
  assert all(node.evaluated_at.utcoffset() == timedelta(hours=8) for node in frozen)
  first = await projection.TAssistantPaperResolver.page(
    "opportunities", "account-1", source.execution_id, 1
  )
  second = await projection.TAssistantPaperResolver.page(
    "opportunities", "account-1", source.execution_id, 1, first.page_info.end_cursor
  )
  assert first.nodes[0].evidence_id != second.nodes[0].evidence_id
  with pytest.raises(ValueError, match="CURSOR_SCOPE"):
    await projection.TAssistantPaperResolver.page(
      "orders", "account-1", source.execution_id, 1, first.page_info.end_cursor
    )
  assert not (
    await projection.TAssistantPaperResolver.page(
      "orders", "account-1", source.execution_id
    )
  ).nodes
  denied = await query(
    'query($id:String!){tAssistantPaperExecution(accountId:"foreign",executionId:$id){executionId}}',
    {"id": source.execution_id},
  )
  assert denied.errors
  with pytest.raises(ValueError, match="NOT_FOUND"):
    await projection.TAssistantPaperResolver.execution("foreign", source.execution_id)
  async with sessions() as db, db.begin():
    await db.execute(
      update(TAssistantExecutionRecord)
      .where(TAssistantExecutionRecord.execution_id == source.execution_id)
      .values(environment="LIVE")
    )
  with pytest.raises(ValueError, match="NOT_FOUND"):
    await projection.TAssistantPaperResolver.execution("account-1", source.execution_id)


async def test_schema_reads_real_orders_allocations_without_writes_or_n_plus_one(
  sessions, frozen_config, monkeypatch
):
  source, witnesses = await seeded(sessions)

  async def provider(code):
    return witnesses[code]

  result = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert len(result.order_ids) == 2
  monkeypatch.setattr(projection, "AsyncSessionLocal", sessions)
  first_allocation = await projection.TAssistantPaperResolver.page(
    "allocations", "account-1", source.execution_id, 1
  )
  second_allocation = await projection.TAssistantPaperResolver.page(
    "allocations",
    "account-1",
    source.execution_id,
    1,
    first_allocation.page_info.end_cursor,
  )
  assert first_allocation.nodes[0].rank == 1
  assert second_allocation.nodes[0].rank == 2
  assert first_allocation.nodes[0].decision_id != second_allocation.nodes[0].decision_id
  assert second_allocation.page_info.has_next_page is False
  assert first_allocation.nodes[0].reason_codes is not None
  statements = []

  def observe(_connection, _cursor, statement, _parameters, _context, _many):
    statements.append(statement.lower())

  engine = sessions.kw["bind"].sync_engine
  event.listen(engine, "before_cursor_execute", observe)
  try:
    data = {}
    fields = [
      'tAssistantPaperExecutions(accountId:"account-1"){nodes{executionId seedPresent seedAsOf entryReadinessReasons}}',
      'tAssistantPaperOrders(accountId:"account-1",executionId:$id){nodes{orderId ownerType ownerId filledVolume sourceAt acceptedAt} pageInfo{hasNextPage}}',
      'tAssistantPaperAllocations(accountId:"account-1",executionId:$id){nodes{status rank action allocatedAmountCap nextEligibleAt}}',
      'tAssistantPaperExitPlans(accountId:"account-1",executionId:$id){nodes{planId remainingVolume lastError}}',
      'tAssistantPaperReasons(accountId:"account-1",executionId:$id){nodes{eventId eventType reasonCode}}',
    ]
    # The fixture has one SQLite connection; separate operations avoid concurrent
    # BEGIN on that connection while each resolver still exercises Strawberry.
    for field in fields:
      header = "query($id:String!)" if "$id" in field else "query"
      response = await query(header + "{" + field + "}", {"id": source.execution_id})
      assert not response.errors
      data.update(response.data)
  finally:
    event.remove(engine, "before_cursor_execute", observe)
  assert data["tAssistantPaperExecutions"]["nodes"][0]["seedPresent"] is True
  orders = data["tAssistantPaperOrders"]["nodes"]
  assert len(orders) == 2 and all(
    row["ownerId"] == source.execution_id for row in orders
  )
  assert all(row["acceptedAt"].endswith("+00:00") for row in orders)
  assert len(data["tAssistantPaperAllocations"]["nodes"]) == 2
  assert len([sql for sql in statements if sql.lstrip().startswith("select")]) == 9
  assert all(sql.lstrip().startswith(("select", "begin")) for sql in statements)
  assert not any(
    f"from {table}" in sql or f"join {table}" in sql
    for sql in statements
    for table in ("positions", "accounts", "orders", "agent_command_outbox")
  )


@pytest.mark.parametrize("first", [0, -1, 101])
async def test_page_size_rejected_before_database(first):
  with pytest.raises(ValueError, match="PAGE_SIZE"):
    await projection.TAssistantPaperResolver.page(
      "executions", "account-1", first=first
    )


async def test_actual_exit_plan_sell_owner_and_protection_scope(sessions, monkeypatch):
  from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
  from sqlalchemy import select

  from tests.infrastructure.test_paper_receipt_convergence import (
    test_public_plan_sell_receipt_closes_batch_without_pending_mismatch as seed_completed_exit,
  )

  # Reuse the complete real ledger BUY -> public plan -> SELL receipt scenario.
  await seed_completed_exit(sessions)
  monkeypatch.setattr(projection, "AsyncSessionLocal", sessions)
  async with sessions() as db:
    execution_id = await db.scalar(select(TAssistantExecutionRecord.execution_id))
  orders = await projection.TAssistantPaperResolver.page(
    "orders", "account-1", execution_id
  )
  sells = [row for row in orders.nodes if row.side == "SELL"]
  assert len(sells) == 1
  assert sells[0].owner_type == "EXIT_PLAN" and sells[0].owner_id == "paper-plan"
  assert sells[0].status == "FILLED" and sells[0].filled_volume == 100
  assert sells[0].source_at <= sells[0].accepted_at
  plans = await projection.TAssistantPaperResolver.page(
    "exitPlans", "account-1", execution_id
  )
  assert len(plans.nodes) == 1
  assert plans.nodes[0].status == "COMPLETED"
  assert plans.nodes[0].protected_volume == plans.nodes[0].exited_volume == 100
  assert plans.nodes[0].remaining_volume == 0
  async with sessions() as db, db.begin():
    await db.execute(update(AutoExitPlanRecord).values(account_id="foreign"))
  assert not (
    await projection.TAssistantPaperResolver.page(
      "exitPlans", "account-1", execution_id
    )
  ).nodes


async def test_execution_time_order_scope_and_persisted_readiness_reasons(
  sessions, frozen_config, monkeypatch
):
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantExecutionEventRecord,
  )
  from sqlalchemy import select

  source, _ = await seeded(sessions, initialize=False)
  monkeypatch.setattr(projection, "AsyncSessionLocal", sessions)
  async with sessions() as db, db.begin():
    original = await db.scalar(
      select(TAssistantExecutionRecord).where(
        TAssistantExecutionRecord.execution_id == source.execution_id
      )
    )
    values = {
      column.name: getattr(original, column.name)
      for column in TAssistantExecutionRecord.__table__.columns
    }
    values.update(
      execution_id="z-newer",
      created_at=source.now + timedelta(days=1),
      status="CREATED",
      entry_readiness="BLOCKED",
      entry_readiness_reasons=["PAPER_SEED_REQUIRED"],
    )
    db.add(TAssistantExecutionRecord(**values))
    db.add(
      TAssistantExecutionEventRecord(
        event_id="audit-1",
        execution_id="z-newer",
        event_key="readiness-1",
        event_type="ENTRY_BLOCKED",
        occurred_at=source.now,
        source_type="READINESS",
        source_id=None,
        payload={"reason_codes": ["PAPER_SEED_REQUIRED"]},
      )
    )
  first = await projection.TAssistantPaperResolver.page(
    "executions", "account-1", first=1
  )
  assert first.nodes[0].execution_id == "z-newer"
  assert first.nodes[0].entry_readiness_reasons == ["PAPER_SEED_REQUIRED"]
  second = await projection.TAssistantPaperResolver.page(
    "executions", "account-1", first=1, after=first.page_info.end_cursor
  )
  assert second.nodes[0].execution_id == source.execution_id
  assert not second.page_info.has_next_page
  reasons = await projection.TAssistantPaperResolver.page(
    "reasons", "account-1", "z-newer"
  )
  assert reasons.nodes[0].reason_codes == ["PAPER_SEED_REQUIRED"]
  assert reasons.nodes[0].source_id is None
  with pytest.raises(ValueError, match="CURSOR_SCOPE"):
    await projection.TAssistantPaperResolver.page(
      "reasons", "account-1", source.execution_id, after=reasons.page_info.end_cursor
    )
