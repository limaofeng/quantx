"""
时间序列数据库基础类和异常
提供类似 JPA 的仓储模式设计
"""

import dataclasses
import json
import logging
from abc import ABC, abstractmethod
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar, Union

import pandas as pd

from database.timeseries import get_timeseries_operations
from database.timeseries_connection import InfluxDBError
from core.utils import time_utils

logger = logging.getLogger(__name__)


class BaseModel(ABC):
  """时间序列模型基类"""

  def to_dict(self) -> Dict[str, Any]:
    """转换为字典格式，用于写入数据库"""
    return {k: v for k, v in self.__dict__.items() if v is not None}

  @abstractmethod
  def get_measurement_name(self) -> str:
    """获取测量名称（表名）"""
    pass

  @abstractmethod
  def get_tag_columns(self) -> List[str]:
    """获取标签列名"""
    pass

  @abstractmethod
  def get_timestamp_column(self) -> str:
    """获取时间戳列名"""
    pass


class AttributeConverter:
  def convert_to_database_column(
    self, dataset: pd.DataFrame, name: str
  ) -> pd.DataFrame:
    """将实体属性值转换为数据库列值"""
    return dataset

  def convert_to_entity_attribute(
    self, dataset: pd.DataFrame, name: str
  ) -> pd.DataFrame:
    """将数据库列值转换为实体属性值"""
    return dataset


class ListAttributeConverter(AttributeConverter):
  """把 list 字段展开为 prefix1..prefixN 写入；读取时从 prefix1..N / {name}_json 合并回 list。
  convert_to_database_column 返回 dict{prefix1:..., ...}；convert_to_entity_attribute 接受整行 dict 并返回 list。
  """

  def __init__(
    self, prefix: str = None, max_levels: int = 5, backup_json: bool = False
  ):
    self.prefix = prefix
    self.max_levels = max_levels
    self.backup_json = backup_json

  def convert_to_database_column(self, dataset: pd.DataFrame, name: str) -> dict:
    # dataset 保证为 pd.DataFrame（按要求），对列 name 进行向量化展开为 prefix1..prefixN
    col = name
    # 如果列不存在直接返回原 DataFrame（调用方应保证列存在，但这里容错）
    if col not in dataset.columns:
      return dataset

    # 把值规范为 list：支持 list/tuple、JSON 字符串、单个标量
    def to_list(x):
      if x is None:
        return []
      if isinstance(x, (list, tuple)):
        return list(x)
      if isinstance(x, str):
        try:
          return json.loads(x)
        except Exception:
          return []
      return [x]

    series = dataset[col].apply(to_list)
    used_prefix = self.prefix or name

    # 用向量化的方式填充新列（str.get 对 list-like Series 表现良好）
    for i in range(self.max_levels):
      dataset[f"{used_prefix}{i + 1}"] = series.str.get(i)

    if self.backup_json:
      dataset[f"{name}_json"] = series.apply(json.dumps)

    # 删除原始 list 列
    dataset.drop(columns=[col], inplace=True)
    return dataset

  def convert_to_entity_attribute(self, dataset: pd.DataFrame, name: str) -> list:
    """
    从展开的列（prefix1..prefixN）以及可选的 {name}_json 还原回原始的 list 列（列级批量恢复）。
    预期 dataset 一定为 pd.DataFrame，返回修改后的 DataFrame（在默认行为下删除展开列）。
    """
    used_prefix = self.prefix or name
    json_key = f"{name}_json"

    # 收集存在的展开列
    cols = [
      f"{used_prefix}{i + 1}"
      for i in range(self.max_levels)
      if f"{used_prefix}{i + 1}" in dataset.columns
    ]

    # 如果既没有展开列也没有 json 备份，直接返回原 DataFrame
    if not cols and json_key not in dataset.columns:
      return dataset

    def row_to_list(row):
      # 优先使用 json 备份
      if json_key in dataset.columns:
        try:
          v = row.get(json_key)
          if v is not None:
            return json.loads(v)
        except Exception:
          pass
      # 否则按顺序从展开列收集非 None 值
      res = []
      for c in cols:
        v = row.get(c)
        if v is not None:
          res.append(v)
      return res

    # 批量应用，得到每行的 list
    dataset[name] = dataset.apply(lambda r: row_to_list(r), axis=1)

    # 删除展开列；是否保留 json 由 backup_json 决定
    if cols:
      dataset.drop(columns=cols, inplace=True, errors="ignore")
    if not self.backup_json and json_key in dataset.columns:
      dataset.drop(columns=[json_key], inplace=True, errors="ignore")

    return dataset


