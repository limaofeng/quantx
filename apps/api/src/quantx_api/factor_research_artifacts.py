"""Bounded, read-only factor research projections and exact predicate lookup."""

from __future__ import annotations

import json
import math
import re
from collections import OrderedDict
from copy import deepcopy
from datetime import date
from threading import Lock
from typing import Any

import yaml
from quantx_domain.factors import FACTOR_BY_ID, FACTOR_VERSION, normalize_conditions

from quantx_api.research_artifacts import (
  _KEY_PATTERN,
  _MAX_CONFIG_BYTES,
  _MAX_MANIFEST_BYTES,
  _SEGMENT_PATTERN,
  ResearchArtifactError,
  ResearchArtifactStore,
  ResearchRunRecord,
  _is_link_like,
  _run_sort_key,
)

MAX_FACTOR_METRICS_BYTES = 64 * 1024 * 1024
MAX_FACTOR_REPORTS = 64
MAX_FACTOR_ROWS = 12_000
MAX_FACTOR_MATCH_ENTRIES = 512
MAX_FACTOR_MATCH_RUNS = 128
MAX_FACTOR_MATCH_BYTES = 256 * 1024 * 1024
_SUMMARY_CACHE_LIMIT = 32
_SUMMARY_CACHE_BYTES = 8 * 1024 * 1024
_SUMMARY_CACHE: OrderedDict[tuple[str, int, int], tuple[dict, int]] = OrderedDict()
_SUMMARY_CACHE_LOCK = Lock()
DEFAULT_HORIZONS = list(range(1, 21))
BASE_UNIVERSE = {
  "instrument_type": "stock",
  "stock_codes": None,
  "lookback_years": 5,
  "end_date": "latest",
  "benchmark_code": "000300.SH",
  "minimum_listing_days": 0,
  "exclude_st": False,
  "include_industries": [],
  "exclude_industries": [],
}
_UNIVERSE_KEYS = frozenset(BASE_UNIVERSE)
_STOCK_CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
_MAX_UNIVERSE_STOCK_CODES = 10_000
_MAX_MINIMUM_LISTING_DAYS = 100_000
_ROW_STRINGS = {"group", "return_basis", "period", "inference_status"}
_ROW_INTS = {"horizon", "sample_count", "stock_count", "date_count"}
_ROW_FLOATS = {
  "up_rate", "mean_return", "median_return", "date_equal_up_rate",
  "date_equal_mean_return", "baseline_up_rate", "up_rate_lift",
  "mean_return_lift", "ci_low", "ci_high", "p_value", "q_value",
  "mean_ci_low", "mean_ci_high", "mean_p_value", "mean_q_value",
}
_DEFINITION_KEYS = {
  "id", "label", "category", "description", "unit", "lookback", "kind",
  "research_supported", "unsupported_reason", "version", "operators",
}
_COVERAGE_KEYS = {
  "sample_count", "valid_count", "missing_count", "stock_count", "date_count",
  "requested_stock_codes", "historical_st_available", "historical_industry_available",
  "restricted_universe",
}
_DISTRIBUTION_KEYS = {"factor_id", "group", "lower", "upper", "count", "missing_count"}


def _strings(value: Any, limit: int = 200) -> list[str]:
  if not isinstance(value, list) or len(value) > limit:
    raise ResearchArtifactError("研究字符串列表格式或长度无效")
  if any(not isinstance(item, str) or len(item) > 1000 for item in value):
    raise ResearchArtifactError("研究字符串格式无效")
  return value


def _scalar(value: Any) -> Any:
  if value is None or isinstance(value, bool):
    return value
  if isinstance(value, str):
    return value[:1000]
  if isinstance(value, date):
    return value.isoformat()
  if isinstance(value, (int, float)) and math.isfinite(value):
    return value
  if isinstance(value, list) and len(value) <= 20_000 and not any(isinstance(item, (list, dict)) for item in value):
    return [_scalar(item) for item in value]
  raise ResearchArtifactError("研究统计字段不是有界有限数值")


def _projection(value: Any, keys: set[str]) -> dict[str, Any]:
  if not isinstance(value, dict):
    raise ResearchArtifactError("研究统计必须是 object")
  return {key: _scalar(item) for key, item in value.items() if key in keys}


