import json
from pathlib import Path

import pytest
from quantx_api.research_artifacts import (
  ResearchArtifactError,
  ResearchArtifactStore,
  research_runs_root,
  stable_run_key,
)


def _write_run(
  root: Path,
  *,
  study_id: str = "volume-shock",
  version: str = "v1",
  run_id: str = "20260729-120000-abcdef12",
  status: str = "success",
  completed_at: str = "2026-07-29T12:01:00+00:00",
  metrics: dict | None = None,
) -> Path:
  run_dir = root / f"{study_id}-{version}" / run_id
  run_dir.mkdir(parents=True)
  manifest = {
    "run_id": run_id,
    "study_id": study_id,
    "version": version,
    "status": status,
    "started_at": "2026-07-29T12:00:00+00:00",
    "completed_at": completed_at,
    "event_count": 12,
    "elapsed_seconds": 60.5,
    "config_hash": "a" * 64,
    "git": {"commit": "must-not-be-exposed"},
    "artifacts": [{"path": "report.html"}],
  }
  (run_dir / "manifest.json").write_text(
    json.dumps(manifest),
    encoding="utf-8",
  )
  (run_dir / "resolved-config.yaml").write_text(
    "study: volume-shock\nsecret: must-not-be-exposed\n",
    encoding="utf-8",
  )
  (run_dir / "data-quality.json").write_text(
    json.dumps(
      {
        "is_usable": True,
        "row_count": 1000,
        "requested_codes": ["000001.SZ"],
        "coverage": [
          {
            "stock_code": "000001.SZ",
            "rows": 1000,
            "private_path": "must-not-be-exposed",
          }
        ],
        "source_provenance": {
          "kind": "qmt-daily-bar-archive",
          "archive_format": "quantx-qmt-daily-bars-source-v1",
          "schema_version": 1,
          "ledger_path": "F:/private/research-source/ledger.json",
          "ledger_sha256": "b" * 64,
          "metadata_universe_validated": True,
          "boundary_tolerance_days": 7,
          "required_request_count": 180,
          "selected_request_count": 180,
          "selected_chunk_count": 1132,
          "selected_source_record_count": 5_470_541,
          "selected_chunk_manifest_sha256": "c" * 64,
          "emitted_rows": 5_470_541,
          "campaign": {
            "run_key": "full-a-share-v2",
            "start_date": "20200313",
            "end_date": "20260729",
            "universe_sha256": "d" * 64,
            "job_plan_sha256": "e" * 64,
            "source_state_path": "F:/private/campaign.json",
          },
          "preprocessing": {
            "compatibility": "quantx-worker-preprocess-market-data",
            "price_decimals": 3,
            "volume_amount_decimals": 2,
            "timezone": "Asia/Shanghai",
            "private_option": "must-not-be-exposed",
          },
          "queries": [
            {
              "requested_start": "2020-03-13",
              "requested_end": "2026-07-30",
              "requested_code_count": 300,
              "requested_codes_sha256": "f" * 64,
              "selected_request_count": 12,
              "available_start": "2020-03-13",
              "available_end": "2026-07-29",
              "boundary_truncated": True,
              "emitted_rows": 300_000,
              "private_path": "must-not-be-exposed",
            }
          ],
          "requests": [
            {
              "request_id": "must-not-be-exposed",
              "job_id": "must-not-be-exposed",
            }
          ],
        },
        "data_fingerprint": "must-not-be-exposed",
      }
    ),
    encoding="utf-8",
  )
  if metrics is None and status == "success":
    metrics = _sample_metrics()
  if metrics is not None:
    (run_dir / "metrics.json").write_text(
      json.dumps(metrics),
      encoding="utf-8",
    )
  return run_dir


