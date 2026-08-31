import json
from pathlib import Path

import pytest
from quantx_api.factor_research_artifacts import (
  BASE_UNIVERSE,
  DEFAULT_HORIZONS,
  FactorResearchArtifactStore,
)
from quantx_api.research_artifacts import ResearchArtifactError, stable_run_key


def _condition(factor_id="volume_ratio", value=1.5):
  return {"factor_id": factor_id, "operator": "gte", "value": value}


def _request(*, conditions=None, exclude_st=False, factor_ids=None, kind="joint"):
  conditions = conditions if conditions is not None else [_condition()]
  return {
    "request_id": "current", "kind": kind,
    "factor_ids": factor_ids or sorted({item["factor_id"] for item in conditions}),
    "conditions": conditions,
    "universe": {**BASE_UNIVERSE, "exclude_st": exclude_st},
  }


def _write_factor_run(root, *, conditions=None, run_id="20260901-100000-abcd1234",
                      completed_at="2026-09-01T10:00:00+08:00", factor_version="daily-v1",
                      kind="joint", date_count=100, horizons=None, stock_codes=None):
  conditions = conditions if conditions is not None else [_condition()]
  path = root / "factor-study-v1" / run_id
  path.mkdir(parents=True)
  key = stable_run_key(study_id="factor-study", version="v1", run_id=run_id)
  (path / "manifest.json").write_text(json.dumps({
    "run_id": run_id, "study_id": "factor-study", "version": "v1", "status": "success",
    "completed_at": completed_at, "config_hash": "a" * 64,
  }), encoding="utf-8")
  (path / "resolved-config.yaml").write_text(
    "study: factor-study\ndate_range: [2021-08-31, 2026-08-31]\n", encoding="utf-8",
  )
  (path / "data-quality.json").write_text('{"is_usable":true}', encoding="utf-8")
  row = {
    "group": "joint", "horizon": 1, "return_basis": "close", "period": "all",
    "sample_count": 1000, "stock_count": 20, "date_count": date_count,
    "up_rate": 0.52, "mean_return": 0.001, "median_return": 0.002,
    "date_equal_up_rate": 0.51, "date_equal_mean_return": 0.001,
    "baseline_up_rate": 0.5, "up_rate_lift": 0.01, "mean_return_lift": 0.001,
    "ci_low": -0.01, "ci_high": 0.02, "p_value": 0.4, "q_value": 0.6,
    "inference_status": "descriptive", "private_path": "never expose",
  }
  report = {
    "report_id": "joint-test", "kind": kind,
    "factor_ids": ["volume_ratio"] if kind == "single" else sorted({item["factor_id"] for item in conditions}),
    "conditions": conditions, "definitions": [{"id": "volume_ratio", "private": "hidden"}],
    "coverage": {"valid_count": 1000, "date_count": date_count, "requested_stock_codes": stock_codes},
    "distribution": [], "rows": [row], "warnings": ["不是个股预测概率"],
  }
  metrics = {
    "schema_version": 1, "factor_version": factor_version, "universe": BASE_UNIVERSE,
    "horizons": horizons or DEFAULT_HORIZONS, "return_bases": ["close", "next_open"],
    "data_start": "2021-09-01", "data_end": "2026-08-31", "reports": [report], "warnings": [],
  }
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  return key, metrics, path


def test_match_normalizes_order_and_deduplicates_without_ignoring_thresholds(tmp_path):
  conditions = [_condition(), _condition("rsi12", 0)]
  _write_factor_run(tmp_path, conditions=conditions)
  store = FactorResearchArtifactStore(tmp_path)
  found = store.match_reports([_request(conditions=[conditions[1], conditions[0], conditions[1]])])[0]
  assert found["status"] == "MATCHED"
  assert len(found["reports"]) == 1
  changed = store.match_reports([_request(conditions=[_condition(value=2), conditions[1]])])[0]
  assert changed["status"] == "MISSING"
  assert changed["reports"] == []


def test_historical_st_filters_only_reference_broader_report_and_block_execution(tmp_path):
  _write_factor_run(tmp_path)
  found = FactorResearchArtifactStore(tmp_path).match_reports([_request(exclude_st=True)])[0]
  assert found["status"] == "REFERENCE_ONLY"
  assert found["blockers"]
  assert found["config_json"]["universe"]["exclude_st"] is True
  assert "未应用" in found["reason"]


def test_unsupported_factor_is_not_silently_removed_from_combination(tmp_path):
  _write_factor_run(tmp_path)
  conditions = [_condition(), _condition("roe_ttm", 5)]
  found = FactorResearchArtifactStore(tmp_path).match_reports([_request(conditions=conditions)])[0]
  assert found["status"] == "UNSUPPORTED"
  assert found["reports"] == []
  assert len(found["config_json"]["conditions"]) == 2


