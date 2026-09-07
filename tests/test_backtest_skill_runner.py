"""Offline verification of the skill runner's execution and database boundaries."""

import contextlib
import importlib.util
import io
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
  Path(__file__).resolve().parents[1]
  / ".codex/skills/strategy-backtest-rerun/scripts/rerun_backtest.py"
)
SPEC = importlib.util.spec_from_file_location("backtest_skill_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
RUN_ID = "11111111-1111-4111-8111-111111111111"
TEST_URL = "postgresql+asyncpg://test:placeholder@localhost/quantx_test"


class BacktestSkillRunnerTests(unittest.TestCase):
  def setUp(self):
    self.env = patch.dict(os.environ, {}, clear=True)
    self.env.start()
    self.addCleanup(self.env.stop)
    self.output = io.StringIO()
    self.stack = contextlib.ExitStack()
    self.stack.enter_context(contextlib.redirect_stdout(self.output))
    self.stack.enter_context(contextlib.redirect_stderr(self.output))
    self.addCleanup(self.stack.close)

  def test_explicit_run_id_and_valid_window_required(self):
    for args in [
      [],
      ["--run-id", "bad"],
      ["--run-id", RUN_ID, "--start", "2026-01-01"],
      ["--run-id", RUN_ID, "--start", "2026-02-01", "--end", "2026-01-01"],
    ]:
      with self.subTest(args=args), self.assertRaises(SystemExit):
        runner.main(args)

  def test_dry_run_never_reads_database(self):
    with patch.object(
      runner, "_configure_test_environment", side_effect=AssertionError
    ):
      self.assertEqual(runner.main(["--run-id", RUN_ID, "--dry-run"]), 0)

  def test_live_or_conflicting_database_is_rejected_without_exposing_credentials(self):
    for url, name in [
      ("", ""),
      ("postgresql+asyncpg://test:SECRET@localhost/quantx", ""),
      (TEST_URL, "other_test"),
    ]:
      with (
        self.subTest(url=url),
        patch.dict(
          os.environ,
          {
            "QUANTX_TEST_DATABASE_URL": url,
            "QUANTX_TEST_DATABASE_NAME": name,
          },
        ),
      ):
        self.assertEqual(runner.main(["--run-id", RUN_ID]), 2)
    self.assertNotIn("SECRET", self.output.getvalue())

  def test_run_and_summary_share_pytest_environment_and_unique_temp_path(self):
    os.environ["QUANTX_TEST_DATABASE_URL"] = TEST_URL
    os.environ["ENABLE_REAL_TRADING"] = "true"
    os.environ["QMT_REAL_TRADING_ENABLED"] = "true"
    os.environ["PULLBACK_GRID_RERUN_BACKTEST_START_TIME"] = "stale"
    paths = []

    def pytest_main(args):
      self.assertEqual(os.environ["DATABASE_URL"], TEST_URL)
      self.assertEqual(os.environ["ENV"], "testing")
      self.assertEqual(os.environ["ENABLE_REAL_TRADING"], "false")
      self.assertEqual(os.environ["QMT_REAL_TRADING_ENABLED"], "false")
      self.assertNotIn("PULLBACK_GRID_RERUN_BACKTEST_START_TIME", os.environ)
      paths.append(args[args.index("--basetemp") + 1])
      os.environ["RUNNER_TEST_SENTINEL"] = "pytest-in-this-process"
      return 0

    async def summary(run_id):
      self.assertEqual(run_id, RUN_ID)
      self.assertEqual(os.environ["DATABASE_URL"], TEST_URL)
      self.assertEqual(os.environ["RUNNER_TEST_SENTINEL"], "pytest-in-this-process")
      return {"run_mode": "backtest", "latest_status": "COMPLETED"}

    with (
      patch.dict(sys.modules, {"pytest": types.SimpleNamespace(main=pytest_main)}),
      patch.object(runner, "_query_latest_summary", summary),
    ):
      self.assertEqual(runner.main(["--run-id", RUN_ID]), 0)
      self.assertEqual(runner.main(["--run-id", RUN_ID]), 0)
    self.assertEqual(len(set(paths)), 2)
    for path in paths:
      self.assertTrue(
        Path(path).is_relative_to(runner._repo_root() / ".codex_screenshots")
      )

  def test_failed_pytest_does_not_report_an_old_success(self):
    os.environ["QUANTX_TEST_DATABASE_URL"] = TEST_URL
    with (
      patch.dict(sys.modules, {"pytest": types.SimpleNamespace(main=lambda args: 1)}),
      patch.object(runner, "_query_latest_summary", side_effect=AssertionError),
    ):
      self.assertEqual(runner.main(["--run-id", RUN_ID]), 1)

  def test_incomplete_run_fails_but_summary_only_does_not_execute_pytest(self):
    os.environ["QUANTX_TEST_DATABASE_URL"] = TEST_URL

    async def summary(run_id):
      return {"run_mode": "backtest", "latest_status": "RUNNING"}

    with patch.object(runner, "_query_latest_summary", summary):
      with patch.dict(
        sys.modules, {"pytest": types.SimpleNamespace(main=lambda args: 0)}
      ):
        self.assertEqual(runner.main(["--run-id", RUN_ID]), 1)
      with patch.dict(sys.modules, {"pytest": None}):
        self.assertEqual(runner.main(["--run-id", RUN_ID, "--summary-only"]), 0)


if __name__ == "__main__":
  unittest.main()
