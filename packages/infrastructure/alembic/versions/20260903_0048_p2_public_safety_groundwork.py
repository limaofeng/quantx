"""P2 public exit/capacity/admission safety groundwork."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260903_0048"
down_revision = "20260903_0047"
branch_labels = None
depends_on = None


def _fail_on_shadow_conflicts() -> None:
  """Prove existing public owner and order identities before adding P2 facts."""

  op.execute(
    sa.text(
      """
      DO $$
      BEGIN
        IF EXISTS (
          SELECT 1
          FROM trade_intents
          WHERE owner_type IS NULL OR btrim(owner_type) = ''
             OR owner_id IS NULL OR btrim(owner_id) = ''
             OR environment NOT IN ('PAPER','LIVE','BACKTEST')
        ) THEN
          RAISE EXCEPTION 'P2_SHADOW_CONFLICT:invalid_trade_intent_owner';
        END IF;
        IF EXISTS (
          SELECT client_order_id
          FROM trade_command_outbox
          GROUP BY client_order_id HAVING count(*) > 1
        ) THEN
          RAISE EXCEPTION 'P2_SHADOW_CONFLICT:duplicate_outbox_client_order';
        END IF;
        IF EXISTS (
          SELECT environment, owner_type, owner_id, idempotency_key
          FROM trade_intents
          GROUP BY environment, owner_type, owner_id, idempotency_key
          HAVING count(*) > 1
        ) THEN
          RAISE EXCEPTION 'P2_SHADOW_CONFLICT:duplicate_owner_intent';
        END IF;
        IF EXISTS (
          SELECT source_type, source_id
          FROM auto_exit_plans
          GROUP BY source_type, source_id HAVING count(*) > 1
        ) THEN
          RAISE EXCEPTION 'P2_SHADOW_CONFLICT:duplicate_exit_plan_source';
        END IF;
      END $$;
      """
    )
  )


def upgrade() -> None:
  _fail_on_shadow_conflicts()
  op.add_column(
    "trade_intents",
    sa.Column("admission_batch_id", sa.String(length=36), nullable=True),
  )
  op.add_column(
    "trade_intents",
    sa.Column("admission_rank", sa.Integer(), nullable=True),
  )
  op.add_column(
    "trade_intents",
    sa.Column("admission_policy_version", sa.String(length=64), nullable=True),
  )
  op.add_column(
    "trade_intents",
    sa.Column("admission_input_fingerprint", sa.String(length=64), nullable=True),
  )
  op.create_check_constraint(
    "ck_trade_intent_admission_identity",
    "trade_intents",
    "(admission_batch_id IS NULL AND admission_rank IS NULL AND "
    "admission_policy_version IS NULL AND admission_input_fingerprint IS NULL) "
    "OR (admission_batch_id IS NOT NULL AND admission_rank >= 1 AND "
    "admission_policy_version IS NOT NULL AND "
    "admission_input_fingerprint IS NOT NULL)",
  )
  op.create_index(
    "uq_trade_intent_admission_rank",
    "trade_intents",
    ["admission_batch_id", "admission_rank"],
    unique=True,
    postgresql_where=sa.text("admission_batch_id IS NOT NULL"),
  )
  op.create_table(
    "account_risk_increase_admission_batches",
    sa.Column("admission_batch_id", sa.String(length=36), nullable=False),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("environment", sa.String(length=16), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("policy_version", sa.String(length=64), nullable=False),
    sa.Column("account_snapshot_id", sa.String(length=128), nullable=False),
    sa.Column("account_snapshot_hash", sa.String(length=64), nullable=False),
    sa.Column("obligation_watermark", sa.String(length=64), nullable=False),
    sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("intent_manifest_hash", sa.String(length=64), nullable=False),
    sa.Column("status", sa.String(length=24), nullable=False),
    sa.Column("processing_owner", sa.String(length=128), nullable=True),
    sa.Column("processing_fence_token", sa.String(length=36), nullable=True),
    sa.Column("processing_lease_until", sa.DateTime(), nullable=True),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
    sa.Column("committed_at", sa.DateTime(), nullable=True),
    sa.Column("terminal_reason", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.CheckConstraint("environment = 'LIVE'", name="ck_risk_admission_batch_live"),
    sa.CheckConstraint(
      "status IN ('PREPARED','COMMITTED','SUPERSEDED','EXPIRED','FAILED')",
      name="ck_risk_admission_batch_status",
    ),
    sa.CheckConstraint("attempt >= 1", name="ck_risk_admission_batch_attempt"),
    sa.PrimaryKeyConstraint("admission_batch_id"),
    sa.UniqueConstraint(
      "account_id",
      "environment",
      "input_fingerprint",
      "attempt",
      name="uq_risk_admission_batch_input_attempt",
    ),
    sa.UniqueConstraint(
      "account_id",
      "environment",
      "attempt",
      name="uq_risk_admission_batch_attempt",
    ),
  )
  op.create_index(
    "ix_risk_admission_batch_recovery",
    "account_risk_increase_admission_batches",
    ["account_id", "status", "processing_lease_until", "created_at"],
  )
  op.create_foreign_key(
    "fk_trade_intent_admission_batch",
    "trade_intents",
    "account_risk_increase_admission_batches",
    ["admission_batch_id"],
    ["admission_batch_id"],
    ondelete="RESTRICT",
  )
  op.create_table(
    "account_risk_increase_admission_items",
    sa.Column("admission_item_id", sa.String(length=36), nullable=False),
    sa.Column("admission_batch_id", sa.String(length=36), nullable=False),
    sa.Column("intent_id", sa.String(length=36), nullable=False),
    sa.Column("admission_rank", sa.Integer(), nullable=False),
    sa.Column("owner_type", sa.String(length=32), nullable=False),
    sa.Column("owner_id", sa.String(length=128), nullable=False),
    sa.Column("intent_created_at", sa.DateTime(), nullable=False),
    sa.CheckConstraint(
      "owner_type IN ('STRATEGY_RUN','T_ASSISTANT_EXECUTION','ENTRY_PLAN',"
      "'BOARD_ASSISTANT_EXECUTION','MANUAL_COMMAND')",
      name="ck_risk_admission_item_owner_type",
    ),
    sa.CheckConstraint(
      "length(owner_id) > 0 AND owner_id = trim(owner_id)",
      name="ck_risk_admission_item_owner_id",
    ),
    sa.CheckConstraint("admission_rank >= 1", name="ck_risk_admission_item_rank"),
    sa.ForeignKeyConstraint(
      ["admission_batch_id"],
      ["account_risk_increase_admission_batches.admission_batch_id"],
      ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(["intent_id"], ["trade_intents.id"], ondelete="RESTRICT"),
    sa.PrimaryKeyConstraint("admission_item_id"),
    sa.UniqueConstraint(
      "admission_batch_id",
      "intent_id",
      name="uq_risk_admission_item_intent",
    ),
    sa.UniqueConstraint(
      "admission_batch_id",
      "admission_rank",
      name="uq_risk_admission_item_rank",
    ),
  )
  op.create_index(
    "ix_risk_admission_item_intent",
    "account_risk_increase_admission_items",
    ["intent_id"],
  )
  op.execute(
    sa.text(
      """
      CREATE OR REPLACE FUNCTION quantx_enforce_risk_admission_item_binding()
      RETURNS trigger AS $$
      DECLARE
        intent_row trade_intents%ROWTYPE;
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'RISK_ADMISSION_ITEM_IMMUTABLE';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM account_risk_increase_admission_batches
          WHERE admission_batch_id = NEW.admission_batch_id AND status = 'PREPARED'
        ) THEN
          RAISE EXCEPTION 'RISK_ADMISSION_ITEM_BATCH_NOT_PREPARED';
        END IF;
        SELECT * INTO intent_row FROM trade_intents WHERE id = NEW.intent_id;
        IF NOT FOUND
           OR intent_row.admission_batch_id IS DISTINCT FROM NEW.admission_batch_id
           OR intent_row.admission_rank IS DISTINCT FROM NEW.admission_rank
           OR intent_row.owner_type IS DISTINCT FROM NEW.owner_type
           OR intent_row.owner_id IS DISTINCT FROM NEW.owner_id THEN
          RAISE EXCEPTION 'RISK_ADMISSION_ITEM_INTENT_CONFLICT';
        END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql;
      """
    )
  )
  op.execute(
    sa.text(
      """
      DROP TRIGGER IF EXISTS trg_risk_admission_item_binding
      ON account_risk_increase_admission_items
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE TRIGGER trg_risk_admission_item_binding
      BEFORE INSERT OR UPDATE OR DELETE ON account_risk_increase_admission_items
      FOR EACH ROW EXECUTE FUNCTION quantx_enforce_risk_admission_item_binding()
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE OR REPLACE FUNCTION quantx_enforce_risk_admission_intent_binding()
      RETURNS trigger AS $$
      DECLARE
        item_row account_risk_increase_admission_items%ROWTYPE;
        current_intent trade_intents%ROWTYPE;
      BEGIN
        -- Deferred triggers retain the NEW snapshot from each statement. An
        -- intent can be staged then assigned (or reassigned) in one transaction;
        -- validate the final persisted row against the final item manifest.
        SELECT * INTO current_intent FROM trade_intents WHERE id = NEW.id;
        IF NOT FOUND THEN
          RETURN NEW;
        END IF;
        -- A terminal batch retains its immutable historical manifest. Only
        -- PREPARED manifests still own the current admission projection.
        IF EXISTS (
          SELECT 1 FROM account_risk_increase_admission_items item
          JOIN account_risk_increase_admission_batches batch
            ON batch.admission_batch_id = item.admission_batch_id
          WHERE item.intent_id = current_intent.id
            AND batch.status = 'PREPARED'
            AND item.admission_batch_id
                IS DISTINCT FROM current_intent.admission_batch_id
        ) THEN
          RAISE EXCEPTION 'RISK_ADMISSION_INTENT_ITEM_CONFLICT';
        END IF;
        IF current_intent.admission_batch_id IS NULL THEN
          RETURN NEW;
        END IF;
        SELECT * INTO item_row
        FROM account_risk_increase_admission_items
        WHERE intent_id = current_intent.id
          AND admission_batch_id = current_intent.admission_batch_id;
        IF NOT FOUND
           OR item_row.admission_rank IS DISTINCT FROM current_intent.admission_rank
           OR item_row.owner_type IS DISTINCT FROM current_intent.owner_type
           OR item_row.owner_id IS DISTINCT FROM current_intent.owner_id THEN
          RAISE EXCEPTION 'RISK_ADMISSION_INTENT_ITEM_CONFLICT';
        END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql;
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE OR REPLACE FUNCTION quantx_enforce_risk_admission_batch_terminal()
      RETURNS trigger AS $$
      BEGIN
        IF OLD.status <> 'PREPARED' AND NEW.status IS DISTINCT FROM OLD.status THEN
          RAISE EXCEPTION 'RISK_ADMISSION_BATCH_TERMINAL_IMMUTABLE';
        END IF;
        RETURN NEW;
      END;
      $$ LANGUAGE plpgsql;
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE TRIGGER trg_risk_admission_batch_terminal
      BEFORE UPDATE OF status ON account_risk_increase_admission_batches
      FOR EACH ROW EXECUTE FUNCTION quantx_enforce_risk_admission_batch_terminal()
      """
    )
  )
  op.execute(
    sa.text(
      """
      DROP TRIGGER IF EXISTS trg_risk_admission_intent_binding
      ON trade_intents
      """
    )
  )
  op.execute(
    sa.text(
      """
      CREATE CONSTRAINT TRIGGER trg_risk_admission_intent_binding
      AFTER INSERT OR UPDATE ON trade_intents
      DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION quantx_enforce_risk_admission_intent_binding()
      """
    )
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