@pytest.mark.parametrize("kwargs, expected", [
  ({"factor_version": "daily-old"}, "MISSING"),
  ({"date_count": 3}, "DATA_INSUFFICIENT"),
  ({"horizons": [1, 5]}, "REFERENCE_ONLY"),
  ({"stock_codes": ["000001.SZ"]}, "REFERENCE_ONLY"),
])
def test_version_window_coverage_and_stock_subset_are_not_false_matches(tmp_path, kwargs, expected):
  _write_factor_run(tmp_path, **kwargs)
  found = FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == expected


def test_exact_default_report_precedes_newer_reference_run(tmp_path):
  key, _, _ = _write_factor_run(tmp_path)
  _write_factor_run(tmp_path, run_id="20260902-100000-abcd1234",
                    completed_at="2026-09-02T10:00:00+08:00", horizons=[1, 5])
  found = FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == "MATCHED"
  assert found["reports"][0]["run_key"] == key
  assert len(found["reports"]) == 2
  assert found["reports"][0]["match_status"] == "MATCHED"
  assert found["reports"][1]["match_status"] == "REFERENCE_ONLY"
  assert found["reports"][1]["match_reason"]


@pytest.mark.parametrize("date_range, expected", [
  (["2021-08-31", "2026-08-31"], "MATCHED"),
  (["2019-02-28", "2024-02-29"], "MATCHED"),
  (["2025-08-31", "2026-08-31"], "REFERENCE_ONLY"),
  (["2020-08-31", "2026-08-31"], "REFERENCE_ONLY"),
  (["2026-08-31", "2021-08-31"], "REFERENCE_ONLY"),
  (["invalid", "2026-08-31"], "REFERENCE_ONLY"),
  (None, "REFERENCE_ONLY"),
])
def test_match_uses_frozen_history_window_not_declared_years_or_actual_coverage(
  tmp_path, date_range, expected,
):
  _, _, path = _write_factor_run(tmp_path)
  # An explicit date_range overrides lookback_years in the research runner.
  (path / "resolved-config.yaml").write_text(json.dumps({
    "study": "factor-study", "date_range": date_range,
    "universe": {"lookback_years": 5},
  }), encoding="utf-8")
  result = FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert result["status"] == expected
  assert result["reports"][0]["match_status"] == expected
  if expected == "REFERENCE_ONLY":
    assert "历史区间" in result["reports"][0]["match_reason"]


def test_history_config_change_or_deletion_is_not_hidden_by_metrics_cache(tmp_path):
  _, _, path = _write_factor_run(tmp_path)
  store = FactorResearchArtifactStore(tmp_path)
  assert store.match_reports([_request()])[0]["status"] == "MATCHED"
  config = path / "resolved-config.yaml"
  config.write_text("date_range: [2025-08-31, 2026-08-31]\n", encoding="utf-8")
  assert store.match_reports([_request()])[0]["status"] == "REFERENCE_ONLY"
  config.unlink()
  missing = store.match_reports([_request()])[0]
  assert missing["status"] == "REFERENCE_ONLY"
  assert "缺失或无效" in missing["reason"]


def test_report_projection_and_existing_research_details_are_bounded(tmp_path):
  key, _, _ = _write_factor_run(tmp_path)
  store = FactorResearchArtifactStore(tmp_path)
  detail = store.get_factor_report(key, "joint-test")
  assert detail["rows"][0]["up_rate"] == 0.52
  assert "private_path" not in detail["rows"][0]
  assert "private" not in detail["definitions"][0]
  assert len(store.get_run(key).factor_reports) == 1
  assert store.get_factor_report(key, "not-present") is None
  with pytest.raises(ResearchArtifactError):
    store.get_factor_report(key, "../../report")


def test_invalid_latest_artifact_is_visible_as_error_not_a_match(tmp_path):
  _, metrics, path = _write_factor_run(tmp_path)
  metrics["reports"][0]["conditions"] = [_condition("nonexistent")]
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  found = FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == "ARTIFACT_ERROR"


def test_joint_empty_cohort_is_insufficient_even_when_base_coverage_is_large(tmp_path):
  _, metrics, path = _write_factor_run(tmp_path)
  metrics["reports"][0]["rows"][0].update(sample_count=0, date_count=0)
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  found = FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]
  assert found["status"] == "DATA_INSUFFICIENT"


