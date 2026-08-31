"""Create immutable StrategyRun revisions behind one stable managed plan."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Mapping, Optional, Type

from quantx_domain.strategies.base import StrategyBase, StrategyRunMode
from sqlalchemy import select

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.managed_plan import (
  ManagedPlanConfigRevision,
  ManagedPlanRecord,
)
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.repositories.managed_plan_repository import (
  ManagedPlanRepository,
  managed_plan_config_fingerprint,
)
from quantx_infrastructure.services.runtime_obligations import (
  runtime_obligation_blocker,
)


def managed_runtime_has_live_consumer(runtime: Any) -> bool:
  """Prove that one exact runtime is RUNNING and owns a live consumer task."""

  if runtime is None:
    return False
  status = str(
    getattr(getattr(runtime, "status", None), "value", getattr(runtime, "status", ""))
    or ""
  ).upper()
  task = getattr(runtime, "task", None)
  return bool(
    status == "RUNNING"
    and task is not None
    and not getattr(task, "done", lambda: True)()
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
    config_fingerprint = managed_plan_config_fingerprint(snapshot)
    normalized_command_id = self._normalized_command_id(command_id)
    run_id = self._deterministic_run_id(
      plan_id=plan_id,
      config_version=1,
      command_id=normalized_command_id,
      config_fingerprint=config_fingerprint,
    )
    session_context = self._session_factory()
    if session_context is None:
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
        command_id=normalized_command_id,
        persist=False,
      )
      return run_id, 1
    created = False
    async with session_context as db:
      repo = ManagedPlanRepository(db)
      current = await repo.find(plan_id, for_update=True)
      if current is None:
        current, revision = await repo.create_plan(
          plan_id=plan_id,
          plan_kind=plan_kind,
          account_id=account_id,
          instrument_code=instrument_code,
          config_snapshot=snapshot,
          state_migration_policy=state_migration_policy,
          created_by_user_id=created_by_user_id,
          last_command_id=normalized_command_id or None,
        )
        created = True
      else:
        revision = await repo.current_revision(plan_id, for_update=True)
        self._validate_replayed_plan_revision(
          plan=current,
          revision=revision,
          expected_plan_kind=plan_kind,
          expected_account_id=account_id,
          expected_instrument_code=instrument_code,
          expected_config_version=1,
          expected_config_snapshot=snapshot,
          expected_config_fingerprint=config_fingerprint,
          expected_state_migration_policy=state_migration_policy,
          expected_created_by_user_id=created_by_user_id,
          expected_command_id=normalized_command_id,
          expected_supersedes_run_id=None,
          expected_run_id=run_id,
        )
      await db.commit()
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
        command_id=normalized_command_id,
      )
    except Exception as exc:
      # Identity/config replay conflicts are detected before this boundary and
      # must not damage an otherwise healthy plan.  From here onward this
      # command owns a durable (possibly partial) creation and any ordinary
      # failure is converged to an explicit fail-closed state.
      if created or normalized_command_id:
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
    config_fingerprint = managed_plan_config_fingerprint(snapshot)
    normalized_command_id = self._normalized_command_id(command_id)
    next_version = int(expected_version) + 1
    run_id = self._deterministic_run_id(
      plan_id=plan_id,
      config_version=next_version,
      command_id=normalized_command_id,
      config_fingerprint=config_fingerprint,
    )
    session_context = self._session_factory()
    if session_context is None:
      old_run_id = plan_id
      await self._stop_runtime(old_run_id)
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
        config_version=next_version,
        config_snapshot=snapshot,
        config_fingerprint=config_fingerprint,
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
        command_id=normalized_command_id,
        persist=False,
      )
      return run_id, next_version, old_run_id
    async with session_context as db:
      repo = ManagedPlanRepository(db)
      current = await repo.find(plan_id, for_update=True)
      if current is None:
        raise ValueError("托管计划不存在")
      plan_kind = str(current.plan_kind or "").upper()
      account_id = str(current.account_id or "")
      instrument_code = str(current.instrument_code or "")
      current_version = int(current.current_config_version or 0)
      if current_version == int(expected_version):
        old_run_id = str(current.current_run_id or "")
      elif current_version == next_version:
        revision = await repo.current_revision(plan_id, for_update=True)
        old_run_id = str(getattr(revision, "supersedes_run_id", None) or "")
        self._validate_replayed_plan_revision(
          plan=current,
          revision=revision,
          expected_plan_kind=plan_kind,
          expected_account_id=account_id,
          expected_instrument_code=instrument_code,
          expected_config_version=next_version,
          expected_config_snapshot=snapshot,
          expected_config_fingerprint=config_fingerprint,
          expected_state_migration_policy=state_migration_policy,
          expected_created_by_user_id=created_by_user_id,
          expected_command_id=normalized_command_id,
          expected_supersedes_run_id=old_run_id or None,
          expected_run_id=run_id,
        )
      else:
        raise ValueError(
          f"CONFIG_VERSION_CONFLICT: current={current.current_config_version}"
        )
      await db.commit()

    # A rejected stop (for example, an active protection plan) is a rejected
    # revision, not a plan failure. Do not mutate its version, pointer or status.
    if old_run_id:
      await self._stop_runtime(old_run_id)
    try:
      if current_version == int(expected_version):
        async with self._session_factory() as db:
          await ManagedPlanRepository(db).append_revision(
            plan_id=plan_id,
            expected_version=expected_version,
            config_snapshot=snapshot,
            state_migration_policy=state_migration_policy,
            supersedes_run_id=old_run_id or None,
            run_id=run_id,
            created_by_user_id=created_by_user_id,
          )
          await db.commit()
      await self._create_and_bind_run(
        plan_id=plan_id,
        plan_kind=plan_kind,
        account_id=account_id,
        instrument_code=instrument_code,
        config_version=next_version,
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
        command_id=normalized_command_id,
      )
    except Exception as exc:
      async with self._session_factory() as db:
        plan = await ManagedPlanRepository(db).find(plan_id, for_update=True)
        if plan is not None and (
          plan.current_run_id == run_id
          or (
            plan.current_run_id == old_run_id
            and int(plan.current_config_version) == int(expected_version)
          )
        ):
          # The old binding remains queryable and can be retried. Once a new
          # run is published, keep its identity even if its startup fails.
          plan.status = "ERROR" if plan.current_run_id == run_id else "PAUSED"
          plan.last_error = str(exc)[:2000]
          await db.commit()
      raise
    return run_id, next_version, old_run_id

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

  async def validate_current_binding(
    self,
    *,
    plan_id: str,
    plan_kind: str,
    account_id: str,
    instrument_code: str,
    config_version: int,
    config_snapshot: Mapping[str, Any],
    parameters: Mapping[str, Any],
    strategy_id: int,
    strategy_class: Type[StrategyBase],
    mode: StrategyRunMode,
    name: str,
    command_id: str,
    state_migration_policy: str,
    desired_enabled: bool,
    created_by_user_id: Optional[str] = None,
    parent_run_id: Optional[str] = None,
  ) -> str:
    """Read-only proof that one finalized command owns one exact live run."""

    session_context = self._session_factory()
    if session_context is None:
      raise RuntimeError("托管计划绑定校验缺少持久化会话")
    snapshot = dict(config_snapshot or {})
    fingerprint = managed_plan_config_fingerprint(snapshot)
    normalized_command_id = self._normalized_command_id(command_id)
    expected_run_id = self._deterministic_run_id(
      plan_id=plan_id,
      config_version=config_version,
      command_id=normalized_command_id,
      config_fingerprint=fingerprint,
    )
    async with session_context as db:
      repo = ManagedPlanRepository(db)
      plan = await repo.find(plan_id)
      revision = await repo.current_revision(plan_id)
      if plan is None or revision is None:
        raise RuntimeError("托管计划或当前配置版本不存在")
      expected_supersedes_run_id: Optional[str] = None
      if int(config_version) > 1:
        previous_revision = await db.scalar(
          select(ManagedPlanConfigRevision).where(
            ManagedPlanConfigRevision.plan_id == plan_id,
            ManagedPlanConfigRevision.config_version == int(config_version) - 1,
          )
        )
        if previous_revision is None or not previous_revision.run_id:
          raise RuntimeError("托管计划上一配置版本没有唯一 StrategyRun")
        expected_supersedes_run_id = str(previous_revision.run_id)
      self._validate_replayed_plan_revision(
        plan=plan,
        revision=revision,
        expected_plan_kind=plan_kind,
        expected_account_id=account_id,
        expected_instrument_code=instrument_code,
        expected_config_version=config_version,
        expected_config_snapshot=snapshot,
        expected_config_fingerprint=fingerprint,
        expected_state_migration_policy=state_migration_policy,
        expected_created_by_user_id=created_by_user_id,
        expected_command_id=normalized_command_id,
        expected_supersedes_run_id=expected_supersedes_run_id,
        expected_run_id=expected_run_id,
      )
      if (
        str(plan.current_run_id or "") != expected_run_id
        or str(revision.run_id or "") != expected_run_id
      ):
        raise RuntimeError("托管计划当前配置尚未绑定确定性 StrategyRun")
      expected_plan_status = "RUNNING" if desired_enabled else "PAUSED"
      if str(plan.status or "").upper() != expected_plan_status:
        raise RuntimeError("托管计划与人工计划启停状态不一致")
      persisted_run = await db.scalar(
        select(StrategyRun).where(StrategyRun.id == expected_run_id)
      )
      if persisted_run is None:
        raise RuntimeError("托管计划当前 StrategyRun 未持久化")
      self._validate_persisted_run(
        persisted_run,
        plan_id=plan_id,
        plan_kind=plan_kind,
        account_id=account_id,
        instrument_code=instrument_code,
        config_version=config_version,
        config_snapshot=snapshot,
        config_fingerprint=fingerprint,
        parameters=parameters,
        strategy_id=strategy_id,
        mode=mode,
        name=name,
        supersedes_run_id=expected_supersedes_run_id,
        parent_run_id=parent_run_id,
      )
    runtime = self._runtime_manager.get_run(expected_run_id)
    self._validate_runtime(
      runtime,
      strategy_class=strategy_class,
      mode=mode,
      instrument_code=instrument_code,
      parameters=parameters,
    )
    runtime_status = str(
      getattr(
        getattr(runtime, "status", None),
        "value",
        getattr(runtime, "status", ""),
      )
      or ""
    ).upper()
    expected_runtime_statuses = {"RUNNING"} if desired_enabled else {
      "PENDING",
      "PAUSED",
    }
    if runtime_status not in expected_runtime_statuses:
      raise RuntimeError("托管计划 Engine StrategyRun 启停状态不一致")
    if desired_enabled and not managed_runtime_has_live_consumer(runtime):
      raise RuntimeError("托管计划 Engine StrategyRun 缺少活动消费任务")
    return expected_run_id

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
    command_id: str,
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
        "command_id": command_id or None,
      },
    }
    persisted_run = None
    if persist:
      async with self._session_factory() as db:
        persisted_run = await db.scalar(
          select(StrategyRun).where(StrategyRun.id == run_id)
        )
        if persisted_run is not None:
          self._validate_persisted_run(
            persisted_run,
            plan_id=plan_id,
            plan_kind=plan_kind,
            account_id=account_id,
            instrument_code=instrument_code,
            config_version=config_version,
            config_snapshot=config_snapshot,
            config_fingerprint=config_fingerprint,
            parameters=bound_parameters,
            strategy_id=strategy_id,
            mode=mode,
            name=name,
            supersedes_run_id=supersedes_run_id,
            parent_run_id=parent_run_id,
          )
    runtime = self._runtime_manager.get_run(run_id)
    if persisted_run is None:
      if runtime is not None:
        raise RuntimeError("托管计划存在未持久化的同标识 StrategyRun")
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
      if persist:
        async with self._session_factory() as db:
          persisted_run = await db.scalar(
            select(StrategyRun).where(StrategyRun.id == run_id)
          )
          if persisted_run is None:
            raise RuntimeError("托管计划 StrategyRun 未完成持久化")
          self._validate_persisted_run(
            persisted_run,
            plan_id=plan_id,
            plan_kind=plan_kind,
            account_id=account_id,
            instrument_code=instrument_code,
            config_version=config_version,
            config_snapshot=config_snapshot,
            config_fingerprint=config_fingerprint,
            parameters=bound_parameters,
            strategy_id=strategy_id,
            mode=mode,
            name=name,
            supersedes_run_id=supersedes_run_id,
            parent_run_id=parent_run_id,
          )
    elif runtime is None:
      raise RuntimeError("托管计划 StrategyRun 已持久化但未被 Engine 恢复")
    self._validate_runtime(
      runtime,
      strategy_class=strategy_class,
      mode=mode,
      instrument_code=instrument_code,
      parameters=bound_parameters,
    )
    state_snapshot = dict(initial_state or {})
    if persist:
      async with self._session_factory() as db:
        repo = ManagedPlanRepository(db)
        managed_plan = await repo.find(plan_id, for_update=True)
        revision = await repo.find_revision(plan_id, config_version, for_update=True)
        if managed_plan is None or revision is None:
          raise RuntimeError("托管计划或配置版本在运行绑定前丢失")
        if int(managed_plan.current_config_version or 0) not in {
          int(config_version), int(config_version) - 1
        }:
          raise ValueError("托管计划绑定运行时版本已变化")
        if revision.run_id and str(revision.run_id) != run_id:
          raise ValueError("托管计划配置版本已经绑定其他运行")
        already_bound = managed_plan.current_run_id == run_id
        if state_snapshot and not already_bound:
          persisted_state = await db.scalar(
            select(StrategyRunState).where(StrategyRunState.run_id == run_id)
          )
          if persisted_state is None:
            if runtime is not None and runtime.strategy is not None:
              runtime.strategy.apply_state_snapshot(state_snapshot)
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
          elif dict(persisted_state.custom_state or {}) != state_snapshot:
            raise RuntimeError("未启动托管运行的初始状态与命令不一致")
        if not already_bound:
          await repo.bind_run(
            plan_id=plan_id,
            config_version=config_version,
            run_id=run_id,
            status="PENDING" if start_immediately else "PAUSED",
            command_id=command_id,
          )
        await db.commit()
    elif state_snapshot and runtime is not None and runtime.strategy is not None:
      runtime.strategy.apply_state_snapshot(state_snapshot)
    if start_immediately:
      if not await self._ensure_started(run_id):
        await self.set_status(plan_id, "ERROR", error="StrategyRun 启动失败")
        raise RuntimeError("托管计划 StrategyRun 启动失败")
      await self.set_status(plan_id, "RUNNING")

  @staticmethod
  def _normalized_command_id(command_id: Optional[str]) -> str:
    normalized = str(command_id or "").strip()
    if len(normalized) > 128:
      raise ValueError("托管计划命令标识不能超过 128 个字符")
    return normalized

  @staticmethod
  def _deterministic_run_id(
    *,
    plan_id: str,
    config_version: int,
    command_id: str,
    config_fingerprint: str,
  ) -> str:
    identity = (
      f"quantx:managed-plan-run:v1:{plan_id}:{int(config_version)}:"
      f"{command_id or config_fingerprint}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, identity))

  @staticmethod
  def _validate_replayed_plan_revision(
    *,
    plan: ManagedPlanRecord,
    revision: Any,
    expected_plan_kind: str,
    expected_account_id: str,
    expected_instrument_code: str,
    expected_config_version: int,
    expected_config_snapshot: Mapping[str, Any],
    expected_config_fingerprint: str,
    expected_state_migration_policy: str,
    expected_created_by_user_id: Optional[str],
    expected_command_id: str,
    expected_supersedes_run_id: Optional[str],
    expected_run_id: str,
  ) -> None:
    if revision is None:
      raise ValueError("MANAGED_PLAN_REPLAY_CONFLICT:配置版本不存在")
    if (
      str(plan.plan_kind or "").upper() != str(expected_plan_kind or "").upper()
      or str(plan.account_id or "") != str(expected_account_id or "")
      or str(plan.instrument_code or "").upper()
      != str(expected_instrument_code or "").upper()
      or int(plan.current_config_version or 0) != int(expected_config_version)
      or str(plan.last_command_id or "") != expected_command_id
      or int(revision.config_version or 0) != int(expected_config_version)
      or dict(revision.config_snapshot or {}) != dict(expected_config_snapshot or {})
      or str(revision.config_fingerprint or "") != expected_config_fingerprint
      or str(revision.state_migration_policy or "")
      != str(expected_state_migration_policy or "")
      or str(revision.created_by_user_id or "")
      != str(expected_created_by_user_id or "")
      or str(revision.supersedes_run_id or "")
      != str(expected_supersedes_run_id or "")
    ):
      raise ValueError("MANAGED_PLAN_REPLAY_CONFLICT:命令或配置不一致")
    if revision.run_id and str(revision.run_id) != expected_run_id:
      raise ValueError("MANAGED_PLAN_REPLAY_CONFLICT:配置版本绑定了其他运行")
    if plan.current_run_id and str(plan.current_run_id) != expected_run_id:
      raise ValueError("MANAGED_PLAN_REPLAY_CONFLICT:当前运行标识不一致")

  @staticmethod
  def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
      try:
        value = json.loads(value)
      except (TypeError, ValueError) as exc:
        raise ValueError("托管 StrategyRun 参数不是有效 JSON") from exc
    if not isinstance(value, Mapping):
      raise ValueError("托管 StrategyRun 参数格式无效")
    return dict(value)

  @classmethod
  def _parameters_include(
    cls,
    actual: Any,
    expected: Mapping[str, Any],
  ) -> bool:
    values = cls._mapping(actual)
    return all(values.get(key) == value for key, value in dict(expected or {}).items())

  @classmethod
  def _validate_persisted_run(
    cls,
    run: StrategyRun,
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
    mode: StrategyRunMode,
    name: str,
    supersedes_run_id: Optional[str],
    parent_run_id: Optional[str],
  ) -> None:
    persisted_mode = str(getattr(run.mode, "value", run.mode) or "").lower()
    expected_mode = str(getattr(mode, "value", mode) or "").lower()
    persisted_parameters = cls._mapping(run.parameters)
    if (
      str(run.plan_id or "") != str(plan_id)
      or str(run.plan_kind or "").upper() != str(plan_kind or "").upper()
      or int(run.plan_config_version or 0) != int(config_version)
      or dict(run.frozen_config_snapshot or {}) != dict(config_snapshot or {})
      or str(run.frozen_config_fingerprint or "") != str(config_fingerprint)
      or str(run.supersedes_run_id or "") != str(supersedes_run_id or "")
      or str(run.parent_run_id or "") != str(parent_run_id or "")
      or int(run.strategy_id or 0) != int(strategy_id)
      or persisted_mode != expected_mode
      or [str(item) for item in list(run.instruments or [])]
      != [str(instrument_code)]
      or str(run.name or "") != str(name or "")
      or not cls._parameters_include(persisted_parameters, parameters)
      or str(persisted_parameters.get("account_id") or "")
      != str(account_id or "")
    ):
      raise ValueError("MANAGED_PLAN_REPLAY_CONFLICT:孤儿 StrategyRun 与命令不一致")

  @classmethod
  def _validate_runtime(
    cls,
    runtime: Any,
    *,
    strategy_class: Type[StrategyBase],
    mode: StrategyRunMode,
    instrument_code: str,
    parameters: Mapping[str, Any],
  ) -> None:
    if runtime is None:
      raise RuntimeError("托管计划 StrategyRun 未进入 Engine 运行时")
    runtime_class = getattr(runtime, "strategy_class", None)
    context = getattr(runtime, "context", None)
    runtime_parameters = dict(
      (
        getattr(context, "parameters", {})
        if context is not None
        else getattr(runtime, "parameters", {})
      )
      or {}
    )
    class_mismatch = runtime_class is not None and runtime_class is not strategy_class
    context_mismatch = False
    if context is not None:
      context_mismatch = (
        str(
          getattr(
            getattr(context, "mode", None),
            "value",
            getattr(context, "mode", ""),
          )
        ).lower()
        != str(getattr(mode, "value", mode)).lower()
        or [str(item) for item in list(getattr(context, "instruments", []) or [])]
        != [str(instrument_code)]
      )
    if class_mismatch or context_mismatch or not cls._parameters_include(
      runtime_parameters,
      parameters,
    ):
      raise ValueError("MANAGED_PLAN_REPLAY_CONFLICT:Engine 运行时与命令不一致")

  async def _ensure_started(self, run_id: str) -> bool:
    runtime = self._runtime_manager.get_run(run_id)
    if runtime is None:
      return False
    status = str(
      getattr(getattr(runtime, "status", None), "value", getattr(runtime, "status", ""))
      or ""
    ).upper()
    if managed_runtime_has_live_consumer(runtime):
      return True
    started = False
    if status == "PAUSED":
      started = bool(await self._runtime_manager.resume_strategy(run_id))
    else:
      started = bool(await self._runtime_manager.start_strategy(run_id))
    if not started:
      return False
    return managed_runtime_has_live_consumer(self._runtime_manager.get_run(run_id))

  async def _stop_runtime(self, run_id: str) -> None:
    runtime = self._runtime_manager.get_run(run_id)
    if runtime is None and await self._durable_runtime_is_safely_stopped(run_id):
      return
    try:
      stopped = await self._runtime_manager.stop_strategy(run_id, force=False)
    except TypeError:
      stopped = await self._runtime_manager.stop_strategy(run_id)
    if stopped is True:
      return
    if self._runtime_manager.get_run(run_id) is None and (
      await self._durable_runtime_is_safely_stopped(run_id)
    ):
      return
    raise RuntimeError("旧 StrategyRun 未能确认停止，拒绝创建并行托管运行")

  async def _durable_runtime_is_safely_stopped(self, run_id: str) -> bool:
    session_context = self._session_factory()
    if session_context is None:
      return False
    async with session_context as db:
      persisted = await db.scalar(
        select(StrategyRun).where(StrategyRun.id == run_id)
      )
      if persisted is None:
        return False
      status = str(
        getattr(persisted.status, "value", persisted.status) or ""
      ).upper()
      if status not in {"STOPPED", "COMPLETED", "ERROR"}:
        return False
      active_managed_owner = await db.scalar(
        select(ManagedPlanRecord.plan_id)
        .where(ManagedPlanRecord.current_run_id == run_id)
        .where(ManagedPlanRecord.status.in_(["PENDING", "RUNNING"]))
        .limit(1)
      )
      if active_managed_owner is not None:
        return False
      return await runtime_obligation_blocker(db, run_id) is None
