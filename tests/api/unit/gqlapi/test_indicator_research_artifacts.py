import hashlib
import json
from pathlib import Path

import pytest
from quantx_api.indicator_research_artifacts import (
  BASE_UNIVERSE,
  DEFAULT_HORIZONS,
  IndicatorResearchArtifactStore,
  canonical_universe,
)
from quantx_api.research_artifacts import (
  ResearchArtifactError,
  ResearchArtifactStore,
  stable_run_key,
  validate_indicator_study_data_quality,
)


def _condition(indicator_id="volume_ratio", value=1.5):
  return {"indicator_id": indicator_id, "operator": "gte", "value": value}


def _request(*, conditions=None, exclude_st=False, indicator_ids=None, kind="joint"):
  conditions = conditions if conditions is not None else [_condition()]
  return {
    "request_id": "current",
    "kind": kind,
    "indicator_ids": indicator_ids
    or sorted({item["indicator_id"] for item in conditions}),
    "conditions": conditions,
    "universe": {**BASE_UNIVERSE, "exclude_st": exclude_st},
  }


def _write_indicator_run(
  root,
  *,
  conditions=None,
  run_id="20260901-100000-abcd1234",
  completed_at="2026-09-01T10:00:00+08:00",
  indicator_version="daily-indicator-v1",
  kind="joint",
  date_count=100,
  horizons=None,
  stock_codes=None,
  universe=None,
):
  conditions = conditions if conditions is not None else [_condition()]
  path = root / "indicator-study-v1" / run_id
  path.mkdir(parents=True)
  key = stable_run_key(study_id="indicator-study", version="v1", run_id=run_id)
  quality = {
    "is_usable": True,
    "dividend_factor_coverage": {
      "is_complete": True,
      "evidence_schema_version": 2,
      "verified_code_window_count": 1,
      "evidence_content_sha256": "b" * 64,
      "requested_codes": ["000001.SZ"],
      "covered_codes": ["000001.SZ"],
      "uncovered_codes": [],
    },
  }
  quality_bytes = json.dumps(quality).encode("utf-8")
  (path / "data-quality.json").write_bytes(quality_bytes)
  (path / "manifest.json").write_text(
    json.dumps(
      {
        "run_id": run_id,
        "study_id": "indicator-study",
        "version": "v1",
        "status": "success",
        "completed_at": completed_at,
        "config_hash": "a" * 64,
        "artifacts": [
          {
            "path": "data-quality.json",
            "bytes": len(quality_bytes),
            "sha256": hashlib.sha256(quality_bytes).hexdigest(),
          }
        ],
      }
    ),
    encoding="utf-8",
  )
  (path / "resolved-config.yaml").write_text(
    "study: indicator-study\ndate_range: [2021-08-31, 2026-08-31]\n",
    encoding="utf-8",
  )
  row = {
    "group": "joint",
    "horizon": 1,
    "return_basis": "close",
    "period": "all",
    "sample_count": 1000,
    "stock_count": 20,
    "date_count": date_count,
    "up_rate": 0.52,
    "mean_return": 0.001,
    "median_return": 0.002,
    "date_equal_up_rate": 0.51,
    "date_equal_mean_return": 0.001,
    "baseline_up_rate": 0.5,
    "up_rate_lift": 0.01,
    "mean_return_lift": 0.001,
    "ci_low": -0.01,
    "ci_high": 0.02,
    "p_value": 0.4,
    "q_value": 0.6,
    "inference_status": "descriptive",
    "private_path": "never expose",
  }
  report = {
    "report_id": "joint-test",
    "kind": kind,
    "indicator_ids": ["volume_ratio"]
    if kind == "single"
    else sorted({item["indicator_id"] for item in conditions}),
    "conditions": conditions,
    "definitions": [{"id": "volume_ratio", "private": "hidden"}],
    "coverage": {
      "valid_count": 1000,
      "date_count": date_count,
      "requested_stock_codes": stock_codes,
    },
    "distribution": [],
    "rows": [row],
    "warnings": ["不是个股预测概率"],
  }
  metrics = {
    "schema_version": 1,
    "indicator_version": indicator_version,
    "universe": universe or BASE_UNIVERSE,
    "horizons": horizons or DEFAULT_HORIZONS,
    "return_bases": ["close", "next_open"],
    "data_start": "2021-09-01",
    "data_end": "2026-08-31",
    "reports": [report],
    "warnings": [],
  }
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  return key, metrics, path


