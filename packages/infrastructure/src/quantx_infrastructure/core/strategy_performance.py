"""Strategy performance sampling, aggregation, and snapshot export."""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Optional, Tuple

from quantx_domain.strategies.base import StrategyRunMode

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.strategy_backtest import StrategyBacktest
from quantx_infrastructure.repositories.backtest_repository import BacktestRepository
from quantx_infrastructure.repositories.strategy_performance_sample_repository import (
  StrategyPerformanceSampleRepository,
)
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)

logger = logging.getLogger(__name__)

PERFORMANCE_COMPRESSION_POLICY = "minute_close_execution_extrema_v1"
PERFORMANCE_SNAPSHOT_FORMAT = "strategy_performance_snapshot_v2"


def _as_float(value: Any, default: float = 0.0) -> float:
  try:
    if value is None:
      return default
    number = float(value)
    if math.isnan(number) or math.isinf(number):
      return default
    return number
  except (TypeError, ValueError):
    return default


def _as_int(value: Any, default: int = 0) -> int:
  try:
    if value is None:
      return default
    return int(value)
  except (TypeError, ValueError):
    return default


def _parse_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if isinstance(value, str) and value:
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
      return None
  return None


def _normalize_datetime(value: Any) -> datetime:
  parsed = _parse_datetime(value)
  if not parsed and hasattr(value, "to_pydatetime"):
    parsed = value.to_pydatetime()
  parsed = parsed or time_utils.now()
  if parsed.tzinfo is not None:
    parsed = parsed.replace(tzinfo=None)
  return parsed


def _metrics_dict(metrics: Any) -> Dict[str, Any]:
  if not metrics:
    return {}
  if isinstance(metrics, dict):
    return dict(metrics)
  if hasattr(metrics, "model_dump"):
    return dict(metrics.model_dump(mode="json"))
  if hasattr(metrics, "dict"):
    return dict(metrics.dict())
  return {}


def _sample_dict(sample: Any) -> Dict[str, Any]:
  if isinstance(sample, dict):
    return dict(sample)
  if hasattr(sample, "to_dict"):
    return sample.to_dict()
  return {}


def _sample_time(sample: Dict[str, Any]) -> datetime:
  return _parse_datetime(sample.get("timestamp")) or time_utils.now()


def _sample_sequence(sample: Dict[str, Any]) -> int:
  return _as_int(sample.get("sequence"), 0)


def _sample_minute_key(sample: Dict[str, Any]) -> str:
  return _sample_time(sample).strftime("%Y-%m-%dT%H:%M")


def _sample_value_key(sample: Dict[str, Any]) -> Tuple[float, float, float]:
  return (
    _as_float(sample.get("equity")),
    _as_float(sample.get("return_pct")),
    _as_float(sample.get("drawdown_pct")),
  )


def _duration_days(start: Optional[datetime], end: Optional[datetime]) -> float:
  if not start or not end or end <= start:
    return 0.0
  return max((end - start).total_seconds() / 86400.0, 0.0)


def _resolve_path(raw_path: Optional[str]) -> Optional[str]:
  if not raw_path:
    return None
  candidates = [
    raw_path,
    os.path.join("data", raw_path),
    os.path.join("data", "backtests", os.path.basename(raw_path)),
  ]
  for path in candidates:
    if os.path.exists(path):
      return path
  return None


