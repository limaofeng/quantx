---
name: strategy-backtest-rerun
description: Rerun and verify QuantX strategy backtests from an existing StrategyRun. Use for retrying rerunBacktestVersion, executing apps/api/tests/integration/core/strategies/test_backtest_rerun_real.py, changing a backtest window, or summarizing the latest version, trades, and performance snapshot.
---

# Strategy Backtest Rerun

- Use only for backtests, never live-trading tests.
- Do not start API, Engine, Worker, or QMT Agent for this workflow.
- Use `C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe` when available.
- Confirm `apps/api/.env` points to the intended stores.

Run:

```powershell
& "C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe" `
  .\.codex\skills\strategy-backtest-rerun\scripts\rerun_backtest.py `
  --run-id "<strategy-run-id>" `
  --start "2026-04-14 00:00:00" `
  --end "2026-05-14 23:59:59"
```

The script runs the integration test from `apps/api`, then prints the latest
backtest version/status, parent status, trades/intents/orders, performance
metrics, snapshot path, and temporary sample count.

Success requires pytest to pass and the latest status to be `COMPLETED`. Treat
database unavailability as infrastructure state. Inspect interrupted `RUNNING`
versions before changing any state. Use `--summary-only` to inspect and
`--dry-run` to preview.