def _write_indexed_quality(path: Path, quality: dict) -> None:
  quality_bytes = json.dumps(quality).encode("utf-8")
  (path / "data-quality.json").write_bytes(quality_bytes)
  manifest_path = path / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  entry = next(
    item for item in manifest["artifacts"] if item["path"] == "data-quality.json"
  )
  entry.update(
    bytes=len(quality_bytes),
    sha256=hashlib.sha256(quality_bytes).hexdigest(),
  )
  manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_canonical_universe_preserves_full_bounded_sample_identity():
  assert canonical_universe({}) == BASE_UNIVERSE
  assert canonical_universe(
    {
      "stock_codes": [" 600000.sh", "000001.SZ", "600000.SH"],
      "lookback_years": 8,
      "end_date": "2026-08-31",
      "benchmark_code": "000905.sh",
      "minimum_listing_days": 120,
    }
  ) == {
    **BASE_UNIVERSE,
    "stock_codes": ["000001.SZ", "600000.SH"],
    "lookback_years": 8,
    "end_date": "2026-08-31",
    "benchmark_code": "000905.SH",
    "minimum_listing_days": 120,
  }


@pytest.mark.parametrize(
  "universe",
  [
    {"stock_codes": []},
    {"stock_codes": ["../../secret"]},
    {"lookback_years": 31},
    {"end_date": "2026-02-30"},
    {"benchmark_code": "../../secret"},
    {"minimum_listing_days": True},
    {"minimum_listing_days": 100_001},
    {"unknown_identity": "ignored-before"},
  ],
)
def test_canonical_universe_rejects_unbounded_or_unknown_identity(universe):
  with pytest.raises(ResearchArtifactError):
    canonical_universe(universe)


@pytest.mark.parametrize(
  ("run_universe", "request_universe"),
  [
    (
      {**BASE_UNIVERSE, "stock_codes": ["000001.SZ"]},
      {**BASE_UNIVERSE, "stock_codes": ["000002.SZ"]},
    ),
    (
      {**BASE_UNIVERSE, "minimum_listing_days": 120},
      BASE_UNIVERSE,
    ),
  ],
)
def test_different_stock_or_listing_day_universe_is_never_an_exact_match(
  tmp_path,
  run_universe,
  request_universe,
):
  _write_indicator_run(tmp_path, universe=run_universe)
  request = _request()
  request["universe"] = request_universe

  found = IndicatorResearchArtifactStore(tmp_path).match_reports([request])[0]

  assert found["status"] == "MISSING"
  assert found["reports"] == []


def test_match_normalizes_order_and_deduplicates_without_ignoring_thresholds(tmp_path):
  conditions = [_condition(), _condition("rsi12", 0)]
  _write_indicator_run(tmp_path, conditions=conditions)
  store = IndicatorResearchArtifactStore(tmp_path)
  found = store.match_reports(
    [_request(conditions=[conditions[1], conditions[0], conditions[1]])]
  )[0]
  assert found["status"] == "MATCHED"
  assert len(found["reports"]) == 1
  changed = store.match_reports(
    [_request(conditions=[_condition(value=2), conditions[1]])]
  )[0]
  assert changed["status"] == "MISSING"
  assert changed["reports"] == []


def test_historical_st_filters_only_reference_broader_report_and_block_execution(
  tmp_path,
):
  _write_indicator_run(tmp_path)
  found = IndicatorResearchArtifactStore(tmp_path).match_reports(
    [_request(exclude_st=True)]
  )[0]
  assert found["status"] == "REFERENCE_ONLY"
  assert found["blockers"]
  assert found["config_json"]["universe"]["exclude_st"] is True
  assert "未应用" in found["reason"]


def test_unsupported_indicator_is_not_silently_removed_from_combination(tmp_path):
  _write_indicator_run(tmp_path)
  conditions = [_condition(), _condition("roe_ttm", 5)]
  found = IndicatorResearchArtifactStore(tmp_path).match_reports(
    [_request(conditions=conditions)]
  )[0]
  assert found["status"] == "UNSUPPORTED"
  assert found["reports"] == []
  assert len(found["config_json"]["conditions"]) == 2


@pytest.mark.parametrize(
  "kwargs, expected",
  [
    ({"indicator_version": "daily-old"}, "MISSING"),
    ({"date_count": 3}, "DATA_INSUFFICIENT"),
    ({"horizons": [1, 5]}, "REFERENCE_ONLY"),
    ({"stock_codes": ["000001.SZ"]}, "REFERENCE_ONLY"),
  ],
)
def test_version_window_coverage_and_stock_subset_are_not_false_matches(
  tmp_path, kwargs, expected
):
  _write_indicator_run(tmp_path, **kwargs)
  found = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == expected


