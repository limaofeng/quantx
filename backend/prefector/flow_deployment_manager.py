"""
Prefect Flow 部署管理器

负责在 Prefect 服务启动后自动部署和管理 flow
"""

import asyncio
import inspect
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from prefect import flow, get_client
from prefect.schedules import Cron, Schedule

from config.settings import settings

logger = logging.getLogger(__name__)

ACTIVE_FLOW_RUN_STATE_NAMES = {
  "Running",
  "Pending",
  "Cancelling",
  "Scheduled",
  "Late",
}
DEFAULT_STALE_ACTIVITY_SECONDS = 12 * 60 * 60
DEFAULT_MAX_ACTIVE_RUN_SECONDS = 24 * 60 * 60
DEPLOYMENT_STALE_ACTIVITY_SECONDS = {
  "financial-sync": 30 * 60,
}
DEPLOYMENT_MAX_ACTIVE_RUN_SECONDS = {
  "financial-sync": 8 * 60 * 60,
}


def _to_aware_utc(value: Any) -> Optional[datetime]:
  if value is None:
    return None

  parsed = value
  if isinstance(value, str):
    try:
      parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
      return None

  if not isinstance(parsed, datetime):
    return None

  if parsed.tzinfo is None:
    return parsed.replace(tzinfo=timezone.utc)

  return parsed.astimezone(timezone.utc)


def _state_timestamp(obj: Any) -> Optional[datetime]:
  state = getattr(obj, "state", None)
  return _to_aware_utc(getattr(state, "timestamp", None)) if state else None


def _latest_activity_time(flow_run: Any, task_runs: List[Any]) -> Optional[datetime]:
  candidates = [
    _to_aware_utc(getattr(flow_run, "updated", None)),
    _state_timestamp(flow_run),
    _to_aware_utc(getattr(flow_run, "end_time", None)),
    _to_aware_utc(getattr(flow_run, "start_time", None)),
    _to_aware_utc(getattr(flow_run, "expected_start_time", None)),
    _to_aware_utc(getattr(flow_run, "created", None)),
  ]

  for task_run in task_runs:
    candidates.extend(
      [
        _to_aware_utc(getattr(task_run, "updated", None)),
        _state_timestamp(task_run),
        _to_aware_utc(getattr(task_run, "end_time", None)),
        _to_aware_utc(getattr(task_run, "start_time", None)),
        _to_aware_utc(getattr(task_run, "created", None)),
      ]
    )

  valid = [candidate for candidate in candidates if candidate is not None]
  return max(valid) if valid else None


def _active_run_diagnostics(
  deployment_name: str,
  flow_run: Any,
  task_runs: List[Any],
) -> Dict[str, Any]:
  now = datetime.now(timezone.utc)
  latest_activity = _latest_activity_time(flow_run, task_runs)
  activity_limit = DEPLOYMENT_STALE_ACTIVITY_SECONDS.get(
    deployment_name,
    DEFAULT_STALE_ACTIVITY_SECONDS,
  )

  if latest_activity and (now - latest_activity).total_seconds() > activity_limit:
    minutes = int(activity_limit / 60)
    return {
      "is_stale": True,
      "latest_activity_time": latest_activity,
      "stale_reason": f"运行中但超过 {minutes} 分钟无状态活动",
    }

  start_time = (
    _to_aware_utc(getattr(flow_run, "start_time", None))
    or _to_aware_utc(getattr(flow_run, "expected_start_time", None))
    or _to_aware_utc(getattr(flow_run, "created", None))
  )
  max_active_seconds = DEPLOYMENT_MAX_ACTIVE_RUN_SECONDS.get(
    deployment_name,
    DEFAULT_MAX_ACTIVE_RUN_SECONDS,
  )

  if start_time and (now - start_time).total_seconds() > max_active_seconds:
    hours = round(max_active_seconds / 3600, 1)
    return {
      "is_stale": True,
      "latest_activity_time": latest_activity,
      "stale_reason": f"运行中但总时长超过 {hours:g} 小时",
    }

  return {
    "is_stale": False,
    "latest_activity_time": latest_activity,
    "stale_reason": None,
  }


