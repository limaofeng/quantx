"""
关系型数据库基础类和仓储抽象
包含：
1. SQLAlchemy 基础模型类
2. 时间戳混入类
3. 基础仓储抽象类
4. 分页和排序功能

使用示例:

# 1. 基础分页查询
pageable = Pageable.of(page=0, size=10)
result = await repository.find_page(pageable)

# 2. 带排序的分页查询
sort = Sort.by("name", "ASC").and_sort("created_at", "DESC")
pageable = Pageable.of(page=0, size=20, sort=sort)
result = await repository.find_page(pageable)

# 3. 使用 WhereBuilder 构建过滤条件
from quantx_infrastructure.database import WhereBuilder
from quantx_infrastructure.models import Instrument

where = (
    WhereBuilder()
    .lt(Instrument.pre_close, 123)
    .like(Instrument.name, "%股票%")
    .in_(Instrument.market, ["SH", "SZ"])
    .is_not_null(Instrument.list_date)
)
result = await repository.find_page(pageable, where)

# 4. 计数查询
count = await repository.count(where)

# 5. 存在性检查
exists = await repository.exists(WhereBuilder().eq(User.email, "test@example.com"))

# 6. 传统风格的列表查询（返回 List）
items = await repository.find_all(where, skip=0, limit=20, sort=Sort.by("name"))

# 7. 使用 Pageable 的便捷方法
first_page = Pageable.of_size(10)  # 第一页，10条记录
next_page = first_page.next()      # 下一页
unpaged = Pageable.unpaged()       # 不分页

WhereBuilder 支持的方法:
- eq, ne: 等于/不等于
- lt, lte, gt, gte: 比较操作符
- in_, not_in: 包含/不包含
- between: 范围查询
- like, ilike: 模糊匹配
- is_null, is_not_null: 空值检查
- custom: 自定义条件

注意：类型转换（如枚举）应在子类的业务方法中处理，而不是在基类中。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
  Any,
  Dict,
  Generic,
  List,
  Optional,
  Self,
  Type,
  TypeVar,
  Union,
)

from sqlalchemy import Column, DateTime, Integer, and_, asc, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func as sql_func
from sqlalchemy.sql.elements import ColumnElement

from quantx_infrastructure.database.types import Pageable, Pagination, Sort

# 创建基类
Base = declarative_base()

M = TypeVar("M")  # Model type


class WhereBuilder(Generic[M]):
  """类型安全的过滤条件构建器

  使用示例:
  >>> from quantx_infrastructure.models import Instrument, Sector, SectorStock
  >>>
  >>> # 基础查询
  >>> where = (
  ...     WhereBuilder()
  ...     .lt(Instrument.pre_close, 123)
  ...     .like(Instrument.name, "%股票%")
  ...     .build()
  ... )
  >>>
  >>> # 联表查询
  >>> filters = (
  ...     FilterBuilder()
  ...     .join(SectorStock, Instrument.id == SectorStock.stock_code)
  ...     .join(Sector, SectorStock.sector_id == Sector.id)
  ...     .eq(Sector.name, "科技")
  ...     .gt(Instrument.pre_close, 50)
  ...     .build()
  ... )
  >>> result = await repository.find_all(pageable, filters)
  """

  def __init__(self):
    self._conditions: List[ColumnElement] = []
    self._joins: List[tuple] = []  # 存储 JOIN 配置

  def apply_conventions_from_dict(
    self,
    model_class: Type[M],
    filter_data: Dict[str, Any],
    ignore_fields: Optional[set] = None,
  ) -> Self:
    """
    通过一套标准逻辑，从字典自动应用过滤条件。
    - 根据字段名称的后缀（如 _contains, _in）应用通用过滤规则。
    - 忽略 ignore_fields 集合中指定的字段。
    """
    ignore_fields = ignore_fields or set()

    for field_name, value in filter_data.items():
      if value is None or field_name in ignore_fields:
        continue

      # 默认操作为等于
      op = "eq"
      model_field_name = field_name

      # 根据命名约定解析操作符和字段名
      if field_name.endswith("_contains"):
        op = "like"
        model_field_name = field_name.removesuffix("_contains")
        value = f"%{value}%"
      elif field_name.endswith("_in"):
        op = "in_"
        model_field_name = field_name.removesuffix("_in")
      elif field_name.endswith("_ne"):
        op = "ne"
        model_field_name = field_name.removesuffix("_ne")
      elif field_name.endswith("_gt"):
        op = "gt"
        model_field_name = field_name.removesuffix("_gt")
      elif field_name.endswith("_gte"):
        op = "gte"
        model_field_name = field_name.removesuffix("_gte")
      elif field_name.endswith("_lt"):
        op = "lt"
        model_field_name = field_name.removesuffix("_lt")
      elif field_name.endswith("_lte"):
        op = "lte"
        model_field_name = field_name.removesuffix("_lte")

      # 检查模型中是否存在对应的字段
      if hasattr(model_class, model_field_name):
        model_attr = getattr(model_class, model_field_name)
        # 获取 builder 上的过滤方法 (eq, like, in_) 并调用
        filter_method = getattr(self, op)
        filter_method(model_attr, value)
      else:
        # 可以在此添加日志记录，用于调试未处理的字段
        pass

    return self

  def join(self, target: Any, onclause: Any) -> Self:
    """添加 JOIN 子句"""
    self._joins.append((target, onclause))
    return self

  def eq(self, column: Column, value: Any) -> Self:
    """等于 (=)"""
    self._conditions.append(column == value)
    return self

  def ne(self, column: Column, value: Any) -> Self:
    """不等于 (!=)"""
    self._conditions.append(column != value)
    return self

  def lt(self, column: Column, value: Any) -> Self:
    """小于 (<)"""
    self._conditions.append(column < value)
    return self

  def lte(self, column: Column, value: Any) -> Self:
    """小于等于 (<=)"""
    self._conditions.append(column <= value)
    return self

  def gt(self, column: Column, value: Any) -> Self:
    """大于 (>)"""
    self._conditions.append(column > value)
    return self

  def gte(self, column: Column, value: Any) -> Self:
    """大于等于 (>=)"""
    self._conditions.append(column >= value)
    return self

  def in_(self, column: Column, values: Union[List, tuple, set]) -> Self:
    """包含在列表中 (IN)"""
    self._conditions.append(column.in_(list(values)))
    return self

  def not_in(self, column: Column, values: Union[List, tuple, set]) -> Self:
    """不包含在列表中 (NOT IN)"""
    self._conditions.append(column.notin_(list(values)))
    return self

  def between(self, column: Column, start: Any, end: Any) -> Self:
    """在范围内 (BETWEEN)"""
    self._conditions.append(column.between(start, end))
    return self

  def like(self, column: Column, pattern: str) -> Self:
    """模糊匹配 (LIKE)"""
    self._conditions.append(column.like(pattern))
    return self

  def ilike(self, column: Column, pattern: str) -> Self:
    """忽略大小写的模糊匹配 (ILIKE)"""
    self._conditions.append(column.ilike(pattern))
    return self

  def is_null(self, column: Column) -> Self:
    """是否为空 (IS NULL)"""
    self._conditions.append(column.is_(None))
    return self

  def is_not_null(self, column: Column) -> Self:
    """是否不为空 (IS NOT NULL)"""
    self._conditions.append(column.isnot(None))
    return self

  def custom(self, condition: ColumnElement) -> Self:
    """自定义条件"""
    self._conditions.append(condition)
    return self

  def build(self) -> tuple[List[ColumnElement], List[tuple]]:
    """构建过滤条件和 JOIN 列表"""
    return self._conditions, self._joins

  def build_and(self) -> Optional[ColumnElement]:
    """构建 AND 条件"""
    if not self._conditions:
      return None
    if len(self._conditions) == 1:
      return self._conditions[0]
    return and_(*self._conditions)


class TimestampMixin:
  """时间戳混入类"""

  created_at = Column(DateTime, default=sql_func.now(), nullable=False)
  updated_at = Column(
    DateTime, default=sql_func.now(), onupdate=sql_func.now(), nullable=False
  )


class BaseModel(Base):
  """基础模型类"""

  __abstract__ = True

  id = Column(Integer, primary_key=True, index=True, autoincrement=True)


@dataclass
class BulkSaveResult:
  """批量保存结果"""

  saved_entities: List[M]
  saved_count: int
  inserted_count: int
  updated_count: int
  deleted_count: int = 0


class BaseRepository(Generic[M]):
  """基础异步仓储抽象类"""

  model_class: Type[M] = None

  def __init__(self, db_session: AsyncSession):
    self.db = db_session

  async def find_by_id(self, id: Any) -> Optional[M]:
    """根据ID获取实体"""
    if self.model_class is None:
      raise NotImplementedError("model_class must be set in the subclass")

    result = await self.db.execute(
      select(self.model_class).filter(self.model_class.id == id)
    )
    return result.scalar_one_or_none()

  async def find_one(self, where: Optional["WhereBuilder"] = None) -> Optional[M]:
    """
    根据筛选条件查找单个实体
    如果找到多个，只返回第一个
    """
    query = select(self.model_class)
    query = self._build_query_with_where(query, where)
    query = query.limit(1)
    result = await self.db.execute(query)
    return result.scalar_one_or_none()

  async def create(self, entity: M) -> M:
    """创建实体"""
    if self.model_class is None:
      raise NotImplementedError("model_class must be set in the subclass")

    self.db.add(entity)
    await self.db.commit()
    await self.db.refresh(entity)
    return entity

  async def update(self, id: Any, entity: M) -> Optional[M]:
    """更新实体"""
    if self.model_class is None:
      raise NotImplementedError("model_class must be set in the subclass")

    # 先检查实体是否存在
    existing_entity = await self.find_by_id(id)
    if existing_entity is None:
      return None

    # 设置实体的ID以确保更新正确的记录
    setattr(entity, "id", id)

    merged_entity = await self.db.merge(entity)
    await self.db.commit()
    await self.db.refresh(merged_entity)
    return merged_entity

  async def delete_by_id(self, id: Any) -> bool:
    """根据ID删除实体"""
    if self.model_class is None:
      raise NotImplementedError("model_class must be set in the subclass")

    entity = await self.find_by_id(id)
    if entity:
      await self.db.delete(entity)
      await self.db.commit()
      return True
    return False

  async def bulk_delete_by_ids(self, ids: List[Any]) -> int:
    """根据ID列表批量删除实体"""
    if self.model_class is None:
      raise NotImplementedError("model_class must be set in the subclass")

    if not ids:
      return 0

    # 构建批量删除查询
    delete_stmt = delete(self.model_class).where(self.model_class.id.in_(ids))

    # 执行删除
    result = await self.db.execute(delete_stmt)

    # 提交事务
    await self.db.commit()

    # 返回删除的记录数量
    return result.rowcount

  async def save(self, entity: M) -> M:
    """保存实体实例（插入或更新）"""
    if self.model_class is None:
      raise NotImplementedError("model_class must be set in the subclass")

    try:
      merged_entity = await self.db.merge(entity)
      await self.db.commit()
      await self.db.refresh(merged_entity)
      return merged_entity
    except Exception as e:
      await self.db.rollback()
      raise e

  async def bulk_save(self, entities: List[M]) -> BulkSaveResult:
    """批量保存实体实例（插入或更新）

    返回: BulkSaveResult 对象，包含保存的实体列表、插入数量和更新数量
    """
    if self.model_class is None:
      raise NotImplementedError("model_class must be set in the subclass")

    saved_entities = []
    inserted_count = 0
    updated_count = 0

    try:
      for entity in entities:
        # 检查是否为插入（ID 为 None）或更新（ID 存在）
        is_insert = getattr(entity, "id", None) is None

        merged_entity = await self.db.merge(entity)
        saved_entities.append(merged_entity)

        if is_insert:
          inserted_count += 1
        else:
          updated_count += 1

      await self.db.commit()
      for entity in saved_entities:
        await self.db.refresh(entity)

      return BulkSaveResult(
        saved_entities,
        inserted_count + updated_count,
        inserted_count,
        updated_count,
      )
    except Exception as e:
      await self.db.rollback()
      raise e

  def _build_query_with_where(self, base_query, where: Optional["WhereBuilder"]):
    """构建带过滤和 JOIN 的查询"""
    query = base_query
    if where:
      conditions, joins = where.build()
      for target, onclause in joins:
        query = query.join(target, onclause)
      if conditions:
        query = query.where(and_(*conditions))
    return query

  def _apply_sort(self, query, sort: "Sort"):
    """
    应用排序的通用方法

    Args:
        query: SQLAlchemy 查询对象
        sort: 排序对象

    Returns:
        查询对象（已应用排序）
    """
    if sort and sort.sorted:
      # 动态导入避免循环导入
      from quantx_infrastructure.database.types import SortDirection

      order_clauses = []
      for sort_order in sort.orders:
        column = getattr(self.model_class, sort_order.property, None)
        if column is not None:
          if sort_order.direction == SortDirection.DESC:
            order_clauses.append(desc(column))
          else:
            order_clauses.append(asc(column))

      if order_clauses:
        query = query.order_by(*order_clauses)

    return query

  async def find_all(
    self,
    where: Optional["WhereBuilder"] = None,
    skip: int = 0,
    limit: int = 100,
    sort: Optional["Sort"] = None,
  ) -> List[M]:
    """
    查询实体列表（传统风格）：
    - where: FilterBuilder 实例
    - skip: 跳过记录数（偏移量）
    - limit: 限制返回记录数
    - sort: 排序条件
    返回 List[M] 对象
    """
    # 构建基础查询
    query = select(self.model_class)
    query = self._build_query_with_where(query, where)

    # 应用排序
    query = self._apply_sort(query, sort)

    # 应用分页
    query = query.offset(skip).limit(limit)

    # 执行查询
    result = await self.db.execute(query)
    return list(result.scalars().all())

  async def find_page(
    self,
    pageable: "Pageable",
    where: Optional["WhereBuilder"] = None,
  ) -> "Pagination[M]":
    """
    分页查询实体：
    - pageable: 分页和排序参数（Pageable 对象）
    - where: FilterBuilder 实例
    返回 Pagination[M] 对象
    """
    # 构建基础查询
    base_query = select(self.model_class)
    query_with_where = self._build_query_with_where(base_query, where)

    # 应用排序
    sorted_query = self._apply_sort(query_with_where, pageable.sort)

    # 应用分页
    paginated_query = sorted_query.offset(pageable.offset).limit(pageable.page_size)

    # 执行查询
    result = await self.db.execute(paginated_query)
    content = list(result.scalars().all())

    # 获取总数
    count_query = select(func.count()).select_from(self.model_class)
    count_query_with_where = self._build_query_with_where(count_query, where)

    count_result = await self.db.execute(count_query_with_where)
    total_elements = count_result.scalar_one()

    # 动态导入避免循环导入
    from quantx_infrastructure.database.types import Pagination

    return Pagination(content=content, pageable=pageable, total_elements=total_elements)

  async def count(self, where: Optional["WhereBuilder"] = None) -> int:
    """
    计算符合条件的记录总数
    """
    # 构建计数查询
    count_query = select(func.count()).select_from(self.model_class)
    count_query_with_where = self._build_query_with_where(count_query, where)

    count_result = await self.db.execute(count_query_with_where)
    return count_result.scalar_one()

  async def exists(self, where: Optional["WhereBuilder"] = None) -> bool:
    """
    检查是否存在符合条件的记录
    """
    count = await self.count(where)
    return count > 0
