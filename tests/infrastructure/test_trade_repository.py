from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from quantx_infrastructure.repositories.trade_repository import TradeRepository
from sqlalchemy.dialects.postgresql import asyncpg


def _empty_session() -> MagicMock:
  result = MagicMock()
  result.scalars.return_value.all.return_value = []
  session = MagicMock()
  session.execute = AsyncMock(return_value=result)
  return session


def _compiled_statement(session: MagicMock):
  statement = session.execute.await_args.args[0]
  return statement.compile(dialect=asyncpg.dialect())


@pytest.mark.asyncio
async def test_find_all_by_date_uses_half_open_datetime_range() -> None:
  session = _empty_session()

  await TradeRepository(session).find_all_by_date("2026-07-28", "account-1")

  compiled = _compiled_statement(session)
  sql = str(compiled).lower()
  assert "date(" not in sql
  assert "trades.traded_time >= " in sql
  assert "trades.traded_time < " in sql
  assert datetime(2026, 7, 28) in compiled.params.values()
  assert datetime(2026, 7, 29) in compiled.params.values()
  assert "account-1" in compiled.params.values()


@pytest.mark.asyncio
async def test_find_all_by_date_range_includes_the_whole_end_date() -> None:
  session = _empty_session()

  await TradeRepository(session).find_all_by_date_range(
    "2026-07-01",
    "2026-07-28",
    "account-1",
  )

  compiled = _compiled_statement(session)
  sql = str(compiled).lower()
  assert "date(" not in sql
  assert "trades.traded_time >= " in sql
  assert "trades.traded_time < " in sql
  assert datetime(2026, 7, 1) in compiled.params.values()
  assert datetime(2026, 7, 29) in compiled.params.values()
