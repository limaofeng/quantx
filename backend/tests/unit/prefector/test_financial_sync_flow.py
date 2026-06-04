from unittest.mock import patch

import pytest

from prefector.flows.batch_financial_sync_flow import batch_financial_sync_flow


@pytest.mark.asyncio
async def test_batch_financial_sync_flow_uses_explicit_stock_codes():
  with patch(
    "prefector.flows.batch_financial_sync_flow.InstrumentService"
  ) as mock_service_cls, patch(
    "prefector.flows.batch_financial_sync_flow.sync_financial_batch_task"
  ) as mock_sync_task, patch(
    "prefector.flows.batch_financial_sync_flow.generate_batch_sync_report"
  ) as mock_report:

    async def sync_side_effect(codes):
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

    result = await batch_financial_sync_flow(
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
