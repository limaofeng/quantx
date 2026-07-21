CREATE TABLE IF NOT EXISTS closed_position_cycles (
  id VARCHAR(64) PRIMARY KEY,
  account_id VARCHAR(50) NOT NULL,
  account_type VARCHAR(30),
  stock_code VARCHAR(20) NOT NULL,
  instrument_name VARCHAR(50),
  opened_at TIMESTAMP,
  closed_at TIMESTAMP NOT NULL,
  buy_volume INTEGER NOT NULL DEFAULT 0,
  sell_volume INTEGER NOT NULL DEFAULT 0,
  average_buy_price NUMERIC(15, 4),
  average_sell_price NUMERIC(15, 4),
  gross_buy_amount NUMERIC(18, 2) NOT NULL DEFAULT 0,
  gross_sell_amount NUMERIC(18, 2) NOT NULL DEFAULT 0,
  gross_realized_pnl NUMERIC(18, 2),
  gross_realized_pnl_percent NUMERIC(12, 4),
  related_trade_ids JSON NOT NULL DEFAULT '[]',
  source VARCHAR(30) NOT NULL DEFAULT 'POSITION_CALLBACK',
  pnl_quality VARCHAR(30) NOT NULL DEFAULT 'INCOMPLETE_HISTORY',
  quality_flags JSON NOT NULL DEFAULT '[]',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_closed_position_cycles_account_closed
  ON closed_position_cycles (account_id, closed_at DESC);
CREATE INDEX IF NOT EXISTS ix_closed_position_cycles_stock_code
  ON closed_position_cycles (stock_code);
