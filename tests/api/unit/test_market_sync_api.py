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


@pytest.mark.parametrize("member", [True, False])
async def test_resume_checks_run_membership_before_forwarding(monkeypatch, member):
  from quantx_contracts.market_data_service import ResumeHistory

  audit = Mock(contains_request=AsyncMock(return_value=member), close=AsyncMock())
  monkeypatch.setattr(market_sync_api, "MarketDataSyncAudit", Mock(return_value=audit))
  client = Mock(
    resume_market_data_request=AsyncMock(return_value={"status": "UPLOADED"}),
    close=AsyncMock(),
  )
  factory = Mock(return_value=client)
  monkeypatch.setattr(market_sync_api, "LocalMarketDataClient", factory)
  principal = Mock(is_native_session=False)
  args = (UUID(int=1), UUID(int=2), ResumeHistory(reason="fixed storage"), principal)
  if member:
    assert (await market_sync_api.resume_partition(*args))["status"] == "UPLOADED"
    client.resume_market_data_request.assert_awaited_once_with(
      str(UUID(int=2)), reason="fixed storage"
    )
    client.close.assert_awaited_once()
  else:
    with pytest.raises(HTTPException) as caught:
      await market_sync_api.resume_partition(*args)
    assert caught.value.status_code == 404
    factory.assert_not_called()
  principal.require_permission.assert_called_once_with("operations:write")
  audit.close.assert_awaited_once()


async def test_native_session_cannot_resume_sync(monkeypatch):
  from quantx_contracts.market_data_service import ResumeHistory

  factory = Mock()
  monkeypatch.setattr(market_sync_api, "MarketDataSyncAudit", factory)
  with pytest.raises(HTTPException) as caught:
    await market_sync_api.resume_partition(
      UUID(int=1),
      UUID(int=2),
      ResumeHistory(reason="fixed"),
      Mock(is_native_session=True),
    )
  assert caught.value.status_code == 403
  factory.assert_not_called()


async def test_read_only_permission_cannot_resume(monkeypatch):
  from quantx_contracts.market_data_service import ResumeHistory

  factory = Mock()
  monkeypatch.setattr(market_sync_api, "MarketDataSyncAudit", factory)
  principal = Mock(is_native_session=False)
  principal.require_permission.side_effect = forbidden("denied")
  with pytest.raises(HTTPException) as caught:
    await market_sync_api.resume_partition(
      UUID(int=1), UUID(int=2), ResumeHistory(reason="fixed"), principal
    )
  assert caught.value.status_code == 403
  factory.assert_not_called()
