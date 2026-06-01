"""
策略参数管理系统 - 支持schema验证和多层参数合并
"""

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from jsonschema import ValidationError, validate


@dataclass
class ParameterSource:
  """参数源配置"""

  name: str
  priority: int  # 数值越高优先级越高
  data: Dict[str, Any] = field(default_factory=dict)


class ParameterManager:
  """参数管理器 - 处理多层参数合并和验证"""

  def __init__(self):
    self.sources: List[ParameterSource] = []
    self.schemas: Dict[str, Dict[str, Any]] = {}

  def add_source(self, name: str, priority: int, data: Dict[str, Any]) -> None:
    """添加参数源"""
    source = ParameterSource(name=name, priority=priority, data=data)
    self.sources.append(source)
    # 按优先级排序（高优先级在后）
    self.sources.sort(key=lambda s: s.priority)

  def load_from_file(self, file_path: Union[str, Path], priority: int = 1) -> None:
    """从文件加载参数"""
    file_path = Path(file_path)

    if not file_path.exists():
      raise FileNotFoundError(f"配置文件不存在: {file_path}")

    try:
      with open(file_path, "r", encoding="utf-8") as f:
        if file_path.suffix.lower() in [".yml", ".yaml"]:
          data = yaml.safe_load(f)
        elif file_path.suffix.lower() == ".json":
          data = json.load(f)
        else:
          raise ValueError(f"不支持的文件格式: {file_path.suffix}")

      self.add_source(str(file_path), priority, data or {})

    except Exception as e:
      raise ValueError(f"加载配置文件失败 {file_path}: {e}")

  def load_from_env(self, prefix: str = "STRATEGY_", priority: int = 3) -> None:
    """从环境变量加载参数"""
    env_data = {}

    for key, value in os.environ.items():
      if key.startswith(prefix):
        # 去掉前缀，转换为小写
        param_key = key[len(prefix) :].lower()

        # 尝试解析为JSON，否则作为字符串
        try:
          env_data[param_key] = json.loads(value)
        except json.JSONDecodeError:
          env_data[param_key] = value

    if env_data:
      self.add_source("environment", priority, env_data)

  def register_schema(self, strategy_name: str, schema: Dict[str, Any]) -> None:
    """注册策略参数schema"""
    self.schemas[strategy_name] = schema

  def merge_parameters(
    self, strategy_name: str, override_params: Optional[Dict[str, Any]] = None
  ) -> Dict[str, Any]:
    """
    合并参数（按优先级：override_params > 环境变量 > 配置文件 > 默认值）
    """
    merged = {}

    # 按优先级从低到高合并
    for source in self.sources:
      strategy_params = source.data.get(strategy_name, {})
      if isinstance(strategy_params, dict):
        merged = self._deep_merge(merged, strategy_params)

    # 最高优先级：运行时覆盖参数
    if override_params:
      merged = self._deep_merge(merged, override_params)

    return merged

  def validate_parameters(
    self, strategy_name: str, parameters: Dict[str, Any]
  ) -> Dict[str, Any]:
    """验证参数"""
    if strategy_name not in self.schemas:
      # 如果没有schema，返回原参数
      return parameters

    schema = self.schemas[strategy_name]

    try:
      validate(instance=parameters, schema=schema)
      return parameters
    except ValidationError as e:
      raise ValueError(f"参数验证失败 {strategy_name}: {e.message}")

  def get_parameter_template(self, strategy_name: str) -> Dict[str, Any]:
    """获取参数模板（基于schema生成）"""
    if strategy_name not in self.schemas:
      return {}

    schema = self.schemas[strategy_name]
    template = {}

    # 从schema的properties生成模板
    properties = schema.get("properties", {})

    for param_name, param_schema in properties.items():
      if "default" in param_schema:
        template[param_name] = param_schema["default"]
      elif param_schema.get("type") == "string":
        template[param_name] = ""
      elif param_schema.get("type") == "number":
        template[param_name] = 0.0
      elif param_schema.get("type") == "integer":
        template[param_name] = 0
      elif param_schema.get("type") == "boolean":
        template[param_name] = False
      elif param_schema.get("type") == "array":
        template[param_name] = []
      elif param_schema.get("type") == "object":
        template[param_name] = {}

    return template

  def _deep_merge(self, dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
    """深度合并字典"""
    result = deepcopy(dict1)

    for key, value in dict2.items():
      if key in result and isinstance(result[key], dict) and isinstance(value, dict):
        result[key] = self._deep_merge(result[key], value)
      else:
        result[key] = deepcopy(value)

    return result

  def save_parameters(
    self, file_path: Union[str, Path], strategy_name: str, parameters: Dict[str, Any]
  ) -> None:
    """保存参数到文件"""
    file_path = Path(file_path)

    # 读取现有配置
    existing_config = {}
    if file_path.exists():
      try:
        with open(file_path, "r", encoding="utf-8") as f:
          if file_path.suffix.lower() in [".yml", ".yaml"]:
            existing_config = yaml.safe_load(f) or {}
          elif file_path.suffix.lower() == ".json":
            existing_config = json.load(f)
      except Exception:
        pass  # 如果读取失败，使用空配置

    # 更新策略参数
    existing_config[strategy_name] = parameters

    # 保存配置
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(file_path, "w", encoding="utf-8") as f:
      if file_path.suffix.lower() in [".yml", ".yaml"]:
        yaml.dump(existing_config, f, default_flow_style=False, allow_unicode=True)
      elif file_path.suffix.lower() == ".json":
        json.dump(existing_config, f, indent=2, ensure_ascii=False)

  def get_all_parameters(self) -> Dict[str, Dict[str, Any]]:
    """获取所有策略的合并后参数"""
    all_strategies = set()

    # 收集所有策略名称
    for source in self.sources:
      all_strategies.update(source.data.keys())

    result = {}
    for strategy_name in all_strategies:
      if isinstance(strategy_name, str):
        result[strategy_name] = self.merge_parameters(strategy_name)

    return result

  def clear_sources(self) -> None:
    """清除所有参数源"""
    self.sources.clear()

  def get_statistics(self) -> Dict[str, Any]:
    """获取参数管理器统计信息"""
    return {
      "sources_count": len(self.sources),
      "schemas_count": len(self.schemas),
      "sources": [
        {
          "name": source.name,
          "priority": source.priority,
          "strategies_count": len(source.data),
        }
        for source in self.sources
      ],
    }


# 常用的参数schema定义
COMMON_PARAMETER_SCHEMAS = {
  "base_strategy": {
    "type": "object",
    "properties": {
      "initial_capital": {
        "type": "number",
        "minimum": 1000,
        "default": 1000000,
        "description": "初始资金",
      },
      "max_position_pct": {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "default": 0.1,
        "description": "最大持仓比例",
      },
      "risk_free_rate": {
        "type": "number",
        "minimum": 0,
        "default": 0.03,
        "description": "无风险利率",
      },
      "transaction_cost": {
        "type": "number",
        "minimum": 0,
        "default": 0.001,
        "description": "交易成本",
      },
    },
    "required": ["initial_capital"],
  },
  "ma_cross_strategy": {
    "type": "object",
    "properties": {
      "short_period": {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 5,
        "description": "短期均线周期",
      },
      "long_period": {
        "type": "integer",
        "minimum": 2,
        "maximum": 500,
        "default": 20,
        "description": "长期均线周期",
      },
      "ma_type": {
        "type": "string",
        "enum": ["SMA", "EMA", "WMA"],
        "default": "SMA",
        "description": "均线类型",
      },
    },
    "required": ["short_period", "long_period"],
    "additionalProperties": True,
  },
  "rsi_strategy": {
    "type": "object",
    "properties": {
      "rsi_period": {
        "type": "integer",
        "minimum": 2,
        "maximum": 50,
        "default": 14,
        "description": "RSI周期",
      },
      "oversold_level": {
        "type": "number",
        "minimum": 0,
        "maximum": 50,
        "default": 30,
        "description": "超卖水平",
      },
      "overbought_level": {
        "type": "number",
        "minimum": 50,
        "maximum": 100,
        "default": 70,
        "description": "超买水平",
      },
    },
    "required": ["rsi_period"],
    "additionalProperties": True,
  },
}


# 全局参数管理器实例
parameter_manager = ParameterManager()

# 注册常用schema
for name, schema in COMMON_PARAMETER_SCHEMAS.items():
  parameter_manager.register_schema(name, schema)
