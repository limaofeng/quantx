"""P4 isolated PAPER facts and environment-scoped public admission.

Revision ID: 20260907_0053
Revises: 20260907_0052
"""

from alembic import op

revision = "20260907_0053"
down_revision = "20260907_0052"
branch_labels = None
depends_on = None

_TABLE_DDL = (
  """CREATE TABLE paper_execution_accounts (
  execution_id VARCHAR(36) NOT NULL,
  account_id VARCHAR(50) NOT NULL,
  environment VARCHAR(16) NOT NULL,
  seed_snapshot_id VARCHAR(128) NOT NULL,
  seed_snapshot_hash VARCHAR(64) NOT NULL,
  seed_as_of TIMESTAMP WITH TIME ZONE NOT NULL,
  seed_payload JSON NOT NULL,
  matching_policy_version VARCHAR(64) NOT NULL,
  broker_checkpoint JSON NOT NULL,
  bucket_checkpoint JSON NOT NULL,
  revision INTEGER NOT NULL,
  snapshot_hash VARCHAR(64) NOT NULL,
  initial_snapshot_hash VARCHAR(64) NOT NULL,
  snapshot_as_of TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (execution_id),
  CONSTRAINT ck_paper_account_environment CHECK (environment = 'PAPER'),
  CONSTRAINT ck_paper_account_revision CHECK (revision >= 0),
  CONSTRAINT ck_paper_account_hashes CHECK (length(seed_snapshot_hash) = 64 AND length(snapshot_hash) = 64 AND length(initial_snapshot_hash) = 64),
  CONSTRAINT ck_paper_account_causality CHECK (snapshot_as_of >= seed_as_of),
  FOREIGN KEY(execution_id) REFERENCES t_assistant_executions (execution_id) ON DELETE RESTRICT
)""",
  """CREATE TABLE paper_execution_events (
  event_id VARCHAR(80) NOT NULL,
  execution_id VARCHAR(36) NOT NULL,
  environment VARCHAR(16) NOT NULL,
  event_key VARCHAR(256) NOT NULL,
  event_type VARCHAR(16) NOT NULL,
  revision INTEGER NOT NULL,
  input_hash VARCHAR(64) NOT NULL,
  input_payload JSON NOT NULL,
  result_payload JSON NOT NULL,
  resulting_snapshot_hash VARCHAR(64) NOT NULL,
  previous_snapshot_hash VARCHAR(64) NOT NULL,
  occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (event_id),
  CONSTRAINT uq_paper_event_revision UNIQUE (execution_id, revision),
  CONSTRAINT uq_paper_event_key UNIQUE (execution_id, event_key),
  CONSTRAINT ck_paper_event_environment CHECK (environment = 'PAPER'),
  CONSTRAINT ck_paper_event_revision CHECK (revision >= 1),
  CONSTRAINT ck_paper_event_type CHECK (event_type IN ('ORDER','QUOTE','CANCEL')),
  CONSTRAINT ck_paper_event_hashes CHECK (length(input_hash) = 64 AND length(resulting_snapshot_hash) = 64 AND length(previous_snapshot_hash) = 64),
  FOREIGN KEY(execution_id) REFERENCES paper_execution_accounts (execution_id) ON DELETE RESTRICT
)""",
  """CREATE TABLE paper_execution_orders (
  order_id VARCHAR(80) NOT NULL,
  execution_id VARCHAR(36) NOT NULL,
  environment VARCHAR(16) NOT NULL,
  owner_type VARCHAR(32) NOT NULL,
  owner_id VARCHAR(128) NOT NULL,
  intent_id VARCHAR(36) NOT NULL,
  allocation_decision_id VARCHAR(80),
  admission_batch_id VARCHAR(36),
  instrument_code VARCHAR(20) NOT NULL,
  order_attempt INTEGER NOT NULL,
  side VARCHAR(4) NOT NULL,
  volume INTEGER NOT NULL,
  limit_price NUMERIC(24, 8) NOT NULL,
  filled_volume INTEGER NOT NULL,
  status VARCHAR(24) NOT NULL,
  request_payload JSON NOT NULL,
  response_payload JSON NOT NULL,
  sizing_evidence JSON NOT NULL,
  risk_evidence JSON NOT NULL,
  last_event_id VARCHAR(80) NOT NULL,
  submitted_at TIMESTAMP WITH TIME ZONE NOT NULL,
  expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
  PRIMARY KEY (order_id),
  CONSTRAINT uq_paper_order_intent_attempt UNIQUE (execution_id, intent_id, order_attempt),
  CONSTRAINT ck_paper_order_environment CHECK (environment = 'PAPER'),
  CONSTRAINT ck_paper_order_owner_type CHECK (owner_type IN ('T_ASSISTANT_EXECUTION','EXIT_PLAN')),
  CONSTRAINT ck_paper_order_side CHECK (side IN ('BUY','SELL')),
  CONSTRAINT ck_paper_order_status CHECK (status IN ('PENDING','SUBMITTED','PARTIAL_FILLED','FILLED','CANCELLED','REJECTED','EXPIRED')),
  CONSTRAINT ck_paper_order_volume CHECK (order_attempt >= 0 AND volume > 0 AND filled_volume >= 0 AND filled_volume <= volume),
  CONSTRAINT ck_paper_order_filled_status CHECK ((status = 'FILLED' AND filled_volume = volume) OR (status = 'PARTIAL_FILLED' AND filled_volume > 0 AND filled_volume < volume) OR (status IN ('PENDING','SUBMITTED','REJECTED') AND filled_volume = 0) OR status IN ('CANCELLED','EXPIRED')),
  CONSTRAINT ck_paper_order_price CHECK (limit_price > 0 AND limit_price <> 'NaN'),
  CONSTRAINT ck_paper_order_ttl CHECK (expires_at > submitted_at),
  FOREIGN KEY(execution_id) REFERENCES paper_execution_accounts (execution_id) ON DELETE RESTRICT,
  FOREIGN KEY(intent_id) REFERENCES trade_intents (id) ON DELETE RESTRICT,
  FOREIGN KEY(allocation_decision_id) REFERENCES t_allocation_decisions (decision_id) ON DELETE RESTRICT,
  FOREIGN KEY(admission_batch_id) REFERENCES account_risk_increase_admission_batches (admission_batch_id) ON DELETE RESTRICT,
  FOREIGN KEY(last_event_id) REFERENCES paper_execution_events (event_id) ON DELETE RESTRICT
)""",
  """CREATE INDEX ix_paper_order_active ON paper_execution_orders (execution_id, status, instrument_code)""",
  """CREATE TABLE paper_execution_fills (
  fill_id VARCHAR(80) NOT NULL,
  execution_id VARCHAR(36) NOT NULL,
  environment VARCHAR(16) NOT NULL,
  order_id VARCHAR(80) NOT NULL,
  event_id VARCHAR(80) NOT NULL,
  volume INTEGER NOT NULL,
  price NUMERIC(24, 8) NOT NULL,
  fee NUMERIC(24, 8) NOT NULL,
  occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
  trade_payload JSON NOT NULL,
  PRIMARY KEY (fill_id),
  CONSTRAINT ck_paper_fill_environment CHECK (environment = 'PAPER'),
  CONSTRAINT ck_paper_fill_volume CHECK (volume > 0),
  CONSTRAINT ck_paper_fill_amounts CHECK (price > 0 AND price <> 'NaN' AND fee >= 0 AND fee <> 'NaN'),
  FOREIGN KEY(execution_id) REFERENCES paper_execution_accounts (execution_id) ON DELETE RESTRICT,
  FOREIGN KEY(order_id) REFERENCES paper_execution_orders (order_id) ON DELETE RESTRICT,
  FOREIGN KEY(event_id) REFERENCES paper_execution_events (event_id) ON DELETE RESTRICT
)""",
  """CREATE INDEX ix_paper_fill_order ON paper_execution_fills (order_id, occurred_at)""",
)