def _sample_metrics() -> dict:
  statistic = {
    "dimensions": {
      "event_direction": "up",
      "price_position_bin": "high",
      "rvol_bin": "[2,3)",
      "unknown_dimension": "must-not-be-exposed",
    },
    "return_kind": "close_response",
    "horizon": 5,
    "benchmark": "absolute",
    "sample_size": 12,
    "mean": 0.02,
    "median": 0.01,
    "positive_rate": 0.6,
    "p05": -0.03,
    "p25": -0.01,
    "p75": 0.03,
    "p95": 0.08,
    "mae_mean": -0.04,
    "mfe_mean": 0.09,
    "ci_low": -0.01,
    "ci_high": 0.04,
    "p_value": 0.1,
    "q_value": 0.2,
    "significant": False,
    "private_value": "must-not-be-exposed",
  }
  return {
    "event_count": 12,
    "study_id": "volume-shock",
    "version": "v1",
    "event_curve": [
      {
        "return_kind": "close_response",
        "horizon": 5,
        "benchmark": "absolute",
        "sample_size": 12,
        "unique_dates": 8,
        "mean": 0.02,
        "median": 0.01,
        "positive_rate": 0.6,
        "ci_low": -0.01,
        "ci_high": 0.04,
        "private_value": "must-not-be-exposed",
      }
    ],
    "grouped_statistics": [statistic],
    "analysis_sample_count": 120,
    "comparison": [
      {
        "dimensions": {
          "comparison": "shock_minus_normal",
          "price_position_bin": "high",
          "unknown_dimension": "must-not-be-exposed",
        },
        "return_kind": "close_response",
        "horizon": 5,
        "benchmark": "absolute",
        "shock_sample_size": 12,
        "normal_sample_size": 18,
        "unique_dates": 8,
        "shock_mean": 0.02,
        "shock_median": 0.01,
        "normal_mean": 0.005,
        "normal_median": 0.004,
        "spread_mean": 0.015,
        "spread_median": 0.006,
        "ci_low": 0.001,
        "ci_high": 0.029,
        "p_value": 0.04,
        "q_value": 0.08,
        "significant": False,
        "private_value": "must-not-be-exposed",
      }
    ],
    "comparison_sensitivity": {
      "cooldown_5d": [
        {
          "dimensions": {
            "comparison": "shock_minus_normal",
            "price_position_bin": "high",
          },
          "return_kind": "close_response",
          "horizon": 5,
          "benchmark": "absolute",
          "shock_sample_size": 14,
          "normal_sample_size": 18,
          "unique_dates": 8,
          "shock_mean": 0.018,
          "normal_mean": 0.005,
          "spread_mean": 0.013,
          "ci_low": -0.001,
          "ci_high": 0.027,
          "p_value": 0.06,
          "q_value": 0.12,
          "significant": False,
        }
      ]
    },
    "regressions": [
      {
        "return_kind": "close_response",
        "horizon": 5,
        "dependent_variable": "csi300_excess_close_h5",
        "nobs": 120,
        "r_squared": 0.12,
        "covariance": "two_way_cluster",
        "coefficients": [
          {
            "term": "volume_position_interaction",
            "estimate": 0.02,
            "std_error": 0.01,
            "t_stat": 2.0,
            "p_value": 0.04,
            "ci_low": 0.001,
            "ci_high": 0.039,
            "q_value": 0.08,
            "significant": False,
            "private_value": "must-not-be-exposed",
          }
        ],
        "warnings": [],
        "private_value": "must-not-be-exposed",
      }
    ],
    "robustness": {"cooldown_5d": [statistic]},
    "warnings": ["历史相关性不构成因果证据"],
    "private_value": "must-not-be-exposed",
  }


def test_store_lists_final_runs_with_opaque_stable_keys_and_pagination(tmp_path):
  older = _write_run(
    tmp_path,
    run_id="20260728-120000-aaaaaaaa",
    completed_at="2026-07-28T12:01:00+00:00",
  )
  newer = _write_run(
    tmp_path,
    run_id="20260729-120000-bbbbbbbb",
    completed_at="2026-07-29T12:01:00+00:00",
    status="failed",
  )
  malformed = tmp_path / "volume-shock-v1" / "20260730-120000-cccccccc"
  malformed.mkdir()
  (malformed / "manifest.json").write_text("{broken", encoding="utf-8")

  store = ResearchArtifactStore(tmp_path)
  items, total = store.list_runs(limit=1, offset=0)

  assert total == 2
  assert [item.run_directory for item in items] == [newer]
  assert len(items[0].key) == 64
  assert "/" not in items[0].key
  assert "\\" not in items[0].key
  assert items[0].key == stable_run_key(
    study_id="volume-shock",
    version="v1",
    run_id=newer.name,
  )

  successes, success_total = store.list_runs(status="success")
  assert success_total == 1
  assert successes[0].run_directory == older

  _write_run(
    tmp_path,
    run_id="20260727-120000-dddddddd",
    completed_at="2026-07-27T12:01:00+00:00",
    status="failed_preflight",
  )
  failures, failure_total = store.list_runs(status="failed")
  assert failure_total == 2
  assert {item.status for item in failures} == {"failed", "failed_preflight"}


