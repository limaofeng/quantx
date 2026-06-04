import pytest


@pytest.mark.asyncio
async def test_current_account_returns_null_when_account_unavailable(monkeypatch):
  from gqlapi.resolvers.account import AccountResolver
  from gqlapi.schema import schema

  async def fake_current_account():
    return None

  monkeypatch.setattr(
    AccountResolver,
    "get_current_account_async",
    staticmethod(fake_current_account),
  )

  result = await schema.execute(
    """
    query {
      __typename
      currentAccount {
        id
      }
    }
    """
  )

  assert result.errors is None
  assert result.data == {"__typename": "Query", "currentAccount": None}


def test_trading_registry_can_skip_reconnect_for_cached_manager(monkeypatch):
  from miniqmt.manager_registry import XTTradingManagerRegistry

  class FakeTradingManager:
    is_connected = False

  registry = XTTradingManagerRegistry()
  fake_manager = FakeTradingManager()
  monkeypatch.setattr(registry, "_managers", {"test-account": fake_manager})

  def fail_reconnect(*args, **kwargs):
    raise AssertionError("reconnect should not run for passive account queries")

  monkeypatch.setattr(registry, "_reconnect_manager", fail_reconnect)

  assert registry.get_manager("test-account", reconnect=False) is fake_manager
