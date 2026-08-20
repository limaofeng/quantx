"""GraphQL resolver bridge for account-level board historical replays."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional

from quantx_domain.trading.limit_up_board_replay import (
  LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE,
  get_limit_up_board_replay_scenarios,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.engine_command_service import (
  engine_command_service,
)
from quantx_infrastructure.services.limit_up_board_assistant_projection_service import (
  limit_up_board_assistant_projection_service,
)
from quantx_infrastructure.services.limit_up_board_replay_projection_service import (
  limit_up_board_replay_projection_service,
)
from quantx_infrastructure.services.limit_up_board_replay_result_service import (
  limit_up_board_replay_result_service,
)

from quantx_api.gqlapi.types.limit_up_board_replay_types import (
  LimitUpBoardReplay,
  LimitUpBoardReplayArtifact,
  LimitUpBoardReplayArtifacts,
  LimitUpBoardReplayConstraintMetric,
  LimitUpBoardReplayCoverage,
  LimitUpBoardReplayCurvePage,
  LimitUpBoardReplayCurvePoint,
  LimitUpBoardReplayDataQuality,
  LimitUpBoardReplayFunnel,
  LimitUpBoardReplayInputManifest,
  LimitUpBoardReplayMutationResult,
  LimitUpBoardReplayOpenPosition,
  LimitUpBoardReplayPreparation,
  LimitUpBoardReplayRejectionReason,
  LimitUpBoardReplayRequest,
  LimitUpBoardReplayRequestedRange,
  LimitUpBoardReplayScenario,
  LimitUpBoardReplayScenarioAssumption,
  LimitUpBoardReplayStartInput,
  LimitUpBoardReplaySummary,
  LimitUpBoardReplayTickFieldQuality,
  LimitUpBoardReplayTickLoadError,
  LimitUpBoardReplayTrade,
  LimitUpBoardReplayTradePage,
  LimitUpBoardReplayVersions,
)

logger = logging.getLogger(__name__)

_JOB_ID_NAMESPACE = uuid.uuid5(
  uuid.NAMESPACE_URL,
  "quantx:limit-up-board-replay-job:v1",
)
_ACTIVE_STATUSES = frozenset({"PENDING", "STARTING", "RUNNING"})


class LimitUpBoardReplayResolver:
  projection_service = limit_up_board_replay_projection_service
  assistant_projection_service = limit_up_board_assistant_projection_service
  result_service = limit_up_board_replay_result_service

  @classmethod
  async def prepare(
    cls,
    account_id: str,
    start_time: datetime,
    end_time: datetime,
    scenario_profile: str,
  ) -> LimitUpBoardReplayPreparation:
    profile = _profile_value(scenario_profile)
    blockers = _validate_time_range(start_time, end_time)
    assistant = await cls.assistant_projection_service.get(account_id)
    if assistant is None:
      blockers.append("ASSISTANT_NOT_CONFIGURED")

    jobs = await cls.projection_service.list_by_account(account_id, 100)
    active = next(
      (
        item
        for item in jobs
        if str(item.get("status") or "").upper() in _ACTIVE_STATUSES
      ),
      None,
    )
    if active is not None:
      blockers.append("ACTIVE_REPLAY_EXISTS")

    scenarios = _scenario_assumptions(profile)
    ready = not blockers
    if ready:
      message = "可以启动；候选时点数据完整性将在任务启动时执行最终校验"
    elif "ACTIVE_REPLAY_EXISTS" in blockers:
      message = "当前账户已有执行中的打板回放任务"
    elif "ASSISTANT_NOT_CONFIGURED" in blockers:
      message = "请先保存打板助手配置"
    else:
      message = "回放时间范围无效"
    return LimitUpBoardReplayPreparation(
      account_id=account_id,
      start_time=start_time,
      end_time=end_time,
      scenario_profile=profile,
      ready=ready,
      assistant_config_version=int((assistant or {}).get("config_version", 0) or 0),
      assistant_projection_version=str(
        (assistant or {}).get("projection_version", "0") or "0"
      ),
      has_active_job=active is not None,
      active_job_id=(str(active.get("job_id")) if active else None),
      message=message,
      blockers=blockers,
      warnings=([] if blockers else ["DATA_QUALITY_VALIDATED_ON_START"]),
      scenarios=scenarios,
    )

  @classmethod
  async def get(cls, job_id: str) -> Optional[LimitUpBoardReplay]:
    snapshot = await cls.projection_service.get(str(job_id or "").strip())
    if not snapshot:
      return None
    results = await cls._load_scenario_results([snapshot])
    return cls._replay_type(snapshot, results)

  @classmethod
  async def replay_account_id(cls, job_id: str) -> Optional[str]:
    snapshot = await cls.projection_service.get(str(job_id or "").strip())
    account_id = snapshot.get("account_id") if snapshot else None
    return str(account_id) if account_id else None

  @classmethod
  async def history(
    cls,
    account_id: str,
    limit: int,
  ) -> list[LimitUpBoardReplay]:
    snapshots = await cls.projection_service.list_by_account(account_id, limit)
    results = await cls._load_scenario_results(snapshots)
    return [cls._replay_type(item, results) for item in snapshots]

  @classmethod
  async def trades(
    cls,
    job_id: str,
    scenario_id: str,
    offset: int,
    limit: int,
  ) -> LimitUpBoardReplayTradePage:
    snapshot, scenario, result = await cls._scenario_result(job_id, scenario_id)
    items = list(result.get("trades") or [])
    start, page_size = _page_window(offset, limit, maximum=500)
    page = items[start : start + page_size]
    return LimitUpBoardReplayTradePage(
      job_id=str(snapshot.get("job_id") or ""),
      scenario_id=str(scenario.get("scenario_id") or ""),
      total=len(items),
      offset=start,
      limit=page_size,
      has_more=start + len(page) < len(items),
      items=[_trade_type(item) for item in page],
    )

  @classmethod
  async def curve(
    cls,
    job_id: str,
    scenario_id: str,
    offset: int,
    limit: int,
  ) -> LimitUpBoardReplayCurvePage:
    snapshot, scenario, result = await cls._scenario_result(job_id, scenario_id)
    items = list(result.get("curve") or [])
    start, page_size = _page_window(offset, limit, maximum=2_000)
    page = items[start : start + page_size]
    return LimitUpBoardReplayCurvePage(
      job_id=str(snapshot.get("job_id") or ""),
      scenario_id=str(scenario.get("scenario_id") or ""),
      total=len(items),
      offset=start,
      limit=page_size,
      has_more=start + len(page) < len(items),
      items=[_curve_point_type(item) for item in page],
    )

  @classmethod
  async def _scenario_result(
    cls,
    job_id: str,
    scenario_id: str,
  ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    snapshot = await cls.projection_service.get(str(job_id or "").strip())
    if snapshot is None:
      raise ValueError("打板历史回放任务不存在")
    normalized_scenario_id = str(scenario_id or "").strip().upper()
    scenario = next(
      (
        dict(item or {})
        for item in list(snapshot.get("scenarios") or [])
        if str(dict(item or {}).get("scenario_id") or "").upper()
        == normalized_scenario_id
      ),
      None,
    )
    if scenario is None:
      raise ValueError("打板历史回放成交情景不存在")
    backtest_id = str(scenario.get("backtest_id") or "")
    results = await cls.result_service.load_many([backtest_id])
    result = dict(results.get(backtest_id) or {})
    _validate_result_scenario(result, normalized_scenario_id)
    return dict(snapshot), scenario, result

  @classmethod
  async def _load_scenario_results(
    cls,
    snapshots: list[Mapping[str, Any]],
  ) -> dict[str, dict[str, Any]]:
    backtest_ids = [
      str(scenario.get("backtest_id") or "")
      for snapshot in snapshots
      for scenario in list(snapshot.get("scenarios") or [])
      if isinstance(scenario, Mapping) and scenario.get("backtest_id")
    ]
    return await cls.result_service.load_many(backtest_ids)

  @classmethod
  async def start(
    cls,
    input: LimitUpBoardReplayStartInput,
  ) -> LimitUpBoardReplayMutationResult:
    idempotency_key = str(input.idempotency_key or "").strip()
    if not idempotency_key:
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="INVALID_IDEMPOTENCY_KEY",
        message="回放请求幂等键不能为空",
      )
    profile = _profile_value(input.scenario_profile)
    blockers = _validate_time_range(input.start_time, input.end_time)
    if input.initial_cash is not None and input.initial_cash < 0:
      blockers.append("INVALID_INITIAL_CASH")
    if input.initial_total_asset is not None and input.initial_total_asset <= 0:
      blockers.append("INVALID_INITIAL_TOTAL_ASSET")
    if blockers:
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message="；".join(blockers),
      )

    job_id = _replay_job_id(input.account_id, idempotency_key)
    existing = await cls.projection_service.get(job_id)
    if existing is not None:
      if str(existing.get("account_id") or "") != str(input.account_id):
        return LimitUpBoardReplayMutationResult(
          success=False,
          code="IDEMPOTENCY_CONFLICT",
          message="回放幂等键已绑定其他账户",
        )
      if not _request_matches_input(existing.get("request"), input, profile):
        return LimitUpBoardReplayMutationResult(
          success=False,
          code="IDEMPOTENCY_CONFLICT",
          message="回放幂等键已绑定其他请求参数",
        )
      results = await cls._load_scenario_results([existing])
      return LimitUpBoardReplayMutationResult(
        success=True,
        code="REPLAY_ALREADY_EXISTS",
        message="已返回该幂等请求对应的打板历史回放",
        replay=cls._replay_type(existing, results),
      )

    preparation = await cls.prepare(
      input.account_id,
      input.start_time,
      input.end_time,
      profile,
    )
    if not preparation.ready:
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_NOT_READY",
        message=preparation.message,
      )

    payload = _start_payload(input, job_id=job_id, scenario_profile=profile)
    try:
      receipt = await engine_command_service.request(
        "LIMIT_UP_BOARD_REPLAY_START",
        {"input": payload},
        aggregate_id=job_id,
        idempotency_key=(
          f"limit-up-board-replay:{input.account_id}:{idempotency_key}"
        ),
      )
    except Exception:
      logger.exception(
        "提交打板历史回放命令失败: account_id=%s job_id=%s",
        input.account_id,
        job_id,
      )
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_START_FAILED",
        message="打板历史回放请求提交失败，请稍后重试",
      )
    if receipt.status == "FAILED":
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_START_FAILED",
        message=receipt.error or "打板历史回放启动失败",
      )

    snapshot = dict(receipt.result or {})
    if not snapshot:
      snapshot = await cls.projection_service.get(job_id) or {}
    if not snapshot:
      snapshot = _pending_snapshot(payload)
    results = await cls._load_scenario_results([snapshot])
    replay = cls._replay_type(snapshot, results)
    started = receipt.status == "SUCCEEDED" and replay.status in {
      "STARTING",
      "RUNNING",
      "COMPLETED",
    }
    return LimitUpBoardReplayMutationResult(
      success=True,
      code="REPLAY_STARTED" if started else "REPLAY_ACCEPTED",
      message=(
        "打板历史回放已启动"
        if started
        else "打板历史回放请求已接受，正在后台准备"
      ),
      replay=replay,
    )

  @classmethod
  async def cancel(cls, job_id: str) -> LimitUpBoardReplayMutationResult:
    normalized = str(job_id or "").strip()
    snapshot = await cls.projection_service.get(normalized)
    if snapshot is None:
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_NOT_FOUND",
        message="打板历史回放任务不存在",
      )
    if str(snapshot.get("status") or "").upper() not in _ACTIVE_STATUSES:
      results = await cls._load_scenario_results([snapshot])
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_NOT_CANCELLABLE",
        message="只有等待中或执行中的回放任务可以取消",
        replay=cls._replay_type(snapshot, results),
      )
    try:
      receipt = await engine_command_service.request(
        "LIMIT_UP_BOARD_REPLAY_CANCEL",
        {"job_id": normalized},
        aggregate_id=normalized,
        idempotency_key=f"limit-up-board-replay-cancel:{normalized}",
      )
    except Exception:
      logger.exception("提交打板历史回放取消命令失败: job_id=%s", normalized)
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_CANCEL_FAILED",
        message="打板历史回放取消请求提交失败，请稍后重试",
      )
    if receipt.status == "FAILED":
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_CANCEL_FAILED",
        message=receipt.error or "打板历史回放取消失败",
      )
    result = dict(receipt.result or {})
    if not result:
      result = await cls.projection_service.get(normalized) or snapshot
    results = await cls._load_scenario_results([result])
    replay = cls._replay_type(result, results)
    cancelled = replay.status == "CANCELLED"
    return LimitUpBoardReplayMutationResult(
      success=True,
      code="REPLAY_CANCELLED" if cancelled else "REPLAY_CANCEL_ACCEPTED",
      message="打板历史回放已取消" if cancelled else "取消请求已接受",
      replay=replay,
    )

  @classmethod
  def _replay_type(
    cls,
    data: Mapping[str, Any],
    scenario_results: Optional[Mapping[str, Mapping[str, Any]]] = None,
  ) -> LimitUpBoardReplay:
    profile = str(
      data.get("scenario_profile") or LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE
    ).upper()
    request = dict(data.get("request") or {})
    request.setdefault("scenario_profile", profile)
    specs = {
      item.scenario_id: item
      for item in get_limit_up_board_replay_scenarios(profile)
    }
    scenarios = []
    for raw in list(data.get("scenarios") or []):
      item = dict(raw or {})
      scenario_id = str(item.get("scenario_id") or "")
      spec = specs.get(scenario_id)
      backtest_id = str(item.get("backtest_id") or "")
      result = dict((scenario_results or {}).get(backtest_id) or {})
      _validate_result_scenario(result, scenario_id)
      scenarios.append(
        LimitUpBoardReplayScenario(
          scenario_id=scenario_id,
          label=spec.label if spec else scenario_id,
          backtest_id=_optional_string(backtest_id),
          status=str(item.get("status") or "PENDING").upper(),
          progress_pct=float(item.get("progress_pct", 0.0) or 0.0),
          processed_until=_datetime(item.get("processed_until")),
          revision=str(item.get("revision") or "0"),
          error_message=_optional_string(item.get("error_message")),
          confirmation_delay_ms=int(item.get("confirmation_delay_ms", 0) or 0),
          participation_cap_pct=float(
            item.get("participation_cap_pct", 0.0) or 0.0
          ),
          book_depth_participation_pct=float(
            item.get("book_depth_participation_pct", 0.0) or 0.0
          ),
          theoretical_upper_bound=bool(
            spec and spec.is_theoretical_upper_bound
          ),
          result_available=bool(result),
          result_schema_version=int(result.get("schema_version", 0) or 0),
          no_queue_credit=bool(result.get("no_queue_credit", True)),
          summary=_summary_type(result.get("summary")),
          funnel=_funnel_type(result.get("funnel")),
          constraint_statistics=_constraint_metrics(
            result.get("constraint_statistics")
          ),
          rejection_reasons=_rejection_reasons(
            result.get("rejection_reasons")
          ),
          open_positions=_open_positions(result.get("open_positions")),
        )
      )
    return LimitUpBoardReplay(
      job_id=str(data.get("job_id") or ""),
      account_id=str(data.get("account_id") or ""),
      status=str(data.get("status") or "PENDING").upper(),
      progress_pct=float(data.get("progress_pct", 0.0) or 0.0),
      processed_until=_datetime(data.get("processed_until")),
      revision=str(data.get("revision") or "0"),
      scenario_profile=profile,
      request=_request_type(request),
      dataset_fingerprint=str(data.get("dataset_fingerprint") or ""),
      config_fingerprint=str(data.get("config_fingerprint") or ""),
      input_manifest=_manifest_type(data.get("input_manifest")),
      data_quality=_data_quality_type(data.get("data_quality")),
      error_message=_optional_string(data.get("error_message")),
      started_at=_datetime(data.get("started_at")),
      completed_at=_datetime(data.get("completed_at")),
      created_at=_datetime(data.get("created_at")),
      updated_at=_datetime(data.get("updated_at")),
      scenarios=scenarios,
    )


def _profile_value(value: Any) -> str:
  raw = value.value if isinstance(value, Enum) else value
  profile = str(raw or LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE).strip().upper()
  get_limit_up_board_replay_scenarios(profile)
  return profile


def _scenario_assumptions(
  profile: str,
) -> list[LimitUpBoardReplayScenarioAssumption]:
  return [
    LimitUpBoardReplayScenarioAssumption(
      scenario_id=item.scenario_id,
      label=item.label,
      confirmation_delay_ms=item.confirmation_delay_ms,
      participation_cap_pct=item.participation_cap_pct,
      book_depth_participation_pct=item.book_depth_participation_pct,
      theoretical_upper_bound=item.is_theoretical_upper_bound,
    )
    for item in get_limit_up_board_replay_scenarios(profile)
  ]


def _validate_time_range(start_time: datetime, end_time: datetime) -> list[str]:
  if start_time >= end_time:
    return ["INVALID_TIME_RANGE"]
  return []


def _replay_job_id(account_id: str, idempotency_key: str) -> str:
  return str(
    uuid.uuid5(
      _JOB_ID_NAMESPACE,
      f"{str(account_id or '').strip()}:{str(idempotency_key or '').strip()}",
    )
  )


def _start_payload(
  input: LimitUpBoardReplayStartInput,
  *,
  job_id: str,
  scenario_profile: str,
) -> dict[str, Any]:
  return {
    "job_id": job_id,
    "account_id": str(input.account_id or "").strip(),
    "start_time": input.start_time,
    "end_time": input.end_time,
    "scenario_profile": scenario_profile,
    "initial_cash": input.initial_cash,
    "initial_total_asset": input.initial_total_asset,
  }


def _request_matches_input(
  value: Any,
  input: LimitUpBoardReplayStartInput,
  scenario_profile: str,
) -> bool:
  request = dict(value or {})
  try:
    return all(
      (
        _replay_time(request.get("start_time")) == _replay_time(input.start_time),
        _replay_time(request.get("end_time")) == _replay_time(input.end_time),
        str(
          request.get("scenario_profile") or LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE
        ).upper()
        == scenario_profile,
        input.initial_cash is None
        or _optional_float(request.get("initial_cash")) == input.initial_cash,
        input.initial_total_asset is None
        or _optional_float(request.get("initial_total_asset"))
        == input.initial_total_asset,
      )
    )
  except (TypeError, ValueError):
    return False


def _pending_snapshot(request: Mapping[str, Any]) -> dict[str, Any]:
  profile = str(request.get("scenario_profile") or "STANDARD_V1")
  return {
    "job_id": str(request.get("job_id") or ""),
    "account_id": str(request.get("account_id") or ""),
    "status": "PENDING",
    "progress_pct": 0.0,
    "processed_until": None,
    "revision": "0",
    "scenario_profile": profile,
    "request": dict(request),
    "dataset_fingerprint": "",
    "config_fingerprint": "",
    "input_manifest": {},
    "data_quality": {
      "status": "PENDING",
      "warnings": ["DATA_QUALITY_VALIDATED_ON_START"],
    },
    "error_message": None,
    "started_at": None,
    "completed_at": None,
    "created_at": None,
    "updated_at": None,
    "scenarios": [
      {
        **item.to_dict(),
        "status": "PENDING",
        "progress_pct": 0.0,
        "processed_until": None,
        "revision": "0",
        "error_message": None,
        "backtest_id": None,
      }
      for item in get_limit_up_board_replay_scenarios(profile)
    ],
  }


def _request_type(data: Mapping[str, Any]) -> LimitUpBoardReplayRequest:
  start_time = _datetime(data.get("start_time"))
  end_time = _datetime(data.get("end_time"))
  if start_time is None or end_time is None:
    raise ValueError("打板回放投影缺少请求时间范围")
  return LimitUpBoardReplayRequest(
    start_time=start_time,
    end_time=end_time,
    scenario_profile=str(
      data.get("scenario_profile") or LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE
    ),
    initial_cash=_optional_float(data.get("initial_cash")),
    initial_total_asset=_optional_float(data.get("initial_total_asset")),
  )


def _summary_type(value: Any) -> Optional[LimitUpBoardReplaySummary]:
  if not isinstance(value, Mapping):
    return None
  data = dict(value)
  return LimitUpBoardReplaySummary(
    initial_equity=float(data.get("initial_equity", 0.0) or 0.0),
    final_equity=float(data.get("final_equity", 0.0) or 0.0),
    total_return_pct=float(data.get("total_return_pct", 0.0) or 0.0),
    max_drawdown_pct=float(data.get("max_drawdown_pct", 0.0) or 0.0),
    cvar95_loss_pct=float(data.get("cvar95_loss_pct", 0.0) or 0.0),
    fees=float(data.get("fees", 0.0) or 0.0),
    fill_rate_pct=float(data.get("fill_rate_pct", 0.0) or 0.0),
    open_position_count=int(data.get("open_position_count", 0) or 0),
    open_order_count=int(data.get("open_order_count", 0) or 0),
    unsellable_position_count=int(
      data.get("unsellable_position_count", 0) or 0
    ),
  )


def _funnel_type(value: Any) -> Optional[LimitUpBoardReplayFunnel]:
  if not isinstance(value, Mapping):
    return None
  data = dict(value)
  return LimitUpBoardReplayFunnel(
    candidate_frames=int(data.get("candidate_frames", 0) or 0),
    candidate_observations=int(data.get("candidate_observations", 0) or 0),
    qualified_observations=int(data.get("qualified_observations", 0) or 0),
    entry_intents=int(data.get("entry_intents", 0) or 0),
    approval_due=int(data.get("approval_due", 0) or 0),
    approval_rejected=int(data.get("approval_rejected", 0) or 0),
    orders=int(data.get("orders", 0) or 0),
    filled_orders=int(data.get("filled_orders", 0) or 0),
    partial_orders=int(data.get("partial_orders", 0) or 0),
    expired_orders=int(data.get("expired_orders", 0) or 0),
    trades=int(data.get("trades", 0) or 0),
    completed_exits=int(data.get("completed_exits", 0) or 0),
  )


def _constraint_metrics(value: Any) -> list[LimitUpBoardReplayConstraintMetric]:
  if not isinstance(value, Mapping):
    return []
  return [
    LimitUpBoardReplayConstraintMetric(key=str(key), value=float(item or 0.0))
    for key, item in sorted(value.items())
    if isinstance(item, (int, float)) and not isinstance(item, bool)
  ]


def _rejection_reasons(value: Any) -> list[LimitUpBoardReplayRejectionReason]:
  if not isinstance(value, Mapping):
    return []
  return [
    LimitUpBoardReplayRejectionReason(
      reason=str(reason),
      count=max(0, int(count or 0)),
    )
    for reason, count in sorted(value.items())
  ]


def _open_positions(value: Any) -> list[LimitUpBoardReplayOpenPosition]:
  return [
    LimitUpBoardReplayOpenPosition(
      instrument_code=str(item.get("instrument_code") or ""),
      volume=max(0, int(item.get("volume", 0) or 0)),
      available_volume=max(0, int(item.get("available_volume", 0) or 0)),
      average_price=float(item.get("average_price", 0.0) or 0.0),
      last_price=float(item.get("last_price", 0.0) or 0.0),
      market_value=float(item.get("market_value", 0.0) or 0.0),
      status=str(item.get("status") or "UNKNOWN"),
    )
    for raw in list(value or [])
    if isinstance(raw, Mapping)
    for item in [dict(raw)]
  ]


def _trade_type(value: Any) -> LimitUpBoardReplayTrade:
  data = dict(value or {})
  return LimitUpBoardReplayTrade(
    trade_id=str(data.get("trade_id") or ""),
    order_id=str(data.get("order_id") or ""),
    instrument_code=str(data.get("instrument_code") or ""),
    side=str(data.get("side") or ""),
    price=float(data.get("price", 0.0) or 0.0),
    volume=max(0, int(data.get("volume", 0) or 0)),
    amount=float(data.get("amount", 0.0) or 0.0),
    fees=float(data.get("fees", 0.0) or 0.0),
    trade_time=_datetime(data.get("trade_time")),
  )


def _curve_point_type(value: Any) -> LimitUpBoardReplayCurvePoint:
  data = dict(value or {})
  timestamp = _datetime(data.get("timestamp"))
  if timestamp is None:
    raise ValueError("打板回放权益曲线点缺少时间")
  return LimitUpBoardReplayCurvePoint(
    timestamp=timestamp,
    equity=float(data.get("equity", 0.0) or 0.0),
    return_pct=float(data.get("return_pct", 0.0) or 0.0),
  )


def _page_window(offset: int, limit: int, *, maximum: int) -> tuple[int, int]:
  return max(0, int(offset or 0)), max(1, min(int(limit or 1), maximum))


def _validate_result_scenario(result: Mapping[str, Any], scenario_id: str) -> None:
  result_scenario_id = str(result.get("scenario_id") or "").strip().upper()
  if result_scenario_id and result_scenario_id != str(scenario_id).upper():
    raise ValueError("打板回放结果与成交情景绑定不一致")


def _manifest_type(value: Any) -> LimitUpBoardReplayInputManifest:
  data = dict(value or {})
  requested_range = dict(data.get("requested_range") or {})
  versions = dict(data.get("versions") or {})
  artifacts = dict(data.get("artifacts") or {})
  return LimitUpBoardReplayInputManifest(
    schema_version=int(data.get("schema_version", 0) or 0),
    source=str(data.get("source") or ""),
    requested_range=LimitUpBoardReplayRequestedRange(
      start_time=_datetime(requested_range.get("start_time")),
      end_time=_datetime(requested_range.get("end_time")),
      timezone=str(requested_range.get("timezone") or "Asia/Shanghai"),
    ),
    config_fingerprint=str(data.get("config_fingerprint") or ""),
    snapshot_refs_fingerprint=str(data.get("snapshot_refs_fingerprint") or ""),
    versions=LimitUpBoardReplayVersions(
      score=_strings(versions.get("score")),
      feature=_strings(versions.get("feature")),
      promotion_model=_strings(versions.get("promotion_model")),
      exit_policy=_strings(versions.get("exit_policy")),
    ),
    coverage=_coverage_type(data.get("coverage")),
    artifacts=LimitUpBoardReplayArtifacts(
      candidate_universe=_artifact_type(artifacts.get("candidate_universe")),
      raw_ticks=_artifact_type(artifacts.get("raw_ticks")),
    ),
    data_quality=_data_quality_type(data.get("data_quality")),
    dataset_fingerprint=str(data.get("dataset_fingerprint") or ""),
    manifest_sha256=str(data.get("manifest_sha256") or ""),
  )


def _data_quality_type(value: Any) -> LimitUpBoardReplayDataQuality:
  data = dict(value or {})
  coverage = dict(data.get("coverage") or {})
  tick_field_quality = dict(data.get("tick_field_quality") or {})
  return LimitUpBoardReplayDataQuality(
    status=str(data.get("status") or "PENDING").upper(),
    executable=bool(data.get("executable", False)),
    source=str(data.get("source") or ""),
    raw_tick_count=int(coverage.get("raw_tick_count", 0) or 0),
    five_level_missing=int(
      tick_field_quality.get("missing_five_level_book_count", 0) or 0
    ),
    native_limit_missing=int(
      tick_field_quality.get("missing_native_price_limits_count", 0) or 0
    ),
    fresh_coverage=float(
      coverage.get("candidate_fresh_tick_coverage_pct", 0.0) or 0.0
    ),
    coverage=_coverage_type(coverage),
    tick_field_quality=_tick_field_quality_type(tick_field_quality),
    tick_load_errors=_tick_load_errors(data.get("tick_load_errors")),
    future_data_violations=int(data.get("future_data_violations", 0) or 0),
    candidate_frame_count_mismatches=int(
      data.get("candidate_frame_count_mismatches", 0) or 0
    ),
    score_versions=_strings(data.get("score_versions")),
    feature_versions=_strings(data.get("feature_versions")),
    model_versions=_strings(data.get("model_versions")),
    exit_policy_versions=_strings(data.get("exit_policy_versions")),
    blockers=_strings(data.get("blockers")),
    warnings=_strings(data.get("warnings")),
  )


def _coverage_type(value: Any) -> LimitUpBoardReplayCoverage:
  data = dict(value or {})
  return LimitUpBoardReplayCoverage(
    frame_count=int(data.get("frame_count", 0) or 0),
    candidate_observations=int(data.get("candidate_observations", 0) or 0),
    promotion_eligible_observations=int(
      data.get("promotion_eligible_observations", 0) or 0
    ),
    candidate_instrument_count=int(
      data.get("candidate_instrument_count", 0) or 0
    ),
    covered_trading_dates=_strings(data.get("covered_trading_dates")),
    expected_trading_dates=_strings(data.get("expected_trading_dates")),
    missing_trading_dates=_strings(data.get("missing_trading_dates")),
    first_observed_at=_datetime(data.get("first_observed_at")),
    last_observed_at=_datetime(data.get("last_observed_at")),
    max_frame_gap_seconds=float(data.get("max_frame_gap_seconds", 0.0) or 0.0),
    frame_gaps_over_15_seconds=int(
      data.get("frame_gaps_over_15_seconds", 0) or 0
    ),
    missing_continuous_sessions=_strings(
      data.get("missing_continuous_sessions")
    ),
    session_boundary_gaps_over_15_seconds=int(
      data.get("session_boundary_gaps_over_15_seconds", 0) or 0
    ),
    scanner_stopped_frames=int(data.get("scanner_stopped_frames", 0) or 0),
    raw_tick_count=int(data.get("raw_tick_count", 0) or 0),
    raw_tick_instrument_count=int(
      data.get("raw_tick_instrument_count", 0) or 0
    ),
    missing_tick_instruments=_strings(data.get("missing_tick_instruments")),
    candidate_fresh_tick_coverage_pct=float(
      data.get("candidate_fresh_tick_coverage_pct", 0.0) or 0.0
    ),
    candidate_observations_without_fresh_tick=int(
      data.get("candidate_observations_without_fresh_tick", 0) or 0
    ),
    max_candidate_tick_age_seconds=float(
      data.get("max_candidate_tick_age_seconds", 0.0) or 0.0
    ),
  )


def _tick_field_quality_type(value: Any) -> LimitUpBoardReplayTickFieldQuality:
  data = dict(value or {})
  return LimitUpBoardReplayTickFieldQuality(
    tick_count=int(data.get("tick_count", 0) or 0),
    invalid_identity_count=int(data.get("invalid_identity_count", 0) or 0),
    derived_source_time_count=int(
      data.get("derived_source_time_count", 0) or 0
    ),
    missing_native_price_limits_count=int(
      data.get("missing_native_price_limits_count", 0) or 0
    ),
    missing_stock_status_count=int(
      data.get("missing_stock_status_count", 0) or 0
    ),
    missing_price_tick_count=int(
      data.get("missing_price_tick_count", 0) or 0
    ),
    missing_five_level_book_count=int(
      data.get("missing_five_level_book_count", 0) or 0
    ),
    missing_price_fields_count=int(
      data.get("missing_price_fields_count", 0) or 0
    ),
    duplicate_identity_count=int(
      data.get("duplicate_identity_count", 0) or 0
    ),
    conflicting_identity_count=int(
      data.get("conflicting_identity_count", 0) or 0
    ),
    blockers=_strings(data.get("blockers")),
    warnings=_strings(data.get("warnings")),
  )


def _tick_load_errors(value: Any) -> list[LimitUpBoardReplayTickLoadError]:
  if not isinstance(value, Mapping):
    return []
  return [
    LimitUpBoardReplayTickLoadError(
      instrument_code=str(instrument_code),
      message=str(message),
    )
    for instrument_code, message in sorted(value.items())
  ]


def _artifact_type(value: Any) -> LimitUpBoardReplayArtifact:
  data = dict(value or {})
  return LimitUpBoardReplayArtifact(
    content_sha256=str(data.get("content_sha256") or ""),
    row_count=int(data.get("row_count", 0) or 0),
    format=str(data.get("format") or ""),
    compression=str(data.get("compression") or ""),
  )


def _datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime) or value is None:
    return value
  return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _replay_time(value: Any) -> Optional[datetime]:
  parsed = _datetime(value)
  return time_utils.to_shanghai(parsed) if parsed is not None else None


def _optional_string(value: Any) -> Optional[str]:
  if value is None:
    return None
  normalized = str(value)
  return normalized if normalized else None


def _optional_float(value: Any) -> Optional[float]:
  return float(value) if value is not None else None


def _strings(value: Any) -> list[str]:
  return [str(item) for item in list(value or [])]
