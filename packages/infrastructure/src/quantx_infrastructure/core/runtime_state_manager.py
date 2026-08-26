"""
策略运行时状态管理器

负责管理策略运行过程中的状态持久化和恢复，包括：
- 日志持久化（JSONL 文件存储）
- 资金与自定义状态持久化（StrategyRunState 表）
- 持仓持久化（StrategyRunPosition 表）
- 订单、成交和策略算法状态持久化
"""

import asyncio
import copy
import hashlib
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional

from quantx_infrastructure.core.utils import time_utils

if TYPE_CHECKING:
    from quantx_domain.strategies.base import TradeIntent

    from quantx_infrastructure.core.backtest_result_storage import (
        BacktestResultStorage,
    )

try:
    import aiofiles
except ModuleNotFoundError:  # pragma: no cover - lightweight test environments
    aiofiles = None


BUCKET_LEDGER_CUSTOM_STATE_KEY = "bucket_ledger_snapshot"
APPLIED_CORPORATE_ACTIONS_KEY = "applied_corporate_actions"
GRID_BOOK_CUSTOM_STATE_KEY = "grid_book_snapshot"
ORDER_CASH_RESERVATIONS_KEY = "order_cash_reservations"
ORDER_POSITION_RESERVATIONS_KEY = "order_position_reservations"
APPLIED_RUNTIME_EVENT_KEYS = "applied_runtime_event_keys"
RUNTIME_SNAPSHOT_ATTEMPT_KEY = "runtime_snapshot_attempt"
RUNTIME_CHECKPOINTS_KEY = "runtime_checkpoints"
RUNTIME_RECONCILIATION_STATUS_KEY = "runtime_reconciliation_status"
RUNTIME_RECONCILIATION_REASON_KEY = "runtime_reconciliation_reason"
BUCKET_LEDGER_RECONCILE_REQUIRED_KEY = "bucket_ledger_reconcile_required"
BUCKET_LEDGER_VIOLATIONS_KEY = "bucket_ledger_violations"
MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY = (
    "market_continuity_reconcile_required"
)
T_TRADE_MATERIAL_EVENT_OUTBOX_KEY = "t_trade_material_event_outbox"
T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY = "t_trade_diagnostic_event_outbox"
T_TRADE_PAPER_FILL_OUTBOX_KEY = "t_trade_paper_fill_outbox"
_T_TRADE_OPPORTUNITY_EVALUATION_EVENT = "T_TRADE_OPPORTUNITY_EVALUATION"
_PREPARED_DIAGNOSTIC_OUTBOX_MANIFEST_KEY = "materialization_outbox_manifest"
_MAX_APPLIED_RUNTIME_EVENT_KEYS = 2_000
# A single personal-account BACKTEST day can contain roughly 4,800 exact
# non-coalescible MATERIAL evaluations in the fixed 9,600-Tick workload.
# Keep a bounded capacity above that one-day recovery unit; crossing it remains
# fail-closed rather than silently losing an audit event.
_MAX_T_TRADE_MATERIAL_OUTBOX_EVENTS = 8_192
_MAX_T_TRADE_DIAGNOSTIC_OUTBOX_EVENTS = 8_192
_MAX_T_TRADE_PAPER_FILL_OUTBOX_FACTS = 128
_MAX_TERMINAL_TRADE_INTENT_CACHE_ENTRIES = 512
_DECISION_TRACE_SUPPLEMENTAL_FORMAT = "DECISION_TRACE_SUPPLEMENTAL_V1"
_TERMINAL_TRADE_INTENT_STATUSES = frozenset(
    {
        "CANCELED",
        "CANCELLED",
        "EXPIRED",
        "FAILED",
        "FILLED",
        "RECONCILED_ZERO_FILL",
        "REJECTED",
        "SUPPRESSED",
    }
)
_MANAGER_OWNED_CUSTOM_STATE_KEYS = frozenset(
    {
        APPLIED_CORPORATE_ACTIONS_KEY,
        APPLIED_RUNTIME_EVENT_KEYS,
        BUCKET_LEDGER_CUSTOM_STATE_KEY,
        BUCKET_LEDGER_RECONCILE_REQUIRED_KEY,
        BUCKET_LEDGER_VIOLATIONS_KEY,
        MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY,
        ORDER_CASH_RESERVATIONS_KEY,
        ORDER_POSITION_RESERVATIONS_KEY,
        RUNTIME_RECONCILIATION_REASON_KEY,
        RUNTIME_RECONCILIATION_STATUS_KEY,
        RUNTIME_CHECKPOINTS_KEY,
        RUNTIME_SNAPSHOT_ATTEMPT_KEY,
        T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY,
        T_TRADE_MATERIAL_EVENT_OUTBOX_KEY,
        T_TRADE_PAPER_FILL_OUTBOX_KEY,
    }
)
# Strategy snapshots must never erase state that is independently owned by the
# runtime coordinator.  ``grid_book_snapshot`` is deliberately kept outside
# the general manager-owned set because an explicit durable callback is allowed
# to update it; a passive strategy-state capture, however, must preserve the
# manager's current grid-book authority.
_STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS = (
    _MANAGER_OWNED_CUSTOM_STATE_KEYS
    | {
        GRID_BOOK_CUSTOM_STATE_KEY,
        # The executor owns this book and writes it through ``set_custom``.
        # It is absent from an ordinary StrategyBase state snapshot.
        "auto_exit_plan_book",
    }
)


class RuntimeStateRestoreStatus(str, Enum):
    """Outcome of a completed runtime-state restore attempt."""

    RESTORED = "RESTORED"
    NOT_FOUND = "NOT_FOUND"
    PERSISTENCE_DISABLED = "PERSISTENCE_DISABLED"


@dataclass(frozen=True)
class RuntimeStateRestoreResult:
    """A successful restore query, including the intentional no-row case."""

    status: RuntimeStateRestoreStatus
    state: Dict[str, Any]


