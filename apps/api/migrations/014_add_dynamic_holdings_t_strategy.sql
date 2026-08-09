-- Add the dynamic-holdings universe mode and account-level T-trade state tables.
-- PostgreSQL / asyncpg deployment migration; safe to execute repeatedly.

DO $$
BEGIN
  CREATE TYPE strategy_instrument_universe_mode AS ENUM (
    'STATIC',
    'ACCOUNT_HOLDINGS'
  );
EXCEPTION
  WHEN duplicate_object THEN NULL;
END
$$;

ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS instrument_universe_mode
    strategy_instrument_universe_mode NOT NULL DEFAULT 'STATIC';

CREATE TABLE IF NOT EXISTS t_trade_global_configs (
  id VARCHAR(36) PRIMARY KEY,
  account_id VARCHAR(50) NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT FALSE,
  mode VARCHAR(16) NOT NULL DEFAULT 'paper',
  auto_exit_acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
  ignored_stock_codes JSON NOT NULL DEFAULT '[]'::json,
  settings JSON NOT NULL DEFAULT '{}'::json,
  config_version INTEGER NOT NULL DEFAULT 1,
  strategy_run_id VARCHAR(36),
  universe_revision INTEGER NOT NULL DEFAULT 0,
  last_reconciled_at TIMESTAMP,
  last_error TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_t_trade_global_configs_account_id
  ON t_trade_global_configs(account_id);

CREATE INDEX IF NOT EXISTS ix_t_trade_global_configs_strategy_run_id
  ON t_trade_global_configs(strategy_run_id);

CREATE TABLE IF NOT EXISTS broker_position_snapshots (
  account_id VARCHAR(50) PRIMARY KEY,
  sequence BIGINT NOT NULL DEFAULT 0,
  source VARCHAR(32) NOT NULL DEFAULT 'MINIQMT',
  reported_at TIMESTAMP,
  received_at TIMESTAMP,
  position_count INTEGER NOT NULL DEFAULT 0,
  is_complete BOOLEAN NOT NULL DEFAULT FALSE,
  last_error TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);

COMMENT ON COLUMN strategies.instrument_universe_mode IS
  'Strategy instrument universe source: STATIC or ACCOUNT_HOLDINGS';
COMMENT ON TABLE t_trade_global_configs IS
  'One global dynamic-holdings T-trade configuration per broker account';
COMMENT ON TABLE broker_position_snapshots IS
  'Metadata for the latest broker position snapshot applied to positions';
