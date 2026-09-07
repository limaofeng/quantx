"""Bounded SQL projections; no broker state, runtime startup or write session."""

import base64
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from quantx_domain.trading.t_assistant_market_state import decode_candidate_evidence
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord as Exit
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord as Seed,
)
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionEventRecord as PaperEvent,
)
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionOrderRecord as Order,
)
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord as Batch,
)
from quantx_infrastructure.models.t_allocation import (
  TAllocationDecisionRecord as Decision,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord as Event,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionRecord as Execution,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation as Opportunity,
)
from sqlalchemy import and_, func, or_, select

from ..types.common_types import PageInfo
from ..types.t_assistant_paper_types import (
  TAssistantPaperAllocation,
  TAssistantPaperExecution,
  TAssistantPaperExitPlan,
  TAssistantPaperOpportunity,
  TAssistantPaperOrder,
  TAssistantPaperPage,
  TAssistantPaperReason,
)


def _utc(value):
  return (
    value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value
  )


def _exchange(value):
  return (
    value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    if value is not None and value.tzinfo is None
    else value
  )


def _ms(value):
  return (
    datetime.fromtimestamp(value / 1000, UTC)
    if type(value) is int and value >= 0
    else None
  )


def _strings(value):
  return (
    value
    if isinstance(value, list) and all(isinstance(x, str) for x in value)
    else None
  )


def _cursor(scope, key):
  return base64.urlsafe_b64encode(
    json.dumps(
      [1, *scope, [_utc(x).isoformat() if isinstance(x, datetime) else x for x in key]],
      separators=(",", ":"),
    ).encode()
  ).decode()


def _decode(cursor, scope, types):
  try:
    value = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
    if value[:4] != [1, *scope] or len(value) != 5:
      raise ValueError
    key = value[4]
    if not isinstance(key, list) or len(key) != len(types):
      raise ValueError
    for i, expected in enumerate(types):
      if expected is datetime:
        key[i] = datetime.fromisoformat(key[i])
        if key[i].tzinfo is None:
          raise ValueError
        key[i] = key[i].astimezone(UTC)
      elif type(key[i]) is not expected:
        raise ValueError
    return key
  except (ValueError, TypeError, UnicodeError) as exc:
    raise ValueError("T_PAPER_CURSOR_SCOPE_INVALID") from exc


def _execution(row, seed):
  return TAssistantPaperExecution(
    **{
      name: getattr(row, name)
      for name in (
        "execution_id",
        "environment",
        "status",
        "entry_readiness",
        "entry_readiness_reasons",
        "scorer_mode",
        "config_version_id",
        "frozen_config_version",
        "config_snapshot_hash",
        "policy_version",
        "feature_schema_version",
      )
    },
    entry_readiness_as_of=_utc(row.entry_readiness_as_of),
    created_at=_utc(row.created_at),
    seed_present=seed is not None,
    seed_as_of=_utc(seed.seed_as_of) if seed else None,
    seed_snapshot_id=seed.seed_snapshot_id if seed else None,
    snapshot_as_of=_utc(seed.snapshot_as_of) if seed else None,
  )


def _opportunity(row):
  witness = row.payload.get("candidate_evidence")
  candidate = tick = None
  if isinstance(witness, dict):
    try:
      candidate, tick, _ = decode_candidate_evidence(witness)
      if (
        candidate.candidate_id != row.candidate_id
        or tick.instrument_code != row.instrument_code
      ):
        candidate = tick = None
    except ValueError:
      pass
  return TAssistantPaperOpportunity(
    evidence_id=row.id,
    candidate_id=row.candidate_id,
    instrument_code=row.instrument_code,
    event_type=row.event_type,
    evaluated_at=_exchange(row.evaluated_at),
    created_at=_utc(row.created_at),
    frozen_evidence_present=candidate is not None,
    source_at=_ms(candidate.source_time_ms) if candidate else None,
    accepted_at=_ms(tick.received_at_ms) if tick else None,
    score=float(candidate.score) if candidate else None,
    reason_codes=_strings(witness.get("evaluation", {}).get("reason_codes"))
    if candidate
    else None,
    candidate_fingerprint=candidate.fingerprint if candidate else None,
  )


def _allocation(batch, decision):
  return TAssistantPaperAllocation(
    **{
      name: getattr(batch, name)
      for name in (
        "allocation_batch_id",
        "cycle_id",
        "allocation_attempt",
        "status",
        "terminal_reason",
      )
    },
    created_at=_utc(batch.created_at),
    committed_at=_utc(batch.committed_at),
    expires_at=_utc(batch.expires_at),
    **{
      name: getattr(decision, name) if decision else None
      for name in (
        "decision_id",
        "instrument_code",
        "candidate_id",
        "rank",
        "action",
        "requested_amount_ceiling",
        "allocated_amount_cap",
      )
    },
    next_eligible_at=_utc(decision.next_eligible_at) if decision else None,
    reason_codes=_strings(decision.evidence.get("blockers")) if decision else None,
  )


def _reason(row):
  reason = row.payload.get("reason_code", row.payload.get("reason"))
  return TAssistantPaperReason(
    event_id=row.event_id,
    event_type=row.event_type,
    occurred_at=_utc(row.occurred_at),
    source_type=row.source_type,
    source_id=row.source_id,
    reason_code=reason if isinstance(reason, str) else None,
    reason_codes=_strings(row.payload.get("reason_codes")),
  )


def _order(row, event):
  return TAssistantPaperOrder(
    **{
      name: getattr(row, name)
      for name in (
        "order_id",
        "intent_id",
        "instrument_code",
        "owner_type",
        "owner_id",
        "side",
        "status",
        "volume",
        "filled_volume",
        "limit_price",
      )
    },
    submitted_at=_utc(row.submitted_at),
    expires_at=_utc(row.expires_at),
    source_at=_utc(event.quote_source_at)
    if event and event.event_type == "QUOTE"
    else None,
    accepted_at=_utc(event.occurred_at) if event else None,
  )


