from datetime import datetime
from enum import Enum
from typing import Annotated, Optional

import strawberry

from models import Instrument as InstrumentModel
from models.enums import InstrumentType

from .common_types import OrderDirection
from .market_data_types import StockQuote


@strawberry.enum
class InstrumentOrderField(Enum):
  """金融工具可排序字段"""

  CODE = "code"
  NAME = "name"
  MARKET = "market"
  INSTRUMENT_TYPE = "instrument_type"
  PRE_CLOSE = "pre_close"
  LIST_DATE = "list_date"
  DELIST_DATE = "delist_date"
  IS_TRADING = "is_trading"


@strawberry.input
class InstrumentOrder:
  """金融工具排序配置"""

  field: InstrumentOrderField = strawberry.field(description="排序字段")
  direction: OrderDirection = strawberry.field(
    default=OrderDirection.ASC, description="排序方向"
  )


@strawberry.input
class InstrumentWhereInput:
  """金融工具查询条件"""

  sector: Optional[str] = strawberry.field(default=None, description="按板块名称过滤")
  type: Optional[InstrumentType] = strawberry.field(
    default=None, description="按工具类型过滤 (e.g., 'STOCK', 'FUND')"
  )
  type_in: Optional[list[InstrumentType]] = strawberry.field(
    name="type_in", default=None, description="按多个工具类型过滤"
  )
  market: Optional[str] = strawberry.field(
    default=None, description="按市场过滤 (e.g., 'SH', 'SZ')"
  )
  name_contains: Optional[str] = strawberry.field(
    name="name_contains", default=None, description="按名称模糊搜索"
  )
  stock_code_contains: Optional[str] = strawberry.field(
    name="stockCode_contains", default=None, description="按股票代码模糊搜索"
  )
  is_trading: Optional[bool] = strawberry.field(default=None, description="是否可交易")


@strawberry.type(description="金融产品基本信息")
class Instrument:
  id: str = strawberry.field(description="产品代码(主键)")
  market: Optional[str] = strawberry.field(description="合约市场代码")
  instrument_id: str = strawberry.field(description="合约代码")
  name: Optional[str] = strawberry.field(description="合约名称")
  type: Optional[InstrumentType] = strawberry.field(description="合约类型")
  abbreviation: Optional[str] = strawberry.field(description="合约名称的拼音简写")
  product_id: Optional[str] = strawberry.field(description="合约的品种ID")
  product_name: Optional[str] = strawberry.field(description="合约的品种名称")
  exchange_code: Optional[str] = strawberry.field(description="交易所代码")
  pre_close: Optional[float] = strawberry.field(description="前收盘价格")
  up_stop_price: Optional[float] = strawberry.field(description="当日涨停价")
  down_stop_price: Optional[float] = strawberry.field(description="当日跌停价")
  float_volume: Optional[float] = strawberry.field(description="流通股本")
  total_volume: Optional[float] = strawberry.field(description="总股本")
  price_tick: Optional[float] = strawberry.field(description="最小变价单位")
  is_trading: Optional[bool] = strawberry.field(description="合约是否可交易")
  interest_accrual_days: Optional[int] = strawberry.field(description="计息天数")
  created_at: Optional[datetime] = strawberry.field(description="创建时间")
  updated_at: Optional[datetime] = strawberry.field(description="更新时间")

  @strawberry.field(description="获取股票最新实时行情")
  async def quote(
    self,
    info: strawberry.types.Info,
  ) -> Optional[Annotated["StockQuote", strawberry.lazy(".market_data_types")]]:
    """延迟加载股票实时行情数据，使用 DataLoader 批量加载避免 N+1 查询"""
    loader = info.context["quote_loader"]
    return await loader.load(self.id)

  @staticmethod
  def from_model(model: InstrumentModel) -> "Instrument":
    """从数据库 Model 转换为 GraphQL 类型"""
    return Instrument(
      id=model.id,
      market=model.market,
      instrument_id=model.instrument_id,
      name=model.name,
      type=model.type,
      abbreviation=model.abbreviation,
      product_id=model.product_id,
      product_name=model.product_name,
      exchange_code=model.exchange_code,
      pre_close=model.pre_close,
      up_stop_price=model.up_stop_price,
      down_stop_price=model.down_stop_price,
      float_volume=model.float_volume,
      total_volume=model.total_volume,
      price_tick=model.price_tick,
      is_trading=model.is_trading,
      interest_accrual_days=model.interest_accrual_days,
      created_at=model.created_at,
      updated_at=model.updated_at,
    )
