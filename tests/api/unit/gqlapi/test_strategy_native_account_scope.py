from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_api.auth.errors import AuthError
from quantx_api.auth.principal import Principal
from quantx_api.auth.tokens import utcnow
from quantx_api.gqlapi.resolvers.strategies import StrategyResolver
from quantx_api.gqlapi.schemas.strategy_schema import StrategyMutation, StrategyQuery


def _info(*, active_account_id: str | None) -> SimpleNamespace:
  accounts = ("ACCOUNT-1", "ACCOUNT-2") if active_account_id is None else (active_account_id,)
  return SimpleNamespace(
    context={
      "principal": Principal(
        user_id="user-1",
        username="user",
        display_name="User",
        device_session_id="session-1",
        access_token_expires_at=utcnow() + timedelta(minutes=5),
        permissions=frozenset({"strategy:read", "strategy:control"}),
        authorized_account_ids=accounts,
        active_account_id=active_account_id,
      )
    }
  )


@pytest.mark.asyncio
async def test_native_strategy_lists_are_forced_to_active_account():
  with (
    patch.object(
      StrategyResolver,
      "get_strategy_instances",
      new=AsyncMock(return_value=[]),
    ) as instances,
    patch.object(
      StrategyResolver,
      "get_strategy_runs",
      new=AsyncMock(return_value=[]),
    ) as runs,
  ):
    info = _info(active_account_id="ACCOUNT-1")
    assert await StrategyQuery().strategy_instances(info) == []
    assert await StrategyQuery().strategy_runs(info) == []

  assert instances.await_args.kwargs["account_id"] == "ACCOUNT-1"
  assert runs.await_args.kwargs["account_id"] == "ACCOUNT-1"


@pytest.mark.asyncio
async def test_legacy_web_strategy_list_keeps_existing_unscoped_shape():
  with patch.object(
    StrategyResolver,
    "get_strategy_instances",
    new=AsyncMock(return_value=[]),
  ) as resolver:
    assert (
      await StrategyQuery().strategy_instances(_info(active_account_id=None))
      == []
    )

  assert resolver.await_args.kwargs["account_id"] is None


@pytest.mark.asyncio
async def test_native_strategy_detail_rejects_cross_account_before_read():
  with (
    patch.object(
      StrategyResolver,
      "strategy_run_account_id",
      new=AsyncMock(return_value="ACCOUNT-2"),
    ),
    patch.object(
      StrategyResolver,
      "get_strategy_instance",
      new=AsyncMock(),
    ) as resolver,
  ):
    with pytest.raises(AuthError) as caught:
      await StrategyQuery().strategy_instance(
        _info(active_account_id="ACCOUNT-1"),
        "run-other-account",
      )

  assert caught.value.code == "FORBIDDEN"
  resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_strategy_lifecycle_rejects_cross_account_before_write():
  with (
    patch.object(
      StrategyResolver,
      "strategy_run_account_id",
      new=AsyncMock(return_value="ACCOUNT-2"),
    ),
    patch.object(
      StrategyResolver,
      "pause_strategy_instance",
      new=AsyncMock(),
    ) as resolver,
  ):
    with pytest.raises(AuthError) as caught:
      await StrategyMutation().pause_strategy_instance(
        _info(active_account_id="ACCOUNT-1"),
        "run-other-account",
      )

  assert caught.value.code == "FORBIDDEN"
  resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_strategy_detail_allows_matching_account():
  expected = object()
  with (
    patch.object(
      StrategyResolver,
      "strategy_run_account_id",
      new=AsyncMock(return_value="ACCOUNT-1"),
    ),
    patch.object(
      StrategyResolver,
      "get_strategy_instance",
      new=AsyncMock(return_value=expected),
    ) as resolver,
  ):
    actual = await StrategyQuery().strategy_instance(
      _info(active_account_id="ACCOUNT-1"),
      "run-own-account",
    )

  assert actual is expected
  resolver.assert_awaited_once_with("run-own-account")
