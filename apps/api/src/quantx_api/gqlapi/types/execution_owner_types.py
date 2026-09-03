"""GraphQL projection of the shared execution-owner contract.

The durable execution owner is defined once in ``quantx_contracts``.  These
types only expose that contract to GraphQL; they intentionally do not infer an
owner from a strategy run, plan metadata, or an approval action.
"""

from __future__ import annotations

import strawberry
from quantx_contracts import (
  ExecutionEnvironment as ContractExecutionEnvironment,
)
from quantx_contracts import (
  ExecutionOwnerRef as ContractExecutionOwnerRef,
)
from quantx_contracts import (
  ExecutionOwnerType as ContractExecutionOwnerType,
)

# Decorate the contract enums in place so the Python and GraphQL layers share
# one value set and do not grow parallel, subtly different owner contracts.
ExecutionOwnerType = strawberry.enum(
  ContractExecutionOwnerType,
  name="ExecutionOwnerType",
  description="公共执行身份的唯一 owner 类型",
)
ExecutionEnvironment = strawberry.enum(
  ContractExecutionEnvironment,
  name="ExecutionEnvironment",
  description="公共执行事实所属的唯一环境",
)


@strawberry.type(description="公共执行身份引用")
class ExecutionOwnerRef:
  """GraphQL output for a server-resolved execution owner."""

  owner_type: ExecutionOwnerType
  owner_id: str

  @classmethod
  def from_contract(
    cls,
    value: ContractExecutionOwnerRef,
  ) -> "ExecutionOwnerRef":
    if not isinstance(value, ContractExecutionOwnerRef):
      raise TypeError("execution owner must be an ExecutionOwnerRef")
    return cls(owner_type=value.owner_type, owner_id=value.owner_id)

  def to_contract(self) -> ContractExecutionOwnerRef:
    return ContractExecutionOwnerRef(
      owner_type=self.owner_type,
      owner_id=self.owner_id,
    )


__all__ = [
  "ExecutionEnvironment",
  "ExecutionOwnerRef",
  "ExecutionOwnerType",
]
