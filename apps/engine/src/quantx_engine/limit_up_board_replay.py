"""Deterministic account-level historical replay for the board assistant."""

from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Sequence
from zoneinfo import ZoneInfo

from quantx_domain.market import Tick
from quantx_domain.strategies.base import StrategyRunMode
from quantx_domain.trading.limit_up_board_replay import (
  LimitUpBoardReplayScenarioSpec,
  get_limit_up_board_replay_scenarios,
)
from quantx_domain.trading.limit_up_board_universe import (
  select_limit_up_board_universe,
)
from quantx_domain.trading.market_rules import MarketDataSnapshot

from .replay_clock import ReplayClock

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class ReplayEventPriority(IntEnum):
  """Stable ordering for events with the same exchange timestamp.

  A market Tick is intentionally consumed before a universe snapshot observed
  at that same timestamp.  That prevents a radar observation derived from a
  Tick from giving the strategy a chance to trade on that very Tick.  Manual
  approval is last, after every quote and universe update at its due time.
  """

  MARKET_TICK = 10
  UNIVERSE_SNAPSHOT = 20
  APPROVAL_DUE = 30


@dataclass(frozen=True)
class ReplayDelayScenario:
  scenario_id: str
  label: str
  confirmation_delay_ms: int
  participation_cap_pct: float
  book_depth_participation_pct: float
  is_theoretical_upper_bound: bool = False

  @classmethod
  def from_spec(cls, spec: LimitUpBoardReplayScenarioSpec) -> "ReplayDelayScenario":
    return cls(**spec.to_dict())

  @classmethod
  def from_runtime_parameters(
    cls,
    parameters: Mapping[str, Any],
  ) -> "ReplayDelayScenario":
    scenario_id = str(
      parameters.get("limit_up_board_replay_scenario_id") or "BASE"
    ).upper()
    specs = {
      spec.scenario_id: spec for spec in get_limit_up_board_replay_scenarios()
    }
    if scenario_id not in specs:
      raise ValueError(f"Unsupported board replay scenario: {scenario_id}")
    scenario = cls.from_spec(specs[scenario_id])
    raw_delay = parameters.get(
      "limit_up_board_replay_confirmation_delay_ms",
      parameters.get("board_replay_confirmation_delay_ms"),
    )
    if raw_delay is not None and int(raw_delay) != scenario.confirmation_delay_ms:
      raise ValueError(
        "Board replay confirmation delay does not match the versioned scenario"
      )
    return scenario


@dataclass(frozen=True)
class UniverseSnapshotEvent:
  timestamp: datetime
  instruments: tuple[str, ...]
  instrument_metadata: Dict[str, Dict[str, Any]]
  snapshot_id: str = ""
  candidate_count: int = 0
  qualified_count: int = 0
  candidates: tuple[Dict[str, Any], ...] = ()

  @classmethod
  def from_record(cls, record: Mapping[str, Any]) -> "UniverseSnapshotEvent":
    timestamp = _parse_timestamp(
      record.get("timestamp") or record.get("observed_at")
    )
    instruments = tuple(
      _unique_codes(record.get("instruments") or [])
    )
    metadata = {
      str(code or "").strip().upper(): dict(value or {})
      for code, value in dict(record.get("instrument_metadata") or {}).items()
      if str(code or "").strip()
    }
    candidates = tuple(
      dict(item or {}) for item in list(record.get("candidates") or [])
    )
    return cls(
      timestamp=timestamp,
      instruments=instruments,
      instrument_metadata=metadata,
      snapshot_id=str(record.get("snapshot_id") or ""),
      candidate_count=max(
        0,
        int(record.get("candidate_count", len(candidates)) or 0),
      ),
      qualified_count=max(0, int(record.get("qualified_count", 0) or 0)),
      candidates=candidates,
    )

  def project(
    self,
    *,
    settings: Mapping[str, Any],
    sticky_codes: Iterable[str],
  ) -> "UniverseSnapshotEvent":
    if not self.candidates:
      return self
    selection = select_limit_up_board_universe(
      self.candidates,
      settings=settings,
      enabled=True,
      sticky_codes=tuple(sticky_codes),
    )
    return UniverseSnapshotEvent(
      timestamp=self.timestamp,
      instruments=selection.instruments,
      instrument_metadata=selection.metadata,
      snapshot_id=self.snapshot_id,
      candidate_count=self.candidate_count or len(self.candidates),
      qualified_count=selection.qualified_count,
      candidates=self.candidates,
    )


@dataclass(order=True)
class _QueuedReplayEvent:
  timestamp: datetime
  priority: int
  stable_key: str
  sequence: int
  kind: str = field(compare=False)
  payload: Any = field(compare=False)


