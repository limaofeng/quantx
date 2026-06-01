"""
策略状态 Schema（独立结构）

用于定义策略运行时 state 的默认结构与默认值。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StateProperty:
  """状态字段定义"""

  type: str
  default: Any = None
  title: Optional[str] = None
  description: Optional[str] = None


@dataclass
class StateSchema:
  """状态 Schema 定义"""

  type: str = "object"
  properties: Dict[str, StateProperty] = field(default_factory=dict)
  required: List[str] = field(default_factory=list)

  def build_defaults(self) -> Dict[str, Any]:
    """生成默认状态字典"""
    defaults: Dict[str, Any] = {}
    for key, prop in (self.properties or {}).items():
      if prop.default is not None:
        defaults[key] = prop.default
    return defaults
