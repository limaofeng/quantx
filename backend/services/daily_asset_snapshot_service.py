"""Daily close asset snapshot service."""

from datetime import date, datetime
from hashlib import md5
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from core.utils import time_utils
from database.connection import get_async_db
from models.account import Account
from models.daily_asset_snapshot import DailyAssetSnapshot
from models.enums import AccountType, StrategyRunMode, StrategyRunStatus
from models.strategy_run import StrategyRun
from repositories.account_repository import AccountRepository
from repositories.daily_asset_snapshot_repository import (
  DailyAssetPositionSnapshotRepository,
  DailyAssetSnapshotRepository,
)
from repositories.strategy_run_state_repository import (
  StrategyRunPositionRepository,
  StrategyRunStateRepository,
)


ACCOUNT_SCOPE = "ACCOUNT"
STRATEGY_SCOPE = "STRATEGY"
ACCOUNT_SOURCE = "MINIQMT"
STRATEGY_SOURCE = "STRATEGY_RUN_STATE"


class DailyAssetSnapshotService:
  """Creates account and strategy daily close snapshots."""

  async def record_account_snapshot(
    self,
    *,
    account_id: str,
    account_info: Dict[str, Any],
    trade_date: Optional[date] = None,
    snapshot_at: Optional[datetime] = None,
    account_type: AccountType = AccountType.STOCK,
    net_capital_flow_cny: float = 0.0,
    positions: Optional[Iterable[Dict[str, Any]]] = None,
  ) -> DailyAssetSnapshot:
    trade_date = trade_date or time_utils.today()
    snapshot_at = snapshot_at or time_utils.now()
    account_type = _normalize_account_type(account_type)

    async for db in get_async_db():
      account_repo = AccountRepository(db)
      snapshot_repo = DailyAssetSnapshotRepository(db)
      position_repo = DailyAssetPositionSnapshotRepository(db)

      await self._upsert_current_account(
        account_repo=account_repo,
        account_id=account_id,
        account_type=account_type,
        account_info=account_info,
      )

      record = await self._record_snapshot(
        snapshot_repo=snapshot_repo,
        scope_type=ACCOUNT_SCOPE,
        scope_id=account_id,
        account_id=account_id,
        account_type=account_type,
        strategy_run_id=None,
        trade_date=trade_date,
        snapshot_at=snapshot_at,
        source=ACCOUNT_SOURCE,
        total_asset=_first_number(account_info, "total_asset", "totalAsset"),
        cash_available=_first_number(account_info, "cash", "available_cash", "cash_available"),
        cash_frozen=_first_number(account_info, "frozen_cash", "cash_frozen"),
        market_value=_first_number(account_info, "market_value", "marketValue"),
        net_capital_flow_cny=net_capital_flow_cny,
        data_quality="OK",
        metadata={"account_id": account_id},
      )

      if positions is not None:
        await position_repo.replace_for_snapshot(record.id, positions)

      return record

  async def record_strategy_snapshots_for_account(
    self,
    *,
    account_id: str,
    trade_date: Optional[date] = None,
    snapshot_at: Optional[datetime] = None,
  ) -> List[DailyAssetSnapshot]:
    """Record strategy attribution snapshots for runs explicitly bound to account_id."""

    trade_date = trade_date or time_utils.today()
    snapshot_at = snapshot_at or time_utils.now()
    records: List[DailyAssetSnapshot] = []

    async for db in get_async_db():
      run_stmt = select(StrategyRun).where(
        StrategyRun.mode.in_([StrategyRunMode.LIVE, StrategyRunMode.PAPER]),
        StrategyRun.status.in_(
          [
            StrategyRunStatus.RUNNING,
            StrategyRunStatus.PAUSED,
            StrategyRunStatus.PENDING,
          ]
        ),
      )
      result = await db.execute(run_stmt)
      runs = list(result.scalars().all())

      snapshot_repo = DailyAssetSnapshotRepository(db)
      position_repo = DailyAssetPositionSnapshotRepository(db)
      state_repo = StrategyRunStateRepository(db)
      run_position_repo = StrategyRunPositionRepository(db)

      for run in runs:
        if not _run_matches_account(run, account_id):
          continue

        state = await state_repo.get_state(run.id)
        if state is None:
          continue

        positions = await run_position_repo.get_all_positions(run.id)
        position_dicts = [position.to_dict() for position in positions]
        market_value = sum(float(item.get("market_value") or 0.0) for item in position_dicts)
        total_asset = float(state.total_asset or 0.0)
        if total_asset <= 0:
          total_asset = float(state.cash or 0.0) + float(state.frozen_cash or 0.0) + market_value

        record = await self._record_snapshot(
          snapshot_repo=snapshot_repo,
          scope_type=STRATEGY_SCOPE,
          scope_id=run.id,
          account_id=account_id,
          account_type=None,
          strategy_run_id=run.id,
          trade_date=trade_date,
          snapshot_at=snapshot_at,
          source=STRATEGY_SOURCE,
          total_asset=total_asset,
          cash_available=float(state.cash or 0.0),
          cash_frozen=float(state.frozen_cash or 0.0),
          market_value=market_value,
          net_capital_flow_cny=0.0,
          data_quality="ESTIMATED",
          metadata={
            "account_id": account_id,
            "run_name": run.name,
            "mode": run.mode.value if hasattr(run.mode, "value") else str(run.mode),
            "quality_flags": ["ESTIMATED_STRATEGY_ATTRIBUTION"],
          },
        )
        await position_repo.replace_for_snapshot(record.id, position_dicts)
        records.append(record)

      return records

  async def get_snapshots(
    self,
    *,
    account_id: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 366,
  ) -> List[DailyAssetSnapshot]:
    async for db in get_async_db():
      repo = DailyAssetSnapshotRepository(db)
      return await repo.find_range(
        account_id=account_id,
        strategy_run_id=strategy_run_id,
        scope_type=scope_type,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
      )

  async def get_latest_account_snapshot(
    self, account_id: str
  ) -> Optional[DailyAssetSnapshot]:
    async for db in get_async_db():
      repo = DailyAssetSnapshotRepository(db)
      return await repo.find_latest_for_account(account_id)

  async def _record_snapshot(
    self,
    *,
    snapshot_repo: DailyAssetSnapshotRepository,
    scope_type: str,
    scope_id: str,
    account_id: Optional[str],
    account_type: Optional[AccountType],
    strategy_run_id: Optional[str],
    trade_date: date,
    snapshot_at: datetime,
    source: str,
    total_asset: float,
    cash_available: float,
    cash_frozen: float,
    market_value: float,
    net_capital_flow_cny: float,
    data_quality: str,
    metadata: Dict[str, Any],
  ) -> DailyAssetSnapshot:
    scope_type = str(scope_type).upper()
    scope_key = DailyAssetSnapshotRepository.scope_key(scope_type, scope_id)
    previous = await snapshot_repo.find_previous(scope_key, trade_date)
    values = self.build_snapshot_values(
      scope_type=scope_type,
      scope_key=scope_key,
      account_id=account_id,
      account_type=account_type,
      strategy_run_id=strategy_run_id,
      trade_date=trade_date,
      snapshot_at=snapshot_at,
      source=source,
      total_asset_cny=total_asset,
      cash_available_cny=cash_available,
      cash_frozen_cny=cash_frozen,
      market_value_cny=market_value,
      net_capital_flow_cny=net_capital_flow_cny,
      previous_snapshot=previous,
      data_quality=data_quality,
      metadata=metadata,
    )
    return await snapshot_repo.upsert_snapshot(values)

  @staticmethod
  def build_snapshot_values(
    *,
    scope_type: str,
    scope_key: str,
    account_id: Optional[str],
    account_type: Optional[AccountType],
    strategy_run_id: Optional[str],
    trade_date: date,
    snapshot_at: datetime,
    source: str,
    total_asset_cny: float,
    cash_available_cny: float,
    cash_frozen_cny: float,
    market_value_cny: float,
    net_capital_flow_cny: float,
    previous_snapshot: Optional[DailyAssetSnapshot],
    data_quality: str = "OK",
    metadata: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    metadata = dict(metadata or {})
    quality_flags = list(metadata.get("quality_flags") or [])

    total_asset_cny = round(float(total_asset_cny or 0.0), 4)
    cash_available_cny = round(float(cash_available_cny or 0.0), 4)
    cash_frozen_cny = round(float(cash_frozen_cny or 0.0), 4)
    market_value_cny = round(float(market_value_cny or 0.0), 4)
    net_capital_flow_cny = round(float(net_capital_flow_cny or 0.0), 4)

    component_total = cash_available_cny + cash_frozen_cny + market_value_cny
    component_diff = round(total_asset_cny - component_total, 4)
    metadata["asset_component_total_cny"] = component_total
    metadata["asset_component_diff_cny"] = component_diff
    if abs(component_diff) > 0.01:
      quality_flags.append("ASSET_COMPONENT_MISMATCH")

    gross_delta = None
    daily_pnl = None
    daily_return_pct = None
    previous_snapshot_id = None
    if previous_snapshot is None:
      quality_flags.append("NO_PREVIOUS_SNAPSHOT")
    else:
      previous_asset = float(previous_snapshot.total_asset_cny or 0.0)
      previous_snapshot_id = previous_snapshot.id
      gross_delta = round(total_asset_cny - previous_asset, 4)
      daily_pnl = round(gross_delta - net_capital_flow_cny, 4)
      base = previous_asset + net_capital_flow_cny
      daily_return_pct = round((daily_pnl / base) * 100, 6) if base > 0 else None

    if quality_flags:
      metadata["quality_flags"] = sorted(set(quality_flags))
      if data_quality == "OK":
        data_quality = quality_flags[0]

    return {
      "id": DailyAssetSnapshot.make_id(scope_key, trade_date),
      "scope_type": str(scope_type).upper(),
      "scope_key": scope_key,
      "account_id": account_id,
      "account_type": account_type,
      "strategy_run_id": strategy_run_id,
      "trade_date": trade_date,
      "snapshot_at": snapshot_at,
      "source": source,
      "total_asset_cny": total_asset_cny,
      "cash_available_cny": cash_available_cny,
      "cash_frozen_cny": cash_frozen_cny,
      "market_value_cny": market_value_cny,
      "gross_asset_delta_cny": gross_delta,
      "net_capital_flow_cny": net_capital_flow_cny,
      "daily_pnl_cny": daily_pnl,
      "daily_return_pct": daily_return_pct,
      "previous_snapshot_id": previous_snapshot_id,
      "data_quality": data_quality,
      "snapshot_metadata": metadata,
    }

  async def _upsert_current_account(
    self,
    *,
    account_repo: AccountRepository,
    account_id: str,
    account_type: AccountType,
    account_info: Dict[str, Any],
  ) -> Account:
    account = await account_repo.find_by_account_id(account_id, account_type)
    if account is None:
      account = Account(
        id=_account_pk(account_id, account_type),
        account_id=account_id,
        account_type=account_type,
      )

    account.total_asset = _first_number(account_info, "total_asset", "totalAsset")
    account.cash = _first_number(account_info, "cash", "available_cash", "cash_available")
    account.market_value = _first_number(account_info, "market_value", "marketValue")
    account.frozen_cash = _first_number(account_info, "frozen_cash", "cash_frozen")
    return await account_repo.save(account)


def _first_number(data: Dict[str, Any], *keys: str) -> float:
  for key in keys:
    value = data.get(key)
    if value is not None:
      try:
        return float(value)
      except (TypeError, ValueError):
        return 0.0
  return 0.0


def _normalize_account_type(value: Any) -> AccountType:
  if isinstance(value, AccountType):
    return value
  if isinstance(value, int):
    resolved = AccountType.from_int(value)
    return resolved or AccountType.STOCK
  if isinstance(value, str):
    try:
      return AccountType(value.upper())
    except ValueError:
      return AccountType.STOCK
  return AccountType.STOCK


def _account_pk(account_id: str, account_type: AccountType) -> str:
  raw = f"{account_id}:{account_type.value}"
  return md5(raw.encode("utf-8")).hexdigest()


def _run_matches_account(run: StrategyRun, account_id: str) -> bool:
  params = dict(run.parameters or {})
  configured = (
    params.get("account_id")
    or params.get("accountId")
    or (params.get("account") or {}).get("account_id")
    or (params.get("account") or {}).get("accountId")
  )
  return str(configured or "") == str(account_id)