def test_store_discovers_and_filters_resource_failures(tmp_path):
  resource_failure = _write_run(
    tmp_path,
    run_id="20260730-120000-resource",
    status="failed_resource",
  )
  store = ResearchArtifactStore(tmp_path)

  resources, resource_total = store.list_runs(status="failed_resource")
  failures, failure_total = store.list_runs(status="failed")

  assert resource_total == 1
  assert resources[0].run_directory == resource_failure
  assert resources[0].status == "failed_resource"
  assert failure_total == 1
  assert failures[0].run_directory == resource_failure
  assert store.get_run(resources[0].key) is not None


def test_detail_exposes_only_whitelisted_quality_and_metric_fields(tmp_path):
  run_dir = _write_run(tmp_path)
  store = ResearchArtifactStore(tmp_path)
  summary = store.list_runs()[0][0]

  detail = store.get_run(summary.key)

  assert detail is not None
  assert detail.summary.study_id == "volume-shock"
  assert detail.data_quality == {
    "is_usable": True,
    "row_count": 1000,
    "requested_codes": ["000001.SZ"],
    "coverage": [{"stock_code": "000001.SZ", "rows": 1000}],
    "source_provenance": {
      "kind": "qmt-daily-bar-archive",
      "archive_format": "quantx-qmt-daily-bars-source-v1",
      "schema_version": 1,
      "ledger_sha256": "b" * 64,
      "metadata_universe_validated": True,
      "boundary_tolerance_days": 7,
      "required_request_count": 180,
      "selected_request_count": 180,
      "selected_chunk_count": 1132,
      "selected_source_record_count": 5_470_541,
      "selected_chunk_manifest_sha256": "c" * 64,
      "emitted_rows": 5_470_541,
      "campaign": {
        "run_key": "full-a-share-v2",
        "start_date": "20200313",
        "end_date": "20260729",
        "universe_sha256": "d" * 64,
        "job_plan_sha256": "e" * 64,
      },
      "preprocessing": {
        "compatibility": "quantx-worker-preprocess-market-data",
        "price_decimals": 3,
        "volume_amount_decimals": 2,
        "timezone": "Asia/Shanghai",
      },
      "queries": [
        {
          "requested_start": "2020-03-13",
          "requested_end": "2026-07-30",
          "requested_code_count": 300,
          "requested_codes_sha256": "f" * 64,
          "selected_request_count": 12,
          "available_start": "2020-03-13",
          "available_end": "2026-07-29",
          "boundary_truncated": True,
          "emitted_rows": 300_000,
        }
      ],
    },
  }
  assert detail.event_curve[0]["mean"] == 0.02
  assert detail.analysis_sample_count == 120
  assert "private_value" not in detail.event_curve[0]
  assert detail.interaction_heatmap[0]["dimensions"] == {
    "event_direction": "up",
    "price_position_bin": "high",
    "rvol_bin": "[2,3)",
  }
  assert "private_value" not in detail.regressions[0]
  assert "private_value" not in detail.regressions[0]["coefficients"][0]
  assert detail.regressions[0]["coefficients"][0]["q_value"] == 0.08
  assert detail.comparison[0]["dimensions"] == {
    "comparison": "shock_minus_normal",
    "price_position_bin": "high",
  }
  assert detail.comparison[0]["spread_mean"] == 0.015
  assert "private_value" not in detail.comparison[0]
  assert detail.comparison_sensitivity["cooldown_5d"][0]["spread_mean"] == 0.013
  assert detail.robustness["cooldown_5d"][0]["sample_size"] == 12
  assert detail.artifact_errors == ()
  assert not hasattr(detail, "manifest")
  assert not hasattr(detail, "resolved_config")
  assert run_dir.exists()


