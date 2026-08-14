from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_api.auth.errors import AuthError
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.resolvers.watchlist import WatchlistResolver
from quantx_api.gqlapi.schemas.watchlist_schema import WatchlistMutation
from quantx_api.gqlapi.types.watchlist_types import (
  AddWatchlistItemInput,
  ReorderWatchlistInput,
  WatchlistMutationResult,
)


def _info(*, account_id: str = "ACCOUNT-1") -> SimpleNamespace:
  principal = Principal(
    user_id="user-1",
    username="user",
    display_name="User",
    device_session_id="session-1",
    access_token_expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
    + timedelta(minutes=5),
    permissions=frozenset({"watchlist:write"}),
    authorized_account_ids=(account_id,),
  )
  return SimpleNamespace(context={"principal": principal})


@pytest.mark.asyncio
async def test_add_watchlist_uses_device_active_account_when_input_omits_it():
  result = WatchlistMutationResult(success=True, message="ok")
  with patch.object(
    WatchlistResolver,
    "add_watchlist_item",
    new=AsyncMock(return_value=result),
  ) as resolver:
    actual = await WatchlistMutation().add_watchlist_item(
      _info(),
      AddWatchlistItemInput(stock_code="600519.SH"),
    )

  assert actual is result
  assert resolver.await_args.kwargs["account_id"] == "ACCOUNT-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("method_name", "call"),
  [
    (
      "remove_watchlist_item",
      lambda mutation, info: mutation.remove_watchlist_item(
        info,
        "600519.SH",
        None,
      ),
    ),
    (
      "replace_watchlist",
      lambda mutation, info: mutation.replace_watchlist(
        info,
        ["600519.SH"],
        None,
      ),
    ),
    (
      "reorder_watchlist",
      lambda mutation, info: mutation.reorder_watchlist(
        info,
        ReorderWatchlistInput(symbols=["600519.SH"]),
      ),
    ),
  ],
)
async def test_watchlist_mutations_never_pass_an_implicit_default_account(
  method_name,
  call,
):
  result = WatchlistMutationResult(success=True, message="ok")
  with patch.object(
    WatchlistResolver,
    method_name,
    new=AsyncMock(return_value=result),
  ) as resolver:
    actual = await call(WatchlistMutation(), _info())

  assert actual is result
  assert "ACCOUNT-1" in resolver.await_args.args or (
    resolver.await_args.kwargs.get("account_id") == "ACCOUNT-1"
  )


@pytest.mark.asyncio
async def test_watchlist_mutation_rejects_cross_account_before_resolver():
  with patch.object(
    WatchlistResolver,
    "remove_watchlist_item",
    new=AsyncMock(),
  ) as resolver:
    with pytest.raises(AuthError) as caught:
      await WatchlistMutation().remove_watchlist_item(
        _info(),
        "600519.SH",
        "ACCOUNT-2",
      )

  assert caught.value.code == "FORBIDDEN"
  resolver.assert_not_awaited()
