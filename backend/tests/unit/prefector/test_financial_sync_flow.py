import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def load_backend_prefector_package():
  package_dir = Path(__file__).parents[3] / "prefector"
  spec = importlib.util.spec_from_file_location(
    "prefector",
    package_dir / "__init__.py",
    submodule_search_locations=[str(package_dir)],
  )
  module = importlib.util.module_from_spec(spec)
  sys.modules["prefector"] = module
  spec.loader.exec_module(module)


load_backend_prefector_package()

from prefector.flows.batch_financial_sync_flow import batch_financial_sync_flow


@pytest.fixture(autouse=True)
def stub_prefect_run_logger(monkeypatch):
  import logging

  flow_module = importlib.import_module("prefector.flows.batch_financial_sync_flow")
  monkeypatch.setattr(
    flow_module,
    "get_run_logger",
    lambda: logging.getLogger("test.financial_sync_flow"),
  )


@pytest.mark.asyncio
async def test_batch_financial_sync_flow_uses_explicit_stock_codes():
  with patch(
    "prefector.flows.batch_financial_sync_flow.InstrumentService"
  ) as mock_service_cls, patch(
    "prefector.flows.batch_financial_sync_flow.sync_financial_batch_task"
  ) as mock_sync_task, patch(
    "prefector.flows.batch_financial_sync_flow.generate_batch_sync_report"
  ) as mock_report:

    async def sync_side_effect(codes, **kwargs):
      return {
        "status": "success",
        "success": len(codes),
        "failed": 0,
        "saved_count": 12,
      }

    async def report_side_effect(**kwargs):
      return kwargs

    mock_sync_task.side_effect = sync_side_effect
    mock_report.side_effect = report_side_effect

    result = await batch_financial_sync_flow.fn(
      stock_codes=["600519.SH", "600519.SH", "000001.SZ"],
      max_concurrency=1,
    )

    mock_service_cls.assert_not_called()
    mock_sync_task.assert_called_once_with(
      ["600519.SH", "000001.SZ"],
      batch_index=1,
      batch_total=1,
      timeout_seconds=300,
    )
    assert result["total_stocks"] == 2
    assert result["success_count"] == 2
    assert result["total_records_saved"] == 12


@pytest.mark.asyncio
async def test_batch_financial_sync_flow_marks_partial_batch_as_not_success():
  with patch(
    "prefector.flows.batch_financial_sync_flow.sync_financial_batch_task"
  ) as mock_sync_task, patch(
    "prefector.flows.batch_financial_sync_flow.generate_batch_sync_report"
  ) as mock_report, patch(
    "prefector.flows.batch_financial_sync_flow.send_sync_notification",
    new_callable=AsyncMock,
  ) as mock_notification:

    async def sync_side_effect(codes, **kwargs):
      return {
        "status": "partial",
        "success": len(codes),
        "failed": 0,
        "saved_count": 12,
      }

    async def report_side_effect(**kwargs):
      return kwargs

    mock_sync_task.side_effect = sync_side_effect
    mock_report.side_effect = report_side_effect

    result = await batch_financial_sync_flow.fn(
      stock_codes=["600519.SH", "000001.SZ"],
      max_concurrency=1,
      fail_on_batch_error=False,
    )

    assert result["status"] == "partial"
    assert result["success_count"] == 2
    assert result["failed_count"] == 0
    assert result["error_count"] == 1
    assert result["error_stocks"] == [
      "Batch 1/1: status=partial, success=2, failed=0, error=None"
    ]
    mock_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_financial_sync_flow_rejects_mismatched_batch_result():
  with patch(
    "prefector.flows.batch_financial_sync_flow.sync_financial_batch_task"
  ) as mock_sync_task, patch(
    "prefector.flows.batch_financial_sync_flow.generate_batch_sync_report"
  ) as mock_report, patch(
    "prefector.flows.batch_financial_sync_flow.send_sync_notification",
    new_callable=AsyncMock,
  ):

    async def sync_side_effect(codes, **kwargs):
      if kwargs["batch_index"] == 1:
        return {
          "status": "success",
          "batch_index": 1,
          "batch_total": 2,
          "total": len(codes),
          "success": len(codes),
          "failed": 0,
          "saved_count": 20,
          "stock_range_start": codes[0],
          "stock_range_end": codes[-1],
          "stock_codes": codes,
        }
      return {
        "status": "success",
        "batch_index": 1,
        "batch_total": 2,
        "total": len(codes),
        "success": len(codes),
        "failed": 0,
        "saved_count": 10,
        "stock_range_start": codes[0],
        "stock_range_end": codes[-1],
        "stock_codes": codes,
      }

    async def report_side_effect(**kwargs):
      return kwargs

    mock_sync_task.side_effect = sync_side_effect
    mock_report.side_effect = report_side_effect

    result = await batch_financial_sync_flow.fn(
      stock_codes=["600519.SH", "000001.SZ", "688088.SH"],
      batch_size=2,
      max_concurrency=1,
      fail_on_batch_error=False,
    )

    assert result["status"] == "partial"
    assert result["success_count"] == 2
    assert result["failed_count"] == 1
    assert result["error_count"] == 1
    assert result["total_records_saved"] == 20
    assert result["error_stocks"] == [
      "Batch 2/2: 返回结果不匹配: batch_index expected=2, actual=1"
    ]


@pytest.mark.asyncio
async def test_batch_financial_sync_flow_raises_after_batch_exception_report():
  with patch(
    "prefector.flows.batch_financial_sync_flow.sync_financial_batch_task"
  ) as mock_sync_task, patch(
    "prefector.flows.batch_financial_sync_flow.generate_batch_sync_report"
  ) as mock_report, patch(
    "prefector.flows.batch_financial_sync_flow.send_sync_notification",
    new_callable=AsyncMock,
  ) as mock_notification:

    async def sync_side_effect(codes, **kwargs):
      raise TimeoutError("财务批次 worker 超时 300s")

    async def report_side_effect(**kwargs):
      return kwargs

    mock_sync_task.side_effect = sync_side_effect
    mock_report.side_effect = report_side_effect

    with pytest.raises(RuntimeError, match="财务数据同步存在异常批次"):
      await batch_financial_sync_flow.fn(
        stock_codes=["600519.SH", "000001.SZ"],
        max_concurrency=1,
      )

    report_kwargs = mock_report.call_args.kwargs
    assert report_kwargs["status"] == "failed"
    assert report_kwargs["success_count"] == 0
    assert report_kwargs["failed_count"] == 2
    assert report_kwargs["error_count"] == 1
    assert report_kwargs["error_stocks"] == [
      "Batch 1/1: 财务批次 worker 超时 300s"
    ]
    mock_notification.assert_awaited_once()
