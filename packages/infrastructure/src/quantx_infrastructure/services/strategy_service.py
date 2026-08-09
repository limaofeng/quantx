"""
策略服务
处理策略相关的业务逻辑
"""

from typing import Any, Dict, List

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository


class StrategyService:
  """策略服务类"""

  def __init__(self):
    pass

  async def get_strategies(self) -> List[Dict[str, Any]]:
    """获取策略模板列表"""
    async for db in get_async_db():
      strategy_repo = StrategyRepository(db)
      strategies_from_db = await strategy_repo.find_all_active()

      # 转换为业务对象
      strategies = []
      for strategy_db in strategies_from_db:
        strategy_data = self._strategy_to_dict(strategy_db)

        strategies.append(strategy_data)

      return strategies

  async def get_strategy_runs(self, user_id: str = "default") -> List[Dict[str, Any]]:
    """获取策略运行列表"""
    async for db in get_async_db():
      strategy_repo = StrategyRepository(db)
      runs_from_db = await strategy_repo.find_all_strategy_runs(user_id)

      # 转换为业务对象
      runs = []
      for run_db in runs_from_db:
        run_data = self._strategy_run_to_dict(run_db)
        runs.append(run_data)

      return runs

  async def create_strategy_run(self, run_input: Dict[str, Any]) -> Dict[str, Any]:
    """创建策略运行"""
    async for db in get_async_db():
      # 验证输入参数
      self._validate_strategy_run_input(run_input)

      # 生成UUID作为运行ID
      import uuid

      run_id = str(uuid.uuid4())

      run_data = {
        "id": run_id,
        "strategy_id": run_input["strategy_id"],
        "stock_id": run_input["stock_id"],
        "parameters": run_input.get("parameters", "{}"),
        "status": "STOPPED",
        "user_id": run_input.get("user_id", "default"),
      }

      strategy_repo = StrategyRepository(db)
      new_run = await strategy_repo.create_strategy_run(run_data)

      return self._strategy_run_to_dict(new_run)

  async def start_strategy_run(self, run_id: str) -> Dict[str, str]:
    """启动策略运行"""
    return await self._update_strategy_run_status(run_id, "RUNNING")

  async def stop_strategy_run(self, run_id: str) -> Dict[str, str]:
    """停止策略运行"""
    return await self._update_strategy_run_status(run_id, "STOPPED")

  async def pause_strategy_run(self, run_id: str) -> Dict[str, str]:
    """暂停策略运行"""
    return await self._update_strategy_run_status(run_id, "PAUSED")

  async def _update_strategy_run_status(
    self, run_id: str, status: str
  ) -> Dict[str, str]:
    """更新策略运行状态"""
    async for db in get_async_db():
      strategy_repo = StrategyRepository(db)
      updated_run = await strategy_repo.update_strategy_run_status(run_id, status)

      if updated_run:
        action_map = {"RUNNING": "启动", "STOPPED": "停止", "PAUSED": "暂停"}
        action = action_map.get(status, "更新")
        return {"message": f"策略运行已{action}"}
      else:
        return {"message": "策略运行不存在"}

  def _validate_strategy_run_input(self, run_input: Dict[str, Any]):
    """验证策略运行输入参数"""
    required_fields = ["strategy_id", "stock_id"]
    for field in required_fields:
      if field not in run_input:
        raise ValueError(f"缺少必要字段: {field}")

  def _strategy_to_dict(self, strategy_db) -> Dict[str, Any]:
    """将Strategy对象转换为字典"""
    return {
      "id": strategy_db.id,
      "name": strategy_db.name,
      "description": strategy_db.description,
      "file_path": strategy_db.file_path,
      "class_name": strategy_db.class_name,
      "parameter_schema": strategy_db.parameter_schema,
      "is_active": strategy_db.is_active,
      "created_at": strategy_db.created_at,
      "updated_at": strategy_db.updated_at,
    }

  def _strategy_run_to_dict(self, run_db) -> Dict[str, Any]:
    """将StrategyRun对象转换为字典"""
    return {
      "id": run_db.id,
      "strategy_id": run_db.strategy_id,
      "strategy_name": run_db.strategy.name,
      "stock_id": run_db.stock_id,
      "stock_code": run_db.stock.code,
      "stock_name": run_db.stock.name,
      "parameters": run_db.parameters,
      "status": run_db.status,
      "start_time": run_db.start_time,
      "stop_time": run_db.stop_time,
      "profit_loss": run_db.profit_loss,
      "total_trades": run_db.total_trades,
      "error_message": run_db.error_message,
      "user_id": run_db.user_id,
      "created_at": run_db.created_at,
      "updated_at": run_db.updated_at,
    }
