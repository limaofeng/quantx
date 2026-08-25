"""GraphQL resolver facade for T-trade sessions."""

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import asdict
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Mapping, Optional

import strawberry
from graphql import GraphQLError
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  OperationalAlert as OperationalAlertModel,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
  T_TRADE_EVALUATION_KIND_MATERIAL,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeOpportunityEvaluationRepository,
)
from quantx_infrastructure.services.engine_command_service import (
  EngineCommandIdempotencyError,
  engine_command_service,
)
from quantx_infrastructure.services.operational_alert_service import (
  OperationalAlertService,
)
from quantx_infrastructure.services.qmt_launch_guard import (
  qmt_agent_launch_block_reason,
  qmt_agent_launch_started_at,
  qmt_agent_launch_state,
)
from quantx_infrastructure.services.t_trade_candidate_trace_service import (
  TTradeCandidateTraceService,
)
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  t_trade_monitor_projection_service,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationIdempotencyError,
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
  TTradeCandidateApprovalExpectationInput,
  TTradeCandidateOutcomeAggregate,
  TTradeCandidateStatus,
  TTradeCandidateTrace,
  TTradeCandidateTraceEvent,
  TTradeCandidateTraceIntegrityStatus,
  TTradeCandidateTraceLinks,
  TTradeCandidateTraceMissingReason,
  TTradeCandidateTraceSourceIdentity,
  TTradeDominantPhase,
  TTradeExternalEntryInput,
  TTradeFixedWindowReturnAggregate,
  TTradeGlobalHolding,
  TTradeGlobalMonitor,
  TTradeGlobalMutationResult,
  TTradeGlobalSettingsInput,
  TTradeImportedEntry,
  TTradeLiveReadiness,
  TTradeMomentumPhase,
  TTradeMomentumSignalBranch,
  TTradeMutationResult,
  TTradeOperationsMutationResult,
  TTradePostCandidatePerformance,
  TTradePullbackPhase,
  TTradePullbackSignalBranch,
  TTradeReadinessCheck,
  TTradeReplay,
  TTradeReplayCurvePoint,
  TTradeReplayCycle,
  TTradeReplayCyclePage,
  TTradeReplayDataPreparation,
  TTradeReplayInitialPortfolio,
  TTradeReplayInstrumentResult,
  TTradeReplayMutationResult,
  TTradeReplayPhase,
  TTradeReplayPortfolioSource,
  TTradeReplayPosition,
  TTradeReplayPreparation,
  TTradeReplayReport,
  TTradeReplayStartInput,
  TTradeReplaySummary,
  TTradeRolloutTarget,
  TTradeScoreContribution,
  TTradeSession,
  TTradeSignalBlocker,
  TTradeSignalBlockerAggregate,
  TTradeSignalDataHealth,
  TTradeSignalDiagnosticDenominator,
  TTradeSignalDiagnosticPartition,
  TTradeSignalDiagnostics,
  TTradeSignalEvaluation,
  TTradeSignalEvaluationKind,
  TTradeSignalEvaluationPage,
  TTradeSignalFeatures,
  TTradeSignalFsmDwell,
  TTradeSignalFsmTransition,
  TTradeSignalFunnelStage,
  TTradeSignalGate,
  TTradeSignalPath,
  TTradeSignalPolicy,
  TTradeSignalPolicyInput,
  TTradeSignalPolicyIssue,
  TTradeSignalPolicyPreviewInput,
  TTradeSignalPolicyPreviewResult,
  TTradeSignalReason,
  TTradeSignalScoreBucket,
  TTradeSignalSnapshot,
  TTradeSignalVersionGroup,
  TTradeTimeExitMode,
)
from quantx_api.gqlapi.utils.cursor import decode_datetime_cursor, encode_cursor

logger = logging.getLogger(__name__)

_T_TRADE_GLOBAL_SAVE_APPLIED_CODE = "CONFIG_APPLIED"
_T_TRADE_GLOBAL_SAVE_PENDING_CODE = "CONFIG_APPLY_PENDING"
_T_TRADE_GLOBAL_SAVE_COMMAND_PENDING_CODE = "CONFIG_SAVE_COMMAND_PENDING"
_T_TRADE_GLOBAL_GET_COMMAND_PENDING_CODE = "T_TRADE_GLOBAL_GET_COMMAND_PENDING"
_T_TRADE_GLOBAL_GET_CAPACITY_CODE = "T_TRADE_GLOBAL_GET_SINGLE_FLIGHT_CAPACITY"
_T_TRADE_GLOBAL_GET_MAX_SINGLE_FLIGHTS = 4096
_T_TRADE_GLOBAL_RECONCILE_COMMAND_PENDING_CODE = (
  "T_TRADE_GLOBAL_RECONCILE_COMMAND_PENDING"
)
_T_TRADE_SIGNAL_POLICY_PREVIEW_COMMAND_PENDING_CODE = (
  "T_TRADE_SIGNAL_POLICY_PREVIEW_COMMAND_PENDING"
)
_T_TRADE_APPROVE_ENTRY_COMMAND_PENDING_CODE = "T_TRADE_APPROVE_ENTRY_COMMAND_PENDING"
_T_TRADE_REJECT_ENTRY_COMMAND_PENDING_CODE = "T_TRADE_REJECT_ENTRY_COMMAND_PENDING"
_T_TRADE_IMPORT_EXTERNAL_ENTRY_COMMAND_PENDING_CODE = (
  "T_TRADE_IMPORT_EXTERNAL_ENTRY_COMMAND_PENDING"
)
_T_TRADE_STOP_SESSION_COMMAND_PENDING_CODE = "T_TRADE_STOP_SESSION_COMMAND_PENDING"
_T_TRADE_REPLAY_START_COMMAND_PENDING_CODE = "T_TRADE_REPLAY_START_COMMAND_PENDING"
_T_TRADE_REPLAY_START_OUTCOME_UNKNOWN_CODE = "T_TRADE_REPLAY_START_OUTCOME_UNKNOWN"
_T_TRADE_REPLAY_CANCEL_COMMAND_PENDING_CODE = "T_TRADE_REPLAY_CANCEL_COMMAND_PENDING"

_cold_global_get_keys: dict[str, str] = {}
_cold_global_get_keys_lock = asyncio.Lock()


async def _reserve_cold_global_get_key(account_id: str) -> str:
  async with _cold_global_get_keys_lock:
    existing = _cold_global_get_keys.get(account_id)
    if existing is not None:
      return existing
    if len(_cold_global_get_keys) >= _T_TRADE_GLOBAL_GET_MAX_SINGLE_FLIGHTS:
      raise GraphQLError(
        "全局做 T 监控读取请求过多，请稍后重试",
        extensions={
          "code": _T_TRADE_GLOBAL_GET_CAPACITY_CODE,
          "retryable": True,
        },
      )
    key = f"t-trade-global-get:{account_id}:{uuid.uuid4()}"
    _cold_global_get_keys[account_id] = key
    return key


async def _release_cold_global_get_key(account_id: str, key: str) -> None:
  async with _cold_global_get_keys_lock:
    if _cold_global_get_keys.get(account_id) == key:
      _cold_global_get_keys.pop(account_id, None)


class EngineCommandPendingError(RuntimeError):
  """The Engine has not yet established whether a command was applied."""

  def __init__(self, receipt: Any, command_type: str = "") -> None:
    self.receipt = receipt
    self.command_type = str(command_type or getattr(receipt, "command_type", ""))
    super().__init__(
      "Engine 命令仍在处理中，尚不知是否已提交: "
      f"{getattr(receipt, 'message_id', '')}"
    )


def _pending_command_message(exc: EngineCommandPendingError, action: str) -> str:
  return (
    f"{action}请求仍在处理中，尚不知是否已提交；请稍后查询或重试。"
    f"（命令 {getattr(exc.receipt, 'message_id', '')}）"
  )


def stable_command_payload_digest(payload: Mapping[str, Any]) -> str:
  """Return a deterministic SHA-256 for a normalized JSON command payload.

  ``sort_keys`` makes retries independent of mapping insertion order and
  ``allow_nan=False`` deliberately rejects non-finite floats instead of
  allowing JSON's non-standard ``NaN``/``Infinity`` spellings into an
  idempotency identity.
  """

  try:
    encoded = json.dumps(
      dict(payload),
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
      allow_nan=False,
    )
  except (TypeError, ValueError) as exc:
    raise ValueError("command payload must contain only finite JSON values") from exc
  return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validated_client_idempotency_key(value: Any) -> str:
  normalized = str(value or "").strip()
  if not normalized:
    raise ValueError("idempotency_key must not be blank")
  if len(normalized) > 128:
    raise ValueError("idempotency_key must be at most 128 characters")
  if any(
    ord(character) < 0x20 or ord(character) == 0x7F for character in normalized
  ):
    raise ValueError("idempotency_key must not contain control characters")
  return normalized


def _namespaced_client_idempotency_key(
  operation: str,
  account_id: str,
  client_key: Any,
) -> str:
  normalized = _validated_client_idempotency_key(client_key)
  binding = f"{operation}\x00{account_id}\x00{normalized}"
  digest = hashlib.sha256(binding.encode("utf-8")).hexdigest()
  return f"t-trade:{operation}:{digest}"