def test_exact_default_report_precedes_newer_reference_run(tmp_path):
  key, _, _ = _write_indicator_run(tmp_path)
  _write_indicator_run(
    tmp_path,
    run_id="20260902-100000-abcd1234",
    completed_at="2026-09-02T10:00:00+08:00",
    horizons=[1, 5],
  )
  found = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == "MATCHED"
  assert found["reports"][0]["run_key"] == key
  assert len(found["reports"]) == 2
  assert found["reports"][0]["match_status"] == "MATCHED"
  assert found["reports"][1]["match_status"] == "REFERENCE_ONLY"
  assert found["reports"][1]["match_reason"]


def test_match_stops_after_latest_exact_result(tmp_path, monkeypatch):
  from quantx_api.research_artifacts import ResearchArtifactStore

  _write_indicator_run(
    tmp_path,
    conditions=[_condition(value=2)],
    run_id="20260901-100000-abcd1234",
    completed_at="2026-09-01T10:00:00+08:00",
  )
  _write_indicator_run(
    tmp_path,
    run_id="20260902-100000-abcd1234",
    completed_at="2026-09-02T10:00:00+08:00",
  )
  original_read = ResearchArtifactStore._read_json
  metric_reads = []

  def tracked_read(self, run_directory, filename, **kwargs):
    if filename == "metrics.json":
      metric_reads.append(run_directory.name)
    return original_read(self, run_directory, filename, **kwargs)

  monkeypatch.setattr(ResearchArtifactStore, "_read_json", tracked_read)
  found = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]

  assert found["status"] == "MATCHED"
  assert metric_reads == ["20260902-100000-abcd1234"]


def test_match_discovery_fails_closed_before_unbounded_manifest_scan(
  tmp_path,
  monkeypatch,
):
  import quantx_api.indicator_research_artifacts as artifacts
  from quantx_api.research_artifacts import ResearchArtifactStore

  _write_indicator_run(
    tmp_path,
    run_id="20260901-100000-abcd1234",
    completed_at="2026-09-01T10:00:00+08:00",
  )
  _write_indicator_run(
    tmp_path,
    run_id="20260902-100000-abcd1234",
    completed_at="2026-09-02T10:00:00+08:00",
  )
  manifest_reads = []
  original_read = ResearchArtifactStore._read_json

  def tracked_read(self, run_directory, filename, **kwargs):
    if filename == "manifest.json":
      manifest_reads.append(run_directory.name)
    return original_read(self, run_directory, filename, **kwargs)

  monkeypatch.setattr(artifacts, "MAX_INDICATOR_MATCH_ENTRIES", 1)
  monkeypatch.setattr(ResearchArtifactStore, "_read_json", tracked_read)

  found = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]

  assert found["status"] == "ARTIFACT_ERROR"
  assert "安全预算" in found["reason"]
  assert manifest_reads == []


def test_report_detail_discovery_fails_closed_before_unbounded_manifest_scan(
  tmp_path,
  monkeypatch,
):
  import quantx_api.indicator_research_artifacts as artifacts
  from quantx_api.research_artifacts import ResearchArtifactStore

  key, _, _ = _write_indicator_run(
    tmp_path,
    run_id="20260901-100000-abcd1234",
    completed_at="2026-09-01T10:00:00+08:00",
  )
  _write_indicator_run(
    tmp_path,
    run_id="20260902-100000-abcd1234",
    completed_at="2026-09-02T10:00:00+08:00",
  )
  manifest_reads = []
  original_read = ResearchArtifactStore._read_json

  def tracked_read(self, run_directory, filename, **kwargs):
    if filename == "manifest.json":
      manifest_reads.append(run_directory.name)
    return original_read(self, run_directory, filename, **kwargs)

  monkeypatch.setattr(artifacts, "MAX_INDICATOR_MATCH_ENTRIES", 1)
  monkeypatch.setattr(ResearchArtifactStore, "_read_json", tracked_read)

  with pytest.raises(ResearchArtifactError, match="安全预算"):
    IndicatorResearchArtifactStore(tmp_path).get_indicator_report(key, "joint-test")

  assert manifest_reads == []


