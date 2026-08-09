import json

import pytest


def _create_success_run(root):
  run_id = "20260729-120000-abcdef12"
  run_dir = root / "volume-shock-v1" / run_id
  run_dir.mkdir(parents=True)
  (run_dir / "manifest.json").write_text(
    json.dumps(
      {
        "run_id": run_id,
        "study_id": "volume-shock",
        "version": "v1",
        "status": "success",
        "started_at": "2026-07-29T12:00:00+00:00",
        "completed_at": "2026-07-29T12:01:00+00:00",
        "event_count": 3,
        "elapsed_seconds": 60.0,
        "config_hash": "a" * 64,
        "git": {"commit": "not-public"},
      }
    ),
    encoding="utf-8",
  )
  (run_dir / "resolved-config.yaml").write_text(
    "study: volume-shock\n",
    encoding="utf-8",
  )
  (run_dir / "data-quality.json").write_text(
    json.dumps(
      {
        "is_usable": True,
        "row_count": 801,
        "source_provenance": {
          "kind": "qmt-daily-bar-archive",
          "ledger_sha256": "b" * 64,
          "selected_request_count": 180,
          "ledger_path": "must-not-be-exposed",
        },
      }
    ),
    encoding="utf-8",
  )
  (run_dir / "metrics.json").write_text(
    json.dumps(
      {
        "event_curve": [
          {
            "return_kind": "close_response",
            "horizon": 5,
            "benchmark": "absolute",
            "sample_size": 3,
            "mean": 0.01,
          }
        ],
        "grouped_statistics": [],
        "analysis_sample_count": 801,
        "comparison": [],
        "comparison_sensitivity": {},
        "regressions": [],
        "robustness": {},
        "warnings": ["测试告警"],
      }
    ),
    encoding="utf-8",
  )


@pytest.mark.asyncio
async def test_research_runs_and_detail_graphql_queries(
  monkeypatch,
  tmp_path,
  authorized_graphql_context,
):
  from quantx_api.gqlapi.schema import schema

  _create_success_run(tmp_path)
  monkeypatch.setenv("QUANTX_RESEARCH_RUNS_ROOT", str(tmp_path))

  list_result = await schema.execute(
    """
    query ResearchRuns {
      researchRuns(limit: 20, offset: 0, status: "success") {
        total
        limit
        offset
        items {
          key
          studyId
          version
          runId
          status
          eventCount
          hasMetrics
        }
      }
    }
    """,
    context_value=authorized_graphql_context,
  )

  assert list_result.errors is None
  page = list_result.data["researchRuns"]
  assert page["total"] == 1
  assert page["items"][0]["studyId"] == "volume-shock"
  assert page["items"][0]["version"] == "v1"
  assert page["items"][0]["status"] == "success"
  key = page["items"][0]["key"]

  detail_result = await schema.execute(
    """
    query ResearchRun($key: String!) {
      researchRun(key: $key) {
        summary {
          key
          studyId
          version
          runId
        }
        dataQuality
        analysisSampleCount
        eventCurve
        interactionHeatmap
        comparison
        comparisonSensitivity
        regressions
        robustness
        warnings
        artifactErrors
      }
    }
    """,
    variable_values={"key": key},
    context_value=authorized_graphql_context,
  )

  assert detail_result.errors is None
  detail = detail_result.data["researchRun"]
  assert detail["summary"]["key"] == key
  assert detail["dataQuality"] == {
    "is_usable": True,
    "row_count": 801,
    "source_provenance": {
      "kind": "qmt-daily-bar-archive",
      "ledger_sha256": "b" * 64,
      "selected_request_count": 180,
    },
  }
  assert detail["analysisSampleCount"] == 801
  assert detail["eventCurve"][0]["mean"] == 0.01
  assert detail["interactionHeatmap"] == []
  assert detail["comparison"] == []
  assert detail["comparisonSensitivity"] == {}
  assert detail["warnings"] == ["测试告警"]
  assert detail["artifactErrors"] == []


@pytest.mark.asyncio
async def test_research_query_requires_market_read_permission(
  tmp_path,
):
  from datetime import datetime, timedelta, timezone

  from quantx_api.auth.principal import Principal
  from quantx_api.gqlapi.schema import schema

  _create_success_run(tmp_path)
  context = {
    "principal": Principal(
      user_id="test-user",
      username="test-user",
      display_name="Test User",
      device_session_id="test-session",
      access_token_expires_at=(
        datetime.now(timezone.utc) + timedelta(minutes=5)
      ).replace(tzinfo=None),
      permissions=frozenset({"system-status:read"}),
      authorized_account_ids=(),
    ),
    "request_id": "research-forbidden",
  }

  result = await schema.execute(
    "{ researchRuns { total } }",
    context_value=context,
  )

  assert result.data is None
  assert result.errors[0].extensions["code"] == "FORBIDDEN"
