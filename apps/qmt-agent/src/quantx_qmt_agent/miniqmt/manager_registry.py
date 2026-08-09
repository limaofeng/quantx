"""
XTQuant 管理器注册表
提供交易管理器和数据管理器的复用实例管理
"""

import logging
import threading
import time
from typing import Any, Dict

from .data.data_manager import XTDataManager
from .trading.trading_manager import XTTradingManager

logger = logging.getLogger(__name__)


class XTDataManagerRegistry:
  """
  XTQuant 数据管理器注册表
  提供数据管理器的复用实例管理
  """

  _instance = None
  _lock = threading.RLock()

  def __new__(cls):
    if cls._instance is None:
      with cls._lock:
        if cls._instance is None:
          cls._instance = super().__new__(cls)
          cls._instance._managers = {}
          cls._instance._reconnect_interval = 5.0
          cls._instance._max_reconnect_attempts = 3
          cls._instance._connection_timeout = 30.0
          cls._instance._last_reconnect_attempts = {}
    return cls._instance

  def get_manager(self) -> XTDataManager:
    """
    获取数据管理器实例

    Returns:
        XTDataManager: 数据管理器实例
    """
    with self._lock:
      mgr = self._managers.get("default")
      if mgr is None:
        # 创建新的数据管理器实例
        mgr = XTDataManager()
        self._managers["default"] = mgr
        return mgr

    # 健康检查和重连逻辑
    if not self._is_manager_healthy(mgr):
      self._reconnect_manager(mgr)

    return mgr

  def _is_manager_healthy(self, manager: Any) -> bool:
    """
    检查数据管理器是否健康

    Args:
        manager: 数据管理器实例

    Returns:
        bool: 是否健康
    """
    try:
      # 基本连接状态检查
      if not getattr(manager, "is_connected", False):
        return False

      # 检查连接是否仍然有效（可以通过简单的查询来验证）
      return True

    except Exception:
      return False

  def _reconnect_manager(self, manager: Any) -> None:
    """
    重新连接数据管理器

    Args:
        manager: 数据管理器实例
    """
    with self._lock:
      for attempt in range(self._max_reconnect_attempts):
        try:
          # 尝试重新初始化连接
          init_fn = getattr(manager, "_init_connection", None)
          if callable(init_fn):
            init_fn()

            # 等待连接建立
            start_time = time.time()
            while time.time() - start_time < self._connection_timeout:
              if getattr(manager, "is_connected", False):
                print("XTQuant data manager reconnected successfully")
                return
              time.sleep(0.1)

          # 如果重连失败，创建新的实例
          print("Failed to reconnect XTQuant data manager, creating new instance")
          new_mgr = XTDataManager()
          self._managers["default"] = new_mgr
          return

        except Exception as e:
          print(f"Data manager reconnection attempt {attempt + 1} failed: {e}")
          if attempt < self._max_reconnect_attempts - 1:
            time.sleep(self._reconnect_interval)

      # 如果所有重连尝试都失败，移除失败的实例
      print("All data manager reconnection attempts failed, removing manager")
      self._managers.pop("default", None)

  def clear_manager(self) -> None:
    """清除数据管理器"""
    with self._lock:
      mgr = self._managers.pop("default", None)

    if mgr:
      # 尝试关闭连接
      close_fn = getattr(mgr, "close_connection", None)
      if callable(close_fn):
        try:
          close_fn()
        except Exception as e:
          print(f"Error closing data manager connection: {e}")

  def clear_all_managers(self) -> None:
    """清除所有数据管理器"""
    with self._lock:
      manager_keys = list(self._managers.keys())

    for manager_key in manager_keys:
      with self._lock:
        mgr = self._managers.pop(manager_key, None)

      if not mgr:
        continue

      close_fn = getattr(mgr, "close_connection", None)
      if callable(close_fn):
        try:
          close_fn()
        except Exception as e:
          print(f"Error closing data manager connection: {e}")

  def get_stats(self) -> Dict[str, int]:
    """
    获取数据管理器统计信息

    Returns:
        Dict[str, int]: 统计信息
    """
    with self._lock:
      total_managers = len(self._managers)
      connected_managers = sum(
        1 for mgr in self._managers.values() if getattr(mgr, "is_connected", False)
      )

    return {
      "total_data_managers": total_managers,
      "connected_data_managers": connected_managers,
      "disconnected_data_managers": total_managers - connected_managers,
    }


