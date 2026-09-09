from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi import HTTPException
from quantx_api import market_sync_api
from quantx_infrastructure.auth.errors import forbidden


async def test_market_sync_evidence_requires_permission_and_closes_store(monkeypatch):
  audit = Mock(
    page=AsyncMock(return_value=[]),
    counts=AsyncMock(return_value=[]),
    close=AsyncMock(),
  )
  factory = Mock(return_value=audit)
  monkeypatch.setattr(market_sync_api, "MarketDataSyncAudit", factory)
  principal = Mock(is_native_session=False)
  result = await market_sync_api.partitions(
    UUID(int=1), offset=50, limit=50, principal=principal
  )
  principal.require_permission.assert_called_once_with("system-status:read")
  audit.page.assert_awaited_once_with(offset=50, limit=50)
  audit.close.assert_awaited_once()
  assert result["offset"] == 50
  principal.require_permission.side_effect = forbidden("denied")
  with pytest.raises(HTTPException) as caught:
    await market_sync_api.partitions(
      UUID(int=1), offset=0, limit=50, principal=principal
    )
  assert caught.value.status_code == 403
  assert factory.call_count == 1