@dataclass(frozen=True)
class RuntimeCheckpoint:
    """One coordinator-owned, durably sealed runtime boundary.

    ``DAY_BATCH`` is the BACKTEST policy and ``SESSION_BOUNDARY`` is the
    LIVE/PAPER policy.  The atomic RuntimeState row is the one authoritative
    resume image.  A checkpoint carries only bounded proof that the current
    top-level compact state is that boundary; it deliberately never embeds a
    second rollback payload.
    """

    checkpoint_id: str
    checkpoint_kind: str
    trade_date: str
    session: Optional[str]
    boundary_source_time: Optional[str]
    processed_watermark: Dict[str, Any]
    continuity_generation: Any
    state_fingerprint: str
    completeness: Dict[str, Any]
    sealed_at: Optional[str]
    status: str

    @property
    def complete(self) -> bool:
        """Return whether this is a proved, durably sealed checkpoint."""

        return bool(
            self.status == "SEALED"
            and self.sealed_at
            and self.completeness.get("complete") is True
        )

    @property
    def prepared(self) -> bool:
        """Return whether this is a durable, not-yet-finalized boundary."""

        return bool(
            self.status == "PREPARED"
            and self.sealed_at is None
            and self.completeness.get("complete") is True
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return an isolated JSON-safe durable representation."""

        return {
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_kind": self.checkpoint_kind,
            "trade_date": self.trade_date,
            "session": self.session,
            "boundary_source_time": self.boundary_source_time,
            "processed_watermark": copy.deepcopy(self.processed_watermark),
            "continuity_generation": copy.deepcopy(self.continuity_generation),
            "state_fingerprint": self.state_fingerprint,
            "completeness": copy.deepcopy(self.completeness),
            "sealed_at": self.sealed_at,
            "status": self.status,
        }


@dataclass(frozen=True)
class RestoredManualTradeIntent:
    """One account/run-validated durable manual intent used during startup."""

    intent: "TradeIntent"
    durable_status: str


class RuntimeStateRestoreError(RuntimeError):
    """The durable runtime state could not be read safely."""


@dataclass
class RuntimeStateManager:
    """策略运行时状态管理器"""

    run_id: str

    # 配置
    snapshot_interval: float = 10.0  # 快照间隔（秒）
    persist_enabled: bool = True  # 是否启用持久化
    log_dir: str = "logs/strategy"  # 日志存储目录
    enable_reserve: bool = False  # 是否启用资金冻结逻辑

    # 回测模式配置
    is_backtest: bool = False  # 是否为回测模式
    backtest_id: Optional[str] = None  # 回测记录ID (StrategyBacktest.id)
    _backtest_storage: Optional["BacktestResultStorage"] = field(default=None, repr=False)

    # 内存状态缓存
    _state: Dict[str, Any] = field(default_factory=lambda: {
        "version": 0,
        "positions": {},       # {code: PositionDict}
        "custom": {},          # 自定义状态
        "account": {
            "cash": 0.0,
            "frozen_cash": 0.0,
            "total_asset": 0.0,
        },
        "bucket_ledger": {},
        "decision_traces": [],
        "trade_intents": {},
        "last_updated": None,
    }, repr=False)

    # 标记是否有未保存的更改
    _dirty: bool = field(default=False, repr=False)
    _dirty_revision: int = field(default=0, repr=False)
    _last_snapshot_attempt_revision: int = field(default=-1, repr=False)
    _snapshot_cas_conflicts: int = field(default=0, repr=False)
    _last_snapshot_failure_code: Optional[str] = field(default=None, repr=False)
    _last_snapshot_reconciliation_outcome: Optional[str] = field(
        default=None,
        repr=False,
    )
    # Structured-position persistence is intentionally separate from the
    # generic runtime-state revision.  Coordinator-owned session/day seals and
    # immediate external-fact boundaries may update candidate/custom state;
    # this cache avoids rewriting a complete unchanged position projection.
    # The last successfully durable complete position-code set and structured
    # projection fingerprint.  They are only round-trip optimizations: any
    # restore, CAS conflict, or commit-unknown result forces the next snapshot
    # to replace the complete durable position view before this cache is trusted
    # again.
    _persisted_position_codes: Optional[frozenset[str]] = field(
        default=None,
        repr=False,
    )
    _persisted_position_snapshot_fingerprint: Optional[str] = field(
        default=None,
        repr=False,
    )
    _force_position_snapshot: bool = field(default=True, repr=False)

    # 资金/持仓冻结索引；镜像到 custom state，供 Engine 重启恢复。
    _reservations: Dict[str, float] = field(default_factory=dict, repr=False)
    _position_reservations: Dict[str, Dict[str, int]] = field(
        default_factory=dict, repr=False
    )
    _bucket_ledger: Any = field(default=None, repr=False)
    _decision_trace_logger: Any = field(default=None, repr=False)
    # Decision traces are generated synchronously with a StrategyOutput, but
    # historically every one spawned an independent session/commit task.  That
    # created unbounded concurrent database work and competed directly with the
    # causally-required RuntimeState CAS.  The records below retain their stable
    # UUIDs until the next RuntimeState transaction durably commits both facts.
    # A failed/unknown commit leaves the exact records queued for reconciliation
    # or retry; they are never coalesced or dropped.
    _pending_decision_trace_records: list[Dict[str, Any]] = field(
        default_factory=list,
        repr=False,
    )
    # A failing commit response is indeterminate: PostgreSQL may have already
    # committed the RuntimeState CAS and trace append. Retain this exact token,
    # version and UUID batch until an authoritative read resolves it.
    _pending_trace_commit_unknown_attempts: Dict[str, Dict[str, Any]] = field(
        default_factory=dict,
        repr=False,
    )
    _unpersisted_trade_intent_ids: set[str] = field(
        default_factory=set,
        repr=False,
    )

    # 后台任务
    _snapshot_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _running: bool = field(default=False, repr=False)
    _state_queue: Optional[asyncio.Queue] = field(default=None, repr=False)
    _state_sync_task: Optional[asyncio.Task] = field(default=None, repr=False)
    _state_sync_error: Optional[str] = field(default=None, repr=False)
    # The strategy object remains the authoritative in-memory source until a
    # coordinator boundary requests an exact capture.  The consumer only keeps
    # root-key references here; it must not deep-copy a full
    # ``instrument_states``/``runtime_events`` payload for every Tick.
    _state_sync_strategy: Any = field(default=None, repr=False)
    _state_sync_pending_deltas: Dict[str, Any] = field(
        default_factory=dict,
        repr=False,
    )
    _state_sync_initial_strategy_keys: set[str] = field(
        default_factory=set,
        repr=False,
    )
    _state_sync_captured_strategy_keys: set[str] = field(
        default_factory=set,
        repr=False,
    )
    # The durable projection is deliberately separate from ``_state``.  A
    # strategy may retain reconstructible hot market windows in memory while
    # its checkpoint/restart truth excludes them.  It survives
    # ``stop_state_sync`` long enough for RuntimeStateManager.stop() to write
    # the final snapshot, then is cleared with the terminated source.
    _state_sync_durable_strategy_snapshot: Optional[Dict[str, Any]] = field(
        default=None,
        repr=False,
    )
    _state_sync_has_captured: bool = field(default=False, repr=False)
    _snapshot_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _checkpoint_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _final_snapshot_saved: bool = field(default=False, repr=False)

    # 文件句柄
    _log_file_path: Optional[str] = field(default=None, repr=False)

    # 日志器
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("RuntimeStateManager"),
        repr=False,
    )

    def __post_init__(self):
        from quantx_domain.trading.bucket_ledger import BucketLedger
        from quantx_domain.trading.decision_trace import DecisionTraceLogger

        self.logger = logging.getLogger(f"StateManager-{self.run_id[:8]}")
        self._bucket_ledger = BucketLedger(run_id=self.run_id)
        self._decision_trace_logger = DecisionTraceLogger()
        
        # 确保日志目录存在
        if self.persist_enabled:
            os.makedirs(self.log_dir, exist_ok=True)
            self._log_file_path = os.path.join(self.log_dir, f"{self.run_id}.jsonl")

    # ==================== 生命周期 ====================

    async def start(self) -> None:
        """启动状态管理器"""
        if self._running:
            return

        self._final_snapshot_saved = False
        self._running = True

        # RuntimeState writes are exclusively coordinator-owned boundaries:
        # LIVE/PAPER use explicit session seals and immediate external-fact
        # checkpoints, while BACKTEST uses its explicit day batch.  A periodic
        # task would make ordinary hot diagnostics/trace slices durable without
        # the coordinator's queue and continuity proof, so it is intentionally
        # absent in every mode.
        self._snapshot_task = None
        self.logger.info("状态管理器已启动（等待协调器检查点）: %s", self.run_id)

    async def stop(self) -> None:
        """停止状态管理器"""
        self._running = False

        # 取消后台任务
        snapshot_task = self._snapshot_task
        self._snapshot_task = None
        if snapshot_task and snapshot_task is not asyncio.current_task():
            if not snapshot_task.done():
                snapshot_task.cancel()
            await asyncio.gather(snapshot_task, return_exceptions=True)

        if (
            self._state_sync_error is not None
            or self._state_queue is not None
            or (
                self._state_sync_task is not None
                and not self._state_sync_task.done()
            )
        ):
            raise RuntimeError(
                "策略状态同步尚未权威收敛，拒绝保存最终快照: "
                f"run_id={self.run_id}, error={self._state_sync_error or '-'}"
            )

        # 最后一次保存是停止边界的权威持久化点。版本冲突、
        # 提交结果无法确认等失败不得被吞掉，否则 Executor 会继续
        # 断开 Broker 并把未落盘的运行标记为 STOPPED。
        if not self._final_snapshot_saved:
            if not await self.save_snapshot():
                raise RuntimeError(
                    f"最终状态快照保存失败: run_id={self.run_id}"
                )
            self._final_snapshot_saved = True

        # ``stop_state_sync`` retains this only until the final save above.
        # After a terminal handoff there is no valid source that could make a
        # cached projection authoritative for a later start.
        self._clear_state_sync_source()

        self.logger.info(f"状态管理器已停止: {self.run_id}")

    async def abort_without_final_snapshot(self, strategy=None) -> None:
        """Cancel owned tasks without persisting non-authoritative state.

        This covers both a runtime that never reached RUNNING and a terminal
        generation whose market continuity cannot be proven. This path must not
        call ``save_snapshot`` and overwrite the last authoritative state.
        """

        self._running = False
        current_task = asyncio.current_task()
        tasks = [
            task
            for task in (self._snapshot_task, self._state_sync_task)
            if task is not None and task is not current_task
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._snapshot_task = None
        self._state_sync_task = None
        queue = self._state_queue
        source = self._state_sync_strategy or strategy
        self._state_queue = None
        self._state_sync_error = None
        if queue is not None and source and hasattr(source, "unsubscribe_state"):
            source.unsubscribe_state(queue)
        self._clear_state_sync_source()
        self.logger.info("状态管理器已中止（未保存最终快照）: %s", self.run_id)

    async def start_state_sync(self, strategy) -> None:
        """启动策略状态同步任务（通过订阅事件持久化）"""
        if not strategy or not hasattr(strategy, "subscribe_state"):
            return

        state = getattr(strategy, "state", None)
        to_dict = getattr(state, "to_dict", None)
        if not callable(to_dict):
            raise RuntimeError(
                "策略状态同步缺少权威状态源: "
                f"run_id={self.run_id}"
            )
        try:
            initial_state = to_dict()
        except Exception as exc:
            raise RuntimeError(
                "策略状态同步无法读取权威状态源: "
                f"run_id={self.run_id}, error={exc}"
            ) from exc
        if not isinstance(initial_state, Mapping):
            raise RuntimeError(
                "策略状态同步权威状态源返回值必须是映射: "
                f"run_id={self.run_id}"
            )

        if self._state_sync_task and not self._state_sync_task.done():
            return
        if self._state_queue is not None or self._state_sync_error is not None:
            raise RuntimeError(
                f"上一次策略状态同步未收敛: run_id={self.run_id}"
            )

        self._state_sync_error = None
        self._state_queue = strategy.subscribe_state()
        if self._state_queue is None or not callable(
            getattr(self._state_queue, "join", None)
        ):
            self._state_queue = None
            raise RuntimeError(
                "策略状态同步缺少可排空队列: "
                f"run_id={self.run_id}"
            )
        self._state_sync_strategy = strategy
        self._state_sync_pending_deltas.clear()
        self._state_sync_captured_strategy_keys.clear()
        self._state_sync_durable_strategy_snapshot = None
        self._state_sync_initial_strategy_keys = {
            str(key)
            for key in initial_state
            if str(key or "")
            and str(key) not in _STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS
        }
        self._state_sync_has_captured = False
        self._state_sync_task = asyncio.create_task(
            self._state_sync_loop(),
            name=f"state-sync-{self.run_id[:8]}",
        )

    async def stop_state_sync(self, strategy=None) -> None:
        """停止策略状态同步任务"""
        task = self._state_sync_task
        queue = self._state_queue
        if task is None and queue is None and self._state_sync_strategy is None:
            # A created-but-never-started runtime has no subscribed source to
            # capture.  Its caller may still run generic teardown safely.
            return
        failure = self._state_sync_error
        unfinished = int(getattr(queue, "_unfinished_tasks", 0) or 0)

        if failure is None and queue is not None and unfinished > 0:
            if task is None or task.done():
                failure = (
                    "策略状态同步任务已停止但队列仍有未处理事件: "
                    f"run_id={self.run_id}, unfinished={unfinished}"
                )
            else:
                try:
                    await asyncio.wait_for(queue.join(), timeout=5.0)
                except asyncio.TimeoutError:
                    failure = (
                        "策略状态同步队列停止前未能完全排空: "
                        f"run_id={self.run_id}"
                    )

        # The consumer can fail while ``queue.join`` is waiting.  Its finally
        # block balances the acquired item, so join may return even though that
        # delta was never applied.  Re-read the shared failure evidence before
        # treating the queue as authoritative.
        failure = failure or self._state_sync_error

        if failure is not None:
            self._state_sync_error = failure
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            # Keep the queue and error evidence. Clearing either would let a
            # later final snapshot falsely claim that every strategy delta was
            # incorporated.
            raise RuntimeError(failure)

        # A final generic save happens only after this method has detached the
        # consumer.  Capture once while the authoritative strategy source is
        # still bound, otherwise a final stop snapshot could silently retain an
        # older hot-state image.
        if (
            not self._state_sync_has_captured
            or self._state_sync_pending_deltas
        ):
            try:
                self._capture_bound_strategy_state()
            except Exception as exc:
                failure = (
                    "策略状态同步最终快照捕获失败: "
                    f"run_id={self.run_id}, error={exc}"
                )

        if failure is not None:
            self._state_sync_error = failure
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise RuntimeError(failure)

        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._state_sync_task = None

        source = self._state_sync_strategy or strategy
        if queue and source and hasattr(source, "unsubscribe_state"):
            source.unsubscribe_state(queue)
        self._state_queue = None
        # Keep the compact durable projection until ``stop`` has committed its
        # final generic snapshot.  The complete in-memory state/source itself
        # is detached now and cannot be mutated through this manager.
        self._clear_state_sync_source(clear_durable_projection=False)

    async def _state_sync_loop(self) -> None:
        """监听策略状态事件并同步到持久化层"""
        queue = self._state_queue
        if not queue:
            return

        while self._running:
            try:
                event = await queue.get()
            except asyncio.CancelledError:
                break

            try:
                persist = getattr(event, "persist", True)
                if not persist:
                    continue

                changes = getattr(event, "changes", None)
                key = getattr(event, "key", None)
                value = getattr(event, "value", None)
                if changes:
                    self._stage_strategy_state_delta(changes)
                elif key is not None:
                    self._stage_strategy_state_delta({key: value})
            except Exception as e:
                self._state_sync_error = (
                    f"策略状态同步应用失败: run_id={self.run_id}, error={e}"
                )
                self.logger.exception(self._state_sync_error)
                break
            finally:
                try:
                    queue.task_done()
                except ValueError:
                    pass

    def _clear_state_sync_source(
        self,
        *,
        clear_durable_projection: bool = True,
    ) -> None:
        """Drop only local source/staging references after a terminal handoff."""

        self._state_sync_strategy = None
        self._state_sync_pending_deltas.clear()
        self._state_sync_initial_strategy_keys.clear()
        self._state_sync_captured_strategy_keys.clear()
        self._state_sync_has_captured = False
        if clear_durable_projection:
            self._state_sync_durable_strategy_snapshot = None

    def _stage_strategy_state_delta(self, changes: Mapping[str, Any]) -> None:
        """Remember root-key dirtiness without copying the full strategy state."""

        if not isinstance(changes, Mapping):
            raise TypeError("策略状态同步变更必须是映射")
        staged = False
        for raw_key, value in changes.items():
            key = str(raw_key or "")
            if not key or key in _STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS:
                continue
            # This intentionally retains only a shallow reference.  The
            # strategy remains the source of truth and the coordinator later
            # captures one isolated full snapshot at its causal boundary.
            self._state_sync_pending_deltas[key] = value
            staged = True
        if staged:
            self._state_sync_has_captured = False
            self._mark_dirty()

    def capture_strategy_state_for_persistence(self, strategy: Any) -> None:
        """Capture one compact strategy projection before a durable write.

        Startup recovery can change strategy state before ``start_state_sync``
        subscribes to its notifications.  Those changes still need the same
        persistence projection as an ordinary checkpoint; accepting a raw
        ``state.to_dict()`` there would allow a V3 T-trade hot sample window
        back into RuntimeState.
        """

        self._capture_strategy_state_projection(strategy)

    def _capture_bound_strategy_state(self) -> None:
        """Replace strategy-owned state from one authoritative source snapshot."""

        strategy = self._state_sync_strategy
        current_strategy_keys = {
            key
            for key in dict(self._state.get("custom", {}) or {})
            if key not in _STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS
        }
        captured_keys = (
            current_strategy_keys
            | self._state_sync_initial_strategy_keys
            | set(self._state_sync_pending_deltas)
        )
        captured_strategy_keys = self._capture_strategy_state_projection(
            strategy,
            captured_keys=captured_keys,
        )
        self._state_sync_captured_strategy_keys = captured_strategy_keys
        self._state_sync_pending_deltas.clear()
        self._state_sync_has_captured = True

    def _capture_strategy_state_projection(
        self,
        strategy: Any,
        *,
        captured_keys: Optional[set[str]] = None,
    ) -> set[str]:
        """Capture full memory state plus the only permitted durable image."""

        state = getattr(strategy, "state", None)
        to_dict = getattr(state, "to_dict", None)
        if not callable(to_dict):
            raise RuntimeError("策略状态同步权威状态源不可用")
        source_snapshot = to_dict()
        if not isinstance(source_snapshot, Mapping):
            raise TypeError("策略状态同步权威状态源返回值必须是映射")
        persistence_projection = getattr(
            strategy,
            "persistence_state_snapshot",
            None,
        )
        durable_source_snapshot = (
            persistence_projection()
            if callable(persistence_projection)
            else source_snapshot
        )
        if not isinstance(durable_source_snapshot, Mapping):
            raise TypeError("策略持久化状态投影返回值必须是映射")

        # Filter first, then make exactly one deep copy of strategy-owned
        # state.  Manager-owned gates/outboxes/grid-book objects retain their
        # current authority and are never copied on every hot Tick.
        strategy_snapshot = {
            str(key): value
            for key, value in source_snapshot.items()
            if str(key or "")
            and str(key) not in _STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS
            and (captured_keys is None or str(key) in captured_keys)
        }
        durable_strategy_snapshot = {
            str(key): value
            for key, value in durable_source_snapshot.items()
            if str(key or "")
            and str(key) not in _STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS
            and (captured_keys is None or str(key) in captured_keys)
        }
        if set(durable_strategy_snapshot) != set(strategy_snapshot):
            raise RuntimeError(
                "策略持久化状态投影根键与权威状态不一致"
            )
        self._ensure_compact_t_trade_durable_custom_state(
            durable_strategy_snapshot
        )
        captured_snapshot = copy.deepcopy(strategy_snapshot)
        durable_captured_snapshot = copy.deepcopy(durable_strategy_snapshot)
        current_custom = dict(self._state.get("custom", {}) or {})
        preserved_custom = {
            key: value
            for key, value in current_custom.items()
            if key in _STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS
        }
        next_custom = {**preserved_custom, **captured_snapshot}
        if (
            current_custom != next_custom
            or self._state_sync_durable_strategy_snapshot
            != durable_captured_snapshot
        ):
            self._state["custom"] = next_custom
            self._mark_dirty()
        self._state_sync_durable_strategy_snapshot = durable_captured_snapshot
        return set(captured_snapshot)

    async def drain_strategy_state_changes(
        self,
        *,
        timeout_seconds: float = 5.0,
        capture_state: bool = True,
    ) -> bool:
        """Drain state notifications and optionally capture one full snapshot.

        ``capture_state=False`` is reserved for the executor's hot diagnostic
        path: it proves queue order while retaining only root-key references in
        memory.  The default captures one deep-copied strategy-owned snapshot
        after ``queue.join()`` and before a durability CAS.
        """

        queue = self._state_queue
        sync_task = self._state_sync_task
        if self._state_sync_error is not None:
            self.logger.error(
                "策略状态同步已有失败，拒绝持久化检查点: run_id=%s, error=%s",
                self.run_id,
                self._state_sync_error,
            )
            return False
        if queue is None or sync_task is None or sync_task.done():
            self.logger.error(
                "策略状态同步未运行，拒绝确认行情失效快照: run_id=%s",
                self.run_id,
            )
            return False
        try:
            await asyncio.wait_for(queue.join(), timeout=max(0.1, timeout_seconds))
        except asyncio.TimeoutError:
            self.logger.error(
                "策略状态同步排空超时，拒绝解除行情门禁: run_id=%s",
                self.run_id,
            )
            return False
        if self._state_sync_error is not None or sync_task.done():
            self.logger.error(
                "策略状态同步在检查点期间失败: run_id=%s, error=%s",
                self.run_id,
                self._state_sync_error or "task stopped",
            )
            return False
        if capture_state:
            try:
                # ``queue.join`` above yields no further control before this
                # synchronous capture, so the source snapshot is causally
                # aligned with every published state delta it acknowledges.
                self._capture_bound_strategy_state()
            except Exception as exc:
                self._state_sync_error = (
                    "策略状态同步快照捕获失败: "
                    f"run_id={self.run_id}, error={exc}"
                )
                self.logger.exception(self._state_sync_error)
                return False
        return True

    async def checkpoint_strategy_state_changes(
        self,
        *,
        timeout_seconds: float = 5.0,
    ) -> bool:
        """Drain strategy deltas and durably checkpoint them before routing resumes."""

        if not await self.drain_strategy_state_changes(
            timeout_seconds=timeout_seconds,
        ):
            return False
        return await self.force_save()

    # ==================== 协调器检查点 ====================

    def _normalize_checkpoint_request(
        self,
        *,
        trade_date: date | datetime | str,
        session: Optional[str],
        boundary_source_time: datetime | str | None,
        processed_watermark: Mapping[str, Any],
        continuity_generation: Any,
        completeness: Mapping[str, Any],
    ) -> tuple[str, Optional[str], Optional[str], Dict[str, Any], Any, Dict[str, Any]]:
        """Normalize the coordinator proof once for PREPARED and FINALIZE CASes."""

        return (
            self._normalize_checkpoint_trade_date(trade_date),
            self._normalize_checkpoint_session(session),
            self._normalize_checkpoint_boundary_time(boundary_source_time),
            self._normalize_checkpoint_mapping(
                processed_watermark,
                field_name="processed_watermark",
            ),
            self._normalize_checkpoint_value(
                continuity_generation,
                field_name="continuity_generation",
            ),
            self._normalize_checkpoint_mapping(
                completeness,
                field_name="completeness",
            ),
        )

    async def seal_checkpoint(
        self,
        *,
        trade_date: date | datetime | str,
        session: Optional[str],
        boundary_source_time: datetime | str | None,
        processed_watermark: Mapping[str, Any],
        continuity_generation: Any,
        completeness: Mapping[str, Any],
    ) -> Optional[RuntimeCheckpoint]:
        """Persist one explicit session/day checkpoint through the normal CAS.

        The executor owns the source-stream, queue, and continuity proof.  It
        must state that proof with ``completeness["complete"] is True`` before a
        checkpoint can be sealed.  The manager independently drains its own
        strategy-state subscriber (when attached), captures the resulting
        state fingerprint, and writes the metadata in the same transaction as
        RuntimeState, positions, and any pending decision traces.

        BACKTEST accepts only the generic ``DAY_BATCH`` form (``session=None``).
        LIVE/PAPER accepts the explicit ``AM``/``PM`` session boundaries and
        the executor-owned ``TERMINAL`` prefix.  ``TERMINAL`` is not a market
        session boundary: it is permitted only when the caller proves a
        quiesced terminal handoff through ``completeness["terminal"]``.  A
        missing proof is written as a durable ``BLOCKED`` attempt when
        persistence is available, but never returned as a usable checkpoint.
        """

        (
            normalized_trade_date,
            normalized_session,
            normalized_boundary_time,
            normalized_watermark,
            normalized_generation,
            normalized_completeness,
        ) = self._normalize_checkpoint_request(
            trade_date=trade_date,
            session=session,
            boundary_source_time=boundary_source_time,
            processed_watermark=processed_watermark,
            continuity_generation=continuity_generation,
            completeness=completeness,
        )

        if not self.persist_enabled:
            self._last_snapshot_failure_code = "PERSISTENCE_DISABLED"
            self.logger.error(
                "持久化已禁用，拒绝封存运行时检查点: run_id=%s",
                self.run_id,
            )
            return None

        async with self._checkpoint_lock:
            if self.has_prepared_checkpoint():
                # Only ``finalize_prepared_checkpoint`` may turn a staged
                # outbox into COMPLETE.  A direct seal here would otherwise
                # acknowledge neither its receipt nor its causal boundary.
                self._last_snapshot_failure_code = "PREPARED_CHECKPOINT_PENDING"
                return None
            checkpoint_kind, blockers = self._checkpoint_policy_blockers(
                session=normalized_session,
                boundary_source_time=normalized_boundary_time,
                processed_watermark=normalized_watermark,
                continuity_generation=normalized_generation,
                completeness=normalized_completeness,
            )

            if not blockers:
                state_drained = await self._drain_checkpoint_state_changes()
                if not state_drained:
                    blockers.append("MANAGER_STATE_QUEUE_NOT_DRAINED")
                normalized_completeness["manager_state_queue_drained"] = (
                    state_drained
                )

            if blockers:
                normalized_completeness["complete"] = False
                existing_blockers = normalized_completeness.get("blockers", [])
                if not isinstance(existing_blockers, list):
                    existing_blockers = [str(existing_blockers)]
                normalized_completeness["blockers"] = list(
                    dict.fromkeys(
                        [
                            str(value)
                            for value in [*existing_blockers, *blockers]
                            if str(value).strip()
                        ]
                    )
                )

            state_fingerprint = self._checkpoint_state_fingerprint()
            checkpoint = self._build_runtime_checkpoint(
                checkpoint_kind=checkpoint_kind,
                trade_date=normalized_trade_date,
                session=normalized_session,
                boundary_source_time=normalized_boundary_time,
                processed_watermark=normalized_watermark,
                continuity_generation=normalized_generation,
                state_fingerprint=state_fingerprint,
                completeness=normalized_completeness,
                sealed=not blockers,
            )

            existing = self._checkpoint_by_id(checkpoint.checkpoint_id)
            if (
                existing is not None
                and existing.complete
                and existing.state_fingerprint == state_fingerprint
                and not self._dirty
                and not self._pending_decision_trace_records
                and not self._pending_trace_commit_unknown_attempts
            ):
                return existing
            if (
                existing is not None
                and not checkpoint.complete
                and not self._dirty
                and not self._pending_decision_trace_records
                and not self._pending_trace_commit_unknown_attempts
            ):
                return None

            self._store_runtime_checkpoint(checkpoint)
            if not await self.save_snapshot():
                # A false result means the checkpoint was not authoritatively
                # proven.  Remove only this local candidate so an unrelated
                # later explicit save can never turn a failed seal into a
                # silent success.  If the commit actually happened, a fresh
                # durable restore still discovers the matching metadata.
                self._remove_runtime_checkpoint(checkpoint.checkpoint_id)
                return None

            persisted = self._checkpoint_by_id(checkpoint.checkpoint_id)
            if persisted is None:
                # ``save_snapshot`` may have reconciled to an external CAS
                # winner.  A successful generic save is not proof that this
                # specific checkpoint crossed that winner's transaction.
                return None
            return persisted if persisted.complete else None

    async def prepare_checkpoint(
        self,
        *,
        trade_date: date | datetime | str,
        session: Optional[str],
        boundary_source_time: datetime | str | None,
        processed_watermark: Mapping[str, Any],
        continuity_generation: Any,
        completeness: Mapping[str, Any],
        materialization_events: Iterable[Dict[str, Any]] = (),
    ) -> Optional[RuntimeCheckpoint]:
        """Durably stage one boundary before its post-CAS materialization.

        This method itself stages the exact post-CAS work in the manager-owned
        outbox under the checkpoint lock, then atomically persists that outbox,
        current runtime state, and decision traces with immutable boundary
        metadata.  A crash after it returns is therefore recoverable by
        replaying the outbox idempotently; it is *not* complete yet.
        """

        (
            normalized_trade_date,
            normalized_session,
            normalized_boundary_time,
            normalized_watermark,
            normalized_generation,
            normalized_completeness,
        ) = self._normalize_checkpoint_request(
            trade_date=trade_date,
            session=session,
            boundary_source_time=boundary_source_time,
            processed_watermark=processed_watermark,
            continuity_generation=continuity_generation,
            completeness=completeness,
        )
        normalized_events = self._normalize_t_trade_outbox_items(
            materialization_events,
            identity_key="event_key",
        )
        self._ensure_t_trade_materialization_events(normalized_events)
        normalized_event_keys = tuple(identity for identity, _ in normalized_events)
        if not self.persist_enabled:
            self._last_snapshot_failure_code = "PERSISTENCE_DISABLED"
            return None

        async with self._checkpoint_lock:
            checkpoint_kind, blockers = self._checkpoint_policy_blockers(
                session=normalized_session,
                boundary_source_time=normalized_boundary_time,
                processed_watermark=normalized_watermark,
                continuity_generation=normalized_generation,
                completeness=normalized_completeness,
            )
            if not blockers and not await self._drain_checkpoint_state_changes():
                blockers.append("MANAGER_STATE_QUEUE_NOT_DRAINED")
            if blockers:
                self._last_snapshot_failure_code = "CHECKPOINT_BLOCKED"
                return None

            previous_custom = copy.deepcopy(self._state.get("custom", {}) or {})
            previous_dirty = self._dirty
            previous_revision = self._dirty_revision
            previous_version = int(self._state.get("version", 0) or 0)
            current_outbox = dict(
                self.get_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY, {}) or {}
            )
            for event_key, event in normalized_events:
                existing_event = current_outbox.get(event_key)
                if (
                    existing_event is not None
                    and _json_safe(existing_event) != _json_safe(event)
                ):
                    raise ValueError(
                        "做 T 诊断检查点发件箱 event_key 内容冲突: "
                        f"{event_key}"
                    )
                current_outbox.setdefault(event_key, event)
            if len(current_outbox) > _MAX_T_TRADE_DIAGNOSTIC_OUTBOX_EVENTS:
                raise RuntimeError("做 T 诊断持久化发件箱超过 8192 条安全上限")
            if normalized_events:
                self.set_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY, current_outbox)
            pending_event_keys = {
                str(event.get("event_key") or "").strip()
                for event in self.pending_t_trade_diagnostic_events()
                if str(event.get("event_key") or "").strip()
            }
            if set(normalized_event_keys) != pending_event_keys:
                self._state["custom"] = previous_custom
                self._dirty = previous_dirty
                self._dirty_revision = previous_revision
                self._last_snapshot_failure_code = "PREPARED_OUTBOX_MISSING"
                return None
            # The exact event payloads are durably owned by the top-level
            # outbox.  The checkpoint stores only a bounded manifest proving
            # the precise set and immutable content which FINALIZE must
            # acknowledge.
            normalized_completeness.pop("materialization_event_keys", None)
            normalized_completeness[_PREPARED_DIAGNOSTIC_OUTBOX_MANIFEST_KEY] = (
                self._diagnostic_outbox_manifest(normalized_events)
            )

            state_fingerprint = self._checkpoint_state_fingerprint()
            checkpoint = self._build_runtime_checkpoint(
                checkpoint_kind=checkpoint_kind,
                trade_date=normalized_trade_date,
                session=normalized_session,
                boundary_source_time=normalized_boundary_time,
                processed_watermark=normalized_watermark,
                continuity_generation=normalized_generation,
                state_fingerprint=state_fingerprint,
                completeness=normalized_completeness,
                sealed=False,
                prepared=True,
            )
            existing = self._checkpoint_by_id(checkpoint.checkpoint_id)
            if existing is not None and existing.prepared and not self._dirty:
                return existing
            if self.has_prepared_checkpoint():
                self._last_snapshot_failure_code = "PREPARED_CHECKPOINT_PENDING"
                return None

            self._store_runtime_checkpoint(checkpoint)
            if not await self.save_snapshot():
                if int(self._state.get("version", 0) or 0) == previous_version:
                    self._state["custom"] = previous_custom
                    self._dirty = previous_dirty
                    self._dirty_revision = previous_revision
                return None
            persisted = self._checkpoint_by_id(checkpoint.checkpoint_id)
            return persisted if persisted is not None and persisted.prepared else None

    async def finalize_prepared_checkpoint(
        self,
        *,
        prepared_checkpoint_id: str,
        materialization_event_keys: Iterable[str],
    ) -> Optional[RuntimeCheckpoint]:
        """Ack one exact receipt and durably turn its PREPARED boundary SEALED.

        The final CAS intentionally happens only after the materializer has
        returned a receipt for *this* prepared outbox subset.  It removes that
        subset and the PREPARED record in the same RuntimeState transaction as
        the replacement SEALED metadata.  The top-level compact RuntimeState
        remains the single resume image; the SEALED record contains only its
        bounded boundary proof and state fingerprint.  A failed final CAS
        leaves the durable PREPARED metadata and top-level outbox replayable.
        """

        normalized_id = str(prepared_checkpoint_id or "").strip()
        receipt_keys = self._normalize_checkpoint_event_keys(
            materialization_event_keys
        )
        if not normalized_id or not self.persist_enabled:
            self._last_snapshot_failure_code = "PREPARED_FINALIZE_INVALID"
            return None

        async with self._checkpoint_lock:
            prepared = self._checkpoint_by_id(normalized_id)
            if prepared is None or not prepared.prepared:
                self._last_snapshot_failure_code = "PREPARED_CHECKPOINT_MISSING"
                return None
            pending_events = self._prepared_diagnostic_outbox_events(prepared)
            if pending_events is None:
                self._last_snapshot_failure_code = "PREPARED_OUTBOX_MISMATCH"
                return None
            expected_keys = self._normalize_checkpoint_event_keys(
                event.get("event_key") for event in pending_events
            )
            if receipt_keys != expected_keys:
                self._last_snapshot_failure_code = "PREPARED_RECEIPT_MISMATCH"
                return None
            if self._checkpoint_state_fingerprint() != prepared.state_fingerprint:
                # Never fold state/trace work which arrived during the await of
                # materialization into an older receipt boundary.
                self._last_snapshot_failure_code = "PREPARED_STATE_ADVANCED"
                return None
            previous_custom = copy.deepcopy(self._state.get("custom", {}) or {})
            previous_dirty = self._dirty
            previous_revision = self._dirty_revision
            previous_version = int(self._state.get("version", 0) or 0)
            self.acknowledge_t_trade_diagnostic_events(receipt_keys)
            final_checkpoint = self._build_runtime_checkpoint(
                checkpoint_kind=prepared.checkpoint_kind,
                trade_date=prepared.trade_date,
                session=prepared.session,
                boundary_source_time=prepared.boundary_source_time,
                processed_watermark=prepared.processed_watermark,
                continuity_generation=prepared.continuity_generation,
                state_fingerprint=self._checkpoint_state_fingerprint(),
                completeness=copy.deepcopy(prepared.completeness),
                sealed=True,
            )
            self._remove_runtime_checkpoint(prepared.checkpoint_id)
            self._store_runtime_checkpoint(final_checkpoint)
            if await self.save_snapshot():
                persisted = self._checkpoint_by_id(final_checkpoint.checkpoint_id)
                return persisted if persisted is not None and persisted.complete else None

            # A known-not-committed final CAS must leave the in-memory handoff
            # exactly replayable as well.  An external winner is fail-stop and
            # will be re-read from durable truth by the next owner.
            if int(self._state.get("version", 0) or 0) == previous_version:
                self._state["custom"] = previous_custom
                self._dirty = previous_dirty
                self._dirty_revision = previous_revision
            return None

    @staticmethod
    def _normalize_checkpoint_event_keys(values: Iterable[str]) -> tuple[str, ...]:
        if isinstance(values, str):
            values = (values,)
        return tuple(
            sorted(
                {
                    str(value or "").strip()
                    for value in values
                    if str(value or "").strip()
                }
            )
        )

    @staticmethod
    def _canonical_json_sha256(value: Any) -> str:
        """Hash one finite JSON value with the project's canonical encoding."""

        encoded = json.dumps(
            _json_safe(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _diagnostic_outbox_manifest(
        cls,
        events: Iterable[tuple[str, Mapping[str, Any]]],
    ) -> Dict[str, Any]:
        """Return a bounded proof for the one PREPARED diagnostic handoff.

        The outbox remains the one recoverable source of event payload truth;
        this manifest proves its exact ordered identity and immutable content
        without retaining another per-event copy in checkpoint metadata.
        """

        normalized: list[tuple[str, Dict[str, Any]]] = []
        seen: set[str] = set()
        for raw_identity, raw_payload in events:
            identity = str(raw_identity or "").strip()
            if not identity or identity in seen or not isinstance(raw_payload, Mapping):
                raise ValueError("做 T 诊断检查点发件箱清单无效")
            payload = copy.deepcopy(dict(raw_payload))
            if str(payload.get("event_key") or "").strip() != identity:
                raise ValueError("做 T 诊断检查点发件箱身份不匹配")
            seen.add(identity)
            normalized.append((identity, payload))
        normalized.sort(key=lambda item: item[0])
        ordered_keys = [identity for identity, _payload in normalized]
        return {
            "event_count": len(ordered_keys),
            "ordered_event_keys_sha256": cls._canonical_json_sha256(ordered_keys),
            "payload_sha256": cls._canonical_json_sha256(
                [
                    {"event_key": identity, "payload": payload}
                    for identity, payload in normalized
                ]
            ),
        }

    @classmethod
    def _prepared_diagnostic_outbox_manifest_valid(cls, value: Any) -> bool:
        if not isinstance(value, Mapping):
            return False
        count = value.get("event_count")
        keys_digest = str(value.get("ordered_event_keys_sha256") or "").strip()
        payload_digest = str(value.get("payload_sha256") or "").strip()
        return bool(
            isinstance(count, int)
            and count >= 0
            and len(keys_digest) == 64
            and len(payload_digest) == 64
            and all(character in "0123456789abcdef" for character in keys_digest)
            and all(character in "0123456789abcdef" for character in payload_digest)
        )

    def _current_diagnostic_outbox_items(
        self,
    ) -> Optional[list[tuple[str, Dict[str, Any]]]]:
        raw_outbox = self.get_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY, {})
        if not isinstance(raw_outbox, Mapping):
            return None
        items: list[tuple[str, Dict[str, Any]]] = []
        for raw_identity, raw_event in raw_outbox.items():
            identity = str(raw_identity or "").strip()
            if not identity or not isinstance(raw_event, Mapping):
                return None
            event = copy.deepcopy(dict(raw_event))
            if str(event.get("event_key") or "").strip() != identity:
                return None
            items.append((identity, event))
        return items

    def _prepared_diagnostic_outbox_events(
        self,
        prepared: RuntimeCheckpoint,
    ) -> Optional[list[Dict[str, Any]]]:
        """Validate and return the single outbox exactly staged by PREPARED."""

        expected = prepared.completeness.get(
            _PREPARED_DIAGNOSTIC_OUTBOX_MANIFEST_KEY
        )
        if not self._prepared_diagnostic_outbox_manifest_valid(expected):
            return None
        items = self._current_diagnostic_outbox_items()
        if items is None:
            return None
        try:
            actual = self._diagnostic_outbox_manifest(items)
        except (TypeError, ValueError):
            return None
        if _json_safe(dict(expected)) != actual:
            return None
        return [copy.deepcopy(event) for _identity, event in sorted(items)]

    def has_prepared_checkpoint(self) -> bool:
        """Return whether restored metadata claims an unfinished handoff.

        This deliberately inspects the raw status too: malformed PREPARED
        metadata is itself recovery evidence and must route startup to the
        fail-closed continuity/warming path instead of being ignored.  It
        never restores a prior checkpoint or an embedded payload: the current
        top-level compact state and one metadata record are the only
        authoritative representation.
        """

        raw_records = self._state.get("custom", {}).get(RUNTIME_CHECKPOINTS_KEY, [])
        return bool(
            isinstance(raw_records, list)
            and any(
                isinstance(raw, Mapping)
                and str(raw.get("status") or "").strip().upper() == "PREPARED"
                for raw in raw_records
            )
        )

    def latest_prepared_checkpoint(self) -> Optional[RuntimeCheckpoint]:
        """Return the intact PREPARED handoff for idempotent startup replay.

        An intact handoff retains the current durable RuntimeState rather than
        rolling it back.  Its state fingerprint excludes checkpoint metadata,
        so it proves the restored state/outbox is exactly the one staged by the
        PREPARED CAS.  ``None`` with :meth:`has_prepared_checkpoint` true means
        corruption or an incomplete handoff and is a fail-closed condition.
        """

        try:
            records = self._runtime_checkpoint_records()
            current_fingerprint = self._checkpoint_state_fingerprint()
        except (TypeError, ValueError):
            return None
        for checkpoint in reversed(records):
            if checkpoint.prepared:
                return (
                    checkpoint
                    if checkpoint.state_fingerprint == current_fingerprint
                    and self._prepared_diagnostic_outbox_events(checkpoint) is not None
                    else None
                )
        return None

    def prepared_t_trade_diagnostic_events(
        self,
        prepared_checkpoint_id: str,
    ) -> Optional[list[Dict[str, Any]]]:
        """Return exactly one intact PREPARED outbox, or fail closed.

        The checkpoint stores only a count and content-addressed manifest; the
        complete events live once in the top-level durable outbox.  This method
        is the Engine's handoff boundary: it prevents a materializer from
        touching a changed, missing, or extra event before FINALIZE verifies
        the same manifest again.
        """

        checkpoint_id = str(prepared_checkpoint_id or "").strip()
        if not checkpoint_id:
            self._last_snapshot_failure_code = "PREPARED_CHECKPOINT_MISSING"
            return None
        prepared = self._checkpoint_by_id(checkpoint_id)
        if prepared is None or not prepared.prepared:
            self._last_snapshot_failure_code = "PREPARED_CHECKPOINT_MISSING"
            return None
        events = self._prepared_diagnostic_outbox_events(prepared)
        if events is None:
            self._last_snapshot_failure_code = "PREPARED_OUTBOX_MISMATCH"
        return events

    def latest_complete_checkpoint(
        self,
        *,
        trade_date: date | datetime | str | None = None,
        session: Optional[str] = None,
    ) -> Optional[RuntimeCheckpoint]:
        """Return the current SEALED boundary only when top-level state matches.

        RuntimeState and its positions are committed atomically with this
        metadata.  A fingerprint mismatch is therefore corruption or an
        interrupted/non-authoritative handoff, never an invitation to roll back
        to an embedded historical payload.
        """

        normalized_trade_date = (
            self._normalize_checkpoint_trade_date(trade_date)
            if trade_date is not None
            else None
        )
        normalized_session = self._normalize_checkpoint_session(session)
        try:
            records = self._runtime_checkpoint_records()
            current_fingerprint = self._checkpoint_state_fingerprint()
        except (TypeError, ValueError):
            self.logger.error(
                "运行时检查点元数据无效，拒绝恢复: run_id=%s",
                self.run_id,
            )
            return None

        for checkpoint in reversed(records):
            if not checkpoint.complete:
                continue
            if (
                normalized_trade_date is not None
                and checkpoint.trade_date != normalized_trade_date
            ):
                continue
            if session is not None and checkpoint.session != normalized_session:
                continue
            if checkpoint.state_fingerprint != current_fingerprint:
                self._last_snapshot_failure_code = "COMPLETE_CHECKPOINT_STATE_MISMATCH"
                self.logger.error(
                    "完整检查点与当前 RuntimeState 指纹不一致，拒绝恢复: "
                    "run_id=%s checkpoint_id=%s",
                    self.run_id,
                    checkpoint.checkpoint_id,
                )
                return None
            return checkpoint
        return None

    async def restore_latest_complete_checkpoint(
        self,
        *,
        trade_date: date | datetime | str | None = None,
        session: Optional[str] = None,
    ) -> Optional[RuntimeCheckpoint]:
        """Restore durable RuntimeState, then validate its SEALED metadata."""

        restore_result = await self.restore()
        if restore_result.status == RuntimeStateRestoreStatus.PERSISTENCE_DISABLED:
            return None
        checkpoint = self.latest_complete_checkpoint(
            trade_date=trade_date,
            session=session,
        )
        if checkpoint is None:
            return None
        return checkpoint

    async def _drain_checkpoint_state_changes(self) -> bool:
        """Drain the manager-owned subscriber only when one is attached."""

        if self._state_sync_error is not None:
            return False
        if self._state_queue is None and self._state_sync_task is None:
            return True
        return await self.drain_strategy_state_changes()

    def _checkpoint_policy_blockers(
        self,
        *,
        session: Optional[str],
        boundary_source_time: Optional[str],
        processed_watermark: Mapping[str, Any],
        continuity_generation: Any,
        completeness: Mapping[str, Any],
    ) -> tuple[str, list[str]]:
        """Apply the only supported runtime checkpoint policies.

        Wall-clock and WholeQuoteHub validation belong to the coordinator.  In
        particular, a time threshold alone is never a proof of stream
        completeness; this manager only seals after the caller's explicit
        global-fence assertion.
        """

        if self.is_backtest:
            checkpoint_kind = "DAY_BATCH"
            policy_blockers = (
                [] if session is None else ["BACKTEST_REQUIRES_DAY_BATCH"]
            )
        else:
            checkpoint_kind = "SESSION_BOUNDARY"
            if session in {"AM", "PM"}:
                policy_blockers = []
            elif session == "TERMINAL":
                policy_blockers = (
                    []
                    if completeness.get("terminal") is True
                    else ["TERMINAL_PROOF_REQUIRED"]
                )
            else:
                policy_blockers = [
                    "LIVE_PAPER_REQUIRES_EXPLICIT_AM_PM_OR_TERMINAL_SESSION"
                ]

        blockers = list(policy_blockers)
        if completeness.get("complete") is not True:
            blockers.append("COMPLETENESS_NOT_PROVEN")
        if not boundary_source_time:
            blockers.append("BOUNDARY_SOURCE_TIME_MISSING")
        if not processed_watermark:
            blockers.append("PROCESSED_WATERMARK_MISSING")
        if continuity_generation is None:
            blockers.append("CONTINUITY_GENERATION_MISSING")
        return checkpoint_kind, blockers

    def _build_runtime_checkpoint(
        self,
        *,
        checkpoint_kind: str,
        trade_date: str,
        session: Optional[str],
        boundary_source_time: Optional[str],
        processed_watermark: Dict[str, Any],
        continuity_generation: Any,
        state_fingerprint: str,
        completeness: Dict[str, Any],
        sealed: bool,
        prepared: bool = False,
    ) -> RuntimeCheckpoint:
        status = "PREPARED" if prepared else "SEALED" if sealed else "BLOCKED"
        identity = {
            "run_id": self.run_id,
            "checkpoint_kind": checkpoint_kind,
            "trade_date": trade_date,
            "session": session,
            "boundary_source_time": boundary_source_time,
            "processed_watermark": processed_watermark,
            "continuity_generation": continuity_generation,
            "state_fingerprint": state_fingerprint,
            "completeness": completeness,
            "status": status,
        }
        encoded_identity = json.dumps(
            _json_safe(identity),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        checkpoint_id = hashlib.sha256(
            encoded_identity.encode("utf-8")
        ).hexdigest()
        return RuntimeCheckpoint(
            checkpoint_id=checkpoint_id,
            checkpoint_kind=checkpoint_kind,
            trade_date=trade_date,
            session=session,
            boundary_source_time=boundary_source_time,
            processed_watermark=copy.deepcopy(processed_watermark),
            continuity_generation=copy.deepcopy(continuity_generation),
            state_fingerprint=state_fingerprint,
            completeness=copy.deepcopy(completeness),
            sealed_at=time_utils.now().isoformat() if status == "SEALED" else None,
            status=status,
        )

    def _current_bucket_ledger_snapshot(self) -> Dict[str, Any]:
        """Return one stable bucket snapshot for the current CAS boundary."""

        current = self._state.get("bucket_ledger")
        custom_current = self._state.get("custom", {}).get(
            BUCKET_LEDGER_CUSTOM_STATE_KEY
        )
        if isinstance(current, Mapping) and current:
            snapshot = copy.deepcopy(dict(current))
        elif isinstance(custom_current, Mapping) and custom_current:
            snapshot = copy.deepcopy(dict(custom_current))
        else:
            snapshot = copy.deepcopy(self.get_bucket_ledger_snapshot())
        # BucketLedger.to_dict() carries generated_at.  Freeze that value in
        # memory before calculating a prepared/complete fingerprint so a later
        # read cannot make an otherwise intact PREPARED handoff look corrupt.
        self._state["bucket_ledger"] = copy.deepcopy(snapshot)
        self._state.setdefault("custom", {})[
            BUCKET_LEDGER_CUSTOM_STATE_KEY
        ] = copy.deepcopy(snapshot)
        return snapshot

    @staticmethod
    def _ensure_compact_t_trade_durable_custom_state(
        custom_state: Mapping[str, Any],
    ) -> None:
        """Reject a durable V3 opportunity image that still owns hot samples.

        The strategy owns the exact compact representation because it also owns
        the reducer schema.  RuntimeStateManager nevertheless enforces the
        non-negotiable boundary here: a caller without that projection must
        fail closed instead of serializing a tick window through an unusual
        pre-subscription, final-save, or recovery write path.
        """

        raw_checkpoints = custom_state.get(RUNTIME_CHECKPOINTS_KEY)
        if raw_checkpoints is not None:
            if not isinstance(raw_checkpoints, list) or len(raw_checkpoints) > 1:
                raise RuntimeError("运行时检查点必须只保留一个当前边界")
            for raw_checkpoint in raw_checkpoints:
                if not isinstance(raw_checkpoint, Mapping):
                    raise RuntimeError("运行时检查点元数据无效")
                if {
                    "state_payload",
                    "payload_fingerprint",
                }.intersection(raw_checkpoint):
                    raise RuntimeError("运行时检查点禁止嵌套状态载荷")
                if RuntimeStateManager._runtime_checkpoint_from_dict(raw_checkpoint) is None:
                    raise RuntimeError("运行时检查点元数据无效")

        raw_states = custom_state.get("instrument_states")
        if not isinstance(raw_states, Mapping):
            return
        for raw_code, raw_state in raw_states.items():
            if not isinstance(raw_state, Mapping):
                continue
            opportunity = raw_state.get("opportunity")
            if not isinstance(opportunity, Mapping):
                continue
            if "samples" in opportunity:
                raise RuntimeError(
                    "做 T 持久化投影仍包含内存行情样本: "
                    f"run_id={custom_state.get('run_id', '-')}, "
                    "location=custom, "
                    f"instrument={str(raw_code or '').strip() or '-'}"
                )

    @staticmethod
    def _ensure_t_trade_materialization_events(
        events: Iterable[tuple[str, Mapping[str, Any]]],
    ) -> None:
        """Allow the legacy checkpoint outbox to carry MATERIAL facts only."""

        for _event_key, payload in events:
            event_type = payload.get("type")
            if event_type is None:
                # RuntimeStateManager is shared by non-T strategies. A
                # caller with no T event discriminator remains outside this
                # specialized guard; a discriminated T event is fail-closed.
                continue
            if (
                event_type != _T_TRADE_OPPORTUNITY_EVALUATION_EVENT
                or str(payload.get("record_kind") or "").upper() != "MATERIAL"
            ):
                raise ValueError(
                    "做 T 检查点物化发件箱禁止普通诊断或未知事件"
                )

    @staticmethod
    def _drop_t_trade_opportunity_runtime_events(
        custom_state: Dict[str, Any],
    ) -> None:
        """Remove all T opportunity evaluations from durable RuntimeState.

        MATERIAL evaluation truth is owned by its dedicated evaluation/trace
        records and, while PREPARED, the top-level materialization outbox.  An
        event ring is neither a recovery source nor a second audit store.
        """

        raw_events = custom_state.get("runtime_events")
        if not isinstance(raw_events, (list, tuple)):
            return
        retained = [
            copy.deepcopy(dict(event))
            for event in raw_events
            if isinstance(event, Mapping)
            and event.get("type") != _T_TRADE_OPPORTUNITY_EVALUATION_EVENT
        ]
        if retained:
            custom_state["runtime_events"] = retained
        else:
            custom_state.pop("runtime_events", None)

    def _durable_custom_state_projection(
        self,
        *,
        exclude_checkpoint_metadata: bool = False,
    ) -> Dict[str, Any]:
        """Return the single compact custom-state image for a durable write.

        ``_state["custom"]`` intentionally remains the complete in-memory
        strategy image while a source is bound.  At a coordinator boundary the
        source capture records a separate, validated persistence projection;
        every durable consumer below must derive from that same projection so
        checkpoint payloads, fingerprints, and repository writes cannot drift.
        Manager-owned outboxes/gates/books remain authoritative in ``_state``
        and are merged unchanged.
        """

        current_custom = dict(self._state.get("custom", {}) or {})
        durable_strategy_snapshot = self._state_sync_durable_strategy_snapshot
        if durable_strategy_snapshot is None:
            projected = dict(current_custom)
        else:
            projected = {
                key: value
                for key, value in current_custom.items()
                if key in _STATE_SYNC_PRESERVED_CUSTOM_STATE_KEYS
            }
            projected.update(durable_strategy_snapshot)
        self._drop_t_trade_opportunity_runtime_events(projected)
        if exclude_checkpoint_metadata:
            projected.pop(RUNTIME_CHECKPOINTS_KEY, None)
            projected.pop(RUNTIME_SNAPSHOT_ATTEMPT_KEY, None)
        return copy.deepcopy(projected)

    def _checkpoint_state_projection(self) -> Dict[str, Any]:
        """Capture the current resume truth solely for its state fingerprint.

        This projection is never serialized inside ``runtime_checkpoints``.
        The top-level RuntimeState row is the single durable copy; a PREPARED
        materialization outbox stays top-level and is verified by its compact
        manifest instead of by a recursive checkpoint payload.
        """

        custom_state = self._durable_custom_state_projection(
            exclude_checkpoint_metadata=True,
        )
        custom_state.pop(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY, None)
        bucket_ledger = self._current_bucket_ledger_snapshot()
        custom_state[BUCKET_LEDGER_CUSTOM_STATE_KEY] = copy.deepcopy(bucket_ledger)
        return {
            "account": copy.deepcopy(self._state.get("account", {}) or {}),
            "positions": copy.deepcopy(self._state.get("positions", {}) or {}),
            "custom": custom_state,
            "bucket_ledger": bucket_ledger,
        }

    @staticmethod
    def _checkpoint_state_fingerprint_from_projection(
        projection: Mapping[str, Any],
    ) -> str:
        """Hash canonical resume truth while allowing raw position restoration."""

        account = projection.get("account")
        positions = projection.get("positions")
        custom_state = projection.get("custom")
        bucket_ledger = projection.get("bucket_ledger")
        if not isinstance(account, Mapping):
            raise ValueError("checkpoint state projection account is invalid")
        if not isinstance(positions, Mapping):
            raise ValueError("checkpoint state projection positions is invalid")
        if not isinstance(custom_state, Mapping):
            raise ValueError("checkpoint state projection custom state is invalid")
        if not isinstance(bucket_ledger, Mapping):
            raise ValueError("checkpoint state projection bucket ledger is invalid")
        if _json_safe(custom_state.get(BUCKET_LEDGER_CUSTOM_STATE_KEY)) != _json_safe(
            bucket_ledger
        ):
            raise ValueError("checkpoint state projection bucket ledger is inconsistent")
        canonical = {
            "account": copy.deepcopy(dict(account)),
            "positions": RuntimeStateManager._position_snapshot_projection(
                positions
            ),
            "custom": copy.deepcopy(dict(custom_state)),
        }
        serialized = json.dumps(
            _json_safe(canonical),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _checkpoint_state_fingerprint(self) -> str:
        """Hash current runtime truth without self-referential checkpoint data."""

        return self._checkpoint_state_fingerprint_from_projection(
            self._checkpoint_state_projection()
        )

    def _runtime_checkpoint_records(self) -> list[RuntimeCheckpoint]:
        raw_records = self._state.get("custom", {}).get(
            RUNTIME_CHECKPOINTS_KEY,
            [],
        )
        if raw_records is None:
            return []
        if not isinstance(raw_records, list):
            raise ValueError("runtime checkpoints must be a list")
        if len(raw_records) > 1:
            raise ValueError("runtime checkpoints must retain one current boundary")
        checkpoints: list[RuntimeCheckpoint] = []
        for raw in raw_records:
            checkpoint = self._runtime_checkpoint_from_dict(raw)
            if checkpoint is None:
                raise ValueError("runtime checkpoint metadata is invalid")
            checkpoints.append(checkpoint)
        return checkpoints

    def _checkpoint_by_id(self, checkpoint_id: str) -> Optional[RuntimeCheckpoint]:
        try:
            for checkpoint in reversed(self._runtime_checkpoint_records()):
                if checkpoint.checkpoint_id == checkpoint_id:
                    return checkpoint
        except (TypeError, ValueError):
            return None
        return None

    def _store_runtime_checkpoint(self, checkpoint: RuntimeCheckpoint) -> None:
        # One RuntimeState CAS owns both the top-level resume state and this
        # boundary.  Retaining an older SEALED row cannot restore anything that
        # the current row does not already contain; it only duplicates metadata
        # and obscures a corrupt PREPARED handoff.
        self._runtime_checkpoint_records()
        self._state.setdefault("custom", {})[RUNTIME_CHECKPOINTS_KEY] = [
            checkpoint.to_dict()
        ]
        self._mark_dirty()

    def _remove_runtime_checkpoint(self, checkpoint_id: str) -> None:
        try:
            records = self._runtime_checkpoint_records()
        except (TypeError, ValueError):
            return
        retained = [
            item.to_dict()
            for item in records
            if item.checkpoint_id != checkpoint_id
        ]
        if len(retained) == len(records):
            return
        custom_state = self._state.setdefault("custom", {})
        if retained:
            custom_state[RUNTIME_CHECKPOINTS_KEY] = retained
        else:
            custom_state.pop(RUNTIME_CHECKPOINTS_KEY, None)
        self._mark_dirty()

    @staticmethod
    def _runtime_checkpoint_from_dict(raw: Any) -> Optional[RuntimeCheckpoint]:
        if not isinstance(raw, Mapping):
            return None
        required_keys = {
            "checkpoint_id",
            "checkpoint_kind",
            "trade_date",
            "session",
            "boundary_source_time",
            "processed_watermark",
            "continuity_generation",
            "state_fingerprint",
            "completeness",
            "sealed_at",
            "status",
        }
        # This is intentionally an atomic schema cutover.  A historical nested
        # state payload cannot be safely reconciled with the current top-level
        # RuntimeState and must fail closed rather than become an implicit
        # compatibility format.
        if set(raw) != required_keys:
            return None
        try:
            checkpoint_id = str(raw.get("checkpoint_id") or "").strip()
            checkpoint_kind = str(raw.get("checkpoint_kind") or "").strip()
            trade_date = RuntimeStateManager._normalize_checkpoint_trade_date(
                raw.get("trade_date")
            )
            session = RuntimeStateManager._normalize_checkpoint_session(
                raw.get("session")
            )
            boundary_source_time = RuntimeStateManager._normalize_checkpoint_boundary_time(
                raw.get("boundary_source_time")
            )
            processed_watermark = RuntimeStateManager._normalize_checkpoint_mapping(
                raw.get("processed_watermark"),
                field_name="processed_watermark",
            )
            completeness = RuntimeStateManager._normalize_checkpoint_mapping(
                raw.get("completeness"),
                field_name="completeness",
            )
            continuity_generation = RuntimeStateManager._normalize_checkpoint_value(
                raw.get("continuity_generation"),
                field_name="continuity_generation",
            )
            state_fingerprint = str(raw.get("state_fingerprint") or "").strip()
            sealed_at = raw.get("sealed_at")
            if sealed_at is not None:
                sealed_at = RuntimeStateManager._normalize_checkpoint_boundary_time(
                    sealed_at
                )
            status = str(raw.get("status") or "").strip().upper()
        except (TypeError, ValueError):
            return None

        if (
            len(checkpoint_id) != 64
            or len(state_fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in checkpoint_id)
            or any(
                character not in "0123456789abcdef"
                for character in state_fingerprint
            )
            or checkpoint_kind not in {"DAY_BATCH", "SESSION_BOUNDARY"}
            or status not in {"SEALED", "PREPARED", "BLOCKED"}
            or completeness.get("complete") not in {True, False}
        ):
            return None
        if checkpoint_kind == "DAY_BATCH" and session is not None:
            return None
        if checkpoint_kind == "SESSION_BOUNDARY" and session not in {
            "AM",
            "PM",
            "TERMINAL",
        }:
            return None
        if session == "TERMINAL" and completeness.get("terminal") is not True:
            return None
        if status == "SEALED" and (
            not sealed_at
            or not boundary_source_time
            or not processed_watermark
            or continuity_generation is None
            or completeness.get("complete") is not True
        ):
            return None
        if status == "BLOCKED" and sealed_at is not None:
            return None
        if status == "PREPARED" and (
            sealed_at is not None
            or not boundary_source_time
            or not processed_watermark
            or continuity_generation is None
            or completeness.get("complete") is not True
        ):
            return None
        manifest = completeness.get(_PREPARED_DIAGNOSTIC_OUTBOX_MANIFEST_KEY)
        if status == "PREPARED" and not RuntimeStateManager._prepared_diagnostic_outbox_manifest_valid(
            manifest
        ):
            return None
        if manifest is not None and not RuntimeStateManager._prepared_diagnostic_outbox_manifest_valid(
            manifest
        ):
            return None
        return RuntimeCheckpoint(
            checkpoint_id=checkpoint_id,
            checkpoint_kind=checkpoint_kind,
            trade_date=trade_date,
            session=session,
            boundary_source_time=boundary_source_time,
            processed_watermark=processed_watermark,
            continuity_generation=continuity_generation,
            state_fingerprint=state_fingerprint,
            completeness=completeness,
            sealed_at=sealed_at,
            status=status,
        )

    @staticmethod
    def _normalize_checkpoint_trade_date(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return time_utils.to_shanghai(value).date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, str):
            return date.fromisoformat(value.strip()).isoformat()
        raise ValueError("checkpoint trade_date is required")

    @staticmethod
    def _normalize_checkpoint_session(value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(getattr(value, "value", value) or "").strip().upper()
        return normalized or None

    @staticmethod
    def _normalize_checkpoint_boundary_time(
        value: datetime | str | None,
    ) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            value = datetime.fromisoformat(normalized)
        if not isinstance(value, datetime):
            raise ValueError("checkpoint boundary_source_time must be a datetime")
        return time_utils.to_shanghai(value).isoformat()

    @staticmethod
    def _normalize_checkpoint_mapping(
        value: Mapping[str, Any],
        *,
        field_name: str,
    ) -> Dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"checkpoint {field_name} must be an object")
        normalized = _json_safe(copy.deepcopy(dict(value)))
        try:
            json.dumps(
                normalized,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"checkpoint {field_name} must contain finite JSON"
            ) from exc
        return normalized

    @staticmethod
    def _normalize_checkpoint_value(value: Any, *, field_name: str) -> Any:
        normalized = _json_safe(copy.deepcopy(value))
        try:
            json.dumps(
                normalized,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"checkpoint {field_name} must contain finite JSON"
            ) from exc
        return normalized

    def require_market_continuity_reconciliation(
        self,
        instrument_code: str,
        reason: str,
    ) -> None:
        code = str(instrument_code or "").strip().upper()
        if not code:
            return
        gates = dict(
            self.get_custom(MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY, {}) or {}
        )
        gates[code] = str(reason or "MARKET_DATA_CONTINUITY_LOST")
        self.set_custom(MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY, gates)

    def clear_market_continuity_reconciliation(self, instrument_code: str) -> None:
        code = str(instrument_code or "").strip().upper()
        gates = dict(
            self.get_custom(MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY, {}) or {}
        )
        if code not in gates:
            return
        gates.pop(code, None)
        self.set_custom(MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY, gates)

    def market_continuity_reconciliation(self) -> Dict[str, str]:
        return {
            str(code): str(reason)
            for code, reason in dict(
                self.get_custom(
                    MARKET_CONTINUITY_RECONCILE_REQUIRED_KEY,
                    {},
                )
                or {}
            ).items()
            if code
        }

    async def restore(self) -> RuntimeStateRestoreResult:
        """Restore durable state or raise when its truth cannot be queried.

        ``NOT_FOUND`` is a successful query for a genuinely new run. Database
        failures are never represented as an empty state because PAPER/LIVE
        callers must not continue from fabricated account and position facts.
        """
        self._invalidate_position_snapshot_cache()
        if self._state_sync_strategy is None:
            self._state_sync_durable_strategy_snapshot = None
        if not self.persist_enabled:
            return RuntimeStateRestoreResult(
                status=RuntimeStateRestoreStatus.PERSISTENCE_DISABLED,
                state=self._state,
            )

        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.strategy_run_state_repository import (
                StrategyRunPositionRepository,
                StrategyRunStateRepository,
            )

            queried = False
            found = False
            async for db in get_async_db():
                queried = True
                # Read the complete durable snapshot before mutating memory.
                # A position query failure after a successful state query must
                # not leave a half-restored manager that a caller could retry.
                state_repo = StrategyRunStateRepository(db)
                state_record = await state_repo.get_state(self.run_id)
                pos_repo = StrategyRunPositionRepository(db)
                positions = await pos_repo.get_all_positions(self.run_id)
                if state_record is None and positions:
                    raise RuntimeStateRestoreError(
                        "状态主记录缺失但存在持仓快照，拒绝从不完整运行状态启动: "
                        f"run_id={self.run_id}, positions={len(positions)}"
                    )
                found = state_record is not None or bool(positions)
                restored_ledger = False

                if state_record:
                    self._state["version"] = state_record.version
                    self._state["custom"] = dict(state_record.custom_state or {})
                    self._state["account"] = {
                        "cash": state_record.cash,
                        "frozen_cash": state_record.frozen_cash,
                        "total_asset": state_record.total_asset,
                    }
                    self._restore_reservation_state()
                    ledger_snapshot = self._state["custom"].get(BUCKET_LEDGER_CUSTOM_STATE_KEY)
                    if ledger_snapshot:
                        from quantx_domain.trading.bucket_ledger import BucketLedger

                        self._bucket_ledger = BucketLedger.from_dict(ledger_snapshot)
                        if not self._bucket_ledger.run_id:
                            self._bucket_ledger.run_id = self.run_id
                        restored_ledger = True

                self._state["positions"] = {
                    p.instrument_code: p.to_dict() for p in positions
                }
                self._adopt_durable_position_codes(
                    self._state["positions"]
                )
                if restored_ledger:
                    reconcile_required, _ = self._adopt_restored_bucket_ledger(
                        self._state["positions"],
                        mark_dirty=True,
                    )
                    self._state["bucket_ledger"] = self._bucket_ledger.to_dict()
                    if reconcile_required:
                        self.logger.error(
                            "Bucket ledger 恢复校验失败，运行进入 RECONCILE_REQUIRED: "
                            "run_id=%s violations=%s",
                            self.run_id,
                            self._state["custom"].get(
                                BUCKET_LEDGER_VIOLATIONS_KEY, []
                            ),
                        )
                else:
                    for code, position in self._state["positions"].items():
                        self._bucket_ledger.sync_position(code, position)
                    self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()

                self.logger.info(f"状态恢复完成: positions={len(positions)}")
                break

            if not queried:
                raise RuntimeStateRestoreError(
                    f"状态恢复未获得数据库会话: run_id={self.run_id}"
                )

            # 恢复日志路径
            self._state["log_file"] = self._log_file_path

        except Exception as e:
            self.logger.exception("状态恢复失败: run_id=%s", self.run_id)
            if isinstance(e, RuntimeStateRestoreError):
                raise
            raise RuntimeStateRestoreError(
                f"状态恢复查询失败: run_id={self.run_id}"
            ) from e

        return RuntimeStateRestoreResult(
            status=(
                RuntimeStateRestoreStatus.RESTORED
                if found
                else RuntimeStateRestoreStatus.NOT_FOUND
            ),
            state=self._state,
        )

    async def restore_manual_trade_intent(self, intent_id: str):
        """Rebuild one still-pending manual intent from its durable record."""
        if not self.persist_enabled or not intent_id:
            return None

        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.trade_intent_repository import (
                TradeIntentRepository,
            )

            async for db in get_async_db():
                record = await TradeIntentRepository(db).find_by_id(intent_id)
                if record is None or str(record.status or "").upper() != "AWAITING_APPROVAL":
                    return None

                data = record.to_dict()
                intent = self._manual_trade_intent_from_record(record)
                self._unpersisted_trade_intent_ids.discard(intent_id)
                self._cache_trade_intent(data)
                return intent
        except Exception as e:
            self.logger.error(f"恢复人工确认交易意图失败: intent_id={intent_id}, error={e}")
        return None

    async def restore_v3_manual_candidate_intents(
        self,
        *,
        account_id: str,
        linked_intent_ids: Optional[list[str]] = None,
    ) -> list[RestoredManualTradeIntent]:
        """Load active V3 manual candidates under one exact account/run scope.

        Unlike the ordinary single-intent restore helper, this method is a
        startup safety boundary: database errors, missing ownership, and any
        account mismatch raise instead of silently degrading.
        """

        if not self.persist_enabled:
            return []
        normalized_account_id = str(account_id or "").strip()
        if not normalized_account_id:
            raise RuntimeStateRestoreError(
                f"V3 候选恢复缺少账户绑定: run_id={self.run_id}"
            )

        from quantx_infrastructure.database.connection import get_async_db
        from quantx_infrastructure.models.strategy_run import StrategyRun
        from quantx_infrastructure.repositories.trade_intent_repository import (
            TradeIntentRepository,
        )

        opened_session = False
        async for db in get_async_db():
            opened_session = True
            strategy_run = await db.get(StrategyRun, self.run_id)
            if strategy_run is None:
                raise RuntimeStateRestoreError(
                    f"V3 候选恢复找不到策略运行: run_id={self.run_id}"
                )
            raw_run_parameters = strategy_run.parameters
            if isinstance(raw_run_parameters, str):
                try:
                    raw_run_parameters = json.loads(raw_run_parameters)
                except json.JSONDecodeError as exc:
                    raise RuntimeStateRestoreError(
                        "V3 候选恢复策略运行参数不是有效 JSON 对象: "
                        f"run_id={self.run_id}"
                    ) from exc
            if raw_run_parameters is None:
                run_parameters = {}
            elif isinstance(raw_run_parameters, Mapping):
                run_parameters = dict(raw_run_parameters)
            else:
                raise RuntimeStateRestoreError(
                    "V3 候选恢复策略运行参数不是 JSON 对象: "
                    f"run_id={self.run_id}"
                )
            run_account_id = str(run_parameters.get("account_id") or "").strip()
            if run_account_id != normalized_account_id:
                raise RuntimeStateRestoreError(
                    "V3 候选恢复账户与策略运行不匹配: "
                    f"run_id={self.run_id}"
                )

            records = await TradeIntentRepository(
                db
            ).find_v3_manual_candidate_recovery_intents(
                self.run_id,
                linked_intent_ids=linked_intent_ids,
            )
            restored: list[RestoredManualTradeIntent] = []
            for record in records:
                metadata = dict(record.intent_metadata or {})
                row_account_id = str(record.account_id or "").strip()
                metadata_account_id = str(metadata.get("account_id") or "").strip()
                if (
                    row_account_id != normalized_account_id
                    or metadata_account_id != normalized_account_id
                    or str(record.strategy_run_id or "") != self.run_id
                ):
                    raise RuntimeStateRestoreError(
                        "V3 候选恢复意图所有权不匹配: "
                        f"run_id={self.run_id}, intent_id={record.id}"
                    )
                data = record.to_dict()
                self._unpersisted_trade_intent_ids.discard(str(record.id))
                self._cache_trade_intent(data)
                restored.append(
                    RestoredManualTradeIntent(
                        intent=self._manual_trade_intent_from_record(record),
                        durable_status=str(record.status or "").strip().upper(),
                    )
                )
            return restored
        if not opened_session:
            raise RuntimeStateRestoreError(
                f"V3 候选恢复未获得数据库会话: run_id={self.run_id}"
            )
        return []

    @staticmethod
    def _manual_trade_intent_from_record(record: Any) -> "TradeIntent":
        from quantx_domain.strategies.base import (
            TradeIntent,
            TradeIntentExecutionMode,
        )

        data = record.to_dict()
        metadata = dict(data.get("metadata") or {})
        created_at_raw = metadata.get("intent_created_at")
        created_at = (
            datetime.fromisoformat(created_at_raw)
            if isinstance(created_at_raw, str) and created_at_raw
            else record.created_at
        )
        return TradeIntent(
            strategy_id=str(record.strategy_id or ""),
            run_id=str(record.strategy_run_id),
            instrument_code=str(record.instrument_code),
            direction=str(record.direction),
            bucket=str(record.bucket or "core"),
            reason=str(record.reason or ""),
            priority=str(record.priority or "NORMAL"),
            intent_type=str(record.intent_type) if record.intent_type else None,
            confidence=float(record.confidence or 0.0),
            target_amount=record.target_amount,
            target_position_pct=record.target_position_pct,
            target_volume=record.target_volume,
            limit_price_hint=record.limit_price_hint,
            execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
            approval_ttl_ms=metadata.get("approval_ttl_ms"),
            max_price_deviation_bps=metadata.get("max_price_deviation_bps"),
            metadata=metadata,
            trace_id=record.trace_id,
            intent_id=str(record.id),
            created_at=created_at or time_utils.now(),
        )

    async def get_trade_intent_snapshot(
        self, intent_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return durable intent execution truth for startup reconciliation."""
        if not self.persist_enabled or not intent_id:
            return None

        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.trade_intent_repository import (
                TradeIntentRepository,
            )

            async for db in get_async_db():
                record = await TradeIntentRepository(db).find_by_id(intent_id)
                if record is None:
                    return None
                return dict(record.to_dict())
        except Exception as e:
            self.logger.error(f"读取交易意图快照失败: intent_id={intent_id}, error={e}")
        return None

    async def get_earliest_unapplied_runtime_event_key(self) -> Optional[str]:
        """Return the startup barrier key for this strategy run, if any."""
        if not self.persist_enabled:
            return None

        from sqlalchemy import select

        from quantx_infrastructure.database.connection import get_async_db
        from quantx_infrastructure.models.agent_runtime import StrategyRuntimeEvent

        async for db in get_async_db():
            return await db.scalar(
                select(StrategyRuntimeEvent.business_key)
                .where(
                    StrategyRuntimeEvent.strategy_run_id == self.run_id,
                    StrategyRuntimeEvent.application_status != "APPLIED",
                )
                .order_by(
                    StrategyRuntimeEvent.created_at,
                    StrategyRuntimeEvent.event_id,
                )
                .limit(1)
            )
        raise RuntimeError("数据库会话未返回，无法检查持久化运行时事件")

    # ==================== 快照管理 ====================

    def has_applied_runtime_event(self, event_key: str) -> bool:
        """Return whether this runtime snapshot already contains the event effect."""
        if not event_key:
            return False
        return event_key in set(
            str(value)
            for value in list(
                self._state.get("custom", {}).get(APPLIED_RUNTIME_EVENT_KEYS, [])
                or []
            )
            if value
        )

    async def checkpoint_durable_runtime_event(
        self,
        event_key: str,
        *,
        custom_updates: Optional[Dict[str, Any]] = None,
        strategy_updates: Optional[Dict[str, Any]] = None,
        strategy_unsets: Optional[Iterable[str]] = None,
    ) -> bool:
        """Persist one durable event marker with all current runtime effects.

        The marker stays in memory if a database write fails. A same-process retry
        therefore skips the already-applied callbacks and retries only this atomic
        checkpoint. After a process crash, an uncommitted marker disappears with
        the uncommitted account/position/strategy state and the event is safe to
        apply again.
        """
        normalized_key = str(event_key or "").strip()
        if not normalized_key:
            raise ValueError("durable runtime event key is required")
        source_bound = self._state_sync_strategy is not None
        if source_bound and not await self.drain_strategy_state_changes():
            # A durable external fact must never CAS an older strategy image.
            # The caller retains its runtime-event barrier and fails closed.
            return False
        if custom_updates:
            if source_bound:
                # The bound source already supplied the complete
                # strategy-owned snapshot above.  Retain only executor-owned
                # data which is intentionally absent from StrategyBase.state;
                # re-merging ``custom_updates`` would deep-copy the same hot
                # ``instrument_states`` payload a second time.
                supplemental_updates = {
                    key: value
                    for key, value in custom_updates.items()
                    if key not in _MANAGER_OWNED_CUSTOM_STATE_KEYS
                    and key != GRID_BOOK_CUSTOM_STATE_KEY
                    and key not in self._state_sync_captured_strategy_keys
                }
                if supplemental_updates:
                    self.update_custom_state(supplemental_updates)
            else:
                self.update_strategy_custom_state(
                    custom_updates,
                    full_snapshot=True,
                )
        # ``custom_updates`` is the strategy's complete in-memory snapshot.  It
        # must not overwrite manager/API-owned values such as the grid book.
        # The callback patch, however, is the causal delta produced by this
        # durable report and therefore retains normal strategy ownership,
        # including an explicit grid-book mutation.  Apply both before the
        # marker so they are committed as one atomic snapshot.
        if strategy_updates:
            if source_bound:
                # The explicit grid-book patch is the one intentional
                # exception to passive source capture: the manager owns its
                # durable projection and must receive this causal update.
                grid_update = {
                    key: value
                    for key, value in strategy_updates.items()
                    if key == GRID_BOOK_CUSTOM_STATE_KEY
                }
                if grid_update:
                    self.update_strategy_custom_state(grid_update)
            else:
                self.update_strategy_custom_state(strategy_updates)
        if strategy_unsets:
            self.unset_strategy_custom_state(strategy_unsets)
        keys = [
            str(value)
            for value in list(
                self._state.get("custom", {}).get(APPLIED_RUNTIME_EVENT_KEYS, [])
                or []
            )
            if value
        ]
        if normalized_key not in keys:
            keys.append(normalized_key)
            self._state.setdefault("custom", {})[APPLIED_RUNTIME_EVENT_KEYS] = keys[
                -_MAX_APPLIED_RUNTIME_EVENT_KEYS:
            ]
            self._mark_dirty()
        if not self.persist_enabled:
            return True
        if not self._dirty:
            return True
        if await self.save_snapshot():
            return True
        return await self._adopt_committed_runtime_event(normalized_key)

    async def _adopt_committed_runtime_event(self, event_key: str) -> bool:
        """Recover a commit-unknown snapshot when PostgreSQL has the marker."""
        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.strategy_run_state_repository import (
                StrategyRunPositionRepository,
                StrategyRunStateRepository,
            )

            async for db in get_async_db():
                state_record = await StrategyRunStateRepository(db).get_state(
                    self.run_id
                )
                if state_record is None:
                    return False
                custom_state = copy.deepcopy(state_record.custom_state or {})
                applied_keys = {
                    str(value)
                    for value in list(
                        custom_state.get(APPLIED_RUNTIME_EVENT_KEYS, []) or []
                    )
                    if value
                }
                if event_key not in applied_keys:
                    return False

                positions = await StrategyRunPositionRepository(db).get_all_positions(
                    self.run_id
                )
                self._adopt_durable_position_codes(
                    position.instrument_code for position in positions
                )
                self._state["version"] = int(state_record.version or 0)
                if self._dirty_revision == self._last_snapshot_attempt_revision:
                    self._state["custom"] = custom_state
                    self._state["account"] = {
                        "cash": float(state_record.cash or 0.0),
                        "frozen_cash": float(state_record.frozen_cash or 0.0),
                        "total_asset": float(state_record.total_asset or 0.0),
                    }
                    self._state["positions"] = {
                        position.instrument_code: position.to_dict()
                        for position in positions
                    }
                    self._restore_reservation_state()
                    ledger_snapshot = custom_state.get(
                        BUCKET_LEDGER_CUSTOM_STATE_KEY
                    )
                    restored_ledger = False
                    if ledger_snapshot:
                        from quantx_domain.trading.bucket_ledger import BucketLedger

                        self._bucket_ledger = BucketLedger.from_dict(ledger_snapshot)
                        if not self._bucket_ledger.run_id:
                            self._bucket_ledger.run_id = self.run_id
                        restored_ledger = True
                    if restored_ledger:
                        (
                            _,
                            reconciliation_changed,
                        ) = self._adopt_restored_bucket_ledger(
                            self._state["positions"],
                            mark_dirty=True,
                        )
                        self._state["bucket_ledger"] = self._bucket_ledger.to_dict()
                    else:
                        for code, position in self._state["positions"].items():
                            self._bucket_ledger.sync_position(code, position)
                        self._state["bucket_ledger"] = (
                            self.get_bucket_ledger_snapshot()
                        )
                    self._dirty = bool(
                        restored_ledger and reconciliation_changed
                    )
                else:
                    # Preserve changes created after the uncertain commit. The
                    # adopted version lets the next explicit coordinator seal
                    # persist them.
                    self._dirty = True
                return True
        except Exception as e:
            self.logger.error(
                "恢复提交结果失败: run_id=%s, event_key=%s, error=%s",
                self.run_id,
                event_key,
                e,
            )
        return False

    async def _reconcile_snapshot_attempt(
        self,
        snapshot_token: str,
        *,
        snapshot_revision: int,
        expected_version: int,
    ) -> bool:
        """Resolve commit-unknown and advance past a genuine external CAS win."""
        self._last_snapshot_reconciliation_outcome = "UNAVAILABLE"
        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.strategy_run_state_repository import (
                StrategyRunPositionRepository,
                StrategyRunStateRepository,
            )

            async for db in get_async_db():
                state_record = await StrategyRunStateRepository(db).get_state(
                    self.run_id
                )
                if state_record is None:
                    self._last_snapshot_reconciliation_outcome = "NOT_COMMITTED"
                    return False
                authoritative_version = int(state_record.version or 0)
                custom_state = copy.deepcopy(state_record.custom_state or {})
                if custom_state.get(RUNTIME_SNAPSHOT_ATTEMPT_KEY) == snapshot_token:
                    self._last_snapshot_reconciliation_outcome = "COMMITTED"
                    positions = (
                        await StrategyRunPositionRepository(db).get_all_positions(
                            self.run_id
                        )
                    )
                    self._adopt_durable_position_codes(
                        position.instrument_code for position in positions
                    )
                    self._state["version"] = authoritative_version
                    if self._dirty_revision == snapshot_revision:
                        self._state["custom"] = custom_state
                        self._state["account"] = {
                            "cash": float(state_record.cash or 0.0),
                            "frozen_cash": float(state_record.frozen_cash or 0.0),
                            "total_asset": float(state_record.total_asset or 0.0),
                        }
                        self._state["positions"] = {
                            position.instrument_code: position.to_dict()
                            for position in positions
                        }
                        self._restore_reservation_state()
                        ledger_snapshot = custom_state.get(
                            BUCKET_LEDGER_CUSTOM_STATE_KEY
                        )
                        reconciliation_changed = False
                        if ledger_snapshot:
                            from quantx_domain.trading.bucket_ledger import BucketLedger

                            self._bucket_ledger = BucketLedger.from_dict(
                                ledger_snapshot
                            )
                            if not self._bucket_ledger.run_id:
                                self._bucket_ledger.run_id = self.run_id
                            (
                                _,
                                reconciliation_changed,
                            ) = self._adopt_restored_bucket_ledger(
                                self._state["positions"],
                                mark_dirty=True,
                            )
                            self._state["bucket_ledger"] = (
                                self._bucket_ledger.to_dict()
                            )
                        else:
                            from quantx_domain.trading.bucket_ledger import BucketLedger

                            self._bucket_ledger = BucketLedger(run_id=self.run_id)
                            for code, position in self._state["positions"].items():
                                self._bucket_ledger.sync_position(code, position)
                            self._state["bucket_ledger"] = (
                                self._bucket_ledger.to_dict()
                            )
                        self._dirty = bool(
                            ledger_snapshot and reconciliation_changed
                        )
                    else:
                        self._state.setdefault("custom", {})[
                            RUNTIME_SNAPSHOT_ATTEMPT_KEY
                        ] = snapshot_token
                        self._dirty = True
                    return True

                if authoritative_version > expected_version:
                    self._last_snapshot_reconciliation_outcome = "EXTERNAL_WINNER"
                    # A different writer won the CAS.  The local custom state
                    # was computed from an obsolete snapshot and must never be
                    # retried under the winner's version: doing so can overwrite
                    # a newer candidate with stale Engine state.  Adopt the full
                    # authoritative snapshot and leave the caller to fail-stop
                    # and recompute from a fresh runtime generation.
                    positions = (
                        await StrategyRunPositionRepository(db).get_all_positions(
                            self.run_id
                        )
                    )
                    self._adopt_durable_position_codes(
                        position.instrument_code for position in positions
                    )
                    self._state["version"] = authoritative_version
                    self._state["custom"] = custom_state
                    self._state["account"] = {
                        "cash": float(state_record.cash or 0.0),
                        "frozen_cash": float(state_record.frozen_cash or 0.0),
                        "total_asset": float(state_record.total_asset or 0.0),
                    }
                    self._state["positions"] = {
                        position.instrument_code: position.to_dict()
                        for position in positions
                    }
                    self._restore_reservation_state()
                    ledger_snapshot = custom_state.get(
                        BUCKET_LEDGER_CUSTOM_STATE_KEY
                    )
                    if ledger_snapshot:
                        from quantx_domain.trading.bucket_ledger import BucketLedger

                        self._bucket_ledger = BucketLedger.from_dict(ledger_snapshot)
                        if not self._bucket_ledger.run_id:
                            self._bucket_ledger.run_id = self.run_id
                    else:
                        from quantx_domain.trading.bucket_ledger import BucketLedger

                        self._bucket_ledger = BucketLedger(run_id=self.run_id)
                        for code, position in self._state["positions"].items():
                            self._bucket_ledger.sync_position(code, position)
                    self._state["bucket_ledger"] = self._bucket_ledger.to_dict()
                    self._dirty = False
                if self._last_snapshot_reconciliation_outcome == "UNAVAILABLE":
                    self._last_snapshot_reconciliation_outcome = "NOT_COMMITTED"
                return False
        except Exception as e:
            self.logger.error(
                "恢复快照提交结果失败: run_id=%s, token=%s, error=%s",
                self.run_id,
                snapshot_token,
                e,
            )
        return False

    async def _resolve_pending_trace_commit_unknown_attempts(self) -> bool:
        """Resolve indeterminate trace/CAS commits before a later retry.

        An uncertain batch cannot simply follow a newly adopted winner: if the
        old token committed it is already durable, while an external winner
        proves the captured StrategyOutput lost its causal CAS. Only a
        successful read proving that the old token did not commit may retry the
        same stable UUID batch normally.
        """

        for snapshot_token, attempt in list(
            self._pending_trace_commit_unknown_attempts.items()
        ):
            trace_ids = tuple(str(item) for item in attempt["trace_ids"])
            reconciled = await self._reconcile_snapshot_attempt(
                snapshot_token,
                snapshot_revision=int(attempt["snapshot_revision"]),
                expected_version=int(attempt["expected_version"]),
            )
            outcome = self._last_snapshot_reconciliation_outcome
            if reconciled:
                self._acknowledge_pending_decision_trace_records(trace_ids)
                self._pending_trace_commit_unknown_attempts.pop(
                    snapshot_token,
                    None,
                )
                continue
            if outcome == "EXTERNAL_WINNER":
                self._discard_pending_decision_trace_records(trace_ids)
                self._pending_trace_commit_unknown_attempts.pop(
                    snapshot_token,
                    None,
                )
                continue
            if outcome == "NOT_COMMITTED":
                self._pending_trace_commit_unknown_attempts.pop(
                    snapshot_token,
                    None,
                )
                continue

            self._last_snapshot_failure_code = "PERSISTENCE_ERROR"
            return False
        return True

    async def save_snapshot(self) -> bool:
        """保存状态快照到数据库"""
        if not self.persist_enabled or (
            not self._dirty
            and not self._pending_decision_trace_records
            and not self._pending_trace_commit_unknown_attempts
        ):
            return True
        async with self._snapshot_lock:
            if self._pending_trace_commit_unknown_attempts:
                self._last_snapshot_failure_code = None
                if not await self._resolve_pending_trace_commit_unknown_attempts():
                    return False
            if (
                not self._dirty
                and not self._pending_decision_trace_records
                and not self._pending_trace_commit_unknown_attempts
            ):
                return True
            self._last_snapshot_failure_code = None
            snapshot_token = ""
            snapshot_revision = self._dirty_revision
            expected_version = int(self._state.get("version", 0) or 0)
            commit_attempted = False
            trace_batch = tuple(
                copy.deepcopy(item)
                for item in self._pending_decision_trace_records
            )
            trace_ids = tuple(
                str(item.get("id") or "") for item in trace_batch
            )
            if any(not item for item in trace_ids):
                self._last_snapshot_failure_code = "PERSISTENCE_ERROR"
                self.logger.error(
                    "决策审计缺少稳定记录标识，拒绝保存状态快照: run_id=%s",
                    self.run_id,
                )
                return False
            try:
                from quantx_infrastructure.database.connection import get_async_db
                from quantx_infrastructure.repositories.strategy_decision_trace_repository import (
                    StrategyDecisionTraceRepository,
                )
                from quantx_infrastructure.repositories.strategy_run_state_repository import (
                    StrategyRunPositionRepository,
                    StrategyRunStateRepository,
                )

                self._state["last_updated"] = time_utils.now().isoformat()
                custom_state = self._durable_custom_state_projection()
                self._ensure_compact_t_trade_durable_custom_state(custom_state)
                snapshot_token = str(uuid.uuid4())
                custom_state[RUNTIME_SNAPSHOT_ATTEMPT_KEY] = snapshot_token
                custom_state[BUCKET_LEDGER_CUSTOM_STATE_KEY] = copy.deepcopy(
                    self._current_bucket_ledger_snapshot()
                )
                account = copy.deepcopy(self._state.get("account", {}) or {})
                positions = copy.deepcopy(self._state.get("positions", {}) or {})
                position_projection = self._position_snapshot_projection(positions)
                position_codes = frozenset(
                    item["instrument_code"] for item in position_projection
                )
                position_fingerprint = self._position_snapshot_fingerprint(
                    position_projection
                )
                position_snapshot_required = (
                    self._force_position_snapshot
                    or self._persisted_position_snapshot_fingerprint
                    != position_fingerprint
                )
                self._last_snapshot_attempt_revision = snapshot_revision

                async for db in get_async_db():
                    state_repo = StrategyRunStateRepository(db)
                    saved = await state_repo.upsert_state(
                        run_id=self.run_id,
                        cash=account.get("cash", 0.0),
                        frozen_cash=account.get("frozen_cash", 0.0),
                        total_asset=account.get("total_asset", 0.0),
                        custom_state=custom_state,
                        expected_version=expected_version,
                        commit=False,
                        flush=False,
                    )
                    if not saved:
                        self._snapshot_cas_conflicts += 1
                        self._last_snapshot_failure_code = "CAS_CONFLICT"
                        raise RuntimeError(
                            "runtime state snapshot version conflict: "
                            f"run_id={self.run_id}, expected_version={expected_version}"
                        )
                    if position_snapshot_required:
                        pos_repo = StrategyRunPositionRepository(db)
                        if (
                            not self._force_position_snapshot
                            and self._persisted_position_codes == position_codes
                        ):
                            await pos_repo.update_existing_positions_snapshot(
                                self.run_id,
                                positions,
                                commit=False,
                                flush=False,
                            )
                        else:
                            await pos_repo.replace_positions_snapshot(
                                self.run_id,
                                positions,
                                commit=False,
                                flush=False,
                            )
                    if trace_batch:
                        await StrategyDecisionTraceRepository(db).append_traces(
                            trace_batch,
                            commit=False,
                            flush=False,
                        )
                    commit_attempted = True
                    await db.commit()
                    self._acknowledge_pending_decision_trace_records(trace_ids)
                    self._persisted_position_codes = position_codes
                    self._persisted_position_snapshot_fingerprint = (
                        position_fingerprint
                    )
                    self._force_position_snapshot = False
                    self._state["version"] = expected_version + 1
                    self._state.setdefault("custom", {})[
                        RUNTIME_SNAPSHOT_ATTEMPT_KEY
                    ] = snapshot_token
                    if self._dirty_revision == snapshot_revision:
                        self._dirty = False
                    self.logger.debug(
                        f"状态快照已保存: v{self._state.get('version')}"
                    )
                    break
                return True
            except Exception as e:
                self._invalidate_position_snapshot_cache()
                if self._last_snapshot_failure_code is None:
                    self._last_snapshot_failure_code = "PERSISTENCE_ERROR"
                if self._last_snapshot_failure_code == "CAS_CONFLICT":
                    # ``upsert_state`` rejected the captured RuntimeState
                    # version before this transaction could append any trace.
                    # That StrategyOutput belongs to the losing generation and
                    # must never be carried into a later winner CAS.  Filter
                    # only the captured stable UUIDs: a trace recorded while
                    # the await above yielded belongs to a later generation.
                    self._discard_pending_decision_trace_records(trace_ids)
                elif commit_attempted and trace_batch:
                    self._pending_trace_commit_unknown_attempts[snapshot_token] = {
                        "trace_ids": trace_ids,
                        "snapshot_revision": snapshot_revision,
                        "expected_version": expected_version,
                    }
                self.logger.error(f"保存快照失败: {e}")
                if snapshot_token:
                    reconciled = await self._reconcile_snapshot_attempt(
                        snapshot_token,
                        snapshot_revision=snapshot_revision,
                        expected_version=expected_version,
                    )
                    if reconciled:
                        # The durable snapshot token proves this exact
                        # transaction committed, which also proves its trace
                        # append committed.  Do not retry the stable UUIDs.
                        self._acknowledge_pending_decision_trace_records(trace_ids)
                        self._pending_trace_commit_unknown_attempts.pop(
                            snapshot_token,
                            None,
                        )
                    elif (
                        self._last_snapshot_reconciliation_outcome
                        == "EXTERNAL_WINNER"
                    ):
                        self._discard_pending_decision_trace_records(trace_ids)
                        self._pending_trace_commit_unknown_attempts.pop(
                            snapshot_token,
                            None,
                        )
                    elif (
                        self._last_snapshot_reconciliation_outcome
                        == "NOT_COMMITTED"
                    ):
                        self._pending_trace_commit_unknown_attempts.pop(
                            snapshot_token,
                            None,
                        )
                    return reconciled
                return False

    @property
    def snapshot_cas_conflicts(self) -> int:
        return self._snapshot_cas_conflicts

    @property
    def last_snapshot_failure_code(self) -> Optional[str]:
        return self._last_snapshot_failure_code

    def _mark_dirty(self) -> None:
        """标记状态已更改"""
        self._dirty_revision += 1
        self._dirty = True

    def _mark_positions_dirty(self) -> None:
        """Record a possible structured-position change plus the state change.

        A position operation can update fields that live only in the bucket
        ledger/custom state.  The final durable-table decision is made from
        the normalized projection fingerprint in ``save_snapshot``; this
        marker only ensures a snapshot is attempted.
        """
        self._mark_dirty()

    @staticmethod
    def _position_snapshot_projection(
        positions: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Return exactly the ``StrategyRunPosition`` durable projection.

        Keep this normalization aligned with
        ``StrategyRunPositionRepository._normalize_snapshot_positions``.  The
        repository owns the write itself; this local projection is used only to
        prove that a no-write checkpoint has the same structured position truth
        as the last known durable snapshot.
        """
        projection: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        for raw_code, raw_position in sorted(dict(positions or {}).items()):
            code = str(raw_code or "").strip().upper()
            if not code:
                raise ValueError("runtime position snapshot contains empty code")
            if code in seen_codes:
                raise ValueError(
                    "runtime position snapshot contains duplicate instrument code"
                )
            seen_codes.add(code)
            position = dict(raw_position or {})
            projection.append(
                {
                    "instrument_code": code,
                    "long_volume": int(position.get("long_volume", 0) or 0),
                    "short_volume": int(position.get("short_volume", 0) or 0),
                    "long_avg_price": float(
                        position.get("long_avg_price", 0.0) or 0.0
                    ),
                    "short_avg_price": float(
                        position.get("short_avg_price", 0.0) or 0.0
                    ),
                    "market_value": float(
                        position.get("market_value", 0.0) or 0.0
                    ),
                    "pnl": float(position.get("pnl", 0.0) or 0.0),
                    "last_price": float(position.get("last_price", 0.0) or 0.0),
                }
            )
        return projection

    @staticmethod
    def _position_snapshot_fingerprint(
        projection: Iterable[Mapping[str, Any]],
    ) -> str:
        """Hash the normalized, durable structured-position projection."""
        serialized = json.dumps(
            list(projection),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _invalidate_position_snapshot_cache(self) -> None:
        """Require a complete replacement after an indeterminate write path."""
        self._persisted_position_codes = None
        self._persisted_position_snapshot_fingerprint = None
        self._force_position_snapshot = True

    def _adopt_durable_position_codes(self, codes: Iterable[Any]) -> None:
        """Remember durable codes but force the next checkpoint to verify them.

        A restored/reconciled state is authoritative for recovery, but it did
        not pass through this manager's current snapshot transaction.  The
        next checkpoint must therefore issue a complete replacement, including
        deletions, rather than taking the same-code incremental update path.
        """
        self._persisted_position_codes = frozenset(
            str(code or "").strip().upper() for code in codes
        )
        self._persisted_position_snapshot_fingerprint = None
        self._force_position_snapshot = True

    # ==================== 日志管理 (文件存储) ====================

    def append_log(
        self,
        level: str,
        message: str,
        source: str = "strategy",
        timestamp: Optional[datetime] = None,
    ) -> None:
        """追加日志（异步写入文件）"""
        if not self._log_file_path:
            return

        entry = {
            "timestamp": (timestamp or time_utils.now()).isoformat(),
            "level": level,
            "message": message,
            "source": source,
        }
        
        asyncio.create_task(self._write_log_to_file(entry))
        
    async def _write_log_to_file(self, entry: Dict[str, Any]) -> None:
        try:
            line = json.dumps(entry, ensure_ascii=False) + '\n'
            if aiofiles is None:
                def _write_sync() -> None:
                    with open(self._log_file_path, mode='a', encoding='utf-8') as f:
                        f.write(line)

                await asyncio.to_thread(_write_sync)
                return
            async with aiofiles.open(self._log_file_path, mode='a', encoding='utf-8') as f:
                await f.write(line)
        except Exception as e:
            print(f"写入日志文件失败: {e}")

    def get_log_file_path(self) -> Optional[str]:
        """获取当前运行实例的日志文件路径。"""
        return self._log_file_path

    # ==================== 持仓管理 ====================

    def update_position(
        self,
        instrument_code: str,
        **position_data,
    ) -> None:
        """更新持仓（同步）"""
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        positions[instrument_code] = ensure_position_dict(
            instrument_code,
            {
                "instrument_code": instrument_code,
                **position_data,
            },
        )
        self._state["positions"] = positions
        if self._bucket_ledger and not self.requires_bucket_reconciliation():
            self._bucket_ledger.sync_position(instrument_code, positions[instrument_code])
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._mark_positions_dirty()

    def get_position(self, instrument_code: str) -> Optional[Dict[str, Any]]:
        position = self._state.get("positions", {}).get(instrument_code)
        if position is None:
            return None
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        data = ensure_position_dict(instrument_code, position)
        if self._bucket_ledger and not self.requires_bucket_reconciliation():
            data = self._bucket_ledger.decorate_position(instrument_code, data)
        return data

    def get_all_positions(self) -> Dict[str, Dict[str, Any]]:
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = {}
        for code, position in self._state.get("positions", {}).items():
            data = ensure_position_dict(code, position)
            if self._bucket_ledger and not self.requires_bucket_reconciliation():
                data = self._bucket_ledger.decorate_position(code, data)
            positions[code] = data
        return positions

    def settle_trading_day(self, trading_date: date) -> None:
        """Make previous trading-day buys sellable and reset intraday counters."""
        if self.requires_bucket_reconciliation():
            return
        from quantx_domain.trading.portfolio_state import settle_position

        positions = self._state.get("positions", {})
        changed = False
        positions_changed = False
        for code, position in list(positions.items()):
            settled = settle_position(position, trading_date)
            if settled != position:
                positions[code] = settled
                changed = True
                positions_changed = True
        if self._bucket_ledger:
            self._bucket_ledger.settle_trading_day(trading_date)
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            changed = True
        if changed:
            self._state["positions"] = positions
            if self._bucket_ledger:
                for code, position in positions.items():
                    self._bucket_ledger.sync_position(code, position)
                self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            if positions_changed:
                self._mark_positions_dirty()
            else:
                self._mark_dirty()

    def get_available_volume(self, instrument_code: str) -> int:
        position = self.get_position(instrument_code) or {}
        return int(position.get("available_volume", 0) or 0)

    def apply_corporate_action(
        self,
        instrument_code: str,
        *,
        volume_factor: float = 1.0,
        price_factor: Optional[float] = None,
        cash_dividend_per_share: float = 0.0,
        action_id: Optional[str] = None,
        ex_date: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Apply split/bonus-share/dividend adjustments to positions and buckets."""
        if not self._bucket_ledger or not instrument_code:
            return {}

        custom = self._state.setdefault("custom", {})
        applied_actions = custom.setdefault(APPLIED_CORPORATE_ACTIONS_KEY, [])
        if action_id and action_id in applied_actions:
            return {
                "instrument_code": instrument_code,
                "events": [
                    {"event": "corporate_action_skipped", "action_id": action_id}
                ],
            }

        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        position = ensure_position_dict(instrument_code, positions.get(instrument_code))
        cash_dividend = max(0.0, float(cash_dividend_per_share or 0.0))
        dividend_cash = float(position.get("long_volume", 0) or 0) * cash_dividend

        self._bucket_ledger.sync_position(instrument_code, position)
        patch = self._bucket_ledger.apply_corporate_action(
            instrument_code,
            volume_factor=volume_factor,
            price_factor=price_factor,
            cash_dividend_per_share=cash_dividend,
            action_id=action_id,
            ex_date=ex_date,
        )

        buckets = patch.changed_buckets
        total_volume = sum(
            int(data.get("total_volume", 0) or 0) for data in buckets.values()
        )
        total_market_value = sum(
            float(data.get("market_value", 0.0) or 0.0) for data in buckets.values()
        )
        total_cost = sum(
            float(data.get("avg_price", 0.0) or 0.0)
            * int(data.get("total_volume", 0) or 0)
            for data in buckets.values()
        )
        last_price = next(
            (
                float(data.get("last_price", 0.0) or 0.0)
                for data in buckets.values()
                if float(data.get("last_price", 0.0) or 0.0) > 0
            ),
            0.0,
        )
        position.update(
            {
                "long_volume": total_volume,
                "available_volume": sum(
                    int(data.get("available_volume", 0) or 0)
                    for data in buckets.values()
                ),
                "frozen_volume": sum(
                    int(data.get("frozen_volume", 0) or 0)
                    for data in buckets.values()
                ),
                "today_buy_volume": sum(
                    int(data.get("today_buy_volume", 0) or 0)
                    for data in buckets.values()
                ),
                "long_avg_price": total_cost / total_volume if total_volume > 0 else 0.0,
                "last_price": last_price,
                "market_value": total_market_value,
            }
        )
        position["pnl"] = (
            (position["last_price"] - position["long_avg_price"]) * total_volume
            if total_volume > 0
            else 0.0
        )

        if total_volume <= 0 and int(position.get("short_volume", 0) or 0) <= 0:
            positions.pop(instrument_code, None)
        else:
            positions[instrument_code] = position
        self._state["positions"] = positions

        if dividend_cash > 0:
            account = self._state.get("account", {})
            account["cash"] = float(account.get("cash", 0.0) or 0.0) + dividend_cash
            self._state["account"] = account
        if action_id:
            applied_actions.append(action_id)
        self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._recalculate_total_asset()
        self._mark_positions_dirty()
        return patch.to_dict()

    # ==================== 账户管理 ====================

    def update_account(
        self,
        cash: float,
        frozen_cash: float = 0.0,
        total_asset: float = 0.0,
        non_trading_asset: float = 0.0,
    ) -> None:
        """更新账户信息"""
        self._state["account"] = {
            "cash": cash,
            "frozen_cash": frozen_cash,
            "total_asset": total_asset,
            "non_trading_asset": max(0.0, float(non_trading_asset or 0.0)),
        }
        self._mark_dirty()

    def get_account(self) -> Dict[str, float]:
        return self._state.get("account", {}).copy()

    def _sum_market_value(self) -> float:
        positions = self._state.get("positions", {})
        total = 0.0
        for pos in positions.values():
            if isinstance(pos, dict):
                total += float(pos.get("market_value", 0.0))
        return total

    def _recalculate_total_asset(self) -> None:
        account = self._state.get("account", {})
        cash = float(account.get("cash", 0.0))
        frozen_cash = float(account.get("frozen_cash", 0.0))
        non_trading_asset = max(
            0.0, float(account.get("non_trading_asset", 0.0) or 0.0)
        )
        account["total_asset"] = (
            cash + frozen_cash + self._sum_market_value() + non_trading_asset
        )
        self._state["account"] = account

    def get_account_quota(self) -> Dict[str, float]:
        account = self.get_account()
        cash = float(account.get("cash", 0.0))
        frozen_cash = float(account.get("frozen_cash", 0.0))
        cash_total = cash + frozen_cash
        non_trading_asset = max(
            0.0, float(account.get("non_trading_asset", 0.0) or 0.0)
        )
        total_asset = cash_total + self._sum_market_value() + non_trading_asset
        return {
            "available_cash": cash,
            "frozen_cash": frozen_cash,
            "cash_total": cash_total,
            "total_asset": total_asset,
        }

    def get_reserved_amount(self, order_id: str) -> float:
        return float(self._reservations.get(order_id, 0.0))

    def _sync_reservation_state(self) -> None:
        custom = self._state.setdefault("custom", {})
        custom[ORDER_CASH_RESERVATIONS_KEY] = {
            str(order_id): float(amount)
            for order_id, amount in self._reservations.items()
            if float(amount or 0.0) > 0
        }
        custom[ORDER_POSITION_RESERVATIONS_KEY] = {
            str(order_id): {
                str(code): int(volume)
                for code, volume in reservations.items()
                if int(volume or 0) > 0
            }
            for order_id, reservations in self._position_reservations.items()
            if reservations
        }

    def _restore_reservation_state(self) -> None:
        custom = dict(self._state.get("custom") or {})
        cash_value = custom.get(ORDER_CASH_RESERVATIONS_KEY)
        position_value = custom.get(ORDER_POSITION_RESERVATIONS_KEY)
        cash = cash_value if isinstance(cash_value, dict) else {}
        positions = position_value if isinstance(position_value, dict) else {}
        self._reservations = {
            str(order_id): float(amount)
            for order_id, amount in cash.items()
            if float(amount or 0.0) > 0
        }
        self._position_reservations = {
            str(order_id): {
                str(code): int(volume)
                for code, volume in dict(reservations or {}).items()
                if int(volume or 0) > 0
            }
            for order_id, reservations in positions.items()
            if isinstance(reservations, dict)
        }

    def reserve_cash(self, order_id: str, amount: float) -> bool:
        if not self.enable_reserve:
            return False
        amount = float(amount or 0.0)
        if amount <= 0:
            return False

        account = self._state.get("account", {})
        cash = float(account.get("cash", 0.0))
        if cash < amount:
            return False

        account["cash"] = cash - amount
        account["frozen_cash"] = float(account.get("frozen_cash", 0.0)) + amount
        self._state["account"] = account

        self._reservations[order_id] = self._reservations.get(order_id, 0.0) + amount
        self._sync_reservation_state()
        self._recalculate_total_asset()
        self._mark_dirty()
        return True

    def transfer_reservation(self, old_order_id: str, new_order_id: str) -> None:
        """Move temporary reservations from intent id to real broker order id."""
        if old_order_id == new_order_id:
            return
        cash_reserved = self._reservations.pop(old_order_id, 0.0)
        if cash_reserved:
            self._reservations[new_order_id] = (
                self._reservations.get(new_order_id, 0.0) + cash_reserved
            )

        position_reserved = self._position_reservations.pop(old_order_id, None)
        if position_reserved:
            target = self._position_reservations.setdefault(new_order_id, {})
            for code, volume in position_reserved.items():
                target[code] = target.get(code, 0) + int(volume or 0)
        if self._bucket_ledger:
            self._bucket_ledger.transfer_order(old_order_id, new_order_id)
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._sync_reservation_state()
        self._mark_dirty()

    def consume_cash_reservation(self, order_id: str, amount: float) -> float:
        """Consume frozen cash for a fill and return any unfunded shortfall."""
        amount = float(amount or 0.0)
        if amount <= 0:
            return 0.0
        reserved = float(self._reservations.get(order_id, 0.0))
        if reserved <= 0:
            return amount

        consumed = min(reserved, amount)
        account = self._state.get("account", {})
        account["frozen_cash"] = max(
            0.0, float(account.get("frozen_cash", 0.0)) - consumed
        )
        self._state["account"] = account

        remaining = reserved - consumed
        if remaining <= 1e-8:
            self._reservations.pop(order_id, None)
        else:
            self._reservations[order_id] = remaining

        self._sync_reservation_state()
        self._recalculate_total_asset()
        self._mark_dirty()
        return max(0.0, amount - consumed)

    def release_cash(self, order_id: str, amount: Optional[float] = None) -> bool:
        if not self.enable_reserve:
            return False
        reserved = float(self._reservations.get(order_id, 0.0))
        if reserved <= 0:
            return False

        release_amount = reserved if amount is None else min(float(amount), reserved)
        if release_amount <= 0:
            return False

        account = self._state.get("account", {})
        account["cash"] = float(account.get("cash", 0.0)) + release_amount
        account["frozen_cash"] = max(
            0.0, float(account.get("frozen_cash", 0.0)) - release_amount
        )
        self._state["account"] = account

        remaining = reserved - release_amount
        if remaining <= 0:
            self._reservations.pop(order_id, None)
        else:
            self._reservations[order_id] = remaining

        self._sync_reservation_state()
        self._recalculate_total_asset()
        self._mark_dirty()
        return True

    def reserve_position(
        self, order_id: str, instrument_code: str, volume: int
    ) -> bool:
        volume = int(volume or 0)
        if volume <= 0:
            return False

        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        position = ensure_position_dict(instrument_code, positions.get(instrument_code))
        available = int(position.get("available_volume", 0) or 0)
        if available < volume:
            return False

        position["available_volume"] = available - volume
        position["frozen_volume"] = int(position.get("frozen_volume", 0) or 0) + volume
        positions[instrument_code] = position
        self._state["positions"] = positions

        reserved = self._position_reservations.setdefault(order_id, {})
        reserved[instrument_code] = reserved.get(instrument_code, 0) + volume
        self._sync_reservation_state()
        self._mark_positions_dirty()
        return True

    def release_position(
        self,
        order_id: str,
        instrument_code: Optional[str] = None,
        volume: Optional[int] = None,
    ) -> bool:
        reserved = self._position_reservations.get(order_id)
        if not reserved:
            return False

        from quantx_domain.trading.portfolio_state import ensure_position_dict

        positions = self._state.get("positions", {})
        changed = False
        codes = [instrument_code] if instrument_code else list(reserved.keys())
        for code in codes:
            if not code or code not in reserved:
                continue
            release_volume = (
                reserved[code] if volume is None else min(int(volume or 0), reserved[code])
            )
            if release_volume <= 0:
                continue
            position = ensure_position_dict(code, positions.get(code))
            position["frozen_volume"] = max(
                0, int(position.get("frozen_volume", 0) or 0) - release_volume
            )
            position["available_volume"] = int(position.get("available_volume", 0) or 0) + release_volume
            positions[code] = position

            remaining = reserved[code] - release_volume
            if remaining <= 0:
                reserved.pop(code, None)
            else:
                reserved[code] = remaining
            changed = True

        if not reserved:
            self._position_reservations.pop(order_id, None)
        if changed:
            self._state["positions"] = positions
            self._sync_reservation_state()
            self._mark_positions_dirty()
        return changed

    def consume_position_reservation(
        self, order_id: str, instrument_code: str, volume: int
    ) -> int:
        """Consume frozen shares for a sell fill and return unreserved volume."""
        volume = int(volume or 0)
        if volume <= 0:
            return 0
        reserved = self._position_reservations.get(order_id, {})
        reserved_volume = int(reserved.get(instrument_code, 0) or 0)
        consumed = min(volume, reserved_volume)
        if consumed > 0:
            from quantx_domain.trading.portfolio_state import ensure_position_dict

            positions = self._state.get("positions", {})
            position = ensure_position_dict(instrument_code, positions.get(instrument_code))
            position["frozen_volume"] = max(
                0, int(position.get("frozen_volume", 0) or 0) - consumed
            )
            positions[instrument_code] = position
            self._state["positions"] = positions

            remaining = reserved_volume - consumed
            if remaining <= 0:
                reserved.pop(instrument_code, None)
            else:
                reserved[instrument_code] = remaining
            if not reserved and order_id in self._position_reservations:
                self._position_reservations.pop(order_id, None)
            self._sync_reservation_state()
            self._mark_positions_dirty()
        return max(0, volume - consumed)

    def release_order_resources(self, order_id: str) -> None:
        self.release_cash(order_id)
        self.release_position(order_id)
        if self._bucket_ledger:
            self._bucket_ledger.rollback_order(order_id, reason="order_released")
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            self._mark_dirty()

    def reserve_bucket_order(self, order_id: str, request) -> bool:
        """Reserve bucket inventory for an order request and store pending metadata."""
        if not self._bucket_ledger or not order_id or not request:
            return False
        metadata = dict(getattr(request, "metadata", {}) or {})
        plan = metadata.get("substitution_plan")
        ok = self._bucket_ledger.reserve_order(
            order_id,
            instrument_code=str(getattr(request, "instrument_code", "") or ""),
            order_type=getattr(request, "order_type", None),
            bucket=str(metadata.get("bucket", "core") or "core"),
            volume=int(getattr(request, "volume", 0) or 0),
            price=float(getattr(request, "price", 0.0) or 0.0),
            metadata=metadata,
            substitution_plan=plan,
        )
        if ok:
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
            self._mark_dirty()
        return ok

    def seed_bucket_positions(
        self,
        instrument_code: str,
        bucket_states: Dict[str, Dict[str, Any]],
    ) -> None:
        """Seed initial bucket attribution for an instrument."""
        if not self._bucket_ledger or not instrument_code:
            return
        self._bucket_ledger.set_instrument_buckets(instrument_code, bucket_states)
        self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._mark_dirty()

    def reconciliation_status(self) -> str:
        """Return the runtime-local reconciliation gate status."""
        custom = self._state.get("custom", {})
        if custom.get(BUCKET_LEDGER_RECONCILE_REQUIRED_KEY):
            return "RECONCILE_REQUIRED"
        status = str(custom.get(RUNTIME_RECONCILIATION_STATUS_KEY, "") or "").upper()
        if status:
            return status
        return "READY"

    def requires_reconciliation(self) -> bool:
        """Whether new trade decisions must wait for authoritative reconcile."""
        return bool(self.market_continuity_reconciliation()) or (
            self.requires_bucket_reconciliation()
        )

    def requires_bucket_reconciliation(self) -> bool:
        """Whether bucket attribution is unsafe to update from positions."""
        return self.reconciliation_status() != "READY"

    def _adopt_restored_bucket_ledger(
        self,
        positions: Dict[str, Dict[str, Any]],
        *,
        mark_dirty: bool,
    ) -> tuple[bool, bool]:
        """Validate restored ledger without overwriting conflicting positions."""
        if not self._bucket_ledger:
            return self.requires_bucket_reconciliation(), False

        violations = self._bucket_ledger.validate_invariants(positions)
        custom = self._state.setdefault("custom", {})
        if violations:
            changed = (
                custom.get(RUNTIME_RECONCILIATION_STATUS_KEY)
                != "RECONCILE_REQUIRED"
                or custom.get(RUNTIME_RECONCILIATION_REASON_KEY)
                != "BUCKET_LEDGER_INVARIANT_BROKEN"
                or custom.get(BUCKET_LEDGER_RECONCILE_REQUIRED_KEY) is not True
                or custom.get(BUCKET_LEDGER_VIOLATIONS_KEY) != violations
            )
            custom[RUNTIME_RECONCILIATION_STATUS_KEY] = "RECONCILE_REQUIRED"
            custom[RUNTIME_RECONCILIATION_REASON_KEY] = (
                "BUCKET_LEDGER_INVARIANT_BROKEN"
            )
            custom[BUCKET_LEDGER_RECONCILE_REQUIRED_KEY] = True
            custom[BUCKET_LEDGER_VIOLATIONS_KEY] = violations
            if changed and mark_dirty:
                self._mark_dirty()
            return True, changed

        # This helper is invoked only after atomically reading the authoritative
        # state row and its position rows. A now-consistent snapshot is the
        # durable proof that can clear a previously persisted recovery gate.
        # These four keys are manager-owned exclusively by the bucket
        # invariant recovery path, so clearing them cannot release another
        # reconciliation subsystem's gate.
        reconciliation_keys = {
            RUNTIME_RECONCILIATION_STATUS_KEY,
            RUNTIME_RECONCILIATION_REASON_KEY,
            BUCKET_LEDGER_RECONCILE_REQUIRED_KEY,
            BUCKET_LEDGER_VIOLATIONS_KEY,
        }
        cleared = any(key in custom for key in reconciliation_keys)
        for key in reconciliation_keys:
            custom.pop(key, None)
        if cleared and mark_dirty:
            self._mark_dirty()

        self._hydrate_positions_from_bucket_ledger()
        return False, cleared

    def get_bucket_ledger_snapshot(self) -> Dict[str, Any]:
        if not self._bucket_ledger:
            return {}
        if not self.requires_bucket_reconciliation():
            for code, position in self._state.get("positions", {}).items():
                self._bucket_ledger.sync_position(code, position)
        return self._bucket_ledger.to_dict()

    def _hydrate_positions_from_bucket_ledger(self) -> None:
        if not self._bucket_ledger:
            return
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        snapshot = self._bucket_ledger.to_dict()
        positions = self._state.get("positions", {})
        for code, buckets in dict(snapshot.get("instruments", {}) or {}).items():
            position = ensure_position_dict(str(code), positions.get(code))
            total_volume = sum(
                int(data.get("total_volume", 0) or 0) for data in buckets.values()
            )
            total_market_value = sum(
                float(data.get("market_value", 0.0) or 0.0)
                for data in buckets.values()
            )
            total_cost = sum(
                float(data.get("avg_price", 0.0) or 0.0)
                * int(data.get("total_volume", 0) or 0)
                for data in buckets.values()
            )
            position.update(
                {
                    "long_volume": total_volume,
                    "available_volume": sum(
                        int(data.get("available_volume", 0) or 0)
                        for data in buckets.values()
                    ),
                    "frozen_volume": sum(
                        int(data.get("frozen_volume", 0) or 0)
                        for data in buckets.values()
                    ),
                    "today_buy_volume": sum(
                        int(data.get("today_buy_volume", 0) or 0)
                        for data in buckets.values()
                    ),
                    "long_avg_price": (
                        total_cost / total_volume if total_volume > 0 else 0.0
                    ),
                    "market_value": total_market_value,
                }
            )
            last_price = next(
                (
                    float(data.get("last_price", 0.0) or 0.0)
                    for data in buckets.values()
                    if float(data.get("last_price", 0.0) or 0.0) > 0
                ),
                float(position.get("last_price", 0.0) or 0.0),
            )
            position["last_price"] = last_price
            positions[str(code)] = position
        self._state["positions"] = positions

    def record_decision_trace(self, trace) -> None:
        if not trace:
            return
        if self.persist_enabled:
            # Do not create one independent commit task per decision.  The
            # next causal RuntimeState checkpoint (or final stop checkpoint)
            # appends this immutable record in the same transaction as the CAS.
            # Memory/backtest publication also waits for that same proof: a CAS
            # loser must not leak into a JSONL manifest or in-memory audit.
            self._pending_decision_trace_records.append(
                self._decision_trace_record_data(trace)
            )
        else:
            self._publish_non_durable_decision_trace(trace)
        self._mark_dirty()

    def _publish_non_durable_decision_trace(self, trace) -> None:
        """Publish immediately only when no durable CAS boundary is enabled."""

        if self._decision_trace_logger:
            self._decision_trace_logger.record(trace)
            traces = self._decision_trace_logger.to_list()
            self._state["decision_traces"] = traces[-500:]
        if self._backtest_storage and hasattr(self._backtest_storage, "add_trace"):
            self._backtest_storage.add_trace(trace.to_dict())

    def _publish_durable_decision_trace_record(self, record: Mapping[str, Any]) -> None:
        """Publish an audit only after its relational CAS transaction is proven."""

        trace_payload = self._decision_trace_presentation_payload(record)
        if self._decision_trace_logger:
            try:
                from quantx_domain.trading.decision_trace import DecisionTrace

                timestamp = trace_payload.get("timestamp")
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp)
                if not isinstance(timestamp, datetime):
                    timestamp = time_utils.now()
                trace = DecisionTrace(
                    trace_id=str(
                        trace_payload.get("trace_id") or record.get("trace_id") or ""
                    ),
                    run_id=str(
                        trace_payload.get("run_id")
                        or record.get("strategy_run_id")
                        or self.run_id
                    ),
                    strategy_id=str(trace_payload.get("strategy_id") or ""),
                    instrument_code=str(
                        trace_payload.get("instrument_code")
                        or record.get("instrument_code")
                        or ""
                    ),
                    timestamp=timestamp,
                    input_summary=dict(trace_payload.get("input_summary") or {}),
                    environment=dict(trace_payload.get("environment") or {}),
                    risk_caps=dict(trace_payload.get("risk_caps") or {}),
                    position_profile=dict(trace_payload.get("position_profile") or {}),
                    execution_profile=dict(trace_payload.get("execution_profile") or {}),
                    output_summary=dict(trace_payload.get("output_summary") or {}),
                    state_patch=dict(trace_payload.get("state_patch") or {}),
                    trade_intents=list(trace_payload.get("trade_intents") or []),
                    order_draft=dict(trace_payload.get("order_draft") or {}),
                    order_request=dict(trace_payload.get("order_request") or {}),
                    risk_decision=dict(trace_payload.get("risk_decision") or {}),
                    broker_report=dict(trace_payload.get("broker_report") or {}),
                    tags=list(trace_payload.get("tags") or []),
                    reason=str(trace_payload.get("reason") or ""),
                )
                self._decision_trace_logger.record(trace)
                self._state["decision_traces"] = (
                    self._decision_trace_logger.to_list()[-500:]
                )
            except Exception as exc:
                self.logger.error(
                    "已提交决策审计无法发布到内存日志: run_id=%s trace_id=%s error=%s",
                    self.run_id,
                    record.get("id"),
                    exc,
                )
        if self._backtest_storage and hasattr(self._backtest_storage, "add_trace"):
            try:
                self._backtest_storage.add_trace(copy.deepcopy(trace_payload))
            except Exception as exc:
                self.logger.error(
                    "已提交决策审计无法发布到回测存储: run_id=%s trace_id=%s error=%s",
                    self.run_id,
                    record.get("id"),
                    exc,
                )

    def _acknowledge_pending_decision_trace_records(
        self,
        trace_ids: Iterable[str],
    ) -> None:
        """Remove only records proven durable by the owning transaction.

        ``record_decision_trace`` can run while an async commit has yielded.
        Filtering by the captured stable UUIDs therefore leaves any newer
        trace queued for its own causal checkpoint.
        """

        persisted_ids = {str(item) for item in trace_ids if item}
        if not persisted_ids:
            return
        records_to_publish = [
            item
            for item in self._pending_decision_trace_records
            if str(item.get("id") or "") in persisted_ids
        ]
        for record in records_to_publish:
            self._publish_durable_decision_trace_record(record)
        self._pending_decision_trace_records = [
            item
            for item in self._pending_decision_trace_records
            if str(item.get("id") or "") not in persisted_ids
        ]

    def _discard_pending_decision_trace_records(
        self,
        trace_ids: Iterable[str],
    ) -> None:
        """Drop records that belong to a rejected CAS generation only.

        A CAS conflict occurs before ``append_traces`` is reached, so those
        captured records have no durable counterpart and cannot be retried on
        top of the concurrent winner.  UUID filtering preserves records added
        while the failed snapshot yielded to the event loop.
        """

        discarded_ids = {str(item) for item in trace_ids if item}
        if not discarded_ids:
            return
        self._pending_decision_trace_records = [
            item
            for item in self._pending_decision_trace_records
            if str(item.get("id") or "") not in discarded_ids
        ]

    def _decision_trace_presentation_payload(
        self,
        record: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Rebuild the durable trace's display shape from its split columns."""

        supplemental = dict(record.get("decision_trace") or {})
        timestamp = record.get("decided_at")
        if isinstance(timestamp, str):
            try:
                timestamp = datetime.fromisoformat(timestamp)
            except ValueError:
                timestamp = None
        if not isinstance(timestamp, datetime):
            timestamp = time_utils.now()

        return {
            "_type": "decision_trace",
            "trace_id": str(record.get("trace_id") or ""),
            "run_id": str(record.get("strategy_run_id") or self.run_id),
            "strategy_id": str(record.get("strategy_id") or ""),
            "instrument_code": str(record.get("instrument_code") or ""),
            "timestamp": timestamp.isoformat(),
            "input_summary": copy.deepcopy(
                dict(record.get("input_summary") or {})
            ),
            "environment": copy.deepcopy(
                dict(supplemental.get("environment") or {})
            ),
            "risk_caps": copy.deepcopy(
                dict(supplemental.get("risk_caps") or {})
            ),
            "position_profile": copy.deepcopy(
                dict(supplemental.get("position_profile") or {})
            ),
            "execution_profile": copy.deepcopy(
                dict(supplemental.get("execution_profile") or {})
            ),
            "output_summary": copy.deepcopy(
                dict(record.get("output_summary") or {})
            ),
            "state_patch": copy.deepcopy(
                dict(record.get("state_patch") or {})
            ),
            "trade_intents": copy.deepcopy(
                list(record.get("trade_intents") or [])
            ),
            "order_draft": copy.deepcopy(
                dict(supplemental.get("order_draft") or {})
            ),
            "order_request": copy.deepcopy(
                dict(supplemental.get("order_request") or {})
            ),
            "risk_decision": copy.deepcopy(
                dict(supplemental.get("risk_decision") or {})
            ),
            "broker_report": copy.deepcopy(
                dict(supplemental.get("broker_report") or {})
            ),
            "tags": copy.deepcopy(list(supplemental.get("tags") or [])),
            "reason": str(supplemental.get("reason") or ""),
        }

    def _decision_trace_record_data(self, trace) -> Dict[str, Any]:
        trace_dict = trace.to_dict() if hasattr(trace, "to_dict") else dict(trace or {})
        decided_at = getattr(trace, "timestamp", None)
        if decided_at is None:
            decided_at = time_utils.now()
        input_summary = dict(getattr(trace, "input_summary", {}) or trace_dict.get("input_summary") or {})
        output_summary = dict(getattr(trace, "output_summary", {}) or trace_dict.get("output_summary") or {})
        state_patch = dict(getattr(trace, "state_patch", {}) or trace_dict.get("state_patch") or {})
        trade_intents = list(getattr(trace, "trade_intents", []) or trace_dict.get("trade_intents") or [])
        supplemental = {
            "format": _DECISION_TRACE_SUPPLEMENTAL_FORMAT,
            "environment": dict(trace_dict.get("environment") or {}),
            "risk_caps": dict(trace_dict.get("risk_caps") or {}),
            "position_profile": dict(trace_dict.get("position_profile") or {}),
            "execution_profile": dict(trace_dict.get("execution_profile") or {}),
            "order_draft": dict(trace_dict.get("order_draft") or {}),
            "order_request": dict(trace_dict.get("order_request") or {}),
            "risk_decision": dict(trace_dict.get("risk_decision") or {}),
            "broker_report": dict(trace_dict.get("broker_report") or {}),
            "tags": list(trace_dict.get("tags") or []),
            "reason": str(trace_dict.get("reason") or ""),
        }
        return {
            "id": str(uuid.uuid4()),
            "trace_id": str(getattr(trace, "trace_id", None) or trace_dict.get("trace_id") or uuid.uuid4()),
            "strategy_run_id": str(getattr(trace, "run_id", None) or trace_dict.get("run_id") or self.run_id),
            "strategy_id": str(getattr(trace, "strategy_id", None) or trace_dict.get("strategy_id") or ""),
            "instrument_code": str(getattr(trace, "instrument_code", None) or trace_dict.get("instrument_code") or ""),
            "decided_at": decided_at,
            "input_summary": _json_safe(input_summary),
            "output_summary": _json_safe(output_summary),
            "trade_intents": _json_safe(trade_intents),
            "state_patch": _json_safe(state_patch),
            "decision_trace": _json_safe(supplemental),
        }

    async def record_trade_intent(self, intent, status: str = "PENDING") -> None:
        """Persist a TradeIntent snapshot before it enters sizing/risk routing."""
        data = self._trade_intent_record_data(intent, status=status)
        intent_id = data["id"]
        self._cache_trade_intent(data, prune_terminal=False)
        if self.persist_enabled:
            self._unpersisted_trade_intent_ids.add(intent_id)
        if self._backtest_storage and hasattr(self._backtest_storage, "add_trade_intent"):
            self._backtest_storage.add_trade_intent(dict(data))
        persisted = await self._upsert_trade_intent_record(data, create_only=True)
        if persisted:
            self._unpersisted_trade_intent_ids.discard(intent_id)
            self._prune_terminal_trade_intent_cache(
                self._state.setdefault("trade_intents", {})
            )
        self._mark_dirty()

    async def record_trade_intent_strict(
        self,
        intent,
        status: str = "PENDING",
    ) -> None:
        """Persist an intent or raise before exposing it to runtime consumers.

        The ordinary recorder retains its historical best-effort behaviour for
        existing strategies. Stateful opportunity candidates use this strict
        boundary so a failed database append cannot become an in-memory manual
        approval that disappears on restart.
        """

        data = self._trade_intent_record_data(intent, status=status)
        await self._upsert_trade_intent_record_strict(data, create_only=True)
        self._unpersisted_trade_intent_ids.discard(data["id"])
        self._cache_trade_intent(data)
        if self._backtest_storage and hasattr(
            self._backtest_storage,
            "add_trade_intent",
        ):
            self._backtest_storage.add_trade_intent(dict(data))
        self._mark_dirty()

    async def update_trade_intent_status(
        self, intent_id: Optional[str], status: str, **updates: Any
    ) -> None:
        """Update a persisted TradeIntent lifecycle status."""
        if not intent_id:
            return
        existing = await self._trade_intent_update_base(
            intent_id,
            strict=False,
        )
        if existing is None:
            return
        existing.setdefault("id", intent_id)
        existing["status"] = status
        accumulate_executed_volume = bool(updates.pop("accumulate_executed_volume", False))
        if accumulate_executed_volume and updates.get("executed_volume") is not None:
            previous_volume = int(existing.get("executed_volume", 0) or 0)
            fill_volume = int(updates.get("executed_volume", 0) or 0)
            total_volume = previous_volume + fill_volume
            previous_price = float(existing.get("executed_price", 0.0) or 0.0)
            fill_price = float(updates.get("executed_price", 0.0) or 0.0)
            if total_volume > 0:
                if previous_volume > 0 and previous_price > 0 and fill_price > 0:
                    updates["executed_price"] = (
                        previous_price * previous_volume + fill_price * fill_volume
                    ) / total_volume
                elif fill_price <= 0:
                    updates["executed_price"] = previous_price
            updates["executed_volume"] = total_volume
        existing.update({key: value for key, value in updates.items() if value is not None})
        if self.persist_enabled:
            self._unpersisted_trade_intent_ids.add(intent_id)
        self._cache_trade_intent(existing, prune_terminal=False)
        if self._backtest_storage and hasattr(self._backtest_storage, "add_trade_intent"):
            self._backtest_storage.add_trade_intent(dict(existing))
        persisted = await self._upsert_trade_intent_record(existing)
        if persisted:
            self._unpersisted_trade_intent_ids.discard(intent_id)
            self._prune_terminal_trade_intent_cache(
                self._state.setdefault("trade_intents", {})
            )
        self._mark_dirty()

    async def update_trade_intent_status_strict(
        self,
        intent_id: Optional[str],
        status: str,
        **updates: Any,
    ) -> None:
        """Durably advance an intent status before changing runtime truth."""

        if not intent_id:
            raise ValueError("交易意图标识不能为空")
        existing = await self._trade_intent_update_base(
            intent_id,
            strict=True,
        )
        if existing is None:  # pragma: no cover - strict loader always raises
            raise RuntimeStateRestoreError(
                f"交易意图持久化快照不可用: intent_id={intent_id}"
            )
        existing.setdefault("id", intent_id)
        existing["status"] = status
        if updates.pop("accumulate_executed_volume", False):
            previous_volume = int(existing.get("executed_volume", 0) or 0)
            fill_volume = int(updates.get("executed_volume", 0) or 0)
            total_volume = previous_volume + fill_volume
            previous_price = float(existing.get("executed_price", 0.0) or 0.0)
            fill_price = float(updates.get("executed_price", 0.0) or 0.0)
            if total_volume > 0:
                if previous_volume > 0 and previous_price > 0 and fill_price > 0:
                    updates["executed_price"] = (
                        previous_price * previous_volume + fill_price * fill_volume
                    ) / total_volume
                elif fill_price <= 0:
                    updates["executed_price"] = previous_price
            updates["executed_volume"] = total_volume
        existing.update(
            {key: value for key, value in updates.items() if value is not None}
        )
        await self._upsert_trade_intent_record_strict(existing)
        self._unpersisted_trade_intent_ids.discard(intent_id)
        self._cache_trade_intent(existing)
        if self._backtest_storage and hasattr(
            self._backtest_storage,
            "add_trade_intent",
        ):
            self._backtest_storage.add_trade_intent(dict(existing))
        self._mark_dirty()

    async def _trade_intent_update_base(
        self,
        intent_id: str,
        *,
        strict: bool,
    ) -> Optional[Dict[str, Any]]:
        """Load complete lifecycle truth when the bounded cache missed.

        A terminal record can legitimately receive a late broker report after
        LRU eviction.  In persistent runtimes that update must be based on the
        complete database row; synthesizing ``{"id": ...}`` would reset
        metadata and cumulative fill fields.  Ordinary callers keep their
        historical best-effort contract by returning without a write, while
        strict callers fail closed.
        """

        cached = self._state.setdefault("trade_intents", {}).get(intent_id)
        if cached is not None:
            return dict(cached)
        if not self.persist_enabled:
            return {"id": intent_id}

        try:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.repositories.trade_intent_repository import (
                TradeIntentRepository,
            )

            opened_session = False
            async for db in get_async_db():
                opened_session = True
                record = await TradeIntentRepository(db).find_by_id(intent_id)
                if record is None:
                    raise RuntimeStateRestoreError(
                        "交易意图持久化记录不存在: "
                        f"run_id={self.run_id}, intent_id={intent_id}"
                    )
                record_id = str(getattr(record, "id", "") or "").strip()
                record_run_id = str(
                    getattr(record, "strategy_run_id", "") or ""
                ).strip()
                if record_id != intent_id or record_run_id != self.run_id:
                    raise RuntimeStateRestoreError(
                        "交易意图持久化记录所有权不匹配: "
                        f"run_id={self.run_id}, intent_id={intent_id}"
                    )
                snapshot = dict(record.to_dict())
                # ``to_dict`` is API-oriented and serializes datetimes. Keep
                # the ORM value for a later SQLAlchemy update payload.
                snapshot["executed_time"] = getattr(
                    record,
                    "executed_time",
                    None,
                )
                return snapshot
            if not opened_session:
                raise RuntimeStateRestoreError(
                    "交易意图持久化读取未获得数据库会话: "
                    f"run_id={self.run_id}, intent_id={intent_id}"
                )
        except Exception as exc:
            message = (
                "交易意图缓存缺失且持久化快照读取失败，拒绝残缺更新: "
                f"run_id={self.run_id}, intent_id={intent_id}"
            )
            if strict:
                if isinstance(exc, RuntimeStateRestoreError):
                    raise
                raise RuntimeStateRestoreError(message) from exc
            self.logger.error("%s, error=%s", message, exc)
            return None
        return None

    def _cache_trade_intent(
        self,
        data: Dict[str, Any],
        *,
        prune_terminal: bool = True,
    ) -> None:
        """Keep active intents and only a bounded LRU of durable history.

        ``strategy_trade_intents`` (or the backtest event storage) remains the
        historical source of truth.  Runtime memory is needed for in-flight
        lifecycle updates, especially cumulative partial fills, so statuses
        are evicted only when they are explicitly known to be terminal.
        Unknown/new statuses fail safe by remaining resident.
        """

        intent_id = str(data.get("id") or "").strip()
        if not intent_id:
            raise ValueError("交易意图标识不能为空")
        cache = self._state.setdefault("trade_intents", {})
        # Plain dict insertion order is the LRU order. Refresh an intent on
        # every lifecycle write so the just-completed record remains available
        # for immediate reconciliation and cumulative-fill assertions.
        cache.pop(intent_id, None)
        cache[intent_id] = data
        if prune_terminal:
            self._prune_terminal_trade_intent_cache(cache)

    def _prune_terminal_trade_intent_cache(
        self,
        cache: Dict[str, Dict[str, Any]],
    ) -> None:
        terminal_ids = [
            intent_id
            for intent_id, item in cache.items()
            if intent_id not in self._unpersisted_trade_intent_ids
            and str(_enum_value(dict(item or {}).get("status")) or "")
            .strip()
            .upper()
            in _TERMINAL_TRADE_INTENT_STATUSES
        ]
        excess = len(terminal_ids) - _MAX_TERMINAL_TRADE_INTENT_CACHE_ENTRIES
        for intent_id in terminal_ids[: max(0, excess)]:
            cache.pop(intent_id, None)

    async def _upsert_trade_intent_record(
        self,
        data: Dict[str, Any],
        *,
        create_only: bool = False,
    ) -> bool:
        if not self.persist_enabled:
            return True
        try:
            await self._upsert_trade_intent_record_strict(
                data,
                create_only=create_only,
            )
            return True
        except Exception as e:
            self.logger.error(f"交易意图持久化失败: {e}")
            return False

    async def _upsert_trade_intent_record_strict(
        self,
        data: Dict[str, Any],
        *,
        create_only: bool = False,
    ) -> None:
        if not self.persist_enabled:
            return
        from quantx_infrastructure.database.connection import get_async_db
        from quantx_infrastructure.repositories.trade_intent_repository import (
            TradeIntentRepository,
        )

        payload = self._db_trade_intent_payload(data)
        opened_session = False
        async for db in get_async_db():
            opened_session = True
            repo = TradeIntentRepository(db)
            if create_only:
                await repo.create_intent_idempotent(payload)
            else:
                existing = await repo.find_by_id(payload["id"])
                if existing:
                    await repo.update_intent(payload["id"], payload)
                else:
                    await repo.create_intent(payload)
            break
        if not opened_session:
            raise RuntimeError("交易意图数据库会话不可用")

    def _trade_intent_record_data(self, intent, *, status: str) -> Dict[str, Any]:
        metadata = dict(getattr(intent, "metadata", {}) or {})
        origin = getattr(intent, "origin", None)
        origin_type = _enum_value(getattr(origin, "origin_type", "STRATEGY_RUN"))
        if origin_type == "MANUAL_COMMAND":
            strategy_run_id = None
            owner_type = "MANUAL_COMMAND"
            owner_id = str(getattr(origin, "command_id", "") or "")
            metadata.setdefault("manual_action_type", getattr(origin, "action_type", ""))
            metadata.setdefault(
                "liquidation_group_id",
                getattr(origin, "liquidation_group_id", None),
            )
        else:
            strategy_run_id = str(
                getattr(origin, "run_id", None)
                or getattr(intent, "run_id", self.run_id)
                or self.run_id
            )
            owner_type = "STRATEGY_RUN"
            owner_id = strategy_run_id
            metadata.setdefault("plan_id", getattr(origin, "plan_id", None))
        metadata.setdefault("origin_type", origin_type)
        metadata.setdefault(
            "execution_mode", _enum_value(getattr(intent, "execution_mode", "AUTO"))
        )
        metadata.setdefault("approval_ttl_ms", getattr(intent, "approval_ttl_ms", None))
        metadata.setdefault(
            "max_price_deviation_bps",
            getattr(intent, "max_price_deviation_bps", None),
        )
        created_at = getattr(intent, "created_at", None)
        if created_at is not None and hasattr(created_at, "isoformat"):
            metadata.setdefault("intent_created_at", created_at.isoformat())
        return {
            "id": str(getattr(intent, "intent_id", "") or ""),
            "strategy_run_id": strategy_run_id,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "account_id": str(metadata.get("account_id") or "").strip() or None,
            "strategy_id": str(getattr(intent, "strategy_id", "") or ""),
            "instrument_code": str(getattr(intent, "instrument_code", "") or ""),
            "direction": _enum_value(getattr(intent, "direction", "")),
            "bucket": str(getattr(intent, "bucket", "") or "core"),
            "reason": str(getattr(intent, "reason", "") or ""),
            "priority": _enum_value(getattr(intent, "priority", "NORMAL")),
            "intent_type": _enum_value(getattr(intent, "intent_type", None)),
            "confidence": float(getattr(intent, "confidence", 1.0) or 0.0),
            "target_amount": getattr(intent, "target_amount", None),
            "target_position_pct": getattr(intent, "target_position_pct", None),
            "target_volume": getattr(intent, "target_volume", None),
            "limit_price_hint": getattr(intent, "limit_price_hint", None),
            "trace_id": getattr(intent, "trace_id", None),
            "status": status,
            "metadata": metadata,
            "notes": metadata.get("notes"),
        }

    def _db_trade_intent_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "id",
            "strategy_run_id",
            "owner_type",
            "owner_id",
            "account_id",
            "strategy_id",
            "instrument_code",
            "direction",
            "bucket",
            "reason",
            "priority",
            "intent_type",
            "confidence",
            "target_amount",
            "target_position_pct",
            "target_volume",
            "limit_price_hint",
            "trace_id",
            "risk_decision_id",
            "order_id",
            "status",
            "executed_price",
            "executed_volume",
            "executed_time",
            "metadata",
            "notes",
        }
        payload = {key: data.get(key) for key in allowed if key in data}
        payload.setdefault("metadata", {})
        return payload

    def apply_trade(self, trade) -> None:
        """按成交回报更新持仓与资金（策略额度）"""
        from quantx_domain.brokers.base import OrderType
        from quantx_domain.trading.portfolio_state import ensure_position_dict

        instrument_code = getattr(trade, "instrument_code", None)
        if not instrument_code:
            return

        price = float(getattr(trade, "price", 0.0))
        volume = int(getattr(trade, "volume", 0) or 0)
        if volume <= 0:
            return

        amount = float(getattr(trade, "amount", 0.0) or 0.0)
        if amount <= 0 and price > 0:
            amount = price * volume
        commission = float(getattr(trade, "commission", 0.0) or 0.0)
        order_id = str(getattr(trade, "order_id", "") or "")
        order_metadata = {}
        if self._bucket_ledger and order_id:
            order_metadata.update(self._bucket_ledger.pending_metadata(order_id))
        order_metadata.update(dict(getattr(trade, "metadata", {}) or {}))

        positions = self._state.get("positions", {})
        pos = ensure_position_dict(instrument_code, positions.get(instrument_code))

        long_volume = int(pos.get("long_volume", 0))
        short_volume = int(pos.get("short_volume", 0))
        available_volume = int(pos.get("available_volume", 0) or 0)
        long_avg_price = float(pos.get("long_avg_price", 0.0))
        short_avg_price = float(pos.get("short_avg_price", 0.0))

        account = self._state.get("account", {})
        cash = float(account.get("cash", 0.0))

        trade_type = getattr(trade, "trade_type", None)
        if trade_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
            if trade_type == OrderType.BUY_TO_COVER and short_volume > 0:
                cover_volume = min(volume, short_volume)
                short_volume -= cover_volume
            else:
                total_value = long_avg_price * long_volume + price * volume
                long_volume += volume
                long_avg_price = (
                    total_value / long_volume if long_volume > 0 else 0.0
                )
                pos["today_buy_volume"] = int(pos.get("today_buy_volume", 0) or 0) + volume
            shortfall = (
                self.consume_cash_reservation(order_id, amount + commission)
                if order_id
                else amount + commission
            )
            cash -= shortfall
        elif trade_type == OrderType.SELL:
            unreserved_volume = (
                self.consume_position_reservation(order_id, instrument_code, volume)
                if order_id
                else volume
            )
            reserved_consumed = volume - unreserved_volume
            if reserved_consumed > 0:
                pos["frozen_volume"] = max(
                    0, int(pos.get("frozen_volume", 0) or 0) - reserved_consumed
                )
            sell_volume = min(volume, long_volume)
            long_volume -= sell_volume
            if unreserved_volume > 0:
                available_volume = max(0, available_volume - unreserved_volume)
            cash += amount - commission
        elif trade_type == OrderType.SELL_SHORT:
            total_value = short_avg_price * short_volume + price * volume
            short_volume += volume
            short_avg_price = (
                total_value / short_volume if short_volume > 0 else 0.0
            )
            cash += amount - commission

        pos["long_volume"] = long_volume
        pos["short_volume"] = short_volume
        pos["available_volume"] = min(
            max(0, available_volume),
            max(0, long_volume - int(pos.get("frozen_volume", 0) or 0)),
        )
        pos["long_avg_price"] = long_avg_price
        pos["short_avg_price"] = short_avg_price
        pos["last_price"] = price
        pos["market_value"] = (long_volume - short_volume) * price

        pnl = 0.0
        if long_volume > 0:
            pnl += (price - long_avg_price) * long_volume
        if short_volume > 0:
            pnl += (short_avg_price - price) * short_volume
        pos["pnl"] = pnl

        if long_volume <= 0 and short_volume <= 0 and int(pos.get("frozen_volume", 0) or 0) <= 0:
            positions.pop(instrument_code, None)
        else:
            positions[instrument_code] = pos
        self._state["positions"] = positions

        account["cash"] = cash
        self._state["account"] = account
        self._recalculate_total_asset()
        if self._bucket_ledger:
            self._bucket_ledger.apply_trade(trade, order_metadata)
            self._state["bucket_ledger"] = self.get_bucket_ledger_snapshot()
        self._mark_positions_dirty()

    # ==================== 交易意图管理 ====================

    def set_backtest_mode(
        self,
        backtest_id: str,
        *,
        backtest_version: Optional[int] = None,
    ) -> None:
        """设置为回测模式，初始化文件存储"""
        from quantx_infrastructure.core.backtest_result_storage import (
            BacktestResultStorage,
        )
        self.is_backtest = True
        self.backtest_id = backtest_id
        self._backtest_storage = BacktestResultStorage(
            backtest_id=backtest_id,
            strategy_run_id=self.run_id,
            version=backtest_version,
        )
        self._log_file_path = self._backtest_storage.get_log_file_path()
        self.logger.info(
            f"进入回测模式: backtest_id={backtest_id}, version={backtest_version}"
        )

    async def finalize_backtest(self) -> str:
        """结束回测，将缓冲数据写入文件"""
        if not self._backtest_storage:
            return ""
        path = await self._backtest_storage.flush()
        self.logger.info(f"回测数据已写入: {path}")
        return path

    def get_latest_backtest_grid_book_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取回测期间最后一个 GridBook 快照。"""
        if not self._backtest_storage:
            return None
        return self._backtest_storage.get_latest_grid_book_snapshot()

    def get_backtest_grid_book_snapshot_count(self) -> int:
        """获取回测结果中实际写入的 GridBook 快照数。"""
        if not self._backtest_storage:
            return 0
        return self._backtest_storage.get_grid_book_snapshot_count()

    def get_backtest_grid_book_observed_count(self) -> int:
        """获取回测期间观测到的 GridBook 快照数。"""
        if not self._backtest_storage:
            return 0
        return self._backtest_storage.get_grid_book_observed_count()

    # ==================== 自定义状态（扩展） ====================

    def set_custom(self, key: str, value: Any) -> None:
        """设置策略自定义状态"""
        custom = self._state.get("custom", {})
        custom[key] = value
        self._state["custom"] = custom
        if key == GRID_BOOK_CUSTOM_STATE_KEY and self._backtest_storage:
            self._backtest_storage.add_grid_book_snapshot(dict(value or {}))
        self._mark_dirty()

    def get_custom(self, key: str, default: Any = None) -> Any:
        return self._state.get("custom", {}).get(key, default)

    def get_custom_state(self) -> Dict[str, Any]:
        """获取完整自定义状态"""
        return self._state.get("custom", {}).copy()

    def get_strategy_custom_state(self) -> Dict[str, Any]:
        """Return only strategy-owned state, excluding manager outboxes/gates."""

        return {
            key: copy.deepcopy(value)
            for key, value in dict(self._state.get("custom", {}) or {}).items()
            if key not in _MANAGER_OWNED_CUSTOM_STATE_KEYS
        }

    def update_custom_state(self, updates: Dict[str, Any]) -> None:
        """批量更新自定义状态"""
        if not updates:
            return
        custom = self._state.get("custom", {})
        custom.update(updates)
        self._state["custom"] = custom
        if GRID_BOOK_CUSTOM_STATE_KEY in updates and self._backtest_storage:
            self._backtest_storage.add_grid_book_snapshot(
                dict(updates.get(GRID_BOOK_CUSTOM_STATE_KEY) or {})
            )
        self._mark_dirty()

    def update_strategy_custom_state(
        self,
        updates: Dict[str, Any],
        *,
        full_snapshot: bool = False,
    ) -> None:
        """Merge strategy-owned state without overwriting Engine/API ownership."""

        excluded = _MANAGER_OWNED_CUSTOM_STATE_KEYS
        if full_snapshot:
            excluded = excluded | {GRID_BOOK_CUSTOM_STATE_KEY}
        self.update_custom_state(
            {
                key: copy.deepcopy(value)
                for key, value in dict(updates or {}).items()
                if key not in excluded
            }
        )

    def enqueue_t_trade_material_events(
        self,
        events: Iterable[Dict[str, Any]],
    ) -> None:
        """Append stable MATERIAL events to the manager-owned durable outbox."""

        normalized = self._normalize_t_trade_outbox_items(
            events,
            identity_key="event_key",
        )
        self._ensure_t_trade_materialization_events(normalized)
        if not normalized:
            return
        current = dict(
            self.get_custom(T_TRADE_MATERIAL_EVENT_OUTBOX_KEY, {}) or {}
        )
        for identity, payload in normalized:
            current.setdefault(identity, payload)
        if len(current) > _MAX_T_TRADE_MATERIAL_OUTBOX_EVENTS:
            raise RuntimeError("做 T MATERIAL 持久化发件箱超过 8192 条安全上限")
        self.set_custom(T_TRADE_MATERIAL_EVENT_OUTBOX_KEY, current)

    def pending_t_trade_material_events(self) -> list[Dict[str, Any]]:
        return [
            copy.deepcopy(value)
            for value in dict(
                self.get_custom(T_TRADE_MATERIAL_EVENT_OUTBOX_KEY, {}) or {}
            ).values()
        ]

    def acknowledge_t_trade_material_events(
        self,
        event_keys: Iterable[str],
    ) -> None:
        self._acknowledge_t_trade_outbox_items(
            T_TRADE_MATERIAL_EVENT_OUTBOX_KEY,
            event_keys,
        )

    def enqueue_t_trade_diagnostic_events(
        self,
        events: Iterable[Dict[str, Any]],
    ) -> None:
        """Append diagnostic events for a coordinator-owned durable batch.

        The executor uses this manager-owned outbox for session/day handoff so
        a strategy full-state snapshot can never overwrite an unmaterialized
        diagnostic event before the next explicit CAS boundary.
        """

        normalized = self._normalize_t_trade_outbox_items(
            events,
            identity_key="event_key",
        )
        if not normalized:
            return
        current = dict(
            self.get_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY, {}) or {}
        )
        for identity, payload in normalized:
            current.setdefault(identity, payload)
        if len(current) > _MAX_T_TRADE_DIAGNOSTIC_OUTBOX_EVENTS:
            raise RuntimeError("做 T 诊断持久化发件箱超过 8192 条安全上限")
        self.set_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY, current)

    def pending_t_trade_diagnostic_events(self) -> list[Dict[str, Any]]:
        return [
            copy.deepcopy(value)
            for value in dict(
                self.get_custom(T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY, {}) or {}
            ).values()
        ]

    def acknowledge_t_trade_diagnostic_events(
        self,
        event_keys: Iterable[str],
    ) -> None:
        self._acknowledge_t_trade_outbox_items(
            T_TRADE_DIAGNOSTIC_EVENT_OUTBOX_KEY,
            event_keys,
        )

    def enqueue_t_trade_paper_fill_fact(self, fact: Dict[str, Any]) -> None:
        normalized = self._normalize_t_trade_outbox_items(
            [fact],
            identity_key="fact_key",
        )
        current = dict(self.get_custom(T_TRADE_PAPER_FILL_OUTBOX_KEY, {}) or {})
        for identity, payload in normalized:
            current.setdefault(identity, payload)
        if len(current) > _MAX_T_TRADE_PAPER_FILL_OUTBOX_FACTS:
            raise RuntimeError("做 T PAPER 成交发件箱超过 128 条安全上限")
        self.set_custom(T_TRADE_PAPER_FILL_OUTBOX_KEY, current)

    def pending_t_trade_paper_fill_facts(self) -> list[Dict[str, Any]]:
        return [
            copy.deepcopy(value)
            for value in dict(
                self.get_custom(T_TRADE_PAPER_FILL_OUTBOX_KEY, {}) or {}
            ).values()
        ]

    def acknowledge_t_trade_paper_fill_facts(
        self,
        fact_keys: Iterable[str],
    ) -> None:
        self._acknowledge_t_trade_outbox_items(
            T_TRADE_PAPER_FILL_OUTBOX_KEY,
            fact_keys,
        )

    @staticmethod
    def _normalize_t_trade_outbox_items(
        items: Iterable[Dict[str, Any]],
        *,
        identity_key: str,
    ) -> list[tuple[str, Dict[str, Any]]]:
        normalized: list[tuple[str, Dict[str, Any]]] = []
        for raw in items:
            if not isinstance(raw, dict):
                raise ValueError("做 T 持久化发件箱项目必须是对象")
            payload = copy.deepcopy(raw)
            identity = str(payload.get(identity_key) or "").strip()
            if not identity:
                raise ValueError(f"做 T 持久化发件箱缺少 {identity_key}")
            try:
                json.dumps(payload, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("做 T 持久化发件箱项目不是有限 JSON") from exc
            normalized.append((identity, payload))
        return normalized

    def _acknowledge_t_trade_outbox_items(
        self,
        key: str,
        identities: Iterable[str],
    ) -> None:
        current = dict(self.get_custom(key, {}) or {})
        changed = False
        for identity in identities:
            normalized = str(identity or "").strip()
            if normalized in current:
                current.pop(normalized, None)
                changed = True
        if changed:
            self.set_custom(key, current)

    def unset_strategy_custom_state(self, keys: Iterable[str]) -> None:
        """Delete explicit strategy-owned keys while preserving manager gates."""

        custom = self._state.setdefault("custom", {})
        changed = False
        for key in keys:
            normalized_key = str(key or "")
            if (
                not normalized_key
                or normalized_key in _MANAGER_OWNED_CUSTOM_STATE_KEYS
            ):
                continue
            if normalized_key in custom:
                custom.pop(normalized_key, None)
                changed = True
        if changed:
            self._mark_dirty()

    def set_custom_state(self, state: Dict[str, Any]) -> None:
        """覆盖自定义状态"""
        self._state["custom"] = dict(state or {})
        self._mark_dirty()

    async def force_save(self) -> bool:
        """强制保存"""
        self._mark_dirty()
        return await self.save_snapshot()


def _enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):
        return getattr(value, "value")
    return value
