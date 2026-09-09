"""Require confirmed LIVE entry to be reallocated against post-confirmation facts.

Revision ID: 20260909_0062
Revises: 20260909_0061
"""

from pathlib import Path
from runpy import run_path

from alembic import op

revision = "20260909_0062"
down_revision = "20260909_0061"
branch_labels = None
depends_on = None

_PROOF = """CREATE FUNCTION quantx_t_entry_confirmed(i trade_intents) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM t_assistant_execution_events e
      JOIN trade_confirmation_challenges c ON c.id=e.payload->>'challenge_id'
    WHERE e.execution_id=i.owner_id AND e.event_key='live-entry-confirmed:' || i.id
      AND e.event_type='LIVE_ENTRY_CONFIRMED' AND e.payload->>'intent_id'=i.id
      AND (e.payload->>'allocation_version')::integer <= i.allocation_version
      AND c.owner_type=i.owner_type AND c.owner_id=i.owner_id
      AND c.account_id=i.account_id AND c.environment='LIVE'
      AND c.action='T_TRADE_ENTRY_APPROVAL' AND c.payload->>'intent_id'=i.id
      AND c.consumed_at IS NOT NULL AND c.consumed_at<c.expires_at
      AND c.consumed_at AT TIME ZONE 'Asia/Shanghai' <= e.occurred_at
  )
$$;"""

_REQUEUE = """  IF TG_OP = 'UPDATE' AND OLD.environment='LIVE' AND OLD.allocation_cycle_id IS NOT NULL THEN
    IF OLD.status='AWAITING_APPROVAL' AND NEW.status='ALLOCATION_PENDING' THEN
      IF NOT quantx_t_entry_confirmed(OLD)
        OR NOT EXISTS (SELECT 1 FROM t_assistant_execution_events e
          WHERE e.execution_id=OLD.owner_id AND e.event_key='live-entry-confirmed:' || OLD.id
            AND (e.payload->>'allocation_version')::integer=OLD.allocation_version
            AND e.payload->>'previous_allocation_decision_id'=OLD.allocation_decision_id
            AND e.occurred_at=NEW.updated_at AT TIME ZONE 'UTC')
        OR (NEW.allocation_version, NEW.allocation_decision_id, NEW.allocation_next_eligible_at)
          IS DISTINCT FROM (OLD.allocation_version, OLD.allocation_decision_id, OLD.allocation_next_eligible_at)
        OR OLD.order_id IS NOT NULL OR coalesce(OLD.executed_volume,0)<>0
        OR OLD.admission_batch_id IS NOT NULL OR NEW.admission_batch_id IS NOT NULL
        OR OLD.allocation_version<1 OR OLD.allocation_decision_id IS NULL
        OR NEW.updated_at IS NULL OR NEW.updated_at<OLD.updated_at
        OR NOT EXISTS (SELECT 1 FROM t_assistant_executions e
          JOIN t_trade_global_configs h ON h.id=e.config_id
          WHERE e.execution_id=OLD.owner_id AND e.account_id=OLD.account_id
            AND e.status='RUNNING' AND e.entry_readiness='READY'
            AND e.entry_authorization='MANUAL_CONFIRM' AND h.enabled
            AND h.desired_environment='LIVE' AND h.active_config_version_id=e.config_version_id)
        OR EXISTS (SELECT 1 FROM pending_trade_orders p WHERE p.intent_id=OLD.id)
        OR EXISTS (SELECT 1 FROM order_correlations c WHERE c.intent_id=OLD.id)
      THEN RAISE EXCEPTION 'T_ENTRY_CONFIRMED_REALLOCATION_REQUIRED'; END IF;
    ELSIF OLD.status<>'ALLOCATION_PENDING' AND NEW.status='EXECUTION_READY' AND OLD.status<>NEW.status THEN
      RAISE EXCEPTION 'T_ENTRY_CONFIRMED_REALLOCATION_REQUIRED';
    END IF;
  END IF;
"""
_BASE = run_path(str(Path(__file__).with_name("20260909_0061_t_live_allocation.py")))[
  "_GUARD"
]
_ANCHOR = "  -- Status-only PAPER expiry/revocation"
assert _BASE.count(_ANCHOR) == 1
_GUARD = _BASE.replace(_ANCHOR, _REQUEUE + _ANCHOR, 1)
_COMPLETE = run_path(
  str(Path(__file__).with_name("20260907_0056_t_intent_terminal_controls.py"))
)["_COMPLETE"]
_OLD = "WHEN e.entry_authorization = 'MANUAL_CONFIRM' THEN 'AWAITING_APPROVAL'"
assert _COMPLETE.count(_OLD) == 1
_COMPLETE = _COMPLETE.replace(
  _OLD,
  """WHEN e.entry_authorization = 'MANUAL_CONFIRM'
          AND NOT (b.environment='LIVE' AND quantx_t_entry_confirmed(i)) THEN 'AWAITING_APPROVAL'""",
)

_FRESH = """CREATE FUNCTION quantx_t_confirmation_fresh_cut() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.environment='LIVE' AND NEW.status='COMMITTED' AND EXISTS (
    SELECT 1 FROM t_allocation_decisions d JOIN trade_intents i ON i.id=d.intent_id
      JOIN t_assistant_execution_events e ON e.execution_id=i.owner_id
        AND e.event_key='live-entry-confirmed:' || i.id
    WHERE d.allocation_batch_id=NEW.allocation_batch_id AND (
      (NEW.portfolio_snapshot->'cut'->>'as_of')::timestamptz IS NULL OR
      (NEW.portfolio_snapshot->'cut'->>'account_snapshot_as_of')::timestamptz IS NULL OR
      (NEW.portfolio_snapshot->'cut'->>'obligations_as_of')::timestamptz IS NULL OR
      LEAST((NEW.portfolio_snapshot->'cut'->>'as_of')::timestamptz,
        (NEW.portfolio_snapshot->'cut'->>'account_snapshot_as_of')::timestamptz,
        (NEW.portfolio_snapshot->'cut'->>'obligations_as_of')::timestamptz) < e.occurred_at
    )
  ) THEN RAISE EXCEPTION 'T_ENTRY_POST_CONFIRMATION_SNAPSHOT_REQUIRED'; END IF;
  RETURN NULL;
END $$;"""


def upgrade():
  op.execute(_PROOF)
  op.execute(_GUARD)
  op.execute(_COMPLETE)
  op.execute(_FRESH)
  op.execute("""CREATE CONSTRAINT TRIGGER trg_t_confirmation_fresh_cut
    AFTER INSERT OR UPDATE ON t_allocation_batches DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION quantx_t_confirmation_fresh_cut()""")


def downgrade():
  raise RuntimeError("Confirmed LIVE allocation evidence cannot be removed")
