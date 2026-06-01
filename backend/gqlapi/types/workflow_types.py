from datetime import datetime
from typing import List, Optional

import strawberry


@strawberry.type(description="任务运行状态")
class TaskRun:
  id: str = strawberry.field(description="任务ID")
  name: str = strawberry.field(description="任务名称")
  state: str = strawberry.field(description="任务状态")
  started_at: Optional[datetime] = strawberry.field(description="开始时间")
  finished_at: Optional[datetime] = strawberry.field(description="结束时间")
  total_run_time: Optional[float] = strawberry.field(description="总运行时间(秒)")
  task_inputs: Optional[str] = strawberry.field(description="任务输入")


@strawberry.type(description="日志行")
class LogLine:
  time: datetime = strawberry.field(description="时间戳")
  level: int = strawberry.field(description="日志级别")
  message: str = strawberry.field(description="日志内容")


@strawberry.type(description="Prefect流程运行状态")
class FlowRun:
  id: str = strawberry.field(description="运行ID")
  flow_name: str = strawberry.field(description="流程名称")
  state: str = strawberry.field(description="运行状态")
  expected_start_time: Optional[datetime] = strawberry.field(description="预期开始时间")
  created: Optional[datetime] = strawberry.field(description="创建时间")
  started_at: Optional[datetime] = strawberry.field(description="开始时间")
  finished_at: Optional[datetime] = strawberry.field(description="结束时间")
  total_run_time: Optional[float] = strawberry.field(description="总运行时间(秒)")
  parameters: Optional[str] = strawberry.field(description="运行参数")
  logs: List[str] = strawberry.field(description="简略日志(已弃用)", default_factory=list)
  # 新增字段用于详情页
  task_runs: List[TaskRun] = strawberry.field(description="任务运行列表", default_factory=list)
  detailed_logs: List[LogLine] = strawberry.field(description="详细日志", default_factory=list)






@strawberry.type(description="已部署的流程")
class DeploymentFlowRun:
  id: str = strawberry.field(description="部署ID")
  name: str = strawberry.field(description="部署名称")
  flow_name: str = strawberry.field(description="流程名称")
  description: Optional[str] = strawberry.field(description="详细描述")
  work_pool_name: Optional[str] = strawberry.field(description="工作池名称")
  work_queue_name: str = strawberry.field(description="工作队列名称")
  is_schedule_active: bool = strawberry.field(description="调度是否激活")
  next_run_time: Optional[datetime] = strawberry.field(description="下次运行时间")
  last_run_time: Optional[datetime] = strawberry.field(description="最后运行时间")
  status: Optional[str] = strawberry.field(description="当前状态 (由最近一次运行决定)")
  created: Optional[datetime] = strawberry.field(description="创建时间")
  updated: Optional[datetime] = strawberry.field(description="更新时间")

  @strawberry.field(description="该部署的运行记录")
  async def runs(self, limit: int = 10, offset: int = 0) -> List[FlowRun]:
    from ..resolvers.prefect import PrefectResolver
    return await PrefectResolver.list_flow_runs(deployment_id=self.id, limit=limit, offset=offset)






@strawberry.input(description="部署运行参数")
class DeploymentRunInput:
  deployment_id: str = strawberry.field(description="部署ID")
  parameters: Optional[str] = strawberry.field(description="运行参数(JSON字符串)")


@strawberry.type(description="分页的流程运行记录")
class PaginatedFlowRuns:
  total: int = strawberry.field(description="总记录数")
  items: List[FlowRun] = strawberry.field(description="当前页记录")
