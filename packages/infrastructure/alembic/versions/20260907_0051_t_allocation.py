"""P4 durable allocation attempts and standard intent cycle linkage.

Revision ID: 20260907_0051
Revises: 20260906_0050
"""

from alembic import op

revision = "20260907_0051"
down_revision = "20260906_0050"
branch_labels = None
depends_on = None


_TABLE_DDL = (
  """CREATE TABLE t_allocation_batches (
  allocation_batch_id VARCHAR(36) NOT NULL,
  execution_id VARCHAR(36) NOT NULL,
  cycle_id VARCHAR(36) NOT NULL,
  environment VARCHAR(16) NOT NULL,
  allocation_attempt INTEGER NOT NULL,
  portfolio_input_fingerprint VARCHAR(64) NOT NULL,
  portfolio_snapshot JSON NOT NULL,
  intent_manifest_hash VARCHAR(64) NOT NULL,
  intent_manifest JSON NOT NULL,
  intent_count INTEGER NOT NULL,
  decision_manifest_hash VARCHAR(64),
  status VARCHAR(16) NOT NULL,
  processing_owner VARCHAR(128),
  processing_fence_token VARCHAR(36),
  processing_lease_until TIMESTAMP WITH TIME ZONE,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
  committed_at TIMESTAMP WITH TIME ZONE,
  terminal_reason VARCHAR(128),
  PRIMARY KEY (allocation_batch_id),
  CONSTRAINT uq_t_allocation_cycle_attempt UNIQUE (execution_id, cycle_id, allocation_attempt),
  CONSTRAINT ck_t_allocation_counts CHECK (allocation_attempt >= 1 AND intent_count >= 1),
  CONSTRAINT ck_t_allocation_paper CHECK (environment = 'PAPER'),
  CONSTRAINT ck_t_allocation_status CHECK (status IN ('PREPARED','COMMITTED','SUPERSEDED','EXPIRED','FAILED')),
  CONSTRAINT ck_t_allocation_ttl CHECK (expires_at > created_at),
  CONSTRAINT ck_t_allocation_input_hashes CHECK (length(portfolio_input_fingerprint) = 64 AND length(intent_manifest_hash) = 64),
  CONSTRAINT ck_t_allocation_claim CHECK ((processing_owner IS NULL AND processing_fence_token IS NULL AND processing_lease_until IS NULL) OR (status = 'PREPARED' AND processing_owner IS NOT NULL AND length(trim(processing_owner)) > 0 AND processing_fence_token IS NOT NULL AND length(trim(processing_fence_token)) > 0 AND processing_lease_until IS NOT NULL AND processing_lease_until <= expires_at)),
  CONSTRAINT ck_t_allocation_terminal CHECK ((status = 'PREPARED' AND committed_at IS NULL AND decision_manifest_hash IS NULL AND terminal_reason IS NULL) OR (status = 'COMMITTED' AND committed_at IS NOT NULL AND decision_manifest_hash IS NOT NULL AND length(decision_manifest_hash) = 64 AND terminal_reason IS NULL AND processing_owner IS NULL) OR (status IN ('SUPERSEDED','EXPIRED','FAILED') AND committed_at IS NOT NULL AND decision_manifest_hash IS NULL AND terminal_reason IS NOT NULL AND length(trim(terminal_reason)) > 0 AND processing_owner IS NULL)),
  FOREIGN KEY(execution_id) REFERENCES t_assistant_executions (execution_id) ON DELETE RESTRICT,
  FOREIGN KEY(cycle_id) REFERENCES t_assistant_decision_cycles (cycle_id) ON DELETE RESTRICT
)""",
  """CREATE INDEX ix_t_allocation_recovery ON t_allocation_batches (status, processing_lease_until, expires_at)""",
  """CREATE UNIQUE INDEX uq_t_allocation_prepared_cycle ON t_allocation_batches (execution_id, cycle_id) WHERE status = 'PREPARED'""",
  """CREATE TABLE t_allocation_decisions (
  decision_id VARCHAR(80) NOT NULL,
  allocation_batch_id VARCHAR(36) NOT NULL,
  intent_id VARCHAR(36) NOT NULL,
  intent_version INTEGER NOT NULL,
  candidate_id VARCHAR(128) NOT NULL,
  instrument_code VARCHAR(20) NOT NULL,
  rank INTEGER NOT NULL,
  action VARCHAR(8) NOT NULL,
  requested_amount_ceiling NUMERIC(24, 8) NOT NULL,
  allocated_amount_cap NUMERIC(24, 8) NOT NULL,
  evidence JSON NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL,
  expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
  next_eligible_at TIMESTAMP WITH TIME ZONE,
  PRIMARY KEY (decision_id),
  CONSTRAINT uq_t_allocation_decision_intent UNIQUE (allocation_batch_id, intent_id),
  CONSTRAINT uq_t_allocation_decision_rank UNIQUE (allocation_batch_id, rank),
  CONSTRAINT ck_t_allocation_decision_rank CHECK (rank >= 1 AND intent_version >= 0),
  CONSTRAINT ck_t_allocation_decision_action CHECK (action IN ('ALLOW','CAP','DELAY','REJECT')),
  CONSTRAINT ck_t_allocation_decision_amount CHECK (requested_amount_ceiling <> 'NaN' AND allocated_amount_cap <> 'NaN' AND requested_amount_ceiling > 0 AND allocated_amount_cap >= 0 AND allocated_amount_cap <= requested_amount_ceiling AND ((action = 'ALLOW' AND allocated_amount_cap = requested_amount_ceiling) OR (action = 'CAP' AND allocated_amount_cap > 0 AND allocated_amount_cap < requested_amount_ceiling) OR (action IN ('DELAY','REJECT') AND allocated_amount_cap = 0))),
  CONSTRAINT ck_t_allocation_decision_delay CHECK ((action = 'DELAY' AND next_eligible_at IS NOT NULL AND next_eligible_at > created_at AND next_eligible_at < expires_at) OR (action <> 'DELAY' AND next_eligible_at IS NULL)),
  FOREIGN KEY(allocation_batch_id) REFERENCES t_allocation_batches (allocation_batch_id) ON DELETE RESTRICT,
  FOREIGN KEY(intent_id) REFERENCES trade_intents (id) ON DELETE RESTRICT
)""",
)


