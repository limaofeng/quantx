"""
数据库层通用类型定义
包含分页、排序等通用类型
Spring JPA 风格的分页和排序支持
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, List, Optional, TypeVar

T = TypeVar("T")


class SortDirection(Enum):
  """排序方向枚举"""

  ASC = "ASC"
  DESC = "DESC"


class SortOrder:
  """排序字段配置"""

  def __init__(
    self,
    property: str,
    direction: SortDirection = SortDirection.ASC,
    ignore_case: bool = False,
    null_handling: str = "NATIVE",
  ):
    self.property = property
    self.direction = direction
    self.ignore_case = ignore_case
    self.null_handling = null_handling

  def to_dict(self):
    return {
      "property": self.property,
      "direction": self.direction.value,
      "ignoreCase": self.ignore_case,
      "nullHandling": self.null_handling,
    }


class Sort:
  """排序配置类"""

  def __init__(self, orders: Optional[List[SortOrder]] = None):
    self.orders = orders or []

  @property
  def sorted(self) -> bool:
    return len(self.orders) > 0

  @property
  def unsorted(self) -> bool:
    return not self.sorted

  @property
  def empty(self) -> bool:
    return not self.sorted

  def to_dict(self):
    return {
      "sorted": self.sorted,
      "unsorted": self.unsorted,
      "empty": self.empty,
      "orders": [order.to_dict() for order in self.orders],
    }

  @classmethod
  def by(cls, property: str, direction: str = "ASC") -> "Sort":
    """创建单字段排序"""
    direction = SortDirection(direction.upper())
    return cls([SortOrder(property, direction)])

  def and_sort(self, property: str, direction: str = "ASC") -> "Sort":
    """添加排序字段"""
    direction = SortDirection(direction.upper())
    self.orders.append(SortOrder(property, direction))
    return self

  @classmethod
  def from_order(cls, order_by: Optional[Any] = None) -> Optional["Sort"]:
    """从 GraphQL orderBy 输入创建 Sort 对象 (GitHub 风格)

    支持单个排序对象，枚举字段和方向自动提取

    Args:
      order_by: GraphQL orderBy 输入对象，包含 field 和 direction 属性

    Returns:
      Sort 对象，如果输入为空则返回 None

    示例:
      # GitHub 风格单个排序
      sort_obj = Sort.from_order(order_by)
    """
    if not order_by:
      return None

    if hasattr(order_by.field, "value"):
      field = order_by.field.value
    else:
      field = order_by.field

    if hasattr(order_by.direction, "value"):
      direction = SortDirection[order_by.direction.value]
    else:
      direction = SortDirection(str(order_by.direction).upper())

    return cls([SortOrder(field, direction)])


class Pageable:
  """分页请求参数类"""

  def __init__(
    self,
    page: int = 0,
    size: int = 10,
    sort: Optional[Sort] = None,
  ):
    self.page_number = max(0, page)
    self.page_size = max(1, size)
    self.sort = sort or Sort()

  @property
  def offset(self) -> int:
    """计算偏移量"""
    return self.page_number * self.page_size

  @property
  def paged(self) -> bool:
    return True

  @property
  def is_unpaged(self) -> bool:
    return False

  def to_dict(self):
    return {
      "pageNumber": self.page_number,
      "pageSize": self.page_size,
      "offset": self.offset,
      "sort": self.sort.to_dict(),
      "paged": self.paged,
      "unpaged": self.unpaged,
    }

  @classmethod
  def of(cls, page: int, size: int, sort: Optional[Sort] = None) -> "Pageable":
    """创建分页对象"""
    return cls(page, size, sort)

  @classmethod
  def of_size(cls, size: int) -> "Pageable":
    """创建指定大小的第一页"""
    return cls(0, size)

  @classmethod
  def unpaged(cls) -> "Pageable":
    """创建不分页的 Pageable（实际上是一个大分页）"""
    return cls(0, 999999)

  def first(self) -> "Pageable":
    """获取第一页"""
    return Pageable(0, self.page_size, self.sort)

  def next(self) -> "Pageable":
    """获取下一页"""
    return Pageable(self.page_number + 1, self.page_size, self.sort)

  def previous_or_first(self) -> "Pageable":
    """获取上一页，如果是第一页则返回第一页"""
    return Pageable(max(0, self.page_number - 1), self.page_size, self.sort)

  def with_page(self, page: int) -> "Pageable":
    """返回指定页码的新 Pageable"""
    return Pageable(page, self.page_size, self.sort)

  def with_sort(self, sort: Sort) -> "Pageable":
    """返回指定排序的新 Pageable"""
    return Pageable(self.page_number, self.page_size, sort)


@dataclass
class Pagination(Generic[T]):
  """分页结果类"""

  total_elements: int
  pageable: Pageable
  content: List[T]

  def __init__(self, content: List[T], pageable: Pageable, total_elements: int):
    self.content = content
    self.pageable = pageable
    self.total_elements = total_elements

  @property
  def total_pages(self) -> int:
    """总页数"""
    return (
      self.total_elements + self.pageable.page_size - 1
    ) // self.pageable.page_size

  @property
  def number(self) -> int:
    """当前页码（零基索引）"""
    return self.pageable.page_number

  @property
  def size(self) -> int:
    """每页大小"""
    return self.pageable.page_size

  @property
  def number_of_elements(self) -> int:
    """当前页元素数量"""
    return len(self.content)

  @property
  def first(self) -> bool:
    """是否为第一页"""
    return self.pageable.page_number == 0

  @property
  def last(self) -> bool:
    """是否为最后一页"""
    return self.pageable.page_number == self.total_pages - 1 or self.total_pages == 0

  @property
  def empty(self) -> bool:
    """是否为空页"""
    return self.number_of_elements == 0

  def to_dict(self):
    """转换为字典格式"""
    return {
      "content": self.content,
      "pageable": self.pageable.to_dict(),
      "totalElements": self.total_elements,
      "totalPages": self.total_pages,
      "number": self.number,
      "size": self.size,
      "numberOfElements": self.number_of_elements,
      "first": self.first,
      "last": self.last,
      "empty": self.empty,
    }
