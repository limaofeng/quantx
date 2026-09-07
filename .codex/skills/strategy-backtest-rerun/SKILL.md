---
name: strategy-backtest-rerun
description: Rerun a user-specified QuantX backtest or inspect its saved result in an explicitly selected test database. Never use for live strategy execution.
---

# Strategy Backtest Rerun

This workflow reruns an existing backtest in a dedicated PostgreSQL test database.
It does not rerun production records. Require the target run ID and an explicitly
configured `QUANTX_TEST_DATABASE_URL` with database name `test_*` or `*_test`.
If the requested run exists only in the live store, report the mismatch; do not
copy live records or redirect the test to the live database without a separate task.

Use the repository Python environment with QuantX packages installed (current Windows:
`.venv/Scripts/python.exe`). QMT's conda environment is not required. Do not start API,
Engine, Worker or QMT Agent. A read-only summary request uses `--summary-only`, not a rerun.

From the repository root:
```powershell
.venv/Scripts/python.exe .codex/skills/strategy-backtest-rerun/scripts/rerun_backtest.py --run-id "<UUID>" --dry-run
.venv/Scripts/python.exe .codex/skills/strategy-backtest-rerun/scripts/rerun_backtest.py --run-id "<UUID>"
```

Use `--start` and `--end` together with ISO timestamps only when changing the requested
window; otherwise preserve it. Set database credentials privately in the environment,
never in command arguments, reports or committed files. The helper disables live trading
and runs pytest and summary in the same interpreter and selected test environment.

A rerun request authorizes this specific backtest E2E path and its test-store writes,
not other E2E tests or real trading. Inspect interrupted RUNNING versions before any
retry; do not reset state automatically. Success requires the targeted test to pass and
the latest backtest to be COMPLETED. Report status, version, performance and data gaps.
Future macOS use requires its own installed Python/test environment under the migration
plan; it must not silently connect to the Windows live store.