@dataclass(frozen=True)
class _ApprovalDue:
  intent_id: str
  expires_at_ms: int


@dataclass(frozen=True)
class LimitUpBoardReplayResult:
  scenario_id: str
  started_at: Optional[datetime]
  processed_until: Optional[datetime]
  processed_ticks: int
  processed_universe_snapshots: int
  entry_intents: int
  approval_due: int
  approval_approved: int
  approval_rejected: int
  pending_approvals: int
  open_positions: Dict[str, Dict[str, Any]]
  open_orders: int
  active_exit_plans: int
  constraint_statistics: Dict[str, Any]
  event_trace: tuple[Dict[str, Any], ...] = ()

  def to_dict(self) -> Dict[str, Any]:
    value = asdict(self)
    value["started_at"] = self.started_at.isoformat() if self.started_at else None
    value["processed_until"] = (
      self.processed_until.isoformat() if self.processed_until else None
    )
    value["event_trace"] = list(self.event_trace)
    return value


class LimitUpBoardReplayExecutionPort(Protocol):
  async def advance_replay_time(self, runtime: Any, timestamp: datetime) -> None: ...

  async def process_replay_tick(self, runtime: Any, tick: Tick) -> None: ...

  async def reconcile_replay_universe(
    self,
    runtime: Any,
    instruments: list[str],
    instrument_metadata: Dict[str, Dict[str, Any]],
  ) -> Dict[str, list[str]]: ...

  async def approve_replay_intent(
    self,
    runtime: Any,
    intent_id: str,
  ) -> Dict[str, Any]: ...

  async def reject_replay_intent(
    self,
    runtime: Any,
    intent_id: str,
    reason: str,
  ) -> Dict[str, Any]: ...

  async def cancel_replay_open_buy_orders(
    self,
    runtime: Any,
    reason: str,
  ) -> int: ...

  async def wait_replay_reports(self, runtime: Any) -> None: ...

  def replay_sticky_instruments(self, runtime: Any) -> set[str]: ...

  async def report_replay_progress(
    self,
    runtime: Any,
    processed_until: datetime,
  ) -> None: ...