def upgrade():
  for statement in _TABLE_DDL:
    op.execute(statement)
  op.execute(
    "ALTER TABLE trade_intents ADD COLUMN allocation_cycle_id VARCHAR(36) REFERENCES t_assistant_decision_cycles(cycle_id) ON DELETE RESTRICT"
  )
  op.execute(
    "ALTER TABLE trade_intents ADD COLUMN allocation_version INTEGER NOT NULL DEFAULT 0"
  )
  op.execute(
    "ALTER TABLE trade_intents ADD COLUMN allocation_decision_id VARCHAR(80) REFERENCES t_allocation_decisions(decision_id) ON DELETE RESTRICT"
  )
  op.execute(
    "ALTER TABLE trade_intents ADD COLUMN allocation_next_eligible_at TIMESTAMPTZ"
  )
  op.execute(
    "ALTER TABLE trade_intents ADD CONSTRAINT ck_trade_intent_allocation_version CHECK (allocation_version >= 0)"
  )
  op.execute(
    "ALTER TABLE trade_intents ADD CONSTRAINT ck_trade_intent_allocation_scope CHECK ((allocation_cycle_id IS NULL AND allocation_decision_id IS NULL AND allocation_next_eligible_at IS NULL AND allocation_version = 0) OR (allocation_cycle_id IS NOT NULL AND owner_type = 'T_ASSISTANT_EXECUTION' AND environment = 'PAPER' AND direction = 'BUY'))"
  )
  op.execute(
    "ALTER TABLE trade_intents ADD CONSTRAINT ck_trade_intent_allocation_pending CHECK (status <> 'ALLOCATION_PENDING' OR allocation_cycle_id IS NOT NULL)"
  )
  op.execute("COMMENT ON TABLE t_allocation_batches IS '做 T 组合分配尝试与恢复租约'")
  op.execute(
    "COMMENT ON TABLE t_allocation_decisions IS '做 T 组合分配不可变逐意图决策'"
  )
  for statement in _GUARDS:
    op.execute(statement)


