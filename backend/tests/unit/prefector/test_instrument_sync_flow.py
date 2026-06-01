from unittest.mock import AsyncMock, patch

import pytest

from prefector.flows.instrument_sync_flow import instrument_sync_flow


@pytest.mark.asyncio
async def test_instrument_sync_flow_saves_financial_data_for_stock():
  financial_data = {"Income": object(), "Balance": object()}

  with patch(
    "prefector.flows.instrument_sync_flow.fetch_stock_info",
    new=AsyncMock(return_value={"InstrumentType": 0, "InstrumentID": "600519"}),
  ), patch(
    "prefector.flows.instrument_sync_flow.save_single_stock_data",
    new=AsyncMock(return_value={"records_count": 1}),
  ), patch(
    "prefector.flows.instrument_sync_flow.fetch_stock_financial_data",
    new=AsyncMock(return_value=financial_data),
  ), patch("services.financial_service.FinancialService") as mock_service_cls:
    mock_service = mock_service_cls.return_value
    mock_service.save_batch_financial_data = AsyncMock(return_value=8)

    result = await instrument_sync_flow(stock_code="600519.SH")

    mock_service.save_batch_financial_data.assert_awaited_once_with(
      {"600519.SH": financial_data}
    )
    assert result["financial_records_saved"] == 8