_DIAGNOSTIC_FUNNEL_UNITS = {
  "ELIGIBLE": "MATERIAL_EVENTS",
  "DATA_READY": "MATERIAL_EVENTS",
  "PATTERN": "RUN_SCOPED_EPISODES",
  "PREVIEW": "RUN_SCOPED_EPISODES",
  "CANDIDATE": "RUN_SCOPED_CANDIDATES",
  "TRADE_INTENT": "TRADE_INTENTS",
  "APPROVED": "APPROVED_INTENTS",
  "ORDERED": "ORDERS",
  "FILLED": "FILLS",
}


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
      raise EngineCommandPendingError(receipt, command_type)
    return dict(receipt.result or {})

  @staticmethod
  async def _existing_engine_request(
    message_id: str,
    command_type: str,
  ) -> dict[str, Any]:
    receipt = await engine_command_service.wait(message_id)
    if receipt.status == "FAILED":
      raise ValueError(receipt.error or f"{command_type} failed")
    if receipt.status != "SUCCEEDED":
      raise EngineCommandPendingError(receipt, command_type)
    return dict(receipt.result or {})

  @staticmethod
  def _datetime(value):
    if isinstance(value, datetime) or value is None:
      return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

  @classmethod
  def _with_utc_datetimes(cls, data: dict, *fields: str) -> dict:
    payload = cls._with_datetimes(data, *fields)
    for field in fields:
      value = payload.get(field)
      if value is not None and value.tzinfo is None:
        payload[field] = value.replace(tzinfo=timezone.utc)
    return payload

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

  @staticmethod
  def _command_value(value: Any) -> Any:
    if isinstance(value, Enum):
      return value.value
    if isinstance(value, Mapping):
      return {
        str(key): TTradeResolver._command_value(item) for key, item in value.items()
      }
    if isinstance(value, (list, tuple)):
      return [TTradeResolver._command_value(item) for item in value]
    return value

  @classmethod
  def _command_input(cls, value: Any) -> dict[str, Any]:
    return dict(cls._command_value(asdict(value)))

  @staticmethod
  def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
      raise ValueError(f"{field_name} is required")
    return normalized

  @staticmethod
  def _required_non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
      raise ValueError(f"{field_name} must be an integer")
    normalized = int(value)
    if normalized < 0:
      raise ValueError(f"{field_name} must be non-negative")
    return normalized

  @staticmethod
  def _required_bool(value: Any, field_name: str) -> bool:
    """Reject truthy strings/numbers instead of changing signal semantics."""
    if not isinstance(value, bool):
      raise ValueError(f"{field_name} must be a boolean")
    return value

  @classmethod
  def _required_decimal_string(cls, value: Any, field_name: str) -> str:
    normalized = str(value).strip() if value is not None else ""
    if not normalized:
      raise ValueError(f"{field_name} is required")
    if not normalized.isdecimal():
      raise ValueError(f"{field_name} must be an unsigned decimal string")
    return normalized

  @staticmethod
  def _optional_number(value: Any) -> Optional[float]:
    if value is None:
      return None
    normalized = float(value)
    if normalized != normalized or normalized in {float("inf"), float("-inf")}:
      raise ValueError("signal number must be finite")
    return normalized

  @classmethod
  def _required_number(cls, value: Any, field_name: str) -> float:
    normalized = cls._optional_number(value)
    if normalized is None:
      raise ValueError(f"{field_name} is required")
    return normalized

  @classmethod
  def _millis_datetime(cls, value: Any, field_name: str) -> datetime:
    milliseconds = cls._required_non_negative_int(value, field_name)
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)

  @classmethod
  def _optional_snapshot_datetime(cls, value: Any) -> Optional[datetime]:
    if value is None:
      return None
    if isinstance(value, datetime):
      return value
    if isinstance(value, (int, float)) or str(value).isdecimal():
      return cls._millis_datetime(value, "candidate timestamp")
    return cls._datetime(value)

  @classmethod
  def _signal_policy_type(cls, raw: Mapping[str, Any]) -> TTradeSignalPolicy:
    payload = dict(raw)
    input_fields = {item.name for item in dataclass_fields(TTradeSignalPolicyInput)}
    expected_fields = {
      "policy_version",
      "feature_schema_version",
      *input_fields,
    }
    unknown = sorted(set(payload) - expected_fields)
    if unknown:
      raise ValueError(f"signal policy has unknown fields: {', '.join(unknown)}")
    missing = sorted(input_fields.difference(payload))
    if missing:
      raise ValueError(f"signal policy missing fields: {', '.join(missing)}")
    policy_version = cls._required_text(payload.get("policy_version"), "policy_version")
    feature_schema_version = cls._required_non_negative_int(
      payload.get("feature_schema_version"),
      "feature_schema_version",
    )
    policy = OpportunityPolicy(
      policy_version=policy_version,
      feature_schema_version=feature_schema_version,
      **{name: payload[name] for name in input_fields},
    )
    return cls._signal_policy_from_domain(policy)

  @staticmethod
  def _signal_policy_from_domain(policy: OpportunityPolicy) -> TTradeSignalPolicy:
    payload = policy.to_dict()
    payload["feature_schema_version"] = str(policy.feature_schema_version)
    return TTradeSignalPolicy(
      **TTradeResolver._graphql_kwargs(TTradeSignalPolicy, payload)
    )

  @staticmethod
  def _signal_label(code: str) -> str:
    labels = {
      "DATA_READY": "数据可用于决策",
      "QUOTE_FRESH": "报价新鲜",
      "CONTINUOUS_SESSION": "连续交易时段",
      "TRADING_SESSION": "处于规则允许时段且候选有效期不跨边界",
      "REFERENCE_PROFILE_AVAILABLE": "个股画像可用",
      "REFERENCE_PROFILE_SCHEMA_COMPATIBLE": "画像版本兼容",
      "REFERENCE_PROFILE_CAUSAL": "画像时点无未来数据",
      "PULLBACK_PATTERN_NOT_CONFIRMED": "回撤反弹形态未确认",
      "MOMENTUM_PATTERN_NOT_CONFIRMED": "动量加速形态未确认",
      "SCORE_UNAVAILABLE": "机会分不可计算",
      "SCORE_BELOW_CANDIDATE": "机会分未到候选阈值",
      "MINIMUM_COVERAGE_NOT_REACHED": "因果窗口尚未热满",
      "QUOTE_STALE": "报价陈旧",
      "CONTINUITY_GENERATION_CHANGED": "行情连续代际已变化",
      "ORDER_BOOK_UNAVAILABLE": "盘口不可用",
      "CUMULATIVE_TURNOVER_UNAVAILABLE": "累计成交额不可用",
      "SPARSE_SAMPLE_COVERAGE": "样本覆盖稀疏",
      "PULLBACK_COVERAGE_READY": "回撤路径样本与覆盖已就绪",
      "PULLBACK_SPREAD_ACCEPTABLE": "回撤路径价差可接受",
      "MOMENTUM_ENABLED": "动量路径已启用",
      "MOMENTUM_COVERAGE_READY": "动量路径样本与覆盖已就绪",
      "MOMENTUM_BASELINE_READY": "动量成交基线已就绪",
      "MOMENTUM_MOVE_DURATION_READY": "动量持续时间已满足",
      "MOMENTUM_NEAR_WINDOW_HIGH": "价格仍接近动量窗口高点",
      "MOMENTUM_TURNOVER_AVAILABLE": "动量成交额倍率可计算",
      "MOMENTUM_SPREAD_ACCEPTABLE": "动量路径价差可接受",
      "MOMENTUM_NOT_OVEREXTENDED": "动量未进入过度延伸区",
      "PULLBACK_DEPTH": "回撤深度",
      "REBOUND_STRENGTH": "反弹强度",
      "LOW_STABILIZATION": "低点稳定时长",
      "TURN_SLOPE": "拐点反弹斜率",
      "PULLBACK_VWAP_POSITION": "回撤路径 VWAP 位置",
      "PULLBACK_LIQUIDITY": "回撤路径流动性",
      "PULLBACK_VOLUME_CONFIRMATION": "回撤路径成交额确认",
      "DATA_QUALITY_PENALTY": "数据质量惩罚",
      "PULLBACK_CHASE_PENALTY": "回撤路径追高惩罚",
      "MOMENTUM_RISE": "动量涨幅",
      "MOMENTUM_TURNOVER": "动量成交额加速",
      "MOMENTUM_SLOPE": "动量涨速",
      "HIGH_PERSISTENCE": "窗口高位持续性",
      "MOMENTUM_VWAP_REGIME": "动量 VWAP 甜蜜区",
      "MOMENTUM_LIQUIDITY": "动量路径流动性",
      "BOOK_IMBALANCE": "盘口不平衡",
      "MOMENTUM_OVEREXTENSION_PENALTY": "动量过度延伸惩罚",
    }
    for prefix, label in (
      ("PULLBACK_REQUIRED_", "回撤路径必需字段"),
      ("MOMENTUM_REQUIRED_", "动量路径必需字段"),
      ("REQUIRED_FIELD_", "数据质量必需字段"),
    ):
      if code.startswith(prefix) and code.endswith("_UNAVAILABLE"):
        field = code[len(prefix) : -len("_UNAVAILABLE")]
        return f"{label}不可用（{field}）"
      if code.startswith(prefix):
        field = code[len(prefix) :]
        return f"{label}（{field}）"
    return labels.get(code, f"未注册状态（{code}）")

  @classmethod
  def _reason_type(cls, raw: Any) -> TTradeSignalReason:
    if isinstance(raw, Mapping):
      code = cls._required_text(raw.get("code"), "reason.code")
      label = str(raw.get("label") or cls._signal_label(code))
      detail = str(raw.get("detail") or "")
    else:
      code = cls._required_text(raw, "reason.code")
      label = cls._signal_label(code)
      detail = ""
    return TTradeSignalReason(code=code, label=label, detail=detail)

  @classmethod
  def _gate_type(cls, raw: Mapping[str, Any]) -> TTradeSignalGate:
    code = cls._required_text(raw.get("code"), "gate.code")
    return TTradeSignalGate(
      code=code,
      label=str(raw.get("label") or cls._signal_label(code)),
      passed=cls._required_bool(raw.get("passed"), "gate.passed"),
      observed_value=cls._optional_number(raw.get("observed_value")),
      required_value=cls._optional_number(raw.get("required_value")),
      detail=str(raw.get("detail") or ""),
    )

  @classmethod
  def _contribution_type(cls, raw: Mapping[str, Any]) -> TTradeScoreContribution:
    code = cls._required_text(raw.get("code") or raw.get("name"), "component.code")
    return TTradeScoreContribution(
      code=code,
      label=str(raw.get("label") or cls._signal_label(code)),
      points=cls._required_number(
        raw.get("points", raw.get("contribution")),
        "component.points",
      ),
      max_points=cls._required_number(
        raw.get("max_points", raw.get("weight")),
        "component.max_points",
      ),
      observed_value=cls._optional_number(
        raw.get("observed_value", raw.get("raw_value"))
      ),
      target_value=cls._optional_number(raw.get("target_value")),
      detail=str(raw.get("detail") or ""),
    )

  @classmethod
  def _blocker_type(cls, raw: Any) -> TTradeSignalBlocker:
    if isinstance(raw, Mapping):
      code = cls._required_text(raw.get("code"), "blocker.code")
      label = str(raw.get("label") or cls._signal_label(code))
      detail = str(raw.get("detail") or "")
    else:
      code = cls._required_text(raw, "blocker.code")
      label = cls._signal_label(code)
      detail = ""
    return TTradeSignalBlocker(code=code, label=label, detail=detail)

  @classmethod
  def _features_type(cls, raw: Mapping[str, Any]) -> TTradeSignalFeatures:
    if "sample_count" not in raw:
      raise ValueError("features.sample_count is required")
    payload: dict[str, Any] = {}
    for item in dataclass_fields(TTradeSignalFeatures):
      if item.name == "sample_count":
        payload[item.name] = cls._required_non_negative_int(
          raw.get(item.name), item.name
        )
      else:
        payload[item.name] = cls._optional_number(raw.get(item.name))
    return TTradeSignalFeatures(**payload)

  @classmethod
  def _pullback_branch_type(cls, raw: Mapping[str, Any]) -> TTradePullbackSignalBranch:
    return TTradePullbackSignalBranch(
      phase=TTradePullbackPhase(cls._required_text(raw.get("phase"), "pullback.phase")),
      score=cls._optional_number(raw.get("score")),
      preview=cls._required_bool(raw.get("preview"), "pullback.preview"),
      candidate_ready=cls._required_bool(
        raw.get("candidate_ready"), "pullback.candidate_ready"
      ),
      hard_gates=[cls._gate_type(item) for item in list(raw.get("hard_gates") or [])],
      score_contributions=[
        cls._contribution_type(item) for item in list(raw.get("components") or [])
      ],
      blockers=[cls._blocker_type(item) for item in list(raw.get("blockers") or [])],
    )

  @classmethod
  def _momentum_branch_type(cls, raw: Mapping[str, Any]) -> TTradeMomentumSignalBranch:
    return TTradeMomentumSignalBranch(
      phase=TTradeMomentumPhase(cls._required_text(raw.get("phase"), "momentum.phase")),
      score=cls._optional_number(raw.get("score")),
      preview=cls._required_bool(raw.get("preview"), "momentum.preview"),
      candidate_ready=cls._required_bool(
        raw.get("candidate_ready"), "momentum.candidate_ready"
      ),
      hard_gates=[cls._gate_type(item) for item in list(raw.get("hard_gates") or [])],
      score_contributions=[
        cls._contribution_type(item) for item in list(raw.get("components") or [])
      ],
      blockers=[cls._blocker_type(item) for item in list(raw.get("blockers") or [])],
    )

  @staticmethod
  def _dominant_phase(
    selected_path: Optional[TTradeSignalPath],
    pullback_phase: TTradePullbackPhase,
    momentum_phase: TTradeMomentumPhase,
  ) -> TTradeDominantPhase:
    if selected_path == TTradeSignalPath.PULLBACK_REBOUND:
      return TTradeDominantPhase(f"PULLBACK_{pullback_phase.value}")
    if selected_path == TTradeSignalPath.MOMENTUM_ACCELERATION:
      return TTradeDominantPhase(f"MOMENTUM_{momentum_phase.value}")
    return TTradeDominantPhase.NONE

  @classmethod
  def _signal_snapshot_type(
    cls,
    raw: Any,
  ) -> Optional[TTradeSignalSnapshot]:
    if raw is None:
      return None
    try:
      payload = dict(raw)
      features = cls._features_type(dict(payload["features"]))
      pullback = cls._pullback_branch_type(dict(payload["pullback"]))
      momentum = cls._momentum_branch_type(dict(payload["momentum"]))
      selected_path_value = cls._required_text(
        payload.get("selected_path"), "selected_path"
      )
      selected_path = (
        None if selected_path_value == "NONE" else TTradeSignalPath(selected_path_value)
      )
      source_time_ms = cls._required_decimal_string(
        payload.get("source_time_ms"), "source_time_ms"
      )
      evaluated_at_ms = cls._required_decimal_string(
        payload.get("evaluated_at_ms"), "evaluated_at_ms"
      )
      evaluated_at = cls._millis_datetime(evaluated_at_ms, "evaluated_at_ms")
      source_at = cls._millis_datetime(source_time_ms, "source_time_ms")
      if source_at > evaluated_at:
        raise ValueError("signal source time cannot be later than evaluated time")
      data_age_ms = payload.get("data_age_ms")
      if data_age_ms is None:
        age = int(evaluated_at_ms) - int(source_time_ms)
        data_age_ms = age if age >= 0 else None
      elif data_age_ms is not None:
        data_age_ms = cls._required_non_negative_int(data_age_ms, "data_age_ms")
      pullback_phase = pullback.phase
      momentum_phase = momentum.phase
      selected_components = (
        pullback.score_contributions
        if selected_path == TTradeSignalPath.PULLBACK_REBOUND
        else momentum.score_contributions
        if selected_path == TTradeSignalPath.MOMENTUM_ACCELERATION
        else []
      )
      raw_contributions = payload.get("score_contributions")
      score_contributions = (
        [cls._contribution_type(item) for item in list(raw_contributions)]
        if raw_contributions is not None
        else list(selected_components)
      )
      candidate_id = str(payload.get("candidate_id") or "").strip() or None
      episode_id = str(payload.get("episode_id") or "").strip() or None
      linked_intent = str(payload.get("pending_entry_intent_id") or "").strip() or None
      raw_top_blockers = payload.get("top_blockers")
      if raw_top_blockers is None:
        raw_top_blockers = [
          *list(payload.get("blockers") or []),
          *list(payload.get("external_blockers") or []),
        ]
      coverage = features.coverage_seconds
      scores = [
        pullback.score,
        momentum.score,
        cls._optional_number(payload.get("opportunity_score")),
      ]
      thresholds = [
        cls._required_number(payload.get("preview_threshold"), "preview_threshold"),
        cls._required_number(payload.get("candidate_threshold"), "candidate_threshold"),
        cls._required_number(
          payload.get("revalidate_threshold"), "revalidate_threshold"
        ),
        cls._required_number(payload.get("rearm_threshold"), "rearm_threshold"),
      ]
      if any(value is not None and not 0 <= value <= 100 for value in scores):
        raise ValueError("signal scores must be between 0 and 100")
      if not (
        0 <= thresholds[3]
        < thresholds[0]
        < thresholds[2]
        < thresholds[1]
        <= 100
      ):
        raise ValueError("signal thresholds are not strictly ordered")
      if coverage is not None and coverage < 0:
        raise ValueError("signal coverage cannot be negative")
      return TTradeSignalSnapshot(
        instrument_code=cls._required_text(
          payload.get("instrument_code"), "instrument_code"
        ),
        trade_date=cls._required_text(payload.get("trade_date"), "trade_date"),
        evaluated_at=evaluated_at,
        source_at=source_at,
        source_time_ms=source_time_ms,
        tick_ordinal=cls._required_decimal_string(
          payload.get("tick_ordinal"), "tick_ordinal"
        ),
        continuity_generation=cls._required_decimal_string(
          payload.get("continuity_generation"), "continuity_generation"
        ),
        data_age_ms=data_age_ms,
        window_coverage_seconds=(
          int(round(coverage)) if coverage is not None else None
        ),
        sample_count=features.sample_count,
        data_health=TTradeSignalDataHealth(
          cls._required_text(payload.get("data_health"), "data_health")
        ),
        data_health_reasons=[
          cls._reason_type(item)
          for item in list(payload.get("data_health_reasons") or [])
        ],
        pullback_phase=pullback_phase,
        momentum_phase=momentum_phase,
        dominant_phase=cls._dominant_phase(
          selected_path, pullback_phase, momentum_phase
        ),
        selected_path=selected_path,
        pullback_score=pullback.score,
        momentum_score=momentum.score,
        opportunity_score=scores[2],
        preview_threshold=thresholds[0],
        candidate_threshold=thresholds[1],
        revalidate_threshold=thresholds[2],
        rearm_threshold=thresholds[3],
        features=features,
        pullback=pullback,
        momentum=momentum,
        hard_gates=[
          cls._gate_type(item) for item in list(payload.get("hard_gates") or [])
        ],
        score_contributions=score_contributions,
        top_blockers=[cls._blocker_type(item) for item in list(raw_top_blockers or [])],
        episode_id=strawberry.ID(episode_id) if episode_id else None,
        candidate_id=strawberry.ID(candidate_id) if candidate_id else None,
        candidate_fingerprint=(
          str(payload.get("candidate_fingerprint") or "").strip() or None
        ),
        candidate_status=TTradeCandidateStatus(
          cls._required_text(payload.get("candidate_status"), "candidate_status")
        ),
        candidate_created_at=cls._optional_snapshot_datetime(
          payload.get("candidate_created_at_ms", payload.get("candidate_created_at"))
        ),
        candidate_expires_at=cls._optional_snapshot_datetime(
          payload.get("candidate_expires_at_ms", payload.get("candidate_expires_at"))
        ),
        pending_entry_intent_id=(
          strawberry.ID(linked_intent) if linked_intent else None
        ),
        signal_version=cls._required_non_negative_int(
          payload.get("signal_version"), "signal_version"
        ),
        candidate_state_version=cls._required_non_negative_int(
          payload.get("candidate_state_version"), "candidate_state_version"
        ),
        state_schema_version=cls._required_text(
          payload.get("state_schema_version"), "state_schema_version"
        ),
        feature_schema_version=cls._required_text(
          payload.get("feature_schema_version"), "feature_schema_version"
        ),
        policy_version=cls._required_text(
          payload.get("policy_version"), "policy_version"
        ),
        config_version=cls._required_non_negative_int(
          payload.get("config_version"), "config_version"
        ),
        profile_version=(
          str(
            payload.get("profile_version")
            or payload.get("reference_profile_version")
            or ""
          ).strip()
          or None
        ),
        profile_fingerprint=(
          str(payload.get("profile_fingerprint") or "").strip() or None
        ),
      )
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
      logger.warning("拒绝不完整的做 T V3 信号快照: %s", exc)
      return None

  @classmethod
  def _session_type(cls, data: dict) -> TTradeSession:
    payload = cls._with_datetimes(data, "created_at", "updated_at")
    payload["time_exit_mode"] = cls._time_exit_mode(payload.get("time_exit_mode"))
    payload["created_at"] = cls._datetime(payload.get("created_at"))
    payload["updated_at"] = cls._datetime(payload.get("updated_at"))
    payload["signal_snapshot"] = cls._signal_snapshot_type(
      payload.get("signal_snapshot")
    )
    return TTradeSession(**cls._graphql_kwargs(TTradeSession, payload))

  @classmethod
  def _replay_type(cls, data: dict) -> TTradeReplay:
    payload = cls._with_datetimes(
      data,
      "start_time",
      "end_time",
      "processed_until",
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
    payload["phase"] = TTradeReplayPhase(
      str(payload.get("phase") or "VALIDATING_PORTFOLIO").upper()
    )
    payload.setdefault("phase_progress_pct", 0.0)
    payload.setdefault("phase_message", "")
    preparation = {
      "status": "PENDING",
      "required_instruments": [],
      "required_periods": ["tick"],
      "total_windows": 0,
      "completed_windows": 0,
      **dict(payload.get("data_preparation") or {}),
    }
    payload["data_preparation"] = TTradeReplayDataPreparation(
      **cls._graphql_kwargs(TTradeReplayDataPreparation, preparation)
    )
    initial_portfolio = {
      "source": "SNAPSHOT",
      "as_of": payload.get("snapshot_date") or payload.get("start_time"),
      "snapshot_id": payload.get("snapshot_id"),
      "cash_available": 0.0,
      "total_asset": 0.0,
      "positions": [],
      **dict(payload.get("initial_portfolio") or {}),
    }
    initial_portfolio["source"] = TTradeReplayPortfolioSource(
      str(initial_portfolio.get("source") or "SNAPSHOT").upper()
    )
    initial_portfolio["as_of"] = cls._datetime(initial_portfolio.get("as_of"))
    initial_portfolio["positions"] = [
      TTradeReplayPosition(
        **cls._graphql_kwargs(TTradeReplayPosition, item)
      )
      for item in initial_portfolio.get("positions", [])
    ]
    payload["initial_portfolio"] = TTradeReplayInitialPortfolio(
      **cls._graphql_kwargs(TTradeReplayInitialPortfolio, initial_portfolio)
    )
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
    payload["signal_policy"] = cls._signal_policy_type(
      dict(payload.get("signal_policy") or {})
    )
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
      "limit_up_touch_exit_enabled": True,
      "limit_up_touch_tolerance_ticks": 0,
    }
    for key, value in defaults.items():
      payload.setdefault(key, value)
    return TTradeGlobalMonitor(**cls._graphql_kwargs(TTradeGlobalMonitor, payload))

  @classmethod
  def _apply_qmt_launch_block_to_monitor(cls, data: dict) -> dict:
    """Mask stale live readiness in a persisted monitor projection."""

    reason_code = qmt_agent_launch_block_reason()
    if reason_code is None and qmt_agent_launch_state() == "LAUNCH_ALLOWED":
      launch_started_at = qmt_agent_launch_started_at()
      readiness = data.get("readiness")
      try:
        checked_at = (
          cls._datetime(readiness.get("checked_at"))
          if isinstance(readiness, dict) and readiness.get("checked_at")
          else None
        )
      except (TypeError, ValueError):
        checked_at = None
      if checked_at is not None and checked_at.tzinfo is not None:
        checked_at = checked_at.astimezone(timezone.utc).replace(tzinfo=None)
      if (
        launch_started_at is None
        or checked_at is None
        or checked_at < launch_started_at
      ):
        reason_code = "QMT_LAUNCH_PENDING_CURRENT_HEARTBEAT"
    if reason_code is None:
      return data

    payload = dict(data)
    message = f"QMT Agent 本地启动被阻断，实盘能力已关闭（{reason_code}）"
    payload.update(
      {
        "agent_status": "BLOCKED",
        "can_approve": False,
        "can_activate_live": False,
      }
    )
    payload["blocked_reasons"] = list(
      dict.fromkeys([*list(payload.get("blocked_reasons") or []), message])
    )

    raw_readiness = payload.get("readiness")
    if not isinstance(raw_readiness, dict) or not raw_readiness:
      # Old/cold projections may not contain the nested readiness object. The
      # top-level monitor fields above are sufficient to close live controls;
      # do not fabricate a partial GraphQL object with missing required fields.
      return payload
    readiness = dict(raw_readiness)
    readiness.update(
      {
        "ready": False,
        "status": "BLOCKED",
        "preparation_ready": False,
        "automation_ready": False,
        "agent_status": "BLOCKED",
        "agent_device_id": None,
        "agent_mode": "offline",
        "protocol_version": "",
        "can_approve": False,
        "can_activate_live": False,
      }
    )
    readiness["blocked_reasons"] = list(
      dict.fromkeys([*list(readiness.get("blocked_reasons") or []), message])
    )
    readiness["preparation_blocked_reasons"] = list(
      dict.fromkeys(
        [*list(readiness.get("preparation_blocked_reasons") or []), message]
      )
    )
    checks = []
    launch_check_seen = False
    for raw_check in list(readiness.get("checks") or []):
      check = dict(raw_check)
      code = str(check.get("code") or "")
      if code in {
        "QMT_AGENT_LAUNCH_ALLOWED",
        "LIVE_AGENT_READY",
        "AGENT_MODE_LIVE",
        "PROTOCOL_1_1",
      }:
        check.update(
          {
            "passed": False,
            "message": message,
            "scope": "PREPARATION",
          }
        )
      if code == "QMT_AGENT_LAUNCH_ALLOWED":
        launch_check_seen = True
      checks.append(check)
    if not launch_check_seen:
      checks.append(
        {
          "code": "QMT_AGENT_LAUNCH_ALLOWED",
          "passed": False,
          "message": message,
          "scope": "PREPARATION",
        }
      )
    readiness["checks"] = checks
    payload["readiness"] = readiness
    return payload

  @classmethod
  def _readiness_type(cls, data: dict) -> TTradeLiveReadiness:
    payload = cls._with_utc_datetimes(
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
      payload = {"account_id": account_id}
      idempotency_key = await _reserve_cold_global_get_key(account_id)
      try:
        monitor = await cls._engine_request(
          "T_TRADE_GLOBAL_GET",
          payload,
          account_id,
          # A cold read is a projection rebuild, not a repeatable business
          # operation.  A process-local single-flight key only coalesces
          # concurrent wakeups; terminal results are always released so a
          # later projection loss gets a fresh one-shot command identity.
          idempotency_key=idempotency_key,
        )
      except EngineCommandPendingError as exc:
        raise GraphQLError(
          _pending_command_message(exc, "全局做 T 监控读取"),
          extensions={
            "code": _T_TRADE_GLOBAL_GET_COMMAND_PENDING_CODE,
            "retryable": True,
            "commandId": str(getattr(exc.receipt, "message_id", "")),
          },
        ) from None
      except EngineCommandIdempotencyError as exc:
        await _release_cold_global_get_key(account_id, idempotency_key)
        raise GraphQLError(
          "全局做 T 监控读取请求的幂等键已被其他命令占用，请重新发起请求",
          extensions={"code": exc.code, "retryable": False},
        ) from None
      except Exception:
        await _release_cold_global_get_key(account_id, idempotency_key)
        raise
      else:
        await _release_cold_global_get_key(account_id, idempotency_key)
    else:
      # A projection hit is a terminal read for this process-local wakeup.
      # Do not retain a stale key until the bounded map fills up.
      stale_key = _cold_global_get_keys.get(account_id)
      if stale_key is not None:
        await _release_cold_global_get_key(account_id, stale_key)
    monitor = cls._apply_qmt_launch_block_to_monitor(monitor)
    return cls._global_monitor_type(monitor)

  @classmethod
  async def save_global_monitor(
    cls, input: TTradeGlobalSettingsInput
  ) -> TTradeGlobalMutationResult:
    try:
      if input.expected_config_version < 0:
        raise ValueError("expected_config_version must be non-negative")
      command_input = cls._normalized_command_input(input)
      idempotency_digest = stable_command_payload_digest(command_input)
      command_result = await cls._engine_request(
        "T_TRADE_GLOBAL_SAVE",
        {"input": command_input},
        input.account_id,
        idempotency_key=f"t-trade-global-save:{idempotency_digest}",
      )
      monitor = dict(command_result)
      apply_code = str(command_result.get("apply_code") or "").strip()
      apply_status = str(command_result.get("apply_status") or "").strip()
      last_error = str(command_result.get("last_error") or "").strip()
      committed_version = command_result.get("config_version")
      try:
        committed_version = int(committed_version)
      except (TypeError, ValueError, OverflowError):
        committed_version = None

      pending = bool(last_error) or apply_status == "PENDING" or bool(
        apply_code == _T_TRADE_GLOBAL_SAVE_PENDING_CODE
      )
      if pending:
        # The Engine command row is intentionally immutable.  A retry of a
        # previously pending command may therefore replay its old result after
        # periodic/manual reconcile has already recovered the durable config.
        # Only promote that replay to APPLIED when the current projection is
        # exactly the version committed by this command and has no error.
        try:
          latest = await t_trade_monitor_projection_service.get(input.account_id)
        except Exception as exc:
          logger.warning(
            "读取做 T 保存后的最新监控投影失败: account=%s error=%s",
            input.account_id,
            exc,
          )
          latest = None
        latest_version = None
        if latest is not None:
          try:
            latest_version = int(latest.get("config_version"))
          except (TypeError, ValueError, OverflowError):
            latest_version = None
        if (
          latest is not None
          and committed_version is not None
          and latest_version == committed_version
          and not str(latest.get("last_error") or "").strip()
        ):
          monitor = dict(latest)
          return TTradeGlobalMutationResult(
            success=True,
            code=_T_TRADE_GLOBAL_SAVE_APPLIED_CODE,
            message="全局做 T 监控设置已保存并应用",
            monitor=cls._global_monitor_type(monitor),
          )
        if (
          latest is not None
          and committed_version is not None
          and latest_version == committed_version
        ):
          monitor = dict(latest)
        return TTradeGlobalMutationResult(
          success=False,
          code=_T_TRADE_GLOBAL_SAVE_PENDING_CODE,
          message=(
            "配置已保存，但尚未应用；请稍后重试。"
            f"{last_error or str(monitor.get('last_error') or '').strip()}"
          ),
          monitor=cls._global_monitor_type(monitor),
        )
      return TTradeGlobalMutationResult(
        success=True,
        code=_T_TRADE_GLOBAL_SAVE_APPLIED_CODE,
        message="全局做 T 监控设置已保存并应用",
        monitor=cls._global_monitor_type(monitor),
      )
    except EngineCommandPendingError as exc:
      return TTradeGlobalMutationResult(
        success=False,
        code=_T_TRADE_GLOBAL_SAVE_COMMAND_PENDING_CODE,
        message=(
          "请求仍在处理，尚不知是否已提交；请保留当前草稿，"
          "稍后查询或重试。"
          f"（命令 {getattr(exc.receipt, 'message_id', '')}）"
        ),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeGlobalMutationResult(
        success=False,
        code=exc.code,
        message="配置请求幂等键已绑定不同内容，请基于当前草稿重新发起保存",
      )
    except ValueError as exc:
      if "CONFIG_VERSION_CONFLICT" in str(exc):
        latest = await t_trade_monitor_projection_service.get(input.account_id)
        return TTradeGlobalMutationResult(
          success=False,
          code="CONFIG_VERSION_CONFLICT",
          message="配置已被其他操作更新；请基于最新版本重新预览后保存",
          monitor=cls._global_monitor_type(latest) if latest else None,
        )
      return TTradeGlobalMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )

  @classmethod
  async def reconcile_global_monitor(
    cls, account_id: str, idempotency_key: str
  ) -> TTradeGlobalMutationResult:
    try:
      normalized_key = _validated_client_idempotency_key(idempotency_key)
    except ValueError as exc:
      return TTradeGlobalMutationResult(
        success=False,
        code="INVALID_IDEMPOTENCY_KEY",
        message=str(exc),
      )
    try:
      monitor = await cls._engine_request(
        "T_TRADE_GLOBAL_RECONCILE",
        {"account_id": account_id},
        account_id,
        idempotency_key=_namespaced_client_idempotency_key(
          "global-reconcile", account_id, normalized_key
        ),
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
    except EngineCommandPendingError as exc:
      return TTradeGlobalMutationResult(
        success=False,
        code=_T_TRADE_GLOBAL_RECONCILE_COMMAND_PENDING_CODE,
        message=_pending_command_message(exc, "全局做 T 监控同步"),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeGlobalMutationResult(
        success=False,
        code=exc.code,
        message="幂等键已绑定其他全局做 T 同步请求，请生成新的请求幂等键",
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
  async def list_signal_evaluations(
    cls,
    account_id: str,
    *,
    stock_code: Optional[str],
    event_kinds: Optional[List[TTradeSignalEvaluationKind]],
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    first: int,
    after: Optional[str],
  ) -> TTradeSignalEvaluationPage:
    if first < 1 or first > 100:
      raise ValueError("做 T 信号评估分页条数必须在 1 到 100 之间")
    if start_time is not None and end_time is not None and start_time > end_time:
      raise ValueError("做 T 信号评估开始时间不能晚于结束时间")
    cursor_time = None
    cursor_id = None
    if after:
      cursor_time, cursor_id = decode_datetime_cursor(after)
    requested_kinds = event_kinds or [TTradeSignalEvaluationKind.MATERIAL]
    normalized_kinds = list(dict.fromkeys(item.value for item in requested_kinds))
    supported = {
      T_TRADE_EVALUATION_KIND_MATERIAL,
      T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
    }
    if any(item not in supported for item in normalized_kinds):
      raise ValueError("不支持的做 T 信号评估种类")

    async with AsyncSessionLocal() as db:
      repository = TTradeOpportunityEvaluationRepository(db)
      batches = [
        await repository.list_evaluations(
          account_id=account_id,
          limit=first + 1,
          instrument_code=stock_code,
          record_kind=kind,
          started_at=start_time,
          ended_at=end_time,
          cursor_evaluated_at=cursor_time,
          cursor_id=cursor_id,
        )
        for kind in normalized_kinds
      ]
    rows = sorted(
      (row for batch in batches for row in batch),
      key=lambda row: (row.evaluated_at, str(row.id)),
      reverse=True,
    )
    has_next_page = len(rows) > first
    rows = rows[:first]
    items: list[TTradeSignalEvaluation] = []
    cursors: list[str] = []
    for row in rows:
      evaluated_at = cls._datetime(row.evaluated_at)
      if evaluated_at is None:
        raise ValueError("做 T 信号评估缺少 evaluated_at")
      cursors.append(encode_cursor(evaluated_at, str(row.id)))
      evidence = dict(row.payload or {})
      items.append(
        TTradeSignalEvaluation(
          id=strawberry.ID(str(row.id)),
          account_id=str(row.account_id),
          run_id=strawberry.ID(str(row.strategy_run_id)),
          stock_code=str(row.instrument_code),
          event_kind=TTradeSignalEvaluationKind(str(row.record_kind)),
          event_type=str(row.event_type),
          evaluated_at=evaluated_at,
          window_started_at=cls._datetime(row.window_started_at),
          window_ended_at=cls._datetime(row.window_ended_at),
          coalesced_count=int(row.coalesced_count),
          policy_version=str(row.policy_version),
          schema_version=str(row.schema_version),
          content_fingerprint=str(row.content_fingerprint),
          signal_snapshot=cls._signal_snapshot_type(evidence.get("signal_snapshot")),
        )
      )
    return TTradeSignalEvaluationPage(
      items=items,
      page_info=PageInfo(
        has_next_page=has_next_page,
        has_previous_page=bool(after),
        start_cursor=cursors[0] if cursors else None,
        end_cursor=cursors[-1] if cursors else None,
      ),
    )

  @classmethod
  async def candidate_trace(
    cls,
    account_id: str,
    strategy_run_id: str,
    candidate_id: str,
  ) -> Optional[TTradeCandidateTrace]:
    normalized_strategy_run_id = cls._required_text(
      strategy_run_id,
      "strategy_run_id",
    )
    normalized_candidate_id = cls._required_text(candidate_id, "candidate_id")
    async with AsyncSessionLocal() as db:
      trace = await TTradeCandidateTraceService(db).get_trace(
        account_id=account_id,
        strategy_run_id=normalized_strategy_run_id,
        candidate_id=normalized_candidate_id,
      )
    if trace is None:
      return None
    source = trace.source_identity
    links = trace.links
    return TTradeCandidateTrace(
      account_id=trace.account_id,
      candidate_id=trace.candidate_id,
      strategy_run_id=trace.strategy_run_id,
      instrument_code=trace.instrument_code,
      source_evaluation_id=trace.source_evaluation_id,
      source_identity=TTradeCandidateTraceSourceIdentity(
        source_time_ms=(
          None if source.source_time_ms is None else str(source.source_time_ms)
        ),
        tick_ordinal=(
          None if source.tick_ordinal is None else str(source.tick_ordinal)
        ),
        continuity_generation=source.continuity_generation,
        trade_date=source.trade_date,
        candidate_fingerprint=source.candidate_fingerprint,
        policy_version=source.policy_version,
        feature_schema_version=source.feature_schema_version,
        profile_version=source.profile_version,
      ),
      integrity_status=TTradeCandidateTraceIntegrityStatus(trace.integrity_status),
      missing_reasons=[
        TTradeCandidateTraceMissingReason(
          code=item.code,
          stage=item.stage,
          expected=item.expected,
          detail=item.detail,
        )
        for item in trace.missing_reasons
      ],
      links=TTradeCandidateTraceLinks(
        evaluation_ids=list(links.evaluation_ids),
        intent_ids=list(links.intent_ids),
        client_order_ids=list(links.client_order_ids),
        correlation_ids=list(links.correlation_ids),
        broker_order_ids=list(links.broker_order_ids),
        order_ids=list(links.order_ids),
        trade_ids=list(links.trade_ids),
        batch_ids=list(links.batch_ids),
        exit_plan_ids=list(links.exit_plan_ids),
        exit_plan_event_ids=list(links.exit_plan_event_ids),
      ),
      events=[
        TTradeCandidateTraceEvent(
          stage=item.stage,
          event_type=item.event_type,
          entity_id=item.entity_id,
          occurred_at=item.occurred_at,
          status=item.status,
          related_ids={key: list(values) for key, values in item.related_ids.items()},
          details=dict(item.details),
        )
        for item in trace.events
      ],
    )

  @classmethod
  async def signal_diagnostics(
    cls,
    account_id: str,
    *,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
    merge_versions: bool = False,
  ) -> TTradeSignalDiagnostics:
    if start_time >= end_time:
      raise ValueError("做 T 信号诊断开始时间必须早于结束时间")
    provider = getattr(cls.service, "signal_diagnostics", None)
    if callable(provider):
      raw = await provider(
        account_id,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        merge_versions=merge_versions,
      )
      return cls._signal_diagnostics_type(
        raw,
        account_id=account_id,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
      )
    return cls._unavailable_signal_diagnostics(
      account_id=account_id,
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      merge_versions=merge_versions,
    )

  @staticmethod
  def _unavailable_signal_diagnostics(
    *,
    account_id: str,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
    reason_code: str = "DIAGNOSTICS_PROJECTION_UNAVAILABLE",
    reason: str = "诊断聚合真源尚未接通；未使用 Tick 数或部分历史伪造统计结果",
    merge_versions: bool = False,
  ) -> TTradeSignalDiagnostics:
    return TTradeSignalDiagnostics(
      available=False,
      reason_code=reason_code,
      reason=reason,
      account_id=account_id,
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      merged_versions=merge_versions,
      warnings=[],
      partitions=[],
      version_groups=[],
    )

  @classmethod
  def _signal_diagnostics_type(
    cls,
    raw: Mapping[str, Any],
    *,
    account_id: str,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
  ) -> TTradeSignalDiagnostics:
    payload = dict(raw)
    if payload.get("available") is not True:
      return cls._unavailable_signal_diagnostics(
        account_id=account_id,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        reason_code=str(
          payload.get("reason_code") or "DIAGNOSTICS_PROJECTION_UNAVAILABLE"
        ),
        reason=str(payload.get("reason") or "诊断聚合真源当前不可用"),
        merge_versions=bool(payload.get("merged_versions", False)),
      )
    merged_versions = payload.get("merged_versions")
    if not isinstance(merged_versions, bool):
      raise ValueError("diagnostics.merged_versions 必须是布尔值")
    raw_warnings = payload.get("warnings")
    if not isinstance(raw_warnings, list):
      raise ValueError("diagnostics.warnings 必须是列表")
    warnings = [
      cls._required_text(value, "diagnostics.warning") for value in raw_warnings
    ]

    def count(value: Any, field_name: str) -> int:
      return cls._required_non_negative_int(value, field_name)

    raw_partitions = payload.get("partitions")
    if not isinstance(raw_partitions, list):
      raise ValueError("diagnostics.partitions 必须是列表")
    partitions: list[TTradeSignalDiagnosticPartition] = []
    for raw_partition in raw_partitions:
      partition = dict(raw_partition)
      policy_version = cls._required_text(
        partition.get("policy_version"), "partition.policy_version"
      )
      feature_schema_version = cls._required_text(
        partition.get("feature_schema_version"),
        "partition.feature_schema_version",
      )
      profile_version = str(partition.get("profile_version") or "").strip() or None
      denominator = dict(partition.get("denominator") or {})
      denominator_code = cls._required_text(
        denominator.get("code"), "diagnostics.denominator.code"
      )
      if denominator_code != "READY_INSTRUMENT_SECONDS":
        raise ValueError("做 T 诊断分母必须是 READY_INSTRUMENT_SECONDS")
      ready_seconds = cls._required_number(
        denominator.get("ready_instrument_seconds"),
        "diagnostics.denominator.ready_instrument_seconds",
      )
      if ready_seconds < 0:
        raise ValueError("做 T 诊断 READY 标的时长不能为负数")

      funnel = []
      previous_stage_code: Optional[str] = None
      for item in [dict(value) for value in list(partition.get("funnel") or [])]:
        stage_code = cls._required_text(item.get("code"), "funnel.code")
        stage_denominator_code = str(item.get("denominator_code") or "").strip() or None
        if stage_denominator_code != previous_stage_code:
          raise ValueError("做 T 漏斗转换分母必须引用紧邻的前一阶段")
        unit_code = cls._required_text(item.get("unit_code"), "funnel.unit_code")
        if _DIAGNOSTIC_FUNNEL_UNITS.get(stage_code) != unit_code:
          raise ValueError("做 T 漏斗阶段或单位代码无效")
        funnel.append(
          TTradeSignalFunnelStage(
            code=stage_code,
            label=cls._required_text(item.get("label"), "funnel.label"),
            unit_code=unit_code,
            denominator_code=stage_denominator_code,
            count=count(item.get("count"), "funnel.count"),
            conversion_rate=cls._optional_number(item.get("conversion_rate")),
          )
        )
        previous_stage_code = stage_code
      if [item.code for item in funnel] != list(_DIAGNOSTIC_FUNNEL_UNITS):
        raise ValueError("做 T 诊断必须返回完整且有序的九阶段漏斗")
      blockers = []
      for item in [dict(value) for value in list(partition.get("blockers") or [])]:
        blocker_denominator_code = cls._required_text(
          item.get("denominator_code"), "blocker.denominator_code"
        )
        if blocker_denominator_code not in {
          "READY_INSTRUMENT_SECONDS",
          "EPISODES",
          "MATERIAL_EVENTS",
        }:
          raise ValueError("做 T blocker 分母代码无效")
        blocker_denominator_value = cls._required_number(
          item.get("denominator_value"), "blocker.denominator_value"
        )
        if blocker_denominator_value < 0:
          raise ValueError("做 T blocker 分母不能为负数")
        rate = cls._optional_number(item.get("rate"))
        if rate is not None and not 0 <= rate <= 1:
          raise ValueError("做 T blocker 比率必须在 0 到 1 之间")
        blockers.append(
          TTradeSignalBlockerAggregate(
            blocker=cls._blocker_type(item.get("blocker", item)),
            count=count(item.get("count"), "blocker.count"),
            rate=rate,
            denominator_code=blocker_denominator_code,
            denominator_value=blocker_denominator_value,
          )
        )
      score_distribution = []
      for item in [
        dict(value) for value in list(partition.get("score_distribution") or [])
      ]:
        raw_path = str(item.get("path") or "NONE")
        score_distribution.append(
          TTradeSignalScoreBucket(
            policy_version=cls._required_text(
              item.get("policy_version"), "score.policy_version"
            ),
            feature_schema_version=cls._required_text(
              item.get("feature_schema_version"),
              "score.feature_schema_version",
            ),
            profile_version=str(item.get("profile_version") or "").strip() or None,
            path=(None if raw_path == "NONE" else TTradeSignalPath(raw_path)),
            lower_bound=cls._required_number(item.get("lower_bound"), "score.lower"),
            upper_bound=cls._required_number(item.get("upper_bound"), "score.upper"),
            count=count(item.get("count"), "score.count"),
          )
        )
      if not merged_versions:
        partition_coordinate = (
          policy_version,
          feature_schema_version,
          profile_version,
        )
        if any(
          (
            bucket.policy_version,
            bucket.feature_schema_version,
            bucket.profile_version,
          )
          != partition_coordinate
          for bucket in score_distribution
        ):
          raise ValueError("做 T 分数桶版本坐标必须与所在诊断分区一致")
      fsm_dwell = [
        TTradeSignalFsmDwell(
          branch=cls._required_text(item.get("branch"), "fsm.branch"),
          phase=cls._required_text(item.get("phase"), "fsm.phase"),
          duration_seconds=cls._required_number(
            item.get("duration_seconds"), "fsm.duration_seconds"
          ),
          transition_count=count(item.get("transition_count"), "fsm.transition_count"),
        )
        for item in [dict(value) for value in list(partition.get("fsm_dwell") or [])]
      ]
      fsm_transitions = [
        TTradeSignalFsmTransition(
          branch=cls._required_text(item.get("branch"), "fsm_edge.branch"),
          from_phase=cls._required_text(item.get("from_phase"), "fsm_edge.from_phase"),
          to_phase=cls._required_text(item.get("to_phase"), "fsm_edge.to_phase"),
          count=count(item.get("count"), "fsm_edge.count"),
        )
        for item in [
          dict(value) for value in list(partition.get("fsm_transitions") or [])
        ]
      ]
      candidate_outcomes = [
        TTradeCandidateOutcomeAggregate(
          code=cls._required_text(item.get("code"), "candidate_outcome.code"),
          label=cls._required_text(item.get("label"), "candidate_outcome.label"),
          count=count(item.get("count"), "candidate_outcome.count"),
        )
        for item in [
          dict(value) for value in list(partition.get("candidate_outcomes") or [])
        ]
      ]
      performance = dict(partition.get("post_candidate_performance") or {})
      raw_fixed_returns = performance.get("fixed_window_returns")
      raw_required_codes = performance.get("required_data_codes")
      if not isinstance(raw_fixed_returns, list) or not isinstance(
        raw_required_codes, list
      ):
        raise ValueError("做 T 成交后表现契约缺少固定窗口或权威数据代码")
      if not isinstance(performance.get("available"), bool):
        raise ValueError("performance.available 必须是布尔值")
      performance_available = performance.get("available") is True
      performance_reason_code = (
        str(performance.get("reason_code") or "").strip() or None
      )
      performance_reason = str(performance.get("reason") or "").strip() or None
      required_data_codes = [
        cls._required_text(value, "performance.required_data_code")
        for value in raw_required_codes
      ]
      if not performance_available and (
        performance_reason_code is None
        or performance_reason is None
        or not required_data_codes
      ):
        raise ValueError("不可用的做 T 成交后表现必须说明原因和缺失权威数据")
      if performance_available and required_data_codes:
        raise ValueError("可用的做 T 成交后表现不能再声明缺失权威数据")
      performance_sample_count = count(
        performance.get("sample_count"), "performance.sample_count"
      )
      performance_net_mfe = cls._optional_number(performance.get("net_mfe_pct"))
      performance_net_mae = cls._optional_number(performance.get("net_mae_pct"))
      if not performance_available and (
        performance_sample_count != 0
        or performance_net_mfe is not None
        or performance_net_mae is not None
        or raw_fixed_returns
      ):
        raise ValueError(
          "不可用的做 T 成交后表现不得携带样本、收益或固定窗口数据"
        )
      if performance_available and (
        performance_sample_count <= 0
        or performance_reason_code is not None
        or performance_reason is not None
      ):
        raise ValueError("可用的做 T 成交后表现必须包含样本且不得携带失败原因")
      fixed_returns = []
      for raw_return in raw_fixed_returns:
        item = dict(raw_return)
        window_seconds = count(item.get("window_seconds"), "return.window_seconds")
        if window_seconds <= 0:
          raise ValueError("固定收益窗口必须大于零秒")
        fixed_returns.append(
          TTradeFixedWindowReturnAggregate(
            window_seconds=window_seconds,
            sample_count=count(item.get("sample_count"), "return.sample_count"),
            average_net_return_pct=cls._optional_number(
              item.get("average_net_return_pct")
            ),
          )
        )
      partitions.append(
        TTradeSignalDiagnosticPartition(
          policy_version=policy_version,
          feature_schema_version=feature_schema_version,
          profile_version=profile_version,
          denominator=TTradeSignalDiagnosticDenominator(
            code=denominator_code,
            label=cls._required_text(
              denominator.get("label"), "diagnostics.denominator.label"
            ),
            ready_instrument_seconds=ready_seconds,
          ),
          funnel=funnel,
          blockers=blockers,
          score_distribution=score_distribution,
          fsm_dwell=fsm_dwell,
          fsm_transitions=fsm_transitions,
          candidate_outcomes=candidate_outcomes,
          post_candidate_performance=TTradePostCandidatePerformance(
            available=performance_available,
            reason_code=performance_reason_code,
            reason=performance_reason,
            sample_count=performance_sample_count,
            net_mfe_pct=performance_net_mfe,
            net_mae_pct=performance_net_mae,
            fixed_window_returns=fixed_returns,
            required_data_codes=required_data_codes,
          ),
        )
      )
    raw_version_groups = payload.get("version_groups")
    if not isinstance(raw_version_groups, list):
      raise ValueError("diagnostics.version_groups 必须是列表")
    version_groups = [
      TTradeSignalVersionGroup(
        policy_version=cls._required_text(
          item.get("policy_version"), "version.policy_version"
        ),
        feature_schema_version=cls._required_text(
          item.get("feature_schema_version"), "version.feature_schema_version"
        ),
        profile_version=str(item.get("profile_version") or "").strip() or None,
        count=count(item.get("count"), "version.count"),
      )
      for item in [dict(value) for value in raw_version_groups]
    ]
    partition_coordinates = {
      (
        item.policy_version,
        item.feature_schema_version,
        item.profile_version,
      )
      for item in partitions
    }
    if not merged_versions and (
      len(partition_coordinates) != len(partitions)
      or any(item.policy_version == "MIXED" for item in partitions)
    ):
      raise ValueError("做 T 诊断默认结果必须按唯一版本坐标分区")
    version_coordinates = {
      (
        item.policy_version,
        item.feature_schema_version,
        item.profile_version,
      )
      for item in version_groups
    }
    if not merged_versions and partition_coordinates != version_coordinates:
      raise ValueError("做 T 诊断版本分区与版本分组不一致")
    if merged_versions and len(partitions) > 1:
      raise ValueError("显式合并版本时只能返回一个诊断分区")
    if (
      merged_versions
      and len(version_groups) > 1
      and "MIXED_SIGNAL_VERSIONS_EXPLICITLY_MERGED" not in warnings
    ):
      raise ValueError("合并不同做 T 规则版本时必须返回显式警告")
    if (
      merged_versions
      and len(version_groups) > 1
      and partitions
      and (
        partitions[0].policy_version != "MIXED"
        or partitions[0].feature_schema_version != "MIXED"
      )
    ):
      raise ValueError("合并不同做 T 规则版本时分区必须标记为 MIXED")
    for partition in partitions:
      for bucket in partition.score_distribution:
        coordinate = (
          bucket.policy_version,
          bucket.feature_schema_version,
          bucket.profile_version,
        )
        if coordinate not in version_coordinates:
          raise ValueError("做 T 分数桶版本坐标不在诊断版本分组内")
    return TTradeSignalDiagnostics(
      available=True,
      reason_code=None,
      reason=None,
      account_id=account_id,
      stock_code=stock_code,
      start_time=start_time,
      end_time=end_time,
      merged_versions=merged_versions,
      warnings=warnings,
      partitions=partitions,
      version_groups=version_groups,
    )

  @staticmethod
  def _policy_from_input(input: TTradeSignalPolicyInput) -> OpportunityPolicy:
    return OpportunityPolicy(**asdict(input))

  @classmethod
  async def preview_signal_policy(
    cls,
    input: TTradeSignalPolicyPreviewInput,
  ) -> TTradeSignalPolicyPreviewResult:
    if input.expected_config_version < 0:
      return TTradeSignalPolicyPreviewResult(
        valid=False,
        config_version=input.expected_config_version,
        errors=[
          TTradeSignalPolicyIssue(
            code="INVALID_EXPECTED_CONFIG_VERSION",
            field="expected_config_version",
            message="expected_config_version must be non-negative",
          )
        ],
        warnings=[],
        normalized_policy=None,
        changed_fields=[],
        requires_rewarm=False,
      )
    try:
      normalized = cls._policy_from_input(input.signal_policy)
    except (TypeError, ValueError) as exc:
      message = str(exc)
      field_name = next(
        (
          item.name
          for item in dataclass_fields(TTradeSignalPolicyInput)
          if item.name in message
        ),
        None,
      )
      return TTradeSignalPolicyPreviewResult(
        valid=False,
        config_version=input.expected_config_version,
        errors=[
          TTradeSignalPolicyIssue(
            code=(f"INVALID_{field_name.upper()}" if field_name else "INVALID_POLICY"),
            field=field_name,
            message=message,
          )
        ],
        warnings=[],
        normalized_policy=None,
        changed_fields=[],
        requires_rewarm=False,
      )
    preview_payload = {
      "input": {
        "account_id": input.account_id,
        "expected_config_version": input.expected_config_version,
        "signal_policy": normalized.to_dict(),
      }
    }
    try:
      result = await cls._engine_request(
        "T_TRADE_SIGNAL_POLICY_PREVIEW",
        preview_payload,
        input.account_id,
        # Policy preview is pure/read-only.  Do not bind a future preview to
        # an old deploy's permanent digest key; callers may retry explicitly
        # while the one-shot command is pending.
        idempotency_key=(
          f"t-trade-signal-policy-preview:{input.account_id}:{uuid.uuid4()}"
        ),
      )
    except EngineCommandPendingError as exc:
      return TTradeSignalPolicyPreviewResult(
        valid=False,
        config_version=input.expected_config_version,
        errors=[
          TTradeSignalPolicyIssue(
            code=_T_TRADE_SIGNAL_POLICY_PREVIEW_COMMAND_PENDING_CODE,
            field=None,
            message=_pending_command_message(exc, "做 T 规则预览"),
          )
        ],
        warnings=[],
        normalized_policy=None,
        changed_fields=[],
        requires_rewarm=False,
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeSignalPolicyPreviewResult(
        valid=False,
        config_version=input.expected_config_version,
        errors=[
          TTradeSignalPolicyIssue(
            code=exc.code,
            field=None,
            message="规则预览请求幂等键已被其他命令占用，请重新发起预览",
          )
        ],
        warnings=[],
        normalized_policy=None,
        changed_fields=[],
        requires_rewarm=False,
      )
    except ValueError as exc:
      code = (
        "CONFIG_VERSION_CONFLICT"
        if "CONFIG_VERSION_CONFLICT" in str(exc)
        else "POLICY_PREVIEW_FAILED"
      )
      return TTradeSignalPolicyPreviewResult(
        valid=False,
        config_version=input.expected_config_version,
        errors=[TTradeSignalPolicyIssue(code=code, field=None, message=str(exc))],
        warnings=[],
        normalized_policy=None,
        changed_fields=[],
        requires_rewarm=False,
      )

    def issue_type(raw: Any) -> TTradeSignalPolicyIssue:
      if isinstance(raw, Mapping):
        return TTradeSignalPolicyIssue(
          code=str(raw.get("code") or "POLICY_VALIDATION_ISSUE"),
          field=str(raw.get("field") or "") or None,
          message=str(raw.get("message") or ""),
        )
      return TTradeSignalPolicyIssue(
        code="POLICY_VALIDATION_ISSUE",
        field=None,
        message=str(raw),
      )

    errors = [issue_type(item) for item in list(result.get("errors") or [])]
    warnings = [issue_type(item) for item in list(result.get("warnings") or [])]
    raw_normalized = result.get("normalized_policy")
    try:
      normalized_type = (
        cls._signal_policy_type(dict(raw_normalized)) if raw_normalized else None
      )
    except (TypeError, ValueError) as exc:
      logger.warning("拒绝不完整的做 T 规则预览结果: %s", exc)
      normalized_type = None
      errors.append(
        TTradeSignalPolicyIssue(
          code="INCOMPLETE_POLICY_PREVIEW",
          field="normalized_policy",
          message="Engine 未返回完整规范化规则",
        )
      )
    valid = not errors and normalized_type is not None
    return TTradeSignalPolicyPreviewResult(
      valid=valid,
      config_version=cls._required_non_negative_int(
        result.get("config_version", input.expected_config_version),
        "config_version",
      ),
      errors=errors,
      warnings=warnings,
      normalized_policy=normalized_type,
      changed_fields=[str(item) for item in list(result.get("changed_fields") or [])],
      requires_rewarm=bool(result.get("requires_rewarm", False)),
    )

  @classmethod
  def _normalized_command_input(cls, input: Any) -> dict[str, Any]:
    payload = cls._command_input(input)
    if "account_id" in payload:
      payload["account_id"] = str(payload["account_id"] or "").strip()
    if "mode" in payload:
      payload["mode"] = str(payload["mode"] or "paper").strip().lower()
    raw_policy = getattr(input, "signal_policy", None)
    if raw_policy is not None:
      payload["signal_policy"] = cls._policy_from_input(raw_policy).to_dict()
    raw_portfolio = dict(payload.pop("portfolio", {}) or {})
    if raw_portfolio:
      source = str(raw_portfolio.get("source") or "").strip().upper()
      payload["portfolio_source"] = source
      payload["initial_portfolio_as_of"] = raw_portfolio.get("as_of")
      if source == "SNAPSHOT":
        payload["expected_snapshot_id"] = str(
          raw_portfolio.get("snapshot_id") or ""
        ).strip()
        payload["initial_positions"] = []
      else:
        cash = raw_portfolio.get("cash_available")
        positions = []
        for item in list(raw_portfolio.get("positions") or []):
          row = dict(item or {})
          volume = int(row.get("volume", 0) or 0)
          avg_price = float(row.get("avg_price", 0.0) or 0.0)
          positions.append(
            {
              "stock_code": str(row.get("stock_code") or "").strip().upper(),
              "volume": volume,
              "available_volume": volume,
              "avg_price": avg_price,
              "last_price": avg_price,
              "market_value": max(0.0, avg_price * volume),
            }
          )
        payload["initial_cash"] = cash
        payload["initial_positions"] = positions
        if cash is not None:
          payload["initial_total_asset"] = float(cash) + sum(
            float(item["market_value"]) for item in positions
          )
    return payload

  @classmethod
  def approval_command_payload(
    cls,
    run_id: str,
    intent_id: str,
    *,
    expectation: TTradeCandidateApprovalExpectationInput,
    actor_id: str = "",
    device_session_id: str = "",
    approval_channel: str = "WEB",
  ) -> dict[str, Any]:
    candidate_id = cls._required_text(
      str(expectation.candidate_id), "expected_candidate_id"
    )
    candidate_fingerprint = cls._required_text(
      expectation.candidate_fingerprint,
      "expected_candidate_fingerprint",
    )
    policy_version = cls._required_text(
      expectation.policy_version,
      "expected_policy_version",
    )
    signal_version = cls._required_non_negative_int(
      expectation.signal_version,
      "expected_signal_version",
    )
    candidate_state_version = cls._required_non_negative_int(
      expectation.candidate_state_version,
      "expected_candidate_state_version",
    )
    config_version = cls._required_non_negative_int(
      expectation.config_version,
      "expected_config_version",
    )
    return {
      "run_id": run_id,
      "intent_id": intent_id,
      "expected_signal_version": signal_version,
      "expected_candidate_id": candidate_id,
      "expected_candidate_fingerprint": candidate_fingerprint,
      "expected_candidate_state_version": candidate_state_version,
      "expected_config_version": config_version,
      "expected_policy_version": policy_version,
      "approval_audit": {
        "actor_id": str(actor_id or "")[:64],
        "device_session_id": str(device_session_id or "")[:64],
        "channel": str(approval_channel or "WEB")[:32],
      },
    }

  @classmethod
  def approval_command_idempotency_key(
    cls,
    *,
    account_id: str,
    run_id: str,
    intent_id: str,
    client_key: str,
  ) -> str:
    return _namespaced_client_idempotency_key(
      "approve-entry",
      f"{str(account_id or '')}\x00{run_id}\x00{intent_id}",
      _validated_client_idempotency_key(client_key),
    )

  @classmethod
  async def approve_entry(
    cls,
    run_id: str,
    intent_id: str,
    *,
    expectation: TTradeCandidateApprovalExpectationInput,
    idempotency_key: str,
    actor_id: str = "",
    device_session_id: str = "",
    approval_channel: str = "WEB",
  ) -> TTradeMutationResult:
    try:
      command_payload = cls.approval_command_payload(
        run_id,
        intent_id,
        expectation=expectation,
        actor_id=actor_id,
        device_session_id=device_session_id,
        approval_channel=approval_channel,
      )
      session = await cls.service.get_session(run_id)
      result = await cls._engine_request(
        "T_TRADE_APPROVE_ENTRY",
        command_payload,
        run_id,
        idempotency_key=cls.approval_command_idempotency_key(
          account_id=str(session.get("account_id") or ""),
          run_id=run_id,
          intent_id=intent_id,
          client_key=_validated_client_idempotency_key(idempotency_key),
        ),
      )
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"]) if result.get("session") else None,
      )
    except EngineCommandPendingError as exc:
      return TTradeMutationResult(
        success=False,
        code=_T_TRADE_APPROVE_ENTRY_COMMAND_PENDING_CODE,
        message=_pending_command_message(exc, "做 T 入场审批"),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeMutationResult(
        success=False,
        code=exc.code,
        message="入场审批幂等键已绑定不同请求，请生成新的请求幂等键",
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
        idempotency_key=f"t-trade-reject-entry:{run_id}:{intent_id}",
      )
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"]) if result.get("session") else None,
      )
    except EngineCommandPendingError as exc:
      return TTradeMutationResult(
        success=False,
        code=_T_TRADE_REJECT_ENTRY_COMMAND_PENDING_CODE,
        message=_pending_command_message(exc, "做 T 入场拒绝"),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeMutationResult(
        success=False,
        code=exc.code,
        message="入场拒绝幂等键已绑定不同请求，请生成新的请求幂等键",
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
        idempotency_key=(
          f"t-trade-import-external-entry:{input.run_id}:{input.order_id}"
        ),
      )
      return TTradeMutationResult(
        success=True,
        code=str(result["code"]),
        message=str(result["message"]),
        session=cls._session_type(result["session"]),
      )
    except EngineCommandPendingError as exc:
      return TTradeMutationResult(
        success=False,
        code=_T_TRADE_IMPORT_EXTERNAL_ENTRY_COMMAND_PENDING_CODE,
        message=_pending_command_message(exc, "外部成交导入"),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeMutationResult(
        success=False,
        code=exc.code,
        message="外部成交导入幂等键已绑定不同请求，请生成新的请求幂等键",
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
        idempotency_key=f"t-trade-stop-session:{run_id}",
      )
      return TTradeMutationResult(
        success=bool(result.get("success")),
        code=str(result.get("code", "")),
        message=str(result.get("message", "")),
        session=cls._session_type(result["session"]) if result.get("session") else None,
      )
    except EngineCommandPendingError as exc:
      return TTradeMutationResult(
        success=False,
        code=_T_TRADE_STOP_SESSION_COMMAND_PENDING_CODE,
        message=_pending_command_message(exc, "做 T 会话停止"),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeMutationResult(
        success=False,
        code=exc.code,
        message="会话停止幂等键已绑定不同请求，请生成新的请求幂等键",
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
  async def _rollout_operation_marker_exists(
    cls,
    account_id: str,
    operation_id: str,
    *,
    event_types: set[str],
    actor_user_id: str | None,
    snapshot_id: str | None = None,
    target_stage: str | None = None,
    policy_version: int | None = None,
    confirmation: str | None = None,
  ) -> bool:
    checker = getattr(cls.operations_service, "operation_marker_exists", None)
    if checker is None:
      return False
    return await checker(
      account_id,
      operation_id,
      event_types=event_types,
      actor_user_id=actor_user_id,
      snapshot_id=snapshot_id,
      target_stage=target_stage,
      policy_version=policy_version,
      confirmation=confirmation,
    )

  @classmethod
  async def activate_live(
    cls,
    account_id: str,
    *,
    user_id: str,
    policy_version: int,
    snapshot_id: str,
    target_stage: TTradeRolloutTarget = TTradeRolloutTarget.CANARY,
    confirmation: str = "",
    idempotency_key: str,
  ) -> TTradeOperationsMutationResult:
    try:
      operation_id = _namespaced_client_idempotency_key(
        "activate-live", account_id, idempotency_key
      )
    except ValueError as exc:
      return TTradeOperationsMutationResult(
        success=False,
        code="INVALID_IDEMPOTENCY_KEY",
        message=str(exc),
      )
    try:
      target = str(target_stage.value)
      readiness = await cls.operations_service.activate_rollout(
        account_id,
        user_id=user_id,
        acknowledged_policy_version=policy_version,
        target_stage=target,
        confirmation=confirmation,
        expected_snapshot_id=snapshot_id,
        operation_id=operation_id,
      )
      return TTradeOperationsMutationResult(
        success=True,
        code=f"{target}_ACTIVATED",
        message="账户已进入正式 LIVE 阶段"
        if target == "LIVE"
        else "账户已进入严格 Canary 阶段",
        readiness=cls._readiness_type(readiness),
      )
    except TTradeOperationIdempotencyError as exc:
      return TTradeOperationsMutationResult(
        success=False,
        code=exc.code,
        message="做 T 控制幂等键已绑定不同请求，请生成新的请求幂等键",
      )
    except ValueError as exc:
      if await cls._rollout_operation_marker_exists(
        account_id,
        operation_id,
        event_types={
          "LIVE_ACTIVATED"
          if target == "LIVE"
          else "CANARY_ACTIVATED",
          "LIVE_ACTIVATED",
        },
        actor_user_id=user_id,
        snapshot_id=snapshot_id,
        target_stage=target,
        policy_version=policy_version,
        confirmation=confirmation,
      ):
        return TTradeOperationsMutationResult(
          success=True,
          code=f"{target}_ACTIVATED",
          message="账户控制操作已应用；最新安全状态正在回读",
        )
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
    policy_version: int,
    snapshot_id: str,
    idempotency_key: str,
  ) -> TTradeOperationsMutationResult:
    try:
      operation_id = _namespaced_client_idempotency_key(
        "begin-controlled-window", account_id, idempotency_key
      )
    except ValueError as exc:
      return TTradeOperationsMutationResult(
        success=False,
        code="INVALID_IDEMPOTENCY_KEY",
        message=str(exc),
      )
    try:
      readiness = await cls.operations_service.begin_controlled_window(
        account_id,
        user_id=user_id,
        snapshot_id=snapshot_id,
        expected_policy_version=policy_version,
        operation_id=operation_id,
      )
      return TTradeOperationsMutationResult(
        success=True,
        code="CONTROLLED_WINDOW_STARTED",
        message="已基于当前完整快照建立账户实盘窗口",
        readiness=cls._readiness_type(readiness),
      )
    except TTradeOperationIdempotencyError as exc:
      return TTradeOperationsMutationResult(
        success=False,
        code=exc.code,
        message="做 T 控制幂等键已绑定不同请求，请生成新的请求幂等键",
      )
    except ValueError as exc:
      if await cls._rollout_operation_marker_exists(
        account_id,
        operation_id,
        event_types={"CONTROLLED_WINDOW_STARTED"},
        actor_user_id=user_id,
        snapshot_id=snapshot_id,
        policy_version=policy_version,
      ):
        return TTradeOperationsMutationResult(
          success=True,
          code="CONTROLLED_WINDOW_STARTED",
          message="账户窗口操作已应用；最新安全状态正在回读",
        )
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
    idempotency_key: str,
  ) -> TTradeOperationsMutationResult:
    try:
      operation_id = _namespaced_client_idempotency_key(
        "kill-switch", account_id, idempotency_key
      )
    except ValueError as exc:
      return TTradeOperationsMutationResult(
        success=False,
        code="INVALID_IDEMPOTENCY_KEY",
        message=str(exc),
      )
    try:
      readiness = await cls.operations_service.kill(
        account_id,
        reason,
        user_id=user_id,
        operation_id=operation_id,
      )
      return TTradeOperationsMutationResult(
        success=True,
        code="KILL_SWITCHED",
        message="kill switch 已触发，现有批次转人工处置",
        readiness=cls._readiness_type(readiness),
      )
    except TTradeOperationIdempotencyError as exc:
      return TTradeOperationsMutationResult(
        success=False,
        code=exc.code,
        message="做 T 控制幂等键已绑定不同请求，请生成新的请求幂等键",
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
      idempotency_key = _validated_client_idempotency_key(input.idempotency_key)
    except ValueError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code="INVALID_IDEMPOTENCY_KEY",
        message=str(exc),
      )
    try:
      command_input = cls._normalized_command_input(input)
      command_input["idempotency_key"] = idempotency_key
      receipt = await engine_command_service.request(
        "T_TRADE_REPLAY_START",
        {"input": command_input},
        aggregate_id=input.account_id,
        idempotency_key=_namespaced_client_idempotency_key(
          "replay-start", input.account_id, idempotency_key
        ),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code=exc.code,
        message="回放启动幂等键已绑定不同请求，请生成新的请求幂等键",
      )
    except EngineCommandPendingError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code=_T_TRADE_REPLAY_START_COMMAND_PENDING_CODE,
        message=_pending_command_message(exc, "做 T 历史回放启动"),
      )
    except Exception:
      logger.exception("提交做 T 历史回放命令失败: account_id=%s", input.account_id)
      return TTradeReplayMutationResult(
        success=False,
        code=_T_TRADE_REPLAY_START_OUTCOME_UNKNOWN_CODE,
        message=(
          "做 T 历史回放请求提交结果尚不知是否已提交，请继续使用原操作键重试"
        ),
      )
    if receipt.status == "FAILED":
      return TTradeReplayMutationResult(
        success=False,
        code="REPLAY_START_FAILED",
        message=receipt.error or "做 T 历史回放启动失败",
      )
    if receipt.status != "SUCCEEDED":
      pending = EngineCommandPendingError(receipt, "T_TRADE_REPLAY_START")
      return TTradeReplayMutationResult(
        success=False,
        code=_T_TRADE_REPLAY_START_COMMAND_PENDING_CODE,
        message=_pending_command_message(pending, "做 T 历史回放启动"),
      )

    replay = dict(receipt.result or {})
    if not replay:
      try:
        replay = await cls.replay_service.get(receipt.message_id) or {}
      except Exception:
        logger.exception("读取做 T 历史回放投影失败: run_id=%s", receipt.message_id)
    has_durable_replay = bool(replay)
    if not replay:
      replay = {
        "run_id": receipt.message_id,
        "backtest_id": None,
        "account_id": input.account_id,
        "status": "PENDING",
        "progress_pct": 0.0,
        "revision": "0",
        "processed_until": None,
        "start_time": input.start_time,
        "end_time": input.end_time,
        "snapshot_id": None,
        "snapshot_date": None,
        "created_at": None,
        "updated_at": None,
        "error_message": None,
        "data_quality": "PENDING",
        "data_quality_message": "Engine 已接受请求，正在准备历史数据",
        "skipped_stock_codes": [],
        "summary": None,
        "instruments": [],
        "curve": [],
        "report": None,
      }

    replay_status = str(replay.get("status") or "").upper()
    if (
      receipt.status == "SUCCEEDED"
      and has_durable_replay
      and replay_status in {"RUNNING", "COMPLETED"}
    ):
      return TTradeReplayMutationResult(
        success=True,
        code="REPLAY_STARTED",
        message="做 T 历史回放已启动",
        replay=cls._replay_type(replay),
      )
    return TTradeReplayMutationResult(
      success=True,
      code="REPLAY_ACCEPTED",
      message="做 T 历史回放请求已接受，正在后台准备",
      replay=cls._replay_type(replay),
    )

  @classmethod
  async def cancel_replay(cls, run_id: str) -> TTradeReplayMutationResult:
    try:
      replay = await cls._engine_request(
        "T_TRADE_REPLAY_CANCEL",
        {"run_id": run_id},
        run_id,
        idempotency_key=f"t-trade-replay-cancel:{run_id}",
      )
      return TTradeReplayMutationResult(
        success=True,
        code="REPLAY_CANCELLED",
        message="做 T 历史回放已取消",
        replay=cls._replay_type(replay),
      )
    except EngineCommandPendingError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code=_T_TRADE_REPLAY_CANCEL_COMMAND_PENDING_CODE,
        message=_pending_command_message(exc, "做 T 历史回放取消"),
      )
    except EngineCommandIdempotencyError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code=exc.code,
        message="回放取消幂等键已绑定不同请求，请生成新的请求幂等键",
      )
    except ValueError as exc:
      return TTradeReplayMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )
