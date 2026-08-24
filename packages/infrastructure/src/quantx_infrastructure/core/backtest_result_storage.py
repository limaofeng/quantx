"""
回测结果文件存储

负责将回测过程中产生的详细数据（交易意图、订单、成交）
以 JSONL 格式写入文件，避免污染主数据库。
"""

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiofiles

from quantx_infrastructure.core.utils import time_utils

logger = logging.getLogger(__name__)

MAX_DECISION_SUMMARY_RECORDS = 5000
MAX_DECISION_BLOCK_EVENTS = 3
BACKTEST_AUDIT_MODE_ENV = "BACKTEST_AUDIT_MODE"
AUDIT_MODE_EVENTS_ONLY = "events_only"
AUDIT_MODE_COMPACT = "compact"
AUDIT_MODE_FULL = "full"
VALID_AUDIT_MODES = {
    AUDIT_MODE_EVENTS_ONLY,
    AUDIT_MODE_COMPACT,
    AUDIT_MODE_FULL,
}
BACKTEST_AUDIT_COMPACTION_POLICY = "event_rollup_v1"
NON_EVENT_STATE_PATCH_KEYS = {
    "last_trend_state",
    "last_bar_key",
    "last_tick_bar_key",
    "last_processed_bar_key",
    "last_intent_bar_key",
}


