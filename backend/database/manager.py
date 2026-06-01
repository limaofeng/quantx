"""
数据库管理器
主要负责数据库初始化和时间序列数据管理
关系型数据库的 CRUD 操作请使用 repositories 层
"""

import logging
from typing import Dict

from .relational import create_tables
from .timeseries import (
  get_timeseries_connection,
  init_timeseries,
  shutdown_timeseries,
)

logger = logging.getLogger(__name__)


class DatabaseManager:
  """数据库管理器 - 专注于初始化"""

  def __init__(self):
    self.timeseries_connection = None

  async def initialize(self):
    """初始化数据库连接"""
    # 创建关系型数据库表
    try:
      await create_tables()
      logger.info("关系型数据库表创建成功")
    except Exception as e:
      logger.error(f"关系型数据库表创建失败: {e}")
      raise

    # 初始化时间序列数据库连接
    try:
      init_timeseries()
      self.timeseries_connection = get_timeseries_connection()
      if self.timeseries_connection:
        logger.info("时间序列数据库连接成功")
      else:
        logger.warning("时间序列数据库未配置")
    except Exception as e:
      logger.error(f"时间序列数据库初始化失败: {e}")

  def health_check(self) -> Dict[str, bool]:
    """健康检查"""
    return {
      "relational_db": True,  # SQLAlchemy总是可用的
      "timeseries_db": self.timeseries_connection.is_connected()
      if self.timeseries_connection
      else False,
    }

  async def shutdown(self):
    """关闭所有数据库连接"""
    # 关闭时间序列数据库
    shutdown_timeseries()
    logger.info("时间序列数据库连接已关闭")

    # 关闭关系型数据库
    from .relational_connection import close_database
    await close_database()


# 全局数据库管理器实例
db_manager = DatabaseManager()