# 泛型类型变量
M = TypeVar("M", bound=BaseModel)  # Model type


class BaseRepository(Generic[M]):
  """时间序列数据仓储基类 - 类似 JPA Repository

  提供类似 Spring Data JPA 的接口设计：
  - find_by_xxx(): 根据条件查询
  - save(): 保存实体
  - delete(): 删除实体
  - count(): 统计数量
  - exists(): 检查是否存在
  """

  model_class: Type[M] = None
  measurement: str = None

  _field_converters: Dict[str, AttributeConverter] = None

  def __init__(self):
    self.operations = get_timeseries_operations()

  def find_all(
    self,
    measurement: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    fields: Optional[List[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    order_by: str = "time ASC",
    as_frame: bool = True,
    use_chunking: bool = True,
    chunk_hours: Optional[int] = None,
  ) -> Union[pd.DataFrame, List[M]]:
    """查询所有记录 - 类似 JPA find_all()"""
    start_time = self._normalize_query_time(start_time)
    end_time = self._normalize_query_time(end_time)
    rows = self.operations.query_range(
      measurement=measurement or self.measurement,
      filters=filters,
      start_time=start_time,
      end_time=end_time,
      fields=fields,
      limit=limit,
      offset=offset,
      order_by=order_by,
      use_chunking=use_chunking,
      chunk_hours=chunk_hours,
    )

    if len(rows) == 0:
      return pd.DataFrame() if as_frame else []

    cols = rows[0].keys()

    records = pd.DataFrame(rows, columns=cols, index=None)

    # 使用通用的结果处理方法
    records = self._process_query_results(records)

    if as_frame or not isinstance(records, pd.DataFrame):
      return records

    # 调试信息：打印查询结果概览
    if len(records) > 0:
      logger.debug(
        "Query result: %s records, columns: %s",
        len(records),
        list(records.columns),
      )
    else:
      logger.debug("Query result: No records found")

    return self._bulk_dict_to_entities(records)

  def find_by_id(self, id_value: Any, id_field: str = "id") -> Optional[Dict[str, Any]]:
    """根据ID查询 - 类似 JPA find_by_id()"""
    filters = {id_field: id_value}
    results = self.find_all(filters=filters, limit=1)
    return results[0] if results else None

  def find_first(
    self, filters: Optional[Dict[str, Any]] = None, order_by: str = "time DESC"
  ) -> Optional[Dict[str, Any]]:
    """查询第一条记录 - 类似 JPA find_first()"""
    results = self.find_all(filters=filters, limit=1, order_by=order_by)
    return results[0] if results else None

  def find_top(
    self,
    limit: int,
    filters: Optional[Dict[str, Any]] = None,
    order_by: str = "time DESC",
  ) -> List[Dict[str, Any]]:
    """查询前N条记录 - 类似 JPA find_top()"""
    return self.find_all(filters=filters, limit=limit, order_by=order_by)

  def find_by_time_range(
    self,
    start_time: datetime,
    end_time: datetime,
    filters: Optional[Dict[str, Any]] = None,
    fields: Optional[List[str]] = None,
  ) -> List[Dict[str, Any]]:
    """根据时间范围查询 - 类似 JPA find_by_time_range()"""
    return self.find_all(
      filters=filters, start_time=start_time, end_time=end_time, fields=fields
    )

  def find_by_tags(self, tags: Dict[str, str], **kwargs) -> List[Dict[str, Any]]:
    """根据标签查询 - 类似 JPA find_by_tags()"""
    return self.find_all(filters=tags, **kwargs)

  def find_latest(
    self,
    filters: Optional[Dict[str, Any]] = None,
    fields: Optional[List[str]] = None,
  ) -> Optional[Dict[str, Any]]:
    """查询最新记录 - 类似 JPA find_latest()"""
    return self.find_first(filters=filters, order_by="time DESC")

  def find_oldest(
    self,
    filters: Optional[Dict[str, Any]] = None,
    fields: Optional[List[str]] = None,
  ) -> Optional[Dict[str, Any]]:
    """查询最旧记录 - 类似 JPA find_oldest()"""
    return self.find_first(filters=filters, order_by="time ASC")

  def count(
    self,
    filters: Optional[Dict[str, Any]] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
  ) -> int:
    """统计记录数量 - 类似 JPA count()"""
    start_time = self._normalize_query_time(start_time)
    end_time = self._normalize_query_time(end_time)
    try:
      # 使用 COUNT 查询
      count_query = f"SELECT COUNT(*) as count FROM {self.measurement}"

      # 添加时间范围条件
      conditions = []
      if start_time:
        conditions.append(f"time >= '{start_time.isoformat()}'")
      if end_time:
        conditions.append(f"time <= '{end_time.isoformat()}'")

      # 添加过滤条件
      if filters:
        for key, value in filters.items():
          if isinstance(value, str):
            conditions.append(f"{key} = '{value}'")
          else:
            conditions.append(f"{key} = {value}")

      if conditions:
        count_query += " WHERE " + " AND ".join(conditions)

      results = self.manager.query(count_query)
      return results[0].get("count", 0) if results else 0

    except Exception:
      # 如果COUNT查询失败，使用find_all统计
      results = self.find_all(filters=filters, start_time=start_time, end_time=end_time)
      return len(results)

  def exists(
    self,
    filters: Optional[Dict[str, Any]] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
  ) -> bool:
    """检查是否存在记录 - 类似 JPA exists()"""
    return self.count(filters, start_time, end_time) > 0

  def exists_by_id(self, id_value: Any, id_field: str = "id") -> bool:
    """根据ID检查是否存在 - 类似 JPA existsById()"""
    return self.find_by_id(id_value, id_field) is not None

  # ========== JPA 风格的保存方法 ==========

  def save(self, entity: M):
    """保存数据 - 类似 JPA save()"""
    # 这里需要子类实现数据结构映射
    records = self._build_records_from_entity(entity)

    measurement = entity.get_measurement_name() or self.measurement

    logger.debug("records columns: %s", records.columns)
    logger.debug("records: %s", records)

    self.operations.write_records(
      measurement=measurement,
      records=records,
      timestamp_column=entity.get_timestamp_column(),
      tag_columns=entity.get_tag_columns(),
      batch_size=1,
    )

  def bulk_save(
    self, records: pd.DataFrame, measurement=None, batch_size: int = 1000
  ) -> int:
    """批量保存数据 - 返回成功写入的数量"""
    timestamp_column = self.model_class().get_timestamp_column()
    tag_columns = self.model_class().get_tag_columns()

    if records is None or records.empty:
      logger.debug("bulk_save: 无记录需要保存")
      return 0

    if timestamp_column in records.columns:
      records[timestamp_column] = self._normalize_timestamp_series(
        records[timestamp_column]
      )

    # 记录写入前的行数
    record_count = len(records)

    for col in tag_columns:
      if col in records.columns:
        records[col] = records[col].astype(str)

    self.operations.write_records(
      measurement=measurement or self.measurement,
      records=records,
      timestamp_column=timestamp_column,
      tag_columns=tag_columns,
      batch_size=batch_size,
    )
    return record_count

  def delete_by(
    self,
    field: str,
    value: Any,
  ) -> bool:
    """根据字段删除 - 类似 JPA delete_by()"""
    try:
      # 构建删除查询
      delete_query = f"DELETE FROM {self.measurement} WHERE {field} = "
      if isinstance(value, str):
        delete_query += f"'{value}'"
      else:
        delete_query += str(value)

      self.operations.query(delete_query)
      return True
    except Exception:
      return False

  def delete(
    self, filters: Optional[Dict[str, Any]] = None, measurement: str = None
  ) -> bool:
    try:
      delete_query = f"DELETE FROM {measurement or self.measurement}"

      if filters:
        conditions = []
        for key, value in filters.items():
          if isinstance(value, str):
            conditions.append(f"{key} = '{value}'")
          else:
            conditions.append(f"{key} = {value}")

        if conditions:
          delete_query += " WHERE " + " AND ".join(conditions)

      self.operations.query(delete_query)
      return True
    except Exception:
      return False

  def delete_by_time_range(
    self,
    start_time: datetime,
    end_time: datetime,
    filters: Optional[Dict[str, Any]] = None,
  ) -> bool:
    """根据时间范围删除 - 类似 JPA delete_by_time_range()"""
    try:
      start_time = self._normalize_query_time(start_time)
      end_time = self._normalize_query_time(end_time)
      # 构建删除查询
      delete_query = f"DELETE FROM {self.measurement} WHERE time >= '{start_time.isoformat()}' AND time <= '{end_time.isoformat()}'"

      # 添加过滤条件
      if filters:
        for key, value in filters.items():
          if isinstance(value, str):
            delete_query += f" AND {key} = '{value}'"
          else:
            delete_query += f" AND {key} = {value}"

      self.manager.query(delete_query)
      return True
    except Exception:
      return False

  def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> bool:
    """删除所有记录 - 类似 JPA delete_all()"""
    try:
      delete_query = f"DELETE FROM {self.measurement}"

      if filters:
        conditions = []
        for key, value in filters.items():
          if isinstance(value, str):
            conditions.append(f"{key} = '{value}'")
          else:
            conditions.append(f"{key} = {value}")

        if conditions:
          delete_query += " WHERE " + " AND ".join(conditions)

      self.manager.query(delete_query)
      return True
    except Exception:
      return False

  def _build_records_from_entity(self, entity: M) -> pd.DataFrame:
    """将实体转换为 InfluxDB Point"""
    timestamp = getattr(entity, entity.get_timestamp_column())
    timestamp = self._normalize_query_time(timestamp)
    tags = {name: getattr(entity, name) for name in entity.get_tag_columns()}
    fields = {
      name: getattr(entity, name)
      for name in entity.__dict__.keys()
      if name not in tags and name != entity.get_timestamp_column()
    }

    # 构建 DataFrame
    df = pd.DataFrame([{**tags, **fields, entity.get_timestamp_column(): timestamp}])

    _field_converters = self._get_field_converters()
    for col, converter in _field_converters.items():
      if col in df.columns:
        try:
          df = converter.convert_to_database_column(df, col)
        except Exception as e:
          logger.warning(f"字段转换失败 {col}: {e}")

    return df

  def _normalize_query_time(self, value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
      return None
    if isinstance(value, date) and not isinstance(value, datetime):
      value = datetime.combine(value, time.min)
    return time_utils.to_utc(value)

  def _normalize_timestamp_series(self, series: pd.Series) -> pd.Series:
    series = pd.to_datetime(series, errors="coerce")
    tzinfo = time_utils.now_aware().tzinfo
    if series.dt.tz is None:
      series = series.dt.tz_localize(tzinfo)
    return series.dt.tz_convert("UTC")

  def _get_field_converters(self) -> Dict[str, AttributeConverter]:
    """获取字段转换器（缓存版本）"""
    if self._field_converters is not None:
      return self._field_converters

    self._field_converters = {}
    try:
      for f in dataclasses.fields(self.model_class):
        conv = f.metadata.get("converter")
        if isinstance(conv, AttributeConverter):
          self._field_converters[f.name] = conv
    except Exception:
      self._field_converters = {}
    return self._field_converters

  def _process_query_results(self, records: pd.DataFrame) -> pd.DataFrame:
    """处理查询结果的通用转换逻辑"""
    if records.empty:
      return records

    # 处理时间列为本地时间
    timestamp_column = self.model_class().get_timestamp_column()
    if timestamp_column in records.columns:
      records[timestamp_column] = pd.to_datetime(
        records[timestamp_column], utc=True
      ).dt.tz_convert("Asia/Shanghai")

    # 处理字段转换器
    _field_converters = self._get_field_converters()
    for col, converter in _field_converters.items():
      if col in records.columns:
        try:
          records = converter.convert_to_entity_attribute(records, col)
        except Exception as e:
          logger.warning(f"字段转换失败 {col}: {e}")

    return records

  def _bulk_dict_to_entities(self, data_list: pd.DataFrame) -> List[M]:
    """批量转换，进一步优化性能"""
    if data_list.empty:
      return []
    results = []
    for _, data in data_list.iterrows():
      results.append(self.model_class(**data))
    return results

  def find_last_minutes(
    self, minutes: int, filters: Optional[Dict[str, Any]] = None
  ) -> List[Dict[str, Any]]:
    """查询最近N分钟的数据"""
    end_time = time_utils.to_utc(time_utils.now_aware())
    start_time = end_time - timedelta(minutes=minutes)
    return self.find_by_time_range(start_time, end_time, filters)

  def find_last_hours(
    self, hours: int, filters: Optional[Dict[str, Any]] = None
  ) -> List[Dict[str, Any]]:
    """查询最近N小时的数据"""
    end_time = time_utils.to_utc(time_utils.now_aware())
    start_time = end_time - timedelta(hours=hours)
    return self.find_by_time_range(start_time, end_time, filters)

  def find_last_days(
    self, days: int, filters: Optional[Dict[str, Any]] = None
  ) -> List[Dict[str, Any]]:
    """查询最近N天的数据"""
    end_time = time_utils.to_utc(time_utils.now_aware())
    start_time = end_time - timedelta(days=days)
    return self.find_by_time_range(start_time, end_time, filters)

  def find_today(
    self, filters: Optional[Dict[str, Any]] = None
  ) -> List[Dict[str, Any]]:
    """查询今天的数据"""
    now_sh = time_utils.now_aware()
    start_time = time_utils.to_utc(
      now_sh.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    end_time = time_utils.to_utc(now_sh)
    return self.find_by_time_range(start_time, end_time, filters)

  def find_this_week(
    self, filters: Optional[Dict[str, Any]] = None
  ) -> List[Dict[str, Any]]:
    """查询本周的数据"""
    now_sh = time_utils.now_aware()
    start_time_sh = now_sh - timedelta(days=now_sh.weekday())
    start_time_sh = start_time_sh.replace(hour=0, minute=0, second=0, microsecond=0)
    start_time = time_utils.to_utc(start_time_sh)
    end_time = time_utils.to_utc(now_sh)
    return self.find_by_time_range(start_time, end_time, filters)

  def find_this_month(
    self, filters: Optional[Dict[str, Any]] = None
  ) -> List[Dict[str, Any]]:
    """查询本月的数据"""
    now_sh = time_utils.now_aware()
    start_time = time_utils.to_utc(
      now_sh.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    )
    end_time = time_utils.to_utc(now_sh)
    return self.find_by_time_range(start_time, end_time, filters)


# 导出所有公共接口
__all__ = [
  # 异常类
  "InfluxDBError",
  "ConnectionError",
  # 仓储类
  "BaseRepository",
]
