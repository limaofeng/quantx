"""Auditable aggregates for V3 T-trade opportunity evaluations."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from quantx_domain.trading.t_trade_candidate_outcome import (
  CandidateOutcomeState,
  PostFillOutcomeStatus,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_trade_candidate_outcome_repository import (
  TTradeCandidateOutcomeRepository,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeOpportunityEvaluationRepository,
)

_MAX_READY_INTERVAL_SECONDS = 5.0
_MAX_DIAGNOSTIC_RANGE = timedelta(days=31)
_MAX_DIAGNOSTIC_ROWS = 50_000
_DIAGNOSTIC_PAGE_SIZE = 500
_FUNNEL_LABELS = {
  "ELIGIBLE": "合格持仓评估",
  "DATA_READY": "数据可决策",
  "PATTERN": "形态片段",
  "PREVIEW": "越过预览阈值",
  "CANDIDATE": "候选信号",
  "TRADE_INTENT": "待确认意图",
  "APPROVED": "人工确认",
  "ORDERED": "已下单",
  "FILLED": "已成交",
}
_ELIGIBILITY_BLOCKERS = {
  "UNIVERSE_ELIGIBILITY_UNAVAILABLE",
  "POSITION_NOT_ELIGIBLE",
  "INSTRUMENT_DRAINING",
}
_MIXED_VERSION_WARNING = "MIXED_SIGNAL_VERSIONS_EXPLICITLY_MERGED"
_OUTCOME_LABELS = {
  "AWAITING_APPROVAL": "待确认",
  "APPROVED": "已确认",
  "FILLED": "已成交",
  "REJECTED": "已拒绝",
  "EXPIRED": "已过期",
  "SUPPRESSED": "已抑制",
  "DUPLICATE_SUPPRESSED": "重复抑制",
}
_PERFORMANCE_REQUIRED_DATA_CODES = (
  "AUTHORITATIVE_EXECUTION_FEE_LEDGER",
  "COMPLETE_POST_FILL_CAUSAL_MARKET_PATH",
  "FIXED_WINDOW_SESSION_POLICY",
)


class _DiagnosticsCapacityExceeded(RuntimeError):
  """Internal sentinel: never return a silently truncated diagnostic cohort."""


class TTradeSignalDiagnosticsService:
  async def signal_diagnostics(
    self,
    account_id: str,
    *,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
    db: AsyncSession,
    strategy_run_id: Optional[str] = None,
    merge_versions: bool = False,
  ) -> dict[str, Any]:
    if start_time >= end_time:
      raise ValueError("做 T 信号诊断开始时间必须早于结束时间")
    if end_time - start_time > _MAX_DIAGNOSTIC_RANGE:
      return _unavailable_diagnostics(
        "DIAGNOSTICS_RANGE_TOO_LARGE",
        "单次做 T 诊断最多查询 31 天；请缩小时间范围后重试。",
        merge_versions=merge_versions,
      )
    try:
      evaluations = await self._load_evaluations(
        TTradeOpportunityEvaluationRepository(db),
        account_id=account_id,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        strategy_run_id=strategy_run_id,
      )
      if len(evaluations) > _MAX_DIAGNOSTIC_ROWS:
        raise _DiagnosticsCapacityExceeded
      intents = await self._load_intents(
        db,
        account_id=account_id,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        strategy_run_id=strategy_run_id,
        limit=_MAX_DIAGNOSTIC_ROWS - len(evaluations) + 1,
      )
      if len(evaluations) + len(intents) > _MAX_DIAGNOSTIC_ROWS:
        raise _DiagnosticsCapacityExceeded
      outcomes = await self._load_outcomes(
        db,
        account_id=account_id,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        strategy_run_id=strategy_run_id,
        limit=_MAX_DIAGNOSTIC_ROWS - len(evaluations) - len(intents) + 1,
      )
      if len(evaluations) + len(intents) + len(outcomes) > _MAX_DIAGNOSTIC_ROWS:
        raise _DiagnosticsCapacityExceeded
    except _DiagnosticsCapacityExceeded:
      return _unavailable_diagnostics(
        "DIAGNOSTICS_ROW_LIMIT_EXCEEDED",
        "诊断证据超过单次 50000 行安全上限；请缩小时间或限定股票。",
        merge_versions=merge_versions,
      )
    diagnostics = self.aggregate(
      evaluations=evaluations,
      intents=intents,
      outcomes=outcomes,
      merge_versions=merge_versions,
    )
    diagnostics["scope"] = {
      "strategy_run_id": str(strategy_run_id or "").strip() or None,
      "stock_code": str(stock_code or "").strip().upper() or None,
      "start_time": start_time.isoformat(),
      "end_time": end_time.isoformat(),
    }
    return diagnostics

  async def _load_evaluations(
    self,
    repository: TTradeOpportunityEvaluationRepository,
    *,
    account_id: str,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
    strategy_run_id: Optional[str],
  ) -> list[Any]:
    rows: list[Any] = []
    cursor_at: Optional[datetime] = None
    cursor_id: Optional[str] = None
    while True:
      remaining = _MAX_DIAGNOSTIC_ROWS - len(rows) + 1
      if remaining <= 0:
        raise _DiagnosticsCapacityExceeded
      page_size = min(_DIAGNOSTIC_PAGE_SIZE, remaining)
      page = await repository.list_evaluations(
        account_id=account_id,
        instrument_code=stock_code,
        strategy_run_id=strategy_run_id,
        started_at=start_time,
        ended_at=end_time,
        cursor_evaluated_at=cursor_at,
        cursor_id=cursor_id,
        limit=page_size,
      )
      rows.extend(page)
      if len(rows) > _MAX_DIAGNOSTIC_ROWS:
        raise _DiagnosticsCapacityExceeded
      if len(page) < page_size:
        break
      cursor_at = page[-1].evaluated_at
      cursor_id = page[-1].id
    rows.sort(key=lambda row: (row.evaluated_at, row.id))
    return rows

  @staticmethod
  async def _load_intents(
    db: AsyncSession,
    *,
    account_id: str,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
    strategy_run_id: Optional[str],
    limit: int,
  ) -> list[TradeIntentRecord]:
    conditions = [
      TradeIntentRecord.account_id == str(account_id),
      TradeIntentRecord.created_at >= time_utils.to_shanghai(start_time),
      TradeIntentRecord.created_at <= time_utils.to_shanghai(end_time),
      TradeIntentRecord.direction == "BUY",
    ]
    if stock_code:
      conditions.append(
        TradeIntentRecord.instrument_code == str(stock_code).strip().upper()
      )
    if strategy_run_id:
      conditions.append(
        TradeIntentRecord.strategy_run_id == str(strategy_run_id).strip()
      )
    normalized_limit = max(1, min(int(limit), _MAX_DIAGNOSTIC_ROWS + 1))
    result = await db.execute(
      select(TradeIntentRecord)
      .where(*conditions)
      .order_by(TradeIntentRecord.created_at.asc(), TradeIntentRecord.id.asc())
      .limit(normalized_limit)
    )
    raw_rows = list(result.scalars().all())
    if len(raw_rows) >= normalized_limit:
      # The SQL predicate intentionally remains portable across PostgreSQL and
      # SQLite JSON implementations.  A saturated pre-filter is therefore
      # treated as overflow instead of pretending the filtered subset is a
      # complete cohort.
      raise _DiagnosticsCapacityExceeded
    return [row for row in raw_rows if _is_v3_opportunity_intent(row)]

  @staticmethod
  async def _load_outcomes(
    db: AsyncSession,
    *,
    account_id: str,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
    strategy_run_id: Optional[str],
    limit: int,
  ) -> list[Any]:
    return await TTradeCandidateOutcomeRepository(db).list_for_scope(
      account_id=account_id,
      instrument_code=stock_code,
      started_at=start_time,
      ended_at=end_time,
      strategy_run_id=strategy_run_id,
      limit=max(1, min(int(limit), _MAX_DIAGNOSTIC_ROWS + 1)),
    )

  def aggregate(
    self,
    *,
    evaluations: Sequence[Any],
    intents: Sequence[Any],
    outcomes: Sequence[Any] = (),
    merge_versions: bool = False,
  ) -> dict[str, Any]:
    snapshots = [
      (row, snapshot) for row in evaluations if (snapshot := _snapshot(row)) is not None
    ]
    versions: Counter[tuple[str, str, Optional[str]]] = Counter()
    for _, snapshot in snapshots:
      versions[_version_key(snapshot)] += 1
    version_groups = [
      {
        "policy_version": key[0],
        "feature_schema_version": key[1],
        "profile_version": key[2],
        "count": count,
      }
      for key, count in sorted(
        versions.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2] or ""),
      )
    ]
    normalized_intents = list(intents)
    if merge_versions and snapshots:
      grouped_snapshots: list[
        tuple[
          Optional[tuple[str, str, Optional[str]]],
          Sequence[tuple[Any, Mapping[str, Any]]],
        ]
      ] = [
        (
          None if len(versions) > 1 else next(iter(versions)),
          snapshots,
        )
      ]
    else:
      grouped: dict[
        tuple[str, str, Optional[str]],
        list[tuple[Any, Mapping[str, Any]]],
      ] = defaultdict(list)
      for item in snapshots:
        grouped[_version_key(item[1])].append(item)
      grouped_snapshots = [
        (key, grouped[key])
        for key in sorted(
          grouped,
          key=lambda item: (item[0], item[1], item[2] or ""),
        )
      ]
    partitions = [
      _aggregate_partition(
        partition_snapshots,
        normalized_intents,
        outcomes,
        version_key=version_key,
      )
      for version_key, partition_snapshots in grouped_snapshots
    ]
    return {
      "available": True,
      "merged_versions": bool(merge_versions),
      "warnings": (
        [_MIXED_VERSION_WARNING] if merge_versions and len(version_groups) > 1 else []
      ),
      "partitions": partitions,
      "version_groups": version_groups,
    }


def _aggregate_partition(
  snapshots: Sequence[tuple[Any, Mapping[str, Any]]],
  intents: Sequence[Any],
  outcomes: Sequence[Any],
  *,
  version_key: Optional[tuple[str, str, Optional[str]]],
) -> dict[str, Any]:
  ready_seconds, fsm_dwell, fsm_transitions = _time_aggregates(snapshots)
  material = [item for item in snapshots if _is_material(item[0])]
  eligible = [item for item in material if _is_eligible(item[1])]
  data_ready = [
    item for item in eligible if str(item[1].get("data_health") or "") == "READY"
  ]
  pattern_episodes = _unique_values(data_ready, "episode_id")
  preview_episodes = {
    (_strategy_run_id(row), str(snapshot.get("episode_id")))
    for row, snapshot in data_ready
    if snapshot.get("episode_id")
    and _number(snapshot.get("opportunity_score")) is not None
    and _number(snapshot.get("preview_threshold")) is not None
    and _number(snapshot.get("opportunity_score"))
    >= _number(snapshot.get("preview_threshold"))
  }
  candidates = _unique_values(data_ready, "candidate_id")
  partition_outcomes = [
    item
    for item in outcomes
    if (
      str(getattr(item, "strategy_run_id", "")),
      str(getattr(item, "candidate_id", "")),
    )
    in candidates
  ]
  partition_intents = [
    item for item in intents if _intent_candidate_key(item) in candidates
  ]
  intent_ids = {
    str(getattr(item, "id", ""))
    for item in partition_intents
    if str(getattr(item, "id", ""))
  }
  approved = [item for item in partition_intents if _intent_approved(item)]
  ordered = [item for item in partition_intents if getattr(item, "order_id", None)]
  filled = [item for item in partition_intents if _intent_filled(item)]
  filled_candidate_keys = {
    candidate_key
    for item in filled
    if (candidate_key := _intent_candidate_key(item)) != ("", "")
  }
  funnel_counts = [
    ("ELIGIBLE", "MATERIAL_EVENTS", len(eligible)),
    ("DATA_READY", "MATERIAL_EVENTS", len(data_ready)),
    ("PATTERN", "RUN_SCOPED_EPISODES", len(pattern_episodes)),
    ("PREVIEW", "RUN_SCOPED_EPISODES", len(preview_episodes)),
    ("CANDIDATE", "RUN_SCOPED_CANDIDATES", len(candidates)),
    ("TRADE_INTENT", "TRADE_INTENTS", len(intent_ids)),
    ("APPROVED", "APPROVED_INTENTS", len(approved)),
    ("ORDERED", "ORDERS", len(ordered)),
    ("FILLED", "FILLS", len(filled)),
  ]
  funnel: list[dict[str, Any]] = []
  previous: Optional[int] = None
  previous_code: Optional[str] = None
  for code, unit_code, count in funnel_counts:
    funnel.append(
      {
        "code": code,
        "label": _FUNNEL_LABELS[code],
        "unit_code": unit_code,
        "denominator_code": previous_code,
        "count": count,
        "conversion_rate": (
          None if previous in {None, 0} else round(count / previous, 6)
        ),
      }
    )
    previous = count
    previous_code = code

  blocker_counts: Counter[tuple[str, str, str]] = Counter()
  for _, snapshot in material:
    seen: set[tuple[str, str, str]] = set()
    for blocker in snapshot.get("top_blockers") or snapshot.get("blockers") or []:
      normalized = _blocker(blocker)
      if normalized:
        seen.add(normalized)
    blocker_counts.update(seen)
  blocker_denominator = len(material)
  blockers = [
    {
      "blocker": {"code": code, "label": label, "detail": detail},
      "count": count,
      "rate": (
        None if blocker_denominator == 0 else round(count / blocker_denominator, 6)
      ),
      "denominator_code": "MATERIAL_EVENTS",
      "denominator_value": float(blocker_denominator),
    }
    for (code, label, detail), count in blocker_counts.most_common()
  ]

  score_counts: Counter[tuple[str, str, Optional[str], str, float, float]] = Counter()
  for _, snapshot in snapshots:
    score = _number(snapshot.get("opportunity_score"))
    if score is None:
      continue
    policy_version, feature_schema_version, profile_version = _version_key(snapshot)
    lower, upper = _score_bucket(score)
    score_counts[
      (
        policy_version,
        feature_schema_version,
        profile_version,
        str(snapshot.get("selected_path") or "NONE"),
        lower,
        upper,
      )
    ] += 1
  score_distribution = [
    {
      "policy_version": policy_version,
      "feature_schema_version": feature_schema_version,
      "profile_version": profile_version,
      "path": path,
      "lower_bound": lower,
      "upper_bound": upper,
      "count": count,
    }
    for (
      policy_version,
      feature_schema_version,
      profile_version,
      path,
      lower,
      upper,
    ), count in sorted(
      score_counts.items(),
      key=lambda item: (
        item[0][0],
        item[0][1],
        item[0][2] or "",
        item[0][3],
        item[0][4],
      ),
    )
  ]
  if version_key is None:
    policy_version = "MIXED"
    feature_schema_version = "MIXED"
    profile_version = None
  else:
    policy_version, feature_schema_version, profile_version = version_key
  return {
    "policy_version": policy_version,
    "feature_schema_version": feature_schema_version,
    "profile_version": profile_version,
    "denominator": {
      "code": "READY_INSTRUMENT_SECONDS",
      "label": "READY 标的时长（秒）",
      "ready_instrument_seconds": round(ready_seconds, 3),
    },
    "funnel": funnel,
    "blockers": blockers,
    "score_distribution": score_distribution,
    "fsm_dwell": fsm_dwell,
    "fsm_transitions": fsm_transitions,
    "candidate_outcomes": _candidate_outcomes(snapshots, partition_intents),
    "post_candidate_performance": _post_candidate_performance(
      partition_outcomes,
      expected_filled_candidate_keys=filled_candidate_keys,
    ),
  }


def _time_aggregates(
  snapshots: Sequence[tuple[Any, Mapping[str, Any]]],
) -> tuple[float, list[dict[str, Any]], list[dict[str, Any]]]:
  by_instrument: dict[
    tuple[str, str],
    list[tuple[Any, Mapping[str, Any]]],
  ] = defaultdict(list)
  for row, snapshot in snapshots:
    by_instrument[(_strategy_run_id(row), str(row.instrument_code))].append(
      (row, snapshot)
    )
  ready_seconds = 0.0
  dwell: Counter[tuple[str, str]] = Counter()
  incoming_transitions: Counter[tuple[str, str]] = Counter()
  transition_edges: Counter[tuple[str, str, str]] = Counter()
  for stream in by_instrument.values():
    stream.sort(key=lambda item: (item[0].evaluated_at, str(item[0].id)))
    previous_phases: dict[str, str] = {}
    for index, (row, snapshot) in enumerate(stream):
      lineage = _market_lineage(snapshot)
      connected_to_previous = False
      if index > 0:
        previous_row, previous_snapshot = stream[index - 1]
        previous_gap = (row.evaluated_at - previous_row.evaluated_at).total_seconds()
        connected_to_previous = (
          lineage == _market_lineage(previous_snapshot)
          and 0.0 <= previous_gap <= _MAX_READY_INTERVAL_SECONDS
        )
      if not connected_to_previous:
        previous_phases.clear()
      phases = {
        "PULLBACK": str((snapshot.get("pullback") or {}).get("phase") or "UNKNOWN"),
        "MOMENTUM": str((snapshot.get("momentum") or {}).get("phase") or "UNKNOWN"),
      }
      for branch, phase in phases.items():
        previous_phase = previous_phases.get(branch)
        if previous_phase is not None and previous_phase != phase:
          incoming_transitions[(branch, phase)] += 1
          transition_edges[(branch, previous_phase, phase)] += 1
        previous_phases[branch] = phase
      if index + 1 >= len(stream):
        continue
      next_row, next_snapshot = stream[index + 1]
      if lineage != _market_lineage(next_snapshot):
        continue
      seconds = max(
        0.0,
        (next_row.evaluated_at - row.evaluated_at).total_seconds(),
      )
      if seconds > _MAX_READY_INTERVAL_SECONDS:
        continue
      if str(snapshot.get("data_health") or "") == "READY":
        ready_seconds += seconds
      for branch, phase in phases.items():
        dwell[(branch, phase)] += seconds
  result = [
    {
      "branch": branch,
      "phase": phase,
      "duration_seconds": round(duration, 3),
      "transition_count": incoming_transitions[(branch, phase)],
    }
    for (branch, phase), duration in sorted(dwell.items())
  ]
  edges = [
    {
      "branch": branch,
      "from_phase": from_phase,
      "to_phase": to_phase,
      "count": count,
    }
    for (branch, from_phase, to_phase), count in sorted(transition_edges.items())
  ]
  return ready_seconds, result, edges


def _candidate_outcomes(
  snapshots: Sequence[tuple[Any, Mapping[str, Any]]],
  intents: Sequence[Any],
) -> list[dict[str, Any]]:
  by_candidate: dict[tuple[str, str], set[str]] = defaultdict(set)
  duplicate_candidates: set[tuple[str, str]] = set()
  for row, snapshot in snapshots:
    candidate_id = str(snapshot.get("candidate_id") or "")
    if not candidate_id:
      continue
    candidate_key = (_strategy_run_id(row), candidate_id)
    by_candidate[candidate_key].add(str(snapshot.get("candidate_status") or "NONE"))
    blocker_codes = {
      normalized[0]
      for item in snapshot.get("top_blockers") or snapshot.get("blockers") or []
      if (normalized := _blocker(item)) is not None
    }
    if "EPISODE_ALREADY_CONSUMED" in blocker_codes:
      duplicate_candidates.add(candidate_key)
  intent_by_candidate = {_intent_candidate_key(item): item for item in intents}
  counts: Counter[str] = Counter()
  for candidate_key, statuses in by_candidate.items():
    intent = intent_by_candidate.get(candidate_key)
    intent_status = str(getattr(intent, "status", "") or "").upper()
    if _intent_filled(intent):
      outcome = "FILLED"
    elif _intent_approved(intent):
      outcome = "APPROVED"
    elif intent_status == "REJECTED":
      outcome = "REJECTED"
    elif intent_status in {"EXPIRED", "CANCELLED"}:
      outcome = "EXPIRED"
    elif candidate_key in duplicate_candidates:
      outcome = "DUPLICATE_SUPPRESSED"
    elif "SUPPRESSED" in statuses:
      outcome = "SUPPRESSED"
    else:
      outcome = "AWAITING_APPROVAL"
    counts[outcome] += 1
  return [
    {"code": code, "label": _OUTCOME_LABELS[code], "count": count}
    for code, count in sorted(counts.items())
  ]


def _post_candidate_performance(
  outcomes: Sequence[Any],
  *,
  expected_filled_candidate_keys: set[tuple[str, str]],
) -> dict[str, Any]:
  if not expected_filled_candidate_keys:
    return _unavailable_performance(
      "POST_FILL_OUTCOME_NOT_RECORDED",
      "当前候选尚无权威入场成交，无法形成成交后因果样本。",
      _PERFORMANCE_REQUIRED_DATA_CODES,
    )
  outcomes_by_candidate: dict[tuple[str, str], list[Any]] = defaultdict(list)
  for row in outcomes:
    candidate_key = _candidate_outcome_key(row)
    if candidate_key in expected_filled_candidate_keys:
      outcomes_by_candidate[candidate_key].append(row)
  if any(
    len(outcomes_by_candidate.get(candidate_key, ())) != 1
    for candidate_key in expected_filled_candidate_keys
  ):
    return _unavailable_performance(
      "POST_FILL_OUTCOME_COHORT_INCOMPLETE",
      "权威已成交候选与成交后结果未一一对应；为避免幸存者偏差，禁止聚合残存样本。",
      ("COMPLETE_FILLED_CANDIDATE_OUTCOME_COHORT",),
    )
  states: list[CandidateOutcomeState] = []
  try:
    for candidate_key in sorted(expected_filled_candidate_keys):
      row = outcomes_by_candidate[candidate_key][0]
      payload = getattr(row, "state", None)
      if not isinstance(payload, Mapping):
        raise ValueError("candidate outcome state is not an object")
      state = CandidateOutcomeState.from_dict(payload)
      state_key = (
        state.definition.strategy_run_id,
        state.definition.candidate_id,
      )
      if state_key != candidate_key:
        raise ValueError("candidate outcome identity does not match persisted row")
      states.append(state)
  except (KeyError, TypeError, ValueError, OverflowError):
    return _unavailable_performance(
      "POST_FILL_OUTCOME_STATE_INVALID",
      "候选结果聚合状态损坏或结构版本不兼容，已拒绝计算。",
      ("VALID_CANDIDATE_OUTCOME_STATE",),
    )

  # The denominator is every candidate with any authoritative entry fill, not
  # only the candidates whose full observation window happened to survive.
  # Dropping early exits, partial entries, gaps, halts, or still-maturing paths
  # would make the reported return/MFE/MAE a survivor-only statistic.
  post_fill_cohort = [state for state in states if state.execution.entry_volume > 0]
  if len(post_fill_cohort) != len(expected_filled_candidate_keys):
    return _unavailable_performance(
      "POST_FILL_OUTCOME_COHORT_INCOMPLETE",
      "至少一个权威已成交候选缺少对应入场成交事实；为避免幸存者偏差，禁止聚合残存样本。",
      ("COMPLETE_FILLED_CANDIDATE_OUTCOME_COHORT",),
    )
  incomplete_cohort = [
    state
    for state in post_fill_cohort
    if state.post_fill.status is not PostFillOutcomeStatus.MATURED
  ]
  if incomplete_cohort:
    return _unavailable_performance(
      "POST_FILL_COHORT_INCOMPLETE",
      "成交后样本中存在部分入场、提前退出、行情中断或尚未成熟窗口；为避免幸存者偏差，禁止仅聚合其余成熟样本。",
      ("COMPLETE_POST_FILL_COHORT",),
    )
  matured = post_fill_cohort
  if any(not state.post_fill.net_available for state in matured):
    return _unavailable_performance(
      "AUTHORITATIVE_EXECUTION_FEES_INCOMPLETE",
      "至少一个成熟成交后样本缺少权威费用，禁止从其余样本推断费用后表现。",
      ("AUTHORITATIVE_EXECUTION_FEE_LEDGER",),
    )

  net_mfe_values = [
    float(state.post_fill.running_net_mfe_pct)
    for state in matured
    if state.post_fill.running_net_mfe_pct is not None
  ]
  net_mae_values = [
    float(state.post_fill.running_net_mae_pct)
    for state in matured
    if state.post_fill.running_net_mae_pct is not None
  ]
  if len(net_mfe_values) != len(matured) or len(net_mae_values) != len(matured):
    return _unavailable_performance(
      "POST_FILL_NET_EXCURSION_INCOMPLETE",
      "成熟样本缺少完整费用后 MFE/MAE，禁止以零或部分样本代替。",
      ("COMPLETE_POST_FILL_NET_EXCURSION",),
    )
  window_values: dict[int, list[float]] = defaultdict(list)
  for state in matured:
    for horizon in state.post_fill.horizons:
      if horizon.net_return_pct is None:
        return _unavailable_performance(
          "POST_FILL_NET_WINDOW_INCOMPLETE",
          "成熟样本缺少费用后固定窗口收益，禁止以零代替。",
          ("COMPLETE_POST_FILL_NET_FIXED_WINDOWS",),
        )
      window_values[int(horizon.horizon_seconds)].append(float(horizon.net_return_pct))
  if any(len(values) != len(matured) for values in window_values.values()):
    return _unavailable_performance(
      "POST_FILL_WINDOW_SCHEMA_MISMATCH",
      "成熟样本的固定窗口集合不一致，禁止跨结构版本聚合。",
      ("CONSISTENT_FIXED_WINDOW_SCHEMA",),
    )
  return {
    "available": True,
    "reason_code": None,
    "reason": None,
    "sample_count": len(matured),
    "net_mfe_pct": sum(net_mfe_values) / len(net_mfe_values),
    "net_mae_pct": sum(net_mae_values) / len(net_mae_values),
    "fixed_window_returns": [
      {
        "window_seconds": seconds,
        "sample_count": len(values),
        "average_net_return_pct": sum(values) / len(values),
      }
      for seconds, values in sorted(window_values.items())
    ],
    "required_data_codes": [],
  }


def _unavailable_performance(
  reason_code: str,
  reason: str,
  required_data_codes: Sequence[str],
) -> dict[str, Any]:
  return {
    "available": False,
    "reason_code": reason_code,
    "reason": reason,
    "sample_count": 0,
    "net_mfe_pct": None,
    "net_mae_pct": None,
    "fixed_window_returns": [],
    "required_data_codes": list(required_data_codes),
  }


def _snapshot(row: Any) -> Optional[dict[str, Any]]:
  payload = getattr(row, "payload", None)
  if not isinstance(payload, Mapping):
    return None
  value = payload.get("signal_snapshot")
  return dict(value) if isinstance(value, Mapping) else None


def _unique_values(
  snapshots: Sequence[tuple[Any, Mapping[str, Any]]],
  key: str,
) -> set[tuple[str, str]]:
  return {
    (_strategy_run_id(row), str(value))
    for row, snapshot in snapshots
    if (value := snapshot.get(key)) is not None and str(value)
  }


def _strategy_run_id(row: Any) -> str:
  return str(getattr(row, "strategy_run_id", "") or "")


def _market_lineage(
  snapshot: Mapping[str, Any],
) -> tuple[str, str, tuple[str, str, Optional[str]]]:
  return (
    str(snapshot.get("trade_date") or ""),
    str(snapshot.get("continuity_generation") or ""),
    _version_key(snapshot),
  )


def _version_key(
  snapshot: Mapping[str, Any],
) -> tuple[str, str, Optional[str]]:
  return (
    str(snapshot.get("policy_version") or "UNVERSIONED"),
    str(snapshot.get("feature_schema_version") or "UNVERSIONED"),
    str(
      snapshot.get("profile_version") or snapshot.get("reference_profile_version") or ""
    ).strip()
    or None,
  )


def _is_material(row: Any) -> bool:
  return str(getattr(row, "record_kind", "") or "").upper() == "MATERIAL"


def _is_eligible(snapshot: Mapping[str, Any]) -> bool:
  blocker_codes = {
    normalized[0]
    for value in snapshot.get("top_blockers") or snapshot.get("blockers") or []
    if (normalized := _blocker(value)) is not None
  }
  return blocker_codes.isdisjoint(_ELIGIBILITY_BLOCKERS)


def _intent_candidate_key(intent: Any) -> tuple[str, str]:
  metadata = getattr(intent, "intent_metadata", None) or {}
  if not isinstance(metadata, Mapping):
    return "", ""
  return (
    str(getattr(intent, "strategy_run_id", "") or ""),
    str(metadata.get("candidate_id") or ""),
  )


def _candidate_outcome_key(outcome: Any) -> tuple[str, str]:
  return (
    str(getattr(outcome, "strategy_run_id", "") or ""),
    str(getattr(outcome, "candidate_id", "") or ""),
  )


def _blocker(value: Any) -> Optional[tuple[str, str, str]]:
  if isinstance(value, Mapping):
    code = str(value.get("code") or "").strip()
    if not code:
      return None
    return (
      code,
      str(value.get("label") or code),
      str(value.get("detail") or ""),
    )
  code = str(value or "").strip()
  return (code, code, "") if code else None


def _score_bucket(score: float) -> tuple[float, float]:
  for lower, upper in ((0.0, 40.0), (40.0, 55.0), (55.0, 72.0), (72.0, 85.0)):
    if score < upper:
      return lower, upper
  return 85.0, 100.0


def _number(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if normalized == normalized else None


def _is_v3_opportunity_intent(intent: Any) -> bool:
  metadata = getattr(intent, "intent_metadata", None) or {}
  if not isinstance(metadata, Mapping):
    return False
  try:
    schema_version = int(metadata.get("opportunity_schema_version", 0) or 0)
  except (TypeError, ValueError, OverflowError):
    return False
  return schema_version == 3 and bool(metadata.get("candidate_id"))


def _intent_approved(intent: Any) -> bool:
  if intent is None:
    return False
  status = str(getattr(intent, "status", "") or "").upper()
  return status in {
    "APPROVED",
    "ROUTED",
    "SUBMITTED",
    "ACCEPTED",
    "PARTIAL_FILLED",
    "FILLED",
  } or bool(getattr(intent, "order_id", None))


def _intent_filled(intent: Any) -> bool:
  if intent is None:
    return False
  status = str(getattr(intent, "status", "") or "").upper()
  return (
    status in {"PARTIAL_FILLED", "FILLED"}
    or int(getattr(intent, "executed_volume", 0) or 0) > 0
  )


def _unavailable_diagnostics(
  reason_code: str,
  reason: str,
  *,
  merge_versions: bool,
) -> dict[str, Any]:
  """Return an explicit unavailable contract; never disguise truncation."""

  return {
    "available": False,
    "reason_code": str(reason_code),
    "reason": str(reason),
    "merged_versions": bool(merge_versions),
    "warnings": [],
    "partitions": [],
    "version_groups": [],
  }


__all__ = ["TTradeSignalDiagnosticsService"]