def canonical_universe(value: Any) -> dict[str, Any]:
  if not isinstance(value, dict):
    raise ResearchArtifactError("研究股票池格式无效")
  if any(key not in _UNIVERSE_KEYS for key in value):
    raise ResearchArtifactError("研究股票池包含未知字段")
  instrument_type = value.get("instrument_type", "stock")
  exclude_st = value.get("exclude_st", False)
  if instrument_type not in {"stock", "etf", "stock_and_etf"} or not isinstance(exclude_st, bool):
    raise ResearchArtifactError("研究股票池取值无效")

  raw_stock_codes = value.get("stock_codes")
  stock_codes = None
  if raw_stock_codes is not None:
    if (
      not isinstance(raw_stock_codes, list)
      or not 1 <= len(raw_stock_codes) <= _MAX_UNIVERSE_STOCK_CODES
    ):
      raise ResearchArtifactError("研究股票列表格式或数量无效")
    normalized_codes = []
    for raw_code in raw_stock_codes:
      if not isinstance(raw_code, str) or len(raw_code) > 16:
        raise ResearchArtifactError("研究股票代码格式无效")
      code = raw_code.strip().upper()
      if not _STOCK_CODE_PATTERN.fullmatch(code):
        raise ResearchArtifactError("研究股票代码格式无效")
      normalized_codes.append(code)
    stock_codes = sorted(set(normalized_codes))

  lookback_years = value.get("lookback_years", 5)
  if (
    isinstance(lookback_years, bool)
    or not isinstance(lookback_years, int)
    or not 1 <= lookback_years <= 30
  ):
    raise ResearchArtifactError("研究回看年数无效")

  raw_end_date = value.get("end_date", "latest")
  if raw_end_date == "latest":
    end_date = "latest"
  elif isinstance(raw_end_date, str) and len(raw_end_date) == 10:
    try:
      end_date = date.fromisoformat(raw_end_date).isoformat()
    except ValueError as exc:
      raise ResearchArtifactError("研究截止日期无效") from exc
  elif type(raw_end_date) is date:
    end_date = raw_end_date.isoformat()
  else:
    raise ResearchArtifactError("研究截止日期无效")

  benchmark_code = value.get("benchmark_code", "000300.SH")
  if not isinstance(benchmark_code, str) or len(benchmark_code) > 16:
    raise ResearchArtifactError("研究基准代码无效")
  benchmark_code = benchmark_code.strip().upper()
  if not _STOCK_CODE_PATTERN.fullmatch(benchmark_code):
    raise ResearchArtifactError("研究基准代码无效")

  minimum_listing_days = value.get("minimum_listing_days", 0)
  if (
    isinstance(minimum_listing_days, bool)
    or not isinstance(minimum_listing_days, int)
    or not 0 <= minimum_listing_days <= _MAX_MINIMUM_LISTING_DAYS
  ):
    raise ResearchArtifactError("研究最短上市天数无效")

  return {
    "instrument_type": instrument_type,
    "stock_codes": stock_codes,
    "lookback_years": lookback_years,
    "end_date": end_date,
    "benchmark_code": benchmark_code,
    "minimum_listing_days": minimum_listing_days,
    "exclude_st": exclude_st,
    "include_industries": sorted(set(_strings(value.get("include_industries", []), 100))),
    "exclude_industries": sorted(set(_strings(value.get("exclude_industries", []), 100))),
  }


