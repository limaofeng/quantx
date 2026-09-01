from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.schemas.stock_selection_schema import (
  _resolve_candidate_runs,
  _validate_candidate_input,
)
from quantx_api.gqlapi.types.stock_selection_types import (
  StockProbabilityCandidateInput,
)


def test_probability_and_indicator_contracts_are_atomic() -> None:
  graphql = schema.as_str()

  assert "stockProbabilityCandidates" in graphql
  assert "stockSelectionModels" in graphql
  assert "registerStockSelectionModel" in graphql
  assert "setStockSelectionModelStage" in graphql
  assert "stockIndicatorCatalog" in graphql
  assert "indicatorConditions" in graphql
  assert "stockIndicatorReportMatches" in graphql
  assert "candidateRuleVersion" in graphql
  assert "factorSnapshotSha256" in graphql
  assert "selectionMetrics" in graphql
  assert "stockFactorCatalog" not in graphql
  assert "factorConditions" not in graphql
  assert "stockFactorReportMatches" not in graphql


def test_probability_candidate_input_is_bounded() -> None:
  _validate_candidate_input(
    StockProbabilityCandidateInput(
      as_of=date(2026, 9, 1), minimum_probability=0.6, limit=50, offset=0
    )
  )

  with pytest.raises(ValueError, match="minimumProbability"):
    _validate_candidate_input(StockProbabilityCandidateInput(minimum_probability=1.1))
  with pytest.raises(ValueError, match="limit"):
    _validate_candidate_input(StockProbabilityCandidateInput(limit=201))


def test_latest_failed_attempt_never_falls_back_to_old_candidates() -> None:
  runs = [
    SimpleNamespace(
      model_version="model-a",
      model_stage="ACTIVE",
      as_of_date=date(2026, 9, 1),
      started_at=datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
      status="FAILED",
    ),
    SimpleNamespace(
      model_version="model-a",
      model_stage="ACTIVE",
      as_of_date=date(2026, 9, 1),
      started_at=datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
      status="SUCCESS",
    ),
    SimpleNamespace(
      model_version="model-a",
      model_stage="ACTIVE",
      as_of_date=date(2026, 8, 31),
      started_at=datetime(2026, 8, 31, 15, tzinfo=timezone.utc),
      status="SUCCESS",
    ),
  ]

  resolved, selected, warnings = _resolve_candidate_runs(
    runs,
    current_stages={"model-a": "ACTIVE"},
    requested_date=None,
  )

  assert resolved == date(2026, 9, 1)
  assert selected == []
  assert warnings == ["模型 model-a 最新运行状态为 FAILED，未复用旧候选"]


def test_old_shadow_run_is_not_reused_after_model_activation() -> None:
  runs = [
    SimpleNamespace(
      model_version="model-a",
      model_stage="SHADOW",
      as_of_date=date(2026, 9, 1),
      started_at=datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
      status="SUCCESS",
    )
  ]

  resolved, selected, warnings = _resolve_candidate_runs(
    runs,
    current_stages={"model-a": "ACTIVE"},
    requested_date=None,
  )

  assert resolved is None
  assert selected == []
  assert warnings == ["尚无当前模型阶段的概率推理运行"]