def upgrade():
  for statement in _TABLE_DDL:
    op.execute(statement)
  for table, comment in (
    ("paper_execution_accounts", "PAPER 独立执行账户与冻结种子证据"),
    ("paper_execution_events", "PAPER 撮合输入与账户版本审计"),
    ("paper_execution_orders", "PAPER 隔离委托事实"),
    ("paper_execution_fills", "PAPER 隔离成交事实"),
  ):
    op.execute(f"COMMENT ON TABLE {table} IS '{comment}'")
  op.execute(
    "ALTER TABLE account_risk_increase_admission_batches ADD COLUMN paper_execution_id VARCHAR(36) REFERENCES paper_execution_accounts(execution_id) ON DELETE RESTRICT"
  )
  op.execute(
    "ALTER TABLE account_risk_increase_admission_batches DROP CONSTRAINT ck_risk_admission_batch_live"
  )
  op.execute(
    "ALTER TABLE account_risk_increase_admission_batches ADD CONSTRAINT ck_risk_admission_batch_scope CHECK ((environment = 'LIVE' AND paper_execution_id IS NULL) OR (environment = 'PAPER' AND paper_execution_id IS NOT NULL))"
  )
  op.execute(
    "ALTER TABLE account_risk_increase_admission_batches DROP CONSTRAINT uq_risk_admission_batch_input_attempt"
  )
  op.execute(
    "ALTER TABLE account_risk_increase_admission_batches DROP CONSTRAINT uq_risk_admission_batch_attempt"
  )
  op.execute(
    "CREATE UNIQUE INDEX uq_risk_admission_batch_attempt ON account_risk_increase_admission_batches(account_id,environment,attempt) WHERE environment='LIVE'"
  )
  op.execute(
    "CREATE UNIQUE INDEX uq_risk_admission_paper_attempt ON account_risk_increase_admission_batches(paper_execution_id,attempt) WHERE environment='PAPER'"
  )
  for statement in _GUARDS:
    op.execute(statement)