@pytest.mark.parametrize(
  "bad_key",
  [
    "../manifest.json",
    "..\\manifest.json",
    "a" * 63,
    "g" * 64,
    "",
  ],
)
def test_detail_rejects_non_opaque_keys(tmp_path, bad_key):
  _write_run(tmp_path)

  with pytest.raises(ResearchArtifactError, match="key 格式无效"):
    ResearchArtifactStore(tmp_path).get_run(bad_key)


@pytest.mark.parametrize(
  "payload",
  [
    "{broken",
    '{"event_curve": [{"mean": NaN}]}',
    '{"event_curve": [{"mean": Infinity}]}',
  ],
)
def test_bad_metrics_are_isolated_without_hiding_other_runs(tmp_path, payload):
  bad_dir = _write_run(tmp_path, run_id="20260729-120000-badbad12")
  good_dir = _write_run(tmp_path, run_id="20260729-120000-good1234")
  (bad_dir / "metrics.json").write_text(payload, encoding="utf-8")
  store = ResearchArtifactStore(tmp_path)
  items, total = store.list_runs()

  assert total == 2
  bad_summary = next(item for item in items if item.run_id == bad_dir.name)
  good_summary = next(item for item in items if item.run_id == good_dir.name)
  bad_detail = store.get_run(bad_summary.key)
  good_detail = store.get_run(good_summary.key)

  assert bad_detail is not None
  assert bad_detail.event_curve == []
  assert any("metrics.json 不可用" in item for item in bad_detail.artifact_errors)
  assert good_detail is not None
  assert good_detail.event_curve[0]["mean"] == 0.02


def test_oversized_metrics_are_not_read(tmp_path):
  run_dir = _write_run(tmp_path)
  (run_dir / "metrics.json").write_bytes(b" " * (8 * 1024 * 1024 + 1))
  store = ResearchArtifactStore(tmp_path)
  summary = store.list_runs()[0][0]

  detail = store.get_run(summary.key)

  assert summary.has_metrics is False
  assert detail is not None
  assert detail.event_curve == []
  assert any("超过 8388608 字节上限" in item for item in detail.artifact_errors)


def test_env_override_selects_research_root(monkeypatch, tmp_path):
  monkeypatch.setenv("QUANTX_RESEARCH_RUNS_ROOT", str(tmp_path))
  monkeypatch.setenv("QUANTX_ROOT", str(tmp_path / "ignored"))

  assert research_runs_root() == tmp_path
  assert ResearchArtifactStore().root == tmp_path.resolve()


def test_symlinked_metrics_cannot_escape_run_directory(tmp_path):
  run_dir = _write_run(tmp_path, status="failed")
  outside = tmp_path / "outside.json"
  outside.write_text(json.dumps(_sample_metrics()), encoding="utf-8")
  link = run_dir / "metrics.json"
  try:
    link.symlink_to(outside)
  except OSError:
    pytest.skip("当前 Windows 环境不允许创建测试符号链接")

  store = ResearchArtifactStore(tmp_path)
  summary = store.list_runs()[0][0]
  detail = store.get_run(summary.key)

  assert summary.has_metrics is False
  assert detail is not None
  assert detail.event_curve == []
  assert any("metrics.json 不可用" in item for item in detail.artifact_errors)


def test_symlinked_study_directory_is_not_enumerated(tmp_path):
  external = tmp_path.parent / f"{tmp_path.name}-external"
  _write_run(external)
  link = tmp_path / "volume-shock-v1"
  try:
    link.symlink_to(external / "volume-shock-v1", target_is_directory=True)
  except OSError:
    pytest.skip("当前 Windows 环境不允许创建测试符号链接")

  items, total = ResearchArtifactStore(tmp_path).list_runs()

  assert items == []
  assert total == 0


def test_api_source_does_not_import_research_application():
  api_root = Path(__file__).resolve().parents[4] / "apps" / "api" / "src"
  assert api_root.is_dir()
  sources = "\n".join(
    path.read_text(encoding="utf-8") for path in api_root.rglob("*.py")
  )
  assert sources
  assert "import quantx_research" not in sources
  assert "from quantx_research" not in sources
