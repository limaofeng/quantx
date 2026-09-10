"""Calendar reads through authenticated HTTP and actual isolated PostgreSQL."""

# ruff: noqa: F811
from datetime import date

import httpx
import pytest
from quantx_contracts.development_reference import CalendarRequest
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


async def test_calendar_requires_valid_snapshot_and_authorization(references):
  store = references
  app = create_app(store=store, token="internal")
  async with app.router.lifespan_context(app):
    transport = httpx.ASGITransport(app)
    client = LocalMarketDataClient(token="internal", transport=transport)
    try:
      with pytest.raises(httpx.HTTPStatusError) as missing:
        await client.read_calendar(CalendarRequest(year=2026))
      assert missing.value.response.status_code == 503
      async with store.engine.begin() as db:
        await db.execute(
          text(
            "INSERT INTO holidays(market,year,date,description,created_at,updated_at) VALUES ('SH',2026,:day,'holiday',clock_timestamp(),clock_timestamp())"
          ),
          {"day": date(2026, 1, 1)},
        )
      result = await client.read_calendar(CalendarRequest(year=2026))
      assert [item.date for item in result.holidays] == [date(2026, 1, 1)]
      async with httpx.AsyncClient(
        transport=transport, base_url="http://local"
      ) as anonymous:
        response = await anonymous.get(
          "/market-data/internal/v1/reference/calendar", params={"year": 2026}
        )
        assert response.status_code == 401
      await app.state.calendar_reader.slot.acquire()
      try:
        with pytest.raises(httpx.HTTPStatusError) as busy:
          await client.read_calendar(CalendarRequest(year=2026))
        assert busy.value.response.status_code == 429
      finally:
        app.state.calendar_reader.slot.release()
    finally:
      await client.close()
