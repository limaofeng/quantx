from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)
from sqlalchemy.dialects.postgresql import asyncpg

_EXPECTED_T_TRADE_ENTRY_REASONS = {
  "T_TRADE_PULLBACK_REBOUND_ENTRY",
  "T_TRADE_MOMENTUM_ACCELERATION_ENTRY",
}


def _empty_session() -> MagicMock:
  result = MagicMock()
  result.scalars.return_value.all.return_value = []
  session = MagicMock()
  session.execute = AsyncMock(return_value=result)
  return session


def _compiled_statement(session: MagicMock):
  statement = session.execute.await_args.args[0]
  return statement.compile(dialect=asyncpg.dialect())


def _assert_t_trade_entry_reason_filter(compiled) -> None:
  reason_collections = [
    set(value)
    for value in compiled.params.values()
    if isinstance(value, (list, tuple))
    and all(isinstance(item, str) for item in value)
  ]
  assert _EXPECTED_T_TRADE_ENTRY_REASONS in reason_collections


@pytest.mark.asyncio
async def test_find_recent_t_trade_entries_includes_both_entry_signal_types() -> None:
  session = _empty_session()

  rows = await TradeIntentRepository(session).find_recent_t_trade_entries(
    ["run-1"], limit=25
  )

  assert rows == []
  _assert_t_trade_entry_reason_filter(_compiled_statement(session))


@pytest.mark.asyncio
async def test_find_recent_t_trade_entries_page_includes_both_entry_signal_types() -> None:
  session = _empty_session()

  rows, has_next_page = (
    await TradeIntentRepository(session).find_recent_t_trade_entries_page(
      ["run-1"], first=25
    )
  )

  assert rows == []
  assert has_next_page is False
  _assert_t_trade_entry_reason_filter(_compiled_statement(session))
