-- Migrate all persisted T-trade monitors to unlimited protection with optional stop-loss.
-- Idempotent: rows already using the new defaults are left unchanged.

UPDATE t_trade_global_configs
SET
  settings = (
    (
      COALESCE(settings, '{}'::json)::jsonb
      - 'flatten_end_of_day'
      - 'end_of_day_exit_time'
    ) || jsonb_build_object(
      'time_exit_mode', 'UNLIMITED',
      'time_exit_time', COALESCE(
        settings ->> 'time_exit_time',
        settings ->> 'end_of_day_exit_time',
        '14:50'
      ),
      'max_holding_trading_days', COALESCE(
        (settings ->> 'max_holding_trading_days')::integer,
        5
      ),
      'hard_stop_enabled', FALSE
    )
  )::json,
  config_version = config_version + 1,
  updated_at = now()
WHERE
  COALESCE(settings, '{}'::json)::jsonb ? 'flatten_end_of_day'
  OR COALESCE(settings, '{}'::json)::jsonb ? 'end_of_day_exit_time'
  OR COALESCE(settings ->> 'time_exit_mode', '') <> 'UNLIMITED'
  OR COALESCE((settings ->> 'hard_stop_enabled')::boolean, TRUE) IS DISTINCT FROM FALSE;
