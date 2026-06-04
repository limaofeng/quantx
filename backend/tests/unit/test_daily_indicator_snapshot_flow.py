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
  _filter_signal_snapshot_codes,
  _infer_instrument_type,
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


def test_indicator_snapshot_flow_classifies_etf_and_index_codes():
  assert _infer_instrument_type("510300.SH") == "etf"
  assert _infer_instrument_type("000300.SH") == "index"
  assert _infer_instrument_type("399001.SZ") == "index"
  assert _infer_instrument_type("000001.SZ") == "stock"
  assert _infer_instrument_type("ignored", "沪深指数") == "index"


def test_indicator_snapshot_flow_filters_index_codes_from_signal_snapshots():
  codes = ["000001.SZ", "510300.SH", "000300.SH"]
  instrument_type_map = {
    "000001.SZ": "stock",
    "510300.SH": "etf",
    "000300.SH": "index",
  }

  assert _filter_signal_snapshot_codes(codes, instrument_type_map) == [
    "000001.SZ",
    "510300.SH",
  ]