def test_full_report_exposes_frozen_research_choices_not_private_output_paths(tmp_path):
  key, _, path = _write_factor_run(tmp_path)
  (path / "resolved-config.yaml").write_text("""
study: factor-study
version: v1
date_range: [2021-01-01, 2025-12-31]
factor_ids: [volume_ratio]
conditions: []
runtime:
  batch_size: 100
  output_root: F:/private/location
  secret: hidden
statistics:
  bootstrap_samples: 1000
  confidence_level: 0.95
""", encoding="utf-8")
  config = FactorResearchArtifactStore(tmp_path).get_factor_report(key, "joint-test")["config_json"]
  assert config["date_range"] == ["2021-01-01", "2025-12-31"]
  assert config["runtime"] == {"batch_size": 100}
  assert config["statistics"]["confidence_level"] == 0.95


def test_run_type_filter_applies_before_pagination(tmp_path):
  _write_factor_run(tmp_path)
  store = FactorResearchArtifactStore(tmp_path)
  assert store.list_runs(study_id="volume-shock", limit=1)[1] == 0
  assert store.list_runs(study_id="factor-study", limit=1)[1] == 1


def test_summary_cache_reuses_only_projections_and_invalidates_change_or_delete(tmp_path, monkeypatch):
  from quantx_api.factor_research_artifacts import _SUMMARY_CACHE
  from quantx_api.research_artifacts import ResearchArtifactStore

  _, metrics, path = _write_factor_run(tmp_path)
  original_read = ResearchArtifactStore._read_json
  metric_reads = []

  def tracked_read(self, run_directory, filename, **kwargs):
    if filename == "metrics.json":
      metric_reads.append(filename)
    return original_read(self, run_directory, filename, **kwargs)

  monkeypatch.setattr(ResearchArtifactStore, "_read_json", tracked_read)
  assert FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"] == "MATCHED"
  assert FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"] == "MATCHED"
  assert len(metric_reads) == 1
  for payload, _ in _SUMMARY_CACHE.values():
    assert all("rows" not in report and "definitions" not in report for report in payload["reports"])
  metrics["factor_version"] = "daily-v-old"
  (path / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
  assert FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"] == "MISSING"
  assert len(metric_reads) == 2
  (path / "metrics.json").unlink()
  assert FactorResearchArtifactStore(tmp_path).match_reports([_request()])[0]["status"] == "ARTIFACT_ERROR"


def test_rejects_unknown_condition_operators_and_duplicate_request_ids(tmp_path):
  store = FactorResearchArtifactStore(tmp_path)
  with pytest.raises(ValueError):
    store.match_reports([_request(conditions=[{**_condition(), "operator": "execute"}])])
  with pytest.raises(ResearchArtifactError):
    store.match_reports([_request(), _request()])


@pytest.mark.asyncio
async def test_factor_queries_are_typed_and_readonly(tmp_path, monkeypatch, authorized_graphql_context):
  from quantx_api.gqlapi.schema import schema

  key, _, _ = _write_factor_run(tmp_path)
  monkeypatch.setenv("QUANTX_RESEARCH_RUNS_ROOT", str(tmp_path))
  result = await schema.execute("""
    query($key:String!) {
      stockFactorCatalog { id version operators researchSupported }
      stockFactorReportMatches(requests:[{
        requestId:"current",kind:"joint",factorIds:["volume_ratio"],excludeSt:false,
        conditions:[{factorId:"volume_ratio",operator:"gte",value:1.5}]
      }]) { status configJson command blockers reports { runKey reportId } }
      factorReport(runKey:$key,reportId:"joint-test") {
        factorVersion horizons rows { horizon sampleCount upRate inferenceStatus }
        reference { runKey dataEnd } artifactErrors
      }
      researchRun(key:$key) { factorReports { reportId } }
    }
  """, variable_values={"key": key}, context_value=authorized_graphql_context)
  assert result.errors is None
  assert result.data["stockFactorReportMatches"][0]["status"] == "MATCHED"
  assert result.data["factorReport"]["rows"][0]["upRate"] == 0.52
  assert result.data["researchRun"]["factorReports"][0]["reportId"] == "joint-test"
  assert not any("score" in value.lower() for value in result.data["stockFactorCatalog"][0])


def test_frontend_factor_and_research_documents_validate_against_schema():
  from graphql import parse, validate
  from quantx_api.gqlapi.schema import schema

  repository = Path(__file__).resolve().parents[4]
  for relative in (
    "apps/web/src/features/screening/hooks/factors.gql",
    "apps/web/src/features/research/hooks/queries.gql",
  ):
    errors = validate(schema._schema, parse((repository / relative).read_text(encoding="utf-8")))
    assert errors == [], f"{relative}: {[error.message for error in errors]}"
