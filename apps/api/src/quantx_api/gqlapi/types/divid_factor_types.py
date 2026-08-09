from datetime import datetime

import strawberry
from quantx_infrastructure.models.divid_factor import DividFactor


@strawberry.type(description="除权因子")
class DividFactorData:
  stock_code: str = strawberry.field(description="股票代码")
  ex_date: str = strawberry.field(description="除权除息日")
  time: datetime = strawberry.field(description="时间")
  interest: float = strawberry.field(description="分红")
  stock_bonus: float = strawberry.field(description="送股比例")
  stock_gift: float = strawberry.field(description="转增比例")
  allot_num: float = strawberry.field(description="配股数量")
  allot_price: float = strawberry.field(description="配股价格")
  gugai: float = strawberry.field(description="股改")
  dr: float = strawberry.field(description="除权因子")

  @staticmethod
  def from_model(model: DividFactor) -> "DividFactorData":
    return DividFactorData(
      stock_code=model.stock_code,
      ex_date=model.ex_date,
      time=model.time,
      interest=model.interest,
      stock_bonus=model.stock_bonus,
      stock_gift=model.stock_gift,
      allot_num=model.allot_num,
      allot_price=model.allot_price,
      gugai=model.gugai,
      dr=model.dr,
    )