class XTTradingManagerRegistry:
  """
  XTQuant 交易管理器注册表
  提供按账户复用的交易管理器实例管理
  """

  _instance = None
  _lock = threading.RLock()

  def __new__(cls):
    if cls._instance is None:
      with cls._lock:
        if cls._instance is None:
          cls._instance = super().__new__(cls)
          cls._instance._managers = {}
          cls._instance._reconnect_interval = 5.0
          cls._instance._max_reconnect_attempts = 3
          cls._instance._connection_timeout = 30.0
    return cls._instance

  def get_manager(
    self, account_id: str, reconnect: bool = True
  ) -> XTTradingManager:
    """
    获取或创建交易管理器实例

    Args:
        account_id: 账户ID
        reconnect: 是否在已有管理器断开时主动重连

    Returns:
        XTTradingManager: 交易管理器实例
    """
    with self._lock:
      mgr = self._managers.get(account_id)
      if mgr is None:
        # 创建新的管理器实例
        mgr = XTTradingManager(account_id)
        self._managers[account_id] = mgr
        return mgr

    # 健康检查和重连逻辑。高频只读接口可选择跳过重连，避免在交易端
    # 不可用时把 GraphQL 请求线程池堆满。
    if reconnect and not self._is_manager_healthy(mgr):
      mgr = self._reconnect_manager(account_id, mgr)

    return mgr

  def _is_manager_healthy(self, manager: XTTradingManager) -> bool:
    """
    检查管理器是否健康

    Args:
        manager: 交易管理器实例

    Returns:
        bool: 是否健康
    """
    try:
      # 基本连接状态检查
      if not getattr(manager, "is_connected", False):
        return False

      # 检查会话是否仍然有效（可以通过简单的查询来验证）
      return True

    except Exception:
      return False

  def _reconnect_manager(
    self,
    account_id: str,
    manager: XTTradingManager,
  ) -> XTTradingManager:
    """
    重新连接管理器

    Args:
        account_id: 账户ID
        manager: 交易管理器实例
    """
    with self._lock:
      if not hasattr(self, "_last_reconnect_attempts"):
        self._last_reconnect_attempts = {}
      now = time.monotonic()
      last_attempt = float(
        self._last_reconnect_attempts.get(account_id, 0.0)
      )
      if now - last_attempt < self._reconnect_interval:
        return manager
      self._last_reconnect_attempts[account_id] = now

      try:
        reconnect = getattr(manager, "reconnect", None)
        if not callable(reconnect) or not reconnect():
          return manager
        self._last_reconnect_attempts.pop(account_id, None)
        logger.info("XTQuant交易连接已自动恢复: account=%s", account_id)
        return manager
      except Exception as exc:
        logger.warning(
          "XTQuant交易重连失败: account=%s error=%s: %s",
          account_id,
          exc.__class__.__name__,
          exc,
        )
        return manager

  def clear_manager(self, account_id: str) -> None:
    """
    清除指定账户的交易管理器

    Args:
        account_id: 账户ID
    """
    with self._lock:
      mgr = self._managers.pop(account_id, None)
      if hasattr(self, "_last_reconnect_attempts"):
        self._last_reconnect_attempts.pop(account_id, None)

    if mgr:
      # 尝试关闭连接
      close_fn = getattr(mgr, "close_connection", None)
      if callable(close_fn):
        try:
          close_fn()
        except Exception as e:
          print(f"Error closing connection for account {account_id}: {e}")

  def clear_all_managers(self) -> None:
    """清除所有交易管理器"""
    with self._lock:
      account_ids = list(self._managers.keys())

    for account_id in account_ids:
      self.clear_manager(account_id)

  def get_stats(self) -> Dict[str, int]:
    """
    获取管理器统计信息

    Returns:
        Dict[str, int]: 统计信息
    """
    with self._lock:
      total_managers = len(self._managers)
      connected_managers = sum(
        1 for mgr in self._managers.values() if getattr(mgr, "is_connected", False)
      )

    return {
      "total_managers": total_managers,
      "connected_managers": connected_managers,
      "disconnected_managers": total_managers - connected_managers,
    }
