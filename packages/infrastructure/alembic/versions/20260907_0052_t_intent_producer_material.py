"""Freeze T producer material independently of later execution annotations.

Revision ID: 20260907_0052
Revises: 20260907_0051
"""

from alembic import op

revision = "20260907_0052"
down_revision = "20260907_0051"
branch_labels = None
depends_on = None


_GUARD = """CREATE OR REPLACE FUNCTION quantx_t_intent_allocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cycle_owner text; cycle_environment text;
BEGIN
  IF TG_OP = 'UPDATE' AND
     OLD.allocation_cycle_id IS DISTINCT FROM NEW.allocation_cycle_id THEN
    RAISE EXCEPTION 'T_ALLOCATION_INTENT_CYCLE_IMMUTABLE';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.allocation_cycle_id IS NOT NULL AND (
    (NEW.id, NEW.account_id, NEW.strategy_id, NEW.idempotency_key, NEW.instrument_code, NEW.direction, NEW.bucket, NEW.reason, NEW.priority,
     NEW.intent_type, NEW.target_amount, NEW.target_position_pct, NEW.target_volume,
     NEW.limit_price_hint, NEW.confidence, NEW.trace_id)
    IS DISTINCT FROM
    (OLD.id, OLD.account_id, OLD.strategy_id, OLD.idempotency_key, OLD.instrument_code, OLD.direction, OLD.bucket, OLD.reason, OLD.priority,
     OLD.intent_type, OLD.target_amount, OLD.target_position_pct, OLD.target_volume,
     OLD.limit_price_hint, OLD.confidence, OLD.trace_id) OR
    jsonb_build_array(NEW.metadata->'source_execution_ref',
      NEW.metadata->'instrument_code',
      NEW.metadata->'candidate_id',
      NEW.metadata->'candidate_fingerprint',
      NEW.metadata->'policy_version',
      NEW.metadata->'feature_schema_version',
      NEW.metadata->'source_time_ms',
      NEW.metadata->'tick_ordinal',
      NEW.metadata->'opportunity_score',
      NEW.metadata->'requested_entry_amount',
      NEW.metadata->'t_trade_role',
      NEW.metadata->'t_batch_id',
      NEW.metadata->'exit_plan_id',
      NEW.metadata->'exit_plan_template',
      NEW.metadata->'origin_type',
      NEW.metadata->'plan_id',
      NEW.metadata->'execution_mode',
      NEW.metadata->'approval_ttl_ms',
      NEW.metadata->'expiry_policy',
      NEW.metadata->'max_price_deviation_bps',
      NEW.metadata->'intent_created_at')
    IS DISTINCT FROM
    jsonb_build_array(OLD.metadata->'source_execution_ref',
      OLD.metadata->'instrument_code',
      OLD.metadata->'candidate_id',
      OLD.metadata->'candidate_fingerprint',
      OLD.metadata->'policy_version',
      OLD.metadata->'feature_schema_version',
      OLD.metadata->'source_time_ms',
      OLD.metadata->'tick_ordinal',
      OLD.metadata->'opportunity_score',
      OLD.metadata->'requested_entry_amount',
      OLD.metadata->'t_trade_role',
      OLD.metadata->'t_batch_id',
      OLD.metadata->'exit_plan_id',
      OLD.metadata->'exit_plan_template',
      OLD.metadata->'origin_type',
      OLD.metadata->'plan_id',
      OLD.metadata->'execution_mode',
      OLD.metadata->'approval_ttl_ms',
      OLD.metadata->'expiry_policy',
      OLD.metadata->'max_price_deviation_bps',
      OLD.metadata->'intent_created_at')) THEN
    RAISE EXCEPTION 'T_ALLOCATION_INTENT_MATERIAL_IMMUTABLE';
  END IF;
  IF NEW.allocation_cycle_id IS NOT NULL THEN
    IF TG_OP = 'INSERT' AND (NEW.allocation_version <> 0 OR
       NEW.allocation_decision_id IS NOT NULL OR NEW.allocation_next_eligible_at IS NOT NULL OR
       NEW.status <> 'ALLOCATION_PENDING') THEN
      RAISE EXCEPTION 'T_ALLOCATION_INITIAL_INTENT_INVALID';
    END IF;
    SELECT c.execution_id, e.environment INTO cycle_owner, cycle_environment
      FROM t_assistant_decision_cycles c JOIN t_assistant_executions e
        ON e.execution_id = c.execution_id WHERE c.cycle_id = NEW.allocation_cycle_id;
    IF cycle_owner IS DISTINCT FROM NEW.owner_id OR cycle_environment IS DISTINCT FROM NEW.environment THEN
      RAISE EXCEPTION 'T_ALLOCATION_INTENT_OWNER_CONFLICT';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' AND (
     (NEW.allocation_version, NEW.allocation_decision_id, NEW.allocation_next_eligible_at)
     IS DISTINCT FROM (OLD.allocation_version, OLD.allocation_decision_id, OLD.allocation_next_eligible_at)
     OR (OLD.status = 'ALLOCATION_PENDING' AND NEW.status <> OLD.status)) THEN
    IF OLD.status <> 'ALLOCATION_PENDING' OR NEW.allocation_version <> OLD.allocation_version + 1 OR
       NEW.allocation_decision_id IS NULL OR NOT EXISTS (
        SELECT 1 FROM t_allocation_decisions d JOIN t_allocation_batches b
          ON b.allocation_batch_id = d.allocation_batch_id
        WHERE d.decision_id = NEW.allocation_decision_id AND d.intent_id = NEW.id
          AND d.intent_version = OLD.allocation_version AND b.cycle_id = NEW.allocation_cycle_id
          AND b.execution_id = NEW.owner_id AND b.environment = NEW.environment) THEN
      RAISE EXCEPTION 'T_ALLOCATION_INTENT_VERSION_CONFLICT';
    END IF;
  END IF;
  RETURN NEW;
END $$;"""


def upgrade():
  op.execute(_GUARD)


def downgrade():
  raise RuntimeError("T intent producer identity cannot be unfrozen")
