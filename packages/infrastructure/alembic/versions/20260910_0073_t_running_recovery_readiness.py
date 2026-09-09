"""Keep RUNNING exit ownership while recovery revokes entry readiness."""

from alembic import op

revision = "20260910_0073"
down_revision = "20260910_0072"
branch_labels = None
depends_on = None


def constraint(running):
  return (
    f"(status = 'RUNNING' AND {running}) OR "
    "(status = 'DRAINING' AND entry_readiness = 'DRAINING') OR "
    "(status = 'RECONCILE_REQUIRED' AND entry_readiness = 'RECONCILE_REQUIRED') OR "
    "(status IN ('STOPPED','FAILED') AND entry_readiness = 'BLOCKED') OR "
    "status IN ('CREATED','WARMING')"
  )


def replace(expression):
  op.drop_constraint("ck_t_assistant_execution_lifecycle_readiness", "t_assistant_executions")
  op.create_check_constraint("ck_t_assistant_execution_lifecycle_readiness", "t_assistant_executions", constraint(expression))


def upgrade():
  replace("entry_readiness IN ('READY','DEGRADED')")


def downgrade():
  # Refuse rollback with degraded executions; never silently reopen entry.
  replace("entry_readiness = 'READY'")
