"""Create immutable StrategyRun revisions behind one stable managed plan."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Mapping, Optional, Type

from quantx_domain.strategies.base import StrategyBase, StrategyRunMode
from sqlalchemy import select

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.managed_plan import ManagedPlanRecord
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.repositories.managed_plan_repository import (
  ManagedPlanRepository,
  managed_plan_config_fingerprint,
)


class ManagedPlanRuntimeService:
  """Own the Plan -> ConfigRevision -> StrategyRun creation boundary."""

  def __init__(
    self,
    runtime_manager: Any,
    *,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
  ) -> None:
    if runtime_manager is None:
      raise RuntimeError("托管计划运行只能由 QuantX Engine 创建")
    self._runtime_manager = runtime_manager
    self._session_factory = session_factory

  async def create(
    self,
    *,
    plan_id: str,
    plan_kind: str,
    account_id: str,
    instrument_code: str,
    config_snapshot: Mapping[str, Any],
    parameters: Mapping[str, Any],
    strategy_id: int,
    strategy_class: Type[StrategyBase],
    mode: StrategyRunMode,
    name: str,
    start_immediately: bool,
    created_by_user_id: Optional[str] = None,
    command_id: Optional[str] = None,
    state_migration_policy: str = "INITIAL_STATE",
    initial_state: Optional[Mapping[str, Any]] = None,
    parent_run_id: Optional[str] = None,
  ) -> tuple[str, int]:
    snapshot = dict(config_snapshot or {})
    session_context = self._session_factory()
    if session_context is None:
      run_id = str(uuid.uuid4())
      await self._create_and_bind_run(
        plan_id=plan_id,
        plan_kind=plan_kind,
        account_id=account_id,
        instrument_code=instrument_code,
        config_version=1,
        config_snapshot=snapshot,
        config_fingerprint=managed_plan_config_fingerprint(snapshot),
        parameters=parameters,
        strategy_id=strategy_id,
        strategy_class=strategy_class,
        mode=mode,
        name=name,
        run_id=run_id,
        supersedes_run_id=None,
        parent_run_id=parent_run_id,
        initial_state=initial_state,
        start_immediately=start_immediately,
        persist=False,
      )
      return run_id, 1
    async with session_context as db:
      repo = ManagedPlanRepository(db)
      _, revision = await repo.create_plan(
        plan_id=plan_id,
        plan_kind=plan_kind,
        account_id=account_id,
        instrument_code=instrument_code,
        config_snapshot=snapshot,
        state_migration_policy=state_migration_policy,
        created_by_user_id=created_by_user_id,
        last_command_id=command_id,
      )
      config_fingerprint = str(revision.config_fingerprint)
      await db.commit()
    run_id = str(uuid.uuid4())
    try:
      await self._create_and_bind_run(
        plan_id=plan_id,
        plan_kind=plan_kind,
        account_id=account_id,
        instrument_code=instrument_code,
        config_version=1,
        config_snapshot=snapshot,
        config_fingerprint=config_fingerprint,
        parameters=parameters,
        strategy_id=strategy_id,
        strategy_class=strategy_class,
        mode=mode,
        name=name,
        run_id=run_id,
        supersedes_run_id=None,
        parent_run_id=parent_run_id,
        initial_state=initial_state,
        start_immediately=start_immediately,
      )
    except Exception as exc:
      await self.set_status(plan_id, "ERROR", error=str(exc))
      raise
    return run_id, 1

  async def revise(
    self,
    *,
    plan_id: str,
    expected_version: int,
    config_snapshot: Mapping[str, Any],
    parameters: Mapping[str, Any],
    strategy_id: int,
    strategy_class: Type[StrategyBase],
    mode: StrategyRunMode,
    name: str,
    start_immediately: bool,
    state_migration_policy: str,
    initial_state: Optional[Mapping[str, Any]],
    created_by_user_id: Optional[str] = None,
    command_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
  ) -> tuple[str, int, str]:
    snapshot = dict(config_snapshot or {})
    session_context = self._session_factory()
    if session_context is None:
      old_run_id = plan_id
      await self._stop_runtime(old_run_id)
      run_id = str(uuid.uuid4())
      managed_snapshot = dict(
        parameters.get("managed_entry_plan")
        or parameters.get("managed_exit_plan")
        or {}
      )
      await self._create_and_bind_run(
        plan_id=plan_id,
        plan_kind=("EXIT" if parameters.get("managed_exit_plan") else "ENTRY"),
        account_id=str(parameters.get("account_id") or ""),
        instrument_code=str(managed_snapshot.get("instrument_code") or ""),
        config_version=int(expected_version) + 1,
        config_snapshot=snapshot,
        config_fingerprint=managed_plan_config_fingerprint(snapshot),
        parameters=parameters,
        strategy_id=strategy_id,
        strategy_class=strategy_class,
        mode=mode,
        name=name,
        run_id=run_id,
        supersedes_run_id=old_run_id,
        parent_run_id=parent_run_id,
        initial_state=initial_state,
        start_immediately=start_immediately,
        persist=False,
      )
      return run_id, int(expected_version) + 1, old_run_id
    async with session_context as db:
      repo = ManagedPlanRepository(db)
      current = await repo.find(plan_id, for_update=True)
      if current is None:
        raise ValueError("托管计划不存在")
      old_run_id = str(current.current_run_id or "")
      plan_kind = str(current.plan_kind or "").upper()
      account_id = str(current.account_id or "")
      instrument_code = str(current.instrument_code or "")
      _, revision = await repo.append_revision(
        plan_id=plan_id,
        expected_version=expected_version,
        config_snapshot=snapshot,
        state_migration_policy=state_migration_policy,
        supersedes_run_id=old_run_id or None,
        created_by_user_id=created_by_user_id,
        last_command_id=command_id,
      )
      config_fingerprint = str(revision.config_fingerprint)
      await db.commit()

    if old_run_id:
      await self._stop_runtime(old_run_id)
    run_id = str(uuid.uuid4())
    try:
      await self._create_and_bind_run(
        plan_id=plan_id,
        plan_kind=plan_kind,
        account_id=account_id,
        instrument_code=instrument_code,
        config_version=int(expected_version) + 1,
        config_snapshot=snapshot,
        config_fingerprint=config_fingerprint,
        parameters=parameters,
        strategy_id=strategy_id,
        strategy_class=strategy_class,
        mode=mode,
        name=name,
        run_id=run_id,
        supersedes_run_id=old_run_id or None,
        parent_run_id=parent_run_id,
        initial_state=initial_state,
        start_immediately=start_immediately,
      )
    except Exception as exc:
      async with self._session_factory() as db:
        plan = await ManagedPlanRepository(db).find(plan_id, for_update=True)
        if plan is not None:
          plan.status = "ERROR"
          plan.last_error = str(exc)[:2000]
          await db.commit()
      raise
    return run_id, int(expected_version) + 1, old_run_id

  async def current_plan(self, plan_id: str) -> Optional[ManagedPlanRecord]:
    session_context = self._session_factory()
    if session_context is None:
      return None
    async with session_context as db:
      return await ManagedPlanRepository(db).find(plan_id)

  async def current_run_id(self, plan_id: str) -> str:
    plan = await self.current_plan(plan_id)
    if plan is None or not plan.current_run_id:
      raise ValueError("托管计划当前没有可用 StrategyRun")
    return str(plan.current_run_id)

  async def load_state(self, run_id: str) -> dict[str, Any]:
    if not run_id:
      return {}
    runtime = self._runtime_manager.get_run(run_id)
    if runtime is not None and runtime.strategy is not None:
      return dict(runtime.strategy.persistence_state_snapshot() or {})
    session_context = self._session_factory()
    if session_context is None:
      return {}
    async with session_context as db:
      state = await db.scalar(
        select(StrategyRunState).where(StrategyRunState.run_id == run_id)
      )
      return dict(state.custom_state or {}) if state is not None else {}

  async def set_status(self, plan_id: str, status: str, *, error: str = "") -> None:
    session_context = self._session_factory()
    if session_context is None:
      return
    async with session_context as db:
      plan = await ManagedPlanRepository(db).find(plan_id, for_update=True)
      if plan is None:
        return
      plan.status = str(status or "").upper()
      plan.last_error = str(error or "")[:2000] or None
      await db.commit()

  async def _create_and_bind_run(
    self,
    *,
    plan_id: str,
    plan_kind: str,
    account_id: str,
    instrument_code: str,
    config_version: int,
    config_snapshot: Mapping[str, Any],
    config_fingerprint: str,
    parameters: Mapping[str, Any],
    strategy_id: int,
    strategy_class: Type[StrategyBase],
    mode: StrategyRunMode,
    name: str,
    run_id: str,
    supersedes_run_id: Optional[str],
    parent_run_id: Optional[str],
    initial_state: Optional[Mapping[str, Any]],
    start_immediately: bool,
    persist: bool = True,
  ) -> None:
    fingerprint = managed_plan_config_fingerprint(config_snapshot)
    if fingerprint != config_fingerprint:
      raise ValueError("托管计划配置指纹不一致")
    bound_parameters = {
      **dict(parameters or {}),
      "_managed_plan_binding": {
        "plan_id": plan_id,
        "plan_kind": str(plan_kind or "").upper(),
        "config_version": int(config_version),
        "config_snapshot": dict(config_snapshot or {}),
        "config_fingerprint": config_fingerprint,
        "supersedes_run_id": supersedes_run_id,
        "parent_run_id": parent_run_id,
      },
    }
    created_id = await self._runtime_manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=strategy_class,
      mode=mode,
      instruments=[instrument_code],
      parameters=bound_parameters,
      name=name,
      auto_start=False,
      run_id=run_id,
    )
    if str(created_id) != run_id:
      raise RuntimeError("托管计划 StrategyRun 标识不一致")

    runtime = self._runtime_manager.get_run(run_id)
    state_snapshot = dict(initial_state or {})
    if state_snapshot and runtime is not None and runtime.strategy is not None:
      runtime.strategy.apply_state_snapshot(state_snapshot)
    if persist:
      async with self._session_factory() as db:
        if state_snapshot:
          db.add(
            StrategyRunState(
              run_id=run_id,
              cash=0.0,
              frozen_cash=0.0,
              total_asset=0.0,
              custom_state=state_snapshot,
              version=1,
            )
          )
        await ManagedPlanRepository(db).bind_run(
          plan_id=plan_id,
          config_version=config_version,
          run_id=run_id,
          status="PENDING" if start_immediately else "PAUSED",
        )
        await db.commit()
    if start_immediately:
      if not await self._runtime_manager.start_strategy(run_id):
        await self.set_status(plan_id, "ERROR", error="StrategyRun 启动失败")
        raise RuntimeError("托管计划 StrategyRun 启动失败")
      await self.set_status(plan_id, "RUNNING")

  async def _stop_runtime(self, run_id: str) -> None:
    try:
      await self._runtime_manager.stop_strategy(run_id, force=False)
    except TypeError:
      await self._runtime_manager.stop_strategy(run_id)
