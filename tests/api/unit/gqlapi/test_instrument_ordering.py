from types import SimpleNamespace

from quantx_api.gqlapi.types.common_types import OrderDirection
from quantx_api.gqlapi.types.instrument_types import (
  InstrumentOrder,
  InstrumentOrderField,
)
from quantx_infrastructure.database.types import Sort
from quantx_infrastructure.models import Instrument
from quantx_infrastructure.repositories.instrument_repository import (
  InstrumentRepository,
)
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect


def test_graphql_code_order_maps_to_instrument_orm_code_column() -> None:
  graphql_order = InstrumentOrder(
    field=InstrumentOrderField.CODE,
    direction=OrderDirection.ASC,
  )

  sort = Sort.from_order(graphql_order)
  assert sort is not None
  assert sort.orders[0].property == "id"

  statement = InstrumentRepository(SimpleNamespace())._apply_sort(
    select(Instrument), sort
  )
  sql = str(statement.compile(dialect=sqlite_dialect()))

  assert "ORDER BY instruments.code ASC" in sql
