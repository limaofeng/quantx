"""Read-only factor-study runner, using the existing audited data sources."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import math
import os
import platform
import socket
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import psutil
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from quantx_domain.factors import (
  FACTOR_VERSION,
  calculate_factor_frame,
  valid_factor_observations,
)

from quantx_research.artifacts import (
  create_run_directory,
  file_sha256,
  fingerprint,
  git_state,
  runtime_metadata,
  write_json,
  write_yaml,
)
from quantx_research.data import (
  DividendFactorCoverageError,
  ResearchDataSource,
  apply_dividend_adjustment,
  build_quality_report,
  combine_quality_reports,
  normalize_daily_bars,
  normalize_dividend_factors,
)
from quantx_research.data.dataset_builder import _append_instrument_dates, _deduplicate
from quantx_research.factor_config import FactorStudyConfig
from quantx_research.factor_study import (
  FACTOR_REPORT_CHECKPOINT_SCHEMA_VERSION,
  WARNINGS,
  add_factor_outcomes,
  all_factor_report_checkpoints_exist,
  analyze_factor_partitions,
)
from quantx_research.runtime_memory import (
  PhysicalMemoryGuardError,
  PhysicalMemoryMonitorError,
  RuntimeMemoryMonitor,
)
from quantx_research.staging import (
  _load_benchmark,
  _prepare_universe_and_factor_gate,
  _source_provenance,
)

FACTOR_STATISTICS_INPUT_SCHEMA_VERSION = 2
FACTOR_STATISTICS_LEGACY_INPUT_SCHEMA_VERSION = 1
FACTOR_STATISTICS_ENGINE_IDENTITY_SCHEMA_VERSION = 1
FACTOR_STATISTICS_ENGINE_VERSION = "factor-statistics-v2"
FACTOR_DIVIDEND_COVERAGE_EVIDENCE_SCHEMA_VERSION = 2
# Keep this list complete whenever the post-sample statistical call chain gains
# a source module; the frozen sample SHA already binds all earlier data work.
_FACTOR_STATISTICS_SOURCE_FILES = (
  (
    "quantx_research.factor_runner",
    "apps/research/src/quantx_research/factor_runner.py",
  ),
  (
    "quantx_research.factor_study",
    "apps/research/src/quantx_research/factor_study.py",
  ),
  (
    "quantx_research.factor_config",
    "apps/research/src/quantx_research/factor_config.py",
  ),
  (
    "quantx_research.core.statistics",
    "apps/research/src/quantx_research/core/statistics.py",
  ),
  (
    "quantx_research.core.config",
    "apps/research/src/quantx_research/core/config.py",
  ),
  ("quantx_domain.factors", "packages/domain/src/quantx_domain/factors.py"),
)


def load_factor_config(path: str | Path) -> FactorStudyConfig:
  payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
  if not isinstance(payload, dict):
    raise ValueError("研究配置根节点必须是 YAML mapping")
  return FactorStudyConfig.model_validate(payload)


async def _freeze_window(
  config: FactorStudyConfig,
  *,
  source: ResearchDataSource | None,
  market_data_archive: str | Path | None,
) -> FactorStudyConfig:
  from quantx_research.runner import _research_source, _shift_years

  if config.date_range is not None:
    return config
  end = config.universe.end_date
  if end == "latest":
    async with _research_source(
      source, market_data_archive=market_data_archive
    ) as active:
      resolver = getattr(active, "latest_daily_date", None)
      if not callable(resolver):
        raise ValueError("数据源不能解析最新已持久化日线；请显式指定 date_range")
      end = await resolver(config.universe.benchmark_code)
  return config.model_copy(
    update={
      "date_range": (_shift_years(end, -config.universe.lookback_years), end),
    }
  )


async def validate_factor_study(
  config_path: str | Path,
  *,
  source: ResearchDataSource | None = None,
  market_data_archive: str | Path | None = None,
) -> dict[str, Any]:
  from quantx_research.runner import REPO_ROOT, _research_source

  config = await _freeze_window(
    load_factor_config(config_path),
    source=source,
    market_data_archive=market_data_archive,
  )
  root = REPO_ROOT / ".runtime" / "research-staging"
  root.mkdir(parents=True, exist_ok=True)
  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  quality: dict[str, Any] = {}
  errors: list[str] = []
  try:
    with (
      monitor,
      tempfile.TemporaryDirectory(prefix="validate-factor-", dir=root) as temporary,
    ):
      async with _research_source(
        source, market_data_archive=market_data_archive
      ) as active:
        features = await stage_factor_features(active, config, Path(temporary), monitor)
        quality = dict(features.quality)
      _, quality = finish_factor_partitions(features, config, Path(temporary), monitor)
      errors = _preflight_errors(quality)
  except DividendFactorCoverageError as exc:
    quality["dividend_factor_coverage"] = exc.report.to_dict()
    errors = [str(exc)]
  except (PhysicalMemoryGuardError, PhysicalMemoryMonitorError) as exc:
    errors = [str(exc)]
    quality["resource_error"] = str(exc)
  quality["runtime_memory"] = monitor.to_dict()
  return {
    "valid": not errors,
    "study_id": config.study_id,
    "version": config.version,
    "event_count": 0,
    "analysis_sample_count": quality.get("analysis_sample_count", 0),
    "data_quality": quality,
    "errors": errors,
  }


async def run_factor_study(
  config_path: str | Path,
  *,
  source: ResearchDataSource | None = None,
  market_data_archive: str | Path | None = None,
  output_root: str | Path | None = None,
  now: datetime | None = None,
  resume_run_dir: str | Path | None = None,
) -> Path:
  from quantx_research.runner import (
    REPO_ROOT,
    ResearchPreflightError,
    ResearchResourceError,
    _research_source,
    _resolve_output_root,
  )

  resuming = resume_run_dir is not None
  attempt_id = uuid.uuid4().hex
  if resuming and any(
    value is not None for value in (source, market_data_archive, output_root, now)
  ):
    raise ValueError(
      "恢复 factor-study 只读取冻结样本；不能同时设置 source、"
      "market_data_archive、output_root 或 now"
    )

  statistics_engine = _factor_statistics_engine_identity()
  statistics_identity: dict[str, Any] | None = None
  if resuming:
    run_dir = _resolve_factor_resume_directory(Path(resume_run_dir))
    preliminary_manifest = _read_json_object(
      run_dir / "manifest.json", label="运行 manifest"
    )
    previous_active_attempt = preliminary_manifest.get("active_attempt")
    lease = _acquire_factor_run_lease(
      run_dir,
      attempt_id=attempt_id,
      previous_status=str(preliminary_manifest.get("status")),
      previous_active_attempt=previous_active_attempt,
    )
    try:
      (
        run_dir,
        config,
        resolved,
        manifest,
        quality,
        statistics_identity,
      ) = _load_factor_resume(
        config_path,
        run_dir,
        statistics_engine=statistics_engine,
      )
    except BaseException:
      lease.release()
      raise
    config_hash = fingerprint(resolved)
  else:
    config = await _freeze_window(
      load_factor_config(config_path),
      source=source,
      market_data_archive=market_data_archive,
    )
    resolved = config.model_dump(mode="json")
    config_hash = fingerprint(resolved)
    root = _resolve_output_root(output_root or config.runtime.output_root)
    run_dir = create_run_directory(
      root, config.study_id, config.version, config_hash, now=now
    )
    manifest = {
      "run_id": run_dir.name,
      "study_id": config.study_id,
      "version": config.version,
      "status": "running",
      "started_at": datetime.now(timezone.utc).isoformat(),
      "config_hash": config_hash,
      "factor_version": FACTOR_VERSION,
      "statistics_engine": statistics_engine,
      "git": git_state(REPO_ROOT),
      "runtime": runtime_metadata(),
      "event_count": 0,
      "analysis_sample_count": 0,
      "errors": [],
    }
    quality = {}
    previous_active_attempt = None
    lease = _acquire_factor_run_lease(
      run_dir,
      attempt_id=attempt_id,
      previous_status="new",
      previous_active_attempt=None,
    )
  started = datetime.now(timezone.utc)
  manifest["active_attempt"] = lease.identity
  resume_attempt: dict[str, Any] | None = None
  if resuming:
    resume_attempt = {
      "started_at": started.isoformat(),
      "previous_status": manifest["status"],
      "previous_errors": list(manifest.get("errors", [])),
      "previous_completed_at": manifest.get("completed_at"),
      "previous_elapsed_seconds": manifest.get("elapsed_seconds"),
      "previous_runtime_memory": manifest.get("runtime_memory")
      or quality.get("runtime_memory"),
      "previous_git": manifest.get("git"),
      "previous_active_attempt": previous_active_attempt,
      "git": git_state(REPO_ROOT),
      "status": "running",
      "errors": [],
    }
    manifest.setdefault("resume_attempts", []).append(resume_attempt)
    for terminal_field in (
      "completed_at",
      "elapsed_seconds",
      "failure_kind",
      "runtime_memory",
    ):
      manifest.pop(terminal_field, None)
    manifest.update(
      status="running",
      errors=[],
      last_attempt_started_at=started.isoformat(),
    )
  try:
    if not resuming:
      write_yaml(run_dir / "resolved-config.yaml", resolved)
    _atomic_write_json(run_dir / "manifest.json", manifest)
  except BaseException:
    lease.release()
    raise

  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  try:
    with (
      monitor,
      tempfile.TemporaryDirectory(prefix=".staging-", dir=run_dir) as temporary,
    ):
      staging = Path(temporary)
      if resuming:
        sample_path = run_dir / "analysis-sample.parquet"
        partitions = (
          {}
          if all_factor_report_checkpoints_exist(
            run_dir / "statistics-checkpoints", config
          )
          else _partition_existing_factor_sample(sample_path, staging, monitor)
        )
        quality["warnings"] = list(
          dict.fromkeys(
            [
              *quality.get("warnings", []),
              "本次从同一运行的已核验冻结样本恢复统计；未重新读取行情或计算因子/收益。",
            ]
          )
        )
      else:
        async with _research_source(
          source, market_data_archive=market_data_archive
        ) as active:
          features = await stage_factor_features(active, config, staging, monitor)
          quality = dict(features.quality)
        partitions, quality = finish_factor_partitions(
          features, config, staging, monitor
        )
        errors = _preflight_errors(quality)
        if errors:
          raise ResearchPreflightError("; ".join(errors), run_dir=run_dir)
        sample_path = run_dir / "analysis-sample.parquet"
        _write_sample(partitions, sample_path, monitor)
        sample_sha256 = file_sha256(sample_path)
        statistics_identity = _factor_statistics_identity(
          config_hash=config_hash,
          data_fingerprint=str(quality["data_fingerprint"]),
          sample_sha256=sample_sha256,
          sample_count=int(quality["analysis_sample_count"]),
          data_start=str(quality["data_start"]),
          data_end=str(quality["data_end"]),
          analysis_trading_dates=quality["analysis_trading_dates"],
          statistics_engine=statistics_engine,
        )
        manifest["statistics_input"] = statistics_identity
        manifest["statistics_engine"] = statistics_identity["statistics_engine"]
        manifest.update(
          analysis_sample_count=quality["analysis_sample_count"],
          data_fingerprint=quality["data_fingerprint"],
        )
        _atomic_write_json(run_dir / "data-quality.json", quality)
        manifest["artifacts"] = _factor_recovery_artifact_index(
          run_dir,
          sample_sha256=sample_sha256,
        )
        _atomic_write_json(run_dir / "manifest.json", manifest)

      if statistics_identity is None:
        raise RuntimeError("factor-study 统计输入身份未初始化")
      attempt_task = asyncio.current_task()

      def raise_if_cancelled(stage: str) -> None:
        if attempt_task is not None and attempt_task.cancelling():
          raise asyncio.CancelledError(
            f"task cancellation observed after durable {stage}"
          )

      def record_checkpoint(
        completed: int, total: int, report_id: str, reused: bool
      ) -> None:
        manifest["statistics_progress"] = {
          "completed_reports": completed,
          "total_reports": total,
          "last_report_id": report_id,
          "last_report_reused": reused,
        }
        _atomic_write_json(run_dir / "manifest.json", manifest)
        raise_if_cancelled("report checkpoint")

      # Give a real task cancellation one cooperative checkpoint after the
      # frozen inputs are committed and before the long synchronous statistics
      # phase. SIGINT-driven asyncio cancellation can therefore terminate into
      # a resumable failed manifest instead of leaking status=running.
      await asyncio.sleep(0)
      metrics = analyze_factor_partitions(
        partitions,
        config,
        staging_directory=staging,
        monitor=monitor,
        data_start=quality["data_start"],
        data_end=quality["data_end"],
        calendar=pd.DatetimeIndex(quality["analysis_trading_dates"]),
        checkpoint_directory=run_dir / "statistics-checkpoints",
        checkpoint_identity=statistics_identity,
        on_report_checkpoint=record_checkpoint,
      )
      raise_if_cancelled("statistics family")
      metrics["config_hash"] = config_hash
      metrics["data_fingerprint"] = quality["data_fingerprint"]
      metrics["warnings"] = list(
        dict.fromkeys(metrics["warnings"] + quality["warnings"])
      )
      for report in metrics["reports"]:
        report["warnings"] = list(
          dict.fromkeys(report["warnings"] + quality["warnings"])
        )
      # A threshold cohort isn't an execution event. Keep the generic run
      # event count zero instead of inventing a signal/event interpretation.
      _atomic_write_json(run_dir / "metrics.json", metrics)
      if (run_dir / "metrics.json").stat().st_size > 64 * 1024**2:
        raise ResearchPreflightError(
          "结构化因子产物超过页面64MiB安全读取上限；请拆分研究配置",
          run_dir=run_dir,
        )
      table_dir = run_dir / "tables"
      table_dir.mkdir(exist_ok=True)
      for report in metrics["reports"]:
        _atomic_write_csv(
          table_dir / f"{report['report_id']}.csv",
          pd.DataFrame(report["rows"]),
        )
        raise_if_cancelled("report table")
      render_factor_report(run_dir)
      raise_if_cancelled("HTML report")
    manifest["status"] = "success"
  except DividendFactorCoverageError as exc:
    quality["dividend_factor_coverage"] = exc.report.to_dict()
    manifest.update(status="failed_preflight", errors=[str(exc)])
    raise ResearchPreflightError(str(exc), run_dir=run_dir) from exc
  except (PhysicalMemoryGuardError, PhysicalMemoryMonitorError) as exc:
    manifest.update(status="failed_resource", errors=[str(exc)])
    raise ResearchResourceError(str(exc), run_dir=run_dir) from exc
  except ResearchPreflightError as exc:
    manifest.update(status="failed_preflight", errors=[str(exc)])
    raise
  except asyncio.CancelledError as exc:
    manifest.update(
      status="failed",
      failure_kind="cancelled",
      errors=[f"CancelledError: {exc or 'task cancelled'}"],
    )
    raise
  except (KeyboardInterrupt, SystemExit) as exc:
    manifest.update(status="failed", errors=[f"{type(exc).__name__}: {exc}"])
    raise
  except Exception as exc:
    manifest.update(status="failed", errors=[f"{type(exc).__name__}: {exc}"])
    raise
  finally:
    try:
      finished = datetime.now(timezone.utc)
      runtime_memory = monitor.to_dict()
      if manifest.get("status") == "running":
        exc_type, exc, _ = sys.exc_info()
        label = exc_type.__name__ if exc_type is not None else "BaseException"
        manifest.update(
          status="failed",
          errors=[f"{label}: {exc or 'attempt ended without terminal status'}"],
        )
      if resume_attempt is not None:
        resume_attempt.update(
          completed_at=finished.isoformat(),
          elapsed_seconds=(finished - started).total_seconds(),
          status=manifest["status"],
          errors=list(manifest["errors"]),
        )
      manifest.update(
        completed_at=finished.isoformat(),
        elapsed_seconds=(finished - started).total_seconds(),
        analysis_sample_count=quality.get("analysis_sample_count", 0),
        data_fingerprint=quality.get("data_fingerprint"),
        runtime_memory=runtime_memory,
      )
      manifest.pop("active_attempt", None)
      if _factor_recovery_inputs_committed(manifest):
        for recovery_path in (
          "analysis-sample.parquet",
          "data-quality.json",
          "resolved-config.yaml",
        ):
          _verify_indexed_artifact(run_dir, manifest, recovery_path)
      else:
        _atomic_write_json(run_dir / "data-quality.json", quality)
      manifest["artifacts"] = _factor_artifact_index(run_dir, lease_path=lease.path)
      _atomic_write_json(run_dir / "manifest.json", manifest)
    finally:
      lease.release()
  return run_dir


@dataclass(slots=True)
class _FactorRunLease:
  path: Path
  identity: dict[str, Any]
  stream: Any

  def release(self) -> None:
    if self.stream is None:
      return
    try:
      _unlock_factor_run_lease(self.stream)
    finally:
      self.stream.close()
      self.stream = None


def _acquire_factor_run_lease(
  run_dir: Path,
  *,
  attempt_id: str,
  previous_status: str,
  previous_active_attempt: Any,
) -> _FactorRunLease:
  path = run_dir / ".factor-study-run-lease.json"
  if previous_status == "running":
    previous = _validated_attempt_identity(
      previous_active_attempt,
      label="running manifest active_attempt",
    )
    if previous["run_id"] != run_dir.name:
      raise ValueError("running manifest active_attempt 不属于当前运行")
    if _attempt_process_is_active(previous):
      raise ValueError("factor-study 运行仍由活动进程持有，不能接管")

  try:
    stream = path.open("a+b")
  except OSError as exc:
    raise ValueError("factor-study 运行租约仍由活动进程持有，不能接管") from exc
  try:
    if path.stat().st_size == 0:
      stream.write(b"\0")
      stream.flush()
    _lock_factor_run_lease(stream)
  except OSError as exc:
    stream.close()
    raise ValueError("factor-study 运行租约仍由活动进程持有，不能接管") from exc

  identity = _current_attempt_identity(run_dir, attempt_id)
  try:
    stream.seek(0)
    stream.truncate()
    stream.write(
      (json.dumps(identity, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    )
    stream.flush()
    os.fsync(stream.fileno())
  except BaseException:
    _unlock_factor_run_lease(stream)
    stream.close()
    raise
  return _FactorRunLease(path=path, identity=identity, stream=stream)


def _lock_factor_run_lease(stream: Any) -> None:
  stream.seek(0)
  if os.name == "nt":
    import msvcrt

    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    return
  import fcntl

  fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_factor_run_lease(stream: Any) -> None:
  stream.seek(0)
  if os.name == "nt":
    import msvcrt

    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    return
  import fcntl

  fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _current_attempt_identity(run_dir: Path, attempt_id: str) -> dict[str, Any]:
  process = psutil.Process(os.getpid())
  return {
    "schema_version": 1,
    "run_id": run_dir.name,
    "attempt_id": attempt_id,
    "host": socket.gethostname(),
    "pid": process.pid,
    "process_create_time": process.create_time(),
    "acquired_at": datetime.now(timezone.utc).isoformat(),
  }


def _validated_attempt_identity(value: Any, *, label: str) -> dict[str, Any]:
  if not isinstance(value, dict):
    raise ValueError(f"{label} 缺失，不能证明原运行已停止")
  required = {
    "schema_version",
    "run_id",
    "attempt_id",
    "host",
    "pid",
    "process_create_time",
  }
  if (
    value.get("schema_version") != 1
    or not required.issubset(value)
    or not isinstance(value.get("run_id"), str)
    or not value["run_id"]
    or not isinstance(value.get("attempt_id"), str)
    or not value["attempt_id"]
    or not isinstance(value.get("host"), str)
    or not value["host"]
    or isinstance(value.get("pid"), bool)
    or not isinstance(value.get("pid"), int)
    or value["pid"] <= 0
    or isinstance(value.get("process_create_time"), bool)
    or not isinstance(value.get("process_create_time"), (int, float))
    or not math.isfinite(float(value["process_create_time"]))
    or float(value["process_create_time"]) <= 0
  ):
    raise ValueError(f"{label} 非法，不能证明原运行已停止")
  return value


def _attempt_process_is_active(identity: dict[str, Any]) -> bool:
  if identity["host"] != socket.gethostname():
    raise ValueError("运行租约属于其他主机，不能证明原运行已停止")
  try:
    process = psutil.Process(int(identity["pid"]))
    create_time = process.create_time()
  except psutil.NoSuchProcess:
    return False
  except (psutil.AccessDenied, psutil.Error) as exc:
    raise ValueError("不能核验运行租约进程，拒绝接管") from exc
  return abs(create_time - float(identity["process_create_time"])) < 0.001


def _load_factor_resume(
  config_path: str | Path,
  requested_run_dir: Path,
  *,
  statistics_engine: dict[str, Any],
) -> tuple[
  Path,
  FactorStudyConfig,
  dict[str, Any],
  dict[str, Any],
  dict[str, Any],
  dict[str, Any],
]:
  run_dir = _resolve_factor_resume_directory(requested_run_dir)

  manifest = _read_json_object(run_dir / "manifest.json", label="运行 manifest")
  if manifest.get("run_id") != run_dir.name:
    raise ValueError("恢复运行目录名与 manifest.run_id 不一致")
  if manifest.get("study_id") != "factor-study":
    raise ValueError("只能恢复 factor-study 运行")
  if manifest.get("status") not in {"failed_resource", "failed", "running"}:
    raise ValueError(
      "只允许恢复 failed_resource、failed 或可证明已停止的 running factor-study 运行"
    )
  if manifest.get("factor_version") != FACTOR_VERSION:
    raise ValueError("恢复运行的 factor_version 与当前实现不一致")

  resolved_path, _ = _verify_indexed_artifact(
    run_dir,
    manifest,
    "resolved-config.yaml",
  )
  saved_payload = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
  if not isinstance(saved_payload, dict):
    raise ValueError("恢复运行的 resolved-config.yaml 非法")
  saved_config = FactorStudyConfig.model_validate(saved_payload)
  requested_config = load_factor_config(config_path)
  if requested_config.date_range is None:
    requested_config = requested_config.model_copy(
      update={"date_range": saved_config.date_range}
    )
  resolved = saved_config.model_dump(mode="json")
  if requested_config.model_dump(mode="json") != resolved:
    raise ValueError("恢复配置与冻结 resolved-config.yaml 不一致")
  config_hash = fingerprint(resolved)
  if manifest.get("config_hash") != config_hash:
    raise ValueError("恢复配置指纹与 manifest.config_hash 不一致")

  quality_path, _ = _verify_indexed_artifact(
    run_dir,
    manifest,
    "data-quality.json",
  )
  quality = _read_json_object(quality_path, label="数据质量产物")
  _validate_resume_dividend_factor_coverage(quality)
  data_fingerprint = str(quality.get("data_fingerprint") or "")
  if not data_fingerprint or manifest.get("data_fingerprint") != data_fingerprint:
    raise ValueError("恢复运行的数据指纹缺失或不一致")
  sample_count = int(quality.get("analysis_sample_count") or 0)
  sample_path, sample_sha256 = _verify_indexed_artifact(
    run_dir,
    manifest,
    "analysis-sample.parquet",
  )
  if not sample_path.is_file() or sample_count <= 0:
    raise ValueError("恢复运行缺少完整冻结 analysis-sample.parquet")
  metadata = pq.read_metadata(sample_path)
  if metadata.num_rows != sample_count:
    raise ValueError("冻结样本行数与 data-quality.json 不一致")
  identity = _factor_statistics_identity(
    config_hash=config_hash,
    data_fingerprint=data_fingerprint,
    sample_sha256=sample_sha256,
    sample_count=sample_count,
    data_start=str(quality.get("data_start") or ""),
    data_end=str(quality.get("data_end") or ""),
    analysis_trading_dates=quality.get("analysis_trading_dates"),
    statistics_engine=statistics_engine,
  )
  recorded_identity = manifest.get("statistics_input")
  if recorded_identity is not None and not isinstance(recorded_identity, dict):
    raise ValueError("恢复运行的 statistics_input 身份非法")
  legacy_identity = recorded_identity is None or not isinstance(
    recorded_identity.get("statistics_engine"), dict
  )
  if legacy_identity:
    if _statistics_has_persisted_output(run_dir, manifest):
      raise ValueError(
        "恢复运行缺少统计引擎身份且已有派生统计产物、进度或索引；"
        "拒绝跨统计实现混用任何结果"
      )
    if (
      recorded_identity is not None
      and recorded_identity != _legacy_statistics_identity(identity)
    ):
      raise ValueError("恢复运行的旧 statistics_input 身份不一致")
  elif recorded_identity != identity:
    raise ValueError("恢复运行的 statistics_input 身份不一致")

  recorded_engine = manifest.get("statistics_engine")
  if recorded_engine is not None and recorded_engine != identity["statistics_engine"]:
    raise ValueError("恢复运行的 statistics_engine 身份不一致")
  if legacy_identity:
    previous_statistics_progress = manifest.pop("statistics_progress", None)
    manifest.setdefault("statistics_identity_upgrades", []).append(
      {
        "schema_version": 1,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
        "reason": "legacy_identity_without_statistics_checkpoints",
        "from_statistics_input_schema_version": (
          recorded_identity.get("schema_version")
          if isinstance(recorded_identity, dict)
          else None
        ),
        "to_statistics_input_schema_version": (FACTOR_STATISTICS_INPUT_SCHEMA_VERSION),
        "previous_statistics_input_sha256": (
          fingerprint(recorded_identity)
          if isinstance(recorded_identity, dict)
          else None
        ),
        "previous_statistics_progress": previous_statistics_progress,
      }
    )
    quality["warnings"] = list(
      dict.fromkeys(
        [
          *quality.get("warnings", []),
          "旧统计输入身份在无检查点边界升级；本次从冻结样本重新计算全部统计报告。",
        ]
      )
    )
  manifest["statistics_input"] = identity
  manifest["statistics_engine"] = identity["statistics_engine"]
  return run_dir, saved_config, resolved, manifest, quality, identity


def _resolve_factor_resume_directory(requested_run_dir: Path) -> Path:
  try:
    run_dir = requested_run_dir.resolve(strict=True)
  except OSError as exc:
    raise ValueError(f"恢复运行目录不可访问: {requested_run_dir}") from exc
  if not run_dir.is_dir():
    raise ValueError(f"恢复运行路径不是目录: {run_dir}")
  return run_dir


def _factor_statistics_identity(
  *,
  config_hash: str,
  data_fingerprint: str,
  sample_sha256: str,
  sample_count: int,
  data_start: str,
  data_end: str,
  analysis_trading_dates: Any,
  statistics_engine: dict[str, Any],
) -> dict[str, Any]:
  if (
    not data_start
    or not data_end
    or not isinstance(analysis_trading_dates, list)
    or not analysis_trading_dates
  ):
    raise ValueError("factor-study 统计日历身份缺失")
  return {
    "schema_version": FACTOR_STATISTICS_INPUT_SCHEMA_VERSION,
    "checkpoint_schema_version": FACTOR_REPORT_CHECKPOINT_SCHEMA_VERSION,
    "config_hash": config_hash,
    "data_fingerprint": data_fingerprint,
    "analysis_sample_sha256": sample_sha256,
    "analysis_sample_count": sample_count,
    "data_start": data_start,
    "data_end": data_end,
    "analysis_trading_dates_sha256": fingerprint(analysis_trading_dates),
    "factor_version": FACTOR_VERSION,
    "statistics_engine": statistics_engine,
  }


def _factor_statistics_engine_identity() -> dict[str, Any]:
  """Bind checkpoints to the exact statistical source and runtime versions."""
  from quantx_research.runner import REPO_ROOT

  source_files: list[dict[str, str]] = []
  for module, relative_path in sorted(_FACTOR_STATISTICS_SOURCE_FILES):
    expected_path = (REPO_ROOT / relative_path).resolve()
    loaded_module = sys.modules.get(module)
    loaded_file = getattr(loaded_module, "__file__", None)
    if not loaded_file:
      raise ValueError(f"factor-study 统计引擎模块未加载: {module}")
    try:
      path = Path(loaded_file).resolve(strict=True)
    except OSError as exc:
      raise ValueError(f"factor-study 统计引擎源码不可读取: {module}") from exc
    if path != expected_path:
      raise ValueError(
        "factor-study 统计引擎模块未从预期工作区加载: "
        f"{module} ({path} != {expected_path})"
      )
    if not path.is_file():
      raise ValueError(f"factor-study 统计引擎源码不存在: {relative_path}")
    source_files.append(
      {
        "module": module,
        "path": relative_path,
        "sha256": file_sha256(path),
      }
    )
  dependencies = {
    "python": platform.python_version(),
    "numpy": np.__version__,
    "pandas": pd.__version__,
    "pyarrow": pa.__version__,
  }
  source_sha256 = fingerprint(source_files)
  payload: dict[str, Any] = {
    "schema_version": FACTOR_STATISTICS_ENGINE_IDENTITY_SCHEMA_VERSION,
    "engine_version": FACTOR_STATISTICS_ENGINE_VERSION,
    "source_files": source_files,
    "source_sha256": source_sha256,
    "dependencies": dependencies,
  }
  return {**payload, "engine_sha256": fingerprint(payload)}


def _legacy_statistics_identity(identity: dict[str, Any]) -> dict[str, Any]:
  projected = {
    key: value for key, value in identity.items() if key != "statistics_engine"
  }
  projected["schema_version"] = FACTOR_STATISTICS_LEGACY_INPUT_SCHEMA_VERSION
  return projected


def _statistics_checkpoint_directory_nonempty(run_dir: Path) -> bool:
  checkpoint_directory = run_dir / "statistics-checkpoints"
  if not checkpoint_directory.exists():
    return False
  if not checkpoint_directory.is_dir():
    raise ValueError("statistics-checkpoints 不是目录，拒绝恢复")
  try:
    next(checkpoint_directory.iterdir())
  except StopIteration:
    return False
  except OSError as exc:
    raise ValueError("statistics-checkpoints 不可读取，拒绝恢复") from exc
  return True


def _statistics_has_persisted_output(run_dir: Path, manifest: dict[str, Any]) -> bool:
  if _statistics_checkpoint_directory_nonempty(run_dir):
    return True
  if manifest.get("statistics_progress") is not None:
    return True
  artifacts = manifest.get("artifacts")
  if isinstance(artifacts, list):
    for entry in artifacts:
      path = entry.get("path") if isinstance(entry, dict) else None
      if path in {"metrics.json", "report.html"} or (
        isinstance(path, str)
        and path.startswith(("tables/", "statistics-checkpoints/"))
      ):
        return True
  if any((run_dir / name).exists() for name in ("metrics.json", "report.html")):
    return True
  if any(
    any(run_dir.glob(pattern))
    for pattern in (".metrics.json*", ".report.html*", "metrics.json*", "report.html*")
  ):
    return True
  tables = run_dir / "tables"
  if not tables.exists():
    return False
  if not tables.is_dir():
    return True
  try:
    next(tables.iterdir())
  except StopIteration:
    return False
  except OSError:
    return True
  return True


def _factor_recovery_inputs_committed(manifest: dict[str, Any]) -> bool:
  """Whether immutable recovery inputs have already been atomically indexed."""
  if not isinstance(manifest.get("statistics_input"), dict):
    return False
  artifacts = manifest.get("artifacts")
  if not isinstance(artifacts, list):
    return False
  required = {
    "analysis-sample.parquet",
    "data-quality.json",
    "resolved-config.yaml",
  }
  counts = {
    relative_path: sum(
      1
      for item in artifacts
      if isinstance(item, dict) and item.get("path") == relative_path
    )
    for relative_path in required
  }
  return all(count == 1 for count in counts.values())


def _verify_indexed_artifact(
  run_dir: Path,
  manifest: dict[str, Any],
  relative_path: str,
) -> tuple[Path, str]:
  artifacts = manifest.get("artifacts")
  if not isinstance(artifacts, list):
    raise ValueError("恢复运行缺少 manifest artifact 索引")
  entries = [
    item
    for item in artifacts
    if isinstance(item, dict) and item.get("path") == relative_path
  ]
  if len(entries) != 1:
    raise ValueError(f"恢复运行的 artifact 索引缺失或重复: {relative_path}")
  path = run_dir / relative_path
  if not path.is_file():
    raise ValueError(f"恢复运行缺少已索引产物: {relative_path}")
  entry = entries[0]
  if entry.get("bytes") != path.stat().st_size:
    raise ValueError(f"恢复运行的 artifact 大小不一致: {relative_path}")
  digest = file_sha256(path)
  if entry.get("sha256") != digest:
    raise ValueError(f"恢复运行的 artifact SHA256 不一致: {relative_path}")
  return path, digest


def _factor_recovery_artifact_index(
  run_dir: Path,
  *,
  sample_sha256: str,
) -> list[dict[str, Any]]:
  entries = []
  for relative_path in (
    "analysis-sample.parquet",
    "data-quality.json",
    "resolved-config.yaml",
  ):
    path = run_dir / relative_path
    entries.append(
      {
        "path": relative_path,
        "bytes": path.stat().st_size,
        "sha256": (
          sample_sha256
          if relative_path == "analysis-sample.parquet"
          else file_sha256(path)
        ),
      }
    )
  return entries


def _factor_artifact_index(
  run_dir: Path,
  *,
  lease_path: Path,
) -> list[dict[str, Any]]:
  entries = []
  for path in sorted(item for item in run_dir.rglob("*") if item.is_file()):
    if path.name == "manifest.json" or path == lease_path:
      continue
    entries.append(
      {
        "path": path.relative_to(run_dir).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
      }
    )
  return entries


def _partition_existing_factor_sample(
  sample_path: Path,
  directory: Path,
  monitor: RuntimeMemoryMonitor,
) -> dict[str, list[Path]]:
  """Rebuild transient month projections without reading market data."""
  months_dir = directory / "resume-months"
  months_dir.mkdir()
  partitions: dict[str, list[Path]] = {}
  parquet = pq.ParquetFile(sample_path)
  observed_rows = 0
  for index, batch in enumerate(
    parquet.iter_batches(batch_size=65_536, use_threads=False)
  ):
    monitor.guard("factor_resume_partition", estimated_increment_bytes=batch.nbytes * 3)
    frame = batch.to_pandas()
    frame["event_date"] = pd.to_datetime(frame["event_date"], errors="coerce")
    if frame["event_date"].isna().any():
      raise ValueError("冻结样本包含非法 event_date")
    observed_rows += len(frame)
    for month, monthly in frame.groupby(frame["event_date"].dt.strftime("%Y-%m")):
      output = months_dir / f"{month}-{index:05d}.parquet"
      monthly.to_parquet(output, index=False, row_group_size=65_536)
      partitions.setdefault(str(month), []).append(output)
    monitor.checkpoint("factor_resume_partition")
  if observed_rows != parquet.metadata.num_rows:
    raise ValueError("冻结样本分区行数校验失败")
  return partitions


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
  try:
    value = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    raise ValueError(f"{label} 不可读: {path}") from exc
  if not isinstance(value, dict):
    raise ValueError(f"{label} 必须是 JSON object: {path}")
  return value


def _validate_resume_dividend_factor_coverage(quality: dict[str, Any]) -> None:
  """Require a complete, internally consistent schema-v2 coverage proof."""

  coverage = quality.get("dividend_factor_coverage")
  if not isinstance(coverage, dict):
    raise ValueError("恢复运行缺少完整 schema-v2 逐代码复权覆盖证据")

  schema_version = coverage.get("evidence_schema_version")
  verified_count = coverage.get("verified_code_window_count")
  evidence_sha256 = coverage.get("evidence_content_sha256")
  if (
    coverage.get("is_complete") is not True
    or type(schema_version) is not int
    or schema_version != FACTOR_DIVIDEND_COVERAGE_EVIDENCE_SCHEMA_VERSION
    or type(verified_count) is not int
    or verified_count <= 0
    or not isinstance(evidence_sha256, str)
    or len(evidence_sha256) != 64
    or any(character not in "0123456789abcdef" for character in evidence_sha256)
  ):
    raise ValueError("恢复运行的复权覆盖证据不是完整 schema-v2 逐代码证明")

  code_sets: dict[str, list[str]] = {}
  for field in ("requested_codes", "covered_codes", "uncovered_codes"):
    value = coverage.get(field)
    if (
      not isinstance(value, list)
      or any(not isinstance(code, str) or not code.strip() for code in value)
      or len(value) != len(set(value))
    ):
      raise ValueError("恢复运行的 schema-v2 复权覆盖代码集合非法")
    code_sets[field] = value

  requested_codes = code_sets["requested_codes"]
  covered_codes = code_sets["covered_codes"]
  if (
    not requested_codes
    or code_sets["uncovered_codes"]
    or len(covered_codes) != len(requested_codes)
    or set(covered_codes) != set(requested_codes)
    or verified_count < len(requested_codes)
  ):
    raise ValueError("恢复运行的 schema-v2 复权覆盖代码集合不一致")


def _atomic_write_json(path: Path, value: Any) -> None:
  temporary = path.with_name(f".{path.name}.partial")
  try:
    write_json(temporary, value)
    temporary.replace(path)
  finally:
    temporary.unlink(missing_ok=True)


def _atomic_write_csv(path: Path, frame: pd.DataFrame) -> None:
  temporary = path.with_name(f".{path.name}.partial")
  try:
    frame.to_csv(temporary, index=False)
    temporary.replace(path)
  finally:
    temporary.unlink(missing_ok=True)


def _read_factor_metrics(path: Path) -> dict[str, Any]:
  metrics = _read_json_object(path, label="因子研究 metrics")
  reports = metrics.get("reports")
  warnings = metrics.get("warnings")
  if (
    metrics.get("schema_version") != 1
    or metrics.get("study_id") != "factor-study"
    or not isinstance(reports, list)
    or not reports
    or not isinstance(warnings, list)
  ):
    raise ValueError("因子研究 metrics 结构不完整，拒绝渲染")
  report_ids: list[str] = []
  for report in reports:
    if not isinstance(report, dict):
      raise ValueError("因子研究 metrics 报告结构不完整，拒绝渲染")
    report_id = report.get("report_id")
    if (
      not isinstance(report_id, str)
      or not report_id
      or not isinstance(report.get("rows"), list)
      or not all(
        key in report
        for key in ("definitions", "conditions", "coverage", "distribution")
      )
    ):
      raise ValueError("因子研究 metrics 报告结构不完整，拒绝渲染")
    report_ids.append(report_id)
  if len(report_ids) != len(set(report_ids)):
    raise ValueError("因子研究 metrics 含重复 report_id，拒绝渲染")
  return metrics


def _validate_factor_render_inputs(
  metrics: dict[str, Any], manifest: dict[str, Any]
) -> None:
  for key in ("factor_version", "config_hash", "data_fingerprint"):
    if metrics.get(key) != manifest.get(key):
      raise ValueError(f"因子研究 metrics.{key} 与 manifest 不一致，拒绝渲染")
  progress = manifest.get("statistics_progress")
  reports = metrics["reports"]
  if (
    not isinstance(progress, dict)
    or progress.get("completed_reports") != progress.get("total_reports")
    or progress.get("total_reports") != len(reports)
    or progress.get("last_report_id") != reports[-1].get("report_id")
  ):
    raise ValueError("因子研究报告族不完整或与 manifest 进度不一致，拒绝渲染")


def _replace_factor_artifact_entry(
  manifest: dict[str, Any], run_dir: Path, relative_path: str
) -> None:
  artifacts = manifest.get("artifacts")
  if not isinstance(artifacts, list) or not all(
    isinstance(item, dict) and isinstance(item.get("path"), str) for item in artifacts
  ):
    raise ValueError("运行 manifest 缺少 artifact 索引")
  positions = [
    index
    for index, item in enumerate(artifacts)
    if isinstance(item, dict) and item.get("path") == relative_path
  ]
  if len(positions) > 1:
    raise ValueError(f"运行 manifest artifact 索引重复: {relative_path}")
  path = run_dir / relative_path
  entry = {
    "path": relative_path,
    "bytes": path.stat().st_size,
    "sha256": file_sha256(path),
  }
  if positions:
    artifacts[positions[0]] = entry
  else:
    artifacts.append(entry)
    artifacts.sort(key=lambda item: str(item.get("path", "")))


def render_factor_existing(run_dir: str | Path) -> Path:
  """Regenerate a completed factor report under the same exclusive run lease."""
  directory = _resolve_factor_resume_directory(Path(run_dir))
  preliminary = _read_json_object(directory / "manifest.json", label="运行 manifest")
  lease = _acquire_factor_run_lease(
    directory,
    attempt_id=f"render-{uuid.uuid4().hex}",
    previous_status=str(preliminary.get("status")),
    previous_active_attempt=preliminary.get("active_attempt"),
  )
  try:
    manifest = _read_json_object(directory / "manifest.json", label="运行 manifest")
    if (
      manifest.get("study_id") != "factor-study" or manifest.get("status") != "success"
    ):
      raise ValueError("只能重新渲染已成功完成的 factor-study 运行")
    metrics_path, _ = _verify_indexed_artifact(directory, manifest, "metrics.json")
    metrics = _read_factor_metrics(metrics_path)
    _validate_factor_render_inputs(metrics, manifest)
    report = render_factor_report(directory, metrics=metrics)
    _replace_factor_artifact_entry(
      manifest,
      directory,
      "report.html",
    )
    _atomic_write_json(directory / "manifest.json", manifest)
    return report
  finally:
    lease.release()


@dataclass
class _FactorFeatureStage:
  paths: list[Path]
  calendar: pd.DatetimeIndex
  quality: dict[str, Any]


def factor_data_fingerprint(
  panel_sha256: str,
  factor_calendar: pd.DatetimeIndex,
  outcome_calendar: pd.DatetimeIndex,
) -> str:
  """Both calendars are inputs, even when the stock bars themselves agree."""

  def days(values: pd.DatetimeIndex) -> list[str]:
    normalized = (
      pd.DatetimeIndex(pd.to_datetime(values, utc=True))
      .tz_convert("Asia/Shanghai")
      .normalize()
      .unique()
      .sort_values()
    )
    return normalized.strftime("%Y-%m-%d").tolist()

  return fingerprint(
    {
      "panel_sha256": panel_sha256,
      "factor_calendar": days(factor_calendar),
      "outcome_calendar": days(outcome_calendar),
    }
  )


async def build_factor_partitions(
  source: ResearchDataSource,
  config: FactorStudyConfig,
  directory: Path,
  monitor: RuntimeMemoryMonitor,
) -> tuple[dict[str, list[Path]], dict[str, Any]]:
  """Convenience for an externally owned source; production separates stages."""
  features = await stage_factor_features(source, config, directory, monitor)
  return finish_factor_partitions(features, config, directory, monitor)


async def stage_factor_features(
  source: ResearchDataSource,
  config: FactorStudyConfig,
  directory: Path,
  monitor: RuntimeMemoryMonitor,
) -> _FactorFeatureStage:
  """Finish all source reads before releasing the read-only DB transaction."""
  from quantx_research.runner import resolve_analysis_window

  analysis_start, end = resolve_analysis_window(config)
  start = analysis_start - timedelta(days=max(400, config.required_lookback * 2))
  requested_source_start = start
  provenance = _source_provenance(source)
  archive_start = (provenance.get("campaign") or {}).get("start_date")
  if provenance.get("kind") == "qmt-daily-bar-archive" and archive_start:
    # A conservative calendar buffer may extend before an archive even when
    # enough observations exist. Preserve missing factor warmup as NaN; never
    # request non-existent evidence or pretend the clipped prefix was present.
    start = max(start, pd.Timestamp(archive_start).date())
  (
    codes,
    instruments,
    benchmark_instrument,
    coverage,
  ) = await _prepare_universe_and_factor_gate(
    source,
    config,
    start=start,
    end=end,
    analysis_start=analysis_start,
  )
  benchmark = await _load_benchmark(
    source,
    config.universe.benchmark_code,
    benchmark_instrument,
    start=start,
    end=end,
  )
  calendar_dates = set(pd.to_datetime(benchmark["time"]).dropna())
  factor_calendar = pd.DatetimeIndex(
    pd.to_datetime(benchmark["time"]).dropna()
  ).normalize()
  if factor_calendar.empty:
    raise ValueError("缺少基准交易日历，无法审计历史因子中的物理行情缺口")
  features_dir = directory / "features"
  months_dir = directory / "months"
  features_dir.mkdir()
  months_dir.mkdir()
  feature_paths: list[Path] = []
  quality_reports = []
  hasher = hashlib.sha256()
  sorted_codes = sorted(codes)
  # Full histories stay within one batch. Capping at 100 symbols bounds the
  # temporary feature/outcome expansion even if a user requests a larger batch.
  batch_size = min(config.runtime.batch_size, 100)
  counts = {factor_id: 0 for factor_id in config.required_factor_ids}
  for index in range(0, len(sorted_codes), batch_size):
    batch = sorted_codes[index : index + batch_size]
    estimate = len(batch) * max(1, (end - start).days) * (len(counts) + 12) * 32
    monitor.guard("factor_load_batch", estimated_increment_bytes=estimate)
    raw = normalize_daily_bars(
      await source.load_daily_bars(batch, start, end, batch_size=len(batch))
    )
    raw = raw[raw["stock_code"].isin(batch)].copy()
    raw["raw_close"] = raw["close"]
    factors = normalize_dividend_factors(
      await source.load_dividend_factors(batch, start=start, end=end)
    )
    panel = apply_dividend_adjustment(raw, factors, mode="point_in_time", as_of=end)
    metadata = instruments[instruments["stock_code"].isin(batch)]
    panel = _append_instrument_dates(panel, metadata)
    quality_reports.append(
      build_quality_report(
        panel,
        requested_codes=batch,
        requested_start=start,
        requested_end=end,
        metadata_codes=metadata["stock_code"].dropna().astype(str),
        minimum_observations=config.required_lookback,
      )
    )
    panel = _deduplicate(panel)
    hasher.update(pd.util.hash_pandas_object(panel, index=False).values.tobytes())
    if panel.empty:
      continue
    panel["event_date"] = pd.to_datetime(panel["time"]).dt.normalize()
    calendar_dates.update(panel["event_date"].dropna())
    valid = valid_factor_observations(panel)
    valid &= panel["stock_code"].isin(metadata["stock_code"])
    panel["outcome_valid"] = valid
    feature_frames = []
    for _, stock in panel.groupby("stock_code", sort=False):
      stock = stock.copy()
      for factor_id in counts:
        stock[factor_id] = np.nan
      calculation_input = stock.copy()
      calculation_input.loc[
        ~stock["outcome_valid"],
        [
          "open",
          "high",
          "low",
          "close",
          "raw_close",
          "volume",
          "amount",
        ],
      ] = np.nan
      computed = calculate_factor_frame(
        calculation_input, trading_dates=factor_calendar
      )
      for factor_id in counts:
        stock[factor_id] = pd.to_numeric(computed[factor_id], errors="coerce").astype(
          float
        )
      feature_frames.append(stock)
    featured = pd.concat(feature_frames, ignore_index=True)
    path = features_dir / f"batch-{index // batch_size:05d}.parquet"
    featured[
      [
        "stock_code",
        "event_date",
        "open",
        "close",
        "outcome_valid",
        "open_date",
        *counts,
      ]
    ].to_parquet(path, index=False)
    feature_paths.append(path)
  calendar = pd.DatetimeIndex(sorted(calendar_dates))
  quality = combine_quality_reports(
    quality_reports,
    requested_codes=sorted_codes,
    requested_start=start,
    requested_end=end,
  ).to_dict()
  quality.update(
    {
      "factor_version": FACTOR_VERSION,
      "dividend_factor_coverage": coverage.to_dict(),
      "data_fingerprint": factor_data_fingerprint(
        hasher.hexdigest(), factor_calendar, calendar
      ),
      "source_provenance": _source_provenance(source),
      "requested_analysis_start": analysis_start.isoformat(),
      "requested_analysis_end": end.isoformat(),
      "requested_source_start": requested_source_start.isoformat(),
      "actual_source_start": start.isoformat(),
      "calendar_sessions": len(calendar),
      "analysis_trading_dates": [
        day.date().isoformat()
        for day in calendar
        if pd.Timestamp(analysis_start) <= day <= pd.Timestamp(end)
      ],
      "warnings": list(dict.fromkeys([*quality["warnings"], *WARNINGS])),
      "universe": config.universe.model_dump(mode="json"),
    }
  )
  if start > requested_source_start:
    quality["warnings"].append(
      "复权和因子预热查询受 archive 起点限制；完整历史窗口不足的因子值保持不可用。"
    )
  return _FactorFeatureStage(feature_paths, calendar, quality)


def finish_factor_partitions(
  features: _FactorFeatureStage,
  config: FactorStudyConfig,
  directory: Path,
  monitor: RuntimeMemoryMonitor,
) -> tuple[dict[str, list[Path]], dict[str, Any]]:
  """CPU/disk-only phase: no database connection is retained by the runner."""
  from quantx_research.runner import resolve_analysis_window

  analysis_start, end = resolve_analysis_window(config)
  months_dir = directory / "months"
  counts = {factor_id: 0 for factor_id in config.required_factor_ids}
  actual_dates: set[pd.Timestamp] = set()
  partitions: dict[str, list[Path]] = {}
  sample_count = 0
  for index, path in enumerate(features.paths):
    monitor.guard(
      "factor_outcomes_batch",
      estimated_increment_bytes=pq.read_metadata(path).num_rows
      * (len(counts) + len(config.outcomes.horizons) * 2 + 8)
      * 32,
    )
    panel = pd.read_parquet(path)
    panel = add_factor_outcomes(panel, features.calendar, config.outcomes.horizons)
    panel = panel[
      panel["event_date"].between(pd.Timestamp(analysis_start), pd.Timestamp(end))
      & panel["outcome_valid"]
    ].copy()
    if config.universe.minimum_listing_days:
      panel = panel[
        (panel["event_date"] - panel["open_date"]).dt.days.ge(
          config.universe.minimum_listing_days
        )
      ]
    panel = panel.drop(columns=["open", "close", "outcome_valid", "open_date"])
    sample_count += len(panel)
    actual_dates.update(panel["event_date"].dropna().unique())
    for factor_id in counts:
      counts[factor_id] += int(np.isfinite(panel[factor_id]).sum())
    for month, monthly in panel.groupby(panel["event_date"].dt.strftime("%Y-%m")):
      output = months_dir / f"{month}-{index:05d}.parquet"
      monthly.to_parquet(output, index=False, row_group_size=65_536)
      partitions.setdefault(str(month), []).append(output)
    monitor.checkpoint("factor_outcomes_written")
  quality = dict(features.quality)
  quality.update(
    {
      "analysis_sample_count": sample_count,
      "factor_valid_counts": counts,
      "data_start": pd.Timestamp(min(actual_dates)).date().isoformat()
      if actual_dates
      else None,
      "data_end": pd.Timestamp(max(actual_dates)).date().isoformat()
      if actual_dates
      else None,
    }
  )
  return partitions, quality


def _preflight_errors(quality: dict[str, Any]) -> list[str]:
  errors = []
  if not quality.get("analysis_sample_count"):
    errors.append("研究区间没有有效行情样本")
  if not any(quality.get("factor_valid_counts", {}).values()):
    errors.append("所选因子均缺少足够的历史窗口")
  return errors


def _write_sample(
  partitions: dict[str, list[Path]], target: Path, monitor: RuntimeMemoryMonitor
) -> None:
  temporary = target.with_name(f".{target.name}.partial")
  writer = None
  try:
    for month in sorted(partitions):
      for path in partitions[month]:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=65_536):
          monitor.guard(
            "factor_sample_artifact", estimated_increment_bytes=batch.nbytes * 3
          )
          table = pa.Table.from_batches([batch])
          if writer is None:
            writer = pq.ParquetWriter(temporary, table.schema)
          writer.write_table(table)
    if writer is not None:
      writer.close()
      writer = None
      temporary.replace(target)
  finally:
    if writer is not None:
      writer.close()
    temporary.unlink(missing_ok=True)


def render_factor_report(
  run_dir: str | Path, *, metrics: dict[str, Any] | None = None
) -> Path:
  directory = Path(run_dir)
  metrics = metrics or _read_factor_metrics(directory / "metrics.json")

  def escape(value: object) -> str:
    return html.escape(str(value), quote=True)

  sections = [
    "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>QuantX 因子研究</title>",
    "<style>body{font:14px system-ui;background:#050b16;color:#d9e5f4;margin:24px}table{border-collapse:collapse;width:100%}td,th{padding:6px;border-bottom:1px solid #29374b;text-align:right}th:first-child,td:first-child{text-align:left}section{margin:28px 0}small{color:#9cabbd}</style>",
    "<h1>QuantX 因子历史关联研究</h1>",
    f"<p>{escape(metrics['data_start'])} — {escape(metrics['data_end'])} · 定义 {escape(metrics['factor_version'])}</p>",
    "<p>主口径 C(T+h)/C(T)-1；辅助口径 C(T+h)/O(T+1)-1。单位：交易日；所有比例和收益为小数。</p>",
    "<ul>"
    + "".join(f"<li>{escape(warning)}</li>" for warning in metrics["warnings"])
    + "</ul>",
  ]
  for report in metrics["reports"]:
    sections.append(
      f"<section id='{escape(report['report_id'])}'><h2>{escape(report['report_id'])}</h2>"
    )
    sections.append(
      "<pre>"
      + escape(
        json.dumps(
          {
            key: report[key]
            for key in ("definitions", "conditions", "coverage", "distribution")
          },
          ensure_ascii=False,
          indent=2,
        )
      )
      + "</pre>"
    )
    sections.append(
      pd.DataFrame(report["rows"]).to_html(
        index=False, escape=True, na_rep="—", float_format=lambda value: f"{value:.6f}"
      )
    )
    sections.append("</section>")
  sections.append("</html>")
  output = directory / "report.html"
  temporary = output.with_name(f".{output.name}.partial")
  try:
    temporary.write_text("\n".join(sections), encoding="utf-8")
    temporary.replace(output)
  finally:
    temporary.unlink(missing_ok=True)
  return output
