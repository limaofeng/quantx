"""GraphQL resolver facade for T-trade sessions."""

import uuid
from dataclasses import fields as dataclass_fields
from datetime import datetime
from typing import List, Optional

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  OperationalAlert as OperationalAlertModel,
)
from quantx_infrastructure.services.engine_command_service import engine_command_service
from quantx_infrastructure.services.operational_alert_service import (
  OperationalAlertService,
)
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  t_trade_monitor_projection_service,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from quantx_infrastructure.services.t_trade_replay_service import TTradeReplayService
from quantx_infrastructure.services.t_trade_service import TTradeService

from quantx_api.gqlapi.types.common_types import PageInfo
from quantx_api.gqlapi.types.t_trade_types import (
  OperationalAlert,
  TTradeBatch,
  TTradeBatchEvent,
  TTradeBatchEventPage,
  TTradeBatchPage,
  TTradeEvaluationTelemetry,
  TTradeExternalEntryInput,
  TTradeGlobalHolding,
  TTradeGlobalMonitor,
  TTradeGlobalMutationResult,
  TTradeGlobalSettingsInput,
  TTradeImportedEntry,
  TTradeLiveReadiness,
  TTradeMutationResult,
  TTradeOperationsMutationResult,
  TTradeReadinessCheck,
  TTradeReplay,
  TTradeReplayCurvePoint,
  TTradeReplayCycle,
  TTradeReplayCyclePage,
  TTradeReplayInstrumentResult,
  TTradeReplayMutationResult,
  TTradeReplayPosition,
  TTradeReplayPreparation,
  TTradeReplayReport,
  TTradeReplayStartInput,
  TTradeReplaySummary,
  TTradeRolloutTarget,
  TTradeSession,
  TTradeSignalHistoryEntry,
  TTradeSignalHistoryPage,
  TTradeStartInput,
  TTradeTimeExitMode,
)
from quantx_api.gqlapi.utils.cursor import decode_datetime_cursor, encode_cursor