def _report_projection(report: Any, *, rows: bool) -> dict[str, Any]:
  if not isinstance(report, dict):
    raise ResearchArtifactError("因子报告格式无效")
  report_id = report.get("report_id")
  if not isinstance(report_id, str) or not _SEGMENT_PATTERN.fullmatch(report_id):
    raise ResearchArtifactError("因子报告 ID 格式无效")
  kind = report.get("kind")
  if kind not in {"single", "joint"}:
    raise ResearchArtifactError("因子报告类型无效")
  factors = sorted(set(_strings(report.get("factor_ids"), 64)))
  if not factors or any(factor not in FACTOR_BY_ID for factor in factors):
    raise ResearchArtifactError("因子报告包含未知因子")
  try:
    conditions = normalize_conditions(report.get("conditions", []))
  except (ValueError, TypeError) as exc:
    raise ResearchArtifactError("因子报告条件无效") from exc
  if kind == "single" and (len(factors) != 1 or conditions):
    raise ResearchArtifactError("单因子总体报告不能包含阈值条件")
  if kind == "joint" and (not conditions or sorted({item["factor_id"] for item in conditions}) != factors):
    raise ResearchArtifactError("联合报告因子与条件不一致")
  definitions = report.get("definitions", [])
  distribution = report.get("distribution", [])
  raw_rows = report.get("rows", [])
  if not isinstance(definitions, list) or len(definitions) > 64:
    raise ResearchArtifactError("因子定义超出边界")
  if not isinstance(distribution, list) or len(distribution) > 512:
    raise ResearchArtifactError("因子分布超出边界")
  if not isinstance(raw_rows, list) or len(raw_rows) > MAX_FACTOR_ROWS:
    raise ResearchArtifactError("因子统计行超出边界")
  result = {
    "report_id": report_id, "kind": kind, "factor_ids": factors,
    "conditions": conditions,
    "warnings": _strings(report.get("warnings", [])),
    "coverage": _projection(report.get("coverage", {}), _COVERAGE_KEYS),
  }
  relevant_rows = [row for row in raw_rows if isinstance(row, dict)
                   and row.get("period") == "all"
                   and (row.get("group") == "joint" if kind == "joint" else row.get("group") != "baseline")]
  result["has_adequate_sample"] = any(
    isinstance(row.get("sample_count"), int) and row["sample_count"] > 0
    and isinstance(row.get("date_count"), int) and row["date_count"] >= 30
    and row.get("inference_status") != "insufficient_sample"
    for row in relevant_rows
  )
  if rows:
    safe_rows = []
    for row in raw_rows:
      projected = _projection(row, _ROW_STRINGS | _ROW_INTS | _ROW_FLOATS)
      for key in _ROW_STRINGS:
        if not isinstance(projected.get(key), str):
          raise ResearchArtifactError("因子统计分类字段缺失")
      for key in _ROW_INTS:
        value = projected.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2_147_483_647:
          raise ResearchArtifactError("因子统计计数字段无效")
      for key in _ROW_FLOATS:
        value = projected.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
          raise ResearchArtifactError("因子统计数值字段无效")
        projected[key] = value
      safe_rows.append(projected)
    result.update({
      "definitions": [_projection(item, _DEFINITION_KEYS) for item in definitions],
      "distribution": [_projection(item, _DISTRIBUTION_KEYS) for item in distribution],
      "rows": safe_rows,
    })
  return result


def project_factor_metrics(metrics: dict[str, Any], *, rows: bool = False) -> dict[str, Any]:
  if metrics.get("schema_version") != 1:
    raise ResearchArtifactError("不支持的因子研究产物版本")
  factor_version = metrics.get("factor_version")
  if not isinstance(factor_version, str) or len(factor_version) > 100:
    raise ResearchArtifactError("缺少因子计算版本")
  horizons = metrics.get("horizons")
  if not isinstance(horizons, list) or not horizons or len(horizons) > 60:
    raise ResearchArtifactError("研究周期无效")
  if any(isinstance(h, bool) or not isinstance(h, int) or not 1 <= h <= 60 for h in horizons):
    raise ResearchArtifactError("研究周期无效")
  bases = _strings(metrics.get("return_bases"), 2)
  if not bases or any(item not in {"close", "next_open"} for item in bases):
    raise ResearchArtifactError("收益口径无效")
  reports = metrics.get("reports")
  if not isinstance(reports, list) or len(reports) > MAX_FACTOR_REPORTS:
    raise ResearchArtifactError("因子报告数量超出边界")
  projected = [_report_projection(report, rows=rows) for report in reports]
  if len({report["report_id"] for report in projected}) != len(projected):
    raise ResearchArtifactError("因子报告 ID 重复")
  return {
    "factor_version": factor_version,
    "universe": canonical_universe(metrics.get("universe")),
    "horizons": sorted(set(horizons)), "return_bases": sorted(set(bases)),
    "data_start": _scalar(metrics.get("data_start")),
    "data_end": _scalar(metrics.get("data_end")),
    "reports": projected, "warnings": _strings(metrics.get("warnings", [])),
  }


