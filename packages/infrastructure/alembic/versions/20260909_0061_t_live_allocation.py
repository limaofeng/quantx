"""Enable environment-bound LIVE allocation and audited source drain.

Revision ID: 20260909_0061
Revises: 20260909_0060

No LIVE producer is activated and no historical intent is rewritten. Preserve
0056's immutable-material and PAPER guards verbatim, adding one LIVE drain case.
"""

from pathlib import Path
from runpy import run_path

from alembic import op

revision = "20260909_0061"
down_revision = "20260909_0060"
branch_labels = None
depends_on = None

INTENT_ALLOCATION_SCOPE = (
  "(allocation_cycle_id IS NULL AND allocation_decision_id IS NULL "
  "AND allocation_next_eligible_at IS NULL AND allocation_version = 0) OR "
  "(allocation_cycle_id IS NOT NULL AND owner_type = 'T_ASSISTANT_EXECUTION' "
  "AND environment IN ('PAPER','LIVE') AND direction = 'BUY')"
)

_LIVE_DRAIN = """  IF TG_OP = 'UPDATE' AND OLD.environment = 'LIVE'
     AND OLD.allocation_cycle_id IS NOT NULL
     AND OLD.status IN ('ALLOCATION_PENDING','AWAITING_APPROVAL','EXECUTION_READY','APPROVED','PENDING')
     AND NEW.status = 'CANCELLED' THEN
    IF (NEW.allocation_version, NEW.allocation_decision_id, NEW.allocation_next_eligible_at,
        NEW.admission_batch_id, NEW.admission_rank, NEW.admission_policy_version, NEW.admission_input_fingerprint)
       IS DISTINCT FROM
       (OLD.allocation_version, OLD.allocation_decision_id, OLD.allocation_next_eligible_at,
        OLD.admission_batch_id, OLD.admission_rank, OLD.admission_policy_version, OLD.admission_input_fingerprint)
       OR OLD.order_id IS NOT NULL OR coalesce(OLD.executed_volume,0) <> 0
       OR NEW.updated_at IS NULL OR NEW.updated_at < OLD.updated_at
       OR NOT EXISTS (SELECT 1 FROM t_assistant_executions e WHERE e.execution_id=OLD.owner_id
         AND e.account_id=OLD.account_id AND e.environment='LIVE'
         AND e.status IN ('DRAINING','RECONCILE_REQUIRED'))
       OR EXISTS (SELECT 1 FROM pending_trade_orders p WHERE p.intent_id=OLD.id)
       OR EXISTS (SELECT 1 FROM order_correlations c WHERE c.intent_id=OLD.id)
       OR EXISTS (SELECT 1 FROM trade_command_outbox o WHERE o.owner_type=OLD.owner_type
         AND o.owner_id=OLD.owner_id AND NOT EXISTS
           (SELECT 1 FROM pending_trade_orders p WHERE p.client_order_id=o.client_order_id)
         AND NOT EXISTS (SELECT 1 FROM order_correlations c WHERE c.client_order_id=o.client_order_id)) THEN
      RAISE EXCEPTION 'T_ALLOCATION_LIVE_DRAIN_SCOPE_INVALID';
    END IF;
    RETURN NEW;
  END IF;
"""

_BASE_GUARD = run_path(
  str(Path(__file__).with_name("20260907_0056_t_intent_terminal_controls.py"))
)["_GUARD"]
_ANCHOR = "  -- Status-only PAPER expiry/revocation"
assert _BASE_GUARD.count(_ANCHOR) == 1
_GUARD = _BASE_GUARD.replace(_ANCHOR, _LIVE_DRAIN + _ANCHOR, 1)

_DRAIN_AUDIT = """CREATE FUNCTION quantx_t_live_drain_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.environment='LIVE' AND OLD.allocation_cycle_id IS NOT NULL
    AND OLD.status IN ('ALLOCATION_PENDING','AWAITING_APPROVAL','EXECUTION_READY','APPROVED','PENDING')
    AND NEW.status='CANCELLED' AND NOT EXISTS (
      SELECT 1 FROM t_assistant_execution_events a WHERE a.execution_id=NEW.owner_id
      AND a.event_key='live-drain-intent:' || NEW.id AND a.event_type='LIVE_ENTRY_DRAINED'
      AND a.occurred_at=NEW.updated_at AT TIME ZONE 'UTC'
      AND a.payload->>'intent_id'=NEW.id AND a.payload->>'outcome'='CANCELLED_UNSUBMITTED'
      AND length(trim(a.payload->>'reason'))>0
    ) THEN
    RAISE EXCEPTION 'T_ALLOCATION_LIVE_DRAIN_AUDIT_REQUIRED';
  END IF;
  RETURN NULL;
END $$;"""


def upgrade():
  op.drop_constraint("ck_t_allocation_paper", "t_allocation_batches", type_="check")
  op.create_check_constraint(
    "ck_t_allocation_environment",
    "t_allocation_batches",
    "environment IN ('PAPER','LIVE')",
  )
  op.drop_constraint("ck_trade_intent_allocation_scope", "trade_intents", type_="check")
  op.create_check_constraint(
    "ck_trade_intent_allocation_scope", "trade_intents", INTENT_ALLOCATION_SCOPE
  )
  op.execute(_GUARD)
  op.execute(_DRAIN_AUDIT)
  op.execute("""CREATE CONSTRAINT TRIGGER trg_t_live_drain_audit AFTER UPDATE ON trade_intents
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION quantx_t_live_drain_audit()""")


def downgrade():
  raise RuntimeError("LIVE allocation evidence cannot be removed")
