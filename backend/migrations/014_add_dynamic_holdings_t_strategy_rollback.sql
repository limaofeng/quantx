DROP TABLE IF EXISTS broker_position_snapshots;
DROP TABLE IF EXISTS t_trade_global_configs;

ALTER TABLE strategies
  DROP COLUMN IF EXISTS instrument_universe_mode;

DROP TYPE IF EXISTS strategy_instrument_universe_mode;