class LimitUpBoardReplayRunner:
  """Replay one immutable account/scenario timeline through StrategyExecutor."""

  def __init__(
    self,
    execution: LimitUpBoardReplayExecutionPort,
    scenario: ReplayDelayScenario,
    *,
    selection_settings: Optional[Mapping[str, Any]] = None,
    trace_limit: int = 200,
  ) -> None:
    self.execution = execution
    self.scenario = scenario
    self.selection_settings = (
      dict(selection_settings) if selection_settings is not None else None
    )
    self.trace_limit = max(0, int(trace_limit))

  async def run(
    self,
    runtime: Any,
    *,
    universe_events: Iterable[UniverseSnapshotEvent | Mapping[str, Any]],
    ticks: Iterable[Tick | MarketDataSnapshot | Mapping[str, Any]],
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
  ) -> LimitUpBoardReplayResult:
    context = runtime.context
    if context.mode != StrategyRunMode.BACKTEST:
      raise ValueError("Board replay requires BACKTEST mode")
    if not context.parameters.get("limit_up_board_replay"):
      raise ValueError("Board replay runtime flag is missing")

    start = _normalize_timestamp(
      start_time or context.backtest_start_time
    ) if (start_time or context.backtest_start_time) else None
    end = _normalize_timestamp(
      end_time or context.backtest_end_time
    ) if (end_time or context.backtest_end_time) else None
    if start and end and end < start:
      raise ValueError("Board replay end_time must not precede start_time")

    queue: list[_QueuedReplayEvent] = []
    sequence = 0
    for raw_tick in ticks:
      tick_ordinal = _tick_order_value(raw_tick, "tick_ordinal", sequence)
      source_time_ms = _tick_order_value(raw_tick, "source_time_ms", 0)
      tick = coerce_replay_tick(raw_tick)
      timestamp = _normalize_timestamp(tick.time)
      if source_time_ms <= 0:
        source_time_ms = int(timestamp.timestamp() * 1000)
      if not _within_window(timestamp, start, end):
        continue
      tick.time = timestamp
      heapq.heappush(
        queue,
        _QueuedReplayEvent(
          timestamp,
          int(ReplayEventPriority.MARKET_TICK),
          f"{source_time_ms:020d}:{tick_ordinal:020d}:"
          f"{str(tick.stock_code or '').upper()}",
          sequence,
          "tick",
          tick,
        ),
      )
      sequence += 1
    for raw_event in universe_events:
      event = (
        raw_event
        if isinstance(raw_event, UniverseSnapshotEvent)
        else UniverseSnapshotEvent.from_record(raw_event)
      )
      timestamp = _normalize_timestamp(event.timestamp)
      if not _within_window(timestamp, start, end):
        continue
      if timestamp != event.timestamp:
        event = UniverseSnapshotEvent(
          timestamp=timestamp,
          instruments=event.instruments,
          instrument_metadata=event.instrument_metadata,
          snapshot_id=event.snapshot_id,
          candidate_count=event.candidate_count,
          qualified_count=event.qualified_count,
          candidates=event.candidates,
        )
      heapq.heappush(
        queue,
        _QueuedReplayEvent(
          timestamp,
          int(ReplayEventPriority.UNIVERSE_SNAPSHOT),
          event.snapshot_id,
          sequence,
          "universe",
          event,
        ),
      )
      sequence += 1

    first_event_at = queue[0].timestamp if queue else start
    if runtime.replay_clock is None and first_event_at is not None:
      runtime.replay_clock = ReplayClock(start or first_event_at)

    counters = context.parameters.setdefault(
      "limit_up_board_replay_counters", {}
    )
    counters.setdefault("rejection_reasons", {})
    scheduled: set[str] = set()
    event_trace: list[Dict[str, Any]] = []
    processed_ticks = 0
    processed_universe = 0
    entry_intents = 0
    approval_due = 0
    approval_approved = 0
    approval_rejected = 0
    processed_until: Optional[datetime] = None

    def trace(kind: str, timestamp: datetime, key: str = "") -> None:
      if len(event_trace) >= self.trace_limit:
        return
      event_trace.append(
        {"timestamp": timestamp.isoformat(), "kind": kind, "key": key}
      )

    def schedule_new_approvals(timestamp: datetime) -> None:
      nonlocal sequence, entry_intents
      for intent_id, intent in list(runtime.pending_approvals.items()):
        if intent_id in scheduled:
          continue
        scheduled.add(intent_id)
        entry_intents += 1
        due_at = timestamp + timedelta(
          milliseconds=self.scenario.confirmation_delay_ms
        )
        expiry = dict(intent.expiry_policy or {}).get("expire_at_ms", 0)
        try:
          expires_at_ms = int(expiry or 0)
        except (TypeError, ValueError):
          expires_at_ms = 0
        if end is not None and due_at > end:
          continue
        heapq.heappush(
          queue,
          _QueuedReplayEvent(
            due_at,
            int(ReplayEventPriority.APPROVAL_DUE),
            str(intent.instrument_code or "").upper(),
            sequence,
            "approval",
            _ApprovalDue(intent_id=intent_id, expires_at_ms=expires_at_ms),
          ),
        )
        sequence += 1

    schedule_new_approvals(first_event_at or start or end or datetime.min)
    while queue:
      event = heapq.heappop(queue)
      processed_until = event.timestamp
      if event.kind == "tick":
        tick = event.payload
        await self.execution.process_replay_tick(runtime, tick)
        await self.execution.wait_replay_reports(runtime)
        processed_ticks += 1
        trace("MARKET_TICK", event.timestamp, tick.stock_code)
      elif event.kind == "universe":
        snapshot = event.payload.project(
          settings=self.selection_settings or context.parameters,
          sticky_codes=self.execution.replay_sticky_instruments(runtime),
        )
        await self.execution.advance_replay_time(runtime, event.timestamp)
        await self.execution.reconcile_replay_universe(
          runtime,
          list(snapshot.instruments),
          dict(snapshot.instrument_metadata),
        )
        await self.execution.wait_replay_reports(runtime)
        processed_universe += 1
        counters["candidate_frames"] = int(
          counters.get("candidate_frames", 0) or 0
        ) + 1
        counters["candidate_observations"] = int(
          counters.get("candidate_observations", 0) or 0
        ) + snapshot.candidate_count
        counters["qualified_observations"] = int(
          counters.get("qualified_observations", 0) or 0
        ) + snapshot.qualified_count
        await self.execution.report_replay_progress(runtime, event.timestamp)
        trace("UNIVERSE_SNAPSHOT", event.timestamp, snapshot.snapshot_id)
      else:
        due = event.payload
        await self.execution.advance_replay_time(runtime, event.timestamp)
        approval_due += 1
        result = await self.execution.approve_replay_intent(
          runtime,
          due.intent_id,
        )
        await self.execution.wait_replay_reports(runtime)
        if (
          not result.get("success")
          and str(result.get("code") or "") == "INTENT_NOT_AWAITING_APPROVAL"
          and due.expires_at_ms > 0
          and int(event.timestamp.timestamp() * 1000) >= due.expires_at_ms
        ):
          result = {**result, "code": "APPROVAL_TTL_EXPIRED"}
        if result.get("success"):
          approval_approved += 1
        else:
          approval_rejected += 1
          code = str(result.get("code") or "APPROVAL_REJECTED")
          reasons = counters["rejection_reasons"]
          reasons[code] = int(reasons.get(code, 0) or 0) + 1
        trace("APPROVAL_DUE", event.timestamp, due.intent_id)
      schedule_new_approvals(event.timestamp)

    if end is not None and (processed_until is None or end >= processed_until):
      await self.execution.advance_replay_time(runtime, end)
      await self.execution.wait_replay_reports(runtime)
      processed_until = end
    window_end_rejections = 0
    for intent_id, intent in list(runtime.pending_approvals.items()):
      direction = str(getattr(getattr(intent, "direction", ""), "value", getattr(intent, "direction", ""))).upper()
      if direction != "BUY":
        continue
      result = await self.execution.reject_replay_intent(
        runtime,
        intent_id,
        "REPLAY_WINDOW_END",
      )
      if result.get("success"):
        window_end_rejections += 1
    cancelled_window_end_orders = await self.execution.cancel_replay_open_buy_orders(
      runtime,
      "REPLAY_WINDOW_END",
    )
    await self.execution.wait_replay_reports(runtime)
    if window_end_rejections:
      approval_rejected += window_end_rejections
      reasons = counters["rejection_reasons"]
      reasons["REPLAY_WINDOW_END"] = int(
        reasons.get("REPLAY_WINDOW_END", 0) or 0
      ) + window_end_rejections
    counters["window_end_cancelled_buy_orders"] = int(
      counters.get("window_end_cancelled_buy_orders", 0) or 0
    ) + cancelled_window_end_orders

    counters["entry_intents"] = int(counters.get("entry_intents", 0) or 0) + entry_intents
    counters["approval_due"] = int(counters.get("approval_due", 0) or 0) + approval_due
    counters["approval_rejected"] = int(
      counters.get("approval_rejected", 0) or 0
    ) + approval_rejected
    counters["approval_approved"] = int(
      counters.get("approval_approved", 0) or 0
    ) + approval_approved

    broker = getattr(runtime, "broker", None)
    positions = {
      str(code): {
        "long_volume": int(getattr(position, "long_volume", 0) or 0),
        "available_volume": int(getattr(position, "available_volume", 0) or 0),
        "today_buy_volume": int(getattr(position, "today_buy_volume", 0) or 0),
        "average_price": float(getattr(position, "long_avg_price", 0.0) or 0.0),
        "last_price": float(getattr(position, "last_price", 0.0) or 0.0),
        "market_value": float(getattr(position, "market_value", 0.0) or 0.0),
      }
      for code, position in sorted(
        dict(getattr(broker, "positions", {}) or {}).items()
      )
      if int(getattr(position, "long_volume", 0) or 0) > 0
    }
    open_statuses = {"PENDING", "SUBMITTED", "ACCEPTED", "PARTIAL_FILLED"}
    open_orders = sum(
      str(getattr(getattr(order, "status", ""), "value", getattr(order, "status", ""))).upper()
      in open_statuses
      for order in dict(getattr(broker, "orders", {}) or {}).values()
    )
    constraints = (
      dict(broker.get_constraint_statistics() or {})
      if broker is not None and hasattr(broker, "get_constraint_statistics")
      else {}
    )
    active_exit_plans = len(runtime.exit_plan_book.active_plans())
    result = LimitUpBoardReplayResult(
      scenario_id=self.scenario.scenario_id,
      started_at=first_event_at,
      processed_until=processed_until,
      processed_ticks=processed_ticks,
      processed_universe_snapshots=processed_universe,
      entry_intents=entry_intents,
      approval_due=approval_due,
      approval_approved=approval_approved,
      approval_rejected=approval_rejected,
      pending_approvals=len(runtime.pending_approvals),
      open_positions=positions,
      open_orders=open_orders,
      active_exit_plans=active_exit_plans,
      constraint_statistics=constraints,
      event_trace=tuple(event_trace),
    )
    context.parameters["limit_up_board_replay_summary"] = result.to_dict()
    return result


