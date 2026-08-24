"""Engine-facing persistence and point-in-time profile adapters for T-trade V3."""

from __future__ import annotations

import asyncio
import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
    T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
    T_TRADE_EVALUATION_KIND_MATERIAL,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
    TTradeInstrumentProfileRepository,
    TTradeOpportunityEvaluationRepository,
)

T_TRADE_OPPORTUNITY_EVALUATION_EVENT = "T_TRADE_OPPORTUNITY_EVALUATION"
T_TRADE_PROFILE_SCHEMA_VERSION = "1"
T_TRADE_DIAGNOSTIC_WINDOW_MS = 2_000
# A diagnostic stream is allowed to remain open for one source-time window
# after its last observation.  The value is deliberately independent of wall
# clock time: replaying old data must not keep a live-runtime window alive.
T_TRADE_DIAGNOSTIC_IDLE_MS = T_TRADE_DIAGNOSTIC_WINDOW_MS
# This is a process-wide bound for pending (not yet materialized) windows.  A
# caller may use the constructor override in tests or a separately sized
# runtime, but the default must remain finite.
T_TRADE_MAX_DIAGNOSTIC_WINDOWS = 256
_REQUIRED_PROFILE_FIELDS = frozenset(
    {
        "pullback_threshold_pct",
        "momentum_rise_threshold_pct",
        "momentum_amount_velocity_ratio",
        "pullback_max_spread_ticks",
        "momentum_max_spread_ticks",
    }
)
_BLOCKER_LABELS = {
    "DATA_READY": "数据可用于决策",
    "QUOTE_FRESH": "报价新鲜",
    "CONTINUOUS_SESSION": "连续交易时段",
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
    "INTENT_EMISSION_CONTEXT_MISSING": "发意图门禁上下文缺失",
    "INTENT_EMISSION_NOT_ALLOWED": "当前不允许创建入场意图",
    "UNIVERSE_ELIGIBILITY_UNAVAILABLE": "持仓标的资格不可用",
    "POSITION_NOT_ELIGIBLE": "当前持仓不满足做 T 资格",
    "INSTRUMENT_DRAINING": "标的正在退出监控池",
    "T_TRADE_RECONCILIATION_REQUIRED": "做 T 状态需要对账",
    "ACTIVE_T_BATCH_EXISTS": "已有活跃做 T 批次",
    "INTENT_PENDING": "已有交易意图处理中",
    "COOLDOWN_ACTIVE": "做 T 冷却期尚未结束",
    "ENTRY_CUTOFF_REACHED": "已到新入场截止时间",
}
_BLOCKER_PRIORITY = {
    "CONTINUITY_GENERATION_CHANGED": 0,
    "QUOTE_STALE": 1,
    "REFERENCE_PROFILE_AVAILABLE": 2,
    "REFERENCE_PROFILE_SCHEMA_COMPATIBLE": 2,
    "REFERENCE_PROFILE_CAUSAL": 2,
    "MINIMUM_COVERAGE_NOT_REACHED": 3,
    "ORDER_BOOK_UNAVAILABLE": 4,
    "CUMULATIVE_TURNOVER_UNAVAILABLE": 4,
    "T_TRADE_RECONCILIATION_REQUIRED": 10,
    "INTENT_EMISSION_CONTEXT_MISSING": 11,
    "INTENT_EMISSION_NOT_ALLOWED": 11,
    "UNIVERSE_ELIGIBILITY_UNAVAILABLE": 12,
    "POSITION_NOT_ELIGIBLE": 12,
    "INSTRUMENT_DRAINING": 13,
    "ACTIVE_T_BATCH_EXISTS": 14,
    "INTENT_PENDING": 14,
    "COOLDOWN_ACTIVE": 15,
    "ENTRY_CUTOFF_REACHED": 15,
    "PULLBACK_PATTERN_NOT_CONFIRMED": 30,
    "MOMENTUM_PATTERN_NOT_CONFIRMED": 30,
    "SCORE_UNAVAILABLE": 31,
    "SCORE_BELOW_CANDIDATE": 31,
}


@dataclass
class _PendingDiagnosticWindow:
    """One source-time window waiting for its causally latest diagnostic."""

    account_id: str
    strategy_run_id: str
    instrument_code: str
    window_started_at_ms: int
    latest_event: dict[str, Any]
    latest_source_time_ms: int
    # A receipt must acknowledge source events in their observed order.  A
    # dictionary gives us an insertion-ordered de-duplication set without
    # letting a later retry reshuffle the raw source identities.
    event_keys: dict[str, None] = field(default_factory=dict)
    coalesced_count: int = 0

    @property
    def window_ended_at_ms(self) -> int:
        return int(self.latest_event["evaluated_at_ms"])

    def observe(self, event: dict[str, Any]) -> None:
        event_key = _required_text(event.get("event_key"), "评估事件键")
        if event_key in self.event_keys:
            return
        evaluated_at_ms = _positive_int(event.get("evaluated_at_ms"), "评估时间")
        if evaluated_at_ms < self.window_ended_at_ms:
            raise ValueError("做 T 合并诊断事件发生乱序")
        source_time_ms = _event_source_time_ms(event)
        self.event_keys[event_key] = None
        self.coalesced_count += _diagnostic_source_coalesced_count(event)
        self.latest_event = dict(event)
        self.latest_source_time_ms = max(self.latest_source_time_ms, source_time_ms)

    def materialization_event(self) -> dict[str, Any]:
        event = dict(self.latest_event)
        event.update(
            {
                "event_key": (
                    f"{self.strategy_run_id}:{self.instrument_code}:"
                    f"DIAGNOSTIC:{self.window_started_at_ms}"
                ),
                "record_kind": T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
                "window_started_at_ms": self.window_started_at_ms,
                "window_ended_at_ms": self.window_ended_at_ms,
                "coalesced_count": self.coalesced_count,
            }
        )
        return event