@pytest.mark.parametrize("operation", ["list", "detail"])
def test_generic_indicator_discovery_bounds_aggregate_data_quality_reads(
  tmp_path,
  monkeypatch,
  operation,
):
  import quantx_api.research_artifacts as artifacts

  key, _, first = _write_indicator_run(
    tmp_path,
    run_id="20260901-100000-abcd1234",
  )
  _, _, second = _write_indicator_run(
    tmp_path,
    run_id="20260902-100000-abcd1234",
  )
  quality_bytes = sum(
    (run_directory / "data-quality.json").stat().st_size
    for run_directory in (first, second)
  )
  monkeypatch.setattr(
    artifacts,
    "MAX_RESEARCH_DISCOVERY_DATA_QUALITY_BYTES",
    quality_bytes - 1,
  )
  manifest_reads = []
  original_read = ResearchArtifactStore._read_json

  def tracked_read(self, run_directory, filename, **kwargs):
    if filename == "manifest.json":
      manifest_reads.append(run_directory.name)
    return original_read(self, run_directory, filename, **kwargs)

  monkeypatch.setattr(ResearchArtifactStore, "_read_json", tracked_read)
  store = ResearchArtifactStore(tmp_path)

  with pytest.raises(ResearchArtifactError, match="安全预算"):
    store.list_runs() if operation == "list" else store.get_run(key)

  assert manifest_reads == []


@pytest.mark.parametrize("budget_kind", ["runs", "bytes"])
def test_match_reports_fail_closed_when_scan_budget_is_exhausted(
  tmp_path,
  monkeypatch,
  budget_kind,
):
  import quantx_api.indicator_research_artifacts as artifacts

  _write_indicator_run(
    tmp_path,
    conditions=[_condition(value=2)],
    run_id="20260902-100000-abcd1234",
    completed_at="2026-09-02T10:00:00+08:00",
  )
  _write_indicator_run(
    tmp_path,
    run_id="20260901-100000-abcd1234",
    completed_at="2026-09-01T10:00:00+08:00",
  )
  if budget_kind == "runs":
    monkeypatch.setattr(artifacts, "MAX_INDICATOR_MATCH_RUNS", 1)
  else:
    monkeypatch.setattr(artifacts, "MAX_INDICATOR_MATCH_BYTES", 1)

  found = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]

  assert found["status"] == "ARTIFACT_ERROR"
  assert "安全预算" in found["reason"]


@pytest.mark.parametrize(
  "date_range, expected",
  [
    (["2021-08-31", "2026-08-31"], "MATCHED"),
    (["2019-02-28", "2024-02-29"], "MATCHED"),
    (["2025-08-31", "2026-08-31"], "REFERENCE_ONLY"),
    (["2020-08-31", "2026-08-31"], "REFERENCE_ONLY"),
    (["2026-08-31", "2021-08-31"], "REFERENCE_ONLY"),
    (["invalid", "2026-08-31"], "REFERENCE_ONLY"),
    (None, "REFERENCE_ONLY"),
  ],
)
def test_match_uses_frozen_history_window_not_declared_years_or_actual_coverage(
  tmp_path,
  date_range,
  expected,
):
  _, _, path = _write_indicator_run(tmp_path)
  # An explicit date_range overrides lookback_years in the research runner.
  (path / "resolved-config.yaml").write_text(
    json.dumps(
      {
        "study": "indicator-study",
        "date_range": date_range,
        "universe": {"lookback_years": 5},
      }
    ),
    encoding="utf-8",
  )
  result = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert result["status"] == expected
  assert result["reports"][0]["match_status"] == expected
  if expected == "REFERENCE_ONLY":
    assert "历史区间" in result["reports"][0]["match_reason"]


def test_history_config_change_or_deletion_is_not_hidden_by_metrics_cache(tmp_path):
  _, _, path = _write_indicator_run(tmp_path)
  store = IndicatorResearchArtifactStore(tmp_path)
  assert store.match_reports([_request()])[0]["status"] == "MATCHED"
  config = path / "resolved-config.yaml"
  config.write_text("date_range: [2025-08-31, 2026-08-31]\n", encoding="utf-8")
  assert store.match_reports([_request()])[0]["status"] == "REFERENCE_ONLY"
  config.unlink()
  missing = store.match_reports([_request()])[0]
  assert missing["status"] == "REFERENCE_ONLY"
  assert "缺失或无效" in missing["reason"]


def test_report_projection_and_existing_research_details_are_bounded(tmp_path):
  key, _, _ = _write_indicator_run(tmp_path)
  store = IndicatorResearchArtifactStore(tmp_path)
  detail = store.get_indicator_report(key, "joint-test")
  assert detail["rows"][0]["up_rate"] == 0.52
  assert "private_path" not in detail["rows"][0]
  assert "private" not in detail["definitions"][0]
  assert len(store.get_run(key).indicator_reports) == 1
  assert store.get_indicator_report(key, "not-present") is None
  with pytest.raises(ResearchArtifactError):
    store.get_indicator_report(key, "../../report")


def test_invalid_latest_artifact_is_visible_as_error_not_a_match(tmp_path):
  _, metrics, path = _write_indicator_run(tmp_path)
  metrics["reports"][0]["conditions"] = [_condition("nonexistent")]
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  found = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == "ARTIFACT_ERROR"


def test_legacy_success_coverage_cannot_publish_or_match_reports(tmp_path):
  key, _, path = _write_indicator_run(tmp_path)
  quality_path = path / "data-quality.json"
  quality = json.loads(quality_path.read_text(encoding="utf-8"))
  coverage = quality["dividend_factor_coverage"]
  coverage.pop("evidence_schema_version")
  coverage.pop("verified_code_window_count")
  coverage.pop("evidence_content_sha256")
  _write_indexed_quality(path, quality)

  exact = _request()
  reference = _request(exclude_st=True)
  reference["request_id"] = "reference"
  store = IndicatorResearchArtifactStore(tmp_path)
  matches = store.match_reports([exact, reference])

  assert [item["status"] for item in matches] == [
    "ARTIFACT_ERROR",
    "ARTIFACT_ERROR",
  ]
  assert all(not item["reports"] for item in matches)
  assert store.list_runs(study_id="indicator-study")[1] == 0
  assert store.get_run(key) is None
  assert ResearchArtifactStore(tmp_path).get_run(key) is None
  with pytest.raises(ResearchArtifactError, match="无法安全读取"):
    store.get_indicator_report(key, "joint-test")


@pytest.mark.parametrize(
  ("field", "value"),
  [
    ("evidence_schema_version", True),
    ("verified_code_window_count", True),
    ("verified_code_window_count", 0),
    ("evidence_content_sha256", "A" * 64),
    ("evidence_content_sha256", "a" * 63),
    ("requested_codes", ["000001.SZ", "000001.SZ"]),
    ("covered_codes", []),
    ("uncovered_codes", ["000001.SZ"]),
  ],
)
def test_indicator_publication_coverage_validator_fails_closed(field, value):
  coverage = {
    "is_complete": True,
    "evidence_schema_version": 2,
    "verified_code_window_count": 1,
    "evidence_content_sha256": "a" * 64,
    "requested_codes": ["000001.SZ"],
    "covered_codes": ["000001.SZ"],
    "uncovered_codes": [],
  }
  coverage[field] = value

  with pytest.raises(ResearchArtifactError, match="schema-v2"):
    validate_indicator_study_data_quality({"dividend_factor_coverage": coverage})


@pytest.mark.parametrize("index_field", ["bytes", "sha256"])
def test_indicator_publication_requires_data_quality_bytes_and_sha_match(
  tmp_path,
  index_field,
):
  _, _, path = _write_indicator_run(tmp_path)
  manifest_path = path / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  entry = manifest["artifacts"][0]
  entry[index_field] = entry[index_field] + 1 if index_field == "bytes" else "0" * 64
  manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

  result = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]

  assert result["status"] == "ARTIFACT_ERROR"
  assert result["reports"] == []


def test_joint_empty_cohort_is_insufficient_even_when_base_coverage_is_large(tmp_path):
  _, metrics, path = _write_indicator_run(tmp_path)
  metrics["reports"][0]["rows"][0].update(sample_count=0, date_count=0)
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  found = IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == "DATA_INSUFFICIENT"


def test_full_report_exposes_frozen_research_choices_not_private_output_paths(tmp_path):
  key, _, path = _write_indicator_run(tmp_path)
  (path / "resolved-config.yaml").write_text(
    """
study: indicator-study
version: v1
date_range: [2021-01-01, 2025-12-31]
indicator_ids: [volume_ratio]
conditions: []
runtime:
  batch_size: 100
  output_root: F:/private/location
  secret: hidden
statistics:
  bootstrap_samples: 1000
  confidence_level: 0.95
""",
    encoding="utf-8",
  )
  config = IndicatorResearchArtifactStore(tmp_path).get_indicator_report(
    key, "joint-test"
  )["config_json"]
  assert config["date_range"] == ["2021-01-01", "2025-12-31"]
  assert config["runtime"] == {"batch_size": 100}
  assert config["statistics"]["confidence_level"] == 0.95


def test_run_type_filter_applies_before_pagination(tmp_path):
  _write_indicator_run(tmp_path)
  store = IndicatorResearchArtifactStore(tmp_path)
  assert store.list_runs(study_id="volume-shock", limit=1)[1] == 0
  assert store.list_runs(study_id="indicator-study", limit=1)[1] == 1


def test_summary_cache_reuses_only_projections_and_invalidates_change_or_delete(
  tmp_path, monkeypatch
):
  from quantx_api.indicator_research_artifacts import _SUMMARY_CACHE
  from quantx_api.research_artifacts import ResearchArtifactStore

  _, metrics, path = _write_indicator_run(tmp_path)
  original_read = ResearchArtifactStore._read_json
  metric_reads = []

  def tracked_read(self, run_directory, filename, **kwargs):
    if filename == "metrics.json":
      metric_reads.append(filename)
    return original_read(self, run_directory, filename, **kwargs)

  monkeypatch.setattr(ResearchArtifactStore, "_read_json", tracked_read)
  assert (
    IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"]
    == "MATCHED"
  )
  assert (
    IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"]
    == "MATCHED"
  )
  assert len(metric_reads) == 1
  for payload, _ in _SUMMARY_CACHE.values():
    assert all(
      "rows" not in report and "definitions" not in report
      for report in payload["reports"]
    )
  metrics["indicator_version"] = "daily-v-old"
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  assert (
    IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"]
    == "MISSING"
  )
  assert len(metric_reads) == 2
  (path / "metrics.json").unlink()
  assert (
    IndicatorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"]
    == "ARTIFACT_ERROR"
  )


def test_rejects_unknown_condition_operators_and_duplicate_request_ids(tmp_path):
  store = IndicatorResearchArtifactStore(tmp_path)
  with pytest.raises(ValueError):
    store.match_reports(
      [_request(conditions=[{**_condition(), "operator": "execute"}])]
    )
  with pytest.raises(ResearchArtifactError):
    store.match_reports([_request(), _request()])


@pytest.mark.asyncio
async def test_indicator_queries_are_typed_and_readonly(
  tmp_path, monkeypatch, authorized_graphql_context
):
  from quantx_api.gqlapi.schema import schema

  key, _, _ = _write_indicator_run(tmp_path)
  monkeypatch.setenv("QUANTX_RESEARCH_RUNS_ROOT", str(tmp_path))
  result = await schema.execute(
    """
    query($key:String!) {
      stockIndicatorCatalog { id version operators researchSupported }
      stockIndicatorReportMatches(requests:[{
        requestId:"current",kind:"joint",indicatorIds:["volume_ratio"],excludeSt:false,
        conditions:[{indicatorId:"volume_ratio",operator:"gte",value:1.5}]
      }]) { status configJson command blockers reports { runKey reportId } }
      indicatorReport(runKey:$key,reportId:"joint-test") {
        indicatorVersion horizons rows { horizon sampleCount upRate inferenceStatus }
        reference { runKey dataEnd } artifactErrors
      }
      researchRun(key:$key) { indicatorReports { reportId } }
    }
  """,
    variable_values={"key": key},
    context_value=authorized_graphql_context,
  )
  assert result.errors is None
  assert result.data["stockIndicatorReportMatches"][0]["status"] == "MATCHED"
  assert (
    result.data["stockIndicatorReportMatches"][0]["configJson"]["universe"]
    == BASE_UNIVERSE
  )
  assert result.data["indicatorReport"]["rows"][0]["upRate"] == 0.52
  assert result.data["researchRun"]["indicatorReports"][0]["reportId"] == "joint-test"
  assert not any(
    "score" in value.lower() for value in result.data["stockIndicatorCatalog"][0]
  )


def test_frontend_indicator_and_research_documents_validate_against_schema():
  from graphql import parse, validate
  from quantx_api.gqlapi.schema import schema

  repository = Path(__file__).resolve().parents[4]
  for relative in (
    "apps/web/src/features/screening/hooks/indicators.gql",
    "apps/web/src/features/research/hooks/queries.gql",
  ):
    errors = validate(
      schema._schema, parse((repository / relative).read_text(encoding="utf-8"))
    )
    assert errors == [], f"{relative}: {[error.message for error in errors]}"