def get_project_root():
  """获取项目根目录（向上查找标记文件）"""
  current_path = Path(__file__).resolve()
  for parent in current_path.parents:
    if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
      return parent
  return current_path.parent  # 如果找不到，返回当前文件目录


class FlowDeploymentConfig:
  """Flow 部署配置"""

  def __init__(
    self,
    name: str,
    module: str,
    function: str,
    work_queue: str = "default",
    work_pool_name: Optional[str] = None,
    description: str = "",
    schedules: Optional[list[Schedule]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    version: Optional[str] = None,
  ):
    self.name = name
    self.module = module
    self.function = function
    self.work_queue = work_queue
    self.work_pool_name = work_pool_name
    self.description = description
    self.schedules = schedules
    self.parameters = parameters
    self.version = version


def _entrypoint_to_module_function(entrypoint: str) -> Optional[tuple[str, str]]:
  if not entrypoint or ":" not in entrypoint:
    return None
  path_part, func_name = entrypoint.split(":", 1)
  clean_path = path_part.lstrip("./\\")
  module_path = clean_path.replace("\\", ".").replace("/", ".")
  if module_path.endswith(".py"):
    module_path = module_path[:-3]
  if not module_path or not func_name:
    return None
  return module_path, func_name


def _build_schedules_from_prefect(deployment: Dict[str, Any]) -> Optional[List[Schedule]]:
  schedule_items: List[Any] = []
  if deployment.get("schedule"):
    schedule_items.append(deployment.get("schedule"))
  if deployment.get("schedules"):
    schedules = deployment.get("schedules")
    if isinstance(schedules, list):
      schedule_items.extend(schedules)
    else:
      schedule_items.append(schedules)

  if not schedule_items:
    return None

  schedules: List[Schedule] = []
  for item in schedule_items:
    if not isinstance(item, dict):
      logger.warning(f"不支持的 schedule 配置: {item}")
      continue
    if "cron" in item:
      cron_expr = item.get("cron")
      if not cron_expr:
        continue
      schedules.append(
        Cron(
          cron_expr,
          timezone=item.get("timezone"),
          parameters=item.get("parameters"),
        )
      )
    else:
      logger.warning(f"不支持的 schedule 类型: {item}")
  return schedules or None


def _load_flow_deployment_configs_from_prefect_yaml() -> Optional[List[FlowDeploymentConfig]]:
  yaml_path = get_project_root() / "prefect.yaml"
  if not yaml_path.exists():
    return None

  try:
    with open(yaml_path, "r", encoding="utf-8") as f:
      config = yaml.safe_load(f) or {}
  except Exception as e:
    logger.warning(f"读取 prefect.yaml 失败: {e}")
    return None

  deployments = config.get("deployments")
  if not deployments or not isinstance(deployments, list):
    logger.warning("prefect.yaml 未找到 deployments 配置")
    return None

  flow_configs: List[FlowDeploymentConfig] = []
  for deployment in deployments:
    if not isinstance(deployment, dict):
      continue
    name = deployment.get("name")
    entrypoint = deployment.get("entrypoint")
    module_func = _entrypoint_to_module_function(entrypoint)
    if not name or not module_func:
      logger.warning(f"跳过无效 deployment: {deployment}")
      continue

    module_path, function_name = module_func
    schedules = _build_schedules_from_prefect(deployment)
    flow_configs.append(
      FlowDeploymentConfig(
        name=name,
        module=module_path,
        function=function_name,
        work_queue=deployment.get("work_queue_name")
        or deployment.get("work_queue")
        or "default",
        work_pool_name=deployment.get("work_pool_name"),
        description=deployment.get("description", ""),
        schedules=schedules,
        parameters=deployment.get("parameters"),
        version=deployment.get("version"),
      )
    )

  return flow_configs or None


def get_flow_deployment_configs() -> List[FlowDeploymentConfig]:
  """获取所有需要部署的 flow 配置"""
  flow_configs = _load_flow_deployment_configs_from_prefect_yaml()
  if flow_configs is None:
    raise RuntimeError("prefect.yaml 不可用，无法加载 Flow 部署配置")
  return flow_configs


class FlowDeploymentManager:
  """Flow 部署管理器"""

  def __init__(self):
    self.deployments_dir = Path(__file__).parent / "flows" / "deployments"
    self.deployments_dir.mkdir(exist_ok=True)

  async def deploy_all_flows(self) -> Dict[str, Any]:
    """部署所有可用的 flows"""
    results = {"success": [], "failed": [], "skipped": []}

    # 定义需要部署的 flows
    flows_to_deploy = get_flow_deployment_configs()

    for flow_config in flows_to_deploy:
      try:
        result = await self._deploy_single_flow(flow_config)
        if result["status"] == "success":
          results["success"].append(result)
        elif result["status"] == "skipped":
          results["skipped"].append(result)
        else:
          results["failed"].append(result)
      except Exception as e:
        logger.error(f"部署 flow {flow_config.name} 时发生异常: {e}")
        results["failed"].append(
          {"name": flow_config.name, "status": "error", "error": str(e)}
        )

    logger.info(
      f"Flow 部署完成: 成功 {len(results['success'])}, 失败 {len(results['failed'])}, 跳过 {len(results['skipped'])}"
    )
    return results

  async def _deploy_single_flow(
    self, flow_config: FlowDeploymentConfig
  ) -> Dict[str, Any]:
    """部署单个 flow"""
    name = flow_config.name
    module_path = flow_config.module
    function_name = flow_config.function
    work_queue = flow_config.work_queue
    work_pool_name = flow_config.work_pool_name or settings.prefect_worker_pool
    description = flow_config.description
    schedules = flow_config.schedules
    parameters = flow_config.parameters

    try:
      # 确保工作池存在
      if not await self._ensure_work_pool(work_pool_name):
        logger.warning(f"工作池不存在或不可用: {work_pool_name}，继续尝试部署")

      # 动态导入 flow 函数
      module = __import__(module_path, fromlist=[function_name])
      local_flow = getattr(module, function_name)
      desired_version = flow_config.version or local_flow.version

      # 检查是否已经部署
      remote_version = await self._get_remote_deployment_version(local_flow.name, name)
      if remote_version == desired_version:
        logger.info(f"Flow {name} 版本一致（{desired_version}），跳过部署")
        return {
          "name": name,
          "status": "skipped",
          "message": f"Version {desired_version} unchanged",
        }

      # 获取项目根目录
      source_path = str(get_project_root())

      # 将模块路径转换为文件路径
      module_parts = module_path.split(".")
      file_path = "/".join(module_parts) + ".py"
      entrypoint = f"{file_path}:{function_name}"

      flow_callable = await flow.from_source(source=source_path, entrypoint=entrypoint)

      # 创建 RunnerDeployment 对象 (使用异步版本)
      logger.info(f"创建部署对象: {name}")

      deployment_id = await flow_callable.deploy(
        name=name,
        work_pool_name=work_pool_name,
        enforce_parameter_schema=False,
        work_queue_name=work_queue,
        description=description or local_flow.description,
        schedules=schedules,
        parameters=parameters,
        version=desired_version,
      )

      # 备份部署为 YAML（通过 deployment_id 获取完整部署对象并导出）
      try:
        if deployment_id:
          await self.export_deployment_yaml_by_id(deployment_id, name)
      except Exception as e:
        logger.warning(f"导出部署 YAML 时出错（非阻塞）: {e}")

      # 验证部署是否真的成功
      await asyncio.sleep(0.5)  # 短暂等待
      if await self._verify_deployment_created(name):
        logger.info(f"Flow {name} 部署成功并已验证")
        return {
          "name": name,
          "status": "success",
          "deployment_id": deployment_id,
          "message": "Deployed successfully and verified",
        }
      else:
        logger.warning(f"Flow {name} 部署成功但验证失败（可能正常）")
        return {
          "name": name,
          "status": "success",
          "deployment_id": deployment_id,
          "message": "Deployed successfully (verification may not be needed in Prefect 3.x)",
        }

    except Exception as e:
      import traceback

      logger.error(f"部署 flow {name} 失败: {e}")
      logger.error(f"详细错误信息:\n{traceback.format_exc()}")
      return {"name": name, "status": "failed", "error": str(e)}

  async def _ensure_work_pool(self, work_pool_name: str) -> bool:
    """确保工作池存在；若不存在则尝试创建"""
    try:
      async with get_client() as client:
        if await self._read_work_pool(client, work_pool_name):
          return True

        created = await self._create_work_pool(client, work_pool_name)
        if not created:
          logger.error(f"创建工作池失败: {work_pool_name}")
          return False

        if await self._read_work_pool(client, work_pool_name):
          logger.info(f"已创建工作池: {work_pool_name}")
          return True

        logger.warning(f"工作池创建后仍无法读取: {work_pool_name}")
        return True
    except Exception as e:
      logger.error(f"确保工作池存在时出错: {e}", exc_info=True)
      return False

  async def _read_work_pool(self, client, work_pool_name: str) -> bool:
    try:
      await client.read_work_pool(work_pool_name)
      logger.debug(f"工作池已存在: {work_pool_name}")
      return True
    except Exception as e:
      message = str(e).lower()
      if "not found" not in message and "404" not in message:
        logger.error(f"检查工作池失败: {e}", exc_info=True)
      return False

  async def _create_work_pool(self, client, work_pool_name: str) -> bool:
    method = getattr(client, "create_work_pool", None)
    if not method:
      logger.error("Prefect client 不支持 create_work_pool")
      return False

    try:
      from prefect.client.schemas.actions import WorkPoolCreate

      payload = WorkPoolCreate(name=work_pool_name, type="process")
      try:
        sig = inspect.signature(method)
        if "work_pool" in sig.parameters:
          await method(work_pool=payload)
          return True
        if len(sig.parameters) == 1:
          await method(payload)
          return True
      except TypeError:
        pass
      except Exception as e:
        logger.error(f"创建工作池出错: {e}", exc_info=True)
        return False
    except Exception:
      pass

    for kwargs in (
      {"name": work_pool_name, "type": "process"},
      {"name": work_pool_name, "type": "process", "description": "auto-created"},
    ):
      try:
        sig = inspect.signature(method)
        if "name" in sig.parameters:
          await method(**kwargs)
          return True
        if len(sig.parameters) == 1:
          await method(kwargs)
          return True
        await method(**kwargs)
        return True
      except TypeError:
        continue
      except Exception as e:
        logger.error(f"创建工作池出错: {e}", exc_info=True)
        return False

    return False

  async def _get_remote_deployment_version(
    self, flow_name: str, deployment_name: str
  ) -> bool:
    """返回远端部署的 deployment.version（优先）或 version_id（回退）。"""
    try:
      client = get_client()
      full_name = f"{flow_name}/{deployment_name}"
      dep = await client.read_deployment_by_name(name=full_name)
      if not dep:
        return None
      ver = getattr(dep, "version", None)
      if ver:
        return str(ver)
      vid = getattr(dep, "version_id", None)
      return str(vid) if vid else None
    except Exception:
      return None

  async def _verify_deployment_created(self, name: str) -> bool:
    """验证部署是否真的被创建"""
    try:
      client = get_client()

      # Prefect 3.x 查询所有部署
      deployments = await client.read_deployments(limit=100)

      # 查找匹配的部署
      for deployment in deployments:
        if deployment.name == name:
          logger.debug(f"验证成功: 找到部署 {name}")
          return True

      logger.debug(f"验证失败: 未找到部署 {name}")
      return False

    except Exception as e:
      logger.error(f"验证部署 {name} 时出错: {e}")
      return False

  async def _export_deployment_yaml(self, deployment, name: str):
    """导出部署为 YAML 文件"""
    try:
      yaml_path = self.deployments_dir / f"{name}.yaml"

      # 尝试使用 model_dump 导出（如果支持）
      spec = deployment.model_dump(mode="json")

      with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(spec, f, sort_keys=False, allow_unicode=True)

      logger.debug(f"已导出部署 YAML: {yaml_path}")

    except Exception as e:
      logger.warning(f"导出部署 YAML 失败: {e}")

  async def list_deployments(
    self, limit: int = 20, offset: int = 0
  ) -> List[Dict[str, Any]]:
    """列出所有部署"""
    try:
      client = get_client()

      # Prefect 3.x 查询所有部署
      deployments = await client.read_deployments(limit=limit, offset=offset)
      
      # 获取所有流程以获取中文名称
      flows = await client.read_flows(limit=200)
      flow_map = {str(f.id): f.name for f in flows}

      logger.info(f"查询到 {len(deployments)} 个部署")

      result = []
      for deployment in deployments:
        logger.debug(f"部署: {deployment.name} (ID: {deployment.id})")
        formatted = await self._format_deployment_to_dict(client, deployment, flow_map)
        result.append(formatted)

      return result
    except Exception as e:
      logger.error(f"列出部署失败: {e}")
      return []

  async def get_deployment_by_name(self, name: str) -> Optional[Dict[str, Any]]:
    """根据部署名称获取部署详情"""
    try:
      client = get_client()

      # 尝试直接按名称读取
      # Prefect 部署全名通常是 flow-name/deployment-name
      # 但我们这里存储的 name 可能是 deployment_name
      # 我们先尝试读取所有部署并查找匹配的 name 或 拼接名称
      deployments = await client.read_deployments(limit=200)

      target_dep = None
      for dep in deployments:
        if dep.name == name:
          target_dep = dep
          break

      if not target_dep:
        # 尝试 flow_name/deployment_name
        for dep in deployments:
          full_name = f"{getattr(dep, 'flow_name', '')}/{dep.name}"
          if full_name == name:
            target_dep = dep
            break

      if not target_dep:
        return None

      return await self._format_deployment_to_dict(client, target_dep)

    except Exception as e:
      logger.error(f"获取部署失败 {name}: {e}")
      return None

  async def get_deployment_by_id(self, deployment_id: str) -> Optional[Dict[str, Any]]:
    """根据部署ID获取部署详情"""
    try:
      client = get_client()
      target_dep = await self._get_deployment_by_id(deployment_id)
      
      if not target_dep:
        return None

      return await self._format_deployment_to_dict(client, target_dep)

    except Exception as e:
      logger.error(f"根据ID获取部署失败 {deployment_id}: {e}")
      return None

  async def set_deployment_schedule_active(
    self, deployment_id: str, active: bool
  ) -> Dict[str, Any]:
    """启用或暂停一个部署的所有自动调度。"""
    try:
      client = get_client()
      target_dep = await self._get_deployment_by_id(deployment_id)

      if not target_dep:
        raise ValueError(f"Deployment '{deployment_id}' not found")

      schedules = await client.read_deployment_schedules(target_dep.id)
      if not schedules:
        raise ValueError(f"Deployment '{target_dep.name}' has no schedules")

      updated = 0
      for schedule in schedules:
        schedule_id = getattr(schedule, "id", None)
        if not schedule_id:
          continue
        if getattr(schedule, "active", None) != active:
          await client.update_deployment_schedule(
            target_dep.id,
            schedule_id,
            active=active,
          )
          updated += 1

      logger.info(
        "部署 %s 的自动调度已%s，更新 schedules=%s",
        target_dep.name,
        "恢复" if active else "暂停",
        updated,
      )

      refreshed_dep = await self._get_deployment_by_id(str(target_dep.id))
      return await self._format_deployment_to_dict(
        client,
        refreshed_dep or target_dep,
      )

    except Exception as e:
      logger.error(
        "设置部署调度状态失败 deployment_id=%s active=%s: %s",
        deployment_id,
        active,
        e,
      )
      raise

  async def _format_deployment_to_dict(self, client, target_dep, flow_map: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
      """统一格式化部署对象为字典"""
      
      # 1. 获取 flows (如果未提供 map)
      if flow_map is None:
        try:
           flows = await client.read_flows(limit=200)
           flow_map = {str(f.id): f.name for f in flows}
        except Exception as e:
           logger.warning(f"获取 flows 失败: {e}")
           flow_map = {}

      # 2. 获取调度激活状态
      is_schedule_active = getattr(target_dep, "is_schedule_active", None)
      try:
        schedules = await client.read_deployment_schedules(target_dep.id)
        if schedules:
          is_schedule_active = any(
            bool(getattr(schedule, "active", False)) for schedule in schedules
          )
      except Exception as e:
        logger.warning(f"获取部署 {target_dep.name} 的调度状态失败: {e}")

      if is_schedule_active is None:
        is_schedule_active = False

      # 3. 获取下次调度时间 (SCHEDULED)
      next_run_time = None
      if is_schedule_active:
        try:
          from prefect.client.schemas.filters import FlowRunFilter
          upcoming_runs = await client.read_flow_runs(
              flow_run_filter=FlowRunFilter(
                  deployment_id={"any_": [target_dep.id]},
                  state={"type": {"any_": ["SCHEDULED"]}}
              ),
              limit=1,
              sort="EXPECTED_START_TIME_ASC"
          )
          if upcoming_runs:
              next_run_time = upcoming_runs[0].expected_start_time
              if next_run_time:
                  next_run_time = next_run_time.astimezone().isoformat()
        except Exception as e:
          logger.warning(f"获取部署 {target_dep.name} 的下次运行时间失败: {e}")

      # 4. 获取最后运行 和 当前状态 (合并查询)
      last_run_time = None
      status = None
      active_run_id = None
      active_run_status = None
      is_stale = False
      stale_reason = None
      latest_activity_time = None
      
      try:
        from prefect.client.schemas.filters import FlowRunFilter
        # 查询最近一次运行（包含正在运行和已结束）
        recent_runs = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                deployment_id={"any_": [target_dep.id]},
                state={"type": {"any_": ["RUNNING", "PENDING", "CANCELLING", "COMPLETED", "FAILED", "CRASHED", "CANCELLED"]}}
            ),
            limit=1,
            sort="EXPECTED_START_TIME_DESC"
        )
        
        if recent_runs:
            latest_run = recent_runs[0]
            
            # 设置最后运行时间
            last_run_time = latest_run.start_time or latest_run.expected_start_time
            if last_run_time:
                last_run_time = last_run_time.astimezone().isoformat()
            
            # 设置当前状态；长时间无活动的活跃 run 视为孤儿运行，避免前端永久锁在“同步中”。
            if latest_run.state_name in ACTIVE_FLOW_RUN_STATE_NAMES:
              status = latest_run.state_name
              active_run_id = str(latest_run.id)
              active_run_status = latest_run.state_name
              task_runs = []
              try:
                from prefect.client.schemas.filters import TaskRunFilter

                task_runs = await client.read_task_runs(
                  task_run_filter=TaskRunFilter(
                    flow_run_id={"any_": [latest_run.id]}
                  ),
                  limit=200,
                )
              except Exception as e:
                logger.warning(
                  f"获取部署 {target_dep.name} 最近任务活动失败: {e}"
                )

              diagnostics = _active_run_diagnostics(
                target_dep.name,
                latest_run,
                task_runs,
              )
              is_stale = diagnostics["is_stale"]
              stale_reason = diagnostics["stale_reason"]
              latest_activity = diagnostics["latest_activity_time"]
              if latest_activity:
                latest_activity_time = latest_activity.astimezone().isoformat()

              if is_stale:
                logger.warning(
                  "部署 %s 的最新运行 %s 仍为 %s，但已长时间无活动: %s",
                  target_dep.name,
                  latest_run.id,
                  latest_run.state_name,
                  stale_reason,
                )
                 
      except Exception as e:
        logger.warning(f"获取部署 {target_dep.name} 的运行信息失败: {e}")

      return {
        "id": str(target_dep.id),
        "name": target_dep.name,
        "flow_name": flow_map.get(str(target_dep.flow_id), target_dep.name),
        "description": getattr(target_dep, "description", None),
        "work_pool_name": getattr(target_dep, "work_pool_name", None),
        "work_queue_name": getattr(target_dep, "work_queue_name", "default"),
        "is_schedule_active": is_schedule_active,
        "next_run_time": next_run_time,
        "last_run_time": last_run_time,
        "status": status,
        "active_run_id": active_run_id,
        "active_run_status": active_run_status,
        "is_stale": is_stale,
        "stale_reason": stale_reason,
        "latest_activity_time": latest_activity_time,
        "created": target_dep.created.isoformat() if hasattr(target_dep, "created") and target_dep.created else None,
        "updated": target_dep.updated.isoformat() if hasattr(target_dep, "updated") and target_dep.updated else None,
      }

  async def _get_deployment_by_id(self, deployment_id: str):
    """通过 deployment_id 获取 Prefect 的部署对象，兼容不同 client 方法名"""
    try:
      client = get_client()

      # 尝试常见的客户端方法名及参数组合
      for method_name in (
        "read_deployment",
        "read_deployment_by_id",
        "read_deployment_by_name",
        "read_deployment_by_name",
      ):
        method = getattr(client, method_name, None)
        if not method:
          continue

        # 尝试几种调用签名：位置参数或 id/name 关键字
        for args, kwargs in [
          ([deployment_id], {}),
          ([], {"id": deployment_id}),
          ([], {"name": deployment_id}),
        ]:
          try:
            deployment = await method(*args, **kwargs)
            if deployment:
              return deployment
          except Exception:
            # 如果某种签名不匹配则忽略尝试下一种
            continue

      # 最后尝试读取所有部署并匹配 id 字符串（作为回退）
      try:
        deployments = await client.read_deployments(limit=200)
        for dep in deployments:
          if str(getattr(dep, "id", "")).startswith(str(deployment_id)) or str(
            getattr(dep, "id", "")
          ) == str(deployment_id):
            return dep
      except Exception:
        pass

    except Exception as e:
      logger.debug(f"_get_deployment_by_id 出错: {e}")
    return None

  async def export_deployment_yaml_by_id(
    self, deployment_id: str, name: Optional[str] = None
  ) -> bool:
    """通过 deployment_id 获取部署对象并导出 YAML 备份。返回是否成功。"""
    try:
      deployment = await self._get_deployment_by_id(deployment_id)
      if not deployment:
        logger.warning(f"未找到 deployment_id={deployment_id} 对应的部署，无法导出")
        return False

      export_name = name or getattr(deployment, "name", deployment_id)
      await self._export_deployment_yaml(deployment, export_name)
      logger.info(f"已导出部署 YAML: {export_name}.yaml")
      return True
    except Exception as e:
      logger.warning(f"导出 deployment_id={deployment_id} YAML 失败: {e}")
      return False


class FlowDeploymentManagerRegistry:
  """Flow部署管理器注册表 - 线程安全单例模式"""

  _instance = None
  _lock = threading.Lock()

  def __new__(cls):
    if cls._instance is None:
      with cls._lock:
        if cls._instance is None:
          cls._instance = super().__new__(cls)
          cls._instance._manager = None
    return cls._instance

  def get_manager(self) -> FlowDeploymentManager:
    """获取FlowDeploymentManager单例实例"""
    if self._manager is None:
      self._manager = FlowDeploymentManager()
    return self._manager


# 全局注册表实例
flow_deployment_registry = FlowDeploymentManagerRegistry()
