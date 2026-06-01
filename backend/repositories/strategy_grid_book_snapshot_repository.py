"""Repository for structured GridBook snapshots."""

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.relational_base import BaseRepository
from models.strategy_grid_book_snapshot import StrategyGridBookSnapshot


CURRENT_SNAPSHOT = "CURRENT"
TEMPLATE_SNAPSHOT = "TEMPLATE"
BACKTEST_FINAL_SNAPSHOT = "BACKTEST_FINAL"


class StrategyGridBookSnapshotRepository(BaseRepository[StrategyGridBookSnapshot]):
  """GridBook 快照仓储。

  - CURRENT: 当前可查询快照，面向模拟/实盘运行态。
  - TEMPLATE: 回测模板快照，面向参数/GridBook 编辑与重新回测复制。
  - BACKTEST_FINAL: 某个回测版本的最终快照，面向版本切换和 UI 查询。
  """

  model_class = StrategyGridBookSnapshot

  def __init__(self, db_session: AsyncSession):
    super().__init__(db_session)

  @staticmethod
  def current_key(strategy_run_id: str) -> str:
    return f"{CURRENT_SNAPSHOT}:{strategy_run_id}"

  @staticmethod
  def template_key(strategy_run_id: str) -> str:
    return f"{TEMPLATE_SNAPSHOT}:{strategy_run_id}"

  @staticmethod
  def backtest_final_key(backtest_id: str) -> str:
    return f"{BACKTEST_FINAL_SNAPSHOT}:{backtest_id}"

  async def upsert_snapshot(
    self,
    *,
    strategy_run_id: str,
    snapshot_type: str,
    snapshot: Dict[str, Any],
    backtest_id: Optional[str] = None,
    backtest_version: Optional[int] = None,
    mode: Optional[str] = None,
    source_path: Optional[str] = None,
    snapshot_count: int = 0,
    observed_count: int = 0,
    note: Optional[str] = None,
  ) -> StrategyGridBookSnapshot:
    snapshot = dict(snapshot or {})
    snapshot_type = str(snapshot_type or "").upper()
    if snapshot_type == BACKTEST_FINAL_SNAPSHOT:
      if not backtest_id:
        raise ValueError("BACKTEST_FINAL 快照必须提供 backtest_id")
      snapshot_id = self.backtest_final_key(backtest_id)
    elif snapshot_type == CURRENT_SNAPSHOT:
      snapshot_id = self.current_key(strategy_run_id)
    elif snapshot_type == TEMPLATE_SNAPSHOT:
      snapshot_id = self.template_key(strategy_run_id)
    else:
      raise ValueError(f"未知 GridBook 快照类型: {snapshot_type}")

    record = await self.find_by_id(snapshot_id)
    values = {
      "strategy_run_id": strategy_run_id,
      "backtest_id": backtest_id,
      "backtest_version": backtest_version,
      "mode": str(mode or "").upper() or None,
      "snapshot_type": snapshot_type,
      "instrument_code": snapshot.get("instrument_code") or snapshot.get("instrumentCode"),
      "grid_book_version": int(snapshot.get("version", 1) or 1),
      "parameter_version": str(snapshot.get("parameter_version") or snapshot.get("parameterVersion") or ""),
      "snapshot": snapshot,
      "snapshot_count": int(snapshot_count or 0),
      "observed_count": int(observed_count or 0),
      "source_path": source_path,
      "note": note,
    }

    if record:
      for key, value in values.items():
        setattr(record, key, value)
    else:
      record = StrategyGridBookSnapshot(id=snapshot_id, **values)
      self.db.add(record)

    await self.db.commit()
    await self.db.refresh(record)
    return record

  async def upsert_current(
    self,
    *,
    strategy_run_id: str,
    snapshot: Dict[str, Any],
    mode: Optional[str] = None,
    note: Optional[str] = None,
  ) -> StrategyGridBookSnapshot:
    return await self.upsert_snapshot(
      strategy_run_id=strategy_run_id,
      snapshot_type=CURRENT_SNAPSHOT,
      snapshot=snapshot,
      mode=mode,
      note=note,
    )

  async def upsert_template(
    self,
    *,
    strategy_run_id: str,
    snapshot: Dict[str, Any],
    mode: Optional[str] = "BACKTEST",
    note: Optional[str] = None,
  ) -> StrategyGridBookSnapshot:
    return await self.upsert_snapshot(
      strategy_run_id=strategy_run_id,
      snapshot_type=TEMPLATE_SNAPSHOT,
      snapshot=snapshot,
      mode=mode,
      note=note,
    )

  async def upsert_backtest_final(
    self,
    *,
    strategy_run_id: str,
    backtest_id: str,
    backtest_version: int,
    snapshot: Dict[str, Any],
    source_path: Optional[str] = None,
    snapshot_count: int = 0,
    observed_count: int = 0,
  ) -> StrategyGridBookSnapshot:
    return await self.upsert_snapshot(
      strategy_run_id=strategy_run_id,
      backtest_id=backtest_id,
      backtest_version=backtest_version,
      mode="BACKTEST",
      snapshot_type=BACKTEST_FINAL_SNAPSHOT,
      snapshot=snapshot,
      source_path=source_path,
      snapshot_count=snapshot_count,
      observed_count=observed_count,
    )

  async def get_current(self, strategy_run_id: str) -> Optional[StrategyGridBookSnapshot]:
    return await self.find_by_id(self.current_key(strategy_run_id))

  async def get_template(self, strategy_run_id: str) -> Optional[StrategyGridBookSnapshot]:
    return await self.find_by_id(self.template_key(strategy_run_id))

  async def get_backtest_final(
    self,
    backtest_id: str,
  ) -> Optional[StrategyGridBookSnapshot]:
    return await self.find_by_id(self.backtest_final_key(backtest_id))

  async def get_backtest_final_by_version(
    self,
    strategy_run_id: str,
    version: int,
  ) -> Optional[StrategyGridBookSnapshot]:
    stmt = select(StrategyGridBookSnapshot).where(
      StrategyGridBookSnapshot.strategy_run_id == strategy_run_id,
      StrategyGridBookSnapshot.snapshot_type == BACKTEST_FINAL_SNAPSHOT,
      StrategyGridBookSnapshot.backtest_version == version,
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()

  async def get_latest_backtest_final(
    self,
    strategy_run_id: str,
  ) -> Optional[StrategyGridBookSnapshot]:
    stmt = (
      select(StrategyGridBookSnapshot)
      .where(
        StrategyGridBookSnapshot.strategy_run_id == strategy_run_id,
        StrategyGridBookSnapshot.snapshot_type == BACKTEST_FINAL_SNAPSHOT,
      )
      .order_by(
        StrategyGridBookSnapshot.backtest_version.desc(),
        StrategyGridBookSnapshot.updated_at.desc(),
      )
      .limit(1)
    )
    result = await self.db.execute(stmt)
    return result.scalar_one_or_none()
