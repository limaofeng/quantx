"""
Prefect Flow Hooks

定义 Flow 状态变更时触发的回调函数，通过 Redis Pub/Sub 广播状态更新
"""

import logging
from typing import Dict, Optional

from prefect import Flow
from prefect.client.schemas.objects import FlowRun
from prefect.states import State

logger = logging.getLogger(__name__)

# 缓存 flow_name -> deployment_name 映射
_flow_to_deployment_cache: Optional[Dict[str, str]] = None


def _build_flow_to_deployment_map() -> Dict[str, str]:
    """从 FlowDeploymentManager 的配置中动态构建映射"""
    global _flow_to_deployment_cache
    
    if _flow_to_deployment_cache is not None:
        return _flow_to_deployment_cache
    
    mapping = {}
    
    try:
        # 动态导入以避免循环依赖
        from importlib import import_module
        from .flow_deployment_manager import get_flow_deployment_configs

        for flow_config in get_flow_deployment_configs():
            try:
                module = import_module(flow_config.module)
                flow_func = getattr(module, flow_config.function, None)
                if flow_func and hasattr(flow_func, "name"):
                    flow_name = flow_func.name
                    mapping[flow_name] = flow_config.name
                    logger.debug(f"映射: '{flow_name}' -> '{flow_config.name}'")
            except Exception as e:
                logger.warning(
                    f"无法导入 {flow_config.module}.{flow_config.function}: {e}"
                )
    except Exception as e:
        logger.error(f"构建 flow->deployment 映射失败: {e}")
    
    _flow_to_deployment_cache = mapping
    return mapping


def _get_deployment_name_from_flow(flow: Flow) -> str:
    """从 Flow 获取对应的 deployment 名称（动态构建映射）"""
    mapping = _build_flow_to_deployment_map()
    
    # 优先使用动态映射
    if flow.name in mapping:
        return mapping[flow.name]
    
    # 回退：将中文名转为 kebab-case（不太可靠，仅作为最后手段）
    logger.warning(f"Flow '{flow.name}' 未在映射中找到，使用回退逻辑")
    return flow.name.lower().replace(" ", "-").replace("_", "-")


def _broadcast_event(flow: Flow, flow_run: FlowRun, state: State):
    """通过 Redis Pub/Sub 广播事件（跨进程通信）"""
    import json
    import redis
    from config.settings import settings
    from database.redis_pubsub import get_deployment_channel
    
    deployment_name = _get_deployment_name_from_flow(flow)
    
    event = {
        "deployment_name": deployment_name,
        "status": state.name,  # 例如: "Running", "Completed", "Failed"
        "flow_run_id": str(flow_run.id) if flow_run else None,
        "flow_name": flow.name,
        "message": f"Flow '{flow.name}' 状态变更为 {state.name}",
    }
    
    logger.info(f"[Hook] {flow.name} -> {state.name} (deployment: {deployment_name})")
    
    # 使用同步 Redis 客户端发布（Worker 进程中可能没有运行中的事件循环）
    try:
        r = redis.Redis(
            host=getattr(settings, "redis_host", "localhost"),
            port=getattr(settings, "redis_port", 6379),
            db=getattr(settings, "redis_db", 0),
            password=getattr(settings, "redis_password", "") or None,
        )
        channel = get_deployment_channel(deployment_name)
        payload = json.dumps(event, ensure_ascii=False, default=str)
        receivers = r.publish(channel, payload)
        logger.debug(f"Published to {channel}, receivers: {receivers}")
        r.close()
    except Exception as e:
        logger.error(f"Failed to publish event to Redis: {e}")


def on_flow_running(flow: Flow, flow_run: FlowRun, state: State):
    """Flow 开始运行时触发"""
    _broadcast_event(flow, flow_run, state)


def on_flow_completed(flow: Flow, flow_run: FlowRun, state: State):
    """Flow 成功完成时触发"""
    _broadcast_event(flow, flow_run, state)


def on_flow_failed(flow: Flow, flow_run: FlowRun, state: State):
    """Flow 失败时触发"""
    _broadcast_event(flow, flow_run, state)


def on_flow_cancelled(flow: Flow, flow_run: FlowRun, state: State):
    """Flow 被取消时触发"""
    _broadcast_event(flow, flow_run, state)


def on_flow_crashed(flow: Flow, flow_run: FlowRun, state: State):
    """Flow 崩溃时触发"""
    _broadcast_event(flow, flow_run, state)


# 常用 hook 组合，可以直接解包到 @flow 装饰器
STANDARD_FLOW_HOOKS = {
    "on_running": [on_flow_running],
    "on_completion": [on_flow_completed],
    "on_failure": [on_flow_failed],
    "on_cancellation": [on_flow_cancelled],
    "on_crashed": [on_flow_crashed],
}
