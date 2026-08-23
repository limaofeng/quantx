"""Fail-closed V3 evidence used before a T-trade execution rollout.

The evaluator intentionally reads only immutable/projection facts that already
exist in the operational database.  It does not manufacture a readiness
record, write a migration, or use process-local telemetry: a process restart
must never turn an unproven PAPER or replay result into a LIVE permission.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from typing import Any, Iterable, Mapping, Sequence
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import (
  AccountTradingRollout,
  AccountTradingRolloutEvent,
)
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.models.strategy_backtest import StrategyBacktest
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_candidate_outcome import (
  TTradeCandidateOutcome,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  T_TRADE_EVALUATION_KIND_MATERIAL,
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.t_trade_replay_projection import (
  TTradeReplayProjection,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

V3_ROLLOUT_EVIDENCE_SCHEMA_VERSION = 1
MIN_REPLAY_TRADING_DAYS = 20
MIN_PAPER_TRADING_DAYS = 5
MIN_PAPER_COMPLETED_CANDIDATES = 20
MAX_REPLAY_ROWS = 64
MAX_EVIDENCE_ROWS = 2_000
MAX_REVIEW_EVENT_ROWS = 64
# Readiness is polled by the monitor and UI.  A blocked proof is safe to
# reuse briefly: it can only delay a later promotion, never permit one.  A
# query error has a smaller TTL so an operational recovery is surfaced soon.
NEGATIVE_EVIDENCE_CACHE_TTL_SECONDS = 30.0
QUERY_ERROR_EVIDENCE_CACHE_TTL_SECONDS = 5.0
MAX_NEGATIVE_EVIDENCE_CACHE_ENTRIES = 64
MAX_NEGATIVE_EVIDENCE_IN_FLIGHT = 64
V3_OPERATOR_REVIEW_CONFIRMATION = "AUTHENTICATED_IDEMPOTENT_ACTIVATION"
PAPER_SESSION_EDGE_TOLERANCE = timedelta(minutes=5)
PAPER_MAX_EVIDENCE_GAP = timedelta(minutes=15)
PAPER_TRADING_SESSIONS = (
  (time(9, 30), time(11, 30)),
  (time(13, 0), time(15, 0)),
)

logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")

V3_ROLLOUT_GATE_CODES = frozenset(
  {
    "V3_EVIDENCE_QUERY_AVAILABLE",
    "V3_REPLAY_20_TRADING_DAYS",
    "V3_REPLAY_STRICT_CAUSAL",
    "V3_REPLAY_NORMAL_AND_ABNORMAL_COVERAGE",
    "V3_REPLAY_EPISODE_DUPLICATES_ZERO",
    "V3_REPLAY_GHOST_CANDIDATES_ZERO",
    "V3_REPLAY_FUTURE_DATA_ZERO",
    "V3_PAPER_5_CONSECUTIVE_TRADING_DAYS",
    "V3_PAPER_20_COMPLETED_CANDIDATE_LIFECYCLES",
    "V3_PAPER_EPISODE_DUPLICATES_ZERO",
    "V3_PAPER_GHOST_CANDIDATES_ZERO",
    "V3_PAPER_FUTURE_DATA_ZERO",
    "V3_PAPER_CANDIDATE_TRACE_COMPLETE",
    "V3_CANARY_LIMITS_CONFIGURED",
    "V3_OPERATOR_REVIEW_CONFIRMED",
  }
)


@dataclass(frozen=True, slots=True)
class _CandidateFacts:
  candidate_keys: frozenset[tuple[str, str]]
  candidate_count: int
  duplicate_count: int
  ghost_count: int
  future_data_violation_count: int
  trace_broken_count: int
  duplicate_intent_count: int
  matured_count: int
  primary_blocker: str
  policy_versions: tuple[str, ...]


@dataclass(slots=True)
class _NegativeEvidenceCacheEntry:
  """A local, fail-closed readiness result with a monotonic expiry."""

  expires_at: float
  result: dict[str, Any]
  reason: str


@dataclass(slots=True)
class _NegativeEvidenceCacheLoopState:
  """Loop-local coalescing state.

  ``asyncio`` synchronization primitives must not cross event loops.  The
  evaluator is a process-wide service singleton, so every loop gets its own
  bounded cache and in-flight registry instead of reusing an ``asyncio.Lock``
  created by a previous test or worker loop.
  """

  entries: OrderedDict[tuple[str, ...], _NegativeEvidenceCacheEntry]
  in_flight: dict[tuple[str, ...], asyncio.Future[dict[str, Any]]]
  guard: asyncio.Lock


def _text(value: Any) -> str:
  return str(value or "").strip()


def _mode(value: Any) -> str:
  return _text(getattr(value, "value", value)).upper()


def _integer(value: Any, default: int = 0) -> int:
  try:
    return int(value)
  except (TypeError, ValueError, OverflowError):
    return default


def _positive_integer(value: Any) -> int | None:
  normalized = _integer(value, -1)
  return normalized if normalized >= 0 else None


def _number(value: Any, default: float = 0.0) -> float:
  try:
    return float(value)
  except (TypeError, ValueError, OverflowError):
    return default


def _mapping(value: Any) -> dict[str, Any]:
  return dict(value) if isinstance(value, Mapping) else {}


def _datetime_text(value: Any) -> str:
  return value.isoformat(timespec="microseconds") if isinstance(value, datetime) else ""


def _snapshot(row: Any) -> dict[str, Any]:
  return _mapping(_mapping(getattr(row, "payload", None)).get("signal_snapshot"))


def _row_date(row: Any) -> date | None:
  snapshot = _snapshot(row)
  raw = _text(snapshot.get("trade_date"))
  try:
    return date.fromisoformat(raw)
  except ValueError:
    pass
  value = getattr(row, "evaluated_at", None)
  return value.date() if isinstance(value, datetime) else None


def _v3_evaluation(row: Any) -> bool:
  return _integer(getattr(row, "schema_version", None), 0) >= 3


def _candidate_key(row: Any) -> tuple[str, str] | None:
  candidate_id = _text(getattr(row, "candidate_id", None))
  run_id = _text(getattr(row, "strategy_run_id", None))
  snapshot_id = _text(_snapshot(row).get("candidate_id"))
  if not candidate_id or not run_id or candidate_id != snapshot_id:
    return None
  return run_id, candidate_id


def _canonical_fingerprint(value: Mapping[str, Any]) -> str:
  encoded = json.dumps(
    dict(value),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
    default=str,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


class TTradeV3RolloutEvidenceEvaluator:
  """Evaluate the V3 promotion evidence without mutating account state."""

  def __init__(self) -> None:
    # ``TTradeOperationsService`` owns one evaluator for the process.  Keep
    # the state explicitly loop-local so test runners and any future worker
    # loop cannot await a lock/future belonging to another loop.
    self._cache_state_lock = threading.RLock()
    self._cache_states: WeakKeyDictionary[
      asyncio.AbstractEventLoop,
      _NegativeEvidenceCacheLoopState,
    ] = WeakKeyDictionary()

  @staticmethod
  def _cache_key(
    *,
    account_id: str,
    rollout: AccountTradingRollout | None,
  ) -> tuple[str, ...]:
    """Bind a negative proof to every rollout fact that can make it stale.

    In particular, policy/snapshot/hash/stage changes always select a new
    key.  ``updated_at`` covers other rollout writes (for example canary
    limits), while the explicit fields make the safety binding auditable even
    if an ORM test double has no timestamps.
    """

    return (
      _text(account_id),
      _text(getattr(rollout, "policy_version", None)),
      _text(getattr(rollout, "last_snapshot_id", None)),
      _text(getattr(rollout, "last_snapshot_hash", None)),
      _text(getattr(rollout, "stage", None)).upper(),
      _text(getattr(rollout, "enabled", None)),
      _text(getattr(rollout, "max_active_batches", None)),
      _text(getattr(rollout, "max_batch_volume", None)),
      _text(getattr(rollout, "max_order_amount", None)),
      _text(getattr(rollout, "max_total_exposure_pct", None)),
      _text(getattr(rollout, "acknowledged_policy_version", None)),
      _datetime_text(getattr(rollout, "updated_at", None)),
    )

  @staticmethod
  def _cache_key_digest(key: tuple[str, ...]) -> str:
    # Expose only a stable diagnostic token.  Cache metadata is intentionally
    # top-level/internal and never part of the canonical evidence summary.
    return hashlib.sha256("\x1f".join(key).encode("utf-8")).hexdigest()[:16]

  def _loop_cache_state(self) -> _NegativeEvidenceCacheLoopState:
    loop = asyncio.get_running_loop()
    with self._cache_state_lock:
      state = self._cache_states.get(loop)
      if state is None:
        state = _NegativeEvidenceCacheLoopState(
          entries=OrderedDict(),
          in_flight={},
          guard=asyncio.Lock(),
        )
        self._cache_states[loop] = state
      return state

  @staticmethod
  def _negative_result(result: Mapping[str, Any]) -> bool:
    checks = list(result.get("checks") or [])
    # A malformed result must never be treated as a green proof.
    return not checks or any(not bool(item.get("passed")) for item in checks)

  @staticmethod
  def _negative_cache_ttl(result: Mapping[str, Any]) -> tuple[float, str]:
    summary = _mapping(result.get("summary"))
    if _text(summary.get("query_error")):
      return QUERY_ERROR_EVIDENCE_CACHE_TTL_SECONDS, "query_error"
    return NEGATIVE_EVIDENCE_CACHE_TTL_SECONDS, "blocked"

  @staticmethod
  def _cache_result(
    result: Mapping[str, Any],
    *,
    key: tuple[str, ...],
    state: str,
    reason: str,
    ttl_remaining_seconds: float = 0.0,
  ) -> dict[str, Any]:
    """Return an isolated result plus internal-only cache diagnostics.

    The metadata deliberately lives outside ``summary``.  ``summary`` is
    canonicalized for the operator-review fingerprint, so hit/miss/age must
    never invalidate a challenge with no corresponding business change.
    """

    copied = deepcopy(dict(result))
    copied["_cache"] = {
      "state": state,
      "reason": reason,
      "key": TTradeV3RolloutEvidenceEvaluator._cache_key_digest(key),
      "ttl_remaining_seconds": max(0.0, round(ttl_remaining_seconds, 3)),
    }
    return copied

  @staticmethod
  def _read_cached_negative(
    state: _NegativeEvidenceCacheLoopState,
    key: tuple[str, ...],
    *,
    now: float,
  ) -> _NegativeEvidenceCacheEntry | None:
    entry = state.entries.get(key)
    if entry is None:
      return None
    if entry.expires_at <= now:
      state.entries.pop(key, None)
      return None
    state.entries.move_to_end(key)
    return entry

  @staticmethod
  def _store_negative(
    state: _NegativeEvidenceCacheLoopState,
    key: tuple[str, ...],
    *,
    result: Mapping[str, Any],
    now: float,
  ) -> _NegativeEvidenceCacheEntry:
    ttl, reason = TTradeV3RolloutEvidenceEvaluator._negative_cache_ttl(result)
    entry = _NegativeEvidenceCacheEntry(
      expires_at=now + ttl,
      # Results contain nested summary/check maps.  Never let a caller mutate
      # the shared negative evidence used by a later readiness poll.
      result=deepcopy(dict(result)),
      reason=reason,
    )
    state.entries[key] = entry
    state.entries.move_to_end(key)
    while len(state.entries) > MAX_NEGATIVE_EVIDENCE_CACHE_ENTRIES:
      state.entries.popitem(last=False)
    return entry

  async def _evaluate_uncached(
    self,
    db: AsyncSession,
    *,
    account_id: str,
    rollout: AccountTradingRollout | None,
  ) -> dict[str, Any]:
    """Load and evaluate durable facts once, converting query failures."""

    try:
      loaded = await self._load(db, account_id=account_id)
      paper_calendar = await self._paper_calendar(loaded["paper_evaluations"])
      return self.evaluate_records(
        account_id=account_id,
        rollout=rollout,
        global_config=loaded["global_config"],
        live_run=loaded["live_run"],
        replay_rows=loaded["replays"],
        replay_evaluations=loaded["replay_evaluations"],
        replay_outcomes=loaded["replay_outcomes"],
        replay_intents=loaded["replay_intents"],
        paper_runs=loaded["paper_runs"],
        paper_evaluations=loaded["paper_evaluations"],
        paper_outcomes=loaded["paper_outcomes"],
        paper_intents=loaded["paper_intents"],
        paper_calendar=paper_calendar,
        replay_rows_truncated=loaded["replay_rows_truncated"],
        replay_evidence_truncated=loaded["replay_evidence_truncated"],
        paper_runs_truncated=loaded["paper_runs_truncated"],
        paper_evidence_truncated=loaded["paper_evidence_truncated"],
        review_events=loaded["review_events"],
        review_events_truncated=loaded["review_events_truncated"],
        query_available=True,
      )
    except Exception as exc:  # Readiness must describe an unavailable proof.
      logger.warning(
        "t_trade_v3_rollout_evidence_query_failed",
        extra={
          "event": "t_trade_v3_rollout_evidence_query_failed",
          "account_id": account_id,
          "error_type": type(exc).__name__,
        },
      )
      return self._unavailable(account_id=account_id, exc=exc)

  async def evaluate(
    self,
    db: AsyncSession,
    *,
    account_id: str,
    rollout: AccountTradingRollout | None,
    bypass_cache: bool = False,
  ) -> dict[str, Any]:
    """Return stable checks even when evidence is absent or unreadable.

    A readiness endpoint is an operator-facing status endpoint.  Missing data
    must therefore produce explicit blocked reasons instead of a generic 500;
    every such result remains fail-closed for CANARY and LIVE activation.
    """
    key = self._cache_key(account_id=account_id, rollout=rollout)
    if bypass_cache:
      # The final rollout transition runs under the row lock and must read
      # durable truth, even if a previous status poll cached a blocked result.
      # Do not write this result into the cache either: activation is a safety
      # boundary, not a cache refresh mechanism.
      return self._cache_result(
        await self._evaluate_uncached(
          db,
          account_id=account_id,
          rollout=rollout,
        ),
        key=key,
        state="bypass",
        reason="activation_locked_fresh_read",
      )

    cache_state = self._loop_cache_state()
    now = monotonic()
    cached = self._read_cached_negative(cache_state, key, now=now)
    if cached is not None:
      return self._cache_result(
        cached.result,
        key=key,
        state="hit",
        reason=cached.reason,
        ttl_remaining_seconds=cached.expires_at - now,
      )

    # Coalesce only calls on this event loop.  A process-wide asyncio.Future
    # would be unsafe across test/event-loop boundaries, and is unnecessary
    # for the single-loop API/Engine request paths.
    leader = False
    future: asyncio.Future[dict[str, Any]] | None = None
    async with cache_state.guard:
      now = monotonic()
      cached = self._read_cached_negative(cache_state, key, now=now)
      if cached is not None:
        return self._cache_result(
          cached.result,
          key=key,
          state="hit",
          reason=cached.reason,
          ttl_remaining_seconds=cached.expires_at - now,
        )
      future = cache_state.in_flight.get(key)
      if (
        future is None and len(cache_state.in_flight) < MAX_NEGATIVE_EVIDENCE_IN_FLIGHT
      ):
        future = asyncio.get_running_loop().create_future()
        cache_state.in_flight[key] = future
        leader = True

    if not leader and future is not None:
      result = await asyncio.shield(future)
      return self._cache_result(
        result,
        key=key,
        state="coalesced",
        reason="in_flight",
      )

    if not leader:
      # The bounded in-flight map is saturated by other keys.  Preserve
      # correctness with an uncached query rather than evicting an active
      # key and accidentally permitting a stampede for it.
      return self._cache_result(
        await self._evaluate_uncached(
          db,
          account_id=account_id,
          rollout=rollout,
        ),
        key=key,
        state="capacity_bypass",
        reason="in_flight_capacity",
      )

    assert future is not None
    try:
      result = await self._evaluate_uncached(
        db,
        account_id=account_id,
        rollout=rollout,
      )
      cached_entry = None
      if self._negative_result(result):
        cached_entry = self._store_negative(
          cache_state,
          key,
          result=result,
          now=monotonic(),
        )
      future.set_result(deepcopy(result))
      return self._cache_result(
        result,
        key=key,
        state="miss",
        reason=(cached_entry.reason if cached_entry is not None else "all_passed"),
        ttl_remaining_seconds=(
          cached_entry.expires_at - monotonic() if cached_entry is not None else 0.0
        ),
      )
    except BaseException:
      # Expected database errors are converted to a blocked proof above.  For
      # cancellation or interpreter-level failures, unblock any followers
      # without leaking a loop-bound failed Future into the next request.
      future.cancel()
      raise
    finally:
      async with cache_state.guard:
        if cache_state.in_flight.get(key) is future:
          cache_state.in_flight.pop(key, None)

  async def _load(self, db: AsyncSession, *, account_id: str) -> dict[str, Any]:
    replay_rows = list(
      (
        await db.execute(
          select(StrategyRun, TTradeReplayProjection, StrategyBacktest)
          .join(
            TTradeReplayProjection,
            TTradeReplayProjection.run_id == StrategyRun.id,
          )
          .outerjoin(
            StrategyBacktest,
            StrategyBacktest.strategy_run_id == StrategyRun.id,
          )
          .where(TTradeReplayProjection.account_id == account_id)
          .order_by(
            StrategyBacktest.end_time.desc().nullslast(),
            StrategyBacktest.version.desc().nullslast(),
            StrategyRun.updated_at.desc(),
          )
          .limit(MAX_REPLAY_ROWS + 1)
        )
      ).all()
    )
    replay_rows_truncated = len(replay_rows) > MAX_REPLAY_ROWS
    replay_rows = replay_rows[:MAX_REPLAY_ROWS]
    replay_run_ids = tuple(
      sorted(
        {
          _text(run.id)
          for run, _, _ in replay_rows
          if _text(run.id)
          and bool(_mapping(getattr(run, "parameters", None)).get("t_trade_replay"))
        }
      )
    )
    replay_evidence = await self._load_scope_evidence(
      db,
      account_id=account_id,
      run_ids=replay_run_ids,
    )

    paper_runs = list(
      (
        await db.execute(
          select(StrategyRun)
          .where(
            StrategyRun.mode == StrategyRunMode.PAPER,
            StrategyRun.parameters["account_id"].as_string() == account_id,
          )
          .order_by(StrategyRun.updated_at.desc())
          .limit(MAX_REPLAY_ROWS + 1)
        )
      )
      .scalars()
      .all()
    )
    paper_runs_truncated = len(paper_runs) > MAX_REPLAY_ROWS
    paper_runs = paper_runs[:MAX_REPLAY_ROWS]
    paper_run_ids = tuple(
      sorted(
        {
          _text(run.id)
          for run in paper_runs
          if _text(run.id)
          and _text(_mapping(getattr(run, "parameters", None)).get("account_id"))
          == account_id
        }
      )
    )
    paper_evidence = await self._load_scope_evidence(
      db,
      account_id=account_id,
      run_ids=paper_run_ids,
    )
    global_config = await db.get(TTradeGlobalConfig, account_id)
    live_run = None
    if global_config is not None and _text(global_config.strategy_run_id):
      candidate = await db.get(StrategyRun, _text(global_config.strategy_run_id))
      if candidate is not None:
        live_run = candidate
    review_events = list(
      (
        await db.execute(
          select(AccountTradingRolloutEvent)
          .where(AccountTradingRolloutEvent.account_id == account_id)
          .order_by(
            AccountTradingRolloutEvent.created_at.desc(),
            AccountTradingRolloutEvent.event_id.desc(),
          )
          .limit(MAX_REVIEW_EVENT_ROWS + 1)
        )
      )
      .scalars()
      .all()
    )
    review_events_truncated = len(review_events) > MAX_REVIEW_EVENT_ROWS
    return {
      "replays": replay_rows,
      "replay_rows_truncated": replay_rows_truncated,
      "replay_evaluations": replay_evidence["evaluations"],
      "replay_outcomes": replay_evidence["outcomes"],
      "replay_intents": replay_evidence["intents"],
      "replay_evidence_truncated": replay_evidence["truncated"],
      "paper_runs": paper_runs,
      "paper_runs_truncated": paper_runs_truncated,
      "paper_evaluations": paper_evidence["evaluations"],
      "paper_outcomes": paper_evidence["outcomes"],
      "paper_intents": paper_evidence["intents"],
      "paper_evidence_truncated": paper_evidence["truncated"],
      "global_config": global_config,
      "live_run": live_run,
      "review_events": review_events[:MAX_REVIEW_EVENT_ROWS],
      "review_events_truncated": review_events_truncated,
    }

  async def _load_scope_evidence(
    self,
    db: AsyncSession,
    *,
    account_id: str,
    run_ids: Sequence[str],
  ) -> dict[str, Any]:
    if not run_ids:
      return {"evaluations": [], "outcomes": [], "intents": [], "truncated": False}
    run_filter = tuple(run_ids)
    evaluations = list(
      (
        await db.execute(
          select(TTradeOpportunityEvaluation)
          .where(
            TTradeOpportunityEvaluation.account_id == account_id,
            TTradeOpportunityEvaluation.strategy_run_id.in_(run_filter),
          )
          .order_by(
            TTradeOpportunityEvaluation.evaluated_at.desc(),
            TTradeOpportunityEvaluation.id.desc(),
          )
          .limit(MAX_EVIDENCE_ROWS + 1)
        )
      )
      .scalars()
      .all()
    )
    outcomes = list(
      (
        await db.execute(
          select(TTradeCandidateOutcome)
          .where(
            TTradeCandidateOutcome.account_id == account_id,
            TTradeCandidateOutcome.strategy_run_id.in_(run_filter),
          )
          .order_by(
            TTradeCandidateOutcome.candidate_at.desc(),
            TTradeCandidateOutcome.id.desc(),
          )
          .limit(MAX_EVIDENCE_ROWS + 1)
        )
      )
      .scalars()
      .all()
    )
    intents = list(
      (
        await db.execute(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.account_id == account_id,
            TradeIntentRecord.strategy_run_id.in_(run_filter),
          )
          .order_by(TradeIntentRecord.created_at.desc(), TradeIntentRecord.id.desc())
          .limit(MAX_EVIDENCE_ROWS + 1)
        )
      )
      .scalars()
      .all()
    )
    truncated = any(
      len(rows) > MAX_EVIDENCE_ROWS for rows in (evaluations, outcomes, intents)
    )
    return {
      "evaluations": evaluations[:MAX_EVIDENCE_ROWS],
      "outcomes": outcomes[:MAX_EVIDENCE_ROWS],
      "intents": intents[:MAX_EVIDENCE_ROWS],
      "truncated": truncated,
    }

  async def _paper_calendar(
    self,
    evaluations: Sequence[Any],
  ) -> list[date] | None:
    dates = sorted({_row_date(row) for row in evaluations if _row_date(row)})
    if not dates:
      return []
    try:
      return await TradingDateHelper().get_trading_calendar(
        market="SH",
        start_date=dates[0],
        end_date=dates[-1],
      )
    except Exception:
      # The caller turns this into a precise blocked reason rather than
      # silently treating weekdays as exchange trading dates.
      return None

  def evaluate_records(
    self,
    *,
    account_id: str,
    rollout: Any,
    global_config: Any,
    live_run: Any,
    replay_rows: Sequence[tuple[Any, Any, Any]],
    replay_evaluations: Sequence[Any],
    replay_outcomes: Sequence[Any],
    replay_intents: Sequence[Any],
    paper_runs: Sequence[Any],
    paper_evaluations: Sequence[Any],
    paper_outcomes: Sequence[Any],
    paper_intents: Sequence[Any],
    paper_calendar: Sequence[date] | None,
    replay_rows_truncated: bool,
    replay_evidence_truncated: bool,
    paper_runs_truncated: bool,
    paper_evidence_truncated: bool,
    review_events: Sequence[Any],
    review_events_truncated: bool,
    query_available: bool,
  ) -> dict[str, Any]:
    """Pure evaluation seam used by focused tests and the read-only loader."""

    replay = self._replay_summary(
      replay_rows=replay_rows,
      evaluations=replay_evaluations,
      outcomes=replay_outcomes,
      intents=replay_intents,
      rows_truncated=replay_rows_truncated,
      evidence_truncated=replay_evidence_truncated,
    )
    paper = self._paper_summary(
      runs=paper_runs,
      evaluations=paper_evaluations,
      outcomes=paper_outcomes,
      intents=paper_intents,
      calendar=paper_calendar,
      evidence_truncated=bool(paper_runs_truncated or paper_evidence_truncated),
    )
    canary = self._canary_limits_summary(
      rollout=rollout,
      global_config=global_config,
      live_run=live_run,
    )
    review_inputs = self._operator_review_inputs(
      global_config=global_config,
      live_run=live_run,
      replay=replay,
      paper=paper,
    )
    review_evidence_fingerprint = _canonical_fingerprint(
      {
        "schema_version": V3_ROLLOUT_EVIDENCE_SCHEMA_VERSION,
        "account_id": account_id,
        "policy_version": _integer(getattr(rollout, "policy_version", None)),
        "snapshot_id": _text(getattr(rollout, "last_snapshot_id", None)),
        "replay": replay,
        "paper": paper,
        "canary": canary,
        "review_inputs": review_inputs,
      }
    )
    operator_review = self._operator_review_summary(
      rollout=rollout,
      review_inputs=review_inputs,
      review_evidence_fingerprint=review_evidence_fingerprint,
      review_events=review_events,
      review_events_truncated=review_events_truncated,
    )

    checks = [
      self._check(
        "V3_EVIDENCE_QUERY_AVAILABLE",
        query_available,
        "V3 上线证据查询不可用，已拒绝执行阶段激活",
      ),
      self._check(
        "V3_REPLAY_20_TRADING_DAYS",
        replay["trading_day_count"] >= MIN_REPLAY_TRADING_DAYS,
        "缺少至少 20 个交易日的 V3 历史回放证据",
      ),
      self._check(
        "V3_REPLAY_STRICT_CAUSAL",
        bool(replay["strict_causal"]),
        "回放未证明严格因果、完整 Tick 读取和 data_quality=OK",
      ),
      self._check(
        "V3_REPLAY_NORMAL_AND_ABNORMAL_COVERAGE",
        bool(replay["normal_and_abnormal_covered"]),
        "回放未证明同时覆盖正常与异常行情日",
      ),
      self._check(
        "V3_REPLAY_EPISODE_DUPLICATES_ZERO",
        bool(replay["quality_evidence_available"])
        and replay["duplicate_count"] == 0
        and not replay["evidence_truncated"],
        self._quality_message("回放 episode 重复", replay),
      ),
      self._check(
        "V3_REPLAY_GHOST_CANDIDATES_ZERO",
        bool(replay["quality_evidence_available"])
        and replay["ghost_count"] == 0
        and not replay["evidence_truncated"],
        self._quality_message("回放幽灵候选", replay),
      ),
      self._check(
        "V3_REPLAY_FUTURE_DATA_ZERO",
        bool(replay["quality_evidence_available"])
        and replay["future_data_violation_count"] == 0
        and not replay["evidence_truncated"],
        self._quality_message("回放未来数据违规", replay),
      ),
      self._check(
        "V3_PAPER_5_CONSECUTIVE_TRADING_DAYS",
        bool(paper["consecutive_days_ready"]),
        paper["consecutive_days_message"],
      ),
      self._check(
        "V3_PAPER_20_COMPLETED_CANDIDATE_LIFECYCLES",
        paper["matured_count"] >= MIN_PAPER_COMPLETED_CANDIDATES,
        "PAPER 完成的候选生命周期不足 20 个",
      ),
      self._check(
        "V3_PAPER_EPISODE_DUPLICATES_ZERO",
        bool(paper["quality_evidence_available"])
        and paper["duplicate_count"] == 0
        and not paper["evidence_truncated"],
        self._quality_message("PAPER episode 重复", paper),
      ),
      self._check(
        "V3_PAPER_GHOST_CANDIDATES_ZERO",
        bool(paper["quality_evidence_available"])
        and paper["ghost_count"] == 0
        and not paper["evidence_truncated"],
        self._quality_message("PAPER 幽灵候选", paper),
      ),
      self._check(
        "V3_PAPER_FUTURE_DATA_ZERO",
        bool(paper["quality_evidence_available"])
        and paper["future_data_violation_count"] == 0
        and not paper["evidence_truncated"],
        self._quality_message("PAPER 未来数据违规", paper),
      ),
      self._check(
        "V3_PAPER_CANDIDATE_TRACE_COMPLETE",
        bool(paper["quality_evidence_available"])
        and paper["trace_broken_count"] == 0
        and not paper["evidence_truncated"],
        self._quality_message("PAPER 候选追溯", paper),
      ),
      self._check(
        "V3_CANARY_LIMITS_CONFIGURED",
        bool(canary["passed"]),
        str(canary["message"]),
      ),
      self._check(
        "V3_OPERATOR_REVIEW_CONFIRMED",
        bool(operator_review["confirmed"]),
        str(operator_review["message"]),
      ),
    ]
    summary = {
      "schema_version": V3_ROLLOUT_EVIDENCE_SCHEMA_VERSION,
      "account_id": account_id,
      "replay": replay,
      "paper": paper,
      "canary": canary,
      "operator_review": operator_review,
    }
    summary["fingerprint"] = _canonical_fingerprint(summary)
    return {"checks": checks, "summary": summary}

  @staticmethod
  def _check(code: str, passed: bool, message: str) -> dict[str, Any]:
    return {
      "code": code,
      "passed": bool(passed),
      "message": "" if passed else message,
      "scope": "AUTOMATION",
    }

  @staticmethod
  def _quality_message(prefix: str, summary: Mapping[str, Any]) -> str:
    if bool(summary.get("evidence_truncated")):
      return f"{prefix}证据超过有界查询上限，拒绝以截断数据放行"
    for key, label in (
      ("duplicate_count", "次数"),
      ("ghost_count", "数量"),
      ("future_data_violation_count", "数量"),
      ("trace_broken_count", "断链数"),
    ):
      count = _integer(summary.get(key), 0)
      if count:
        return f"{prefix}{label}为 {count}，必须为 0"
    return f"{prefix}证据缺失，拒绝放行"

  def _replay_summary(
    self,
    *,
    replay_rows: Sequence[tuple[Any, Any, Any]],
    evaluations: Sequence[Any],
    outcomes: Sequence[Any],
    intents: Sequence[Any],
    rows_truncated: bool,
    evidence_truncated: bool,
  ) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    latest_backtests: dict[str, tuple[Any, Any, Any]] = {}
    for run, projection, backtest in replay_rows:
      run_id = _text(getattr(run, "id", None))
      if not run_id or not bool(
        _mapping(getattr(run, "parameters", None)).get("t_trade_replay")
      ):
        continue
      previous = latest_backtests.get(run_id)
      if previous is None or _integer(
        getattr(backtest, "version", None), -1
      ) > _integer(getattr(previous[2], "version", None), -1):
        latest_backtests[run_id] = (run, projection, backtest)
    for run, projection, backtest in latest_backtests.values():
      metrics = _mapping(getattr(backtest, "metrics", None))
      replay_metrics = _mapping(metrics.get("t_trade_replay"))
      proof = _mapping(replay_metrics.get("rollout_evidence"))
      dates = self._proof_dates(proof)
      run_id = _text(getattr(run, "id", None))
      candidate_facts = self._candidate_facts(
        evaluations=[
          row
          for row in evaluations
          if _text(getattr(row, "strategy_run_id", None)) == run_id
        ],
        outcomes=[
          row
          for row in outcomes
          if _text(getattr(row, "strategy_run_id", None)) == run_id
        ],
        intents=[
          row
          for row in intents
          if _text(getattr(row, "strategy_run_id", None)) == run_id
        ],
        require_matured_outcome=False,
      )
      tick_audit = _mapping(
        _mapping(replay_metrics.get("methodology")).get("tick_read_audit")
      )
      strict_causal = (
        _text(getattr(projection, "status", None)).upper() == "COMPLETED"
        and _text(getattr(backtest, "status", None)).upper() == "COMPLETED"
        and proof.get("strict_causal") is True
        and _text(replay_metrics.get("data_quality")).upper() == "OK"
        and _integer(tick_audit.get("verified_windows"), 0) > 0
        and not list(tick_audit.get("issues") or [])
      )
      normal_and_abnormal_covered = self._proof_covers_normal_and_abnormal(proof, dates)
      quality_evidence_available = bool(candidate_facts.candidate_count)
      candidate_evidence_truncated = bool(rows_truncated or evidence_truncated)
      gate_passes = {
        "trading_days": len(dates) >= MIN_REPLAY_TRADING_DAYS,
        "strict_causal": strict_causal,
        "normal_and_abnormal_coverage": normal_and_abnormal_covered,
        "episode_duplicates_zero": quality_evidence_available
        and candidate_facts.duplicate_count == 0
        and not candidate_evidence_truncated,
        "ghost_candidates_zero": quality_evidence_available
        and candidate_facts.ghost_count == 0
        and not candidate_evidence_truncated,
        "future_data_zero": quality_evidence_available
        and candidate_facts.future_data_violation_count == 0
        and not candidate_evidence_truncated,
      }
      candidates.append(
        {
          "run_id": run_id,
          "backtest_id": _text(getattr(backtest, "id", None)),
          "backtest_version": _integer(getattr(backtest, "version", None), 0),
          "completed_at": _datetime_text(getattr(backtest, "end_time", None)),
          "created_at": _datetime_text(getattr(backtest, "created_at", None)),
          "run_updated_at": _datetime_text(getattr(run, "updated_at", None)),
          "trading_dates": [item.isoformat() for item in dates],
          "trading_day_count": len(dates),
          "strict_causal": strict_causal,
          "normal_and_abnormal_covered": normal_and_abnormal_covered,
          "duplicate_count": candidate_facts.duplicate_count,
          "ghost_count": candidate_facts.ghost_count,
          "future_data_violation_count": candidate_facts.future_data_violation_count,
          "trace_broken_count": candidate_facts.trace_broken_count,
          "duplicate_intent_count": candidate_facts.duplicate_intent_count,
          "candidate_count": candidate_facts.candidate_count,
          "quality_evidence_available": quality_evidence_available,
          "evidence_truncated": candidate_evidence_truncated,
          "selection_gate_passes": gate_passes,
          "selection_gate_pass_count": sum(gate_passes.values()),
          "after_fee": self._after_fee_summary(replay_metrics),
          "policy_versions": list(candidate_facts.policy_versions),
          "primary_blocker": candidate_facts.primary_blocker,
        }
      )
    if candidates:
      # Never merge independent replays: all promotion facts must originate
      # from one successful, attributable 20-day replay.
      selected = max(
        candidates,
        key=lambda item: (
          int(item["selection_gate_pass_count"]),
          item["completed_at"],
          int(item["backtest_version"]),
          item["created_at"],
          item["run_updated_at"],
          item["backtest_id"],
        ),
      )
      return {"candidate_replay_count": len(candidates), **selected}
    return {
      "candidate_replay_count": 0,
      "run_id": "",
      "backtest_id": "",
      "backtest_version": 0,
      "completed_at": "",
      "created_at": "",
      "run_updated_at": "",
      "trading_dates": [],
      "trading_day_count": 0,
      "strict_causal": False,
      "normal_and_abnormal_covered": False,
      "duplicate_count": 0,
      "ghost_count": 0,
      "future_data_violation_count": 0,
      "trace_broken_count": 0,
      "duplicate_intent_count": 0,
      "candidate_count": 0,
      "quality_evidence_available": False,
      "evidence_truncated": bool(rows_truncated or evidence_truncated),
      "selection_gate_passes": {},
      "selection_gate_pass_count": 0,
      "after_fee": {},
      "policy_versions": [],
      "primary_blocker": "",
    }

  @staticmethod
  def _proof_dates(proof: Mapping[str, Any]) -> list[date]:
    result: list[date] = []
    for value in list(proof.get("trading_dates") or []):
      try:
        result.append(date.fromisoformat(_text(value)))
      except ValueError:
        continue
    return sorted(set(result))

  @staticmethod
  def _proof_covers_normal_and_abnormal(
    proof: Mapping[str, Any],
    trading_dates: Sequence[date],
  ) -> bool:
    coverage = _mapping(proof.get("market_scenario_coverage"))
    allowed = {item.isoformat() for item in trading_dates}
    normal = {_text(item) for item in list(coverage.get("normal_trading_dates") or [])}
    abnormal = {
      _text(item) for item in list(coverage.get("abnormal_trading_dates") or [])
    }
    return bool(normal and abnormal and normal <= allowed and abnormal <= allowed)

  def _paper_summary(
    self,
    *,
    runs: Sequence[Any],
    evaluations: Sequence[Any],
    outcomes: Sequence[Any],
    intents: Sequence[Any],
    calendar: Sequence[date] | None,
    evidence_truncated: bool,
  ) -> dict[str, Any]:
    v3_evaluations = [row for row in evaluations if _v3_evaluation(row)]
    observed_dates = {_row_date(row) for row in v3_evaluations if _row_date(row)}
    candidates: list[dict[str, Any]] = []
    coverage_attempts: list[dict[str, Any]] = []
    for run in runs:
      run_id = _text(getattr(run, "id", None))
      if not run_id or _mode(getattr(run, "mode", None)) != "PAPER":
        continue
      run_rows = [
        row
        for row in v3_evaluations
        if _text(getattr(row, "strategy_run_id", None)) == run_id
      ]
      run_dates = {_row_date(row) for row in run_rows if _row_date(row)}
      for window in self._consecutive_windows(run_dates, calendar):
        coverage = self._paper_window_coverage(
          run=run,
          evaluations=run_rows,
          trading_dates=window,
        )
        coverage_attempts.append(coverage)
        if not coverage["passed"]:
          continue
        window_dates = set(window)
        rows = [row for row in run_rows if _row_date(row) in window_dates]
        facts = self._candidate_facts(
          evaluations=rows,
          outcomes=[
            row
            for row in outcomes
            if _text(getattr(row, "strategy_run_id", None)) == run_id
            and self._outcome_date(row) in window_dates
          ],
          intents=[
            row
            for row in intents
            if _text(getattr(row, "strategy_run_id", None)) == run_id
          ],
          require_matured_outcome=True,
        )
        candidates.append(
          {
            "strategy_run_id": run_id,
            "run_status": coverage["run_status"],
            "run_started_at": coverage["run_started_at"],
            "run_stopped_at": coverage["run_stopped_at"],
            "trading_dates": [item.isoformat() for item in window],
            "intraday_coverage": coverage,
            "matured_count": facts.matured_count,
            "duplicate_count": facts.duplicate_count,
            "ghost_count": facts.ghost_count,
            "future_data_violation_count": facts.future_data_violation_count,
            "trace_broken_count": facts.trace_broken_count,
            "duplicate_intent_count": facts.duplicate_intent_count,
            "candidate_count": facts.candidate_count,
            "quality_evidence_available": bool(facts.candidate_count),
            "primary_blocker": facts.primary_blocker,
            "policy_versions": list(facts.policy_versions),
          }
        )
    if candidates:
      selected = max(
        candidates,
        key=lambda item: (
          item["duplicate_count"] == 0
          and item["ghost_count"] == 0
          and item["future_data_violation_count"] == 0
          and item["trace_broken_count"] == 0,
          int(item["matured_count"]),
          item["trading_dates"],
        ),
      )
      consecutive_days_ready = not evidence_truncated
      return {
        **selected,
        "consecutive_days_ready": consecutive_days_ready,
        "evidence_truncated": bool(evidence_truncated),
        "consecutive_days_message": (
          ""
          if consecutive_days_ready
          else "PAPER 运行或评估证据超过有界查询上限，拒绝以截断数据放行"
        ),
      }
    if evidence_truncated:
      reason = "PAPER 运行或评估证据超过有界查询上限，拒绝以截断数据放行"
    elif calendar is None:
      reason = "PAPER 交易日历不可用，拒绝以自然日替代交易日连续性"
    elif coverage_attempts:
      reason = "PAPER 尚无单一持久运行完整覆盖连续 5 个交易日的上午和下午交易时段"
    else:
      reason = "PAPER 尚未由单一持久运行覆盖至少 5 个连续交易日"
    facts = self._candidate_facts(
      evaluations=v3_evaluations,
      outcomes=outcomes,
      intents=intents,
      require_matured_outcome=True,
    )
    best_coverage = (
      max(
        coverage_attempts,
        key=lambda item: (
          bool(item["lifecycle_ready"]),
          int(item["covered_session_count"]),
          item["trading_dates"],
          item["strategy_run_id"],
        ),
      )
      if coverage_attempts
      else {}
    )
    return {
      "strategy_run_id": _text(best_coverage.get("strategy_run_id")),
      "run_status": _text(best_coverage.get("run_status")),
      "run_started_at": _text(best_coverage.get("run_started_at")),
      "run_stopped_at": _text(best_coverage.get("run_stopped_at")),
      "trading_dates": sorted(item.isoformat() for item in observed_dates),
      "intraday_coverage": best_coverage,
      "consecutive_days_ready": False,
      "consecutive_days_message": reason,
      "matured_count": facts.matured_count,
      "duplicate_count": facts.duplicate_count,
      "ghost_count": facts.ghost_count,
      "future_data_violation_count": facts.future_data_violation_count,
      "trace_broken_count": facts.trace_broken_count,
      "duplicate_intent_count": facts.duplicate_intent_count,
      "candidate_count": facts.candidate_count,
      "quality_evidence_available": bool(facts.candidate_count),
      "primary_blocker": facts.primary_blocker,
      "policy_versions": list(facts.policy_versions),
      "evidence_truncated": bool(evidence_truncated),
    }

  def _paper_window_coverage(
    self,
    *,
    run: Any,
    evaluations: Sequence[Any],
    trading_dates: Sequence[date],
  ) -> dict[str, Any]:
    run_id = _text(getattr(run, "id", None))
    run_status = _mode(getattr(run, "status", None))
    run_started_at = self._local_naive(getattr(run, "start_time", None))
    run_stopped_at = self._local_naive(getattr(run, "stop_time", None))
    first_open = datetime.combine(trading_dates[0], PAPER_TRADING_SESSIONS[0][0])
    last_close = datetime.combine(trading_dates[-1], PAPER_TRADING_SESSIONS[-1][1])
    terminal_status = run_status in {"COMPLETED", "STOPPED"}
    lifecycle_ready = bool(
      run_started_at
      and run_started_at <= first_open + PAPER_SESSION_EDGE_TOLERANCE
      and run_status in {"RUNNING", "COMPLETED", "STOPPED"}
      and not _text(getattr(run, "error_message", None))
      and (
        (not terminal_status and run_stopped_at is None)
        or (
          terminal_status
          and run_stopped_at is not None
          and run_stopped_at >= last_close - PAPER_SESSION_EDGE_TOLERANCE
        )
      )
    )
    daily: list[dict[str, Any]] = []
    covered_session_count = 0
    for trading_date in trading_dates:
      intervals = [
        interval
        for row in evaluations
        if _row_date(row) == trading_date
        for interval in [self._paper_evidence_interval(row)]
        if interval is not None
      ]
      sessions: list[dict[str, Any]] = []
      for session_start, session_end in PAPER_TRADING_SESSIONS:
        summary = self._paper_session_coverage(
          intervals=intervals,
          started_at=datetime.combine(trading_date, session_start),
          ended_at=datetime.combine(trading_date, session_end),
        )
        sessions.append(summary)
        if summary["covered"]:
          covered_session_count += 1
      daily.append(
        {
          "trading_date": trading_date.isoformat(),
          "covered": all(item["covered"] for item in sessions),
          "sessions": sessions,
        }
      )
    required_session_count = len(trading_dates) * len(PAPER_TRADING_SESSIONS)
    return {
      "strategy_run_id": run_id,
      "run_status": run_status,
      "run_started_at": _datetime_text(run_started_at),
      "run_stopped_at": _datetime_text(run_stopped_at),
      "trading_dates": [item.isoformat() for item in trading_dates],
      "lifecycle_ready": lifecycle_ready,
      "covered_session_count": covered_session_count,
      "required_session_count": required_session_count,
      "passed": lifecycle_ready and covered_session_count == required_session_count,
      "days": daily,
    }

  @staticmethod
  def _paper_session_coverage(
    *,
    intervals: Sequence[tuple[datetime, datetime]],
    started_at: datetime,
    ended_at: datetime,
  ) -> dict[str, Any]:
    relevant = sorted(
      (
        (max(started_at, interval_start), min(ended_at, interval_end))
        for interval_start, interval_end in intervals
        if interval_end >= started_at and interval_start <= ended_at
      ),
      key=lambda item: (item[0], item[1]),
    )
    if not relevant:
      return {
        "started_at": _datetime_text(started_at),
        "ended_at": _datetime_text(ended_at),
        "covered": False,
        "evidence_interval_count": 0,
        "max_gap_seconds": int((ended_at - started_at).total_seconds()),
      }
    cursor = started_at
    max_gap = timedelta(0)
    for interval_start, interval_end in relevant:
      if interval_start > cursor:
        max_gap = max(max_gap, interval_start - cursor)
      cursor = max(cursor, interval_end)
    if cursor < ended_at:
      max_gap = max(max_gap, ended_at - cursor)
    covered = bool(
      relevant[0][0] <= started_at + PAPER_SESSION_EDGE_TOLERANCE
      and max(item[1] for item in relevant) >= ended_at - PAPER_SESSION_EDGE_TOLERANCE
      and max_gap <= PAPER_MAX_EVIDENCE_GAP
    )
    return {
      "started_at": _datetime_text(started_at),
      "ended_at": _datetime_text(ended_at),
      "covered": covered,
      "evidence_interval_count": len(relevant),
      "max_gap_seconds": int(max_gap.total_seconds()),
    }

  def _paper_evidence_interval(
    self,
    row: Any,
  ) -> tuple[datetime, datetime] | None:
    persisted_at = self._local_naive(getattr(row, "evaluated_at", None))
    source_at = self._source_time(_snapshot(row).get("source_time_ms"))
    if persisted_at is None or source_at is None or source_at > persisted_at:
      return None
    if _text(getattr(row, "record_kind", None)).upper() == "COALESCED_DIAGNOSTIC":
      window_started_at = self._local_naive(getattr(row, "window_started_at", None))
      window_ended_at = self._local_naive(getattr(row, "window_ended_at", None))
      if (
        window_started_at is None
        or window_ended_at is None
        or window_started_at > window_ended_at
        or window_ended_at > persisted_at
      ):
        return None
      return window_started_at, window_ended_at
    return source_at, source_at

  @staticmethod
  def _source_time(value: Any) -> datetime | None:
    source_time_ms = _positive_integer(value)
    if source_time_ms is None:
      return None
    try:
      return (
        datetime.fromtimestamp(source_time_ms / 1_000, timezone.utc)
        .astimezone(_SHANGHAI)
        .replace(tzinfo=None)
      )
    except (OSError, OverflowError, ValueError):
      return None

  @staticmethod
  def _local_naive(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
      return None
    if value.tzinfo is None:
      return value
    return value.astimezone(_SHANGHAI).replace(tzinfo=None)

  @staticmethod
  def _consecutive_windows(
    observed_dates: set[date],
    calendar: Sequence[date] | None,
  ) -> list[tuple[date, ...]]:
    if calendar is None:
      return []
    days = list(calendar)
    windows: list[tuple[date, ...]] = []
    for index in range(max(0, len(days) - MIN_PAPER_TRADING_DAYS + 1)):
      window = tuple(days[index : index + MIN_PAPER_TRADING_DAYS])
      if len(window) == MIN_PAPER_TRADING_DAYS and set(window) <= observed_dates:
        windows.append(window)
    return windows

  @staticmethod
  def _outcome_date(row: Any) -> date | None:
    value = getattr(row, "candidate_at", None)
    return value.date() if isinstance(value, datetime) else None

  def _candidate_facts(
    self,
    *,
    evaluations: Sequence[Any],
    outcomes: Sequence[Any],
    intents: Sequence[Any],
    require_matured_outcome: bool,
  ) -> _CandidateFacts:
    material_rows = [
      row
      for row in evaluations
      if _v3_evaluation(row)
      and _text(getattr(row, "record_kind", None)).upper()
      == T_TRADE_EVALUATION_KIND_MATERIAL
      and _text(getattr(row, "candidate_id", None))
    ]
    rows_by_candidate: dict[tuple[str, str], list[Any]] = defaultdict(list)
    malformed_candidate_rows = 0
    for row in material_rows:
      key = _candidate_key(row)
      if key is None:
        malformed_candidate_rows += 1
      else:
        rows_by_candidate[key].append(row)
    outcome_by_candidate = {
      (
        _text(getattr(row, "strategy_run_id", None)),
        _text(getattr(row, "candidate_id", None)),
      ): row
      for row in outcomes
      if _text(getattr(row, "strategy_run_id", None))
      and _text(getattr(row, "candidate_id", None))
    }
    intents_by_candidate: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
      list
    )
    for row in intents:
      metadata = _mapping(getattr(row, "intent_metadata", None))
      key = (
        _text(getattr(row, "strategy_run_id", None)),
        _text(metadata.get("candidate_id")),
      )
      if key[0] and key[1]:
        intents_by_candidate[key].append(
          {
            "intent_id": _text(getattr(row, "id", None)),
            "candidate_fingerprint": _text(metadata.get("candidate_fingerprint")),
          }
        )

    episode_candidates: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    missing_episode_count = 0
    future_candidates: set[tuple[str, str]] = set()
    trace_broken_candidates: set[tuple[str, str]] = set()
    blocker_counts: Counter[str] = Counter()
    policy_versions: set[str] = set()
    duplicate_intent_candidates = {
      key for key, rows in intents_by_candidate.items() if len(rows) > 1
    }
    for key, rows in rows_by_candidate.items():
      snapshots = [_snapshot(row) for row in rows]
      latest = snapshots[-1]
      policy = _text(latest.get("policy_version"))
      if policy:
        policy_versions.add(policy)
      for snapshot in snapshots:
        for blocker in self._blocker_codes(snapshot):
          blocker_counts[blocker] += 1
        episode = _text(snapshot.get("episode_id"))
        instrument = _text(snapshot.get("instrument_code")) or _text(
          getattr(rows[0], "instrument_code", None)
        )
        if not episode or not instrument:
          missing_episode_count += 1
        else:
          episode_candidates[(key[0], instrument.upper(), episode)].add(key[1])
        if self._future_data_violation(snapshot):
          future_candidates.add(key)
      outcome = outcome_by_candidate.get(key)
      candidate_intents = intents_by_candidate.get(key, [])
      if (
        not self._trace_complete(
          key=key,
          snapshots=snapshots,
          outcome=outcome,
          intent=candidate_intents[0] if len(candidate_intents) == 1 else None,
          require_matured_outcome=require_matured_outcome,
        )
        or key in duplicate_intent_candidates
      ):
        trace_broken_candidates.add(key)
    outcome_keys = set(outcome_by_candidate)
    candidate_keys = set(rows_by_candidate)
    ghost_count = len(candidate_keys ^ outcome_keys) + malformed_candidate_rows
    for key in candidate_keys & outcome_keys:
      row = outcome_by_candidate[key]
      snapshot = _snapshot(rows_by_candidate[key][0])
      if (
        _text(getattr(row, "candidate_fingerprint", None))
        != _text(snapshot.get("candidate_fingerprint"))
        or _text(getattr(row, "instrument_code", None)).upper()
        != _text(snapshot.get("instrument_code")).upper()
        or _text(getattr(row, "policy_version", None))
        != _text(snapshot.get("policy_version"))
      ):
        ghost_count += 1
    duplicate_count = missing_episode_count + sum(
      max(0, len(candidate_ids) - 1) for candidate_ids in episode_candidates.values()
    )
    duplicate_intent_count = sum(
      max(0, len(rows) - 1)
      for key, rows in intents_by_candidate.items()
      if key in candidate_keys
    )
    matured_count = sum(
      _text(getattr(outcome_by_candidate[key], "status", None)).upper() == "MATURED"
      for key in candidate_keys
      if key in outcome_by_candidate
    )
    primary_blocker = ""
    if blocker_counts:
      primary_blocker = min(
        blocker_counts,
        key=lambda code: (-blocker_counts[code], code),
      )
    return _CandidateFacts(
      candidate_keys=frozenset(candidate_keys),
      candidate_count=len(candidate_keys),
      duplicate_count=duplicate_count,
      ghost_count=ghost_count,
      future_data_violation_count=len(future_candidates),
      trace_broken_count=len(trace_broken_candidates) + malformed_candidate_rows,
      duplicate_intent_count=duplicate_intent_count,
      matured_count=matured_count,
      primary_blocker=primary_blocker,
      policy_versions=tuple(sorted(policy_versions)),
    )

  @staticmethod
  def _blocker_codes(snapshot: Mapping[str, Any]) -> Iterable[str]:
    for item in list(snapshot.get("top_blockers") or snapshot.get("blockers") or []):
      if isinstance(item, Mapping):
        code = _text(item.get("code"))
      else:
        code = _text(item)
      if code:
        yield code

  @staticmethod
  def _future_data_violation(snapshot: Mapping[str, Any]) -> bool:
    source_time = _positive_integer(snapshot.get("source_time_ms"))
    evaluated_at = _positive_integer(snapshot.get("evaluated_at_ms"))
    if source_time is None or evaluated_at is None or source_time > evaluated_at:
      return True
    gates = {
      _text(item.get("code")): bool(item.get("passed"))
      for item in list(snapshot.get("hard_gates") or [])
      if isinstance(item, Mapping) and _text(item.get("code"))
    }
    return gates.get("REFERENCE_PROFILE_CAUSAL") is not True

  @staticmethod
  def _trace_complete(
    *,
    key: tuple[str, str],
    snapshots: Sequence[Mapping[str, Any]],
    outcome: Any,
    intent: Mapping[str, Any] | None,
    require_matured_outcome: bool,
  ) -> bool:
    if outcome is None:
      return False
    if (
      require_matured_outcome
      and _text(getattr(outcome, "status", None)).upper() != "MATURED"
    ):
      return False
    latest = dict(snapshots[-1]) if snapshots else {}
    required_text = (
      "candidate_fingerprint",
      "continuity_generation",
      "policy_version",
      "feature_schema_version",
    )
    if any(not _text(latest.get(field)) for field in required_text):
      return False
    if (
      _positive_integer(latest.get("source_time_ms")) is None
      or _positive_integer(latest.get("tick_ordinal")) is None
      or not _text(
        latest.get("profile_version") or latest.get("reference_profile_version")
      )
      or not isinstance(latest.get("score_contributions"), list)
      or not isinstance(latest.get("hard_gates"), list)
      or not latest.get("hard_gates")
    ):
      return False
    expected_intent_id = ""
    for snapshot in snapshots:
      expected_intent_id = (
        _text(snapshot.get("pending_entry_intent_id")) or expected_intent_id
      )
    if expected_intent_id:
      return bool(
        intent
        and _text(intent.get("intent_id")) == expected_intent_id
        and _text(intent.get("candidate_fingerprint"))
        == _text(latest.get("candidate_fingerprint"))
      )
    # A candidate suppressed by a durable hard gate has a traceable absence of
    # TradeIntent.  Treating that explicit non-entry as a broken lineage would
    # incorrectly force PAPER to manufacture orders solely to pass a gate.
    return bool(key[0] and key[1])

  @staticmethod
  def _after_fee_summary(replay_metrics: Mapping[str, Any]) -> dict[str, float]:
    summary = _mapping(replay_metrics.get("summary"))
    return {
      "t_net_profit": _number(summary.get("t_net_profit")),
      "total_fees": _number(summary.get("total_fees")),
      "excess_return_pct": _number(summary.get("excess_return_pct")),
    }

  @staticmethod
  def _canary_limits_summary(
    *,
    rollout: Any,
    global_config: Any,
    live_run: Any,
  ) -> dict[str, Any]:
    if rollout is None:
      return {"passed": False, "message": "账户灰度限制配置不存在"}
    if global_config is None:
      return {"passed": False, "message": "做 T 全局配置不存在，无法验证有限标的"}
    settings = _mapping(getattr(global_config, "settings", None))
    instruments = list(getattr(live_run, "instruments", None) or [])
    passed = (
      _integer(getattr(rollout, "max_active_batches", None), 0) == 1
      and _number(getattr(rollout, "max_batch_volume", None), 0.0) > 0.0
      and _number(getattr(rollout, "max_order_amount", None), 0.0) > 0.0
      and 0.0 < _number(getattr(rollout, "max_total_exposure_pct", None), 0.0) <= 0.02
      and _text(getattr(global_config, "mode", None)).lower() == "live"
      and bool(getattr(global_config, "auto_exit_acknowledged", False))
      and live_run is not None
      and _mode(getattr(live_run, "mode", None)) == "LIVE"
      and len(instruments) == 1
      and _integer(settings.get("max_concurrent_batches"), 0) == 1
      and 0.0 < _number(settings.get("max_total_t_exposure_pct"), 0.0) <= 0.02
      and 0.0
      < _number(settings.get("max_trade_amount"), 0.0)
      <= _number(getattr(rollout, "max_order_amount", None), 0.0)
    )
    return {
      "passed": passed,
      "message": ""
      if passed
      else "CANARY 必须预先配置单标的、单批次和不超过 2% 总敞口",
      "instrument_count": len(instruments),
      "instruments": sorted(_text(item).upper() for item in instruments if _text(item)),
      "max_active_batches": _integer(getattr(rollout, "max_active_batches", None)),
      "max_total_exposure_pct": _number(
        getattr(rollout, "max_total_exposure_pct", None)
      ),
    }

  @staticmethod
  def _operator_review_inputs(
    *,
    global_config: Any,
    live_run: Any,
    replay: Mapping[str, Any],
    paper: Mapping[str, Any],
  ) -> dict[str, Any]:
    """Build the exact §19.5 review payload from durable state.

    This payload is included in the evidence fingerprint that the native,
    device-bound control challenge signs.  It deliberately contains the four
    operator-review subjects required by the specification rather than a
    generic "activate" acknowledgement.
    """

    settings = _mapping(getattr(global_config, "settings", None))
    run_parameters = _mapping(getattr(live_run, "parameters", None))
    frequency_sources = {
      **{
        str(key): value
        for key, value in settings.items()
        if any(
          token in str(key).lower()
          for token in ("interval", "cadence", "frequency", "session")
        )
      },
      **{
        str(key): value
        for key, value in run_parameters.items()
        if any(
          token in str(key).lower()
          for token in ("interval", "cadence", "frequency", "session")
        )
      },
    }
    return {
      "thresholds": _mapping(
        run_parameters.get("signal_policy") or settings.get("signal_policy")
      ),
      "frequency": {key: frequency_sources[key] for key in sorted(frequency_sources)},
      "primary_blocker": _text(paper.get("primary_blocker"))
      or _text(replay.get("primary_blocker")),
      "after_fee": _mapping(replay.get("after_fee")),
    }

  @staticmethod
  def _operator_review_summary(
    *,
    rollout: Any,
    review_inputs: Mapping[str, Any],
    review_evidence_fingerprint: str,
    review_events: Sequence[Any],
    review_events_truncated: bool,
  ) -> dict[str, Any]:
    """Find an explicit, append-only acknowledgement of current evidence.

    The acknowledgement is written by the existing authenticated, idempotent
    activation route (including its device-bound two-phase variant), atomically
    with its activation event.  A historical activation click, a copied policy
    number, or a stale evidence fingerprint can therefore never satisfy this
    gate.
    """

    policy_version = _integer(getattr(rollout, "policy_version", None))
    snapshot_id = _text(getattr(rollout, "last_snapshot_id", None))
    base = {
      "confirmed": False,
      "message": (
        "缺少绑定当前阈值、频率、主阻断项与费后结果的 V3 审阅确认；"
        "请重新审阅后使用受鉴权且带幂等键的启用确认"
      ),
      "policy_version": policy_version,
      "snapshot_id": snapshot_id,
      "review_evidence_fingerprint": review_evidence_fingerprint,
      "review_inputs": dict(review_inputs),
    }
    if rollout is None or not snapshot_id:
      return base
    if review_events_truncated:
      base["message"] = "V3 审阅确认审计事件超出有界查询上限，拒绝以截断数据放行"
      return base
    for event in review_events:
      if _text(getattr(event, "event_type", None)) not in {
        "CANARY_ACTIVATED",
        "LIVE_ACTIVATED",
      }:
        continue
      details = _mapping(getattr(event, "details", None))
      acknowledgement = _mapping(details.get("operatorReview"))
      if (
        acknowledgement.get("acknowledged") is True
        and _text(acknowledgement.get("confirmation"))
        == V3_OPERATOR_REVIEW_CONFIRMATION
        and _text(acknowledgement.get("evidenceFingerprint"))
        == review_evidence_fingerprint
        and _integer(acknowledgement.get("policyVersion")) == policy_version
        and _text(acknowledgement.get("snapshotId")) == snapshot_id
        and _text(getattr(event, "snapshot_id", None)) == snapshot_id
        and _text(acknowledgement.get("operationId"))
        and _text(acknowledgement.get("operationId"))
        == _text(details.get("operationId"))
      ):
        return {
          **base,
          "confirmed": True,
          "message": "",
          "event_id": _text(getattr(event, "event_id", None)),
          "confirmed_by_user_id": _text(getattr(event, "actor_user_id", None)),
        }
    return base

  def _unavailable(self, *, account_id: str, exc: Exception) -> dict[str, Any]:
    summary = {
      "schema_version": V3_ROLLOUT_EVIDENCE_SCHEMA_VERSION,
      "account_id": account_id,
      "query_error": type(exc).__name__,
      "replay": {},
      "paper": {},
      "canary": {},
      "operator_review": {
        "confirmed": False,
        "message": "V3 上线证据查询失败，无法验证审阅确认",
      },
    }
    summary["fingerprint"] = _canonical_fingerprint(summary)
    checks = [
      self._check(
        "V3_EVIDENCE_QUERY_AVAILABLE",
        False,
        "V3 上线证据查询失败，已拒绝执行阶段激活",
      )
    ]
    checks.extend(
      self._check(
        code,
        False,
        "V3 上线证据不可用，已拒绝执行阶段激活",
      )
      for code in sorted(V3_ROLLOUT_GATE_CODES - {"V3_EVIDENCE_QUERY_AVAILABLE"})
    )
    return {"checks": checks, "summary": summary}


__all__ = [
  "MAX_EVIDENCE_ROWS",
  "MIN_PAPER_COMPLETED_CANDIDATES",
  "MIN_PAPER_TRADING_DAYS",
  "MIN_REPLAY_TRADING_DAYS",
  "TTradeV3RolloutEvidenceEvaluator",
  "V3_OPERATOR_REVIEW_CONFIRMATION",
  "V3_ROLLOUT_GATE_CODES",
  "V3_ROLLOUT_EVIDENCE_SCHEMA_VERSION",
]