@dataclass
class StrategyPerformanceRecorder:
  """Buffered event-level sampler for a running strategy."""

  run_id: str
  mode: StrategyRunMode
  backtest_id: Optional[str]
  initial_capital: float
  batch_size: int = 1000
  _buffer: List[Dict[str, Any]] = field(default_factory=list)
  _sequence: int = 0
  _peak_equity: float = 0.0
  _last_trade_total_pnl: float = 0.0
  _disabled: bool = False

  async def record(
    self,
    runtime: Any,
    event_type: str,
    event: Any = None,
    *,
    timestamp: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
  ) -> None:
    if self._disabled or not runtime or not getattr(runtime, "broker", None):
      return

    try:
      account_result = runtime.broker.get_account()
      account = (
        await account_result if inspect.isawaitable(account_result) else account_result
      )
    except Exception as exc:
      logger.debug("Skip performance sample; account unavailable: %s", exc)
      return

    now = _normalize_datetime(
      timestamp
      or getattr(event, "trade_time", None)
      or getattr(event, "time", None)
      or getattr(runtime.context, "current_time", None)
      or time_utils.now()
    )
    equity = _as_float(getattr(account, "total_asset", 0.0), self.initial_capital)
    cash = _as_float(getattr(account, "cash", 0.0), 0.0)
    market_value = _as_float(getattr(account, "market_value", 0.0), 0.0)
    total_pnl = _as_float(getattr(account, "total_pnl", equity - self.initial_capital))
    positions = getattr(account, "positions", {}) or {}
    unrealized_pnl = sum(_as_float(getattr(pos, "pnl", 0.0)) for pos in positions.values())
    realized_pnl = total_pnl - unrealized_pnl

    self._peak_equity = max(self._peak_equity or self.initial_capital, equity)
    drawdown_pct = (
      ((self._peak_equity - equity) / self._peak_equity) * 100.0
      if self._peak_equity > 0
      else 0.0
    )
    return_pct = (
      ((equity - self.initial_capital) / self.initial_capital) * 100.0
      if self.initial_capital > 0
      else 0.0
    )

    event_metadata = dict(metadata or {})
    request = getattr(event, "request", None)
    request_metadata = dict(getattr(request, "metadata", {}) or {})
    event_metadata.update(
      {
        key: value
        for key, value in {
          "trade_type": getattr(getattr(event, "trade_type", None), "value", getattr(event, "trade_type", None)),
          "price": getattr(event, "price", None),
          "volume": getattr(event, "volume", None),
          "status": getattr(getattr(event, "status", None), "value", getattr(event, "status", None)),
        }.items()
        if value is not None
      }
    )

    if event_type == "trade":
      event_metadata["trade_pnl_delta"] = total_pnl - self._last_trade_total_pnl
      self._last_trade_total_pnl = total_pnl

    self._sequence += 1
    self._buffer.append(
      {
        "run_id": self.run_id,
        "backtest_id": self.backtest_id,
        "mode": getattr(self.mode, "value", self.mode),
        "timestamp": now,
        "sequence": self._sequence,
        "event_type": event_type,
        "equity": equity,
        "cash": cash,
        "market_value": market_value,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "return_pct": return_pct,
        "drawdown_pct": drawdown_pct,
        "benchmark_return_pct": None,
        "intent_id": request_metadata.get("intent_id") or event_metadata.get("intent_id"),
        "order_id": getattr(event, "order_id", None) or event_metadata.get("order_id"),
        "trade_id": getattr(event, "trade_id", None) or event_metadata.get("trade_id"),
        "sample_metadata": event_metadata,
      }
    )

    if len(self._buffer) >= self.batch_size:
      await self.flush()

  async def flush(self) -> None:
    if self._disabled or not self._buffer:
      return
    batch = list(self._buffer)
    self._buffer.clear()
    try:
      async for db in get_async_db():
        repo = StrategyPerformanceSampleRepository(db)
        await repo.bulk_create(batch)
        break
    except Exception as exc:
      self._disabled = True
      logger.warning("Performance sampling disabled after persistence error: %s", exc)


