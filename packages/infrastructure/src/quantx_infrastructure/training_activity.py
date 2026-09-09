"""Bounded read-only projection of active Trainer control-plane rows."""

from sqlalchemy import String, case, cast, literal, select, union_all

from quantx_infrastructure.models.research_preparation import (
  ResearchPreparationJob as Job,
)
from quantx_infrastructure.models.stock_selection import (
  StockSelectionTrainingRun as Run,
)

ACTIVITY_LIMIT = 50


async def read_training_activity(db):
  training = select(
    literal("TRAINING").label("type"),
    Run.run_id.label("id"),
    Run.run_kind.label("kind"),
    Run.status,
    Run.phase,
    Run.completed_units,
    Run.total_units,
  ).where(Run.status.in_(("QUEUED", "RUNNING")))
  frozen_input = cast(Job.request["certification_input"].as_string(), String)
  preparation = select(
    literal("PREPARATION").label("type"),
    Job.job_id.label("id"),
    Job.kind,
    Job.status,
    Job.phase,
    literal(None).label("completed_units"),
    literal(None).label("total_units"),
  ).where(
    Job.status.in_(("QUEUED", "RUNNING")),
    (Job.kind == "GPU")
    | (
      (Job.kind == "CERTIFY")
      & frozen_input.is_not(None)
      & frozen_input.not_in(("{}", "false", "0", ""))
    ),
  )
  active = union_all(training, preparation).subquery()
  statement = (
    select(active)
    .order_by(
      case((active.c.status == "RUNNING", 0), else_=1),
      active.c.type,
      active.c.id,
    )
    .limit(ACTIVITY_LIMIT + 1)
  )
  rows = list((await db.execute(statement)).mappings())
  return {
    "tasks": [dict(row) for row in rows[:ACTIVITY_LIMIT]],
    "truncated": len(rows) > ACTIVITY_LIMIT,
  }