_GUARDS = (
  """CREATE FUNCTION quantx_t_allocation_batch_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cycle_owner text; cycle_status text; owner_environment text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'T_ALLOCATION_HISTORY_IMMUTABLE';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.status <> 'PREPARED' OR
       (to_jsonb(NEW) - ARRAY['status','processing_owner','processing_fence_token',
        'processing_lease_until','committed_at','terminal_reason','decision_manifest_hash'])
       IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['status','processing_owner','processing_fence_token',
        'processing_lease_until','committed_at','terminal_reason','decision_manifest_hash']) THEN
      RAISE EXCEPTION 'T_ALLOCATION_HISTORY_IMMUTABLE';
    END IF;
  END IF;
  SELECT c.execution_id, c.status, e.environment
    INTO cycle_owner, cycle_status, owner_environment
    FROM t_assistant_decision_cycles c JOIN t_assistant_executions e
      ON e.execution_id = c.execution_id WHERE c.cycle_id = NEW.cycle_id;
  IF cycle_owner IS DISTINCT FROM NEW.execution_id OR
     cycle_status IS DISTINCT FROM 'PROPOSALS_COMMITTED' OR
     owner_environment IS DISTINCT FROM NEW.environment THEN
    RAISE EXCEPTION 'T_ALLOCATION_CYCLE_OWNER_CONFLICT';
  END IF;
  IF jsonb_typeof(NEW.intent_manifest::jsonb) IS DISTINCT FROM 'array' OR
     jsonb_array_length(NEW.intent_manifest::jsonb) <> NEW.intent_count THEN
    RAISE EXCEPTION 'T_ALLOCATION_MANIFEST_SHAPE';
  END IF;
  RETURN NEW;
END $$;""",
  """CREATE TRIGGER trg_t_allocation_batch_guard BEFORE INSERT OR UPDATE OR DELETE
  ON t_allocation_batches FOR EACH ROW EXECUTE FUNCTION quantx_t_allocation_batch_guard();""",
  """CREATE FUNCTION quantx_t_allocation_decision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'T_ALLOCATION_DECISION_IMMUTABLE'; END $$;""",
  """CREATE TRIGGER trg_t_allocation_decision_immutable BEFORE UPDATE OR DELETE
  ON t_allocation_decisions FOR EACH ROW EXECUTE FUNCTION quantx_t_allocation_decision_immutable();""",
  """CREATE FUNCTION quantx_t_allocation_complete() RETURNS trigger LANGUAGE plpgsql AS $$
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
          ELSE 'EXECUTION_READY' END) OR
        (d.action = 'REJECT' AND i.status NOT IN ('REJECTED','EXPIRED')) OR
        NOT EXISTS (SELECT 1 FROM jsonb_array_elements(b.intent_manifest::jsonb) m
          WHERE m->>'intent_id' = i.id AND (m->>'intent_version')::integer = d.intent_version
          AND m->>'candidate_id' = d.candidate_id))) THEN
    RAISE EXCEPTION 'T_ALLOCATION_INTENT_BINDING_CONFLICT';
  END IF;
  RETURN NULL;
END $$;""",
  """CREATE CONSTRAINT TRIGGER trg_t_allocation_batch_complete AFTER INSERT OR UPDATE
  ON t_allocation_batches DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION quantx_t_allocation_complete();""",
  """CREATE CONSTRAINT TRIGGER trg_t_allocation_decision_complete AFTER INSERT
  ON t_allocation_decisions DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION quantx_t_allocation_complete();""",
  """CREATE FUNCTION quantx_t_intent_allocation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cycle_owner text; cycle_environment text;
BEGIN
  IF TG_OP = 'UPDATE' AND
     OLD.allocation_cycle_id IS DISTINCT FROM NEW.allocation_cycle_id THEN
    RAISE EXCEPTION 'T_ALLOCATION_INTENT_CYCLE_IMMUTABLE';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.allocation_cycle_id IS NOT NULL AND (
    (NEW.instrument_code, NEW.direction, NEW.bucket, NEW.reason, NEW.priority,
     NEW.intent_type, NEW.target_amount, NEW.target_position_pct, NEW.target_volume,
     NEW.limit_price_hint, NEW.confidence, NEW.trace_id)
    IS DISTINCT FROM
    (OLD.instrument_code, OLD.direction, OLD.bucket, OLD.reason, OLD.priority,
     OLD.intent_type, OLD.target_amount, OLD.target_position_pct, OLD.target_volume,
     OLD.limit_price_hint, OLD.confidence, OLD.trace_id) OR
    jsonb_build_array(NEW.metadata->'candidate_id', NEW.metadata->'candidate_fingerprint',
      NEW.metadata->'policy_version', NEW.metadata->'feature_schema_version',
      NEW.metadata->'intent_created_at', NEW.metadata->'expiry_policy',
      NEW.metadata->'approval_ttl_ms', NEW.metadata->'t_trade_role')
    IS DISTINCT FROM
    jsonb_build_array(OLD.metadata->'candidate_id', OLD.metadata->'candidate_fingerprint',
      OLD.metadata->'policy_version', OLD.metadata->'feature_schema_version',
      OLD.metadata->'intent_created_at', OLD.metadata->'expiry_policy',
      OLD.metadata->'approval_ttl_ms', OLD.metadata->'t_trade_role')) THEN
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
END $$;""",
  """CREATE TRIGGER trg_t_intent_allocation_guard BEFORE INSERT OR UPDATE ON trade_intents
  FOR EACH ROW EXECUTE FUNCTION quantx_t_intent_allocation_guard();""",
)


def downgrade():
  raise RuntimeError("P4 allocation evidence cannot be destructively downgraded")
