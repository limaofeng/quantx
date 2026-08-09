"""
Instrument 专用的 WhereBuilder
预设好联表逻辑,提供更简洁的 API
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, List, Optional

from quantx_infrastructure.database.relational_base import WhereBuilder
from quantx_infrastructure.models import Instrument, Sector, SectorStock


class InstrumentWhereBuilder(WhereBuilder[Instrument]):
  """Instrument 专用过滤构建器

  预设了常用的联表关系,提供便捷方法

  使用示例:
  >>> # 按板块查询
  >>> where = InstrumentWhereBuilder().by_sector("科技").gt(Instrument.pre_close, 50)
  >>> result = await repository.find_all(pageable, where)
  >>>
  >>> # 多条件组合
  >>> where = (
  ...     InstrumentWhereBuilder()
  ...     .by_sector("科技")
  ...     .in_(Instrument.market, ["SH", "SZ"])
  ...     .between(Instrument.pre_close, 10, 100)
  ... )
  >>> result = await repository.find_page(pageable, where)
  """

  def __init__(self):
    super().__init__()
    self._sector_join_added = False

  def _add_sector_join_if_needed(self):
    """如果尚未添加，则添加 Sector 相关的 JOIN"""
    if not self._sector_join_added:
      self.join(SectorStock, Instrument.id == SectorStock.stock_code).join(
        Sector, SectorStock.sector_id == Sector.id
      )
      self._sector_join_added = True

  def by_sector(self, sector_name: str) -> InstrumentWhereBuilder:
    """按板块名称过滤"""
    self._add_sector_join_if_needed()
    self.eq(Sector.name, sector_name)
    return self

  def by_sectors(self, sector_names: List[str]) -> InstrumentWhereBuilder:
    """按多个板块名称过滤"""
    self._add_sector_join_if_needed()
    self.in_(Sector.name, sector_names)
    return self

  @classmethod
  def from_input(
    cls, filters: Optional[Any]
  ) -> "InstrumentWhereBuilder":
    """
    从GraphQL输入类型构建过滤器。
    1. 处理本类特有的过滤逻辑（如 sector, instrument_type）。
    2. 将其余字段交由基类的标准约定逻辑处理。
    """
    builder = cls()
    if not filters:
      return builder

    if isinstance(filters, dict):
      filter_data = dict(filters)
    elif is_dataclass(filters):
      filter_data = asdict(filters)
    elif hasattr(filters, "model_dump"):
      filter_data = filters.model_dump()
    else:
      filter_data = vars(filters)
    special_fields = set()

    # 步骤1: 处理特殊字段
    if "sector" in filter_data and filter_data["sector"] is not None:
      builder.by_sector(filter_data["sector"])
      special_fields.add("sector")

    if "stock_code" in filter_data and filter_data["stock_code"] is not None:
      builder.eq(Instrument.id, filter_data["stock_code"])
      special_fields.add("stock_code")

    if (
      "stock_code_contains" in filter_data
      and filter_data["stock_code_contains"] is not None
    ):
      builder.like(Instrument.id, f"%{filter_data['stock_code_contains']}%")
      special_fields.add("stock_code_contains")

    # 步骤2: 将剩余字段交由基类处理
    builder.apply_conventions_from_dict(
      model_class=Instrument,
      filter_data=filter_data,
      ignore_fields=special_fields,
    )

    return builder
