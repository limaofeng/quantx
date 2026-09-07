"""Separate PAPER quote source time from event acceptance time.

Revision ID: 20260907_0055
Revises: 20260907_0054
"""

from alembic import op

revision = "20260907_0055"
down_revision = "20260907_0054"
branch_labels = None
depends_on = None


def upgrade():
  # No guessed availability and no rewriting immutable v1 hashes/checkpoints.
  op.execute("LOCK TABLE paper_execution_accounts IN ACCESS EXCLUSIVE MODE")
  op.execute("""DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM paper_execution_accounts) THEN
      RAISE EXCEPTION 'PAPER_V2_REQUIRES_EMPTY_ACCOUNT_STORE';
    END IF;
  END $$""")
  op.execute(
    "ALTER TABLE paper_execution_accounts ADD CONSTRAINT ck_paper_account_matching_policy CHECK (matching_policy_version = 'paper-strict-book-v2')"
  )
  op.execute(
    "ALTER TABLE paper_execution_events ADD COLUMN quote_source_at TIMESTAMP WITH TIME ZONE"
  )
  op.execute(
    "COMMENT ON COLUMN paper_execution_events.quote_source_at IS 'QUOTE 原始行情源时间；occurred_at 为本地受理时间'"
  )
  op.execute("""ALTER TABLE paper_execution_events ADD CONSTRAINT ck_paper_event_quote_clock CHECK (
    (event_type='QUOTE' AND quote_source_at IS NOT NULL AND quote_source_at<=occurred_at) OR
    (event_type<>'QUOTE' AND quote_source_at IS NULL))""")
  op.create_index(
    "ix_paper_event_scope_quote_source",
    "paper_execution_events",
    ["execution_id", "event_type", "quote_source_at"],
  )
  op.execute("""CREATE FUNCTION quantx_paper_event_clock_guard() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE a paper_execution_accounts; source_text text; accepted_text text; code text; prior_source text;
  BEGIN
    SELECT * INTO a FROM paper_execution_accounts WHERE execution_id=NEW.execution_id;
    IF NOT FOUND OR NEW.occurred_at < a.snapshot_as_of OR NOT isfinite(NEW.occurred_at) THEN
      RAISE EXCEPTION 'PAPER_EVENT_ACCEPTANCE_NOT_CAUSAL';
    END IF;
    IF NEW.event_type='QUOTE' THEN
      source_text := NEW.input_payload::jsonb->'quote'->>'timestamp';
      accepted_text := NEW.input_payload::jsonb->>'accepted_at';
      code := NEW.input_payload::jsonb->'quote'->>'instrument_code';
      IF jsonb_typeof(NEW.input_payload::jsonb->'quote'->'timestamp') IS DISTINCT FROM 'string' OR
         jsonb_typeof(NEW.input_payload::jsonb->'quote'->'instrument_code') IS DISTINCT FROM 'string' OR
         jsonb_typeof(NEW.input_payload::jsonb->'accepted_at') IS DISTINCT FROM 'string' OR
         source_text !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' OR
         accepted_text !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' OR
         NEW.quote_source_at IS DISTINCT FROM source_text::timestamptz OR
         NEW.occurred_at IS DISTINCT FROM accepted_text::timestamptz OR
         NOT isfinite(NEW.quote_source_at) THEN
        RAISE EXCEPTION 'PAPER_QUOTE_EVENT_CLOCK_CONFLICT';
      END IF;
      prior_source := a.broker_checkpoint::jsonb #>> ARRAY['material','market_snapshots',code,'timestamp'];
      IF prior_source IS NOT NULL AND NEW.quote_source_at < prior_source::timestamptz THEN
        RAISE EXCEPTION 'PAPER_QUOTE_SOURCE_NOT_MONOTONIC';
      END IF;
      IF prior_source IS NOT NULL AND NEW.quote_source_at = prior_source::timestamptz AND
         (NEW.result_payload::jsonb->'fill_ids') IS DISTINCT FROM '[]'::jsonb THEN
        RAISE EXCEPTION 'PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY';
      END IF;
    END IF;
    RETURN NEW;
  END $$""")
  op.execute("""CREATE TRIGGER trg_paper_event_clock BEFORE INSERT ON paper_execution_events
    FOR EACH ROW EXECUTE FUNCTION quantx_paper_event_clock_guard()""")
  op.execute("""CREATE OR REPLACE FUNCTION quantx_paper_fill_binding() RETURNS trigger LANGUAGE plpgsql AS $$
  DECLARE o paper_execution_orders; e paper_execution_events;
  BEGIN
    SELECT * INTO o FROM paper_execution_orders WHERE order_id=NEW.order_id;
    SELECT * INTO e FROM paper_execution_events WHERE event_id=NEW.event_id;
    IF o.execution_id IS DISTINCT FROM NEW.execution_id OR
       e.execution_id IS DISTINCT FROM NEW.execution_id OR e.event_type IS DISTINCT FROM 'QUOTE' OR
       NEW.occurred_at IS DISTINCT FROM e.occurred_at OR e.quote_source_at IS NULL OR
       e.quote_source_at <= o.submitted_at OR e.quote_source_at > NEW.occurred_at OR
       NEW.occurred_at >= o.expires_at OR
       (e.quote_source_at AT TIME ZONE 'Asia/Shanghai')::date <> (NEW.occurred_at AT TIME ZONE 'Asia/Shanghai')::date OR
       (e.input_payload::jsonb->'quote'->>'instrument_code') IS DISTINCT FROM o.instrument_code OR
       (e.result_payload::jsonb->'fill_ids' @> jsonb_build_array(NEW.fill_id)) IS DISTINCT FROM true THEN
      RAISE EXCEPTION 'PAPER_FILL_SCOPE_OR_CAUSALITY_CONFLICT';
    END IF;
    IF (SELECT coalesce(sum(volume),0) FROM paper_execution_fills WHERE order_id=o.order_id) <> o.filled_volume THEN
      RAISE EXCEPTION 'PAPER_ORDER_FILL_SUM_CONFLICT';
    END IF;
    RETURN NULL;
  END $$""")


def downgrade():
  raise RuntimeError("PAPER dual-clock facts cannot be destructively downgraded")
