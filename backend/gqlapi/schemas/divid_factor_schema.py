from datetime import datetime
from typing import List, Optional

import strawberry

from ..resolvers.divid_factor import DividFactorResolver
from ..types import DividFactorData


@strawberry.type(description="除权因子相关查询")
class DividFactorQuery:
  @strawberry.field(description="获取除权因子数据")
  async def divid_factors(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
  ) -> List[DividFactorData]:
    return await DividFactorResolver.get_divid_factors(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=limit,
    )
