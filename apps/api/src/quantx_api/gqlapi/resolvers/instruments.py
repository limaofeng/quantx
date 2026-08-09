from typing import List, Optional

from quantx_infrastructure.database.types import Sort
from quantx_infrastructure.repositories.instrument_where_builder import (
  InstrumentWhereBuilder,
)
from quantx_infrastructure.services.instrument_service import InstrumentService

from ..types import Connection, InstrumentOrder, InstrumentWhereInput
from ..types import Instrument as InstrumentGQL
from ..utils import paginate_service

instrument_service = InstrumentService()


class InstrumentResolver:
  @staticmethod
  async def get_instruments(
    limit: int,
    where: Optional[InstrumentWhereInput] = None,
    order_by: Optional[InstrumentOrder] = None,
  ) -> List[InstrumentGQL]:
    """获取金融工具列表（非分页）"""
    sort_obj = Sort.from_order(order_by)
    filter_builder = InstrumentWhereBuilder.from_input(where)

    models = await instrument_service.find_all(
      where=filter_builder, sort=sort_obj, limit=limit
    )

    return [InstrumentGQL.from_model(m) for m in models]

  @staticmethod
  async def get_instrument(stock_code: str) -> Optional[InstrumentGQL]:
    """获取单个股票信息"""
    model = await instrument_service.find_by_id(stock_code)
    return InstrumentGQL.from_model(model) if model else None

  @staticmethod
  async def get_instruments_connection(
    first: int,
    after: Optional[str] = None,
    where: Optional[InstrumentWhereInput] = None,
    order_by: Optional[InstrumentOrder] = None,
  ) -> Connection[InstrumentGQL]:
    """获取金融工具列表（光标分页）"""
    return await paginate_service(
      service_find_page=instrument_service.find_page,
      converter=InstrumentGQL.from_model,
      first=first,
      after=after,
      sort=Sort.from_order(order_by),
      where=InstrumentWhereBuilder.from_input(where),
    )
