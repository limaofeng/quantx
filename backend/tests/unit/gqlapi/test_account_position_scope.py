import pytest


@pytest.mark.asyncio
async def test_positions_passes_account_id_to_service(monkeypatch):
  from gqlapi.resolvers.positions import PositionResolver
  from services.position_service import PositionService

  captured = []

  async def fake_get_positions(self, account_id=None):
    captured.append(account_id)
    return []

  async def fake_get_latest_prices(stock_codes):
    return {}

  monkeypatch.setattr(PositionService, "get_positions", fake_get_positions)
  monkeypatch.setattr(
    "gqlapi.resolvers.positions.market_data_service.get_latest_prices",
    fake_get_latest_prices,
  )

  assert await PositionResolver.get_positions("account-a") == []
  assert captured == ["account-a"]


@pytest.mark.asyncio
async def test_closed_cycles_query_requires_explicit_account_id():
  from gqlapi.schema import schema

  result = await schema.execute(
    """
    query {
      closedPositionCycles(limit: 20, offset: 0) {
        totalCount
      }
    }
    """
  )

  assert result.errors
  assert "accountId" in str(result.errors[0])
