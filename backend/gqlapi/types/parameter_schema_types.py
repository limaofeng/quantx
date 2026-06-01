"""
ParameterSchema GraphQL 类型定义
"""

from typing import List, Optional

import strawberry

from models.parameter_schema import (
  ParameterProperty as ParameterPropertyModel,
)
from models.parameter_schema import (
  ParameterSchema as ParameterSchemaModel,
)


@strawberry.type(description="参数属性定义（支持递归嵌套）")
class ParameterProperty:
  """
  参数属性定义，对应 JSON Schema 的属性规范
  """

  type: str = strawberry.field(
    description="参数类型: integer, number, string, boolean, array, object"
  )

  # 嵌套结构支持
  properties: Optional[List["ParameterPropertyEntry"]] = strawberry.field(
    default=None, description="嵌套对象属性列表（type=object 时使用）"
  )
  items: Optional["ParameterProperty"] = strawberry.field(
    default=None, description="数组元素定义（type=array 时使用）"
  )
  required: Optional[List[str]] = strawberry.field(
    default=None, description="嵌套对象必填字段"
  )

  # 基础约束
  default: Optional[strawberry.scalars.JSON] = strawberry.field(
    default=None, description="默认值"
  )
  minimum: Optional[float] = strawberry.field(default=None, description="最小值")
  maximum: Optional[float] = strawberry.field(default=None, description="最大值")
  enum: Optional[List[str]] = strawberry.field(default=None, description="枚举值列表")

  # UI 扩展字段
  title: Optional[str] = strawberry.field(default=None, description="参数标题")
  description: Optional[str] = strawberry.field(default=None, description="参数描述")
  group: Optional[str] = strawberry.field(default=None, description="参数分组")
  unit: Optional[str] = strawberry.field(default=None, description="参数单位")
  step: Optional[float] = strawberry.field(default=None, description="数值步长")
  enum_descriptions: Optional[strawberry.scalars.JSON] = strawberry.field(
    default=None, description="枚举值说明（JSON 对象）"
  )
  widget: Optional[str] = strawberry.field(default=None, description="表单控件类型")
  placeholder: Optional[str] = strawberry.field(default=None, description="占位符文本")

  @staticmethod
  def from_pydantic(prop: ParameterPropertyModel) -> "ParameterProperty":
    """从 Pydantic 模型转换为 GraphQL 类型"""
    # 递归转换嵌套属性为键值对列表
    properties = None
    if prop.properties:
      # 需要前向引用 ParameterPropertyEntry
      from gqlapi.types.parameter_schema_types import ParameterPropertyEntry

      properties = [
        ParameterPropertyEntry(key=key, value=ParameterProperty.from_pydantic(value))
        for key, value in prop.properties.items()
      ]

    items = None
    if prop.items:
      items = ParameterProperty.from_pydantic(prop.items)

    return ParameterProperty(
      type=prop.type,
      properties=properties,
      items=items,
      required=prop.required,
      default=prop.default,
      minimum=prop.minimum,
      maximum=prop.maximum,
      enum=prop.enum,
      title=prop.title,
      description=prop.description,
      group=prop.group,
      unit=prop.unit,
      step=prop.step,
      enum_descriptions=prop.enumDescriptions,
      widget=prop.widget,
      placeholder=prop.placeholder,
    )


@strawberry.type(description="参数属性键值对")
class ParameterPropertyEntry:
  """
  参数属性的键值对表示（用于 GraphQL）
  """

  key: str = strawberry.field(description="参数名称")
  value: ParameterProperty = strawberry.field(description="参数属性定义")


@strawberry.type(description="参数 Schema 定义（GraphQL）")
class ParameterSchema:
  """
  参数 Schema 定义，对应 JSON Schema 规范
  """

  type: str = strawberry.field(description="Schema 类型（通常为 'object'）")

  properties: List[ParameterPropertyEntry] = strawberry.field(
    description="参数属性定义列表（支持嵌套对象和数组）"
  )

  required: List[str] = strawberry.field(
    default_factory=list, description="必填字段列表"
  )

  additional_properties: bool = strawberry.field(
    default=False, description="是否允许额外属性"
  )

  @staticmethod
  def from_pydantic(schema: ParameterSchemaModel) -> "ParameterSchema":
    """从 Pydantic 模型转换为 GraphQL 类型"""
    # 转换所有属性为键值对列表
    properties = [
      ParameterPropertyEntry(key=key, value=ParameterProperty.from_pydantic(value))
      for key, value in schema.properties.items()
    ]

    return ParameterSchema(
      type=schema.type,
      properties=properties,
      required=schema.required,
      additional_properties=schema.additionalProperties,
    )
