"""
K线数据仓储实现
"""

import logging
from typing import Dict, List

from database.timeseries_base import BaseRepository
from models.tick import Tick

logger = logging.getLogger(__name__)


class TickRepository(BaseRepository[Tick]):
  """Tick数据仓储"""

  model_class = Tick
  measurement = "ticks"

  def get_full_tick(self, stock_codes: List[str]) -> Dict[str, Tick]:
    """
    获取完整的Tick数据 - 每只股票的最新记录


    """
    if not stock_codes:
      raise ValueError("未提供有效的股票代码")

    try:
      # 构建股票代码的IN条件
      codes_str = "', '".join(stock_codes)

      # 使用窗口函数查询每只股票的最新记录
      sql = f"""
        SELECT * FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY time DESC) as rn
          FROM {self.measurement}
          WHERE stock_code IN ('{codes_str}')
        ) WHERE rn = 1
      """

      print(sql)

      # 执行查询
      results = self.operations.query(sql, use_cache=True)

      if not results:
        logger.debug(f"未找到股票的Tick数据: {stock_codes}")
        return {}

      # 转换为DataFrame进行字段处理
      import pandas as pd

      df = pd.DataFrame(results)

      # 删除窗口函数添加的rn列
      if "rn" in df.columns:
        df = df.drop("rn", axis=1)

      # 使用基类的通用结果处理方法
      df = self._process_query_results(df)

      ticks = self._bulk_dict_to_entities(df)
      # 转换为Tick对象字典
      result_dict = {}
      for tick in ticks:
        result_dict[tick.stock_code] = tick

      logger.debug(f"成功获取{len(result_dict)}只股票的Tick数据")
      return result_dict

    except Exception as e:
      logger.error(f"获取Tick数据失败: {e}")
      return {}
