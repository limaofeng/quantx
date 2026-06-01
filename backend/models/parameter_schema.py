"""
参数 Schema 类型定义
提供类型安全的参数 schema 访问和验证
"""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field
from sqlalchemy import JSON, TypeDecorator


class ParameterProperty(BaseModel):
  """参数属性定义（支持递归嵌套）"""

  type: str = Field(
    ..., description="参数类型: integer, number, string, boolean, array, object"
  )

  # 嵌套结构支持
  properties: Optional[Dict[str, "ParameterProperty"]] = Field(
    None, description="嵌套对象属性（type=object 时使用）"
  )
  items: Optional["ParameterProperty"] = Field(
    None, description="数组元素定义（type=array 时使用）"
  )
  required: Optional[List[str]] = Field(None, description="嵌套对象必填字段")

  # 基础约束
  default: Optional[Any] = Field(None, description="默认值")
  minimum: Optional[Union[int, float]] = Field(None, description="最小值")
  maximum: Optional[Union[int, float]] = Field(None, description="最大值")
  enum: Optional[List[str]] = Field(None, description="枚举值列表")

  # UI 扩展字段
  title: Optional[str] = Field(None, description="参数标题")
  description: Optional[str] = Field(None, description="参数描述")
  group: Optional[str] = Field(None, description="参数分组")
  unit: Optional[str] = Field(None, description="参数单位")
  step: Optional[Union[int, float]] = Field(None, description="数值步长")
  enumDescriptions: Optional[Dict[str, str]] = Field(None, description="枚举值说明")
  widget: Optional[str] = Field(None, description="表单控件类型")
  placeholder: Optional[str] = Field(None, description="占位符文本")

  class Config:
    extra = "allow"  # 允许额外字段


class ParameterSchema(BaseModel):
  """参数 Schema 定义"""

  type: str = Field(default="object", description="Schema 类型")
  properties: Dict[str, ParameterProperty] = Field(
    default_factory=dict, description="参数属性定义"
  )
  required: List[str] = Field(default_factory=list, description="必填参数列表")
  additionalProperties: bool = Field(default=False, description="是否允许额外属性")

  class Config:
    extra = "allow"


class ParameterSchemaType(TypeDecorator):
  """自动序列化 ParameterSchema 的 SQLAlchemy 类型"""

  impl = JSON
  cache_ok = True

  def process_bind_param(self, value, dialect):
    """写入数据库：Pydantic → JSON"""
    if value is None:
      return None

    if isinstance(value, ParameterSchema):
      return value.model_dump(mode="json")

    if isinstance(value, dict):
      # 验证并转换
      schema = ParameterSchema(**value)
      return schema.model_dump(mode="json")

    raise TypeError(f"Expected ParameterSchema or dict, got {type(value)}")

  def process_result_value(self, value, dialect):
    """从数据库读取：JSON → Pydantic"""
    if value is None:
      return None

    return ParameterSchema(**value)


def extract_default_parameters(parameter_schema: Dict[str, Any]) -> Dict[str, Any]:
  """
  从 parameter_schema 中提取默认参数值

  Args:
      parameter_schema: JSON Schema 格式的参数定义

  Returns:
      默认参数字典
  """
  defaults = {}

  if not parameter_schema or not isinstance(parameter_schema, dict):
    return defaults

  properties = parameter_schema.get("properties", {})

  for param_name, param_def in properties.items():
    if isinstance(param_def, dict) and "default" in param_def:
      defaults[param_name] = param_def["default"]

  return defaults


def validate_parameters(
  parameters: Dict[str, Any], parameter_schema: Dict[str, Any]
) -> tuple[bool, Optional[str]]:
  """
  验证参数是否符合 schema 定义

  Args:
      parameters: 待验证的参数
      parameter_schema: JSON Schema 格式的参数定义

  Returns:
      (是否有效, 错误信息)
  """
  try:
    schema = ParameterSchema(**parameter_schema)

    # 检查必填参数
    for required_param in schema.required:
      if required_param not in parameters:
        return False, f"缺少必填参数: {required_param}"

    # 检查参数类型和范围
    for param_name, param_value in parameters.items():
      if param_name not in schema.properties:
        if not schema.additionalProperties:
          return False, f"不允许的参数: {param_name}"
        continue

      prop = schema.properties[param_name]

      # 类型检查
      if prop.type == "integer" and not isinstance(param_value, int):
        return False, f"参数 {param_name} 应为整数类型"
      elif prop.type == "number" and not isinstance(param_value, (int, float)):
        return False, f"参数 {param_name} 应为数值类型"
      elif prop.type == "string" and not isinstance(param_value, str):
        return False, f"参数 {param_name} 应为字符串类型"
      elif prop.type == "boolean" and not isinstance(param_value, bool):
        return False, f"参数 {param_name} 应为布尔类型"
      elif prop.type == "array" and not isinstance(param_value, list):
        return False, f"参数 {param_name} 应为数组类型"

      # 数值范围检查
      if prop.type in ("integer", "number"):
        if prop.minimum is not None and param_value < prop.minimum:
          return False, f"参数 {param_name} 不能小于 {prop.minimum}"
        if prop.maximum is not None and param_value > prop.maximum:
          return False, f"参数 {param_name} 不能大于 {prop.maximum}"

      # 枚举值检查
      if prop.enum and param_value not in prop.enum:
        return False, f"参数 {param_name} 的值必须是 {prop.enum} 之一"

    return True, None

  except Exception as e:
    return False, f"Schema 验证失败: {str(e)}"


def merge_parameters_with_defaults(
  parameters: Dict[str, Any], parameter_schema: Dict[str, Any]
) -> Dict[str, Any]:
  """
  合并用户参数和默认参数

  Args:
      parameters: 用户提供的参数
      parameter_schema: JSON Schema 格式的参数定义

  Returns:
      合并后的参数字典
  """
  defaults = extract_default_parameters(parameter_schema)
  return {**defaults, **parameters}
