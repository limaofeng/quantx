"""Strategy parameter schemas without SQLAlchemy serialization hooks."""

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class ParameterProperty(BaseModel):
  type: str
  properties: Optional[Dict[str, "ParameterProperty"]] = None
  items: Optional["ParameterProperty"] = None
  required: Optional[List[str]] = None
  default: Optional[Any] = None
  minimum: Optional[Union[int, float]] = None
  maximum: Optional[Union[int, float]] = None
  enum: Optional[List[str]] = None
  title: Optional[str] = None
  description: Optional[str] = None
  group: Optional[str] = None
  unit: Optional[str] = None
  step: Optional[Union[int, float]] = None
  enumDescriptions: Optional[Dict[str, str]] = None
  widget: Optional[str] = None
  placeholder: Optional[str] = None

  model_config = {"extra": "allow"}


class ParameterSchema(BaseModel):
  type: str = "object"
  properties: Dict[str, ParameterProperty] = Field(default_factory=dict)
  required: List[str] = Field(default_factory=list)
  additionalProperties: bool = False

  model_config = {"extra": "allow"}
