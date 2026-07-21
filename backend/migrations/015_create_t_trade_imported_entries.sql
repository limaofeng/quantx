CREATE TABLE IF NOT EXISTS t_trade_imported_entries (
  id VARCHAR(36) PRIMARY KEY,
  account_id VARCHAR(50) NOT NULL,
  source_trade_id VARCHAR(100) NOT NULL,
  source_order_id VARCHAR(100),
  source_trade_time TIMESTAMP,
  stock_code VARCHAR(32) NOT NULL,
  volume INTEGER NOT NULL,
  price DOUBLE PRECISION NOT NULL,
  strategy_run_id VARCHAR(36) NOT NULL,
  batch_id VARCHAR(36) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'IMPORTED',
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now(),
  CONSTRAINT uq_t_trade_source_trade UNIQUE (account_id, source_trade_id)
);
CREATE INDEX IF NOT EXISTS ix_t_trade_imported_entries_account_id ON t_trade_imported_entries(account_id);
CREATE INDEX IF NOT EXISTS ix_t_trade_imported_entries_strategy_run_id ON t_trade_imported_entries(strategy_run_id);
