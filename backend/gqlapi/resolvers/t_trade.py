"""GraphQL resolver facade for T-trade sessions."""

from datetime import datetime
from typing import List, Optional

from gqlapi.types.t_trade_types import (
  TTradeGlobalHolding,
  TTradeGlobalMonitor,
  TTradeGlobalMutationResult,
  TTradeGlobalSettingsInput,
  TTradeExternalEntryInput,
  TTradeImportedEntry,
  TTradeMutationResult,
  TTradeReplay,
  TTradeReplayCycle,
  TTradeReplayCyclePage,
  TTradeReplayCurvePoint,
  TTradeReplayInstrumentResult,
  TTradeReplayMutationResult,
  TTradeReplayPosition,
  TTradeReplayPreparation,
  TTradeReplayStartInput,
  TTradeReplaySummary,
  TTradeSession,
  TTradeStartInput,
  TTradeTimeExitMode,
)
from services.t_trade_global_monitor import t_trade_global_monitor
from services.t_trade_replay_service import TTradeReplayService
from services.t_trade_service import TTradeService


class TTradeResolver:
  service = TTradeService()
  replay_service = TTradeReplayService()

  @staticmethod
  def _datetime(value):
    if isinstance(value, datetime) or value is None:
      return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

  @staticmethod
  def _time_exit_mode(value) -> TTradeTimeExitMode:
    if isinstance(value, TTradeTimeExitMode):
      return value
    try:
      return TTradeTimeExitMode(str(value or "UNLIMITED"))
    except ValueError:
      return TTradeTimeExitMode.UNLIMITED

  @classmethod
  def _session_type(cls, data: dict) -> TTradeSession:
    payload = dict(data)
    payload["time_exit_mode"] = cls._time_exit_mode(
      payload.get("time_exit_mode")
    )
    return TTradeSession(**payload)

  @classmethod
  def _replay_type(cls, data: dict) -> TTradeReplay:
    payload = dict(data)
    payload["start_time"] = cls._datetime(payload.get("start_time"))
    payload["end_time"] = cls._datetime(payload.get("end_time"))
    if payload.get("summary"):
      payload["summary"] = TTradeReplaySummary(**payload["summary"])
    payload["instruments"] = [
      TTradeReplayInstrumentResult(**item)
      for item in payload.get("instruments", [])
    ]
    curve = []
    for item in payload.get("curve", []):
      point = dict(item)
      point["timestamp"] = cls._datetime(point.get("timestamp"))
      curve.append(TTradeReplayCurvePoint(**point))
    payload["curve"] = curve
    return TTradeReplay(**payload)

  @classmethod
  def _global_monitor_type(cls, data: dict) -> TTradeGlobalMonitor:
    payload = dict(data)
    payload["sessions"] = [cls._session_type(session) for session in payload.get("sessions", [])]
    holdings = []
    for holding in payload.get("holdings", []):
      holding_payload = dict(holding)
      if holding_payload.get("session"):
        holding_payload["session"] = cls._session_type(holding_payload["session"])
      holdings.append(TTradeGlobalHolding(**holding_payload))
    payload["holdings"] = holdings
    payload["time_exit_mode"] = cls._time_exit_mode(
      payload.get("time_exit_mode")
    )
    return TTradeGlobalMonitor(**payload)

  @classmethod
  async def get_global_monitor(cls, account_id: str) -> TTradeGlobalMonitor:
    return cls._global_monitor_type(
      await t_trade_global_monitor.get_monitor(account_id)
    )

  @classmethod
  async def save_global_monitor(
    cls, input: TTradeGlobalSettingsInput
  ) -> TTradeGlobalMutationResult:
    try:
      monitor = await t_trade_global_monitor.save_config(vars(input))
      return TTradeGlobalMutationResult(
        success=True,
        code="GLOBAL_MONITOR_SAVED",
        message="全局做 T 监控设置已保存",
        monitor=cls._global_monitor_type(monitor),
      )
    except ValueError as exc:
      return TTradeGlobalMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )

  @classmethod
  async def reconcile_global_monitor(
    cls, account_id: str
  ) -> TTradeGlobalMutationResult:
    try:
      monitor = await t_trade_global_monitor.reconcile_account(account_id)
      error = str(monitor.get("last_error", "") or "")
      return TTradeGlobalMutationResult(
        success=not error,
        code=(
          "GLOBAL_MONITOR_RECONCILED"
          if not error
          else "GLOBAL_MONITOR_RECONCILE_FAILED"
        ),
        message="已重新同步全部持仓" if not error else error,
        monitor=cls._global_monitor_type(monitor),
      )
    except ValueError as exc:
      return TTradeGlobalMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )

  @classmethod
  async def get_session(
    cls, run_id: str, stock_code: Optional[str] = None
  ) -> Optional[TTradeSession]:
    try:
      return cls._session_type(await cls.service.get_session(run_id, stock_code))
    except ValueError:
      return None

  @classmethod
  async def list_sessions(
    cls,
    account_id: Optional[str],
    stock_code: Optional[str],
    active_only: bool,
  ) -> List[TTradeSession]:
    rows = await cls.service.list_sessions(
      account_id=account_id,
      stock_code=stock_code,
      active_only=active_only,
    )
    return [cls._session_type(row) for row in rows]

  @classmethod
  async def list_imported_entries(cls, account_id: str) -> List[TTradeImportedEntry]:
    rows = await cls.service.list_imported_entries(account_id)
    return [TTradeImportedEntry(**row) for row in rows]

  @classmethod
  async def start_session(cls, input: TTradeStartInput) -> TTradeMutationResult:
    try:
      session = await cls.service.start_session(vars(input))
      return TTradeMutationResult(
        success=True,
        code="STARTED",
        message="做 T 会话已启动",
        session=cls._session_type(session),
      )
    except ValueError as exc:
      return TTradeMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )

  @classmethod
  async def approve_entry(
    cls, run_id: str, intent_id: str
  ) -> TTradeMutationResult:
    try:
      result = await cls.service.approve_entry(run_id, intent_id)
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"])
        if result.get("session")
        else None,
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "VALIDATION_FAILED", str(exc))

  @classmethod
  async def reject_entry(
    cls, run_id: str, intent_id: str
  ) -> TTradeMutationResult:
    try:
      result = await cls.service.reject_entry(run_id, intent_id)
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"])
        if result.get("session")
        else None,
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "VALIDATION_FAILED", str(exc))

  @classmethod
  async def import_external_entry(
    cls, input: TTradeExternalEntryInput
  ) -> TTradeMutationResult:
    try:
      result = await cls.service.import_external_entry(
        input.run_id, input.account_id, input.order_id
      )
      return TTradeMutationResult(
        success=True,
        code=str(result["code"]),
        message=str(result["message"]),
        session=cls._session_type(result["session"]),
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "VALIDATION_FAILED", str(exc))

  @classmethod
  async def sync_source_orders(cls, account_id: str) -> TTradeMutationResult:
    try:
      result = await cls.service.sync_source_orders(account_id)
      return TTradeMutationResult(
        success=True,
        code=str(result["code"]),
        message=str(result["message"]),
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "SYNC_FAILED", str(exc))

  @classmethod
  async def stop_session(cls, run_id: str) -> TTradeMutationResult:
    try:
      result = await cls.service.stop_session(run_id)
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"])
        if result.get("session")
        else None,
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "VALIDATION_FAILED", str(exc))

  @classmethod
  async def prepare_replay(
    cls, account_id: str, start_time: datetime
  ) -> TTradeReplayPreparation:
    data = await cls.replay_service.prepare(account_id, start_time)
    data["positions"] = [
      TTradeReplayPosition(
        stock_code=item["stock_code"],
        instrument_name=item["instrument_name"],
        volume=item["volume"],
        available_volume=item["available_volume"],
        avg_price=item["avg_price"],
        last_price=item["last_price"],
        market_value=item["market_value"],
      )
      for item in data.get("positions", [])
    ]
    return TTradeReplayPreparation(**data)

  @classmethod
  async def get_replay(cls, run_id: str) -> Optional[TTradeReplay]:
    data = await cls.replay_service.get(run_id)
    return cls._replay_type(data) if data else None

  @classmethod
  async def replay_history(
    cls, account_id: str, limit: int
  ) -> List[TTradeReplay]:
    rows = await cls.replay_service.history(account_id, limit)
    return [cls._replay_type(row) for row in rows]

  @classmethod
  async def replay_cycles(
    cls, run_id: str, offset: int, limit: int
  ) -> TTradeReplayCyclePage:
    data = await cls.replay_service.cycles(run_id, offset, limit)
    items = []
    for raw in data.get("items", []):
      item = dict(raw)
      item["entry_time"] = cls._datetime(item.get("entry_time"))
      item["exit_time"] = cls._datetime(item.get("exit_time"))
      items.append(TTradeReplayCycle(**item))
    data["items"] = items
    return TTradeReplayCyclePage(**data)

  @classmethod
  async def start_replay(
    cls, input: TTradeReplayStartInput
  ) -> TTradeReplayMutationResult:
    try:
      replay = await cls.replay_service.start(vars(input))
      return TTradeReplayMutationResult(
        success=True,
        code="REPLAY_STARTED",
        message="做 T 历史回放已启动",
        replay=cls._replay_type(replay),
      )
    except ValueError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )

  @classmethod
  async def cancel_replay(cls, run_id: str) -> TTradeReplayMutationResult:
    try:
      replay = await cls.replay_service.cancel(run_id)
      return TTradeReplayMutationResult(
        success=True,
        code="REPLAY_CANCELLED",
        message="做 T 历史回放已取消",
        replay=cls._replay_type(replay),
      )
    except ValueError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )
