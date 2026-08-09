import pytest


@pytest.mark.asyncio
async def test_current_account_returns_null_when_account_unavailable(
  monkeypatch, authorized_graphql_context
):
  from quantx_api.gqlapi.resolvers.account import AccountResolver
  from quantx_api.gqlapi.schema import schema

  async def fake_current_account(account_id):
    assert account_id == "test-account"
    return None

  monkeypatch.setattr(
    AccountResolver,
    "get_account_async",
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
    """,
    context_value=authorized_graphql_context,
  )

  assert result.errors is None
  assert result.data == {"__typename": "Query", "currentAccount": None}
