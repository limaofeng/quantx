"""
策略注册器 - 自动发现和注册策略
"""

import hashlib
import importlib
import inspect
import json
import logging
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from core.strategies.base import StrategyBase
from models.enums import StrategyInstrumentScope, StrategyInstrumentUniverseMode
from core.utils import time_utils

logger = logging.getLogger(__name__)


@dataclass
class StrategyMetadata:
  """策略元数据"""

  name: str
  version: str
  description: str
  class_name: str
  file_path: str
  parameter_schema: Dict[str, Any]
  code_hash: str
  category: Optional[str] = None
  risk_level: Optional[str] = None
  instrument_scope: Optional[StrategyInstrumentScope] = None
  instrument_universe_mode: StrategyInstrumentUniverseMode = (
    StrategyInstrumentUniverseMode.STATIC
  )
  tags: Optional[List[str]] = None

  @property
  def module_path(self) -> str:
    """
    从文件路径动态计算模块路径

    例如: "core/strategies/rsi_strategy.py"
          -> "core.strategies.rsi_strategy"

    Returns:
        模���导入路径
    """
    return self.file_path.replace("/", ".").replace("\\", ".").replace(".py", "")


class StrategyRegistry:
  """策略注册器"""

  def __init__(self):
    self.strategies: Dict[str, StrategyMetadata] = {}
    self._logger = logging.getLogger(__name__)

  def discover_strategies(
    self, package_name: str = "core.strategies"
  ) -> List[StrategyMetadata]:
    """
    自动发现并注册所有策略

    Args:
        package_name: 策略包名

    Returns:
        发现的策略元数据列表
    """
    self._logger.info(f"开始扫描策略包: {package_name}")
    discovered = []

    try:
      # 导入包
      package = importlib.import_module(package_name)
      package_path = Path(package.__file__).parent

      # 遍历包中的所有模块
      for _, module_name, is_pkg in pkgutil.iter_modules([str(package_path)]):
        if is_pkg or module_name.startswith("_"):
          continue

        try:
          # 导入模块
          full_module_name = f"{package_name}.{module_name}"
          module = importlib.import_module(full_module_name)

          # 查找策略类
          for name, obj in inspect.getmembers(module, inspect.isclass):
            # 跳过导入的类和基类
            if obj.__module__ != full_module_name:
              continue
            if not issubclass(obj, StrategyBase) or obj is StrategyBase:
              continue

            # 提取策略元数据
            metadata = self._extract_metadata(obj, module_name, package_path)
            if metadata:
              self.strategies[metadata.class_name] = metadata
              discovered.append(metadata)
              self._logger.info(
                f"发现策略: {metadata.name} v{metadata.version} ({metadata.class_name})"
              )

        except Exception as e:
          self._logger.error(f"导入模块 {module_name} 失败: {e}")
          continue

    except Exception as e:
      self._logger.error(f"扫描策略包失败: {e}")

    self._logger.info(f"策略扫描完成,共发现 {len(discovered)} 个策略")
    return discovered

  def _extract_metadata(
    self, strategy_class: Type[StrategyBase], module_name: str, package_path: Path
  ) -> Optional[StrategyMetadata]:
    """
    提取策略元数据

    Args:
        strategy_class: 策略类
        module_name: 模块名
        package_path: 包路径

    Returns:
        策略元数据
    """
    try:
      # 创建临时实例以访问属性
      # 注意: 这里需要一个临时的context,但我们只是为了获取元数据
      # 实际实例化时会提供真实的context
      from datetime import datetime

      from core.strategies.base import StrategyContext, StrategyRunMode

      temp_context = StrategyContext(
        run_id="temp",
        mode=StrategyRunMode.BACKTEST,
        backtest_start_time=time_utils.now(),
        instruments=[],
        parameters={},
      )

      instance = strategy_class(temp_context)

      # 获取基本信息
      name = instance.name
      version = instance.version
      description = instance.description
      class_name = strategy_class.__name__

      # 获取参数schema（现在返回 ParameterSchema 对象）
      parameter_schema_obj = strategy_class.get_parameter_schema()

      # 计算代码哈希
      file_path = package_path / f"{module_name}.py"
      code_hash = self._calculate_file_hash(file_path)

      # 尝试从类属性获取分类和风险等级
      category = getattr(strategy_class, "CATEGORY", None)
      risk_level = getattr(strategy_class, "RISK_LEVEL", None)
      tags = getattr(strategy_class, "TAGS", None)
      instrument_scope = getattr(strategy_class, "INSTRUMENT_SCOPE", None)
      if isinstance(instrument_scope, str):
        try:
          instrument_scope = StrategyInstrumentScope(instrument_scope)
        except ValueError:
          instrument_scope = None
      instrument_universe_mode = getattr(
        strategy_class,
        "INSTRUMENT_UNIVERSE_MODE",
        StrategyInstrumentUniverseMode.STATIC,
      )
      if isinstance(instrument_universe_mode, str):
        try:
          instrument_universe_mode = StrategyInstrumentUniverseMode(
            instrument_universe_mode
          )
        except ValueError:
          instrument_universe_mode = StrategyInstrumentUniverseMode.STATIC

      return StrategyMetadata(
        name=name,
        version=version,
        description=description,
        class_name=class_name,
        file_path=f"core/strategies/{module_name}.py",
        parameter_schema=parameter_schema_obj.model_dump(),  # 转为字典供元数据存储
        code_hash=code_hash,
        category=category,
        risk_level=risk_level,
        instrument_scope=instrument_scope,
        instrument_universe_mode=instrument_universe_mode,
        tags=tags if isinstance(tags, list) else None,
      )

    except Exception as e:
      self._logger.error(f"提取策略元数据失败 {strategy_class.__name__}: {e}")
      return None

  def _calculate_file_hash(self, file_path: Path) -> str:
    """
    计算文件的SHA256哈希值

    Args:
        file_path: 文件路径

    Returns:
        哈希值
    """
    try:
      with open(file_path, "rb") as f:
        content = f.read()
        return hashlib.sha256(content).hexdigest()
    except Exception as e:
      self._logger.warning(f"计算文件哈希失败 {file_path}: {e}")
      return ""

  def get_strategy(self, class_name: str) -> Optional[StrategyMetadata]:
    """
    获取策略元数据

    Args:
        class_name: 策略类名

    Returns:
        策略元数据
    """
    return self.strategies.get(class_name)

  def get_all_strategies(self) -> List[StrategyMetadata]:
    """
    获取所有策略元数据

    Returns:
        策略元数据列表
    """
    return list(self.strategies.values())

  def get_strategy_class(
    self, class_name: str, file_path: Optional[str] = None
  ) -> Type[StrategyBase]:
    """
    动态加载策略类

    Args:
        class_name: 策略类名
        file_path: 策略文件路径（可选，如果提供则优先使用）

    Returns:
        策略类

    Raises:
        ValueError: 策略不存在或加载失败
    """
    try:
      # 优先使用注册表中的元数据
      metadata = self.strategies.get(class_name)

      if metadata:
        module_path = metadata.module_path
      elif file_path:
        # 如果没有元数据，尝试从 file_path 构建模块路径
        # 例如: "core/strategies/ma_cross.py" -> "core.strategies.ma_cross"
        module_path = file_path.replace("/", ".").replace("\\", ".").replace(".py", "")
      else:
        raise ValueError(f"策略不存在: {class_name}")

      # 动态导入模块
      module = importlib.import_module(module_path)

      # 获取策略类
      strategy_class = getattr(module, class_name)

      # 验证是否为 StrategyBase 子类
      if not issubclass(strategy_class, StrategyBase):
        raise ValueError(f"{class_name} 不是有效的策略类")

      self._logger.info(f"成功加载策略类: {class_name} from {module_path}")
      return strategy_class

    except ImportError as e:
      self._logger.error(f"导入策略模块失败 {class_name}: {e}")
      raise ValueError(f"无法加载策略 {class_name}: 模块导入失败") from e
    except AttributeError as e:
      self._logger.error(f"策略类不存在 {class_name}: {e}")
      raise ValueError(f"策略类 {class_name} 不存在") from e
    except Exception as e:
      self._logger.error(f"加载策略类失败 {class_name}: {e}")
      raise ValueError(f"加载策略失败: {e}") from e

  def to_dict_list(self) -> List[Dict[str, Any]]:
    """
    将所有策略转换为字典列表

    Returns:
        策略字典列表
    """
    return [
      {
        "name": s.name,
        "version": s.version,
        "description": s.description,
        "class_name": s.class_name,
        "file_path": s.file_path,
        "parameter_schema": json.dumps(s.parameter_schema, ensure_ascii=False),
        "code_hash": s.code_hash,
        "category": s.category,
        "risk_level": s.risk_level,
        "tags": json.dumps(s.tags, ensure_ascii=False) if s.tags else None,
      }
      for s in self.strategies.values()
    ]


# 全局注册器实例
strategy_registry = StrategyRegistry()