def report_reference(summary: ResearchRunRecord, metrics: dict, report: dict) -> dict:
  return {
    "run_key": summary.key, "report_id": report["report_id"],
    "kind": report["kind"], "factor_ids": report["factor_ids"],
    "study_id": summary.study_id, "version": summary.version, "run_id": summary.run_id,
    "completed_at": summary.completed_at, "data_start": metrics["data_start"],
    "data_end": metrics["data_end"], "config_hash": summary.config_hash,
    "warnings": list(dict.fromkeys(metrics["warnings"] + report["warnings"])),
  }


def _history_window_mismatch(config: dict | None) -> str | None:
  """Compare the frozen requested window, not its partially covered data dates."""
  window = config.get("date_range") if config else None
  invalid = "冻结历史区间缺失或无效，无法确认默认5年研究窗口"
  if not isinstance(window, list) or len(window) != 2:
    return invalid
  try:
    start, end = (date.fromisoformat(value) for value in window)
    if start > end or end.year <= 5:
      return invalid
    try:
      expected_start = end.replace(year=end.year - 5)
    except ValueError:  # The runner clips February 29 to February 28.
      expected_start = end.replace(year=end.year - 5, month=2, day=28)
  except (TypeError, ValueError):
    return invalid
  if start != expected_start:
    return "冻结历史区间不同于默认最近5年，仅可作为参考报告"
  return None