def _exit(row):
  return TAssistantPaperExitPlan(
    **{
      name: getattr(row, name)
      for name in (
        "plan_id",
        "instrument_code",
        "status",
        "protected_volume",
        "exited_volume",
        "remaining_volume",
        "capacity_status",
        "capacity_error",
        "last_error",
      )
    },
    last_evaluated_at=_exchange(row.last_evaluated_at),
  )


class TAssistantPaperResolver:
  @staticmethod
  async def execution(account_id, execution_id):
    async with AsyncSessionLocal() as db:
      row = (
        await db.execute(
          select(Execution, Seed)
          .outerjoin(
            Seed,
            and_(
              Seed.execution_id == Execution.execution_id,
              Seed.account_id == account_id,
              Seed.environment == "PAPER",
            ),
          )
          .where(
            Execution.execution_id == execution_id,
            Execution.account_id == account_id,
            Execution.environment == "PAPER",
          )
        )
      ).first()
      if row is None:
        raise ValueError("T_PAPER_EXECUTION_NOT_FOUND")
      return _execution(*row)

  @staticmethod
  async def page(kind, account_id, execution_id=None, first=20, after=None):
    if type(first) is not int or not 1 <= first <= 100:
      raise ValueError("T_PAPER_PAGE_SIZE_INVALID")
    scope = [account_id, execution_id, kind]
    async with AsyncSessionLocal() as db:
      if kind != "executions":
        exists = await db.scalar(
          select(Execution.execution_id).where(
            Execution.execution_id == execution_id,
            Execution.account_id == account_id,
            Execution.environment == "PAPER",
          )
        )
        if exists is None:
          raise ValueError("T_PAPER_EXECUTION_NOT_FOUND")
      if kind == "executions":
        statement = (
          select(Execution, Seed)
          .outerjoin(
            Seed,
            and_(
              Seed.execution_id == Execution.execution_id,
              Seed.account_id == account_id,
              Seed.environment == "PAPER",
            ),
          )
          .where(Execution.account_id == account_id, Execution.environment == "PAPER")
        )
        keys, mapper = (
          [Execution.created_at, Execution.execution_id],
          lambda row: _execution(*row),
        )
      elif kind == "opportunities":
        statement = select(Opportunity).where(
          Opportunity.account_id == account_id,
          Opportunity.owner_type == "T_ASSISTANT_EXECUTION",
          Opportunity.owner_id == execution_id,
          Opportunity.environment == "PAPER",
          Opportunity.event_type == "T_OPPORTUNITY_CANDIDATE_FROZEN",
        )
        keys, mapper = [Opportunity.id], lambda row: _opportunity(row[0])
      elif kind == "allocations":
        statement = (
          select(Batch, Decision)
          .outerjoin(
            Decision, Decision.allocation_batch_id == Batch.allocation_batch_id
          )
          .where(Batch.execution_id == execution_id, Batch.environment == "PAPER")
        )
        keys, mapper = (
          [
            Batch.created_at,
            Batch.allocation_batch_id,
            func.coalesce(Decision.rank, 0),
            func.coalesce(Decision.decision_id, ""),
          ],
          lambda row: _allocation(*row),
        )
      elif kind == "reasons":
        statement = select(Event).where(Event.execution_id == execution_id)
        keys, mapper = [Event.event_id], lambda row: _reason(row[0])
      elif kind == "orders":
        statement = (
          select(Order, PaperEvent)
          .outerjoin(
            PaperEvent,
            and_(
              PaperEvent.event_id == Order.last_event_id,
              PaperEvent.execution_id == execution_id,
              PaperEvent.environment == "PAPER",
            ),
          )
          .where(Order.execution_id == execution_id, Order.environment == "PAPER")
        )
        keys, mapper = [Order.order_id], lambda row: _order(*row)
      elif kind == "exitPlans":
        statement = select(Exit).where(
          Exit.account_id == account_id,
          Exit.environment == "PAPER",
          Exit.source_execution_environment == "PAPER",
          Exit.source_execution_owner_type == "T_ASSISTANT_EXECUTION",
          Exit.source_execution_owner_id == execution_id,
        )
        keys, mapper = [Exit.plan_id], lambda row: _exit(row[0])
      else:
        raise ValueError("T_PAPER_PAGE_KIND_INVALID")
      descending = [False] * len(keys)
      types = [str] * len(keys)
      if kind == "executions":
        descending, types = [True, True], [datetime, str]
      elif kind == "allocations":
        descending, types = [True, False, False, False], [datetime, str, int, str]
      if after:
        anchor = _decode(after, scope, types)
        statement = statement.where(
          or_(
            *[
              and_(
                *(keys[j] == anchor[j] for j in range(i)),
                (column < anchor[i] if descending[i] else column > anchor[i]),
              )
              for i, column in enumerate(keys)
            ]
          )
        )
      rows = (
        await db.execute(
          statement.add_columns(*keys)
          .order_by(
            *(
              column.desc() if descending[i] else column.asc()
              for i, column in enumerate(keys)
            )
          )
          .limit(first + 1)
        )
      ).all()
      visible = rows[:first]
      cursors = [_cursor(scope, list(row[-len(keys) :])) for row in visible]
      return TAssistantPaperPage(
        nodes=[mapper(row[: -len(keys)]) for row in visible],
        page_info=PageInfo(
          has_next_page=len(rows) > first,
          has_previous_page=bool(after),
          start_cursor=cursors[0] if cursors else None,
          end_cursor=cursors[-1] if cursors else None,
        ),
      )
