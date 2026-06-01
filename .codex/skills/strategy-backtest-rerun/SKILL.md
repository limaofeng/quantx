---
name: strategy-backtest-rerun
description: Rerun and verify QuantX strategy backtests from an existing StrategyRun. Use when asked to retry or re-run a strategy backtest, test rerunBacktestVersion, execute backend/tests/integration/core/strategies/test_backtest_rerun_real.py, change a backtest time window, or summarize the latest backtest version, trades, and performance snapshot after a rerun.
---

# Strategy Backtest Rerun

## Core Rules

- Use this only for QuantX strategy backtest reruns, never for live trading tests.
- Do not run `backend/start.bat`.
- Use the project Conda Python when available: `C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe`.
- Prefer the bundled script for repeatability:

```powershell
& "C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe" `
  .\.codex\skills\strategy-backtest-rerun\scripts\rerun_backtest.py `
  --run-id "632958c3-751f-4862-80ab-b61ca30c0a8a" `
  --start "2026-04-14 00:00:00" `
  --end "2026-05-14 23:59:59"
```

## Workflow

1. Check the requested `run_id`, start time, and end time. If missing, use the script defaults only when they match the current task.
2. Confirm `backend/.env` points at the intended database and services. For this project the known Prefect URL is `PREFECT_API_URL=http://192.168.101.4:30420/api`.
3. Run the script. It sets:
   - `PULLBACK_GRID_RERUN_REAL_RUN_ID`
   - `PULLBACK_GRID_RERUN_BACKTEST_START_TIME`
   - `PULLBACK_GRID_RERUN_BACKTEST_END_TIME`
4. The script executes:

```powershell
python -m pytest tests/integration/core/strategies/test_backtest_rerun_real.py -q -s --basetemp C:\tmp\quantx-pytest
```

5. After pytest, read the summary printed by the script:
   - latest backtest id/version/status
   - parent run status
   - trades/intents/orders
   - PnL, capital, return, drawdown, win rate
   - `performance_snapshot_path`
   - temp performance sample count

## Interpreting Results

- Success requires the pytest process to pass and the latest backtest status to be `COMPLETED`.
- For completed backtests, `temp_performance_samples` should normally be `0` because full performance is finalized into JSON.
- If no trades are generated, inspect strategy parameters and GridBook/template state before assuming data failure.
- If the test fails because the database is unreachable, report that as infrastructure state; do not claim the strategy failed.
- If a new backtest remains `RUNNING` after an interrupted process, inspect it before manually marking anything; preserve the latest successful completed version as the usable result.

## Useful Follow-Up Checks

Probe the latest state without rerunning:

```powershell
& "C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe" `
  .\.codex\skills\strategy-backtest-rerun\scripts\rerun_backtest.py `
  --run-id "632958c3-751f-4862-80ab-b61ca30c0a8a" `
  --summary-only
```

Preview the pytest command without executing it:

```powershell
& "C:\Users\limao\miniconda3\envs\xtquant-demo\python.exe" `
  .\.codex\skills\strategy-backtest-rerun\scripts\rerun_backtest.py `
  --run-id "632958c3-751f-4862-80ab-b61ca30c0a8a" `
  --start "2026-04-14 00:00:00" `
  --end "2026-05-14 23:59:59" `
  --dry-run
```
