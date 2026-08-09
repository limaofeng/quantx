"""Legacy infrastructure helpers that have not yet become dedicated adapters.

Core exports are loaded lazily so importing a low-level module such as
``core.config`` does not initialize optional integrations.
"""

__version__ = "1.0.0"

_LAZY_EXPORTS = {
  "COMMON_PARAMETER_SCHEMAS": ("quantx_infrastructure.core.config", "COMMON_PARAMETER_SCHEMAS"),
  "ParameterManager": ("quantx_infrastructure.core.config", "ParameterManager"),
  "parameter_manager": ("quantx_infrastructure.core.config", "parameter_manager"),
}


def __getattr__(name):
  if name not in _LAZY_EXPORTS:
    raise AttributeError(f"module 'core' has no attribute {name!r}")
  from importlib import import_module

  module_name, attr_name = _LAZY_EXPORTS[name]
  value = getattr(import_module(module_name), attr_name)
  globals()[name] = value
  return value


__all__ = [
  # 参数管理
  "ParameterManager",
  "parameter_manager",
  "COMMON_PARAMETER_SCHEMAS",
]
