"""GraphQL resolver for persisted closed position lifecycles."""

from datetime import date
from typing import Optional

from services.closed_position_cycle_service import ClosedPositionCycleService

from ..types.portfolio_types import ClosedPositionCycle, ClosedPositionCyclePage


class ClosedPositionCycleResolver:
  @staticmethod
  async def get_page(
    account_id: str,
    start_date: Optional[str],
    end_date: Optional[str],
    limit: int,
    offset: int,
  ) -> ClosedPositionCyclePage:
    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    if parsed_start and parsed_end and parsed_start > parsed_end:
      raise ValueError("开始日期不能晚于结束日期")
    items, total = await ClosedPositionCycleService().get_page(
      account_id,
      parsed_start,
      parsed_end,
      limit,
      offset,
    )
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    return ClosedPositionCyclePage(
      items=[ClosedPositionCycle.from_model(item) for item in items],
      total_count=total,
      has_more=safe_offset + safe_limit < total,
    )
