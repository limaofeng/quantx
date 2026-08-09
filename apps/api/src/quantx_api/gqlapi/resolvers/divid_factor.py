from datetime import datetime
from typing import List, Optional

from quantx_infrastructure.services.divid_factor_service import DividFactorService

from ..types import DividFactorData

divid_factor_service = DividFactorService()


class DividFactorResolver:
  @staticmethod
  async def get_divid_factors(
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = None,
  ) -> List[DividFactorData]:
    if limit is not None and limit <= 0:
      return []

    factors = await divid_factor_service.get_divid_factors(
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      limit=limit,
    )

    return [DividFactorData.from_model(factor) for factor in factors]
