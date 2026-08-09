from enum import Enum
from typing import Generic, List, TypeVar

import strawberry

T = TypeVar("T")


@strawberry.enum
class OrderDirection(Enum):
  """排序方向"""

  ASC = "ASC"
  DESC = "DESC"


@strawberry.type(description="消息响应")
class MessageResponse:
  success: bool = strawberry.field(description="操作是否成功")
  message: str = strawberry.field(description="响应消息")


@strawberry.type(description="操作结果")
class OperationResult:
  success: bool = strawberry.field(description="操作是否成功")
  message: str = strawberry.field(description="结果消息")
  data: str | None = strawberry.field(default=None, description="额外数据")


@strawberry.type
class PageInfo:
  """光标分页信息"""

  has_next_page: bool = strawberry.field(description="是否有下一页")
  has_previous_page: bool = strawberry.field(description="是否有上一页")
  start_cursor: str | None = strawberry.field(description="当前页的起始光标")
  end_cursor: str | None = strawberry.field(description="当前页的结束光标")


@strawberry.type
class Edge(Generic[T]):
  """连接列表中的一个节点"""

  node: T = strawberry.field(description="数据节点")
  cursor: str = strawberry.field(description="节点的唯一光标")


@strawberry.type
class Connection(Generic[T]):
  """分页连接对象"""

  edges: List[Edge[T]] = strawberry.field(description="节点边列表")
  page_info: PageInfo = strawberry.field(description="分页信息")
  total_count: int = strawberry.field(description="列表中的项目总数")