@dataclass(frozen=True)
class CheckpointBatchReceipt:
    """Exact source identities that crossed one durable checkpoint boundary."""

    persisted_event_keys: tuple[str, ...]
    records: tuple[Any, ...] = ()


class TTradeOpportunityRuntimeService:
    """Persist post-CAS evaluation evidence and load prior-only profiles.

    The caller controls ordering: a strategy RuntimeState checkpoint must succeed
    before ``materialize_evaluation`` is invoked.  This adapter deliberately does
    not publish Redis/GraphQL notifications; those are downstream read-model work.
    """

    def __init__(
        self,
        *,
        max_diagnostic_windows: int = T_TRADE_MAX_DIAGNOSTIC_WINDOWS,
        diagnostic_idle_ms: int = T_TRADE_DIAGNOSTIC_IDLE_MS,
    ) -> None:
        if max_diagnostic_windows < 1:
            raise ValueError("max_diagnostic_windows must be positive")
        if diagnostic_idle_ms < 1:
            raise ValueError("diagnostic_idle_ms must be positive")
        self._max_diagnostic_windows = int(max_diagnostic_windows)
        self._diagnostic_idle_ms = int(diagnostic_idle_ms)
        self._diagnostic_windows: dict[
            tuple[str, str, str, int], _PendingDiagnosticWindow
        ] = {}
        self._diagnostic_lock = asyncio.Lock()

    async def materialize_evaluation(
        self,
        *,
        event: dict[str, Any],
        account_id: str,
        strategy_run_id: str,
        repository: Optional[TTradeOpportunityEvaluationRepository] = None,
    ) -> Any:
        record_kind = str(event.get("record_kind") or "").upper()
        if record_kind == T_TRADE_EVALUATION_KIND_DIAGNOSTIC:
            return await self._coalesce_diagnostic(
                event=event,
                account_id=account_id,
                strategy_run_id=strategy_run_id,
                repository=repository,
            )
        if record_kind != T_TRADE_EVALUATION_KIND_MATERIAL:
            raise ValueError("做 T 机会评估 record_kind 无效")

        # A material transition closes any earlier diagnostic source-time window
        # for this stream before the transition itself is appended.
        await self._flush_closed_diagnostic(
            account_id=account_id,
            strategy_run_id=strategy_run_id,
            instrument_code=str(event.get("instrument_code") or "").upper(),
            through_ms=_positive_int(event.get("evaluated_at_ms"), "评估时间"),
            source_time_ms=_event_source_time_ms(event),
            repository=repository,
        )
        normalized = self._normalize_evaluation_event(
            event,
            account_id=account_id,
            strategy_run_id=strategy_run_id,
        )
        if repository is not None:
            return await self._append_evaluation(repository, normalized)

        async for db in get_async_db():
            return await self._append_evaluation(
                TTradeOpportunityEvaluationRepository(db),
                normalized,
            )
        raise RuntimeError("做 T 机会评估数据库会话不可用")

    async def materialize_checkpoint_batch(
        self,
        *,
        events: Sequence[Mapping[str, Any]],
        account_id: str,
        strategy_run_id: str,
    ) -> CheckpointBatchReceipt:
        """Persist one closed checkpoint boundary with a single append-many UoW.

        The batch is deliberately *boundary-owned*: its diagnostics coalesce
        only with other sources supplied here, then every resulting window is
        closed at the checkpoint and written beside any non-actionable
        MATERIAL evidence.  It neither drains nor mutates the service's
        long-lived diagnostic windows, so a failed owned transaction cannot
        consume an unrelated stream or leave a local retry window half moved.
        """

        normalized_account = _required_text(account_id, "证券账户")
        normalized_run = _required_text(strategy_run_id, "策略运行标识")
        normalized_events = self._validated_checkpoint_events(
            events,
            account_id=normalized_account,
            strategy_run_id=normalized_run,
        )
        if not normalized_events:
            return CheckpointBatchReceipt(())

        # ``first_source_index`` is the original source order, not a database
        # ordering accident.  A MATERIAL event terminates only its own
        # instrument's diagnostic segment before the MATERIAL itself.  That
        # prevents diagnostics on the two sides of a MATERIAL transition from
        # being coalesced, even when they share the same two-second window.
        active_segments: dict[
            tuple[str, str, str], tuple[_PendingDiagnosticWindow, int]
        ] = {}
        staged: list[tuple[int, dict[str, Any], tuple[str, ...]]] = []

        def stage_diagnostic_segment(
            pending: _PendingDiagnosticWindow,
            first_source_index: int,
        ) -> None:
            staged.append(
                (
                    first_source_index,
                    self._normalize_evaluation_event(
                        _checkpoint_segment_materialization_event(pending),
                        account_id=normalized_account,
                        strategy_run_id=normalized_run,
                    ),
                    tuple(pending.event_keys),
                )
            )

        for source_index, event in enumerate(normalized_events):
            record_kind = str(event["record_kind"]).upper()
            instrument_code = _required_text(
                event.get("instrument_code"), "证券代码"
            ).upper()
            stream_key = (normalized_account, normalized_run, instrument_code)
            if record_kind == T_TRADE_EVALUATION_KIND_MATERIAL:
                existing = active_segments.pop(stream_key, None)
                if existing is not None:
                    stage_diagnostic_segment(*existing)
                staged.append(
                    (
                        source_index,
                        self._normalize_evaluation_event(
                            event,
                            account_id=normalized_account,
                            strategy_run_id=normalized_run,
                        ),
                        (_required_text(event.get("event_key"), "评估事件键"),),
                    )
                )
                continue

            evaluated_at_ms = _positive_int(event.get("evaluated_at_ms"), "评估时间")
            window_started_at_ms = (
                evaluated_at_ms // T_TRADE_DIAGNOSTIC_WINDOW_MS
            ) * T_TRADE_DIAGNOSTIC_WINDOW_MS
            existing = active_segments.get(stream_key)
            if existing is None:
                pending = _PendingDiagnosticWindow(
                    account_id=normalized_account,
                    strategy_run_id=normalized_run,
                    instrument_code=instrument_code,
                    window_started_at_ms=window_started_at_ms,
                    latest_event=dict(event),
                    latest_source_time_ms=_event_source_time_ms(event),
                )
                pending.observe(event)
                active_segments[stream_key] = (pending, source_index)
            else:
                pending, first_source_index = existing
                if window_started_at_ms < pending.window_started_at_ms:
                    raise ValueError("checkpoint diagnostics must retain source-time order")
                if window_started_at_ms > pending.window_started_at_ms:
                    stage_diagnostic_segment(pending, first_source_index)
                    pending = _PendingDiagnosticWindow(
                        account_id=normalized_account,
                        strategy_run_id=normalized_run,
                        instrument_code=instrument_code,
                        window_started_at_ms=window_started_at_ms,
                        latest_event=dict(event),
                        latest_source_time_ms=_event_source_time_ms(event),
                    )
                    pending.observe(event)
                    active_segments[stream_key] = (pending, source_index)
                else:
                    pending = _copy_diagnostic_window(pending)
                    pending.observe(event)
                    active_segments[stream_key] = (pending, first_source_index)

        for pending, first_source_index in active_segments.values():
            stage_diagnostic_segment(pending, first_source_index)
        staged.sort(key=lambda item: item[0])
        records = [record for _index, record, _source_keys in staged]
        self._validate_checkpoint_record_keys(records)

        # ``append_many`` is the only write path: one fresh multi-row INSERT
        # and one repository-owned commit for diagnostic and MATERIAL records
        # together.  A raise leaves no service-local checkpoint state to undo;
        # callers retain their raw outbox and can retry the exact batch.
        rows = await self._persist_checkpoint_records(records)
        return CheckpointBatchReceipt(
            persisted_event_keys=tuple(
                _required_text(event.get("event_key"), "评估事件键")
                for event in normalized_events
            ),
            records=tuple(rows),
        )

    async def flush_diagnostics(
        self,
        *,
        account_id: str,
        strategy_run_id: str,
        repository: Optional[TTradeOpportunityEvaluationRepository] = None,
    ) -> list[Any]:
        """Flush the final open windows at a deterministic runtime boundary."""

        receipt = await self.flush_diagnostics_with_receipt(
            account_id=account_id,
            strategy_run_id=strategy_run_id,
            repository=repository,
        )
        return list(receipt.records)

    async def flush_diagnostics_with_receipt(
        self,
        *,
        account_id: str,
        strategy_run_id: str,
        repository: Optional[TTradeOpportunityEvaluationRepository] = None,
    ) -> CheckpointBatchReceipt:
        """Flush final windows and identify every raw event now durable."""

        normalized_account = _required_text(account_id, "证券账户")
        normalized_run = _required_text(strategy_run_id, "策略运行标识")
        async with self._diagnostic_lock:
            keys = sorted(
                key
                for key in self._diagnostic_windows
                if key[0] == normalized_account and key[1] == normalized_run
            )
            pending = [self._diagnostic_windows.pop(key) for key in keys]
            if repository is None:
                try:
                    rows = await self._persist_pending_diagnostics(pending)
                except BaseException:
                    # The batch is one transaction.  Either every closed
                    # diagnostic is durable, or all of them remain available
                    # for the caller's retry/terminal cleanup path.
                    self._reinsert_diagnostic_windows(pending)
                    raise
                return CheckpointBatchReceipt(
                    persisted_event_keys=self._pending_diagnostic_event_keys(pending),
                    records=tuple(rows),
                )
            rows: list[Any] = []
            for index, window in enumerate(pending):
                try:
                    rows.append(
                        await self._persist_pending_diagnostic(
                            window,
                            repository=repository,
                        )
                    )
                except BaseException:
                    self._reinsert_diagnostic_windows(pending[index:])
                    raise
            return CheckpointBatchReceipt(
                persisted_event_keys=self._pending_diagnostic_event_keys(pending),
                records=tuple(rows),
            )

    def _validated_checkpoint_events(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        account_id: str,
        strategy_run_id: str,
    ) -> list[dict[str, Any]]:
        """Validate every source before a checkpoint batch touches I/O/state."""

        normalized: list[dict[str, Any]] = []
        source_keys: set[str] = set()
        for raw_event in events:
            if not isinstance(raw_event, Mapping):
                raise ValueError("checkpoint batch event must be a mapping")
            event = dict(raw_event)
            if event.get("type") != T_TRADE_OPPORTUNITY_EVALUATION_EVENT:
                raise ValueError("不是可物化的做 T 机会评估事件")
            event_key = _required_text(event.get("event_key"), "评估事件键")
            if event_key in source_keys:
                raise ValueError("checkpoint batch requires unique evaluation event_key values")
            source_keys.add(event_key)
            for source_field, expected, label in (
                ("account_id", account_id, "证券账户"),
                ("strategy_run_id", strategy_run_id, "策略运行标识"),
            ):
                if (
                    source_field in event
                    and _required_text(event.get(source_field), label) != expected
                ):
                    raise ValueError(
                        f"checkpoint batch {source_field} does not match scope"
                    )

            record_kind = str(event.get("record_kind") or "").upper()
            if record_kind not in {
                T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
                T_TRADE_EVALUATION_KIND_MATERIAL,
            }:
                raise ValueError(
                    "checkpoint batch requires COALESCED_DIAGNOSTIC or MATERIAL events"
                )
            if record_kind == T_TRADE_EVALUATION_KIND_DIAGNOSTIC:
                evaluated_at_ms = _positive_int(event.get("evaluated_at_ms"), "评估时间")
                preview = {
                    **event,
                    "record_kind": T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
                    "window_started_at_ms": (
                        evaluated_at_ms // T_TRADE_DIAGNOSTIC_WINDOW_MS
                    )
                    * T_TRADE_DIAGNOSTIC_WINDOW_MS,
                    "window_ended_at_ms": evaluated_at_ms,
                    "coalesced_count": _diagnostic_source_coalesced_count(event),
                }
                self._normalize_evaluation_event(
                    preview,
                    account_id=account_id,
                    strategy_run_id=strategy_run_id,
                )
            else:
                self._normalize_evaluation_event(
                    event,
                    account_id=account_id,
                    strategy_run_id=strategy_run_id,
                )
            normalized.append(event)
        return normalized

    @staticmethod
    def _validate_checkpoint_record_keys(records: Sequence[Mapping[str, Any]]) -> None:
        durable_keys = [
            _required_text(record.get("event_key"), "评估事件键")
            for record in records
        ]
        if len(set(durable_keys)) != len(durable_keys):
            raise ValueError("checkpoint batch produces duplicate durable event_key values")

    def _stage_diagnostic_event(
        self,
        *,
        event: dict[str, Any],
        account_id: str,
        strategy_run_id: str,
        windows: dict[tuple[str, str, str, int], _PendingDiagnosticWindow],
        pending: list[_PendingDiagnosticWindow],
    ) -> None:
        """Apply one diagnostic to a staging map without crossing I/O."""

        if not isinstance(event, dict) or event.get("type") != (
            T_TRADE_OPPORTUNITY_EVALUATION_EVENT
        ):
            raise ValueError("不是可物化的做 T 机会评估事件")
        instrument_code = _required_text(event.get("instrument_code"), "证券代码").upper()
        evaluated_at_ms = _positive_int(event.get("evaluated_at_ms"), "评估时间")
        source_time_ms = _event_source_time_ms(event)
        window_started_at_ms = (
            evaluated_at_ms // T_TRADE_DIAGNOSTIC_WINDOW_MS
        ) * T_TRADE_DIAGNOSTIC_WINDOW_MS
        stream_prefix = (account_id, strategy_run_id, instrument_code)
        window_key = (*stream_prefix, window_started_at_ms)
        current = windows.get(window_key)
        if current is None:
            candidate = _PendingDiagnosticWindow(
                account_id=account_id,
                strategy_run_id=strategy_run_id,
                instrument_code=instrument_code,
                window_started_at_ms=window_started_at_ms,
                latest_event=dict(event),
                latest_source_time_ms=source_time_ms,
            )
            candidate.observe(event)
        else:
            candidate = _copy_diagnostic_window(current)
            candidate.observe(event)

        closed_keys = {
            key
            for key in windows
            if key[:3] == stream_prefix and key[3] < window_started_at_ms
        }
        inactive_keys = {
            key
            for key, existing in windows.items()
            if key != window_key
            and source_time_ms - existing.latest_source_time_ms
            >= self._diagnostic_idle_ms
        }
        keys_to_persist = closed_keys | inactive_keys
        if window_key not in windows:
            required = len(windows) + 1 - (
                len(keys_to_persist) + self._max_diagnostic_windows
            )
            if required > 0:
                available = [
                    key
                    for key in windows
                    if key != window_key and key not in keys_to_persist
                ]
                available.sort(
                    key=lambda key: self._diagnostic_window_sort_key(windows, key)
                )
                keys_to_persist.update(available[:required])

        selected_keys = sorted(
            keys_to_persist,
            key=lambda key: self._diagnostic_window_sort_key(windows, key),
        )
        pending.extend(windows.pop(key) for key in selected_keys)
        windows[window_key] = candidate

    async def load_reference_profile(
        self,
        *,
        instrument_code: str,
        evaluated_at: datetime,
        required_version: Optional[str] = None,
        repository: Optional[TTradeInstrumentProfileRepository] = None,
    ) -> Optional[dict[str, Any]]:
        """Return the latest complete profile known before the trade date.

        A same-day profile is intentionally invisible even when its ``as_of`` is
        earlier than the current Tick.  This keeps profile construction and
        opportunity replay on the same D-1 information set.
        """

        local_time = time_utils.to_shanghai(evaluated_at)
        cutoff = datetime.combine(local_time.date(), time.min) - timedelta(
            microseconds=1
        )
        if repository is not None:
            row = await repository.latest_at_or_before(
                instrument_code=instrument_code,
                as_of=cutoff,
                schema_version=T_TRADE_PROFILE_SCHEMA_VERSION,
                version=required_version,
            )
        else:
            row = None
            async for db in get_async_db():
                row = await TTradeInstrumentProfileRepository(db).latest_at_or_before(
                    instrument_code=instrument_code,
                    as_of=cutoff,
                    schema_version=T_TRADE_PROFILE_SCHEMA_VERSION,
                    version=required_version,
                )
                break
        if row is None:
            return None
        profile = dict(row.profile or {})
        if not _compatible_profile(profile):
            return None
        profile_as_of = time_utils.to_shanghai(row.as_of).date()
        if profile_as_of >= local_time.date():
            # Repository filtering should already make this impossible. Keep
            # the adapter fail-closed against a malformed fake or future schema.
            return None
        profile.update(
            {
                "profile_version": str(row.version),
                "profile_schema_version": int(row.schema_version),
                "as_of_trade_date": time_utils.to_shanghai(row.as_of)
                .date()
                .isoformat(),
                "profile_fingerprint": str(row.fingerprint),
            }
        )
        return profile

    async def _coalesce_diagnostic(
        self,
        *,
        event: dict[str, Any],
        account_id: str,
        strategy_run_id: str,
        repository: Optional[TTradeOpportunityEvaluationRepository],
    ) -> Any:
        if not isinstance(event, dict) or event.get("type") != (
            T_TRADE_OPPORTUNITY_EVALUATION_EVENT
        ):
            raise ValueError("不是可物化的做 T 机会评估事件")
        normalized_account = _required_text(account_id, "证券账户")
        normalized_run = _required_text(strategy_run_id, "策略运行标识")
        instrument_code = _required_text(
            event.get("instrument_code"), "证券代码"
        ).upper()
        evaluated_at_ms = _positive_int(event.get("evaluated_at_ms"), "评估时间")
        source_time_ms = _event_source_time_ms(event)
        window_started_at_ms = (
            evaluated_at_ms // T_TRADE_DIAGNOSTIC_WINDOW_MS
        ) * T_TRADE_DIAGNOSTIC_WINDOW_MS
        stream_prefix = (normalized_account, normalized_run, instrument_code)
        window_key = (*stream_prefix, window_started_at_ms)
        async with self._diagnostic_lock:
            current = self._diagnostic_windows.get(window_key)
            if current is None:
                candidate = _PendingDiagnosticWindow(
                    account_id=normalized_account,
                    strategy_run_id=normalized_run,
                    instrument_code=instrument_code,
                    window_started_at_ms=window_started_at_ms,
                    latest_event=dict(event),
                    latest_source_time_ms=source_time_ms,
                )
                candidate.observe(event)
            else:
                candidate = _copy_diagnostic_window(current)
                candidate.observe(event)

            closed_keys = {
                key
                for key in self._diagnostic_windows
                if key[:3] == stream_prefix and key[3] < window_started_at_ms
            }
            inactive_keys = {
                key
                for key, pending in self._diagnostic_windows.items()
                if key != window_key
                and source_time_ms - pending.latest_source_time_ms
                >= self._diagnostic_idle_ms
            }
            keys_to_persist = closed_keys | inactive_keys
            if window_key not in self._diagnostic_windows:
                required = len(self._diagnostic_windows) + 1 - (
                    len(keys_to_persist) + self._max_diagnostic_windows
                )
                if required > 0:
                    available = [
                        key
                        for key in self._diagnostic_windows
                        if key != window_key and key not in keys_to_persist
                    ]
                    available.sort(key=self._diagnostic_eviction_sort_key)
                    keys_to_persist.update(available[:required])

            selected_keys = sorted(
                keys_to_persist,
                key=self._diagnostic_eviction_sort_key,
            )
            pending = [self._diagnostic_windows.pop(key) for key in selected_keys]
            if repository is None:
                try:
                    rows = await self._persist_pending_diagnostics(pending)
                except BaseException:
                    # Unlike the caller-owned repository path below, this is
                    # atomic: none of the rows could have committed alone.
                    self._reinsert_diagnostic_windows(pending)
                    raise
                self._diagnostic_windows[window_key] = candidate
                return rows[-1] if rows else None
            last_row = None
            for index, window in enumerate(pending):
                try:
                    last_row = await self._persist_pending_diagnostic(
                        window,
                        repository=repository,
                    )
                except BaseException:
                    # Do not install ``candidate`` until every required
                    # eviction is durable.  Reinsert only the failed and
                    # unattempted windows; successful rows are already safe.
                    self._reinsert_diagnostic_windows(pending[index:])
                    raise

            self._diagnostic_windows[window_key] = candidate
            return last_row

    async def _flush_closed_diagnostic(
        self,
        *,
        account_id: str,
        strategy_run_id: str,
        instrument_code: str,
        through_ms: int,
        source_time_ms: Optional[int],
        repository: Optional[TTradeOpportunityEvaluationRepository],
    ) -> Any:
        stream_prefix = (
            _required_text(account_id, "证券账户"),
            _required_text(strategy_run_id, "策略运行标识"),
            _required_text(instrument_code, "证券代码").upper(),
        )
        async with self._diagnostic_lock:
            closed_keys = {
                key
                for key, pending in self._diagnostic_windows.items()
                if key[:3] == stream_prefix
                and pending.window_started_at_ms + T_TRADE_DIAGNOSTIC_WINDOW_MS
                <= through_ms
            }
            inactive_keys = {
                key
                for key, pending in self._diagnostic_windows.items()
                if source_time_ms is not None
                and source_time_ms - pending.latest_source_time_ms
                >= self._diagnostic_idle_ms
            }
            selected_keys = sorted(
                closed_keys | inactive_keys,
                key=self._diagnostic_eviction_sort_key,
            )
            pending = [self._diagnostic_windows.pop(key) for key in selected_keys]
            if repository is None:
                try:
                    rows = await self._persist_pending_diagnostics(pending)
                except BaseException:
                    self._reinsert_diagnostic_windows(pending)
                    raise
                return rows[-1] if rows else None
            last_row = None
            for index, window in enumerate(pending):
                try:
                    last_row = await self._persist_pending_diagnostic(
                        window,
                        repository=repository,
                    )
                except BaseException:
                    self._reinsert_diagnostic_windows(pending[index:])
                    raise
            return last_row

    def _diagnostic_eviction_sort_key(
        self,
        key: tuple[str, str, str, int],
    ) -> tuple[int, int, tuple[str, str, str, int]]:
        pending = self._diagnostic_windows[key]
        return (
            pending.latest_source_time_ms,
            pending.window_started_at_ms,
            key,
        )

    @staticmethod
    def _diagnostic_window_sort_key(
        windows: Mapping[tuple[str, str, str, int], _PendingDiagnosticWindow],
        key: tuple[str, str, str, int],
    ) -> tuple[int, int, tuple[str, str, str, int]]:
        pending = windows[key]
        return (
            pending.latest_source_time_ms,
            pending.window_started_at_ms,
            key,
        )

    def _reinsert_diagnostic_windows(
        self,
        pending: list[_PendingDiagnosticWindow],
    ) -> None:
        for window in pending:
            key = (
                window.account_id,
                window.strategy_run_id,
                window.instrument_code,
                window.window_started_at_ms,
            )
            self._diagnostic_windows[key] = window

    @staticmethod
    def _pending_diagnostic_event_keys(
        pending: Sequence[_PendingDiagnosticWindow],
        *,
        account_id: Optional[str] = None,
        strategy_run_id: Optional[str] = None,
    ) -> tuple[str, ...]:
        seen: set[str] = set()
        ordered: list[str] = []
        for window in pending:
            if (
                (account_id is not None and window.account_id != account_id)
                or (
                    strategy_run_id is not None
                    and window.strategy_run_id != strategy_run_id
                )
            ):
                continue
            for event_key in window.event_keys:
                if event_key and event_key not in seen:
                    seen.add(event_key)
                    ordered.append(event_key)
        return tuple(ordered)

    async def _persist_pending_diagnostic(
        self,
        pending: _PendingDiagnosticWindow,
        *,
        repository: Optional[TTradeOpportunityEvaluationRepository],
    ) -> Any:
        event = pending.materialization_event()
        normalized = self._normalize_evaluation_event(
            event,
            account_id=pending.account_id,
            strategy_run_id=pending.strategy_run_id,
        )
        if repository is not None:
            return await self._append_evaluation(repository, normalized)
        async for db in get_async_db():
            return await self._append_evaluation(
                TTradeOpportunityEvaluationRepository(db),
                normalized,
            )
        raise RuntimeError("做 T 机会评估数据库会话不可用")

    async def _persist_pending_diagnostics(
        self,
        pending: list[_PendingDiagnosticWindow],
    ) -> list[Any]:
        """Append already-closed diagnostics in one durable transaction.

        A single global source-time advance can close windows for every held
        instrument.  Persisting those independent, already-normalized rows in
        separate sessions made one Tick fan out into N database transactions
        and N idempotency reads. They have no MATERIAL transition between
        them, so one repository-owned batch transaction preserves event keys,
        source-time windows, append-only semantics, and retry behavior without
        that avoidable round-trip fan-out.

        This helper is intentionally used only when the service owns the
        database session.  A supplied repository may belong to a wider caller
        transaction and retains its existing one-by-one behavior.
        """

        records = [
            self._normalize_evaluation_event(
                window.materialization_event(),
                account_id=window.account_id,
                strategy_run_id=window.strategy_run_id,
            )
            for window in pending
        ]
        return await self._persist_checkpoint_records(records)

    async def _persist_checkpoint_records(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> list[Any]:
        """Use exactly one repository-owned append-many transaction."""

        if not records:
            return []
        async for db in get_async_db():
            repository = TTradeOpportunityEvaluationRepository(db)
            # ``append_many`` owns one all-or-nothing transaction and performs
            # one bounded batch reconciliation only after a write uncertainty.
            # Do not reintroduce per-record repository calls here.
            return await repository.append_many(records)
        raise RuntimeError("做 T 机会评估数据库会话不可用")

    @staticmethod
    async def _append_evaluation(
        repository: TTradeOpportunityEvaluationRepository,
        event: dict[str, Any],
        *,
        commit: bool = True,
    ) -> Any:
        common = {
            "event_key": event["event_key"],
            "account_id": event["account_id"],
            "strategy_run_id": event["strategy_run_id"],
            "instrument_code": event["instrument_code"],
            "evaluated_at": event["evaluated_at"],
            "event_type": event["event_type"],
            "policy_version": event["policy_version"],
            "schema_version": event["schema_version"],
            "payload": event["payload"],
            "metrics": event["metrics"],
        }
        if event["record_kind"] == T_TRADE_EVALUATION_KIND_MATERIAL:
            return await repository.append_material(**common, commit=commit)
        return await repository.append_coalesced_diagnostic(
            **common,
            window_started_at=event["window_started_at"],
            window_ended_at=event["window_ended_at"],
            coalesced_count=event["coalesced_count"],
            commit=commit,
        )

    @staticmethod
    def _normalize_evaluation_event(
        event: dict[str, Any],
        *,
        account_id: str,
        strategy_run_id: str,
    ) -> dict[str, Any]:
        if not isinstance(event, dict) or event.get("type") != (
            T_TRADE_OPPORTUNITY_EVALUATION_EVENT
        ):
            raise ValueError("不是可物化的做 T 机会评估事件")
        snapshot = event.get("signal_snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("做 T 机会评估缺少完整 signal_snapshot")
        record_kind = str(event.get("record_kind") or "").upper()
        if record_kind not in {
            T_TRADE_EVALUATION_KIND_MATERIAL,
            T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
        }:
            raise ValueError("做 T 机会评估 record_kind 无效")
        evaluated_at_ms = _positive_int(event.get("evaluated_at_ms"), "评估时间")
        evaluated_at = _from_epoch_ms(evaluated_at_ms)
        external_blockers = event.get("external_blockers")
        normalized_snapshot = dict(snapshot)
        normalized_snapshot["top_blockers"] = _top_blockers(
            normalized_snapshot.get(
                "top_blockers",
                normalized_snapshot.get("blockers"),
            ),
            external_blockers,
        )
        payload = {"signal_snapshot": normalized_snapshot}
        for key in ("transition", "external_blockers", "intent_link"):
            value = event.get(key)
            if value is not None:
                payload[key] = value
        normalized: dict[str, Any] = {
            "event_key": _required_text(event.get("event_key"), "评估事件键"),
            "account_id": _required_text(account_id, "证券账户"),
            "strategy_run_id": _required_text(strategy_run_id, "策略运行标识"),
            "instrument_code": _required_text(
                event.get("instrument_code"), "证券代码"
            ).upper(),
            "evaluated_at": evaluated_at,
            "record_kind": record_kind,
            "event_type": _required_text(event.get("event_type"), "评估事件类型"),
            "policy_version": _required_text(
                snapshot.get("policy_version"), "机会策略版本"
            ),
            "schema_version": str(
                _positive_int(snapshot.get("state_schema_version"), "状态结构版本")
            ),
            "payload": payload,
            "metrics": dict(event.get("metrics") or {}),
            "window_started_at": None,
            "window_ended_at": None,
            "coalesced_count": 1,
        }
        if record_kind == T_TRADE_EVALUATION_KIND_DIAGNOSTIC:
            normalized.update(
                {
                    "window_started_at": _from_epoch_ms(
                        _positive_int(
                            event.get("window_started_at_ms"), "诊断窗口开始时间"
                        )
                    ),
                    "window_ended_at": _from_epoch_ms(
                        _positive_int(
                            event.get("window_ended_at_ms"), "诊断窗口结束时间"
                        )
                    ),
                    "coalesced_count": _positive_int(
                        event.get("coalesced_count"), "诊断合并数量"
                    ),
                }
            )
        return normalized


def _copy_diagnostic_window(
    pending: _PendingDiagnosticWindow,
) -> _PendingDiagnosticWindow:
    return _PendingDiagnosticWindow(
        account_id=pending.account_id,
        strategy_run_id=pending.strategy_run_id,
        instrument_code=pending.instrument_code,
        window_started_at_ms=pending.window_started_at_ms,
        latest_event=dict(pending.latest_event),
        latest_source_time_ms=pending.latest_source_time_ms,
        event_keys=dict(pending.event_keys),
        coalesced_count=pending.coalesced_count,
    )


def _checkpoint_segment_materialization_event(
    pending: _PendingDiagnosticWindow,
) -> dict[str, Any]:
    """Give each checkpoint-local diagnostic segment a replay-stable key.

    A MATERIAL transition can split one two-second source window in two.  The
    legacy window-only diagnostic key would collide in that case, so the
    ordered raw source identities are part of this boundary-specific key.
    """

    source_keys = tuple(pending.event_keys)
    digest = hashlib.sha256("\x1f".join(source_keys).encode("utf-8")).hexdigest()[:16]
    event = pending.materialization_event()
    event["event_key"] = (
        f"{pending.strategy_run_id}:{pending.instrument_code}:"
        f"DIAGNOSTIC:{pending.window_started_at_ms}:SEGMENT:{digest}"
    )
    return event


def _diagnostic_source_coalesced_count(event: Mapping[str, Any]) -> int:
    """Read one checkpoint summary's represented diagnostic observation count."""

    checkpoint_value = event.get("checkpoint_coalesced_count")
    event_value = event.get("coalesced_count")
    if checkpoint_value is None and event_value is None:
        return 1
    if checkpoint_value is not None and event_value is not None:
        checkpoint_count = _positive_int(checkpoint_value, "checkpoint 诊断合并数量")
        event_count = _positive_int(event_value, "诊断合并数量")
        if checkpoint_count != event_count:
            raise ValueError("checkpoint diagnostic coalesced counts disagree")
        return checkpoint_count
    return _positive_int(
        checkpoint_value if checkpoint_value is not None else event_value,
        "checkpoint 诊断合并数量",
    )


def _event_source_time_ms(event: dict[str, Any]) -> int:
    snapshot = event.get("signal_snapshot")
    raw = event.get("source_time_ms")
    if raw is None and isinstance(snapshot, dict):
        raw = snapshot.get("source_time_ms")
    if raw is None:
        raw = event.get("evaluated_at_ms")
    try:
        normalized = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("行情源时间无效") from exc
    if normalized < 0:
        raise ValueError("行情源时间不能小于零")
    return normalized


def _from_epoch_ms(value: int) -> datetime:
    return time_utils.to_shanghai(datetime.fromtimestamp(value / 1000, timezone.utc))


def _positive_int(value: Any, label: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}无效") from exc
    if normalized <= 0:
        raise ValueError(f"{label}必须大于零")
    return normalized


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label}不能为空")
    return normalized


def _top_blockers(market: Any, external: Any) -> list[dict[str, str]]:
    """Merge market and emission blockers into one stable server ranking."""

    ranked: dict[str, tuple[int, int, dict[str, str]]] = {}
    sequence = 0
    for source, values in (
        ("MARKET", _blocker_values(market)),
        ("EXTERNAL_EMISSION", _blocker_values(external)),
    ):
        for raw in values:
            if isinstance(raw, dict):
                code = str(raw.get("code") or "").strip()
                label = str(raw.get("label") or "").strip()
                detail = str(raw.get("detail") or "").strip()
            else:
                code = str(raw or "").strip()
                label = ""
                detail = ""
            if not code:
                continue
            if not label:
                label = _BLOCKER_LABELS.get(code, f"未注册状态（{code}）")
            if not detail and source == "EXTERNAL_EMISSION":
                detail = "外部发意图门禁未通过"
            item = {"code": code, "label": label, "detail": detail}
            priority = _BLOCKER_PRIORITY.get(
                code,
                20 if source == "EXTERNAL_EMISSION" else 40,
            )
            existing = ranked.get(code)
            if existing is None or priority < existing[0]:
                ranked[code] = (priority, sequence, item)
            sequence += 1
    return [
        item
        for _priority, _sequence, item in sorted(
            ranked.values(),
            key=lambda value: (value[0], value[1], value[2]["code"]),
        )
    ]


def _blocker_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError("做 T blocker 必须是列表或结构化对象")


def _compatible_profile(profile: dict[str, Any]) -> bool:
    if not _REQUIRED_PROFILE_FIELDS.issubset(profile):
        return False
    try:
        positive_values = (
            float(profile["pullback_threshold_pct"]),
            float(profile["momentum_rise_threshold_pct"]),
            float(profile["momentum_amount_velocity_ratio"]),
        )
        spread_ticks = (
            int(profile["pullback_max_spread_ticks"]),
            int(profile["momentum_max_spread_ticks"]),
        )
    except (TypeError, ValueError, OverflowError):
        return False
    return all(math.isfinite(value) and value > 0 for value in positive_values) and all(
        value >= 0 for value in spread_ticks
    )


t_trade_opportunity_runtime_service = TTradeOpportunityRuntimeService()


__all__ = [
    "T_TRADE_OPPORTUNITY_EVALUATION_EVENT",
    "T_TRADE_PROFILE_SCHEMA_VERSION",
    "T_TRADE_DIAGNOSTIC_IDLE_MS",
    "T_TRADE_DIAGNOSTIC_WINDOW_MS",
    "T_TRADE_MAX_DIAGNOSTIC_WINDOWS",
    "TTradeOpportunityRuntimeService",
    "t_trade_opportunity_runtime_service",
]
