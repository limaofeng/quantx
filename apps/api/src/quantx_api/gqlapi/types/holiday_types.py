"""
Holiday GraphQL Types
节假日相关的 GraphQL 类型定义
"""

from datetime import date as python_date
from typing import List, Optional

import strawberry
from quantx_infrastructure.models.holidays import Holiday as HolidayModel


@strawberry.type(description="节假日信息")
class Holiday:
    id: int
    market: str
    year: int
    holiday_date: python_date
    description: Optional[str]

    @classmethod
    def from_model(cls, model: HolidayModel) -> "Holiday":
        return cls(
            id=model.id,
            market=model.market,
            year=model.year,
            holiday_date=model.date,
            description=model.description,
        )


@strawberry.input(description="节假日输入")
class HolidayInput:
    date: python_date
    description: Optional[str] = None


@strawberry.type(description="节假日查询结果")
class HolidayQueryResult:
    items: List[Holiday]
    total: int


@strawberry.type(description="节假日操作结果")
class HolidayMutationResult:
    success: bool
    message: str
    holiday: Optional[Holiday] = None

