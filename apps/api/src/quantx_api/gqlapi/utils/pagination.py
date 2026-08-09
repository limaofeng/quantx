"""
GraphQL 分页工具函数
提供 Pagination 到 Connection 的转换
"""

import base64
from typing import Callable, List, Optional, TypeVar

from quantx_infrastructure.database.types import Pageable, Pagination, Sort

from ..types import Connection, Edge, PageInfo

T = TypeVar("T")
G = TypeVar("G")


def decode_cursor(cursor: Optional[str]) -> int:
  """解码光标为页码"""
  if not cursor:
    return 0
  try:
    decoded = base64.b64decode(cursor).decode("utf-8")
    return int(decoded.split(":")[1]) + 1
  except (ValueError, IndexError):
    return 0


def encode_cursor(index: int) -> str:
  """编码索引为光标"""
  return base64.b64encode(f"cursor:{index}".encode()).decode()


def to_connection(
  pagination: Pagination[T],
  converter: Callable[[T], G],
  page: int,
  page_size: int,
) -> Connection[G]:
  """
  将 Pagination 转换为 GraphQL Connection

  Args:
    pagination: 数据库分页结果
    converter: 模型到 GraphQL 类型的转换函数
    page: 当前页码
    page_size: 每页大小

  Returns:
    GraphQL Connection 对象
  """
  edges: List[Edge[G]] = [
    Edge(
      node=converter(item),
      cursor=encode_cursor(page * page_size + idx),
    )
    for idx, item in enumerate(pagination.content)
  ]

  page_info = PageInfo(
    has_next_page=not pagination.last,
    has_previous_page=not pagination.first,
    start_cursor=edges[0].cursor if edges else None,
    end_cursor=edges[-1].cursor if edges else None,
  )

  return Connection(
    edges=edges,
    page_info=page_info,
    total_count=pagination.total_elements,
  )


async def paginate_with_connection(
  first: int,
  after: Optional[str],
  fetch_page: Callable[[Pageable], Pagination[T]],
  converter: Callable[[T], G],
  sort: Optional[Sort] = None,
) -> Connection[G]:
  """
  通用的光标分页查询函数

  Args:
    first: 每页数量
    after: 光标
    fetch_page: 分页查询函数
    converter: 模型到 GraphQL 类型的转换函数
    sort: 排序配置

  Returns:
    GraphQL Connection 对象
  """
  page = decode_cursor(after)
  pageable = Pageable.of(page=page, size=first, sort=sort)

  pagination = await fetch_page(pageable)

  return to_connection(
    pagination=pagination,
    converter=converter,
    page=page,
    page_size=first,
  )


async def paginate_service(
  service_find_page: Callable,
  converter: Callable[[T], G],
  first: int,
  after: Optional[str] = None,
  sort: Optional[Sort] = None,
  **kwargs,
) -> Connection[G]:
  """
  专门针对 Service.find_page 的分页包装器

  Args:
    service_find_page: Service 的 find_page 方法
    converter: 模型到 GraphQL 类型的转换函数
    first: 每页数量
    after: 光标
    sort: 排序配置
    **kwargs: 传递给 service_find_page 的额外参数 (如 filters)

  Returns:
    GraphQL Connection 对象

  示例:
    return await paginate_service(
      service_find_page=instrument_service.find_page,
      converter=InstrumentGQL.from_model,
      first=first,
      after=after,
      sort=Sort.from_order(order_by),
      filters=InstrumentFilterBuilder.from_input(filters),
    )
  """
  page = decode_cursor(after)
  pageable = Pageable.of(page=page, size=first, sort=sort)

  pagination = await service_find_page(pageable=pageable, **kwargs)

  return to_connection(
    pagination=pagination,
    converter=converter,
    page=page,
    page_size=first,
  )