class StrategyPerformanceService:
  """Build strategy performance views from DB samples or backtest snapshots."""

  @staticmethod
  def snapshot_relative_path(backtest_id: str) -> str:
    return f"backtests/performance/{backtest_id}/manifest.json"

  @staticmethod
  def snapshot_file_path(backtest_id: str) -> str:
    return os.path.join(
      "data",
      "backtests",
      "performance",
      backtest_id,
      "manifest.json",
    )

  @staticmethod
  def _write_jsonl(path: str, rows: Iterable[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
      for row in rows:
        fp.write(json.dumps(row, ensure_ascii=False, default=str))
        fp.write("\n")

  @classmethod
  def _write_indexed_snapshot(
    cls,
    file_path: str,
    performance: Dict[str, Any],
  ) -> None:
    snapshot_dir = os.path.dirname(file_path)
    os.makedirs(snapshot_dir, exist_ok=True)

    equity_curve = list(performance.get("equity_curve") or [])
    drawdown_curve = list(performance.get("drawdown_curve") or [])
    cls._write_jsonl(os.path.join(snapshot_dir, "equity_curve.jsonl"), equity_curve)
    cls._write_jsonl(
      os.path.join(snapshot_dir, "drawdown_curve.jsonl"),
      drawdown_curve,
    )

    manifest = {
      key: performance.get(key)
      for key in [
        "run_id",
        "backtest_id",
        "mode",
        "benchmark_code",
        "source",
        "generated_at",
        "summary_only",
        "summary",
        "risk",
        "trade_stats",
        "execution_quality",
        "monthly_returns",
        "data_quality",
        "page_info",
      ]
    }
    manifest.update(
      {
        "storage_format": PERFORMANCE_SNAPSHOT_FORMAT,
        "artifacts": {
          "equity_curve": {
            "path": "equity_curve.jsonl",
            "count": len(equity_curve),
          },
          "drawdown_curve": {
            "path": "drawdown_curve.jsonl",
            "count": len(drawdown_curve),
          },
        },
      }
    )

    with open(file_path, "w", encoding="utf-8") as fp:
      json.dump(manifest, fp, ensure_ascii=False, default=str)

  @classmethod
  async def finalize_backtest_snapshot(
    cls,
    *,
    run_id: str,
    backtest_id: str,
    mode: StrategyRunMode,
    metrics: Dict[str, Any],
  ) -> Tuple[str, Dict[str, Any]]:
    async for db in get_async_db():
      repo = StrategyPerformanceSampleRepository(db)
      samples = [sample.to_dict() for sample in await repo.list_by_backtest(backtest_id)]
      break
    else:
      samples = []

    performance = cls.build_performance(
      run_id=run_id,
      backtest_id=backtest_id,
      mode=getattr(mode, "value", mode),
      samples=samples,
      metrics=metrics,
      benchmark_code=None,
      source="backtest_snapshot",
      limit=None,
    )
    relative_path = cls.snapshot_relative_path(backtest_id)
    file_path = cls.snapshot_file_path(backtest_id)
    cls._write_indexed_snapshot(file_path, performance)

    async for db in get_async_db():
      repo = StrategyPerformanceSampleRepository(db)
      await repo.delete_by_backtest(backtest_id)
      break

    return relative_path, performance

  @classmethod
  async def get_performance(
    cls,
    *,
    run_id: str,
    backtest_id: Optional[str] = None,
    benchmark_code: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 2000,
  ) -> Dict[str, Any]:
    limit = max(1, min(int(limit or 2000), 10000))
    cursor_int = _as_int(cursor, 0) if cursor else None

    if backtest_id:
      async for db in get_async_db():
        backtest_repo = BacktestRepository(db)
        backtest = await backtest_repo.get_backtest(backtest_id)
        break
      if not backtest:
        raise ValueError(f"未找到回测版本: {backtest_id}")
      return cls._performance_from_backtest(
        backtest,
        benchmark_code=benchmark_code,
        cursor=cursor_int,
        limit=limit,
      )

    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      sample_repo = StrategyPerformanceSampleRepository(db)
      run = await run_repo.find_run_by_id(run_id)
      samples = [
        sample.to_dict()
        for sample in await sample_repo.list_by_run(
          run_id,
          cursor=cursor_int,
          limit=limit + 1,
        )
      ]
      break
    if not run:
      raise ValueError(f"未找到策略运行实例: {run_id}")

    return cls.build_performance(
      run_id=run_id,
      backtest_id=None,
      mode=getattr(run.mode, "value", run.mode),
      samples=samples,
      metrics=_metrics_dict(run.metrics),
      benchmark_code=benchmark_code,
      source="runtime_db",
      limit=limit,
    )

  @classmethod
  def _performance_from_backtest(
    cls,
    backtest: StrategyBacktest,
    *,
    benchmark_code: Optional[str],
    cursor: Optional[int],
    limit: int,
  ) -> Dict[str, Any]:
    metrics = _metrics_dict(backtest.metrics)
    indexed_path = cls.snapshot_file_path(str(backtest.id))
    if os.path.exists(indexed_path):
      with open(indexed_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
      if data.get("storage_format") == PERFORMANCE_SNAPSHOT_FORMAT:
        return cls._performance_from_indexed_snapshot(
          indexed_path,
          data,
          benchmark_code=benchmark_code,
          cursor=cursor,
          limit=limit,
        )

    path = metrics.get("performance_snapshot_path")
    resolved = _resolve_path(path)
    if resolved:
      with open(resolved, "r", encoding="utf-8") as fp:
        data = json.load(fp)
      if data.get("storage_format") == PERFORMANCE_SNAPSHOT_FORMAT:
        return cls._performance_from_indexed_snapshot(
          resolved,
          data,
          benchmark_code=benchmark_code,
          cursor=cursor,
          limit=limit,
        )
      try:
        cls._write_indexed_snapshot(indexed_path, data)
        with open(indexed_path, "r", encoding="utf-8") as fp:
          indexed_data = json.load(fp)
        return cls._performance_from_indexed_snapshot(
          indexed_path,
          indexed_data,
          benchmark_code=benchmark_code,
          cursor=cursor,
          limit=limit,
        )
      except Exception as exc:
        logger.warning("Failed to index legacy performance snapshot: %s", exc)
      data["benchmark_code"] = benchmark_code
      return cls.paginate_performance(data, cursor=cursor, limit=limit)

    return cls.build_performance(
      run_id=backtest.strategy_run_id,
      backtest_id=backtest.id,
      mode="backtest",
      samples=[],
      metrics=metrics,
      benchmark_code=benchmark_code,
      source="summary_metrics",
      limit=limit,
      summary_only=True,
      warning="该回测版本没有绩效快照，仅展示摘要指标。",
    )

  @staticmethod
  def _resolve_snapshot_artifact(
    manifest_path: str,
    manifest: Dict[str, Any],
    key: str,
  ) -> Optional[str]:
    artifact = (manifest.get("artifacts") or {}).get(key)
    artifact_path = artifact.get("path") if isinstance(artifact, dict) else artifact
    if not artifact_path:
      return None

    base_dir = os.path.abspath(os.path.dirname(manifest_path))
    path = os.path.abspath(os.path.join(base_dir, str(artifact_path)))
    if path != base_dir and not path.startswith(base_dir + os.sep):
      return None
    return path if os.path.exists(path) else None

  @staticmethod
  def _count_jsonl_rows(path: str) -> int:
    count = 0
    with open(path, "r", encoding="utf-8") as fp:
      for line in fp:
        if line.strip():
          count += 1
    return count

  @staticmethod
  def _sample_indexes(total: int, limit: int) -> List[int]:
    if total <= 0 or limit <= 0:
      return []
    if total <= limit:
      return list(range(total))
    if limit == 1:
      return [total - 1]

    step = (total - 1) / float(limit - 1)
    indexes: List[int] = []
    seen = set()
    for index in range(limit):
      source_index = min(round(index * step), total - 1)
      if source_index in seen:
        continue
      indexes.append(source_index)
      seen.add(source_index)
    if indexes and indexes[-1] != total - 1:
      indexes[-1] = total - 1
    return indexes

  @classmethod
  def _read_curve_points(
    cls,
    path: Optional[str],
    *,
    cursor: Optional[int],
    limit: int,
  ) -> Tuple[List[Dict[str, Any]], int, bool]:
    if not path or not os.path.exists(path):
      return [], 0, False

    if cursor is None:
      total = cls._count_jsonl_rows(path)
      selected_indexes = set(cls._sample_indexes(total, limit))
      points: List[Dict[str, Any]] = []
      if not selected_indexes:
        return points, total, False

      row_index = -1
      with open(path, "r", encoding="utf-8") as fp:
        for line in fp:
          if not line.strip():
            continue
          row_index += 1
          if row_index not in selected_indexes:
            continue
          points.append(json.loads(line))
      return points, total, False

    points = []
    has_more = False
    available = 0
    with open(path, "r", encoding="utf-8") as fp:
      for line in fp:
        if not line.strip():
          continue
        point = json.loads(line)
        if _as_int(point.get("sequence")) <= cursor:
          continue
        available += 1
        if len(points) >= limit:
          has_more = True
          break
        points.append(point)
    return points, available, has_more

  @classmethod
  def _performance_from_indexed_snapshot(
    cls,
    manifest_path: str,
    manifest: Dict[str, Any],
    *,
    benchmark_code: Optional[str],
    cursor: Optional[int],
    limit: int,
  ) -> Dict[str, Any]:
    data = dict(manifest or {})
    equity_path = cls._resolve_snapshot_artifact(
      manifest_path,
      data,
      "equity_curve",
    )
    drawdown_path = cls._resolve_snapshot_artifact(
      manifest_path,
      data,
      "drawdown_curve",
    )
    equity_curve, equity_count, has_more = cls._read_curve_points(
      equity_path,
      cursor=cursor,
      limit=limit,
    )
    drawdown_curve, drawdown_count, _ = cls._read_curve_points(
      drawdown_path,
      cursor=cursor,
      limit=limit,
    )

    data["benchmark_code"] = benchmark_code
    data["equity_curve"] = equity_curve
    data["drawdown_curve"] = drawdown_curve
    last = equity_curve[-1] if equity_curve else None
    data["page_info"] = {
      "has_more": has_more,
      "next_cursor": str(last.get("sequence")) if has_more and last else None,
    }

    quality = dict(data.get("data_quality") or {})
    visible_count = equity_count or drawdown_count
    total_count = (
      visible_count
      if cursor is None and visible_count
      else _as_int(quality.get("sample_count"), visible_count)
    )
    quality["sample_count"] = total_count
    quality["returned_sample_count"] = len(equity_curve)
    quality["truncated"] = bool(has_more or visible_count > len(equity_curve))
    quality["raw_sample_count"] = _as_int(
      quality.get("raw_sample_count"),
      total_count,
    )
    quality["compressed_sample_count"] = _as_int(
      quality.get("compressed_sample_count"),
      total_count,
    )
    quality["compression_policy"] = (
      quality.get("compression_policy") or PERFORMANCE_COMPRESSION_POLICY
    )
    if not equity_curve:
      quality["status"] = "SUMMARY_ONLY"
      quality["warning"] = quality.get("warning") or "绩效曲线快照不可用，仅展示摘要。"
      data["summary_only"] = True
    data["data_quality"] = quality
    return data

  @classmethod
  def build_performance(
    cls,
    *,
    run_id: str,
    backtest_id: Optional[str],
    mode: Any,
    samples: Iterable[Any],
    metrics: Dict[str, Any],
    benchmark_code: Optional[str],
    source: str,
    limit: Optional[int],
    summary_only: bool = False,
    warning: Optional[str] = None,
  ) -> Dict[str, Any]:
    normalized_samples = sorted(
      [_sample_dict(sample) for sample in samples if _sample_dict(sample)],
      key=lambda item: (_sample_sequence(item), _sample_time(item)),
    )
    metrics = _metrics_dict(metrics)
    broker_metrics = _metrics_dict(metrics.get("performance"))
    all_metrics = {**broker_metrics, **metrics}

    sample_count = len(normalized_samples)
    curve_samples = normalized_samples
    compressed_sample_count = sample_count
    compression_policy = None
    if source == "backtest_snapshot" and normalized_samples and limit is None:
      curve_samples = cls.compress_snapshot_samples(normalized_samples)
      compressed_sample_count = len(curve_samples)
      compression_policy = PERFORMANCE_COMPRESSION_POLICY

    curve_sample_count = len(curve_samples)
    truncated = bool(limit and curve_sample_count > limit)
    returned_samples = curve_samples[:limit] if limit else curve_samples
    next_cursor = (
      str(_sample_sequence(returned_samples[-1]))
      if limit and sample_count > limit and returned_samples
      else None
    )

    first_sample = normalized_samples[0] if normalized_samples else {}
    last_sample = normalized_samples[-1] if normalized_samples else {}
    initial_equity = _as_float(
      all_metrics.get("initial_capital"),
      _as_float(first_sample.get("equity"), 0.0),
    )
    final_equity = _as_float(
      all_metrics.get("current_capital", all_metrics.get("final_equity")),
      _as_float(last_sample.get("equity"), initial_equity),
    )
    total_pnl = _as_float(all_metrics.get("total_pnl"), final_equity - initial_equity)
    total_return_pct = _as_float(
      all_metrics.get("total_return_pct"),
      (total_pnl / initial_equity * 100.0) if initial_equity else 0.0,
    )
    max_drawdown_pct = _as_float(
      all_metrics.get("max_drawdown_pct"),
      _as_float(all_metrics.get("max_drawdown"), 0.0) * 100.0,
    )
    if normalized_samples:
      max_drawdown_pct = max(_as_float(s.get("drawdown_pct")) for s in normalized_samples)

    trade_stats = cls._trade_stats(normalized_samples, all_metrics)
    risk_metrics = cls._risk_metrics(normalized_samples, total_return_pct, max_drawdown_pct)
    execution_quality = cls._execution_quality(all_metrics)
    monthly_returns = cls._monthly_returns(normalized_samples)

    has_benchmark = bool(benchmark_code) and any(
      sample.get("benchmark_return_pct") is not None for sample in normalized_samples
    )
    data_warning = warning
    if benchmark_code and not has_benchmark:
      data_warning = "基准数据暂不可用，已隐藏基准曲线。"
    if summary_only:
      data_warning = data_warning or "仅有摘要绩效数据。"

    return {
      "run_id": run_id,
      "backtest_id": backtest_id,
      "mode": str(mode or "").lower(),
      "benchmark_code": benchmark_code if has_benchmark else None,
      "source": source,
      "generated_at": time_utils.now().isoformat(),
      "summary_only": summary_only or not normalized_samples,
      "summary": {
        "initial_equity": initial_equity,
        "final_equity": final_equity,
        "current_equity": final_equity,
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": _as_float(all_metrics.get("sharpe_ratio"), risk_metrics["sharpe_ratio"]),
        "win_rate_pct": _as_float(
          all_metrics.get("win_rate_pct"),
          _as_float(all_metrics.get("win_rate"), 0.0) * 100.0,
        ),
        "total_trades": trade_stats["total_trades"],
      },
      "risk": risk_metrics,
      "trade_stats": trade_stats,
      "execution_quality": execution_quality,
      "equity_curve": cls._series(returned_samples, "return_pct"),
      "drawdown_curve": cls._series(returned_samples, "drawdown_pct"),
      "monthly_returns": monthly_returns,
      "data_quality": {
        "status": "SUMMARY_ONLY" if summary_only or not normalized_samples else "OK",
        "warning": data_warning,
        "sample_count": compressed_sample_count,
        "returned_sample_count": len(returned_samples),
        "truncated": truncated,
        "raw_sample_count": sample_count,
        "compressed_sample_count": compressed_sample_count,
        "compression_policy": compression_policy,
      },
      "page_info": {
        "has_more": False if compression_policy else truncated,
        "next_cursor": None if compression_policy else next_cursor,
      },
    }

  @staticmethod
  def compress_snapshot_samples(
    samples: List[Dict[str, Any]]
  ) -> List[Dict[str, Any]]:
    """Reduce final backtest snapshots while keeping chart-critical points."""
    if len(samples) <= 2:
      return list(samples)

    last_index = len(samples) - 1
    candidate_indexes = {0, last_index}
    minute_stats: Dict[str, Dict[str, int]] = {}

    for index, sample in enumerate(samples):
      event_type = str(sample.get("event_type") or "").lower()
      if event_type in {"order", "trade"}:
        candidate_indexes.add(index)

      minute_key = _sample_minute_key(sample)
      stats = minute_stats.get(minute_key)
      if not stats:
        minute_stats[minute_key] = {
          "last": index,
          "max_equity": index,
          "min_equity": index,
          "max_drawdown": index,
        }
        continue

      stats["last"] = index
      equity = _as_float(sample.get("equity"))
      drawdown = _as_float(sample.get("drawdown_pct"))
      if equity > _as_float(samples[stats["max_equity"]].get("equity")):
        stats["max_equity"] = index
      if equity < _as_float(samples[stats["min_equity"]].get("equity")):
        stats["min_equity"] = index
      if drawdown > _as_float(samples[stats["max_drawdown"]].get("drawdown_pct")):
        stats["max_drawdown"] = index

    for stats in minute_stats.values():
      candidate_indexes.update(stats.values())

    compressed: List[Dict[str, Any]] = []
    last_value: Optional[Tuple[float, float, float]] = None
    for index in sorted(candidate_indexes):
      sample = samples[index]
      event_type = str(sample.get("event_type") or "").lower()
      mandatory = index in {0, last_index} or event_type in {"order", "trade"}
      value_key = _sample_value_key(sample)
      if not mandatory and last_value == value_key:
        continue
      compressed.append(sample)
      last_value = value_key

    return compressed

  @staticmethod
  def paginate_performance(
    performance: Dict[str, Any],
    *,
    cursor: Optional[int],
    limit: int,
  ) -> Dict[str, Any]:
    data = dict(performance or {})
    equity_curve = list(data.get("equity_curve") or [])
    drawdown_curve = list(data.get("drawdown_curve") or [])
    original_equity_count = len(equity_curve)
    if cursor is not None:
      equity_curve = [
        point for point in equity_curve if _as_int(point.get("sequence")) > cursor
      ]
      drawdown_curve = [
        point for point in drawdown_curve if _as_int(point.get("sequence")) > cursor
      ]
      original_equity_count = len(equity_curve)
      has_more = len(equity_curve) > limit
      data["equity_curve"] = equity_curve[:limit]
      data["drawdown_curve"] = drawdown_curve[:limit]
    else:
      has_more = False
      data["equity_curve"] = StrategyPerformanceService._sample_points(
        equity_curve,
        limit,
      )
      data["drawdown_curve"] = StrategyPerformanceService._sample_points(
        drawdown_curve,
        limit,
      )
    last = data["equity_curve"][-1] if data["equity_curve"] else None
    data["page_info"] = {
      "has_more": has_more,
      "next_cursor": str(last.get("sequence")) if has_more and last else None,
    }
    quality = dict(data.get("data_quality") or {})
    quality["returned_sample_count"] = len(data["equity_curve"])
    quality["truncated"] = original_equity_count > len(data["equity_curve"])
    data["data_quality"] = quality
    return data

  @staticmethod
  def _sample_points(
    points: List[Dict[str, Any]],
    limit: int,
  ) -> List[Dict[str, Any]]:
    if not limit or limit <= 0 or len(points) <= limit:
      return points
    if limit == 1:
      return [points[-1]]

    step = (len(points) - 1) / float(limit - 1)
    selected: List[Dict[str, Any]] = []
    used_indexes = set()
    for index in range(limit):
      source_index = min(round(index * step), len(points) - 1)
      if source_index in used_indexes:
        continue
      selected.append(points[source_index])
      used_indexes.add(source_index)
    if selected and selected[-1] is not points[-1]:
      selected[-1] = points[-1]
    return selected

  @staticmethod
  def _series(samples: List[Dict[str, Any]], value_key: str) -> List[Dict[str, Any]]:
    return [
      {
        "sequence": _sample_sequence(sample),
        "timestamp": _sample_time(sample).isoformat(),
        "equity": _as_float(sample.get("equity")),
        "value": _as_float(sample.get(value_key)),
        "benchmark_value": sample.get("benchmark_return_pct"),
        "event_type": sample.get("event_type") or "event",
      }
      for sample in samples
    ]

  @staticmethod
  def _daily_equity(samples: List[Dict[str, Any]]) -> List[Tuple[date, float]]:
    by_day: Dict[date, float] = {}
    for sample in samples:
      by_day[_sample_time(sample).date()] = _as_float(sample.get("equity"))
    return sorted(by_day.items(), key=lambda item: item[0])

  @classmethod
  def _daily_returns(cls, samples: List[Dict[str, Any]]) -> List[float]:
    daily = cls._daily_equity(samples)
    returns: List[float] = []
    for idx in range(1, len(daily)):
      previous = daily[idx - 1][1]
      current = daily[idx][1]
      if previous > 0:
        returns.append((current - previous) / previous)
    return returns

  @classmethod
  def _risk_metrics(
    cls,
    samples: List[Dict[str, Any]],
    total_return_pct: float,
    max_drawdown_pct: float,
  ) -> Dict[str, Any]:
    daily_returns = cls._daily_returns(samples)
    volatility = pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 else 0.0
    sharpe = (
      (mean(daily_returns) / pstdev(daily_returns)) * math.sqrt(252)
      if len(daily_returns) > 1 and pstdev(daily_returns) > 0
      else 0.0
    )
    downside = [value for value in daily_returns if value < 0]
    sortino = (
      (mean(daily_returns) / pstdev(downside)) * math.sqrt(252)
      if len(daily_returns) > 1 and len(downside) > 1 and pstdev(downside) > 0
      else 0.0
    )
    start = _sample_time(samples[0]) if samples else None
    end = _sample_time(samples[-1]) if samples else None
    days = _duration_days(start, end)
    annual_return = (
      ((1.0 + total_return_pct / 100.0) ** (365.0 / days) - 1.0)
      if days > 0 and total_return_pct > -100.0
      else 0.0
    )
    calmar = annual_return / (max_drawdown_pct / 100.0) if max_drawdown_pct > 0 else 0.0
    return {
      "annual_return_pct": annual_return * 100.0,
      "annual_volatility_pct": volatility * 100.0,
      "sharpe_ratio": sharpe,
      "sortino_ratio": sortino,
      "calmar_ratio": calmar,
      "max_drawdown_pct": max_drawdown_pct,
      "max_drawdown_duration_days": cls._max_drawdown_duration_days(samples),
    }

  @staticmethod
  def _max_drawdown_duration_days(samples: List[Dict[str, Any]]) -> int:
    peak = 0.0
    start: Optional[datetime] = None
    max_days = 0
    for sample in samples:
      equity = _as_float(sample.get("equity"))
      ts = _sample_time(sample)
      if equity >= peak:
        if start:
          max_days = max(max_days, int((ts - start).total_seconds() // 86400))
        peak = equity
        start = None
      elif start is None:
        start = ts
    if start and samples:
      max_days = max(
        max_days,
        int((_sample_time(samples[-1]) - start).total_seconds() // 86400),
      )
    return max_days

  @staticmethod
  def _trade_stats(
    samples: List[Dict[str, Any]], metrics: Dict[str, Any]
  ) -> Dict[str, Any]:
    trade_pnls = [
      _as_float((_sample_dict(sample.get("metadata"))).get("trade_pnl_delta"))
      for sample in samples
      if sample.get("event_type") == "trade"
    ]
    wins = [value for value in trade_pnls if value > 0]
    losses = [value for value in trade_pnls if value < 0]
    total_trades = len(trade_pnls) or _as_int(
      metrics.get("total_trades", metrics.get("trades_executed")), 0
    )
    winning_trades = len(wins) or _as_int(metrics.get("winning_trades"), 0)
    losing_trades = len(losses) or _as_int(metrics.get("losing_trades"), 0)
    if not winning_trades and not losing_trades and total_trades:
      win_rate = _as_float(metrics.get("win_rate_pct"), _as_float(metrics.get("win_rate")) * 100.0)
      winning_trades = int(round(total_trades * win_rate / 100.0))
      losing_trades = max(total_trades - winning_trades, 0)
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
      "total_trades": total_trades,
      "winning_trades": winning_trades,
      "losing_trades": losing_trades,
      "win_rate_pct": (winning_trades / total_trades * 100.0) if total_trades else 0.0,
      "avg_win": (gross_profit / len(wins)) if wins else 0.0,
      "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
      "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else 0.0,
      "expectancy": (sum(trade_pnls) / len(trade_pnls)) if trade_pnls else 0.0,
      "max_consecutive_wins": StrategyPerformanceService._max_streak(trade_pnls, True),
      "max_consecutive_losses": StrategyPerformanceService._max_streak(trade_pnls, False),
    }

  @staticmethod
  def _max_streak(values: List[float], positive: bool) -> int:
    current = 0
    best = 0
    for value in values:
      matched = value > 0 if positive else value < 0
      current = current + 1 if matched else 0
      best = max(best, current)
    return best

  @staticmethod
  def _execution_quality(metrics: Dict[str, Any]) -> Dict[str, Any]:
    intents = _as_int(metrics.get("trade_intents_generated"), 0)
    orders = _as_int(metrics.get("orders_placed"), 0)
    trades = _as_int(metrics.get("trades_executed", metrics.get("total_trades")), 0)
    rejected = _as_int(metrics.get("rejected_orders"), 0)
    cancelled = _as_int(metrics.get("cancelled_orders"), 0)
    constraints = _metrics_dict(metrics.get("constraint_statistics"))
    return {
      "intent_count": intents,
      "orders_placed": orders,
      "trades_executed": trades,
      "rejected_orders": rejected,
      "cancelled_orders": cancelled,
      "fill_rate_pct": (trades / orders * 100.0) if orders else 0.0,
      "rejection_rate_pct": (rejected / orders * 100.0) if orders else 0.0,
      "limit_up_buy_blocked": _as_int(
        constraints.get("limit_up_buy_blocked"),
        0,
      ),
      "limit_down_sell_blocked": _as_int(
        constraints.get("limit_down_sell_blocked"),
        0,
      ),
      "suspended_blocked": _as_int(constraints.get("suspended_blocked"), 0),
      "partial_fills": _as_int(constraints.get("partial_fills"), 0),
      "full_fills": _as_int(constraints.get("full_fills"), 0),
      "liquidity_capped_orders": _as_int(
        constraints.get("liquidity_capped_orders"),
        0,
      ),
      "book_depth_capped_orders": _as_int(
        constraints.get("book_depth_capped_orders"),
        0,
      ),
      "expired_orders": _as_int(constraints.get("expired_orders"), 0),
      "unfilled_volume": _as_int(constraints.get("unfilled_volume"), 0),
    }

  @staticmethod
  def _monthly_returns(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not samples:
      return []
    monthly: Dict[str, Dict[str, float]] = {}
    for sample in samples:
      ts = _sample_time(sample)
      key = ts.strftime("%Y-%m")
      equity = _as_float(sample.get("equity"))
      monthly.setdefault(key, {"start": equity, "end": equity})
      monthly[key]["end"] = equity
    rows = []
    for month, values in sorted(monthly.items()):
      start = values["start"]
      end = values["end"]
      rows.append(
        {
          "month": month,
          "return_pct": ((end - start) / start * 100.0) if start else 0.0,
        }
      )
    return rows
