from typing import Any, Dict, List, Optional

import strawberry
from strawberry.scalars import JSON

from ..resolvers.prefect import PrefectResolver
from ..types import (
  DeploymentFlowRun,
  FlowRun,
  OperationResult,
  PaginatedFlowRuns,
)


@strawberry.type(description="工作流相关查询")
class WorkflowQuery:
  @strawberry.field(description="获取流程运行详情")
  async def flow_run(self, run_id: str) -> Optional[FlowRun]:
    return await PrefectResolver.get_flow_run(run_id)


  @strawberry.field(description="列出流程运行记录")
  async def flow_runs(
    self, 
    flow_name: Optional[str] = None, 
    flow_id: Optional[str] = None, 
    deployment_id: Optional[str] = None,
    limit: int = 20, 
    offset: int = 0
  ) -> PaginatedFlowRuns:
    return await PrefectResolver.list_flow_runs(flow_name, flow_id, deployment_id, limit, offset)

  @strawberry.field(description="列出所有已部署的流程")
  async def list_deployments(
    self, limit: int = 20, offset: int = 0
  ) -> List[DeploymentFlowRun]:
    return await PrefectResolver.list_deployments(limit, offset)

  @strawberry.field(description="根据名称获取部署详情")
  async def get_deployment_by_name(
    self, name: str
  ) -> Optional[DeploymentFlowRun]:
    return await PrefectResolver.get_deployment_by_name(name)

  @strawberry.field(description="根据ID获取部署详情")
  async def get_deployment_by_id(
    self, id: str
  ) -> Optional[DeploymentFlowRun]:
    return await PrefectResolver.get_deployment_by_id(id)


@strawberry.type(description="工作流相关变更")
class WorkflowMutation:

  @strawberry.field(description="运行部署")
  async def run_deployment(
    self,
    deployment_id: str,
    parameters: Optional[JSON] = None,
  ) -> FlowRun:
    params_dict: Optional[Dict[str, Any]] = parameters
    return await PrefectResolver.run_deployment(deployment_id, parameters=params_dict)

  @strawberry.field(description="取消流程运行")
  async def cancel_flow_run(self, run_id: str) -> OperationResult:
    return await PrefectResolver.cancel_flow_run(run_id)

  @strawberry.field(description="重试流程运行")
  async def retry_flow_run(self, run_id: str) -> FlowRun:
    return await PrefectResolver.retry_flow_run(run_id)

  @strawberry.field(description="启用或暂停部署的自动调度")
  async def set_deployment_schedule_active(
    self,
    deployment_id: str,
    active: bool,
  ) -> DeploymentFlowRun:
    return await PrefectResolver.set_deployment_schedule_active(
      deployment_id,
      active,
    )
