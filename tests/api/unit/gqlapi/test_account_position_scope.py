import pytest


@pytest.mark.asyncio
async def test_positions_passes_account_id_to_service(monkeypatch):
  from quantx_api.gqlapi.resolvers.positions import PositionResolver
  from quantx_infrastructure.services.position_service import PositionService

  captured = []

  async def fake_get_positions(self, account_id=None):
    captured.append(account_id)
    return []

  monkeypatch.setattr(PositionService, "get_positions", fake_get_positions)

  assert await PositionResolver.get_positions("account-a") == []
  assert captured == ["account-a"]


@pytest.mark.asyncio
async def test_closed_cycles_query_requires_explicit_account_id():
  from quantx_api.gqlapi.schema import schema

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