class FactorResearchArtifactStore(ResearchArtifactStore):
  def _discover_match_runs(
    self,
  ) -> tuple[list[ResearchRunRecord], int, bool, bool]:
    """Discover only the authoritative factor-study directory within hard bounds."""
    if not self.root.exists():
      return [], 0, False, False
    if _is_link_like(self.root) or not self.root.is_dir():
      return [], 0, False, True

    study_directory = self.root / "factor-study-v1"
    if not study_directory.exists():
      return [], 0, False, False
    if not self._safe_directory(study_directory):
      return [], 0, False, True

    run_directories = []
    try:
      for run_directory in study_directory.iterdir():
        if len(run_directories) >= MAX_FACTOR_MATCH_ENTRIES:
          return [], 0, True, False
        run_directories.append(run_directory)
    except OSError:
      return [], 0, False, True

    records = []
    scanned_bytes = 0
    errors_seen = False
    for run_directory in sorted(
      run_directories,
      key=lambda path: path.name,
      reverse=True,
    ):
      if not self._safe_directory(run_directory):
        errors_seen = True
        continue
      try:
        manifest_path = self._artifact_path(run_directory, "manifest.json")
        if _is_link_like(manifest_path) or not manifest_path.is_file():
          raise ResearchArtifactError("因子运行清单不是常规文件")
        manifest_bytes = manifest_path.stat().st_size
        if manifest_bytes > _MAX_MANIFEST_BYTES:
          raise ResearchArtifactError("因子运行清单超过大小上限")
        if scanned_bytes + manifest_bytes > MAX_FACTOR_MATCH_BYTES:
          return [], scanned_bytes, True, errors_seen
        scanned_bytes += manifest_bytes
        record = self._read_summary(run_directory)
      except (OSError, ResearchArtifactError):
        errors_seen = True
        continue
      if record is not None:
        records.append(record)
    records.sort(key=_run_sort_key, reverse=True)
    return records, scanned_bytes, False, errors_seen

  def _metrics(self, summary: ResearchRunRecord, *, rows: bool = False) -> dict:
    # Revalidate the actual artifact before consulting a cross-request cache.
    # Only bounded projections are retained; raw JSON and statistic rows never are.
    path = self._artifact_path(summary.run_directory, "metrics.json")
    if _is_link_like(path) or not path.is_file():
      self._invalidate_summary(path)
      raise ResearchArtifactError("因子产物不是常规文件")
    stat = path.stat()
    if stat.st_size > MAX_FACTOR_METRICS_BYTES:
      self._invalidate_summary(path)
      raise ResearchArtifactError("因子产物超过大小上限")
    identity = (str(path.resolve(strict=True)), stat.st_mtime_ns, stat.st_size)
    if not rows:
      with _SUMMARY_CACHE_LOCK:
        self._evict_changed(identity)
        cached = _SUMMARY_CACHE.get(identity)
        if cached is not None:
          _SUMMARY_CACHE.move_to_end(identity)
          return deepcopy(cached[0])
    result = project_factor_metrics(self._read_json(
      summary.run_directory, "metrics.json", max_bytes=MAX_FACTOR_METRICS_BYTES,
    ), rows=rows)
    after = path.stat()
    if _is_link_like(path) or after.st_mtime_ns != stat.st_mtime_ns or after.st_size != stat.st_size:
      self._invalidate_summary(path)
      raise ResearchArtifactError("读取期间因子产物发生变化，请刷新")
    if not rows:
      size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
      if size <= _SUMMARY_CACHE_BYTES:
        with _SUMMARY_CACHE_LOCK:
          self._evict_changed(identity)
          _SUMMARY_CACHE[identity] = (deepcopy(result), size)
          _SUMMARY_CACHE.move_to_end(identity)
          while (len(_SUMMARY_CACHE) > _SUMMARY_CACHE_LIMIT
                 or sum(entry[1] for entry in _SUMMARY_CACHE.values()) > _SUMMARY_CACHE_BYTES):
            _SUMMARY_CACHE.popitem(last=False)
    return result

  @staticmethod
  def _evict_changed(identity: tuple[str, int, int]) -> None:
    for key in list(_SUMMARY_CACHE):
      if key[0] == identity[0] and key != identity:
        del _SUMMARY_CACHE[key]

  @staticmethod
  def _invalidate_summary(path) -> None:
    # Lexical absolute path also works when the file has just been deleted.
    identity = str(path.absolute())
    with _SUMMARY_CACHE_LOCK:
      for key in list(_SUMMARY_CACHE):
        if key[0] == identity:
          del _SUMMARY_CACHE[key]

  def get_factor_report(self, run_key: str, report_id: str) -> dict | None:
    if not _KEY_PATTERN.fullmatch(run_key) or not _SEGMENT_PATTERN.fullmatch(report_id):
      raise ResearchArtifactError("因子报告身份格式无效")
    records, _, budget_exhausted, _ = self._discover_match_runs()
    if budget_exhausted:
      raise ResearchArtifactError("因子报告扫描达到安全预算")
    records = [item for item in records if item.key == run_key]
    if not records:
      return None
    if len(records) != 1:
      raise ResearchArtifactError("研究运行 key 不唯一")
    summary = records[0]
    metrics = self._metrics(summary, rows=True)
    report = next((item for item in metrics["reports"] if item["report_id"] == report_id), None)
    if report is None:
      return None
    errors: list[str] = []
    self._validate_optional_config(summary.run_directory, errors=errors)
    config_json = self._safe_config(summary, errors)
    return {
      "reference": report_reference(summary, metrics, report),
      "factor_version": metrics["factor_version"], "universe": metrics["universe"],
      "horizons": metrics["horizons"], "return_bases": metrics["return_bases"],
      "conditions": report["conditions"], "definitions": report["definitions"],
      "coverage": report["coverage"], "distribution": report["distribution"],
      "rows": report["rows"], "warnings": metrics["warnings"] + report["warnings"],
      "artifact_errors": errors,
      "config_json": config_json,
    }

  def _safe_config(self, summary: ResearchRunRecord, errors: list[str]) -> dict | None:
    """Preserve frozen research choices without exposing arbitrary paths/secrets."""
    try:
      raw = yaml.safe_load(self._read_text(summary.run_directory, "resolved-config.yaml", max_bytes=_MAX_CONFIG_BYTES))
      if not isinstance(raw, dict):
        raise ResearchArtifactError("研究配置必须是 object")
      safe = _projection(raw, {"study", "version", "factor_ids", "date_range"})
      safe["conditions"] = normalize_conditions(raw.get("conditions", []))
      for key, allowed in {
        "universe": {"instrument_type", "exclude_st", "include_industries", "exclude_industries", "stock_codes", "lookback_years", "end_date", "minimum_listing_days", "benchmark_code"},
        "outcomes": {"horizons", "include_close_response", "include_next_open_return", "include_benchmark_excess", "include_cross_section_excess"},
        "statistics": {"bootstrap_method", "bootstrap_samples", "confidence_level", "fdr_alpha", "minimum_cell_samples", "minimum_inference_dates", "moving_block_length", "random_seed", "regression_benchmark", "run_regression"},
        "runtime": {"batch_size", "memory_sample_interval_seconds", "minimum_available_memory_gib"},
      }.items():
        if key in raw:
          safe[key] = _projection(raw[key], allowed)
      return safe
    except (OSError, ValueError, yaml.YAMLError, RecursionError, TypeError):
      errors.append("冻结研究配置无法安全读取")
      return None

  def match_reports(self, requests: list[dict]) -> list[dict]:
    if not 1 <= len(requests) <= 64:
      raise ResearchArtifactError("每次查询支持1至64个报告请求")
    normalized = [self._request(request) for request in requests]
    if len({item["request_id"] for item in normalized}) != len(normalized):
      raise ResearchArtifactError("报告请求 ID 不得重复")
    results = [{
      "request_id": item["request_id"], "status": "UNSUPPORTED" if item["unsupported"] else "MISSING",
      "reason": "；".join(item["blockers"]) or "尚无匹配研究报告，可下载配置后运行离线分析命令",
      "reports": [], "config_json": item["config"],
      "command": "uv run --no-sync quantx-research run --config factor-study.json",
      "blockers": item["blockers"],
    } for item in normalized]
    if all(item["unsupported"] for item in normalized):
      return results
    records, scanned_bytes, budget_exhausted, errors_seen = (
      self._discover_match_runs()
    )
    scanned_runs = 0
    for record in records:
      if record.study_id != "factor-study" or record.status != "success":
        continue
      if scanned_runs >= MAX_FACTOR_MATCH_RUNS:
        budget_exhausted = True
        break
      scanned_runs += 1
      try:
        metrics_path = self._artifact_path(record.run_directory, "metrics.json")
        if _is_link_like(metrics_path) or not metrics_path.is_file():
          raise ResearchArtifactError("因子产物不是常规文件")
        metrics_bytes = metrics_path.stat().st_size
        if metrics_bytes > MAX_FACTOR_METRICS_BYTES:
          raise ResearchArtifactError("因子产物超过大小上限")
        if scanned_bytes + metrics_bytes > MAX_FACTOR_MATCH_BYTES:
          budget_exhausted = True
          break
        scanned_bytes += metrics_bytes
        metrics = self._metrics(record)
      except (OSError, ResearchArtifactError):
        errors_seen = True
        continue
      # Frozen config is small and revalidated on each lookup. Do not let the
      # metrics-only cache conceal a changed, missing or unsafe history window.
      try:
        config_path = self._artifact_path(
          record.run_directory,
          "resolved-config.yaml",
        )
        if _is_link_like(config_path) or not config_path.is_file():
          raise ResearchArtifactError("冻结研究配置不是常规文件")
        config_bytes = config_path.stat().st_size
        if config_bytes > _MAX_CONFIG_BYTES:
          raise ResearchArtifactError("冻结研究配置超过大小上限")
        if scanned_bytes + config_bytes > MAX_FACTOR_MATCH_BYTES:
          budget_exhausted = True
          break
        scanned_bytes += config_bytes
      except (OSError, ResearchArtifactError):
        errors_seen = True
      history_reason = _history_window_mismatch(self._safe_config(record, []))
      for request, result in zip(normalized, results, strict=True):
        if request["unsupported"]:
          continue
        for report in metrics["reports"]:
          if report["kind"] != request["kind"] or report["factor_ids"] != request["factor_ids"]:
            continue
          if report["conditions"] != request["conditions"] or metrics["factor_version"] != FACTOR_VERSION:
            continue
          # Only an explicitly broader unfiltered A-share report can be a reference.
          exact_universe = metrics["universe"] == request["universe"]
          if not exact_universe and metrics["universe"] != BASE_UNIVERSE:
            continue
          reasons = list(request["blockers"])
          if history_reason:
            reasons.append(history_reason)
          if not exact_universe:
            reasons.append("参考报告未应用当前 ST/行业过滤，不能视为条件匹配")
          if metrics["horizons"] != DEFAULT_HORIZONS or metrics["return_bases"] != ["close", "next_open"]:
            reasons.append("研究周期或收益口径不同于默认1–20日双口径")
          if report["coverage"].get("requested_stock_codes") or report["coverage"].get("restricted_universe"):
            reasons.append("研究使用限定股票样本或非默认上市时长，不代表当前默认股票池")
          insufficient = not report["has_adequate_sample"]
          status = "REFERENCE_ONLY" if reasons else "DATA_INSUFFICIENT" if insufficient else "MATCHED"
          reference = report_reference(record, metrics, report)
          reference["match_status"] = status
          reference["match_reason"] = (
            "；".join(reasons) if reasons else
            "条件匹配，但有效日期不足或样本为空" if insufficient else
            "条件匹配；请同时查看数据覆盖与统计限制"
          )
          reference["warnings"] = list(dict.fromkeys(reference["warnings"] + reasons))
          # Exact/default results precede broader reference runs even when older.
          if result["status"] not in {"MATCHED", "DATA_INSUFFICIENT"} and status in {"MATCHED", "DATA_INSUFFICIENT"}:
            result["reports"] = [reference] + result["reports"]
            result["status"] = status
            result["reason"] = "条件匹配；请同时查看数据覆盖与统计限制" if not insufficient else "条件匹配，但有效日期不足或样本为空"
          else:
            if len(result["reports"]) < 20:
              result["reports"].append(reference)
            if result["status"] == "MISSING":
              result["status"] = status
              result["reason"] = "；".join(reasons)
      if all(
        request["unsupported"]
        or result["status"] in {"MATCHED", "DATA_INSUFFICIENT"}
        for request, result in zip(normalized, results, strict=True)
      ):
        break
    if errors_seen or budget_exhausted:
      for result in results:
        if result["status"] == "MISSING":
          result["status"] = "ARTIFACT_ERROR"
          result["reason"] = (
            "因子报告扫描达到安全预算，未在有界范围内找到匹配报告"
            if budget_exhausted
            else "存在无法安全读取的因子产物，未找到可用匹配报告"
          )
    return results

  @staticmethod
  def _request(request: dict) -> dict:
    request_id = request.get("request_id")
    kind = request.get("kind")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100 or kind not in {"single", "joint"}:
      raise ResearchArtifactError("报告请求身份无效")
    factor_ids = sorted(set(_strings(request.get("factor_ids"), 64)))
    if not factor_ids or any(item not in FACTOR_BY_ID for item in factor_ids):
      raise ResearchArtifactError("报告请求包含未知因子")
    conditions = normalize_conditions(request.get("conditions", []))
    if kind == "single" and (len(factor_ids) != 1 or conditions):
      raise ResearchArtifactError("单因子总体报告只能请求一个因子且不能包含阈值")
    if kind == "joint" and (not conditions or sorted({item["factor_id"] for item in conditions}) != factor_ids):
      raise ResearchArtifactError("联合报告因子必须与所有条件一致")
    universe = canonical_universe(request.get("universe"))
    unsupported = [FACTOR_BY_ID[item] for item in factor_ids if not FACTOR_BY_ID[item].research_supported]
    blockers = [f"{item.label}：{item.unsupported_reason}" for item in unsupported]
    if universe["instrument_type"] != "stock":
      blockers.append("首版研究仅覆盖 A 股，ETF 筛选可用但没有对应研究")
    if universe["exclude_st"]:
      blockers.append("历史 ST 状态尚未还原，不能执行排除历史 ST 的精确研究")
    if universe["include_industries"] or universe["exclude_industries"]:
      blockers.append("历史行业归属尚未还原，不能执行行业过滤的精确研究")
    config = {
      "study": "factor-study", "version": "v1", "factor_ids": factor_ids,
      "conditions": conditions, "universe": universe,
      "outcomes": {"horizons": DEFAULT_HORIZONS},
      "statistics": {"bootstrap_samples": 1000, "minimum_inference_dates": 30},
    }
    return {
      "request_id": request_id, "kind": kind, "factor_ids": factor_ids,
      "conditions": conditions, "universe": universe, "blockers": blockers,
      "unsupported": bool(unsupported) or universe["instrument_type"] != "stock",
      "config": config,
    }