@dataclass
class BacktestResultStorage:
    """回测结果文件存储器"""

    backtest_id: str
    result_dir: str = "data/backtests"
    strategy_run_id: Optional[str] = None
    version: Optional[int] = None
    audit_mode: Optional[str] = None

    # 内存缓冲区
    _trade_intents: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _orders: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _trades: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _traces: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _decision_events: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _no_trade_rollups: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _no_trade_rollup_keys: Dict[str, Dict[str, Any]] = field(default_factory=dict, repr=False)
    _logs: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _grid_books: List[Dict[str, Any]] = field(default_factory=list, repr=False)
    _latest_grid_book: Optional[Dict[str, Any]] = field(default=None, repr=False)
    _last_grid_book_signature: Optional[str] = field(default=None, repr=False)
    _grid_book_observed_count: int = field(default=0, repr=False)

    # 文件路径
    _file_path: Optional[str] = field(default=None, repr=False)
    _base_dir: Optional[str] = field(default=None, repr=False)
    _raw_file_path: Optional[str] = field(default=None, repr=False)
    _index_file_path: Optional[str] = field(default=None, repr=False)
    _decision_events_path: Optional[str] = field(default=None, repr=False)
    _no_trade_rollups_path: Optional[str] = field(default=None, repr=False)
    _decision_summary_path: Optional[str] = field(default=None, repr=False)
    _decision_trade_summary_path: Optional[str] = field(default=None, repr=False)
    _execution_summary_path: Optional[str] = field(default=None, repr=False)
    _execution_logs_path: Optional[str] = field(default=None, repr=False)
    _latest_grid_book_path: Optional[str] = field(default=None, repr=False)
    _manifest_path: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        os.makedirs(self.result_dir, exist_ok=True)
        self.audit_mode = self._normalize_audit_mode(self.audit_mode)
        if self.strategy_run_id and self.version:
            self._base_dir = os.path.join(
                self.result_dir,
                str(self.strategy_run_id),
                f"v{int(self.version)}",
            )
            os.makedirs(self._base_dir, exist_ok=True)
            self._manifest_path = os.path.join(self._base_dir, "manifest.json")
            self._raw_file_path = os.path.join(self._base_dir, "raw_trace.jsonl")
            self._index_file_path = os.path.join(self._base_dir, "raw_trace.index.jsonl")
            self._decision_events_path = os.path.join(
                self._base_dir,
                "decision_events.jsonl",
            )
            self._no_trade_rollups_path = os.path.join(
                self._base_dir,
                "no_trade_rollups.jsonl",
            )
            self._decision_summary_path = os.path.join(
                self._base_dir,
                "decision_summary.jsonl",
            )
            self._decision_trade_summary_path = os.path.join(
                self._base_dir,
                "decision_trade_summary.jsonl",
            )
            self._execution_summary_path = os.path.join(
                self._base_dir,
                "execution_summary.jsonl",
            )
            self._execution_logs_path = os.path.join(
                self._base_dir,
                "execution_logs.jsonl",
            )
            self._latest_grid_book_path = os.path.join(
                self._base_dir,
                "latest_grid_book.json",
            )
            self._file_path = self._manifest_path
        else:
            self._file_path = os.path.join(self.result_dir, f"{self.backtest_id}.jsonl")
            self._raw_file_path = self._file_path
            self._execution_logs_path = os.path.join(
                self.result_dir,
                f"{self.backtest_id}.logs.jsonl",
            )
        logger.info(f"BacktestResultStorage initialized: {self._file_path}")

    @staticmethod
    def _normalize_audit_mode(value: Optional[str]) -> str:
        raw = str(value or os.getenv(BACKTEST_AUDIT_MODE_ENV) or AUDIT_MODE_EVENTS_ONLY)
        mode = raw.strip().lower()
        if mode not in VALID_AUDIT_MODES:
            logger.warning("Unknown backtest audit mode %s; fallback to events_only", raw)
            return AUDIT_MODE_EVENTS_ONLY
        return mode

    def add_trade_intent(self, intent: Dict[str, Any]) -> None:
        """添加交易意图到缓冲区"""
        intent["_type"] = "trade_intent"
        intent["_timestamp"] = time_utils.now().isoformat()
        self._trade_intents.append(intent)

    def add_order(self, order: Dict[str, Any]) -> None:
        """添加订单到缓冲区"""
        order["_type"] = "order"
        order["_timestamp"] = time_utils.now().isoformat()
        self._orders.append(order)

    def add_trade(self, trade: Dict[str, Any]) -> None:
        """添加成交到缓冲区"""
        trade["_type"] = "trade"
        trade["_timestamp"] = time_utils.now().isoformat()
        self._trades.append(trade)

    def add_trace(self, trace: Dict[str, Any]) -> None:
        """添加决策审计轨迹到缓冲区"""
        item = dict(trace or {})
        item["_type"] = "decision_trace"
        item["_timestamp"] = time_utils.now().isoformat()
        if not self._manifest_path or self.audit_mode == AUDIT_MODE_FULL:
            self._traces.append(item)
            return

        event = self._decision_event(item)
        if self._is_key_decision_event(item, event):
            self._decision_events.append(event)
            return

        rollup = self._no_trade_rollup(item, event)
        key = str(rollup.pop("_rollup_key"))
        existing = self._no_trade_rollup_keys.get(key)
        if not existing:
            self._no_trade_rollup_keys[key] = rollup
            self._no_trade_rollups.append(rollup)
            return
        existing["count"] = int(existing.get("count", 0) or 0) + 1
        existing["last_time"] = rollup.get("last_time")
        existing["last_trace_id"] = rollup.get("last_trace_id")
        if rollup.get("last_price") is not None:
            existing["last_price"] = rollup.get("last_price")

    def add_log(self, level: str, message: str, source: str = "strategy") -> None:
        """添加日志到缓冲区"""
        record = {
            "_type": "log",
            "_timestamp": time_utils.now().isoformat(),
            "level": level,
            "message": message,
            "source": source,
        }
        if self._execution_logs_path:
            self._append_jsonl_sync(self._execution_logs_path, record)
            return
        self._logs.append(record)

    def add_grid_book_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """添加 GridBook 快照到缓冲区，语义未变化时只保留最新引用。"""
        item = dict(snapshot or {})
        item["_type"] = "grid_book_snapshot"
        item["_timestamp"] = time_utils.now().isoformat()
        self._grid_book_observed_count += 1
        item["_sequence"] = self._grid_book_observed_count
        self._latest_grid_book = item

        signature = self._grid_book_signature(item)
        if signature == self._last_grid_book_signature:
            return

        self._grid_books.append(item)
        self._last_grid_book_signature = signature

    @staticmethod
    def _grid_book_signature(snapshot: Dict[str, Any]) -> str:
        """生成忽略时间戳等易变字段的 GridBook 语义签名。"""
        data = dict(snapshot or {})
        level_keys = [
            "grid_id",
            "level_index",
            "side",
            "role",
            "price",
            "planned_shares",
            "amount",
            "pct_from_base",
            "expected_profit",
            "enabled",
            "status",
            "monitoring",
            "pending_shares",
            "filled_shares",
            "available_inventory_shares",
            "reserved_inventory_shares",
            "cycle_count",
            "waiting_reason",
            "order_id",
            "entry_price",
            "entry_time",
            "last_intent_id",
            "last_trace_id",
            "reason",
        ]
        lot_keys = [
            "lot_id",
            "source_level_id",
            "source_level_index",
            "source",
            "bucket",
            "entry_price",
            "original_shares",
            "remaining_shares",
            "reserved_shares",
            "reserved_for_level_id",
            "reserved_order_id",
            "status",
        ]
        event_keys = [
            "event_id",
            "sell_level_id",
            "sell_level_index",
            "released_level_id",
            "released_level_index",
            "lot_ids",
            "order_id",
            "intent_id",
            "trade_id",
            "price",
            "shares",
        ]

        semantic = {
            "run_id": data.get("run_id"),
            "instrument_code": data.get("instrument_code"),
            "base_price": data.get("base_price"),
            "parameter_version": data.get("parameter_version"),
            "version": data.get("version"),
            "model_version": data.get("model_version"),
            "inventory_model": data.get("inventory_model"),
            "release_rule": data.get("release_rule"),
            "sell_empty_behavior": data.get("sell_empty_behavior"),
            "needs_backtest": data.get("needs_backtest"),
            "reason": data.get("reason"),
            "levels": [
                {key: dict(level or {}).get(key) for key in level_keys}
                for level in list(data.get("levels") or [])
            ],
            "inventory_lots": [
                {key: dict(lot or {}).get(key) for key in lot_keys}
                for lot in list(data.get("inventory_lots") or [])
            ],
            "release_events": [
                {key: dict(event or {}).get(key) for key in event_keys}
                for event in list(data.get("release_events") or [])
            ],
        }
        return json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )

    async def flush(self) -> str:
        """将缓冲区数据写入文件并返回文件路径"""
        if not self._file_path:
            raise ValueError("File path not initialized")

        all_records = []
        all_records.extend(self._trade_intents)
        all_records.extend(self._orders)
        all_records.extend(self._trades)
        all_records.extend(self._traces)
        all_records.extend(self._logs)
        all_records.extend(self._grid_books)

        # 按时间排序
        all_records.sort(key=self._record_timestamp)

        if self._manifest_path:
            self._flush_indexed_layout(all_records)
        else:
            async with aiofiles.open(self._file_path, mode="w", encoding="utf-8") as f:
                for record in all_records:
                    await f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        logger.info(
            f"Backtest results flushed: {len(self._trade_intents)} trade intents, "
            f"{len(self._orders)} orders, {len(self._trades)} trades, "
            f"{len(self._traces)} traces, "
            f"{len(self._logs)} logs, {len(self._grid_books)} grid books "
            f"(from {self._grid_book_observed_count} observed) -> {self._file_path}"
        )

        # 清空缓冲区
        self._trade_intents.clear()
        self._orders.clear()
        self._trades.clear()
        self._traces.clear()
        self._decision_events.clear()
        self._no_trade_rollups.clear()
        self._no_trade_rollup_keys.clear()
        self._logs.clear()
        self._grid_books.clear()
        self._latest_grid_book = None
        self._last_grid_book_signature = None
        self._grid_book_observed_count = 0

        return self._file_path

    def _flush_indexed_layout(self, all_records: List[Dict[str, Any]]) -> None:
        """Write event-driven audit artifacts for indexed backtest snapshots."""
        if not (
            self._manifest_path
            and self._decision_events_path
            and self._no_trade_rollups_path
            and self._execution_summary_path
        ):
            raise ValueError("Indexed backtest artifact paths are not initialized")

        decision_events: List[Dict[str, Any]] = list(self._decision_events)
        no_trade_rollups: List[Dict[str, Any]] = list(self._no_trade_rollups)
        latest_intents: Dict[str, Dict[str, Any]] = {}
        first_seen: Dict[str, str] = {}
        raw_record_count = 0

        if self.audit_mode == AUDIT_MODE_FULL:
            if not self._raw_file_path or not self._index_file_path:
                raise ValueError("Full audit mode requires raw trace paths")
            local_rollup_keys: Dict[str, Dict[str, Any]] = {}
            with open(self._raw_file_path, "wb") as raw_fp, open(
                self._index_file_path,
                "w",
                encoding="utf-8",
            ) as index_fp:
                for record in all_records:
                    raw_record_count += 1
                    offset = raw_fp.tell()
                    line = self._json_line(record)
                    raw_fp.write(line.encode("utf-8"))

                    index_record = self._index_record(record, offset)
                    index_fp.write(self._json_line(index_record))

                    if record.get("_type") == "decision_trace":
                        event = self._decision_event(record)
                        if self._is_key_decision_event(record, event):
                            decision_events.append(event)
                        else:
                            rollup = self._no_trade_rollup(record, event)
                            key = str(rollup.pop("_rollup_key"))
                            existing = local_rollup_keys.get(key)
                            if existing:
                                existing["count"] = int(existing.get("count", 0) or 0) + 1
                                existing["last_time"] = rollup.get("last_time")
                                existing["last_trace_id"] = rollup.get("last_trace_id")
                                if rollup.get("last_price") is not None:
                                    existing["last_price"] = rollup.get("last_price")
                            else:
                                local_rollup_keys[key] = rollup
                                no_trade_rollups.append(rollup)
        else:
            for stale_path in [self._raw_file_path, self._index_file_path]:
                if stale_path and os.path.isfile(stale_path):
                    try:
                        os.remove(stale_path)
                    except OSError as exc:
                        logger.warning("删除旧 full audit 文件失败: %s (%s)", stale_path, exc)

        for record in self._trade_intents:
            intent_id = str(record.get("id") or record.get("intent_id") or "")
            if intent_id:
                first_seen.setdefault(
                    intent_id, self._format_timestamp(record.get("_timestamp"))
                )
                latest_intents[intent_id] = dict(record)

        execution_summaries = []
        for intent_id, record in latest_intents.items():
            item = dict(record)
            item["_type"] = "execution_summary"
            item.setdefault("intent_id", intent_id)
            item.setdefault("created_at", first_seen.get(intent_id))
            item.setdefault("updated_at", item.get("_timestamp"))
            execution_summaries.append(item)
        execution_summaries.sort(key=self._record_timestamp)
        decision_events.sort(key=lambda item: self._record_timestamp(item))
        no_trade_rollups.sort(
            key=lambda item: str(item.get("last_time") or item.get("first_time") or "")
        )

        self._write_jsonl(self._decision_events_path, decision_events)
        self._write_jsonl(self._no_trade_rollups_path, no_trade_rollups)
        self._write_jsonl(self._execution_summary_path, execution_summaries)

        if self._latest_grid_book and self._latest_grid_book_path:
            with open(self._latest_grid_book_path, "w", encoding="utf-8") as fp:
                json.dump(self._latest_grid_book, fp, ensure_ascii=False, default=str)

        manifest = {
            "schema_version": 3,
            "backtest_id": self.backtest_id,
            "strategy_run_id": self.strategy_run_id,
            "version": self.version,
            "created_at": time_utils.now().isoformat(),
            "audit_mode": self.audit_mode,
            "compaction_policy": BACKTEST_AUDIT_COMPACTION_POLICY,
            "artifacts": {
                "raw_trace": (
                    os.path.basename(self._raw_file_path)
                    if self.audit_mode == AUDIT_MODE_FULL and self._raw_file_path
                    else None
                ),
                "raw_index": (
                    os.path.basename(self._index_file_path)
                    if self.audit_mode == AUDIT_MODE_FULL and self._index_file_path
                    else None
                ),
                "decision_events": os.path.basename(self._decision_events_path),
                "no_trade_rollups": os.path.basename(self._no_trade_rollups_path),
                "execution_summary": os.path.basename(self._execution_summary_path),
                "execution_logs": (
                    os.path.basename(self._execution_logs_path)
                    if self._execution_logs_path
                    else None
                ),
                "latest_grid_book": (
                    os.path.basename(self._latest_grid_book_path)
                    if self._latest_grid_book
                    else None
                ),
            },
            "counts": {
                "records": len(all_records),
                "raw_records": raw_record_count,
                "decision_traces": len(self._traces),
                "decision_events": len(decision_events),
                "no_trade_rollups": len(no_trade_rollups),
                "trade_intent_events": len(self._trade_intents),
                "execution_summaries": len(execution_summaries),
                "orders": len(self._orders),
                "trades": len(self._trades),
                "logs": len(self._logs),
                "execution_logs": self._count_jsonl_records(self._execution_logs_path),
                "grid_books": len(self._grid_books),
                "grid_books_observed": self._grid_book_observed_count,
            },
        }
        with open(self._manifest_path, "w", encoding="utf-8") as fp:
            json.dump(manifest, fp, ensure_ascii=False, default=str, indent=2)

    @staticmethod
    def _json_line(record: Dict[str, Any]) -> str:
        return json.dumps(record, ensure_ascii=False, default=str) + "\n"

    @classmethod
    def _write_jsonl(cls, path: str, records: List[Dict[str, Any]]) -> None:
        with open(path, "w", encoding="utf-8") as fp:
            for record in records:
                fp.write(cls._json_line(record))

    @classmethod
    def _append_jsonl_sync(cls, path: str, record: Dict[str, Any]) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(cls._json_line(record))

    @staticmethod
    def _count_jsonl_records(path: Optional[str]) -> int:
        if not path or not os.path.exists(path):
            return 0
        count = 0
        with open(path, "r", encoding="utf-8") as fp:
            for _ in fp:
                count += 1
        return count

    @staticmethod
    def _index_record(record: Dict[str, Any], offset: int) -> Dict[str, Any]:
        return {
            "type": record.get("_type"),
            "timestamp": record.get("_timestamp")
            or (record.get("input_summary") or {}).get("timestamp")
            or record.get("timestamp"),
            "offset": offset,
            "id": record.get("id"),
            "trace_id": record.get("trace_id"),
            "intent_id": record.get("intent_id") or record.get("id"),
        }

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if value is None:
            return ""
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            try:
                return str(isoformat())
            except (TypeError, ValueError):
                pass
        return str(value)

    @staticmethod
    def _event_timestamp(record: Dict[str, Any]) -> str:
        input_summary = dict(record.get("input_summary") or {})
        environment = dict(record.get("environment") or {})
        for value in [
            input_summary.get("timestamp"),
            environment.get("timestamp"),
            record.get("timestamp"),
            record.get("_timestamp"),
        ]:
            timestamp = BacktestResultStorage._format_timestamp(value)
            if timestamp:
                return timestamp
        return ""

    @classmethod
    def _record_timestamp(cls, record: Dict[str, Any]) -> str:
        for value in [
            record.get("timestamp"),
            (record.get("input_summary") or {}).get("timestamp"),
            record.get("executed_time"),
            record.get("updated_at"),
            record.get("last_time"),
            record.get("_timestamp"),
        ]:
            timestamp = cls._format_timestamp(value)
            if timestamp:
                return timestamp
        return ""

    @staticmethod
    def _event_payload(record: Dict[str, Any]) -> Dict[str, Any]:
        output_summary = dict(record.get("output_summary") or {})
        return dict(output_summary.get("trace_payload") or {})

    @classmethod
    def _event_reason(
        cls,
        record: Dict[str, Any],
        output_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        output = dict(output_summary or record.get("output_summary") or {})
        payload = dict(output.get("trace_payload") or {})
        return str(
            record.get("reason")
            or output.get("reason")
            or payload.get("reason")
            or ""
        )

    @staticmethod
    def _event_bar_key(record: Dict[str, Any], timestamp: str) -> str:
        payload = BacktestResultStorage._event_payload(record)
        bar_key = payload.get("bar_key") or payload.get("barKey")
        if bar_key:
            return str(bar_key)
        return str(timestamp or "")[:16]

    @staticmethod
    def _event_price(record: Dict[str, Any]) -> Optional[float]:
        payload = BacktestResultStorage._event_payload(record)
        environment = dict(record.get("environment") or {})
        input_summary = dict(record.get("input_summary") or {})
        market_context = dict(
            input_summary.get("market_context") or environment or {}
        )
        for value in [
            payload.get("price"),
            market_context.get("price"),
            market_context.get("close"),
            market_context.get("last_price"),
        ]:
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @classmethod
    def _decision_event(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        tags = list(record.get("tags") or [])
        input_summary = dict(record.get("input_summary") or {})
        if not input_summary:
            input_summary = {}
        if "market_context" not in input_summary and record.get("environment"):
            input_summary["market_context"] = dict(record.get("environment") or {})
        if "risk_caps" not in input_summary and record.get("risk_caps"):
            input_summary["risk_caps"] = dict(record.get("risk_caps") or {})
        if "position_profile" not in input_summary and record.get("position_profile"):
            input_summary["position_profile"] = dict(record.get("position_profile") or {})

        output_summary = cls._compact_output_summary(
            dict(record.get("output_summary") or {})
        )
        state_patch = dict(record.get("state_patch") or {})
        trade_intents = [
            cls._compact_trade_intent(dict(intent or {}))
            for intent in list(record.get("trade_intents") or [])
        ]
        timestamp = cls._event_timestamp(record)
        trace_id = str(record.get("trace_id") or "")
        reason = cls._event_reason(record, output_summary)
        return {
            "_type": "decision_event",
            "_timestamp": record.get("_timestamp"),
            "id": str(record.get("id") or trace_id or record.get("_timestamp") or ""),
            "run_id": record.get("run_id") or record.get("strategy_run_id"),
            "strategy_run_id": record.get("strategy_run_id") or record.get("run_id"),
            "strategy_id": record.get("strategy_id"),
            "instrument_code": record.get("instrument_code"),
            "trace_id": trace_id,
            "timestamp": timestamp,
            "input_summary": cls._compact_input_summary(input_summary),
            "output_summary": output_summary,
            "state_patch": cls._compact_state_patch(state_patch),
            "trade_intents": trade_intents,
            "order_draft": cls._compact_state_patch(dict(record.get("order_draft") or {})),
            "order_request": cls._compact_state_patch(dict(record.get("order_request") or {})),
            "risk_decision": cls._compact_state_patch(dict(record.get("risk_decision") or {})),
            "broker_report": cls._compact_state_patch(dict(record.get("broker_report") or {})),
            "reason": reason,
            "tags": tags,
            "has_trade_intent": bool(trade_intents),
            "intent_count": len(trade_intents),
        }

    @staticmethod
    def _has_material_state_patch(record: Dict[str, Any]) -> bool:
        state_patch = dict(record.get("state_patch") or {})
        if not state_patch:
            return False
        if state_patch.get("append_events") or state_patch.get("unset"):
            return True
        updates = dict(state_patch.get("set") or {})
        if updates:
            return any(str(key) not in NON_EVENT_STATE_PATCH_KEYS for key in updates)
        return not any(key in state_patch for key in ["set", "unset", "append_events"])

    @staticmethod
    def _has_append_state_events(record: Dict[str, Any]) -> bool:
        state_patch = dict(record.get("state_patch") or {})
        return bool(state_patch.get("append_events"))

    @classmethod
    def _is_key_decision_event(
        cls,
        record: Dict[str, Any],
        event: Dict[str, Any],
    ) -> bool:
        tags = {str(tag).lower() for tag in list(event.get("tags") or [])}
        if "strategy_output" not in tags:
            return True
        if event.get("trade_intents"):
            return True
        if cls._has_material_state_patch(record):
            if cls._has_append_state_events(record):
                return True
            no_trade_reasons = {
                "tick_no_trade",
                "bar_no_trade",
                "bar_update",
                "no_trade_intent",
                "no_trade",
            }
            if str(event.get("reason") or "").lower() in no_trade_reasons:
                return False
            return True
        for key in ["order_draft", "order_request", "risk_decision", "broker_report"]:
            if record.get(key):
                return True
        if tags - {"strategy_output"}:
            important = {
                "risk_blocked",
                "zero_sized_volume",
                "reserve_failed",
                "broker_report",
                "rejected",
                "cancelled",
                "error",
                "exception",
                "kill_switch",
                "halt",
            }
            if tags & important:
                return True
        reason = str(event.get("reason") or "").lower()
        return any(
            needle in reason
            for needle in [
                "reject",
                "rejected",
                "error",
                "exception",
                "failed",
                "kill",
                "halt",
            ]
        ) and reason not in {"tick_no_trade", "bar_no_trade", "no_trade_intent"}

    @classmethod
    def _no_trade_rollup(
        cls,
        record: Dict[str, Any],
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        input_summary = dict(event.get("input_summary") or {})
        market_context = dict(input_summary.get("market_context") or {})
        risk_caps = dict(input_summary.get("risk_caps") or {})
        timestamp = str(event.get("timestamp") or cls._event_timestamp(record))
        cadence = str(input_summary.get("cadence") or "")
        reason = str(event.get("reason") or "NO_TRADE_INTENT")
        bar_key = cls._event_bar_key(record, timestamp)
        risk_tags = sorted(
            str(tag)
            for tag in list(
                risk_caps.get("risk_tags") or market_context.get("risk_tags") or []
            )
        )
        data_quality = str(market_context.get("data_quality") or "")
        risk_mode = str(risk_caps.get("risk_mode") or "")
        signature = json.dumps(
            {
                "run_id": event.get("run_id"),
                "instrument_code": event.get("instrument_code"),
                "cadence": cadence,
                "bar_key": bar_key,
                "reason": reason,
                "risk_tags": risk_tags,
                "data_quality": data_quality,
                "risk_mode": risk_mode,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "_type": "no_trade_rollup",
            "_rollup_key": signature,
            "run_id": event.get("run_id"),
            "strategy_run_id": event.get("strategy_run_id"),
            "strategy_id": event.get("strategy_id"),
            "instrument_code": event.get("instrument_code"),
            "reason": reason,
            "cadence": cadence,
            "bar_key": bar_key,
            "first_time": timestamp,
            "last_time": timestamp,
            "first_trace_id": event.get("trace_id"),
            "last_trace_id": event.get("trace_id"),
            "count": 1,
            "last_price": cls._event_price(record),
            "risk_tags": risk_tags,
            "data_quality": data_quality,
            "risk_mode": risk_mode,
        }

    @staticmethod
    def _pick_keys(data: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
        return {
            key: data.get(key)
            for key in keys
            if key in data and data.get(key) is not None
        }

    @classmethod
    def _compact_input_summary(cls, input_summary: Dict[str, Any]) -> Dict[str, Any]:
        compact = cls._pick_keys(
            input_summary,
            [
                "input_id",
                "trace_id",
                "run_id",
                "strategy_id",
                "instrument_code",
                "cadence",
                "timestamp",
            ],
        )
        market_context = dict(input_summary.get("market_context") or {})
        if market_context:
            market = cls._pick_keys(
                market_context,
                [
                    "instrument_code",
                    "trade_date",
                    "timestamp",
                    "market_state",
                    "sector_state",
                    "concept_heat_state",
                    "liquidity_state",
                    "breadth_state",
                    "volume_structure",
                    "context_score",
                    "risk_tags",
                    "data_quality",
                    "state_changed_reason",
                    "price",
                    "close",
                    "last_price",
                    "source",
                    "source_fingerprint",
                    "industry_state",
                ],
            )
            master = dict(market_context.get("instrument_master") or {})
            if master:
                market["instrument_master"] = cls._pick_keys(
                    master,
                    [
                        "instrument_code",
                        "trading_date",
                        "exchange",
                        "is_trading_day",
                        "suspended",
                        "is_st",
                        "delist_risk",
                        "limit_up",
                        "limit_down",
                        "data_quality",
                        "risk_tags",
                    ],
                )
            compact["market_context"] = market

        risk_caps = dict(input_summary.get("risk_caps") or {})
        if risk_caps:
            compact["risk_caps"] = cls._pick_keys(
                risk_caps,
                [
                    "risk_mode",
                    "kill_switch_active",
                    "max_position_pct",
                    "min_cash_buffer_pct",
                    "allow_buy",
                    "allow_sell",
                    "allow_intraday_swing_buy",
                    "only_reduce_position",
                    "allow_locked_core_substitution",
                    "t1_insufficient_action",
                    "reason_codes",
                    "risk_tags",
                ],
            )

        position_profile = dict(input_summary.get("position_profile") or {})
        if position_profile:
            compact["position_profile"] = cls._pick_keys(
                position_profile,
                [
                    "profile",
                    "min_position_pct",
                    "max_position_pct",
                    "target_cash_buffer_pct",
                    "allow_core_buy",
                    "allow_swing_buy",
                    "allow_core_sell",
                    "allow_swing_sell",
                    "reason_tags",
                    "current_position_pct",
                    "instrument_code",
                ],
            )
        return compact

    @classmethod
    def _compact_trace_payload(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        compact = cls._pick_keys(
            payload,
            [
                "reason",
                "bar_key",
                "grid_id",
                "grid_count",
                "side",
                "price",
                "volume",
                "intent_key",
            ],
        )
        profile = dict(payload.get("position_profile") or {})
        if profile:
            compact["position_profile"] = cls._pick_keys(
                profile,
                [
                    "profile",
                    "allow_swing_buy",
                    "allow_swing_sell",
                    "allow_core_buy",
                    "allow_core_sell",
                    "buy_disabled_reason",
                    "reason_tags",
                ],
            )
        signal_marker = dict(payload.get("signal_marker") or {})
        if signal_marker:
            # Engine already bounded this T-trade marker before it crossed the
            # RuntimeState durability boundary. Preserve its source/candidate
            # reconciliation facts in the backtest artifact instead of
            # dropping the dedicated-evidence link during export compaction.
            compact["signal_marker"] = signal_marker
        block_events = list(payload.get("block_events") or [])
        if block_events:
            compact["block_events"] = [
                cls._pick_keys(
                    dict(event or {}),
                    [
                        "source",
                        "bar_key",
                        "grid_id",
                        "grid_level_index",
                        "lot_id",
                        "intent_key",
                        "block_reason",
                        "message",
                        "price",
                        "event_time",
                    ],
                )
                for event in block_events[:MAX_DECISION_BLOCK_EVENTS]
            ]
            if len(block_events) > MAX_DECISION_BLOCK_EVENTS:
                compact["block_event_count"] = len(block_events)
        return compact

    @classmethod
    def _compact_output_summary(cls, output_summary: Dict[str, Any]) -> Dict[str, Any]:
        compact = cls._pick_keys(
            output_summary,
            ["trade_intent_count", "decision_tags", "reason", "tags"],
        )
        payload = dict(output_summary.get("trace_payload") or {})
        if payload:
            compact["trace_payload"] = cls._compact_trace_payload(payload)
        return compact

    @classmethod
    def _compact_trade_intent(cls, intent: Dict[str, Any]) -> Dict[str, Any]:
        return cls._pick_keys(
            intent,
            [
                "id",
                "intent_id",
                "trace_id",
                "direction",
                "side",
                "instrument_code",
                "bucket",
                "target_volume",
                "target_amount",
                "target_position_pct",
                "limit_price_hint",
                "price",
                "reason",
                "status",
                "created_at",
                "updated_at",
            ],
        )

    @staticmethod
    def _compact_state_patch(state_patch: Dict[str, Any]) -> Dict[str, Any]:
        if not state_patch:
            return {}
        try:
            encoded = json.dumps(state_patch, ensure_ascii=False, default=str)
        except TypeError:
            return {"_summary": str(state_patch)}
        if len(encoded) <= 4096:
            return state_patch
        return {
            "_truncated": True,
            "keys": sorted(str(key) for key in state_patch.keys()),
        }

    @classmethod
    def _decision_summary(cls, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tags = list(record.get("tags") or [])
        if "strategy_output" not in tags:
            return None

        input_summary = dict(record.get("input_summary") or {})
        output_summary = dict(record.get("output_summary") or {})
        state_patch = dict(record.get("state_patch") or {})
        trade_intents = list(record.get("trade_intents") or [])
        compact_output = cls._compact_output_summary(output_summary)
        timestamp = (
            input_summary.get("timestamp")
            or (record.get("environment") or {}).get("timestamp")
            or record.get("timestamp")
            or record.get("_timestamp")
        )
        trace_id = str(record.get("trace_id") or "")
        reason = (
            record.get("reason")
            or compact_output.get("reason")
            or (compact_output.get("trace_payload") or {}).get("reason")
            or ""
        )
        return {
            "_type": "decision_summary",
            "_timestamp": record.get("_timestamp"),
            "id": str(record.get("id") or trace_id or record.get("_timestamp") or ""),
            "run_id": record.get("run_id") or record.get("strategy_run_id"),
            "strategy_run_id": record.get("strategy_run_id") or record.get("run_id"),
            "strategy_id": record.get("strategy_id"),
            "instrument_code": record.get("instrument_code"),
            "trace_id": trace_id,
            "timestamp": timestamp,
            "input_summary": cls._compact_input_summary(input_summary),
            "output_summary": compact_output,
            "state_patch": cls._compact_state_patch(state_patch),
            "trade_intents": [
                cls._compact_trade_intent(dict(intent or {}))
                for intent in trade_intents
            ],
            "reason": reason,
            "tags": tags,
            "has_trade_intent": bool(trade_intents),
            "intent_count": len(trade_intents),
        }

    async def stream_write_trade_intent(self, intent: Dict[str, Any]) -> None:
        """实时流式写入交易意图（适用于大量数据场景）"""
        if self._manifest_path and self.audit_mode != AUDIT_MODE_FULL:
            self.add_trade_intent(intent)
            return
        intent = dict(intent or {})
        intent["_type"] = "trade_intent"
        intent["_timestamp"] = time_utils.now().isoformat()
        path = self._raw_file_path or self._file_path
        if not path:
            return
        async with aiofiles.open(path, mode="a", encoding="utf-8") as f:
            await f.write(json.dumps(intent, ensure_ascii=False, default=str) + "\n")

    @classmethod
    def load_manifest(cls, file_path: str) -> Optional[Dict[str, Any]]:
        """Load a v2 backtest manifest, returning None for legacy JSONL files."""
        if not file_path or not os.path.exists(file_path):
            return None
        if os.path.isdir(file_path):
            file_path = os.path.join(file_path, "manifest.json")
        if os.path.basename(file_path) != "manifest.json":
            return None
        try:
            with open(file_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @classmethod
    def resolve_artifact_path(cls, manifest_path: str, artifact_key: str) -> Optional[str]:
        """Resolve a path from a manifest artifact key."""
        manifest = cls.load_manifest(manifest_path)
        if not manifest:
            return None
        artifact = (manifest.get("artifacts") or {}).get(artifact_key)
        if not artifact:
            return None
        if isinstance(artifact, dict):
            artifact = artifact.get("path")
        if not artifact:
            return None
        if os.path.isabs(str(artifact)):
            path = str(artifact)
        else:
            path = os.path.join(os.path.dirname(manifest_path), str(artifact))
        return path if os.path.exists(path) else None

    @classmethod
    def raw_trace_path(cls, file_path: str) -> str:
        """Return the raw JSONL path for both manifest and legacy layouts."""
        manifest = cls.load_manifest(file_path)
        if manifest:
            raw_path = cls.resolve_artifact_path(file_path, "raw_trace")
            return raw_path or ""
        raw_path = cls.resolve_artifact_path(file_path, "raw_trace")
        return raw_path or file_path

    @classmethod
    async def load_results(cls, file_path: str) -> Dict[str, List[Dict[str, Any]]]:
        """从文件加载回测结果"""
        trade_intents = []
        orders = []
        trades = []
        traces = []
        logs = []
        grid_books = []

        file_path = cls.raw_trace_path(file_path)
        if not os.path.exists(file_path):
            return {
                "trade_intents": trade_intents,
                "orders": orders,
                "trades": trades,
                "traces": traces,
                "logs": logs,
                "grid_books": grid_books,
            }

        async with aiofiles.open(file_path, mode="r", encoding="utf-8") as f:
            async for line in f:
                try:
                    record = json.loads(line.strip())
                    record_type = record.get("_type")
                    if record_type == "trade_intent":
                        trade_intents.append(record)
                    elif record_type == "order":
                        orders.append(record)
                    elif record_type == "trade":
                        trades.append(record)
                    elif record_type == "decision_trace":
                        traces.append(record)
                    elif record_type == "log":
                        logs.append(record)
                    elif record_type == "grid_book_snapshot":
                        grid_books.append(record)
                except json.JSONDecodeError:
                    continue

        return {
            "trade_intents": trade_intents,
            "orders": orders,
            "trades": trades,
            "traces": traces,
            "logs": logs,
            "grid_books": grid_books,
        }

    @classmethod
    async def load_latest_grid_book_snapshot(
        cls,
        file_path: str,
    ) -> Optional[Dict[str, Any]]:
        """只读取回测结果文件中的最后一个 GridBook 快照。"""
        manifest_snapshot = cls.resolve_artifact_path(file_path, "latest_grid_book")
        if manifest_snapshot:
            try:
                with open(manifest_snapshot, "r", encoding="utf-8") as fp:
                    snapshot = json.load(fp)
                return snapshot if isinstance(snapshot, dict) else None
            except (OSError, json.JSONDecodeError):
                return None

        file_path = cls.raw_trace_path(file_path)
        if not os.path.exists(file_path):
            return None

        latest_snapshot: Optional[Dict[str, Any]] = None
        async with aiofiles.open(file_path, mode="r", encoding="utf-8") as f:
            async for line in f:
                if "grid_book_snapshot" not in line:
                    continue
                try:
                    record = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if record.get("_type") == "grid_book_snapshot":
                    latest_snapshot = record

        return latest_snapshot

    def get_file_path(self) -> str:
        """获取结果文件路径"""
        return self._file_path or ""

    def get_log_file_path(self) -> str:
        """获取回测版本执行日志文件路径。"""
        return self._execution_logs_path or ""

    def get_latest_grid_book_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取内存中最新的 GridBook 快照。"""
        return dict(self._latest_grid_book or {}) if self._latest_grid_book else None

    def get_grid_book_snapshot_count(self) -> int:
        """获取实际写入的 GridBook 快照数。"""
        return len(self._grid_books)

    def get_grid_book_observed_count(self) -> int:
        """获取回测期间观测到的 GridBook 快照数。"""
        return self._grid_book_observed_count
