"""Allow evidenced PAPER pending intent expiry and source revocation.

Revision ID: 20260907_0056
Revises: 20260907_0055
"""

from alembic import op

revision = "20260907_0056"
down_revision = "20260907_0055"
branch_labels = None
depends_on = None

_GUARD = """CREATE OR REPLACE FUNCTION quantx_t_intent_allocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cycle_owner text; cycle_environment text; candidate_expiry numeric;
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
  -- Status-only PAPER expiry/revocation never invents an allocation decision.
  IF TG_OP = 'UPDATE' AND OLD.allocation_cycle_id IS NOT NULL
     AND OLD.status = 'ALLOCATION_PENDING' AND NEW.status IN ('EXPIRED','CANCELLED')
     AND (NEW.allocation_version, NEW.allocation_decision_id, NEW.allocation_next_eligible_at,
          NEW.admission_batch_id, NEW.admission_rank, NEW.admission_policy_version, NEW.admission_input_fingerprint)
       IS NOT DISTINCT FROM
         (OLD.allocation_version, OLD.allocation_decision_id, OLD.allocation_next_eligible_at,
          OLD.admission_batch_id, OLD.admission_rank, OLD.admission_policy_version, OLD.admission_input_fingerprint) THEN
    IF OLD.environment IS DISTINCT FROM 'PAPER' OR OLD.owner_type IS DISTINCT FROM 'T_ASSISTANT_EXECUTION'
       OR OLD.direction IS DISTINCT FROM 'BUY' OR NOT EXISTS (
         SELECT 1 FROM t_assistant_executions e WHERE e.execution_id = OLD.owner_id
           AND e.account_id = OLD.account_id AND e.environment = 'PAPER')
       OR EXISTS (SELECT 1 FROM paper_execution_orders o WHERE o.intent_id = OLD.id) THEN
      RAISE EXCEPTION 'T_ALLOCATION_TERMINAL_SCOPE_INVALID';
    END IF;
    IF NEW.updated_at IS NULL OR NEW.updated_at < OLD.updated_at THEN
      RAISE EXCEPTION 'T_ALLOCATION_TERMINAL_TIME_INVALID';
    END IF;
    IF NEW.status = 'EXPIRED' THEN
      IF jsonb_typeof(OLD.metadata::jsonb->'source_time_ms') IS DISTINCT FROM 'number'
         OR jsonb_typeof(OLD.metadata::jsonb->'approval_ttl_ms') IS DISTINCT FROM 'number'
         OR (OLD.metadata->>'source_time_ms') !~ '^[0-9]+$'
         OR (OLD.metadata->>'approval_ttl_ms') !~ '^[0-9]+$'
         OR (OLD.metadata->>'approval_ttl_ms')::numeric <= 0
         OR jsonb_typeof(OLD.metadata::jsonb->'intent_created_at') IS DISTINCT FROM 'string'
         OR (OLD.metadata->>'intent_created_at') !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
        RAISE EXCEPTION 'T_ALLOCATION_INTENT_TTL_INVALID';
      END IF;
      SELECT (e.payload->'candidate_evidence'->'evaluation'->>'candidate_expires_at_ms')::numeric
        INTO STRICT candidate_expiry
        FROM t_assistant_decision_cycles c
        CROSS JOIN LATERAL jsonb_array_elements(c.output_manifest::jsonb->'accepted_intents') r
        JOIN t_trade_opportunity_evaluations e ON e.event_key = r->>'candidate_evidence_key'
        WHERE c.cycle_id = OLD.allocation_cycle_id AND r->>'intent_id' = OLD.id
          AND e.owner_type = OLD.owner_type AND e.owner_id = OLD.owner_id
          AND e.account_id = OLD.account_id AND e.environment = OLD.environment
          AND e.instrument_code = OLD.instrument_code
          AND e.payload->'candidate_evidence'->'evaluation'->>'candidate_id' = OLD.metadata->>'candidate_id'
          AND e.payload->'candidate_evidence'->'evaluation'->>'candidate_fingerprint' = OLD.metadata->>'candidate_fingerprint';
      IF candidate_expiry IS NULL OR candidate_expiry < 0 OR candidate_expiry <> trunc(candidate_expiry) THEN
        RAISE EXCEPTION 'T_ALLOCATION_EXPIRY_SOURCE_INVALID';
      END IF;
      IF NEW.updated_at AT TIME ZONE 'UTC' <
         LEAST(to_timestamp(candidate_expiry / 1000),
         LEAST((OLD.metadata->>'intent_created_at')::timestamptz,
           to_timestamp((OLD.metadata->>'source_time_ms')::numeric / 1000))
           + ((OLD.metadata->>'approval_ttl_ms')::numeric / 1000) * interval '1 second') THEN
        RAISE EXCEPTION 'T_ALLOCATION_INTENT_NOT_EXPIRED';
      END IF;
    ELSIF NOT EXISTS (
      SELECT 1 FROM t_assistant_executions e WHERE e.execution_id = OLD.owner_id
        AND e.account_id = OLD.account_id AND e.environment = 'PAPER'
        AND e.status IN ('DRAINING','RECONCILE_REQUIRED','STOPPED')) THEN
      RAISE EXCEPTION 'T_ALLOCATION_SOURCE_NOT_REVOKED';
    END IF;
    RETURN NEW;
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


_COMPLETE = """CREATE OR REPLACE FUNCTION quantx_t_allocation_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b t_allocation_batches; actual_count integer; max_rank integer;
BEGIN
  SELECT * INTO b FROM t_allocation_batches WHERE allocation_batch_id = NEW.allocation_batch_id;
  SELECT count(*), max(rank) INTO actual_count, max_rank
    FROM t_allocation_decisions WHERE allocation_batch_id = b.allocation_batch_id;
  IF b.status <> 'COMMITTED' THEN
    IF actual_count <> 0 THEN RAISE EXCEPTION 'T_ALLOCATION_UNCOMMITTED_DECISIONS'; END IF;
    RETURN NULL;
  END IF;
  IF actual_count <> b.intent_count OR max_rank <> b.intent_count THEN
    RAISE EXCEPTION 'T_ALLOCATION_HALF_BATCH';
  END IF;
  IF EXISTS (
    SELECT 1 FROM t_allocation_decisions d
      JOIN trade_intents i ON i.id = d.intent_id
      JOIN t_assistant_executions e ON e.execution_id = b.execution_id
      WHERE d.allocation_batch_id = b.allocation_batch_id AND (
        i.owner_type <> 'T_ASSISTANT_EXECUTION' OR i.owner_id <> b.execution_id OR
        i.environment <> b.environment OR i.direction <> 'BUY' OR
        i.allocation_cycle_id IS DISTINCT FROM b.cycle_id OR
        i.allocation_decision_id IS DISTINCT FROM d.decision_id OR
        i.allocation_version <> d.intent_version + 1 OR
        i.instrument_code <> d.instrument_code OR
        i.allocation_next_eligible_at IS DISTINCT FROM d.next_eligible_at OR
        (d.action = 'DELAY' AND i.status <> 'ALLOCATION_PENDING') OR
        (d.action IN ('ALLOW','CAP') AND i.status <> CASE
          WHEN e.entry_authorization = 'MANUAL_CONFIRM' THEN 'AWAITING_APPROVAL'
          ELSE 'EXECUTION_READY' END AND NOT (
          b.environment = 'PAPER' AND EXISTS (
            SELECT 1 FROM paper_execution_orders o
              JOIN paper_execution_accounts a ON a.execution_id=o.execution_id
              JOIN paper_execution_events receipt ON receipt.event_id=o.last_event_id
              JOIN account_risk_increase_admission_batches admission
                ON admission.admission_batch_id=o.admission_batch_id
              JOIN account_risk_increase_admission_items item
                ON item.admission_batch_id=admission.admission_batch_id AND item.intent_id=i.id
            WHERE o.execution_id=b.execution_id AND o.environment='PAPER'
              AND o.owner_type=i.owner_type AND o.owner_id=i.owner_id
              AND o.intent_id=i.id AND o.instrument_code=i.instrument_code AND o.side='BUY'
              AND o.allocation_decision_id=d.decision_id
              AND o.admission_batch_id=i.admission_batch_id
              AND a.environment='PAPER' AND a.account_id=i.account_id
              AND admission.environment='PAPER' AND admission.paper_execution_id=b.execution_id
              AND admission.account_id=i.account_id AND admission.status='COMMITTED'
              AND item.admission_rank=i.admission_rank
              AND i.admission_policy_version=admission.policy_version
              AND i.admission_input_fingerprint=admission.input_fingerprint
              AND receipt.execution_id=b.execution_id AND receipt.environment='PAPER'
              AND receipt.revision<=a.revision
              AND receipt.result_payload::jsonb->'order_ids' @> jsonb_build_array(o.order_id)
              AND i.status=CASE WHEN o.status IN ('PENDING','SUBMITTED') THEN 'ROUTED' ELSE o.status END
              AND o.filled_volume=(SELECT coalesce(sum(f.volume),0) FROM paper_execution_fills f
                WHERE f.order_id=o.order_id AND f.execution_id=b.execution_id AND f.environment='PAPER')
          )) AND NOT (
          b.environment='PAPER' AND i.status IN ('CANCELLED','REJECTED','EXPIRED')
          AND NOT EXISTS (SELECT 1 FROM paper_execution_orders o WHERE o.intent_id=i.id)
          AND EXISTS (
            SELECT 1 FROM t_assistant_execution_events audit
            WHERE audit.execution_id=b.execution_id
              AND audit.event_key LIKE 'paper-entry:' || i.id || ':%'
              AND audit.occurred_at=i.updated_at AT TIME ZONE 'UTC'
              AND audit.occurred_at>=b.committed_at
              AND audit.payload->>'intent_id'=i.id
              AND audit.payload->>'candidate_id'=d.candidate_id
              AND audit.payload->>'candidate_fingerprint'=i.metadata->>'candidate_fingerprint'
              AND audit.payload->>'instrument_code'=i.instrument_code
              AND audit.payload->>'allocation_decision_id'=d.decision_id
              AND audit.payload::jsonb ?& ARRAY['admission_batch_id','admission_rank','order_id','paper_event_id']
              AND (audit.payload->>'admission_batch_id') IS NOT DISTINCT FROM i.admission_batch_id
              AND (audit.payload::jsonb->'admission_rank') IS NOT DISTINCT FROM coalesce(to_jsonb(i.admission_rank),'null'::jsonb)
              AND audit.payload::jsonb->'order_id'='null'::jsonb
              AND audit.payload::jsonb->'paper_event_id'='null'::jsonb
              AND jsonb_typeof(audit.payload::jsonb->'reason_codes')='array'
              AND jsonb_array_length(audit.payload::jsonb->'reason_codes')>0
              AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(audit.payload::jsonb->'reason_codes') AS reasons(value)
                WHERE jsonb_typeof(reasons.value)<>'string' OR reasons.value='""'::jsonb)
              AND NOT (audit.payload::jsonb->'reason_codes' @> '["PAPER_ADMISSION_PREDECESSOR_PENDING"]'::jsonb)
              AND (
                (i.admission_batch_id IS NULL AND i.admission_rank IS NULL)
                OR EXISTS (
                  SELECT 1 FROM account_risk_increase_admission_batches admission
                    JOIN account_risk_increase_admission_items item
                      ON item.admission_batch_id=admission.admission_batch_id AND item.intent_id=i.id
                  WHERE admission.admission_batch_id=i.admission_batch_id
                    AND admission.status='COMMITTED' AND admission.environment='PAPER'
                    AND admission.paper_execution_id=b.execution_id AND admission.account_id=i.account_id
                    AND item.admission_rank=i.admission_rank
                    AND admission.policy_version=i.admission_policy_version
                    AND admission.input_fingerprint=i.admission_input_fingerprint
                    AND audit.occurred_at>=admission.committed_at AT TIME ZONE 'UTC'
                )
              )
              AND (
                (i.status='CANCELLED' AND audit.event_type='PAPER_ENTRY_REBUILD_REQUIRED'
                  AND audit.payload->>'outcome'='DELAY'
                  AND audit.payload->>'follow_up'='REBUILD_CANDIDATE')
                OR (i.status IN ('REJECTED','EXPIRED') AND audit.event_type='PAPER_ENTRY_REVIEWED'
                  AND audit.payload->>'outcome'='REJECT'
                  AND audit.payload->>'follow_up'='TERMINALIZE_INTENT'
                  AND (i.status='EXPIRED')=EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(audit.payload::jsonb->'reason_codes') AS reasons(value)
                    WHERE reasons.value LIKE '%EXPIRED%'))
              )
          ))) OR
        (d.action = 'REJECT' AND i.status NOT IN ('REJECTED','EXPIRED')) OR
        NOT EXISTS (SELECT 1 FROM jsonb_array_elements(b.intent_manifest::jsonb) m
          WHERE m->>'intent_id' = i.id AND (m->>'intent_version')::integer = d.intent_version
          AND m->>'candidate_id' = d.candidate_id))) THEN
    RAISE EXCEPTION 'T_ALLOCATION_INTENT_BINDING_CONFLICT';
  END IF;
  RETURN NULL;
END $$;"""


def upgrade():
  op.execute(_GUARD)
  op.execute(_COMPLETE)


def downgrade():
  raise RuntimeError("Audited PAPER terminal controls cannot be removed")