class TTradeResolver:
  service = TTradeService()
  replay_service = TTradeReplayService()
  operations_service = TTradeOperationsService()

  @staticmethod
  async def _engine_request(
    command_type: str,
    payload: dict,
    aggregate_id: str,
    idempotency_key: str = "",
  ) -> dict:
    receipt = await engine_command_service.request(
      command_type,
      payload,
      aggregate_id=aggregate_id,
      idempotency_key=(
        idempotency_key or f"{command_type.lower()}:{aggregate_id}:{uuid.uuid4()}"
      ),
    )
    if receipt.status == "FAILED":
      raise ValueError(receipt.error or f"Engine command failed: {command_type}")
    if receipt.status != "SUCCEEDED":
      raise ValueError(f"Engine 命令已排队但尚未确认: {receipt.message_id}")
    return dict(receipt.result or {})

  @staticmethod
  def _datetime(value):
    if isinstance(value, datetime) or value is None:
      return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

  @classmethod
  def _with_datetimes(cls, data: dict, *fields: str) -> dict:
    payload = dict(data)
    for field in fields:
      if field in payload:
        payload[field] = cls._datetime(payload[field])
    return payload

  @staticmethod
  def _graphql_kwargs(graphql_type, data: dict) -> dict:
    """Keep internal projection fields from leaking into GraphQL constructors."""
    known_fields = {item.name for item in dataclass_fields(graphql_type)}
    return {key: value for key, value in dict(data).items() if key in known_fields}

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
    payload = cls._with_datetimes(data, "created_at", "updated_at")
    payload["time_exit_mode"] = cls._time_exit_mode(payload.get("time_exit_mode"))
    payload["created_at"] = cls._datetime(payload.get("created_at"))
    payload["updated_at"] = cls._datetime(payload.get("updated_at"))
    if payload.get("latest_evaluation"):
      evaluation = dict(payload["latest_evaluation"])
      evaluation["last_tick_at"] = cls._datetime(evaluation.get("last_tick_at"))
      payload["latest_evaluation"] = TTradeEvaluationTelemetry(
        **cls._graphql_kwargs(TTradeEvaluationTelemetry, evaluation)
      )
    else:
      payload["latest_evaluation"] = None
    return TTradeSession(**cls._graphql_kwargs(TTradeSession, payload))

  @classmethod
  def _replay_type(cls, data: dict) -> TTradeReplay:
    payload = cls._with_datetimes(
      data,
      "start_time",
      "end_time",
      "created_at",
      "updated_at",
    )
    if payload.get("summary"):
      payload["summary"] = TTradeReplaySummary(
        **cls._graphql_kwargs(TTradeReplaySummary, payload["summary"])
      )
    if payload.get("report"):
      report = dict(payload["report"])
      report["generated_at"] = cls._datetime(report.get("generated_at"))
      payload["report"] = TTradeReplayReport(
        **cls._graphql_kwargs(TTradeReplayReport, report)
      )
    payload["instruments"] = [
      TTradeReplayInstrumentResult(
        **cls._graphql_kwargs(TTradeReplayInstrumentResult, item)
      )
      for item in payload.get("instruments", [])
    ]
    curve = []
    for item in payload.get("curve", []):
      point = dict(item)
      point["timestamp"] = cls._datetime(point.get("timestamp"))
      curve.append(
        TTradeReplayCurvePoint(**cls._graphql_kwargs(TTradeReplayCurvePoint, point))
      )
    payload["curve"] = curve
    return TTradeReplay(**cls._graphql_kwargs(TTradeReplay, payload))

  @classmethod
  def _global_monitor_type(cls, data: dict) -> TTradeGlobalMonitor:
    payload = cls._with_datetimes(
      data,
      "position_snapshot_reported_at",
      "position_snapshot_received_at",
      "last_reconciled_at",
      "created_at",
      "updated_at",
    )
    payload["sessions"] = [
      cls._session_type(session) for session in payload.get("sessions", [])
    ]
    holdings = []
    for holding in payload.get("holdings", []):
      holding_payload = dict(holding)
      if holding_payload.get("session"):
        holding_payload["session"] = cls._session_type(holding_payload["session"])
      holdings.append(
        TTradeGlobalHolding(**cls._graphql_kwargs(TTradeGlobalHolding, holding_payload))
      )
    payload["holdings"] = holdings
    if payload.get("readiness"):
      payload["readiness"] = cls._readiness_type(payload["readiness"])
    payload["time_exit_mode"] = cls._time_exit_mode(payload.get("time_exit_mode"))
    for field_name in (
      "position_snapshot_reported_at",
      "position_snapshot_received_at",
      "last_reconciled_at",
      "created_at",
      "updated_at",
      "projection_generated_at",
    ):
      payload[field_name] = cls._datetime(payload.get(field_name))
    # Projections written before these settings were introduced remain valid.
    defaults = {
      "max_exit_slippage_bps": 30.0,
      "high_profit_lock_enabled": True,
      "high_profit_arm_pct": 4.0,
      "high_profit_max_drawdown_pct": 1.2,
      "rapid_reversal_enabled": True,
      "rapid_reversal_window_seconds": 15,
      "rapid_reversal_drawdown_pct": 0.8,
      "rapid_reversal_confirm_ticks": 2,
      "momentum_enabled": True,
      "momentum_window_seconds": 60,
      "momentum_min_rise_pct": 0.8,
      "momentum_min_move_seconds": 15,
      "momentum_baseline_seconds": 300,
      "momentum_min_amount_velocity_ratio": 2.0,
      "momentum_min_vwap_premium_pct": 2.0,
      "momentum_max_vwap_premium_pct": 3.5,
      "momentum_high_tolerance_ticks": 1,
      "momentum_max_spread_ticks": 10,
      "momentum_max_spread_pct": 0.3,
      "limit_up_touch_exit_enabled": True,
      "limit_up_touch_tolerance_ticks": 0,
    }
    for key, value in defaults.items():
      payload.setdefault(key, value)
    return TTradeGlobalMonitor(**cls._graphql_kwargs(TTradeGlobalMonitor, payload))

  @classmethod
  def _readiness_type(cls, data: dict) -> TTradeLiveReadiness:
    payload = cls._with_datetimes(
      data,
      "snapshot_at",
      "controlled_window_started_at",
      "last_backup_at",
      "checked_at",
    )
    defaults = {
      "status": "READY" if payload.get("ready") else "BLOCKED",
      "preparation_ready": bool(payload.get("ready")),
      "automation_ready": bool(payload.get("ready")),
      "agent_mode": "unknown",
      "protocol_version": "",
      "snapshot_id": None,
      "snapshot_hash": None,
      "snapshot_at": None,
      "reconciliation_age_seconds": None,
      "queued_command_count": 0,
      "queue_delay_seconds": 0.0,
      "dead_letter_count": 0,
      "unresolved_critical_alert_count": 0,
      "manual_coexistence": False,
      "external_order_count": 0,
      "external_trade_count": 0,
      "controlled_window_active": False,
      "controlled_window_snapshot_id": None,
      "controlled_window_started_at": None,
      "new_external_order_count": 0,
      "new_external_trade_count": 0,
      "working_external_order_count": 0,
      "preparation_blocked_reasons": list(payload.get("blocked_reasons") or []),
      "journal_integrity": "unknown",
      "journal_size_bytes": 0,
      "journal_pending_reports": 0,
      "last_backup_at": None,
    }
    for key, value in defaults.items():
      payload.setdefault(key, value)
    payload["checks"] = [
      TTradeReadinessCheck(
        **cls._graphql_kwargs(
          TTradeReadinessCheck,
          {"scope": "AUTOMATION", **dict(item)},
        )
      )
      for item in payload.get("checks", [])
    ]
    return TTradeLiveReadiness(**cls._graphql_kwargs(TTradeLiveReadiness, payload))

  @classmethod
  def _operational_alert_type(
    cls,
    alert: OperationalAlertModel,
  ) -> OperationalAlert:
    return OperationalAlert(
      id=str(alert.id),
      severity=str(alert.severity),
      source=str(alert.source),
      code=str(alert.code),
      account_id=alert.account_id,
      business_id=alert.business_id,
      message=str(alert.message),
      details=dict(alert.details or {}),
      status=str(alert.status),
      occurrences=int(alert.occurrences or 0),
      first_seen_at=alert.first_seen_at,
      last_seen_at=alert.last_seen_at,
      acknowledged_by=alert.acknowledged_by,
      acknowledged_at=alert.acknowledged_at,
      resolved_by=alert.resolved_by,
      resolved_at=alert.resolved_at,
      resolution=alert.resolution,
    )

  @classmethod
  async def get_global_monitor(cls, account_id: str) -> TTradeGlobalMonitor:
    monitor = await t_trade_monitor_projection_service.get(account_id)
    if monitor is None:
      # Cold-start compatibility: the first request asks Engine to build the
      # durable projection; steady-state reads never wait on a command.
      monitor = await cls._engine_request(
        "T_TRADE_GLOBAL_GET",
        {"account_id": account_id},
        account_id,
      )
    return cls._global_monitor_type(monitor)

  @classmethod
  async def save_global_monitor(
    cls, input: TTradeGlobalSettingsInput
  ) -> TTradeGlobalMutationResult:
    try:
      monitor = await cls._engine_request(
        "T_TRADE_GLOBAL_SAVE",
        {"input": vars(input)},
        input.account_id,
      )
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
      monitor = await cls._engine_request(
        "T_TRADE_GLOBAL_RECONCILE",
        {"account_id": account_id},
        account_id,
      )
      error = str(monitor.get("last_error", "") or "")
      await cls.operations_service.mark_reconciled(
        account_id,
        ready=bool(monitor.get("position_snapshot_complete")) and not error,
        reason=error or str(monitor.get("position_snapshot_error") or ""),
      )
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
  async def session_account_id(cls, run_id: str) -> Optional[str]:
    try:
      session = await cls.service.get_session(run_id)
    except ValueError:
      return None
    account_id = session.get("account_id")
    return str(account_id) if account_id else None

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
    return [
      TTradeImportedEntry(
        **cls._graphql_kwargs(
          TTradeImportedEntry,
          cls._with_datetimes(row, "source_trade_time"),
        )
      )
      for row in rows
    ]

  @classmethod
  async def list_signal_history(
    cls, account_id: str, limit: int
  ) -> List[TTradeSignalHistoryEntry]:
    rows = await cls.service.list_signal_history(account_id, limit)
    return [
      TTradeSignalHistoryEntry(
        **cls._graphql_kwargs(
          TTradeSignalHistoryEntry,
          cls._with_datetimes(
            row,
            "created_at",
            "expires_at",
            "updated_at",
          ),
        )
      )
      for row in rows
    ]

  @classmethod
  async def list_signal_history_page(
    cls,
    account_id: str,
    first: int,
    after: Optional[str],
  ) -> TTradeSignalHistoryPage:
    cursor_time = None
    cursor_id = None
    if after:
      cursor_time, cursor_id = decode_datetime_cursor(after)
    rows, has_next_page = await cls.service.list_signal_history_page(
      account_id,
      cursor_created_at=cursor_time,
      cursor_id=cursor_id,
      first=first,
    )
    items = []
    cursors = []
    for row in rows:
      payload = dict(row)
      cursor_created_at = payload.pop("_cursor_created_at")
      cursor_row_id = payload.pop("_cursor_id")
      cursors.append(encode_cursor(cursor_created_at, cursor_row_id))
      items.append(
        TTradeSignalHistoryEntry(
          **cls._graphql_kwargs(
            TTradeSignalHistoryEntry,
            cls._with_datetimes(
              payload,
              "created_at",
              "expires_at",
              "updated_at",
            ),
          )
        )
      )
    return TTradeSignalHistoryPage(
      items=items,
      page_info=PageInfo(
        has_next_page=has_next_page,
        has_previous_page=bool(after),
        start_cursor=cursors[0] if cursors else None,
        end_cursor=cursors[-1] if cursors else None,
      ),
    )

  @classmethod
  async def start_session(cls, input: TTradeStartInput) -> TTradeMutationResult:
    try:
      session = await cls._engine_request(
        "T_TRADE_START_SESSION",
        {"input": vars(input)},
        input.account_id,
      )
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
    cls,
    run_id: str,
    intent_id: str,
    *,
    expected_signal_version: int = 0,
    idempotency_key: str = "",
    actor_id: str = "",
    device_session_id: str = "",
    approval_channel: str = "WEB",
  ) -> TTradeMutationResult:
    try:
      session = await cls.service.get_session(run_id)
      if str(session.get("mode") or "").lower() == "live":
        readiness = await cls.operations_service.readiness(
          str(session.get("account_id") or "")
        )
        if not readiness["can_approve"]:
          return TTradeMutationResult(
            False,
            "LIVE_NOT_READY",
            "；".join(readiness["blocked_reasons"]),
          )
      result = await cls._engine_request(
        "T_TRADE_APPROVE_ENTRY",
        {
          "run_id": run_id,
          "intent_id": intent_id,
          "expected_signal_version": expected_signal_version,
          "approval_audit": {
            "actor_id": str(actor_id or "")[:64],
            "device_session_id": str(device_session_id or "")[:64],
            "channel": str(approval_channel or "WEB")[:32],
          },
        },
        run_id,
        idempotency_key=(
          f"t-trade-approve:{run_id}:{intent_id}:"
          f"{idempotency_key or expected_signal_version}"
        ),
      )
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"]) if result.get("session") else None,
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "VALIDATION_FAILED", str(exc))

  @classmethod
  async def reject_entry(cls, run_id: str, intent_id: str) -> TTradeMutationResult:
    try:
      result = await cls._engine_request(
        "T_TRADE_REJECT_ENTRY",
        {"run_id": run_id, "intent_id": intent_id},
        run_id,
      )
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"]) if result.get("session") else None,
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "VALIDATION_FAILED", str(exc))

  @classmethod
  async def import_external_entry(
    cls, input: TTradeExternalEntryInput
  ) -> TTradeMutationResult:
    try:
      result = await cls._engine_request(
        "T_TRADE_IMPORT_EXTERNAL_ENTRY",
        {
          "run_id": input.run_id,
          "account_id": input.account_id,
          "order_id": input.order_id,
        },
        input.run_id,
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
      result = await cls._engine_request(
        "T_TRADE_STOP_SESSION",
        {"run_id": run_id},
        run_id,
      )
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"]) if result.get("session") else None,
      )
    except ValueError as exc:
      return TTradeMutationResult(False, "VALIDATION_FAILED", str(exc))

  @classmethod
  async def readiness(cls, account_id: str) -> TTradeLiveReadiness:
    return cls._readiness_type(await cls.operations_service.readiness(account_id))

  @classmethod
  async def operational_alerts(
    cls,
    account_id: str,
    *,
    status: Optional[str],
    severity: Optional[str],
    limit: int,
  ) -> List[OperationalAlert]:
    async with AsyncSessionLocal() as db:
      rows = await OperationalAlertService(db).list_alerts(
        account_id=account_id,
        status=status,
        severity=severity,
        limit=limit,
      )
      return [cls._operational_alert_type(row) for row in rows]

  @classmethod
  async def operational_alert_account_id(
    cls,
    alert_id: str,
  ) -> Optional[str]:
    async with AsyncSessionLocal() as db:
      alert = await db.get(OperationalAlertModel, alert_id)
      if alert is None:
        raise ValueError("告警不存在")
      return str(alert.account_id) if alert.account_id else None

  @classmethod
  async def acknowledge_operational_alert(
    cls,
    alert_id: str,
    *,
    actor_id: str,
  ) -> OperationalAlert:
    async with AsyncSessionLocal() as db:
      alert = await OperationalAlertService(db).acknowledge(
        alert_id,
        actor_id=actor_id,
      )
      return cls._operational_alert_type(alert)

  @classmethod
  async def resolve_operational_alert(
    cls,
    alert_id: str,
    *,
    actor_id: str,
    resolution: str,
  ) -> OperationalAlert:
    async with AsyncSessionLocal() as db:
      alert = await OperationalAlertService(db).resolve(
        alert_id,
        actor_id=actor_id,
        resolution=resolution,
      )
      return cls._operational_alert_type(alert)

  @classmethod
  async def list_batches(
    cls,
    account_id: str,
    status_group: Optional[str],
    offset: int,
    limit: int,
  ) -> List[TTradeBatch]:
    rows = await cls.operations_service.list_batches(
      account_id,
      status_group=status_group,
      offset=offset,
      limit=limit,
    )
    return [
      TTradeBatch(
        **cls._graphql_kwargs(
          TTradeBatch,
          cls._with_datetimes(row, "created_at", "updated_at"),
        )
      )
      for row in rows
    ]

  @classmethod
  async def list_batches_page(
    cls,
    account_id: str,
    status_group: Optional[str],
    first: int,
    after: Optional[str],
  ) -> TTradeBatchPage:
    cursor_time = None
    cursor_id = None
    if after:
      cursor_time, cursor_id = decode_datetime_cursor(after)
    rows, has_next_page = await cls.operations_service.list_batches_page(
      account_id,
      status_group=status_group,
      cursor_updated_at=cursor_time,
      cursor_id=cursor_id,
      first=first,
    )
    cursors = [encode_cursor(row["updated_at"], row["batch_id"]) for row in rows]
    return TTradeBatchPage(
      items=[
        TTradeBatch(
          **cls._graphql_kwargs(
            TTradeBatch,
            cls._with_datetimes(row, "created_at", "updated_at"),
          )
        )
        for row in rows
      ],
      page_info=PageInfo(
        has_next_page=has_next_page,
        has_previous_page=bool(after),
        start_cursor=cursors[0] if cursors else None,
        end_cursor=cursors[-1] if cursors else None,
      ),
    )

  @classmethod
  async def list_batch_events(
    cls,
    account_id: str,
    batch_id: Optional[str],
    limit: int,
  ) -> List[TTradeBatchEvent]:
    rows = await cls.operations_service.list_events(
      account_id,
      batch_id=batch_id,
      limit=limit,
    )
    return [
      TTradeBatchEvent(
        **cls._graphql_kwargs(
          TTradeBatchEvent,
          cls._with_datetimes(row, "created_at", "applied_at"),
        )
      )
      for row in rows
    ]

  @classmethod
  async def list_batch_events_page(
    cls,
    account_id: str,
    batch_id: Optional[str],
    first: int,
    after: Optional[str],
  ) -> TTradeBatchEventPage:
    cursor_time = None
    cursor_id = None
    if after:
      cursor_time, cursor_id = decode_datetime_cursor(after)
    rows, has_next_page = await cls.operations_service.list_events_page(
      account_id,
      batch_id=batch_id,
      cursor_created_at=cursor_time,
      cursor_id=cursor_id,
      first=first,
    )
    cursors = [encode_cursor(row["created_at"], row["event_id"]) for row in rows]
    return TTradeBatchEventPage(
      items=[
        TTradeBatchEvent(
          **cls._graphql_kwargs(
            TTradeBatchEvent,
            cls._with_datetimes(row, "created_at", "applied_at"),
          )
        )
        for row in rows
      ],
      page_info=PageInfo(
        has_next_page=has_next_page,
        has_previous_page=bool(after),
        start_cursor=cursors[0] if cursors else None,
        end_cursor=cursors[-1] if cursors else None,
      ),
    )

  @classmethod
  async def activate_live(
    cls,
    account_id: str,
    *,
    user_id: str,
    policy_version: int,
    target_stage: TTradeRolloutTarget = TTradeRolloutTarget.CANARY,
    confirmation: str = "",
  ) -> TTradeOperationsMutationResult:
    try:
      target = str(target_stage.value)
      readiness = await cls.operations_service.activate_rollout(
        account_id,
        user_id=user_id,
        acknowledged_policy_version=policy_version,
        target_stage=target,
        confirmation=confirmation,
      )
      return TTradeOperationsMutationResult(
        success=True,
        code=f"{target}_ACTIVATED",
        message="账户已进入正式 LIVE 阶段"
        if target == "LIVE"
        else "账户已进入严格 Canary 阶段",
        readiness=cls._readiness_type(readiness),
      )
    except ValueError as exc:
      await cls.operations_service.record_event(
        account_id,
        "LIVE_ACTIVATION_REJECTED",
        actor_user_id=user_id,
        details={
          "targetStage": str(target_stage.value),
          "reason": str(exc),
        },
      )
      return TTradeOperationsMutationResult(
        success=False,
        code="LIVE_NOT_READY",
        message=str(exc),
      )

  @classmethod
  async def begin_controlled_window(
    cls,
    account_id: str,
    *,
    user_id: str,
    snapshot_id: str,
  ) -> TTradeOperationsMutationResult:
    try:
      readiness = await cls.operations_service.begin_controlled_window(
        account_id,
        user_id=user_id,
        snapshot_id=snapshot_id,
      )
      return TTradeOperationsMutationResult(
        success=True,
        code="CONTROLLED_WINDOW_STARTED",
        message="已基于当前完整快照建立受控交易窗口",
        readiness=cls._readiness_type(readiness),
      )
    except ValueError as exc:
      await cls.operations_service.record_event(
        account_id,
        "CONTROLLED_WINDOW_REJECTED",
        actor_user_id=user_id,
        details={"snapshotId": snapshot_id, "reason": str(exc)},
      )
      return TTradeOperationsMutationResult(
        success=False,
        code="CONTROLLED_WINDOW_NOT_READY",
        message=str(exc),
      )

  @classmethod
  async def pause_entries(
    cls,
    account_id: str,
    reason: str,
    *,
    user_id: str | None = None,
  ) -> TTradeOperationsMutationResult:
    readiness = await cls.operations_service.pause(
      account_id,
      reason,
      user_id=user_id,
    )
    return TTradeOperationsMutationResult(
      success=True,
      code="ENTRIES_PAUSED",
      message="已停止新买入，现有批次继续受保护",
      readiness=cls._readiness_type(readiness),
    )

  @classmethod
  async def trigger_kill_switch(
    cls,
    account_id: str,
    reason: str,
    *,
    user_id: str | None = None,
  ) -> TTradeOperationsMutationResult:
    readiness = await cls.operations_service.kill(
      account_id,
      reason,
      user_id=user_id,
    )
    return TTradeOperationsMutationResult(
      success=True,
      code="KILL_SWITCHED",
      message="kill switch 已触发，现有批次转人工处置",
      readiness=cls._readiness_type(readiness),
    )

  @classmethod
  async def cancel_order(
    cls,
    account_id: str,
    client_order_id: str,
  ) -> TTradeOperationsMutationResult:
    try:
      result = await cls.operations_service.cancel_order(
        account_id,
        client_order_id,
      )
      return TTradeOperationsMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("status") or "CANCEL_REQUESTED"),
        message=str(result.get("message") or ""),
      )
    except ValueError as exc:
      return TTradeOperationsMutationResult(
        success=False,
        code="CANCEL_NOT_ALLOWED",
        message=str(exc),
      )

  @classmethod
  async def prepare_replay(
    cls, account_id: str, start_time: datetime
  ) -> TTradeReplayPreparation:
    data = cls._with_datetimes(
      await cls.replay_service.prepare(account_id, start_time),
      "start_time",
    )
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
    return TTradeReplayPreparation(**cls._graphql_kwargs(TTradeReplayPreparation, data))

  @classmethod
  async def get_replay(cls, run_id: str) -> Optional[TTradeReplay]:
    data = await cls.replay_service.get(run_id)
    return cls._replay_type(data) if data else None

  @classmethod
  async def replay_account_id(cls, run_id: str) -> Optional[str]:
    data = await cls.replay_service.get(run_id)
    account_id = data.get("account_id") if data else None
    return str(account_id) if account_id else None

  @classmethod
  async def replay_history(cls, account_id: str, limit: int) -> List[TTradeReplay]:
    rows = await cls.replay_service.history(account_id, limit)
    return [cls._replay_type(row) for row in rows]

  @classmethod
  async def replay_cycles(
    cls, run_id: str, offset: int, limit: int
  ) -> TTradeReplayCyclePage:
    data = await cls.replay_service.cycles(run_id, offset, limit)
    items = []
    for raw in data.get("items", []):
      item = cls._with_datetimes(raw, "entry_time", "exit_time")
      items.append(TTradeReplayCycle(**cls._graphql_kwargs(TTradeReplayCycle, item)))
    data["items"] = items
    return TTradeReplayCyclePage(**cls._graphql_kwargs(TTradeReplayCyclePage, data))

  @classmethod
  async def start_replay(
    cls, input: TTradeReplayStartInput
  ) -> TTradeReplayMutationResult:
    try:
      replay = await cls._engine_request(
        "T_TRADE_REPLAY_START",
        {"input": vars(input)},
        input.account_id,
      )
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
      replay = await cls._engine_request(
        "T_TRADE_REPLAY_CANCEL",
        {"run_id": run_id},
        run_id,
      )
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
