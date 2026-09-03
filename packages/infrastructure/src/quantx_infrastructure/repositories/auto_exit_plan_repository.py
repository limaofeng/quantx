"""Repository for persistent Engine-owned automatic exit plans."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional

from quantx_domain.trading.exit_plan import ExitPlan, ExitPlanStatus
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)

RESERVING_EXIT_PLAN_STATUSES = (
  "PENDING_ENTRY",
  "ACTIVE",
  "EXIT_PENDING",
  "PARTIALLY_EXITED",
  "PAUSED",
  "ERROR",
)
TERMINAL_PLAN_STATUSES = ("COMPLETED", "CANCELLED")

_ADAPTIVE_RULE_ID_SUFFIX = "adaptive-volume-price"


class AutoExitPlanConcurrencyError(RuntimeError):
  """The durable plan changed after a runtime loaded its state."""


def auto_exit_plan_state_values(
  plan_state: Mapping[str, Any],
  *,
  evaluated_at: datetime | None = None,
) -> dict[str, Any]:
  """Validate canonical state and derive its query projections.

  ``plan_state`` is the durable aggregate.  The other columns are deliberately
  derived here so a CAS write cannot leave list/capacity queries describing a
  different plan state.
  """

  plan = ExitPlan.from_dict(dict(plan_state or {}))
  canonical_state = plan.to_dict()
  adaptive_state = next(
    (
      dict(value or {})
      for key, value in plan.rule_state.items()
      if str(key).endswith(_ADAPTIVE_RULE_ID_SUFFIX)
    ),
    {},
  )
  resolved_evaluated_at = evaluated_at
  if resolved_evaluated_at is None and plan.last_evaluated_at:
    try:
      resolved_evaluated_at = datetime.fromisoformat(
        plan.last_evaluated_at.replace("Z", "+00:00")
      )
    except ValueError as exc:
      raise ValueError("退出计划最后评估时间不是有效 ISO-8601 时间") from exc
  if resolved_evaluated_at is not None:
    resolved_evaluated_at = time_utils.to_shanghai(resolved_evaluated_at)
  cost_basis = plan.cost_basis
  return {
    "plan_state": canonical_state,
    "status": plan.status.value,
    "enabled": plan.status
    not in {
      ExitPlanStatus.PAUSED,
      ExitPlanStatus.CANCELLED,
      ExitPlanStatus.COMPLETED,
      ExitPlanStatus.ERROR,
    },
    "protected_volume": int(plan.entry_filled_volume),
    "exited_volume": int(plan.exited_volume),
    "remaining_volume": int(plan.remaining_volume),
    "entry_avg_price": float(plan.entry_avg_price),
    "cost_basis_mode": cost_basis.mode.value,
    "cost_basis_snapshot": cost_basis.to_dict(),
    "phase": str(adaptive_state.get("phase") or "WAITING_ARM"),
    "data_quality": str(
      adaptive_state.get("data_quality") or "PRICE_UNAVAILABLE"
    ),
    "last_decision": str(adaptive_state.get("last_decision") or "") or None,
    "peak_price": float(plan.peak_price or 0.0),
    "peak_drawdown_pct": float(
      adaptive_state.get("peak_drawdown_pct", 0.0) or 0.0
    ),
    "volume_velocity": _optional_float(adaptive_state.get("volume_velocity")),
    "weak_score": int(adaptive_state.get("weak_score", 0) or 0),
    "trailing_floor_pct": plan.trailing_floor_pct,
    "pending_client_order_id": plan.pending_order_id or None,
    "last_evaluated_at": resolved_evaluated_at,
  }


class AutoExitPlanRepository:
  def __init__(self, db: AsyncSession):
    self.db = db

  async def find_by_id(
    self, plan_id: str, *, for_update: bool = False
  ) -> Optional[AutoExitPlanRecord]:
    stmt = (
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.plan_id == plan_id)
      .execution_options(populate_existing=True)
    )
    if for_update:
      stmt = stmt.with_for_update()
    return (await self.db.execute(stmt)).scalar_one_or_none()

  async def find_for_strategy_run(
    self,
    strategy_run_id: str,
    *,
    statuses: Optional[list[str]] = None,
    terminal_history_limit: Optional[int] = None,
    for_update: bool = False,
  ) -> list[AutoExitPlanRecord]:
    """Load authoritative plan states owned by one strategy runtime."""

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    base_stmt = (
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.strategy_run_id == normalized_run_id)
      .execution_options(populate_existing=True)
    )
    if statuses is not None:
      normalized_statuses = tuple(
        str(status or "").strip().upper() for status in statuses if str(status or "").strip()
      )
      if not normalized_statuses:
        return []
      base_stmt = base_stmt.where(AutoExitPlanRecord.status.in_(normalized_statuses))
    if terminal_history_limit is not None and statuses is None:
      limit = max(0, min(int(terminal_history_limit or 0), 2000))
      active_stmt = base_stmt.where(
        AutoExitPlanRecord.status.notin_(TERMINAL_PLAN_STATUSES)
      ).order_by(AutoExitPlanRecord.created_at, AutoExitPlanRecord.plan_id)
      terminal_stmt = (
        base_stmt.where(AutoExitPlanRecord.status.in_(TERMINAL_PLAN_STATUSES))
        .order_by(
          desc(AutoExitPlanRecord.updated_at),
          desc(AutoExitPlanRecord.created_at),
          desc(AutoExitPlanRecord.plan_id),
        )
        .limit(limit)
      )
      if for_update:
        active_stmt = active_stmt.with_for_update()
        terminal_stmt = terminal_stmt.with_for_update()
      active = list((await self.db.execute(active_stmt)).scalars().all())
      terminal = list((await self.db.execute(terminal_stmt)).scalars().all())
      return [*active, *reversed(terminal)]
    stmt = base_stmt.order_by(AutoExitPlanRecord.plan_id)
    if for_update:
      stmt = stmt.with_for_update()
    return list((await self.db.execute(stmt)).scalars().all())

  async def find_by_source(
    self, source_type: str, source_id: str
  ) -> Optional[AutoExitPlanRecord]:
    return (
      await self.db.execute(
        select(AutoExitPlanRecord)
        .where(AutoExitPlanRecord.source_type == source_type)
        .where(AutoExitPlanRecord.source_id == source_id)
      )
    ).scalar_one_or_none()

  async def find_active(
    self,
    *,
    account_id: Optional[str] = None,
    instrument_code: Optional[str] = None,
  ) -> list[AutoExitPlanRecord]:
    stmt = (
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.enabled == True)  # noqa: E712
      .where(
        AutoExitPlanRecord.status.in_(("ACTIVE", "PARTIALLY_EXITED", "EXIT_PENDING"))
      )
    )
    if account_id:
      stmt = stmt.where(AutoExitPlanRecord.account_id == account_id)
    if instrument_code:
      stmt = stmt.where(AutoExitPlanRecord.instrument_code == instrument_code)
    return list((await self.db.execute(stmt)).scalars().all())

  async def find_all(
    self,
    *,
    account_id: Optional[str] = None,
    instrument_code: Optional[str] = None,
    statuses: Optional[list[str]] = None,
    source_type: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    limit: int = 200,
  ) -> list[AutoExitPlanRecord]:
    stmt = select(AutoExitPlanRecord)
    if account_id:
      stmt = stmt.where(AutoExitPlanRecord.account_id == account_id)
    if instrument_code:
      stmt = stmt.where(AutoExitPlanRecord.instrument_code == instrument_code)
    if statuses:
      stmt = stmt.where(AutoExitPlanRecord.status.in_(statuses))
    if source_type:
      stmt = stmt.where(AutoExitPlanRecord.source_type == source_type)
    if strategy_run_id:
      stmt = stmt.where(AutoExitPlanRecord.strategy_run_id == strategy_run_id)
    stmt = stmt.order_by(desc(AutoExitPlanRecord.updated_at)).limit(
      max(1, min(int(limit or 200), 500))
    )
    return list((await self.db.execute(stmt)).scalars().all())

  async def find_reserving(
    self,
    *,
    account_id: str,
    instrument_code: str,
    for_update: bool = False,
    execution_mode: str = "live",
    strategy_run_id: Optional[str] = None,
    plan_id: Optional[str] = None,
  ) -> list[AutoExitPlanRecord]:
    mode = str(execution_mode).lower()
    if mode not in {"live", "paper"}:
      raise ValueError("退出容量只支持 live 或 paper")
    stmt = (
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.account_id == account_id)
      .where(AutoExitPlanRecord.instrument_code == instrument_code)
      .where(AutoExitPlanRecord.environment == mode.upper())
      .where(AutoExitPlanRecord.status.in_(RESERVING_EXIT_PLAN_STATUSES))
      .order_by(AutoExitPlanRecord.created_at, AutoExitPlanRecord.plan_id)
    )
    if mode == "paper":
      # A PAPER run owns its own simulated inventory. Monitor-owned previews
      # have no run and may only claim their own frozen plan sample.
      stmt = stmt.where(
        AutoExitPlanRecord.strategy_run_id == strategy_run_id
        if strategy_run_id
        else AutoExitPlanRecord.plan_id == (plan_id or "")
      )
    if for_update:
      stmt = stmt.with_for_update()
    return list((await self.db.execute(stmt)).scalars().all())

  async def find_events(
    self,
    *,
    plan_id: str,
    limit: int = 200,
  ) -> list[AutoExitPlanEvent]:
    stmt = (
      select(AutoExitPlanEvent)
      .where(AutoExitPlanEvent.plan_id == plan_id)
      .order_by(desc(AutoExitPlanEvent.created_at))
      .limit(max(1, min(int(limit or 200), 500)))
    )
    return list((await self.db.execute(stmt)).scalars().all())

  async def save(self, record: AutoExitPlanRecord) -> AutoExitPlanRecord:
    self.db.add(record)
    await self.db.commit()
    await self.db.refresh(record)
    return record

  async def compare_and_swap_state(
    self,
    *,
    plan_id: str,
    expected_state_version: int,
    plan_state: Mapping[str, Any],
    evaluated_at: datetime | None = None,
    commit: bool = True,
  ) -> AutoExitPlanRecord:
    """Persist one material aggregate transition with optimistic locking.

    Configuration identity is checked but never advanced here.  Configuration
    mutations remain responsible for ``config_version``; runtime transitions
    advance only ``state_version``.
    """

    normalized_plan_id = str(plan_id or "").strip()
    if not normalized_plan_id:
      raise ValueError("退出计划标识不能为空")
    version = int(expected_state_version)
    if version < 1:
      raise ValueError("退出计划状态版本必须大于等于 1")
    values = auto_exit_plan_state_values(plan_state, evaluated_at=evaluated_at)
    canonical_state = dict(values["plan_state"])
    plan = ExitPlan.from_dict(canonical_state)
    if plan.plan_id != normalized_plan_id:
      raise ValueError("退出计划状态与持久化标识不一致")
    values.update(
      {
        "state_version": version + 1,
        "updated_at": time_utils.now().replace(tzinfo=None),
      }
    )
    result = await self.db.execute(
      update(AutoExitPlanRecord)
      .where(
        AutoExitPlanRecord.plan_id == normalized_plan_id,
        AutoExitPlanRecord.state_version == version,
        AutoExitPlanRecord.config_version == int(plan.template.config_version),
      )
      .values(**values)
      .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
      if commit:
        await self.db.rollback()
      current = await self.find_by_id(normalized_plan_id)
      if current is not None and dict(current.plan_state or {}) == canonical_state:
        return current
      raise AutoExitPlanConcurrencyError(
        "退出计划状态或配置版本冲突，必须重新装载后重放行情事实"
      )
    if commit:
      await self.db.commit()
    current = await self.find_by_id(normalized_plan_id)
    if current is None:
      raise RuntimeError("退出计划状态更新后无法重新读取")
    return current


def _optional_float(value: Any) -> Optional[float]:
  if value is None or value == "":
    return None
  return float(value)