def downgrade():
  raise RuntimeError("PAPER execution facts cannot be destructively downgraded")


_GUARDS = (
  """CREATE FUNCTION quantx_paper_account_guard() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE e t_assistant_executions;
  BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'PAPER_ACCOUNT_HISTORY_IMMUTABLE'; END IF;
    SELECT * INTO e FROM t_assistant_executions WHERE execution_id=NEW.execution_id;
    IF NOT FOUND OR e.environment <> 'PAPER' OR e.account_id <> NEW.account_id THEN
      RAISE EXCEPTION 'PAPER_ACCOUNT_EXECUTION_SCOPE_CONFLICT';
    END IF;
    IF TG_OP = 'INSERT' THEN
      IF NEW.revision <> 0 OR NEW.snapshot_hash <> NEW.initial_snapshot_hash THEN
        RAISE EXCEPTION 'PAPER_ACCOUNT_INITIAL_STATE_INVALID';
      END IF;
    ELSE
      IF (NEW.execution_id,NEW.account_id,NEW.environment,NEW.seed_snapshot_id,
          NEW.seed_snapshot_hash,NEW.seed_as_of,NEW.seed_payload::jsonb,
          NEW.matching_policy_version,NEW.initial_snapshot_hash)
        IS DISTINCT FROM (OLD.execution_id,OLD.account_id,OLD.environment,OLD.seed_snapshot_id,
          OLD.seed_snapshot_hash,OLD.seed_as_of,OLD.seed_payload::jsonb,
          OLD.matching_policy_version,OLD.initial_snapshot_hash) THEN
        RAISE EXCEPTION 'PAPER_ACCOUNT_SEED_IMMUTABLE';
      END IF;
      IF NEW.revision <> OLD.revision+1 OR NEW.snapshot_as_of < OLD.snapshot_as_of THEN
        RAISE EXCEPTION 'PAPER_ACCOUNT_REVISION_CONFLICT';
      END IF;
    END IF;
    IF jsonb_typeof(NEW.broker_checkpoint::jsonb) <> 'object' OR
       jsonb_typeof(NEW.bucket_checkpoint::jsonb) <> 'object' THEN
      RAISE EXCEPTION 'PAPER_ACCOUNT_CHECKPOINT_INVALID';
    END IF;
    RETURN NEW;
  END $$""",
  """CREATE TRIGGER trg_paper_account_guard BEFORE INSERT OR UPDATE OR DELETE
    ON paper_execution_accounts FOR EACH ROW EXECUTE FUNCTION quantx_paper_account_guard()""",
  """CREATE FUNCTION quantx_paper_fact_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
  BEGIN RAISE EXCEPTION 'PAPER_FACT_IMMUTABLE'; END $$""",
  """CREATE TRIGGER trg_paper_event_immutable BEFORE UPDATE OR DELETE
    ON paper_execution_events FOR EACH ROW EXECUTE FUNCTION quantx_paper_fact_immutable()""",
  """CREATE TRIGGER trg_paper_fill_immutable BEFORE UPDATE OR DELETE
    ON paper_execution_fills FOR EACH ROW EXECUTE FUNCTION quantx_paper_fact_immutable()""",
  """CREATE FUNCTION quantx_paper_ledger_complete() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE a paper_execution_accounts; e paper_execution_events; prior_hash text;
  BEGIN
    SELECT * INTO a FROM paper_execution_accounts WHERE execution_id=NEW.execution_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'PAPER_ACCOUNT_MISSING'; END IF;
    IF TG_TABLE_NAME = 'paper_execution_events' THEN
      e := NEW;
      IF jsonb_typeof(e.result_payload::jsonb->'order_ids') IS DISTINCT FROM 'array' OR
         jsonb_typeof(e.result_payload::jsonb->'fill_ids') IS DISTINCT FROM 'array' THEN
        RAISE EXCEPTION 'PAPER_RECEIPT_MANIFEST_INVALID';
      END IF;
      IF (SELECT count(*) <> count(DISTINCT value) FROM jsonb_array_elements_text(e.result_payload::jsonb->'order_ids')) OR
         (SELECT count(*) <> count(DISTINCT value) FROM jsonb_array_elements_text(e.result_payload::jsonb->'fill_ids')) OR
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(e.result_payload::jsonb->'order_ids') item
           LEFT JOIN paper_execution_orders o ON o.order_id=item.value
           WHERE o.order_id IS NULL OR o.execution_id <> e.execution_id OR o.submitted_at > e.occurred_at) OR
         EXISTS (SELECT 1 FROM jsonb_array_elements_text(e.result_payload::jsonb->'fill_ids') item
           LEFT JOIN paper_execution_fills f ON f.fill_id=item.value
           WHERE f.fill_id IS NULL OR f.execution_id <> e.execution_id OR f.event_id <> e.event_id) THEN
        RAISE EXCEPTION 'PAPER_RECEIPT_FACTS_MISSING_OR_CONFLICTING';
      END IF;
      IF e.revision > a.revision OR e.occurred_at < a.seed_as_of THEN
        RAISE EXCEPTION 'PAPER_EVENT_REVISION_CONFLICT';
      END IF;
      IF e.revision = 1 THEN prior_hash := a.initial_snapshot_hash;
      ELSE
        SELECT resulting_snapshot_hash INTO prior_hash FROM paper_execution_events
          WHERE execution_id=e.execution_id AND revision=e.revision-1;
      END IF;
      IF prior_hash IS DISTINCT FROM e.previous_snapshot_hash THEN
        RAISE EXCEPTION 'PAPER_EVENT_CHAIN_CONFLICT';
      END IF;
    END IF;
    IF a.revision > 0 THEN
      SELECT * INTO e FROM paper_execution_events WHERE execution_id=a.execution_id AND revision=a.revision;
      IF NOT FOUND OR e.resulting_snapshot_hash <> a.snapshot_hash OR e.occurred_at <> a.snapshot_as_of THEN
        RAISE EXCEPTION 'PAPER_LEDGER_HALF_COMMIT';
      END IF;
    END IF;
    RETURN NULL;
  END $$""",
  """CREATE CONSTRAINT TRIGGER trg_paper_account_complete AFTER INSERT OR UPDATE
    ON paper_execution_accounts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION quantx_paper_ledger_complete()""",
  """CREATE CONSTRAINT TRIGGER trg_paper_event_complete AFTER INSERT
    ON paper_execution_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION quantx_paper_ledger_complete()""",
  """CREATE FUNCTION quantx_paper_order_material_guard() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE i trade_intents;
  BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'PAPER_ORDER_HISTORY_IMMUTABLE'; END IF;
    IF TG_OP = 'INSERT' AND NEW.side='BUY' THEN
      SELECT * INTO i FROM trade_intents WHERE id=NEW.intent_id;
      IF NOT FOUND OR i.status <> 'EXECUTION_READY' OR
        i.allocation_decision_id IS DISTINCT FROM NEW.allocation_decision_id OR
        i.admission_batch_id IS DISTINCT FROM NEW.admission_batch_id OR
        NOT EXISTS (SELECT 1 FROM t_allocation_decisions d
          WHERE d.decision_id=NEW.allocation_decision_id AND d.intent_id=i.id
          AND d.intent_version+1=i.allocation_version AND d.action IN ('ALLOW','CAP')) THEN
        RAISE EXCEPTION 'PAPER_BUY_CURRENT_AUTHORIZATION_REQUIRED';
      END IF;
      IF NOT EXISTS (SELECT 1 FROM t_allocation_decisions d
          JOIN account_risk_increase_admission_batches b ON b.admission_batch_id=NEW.admission_batch_id
          WHERE d.decision_id=NEW.allocation_decision_id
          AND d.created_at <= NEW.submitted_at AND NEW.submitted_at < d.expires_at
          AND b.committed_at AT TIME ZONE 'UTC' <= NEW.submitted_at
          AND NEW.submitted_at < b.expires_at AT TIME ZONE 'UTC') THEN
        RAISE EXCEPTION 'PAPER_BUY_AUTHORIZATION_TIME_INVALID';
      END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND
      (to_jsonb(NEW) - ARRAY['status','filled_volume','response_payload','last_event_id'])
      IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['status','filled_volume','response_payload','last_event_id']) THEN
      RAISE EXCEPTION 'PAPER_ORDER_REQUEST_IMMUTABLE';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.filled_volume < OLD.filled_volume OR
       (OLD.status IN ('FILLED','CANCELLED','REJECTED','EXPIRED') AND NEW.status <> OLD.status)) THEN
      RAISE EXCEPTION 'PAPER_ORDER_STATE_REGRESSION';
    END IF;
    IF TG_OP = 'UPDATE' AND (SELECT revision FROM paper_execution_events WHERE event_id=NEW.last_event_id)
       <= (SELECT revision FROM paper_execution_events WHERE event_id=OLD.last_event_id) THEN
      RAISE EXCEPTION 'PAPER_ORDER_EVENT_REGRESSION';
    END IF;
    RETURN NEW;
  END $$""",
  """CREATE TRIGGER trg_paper_order_material BEFORE INSERT OR UPDATE OR DELETE ON paper_execution_orders
    FOR EACH ROW EXECUTE FUNCTION quantx_paper_order_material_guard()""",
  """CREATE FUNCTION quantx_paper_order_complete() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE o paper_execution_orders; i trade_intents; a paper_execution_accounts;
    e paper_execution_events; filled bigint;
  BEGIN
    SELECT * INTO o FROM paper_execution_orders WHERE order_id=NEW.order_id;
    SELECT * INTO a FROM paper_execution_accounts WHERE execution_id=o.execution_id;
    SELECT * INTO i FROM trade_intents WHERE id=o.intent_id;
    SELECT * INTO e FROM paper_execution_events WHERE event_id=o.last_event_id;
    IF a.execution_id IS NULL OR i.id IS NULL OR e.event_id IS NULL OR
      i.environment <> 'PAPER' OR i.account_id IS DISTINCT FROM a.account_id OR
      i.owner_type <> o.owner_type OR i.owner_id <> o.owner_id OR
      i.instrument_code <> o.instrument_code OR i.direction <> o.side OR
      e.execution_id <> o.execution_id OR e.revision > a.revision OR
      (e.result_payload::jsonb->'order_ids' @> jsonb_build_array(o.order_id)) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'PAPER_ORDER_SCOPE_CONFLICT';
    END IF;
    IF o.side = 'BUY' THEN
      IF o.owner_type <> 'T_ASSISTANT_EXECUTION' OR o.owner_id <> o.execution_id OR
        NOT EXISTS (SELECT 1 FROM t_allocation_decisions d JOIN t_allocation_batches b
          ON b.allocation_batch_id=d.allocation_batch_id
          WHERE d.decision_id=o.allocation_decision_id AND d.intent_id=i.id
          AND d.action IN ('ALLOW','CAP') AND b.status='COMMITTED' AND b.execution_id=o.execution_id) OR
        NOT EXISTS (SELECT 1 FROM account_risk_increase_admission_batches b
          JOIN account_risk_increase_admission_items item ON item.admission_batch_id=b.admission_batch_id
          WHERE b.admission_batch_id=o.admission_batch_id AND b.environment='PAPER'
            AND b.paper_execution_id=o.execution_id AND b.status='COMMITTED' AND item.intent_id=i.id) THEN
        RAISE EXCEPTION 'PAPER_BUY_ADMISSION_CONFLICT';
      END IF;
    ELSE
      IF o.owner_type <> 'EXIT_PLAN' OR NOT EXISTS (SELECT 1 FROM auto_exit_plans p
        WHERE p.plan_id=o.owner_id AND p.environment='PAPER'
        AND p.source_execution_owner_type='T_ASSISTANT_EXECUTION'
        AND p.source_execution_owner_id=o.execution_id) THEN
        RAISE EXCEPTION 'PAPER_EXIT_OWNER_CONFLICT';
      END IF;
    END IF;
    SELECT coalesce(sum(volume),0) INTO filled FROM paper_execution_fills WHERE order_id=o.order_id;
    IF filled <> o.filled_volume THEN RAISE EXCEPTION 'PAPER_ORDER_FILL_SUM_CONFLICT'; END IF;
    RETURN NULL;
  END $$""",
  """CREATE CONSTRAINT TRIGGER trg_paper_order_complete AFTER INSERT OR UPDATE
    ON paper_execution_orders DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION quantx_paper_order_complete()""",
  """CREATE FUNCTION quantx_paper_fill_binding() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE o paper_execution_orders; e paper_execution_events;
  BEGIN
    SELECT * INTO o FROM paper_execution_orders WHERE order_id=NEW.order_id;
    SELECT * INTO e FROM paper_execution_events WHERE event_id=NEW.event_id;
    IF o.execution_id IS DISTINCT FROM NEW.execution_id OR
       e.execution_id IS DISTINCT FROM NEW.execution_id OR e.event_type <> 'QUOTE' OR
       NEW.occurred_at <> e.occurred_at OR NEW.occurred_at <= o.submitted_at OR
       NEW.occurred_at >= o.expires_at OR
       (e.result_payload::jsonb->'fill_ids' @> jsonb_build_array(NEW.fill_id)) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'PAPER_FILL_SCOPE_OR_CAUSALITY_CONFLICT';
    END IF;
    IF (SELECT coalesce(sum(volume),0) FROM paper_execution_fills WHERE order_id=o.order_id) <> o.filled_volume THEN
      RAISE EXCEPTION 'PAPER_ORDER_FILL_SUM_CONFLICT';
    END IF;
    RETURN NULL;
  END $$""",
  """CREATE CONSTRAINT TRIGGER trg_paper_fill_binding AFTER INSERT ON paper_execution_fills
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION quantx_paper_fill_binding()""",
  """CREATE FUNCTION quantx_risk_admission_scope_guard() RETURNS trigger LANGUAGE plpgsql AS $$
  BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.environment,NEW.account_id,NEW.paper_execution_id)
      IS DISTINCT FROM (OLD.environment,OLD.account_id,OLD.paper_execution_id) THEN
      RAISE EXCEPTION 'RISK_ADMISSION_SCOPE_IMMUTABLE';
    END IF;
    IF NEW.environment='PAPER' AND NOT EXISTS (SELECT 1 FROM paper_execution_accounts
      WHERE execution_id=NEW.paper_execution_id AND account_id=NEW.account_id) THEN
      RAISE EXCEPTION 'RISK_ADMISSION_PAPER_SCOPE_CONFLICT';
    END IF;
    RETURN NEW;
  END $$""",
  """CREATE TRIGGER trg_risk_admission_scope BEFORE INSERT OR UPDATE
    ON account_risk_increase_admission_batches FOR EACH ROW EXECUTE FUNCTION quantx_risk_admission_scope_guard()""",
  """CREATE OR REPLACE FUNCTION quantx_enforce_risk_admission_item_binding() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE i trade_intents; b account_risk_increase_admission_batches;
  BEGIN
    IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'RISK_ADMISSION_ITEM_IMMUTABLE'; END IF;
    SELECT * INTO b FROM account_risk_increase_admission_batches WHERE admission_batch_id=NEW.admission_batch_id;
    IF NOT FOUND OR b.status <> 'PREPARED' THEN RAISE EXCEPTION 'RISK_ADMISSION_ITEM_BATCH_NOT_PREPARED'; END IF;
    SELECT * INTO i FROM trade_intents WHERE id=NEW.intent_id;
    IF NOT FOUND OR i.admission_batch_id IS DISTINCT FROM b.admission_batch_id OR
      i.admission_rank IS DISTINCT FROM NEW.admission_rank OR i.owner_type <> NEW.owner_type OR
      i.owner_id <> NEW.owner_id OR i.environment <> b.environment OR i.account_id IS DISTINCT FROM b.account_id OR
      (b.environment='PAPER' AND (i.owner_type <> 'T_ASSISTANT_EXECUTION' OR i.owner_id <> b.paper_execution_id)) THEN
      RAISE EXCEPTION 'RISK_ADMISSION_ITEM_INTENT_CONFLICT';
    END IF;
    IF i.owner_type='T_ASSISTANT_EXECUTION' THEN
      IF NOT EXISTS (SELECT 1 FROM t_allocation_decisions d JOIN t_allocation_batches a
        ON a.allocation_batch_id=d.allocation_batch_id WHERE d.decision_id=i.allocation_decision_id
        AND d.intent_id=i.id AND d.action IN ('ALLOW','CAP') AND a.status='COMMITTED'
        AND a.execution_id=i.owner_id AND a.environment=i.environment) THEN
        RAISE EXCEPTION 'RISK_ADMISSION_T_ALLOCATION_REQUIRED';
      END IF;
      IF EXISTS (SELECT 1 FROM account_risk_increase_admission_items other
        JOIN trade_intents oi ON oi.id=other.intent_id
        JOIN t_allocation_decisions od ON od.decision_id=oi.allocation_decision_id
        JOIN t_allocation_decisions nd ON nd.decision_id=i.allocation_decision_id
        WHERE other.admission_batch_id=NEW.admission_batch_id
        AND nd.allocation_batch_id=od.allocation_batch_id
        AND ((nd.rank > od.rank AND NEW.admission_rank < other.admission_rank)
          OR (nd.rank < od.rank AND NEW.admission_rank > other.admission_rank))) THEN
        RAISE EXCEPTION 'RISK_ADMISSION_ALLOCATION_RANK_CONFLICT';
      END IF;
    END IF;
    RETURN NEW;
  END $$""",
)