def load_limit_up_board_replay_dataset(path: str) -> Dict[str, Any]:
  """Load both streams through Infrastructure's strict manifest verifier."""

  from quantx_infrastructure.services.limit_up_board_replay_dataset import (
    load_replay_dataset_artifact,
  )

  return dict(load_replay_dataset_artifact(str(path or "")))


def load_limit_up_board_replay_ticks(path: str) -> Sequence[Mapping[str, Any]]:
  payload = load_limit_up_board_replay_dataset(path)
  records = payload.get("ticks")
  if not isinstance(records, list):
    raise ValueError("Verified board replay inputs must contain a ticks list")
  return records


def coerce_replay_tick(
  value: Tick | MarketDataSnapshot | Mapping[str, Any],
) -> Tick:
  if isinstance(value, Tick):
    if value.time is None:
      raise ValueError("Board replay Tick is missing time")
    return value
  if isinstance(value, MarketDataSnapshot):
    if value.timestamp is None:
      raise ValueError("Board replay market snapshot is missing timestamp")
    return Tick(
      stock_code=value.instrument_code,
      time=value.timestamp,
      last_price=float(value.price or value.close or 0.0),
      open=float(value.open or 0.0),
      high=float(value.high or value.price or 0.0),
      low=float(value.low or value.price or 0.0),
      volume=float(value.volume or 0.0),
      amount=float(value.amount or 0.0),
      price_tick=float(value.price_tick or 0.01),
      up_stop_price=float(value.limit_up or 0.0),
      down_stop_price=float(value.limit_down or 0.0),
      ask_price=list(value.ask_price or []),
      bid_price=list(value.bid_price or []),
      ask_vol=list(value.ask_vol or []),
      bid_vol=list(value.bid_vol or []),
      stock_status=0 if value.is_trading and not value.suspended else 1,
    )
  if isinstance(value, Mapping):
    raw = dict(value or {})
  else:
    raw = {
      item.name: getattr(value, item.name)
      for item in fields(Tick)
      if hasattr(value, item.name)
    }
  nested = raw.get("tick") or raw.get("payload")
  if isinstance(nested, Mapping):
    raw = {**raw, **dict(nested)}
  aliases = {
    "stock_code": raw.get("stock_code") or raw.get("instrument_code") or raw.get("code"),
    "time": raw.get("time") or raw.get("timestamp") or raw.get("observed_at"),
    "last_price": raw.get("last_price") if raw.get("last_price") is not None else raw.get("price"),
    "last_close": raw.get("last_close") if raw.get("last_close") is not None else raw.get("pre_close"),
    "up_stop_price": raw.get("up_stop_price") if raw.get("up_stop_price") is not None else raw.get("limit_up"),
    "down_stop_price": raw.get("down_stop_price") if raw.get("down_stop_price") is not None else raw.get("limit_down"),
  }
  raw.update({key: item for key, item in aliases.items() if item is not None})
  raw["time"] = _parse_timestamp(raw.get("time"))
  allowed = {item.name for item in fields(Tick)}
  tick = Tick(**{key: item for key, item in raw.items() if key in allowed})
  if not tick.stock_code or tick.time is None:
    raise ValueError("Board replay Tick requires instrument code and timestamp")
  return tick


