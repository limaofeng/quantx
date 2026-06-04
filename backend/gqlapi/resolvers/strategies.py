"""
策略GraphQL Resolver - 集成策略管理器服务
"""

import logging
import json
import os
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from core.strategy_manager import strategy_manager
from core.strategy_registry import strategy_registry
from database.connection import get_async_db
from repositories.strategy_repository import StrategyRepository
from repositories.strategy_run_repository import StrategyRunRepository

from core.utils import time_utils
from models.enums import StrategyRunStatus
from models.execution_metrics import ExecutionMetrics
from ..types import (
  BucketLedgerView,
  ExecutionTraceView,
  MessageResponse,
  OperationResult,
  Strategy,
  StrategyDecision,
  StrategyDefinition,
  StrategyInstance,
  StrategyInstanceCreateInput,
  StrategyInstanceParameterUpdateInput,
  StrategyLogEntry,
  StrategyLogPage,
  StrategyRun,
  StrategyRunInput,
  StrategyRunMode,
  StrategyRunUpdateInput,
  TradeIntentView,
)

logger = logging.getLogger(__name__)


class StrategyResolver:
  """策略相关的GraphQL Resolver"""

  @staticmethod
  def _json_object(value):
    if value is None:
      return {}
    if isinstance(value, dict):
      return dict(value)
    if isinstance(value, str):
      try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
      except Exception:
        return {}
    return {}

  @staticmethod
  def _normalize_instrument_code(value: str) -> str:
    return str(value or "").strip().upper()

  @staticmethod
  def _resolve_backtest_result_path(result_path: Optional[str]) -> Optional[str]:
    if not result_path:
      return None
    candidates = [
      result_path,
      os.path.join("data", result_path),
      os.path.join("data", "backtests", os.path.basename(result_path)),
    ]
    for path in candidates:
      if os.path.exists(path):
        return path
    return None

  @staticmethod
  def _resolve_backtest_artifact_path(
    file_path: str,
    artifact_key: str,
  ) -> Optional[str]:
    from core.backtest_result_storage import BacktestResultStorage

    return BacktestResultStorage.resolve_artifact_path(file_path, artifact_key)

  @staticmethod
  def _raw_backtest_result_path(file_path: str) -> str:
    from core.backtest_result_storage import BacktestResultStorage

    return BacktestResultStorage.raw_trace_path(file_path)

  @staticmethod
  def _relative_path(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
      return None
    try:
      return os.path.relpath(file_path, os.getcwd())
    except ValueError:
      return file_path

  @staticmethod
  def _candidate_strategy_log_paths(run_id: str, mode: Optional[str]) -> List[str]:
    candidates: List[str] = []
    if mode:
      candidates.append(os.path.join("logs", "strategy", mode, f"{run_id}.jsonl"))
    candidates.append(os.path.join("logs", "strategy", f"{run_id}.jsonl"))
    return candidates

  @staticmethod
  def _first_existing_path(candidates: List[Optional[str]]) -> Optional[str]:
    for path in candidates:
      if path and os.path.exists(path):
        return path
    return None

  @staticmethod
  def _record_timestamp(record: Dict[str, Any]) -> str:
    return str(
      record.get("timestamp")
      or (record.get("input_summary") or {}).get("timestamp")
      or record.get("executed_time")
      or record.get("updated_at")
      or record.get("_timestamp")
      or ""
    )

  @staticmethod
  def _read_jsonl_records(
    file_path: str,
    *,
    record_type: Optional[str] = None,
  ) -> List[Dict[str, Any]]:
    if not file_path or not os.path.exists(file_path):
      return []
    records: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as fp:
      for line in fp:
        try:
          record = json.loads(line)
        except json.JSONDecodeError:
          continue
        if record_type and record.get("_type") != record_type:
          continue
        records.append(record)
    return records

  @staticmethod
  def _read_jsonl_tail_records(
    file_path: str,
    *,
    limit: int,
    record_type: Optional[str] = None,
  ) -> List[Dict[str, Any]]:
    if not file_path or not os.path.exists(file_path):
      return []
    limit = max(1, min(int(limit or 50), 500))
    block_size = 65536
    with open(file_path, "rb") as fp:
      fp.seek(0, os.SEEK_END)
      position = fp.tell()
      data = b""
      lines: List[bytes] = []
      while position > 0 and len(lines) <= limit:
        read_size = min(block_size, position)
        position -= read_size
        fp.seek(position)
        data = fp.read(read_size) + data
        lines = data.splitlines()

    records: List[Dict[str, Any]] = []
    for raw_line in lines[-limit:]:
      try:
        record = json.loads(raw_line.decode("utf-8"))
      except (UnicodeDecodeError, json.JSONDecodeError):
        continue
      if record_type and record.get("_type") != record_type:
        continue
      records.append(record)
    return records

  @staticmethod
  def _delete_backtest_artifacts(
    backtest_id: str,
    result_path: Optional[str],
  ) -> List[str]:
    """Delete file artifacts for one backtest while staying under data/backtests."""
    from core.backtest_result_storage import BacktestResultStorage

    data_root = os.path.abspath(os.path.join("data", "backtests"))
    candidates = set(StrategyResolver._backtest_result_path_candidates(result_path or ""))
    candidates.add(
      os.path.join("data", "backtests", "performance", f"{backtest_id}.json")
    )
    candidates.add(
      os.path.join(
        "data",
        "backtests",
        "performance",
        str(backtest_id),
        "manifest.json",
      )
    )
    for candidate in list(candidates):
      manifest = BacktestResultStorage.load_manifest(candidate)
      if not manifest:
        continue
      for artifact in dict(manifest.get("artifacts") or {}).values():
        artifact_path = artifact.get("path") if isinstance(artifact, dict) else artifact
        if artifact_path:
          candidates.add(os.path.join(os.path.dirname(candidate), str(artifact_path)))
    deleted: List[str] = []
    for candidate in candidates:
      if not candidate:
        continue
      abs_path = os.path.abspath(candidate)
      try:
        if os.path.commonpath([data_root, abs_path]) != data_root:
          continue
      except ValueError:
        continue
      if not os.path.isfile(abs_path):
        continue
      try:
        os.remove(abs_path)
        deleted.append(abs_path)
      except OSError as exc:
        logger.warning("删除回测文件失败: %s (%s)", abs_path, exc)
    return deleted

  @staticmethod
  async def _resolve_backtest_for_details(
    db,
    instance_id: str,
    backtest_id: Optional[str] = None,
  ):
    from repositories.backtest_repository import BacktestRepository

    repo = BacktestRepository(db)
    if backtest_id:
      return await repo.get_backtest(backtest_id)
    backtests = await repo.get_backtests_by_run(instance_id)
    for backtest in backtests:
      if str(backtest.status or "").upper() == "COMPLETED" and backtest.result_path:
        return backtest
    return backtests[0] if backtests else None

  @staticmethod
  async def _resolve_strategy_log_source(
    run_id: str,
    *,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
  ) -> Dict[str, Any]:
    runtime = strategy_manager.get_run(run_id)
    if runtime and runtime.log_manager:
      runtime_backtest_id = getattr(runtime.context, "backtest_id", None)
      runtime_version = getattr(runtime.context, "backtest_version", None)
      matches_runtime_backtest = (
        not backtest_id
        or str(backtest_id) == str(runtime_backtest_id or "")
      ) and (version is None or int(version) == int(runtime_version or 0))
      if runtime.context.mode != StrategyRunMode.BACKTEST or matches_runtime_backtest:
        file_path = runtime.log_manager.get_log_file_path(run_id)
        if file_path:
          return {
            "path": file_path,
            "record_type": None,
            "mode": runtime.context.mode.value,
            "backtest_id": runtime_backtest_id,
            "backtest_version": runtime_version,
          }

    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      run = await run_repo.find_run_by_id(run_id)
      if not run:
        break

      mode_value = str(getattr(run.mode, "value", run.mode) or "").lower()
      if mode_value == StrategyRunMode.BACKTEST.value:
        from repositories.backtest_repository import BacktestRepository

        backtest_repo = BacktestRepository(db)
        backtest = None
        if backtest_id:
          backtest = await backtest_repo.get_backtest(backtest_id)
          if backtest and backtest.strategy_run_id != run_id:
            backtest = None
        elif version is not None:
          backtest = await backtest_repo.get_backtest_by_run_version(
            run_id,
            int(version),
          )
        else:
          backtest = await StrategyResolver._resolve_backtest_for_details(db, run_id)

        if not backtest:
          break

        result_path = StrategyResolver._resolve_backtest_result_path(
          getattr(backtest, "result_path", None)
        )
        log_path = None
        if result_path:
          log_path = StrategyResolver._resolve_backtest_artifact_path(
            result_path,
            "execution_logs",
          )
        if not log_path:
          candidate_paths = []
          raw_path = None
          if result_path:
            candidate_paths.append(
              os.path.join(os.path.dirname(result_path), "execution_logs.jsonl")
            )
            raw_path = StrategyResolver._raw_backtest_result_path(result_path)
            candidate_paths.append(raw_path)
          candidate_paths.append(
            os.path.join(
              "data",
              "backtests",
              run_id,
              f"v{int(getattr(backtest, 'version', 0) or 0)}",
              "execution_logs.jsonl",
            )
          )
          log_path = StrategyResolver._first_existing_path(candidate_paths)
        else:
          raw_path = None

        return {
          "path": log_path,
          "record_type": (
            "log"
            if log_path
            and raw_path
            and os.path.abspath(log_path) == os.path.abspath(raw_path)
            else None
          ),
          "mode": mode_value,
          "backtest_id": getattr(backtest, "id", None),
          "backtest_version": getattr(backtest, "version", None),
        }

      log_path = StrategyResolver._first_existing_path(
        StrategyResolver._candidate_strategy_log_paths(run_id, mode_value)
      )
      return {
        "path": log_path,
        "record_type": None,
        "mode": mode_value,
        "backtest_id": None,
        "backtest_version": None,
      }

    return {
      "path": None,
      "record_type": None,
      "mode": None,
      "backtest_id": backtest_id,
      "backtest_version": version,
    }

  @staticmethod
  def _parse_strategy_log_line(
    run_id: str,
    line: str,
    *,
    record_type: Optional[str] = None,
  ) -> Optional[StrategyLogEntry]:
    text = line.strip()
    if not text:
      return None
    try:
      record = json.loads(text)
    except json.JSONDecodeError:
      record = {"message": text, "level": "INFO", "source": "file"}
    if record_type and record.get("_type") != record_type:
      return None
    return StrategyLogEntry.from_record(run_id, record)

  @staticmethod
  def _read_strategy_log_page(
    *,
    run_id: str,
    file_path: Optional[str],
    record_type: Optional[str],
    cursor: Optional[int],
    limit: int,
    before: bool,
    tail: bool,
  ) -> Dict[str, Any]:
    limit = min(max(int(limit or 200), 1), 1000)
    if not file_path or not os.path.exists(file_path):
      return {
        "entries": [],
        "start_cursor": 0,
        "end_cursor": 0,
        "has_previous_page": False,
        "has_next_page": False,
        "total_lines": 0,
        "file_size_bytes": 0,
      }

    file_size = os.path.getsize(file_path)
    if tail or cursor is None:
      buffered = deque(maxlen=limit)
      total = 0
      with open(file_path, "r", encoding="utf-8") as fp:
        for line in fp:
          entry = StrategyResolver._parse_strategy_log_line(
            run_id,
            line,
            record_type=record_type,
          )
          if not entry:
            continue
          buffered.append(entry)
          total += 1
      entries = list(buffered)
      start_cursor = max(0, total - len(entries))
      return {
        "entries": entries,
        "start_cursor": start_cursor,
        "end_cursor": total,
        "has_previous_page": start_cursor > 0,
        "has_next_page": False,
        "total_lines": total,
        "file_size_bytes": file_size,
      }

    target_cursor = max(0, int(cursor))
    start_cursor = max(0, target_cursor - limit) if before else target_cursor
    end_cursor = target_cursor if before else target_cursor + limit
    entries: List[StrategyLogEntry] = []
    total = 0

    with open(file_path, "r", encoding="utf-8") as fp:
      for line in fp:
        entry = StrategyResolver._parse_strategy_log_line(
          run_id,
          line,
          record_type=record_type,
        )
        if not entry:
          continue
        if start_cursor <= total < end_cursor:
          entries.append(entry)
        total += 1

    end_cursor = min(end_cursor, total)
    return {
      "entries": entries,
      "start_cursor": start_cursor,
      "end_cursor": end_cursor,
      "has_previous_page": start_cursor > 0,
      "has_next_page": end_cursor < total,
      "total_lines": total,
      "file_size_bytes": file_size,
    }

  @staticmethod
  def _record_intent_ids(record: Dict[str, Any]) -> Set[str]:
    ids: Set[str] = set()
    for item in list(record.get("trade_intents") or []):
      if not isinstance(item, dict):
        continue
      intent_id = item.get("intent_id") or item.get("id")
      if intent_id:
        ids.add(str(intent_id))
    return ids

  @staticmethod
  def _has_value(value: Any) -> bool:
    return value is not None and value != ""

  @staticmethod
  def _record_price_hint(record: Dict[str, Any]) -> Any:
    for key in ("limit_price_hint", "price", "price_intent", "priceIntent"):
      value = record.get(key)
      if StrategyResolver._has_value(value):
        return value

    order_draft = record.get("order_draft")
    if isinstance(order_draft, dict):
      for key in ("limit_price", "price", "sized_price"):
        value = order_draft.get(key)
        if StrategyResolver._has_value(value):
          return value

    order_request = record.get("order_request")
    if isinstance(order_request, dict):
      value = order_request.get("price")
      if StrategyResolver._has_value(value):
        return value
    return None

  @staticmethod
  def _record_matches_intent(record: Dict[str, Any], intent_id: str) -> bool:
    if str(record.get("id") or record.get("intent_id") or "") == intent_id:
      return True
    for key in ("order_draft", "order_request", "broker_report"):
      value = record.get(key)
      if isinstance(value, dict) and str(value.get("intent_id") or "") == intent_id:
        return True
    return False

  @staticmethod
  def _enrich_intent_summary(
    intent: Dict[str, Any],
    source: Optional[Dict[str, Any]],
  ) -> Dict[str, Any]:
    if not source:
      return intent
    if not StrategyResolver._has_value(intent.get("limit_price_hint")):
      price = StrategyResolver._record_price_hint(source)
      if StrategyResolver._has_value(price):
        intent["limit_price_hint"] = price
    for key in (
      "direction",
      "side",
      "instrument_code",
      "bucket",
      "target_volume",
      "target_amount",
      "target_position_pct",
      "reason",
      "status",
      "created_at",
      "updated_at",
    ):
      if not StrategyResolver._has_value(intent.get(key)) and StrategyResolver._has_value(source.get(key)):
        intent[key] = source.get(key)
    return intent

  @staticmethod
  def _enrich_backtest_decision_records(
    file_path: str,
    records: List[Dict[str, Any]],
  ) -> List[Dict[str, Any]]:
    intent_ids: Set[str] = set()
    for record in records:
      intent_ids.update(StrategyResolver._record_intent_ids(record))
    if not intent_ids:
      return records

    intent_records = StrategyResolver._load_backtest_intent_records(
      file_path,
      intent_ids=intent_ids,
      limit=max(len(intent_ids), 50),
    )
    intent_index = {
      str(record.get("id") or record.get("intent_id") or ""): record
      for record in intent_records
      if record.get("id") or record.get("intent_id")
    }

    enriched_records: List[Dict[str, Any]] = []
    for record in records:
      enriched = dict(record)
      enriched_intents: List[Dict[str, Any]] = []
      for raw_intent in list(record.get("trade_intents") or []):
        if not isinstance(raw_intent, dict):
          continue
        intent = dict(raw_intent)
        intent_id = str(intent.get("intent_id") or intent.get("id") or "")
        if intent_id:
          intent = StrategyResolver._enrich_intent_summary(
            intent,
            intent_index.get(intent_id),
          )
          if StrategyResolver._record_matches_intent(record, intent_id):
            intent = StrategyResolver._enrich_intent_summary(intent, record)
        enriched_intents.append(intent)
      enriched["trade_intents"] = enriched_intents
      enriched_records.append(enriched)
    return enriched_records

  @staticmethod
  def _iter_backtest_result_records(file_path: str, record_type: str):
    file_path = StrategyResolver._raw_backtest_result_path(file_path)
    if not file_path or not os.path.exists(file_path):
      return
    needle = f'"_type": "{record_type}"'
    with open(file_path, "r", encoding="utf-8") as fp:
      for line in fp:
        if needle not in line:
          continue
        try:
          record = json.loads(line)
        except json.JSONDecodeError:
          continue
        if record.get("_type") == record_type:
          yield record

  @staticmethod
  def _load_backtest_decision_records(
    file_path: str,
    *,
    limit: int,
  ) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    event_path = StrategyResolver._resolve_backtest_artifact_path(
      file_path,
      "decision_events",
    )
    if event_path:
      records = StrategyResolver._read_jsonl_tail_records(
        event_path,
        limit=limit,
      )
      if records:
        records = sorted(
          records,
          key=StrategyResolver._record_timestamp,
          reverse=True,
        )
        return StrategyResolver._enrich_backtest_decision_records(file_path, records)

    trade_summary_path = StrategyResolver._resolve_backtest_artifact_path(
      file_path,
      "decision_trade_summary",
    )
    if trade_summary_path:
      records = StrategyResolver._read_jsonl_tail_records(
        trade_summary_path,
        limit=limit,
      )
      if records:
        records = sorted(
          records,
          key=StrategyResolver._record_timestamp,
          reverse=True,
        )
        return StrategyResolver._enrich_backtest_decision_records(file_path, records)

    summary_path = StrategyResolver._resolve_backtest_artifact_path(
      file_path,
      "decision_summary",
    )
    if summary_path:
      records = StrategyResolver._read_jsonl_tail_records(
        summary_path,
        limit=limit,
      )
      if records:
        records = sorted(
          records,
          key=StrategyResolver._record_timestamp,
          reverse=True,
        )
        return StrategyResolver._enrich_backtest_decision_records(file_path, records)

    trade_decisions: List[Dict[str, Any]] = []
    recent_outputs = deque(maxlen=limit)
    for record in StrategyResolver._iter_backtest_result_records(file_path, "decision_trace"):
      tags = list(record.get("tags") or [])
      if "strategy_output" not in tags:
        continue
      if record.get("trade_intents"):
        trade_decisions.append(record)
      else:
        recent_outputs.append(record)
    selected = trade_decisions[-limit:] or list(recent_outputs)
    records = sorted(
      selected,
      key=StrategyResolver._record_timestamp,
      reverse=True,
    )
    return StrategyResolver._enrich_backtest_decision_records(file_path, records)

  @staticmethod
  def _find_backtest_decision_intent_ids(
    file_path: str,
    decision_id: str,
  ) -> Optional[Set[str]]:
    summary_paths = [
      StrategyResolver._resolve_backtest_artifact_path(
        file_path,
        "decision_events",
      ),
      StrategyResolver._resolve_backtest_artifact_path(
        file_path,
        "decision_trade_summary",
      ),
      StrategyResolver._resolve_backtest_artifact_path(
        file_path,
        "decision_summary",
      ),
    ]
    for summary_path in [path for path in summary_paths if path]:
      for record in StrategyResolver._read_jsonl_records(summary_path):
        if decision_id in {
          str(record.get("id") or ""),
          str(record.get("trace_id") or ""),
        }:
          return StrategyResolver._record_intent_ids(record)

    for record in StrategyResolver._iter_backtest_result_records(
      file_path,
      "decision_trace",
    ):
      if decision_id in {str(record.get("id") or ""), str(record.get("trace_id") or "")}:
        return StrategyResolver._record_intent_ids(record)
    return None

  @staticmethod
  def _load_backtest_intent_records(
    file_path: str,
    *,
    intent_ids: Optional[Set[str]] = None,
    limit: int = 50,
  ) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 500))
    summary_path = StrategyResolver._resolve_backtest_artifact_path(
      file_path,
      "execution_summary",
    )
    if summary_path:
      if intent_ids is None:
        records = StrategyResolver._read_jsonl_tail_records(
          summary_path,
          limit=limit,
        )
      else:
        records = [
          record for record in StrategyResolver._read_jsonl_records(summary_path)
          if str(record.get("id") or record.get("intent_id") or "") in intent_ids
        ]
      if records:
        return sorted(
          records,
          key=lambda item: item.get("executed_time") or item.get("updated_at") or "",
          reverse=True,
        )[:limit]

    first_seen: Dict[str, str] = {}
    latest_by_id: Dict[str, Dict[str, Any]] = {}
    for record in StrategyResolver._iter_backtest_result_records(file_path, "trade_intent"):
      intent_id = str(record.get("id") or record.get("intent_id") or "")
      if not intent_id:
        continue
      if intent_ids is not None and intent_id not in intent_ids:
        continue
      first_seen.setdefault(intent_id, record.get("_timestamp"))
      latest_by_id[intent_id] = record
    records = []
    for intent_id, record in latest_by_id.items():
      item = dict(record)
      item.setdefault("created_at", first_seen.get(intent_id))
      item.setdefault("updated_at", item.get("_timestamp"))
      records.append(item)
    return sorted(
      records,
      key=lambda item: item.get("executed_time") or item.get("updated_at") or "",
      reverse=True,
    )[:limit]

  @staticmethod
  def _is_single_instrument_strategy(strategy_model, strategy_class) -> bool:
    scope = getattr(strategy_class, "INSTRUMENT_SCOPE", None) or getattr(strategy_model, "instrument_scope", None)
    return str(getattr(scope, "value", scope)).lower() == "single"

  @staticmethod
  def _validate_strategy_instruments(strategy_model, strategy_class, instruments: List[str]) -> List[str]:
    normalized = [StrategyResolver._normalize_instrument_code(item) for item in instruments or [] if item]
    if StrategyResolver._is_single_instrument_strategy(strategy_model, strategy_class):
      if len(normalized) != 1:
        raise ValueError("A 股单标的策略实例必须且只能绑定一个 instrument_code")
      code = normalized[0]
      if not (code.endswith(".SH") or code.endswith(".SZ")):
        raise ValueError("A 股单标的策略仅支持 .SH 或 .SZ 标的")
    return normalized

  @staticmethod
  async def _find_strategy_by_key(strategy_key: str):
    key = str(strategy_key or "").strip()
    async for db in get_async_db():
      repo = StrategyRepository(db)
      strategies = await repo.get_all_strategies()
      for model in strategies:
        candidates = {
          str(model.id),
          str(model.name or ""),
          str(model.class_name or ""),
          str((model.file_path or "").split("/")[-1].replace(".py", "")),
        }
        if key in candidates:
          return model
    return None

  @staticmethod
  async def _instance_from_run_model(db, run_model) -> StrategyInstance:
    from repositories.strategy_decision_trace_repository import StrategyDecisionTraceRepository
    from repositories.trade_intent_repository import TradeIntentRepository

    decision_repo = StrategyDecisionTraceRepository(db)
    intent_repo = TradeIntentRepository(db)
    decisions = await decision_repo.find_by_strategy_run(run_model.id, limit=1)
    intents = await intent_repo.find_recent_by_strategy_run(run_model.id, limit=1)
    return StrategyInstance.from_run(
      run_model,
      last_decision_at=decisions[0].decided_at if decisions else None,
      latest_execution_status=intents[0].status if intents else None,
    )

  @staticmethod
  def _runtime_status_to_model(status_value: str) -> StrategyRunStatus:
    if not status_value:
      return StrategyRunStatus.PENDING
    return StrategyRunStatus(status_value.lower())

  @staticmethod
  def _serialize_metrics(metrics):
    if metrics is None:
      return None
    if isinstance(metrics, dict):
      return metrics
    if isinstance(metrics, ExecutionMetrics):
      return metrics.model_dump(mode="json")
    if hasattr(metrics, "model_dump"):
      return metrics.model_dump(mode="json")
    if hasattr(metrics, "dict"):
      return metrics.dict()
    return None

  @staticmethod
  async def _get_strategy_by_id(strategy_id: int) -> Optional["Strategy"]:
    """根据 strategy_id 获取 Strategy 对象"""
    async for db in get_async_db():
      repo = StrategyRepository(db)
      model = await repo.get_strategy(strategy_id)
      if model:
        return Strategy.from_model(model)
    return None

  @staticmethod
  async def get_strategies() -> List[Strategy]:
    """获取策略模板列表"""
    strategies = []

    async for db in get_async_db():
      repo = StrategyRepository(db)
      strategy_models = await repo.get_all_strategies()

      for model in strategy_models:
        strategy = Strategy.from_model(model)
        strategies.append(strategy)

    return strategies

  @staticmethod
  async def get_strategy(strategy_id: int) -> Optional[Strategy]:
    """获取单个策略模板"""
    async for db in get_async_db():
      repo = StrategyRepository(db)
      model = await repo.get_strategy(strategy_id)

      if model:
        return Strategy.from_model(model)

    return None

  @staticmethod
  async def get_strategy_definitions() -> List[StrategyDefinition]:
    definitions = []
    async for db in get_async_db():
      repo = StrategyRepository(db)
      strategy_models = await repo.get_all_strategies()
      definitions = [StrategyDefinition.from_strategy(model) for model in strategy_models]
      break
    return definitions

  @staticmethod
  async def get_strategy_instances(
    status: Optional[str] = None,
    strategy_key: Optional[str] = None,
    instrument_code: Optional[str] = None,
  ) -> List[StrategyInstance]:
    instances: List[StrategyInstance] = []
    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      runs = await run_repo.find_all_strategy_runs()
      for run in runs:
        if status and str(getattr(run.status, "value", run.status)).lower() != status.lower():
          continue
        if strategy_key:
          run_key = run.strategy.name if run.strategy else str(run.strategy_id)
          if str(run_key) != strategy_key and str(run.strategy_id) != strategy_key:
            continue
        if instrument_code:
          code = StrategyResolver._normalize_instrument_code(instrument_code)
          if code not in [StrategyResolver._normalize_instrument_code(item) for item in (run.instruments or [])]:
            continue
        instances.append(await StrategyResolver._instance_from_run_model(db, run))
      break
    return instances

  @staticmethod
  async def get_strategy_instance(instance_id: str) -> Optional[StrategyInstance]:
    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      run = await run_repo.find_run_by_id(instance_id)
      if run:
        return await StrategyResolver._instance_from_run_model(db, run)
      break
    return None

  @staticmethod
  async def get_strategy_decision_history(
    instance_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    backtest_id: Optional[str] = None,
  ) -> List[StrategyDecision]:
    from repositories.strategy_decision_trace_repository import StrategyDecisionTraceRepository

    async for db in get_async_db():
      if backtest_id:
        backtest = await StrategyResolver._resolve_backtest_for_details(
          db,
          instance_id,
          backtest_id,
        )
        file_path = StrategyResolver._resolve_backtest_result_path(
          getattr(backtest, "result_path", None)
        )
        if file_path:
          records = StrategyResolver._load_backtest_decision_records(
            file_path,
            limit=limit,
          )
          return [StrategyDecision.from_backtest_record(record) for record in records]

      repo = StrategyDecisionTraceRepository(db)
      records = await repo.find_by_strategy_run(instance_id, cursor=cursor, limit=limit)
      output_records = [
        record for record in records
        if "strategy_output" in list((record.decision_trace or {}).get("tags") or [])
      ]
      selected = output_records or records
      if selected and any(record.trade_intents for record in selected):
        return [StrategyDecision.from_record(record) for record in selected]

      backtest = await StrategyResolver._resolve_backtest_for_details(db, instance_id)
      file_path = StrategyResolver._resolve_backtest_result_path(
        getattr(backtest, "result_path", None)
      )
      if file_path:
        records = StrategyResolver._load_backtest_decision_records(
          file_path,
          limit=limit,
        )
        if records:
          return [StrategyDecision.from_backtest_record(record) for record in records]
      return [StrategyDecision.from_record(record) for record in selected]
    return []

  @staticmethod
  async def get_strategy_execution_trace(
    instance_id: str,
    decision_id: Optional[str] = None,
    backtest_id: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
  ) -> List[ExecutionTraceView]:
    from repositories.strategy_decision_trace_repository import StrategyDecisionTraceRepository
    from repositories.trade_intent_repository import TradeIntentRepository

    async for db in get_async_db():
      if backtest_id:
        backtest = await StrategyResolver._resolve_backtest_for_details(
          db,
          instance_id,
          backtest_id,
        )
        file_path = StrategyResolver._resolve_backtest_result_path(
          getattr(backtest, "result_path", None)
        )
        if file_path:
          intent_ids = None
          if decision_id:
            intent_ids = StrategyResolver._find_backtest_decision_intent_ids(
              file_path,
              decision_id,
            )
          records = StrategyResolver._load_backtest_intent_records(
            file_path,
            intent_ids=intent_ids,
            limit=limit,
          )
          return [ExecutionTraceView.from_backtest_intent(record) for record in records]

      intent_repo = TradeIntentRepository(db)
      trace_id = None
      if decision_id:
        decision_repo = StrategyDecisionTraceRepository(db)
        decisions = await decision_repo.find_by_strategy_run(instance_id, limit=200)
        for decision in decisions:
          if decision.id == decision_id or decision.trace_id == decision_id:
            trace_id = decision.trace_id
            break
      records = (
        await intent_repo.find_by_trace_id(instance_id, trace_id)
        if trace_id
        else await intent_repo.find_recent_by_strategy_run(instance_id, limit=limit)
      )
      if records:
        return [ExecutionTraceView.from_intent(record) for record in records]

      backtest = await StrategyResolver._resolve_backtest_for_details(db, instance_id)
      file_path = StrategyResolver._resolve_backtest_result_path(
        getattr(backtest, "result_path", None)
      )
      if file_path:
        intent_ids = None
        if decision_id:
          intent_ids = StrategyResolver._find_backtest_decision_intent_ids(
            file_path,
            decision_id,
          )
        records = StrategyResolver._load_backtest_intent_records(
          file_path,
          intent_ids=intent_ids,
          limit=limit,
        )
        if records:
          return [ExecutionTraceView.from_backtest_intent(record) for record in records]
      return [ExecutionTraceView.from_intent(record) for record in records]
    return []

  @staticmethod
  async def get_strategy_execution_logs(
    run_id: str,
    *,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
    cursor: Optional[int] = None,
    limit: int = 200,
    before: bool = False,
    tail: bool = True,
  ) -> StrategyLogPage:
    source = await StrategyResolver._resolve_strategy_log_source(
      run_id,
      backtest_id=backtest_id,
      version=version,
    )
    page = StrategyResolver._read_strategy_log_page(
      run_id=run_id,
      file_path=source.get("path"),
      record_type=source.get("record_type"),
      cursor=cursor,
      limit=limit,
      before=before,
      tail=tail,
    )
    return StrategyLogPage(
      run_id=run_id,
      mode=source.get("mode"),
      backtest_id=source.get("backtest_id"),
      backtest_version=source.get("backtest_version"),
      source_path=StrategyResolver._relative_path(source.get("path")),
      entries=page["entries"],
      start_cursor=page["start_cursor"],
      end_cursor=page["end_cursor"],
      has_previous_page=page["has_previous_page"],
      has_next_page=page["has_next_page"],
      total_lines=page["total_lines"],
      file_size_bytes=page["file_size_bytes"],
    )

  @staticmethod
  async def get_strategy_bucket_ledger(instance_id: str) -> BucketLedgerView:
    from core.runtime_state_manager import BUCKET_LEDGER_CUSTOM_STATE_KEY
    from repositories.strategy_run_state_repository import StrategyRunStateRepository

    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      run = await run_repo.find_run_by_id(instance_id)
      state_repo = StrategyRunStateRepository(db)
      state_record = await state_repo.get_state(instance_id)
      ledger = {}
      if state_record:
        ledger = dict(state_record.custom_state or {}).get(BUCKET_LEDGER_CUSTOM_STATE_KEY) or {}
      instrument_code = (run.instruments or [""])[0] if run else ""
      buckets = dict((ledger.get("instruments") or {}).get(instrument_code, {}) or {})
      def _volume(bucket: str) -> float:
        data = dict(buckets.get(bucket, {}) or {})
        return float(data.get("total_volume", 0) or 0)
      return BucketLedgerView(
        locked_core=_volume("locked_core"),
        core=_volume("core"),
        swing=_volume("swing"),
        updated_at=state_record.updated_at if state_record else None,
        raw=ledger,
      )
    return BucketLedgerView()

  @staticmethod
  async def get_strategy_grid_book(
    instance_id: str,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
  ):
    from ..types import StrategyGridBook

    snapshot = await StrategyResolver._load_grid_book_snapshot(
      instance_id,
      backtest_id=backtest_id,
      version=version,
    )
    return StrategyGridBook.from_dict(snapshot)

  @staticmethod
  async def get_strategy_performance(
    run_id: str,
    backtest_id: Optional[str] = None,
    benchmark_code: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 2000,
  ):
    from core.strategy_performance import StrategyPerformanceService
    from ..types import StrategyPerformance

    performance = await StrategyPerformanceService.get_performance(
      run_id=run_id,
      backtest_id=backtest_id,
      benchmark_code=benchmark_code,
      cursor=cursor,
      limit=limit,
    )
    return StrategyPerformance.from_dict(performance)

  @staticmethod
  async def update_strategy_grid_book(instance_id: str, input):
    from core.grid_book import (
      GRID_BOOK_CUSTOM_STATE_KEY,
      LOCKED_GRID_BOOK_STATUSES,
      build_grid_book_from_parameters,
      grid_book_levels_to_parameters,
      normalize_grid_book,
      normalize_level,
      now_iso,
    )
    from repositories.strategy_run_state_repository import StrategyRunStateRepository
    from repositories.strategy_grid_book_snapshot_repository import (
      StrategyGridBookSnapshotRepository,
    )
    from ..types import StrategyGridBook

    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      run = await run_repo.find_run_by_id(instance_id)
      if not run:
        raise ValueError(f"未找到策略实例: {instance_id}")

      status_value = str(getattr(run.status, "value", run.status)).lower()
      if status_value == "running":
        raise ValueError("运行中的 GridBook 不可维护，请先暂停实例")

      parameters = StrategyResolver._json_object(run.parameters)
      original_instrument = (
        (run.instruments or [None])[0]
        or parameters.get("instrument_code")
        or parameters.get("instrumentCode")
        or parameters.get("symbol")
        or ""
      )
      instrument_code = StrategyResolver._normalize_instrument_code(original_instrument)
      state_repo = StrategyRunStateRepository(db)
      state_record = await state_repo.get_state(instance_id)
      custom_state = dict(state_record.custom_state or {}) if state_record else {}
      current_snapshot = custom_state.get(GRID_BOOK_CUSTOM_STATE_KEY)
      if not current_snapshot:
        current_snapshot = build_grid_book_from_parameters(
          run_id=instance_id,
          instrument_code=instrument_code,
          parameters=parameters,
          editable=True,
        )
      current_snapshot = normalize_grid_book(
        current_snapshot,
        run_id=instance_id,
        instrument_code=instrument_code,
        parameters=parameters,
        editable=True,
      )
      existing_levels = list(current_snapshot.get("levels") or [])
      existing_by_id = {
        str(level.get("grid_id")): level
        for level in existing_levels
        if level.get("grid_id")
      }
      existing_by_index = {
        int(level.get("level_index", 0) or 0): level
        for level in existing_levels
      }
      incoming_levels = [
        StrategyResolver._grid_book_input_to_level(level, index)
        for index, level in enumerate(list(input.levels or []))
      ]
      incoming_keys = {
        str(level.get("grid_id")) for level in incoming_levels if level.get("grid_id")
      }
      incoming_indexes = {
        int(level.get("level_index", 0) or 0) for level in incoming_levels
      }
      for level in existing_levels:
        status = str(level.get("status") or "").upper()
        if status not in LOCKED_GRID_BOOK_STATUSES:
          continue
        grid_id = str(level.get("grid_id") or "")
        level_index = int(level.get("level_index", 0) or 0)
        if grid_id not in incoming_keys and level_index not in incoming_indexes:
          raise ValueError("待成交、部分成交或已成交档位不可删除")

      updated_at = now_iso()
      updated_levels = []
      for index, raw in enumerate(incoming_levels):
        normalized = normalize_level(raw, index)
        existing = (
          existing_by_id.get(str(normalized.get("grid_id")))
          or existing_by_index.get(int(normalized.get("level_index", index) or index))
          or {}
        )
        existing_status = str(existing.get("status") or "").upper()
        if existing_status in LOCKED_GRID_BOOK_STATUSES:
          for key in ["side", "price", "planned_shares", "enabled"]:
            if existing.get(key) != normalized.get(key):
              raise ValueError("待成交、部分成交或已成交档位不可修改计划字段")
          normalized = dict(existing)
        else:
          normalized["status"] = "DISABLED" if not normalized.get("enabled", True) else "PLANNED"
          normalized["monitoring"] = False
          normalized["pending_shares"] = 0
          normalized["filled_shares"] = 0
          normalized["order_id"] = None
          normalized["entry_price"] = None
          normalized["entry_time"] = None
          normalized["last_intent_id"] = None
          normalized["last_trace_id"] = None
          normalized["reason"] = "grid_book_updated"
        normalized["updated_at"] = updated_at
        updated_levels.append(normalized)

      mode_value = str(getattr(run.mode, "value", run.mode)).lower()
      needs_backtest = mode_value == "backtest"
      next_version = int(current_snapshot.get("version", 1) or 1) + 1
      next_parameter_version = int(parameters.get("_parameter_version") or 1) + 1
      snapshot = normalize_grid_book(
        {
          "run_id": instance_id,
          "instrument_code": instrument_code,
          "base_price": input.base_price if input.base_price is not None else parameters.get("base_price"),
          "parameter_version": str(next_parameter_version),
          "version": next_version,
          "model_version": current_snapshot.get("model_version", 2),
          "inventory_model": current_snapshot.get("inventory_model", "INVENTORY_LEDGER_GRID"),
          "release_rule": current_snapshot.get("release_rule", "NEAREST_LOWER"),
          "sell_empty_behavior": current_snapshot.get("sell_empty_behavior", "WAIT_FOR_INVENTORY"),
          "editable": True,
          "needs_backtest": needs_backtest,
          "levels": updated_levels,
          "inventory_lots": list(current_snapshot.get("inventory_lots") or []),
          "release_events": list(current_snapshot.get("release_events") or []),
          "updated_at": updated_at,
        },
        run_id=instance_id,
        instrument_code=instrument_code,
        parameters=parameters,
        editable=True,
        needs_backtest=needs_backtest,
      )

      parameters["grid_levels"] = grid_book_levels_to_parameters(snapshot["levels"])
      parameters["base_price"] = snapshot["base_price"]
      parameters["instrument_code"] = instrument_code
      parameters["stockCodes"] = [instrument_code]
      parameters["_parameter_version"] = str(next_parameter_version)
      if needs_backtest:
        parameters["_grid_book_needs_backtest"] = True

      await run_repo.update_run(instance_id, {"parameters": parameters})
      snapshot_repo = StrategyGridBookSnapshotRepository(db)
      if needs_backtest:
        await snapshot_repo.upsert_template(
          strategy_run_id=instance_id,
          snapshot=snapshot,
          mode=mode_value.upper(),
          note="grid_book_template_updated",
        )
      else:
        custom_state[GRID_BOOK_CUSTOM_STATE_KEY] = snapshot
        await state_repo.upsert_state(
          run_id=instance_id,
          cash=float(getattr(state_record, "cash", 0.0) or 0.0),
          frozen_cash=float(getattr(state_record, "frozen_cash", 0.0) or 0.0),
          total_asset=float(getattr(state_record, "total_asset", 0.0) or 0.0),
          custom_state=custom_state,
          expected_version=getattr(state_record, "version", None),
        )
        await snapshot_repo.upsert_current(
          strategy_run_id=instance_id,
          snapshot=snapshot,
          mode=mode_value.upper(),
          note="grid_book_updated",
        )

      runtime = strategy_manager.get_run(instance_id)
      if runtime:
        runtime.context.parameters = dict(parameters)
        if runtime.state_manager:
          runtime.state_manager.set_custom(GRID_BOOK_CUSTOM_STATE_KEY, snapshot)
        if runtime.strategy and hasattr(runtime.strategy, "apply_grid_book_snapshot"):
          runtime.strategy.apply_grid_book_snapshot(snapshot)

      return StrategyGridBook.from_dict(snapshot)

    raise ValueError(f"未找到策略实例: {instance_id}")

  @staticmethod
  def _grid_book_input_to_level(level, index: int) -> Dict[str, Any]:
    grid_id = getattr(level, "grid_id", None) or f"grid-{getattr(level, 'level_index', index)}-{getattr(level, 'side', 'LEVEL')}"
    price = float(getattr(level, "price", 0.0) or 0.0)
    planned_shares = int(getattr(level, "planned_shares", 0) or 0)
    return {
      "grid_id": str(grid_id),
      "level_index": int(getattr(level, "level_index", index) or index),
      "side": str(getattr(level, "side", "") or "").upper(),
      "price": price,
      "planned_shares": planned_shares,
      "amount": price * planned_shares,
      "pct_from_base": getattr(level, "pct_from_base", None),
      "expected_profit": getattr(level, "expected_profit", None),
      "enabled": bool(getattr(level, "enabled", True)),
    }

  @staticmethod
  async def _load_grid_book_snapshot(
    instance_id: str,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
  ) -> Dict[str, Any]:
    from core.grid_book import (
      GRID_BOOK_CUSTOM_STATE_KEY,
      build_grid_book_from_parameters,
      grid_book_to_template_snapshot,
      normalize_grid_book,
    )
    from repositories.strategy_run_state_repository import StrategyRunStateRepository
    from repositories.strategy_grid_book_snapshot_repository import (
      StrategyGridBookSnapshotRepository,
    )

    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      run = await run_repo.find_run_by_id(instance_id)
      if not run:
        raise ValueError(f"未找到策略实例: {instance_id}")
      parameters = StrategyResolver._json_object(run.parameters)
      instrument_code = StrategyResolver._normalize_instrument_code(
        (run.instruments or [None])[0]
        or parameters.get("instrument_code")
        or parameters.get("instrumentCode")
        or parameters.get("symbol")
        or ""
      )
      status_value = str(getattr(run.status, "value", run.status)).lower()
      mode_value = str(getattr(run.mode, "value", run.mode)).lower()
      editable = status_value != "running"

      if mode_value == "backtest" and (backtest_id or version is not None):
        backtest, snapshot = await StrategyResolver._backtest_grid_book_snapshot(
          db,
          instance_id,
          backtest_id=backtest_id,
          version=version,
        )
        if backtest:
          backtest_parameters = StrategyResolver._json_object(backtest.parameters)
          backtest_instrument = StrategyResolver._normalize_instrument_code(
            (backtest.instruments or [None])[0]
            or backtest_parameters.get("instrument_code")
            or backtest_parameters.get("instrumentCode")
            or backtest_parameters.get("symbol")
            or instrument_code
          )
          if not snapshot:
            snapshot = build_grid_book_from_parameters(
              run_id=instance_id,
              instrument_code=backtest_instrument,
              parameters=backtest_parameters,
              editable=False,
              needs_backtest=False,
            )
          return normalize_grid_book(
            snapshot,
            run_id=instance_id,
            instrument_code=backtest_instrument,
            parameters=backtest_parameters,
            editable=False,
            needs_backtest=False,
          )

      snapshot_repo = StrategyGridBookSnapshotRepository(db)
      snapshot = None
      if mode_value == "backtest":
        template_record = await snapshot_repo.get_template(instance_id)
        if template_record:
          snapshot = dict(template_record.snapshot or {})
        else:
          latest_snapshot = await StrategyResolver._latest_backtest_grid_book_snapshot(
            db,
            instance_id,
          )
          if latest_snapshot:
            snapshot = grid_book_to_template_snapshot(
              latest_snapshot,
              run_id=instance_id,
              instrument_code=instrument_code,
              parameters=parameters,
              needs_backtest=False,
            )
            await snapshot_repo.upsert_template(
              strategy_run_id=instance_id,
              snapshot=snapshot,
              mode="BACKTEST",
              note="template_bootstrap_from_latest_backtest",
            )
      else:
        state_repo = StrategyRunStateRepository(db)
        state_record = await state_repo.get_state(instance_id)
        custom_state = dict(state_record.custom_state or {}) if state_record else {}
        snapshot = custom_state.get(GRID_BOOK_CUSTOM_STATE_KEY)
        if not snapshot:
          current_record = await snapshot_repo.get_current(instance_id)
          if current_record:
            snapshot = dict(current_record.snapshot or {})

      if not snapshot:
        snapshot = build_grid_book_from_parameters(
          run_id=instance_id,
          instrument_code=instrument_code,
          parameters=parameters,
          editable=editable,
          needs_backtest=bool(parameters.get("_grid_book_needs_backtest")),
        )
      return normalize_grid_book(
        snapshot,
        run_id=instance_id,
        instrument_code=instrument_code,
        parameters=parameters,
        editable=editable,
        needs_backtest=bool(parameters.get("_grid_book_needs_backtest")),
      )
    raise ValueError(f"未找到策略实例: {instance_id}")

  @staticmethod
  def _backtest_result_path_candidates(raw_path: str) -> List[str]:
    import os

    if not raw_path:
      return []
    return [
      raw_path,
      os.path.join("data", raw_path),
      os.path.join("data", "backtests", os.path.basename(raw_path)),
    ]

  @staticmethod
  async def _backtest_grid_book_snapshot(
    db,
    instance_id: str,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
  ):
    from core.backtest_result_storage import BacktestResultStorage
    from repositories.backtest_repository import BacktestRepository
    from repositories.strategy_grid_book_snapshot_repository import (
      StrategyGridBookSnapshotRepository,
    )

    repo = BacktestRepository(db)
    if backtest_id:
      backtest = await repo.get_backtest(backtest_id)
      if not backtest or backtest.strategy_run_id != instance_id:
        return None, None
    elif version is not None:
      backtest = await repo.get_backtest_by_run_version(instance_id, version)
    else:
      return None, None

    if not backtest:
      return None, None

    snapshot_repo = StrategyGridBookSnapshotRepository(db)
    snapshot_record = await snapshot_repo.get_backtest_final(backtest.id)
    if snapshot_record:
      return backtest, dict(snapshot_record.snapshot or {})

    raw_path = getattr(backtest, "result_path", None)
    for path in StrategyResolver._backtest_result_path_candidates(raw_path):
      snapshot = await BacktestResultStorage.load_latest_grid_book_snapshot(path)
      if snapshot:
        await snapshot_repo.upsert_backtest_final(
          strategy_run_id=instance_id,
          backtest_id=backtest.id,
          backtest_version=int(getattr(backtest, "version", 0) or 0),
          snapshot=snapshot,
          source_path=path,
        )
        return backtest, snapshot
    return backtest, None

  @staticmethod
  async def _latest_backtest_grid_book_snapshot(db, instance_id: str) -> Optional[Dict[str, Any]]:
    from core.backtest_result_storage import BacktestResultStorage
    from repositories.backtest_repository import BacktestRepository
    from repositories.strategy_grid_book_snapshot_repository import (
      StrategyGridBookSnapshotRepository,
    )

    snapshot_record = await StrategyGridBookSnapshotRepository(
      db
    ).get_latest_backtest_final(instance_id)
    if snapshot_record:
      return dict(snapshot_record.snapshot or {})

    repo = BacktestRepository(db)
    backtests = await repo.get_backtests_by_run(instance_id)
    for backtest in sorted(backtests, key=lambda item: getattr(item, "version", 0) or 0, reverse=True):
      raw_path = getattr(backtest, "result_path", None)
      if not raw_path:
        continue
      for path in StrategyResolver._backtest_result_path_candidates(raw_path):
        snapshot = await BacktestResultStorage.load_latest_grid_book_snapshot(path)
        if snapshot:
          await StrategyGridBookSnapshotRepository(db).upsert_backtest_final(
            strategy_run_id=instance_id,
            backtest_id=backtest.id,
            backtest_version=int(getattr(backtest, "version", 0) or 0),
            snapshot=snapshot,
            source_path=path,
          )
          return snapshot
    return None

  @staticmethod
  async def get_strategy_runs() -> List[StrategyRun]:
    """获取所有策略运行"""
    runs = []
    strategy_cache = {}

    # 从内存中获取运行中的实例
    for runtime in strategy_manager.get_all_runs():
      # 获取 Strategy 对象
      if runtime.strategy_id not in strategy_cache:
        strategy_cache[runtime.strategy_id] = await StrategyResolver._get_strategy_by_id(runtime.strategy_id)
      strategy = strategy_cache[runtime.strategy_id]

      runs.append(
        StrategyRun(
          id=runtime.run_id,
          name=runtime.name or f"Strategy-{runtime.strategy_id}",
          strategy=strategy,
          mode=runtime.mode,
          instruments=runtime.instruments,
          parameters=runtime.parameters,
          status=StrategyResolver._runtime_status_to_model(runtime.status.value),
          start_time=runtime.start_time,
          stop_time=runtime.stop_time,
          metrics=StrategyResolver._serialize_metrics(runtime.metrics),
          error_message=runtime.error_message,
          create_time=time_utils.now(),
        )
      )

    # 从数据库获取历史实例
    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      db_runs = await run_repo.find_all_strategy_runs()

      for model in db_runs:
        # 跳过已经在内存中的运行
        if not any(run.id == model.id for run in runs):
          # 获取 Strategy 对象
          if model.strategy_id not in strategy_cache:
            strategy_cache[model.strategy_id] = await StrategyResolver._get_strategy_by_id(model.strategy_id)
          strategy = strategy_cache[model.strategy_id]

          runs.append(
            StrategyRun(
              id=model.id,
              name=model.name,
              strategy=strategy,
              mode=model.mode if model.mode else StrategyRunMode.BACKTEST,
              instruments=model.instruments or [],
              parameters=model.parameters or {},
              status=model.status,
              start_time=model.start_time,
              stop_time=model.stop_time,
              metrics=StrategyResolver._serialize_metrics(model.metrics),
              error_message=model.error_message,
              create_time=model.created_at,
            )
          )

    return runs

  @staticmethod
  async def get_strategy_run(run_id: str) -> Optional[StrategyRun]:
    """获取单个策略运行"""
    # 先从内存查找
    runtime = strategy_manager.get_run(run_id)
    if runtime:
      strategy = await StrategyResolver._get_strategy_by_id(runtime.strategy_id)
      return StrategyRun(
        id=runtime.run_id,
        name=runtime.name or f"Strategy-{runtime.strategy_id}",
        strategy=strategy,
        mode=runtime.mode,
        instruments=runtime.instruments,
        parameters=runtime.parameters,
        status=StrategyResolver._runtime_status_to_model(runtime.status.value),
        start_time=runtime.start_time,
        stop_time=runtime.stop_time,
        metrics=StrategyResolver._serialize_metrics(runtime.metrics),
        error_message=runtime.error_message,
        create_time=time_utils.now(),
      )

    # 从数据库查找
    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      model = await run_repo.find_run_by_id(run_id)

      if model:
        strategy = await StrategyResolver._get_strategy_by_id(model.strategy_id)
        return StrategyRun(
          id=model.id,
          name=model.name,
          strategy=strategy,
          mode=model.mode if model.mode else StrategyRunMode.BACKTEST,
          instruments=model.instruments or [],
          parameters=model.parameters or {},
          status=model.status,
          start_time=model.start_time,
          stop_time=model.stop_time,
          metrics=StrategyResolver._serialize_metrics(model.metrics),
          error_message=model.error_message,
          create_time=model.created_at,
        )

    return None

  @staticmethod
  async def run_strategy(
    run_input: StrategyRunInput, auto_start: bool = True
  ) -> StrategyRun:
    """运行策略（创建并启动）"""
    # 获取策略模板信息
    strategy = None
    async for db in get_async_db():
      repo = StrategyRepository(db)
      strategy = await repo.get_strategy(run_input.strategy_id)

    if not strategy:
      raise ValueError(f"策略模板 {run_input.strategy_id} 不存在")

    # 动态加载策略类
    try:
      strategy_class = strategy_registry.get_strategy_class(
        strategy.class_name, strategy.file_path
      )
    except ValueError as e:
      raise ValueError(f"加载策略类失败: {e}") from e

    # 参数已是字典类型,无需解析
    parameters = StrategyResolver._json_object(run_input.parameters)
    instruments = StrategyResolver._validate_strategy_instruments(
      strategy,
      strategy_class,
      run_input.instruments,
    )
    if instruments:
      parameters.setdefault("instrument_code", instruments[0])
      parameters.setdefault("stockCodes", instruments)

    # 验证回测模式的时间参数
    if run_input.mode == StrategyRunMode.BACKTEST:
      if not run_input.start_time or not run_input.end_time:
        raise ValueError("回测模式必须指定开始和结束时间")

    # 运行策略
    run_id = await strategy_manager.run_strategy(
      strategy_id=run_input.strategy_id,
      strategy_class=strategy_class,
      mode=run_input.mode,
      instruments=instruments,
      parameters=parameters,
      name=run_input.name,
      backtest_start_time=run_input.start_time,
      backtest_end_time=run_input.end_time,
      auto_start=auto_start,
    )

    # 返回运行信息
    runtime = strategy_manager.get_run(run_id)
    strategy_obj = Strategy.from_model(strategy)
    return StrategyRun(
      id=run_id,
      name=runtime.name or f"Strategy-{strategy.id}",
      strategy=strategy_obj,
      mode=run_input.mode,
      instruments=runtime.instruments,
      parameters=runtime.parameters,
      status=StrategyResolver._runtime_status_to_model(runtime.status.value),
      start_time=runtime.start_time,
      stop_time=runtime.stop_time,
      metrics=None,
      error_message=None,
      create_time=time_utils.now(),
    )

  @staticmethod
  async def create_strategy_instance(
    input: StrategyInstanceCreateInput,
    auto_start: bool = True,
  ) -> StrategyInstance:
    strategy = await StrategyResolver._find_strategy_by_key(input.strategy_key)
    if not strategy:
      raise ValueError(f"策略定义不存在: {input.strategy_key}")
    try:
      strategy_class = strategy_registry.get_strategy_class(
        strategy.class_name, strategy.file_path
      )
    except ValueError as e:
      raise ValueError(f"加载策略类失败: {e}") from e

    instrument_code = StrategyResolver._normalize_instrument_code(input.instrument_code)
    instruments = StrategyResolver._validate_strategy_instruments(
      strategy,
      strategy_class,
      [instrument_code],
    )
    parameters = StrategyResolver._json_object(input.parameters)
    parameters["instrument_code"] = instrument_code
    parameters["stockCodes"] = [instrument_code]
    parameters.setdefault("_parameter_version", "1")

    if input.mode == StrategyRunMode.BACKTEST and (not input.start_time or not input.end_time):
      raise ValueError("回测模式必须指定开始和结束时间")

    run_id = await strategy_manager.run_strategy(
      strategy_id=strategy.id,
      strategy_class=strategy_class,
      mode=input.mode,
      instruments=instruments,
      parameters=parameters,
      name=input.display_name or f"{strategy.name}-{instrument_code}",
      backtest_start_time=input.start_time,
      backtest_end_time=input.end_time,
      auto_start=auto_start,
    )
    instance = await StrategyResolver.get_strategy_instance(run_id)
    if not instance:
      raise ValueError(f"策略实例创建失败: {run_id}")
    return instance

  @staticmethod
  async def update_strategy_instance_parameters(
    instance_id: str,
    input: StrategyInstanceParameterUpdateInput,
  ) -> Optional[StrategyInstance]:
    runtime = strategy_manager.get_run(instance_id)
    async for db in get_async_db():
      repo = StrategyRunRepository(db)
      run = await repo.find_run_by_id(instance_id)
      if not run:
        return None
      current = StrategyResolver._json_object(run.parameters)
      new_parameters = StrategyResolver._json_object(input.parameters)
      original_instrument = (run.instruments or [current.get("instrument_code") or ""])[0]
      requested_instrument = (
        new_parameters.get("instrument_code")
        or new_parameters.get("instrumentCode")
        or original_instrument
      )
      if StrategyResolver._normalize_instrument_code(requested_instrument) != StrategyResolver._normalize_instrument_code(original_instrument):
        raise ValueError("策略实例已绑定标的不可修改；换股请复制为新实例")

      status_value = str(getattr(run.status, "value", run.status)).lower()
      mode_value = str(getattr(run.mode, "value", run.mode)).lower()
      is_running = status_value == StrategyRunStatus.RUNNING.value
      if is_running and not input.apply_immediately:
        current["_parameter_draft"] = new_parameters
      else:
        version = int(current.get("_parameter_version") or 1) + 1
        new_parameters["instrument_code"] = original_instrument
        new_parameters["stockCodes"] = [original_instrument]
        new_parameters["_parameter_version"] = str(version)
        if mode_value == StrategyRunMode.BACKTEST.value:
          new_parameters["_grid_book_needs_backtest"] = True
        current = new_parameters
        if runtime:
          runtime.context.parameters = dict(current)
          if runtime.strategy and status_value in {
            StrategyRunStatus.PAUSED.value,
            StrategyRunStatus.PENDING.value,
          }:
            runtime.strategy.context.parameters = dict(current)
      await repo.update_run(instance_id, {"parameters": current})
      if not (is_running and not input.apply_immediately):
        from core.grid_book import build_grid_book_from_parameters
        from repositories.strategy_grid_book_snapshot_repository import (
          StrategyGridBookSnapshotRepository,
        )

        snapshot = build_grid_book_from_parameters(
          run_id=instance_id,
          instrument_code=original_instrument,
          parameters=current,
          editable=status_value != StrategyRunStatus.RUNNING.value,
          needs_backtest=mode_value == StrategyRunMode.BACKTEST.value,
        )
        snapshot_repo = StrategyGridBookSnapshotRepository(db)
        if mode_value == StrategyRunMode.BACKTEST.value:
          await snapshot_repo.upsert_template(
            strategy_run_id=instance_id,
            snapshot=snapshot,
            mode=mode_value.upper(),
            note="parameters_updated",
          )
        else:
          await snapshot_repo.upsert_current(
            strategy_run_id=instance_id,
            snapshot=snapshot,
            mode=mode_value.upper(),
            note="parameters_updated",
          )
      return await StrategyResolver._instance_from_run_model(
        db,
        await repo.find_run_by_id(instance_id),
      )
    return None

  @staticmethod
  async def pause_strategy_instance(instance_id: str) -> OperationResult:
    return await StrategyResolver.pause_strategy(instance_id)

  @staticmethod
  async def resume_strategy_instance(instance_id: str) -> OperationResult:
    return await StrategyResolver.resume_strategy(instance_id)

  @staticmethod
  async def archive_strategy_instance(instance_id: str) -> Optional[StrategyInstance]:
    await StrategyResolver.stop_strategy(instance_id)
    return await StrategyResolver.get_strategy_instance(instance_id)

  @staticmethod
  async def clone_strategy_instance(
    source_id: str,
    instrument_code: str,
  ) -> StrategyInstance:
    async for db in get_async_db():
      repo = StrategyRunRepository(db)
      source = await repo.find_run_by_id(source_id)
      if not source:
        raise ValueError(f"未找到源策略实例: {source_id}")
      if not source.strategy:
        raise ValueError("源策略实例缺少策略定义")
      strategy_class = strategy_registry.get_strategy_class(
        source.strategy.class_name,
        source.strategy.file_path,
      )
      code = StrategyResolver._normalize_instrument_code(instrument_code)
      instruments = StrategyResolver._validate_strategy_instruments(
        source.strategy,
        strategy_class,
        [code],
      )
      parameters = StrategyResolver._json_object(source.parameters)
      parameters["instrument_code"] = code
      parameters["stockCodes"] = [code]
      parameters["_parameter_version"] = "1"
      run_id = await strategy_manager.run_strategy(
        strategy_id=source.strategy_id,
        strategy_class=strategy_class,
        mode=source.mode,
        instruments=instruments,
        parameters=parameters,
        name=f"{source.name}-Copy-{code}",
        auto_start=False,
      )
      instance = await StrategyResolver.get_strategy_instance(run_id)
      if not instance:
        raise ValueError(f"策略实例复制失败: {run_id}")
      return instance
    raise ValueError(f"未找到源策略实例: {source_id}")

  @staticmethod
  async def create_strategy_run(run_input: StrategyRunInput) -> StrategyRun:
    """@deprecated 使用 run_strategy() 替代。创建策略运行（不自动启动）"""
    return await StrategyResolver.run_strategy(run_input, auto_start=False)

  @staticmethod
  async def update_strategy_run(
    run_id: str, run_update: StrategyRunUpdateInput
  ) -> Optional[StrategyRun]:
    """更新策略运行参数"""
    # 目前只支持更新参数，运行后不允许更新
    runtime = strategy_manager.get_run(run_id)
    if not runtime:
      return None

    if runtime.status.value != "pending":
      raise ValueError("只能更新未启动的策略运行")

    if run_update.parameters:
      runtime.parameters = run_update.parameters

    # TODO: 更新数据库

    strategy = await StrategyResolver._get_strategy_by_id(runtime.strategy_id)
    return StrategyRun(
      id=runtime.run_id,
      name=runtime.name or f"Strategy-{runtime.strategy_id}",
      strategy=strategy,
      mode=runtime.mode,
      instruments=runtime.instruments,
      parameters=runtime.parameters,
      status=StrategyResolver._runtime_status_to_model(runtime.status.value),
      start_time=runtime.start_time,
      stop_time=runtime.stop_time,
      metrics=StrategyResolver._serialize_metrics(runtime.metrics),
      error_message=runtime.error_message,
      create_time=time_utils.now(),
    )

  @staticmethod
  async def start_strategy(run_id: str) -> OperationResult:
    """启动或重启策略"""
    try:
      success = await strategy_manager.start(run_id)
      if success:
        return OperationResult(success=True, message=f"策略 {run_id} 已启动")
      else:
        return OperationResult(success=False, message=f"策略 {run_id} 启动失败")
    except Exception as e:
      logger.error(f"启动策略失败: {e}")
      return OperationResult(success=False, message=str(e))

  @staticmethod
  async def stop_strategy(run_id: str) -> OperationResult:
    """停止策略"""
    try:
      success = await strategy_manager.stop_strategy(run_id)
      if success:
        return OperationResult(success=True, message=f"策略 {run_id} 已停止")
      else:
        return OperationResult(success=False, message=f"策略 {run_id} 停止失败")
    except Exception as e:
      logger.error(f"停止策略失败: {e}")
      return OperationResult(success=False, message=str(e))

  @staticmethod
  async def pause_strategy(run_id: str) -> OperationResult:
    """暂停策略"""
    try:
      success = await strategy_manager.pause_strategy(run_id)
      if success:
        return OperationResult(success=True, message=f"策略 {run_id} 已暂停")
      else:
        return OperationResult(success=False, message=f"策略 {run_id} 暂停失败")
    except Exception as e:
      logger.error(f"暂停策略失败: {e}")
      return OperationResult(success=False, message=str(e))

  @staticmethod
  async def resume_strategy(run_id: str) -> OperationResult:
    """恢复策略"""
    try:
      success = await strategy_manager.resume_strategy(run_id)
      if success:
        return OperationResult(success=True, message=f"策略 {run_id} 已恢复")
      else:
        return OperationResult(success=False, message=f"策略 {run_id} 恢复失败")
    except Exception as e:
      logger.error(f"恢复策略失败: {e}")
      return OperationResult(success=False, message=str(e))

  # 向后兼容方法（标记为废弃）
  @staticmethod
  async def start_strategy_run(run_id: str) -> OperationResult:
    """@deprecated 使用 start_strategy() 替代"""
    return await StrategyResolver.start_strategy(run_id)

  @staticmethod
  async def stop_strategy_run(run_id: str) -> OperationResult:
    """@deprecated 使用 stop_strategy() 替代"""
    return await StrategyResolver.stop_strategy(run_id)

  @staticmethod
  async def pause_strategy_run(run_id: str) -> OperationResult:
    """@deprecated 使用 pause_strategy() 替代"""
    return await StrategyResolver.pause_strategy(run_id)

  @staticmethod
  async def resume_strategy_run(run_id: str) -> OperationResult:
    """@deprecated 使用 resume_strategy() 替代"""
    return await StrategyResolver.resume_strategy(run_id)

  @staticmethod
  async def delete_strategy_run(run_id: str) -> MessageResponse:
    """删除策略运行（含内存和各表关联数据清理）"""
    try:
      # 1. 从内存和资源中清理
      await strategy_manager.executor.delete(run_id)

      # 2. 从数据库所有相关表中彻底清理
      async for db in get_async_db():
        run_repo = StrategyRunRepository(db)
        success = await run_repo.delete_run(run_id)

        if success:
          return MessageResponse(success=True, message=f"策略运行 {run_id} 及其所有关联数据已彻底清理")

      return MessageResponse(success=False, message=f"策略运行 {run_id} 清理失败")
    except Exception as e:
      logger.error(f"删除策略运行失败: {e}")
      return MessageResponse(success=False, message=str(e))

  @staticmethod
  async def restart_strategy(run_id: str) -> OperationResult:
    """重启策略运行"""
    try:
      success = await strategy_manager.restart_strategy(run_id)
      if success:
        return OperationResult(success=True, message=f"策略 {run_id} 已重启")
      else:
        return OperationResult(success=False, message=f"策略 {run_id} 重启失败")
    except Exception as e:
      logger.error(f"重启策略失败: {e}")
      return OperationResult(success=False, message=str(e))

  @staticmethod
  async def clone_strategy(
    run_id: str,
    target_mode: StrategyRunMode,
    parameter_overrides: Optional[Dict[str, Any]] = None,
  ) -> OperationResult:
    """克隆策略运行"""
    try:
      new_run_id = await strategy_manager.clone_strategy(
        run_id,
        target_mode,
        parameter_overrides=parameter_overrides,
      )
      if new_run_id:
        # 成功时 message 返回新的 run_id
        return OperationResult(success=True, message=str(new_run_id))
      else:
        return OperationResult(success=False, message="克隆策略失败")
    except Exception as e:
      logger.error(f"克隆策略失败: {e}")
      return OperationResult(success=False, message=str(e))

  @staticmethod
  async def rerun_backtest_version(
    run_id: str,
    backtest_start_time: Optional[datetime] = None,
    backtest_end_time: Optional[datetime] = None,
  ):
    """在同一个 StrategyRun 下新增一个回测版本并启动。"""
    from ..types import StrategyBacktest
    from repositories.backtest_repository import BacktestRepository

    try:
      backtest_id = await strategy_manager.rerun_backtest_version(
        run_id,
        backtest_start_time=backtest_start_time,
        backtest_end_time=backtest_end_time,
      )
      async for db in get_async_db():
        repo = BacktestRepository(db)
        backtest = await repo.get_backtest(backtest_id)
        if not backtest:
          raise ValueError(f"新回测版本不存在: {backtest_id}")
        return StrategyBacktest.from_model(backtest)
    except Exception as e:
      logger.error(f"创建回测新版本失败: {e}")
      raise

  @staticmethod
  async def delete_backtest_version(
    run_id: str,
    backtest_id: str,
  ) -> OperationResult:
    """删除指定 StrategyRun 下的一个回测版本。"""
    from repositories.backtest_repository import BacktestRepository

    try:
      async for db in get_async_db():
        repo = BacktestRepository(db)
        backtest = await repo.get_backtest(backtest_id)
        if not backtest:
          return OperationResult(success=False, message="回测版本不存在")
        if backtest.strategy_run_id != run_id:
          return OperationResult(success=False, message="回测版本不属于当前策略运行")

        status = str(backtest.status or "").upper()
        if status in {"RUNNING", "PENDING"}:
          return OperationResult(
            success=False,
            message="运行中或等待中的回测版本不可删除，请等待结束后再操作",
          )

        result_path = backtest.result_path
        version = int(backtest.version or 0)
        deleted = await repo.delete_backtest(backtest_id)
        if not deleted:
          return OperationResult(success=False, message="回测版本删除失败")

        deleted_artifacts = StrategyResolver._delete_backtest_artifacts(
          backtest_id,
          result_path,
        )
        data = json.dumps(
          {
            "backtest_id": backtest_id,
            "deleted_artifacts": len(deleted_artifacts),
          },
          ensure_ascii=False,
        )
        return OperationResult(
          success=True,
          message=f"已删除回测版本 v{version}",
          data=data,
        )
    except Exception as e:
      logger.error("删除回测版本失败: %s", e)
      return OperationResult(success=False, message=str(e))

  @staticmethod
  async def get_backtest_history(run_id: str):
    """获取某个 StrategyRun 的回测历史"""
    from ..types import StrategyBacktest
    from repositories.backtest_repository import BacktestRepository

    try:
      async for db in get_async_db():
        repo = BacktestRepository(db)
        backtests = await repo.get_backtests_by_run(run_id)
        return [StrategyBacktest.from_model(bt) for bt in backtests]
    except Exception as e:
      logger.error(f"获取回测历史失败: {e}")
      return []
