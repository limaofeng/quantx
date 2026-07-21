-- Restore the former mandatory end-of-day and always-on hard-stop behavior.

UPDATE t_trade_global_configs
SET
  settings = (
    (
      COALESCE(settings, '{}'::json)::jsonb
      - 'time_exit_mode'
      - 'time_exit_time'
      - 'max_holding_trading_days'
      - 'hard_stop_enabled'
    ) || jsonb_build_object(
      'flatten_end_of_day', TRUE,
      'end_of_day_exit_time', COALESCE(
        settings ->> 'time_exit_time',
        '14:50'
      )
    )
  )::json,
  config_version = config_version + 1,
  updated_at = now()
WHERE
  COALESCE(settings, '{}'::json)::jsonb ? 'time_exit_mode'
  OR COALESCE(settings, '{}'::json)::jsonb ? 'time_exit_time'
  OR COALESCE(settings, '{}'::json)::jsonb ? 'max_holding_trading_days'
  OR COALESCE(settings, '{}'::json)::jsonb ? 'hard_stop_enabled';
