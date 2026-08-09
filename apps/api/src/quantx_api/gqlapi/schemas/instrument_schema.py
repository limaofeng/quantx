from typing import List, Optional

import strawberry

from ..resolvers.instruments import InstrumentResolver
from ..types import Connection, Instrument, InstrumentOrder, InstrumentWhereInput


@strawberry.type(description="金融工具相关查询")
class InstrumentQuery:
  @strawberry.field(description="获取单个金融工具信息")
  async def instrument(self, stock_code: str) -> Optional[Instrument]:
    return await InstrumentResolver.get_instrument(stock_code)

  @strawberry.field(description="获取金融工具列表（非分页，有数量限制）")
  async def instruments(
    self,
    limit: int = 100,
    where: Optional[InstrumentWhereInput] = None,
    order_by: Optional[InstrumentOrder] = None,
  ) -> List[Instrument]:
    return await InstrumentResolver.get_instruments(limit, where, order_by)

  @strawberry.field(description="获取金融工具列表（光标分页）")
  async def instruments_connection(
    self,
    first: int = 20,
    last: int = 20,
    after: Optional[str] = None,
    before: Optional[str] = None,
    where: Optional[InstrumentWhereInput] = None,
    order_by: Optional[InstrumentOrder] = None,
  ) -> Connection[Instrument]:
    return await InstrumentResolver.get_instruments_connection(
      first, after, where, order_by
    )
