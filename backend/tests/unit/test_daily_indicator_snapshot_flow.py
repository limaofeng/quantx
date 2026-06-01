import importlib.util
import sys
from pathlib import Path


def load_backend_prefector_package():
  package_dir = Path(__file__).parents[2] / "prefector"
  spec = importlib.util.spec_from_file_location(
    "prefector",
    package_dir / "__init__.py",
    submodule_search_locations=[str(package_dir)],
  )
  module = importlib.util.module_from_spec(spec)
  sys.modules["prefector"] = module
  spec.loader.exec_module(module)


load_backend_prefector_package()

from prefector.flows.daily_indicator_snapshot_flow import (  # noqa: E402
  _signal_run_status,
  _signal_run_warnings,
)


def test_signal_run_status_marks_all_failed_batches_as_failed():
  assert _signal_run_status(total_codes=600, saved=0, failed=600) == "failed"


def test_signal_run_status_marks_partial_saved_batches_as_partial_failure():
  assert _signal_run_status(total_codes=600, saved=500, failed=100) == "partial_failure"


def test_signal_run_status_marks_saved_without_failures_as_success():
  assert _signal_run_status(total_codes=600, saved=600, failed=0) == "success"


def test_signal_run_warnings_expose_failure_summary():
  warnings = _signal_run_warnings(
    saved=0,
    skipped=12,
    failed=600,
    errors=["批量拉取 K 线失败: xtdata unavailable"],
  )

  assert "未保存任何日级信号快照" in warnings
  assert "部分标的信号计算失败" in warnings
  assert "批量拉取 K 线失败" in warnings
