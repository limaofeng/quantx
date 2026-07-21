"""
Prefect流程相关的GraphQL解析器
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from prefector import PrefectManagerRegistry

from ..types import (
  DeploymentFlowRun,
  FlowRun,
  LogLine,
  OperationResult,
  PaginatedFlowRuns,
  TaskRun,
)

# 使用注册表获取管理器实例
prefect_registry = PrefectManagerRegistry()


def _convert_to_graphql_flow_run(prefect_flow_run) -> FlowRun:
  """转换prefector的FlowRun到GraphQL FlowRun"""
  task_runs = []
  if hasattr(prefect_flow_run, "task_runs") and prefect_flow_run.task_runs:
      task_runs = [
          TaskRun(
              id=t.id,
              name=t.name,
              state=t.state,
              started_at=t.started_at,
              finished_at=t.finished_at,
              total_run_time=t.total_run_time,
              task_inputs=t.task_inputs
          ) for t in prefect_flow_run.task_runs
      ]

  detailed_logs = []
  if hasattr(prefect_flow_run, "detailed_logs") and prefect_flow_run.detailed_logs:
      detailed_logs = [
          LogLine(
              time=l.timestamp,
              level=l.level,
              message=l.message
          ) for l in prefect_flow_run.detailed_logs
      ]

  return FlowRun(
    id=prefect_flow_run.id,
    flow_name=prefect_flow_run.flow_name,
    state=prefect_flow_run.state,
    expected_start_time=getattr(prefect_flow_run, 'expected_start_time', None),
    created=getattr(prefect_flow_run, 'created', None),
    started_at=prefect_flow_run.started_at,
    finished_at=prefect_flow_run.finished_at,
    total_run_time=prefect_flow_run.total_run_time,
    parameters=prefect_flow_run.parameters,
    logs=prefect_flow_run.logs,
    task_runs=task_runs,
    detailed_logs=detailed_logs
  )




def _convert_to_graphql_deployed_flow(prefect_deployment) -> DeploymentFlowRun:
  """转换prefector的部署信息到GraphQL DeploymentFlowRun"""
  created = None
  if isinstance(prefect_deployment.get("created"), str):
    try:
      created = datetime.fromisoformat(
        prefect_deployment["created"].replace("Z", "+00:00")
      )
    except (ValueError, TypeError):
      created = None
  elif isinstance(prefect_deployment.get("created"), datetime):
    created = prefect_deployment["created"]

  updated = None
  if isinstance(prefect_deployment.get("updated"), str):
    try:
      updated = datetime.fromisoformat(
        prefect_deployment["updated"].replace("Z", "+00:00")
      )
    except (ValueError, TypeError):
      updated = None
  elif isinstance(prefect_deployment.get("updated"), datetime):
    updated = prefect_deployment["updated"]

  next_run_time = None
  if isinstance(prefect_deployment.get("next_run_time"), str):
    try:
      next_run_time = datetime.fromisoformat(
        prefect_deployment["next_run_time"].replace("Z", "+00:00")
      )
    except (ValueError, TypeError):
      next_run_time = None
  elif isinstance(prefect_deployment.get("next_run_time"), datetime):
    next_run_time = prefect_deployment["next_run_time"]

  last_run_time = None
  if isinstance(prefect_deployment.get("last_run_time"), str):
    try:
      last_run_time = datetime.fromisoformat(
        prefect_deployment["last_run_time"].replace("Z", "+00:00")
      )
    except (ValueError, TypeError):
      last_run_time = None
  elif isinstance(prefect_deployment.get("last_run_time"), datetime):
    last_run_time = prefect_deployment["last_run_time"]

  latest_activity_time = None
  if isinstance(prefect_deployment.get("latest_activity_time"), str):
    try:
      latest_activity_time = datetime.fromisoformat(
        prefect_deployment["latest_activity_time"].replace("Z", "+00:00")
      )
    except (ValueError, TypeError):
      latest_activity_time = None
  elif isinstance(prefect_deployment.get("latest_activity_time"), datetime):
    latest_activity_time = prefect_deployment["latest_activity_time"]

  return DeploymentFlowRun(
    id=prefect_deployment.get("id", ""),
    name=prefect_deployment.get("name", ""),
    flow_name=prefect_deployment.get("flow_name", ""),
    description=prefect_deployment.get("description"),
    work_pool_name=prefect_deployment.get("work_pool_name"),
    work_queue_name=prefect_deployment.get("work_queue_name", ""),
    is_schedule_active=prefect_deployment.get("is_schedule_active", False),
    next_run_time=next_run_time,
    last_run_time=last_run_time,
    status=prefect_deployment.get("status"),
    active_run_id=prefect_deployment.get("active_run_id"),
    active_run_status=prefect_deployment.get("active_run_status"),
    is_stale=bool(prefect_deployment.get("is_stale", False)),
    stale_reason=prefect_deployment.get("stale_reason"),
    latest_activity_time=latest_activity_time,
    created=created,
    updated=updated,
  )




class PrefectResolver:
  """Prefect流程解析器"""


  @staticmethod
  async def run_deployment(
    id: str, parameters: Optional[Dict[str, Any]] = None
  ) -> FlowRun:
    """运行部署"""
    import logging
    logger = logging.getLogger(__name__)
    
    manager = prefect_registry.get_manager()
    result = await manager.run_deployment(id, parameters=parameters)
    logger.info(f"Flow run created: {result.id if hasattr(result, 'id') else 'unknown'}")
    
    # 发布 Pending 事件到 Redis，使订阅者能立即收到状态变更
    try:
      from database.redis_pubsub import redis_pubsub, get_deployment_channel
      from prefector.flow_deployment_manager import flow_deployment_registry
      
      # 使用 FlowDeploymentManager 获取 deployment 名称
      deployment_manager = flow_deployment_registry.get_manager()
      deployment = await deployment_manager.get_deployment_by_id(id)
      logger.info(f"Deployment info: {deployment}")
      if deployment:
        deployment_name = deployment.get("name", "")
        channel = get_deployment_channel(deployment_name)
        event = {
          "deployment_name": deployment_name,
          "status": "Pending",
          "flow_run_id": str(result.id) if hasattr(result, "id") else None,
          "message": f"Flow run created for deployment '{deployment_name}'",
        }
        logger.info(f"Publishing Pending event to channel: {channel}")
        count = await redis_pubsub.publish(channel, event)
        logger.info(f"Published Pending event, receivers: {count}")
      else:
        logger.warning(f"Deployment not found by id: {id}")
    except Exception as e:
      # 发布失败不影响主流程
      logger.error(f"Failed to publish Pending event: {e}", exc_info=True)
    
    return _convert_to_graphql_flow_run(result)

  @staticmethod
  async def cancel_flow_run(run_id: str) -> OperationResult:
    """取消流程运行"""
    manager = prefect_registry.get_manager()
    success = await manager.cancel_flow_run(run_id)
    return OperationResult(
      success=success,
      message=f"Flow run {run_id} {'cancelled' if success else 'failed to cancel'}",
      data=run_id if success else None,
    )

  @staticmethod
  async def retry_flow_run(run_id: str) -> FlowRun:
    """重试流程运行"""
    manager = prefect_registry.get_manager()
    result = await manager.retry_flow_run(run_id)
    return _convert_to_graphql_flow_run(result)

  @staticmethod
  async def set_deployment_schedule_active(
    id: str, active: bool
  ) -> DeploymentFlowRun:
    """启用或暂停部署的自动调度。"""
    from prefector.flow_deployment_manager import flow_deployment_registry

    deployment_manager = flow_deployment_registry.get_manager()
    result = await deployment_manager.set_deployment_schedule_active(id, active)

    try:
      from database.redis_pubsub import redis_pubsub, get_deployment_channel

      deployment_name = result.get("name", "")
      if deployment_name:
        await redis_pubsub.publish(
          get_deployment_channel(deployment_name),
          {
            "deployment_name": deployment_name,
            "schedule_active": active,
            "message": f"Deployment schedule {'resumed' if active else 'paused'}",
          },
        )
    except Exception:
      # 订阅刷新失败不影响调度状态变更本身。
      pass

    return _convert_to_graphql_deployed_flow(result)

  @staticmethod
  async def get_flow_run(run_id: str) -> Optional[FlowRun]:
    """获取流程运行详情"""
    manager = prefect_registry.get_manager()
    result = await manager.get_flow_run(run_id)
    
    if result:
        # 获取详细信息
        task_runs = await manager.get_task_runs(result.id)
        logs = await manager.get_flow_run_logs(result.id)
        
        # 填充到结果对象中
        result.task_runs = task_runs
        result.detailed_logs = logs
        
    return _convert_to_graphql_flow_run(result) if result else None

    
  @staticmethod
  async def list_flow_runs(
    flow_name: Optional[str] = None, 
    flow_id: Optional[str] = None,
    deployment_id: Optional[str] = None,
    limit: int = 20, 
    offset: int = 0
  ) -> PaginatedFlowRuns:
    """列出流程运行记录"""
    manager = prefect_registry.get_manager()
    
    # 获取总数
    total = await manager.count_flow_runs(flow_name, flow_id, deployment_id)
    
    # 获取列表
    results = await manager.list_flow_runs(flow_name, flow_id, deployment_id, limit, offset)
    
    return PaginatedFlowRuns(
        total=total,
        items=[_convert_to_graphql_flow_run(result) for result in results]
    )


  @staticmethod
  async def list_deployments(
    limit: int = 20, offset: int = 0
  ) -> List[DeploymentFlowRun]:
    """列出所有已部署的流程"""
    from prefector.flow_deployment_manager import flow_deployment_registry

    deployment_manager = flow_deployment_registry.get_manager()
    results = await deployment_manager.list_deployments(limit, offset)

    return [_convert_to_graphql_deployed_flow(result) for result in results]

  @staticmethod
  async def get_deployment_by_name(name: str) -> Optional[DeploymentFlowRun]:
    """根据名称获取部署详情"""
    from prefector.flow_deployment_manager import flow_deployment_registry

    deployment_manager = flow_deployment_registry.get_manager()
    result = await deployment_manager.get_deployment_by_name(name)

    return _convert_to_graphql_deployed_flow(result) if result else None

  @staticmethod
  async def get_deployment_by_id(id: str) -> Optional[DeploymentFlowRun]:
    """根据ID获取部署详情"""
    from prefector.flow_deployment_manager import flow_deployment_registry

    deployment_manager = flow_deployment_registry.get_manager()
    result = await deployment_manager.get_deployment_by_id(id)

    return _convert_to_graphql_deployed_flow(result) if result else None