def _tick_order_value(value: Any, key: str, default: int) -> int:
  raw = value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)
  try:
    return max(0, int(raw)) if raw is not None else max(0, int(default))
  except (TypeError, ValueError):
    return max(0, int(default))


def _unique_codes(values: Iterable[Any]) -> list[str]:
  result: list[str] = []
  for value in values:
    code = str(value or "").strip().upper()
    if code and code not in result:
      result.append(code)
  return result


def _parse_timestamp(value: Any) -> datetime:
  if isinstance(value, datetime):
    return _normalize_timestamp(value)
  if not value:
    raise ValueError("Board replay event is missing timestamp")
  try:
    return _normalize_timestamp(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
  except ValueError as exc:
    raise ValueError(f"Invalid board replay timestamp: {value!r}") from exc


def _normalize_timestamp(value: datetime) -> datetime:
  if not isinstance(value, datetime):
    raise TypeError("Board replay timestamp must be a datetime")
  if value.tzinfo is None:
    return value
  return value.astimezone(_SHANGHAI).replace(tzinfo=None)


def _within_window(
  timestamp: datetime,
  start: Optional[datetime],
  end: Optional[datetime],
) -> bool:
  return (start is None or timestamp >= start) and (end is None or timestamp <= end)


__all__ = [
  "LimitUpBoardReplayExecutionPort",
  "LimitUpBoardReplayResult",
  "LimitUpBoardReplayRunner",
  "ReplayDelayScenario",
  "ReplayEventPriority",
  "UniverseSnapshotEvent",
  "coerce_replay_tick",
  "load_limit_up_board_replay_dataset",
  "load_limit_up_board_replay_ticks",
]
