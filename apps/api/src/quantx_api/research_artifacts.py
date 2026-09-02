"""Read-only, bounded access to finalized offline research artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FINAL_STATUSES = frozenset(
  {"success", "failed", "failed_preflight", "failed_resource"}
)
_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_ROBUSTNESS_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")

_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_CONFIG_BYTES = 512 * 1024
_MAX_DATA_QUALITY_BYTES = 4 * 1024 * 1024
_MAX_METRICS_BYTES = 8 * 1024 * 1024
_MAX_RUNS = 100
_MAX_OFFSET = 100_000
_MAX_COVERAGE_ROWS = 20_000
_MAX_RESULT_ROWS = 50_000
_MAX_WARNINGS = 200
_MAX_PUBLIC_STRING_LENGTH = 1_000
_MAX_SOURCE_QUERY_ROWS = 2_000

# Discovery is exposed through the generic researchRuns/researchRun queries.
# Keep every filesystem dimension bounded independently so an artifact root
# cannot turn one read-only request into an unbounded directory/manifest scan.
MAX_RESEARCH_DISCOVERY_ENTRIES = 4_096
MAX_RESEARCH_DISCOVERY_RUNS = 1_024
MAX_RESEARCH_DISCOVERY_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_RESEARCH_DISCOVERY_DATA_QUALITY_BYTES = 64 * 1024 * 1024
_INDICATOR_COVERAGE_EVIDENCE_SCHEMA_VERSION = 2
_LOWER_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

_QUALITY_KEYS = frozenset(
  {
    "is_usable",
    "row_count",
    "valid_row_count",
    "requested_start",
    "requested_end",
    "requested_codes",
    "loaded_codes",
    "missing_codes",
    "missing_metadata_codes",
    "insufficient_history_codes",
    "invalid_adjustment_codes",
    "duplicate_rows",
    "missing_price_rows",
    "zero_volume_rows",
    "negative_volume_rows",
    "invalid_ohlc_rows",
    "suspended_rows",
    "warnings",
    "coverage",
    "source_provenance",
  }
)
_COVERAGE_KEYS = frozenset(
  {
    "stock_code",
    "rows",
    "valid_rows",
    "first_time",
    "last_time",
    "first_valid_time",
    "last_valid_time",
    "has_start_coverage",
    "has_end_coverage",
    "has_minimum_observations",
    "has_instrument_metadata",
    "adjustment_valid",
    "duplicate_rows",
    "missing_price_rows",
    "zero_volume_rows",
    "negative_volume_rows",
    "invalid_ohlc_rows",
    "suspended_rows",
  }
)
_EVENT_CURVE_KEYS = frozenset(
  {
    "return_kind",
    "horizon",
    "benchmark",
    "sample_size",
    "unique_dates",
    "mean",
    "median",
    "positive_rate",
    "ci_low",
    "ci_high",
  }
)
_GROUP_STATISTIC_KEYS = frozenset(
  {
    "dimensions",
    "return_kind",
    "horizon",
    "benchmark",
    "sample_size",
    "unique_dates",
    "mean",
    "median",
    "positive_rate",
    "p05",
    "p25",
    "p75",
    "p95",
    "mae_mean",
    "mfe_mean",
    "ci_low",
    "ci_high",
    "p_value",
    "q_value",
    "significant",
  }
)
_DIMENSION_KEYS = frozenset(
  {
    "event_direction",
    "price_position_bin",
    "rvol_bin",
    "is_volume_breakout",
    "is_high_position_stall",
    "comparison",
    "event_type",
  }
)
_REGRESSION_KEYS = frozenset(
  {
    "return_kind",
    "horizon",
    "dependent_variable",
    "nobs",
    "r_squared",
    "coefficients",
    "covariance",
    "warnings",
  }
)
_COEFFICIENT_KEYS = frozenset(
  {
    "term",
    "estimate",
    "std_error",
    "t_stat",
    "p_value",
    "ci_low",
    "ci_high",
    "q_value",
    "significant",
  }
)
_COMPARISON_KEYS = frozenset(
  {
    "dimensions",
    "return_kind",
    "horizon",
    "benchmark",
    "shock_sample_size",
    "normal_sample_size",
    "unique_dates",
    "shock_mean",
    "shock_median",
    "normal_mean",
    "normal_median",
    "spread_mean",
    "spread_median",
    "ci_low",
    "ci_high",
    "p_value",
    "q_value",
    "significant",
  }
)
_SOURCE_PROVENANCE_KEYS = frozenset(
  {
    "kind",
    "archive_format",
    "schema_version",
    "ledger_sha256",
    "metadata_universe_validated",
    "boundary_tolerance_days",
    "required_request_count",
    "selected_request_count",
    "selected_chunk_count",
    "selected_source_record_count",
    "selected_chunk_manifest_sha256",
    "emitted_rows",
  }
)
_SOURCE_CAMPAIGN_KEYS = frozenset(
  {
    "run_key",
    "start_date",
    "end_date",
    "universe_sha256",
    "job_plan_sha256",
  }
)
_SOURCE_PREPROCESSING_KEYS = frozenset(
  {
    "compatibility",
    "price_decimals",
    "volume_amount_decimals",
    "timezone",
  }
)
_SOURCE_QUERY_KEYS = frozenset(
  {
    "requested_start",
    "requested_end",
    "requested_code_count",
    "requested_codes_sha256",
    "selected_request_count",
    "available_start",
    "available_end",
    "boundary_truncated",
    "emitted_rows",
  }
)


class ResearchArtifactError(ValueError):
  """An artifact cannot be safely exposed."""


def validate_indicator_study_data_quality(value: Any) -> None:
  """Require a complete, internally consistent schema-v2 indicator proof."""

  if not isinstance(value, dict):
    raise ResearchArtifactError("指标研究数据质量产物必须是 object")
  coverage = value.get("dividend_factor_coverage")
  if not isinstance(coverage, dict):
    raise ResearchArtifactError("指标研究缺少 schema-v2 逐代码复权覆盖证据")

  schema_version = coverage.get("evidence_schema_version")
  verified_count = coverage.get("verified_code_window_count")
  evidence_sha256 = coverage.get("evidence_content_sha256")
  if (
    coverage.get("is_complete") is not True
    or type(schema_version) is not int
    or schema_version != _INDICATOR_COVERAGE_EVIDENCE_SCHEMA_VERSION
    or type(verified_count) is not int
    or verified_count <= 0
    or not isinstance(evidence_sha256, str)
    or _LOWER_SHA256_PATTERN.fullmatch(evidence_sha256) is None
  ):
    raise ResearchArtifactError("指标研究复权覆盖证据不是完整 schema-v2 证明")

  code_sets: dict[str, list[str]] = {}
  for field in ("requested_codes", "covered_codes", "uncovered_codes"):
    codes = coverage.get(field)
    if (
      not isinstance(codes, list)
      or any(not isinstance(code, str) or not code.strip() for code in codes)
      or len(codes) != len(set(codes))
    ):
      raise ResearchArtifactError("指标研究 schema-v2 复权覆盖代码集合非法")
    code_sets[field] = codes

  requested_codes = code_sets["requested_codes"]
  covered_codes = code_sets["covered_codes"]
  if (
    not requested_codes
    or code_sets["uncovered_codes"]
    or len(covered_codes) != len(requested_codes)
    or set(covered_codes) != set(requested_codes)
    or verified_count < len(requested_codes)
  ):
    raise ResearchArtifactError("指标研究 schema-v2 复权覆盖代码集合不一致")


@dataclass(frozen=True)
class ResearchRunRecord:
  key: str
  run_id: str
  study_id: str
  version: str
  status: str
  started_at: datetime | None
  completed_at: datetime | None
  event_count: int | None
  elapsed_seconds: float | None
  config_hash: str | None
  has_metrics: bool
  run_directory: Path
  artifact_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchRunDetailRecord:
  summary: ResearchRunRecord
  data_quality: dict[str, Any] | None
  analysis_sample_count: int | None
  event_curve: list[dict[str, Any]]
  interaction_heatmap: list[dict[str, Any]]
  comparison: list[dict[str, Any]]
  comparison_sensitivity: dict[str, list[dict[str, Any]]]
  regressions: list[dict[str, Any]]
  robustness: dict[str, list[dict[str, Any]]]
  warnings: list[str]
  artifact_errors: tuple[str, ...]
  indicator_reports: tuple[dict[str, Any], ...] = ()
  selection_metrics: dict[str, Any] | None = None


class ResearchArtifactStore:
  """Discover finalized runs below one configured, read-only root."""

  def __init__(self, root: str | Path | None = None) -> None:
    configured_root = Path(root) if root is not None else research_runs_root()
    self._root = configured_root.expanduser().resolve(strict=False)

  @property
  def root(self) -> Path:
    return self._root

  def list_runs(
    self,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    study_id: str | None = None,
  ) -> tuple[list[ResearchRunRecord], int]:
    _validate_pagination(limit=limit, offset=offset)
    normalized_status = _validate_status(status)
    records = self._discover_runs()
    if study_id is not None:
      if not _SEGMENT_PATTERN.fullmatch(study_id):
        raise ResearchArtifactError("研究类型 ID 格式无效")
      records = [item for item in records if item.study_id == study_id]
    if normalized_status is not None:
      records = [
        item
        for item in records
        if (
          item.status.startswith("failed")
          if normalized_status == "failed"
          else item.status == normalized_status
        )
      ]
    records.sort(key=_run_sort_key, reverse=True)
    return records[offset : offset + limit], len(records)

  def list_runs_for_index(self) -> list[ResearchRunRecord]:
    """Read all bounded artifact summaries for the unified lifecycle index.

    Unlike the legacy paginated query, the index caller owns the global merge
    and pagination.  The existing discovery budgets remain hard limits, while
    filesystem enumeration failures are surfaced instead of being converted
    into an empty result.
    """

    return self._discover_runs(strict=True, include_flat=True)

  def get_run(self, key: str) -> ResearchRunDetailRecord | None:
    summary = self.get_summary(key)
    if summary is None:
      return None

    errors: list[str] = []
    data_quality = self._load_optional_json(
      summary.run_directory,
      "data-quality.json",
      max_bytes=_MAX_DATA_QUALITY_BYTES,
      required=summary.status in {"success", "failed_preflight"},
      errors=errors,
    )
    metrics = self._load_optional_json(
      summary.run_directory,
      "metrics.json",
      max_bytes=self._metrics_limit(summary.study_id),
      required=summary.status == "success",
      errors=errors,
    )
    self._validate_optional_config(summary.run_directory, errors=errors)

    safe_quality = _sanitize_data_quality(data_quality)
    safe_metrics = _sanitize_metrics(metrics)
    indicator_reports: list[dict[str, Any]] = []
    selection_metrics: dict[str, Any] | None = None
    if (
      summary.study_id == "indicator-study"
      and summary.status == "success"
      and metrics is not None
    ):
      from quantx_api.indicator_research_artifacts import (
        project_indicator_metrics,
        report_reference,
      )

      try:
        self._require_indicator_study_publication(summary)
        indicator_metrics = project_indicator_metrics(metrics)
        indicator_reports = [
          report_reference(summary, indicator_metrics, report)
          for report in indicator_metrics["reports"]
        ]
      except ResearchArtifactError as exc:
        errors.append(f"指标报告不可用: {exc}")
    if summary.study_id == "next-day-selection" and summary.status == "success":
      try:
        from quantx_infrastructure.services.stock_selection_artifacts import (
          SelectionArtifactError,
          load_selection_artifact,
        )

        bundle = load_selection_artifact(summary.run_directory)
        safe_quality = bundle.data_quality
        selection_metrics = bundle.metrics
      except (SelectionArtifactError, OSError, ValueError) as exc:
        errors.append(f"概率模型证据不可用: {exc}")
    return ResearchRunDetailRecord(
      summary=summary,
      data_quality=safe_quality,
      analysis_sample_count=safe_metrics["analysis_sample_count"],
      event_curve=safe_metrics["event_curve"],
      interaction_heatmap=safe_metrics["grouped_statistics"],
      comparison=safe_metrics["comparison"],
      comparison_sensitivity=safe_metrics["comparison_sensitivity"],
      regressions=safe_metrics["regressions"],
      robustness=safe_metrics["robustness"],
      warnings=safe_metrics["warnings"],
      artifact_errors=tuple(errors),
      indicator_reports=tuple(indicator_reports),
      selection_metrics=selection_metrics,
    )

  def get_summary(self, key: str) -> ResearchRunRecord | None:
    """Resolve one stable run key without projecting study-specific metrics."""
    if not _KEY_PATTERN.fullmatch(key):
      raise ResearchArtifactError("研究运行 key 格式无效")
    matches = [item for item in self._discover_runs() if item.key == key]
    if not matches:
      return None
    if len(matches) > 1:
      raise ResearchArtifactError("研究运行 key 不唯一")
    return matches[0]

  def _discover_runs(
    self,
    *,
    strict: bool = False,
    include_flat: bool = False,
  ) -> list[ResearchRunRecord]:
    if not self._root.exists():
      return []
    if _is_link_like(self._root) or not self._root.is_dir():
      logger.warning("Research artifact root is not a safe directory")
      if strict:
        raise ResearchArtifactError("研究运行产物根目录不安全")
      return []

    entry_count = 0
    try:
      study_directories = []
      for study_directory in self._root.iterdir():
        entry_count += 1
        if entry_count > MAX_RESEARCH_DISCOVERY_ENTRIES:
          raise ResearchArtifactError("研究运行目录扫描达到安全预算")
        study_directories.append(study_directory)
    except OSError as exc:
      logger.exception("Unable to enumerate research artifact root")
      if strict:
        raise ResearchArtifactError("研究运行产物根目录不可读") from exc
      return []

    run_directories: list[tuple[Path, bool]] = []
    for study_directory in study_directories:
      if not self._safe_directory(study_directory):
        continue
      if include_flat:
        try:
          manifest_path = self._artifact_path(study_directory, "manifest.json")
          if manifest_path.is_file() and not _is_link_like(manifest_path):
            run_directories.append((study_directory, True))
            continue
        except (OSError, ResearchArtifactError) as exc:
          if strict:
            raise ResearchArtifactError("研究运行产物清单不可读") from exc
      try:
        for run_directory in study_directory.iterdir():
          entry_count += 1
          if entry_count > MAX_RESEARCH_DISCOVERY_ENTRIES:
            raise ResearchArtifactError("研究运行目录扫描达到安全预算")
          run_directories.append((run_directory, False))
      except OSError as exc:
        logger.warning("Unable to enumerate a research study directory")
        if strict:
          raise ResearchArtifactError("研究运行产物目录不可读") from exc
        continue

    safe_run_directories = [
      (run_directory, is_flat)
      for run_directory, is_flat in run_directories
      if self._safe_directory(run_directory)
    ]
    if len(safe_run_directories) > MAX_RESEARCH_DISCOVERY_RUNS:
      raise ResearchArtifactError("研究运行数量扫描达到安全预算")

    readable_run_directories: list[tuple[Path, bool]] = []
    manifest_bytes = 0
    for run_directory, _is_flat in safe_run_directories:
      try:
        manifest_path = self._artifact_path(run_directory, "manifest.json")
        if _is_link_like(manifest_path) or not manifest_path.is_file():
          raise ResearchArtifactError("研究运行清单不是常规文件")
        size = manifest_path.stat().st_size
        if size > _MAX_MANIFEST_BYTES:
          raise ResearchArtifactError("研究运行清单超过大小上限")
      except (OSError, ResearchArtifactError):
        logger.warning("Skipping an invalid research run manifest")
        continue
      if manifest_bytes + size > MAX_RESEARCH_DISCOVERY_MANIFEST_BYTES:
        raise ResearchArtifactError("研究运行清单扫描达到安全预算")
      manifest_bytes += size
      readable_run_directories.append((run_directory, _is_flat))

    # A successful indicator run cannot be discovered without reading its complete
    # data-quality proof. Bound that independent dimension before reading any
    # manifest so list/detail discovery cannot scale to GiB of JSON.
    indicator_quality_bytes = 0
    for run_directory, is_flat in readable_run_directories:
      is_indicator_run = run_directory.parent.name.startswith("indicator-study-")
      if is_flat:
        try:
          flat_manifest = self._read_json(
            run_directory,
            "manifest.json",
            max_bytes=_MAX_MANIFEST_BYTES,
          )
        except (OSError, ResearchArtifactError):
          continue
        is_indicator_run = flat_manifest.get("study_id") == "indicator-study"
      if not is_indicator_run:
        continue
      try:
        quality_path = self._artifact_path(run_directory, "data-quality.json")
        if not quality_path.exists():
          continue
        if _is_link_like(quality_path) or not quality_path.is_file():
          continue
        quality_bytes = quality_path.stat().st_size
      except (OSError, ResearchArtifactError):
        continue
      if quality_bytes > _MAX_DATA_QUALITY_BYTES:
        continue
      if (
        indicator_quality_bytes + quality_bytes
        > MAX_RESEARCH_DISCOVERY_DATA_QUALITY_BYTES
      ):
        raise ResearchArtifactError("指标数据质量产物扫描达到安全预算")
      indicator_quality_bytes += quality_bytes

    records: list[ResearchRunRecord] = []
    for run_directory, is_flat in readable_run_directories:
      try:
        record = self._read_summary(
          run_directory,
          allow_flat=include_flat and is_flat,
          normalize_status=include_flat and is_flat,
        )
      except (OSError, ResearchArtifactError):
        logger.warning("Skipping an invalid research run manifest")
        continue
      if record is not None:
        records.append(record)
    return records

  @staticmethod
  def _metrics_limit(study_id: str) -> int:
    return 64 * 1024 * 1024 if study_id == "indicator-study" else _MAX_METRICS_BYTES

  def _safe_directory(self, path: Path) -> bool:
    if not _SEGMENT_PATTERN.fullmatch(path.name):
      return False
    if _is_link_like(path) or not path.is_dir():
      return False
    try:
      resolved = path.resolve(strict=True)
    except OSError:
      return False
    return resolved.is_relative_to(self._root)

  def _read_summary(
    self,
    run_directory: Path,
    *,
    allow_flat: bool = False,
    normalize_status: bool = False,
  ) -> ResearchRunRecord | None:
    manifest = self._read_json(
      run_directory,
      "manifest.json",
      max_bytes=_MAX_MANIFEST_BYTES,
    )
    status = _required_string(manifest, "status")
    if normalize_status:
      status = {
        "SUCCEEDED": "success",
        "FAILED": "failed",
      }.get(status, status)
    if status not in _FINAL_STATUSES:
      return None
    run_id = _required_string(manifest, "run_id")
    study_id = _required_string(manifest, "study_id")
    version = _required_string(manifest, "version")
    if not all(
      _SEGMENT_PATTERN.fullmatch(item) for item in (run_id, study_id, version)
    ):
      raise ResearchArtifactError("manifest 研究身份字段格式无效")
    if run_id != run_directory.name:
      raise ResearchArtifactError("manifest 研究运行 ID 与目录不一致")
    if not allow_flat and run_directory.parent.name != f"{study_id}-{version}":
      raise ResearchArtifactError("manifest 研究身份与目录不一致")
    if study_id == "indicator-study" and status == "success":
      self._read_indexed_factor_data_quality(run_directory, manifest)

    key = stable_run_key(study_id=study_id, version=version, run_id=run_id)
    has_metrics = self._safe_artifact_presence(
      run_directory,
      "metrics.json",
      max_bytes=self._metrics_limit(study_id),
    )
    return ResearchRunRecord(
      key=key,
      run_id=run_id,
      study_id=study_id,
      version=version,
      status=status,
      started_at=_optional_datetime(manifest.get("started_at")),
      completed_at=_optional_datetime(manifest.get("completed_at")),
      event_count=_optional_nonnegative_int(manifest.get("event_count")),
      elapsed_seconds=_optional_nonnegative_float(manifest.get("elapsed_seconds")),
      config_hash=_optional_hash(manifest.get("config_hash")),
      has_metrics=has_metrics,
      run_directory=run_directory,
    )

  def _require_indicator_study_publication(
    self,
    summary: ResearchRunRecord,
  ) -> dict[str, Any]:
    manifest = self._read_json(
      summary.run_directory,
      "manifest.json",
      max_bytes=_MAX_MANIFEST_BYTES,
    )
    identity = (
      _required_string(manifest, "run_id"),
      _required_string(manifest, "study_id"),
      _required_string(manifest, "version"),
      _required_string(manifest, "status"),
    )
    if identity != (
      summary.run_id,
      "indicator-study",
      summary.version,
      "success",
    ):
      raise ResearchArtifactError("指标研究运行身份在读取期间发生变化")
    return self._read_indexed_factor_data_quality(
      summary.run_directory,
      manifest,
    )

  def _read_indexed_factor_data_quality(
    self,
    run_directory: Path,
    manifest: dict[str, Any],
  ) -> dict[str, Any]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
      raise ResearchArtifactError("指标研究清单缺少产物索引")
    entries = [
      entry
      for entry in artifacts
      if isinstance(entry, dict) and entry.get("path") == "data-quality.json"
    ]
    if len(entries) != 1:
      raise ResearchArtifactError("指标研究清单必须唯一索引 data-quality.json")
    entry = entries[0]
    declared_bytes = entry.get("bytes")
    declared_sha256 = entry.get("sha256")
    if (
      type(declared_bytes) is not int
      or not 0 <= declared_bytes <= _MAX_DATA_QUALITY_BYTES
      or not isinstance(declared_sha256, str)
      or _LOWER_SHA256_PATTERN.fullmatch(declared_sha256) is None
    ):
      raise ResearchArtifactError("指标研究数据质量产物索引无效")

    path = self._artifact_path(run_directory, "data-quality.json")
    if _is_link_like(path) or not path.is_file():
      raise ResearchArtifactError("指标研究数据质量产物不是常规文件")
    try:
      if path.stat().st_size > _MAX_DATA_QUALITY_BYTES:
        raise ResearchArtifactError("指标研究数据质量产物超过大小上限")
      raw = path.read_bytes()
    except OSError as exc:
      raise ResearchArtifactError("指标研究数据质量产物不可读") from exc
    if len(raw) > _MAX_DATA_QUALITY_BYTES or len(raw) != declared_bytes:
      raise ResearchArtifactError("指标研究数据质量产物大小与清单不一致")
    if hashlib.sha256(raw).hexdigest() != declared_sha256:
      raise ResearchArtifactError("指标研究数据质量产物摘要与清单不一致")
    try:
      value = json.loads(
        raw.decode("utf-8"),
        parse_constant=_reject_json_constant,
      )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
      raise ResearchArtifactError("指标研究数据质量产物 JSON 格式无效") from exc
    validate_indicator_study_data_quality(value)
    return value

  def _safe_artifact_presence(
    self,
    run_directory: Path,
    filename: str,
    *,
    max_bytes: int,
  ) -> bool:
    try:
      path = self._artifact_path(run_directory, filename)
      return (
        path.exists()
        and path.is_file()
        and not _is_link_like(path)
        and path.stat().st_size <= max_bytes
      )
    except (OSError, ResearchArtifactError):
      return False

  def _load_optional_json(
    self,
    run_directory: Path,
    filename: str,
    *,
    max_bytes: int,
    required: bool,
    errors: list[str],
  ) -> dict[str, Any] | None:
    try:
      path = self._artifact_path(run_directory, filename)
      if not path.exists():
        if required:
          errors.append(f"{filename} 缺失")
        return None
      return self._read_json(run_directory, filename, max_bytes=max_bytes)
    except (OSError, ResearchArtifactError) as exc:
      errors.append(f"{filename} 不可用: {exc}")
      return None

  def _validate_optional_config(
    self,
    run_directory: Path,
    *,
    errors: list[str],
  ) -> None:
    try:
      path = self._artifact_path(run_directory, "resolved-config.yaml")
      if not path.exists():
        errors.append("resolved-config.yaml 缺失")
        return
      self._read_text(
        run_directory,
        "resolved-config.yaml",
        max_bytes=_MAX_CONFIG_BYTES,
      )
    except (OSError, ResearchArtifactError) as exc:
      errors.append(f"resolved-config.yaml 不可用: {exc}")

  def _read_json(
    self,
    run_directory: Path,
    filename: str,
    *,
    max_bytes: int,
  ) -> dict[str, Any]:
    text = self._read_text(run_directory, filename, max_bytes=max_bytes)
    try:
      value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
      raise ResearchArtifactError("JSON 格式无效") from exc
    if not isinstance(value, dict):
      raise ResearchArtifactError("JSON 根节点必须是 object")
    return value

  def _read_text(
    self,
    run_directory: Path,
    filename: str,
    *,
    max_bytes: int,
  ) -> str:
    path = self._artifact_path(run_directory, filename)
    if _is_link_like(path) or not path.is_file():
      raise ResearchArtifactError("产物不是常规文件")
    size = path.stat().st_size
    if size > max_bytes:
      raise ResearchArtifactError(f"产物超过 {max_bytes} 字节上限")
    try:
      return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
      raise ResearchArtifactError("产物不是有效 UTF-8") from exc

  def _artifact_path(self, run_directory: Path, filename: str) -> Path:
    path = run_directory / filename
    try:
      resolved = path.resolve(strict=False)
      resolved_run = run_directory.resolve(strict=True)
    except OSError as exc:
      raise ResearchArtifactError("无法解析产物路径") from exc
    if not resolved.is_relative_to(resolved_run) or not resolved.is_relative_to(
      self._root
    ):
      raise ResearchArtifactError("产物路径越界")
    return path


def research_runs_root() -> Path:
  """Resolve the configured artifact root without importing the research app."""
  explicit = os.environ.get("QUANTX_RESEARCH_RUNS_ROOT")
  if explicit:
    return Path(explicit)
  repository_root = os.environ.get("QUANTX_ROOT")
  if repository_root:
    return Path(repository_root) / ".runtime" / "research-runs"
  return Path(__file__).resolve().parents[4] / ".runtime" / "research-runs"


def stable_run_key(*, study_id: str, version: str, run_id: str) -> str:
  identity = "\0".join((study_id, version, run_id)).encode("utf-8")
  return hashlib.sha256(identity).hexdigest()


def _validate_pagination(*, limit: int, offset: int) -> None:
  if not 1 <= limit <= _MAX_RUNS:
    raise ResearchArtifactError(f"limit 必须在 1 到 {_MAX_RUNS} 之间")
  if not 0 <= offset <= _MAX_OFFSET:
    raise ResearchArtifactError(f"offset 必须在 0 到 {_MAX_OFFSET} 之间")


def _validate_status(status: str | None) -> str | None:
  if status is None:
    return None
  normalized = status.strip().lower()
  if normalized not in _FINAL_STATUSES:
    raise ResearchArtifactError(
      "status 仅支持 success、failed、failed_preflight、failed_resource"
    )
  return normalized


def _required_string(value: dict[str, Any], key: str) -> str:
  item = value.get(key)
  if not isinstance(item, str) or not item.strip() or len(item) > 160:
    raise ResearchArtifactError(f"manifest.{key} 无效")
  return item


def _optional_datetime(value: Any) -> datetime | None:
  if not isinstance(value, str) or len(value) > 64:
    return None
  try:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None


def _optional_nonnegative_int(value: Any) -> int | None:
  if isinstance(value, bool):
    return None
  if isinstance(value, int) and 0 <= value <= 2_147_483_647:
    return value
  return None


def _optional_nonnegative_float(value: Any) -> float | None:
  if isinstance(value, bool):
    return None
  if isinstance(value, (int, float)) and 0 <= float(value) < float("inf"):
    return float(value)
  return None


def _optional_hash(value: Any) -> str | None:
  if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{8,128}", value):
    return value.lower()
  return None


def _run_sort_key(record: ResearchRunRecord) -> tuple[datetime, str]:
  timestamp = record.completed_at or record.started_at or datetime.min
  if timestamp.tzinfo is not None:
    timestamp = timestamp.replace(tzinfo=None)
  return timestamp, record.key


def _is_link_like(path: Path) -> bool:
  if path.is_symlink():
    return True
  is_junction = getattr(path, "is_junction", None)
  return bool(is_junction and is_junction())


def _sanitize_data_quality(
  value: dict[str, Any] | None,
) -> dict[str, Any] | None:
  if value is None:
    return None
  sanitized = _sanitize_scalar_mapping(
    value,
    _QUALITY_KEYS.difference(
      {
        "requested_codes",
        "loaded_codes",
        "missing_codes",
        "missing_metadata_codes",
        "insufficient_history_codes",
        "invalid_adjustment_codes",
        "warnings",
        "coverage",
        "source_provenance",
      }
    ),
  )
  for key in (
    "requested_codes",
    "loaded_codes",
    "missing_codes",
    "missing_metadata_codes",
    "insufficient_history_codes",
    "invalid_adjustment_codes",
  ):
    if key in value:
      sanitized[key] = _sanitize_strings(value.get(key), maximum=_MAX_COVERAGE_ROWS)
  if "warnings" in value:
    sanitized["warnings"] = _sanitize_strings(value.get("warnings"))
  coverage = value.get("coverage")
  if isinstance(coverage, list):
    sanitized["coverage"] = [
      _sanitize_scalar_mapping(row, _COVERAGE_KEYS)
      for row in coverage[:_MAX_COVERAGE_ROWS]
      if isinstance(row, dict)
    ]
  source_provenance = value.get("source_provenance")
  if isinstance(source_provenance, dict):
    sanitized["source_provenance"] = _sanitize_source_provenance(source_provenance)
  return sanitized


def _sanitize_source_provenance(value: dict[str, Any]) -> dict[str, Any]:
  """Expose reproducibility evidence without leaking local paths or request IDs."""
  sanitized = _sanitize_scalar_mapping(value, _SOURCE_PROVENANCE_KEYS)
  campaign = value.get("campaign")
  if isinstance(campaign, dict):
    sanitized["campaign"] = _sanitize_scalar_mapping(
      campaign,
      _SOURCE_CAMPAIGN_KEYS,
    )
  preprocessing = value.get("preprocessing")
  if isinstance(preprocessing, dict):
    sanitized["preprocessing"] = _sanitize_scalar_mapping(
      preprocessing,
      _SOURCE_PREPROCESSING_KEYS,
    )
  queries = value.get("queries")
  if isinstance(queries, list):
    sanitized["queries"] = [
      _sanitize_scalar_mapping(row, _SOURCE_QUERY_KEYS)
      for row in queries[:_MAX_SOURCE_QUERY_ROWS]
      if isinstance(row, dict)
    ]
  return sanitized


def _sanitize_metrics(value: dict[str, Any] | None) -> dict[str, Any]:
  if value is None:
    return {
      "event_curve": [],
      "grouped_statistics": [],
      "comparison": [],
      "comparison_sensitivity": {},
      "regressions": [],
      "robustness": {},
      "warnings": [],
      "analysis_sample_count": None,
    }
  return {
    "analysis_sample_count": _safe_nonnegative_integer(
      value.get("analysis_sample_count")
    ),
    "event_curve": _sanitize_rows(
      value.get("event_curve"),
      allowed_keys=_EVENT_CURVE_KEYS,
    ),
    "grouped_statistics": _sanitize_group_statistics(value.get("grouped_statistics")),
    "comparison": _sanitize_comparison(value.get("comparison")),
    "comparison_sensitivity": _sanitize_comparison_sensitivity(
      value.get("comparison_sensitivity")
    ),
    "regressions": _sanitize_regressions(value.get("regressions")),
    "robustness": _sanitize_robustness(value.get("robustness")),
    "warnings": _sanitize_strings(value.get("warnings")),
  }


def _sanitize_rows(
  value: Any,
  *,
  allowed_keys: frozenset[str],
) -> list[dict[str, Any]]:
  if not isinstance(value, list):
    return []
  return [
    _sanitize_scalar_mapping(row, allowed_keys)
    for row in value[:_MAX_RESULT_ROWS]
    if isinstance(row, dict)
  ]


def _sanitize_group_statistics(value: Any) -> list[dict[str, Any]]:
  if not isinstance(value, list):
    return []
  rows: list[dict[str, Any]] = []
  for raw_row in value[:_MAX_RESULT_ROWS]:
    if not isinstance(raw_row, dict):
      continue
    row = _sanitize_scalar_mapping(
      raw_row,
      _GROUP_STATISTIC_KEYS.difference({"dimensions"}),
    )
    dimensions = raw_row.get("dimensions")
    if isinstance(dimensions, dict):
      row["dimensions"] = _sanitize_scalar_mapping(dimensions, _DIMENSION_KEYS)
    else:
      row["dimensions"] = {}
    rows.append(row)
  return rows


def _sanitize_regressions(value: Any) -> list[dict[str, Any]]:
  if not isinstance(value, list):
    return []
  rows: list[dict[str, Any]] = []
  for raw_row in value[:_MAX_RESULT_ROWS]:
    if not isinstance(raw_row, dict):
      continue
    row = _sanitize_scalar_mapping(
      raw_row,
      _REGRESSION_KEYS.difference({"coefficients", "warnings"}),
    )
    row["coefficients"] = _sanitize_rows(
      raw_row.get("coefficients"),
      allowed_keys=_COEFFICIENT_KEYS,
    )
    row["warnings"] = _sanitize_strings(raw_row.get("warnings"))
    rows.append(row)
  return rows


def _sanitize_comparison(value: Any) -> list[dict[str, Any]]:
  if not isinstance(value, list):
    return []
  rows: list[dict[str, Any]] = []
  for raw_row in value[:_MAX_RESULT_ROWS]:
    if not isinstance(raw_row, dict):
      continue
    row = _sanitize_scalar_mapping(
      raw_row,
      _COMPARISON_KEYS.difference({"dimensions"}),
    )
    dimensions = raw_row.get("dimensions")
    if isinstance(dimensions, dict):
      row["dimensions"] = _sanitize_scalar_mapping(dimensions, _DIMENSION_KEYS)
    else:
      row["dimensions"] = {}
    rows.append(row)
  return rows


def _sanitize_comparison_sensitivity(
  value: Any,
) -> dict[str, list[dict[str, Any]]]:
  if not isinstance(value, dict):
    return {}
  return {
    name: _sanitize_comparison(rows)
    for name, rows in value.items()
    if isinstance(name, str) and _ROBUSTNESS_NAME_PATTERN.fullmatch(name)
  }


def _sanitize_robustness(value: Any) -> dict[str, list[dict[str, Any]]]:
  if not isinstance(value, dict):
    return {}
  return {
    name: _sanitize_group_statistics(rows)
    for name, rows in value.items()
    if isinstance(name, str) and _ROBUSTNESS_NAME_PATTERN.fullmatch(name)
  }


def _sanitize_strings(value: Any, *, maximum: int = _MAX_WARNINGS) -> list[str]:
  if not isinstance(value, list):
    return []
  return [
    item[:_MAX_PUBLIC_STRING_LENGTH]
    for item in value[:maximum]
    if isinstance(item, str)
  ]


def _sanitize_scalar_mapping(
  value: dict[str, Any],
  allowed_keys: frozenset[str],
) -> dict[str, Any]:
  result: dict[str, Any] = {}
  for key, item in value.items():
    if key not in allowed_keys:
      continue
    safe_item = _safe_scalar(item)
    if safe_item is not _UNSAFE:
      result[key] = safe_item
  return result


_UNSAFE = object()


def _safe_nonnegative_integer(value: Any) -> int | None:
  if (
    isinstance(value, int)
    and not isinstance(value, bool)
    and 0 <= value <= 9_007_199_254_740_991
  ):
    return value
  return None


def _safe_scalar(value: Any) -> Any:
  if value is None or isinstance(value, bool):
    return value
  if isinstance(value, int) and not isinstance(value, bool):
    return value if abs(value) <= 9_007_199_254_740_991 else _UNSAFE
  if isinstance(value, float):
    return value if value == value and abs(value) != float("inf") else _UNSAFE
  if isinstance(value, str):
    return value[:_MAX_PUBLIC_STRING_LENGTH]
  return _UNSAFE


def _reject_json_constant(value: str) -> None:
  raise ResearchArtifactError(f"JSON 包含非法数值 {value}")
